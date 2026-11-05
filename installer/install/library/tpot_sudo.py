"""Privileged sudo-mode operations shared by the T-Pot Ansible frontends."""

import contextlib
import errno
import fcntl
import grp
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Iterable, Optional


MARKER = "# Managed by T-Pot: sudo mode\n"
RULE = "/etc/sudoers.d/tpot"
INSTALL_RULE = "/etc/sudoers.d/tpot-install"
LOCK = "/run/lock/tpot-sudo.lock"


class SudoError(RuntimeError):
    def __init__(self, message, changes=()):
        super().__init__(message)
        self.changes = list(changes)


@dataclass(frozen=True)
class Policy:
    alternative: Optional[bool]
    reason: str = ""


def rule(uid: int) -> str:
    if type(uid) is not int or uid <= 0:
        raise ValueError("the sudo rule needs a non-root numeric user ID")
    return f"{MARKER}#{uid} ALL=(ALL:ALL) NOPASSWD: ALL\n"


def owns_rule(text: str, uid: int) -> bool:
    return text == rule(uid)


def inspect_policy(listing: str, owned_paths: Iterable[str]) -> Policy:
    """Use evaluated grants, not group membership, to prove an independent root grant."""
    unknown = Policy(None, "The custom sudo policy cannot be verified automatically; check it with sudo -ll.")
    parts = re.split(r"(?m)^Sudoers entry:[ \t]*(.*)$", listing)
    if len(parts) == 1:
        if re.search(r"(?m)^User \S+ is not allowed to run sudo on .+\.$", listing):
            return Policy(False)
        return unknown
    if re.search(r"(?:^|[,\s])(?:rootpw|targetpw|runaspw|requiretty)(?:$|[,\s])", parts[0]):
        return unknown
    owned = {os.path.realpath(path) for path in owned_paths}
    allowed_options = {"authenticate", "!authenticate", "setenv", "!setenv",
                       "log_input", "!log_input", "log_output", "!log_output"}
    alternative = False
    for source, body in zip(parts[1::2], parts[2::2]):
        source = source.strip()
        if not re.fullmatch(r"/[A-Za-z0-9_./+ -]+", source):
            return unknown
        if os.path.realpath(source) in owned:
            continue
        fields = {}
        current = ""
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            match = re.fullmatch(r"(RunAsUsers|RunAsGroups|Options|Commands):[ \t]*(.*)", line)
            if match:
                current, value = match.groups()
                if current in fields:
                    return unknown
                fields[current] = [value] if value else []
            elif current and not re.match(r"[A-Za-z]+:", line):
                fields[current].append(line)
            else:
                return unknown
        if not fields.get("RunAsUsers") or not fields.get("Commands"):
            return unknown
        users = {item.strip() for item in " ".join(fields["RunAsUsers"]).split(",")}
        groups = {item.strip() for item in " ".join(fields.get("RunAsGroups", ["root"])).split(",")}
        options = {item.strip() for item in " ".join(fields.get("Options", [])).split(",") if item.strip()}
        commands = fields["Commands"]
        if (any(item.startswith("!") for item in users | groups) or
                any(command.startswith("!") for command in commands) or
                not options <= allowed_options):
            return unknown
        if users & {"ALL", "root", "#0"} and groups & {"ALL", "root", "#0"} and commands == ["ALL"]:
            alternative = True
    return Policy(alternative)


def _run(args, *, input=None, check=True, private=False):
    try:
        proc = subprocess.run(args, input=input, stdin=subprocess.DEVNULL if input is None else None,
                              capture_output=True, text=True, timeout=30, env=dict(os.environ, LC_ALL="C"))
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as err:
        raise SudoError(f"{args[0]} could not run ({type(err).__name__}).") from err
    if check and proc.returncode:
        detail = "" if private else ": " + proc.stderr.strip()
        raise SudoError(f"{args[0]} failed (exit {proc.returncode}){detail}")
    return proc


@contextlib.contextmanager
def _lock():
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_nlink != 1 or info.st_mode & 0o077:
            raise SudoError("The sudo-mode lock is not a private root-owned regular file.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            raise SudoError("Another sudo-mode change is running; try again after it finishes.") from err
        yield
    finally:
        os.close(fd)


def _file_state(path, uid):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return "absent"
    except OSError as err:
        if err.errno == errno.ELOOP:
            return "foreign"
        raise
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_gid or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o440):
            return "foreign"
        return "owned" if os.read(fd, 8193) == rule(uid).encode("ascii") else "foreign"
    finally:
        os.close(fd)


def _sudo():
    sudo = shutil.which("sudo")
    if not sudo:
        raise SudoError("Install sudo before changing the sudo mode.")
    version = _run([sudo, "--version"])
    if "sudo-rs" in version.stdout.lower():
        sudo = shutil.which("sudo.ws")
        if not sudo:
            raise SudoError("The traditional sudo (sudo.ws) is needed to verify the sudo policy.")
    return sudo


def _snapshot(user, uid, sudo):
    own = _file_state(RULE, uid)
    temporary = _file_state(INSTALL_RULE, uid)
    owned = [path for path, state in ((RULE, own), (INSTALL_RULE, temporary)) if state == "owned"]
    listing = _run([sudo, "-ll", "-U", user], check=False)
    policy = inspect_policy(listing.stdout, owned)
    if listing.stderr or listing.returncode not in (0, 1):
        policy = Policy(None, "sudo could not verify its policy: " + listing.stderr.strip())
    password = _run(["passwd", "-S", "--", user]).stdout.split()
    if len(password) < 2 or password[0] != user or password[1] not in ("P", "L", "NP"):
        raise SudoError("passwd -S returned an unrecognized account status.")
    probe = _run(["runuser", "-u", user, "--", sudo, "-n", "-k", "-p", "", "--", "/usr/bin/true"], check=False)
    required = False if probe.returncode == 0 else True if "a password is required" in probe.stderr else None
    warnings = [policy.reason] if policy.reason else []
    if own == "foreign" or temporary == "foreign":
        warnings.append("A foreign or modified file occupies a T-Pot sudoers path; it will not be changed.")
    if temporary == "owned":
        warnings.append("The temporary installer grant remains active until the installer exits.")
    if required is None:
        warnings.append("The sudo access probe could not be verified: " + probe.stderr.strip())
    elif required is False and own != "owned" and temporary != "owned":
        warnings.append("A foreign sudo policy still allows sudo without a password.")
    return dict(user=user, uid=uid, own_rule=own, temporary_rule=temporary,
                password_usable=password[1] == "P", alternative=policy.alternative,
                password_required=required, warnings=warnings, changed=False, changes=[])


def _password(value):
    if not value or any(char in value for char in "\r\n\0"):
        raise SudoError("Provide a nonempty, single-line user password.")
    check = _run(["cracklib-check"], input=value + "\n", private=True)
    if not check.stdout.endswith(": OK\n"):
        raise SudoError("The user password does not pass cracklib; choose a stronger password.")


def _ensure_alternative(user, uid, sudo, changes):
    added = []
    try:
        for name in ("sudo", "wheel"):
            try:
                group = grp.getgrnam(name)
            except KeyError:
                continue
            if group.gr_gid in os.getgrouplist(user, pwd.getpwnam(user).pw_gid):
                continue
            _run(["usermod", "-a", "-G", name, "--", user])
            added.append(name)
            if _snapshot(user, uid, sudo)["alternative"] is True:
                changes.extend(f"Added {user} to {group_name}." for group_name in added)
                return
        raise SudoError("No verified alternative sudo grant is available; the own rule was kept.")
    except (SudoError, OSError):
        for name in reversed(added):
            _run(["gpasswd", "-d", user, name])
        raise


def _set_rule(user, uid, sudo, enable):
    parent = os.path.dirname(RULE)
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ""
    backup = ""
    try:
        info = os.fstat(directory)
        if info.st_uid or info.st_mode & 0o022:
            raise SudoError("The sudoers directory must be root-owned and not writable by other users.")
        previous = _file_state(RULE, uid)
        if previous == "foreign":
            raise SudoError("The foreign file at the T-Pot sudoers path will not be changed.")
        if (enable and previous == "owned") or (not enable and previous == "absent"):
            return False
        fd, temporary = tempfile.mkstemp(prefix=".tpot-", dir=parent)
        with os.fdopen(fd, "wb") as out:
            os.fchown(out.fileno(), 0, 0)
            os.fchmod(out.fileno(), 0o440)
            out.write(rule(uid).encode("ascii"))
            out.flush()
            os.fsync(out.fileno())
        _run(["visudo", "-c", "-f", temporary])
        if _file_state(RULE, uid) != previous:
            raise SudoError("The T-Pot sudoers file changed during validation; try again.")
        # Finish validation or rollback before delivering an interruption.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM, signal.SIGHUP})
        try:
            if enable:
                os.replace(temporary, RULE)
                temporary = ""
            else:
                os.replace(RULE, temporary)
                backup, temporary = temporary, ""
            try:
                os.fsync(directory)
                _run(["visudo", "-c"])
                after = _snapshot(user, uid, sudo)
                if (enable and after["password_required"] is not False) or (
                    not enable and (after["alternative"] is not True or after["password_required"] is None)
                ):
                    raise SudoError("The resulting sudo access could not be verified; the previous rule was restored.")
            except (SudoError, OSError):
                if enable:
                    os.unlink(RULE)
                else:
                    os.replace(backup, RULE)
                    backup = ""
                os.fsync(directory)
                raise
            if backup:
                os.unlink(backup)
                backup = ""
            os.fsync(directory)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        return True
    finally:
        if temporary:
            os.unlink(temporary)
        os.close(directory)


def _normal_uid(uid):
    try:
        with open("/etc/login.defs", encoding="utf-8") as stream:
            match = re.search(r"(?m)^[ \t]*UID_MIN[ \t]+([0-9]+)[ \t]*(?:#.*)?$", stream.read())
    except (OSError, UnicodeError) as err:
        raise SudoError("Cannot verify the normal user range from /etc/login.defs.") from err
    if not match or int(match.group(1)) <= 0:
        raise SudoError("Cannot verify UID_MIN in /etc/login.defs.")
    return uid >= int(match.group(1))


def apply(user, action, *, password="", keep=False):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SudoError("Sudo-mode management must run with become on a Linux host.")
    if action not in ("status", "secure", "convenient", "safe_remove"):
        raise SudoError("Unknown sudo-mode action.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*[$]?", user) or user == "tpot":
        raise SudoError("Choose an existing normal user, not root or tpot.")
    try:
        account = pwd.getpwnam(user)
    except KeyError as err:
        raise SudoError("Choose an existing normal user.") from err
    if (account.pw_uid == 0 or not _normal_uid(account.pw_uid)
            or account.pw_shell.rsplit("/", 1)[-1] in ("false", "nologin", "")):
        raise SudoError("Choose a normal login user, not root or a service account.")
    changes = []
    try:
        with _lock():
            sudo = _sudo()
            before = _snapshot(user, account.pw_uid, sudo)
            if action == "status":
                return before
            if action == "safe_remove" and (keep or before["own_rule"] != "owned"
                                            or not before["password_usable"] or before["alternative"] is not True):
                if before["own_rule"] == "owned":
                    before["warnings"].append("The T-Pot sudo rule was kept " + (
                        "as requested." if keep else "to avoid losing sudo access; verify a password and another sudo grant first."
                    ))
                return before
            if "foreign" in (before["own_rule"], before["temporary_rule"]):
                raise SudoError("The foreign file at a T-Pot sudoers path will not be changed.")
            _run(["visudo", "-c"])
            if action == "secure":
                if before["alternative"] is None:
                    raise SudoError("The custom sudo policy is unverified; the own rule was kept.")
                if not before["password_usable"]:
                    if not password:
                        raise SudoError("A usable user password is required; supply --user-password-file before changing anything.")
                    _password(password)
                    _run(["chpasswd"], input=f"{user}:{password}\n", private=True)
                    changes.append("Set the user password (also used by SSH when password login is enabled).")
                if before["alternative"] is not True:
                    _ensure_alternative(user, account.pw_uid, sudo, changes)
            if _set_rule(user, account.pw_uid, sudo, action == "convenient"):
                changes.append("Installed the T-Pot sudo rule." if action == "convenient" else "Removed the T-Pot sudo rule.")
            result = _snapshot(user, account.pw_uid, sudo)
            result.update(changes=changes, changed=bool(changes))
            return result
    except SudoError as err:
        err.changes = changes
        raise
    except OSError as err:
        raise SudoError(f"Sudo-mode file operation failed: {err}", changes) from err


def main():
    from ansible.module_utils.basic import AnsibleModule

    module = AnsibleModule(argument_spec=dict(
        user=dict(type="str", required=True),
        action=dict(type="str", choices=["status", "secure", "convenient", "safe_remove"], default="status"),
        password=dict(type="str", no_log=True),
        keep=dict(type="bool", default=False),
    ), supports_check_mode=True)
    try:
        password = module.params["password"] or ""
        if password.endswith("\n"):
            password = password[:-1].removesuffix("\r")
        if ((module.params["password"] is not None and not password)
                or any(char in password for char in "\r\n\0")):
            raise SudoError("The password file must contain exactly one nonempty line.")
        result = apply(module.params["user"], "status" if module.check_mode else module.params["action"],
                       password=password, keep=module.params["keep"])
    except SudoError as err:
        module.fail_json(msg=str(err), changed=bool(err.changes), changes=err.changes)
    module.exit_json(changed=result["changed"], status=result)


if __name__ == "__main__":
    main()
