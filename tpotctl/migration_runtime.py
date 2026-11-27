"""Private migration runtime and its narrowly scoped systemd start guard."""

import csv
from contextlib import contextmanager, ExitStack
import fcntl
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
import uuid


class RuntimeError(Exception):
    """A maintenance operation could not be completed safely."""


def _identity(unit: str, run_id: str, owner_uid: int) -> None:
    if not isinstance(unit, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+\.service", unit):
        raise RuntimeError("invalid migration service name")
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise RuntimeError("invalid migration run identity")
    if type(owner_uid) is not int or owner_uid < 0:
        raise RuntimeError("invalid migration owner")


def _owned_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError("migration service directory must be owned and not writable by other users")


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def systemd_guard(root: str, unit: str, run_id: str, owner_uid: int, action: str) -> bool:
    """Maintain only the exact owned drop-in for this run; the CLI fixes root to /."""
    _identity(unit, run_id, owner_uid)
    if action not in ("hold", "release", "status"):
        raise RuntimeError("invalid migration guard operation")
    base = Path(root)
    if not base.is_absolute():
        raise RuntimeError("migration service root must be absolute")
    _owned_directory(base)
    path = base
    for part in ("etc", "systemd", "system", unit + ".d"):
        path = path / part
        if action == "hold":
            try:
                path.mkdir(mode=0o755)
                _sync_directory(path.parent)
            except FileExistsError:
                pass
        try:
            _owned_directory(path)
        except FileNotFoundError:
            return False
    filename = path / "90-tpot-migration.conf"
    approved = path / (".tpot-migration-approved-" + run_id)
    if os.path.lexists(approved):
        raise RuntimeError("migration guard approval path unexpectedly exists; refusing an ineffective hold")
    expected = (f"# T-Pot migration {run_id}, owner UID {owner_uid}\n"
                f"[Unit]\nConditionPathExists=/etc/systemd/system/{unit}.d/{approved.name}\n").encode("ascii")
    lock = os.open(path / ".tpot-migration.lock",
                   os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 \
                or stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError("migration guard lock is not an owned private regular file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another migration guard operation is running") from None
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            present = False
        else:
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 \
                        or stat.S_IMODE(info.st_mode) != 0o644 or handle.read(len(expected) + 1) != expected:
                    raise RuntimeError("a foreign migration drop-in exists; nothing was changed")
            present = True
        if action == "status":
            return present
        if action == "release":
            if present:
                os.unlink(filename)
                _sync_directory(path)
            return False
        if not present:
            fd, temporary = tempfile.mkstemp(prefix=".tpot-migration-", dir=path)
            try:
                with os.fdopen(fd, "wb") as handle:
                    os.fchmod(handle.fileno(), 0o644)
                    handle.write(expected)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, filename)
                _sync_directory(path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return True
    finally:
        os.close(lock)


class SystemdHold:
    def __init__(self, run_id: str, become_file: Optional[str] = None, *, unit: str = "tpot.service"):
        _identity(unit, run_id, os.getuid())
        self.run_id = run_id
        self.unit = unit
        self._password = None
        if become_file:
            from tpotctl.sudo_mode import SudoError, read_password_file

            try:
                self._password = read_password_file(become_file)
            except SudoError as err:
                raise RuntimeError(str(err)) from err

    def command(self, *args: str, timeout: int = 180) -> str:
        prefix = ["sudo", "-n"] if self._password is None else ["sudo", "-S", "-p", ""]
        data = None if self._password is None else self._password + "\n"
        try:
            result = subprocess.run(prefix + list(args), input=data, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as err:
            raise RuntimeError(f"privileged migration operation failed: {err}") from err
        if result.returncode:
            detail = " ".join(result.stderr.split())[:300]
            raise RuntimeError(f"privileged migration operation failed: {detail}; use -B FILE if sudo needs a password")
        return result.stdout

    def check_access(self) -> None:
        """Hours of snapshot or packaging pass without sudo: its cached timestamp must not be what works."""
        if self._password is not None:
            return
        try:
            result = subprocess.run(["sudo", "-n", "-k", "true"], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError) as err:
            raise RuntimeError(f"sudo could not be checked: {err}") from err
        if result.returncode:
            raise RuntimeError("sudo needs a password here, and its cached timestamp expires during a long "
                               "migration; use -B FILE with a private password file")

    def guard(self, action: str) -> bool:
        value = self.command("python3", os.path.abspath(__file__), "--guard",
                             self.unit, self.run_id, str(os.getuid()), action)
        try:
            result = json.loads(value)
            if not isinstance(result, dict) or set(result) != {"held"} or type(result["held"]) is not bool:
                raise ValueError("invalid guard result")
        except ValueError as err:
            raise RuntimeError("migration service guard returned an invalid result") from err
        return result["held"]

    def hold(self) -> None:
        if not self.guard("hold"):
            raise RuntimeError("migration service start guard was not installed")
        self.command("systemctl", "daemon-reload")

    def hold_and_stop(self) -> None:
        self.hold()
        self.command("systemctl", "stop", self.unit)
        state = self.command("systemctl", "show", "--property=ActiveState", "--value", self.unit).strip()
        if state not in ("inactive", "failed"):
            raise RuntimeError("the source service did not stop; its migration guard remains in place")

    def release(self) -> None:
        if self.guard("release"):
            raise RuntimeError("migration service start guard was not removed")
        self.command("systemctl", "daemon-reload")

    def start(self) -> None:
        self.command("systemctl", "start", self.unit)


class Journal:
    """One locked, private migration run, bound to its caller-supplied identity."""

    PHASES = frozenset(("new", "held", "snapshot", "packaged", "restoring", "verified", "releasing", "resuming",
                        "released"))

    def __init__(self, directory: str, identity: dict):
        self.directory = Path(os.path.abspath(directory))
        self.identity = dict(identity)
        self.state = {}
        self._lock = None

    @property
    def run_id(self) -> str:
        return self.state["run"]

    def __enter__(self):
        from tpotctl import migration

        try:
            self.directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            migration._private_directory(str(self.directory), create=True)
            fd = os.open(self.directory / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            self._lock = fd
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 \
                    or stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError("migration run lock must be an owned private regular file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("another process owns this migration run") from None
            path = self.directory / "state.json"
            if os.path.lexists(path):
                migration._private_file(str(path), "migration run state")
                handle, info = migration._open_regular(str(path), "migration run state")
                with handle:
                    if info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                        raise RuntimeError("migration run state is no longer private")
                    raw = handle.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise RuntimeError("migration run state is too large")
                self.state = json.loads(raw)
                if not isinstance(self.state, dict) or set(self.state) != {
                    "format", "schema", "run", "identity", "phase", "facts",
                } or self.state["format"] != "tpot-migration-run" or self.state["schema"] != 1:
                    raise RuntimeError("invalid migration run state")
                _identity("tpot.service", self.state["run"], os.getuid())
                if self.state["identity"] != self.identity:
                    raise RuntimeError("migration run belongs to a different source or package")
                if self.state["phase"] not in self.PHASES or not isinstance(self.state["facts"], dict):
                    raise RuntimeError("invalid migration phase or recorded facts")
            else:
                self.state = {"format": "tpot-migration-run", "schema": 1, "run": uuid.uuid4().hex,
                              "identity": self.identity, "phase": "new", "facts": {}}
                migration._write_private(str(path), json.dumps(self.state, sort_keys=True).encode("utf-8"))
            return self
        except (OSError, ValueError, TypeError, migration.MigrationError, RuntimeError) as err:
            if self._lock is not None:
                os.close(self._lock)
                self._lock = None
            raise RuntimeError(f"cannot open migration run: {err}") from err

    def record(self, phase: str, **facts) -> None:
        if self._lock is None or phase not in self.PHASES:
            raise RuntimeError("cannot record a migration without its lock and a valid phase")
        updated = dict(self.state, phase=phase, facts=dict(self.state["facts"], **facts))
        self._publish(updated)

    def next_run(self) -> None:
        from tpotctl import migration

        if self._lock is None or self.state["phase"] != "released":
            raise RuntimeError("a new final export requires the previous run to be released")
        previous = self.directory / ("completed-" + self.run_id + ".json")
        if os.path.lexists(previous):
            migration._private_file(str(previous), "completed migration record")
            if json.loads(previous.read_bytes()) != self.state:
                raise RuntimeError("completed migration record belongs to another state")
        else:
            migration._write_private(str(previous), json.dumps(self.state, sort_keys=True).encode("utf-8"))
        self._publish(dict(self.state, run=uuid.uuid4().hex, phase="new", facts={}))

    def _publish(self, updated: dict) -> None:
        from tpotctl import migration

        path = self.directory / "state.json"
        migration._private_file(str(path), "migration run state")
        fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(updated, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            migration._fsync_directory(str(self.directory))
            self.state = updated
        except (OSError, TypeError, ValueError) as err:
            raise RuntimeError(f"cannot persist migration checkpoint: {err}") from err
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def __exit__(self, exc_type, exc_value, traceback):
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None


@contextmanager
def administration_locks(home: Path, data: Path):
    from tpotctl import fleet_remote, kibana_objects

    with ExitStack() as cleanup:
        try:
            fleet_remote.idle(home)
            state = home / ".local/share/tpotce"
            state.mkdir(mode=0o700, parents=True, exist_ok=True)
            _owned_directory(state)
            fd = os.open(state / "update.lock",
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            cleanup.callback(os.close, fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 \
                    or stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError("update lock is not an owned private regular file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            folder = kibana_objects.Folder.walk(str(data), ("ews",), False)
            if folder is not None:
                cleanup.callback(folder.close)
                check = folder.open_lock("check.lock")
                cleanup.callback(os.close, check)
                fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, fleet_remote.RemoteError, kibana_objects.KibanaError) as err:
            raise RuntimeError(f"cannot acquire migration administration locks: {err}") from err
        yield


class ContainerOpener(urllib.request.OpenerDirector):
    """Reach a maintenance API without exposing ports or giving it external networking."""

    def __init__(self, runtime: "DockerRuntime", name: str, port: int):
        super().__init__()
        self.runtime = runtime
        self.name = name
        self.port = port

    def open(self, fullurl, data=None, timeout=60):
        request = fullurl if isinstance(fullurl, urllib.request.Request) else urllib.request.Request(fullurl, data)
        url = urllib.parse.urlsplit(request.full_url)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.port != self.port \
                or url.username is not None or url.password is not None or url.fragment:
            raise urllib.error.URLError("maintenance API request is not for its isolated endpoint")
        self.runtime._owned("container", self.name)
        args = ["exec", "--user", "0", self.name, "curl", "--silent", "--show-error",
                "--noproxy", "*", "--max-time", str(timeout), "--request", request.get_method(),
                "--write-out", "\n%{http_code}"]
        for key, value in request.header_items():
            if key.lower() not in ("content-type", "kbn-xsrf", "accept"):
                raise urllib.error.URLError("unsupported maintenance API request header")
            args += ["--header", key + ": " + value]
        local = None
        remote = None
        try:
            if request.data is not None:
                fd, local = tempfile.mkstemp(prefix=".http-", dir=self.runtime.journal.directory)
                with os.fdopen(fd, "wb") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(request.data)
                remote = "/tmp/tpot-migration-http-" + uuid.uuid4().hex
                self.runtime.docker("cp", local, self.name + ":" + remote)
                args += ["--data-binary", "@" + remote]
            value = self.runtime.docker(*args, request.full_url)
        except RuntimeError as err:
            raise urllib.error.URLError(str(err)) from err
        finally:
            if local is not None:
                os.unlink(local)
            if remote is not None:
                self.runtime._owned("container", self.name)
                self.runtime.docker("exec", "--user", "0", self.name, "rm", "--", remote)
        body, separator, status = value.rpartition("\n")
        if not separator or not re.fullmatch(r"[1-5][0-9][0-9]", status):
            raise urllib.error.URLError("maintenance API returned an invalid HTTP status")
        stream = io.BytesIO(body.encode("utf-8"))
        code = int(status)
        if not 200 <= code < 300:
            raise urllib.error.HTTPError(request.full_url, code, "maintenance API request failed", {}, stream)
        return urllib.response.addinfourl(stream, {}, request.full_url, code)


# a pull of the 8.19 images or a recursive chown over hundreds of GB outlasts an ordinary Docker call
SLOW_DOCKER = 4 * 3600


class DockerRuntime:
    """Only the containers and network bearing this private run's name and label."""

    def __init__(self, journal: Journal, execute: Optional[Callable[..., str]] = None):
        self.journal = journal
        self.prefix = "tpot-migration-" + journal.run_id
        self.label = "tpot.migration=" + journal.run_id
        self.execute = execute

    def docker(self, *args: str, timeout: int = 180) -> str:
        if self.execute is not None:
            return self.execute("docker", *args, timeout=timeout)
        try:
            result = subprocess.run(["docker", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as err:
            raise RuntimeError(f"migration Docker operation failed: {err}") from err
        if result.returncode:
            raise RuntimeError(f"migration Docker {args[0]} failed: {' '.join(result.stderr.split())[:500]}")
        return result.stdout

    def pull(self, image: str) -> None:
        self.docker("pull", image, timeout=SLOW_DOCKER)

    def name(self, suffix: str) -> str:
        if not isinstance(suffix, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", suffix):
            raise RuntimeError("invalid migration resource suffix")
        return self.prefix + "-" + suffix

    def owned_ids(self, kind: str) -> list:
        if kind not in ("container", "network"):
            raise RuntimeError("unsupported migration resource kind")
        options = ["--all"] if kind == "container" else []
        return self.docker(kind, "ls", "--quiet", *options, "--filter", "label=" + self.label).split()

    def inspect(self, kind: str, identifier: str) -> dict:
        try:
            values = json.loads(self.docker(kind, "inspect", identifier))
        except ValueError as err:
            raise RuntimeError("Docker returned invalid migration resource metadata") from err
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
            raise RuntimeError("Docker did not return one migration resource")
        return values[0]

    def _owned(self, kind: str, identifier: str) -> dict:
        info = self.inspect(kind, identifier)
        labels = info.get("Config", {}).get("Labels") if kind == "container" else info.get("Labels")
        name = info.get("Name")
        if not isinstance(labels, dict) or labels.get("tpot.migration") != self.journal.run_id \
                or not isinstance(name, str) or not name.lstrip("/").startswith(self.prefix + "-"):
            raise RuntimeError("refusing to change a foreign Docker resource")
        return info

    def network(self) -> str:
        name = self.name("network")
        existing = self.owned_ids("network")
        if existing:
            if len(existing) != 1 or self._owned("network", existing[0])["Name"] != name:
                raise RuntimeError("unexpected network in this migration run")
        else:
            self.docker("network", "create", "--internal", "--label", self.label, name)
        if self._owned("network", name).get("Internal") is not True:
            raise RuntimeError("migration network permits external traffic")
        return name

    def container(self, suffix: str, args: list) -> str:
        name = self.name(suffix)
        self.docker("create", "--name", name, "--label", self.label, "--restart=no", *args)
        self._owned("container", name)
        self.docker("start", name)
        return name

    def _private_path(self, directory: str) -> Path:
        path = Path(os.path.abspath(directory))
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("migration data path must be a real directory")
        if path.parent != self.journal.directory:
            claim = self.journal.state["facts"].get("target_data")
            if self.journal.identity.get("operation") != "import" or not isinstance(claim, dict) \
                    or claim.get("state") != "ready" or claim.get("path") != str(path) \
                    or claim.get("device") != info.st_dev or claim.get("inode") != info.st_ino \
                    or path.resolve() != path:
                raise RuntimeError("permission helper requires this run's private or claimed target directory")
        return path

    def target_directory(self, directory: str) -> str:
        """Claim a new target directory before a maintenance process can write it."""
        path = Path(os.path.abspath(directory))
        if self.journal.identity.get("operation") != "import" or path.resolve() != path:
            raise RuntimeError("target data requires an import run and a concrete unlinked path")
        claim = self.journal.state["facts"].get("target_data")
        new = claim is None
        if new:
            if os.path.lexists(path):
                raise RuntimeError("migration will not take over existing target data")
            claim = {"path": str(path), "state": "creating"}
            self.journal.record(self.journal.state["phase"], target_data=claim)
        if not isinstance(claim, dict) or claim.get("path") != str(path) \
                or claim.get("state") not in ("creating", "ready"):
            raise RuntimeError("target data does not match this migration's claim")
        if claim["state"] == "creating":
            parent = path.parent
            while not parent.exists():
                parent = parent.parent
            _owned_directory(parent)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _owned_directory(path.parent)
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                if new:
                    raise RuntimeError("target data appeared while the import was being prepared") from None
            _owned_directory(path)
            info = path.lstat()
            if stat.S_IMODE(info.st_mode) != 0o700 or any(path.iterdir()):
                raise RuntimeError("uncheckpointed target data is not an empty private directory")
            _sync_directory(path.parent)
            self.journal.record(self.journal.state["phase"], target_data=dict(
                claim, state="ready", device=info.st_dev, inode=info.st_ino))
        return str(self._private_path(str(path)))

    def directory(self, suffix: str) -> str:
        self.name(suffix)
        path = self.journal.directory / suffix
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        return str(self._private_path(str(path)))

    @staticmethod
    def mount(path: str, target: str, *, readonly: bool = False) -> str:
        if not os.path.isabs(path) or not target.startswith("/") or \
                any(ord(char) < 32 for char in path + target):
            raise RuntimeError("migration bind mounts require concrete absolute paths")
        parts = ["type=bind", "source=" + path, "target=" + target]
        if readonly:
            parts.append("readonly")
        output = io.StringIO()
        csv.writer(output, lineterminator="").writerow(parts)
        return output.getvalue()

    def image_info(self, image: str) -> dict:
        if not isinstance(image, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]*", image):
            raise RuntimeError("invalid migration image reference")
        return self.inspect("image", image)

    def image_user(self, image: str) -> str:
        user = self.image_info(image).get("Config", {}).get("User")
        if not isinstance(user, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)?", user) \
                or user.split(":")[0] in ("0", "root"):
            raise RuntimeError("migration Elasticsearch image must identify its non-root user")
        return user

    def permissions(self, directory: str, image: str, owner: str, *, recursive: bool = False) -> None:
        path = self._private_path(directory)
        if not re.fullmatch(r"[a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)?", owner):
            raise RuntimeError("invalid migration directory owner")
        script = 'chown "$1" /work && chmod 0700 /work'
        if recursive:
            script = ('chown -R -h "$1" /work && '
                      'find /work -type d -exec chmod 0700 {} + && '
                      'find /work -type f -exec chmod 0600 {} +')
        name = self.container("permissions-" + uuid.uuid4().hex[:12], [
            "--network", "none", "--user", "0:0", "--memory", "128m", "--cpus", "1",
            "--mount", self.mount(str(path), "/work"), "--entrypoint", "/bin/sh",
            image, "-c", script, "migration-permissions", owner,
        ])
        result = self.docker("wait", name, timeout=SLOW_DOCKER).strip()
        self._owned("container", name)
        self.docker("container", "rm", name)
        if result != "0":
            raise RuntimeError("migration directory permissions could not be prepared")

    def stop(self, name: str) -> None:
        if not isinstance(name, str) or not name.startswith(self.prefix + "-"):
            raise RuntimeError("refusing to stop a container outside this migration run")
        info = self._owned("container", name)
        if info.get("State", {}).get("Running") is True:
            self.docker("stop", "--time", "60", name)

    def cleanup(self) -> None:
        errors = []
        for kind in ("container", "network"):
            for identifier in self.owned_ids(kind):
                try:
                    info = self._owned(kind, identifier)
                    if kind == "container" and info.get("State", {}).get("Running") is True:
                        self.docker("stop", "--time", "60", identifier)
                    self.docker(kind, "rm", identifier)
                except RuntimeError as err:
                    errors.append(str(err))
        if errors:
            raise RuntimeError("migration resource cleanup failed: " + "; ".join(errors))

    def elasticsearch(self, image: str, version: str, suffix: str, data_dir: str,
                      repositories: dict):
        from tpotctl.snapshots import SnapshotClient, SnapshotError

        info = self.image_info(image)
        java = "-Xms2g -Xmx2g"
        if info.get("Architecture") == "arm64":
            java += " -XX:UseSVE=0"
        args = ["--network", self.network(), "--cpus", "2", "--memory", "4g",
                "--mount", self.mount(data_dir, "/migration/data"),
                "-e", "ES_JAVA_OPTS=" + java, "-e", "ES_TMPDIR=/tmp",
                "--entrypoint", "/usr/share/elasticsearch/bin/elasticsearch"]
        for folder, (directory, readonly) in repositories.items():
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", folder):
                raise RuntimeError("invalid migration repository mount name")
            args += ["--mount", self.mount(directory, "/snapshots/" + folder, readonly=readonly)]
        args += [image, "-Epath.data=/migration/data", "-Epath.logs=/tmp/migration-logs",
                 "-Ehttp.host=0.0.0.0", "-Ediscovery.type=single-node",
                 "-Expack.security.enabled=false", "-Expack.security.http.ssl.enabled=false",
                 "-Expack.security.transport.ssl.enabled=false", "-Expack.ml.enabled=false",
                 "-Eingest.geoip.downloader.enabled=false", "-Ebootstrap.memory_lock=false",
                 # the capacity check reserved absolute bytes; percentage watermarks would stall a restore
                 "-Ecluster.routing.allocation.disk.threshold_enabled=false"]
        if repositories:
            args.append("-Epath.repo=/snapshots")
        name = self.container(suffix, args)
        client = SnapshotClient("http://127.0.0.1:9200")
        client.opener = ContainerOpener(self, name, 9200)
        deadline = time.monotonic() + 600
        last = "not ready"
        while time.monotonic() < deadline:
            if self._owned("container", name).get("State", {}).get("Running") is not True:
                raise RuntimeError("migration Elasticsearch stopped; its data and start guard are retained")
            try:
                observed = client.version()
                if observed != version:
                    raise RuntimeError("migration Elasticsearch runtime version differs from its verified source")
                health = client.request("GET", "/_cluster/health?wait_for_status=yellow&timeout=5s")
                if not health.get("timed_out") and health.get("status") in ("yellow", "green"):
                    return name, client
                last = "cluster primaries are not all ready"
            except SnapshotError as err:
                last = str(err)
            time.sleep(2)
        raise RuntimeError(f"migration Elasticsearch did not become ready: {last}")

    def kibana(self, image: str, version: str, suffix: str, es_name: str) -> tuple:
        from tpotctl.snapshots import SnapshotClient, SnapshotError

        self.image_info(image)
        network = self.network()
        elasticsearch = self._owned("container", es_name)
        if set(elasticsearch.get("NetworkSettings", {}).get("Networks", {})) != {network} \
                or elasticsearch.get("State", {}).get("Running") is not True:
            raise RuntimeError("Kibana requires this run's isolated, running Elasticsearch")
        name = self.container(suffix, [
            "--network", network, "--cpus", "2", "--memory", "3g",
            "-e", "NODE_OPTIONS=--max-old-space-size=2048", "-e", "ELASTIC_APM_ACTIVE=false",
            "--entrypoint", "/usr/share/kibana/bin/kibana", image,
            "--elasticsearch.hosts=" + json.dumps(["http://" + es_name + ":9200"]),
            "--server.host=0.0.0.0", "--server.basePath=/kibana", "--server.rewriteBasePath=true",
            "--status.allowAnonymous=true",
            "--path.data=/tmp/migration-kibana", "--telemetry.enabled=false", "--telemetry.optIn=false",
        ])
        opener = ContainerOpener(self, name, 5601)
        client = SnapshotClient("http://127.0.0.1:5601")
        client.opener = opener
        deadline = time.monotonic() + 600
        last = "not ready"
        while time.monotonic() < deadline:
            if self._owned("container", name).get("State", {}).get("Running") is not True:
                raise RuntimeError("migration Kibana stopped; its data and start guard are retained")
            try:
                status = client.request("GET", "/kibana/api/status")
                if not isinstance(status, dict) or status.get("version", {}).get("number") != version:
                    raise RuntimeError("migration Kibana runtime version differs from its verified source")
                if status.get("status", {}).get("overall", {}).get("level") == "available":
                    return name, client.url + "/kibana", opener
                last = "Kibana is not fully available"
            except SnapshotError as err:
                last = str(err)
            time.sleep(2)
        raise RuntimeError(f"migration Kibana did not become ready: {last}")


if __name__ == "__main__":
    try:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise RuntimeError("the migration service guard requires Linux root privileges")
        if len(sys.argv) != 6 or sys.argv[1] != "--guard":
            raise RuntimeError("invalid migration service guard invocation")
        held = systemd_guard("/", sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
        print(json.dumps({"held": held}))
    except (RuntimeError, OSError, ValueError) as err:
        print(str(err), file=sys.stderr)
        sys.exit(1)
