"""Sensor-side fleet control, staged with envfile.py; no installed Manager is required."""

import base64
import binascii
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Dict, List
from urllib.parse import urlsplit

if __package__:
    from .envfile import EnvFile
else:
    from envfile import EnvFile


class RemoteError(Exception):
    """Preparation cannot safely continue on this sensor."""


def command(args: List[str], *, password: str = "", timeout: int = 60) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("TPOT_UPDATE_") and key not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4")}
    env.update(TPOT_GUM="off", NO_COLOR="1", GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(args, input=password, capture_output=True, text=True, env=env, timeout=timeout)
    except FileNotFoundError as err:
        raise RemoteError(f"{args[0]} is required on the sensor.") from err
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as err:
        raise RemoteError(f"{args[0]} could not finish ({type(err).__name__}).") from err


def checked(args: List[str], *, timeout: int = 60) -> str:
    result = command(args, timeout=timeout)
    if result.returncode:
        raise RemoteError(f"{args[0]} {args[1]} failed (exit {result.returncode}).")
    return result.stdout.strip()


def validate(request: dict) -> None:
    for key, pattern in (("run_id", r"[a-f0-9]{32}"), ("name", r"[A-Za-z0-9_.-]{1,512}"),
                         ("version", r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}")):
        if not isinstance(request.get(key), str) or not re.fullmatch(pattern, request[key]):
            raise RemoteError(f"Invalid fleet {key}.")
    branch, repo = request.get("branch"), request.get("repo")
    if not isinstance(branch, str) or not branch or branch == "HEAD" or branch.startswith("-"):
        raise RemoteError("Invalid target branch.")
    if command(["git", "check-ref-format", "--branch", branch]).returncode:
        raise RemoteError("Invalid target branch.")
    if (not isinstance(repo, str) or not repo or repo.startswith("-")
            or any(ord(char) < 32 or ord(char) == 127 for char in repo)):
        raise RemoteError("Invalid target repository.")
    if "://" in repo:
        try:
            url = urlsplit(repo)
        except ValueError as err:
            raise RemoteError("Invalid target repository URL.") from err
        if url.scheme not in ("https", "http", "ssh", "file") or url.password or (
                url.scheme in ("https", "http") and url.username):
            raise RemoteError("Use a repository URL without embedded credentials or custom transports.")
    elif not (os.path.isabs(repo) or re.fullmatch(r"[\w.-]+@[\w.-]+:[^\s:]+", repo)):
        raise RemoteError("Invalid target repository transport.")
    if "password" in request:
        password = request["password"]
        if (not isinstance(password, str) or not password or any(char in password for char in "\r\n\0")
                or len(password.encode("utf-8")) > 65536):
            raise RemoteError("The sudo password must be one nonempty line.")


def identity(home: Path, expected: str) -> str:
    try:
        env = EnvFile(str(home / "tpotce/.env"))
        if env.get("TPOT_TYPE") != "SENSOR":
            raise RemoteError("Sensor identity refused: TPOT_TYPE is not SENSOR.")
        name, separator, password = base64.b64decode(
            env.get("TPOT_HIVE_USER") or "", validate=True).decode("utf-8").partition(":")
        if not separator or not password or name != expected:
            raise RemoteError("Sensor identity does not match the authenticated registry name.")
        version = (home / "tpotce/version").read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}", version):
            raise RemoteError("The installed sensor version is invalid.")
        return version
    except (OSError, UnicodeError, ValueError, binascii.Error) as err:
        raise RemoteError("Sensor identity could not be read from its configuration.") from err


def directory(path: Path, *, private: bool = False, create: bool = True) -> None:
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or (private and stat.S_IMODE(info.st_mode) != 0o700)):
        raise RemoteError(f"Cannot use the fleet directory {path}: check ownership, links and permissions.")


def idle(home: Path) -> None:
    info = checked(["systemctl", "show", "tpot-fleet-update.service",
                    "--property=LoadState,ActiveState", "--no-pager"])
    fields = dict(line.split("=", 1) for line in info.splitlines() if "=" in line)
    if fields.get("ActiveState") not in ("inactive", "failed") or fields.get("LoadState") not in ("loaded", "not-found"):
        raise RemoteError("A fleet update is already running, or its unit state cannot be determined.")
    processes = checked(["ps", "-eo", "uid=,pid=,args="])
    for line in processes.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3 or not parts[1].isdigit():
            raise RemoteError("Cannot determine whether a manual update is running.")
        if int(parts[1]) == os.getpid():
            continue
        # ps prints arguments, not shell-escaped source; unrelated unmatched quotes are normal.
        if any(os.path.basename(word.strip("'\"")) == "update.sh" for word in parts[2].split()):
            raise RemoteError("A manual T-Pot update is already running.")
    lock = home / ".local/share/tpotce/update.lock"
    try:
        fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise RemoteError("The update lock is not an owned regular single-link file.")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            raise RemoteError("A T-Pot update or backup is already running.") from err


def sudo(password: str) -> None:
    if password and command(["sudo", "-S", "-p", "", "-v"], password=password + "\n").returncode:
        raise RemoteError("sudo rejected the sensor password.")
    if command(["sudo", "-n", "true"]).returncode:
        raise RemoteError("sudo needs a working password for this sensor.")


def write_private(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, UnicodeError):
        path.unlink()
        raise


def save(path: Path, state: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def storage_warnings() -> List[str]:
    try:
        root = checked(["docker", "info", "--format", "{{.DockerRootDir}}"])
        if not os.path.isabs(root):
            raise RemoteError("Docker returned no absolute data directory.")
        info = os.statvfs(root)
        free = info.f_bavail * info.f_frsize
        if free < 10_000_000_000:
            return [f"Docker storage has less than 10 GB free ({free} bytes)."]
        return []
    except (RemoteError, OSError) as err:
        return [f"Docker storage could not be checked: {err}"]


def prepare(request: dict, home: Path) -> Dict:
    validate(request)
    if checked(["uname", "-s"]) != "Linux" or os.geteuid() == 0:
        raise RemoteError("Fleet updates require the T-Pot user on a Linux sensor, not root.")
    previous_version = identity(home, request["name"])
    idle(home)
    password = request.get("password", "")
    sudo(password)
    directory(home, create=False)
    root = home
    for part in (".local", "share", "tpotce", "fleet"):
        root /= part
        directory(root, private=part == "fleet")
    work = root / request["run_id"]
    try:
        work.mkdir(mode=0o700)
    except FileExistsError as err:
        raise RemoteError("This fleet run already exists; resume or discard it explicitly.") from err
    target = work / "target"
    target.mkdir(mode=0o700)
    checkout = home / "tpotce"
    git = ["git", "-C", str(checkout), "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never"]
    ref = "refs/tpot-fleet/" + request["run_id"]
    checked(git + ["fetch", "--no-tags", "--no-recurse-submodules", "--", request["repo"],
                   "+refs/heads/" + request["branch"] + ":" + ref], timeout=120)
    commit = checked(git + ["rev-parse", "--verify", ref + "^{commit}"])
    if not re.fullmatch(r"[a-f0-9]{40,64}", commit):
        raise RemoteError("Git returned an invalid target commit.")
    version = checked(git + ["show", commit + ":version"])
    if version != request["version"]:
        raise RemoteError("The fetched target version differs from the HIVE; update the HIVE or select its source.")
    for name in ("update.sh", "restore.sh", "version"):
        source = command(git + ["show", commit + ":" + name])
        if source.returncode:
            raise RemoteError(f"The target has no usable {name}.")
        write_private(target / name, source.stdout)
    become = None
    try:
        args = ["bash", str(target / "update.sh"), "--check-data-loss",
                "-r", request["repo"], "-b", request["branch"]]
        if password:
            write_private(work / "become", password + "\n")
            become = work / "become"
            args += ["-B", str(become)]
        result = command(args, timeout=300)
        if result.returncode not in (0, 3):
            raise RemoteError(f"The target data-loss check failed (exit {result.returncode}).")
        losses = [line for line in result.stdout.splitlines() if line.startswith("@@tpot loss ")]
        if result.returncode == 3 and not losses:
            raise RemoteError("The target data-loss check returned no readable loss report.")
        state = dict(run_id=request["run_id"], name=request["name"], repo=request["repo"], branch=request["branch"],
                     version=version, previous_version=previous_version, commit=commit, phase="prepared",
                     losses=losses, warnings=storage_warnings())
        save(work / "state.json", state)
        return state
    finally:
        if become is not None:
            become.unlink()


def main() -> int:
    try:
        raw = sys.stdin.read(1_048_577)
        if len(raw) > 1_048_576:
            raise RemoteError("The fleet request is too large.")
        try:
            request = json.loads(raw)
        except ValueError as err:
            raise RemoteError("Invalid fleet request JSON.") from err
        if not isinstance(request, dict) or request.get("action") != "prepare":
            raise RemoteError("Unknown fleet action.")
        result = prepare(request, Path.home())
    except (RemoteError, OSError, UnicodeError) as err:
        print(json.dumps({"ok": False, "error": str(err)}))
        return 1
    print(json.dumps({"ok": True, "result": result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
