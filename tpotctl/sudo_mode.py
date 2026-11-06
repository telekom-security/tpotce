"""Controller-side transport for the shared sudo-mode Ansible play."""

import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTIONS = ("status", "secure", "convenient", "safe_remove")


class SudoError(RuntimeError):
    pass


@dataclass
class ModeStatus:
    user: str = ""
    uid: Optional[int] = None
    own_rule: str = "unknown"
    temporary_rule: str = "unknown"
    password_usable: Optional[bool] = None
    alternative: Optional[bool] = None
    password_required: Optional[bool] = None
    changed: bool = False
    changes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: str = ""


def read_password_file(path: str) -> str:
    """Read one private UTF-8 password, preserving spaces and an optional final newline."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)):
                raise SudoError(f"Use an owned regular password file, readable only by you: chmod 600 {path}")
            value = stream.read(65537)
        if len(value) > 65536:
            raise SudoError("The password file is too large.")
        password = value.decode("utf-8")
    except (OSError, UnicodeError) as err:
        raise SudoError(f"Cannot read the private password file {path}; use a regular UTF-8 file with chmod 600.") from err
    if password.endswith("\n"):
        password = password[:-1].removesuffix("\r")
    if not password or any(char in password for char in "\r\n\0"):
        raise SudoError("The password file must contain exactly one nonempty line.")
    return password


def command(action: str, inventory: str, *, become_file: str = "", user_password_file: str = "",
            result_file: str = "", repo_dir: str = REPO_DIR, keep: bool = False) -> List[str]:
    if action not in ACTIONS:
        raise SudoError("Unknown sudo-mode action.")
    for path in (become_file, user_password_file):
        if path:
            read_password_file(path)
    variables = dict(tpot_sudo_action=action, tpot_keep_sudo_rule=keep)
    if user_password_file:
        variables["tpot_user_password_file"] = os.path.abspath(user_password_file)
    if result_file:
        variables["tpot_sudo_result_file"] = os.path.abspath(result_file)
    if become_file:
        variables["tpot_become_password_file"] = os.path.abspath(become_file)
        # Ansible's CLI strips all whitespace from a password file, not just its newline.
        variables["ansible_become_password"] = (
            "{{ lookup('file', tpot_become_password_file, lstrip=false, rstrip=false)"
            " | regex_replace('\\r?\\n$', '') }}"
        )
    args = ["ansible-playbook", os.path.join(repo_dir, "installer", "install", "sudo_mode.yml"),
            "-i", inventory, "-e", json.dumps(variables)]
    return args


def stage_uninstall(directory: str, *, repo_dir: str = REPO_DIR, become_file: str = "", keep: bool = False) -> None:
    """Keep the shared play and credentials available after uninstall removes the checkout."""
    password = read_password_file(become_file) if become_file else ""
    if password:
        become_file = os.path.join(directory, "become")
        with open(become_file, "x", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(password + "\n")
    os.mkdir(os.path.join(directory, "library"), 0o700)
    for name in ("sudo_mode.yml", "sudo_reconnect.yml", "library/tpot_sudo.py"):
        with open(os.path.join(repo_dir, "installer", "install", name), "rb") as source:
            with open(os.path.join(directory, name), "xb") as target:
                target.write(source.read())
    variables = json.loads(command("safe_remove", "127.0.0.1,", become_file=become_file,
                                   repo_dir=repo_dir, keep=keep)[-1])
    variables["tpot_cleanup_install"] = True
    with open(os.path.join(directory, "variables.json"), "x", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(variables, stream)


def _status(record: dict) -> ModeStatus:
    error = record.get("error")
    if not isinstance(error, str):
        raise SudoError("Malformed sudo-mode error.")
    if error:
        changes = record.get("changes", [])
        if not isinstance(changes, list) or any(not isinstance(item, str) for item in changes):
            raise SudoError("Malformed sudo-mode changes.")
        return ModeStatus(error=error, changes=changes, changed=bool(changes))
    result = record.get("result")
    if not isinstance(result, dict):
        raise SudoError("Missing sudo-mode status.")
    if (not isinstance(result.get("user"), str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*[$]?", result["user"])
            or type(result.get("uid")) is not int or result["uid"] <= 0):
        raise SudoError("Malformed sudo-mode user.")
    for key in ("own_rule", "temporary_rule"):
        if result.get(key) not in ("owned", "absent", "foreign"):
            raise SudoError("Malformed sudo-mode rule status.")
    for key in ("password_required", "alternative"):
        if key not in result or (result[key] is not None and type(result[key]) is not bool):
            raise SudoError("Malformed sudo-mode access status.")
    for key in ("password_usable", "changed"):
        if type(result.get(key)) is not bool:
            raise SudoError("Malformed sudo-mode account status.")
    for key in ("changes", "warnings"):
        if not isinstance(result.get(key), list) or any(not isinstance(item, str) for item in result[key]):
            raise SudoError("Malformed sudo-mode messages.")
    return ModeStatus(**{key: result[key] for key in ModeStatus.__dataclass_fields__ if key != "error"})


def read_results(path: str) -> Dict[str, ModeStatus]:
    results: Dict[str, ModeStatus] = {}
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                if (not isinstance(record, dict) or type(record.get("schema")) is not int or record["schema"] != 1
                        or not isinstance(record.get("host"), str)
                        or not re.fullmatch(r"[A-Za-z0-9_.:-]+", record["host"])):
                    raise SudoError("Malformed sudo-mode result record.")
                results[record["host"]] = _status(record)
    except (OSError, UnicodeError, ValueError) as err:
        raise SudoError(f"Cannot read sudo-mode results from {path}.") from err
    if not results:
        raise SudoError("No sudo-mode result was returned.")
    return results


def execute(action: str, inventory: str, hosts: List[str], *, local: bool = False,
            become_file: str = "", user_password_file: str = "", repo_dir: str = REPO_DIR,
            invoke: Optional[Callable] = None) -> Tuple[int, Dict[str, ModeStatus]]:
    from tpotctl import bootstrap
    invoke = invoke or bootstrap.run_child
    with tempfile.TemporaryDirectory(prefix="tpot-sudo-") as directory:
        path = os.path.join(directory, "results")
        args = command(action, inventory, become_file=become_file, user_password_file=user_password_file,
                       result_file=path, repo_dir=repo_dir)
        if local:
            args += ["-c", "local"]
        try:
            code = invoke(args, stdin=subprocess.DEVNULL, cwd=repo_dir)
            try:
                size = os.path.getsize(path)
            except FileNotFoundError:
                size = 0
            results = read_results(path) if size else {}
        except OSError as err:
            raise SudoError(f"Cannot run the sudo-mode play: {err}") from err
        if set(results) - set(hosts):
            raise SudoError("The sudo-mode play returned an unexpected host.")
        for host in hosts:
            if host not in results:
                results[host] = ModeStatus(error="No sudo result. Check the SSH connection and sudo access "
                                                "in the Ansible output.")
        if code == 130:
            return 130, results
        return int(bool(code) or any(status.error for status in results.values())), results


def summary(status: ModeStatus) -> str:
    if status.error:
        return status.error
    if status.password_required is None:
        return "Sudo password requirement unknown"
    return "Sudo password required" if status.password_required else "Sudo password not required"
