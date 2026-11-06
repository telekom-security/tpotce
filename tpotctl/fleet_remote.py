"""Sensor-side fleet control, staged with envfile.py; no installed Manager is required."""

import base64
import binascii
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Dict, List
from urllib.parse import urlsplit

if __package__:
    from .envfile import EnvFile
else:
    from envfile import EnvFile


class RemoteError(Exception):
    """Preparation cannot safely continue on this sensor."""


def command_environment() -> dict:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("TPOT_UPDATE_", "GIT_"))
           and key not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4")}
    env.update(TPOT_GUM="off", NO_COLOR="1", GIT_TERMINAL_PROMPT="0",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def command(args: List[str], *, password: str = "", timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, input=password, capture_output=True, text=True,
                              env=command_environment(), timeout=timeout)
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


def run_path(home: Path, run_id: str, *, create: bool = False) -> Path:
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise RemoteError("Invalid fleet run identifier.")
    root = home / ".local/share/tpotce/fleet"
    directory(home, create=False)
    for path in (home / ".local", home / ".local/share", home / ".local/share/tpotce", root):
        directory(path, private=path == root, create=create)
    run = root / run_id
    directory(run, private=True, create=create)
    return run


def read_state(home: Path, run_id: str, *, name: str = "") -> Dict:
    path = run_path(home, run_id) / "state.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
                raise RemoteError("The fleet state is not a private, owned regular file.")
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise RemoteError("The fleet state is too large.")
        state = json.loads(raw.decode("utf-8"))
    except RemoteError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as err:
        raise RemoteError("The fleet state is missing, invalid or unreadable.") from err
    required = {"run_id", "name", "repo", "branch", "version", "previous_version",
                "commit", "phase", "losses", "warnings"}
    optional = {"archive", "exit_code", "started_at", "finished_at", "error", "unit"}
    if not isinstance(state, dict) or not required.issubset(state) or set(state) - required - optional:
        raise RemoteError("The fleet state has an unexpected schema.")
    if (state["run_id"] != run_id or (name and state["name"] != name)
            or not isinstance(state["name"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", state["name"])
            or not isinstance(state["repo"], str) or not state["repo"] or len(state["repo"]) > 4096
            or not isinstance(state["branch"], str) or not state["branch"] or len(state["branch"]) > 256
            or not isinstance(state["version"], str) or not re.fullmatch(
                r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}", state["version"])
            or not isinstance(state["previous_version"], str) or not re.fullmatch(
                r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}", state["previous_version"])
            or not isinstance(state["commit"], str) or not re.fullmatch(r"[a-f0-9]{40,64}", state["commit"])
            or not isinstance(state["phase"], str) or state["phase"] not in {
                "prepared", "running", "completed", "failed", "rollback_running", "rolled_back", "rollback_failed"}
            or not isinstance(state["losses"], list) or len(state["losses"]) > 256
            or not all(isinstance(item, str) and item.startswith("@@tpot loss ") and len(item) <= 4096
                       for item in state["losses"])
            or len(state["losses"]) != len(set(state["losses"]))
            or not isinstance(state["warnings"], list) or len(state["warnings"]) > 256
            or not all(isinstance(item, str) and len(item) <= 4096 for item in state["warnings"])):
        raise RemoteError("The fleet state contains invalid values.")
    try:
        validate({key: state[key] for key in ("run_id", "name", "repo", "branch", "version")})
    except RemoteError as err:
        raise RemoteError("The fleet state contains an invalid target.") from err
    if "archive" in state:
        archive = Path(state["archive"]) if isinstance(state["archive"], str) else Path()
        if (not archive.is_absolute() or archive.parent != home / "tpot_backups" or
                ".." in archive.parts or not archive.name.endswith("_tpot_backup.tar")):
            raise RemoteError("The fleet state contains an invalid backup path.")
    if "exit_code" in state and (not isinstance(state["exit_code"], int) or not -255 <= state["exit_code"] <= 255):
        raise RemoteError("The fleet state contains an invalid exit code.")
    for key in ("started_at", "finished_at"):
        if key in state and (not isinstance(state[key], int) or state[key] < 0):
            raise RemoteError("The fleet state contains an invalid timestamp.")
    if "unit" in state and state["unit"] != "tpot-fleet-update.service":
        raise RemoteError("The fleet state names an unexpected systemd unit.")
    if "error" in state and (not isinstance(state["error"], str) or len(state["error"]) > 4096):
        raise RemoteError("The fleet state contains an invalid error.")
    return state


def remove_private_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        raise RemoteError(f"Refusing to remove an unsafe fleet file: {path.name}.")
    path.unlink()


def read_private_text(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
                raise RemoteError(f"The fleet input {path.name} is not a private regular file.")
            data = stream.read(65537)
        if len(data) > 65536:
            raise RemoteError(f"The fleet input {path.name} is too large.")
        return data.decode("utf-8")
    except RemoteError:
        raise
    except (OSError, UnicodeError) as err:
        raise RemoteError(f"The fleet input {path.name} is missing or unreadable.") from err


def pin_source(home: Path, state: dict, work: Path) -> Path:
    source = work / "source.git"
    if source.exists() or source.is_symlink():
        raise RemoteError("The pinned target repository already exists.")
    checkout = home / "tpotce"
    checked(["git", "init", "--bare", str(source)])
    checked(["git", "--git-dir", str(source), "fetch", "--no-tags", str(checkout),
             f'{state["commit"]}:refs/heads/{state["branch"]}'])
    pinned = checked(["git", "--git-dir", str(source), "rev-parse", "--verify",
                      f'refs/heads/{state["branch"]}^{{commit}}'])
    if pinned != state["commit"]:
        raise RemoteError("The staged Git source does not match the reviewed target commit.")
    return source


def forget_target_ref(home: Path, state: dict) -> None:
    try:
        checked(["git", "-C", str(home / "tpotce"), "update-ref", "-d",
                 "refs/tpot-fleet/" + state["run_id"]])
    except RemoteError as err:
        warning = f"The temporary target ref could not be removed: {err}"
        if warning not in state["warnings"]:
            state["warnings"].append(warning[:4096])


def read_log_tail(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return ""
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise RemoteError("The fleet worker log is not a private regular file.")
        start = max(0, info.st_size - 32768)
        os.lseek(fd, start, os.SEEK_SET)
        data = os.read(fd, 32768)
    except OSError as err:
        raise RemoteError("The fleet worker log could not be read.") from err
    finally:
        os.close(fd)
    if start:
        first_line = data.find(b"\n")
        data = data[first_line + 1:] if first_line >= 0 else b""
    return data.decode("utf-8", errors="replace")


def start(request: dict, home: Path) -> Dict:
    run_id, name = request.get("run_id"), request.get("name")
    if (not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id)
            or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", name)):
        raise RemoteError("Invalid fleet run identifier or sensor name.")
    state = read_state(home, run_id, name=name)
    if state["phase"] != "prepared":
        raise RemoteError("Only a prepared fleet run can be started.")
    if identity(home, name) != state["previous_version"]:
        raise RemoteError("The installed sensor version changed after preparation.")
    approved = request.get("accepted_losses", [])
    if (not isinstance(approved, list) or not all(isinstance(item, str) for item in approved)
            or len(approved) != len(set(approved)) or set(approved) != set(state["losses"])):
        raise RemoteError("Every reported data-loss item requires explicit confirmation.")
    password = request.get("password", "")
    if (not isinstance(password, str) or any(char in password for char in "\r\n\0")
            or len(password.encode("utf-8")) > 65536):
        raise RemoteError("Invalid sudo input.")
    idle(home)
    sudo(password)
    work = run_path(home, run_id)
    pin_source(home, state, work)
    try:
        write_private(work / "become", password + "\n")
        if approved:
            write_private(work / "accept-losses", "\n".join(approved) + "\n")
        for name in ("fleet_remote.py", "envfile.py"):
            source = Path(__file__).with_name(name)
            write_private(work / name, source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as err:
        cleanup_error = None
        for filename in ("become", "accept-losses", "fleet_remote.py", "envfile.py"):
            try:
                remove_private_file(work / filename)
            except RemoteError as cleanup:
                cleanup_error = cleanup
        if cleanup_error:
            raise RemoteError(f"Cannot clean the incomplete fleet worker staging: {cleanup_error}") from err
        raise RemoteError("Cannot stage the persistent fleet worker.") from err
    reset = command(["sudo", "-n", "systemctl", "reset-failed", "tpot-fleet-update.service"])
    if reset.returncode:
        remove_private_file(work / "become")
        if approved:
            remove_private_file(work / "accept-losses")
        raise RemoteError("Cannot reset the previous fleet worker unit.")
    state.update(phase="running", unit="tpot-fleet-update.service", started_at=int(time.time()))
    save(work / "state.json", state)
    args = [
        "sudo", "-S", "-p", "", "systemd-run", "--collect",
        "--unit=tpot-fleet-update.service", f"--uid={os.getuid()}",
        f"--property=WorkingDirectory={work}", "--property=Type=exec",
        f"--setenv=HOME={home}", "--setenv=PYTHONDONTWRITEBYTECODE=1",
        sys.executable, str(work / "fleet_remote.py"), "--worker", run_id, "update",
    ]
    result = command(args, password=password + "\n")
    if result.returncode:
        state.update(phase="failed", exit_code=1, finished_at=int(time.time()),
                     error="systemd-run could not start the fleet worker.")
        save(work / "state.json", state)
        remove_private_file(work / "become")
        if approved:
            remove_private_file(work / "accept-losses")
        raise RemoteError("systemd-run could not start the fleet worker.")
    return state


def status(home: Path, run_id: str, name: str) -> Dict:
    state = read_state(home, run_id, name=name)
    text = checked(["systemctl", "show", "tpot-fleet-update.service",
                    "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus", "--no-pager"])
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    load_state, active_state = fields.get("LoadState"), fields.get("ActiveState")
    if load_state not in ("loaded", "not-found") or active_state not in (
            "active", "activating", "reloading", "deactivating", "inactive", "failed"):
        raise RemoteError("The fleet worker unit returned an invalid status.")
    running_states = ("active", "activating", "reloading", "deactivating")
    work = run_path(home, run_id)
    if state["phase"] in ("running", "rollback_running") and active_state not in running_states:
        phase = "rollback_failed" if state["phase"] == "rollback_running" else "failed"
        state.update(phase=phase, exit_code=1, finished_at=int(time.time()),
                     error="The worker stopped before recording a final result.")
        forget_target_ref(home, state)
        save(work / "state.json", state)
        remove_private_file(work / "become")
        remove_private_file(work / "accept-losses")
    elif state["phase"] in ("completed", "failed", "rolled_back", "rollback_failed") \
            and active_state not in running_states:
        forget_target_ref(home, state)
        save(work / "state.json", state)
    result = dict(state)
    result["unit_active_state"] = active_state
    result["unit_sub_state"] = fields.get("SubState", "")
    log_name = "rollback.log" if state["phase"].startswith("rollback") or (work / "rollback.log").exists() \
        else "worker.log"
    result["log_tail"] = read_log_tail(work / log_name)
    return result


def compose_rows(output: str, label: str) -> List[dict]:
    try:
        try:
            value = json.loads(output)
            rows = value if isinstance(value, list) else [value]
        except ValueError:
            rows = [json.loads(line) for line in output.splitlines() if line.strip()]
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("invalid rows")
        return rows
    except (ValueError, TypeError) as err:
        raise RemoteError(f"docker compose returned invalid {label} JSON.") from err


def runtime_status(request: dict, home: Path) -> Dict:
    run_id, name = request.get("run_id"), request.get("name")
    if (not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id)
            or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", name)):
        raise RemoteError("Invalid fleet run identifier or sensor name.")
    state = read_state(home, run_id, name=name)
    if state["phase"] != "completed":
        raise RemoteError("Runtime health is checked only after a completed update.")
    expected = state["version"]
    installed = identity(home, name)
    env = EnvFile(str(home / "tpotce/.env"))
    configured = env.get("TPOT_VERSION") or ""
    problems = []
    if installed != expected:
        problems.append(f"Installed version is {installed or 'unknown'}, expected {expected}.")
    if configured != expected:
        problems.append(f".env TPOT_VERSION is {configured or 'unknown'}, expected {expected}.")

    service = command(["systemctl", "is-active", "tpot"])
    service_state = service.stdout.strip() or "unknown"
    if service.returncode or service_state != "active":
        problems.append(f"tpot.service is {service_state}.")

    checkout = home / "tpotce"
    config = command(["docker", "compose", "--project-directory", str(checkout),
                      "config", "--format", "json"])
    if config.returncode:
        raise RemoteError(f"docker compose config failed (exit {config.returncode}).")
    try:
        services = json.loads(config.stdout)["services"]
    except (KeyError, TypeError, ValueError) as err:
        raise RemoteError("docker compose returned an invalid service configuration.") from err
    if not isinstance(services, dict) or not services or "tpotinit" not in services:
        raise RemoteError("docker compose does not define the T-Pot services.")
    expected_services = set(services)
    images = {
        service_name: service_config.get("image")
        for service_name, service_config in services.items()
        if isinstance(service_config, dict)
    }
    if len(images) != len(services) or any(not isinstance(image, str) or not image
                                            for image in images.values()):
        raise RemoteError("docker compose returned an invalid service image.")
    expected_init_image = images["tpotinit"]
    if not expected_init_image.endswith(":" + expected):
        problems.append(f"Configured tpotinit image is not tagged {expected}.")

    listing = command(["docker", "compose", "--project-directory", str(checkout),
                       "ps", "--all", "--format", "json"])
    if listing.returncode:
        raise RemoteError(f"docker compose ps failed (exit {listing.returncode}).")
    containers = compose_rows(listing.stdout, "container")
    by_service: Dict[str, List[dict]] = {}
    for container in containers:
        service_name = container.get("Service")
        if not isinstance(service_name, str) or service_name not in expected_services:
            raise RemoteError("docker compose returned a container for an unknown service.")
        by_service.setdefault(service_name, []).append(container)
    for service_name in sorted(expected_services):
        found = by_service.get(service_name, [])
        if not found:
            problems.append(f"{service_name} has no container.")
            continue
        for container in found:
            container_state = container.get("State")
            health = container.get("Health") or ""
            if container_state != "running" or health not in ("", "healthy"):
                problems.append(
                    f"{service_name} is {container_state or 'unknown'}"
                    + (f" ({health})" if health else "") + ".")

    init_containers = by_service.get("tpotinit", [])
    for container in init_containers:
        name_or_id = container.get("Name") or container.get("ID")
        if not isinstance(name_or_id, str) or not name_or_id:
            raise RemoteError("docker compose omitted the tpotinit container identity.")
        inspected = command(["docker", "inspect", f"--format={{.Config.Image}}", name_or_id])
        if inspected.returncode:
            raise RemoteError(f"docker inspect tpotinit failed (exit {inspected.returncode}).")
        actual_image = inspected.stdout.strip()
        if actual_image != expected_init_image:
            problems.append(f"Running tpotinit image is {actual_image or 'unknown'}, expected {expected_init_image}.")

    return {
        "name": name,
        "ready": not problems,
        "version": installed,
        "service": service_state,
        "tpotinit_image": expected_init_image,
        "problems": problems,
        "containers": [
            {"service": service_name, "state": container.get("State", ""),
             "health": container.get("Health") or ""}
            for service_name in sorted(by_service)
            for container in by_service[service_name]
        ],
    }


def checked_archive(home: Path, value: str) -> Path:
    directory(home / "tpot_backups", private=True, create=False)
    archive = Path(value)
    if (not archive.is_absolute() or archive.parent != home / "tpot_backups"
            or ".." in archive.parts or not archive.name.endswith("_tpot_backup.tar")):
        raise RemoteError("The fleet backup archive path is invalid.")
    try:
        info = archive.lstat()
    except OSError as err:
        raise RemoteError("The fleet backup archive is missing or unreadable.") from err
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise RemoteError("The fleet backup archive is not a private regular file.")
    return archive


def rollback(request: dict, home: Path) -> Dict:
    run_id, name = request.get("run_id"), request.get("name")
    if (not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id)
            or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", name)):
        raise RemoteError("Invalid fleet run identifier or sensor name.")
    state = read_state(home, run_id, name=name)
    if state["phase"] not in ("completed", "failed", "rollback_failed") \
            or not isinstance(state.get("archive"), str):
        raise RemoteError("This fleet run has no completed update backup to restore.")
    archive = checked_archive(home, state["archive"])
    password = request.get("password", "")
    if (not isinstance(password, str) or any(char in password for char in "\r\n\0")
            or len(password.encode("utf-8")) > 65536):
        raise RemoteError("Invalid sudo input.")
    idle(home)
    sudo(password)
    work = run_path(home, run_id)
    write_private(work / "become", password + "\n")
    write_private(work / "rollback-archive", str(archive) + "\n")
    reset = command(["sudo", "-n", "systemctl", "reset-failed", "tpot-fleet-update.service"])
    if reset.returncode:
        remove_private_file(work / "become")
        raise RemoteError("Cannot reset the previous fleet worker unit.")
    state["phase"] = "rollback_running"
    state["unit"] = "tpot-fleet-update.service"
    state["started_at"] = int(time.time())
    state.pop("finished_at", None)
    state.pop("exit_code", None)
    state.pop("error", None)
    save(work / "state.json", state)
    args = [
        "sudo", "-S", "-p", "", "systemd-run", "--collect",
        "--unit=tpot-fleet-update.service", f"--uid={os.getuid()}",
        f"--property=WorkingDirectory={work}", "--property=Type=exec",
        f"--setenv=HOME={home}", "--setenv=PYTHONDONTWRITEBYTECODE=1",
        sys.executable, str(work / "fleet_remote.py"), "--worker", run_id, "rollback",
    ]
    result = command(args, password=password + "\n")
    if result.returncode:
        state.update(phase="rollback_failed", exit_code=1, finished_at=int(time.time()),
                     error="systemd-run could not start the rollback worker.")
        save(work / "state.json", state)
        remove_private_file(work / "become")
        raise RemoteError("systemd-run could not start the rollback worker.")
    return state


def discard(request: dict, home: Path) -> Dict:
    run_id, name = request.get("run_id"), request.get("name")
    if (not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id)
            or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", name)):
        raise RemoteError("Invalid fleet run identifier or sensor name.")
    root = home / ".local/share/tpotce/fleet"
    for parent in (home / ".local", home / ".local/share", home / ".local/share/tpotce", root):
        try:
            directory(parent, private=parent == root, create=False)
        except FileNotFoundError:
            return {"run_id": run_id, "discarded": True}
    work = root / run_id
    try:
        info = work.lstat()
    except FileNotFoundError:
        return {"run_id": run_id, "discarded": True}
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RemoteError("The fleet run directory is not private and owned.")
    entries = {item.name for item in work.iterdir()}
    allowed = {
        "state.json", "target", "source.git", "worker.log", "rollback.log", "backup-report.jsonl",
        "become", "accept-losses", "fleet_remote.py", "envfile.py", "rollback-archive",
    }
    if entries - allowed:
        raise RemoteError("The fleet run directory contains unexpected files.")
    state_path = work / "state.json"
    if state_path.exists() or state_path.is_symlink():
        state = read_state(home, run_id, name=name)
        if state["phase"] in ("running", "rollback_running"):
            raise RemoteError("A running fleet job cannot be discarded.")
    else:
        if entries - {"target"}:
            raise RemoteError("The incomplete fleet run contains unexpected files.")
    target = work / "target"
    if target.exists() or target.is_symlink():
        target_info = target.lstat()
        if (not stat.S_ISDIR(target_info.st_mode) or target_info.st_uid != os.geteuid()
                or stat.S_IMODE(target_info.st_mode) != 0o700):
            raise RemoteError("The staged fleet target is not private and owned.")
        if {item.name for item in target.iterdir()} - {"update.sh", "restore.sh", "version"}:
            raise RemoteError("The staged fleet target contains unexpected files.")
        for item in target.iterdir():
            item_info = item.lstat()
            if (not stat.S_ISREG(item_info.st_mode) or item_info.st_uid != os.geteuid()
                    or item_info.st_nlink != 1 or stat.S_IMODE(item_info.st_mode) != 0o600):
                raise RemoteError("A staged fleet target file is not private and owned.")
    for filename in entries - {"target", "source.git"}:
        item_info = (work / filename).lstat()
        if (not stat.S_ISREG(item_info.st_mode) or item_info.st_uid != os.geteuid()
                or item_info.st_nlink != 1 or stat.S_IMODE(item_info.st_mode) != 0o600):
            raise RemoteError(f"The fleet file {filename} is not private and owned.")
    source = work / "source.git"
    if source.exists() or source.is_symlink():
        source_info = source.lstat()
        if (not stat.S_ISDIR(source_info.st_mode) or source_info.st_uid != os.geteuid()
                or stat.S_IMODE(source_info.st_mode) & 0o022):
            raise RemoteError("The staged fleet Git source is not owned and safe.")
    idle(home)
    checked(["git", "-C", str(home / "tpotce"), "update-ref", "-d", "refs/tpot-fleet/" + run_id])
    shutil.rmtree(work)
    return {"run_id": run_id, "discarded": True}


def read_backup_report(path: Path, home: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
                raise RemoteError("The fleet backup report is not a private regular file.")
            data = stream.read(65537)
        if len(data) > 65536:
            raise RemoteError("The fleet backup report is too large.")
        archives = []
        for line in data.decode("utf-8").splitlines():
            record = json.loads(line)
            if not isinstance(record, dict) or set(record) != {"archive"} or not isinstance(record["archive"], str):
                raise ValueError("invalid report row")
            archives.append(str(checked_archive(home, record["archive"])))
        return archives[-1] if archives else ""
    except RemoteError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as err:
        raise RemoteError("The fleet backup report is missing, invalid or unsafe.") from err


def worker(home: Path, run_id: str, operation: str) -> int:
    if operation not in ("update", "rollback"):
        raise RemoteError("Unknown fleet worker operation.")
    state = read_state(home, run_id)
    expected_phase = "running" if operation == "update" else "rollback_running"
    if state["phase"] != expected_phase or state.get("unit") != "tpot-fleet-update.service":
        raise RemoteError("The fleet worker state is not ready to run.")
    work = run_path(home, run_id)
    log = work / ("worker.log" if operation == "update" else "rollback.log")
    log_fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    log_open = True
    report_fd = None
    try:
        if operation == "update":
            report = work / "backup-report.jsonl"
            write_private(report, "")
            report_fd = os.open(report, os.O_WRONLY | os.O_NOFOLLOW)
            if report_fd == 7:
                duplicate = fcntl.fcntl(report_fd, fcntl.F_DUPFD, 8)
                os.close(report_fd)
                report_fd = duplicate
            args = ["bash", str(work / "target/update.sh"), "-y", "-r", str(work / "source.git"),
                    "-b", state["branch"], "-B", str(work / "become")]
            if (work / "accept-losses").exists():
                args += ["--accept-data-loss-file", str(work / "accept-losses")]
            env = command_environment()
            env.update(TPOT_UPDATE_BACKUP_FD=str(report_fd), HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
            log_open = False
            with os.fdopen(log_fd, "ab") as output:
                result = subprocess.run(args, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=subprocess.STDOUT, pass_fds=(report_fd,))
            exit_code = result.returncode
            archive = read_backup_report(report, home)
            if exit_code == 0 and not archive:
                raise RemoteError("The updater succeeded without recording its backup archive.")
            if archive:
                state["archive"] = archive
            phase = "completed" if exit_code == 0 else "failed"
            if exit_code:
                state["error"] = "The target updater failed; inspect the worker log before continuing."
        else:
            archive = checked_archive(home, state.get("archive", ""))
            if read_private_text(work / "rollback-archive") != str(archive) + "\n":
                raise RemoteError("The rollback archive differs from the one explicitly selected.")
            restore = work / "target/restore.sh"
            info = restore.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise RemoteError("The staged restore script is not a private regular file.")
            log_open = False
            with os.fdopen(log_fd, "ab") as output:
                result = subprocess.run(
                    ["bash", str(restore), "-f", str(archive), "-c", "-B", str(work / "become")],
                    cwd=work, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
            exit_code = result.returncode
            if exit_code == 0:
                start_result = command(["sudo", "-S", "-p", "", "systemctl", "start", "tpot"],
                                       password=read_private_text(work / "become"), timeout=900)
                exit_code = start_result.returncode
            phase = "rolled_back" if exit_code == 0 else "rollback_failed"
            if exit_code:
                state["error"] = "The rollback failed; inspect the rollback log before continuing."
        state.update(phase=phase, exit_code=exit_code, finished_at=int(time.time()))
        forget_target_ref(home, state)
        save(work / "state.json", state)
        return exit_code
    except (OSError, RemoteError, subprocess.SubprocessError) as err:
        phase = "rollback_failed" if operation == "rollback" else "failed"
        state.update(phase=phase, exit_code=1, finished_at=int(time.time()),
                     error=str(err)[:4096])
        forget_target_ref(home, state)
        save(work / "state.json", state)
        return 1
    finally:
        if report_fd is not None:
            os.close(report_fd)
        if log_open:
            os.close(log_fd)
        remove_private_file(work / "become")
        remove_private_file(work / "accept-losses")


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
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        try:
            return worker(Path.home(), sys.argv[2], sys.argv[3])
        except (RemoteError, OSError, UnicodeError) as err:
            print(str(err), file=sys.stderr)
            return 1
    try:
        raw = sys.stdin.read(1_048_577)
        if len(raw) > 1_048_576:
            raise RemoteError("The fleet request is too large.")
        try:
            request = json.loads(raw)
        except ValueError as err:
            raise RemoteError("Invalid fleet request JSON.") from err
        if not isinstance(request, dict):
            raise RemoteError("Invalid fleet request.")
        action = request.get("action")
        if action == "prepare":
            result = prepare(request, Path.home())
        elif action == "start":
            result = start(request, Path.home())
        elif action == "status":
            result = status(Path.home(), request.get("run_id"), request.get("name"))
        elif action == "runtime":
            result = runtime_status(request, Path.home())
        elif action == "discard":
            result = discard(request, Path.home())
        elif action == "rollback":
            result = rollback(request, Path.home())
        else:
            raise RemoteError("Unknown fleet action.")
    except (RemoteError, OSError, UnicodeError) as err:
        print(json.dumps({"ok": False, "error": str(err)}))
        return 1
    print(json.dumps({"ok": True, "result": result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
