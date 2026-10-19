"""Users of the T-Pot web UI: WEB_USER in .env and the nginxpasswd nginx reads.

WEB_USER holds base64 of "name:hash" per user, separated by spaces; tpotinit builds
data/nginx/conf/nginxpasswd from it on every start. A change here writes both, so it
counts at once: nginx reads the file on every request. The file is bind mounted on
its own into the nginx container, so it is rewritten in place - a new file (rename)
would not be seen by nginx until a restart.

New and changed passwords are bcrypt. With the cost htpasswd uses by default (5):
nginx checks the password on every request, Kibana alone makes dozens per page, a
high cost would slow the web UI down noticeably (Raspberry Pi). Entries of other
schemes stay as they are until their password is changed.

The .env names who has access. A user of nginxpasswd that WEB_USER does not name (a .env
without the key, an entry removed by hand) is kept by tpotinit in nginxpasswd.lost when it
writes the file anew; file_only() offers the users of both files, take_over() puts them into
WEB_USER (tpot users import). A change refuses while the live file holds users WEB_USER does
not name: writing it would drop them.
"""

import base64
import binascii
import errno
import os
import tempfile
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

from tpotctl import ops
from tpotctl.bootstrap import REPO_DIR
from tpotctl.envfile import EnvError, EnvFile

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
MIN_LENGTH = 12
SCHEMES = [("$2y$", "bcrypt"), ("$2b$", "bcrypt"), ("$2a$", "bcrypt"), ("$apr1$", "apr1 (MD5)"),
           ("$6$", "SHA-512 crypt"), ("$5$", "SHA-256 crypt"), ("{SHA}", "SHA-1")]
_BASE64 = re.compile(r"^([A-Za-z0-9+/]{4})*([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")


class UsersError(Exception):
    """A change of the web users that is not made."""


@dataclass
class WebUser:
    name: str           # "entry 3" for an entry that cannot be read
    scheme: str         # bcrypt, apr1 (MD5), ... or "" if unknown
    entry: str          # as in WEB_USER
    line: str           # name:hash, decoded
    problem: str = ""   # why tpotinit would not start with it

    @property
    def ok(self) -> bool:
        return not self.problem


def parse(value: str) -> List[WebUser]:
    users = []
    for number, entry in enumerate(value.split(), 1):
        if not _BASE64.match(entry):
            users.append(WebUser(f"entry {number}", "", entry, "", "not base64"))
            continue
        try:
            line = base64.b64decode(entry).decode("utf-8", "replace").replace("\n", "")
        except (binascii.Error, ValueError):
            users.append(WebUser(f"entry {number}", "", entry, "", "not base64"))
            continue
        name, sep, secret = line.partition(":")
        if not sep or not name or re.search(r"\s", name):
            users.append(WebUser(f"entry {number}", "", entry, line, "not name:hash"))
            continue
        scheme = next((label for prefix, label in SCHEMES if secret.startswith(prefix)), "")
        problem = "" if scheme else "not a htpasswd hash T-Pot accepts (bcrypt, apr1, SHA crypt, SHA)"
        users.append(WebUser(name, scheme, entry, line, problem))
    return users


def encode(line: str) -> str:
    return base64.b64encode(line.encode("utf-8")).decode("ascii")


@dataclass
class FileEntry:
    """A user of a passwd file (or its .lost) that the .env does not name."""
    name: str
    entry: str          # encode(line), as the .env holds it
    line: str           # name:hash
    source: str         # the file it comes from: nginxpasswd, nginxpasswd.lost, ...


def _lines(path: str, strict: bool = False) -> List[str]:
    """The lines of a passwd file, [] if it is not there; strict: OSError for one that cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return [line.rstrip("\r\n") for line in handle]
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        return []


def read_problems(path: str) -> List[str]:
    """Why the passwd file at path or its .lost cannot be read (not there is fine), one text each."""
    problems = []
    for name in (path, path + ".lost"):
        try:
            _lines(name, strict=True)
        except OSError as err:
            hint = ", log in again for the group tpot" if getattr(err, "errno", None) == errno.EACCES else ""
            problems.append(f"cannot read {name}: {err.strerror or err}{hint}")
    return problems


def _usable(line: str) -> Optional[WebUser]:
    """The user of a line T-Pot would start with, None for anything else (empty, no name:hash, a hash
    T-Pot does not take)."""
    if not line.strip():
        return None
    user = parse(encode(line))[0]
    return user if user.ok else None


def file_only(path: str, known: Iterable[str], lost: bool = True) -> List[FileEntry]:
    """The users of the passwd file at path (and of path.lost) whose name is not in known, each once:
    the live file wins over .lost; in the live file the first line counts (as for nginx), in .lost the
    last one (the newest)."""
    known = set(known)
    live: Dict[str, FileEntry] = {}
    for line in _lines(path):
        user = _usable(line)
        if user and user.name not in known and user.name not in live:
            live[user.name] = FileEntry(user.name, encode(user.line), user.line, os.path.basename(path))
    kept: Dict[str, FileEntry] = {}
    if lost:
        for line in _lines(path + ".lost"):
            user = _usable(line)
            if user and user.name not in known and user.name not in live:
                kept.pop(user.name, None)
                kept[user.name] = FileEntry(user.name, encode(user.line), user.line,
                                            os.path.basename(path) + ".lost")
    return list(live.values()) + list(kept.values())


def foreign_lines(path: str, known: Iterable[str]) -> List[str]:
    """Every line of the passwd file at path whose user is not in known, as tpotinit keeps them."""
    known = set(known)
    return [line for line in _lines(path) if line.strip() and line.partition(":")[0] not in known]


def keep_lines(path: str, lines: List[str]) -> int:
    """Append the lines to path.lost as tpotinit does (one that is there already is not added again);
    how many went in. OSError if it cannot: .lost is a link or no regular file, or the write fails."""
    lost = path + ".lost"
    if not lines:
        return 0
    if os.path.islink(lost):
        raise OSError(errno.ELOOP, "it is a link", lost)
    if os.path.exists(lost) and not os.path.isfile(lost):
        raise OSError(errno.EISDIR, "it is not a regular file", lost)
    there = set(_lines(lost, strict=True))
    new = [line for line in dict.fromkeys(lines) if line not in there]
    if new:
        created = not os.path.exists(lost)
        with open(lost, "a", encoding="utf-8") as handle:
            handle.write("".join(line + "\n" for line in new))
        if created:
            os.chmod(lost, 0o660)
    return len(new)


def drop_lost(path: str, names: Iterable[str]) -> None:
    """Remove the lines of these users from path.lost; nothing if it is not there. .lost is not bind
    mounted, so it is replaced as a whole: a new file in the same folder (mode 660, the group of the
    folder), renamed over it."""
    lost = path + ".lost"
    names = set(names)
    if not os.path.isfile(lost) or os.path.islink(lost):
        return
    lines = _lines(lost, strict=True)
    rest = [line for line in lines if line.partition(":")[0] not in names]
    if rest == lines:
        return
    folder = os.path.dirname(lost) or "."
    handle, temp = tempfile.mkstemp(prefix=".nginx-lost-", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write("".join(line + "\n" for line in rest))
        os.chmod(temp, 0o660)
        try:
            os.chown(temp, -1, os.stat(folder).st_gid)
        except OSError:
            pass
        os.replace(temp, lost)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def keep_or_refuse(path: str, lines: List[str], error: Callable[[str], Exception]) -> int:
    """keep_lines, and error(text) when it cannot: then nothing is written, the file stays as it is."""
    try:
        return keep_lines(path, lines)
    except OSError as err:
        name = os.path.basename(path)
        raise error(f"{len(lines)} line(s) of {name} cannot be kept in {path}.lost ({err.strerror or err}), "
                    f"{name} stays as it is")


def refusal(path: str, known: Iterable[str], kind: str, command: str) -> str:
    """Why a change of the passwd file at path is not made: it holds users the .env does not name,
    writing it would drop them; "" if it holds none."""
    foreign = [e.name for e in file_only(path, known, lost=False)]
    if not foreign:
        return ""
    return (f"{os.path.basename(path)} holds {len(foreign)} {kind} user(s) the .env does not name "
            f"({', '.join(foreign)}); take them over first: {command}")


def check_name(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise UsersError("a user name may only have letters, digits, '_', '.' and '-'")


def _find(tool: str) -> Optional[str]:
    for path in (shutil.which(tool), f"/usr/sbin/{tool}", f"/usr/bin/{tool}"):
        if path and os.access(path, os.X_OK):
            return path
    return None


def weakness(password: str, run: Callable = subprocess.run) -> Optional[str]:
    """Why a password is weak, None if it is fine: cracklib, else a minimum length."""
    if not password:
        return "it is empty"
    cracklib = _find("cracklib-check")
    if cracklib:
        proc = run([cracklib], input=password + "\n", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                   universal_newlines=True)
        # "<password>: OK" or "<password>: <reason>", the password itself is not shown again
        verdict = (proc.stdout or "").strip().rsplit(": ", 1)[-1]
        return None if verdict == "OK" else verdict or "cracklib rejects it"
    if len(password) < MIN_LENGTH:
        return f"it is shorter than {MIN_LENGTH} characters"
    return None


def hash_line(name: str, password: str, repo_dir: str = REPO_DIR, run: Callable = subprocess.run) -> str:
    """name:bcrypt-hash; the password goes to htpasswd on stdin, never on its command line."""
    check_name(name)
    htpasswd = _find("htpasswd")
    if htpasswd:
        command = [htpasswd, "-n", "-i", "-B", name]
    elif shutil.which("docker"):
        env = ops.env_values(repo_dir)
        # the image this installation pulls (.env), else the one of the checkout's version
        tag = env.get("TPOT_VERSION") or ops.tpot_version(repo_dir)
        image = f"{env.get('TPOT_REPO', 'ghcr.io/telekom-security')}/tpotinit" + (f":{tag}" if tag else "")
        command = ["docker", "run", "--rm", "-i", "--entrypoint", "htpasswd", image, "-n", "-i", "-B", name]
    else:
        raise UsersError("htpasswd is missing (apache2-utils / httpd-tools), and there is no docker to run it in")
    proc = run(command, input=password + "\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               universal_newlines=True)
    line = (proc.stdout or "").strip().split("\n")[0].strip()
    if proc.returncode != 0 or not line.startswith(f"{name}:$2"):
        raise UsersError(f"htpasswd failed: {(proc.stderr or '').strip() or 'no bcrypt hash'}")
    return line


@dataclass
class Store:
    repo_dir: str
    env: EnvFile
    passwd_path: str
    hasher: Callable = hash_line

    def users(self) -> List[WebUser]:
        return parse(self.env.get("WEB_USER") or "")

    def find(self, name: str) -> Optional[WebUser]:
        return next((u for u in self.users() if u.name == name), None)

    def file_only(self) -> List[FileEntry]:
        """The users of nginxpasswd and nginxpasswd.lost that WEB_USER does not name."""
        return file_only(self.passwd_path, {u.name for u in self.users()})

    def read_problems(self) -> List[str]:
        return read_problems(self.passwd_path)

    def check_writable(self) -> None:
        """UsersError while nginxpasswd holds users WEB_USER does not name: a change would drop them."""
        why = refusal(self.passwd_path, {u.name for u in self.users()}, "web", "tpot users import")
        if why:
            raise UsersError(why)

    def take_over(self, names: Iterable[str]) -> str:
        """Put these users of file_only() into WEB_USER (and nginxpasswd), their lines leave .lost."""
        names = list(dict.fromkeys(names))
        offered = {e.name: e for e in self.file_only()}
        missing = [n for n in names if n not in offered]
        if missing or not names:
            raise UsersError(f"{', '.join(missing) or 'nothing'} cannot be taken over, see tpot users import")
        note = self._write([u.entry for u in self.users()] + [offered[n].entry for n in names])
        try:
            drop_lost(self.passwd_path, names)
        except OSError as err:
            note += f" ({os.path.basename(self.passwd_path)}.lost keeps them: {err.strerror or err})"
        return note

    def add(self, name: str, password: str, keep: bool = False) -> str:
        """keep: the users nginxpasswd holds and WEB_USER does not name go into nginxpasswd.lost, as
        tpotinit does, instead of a refusal (a SENSOR that becomes a HIVE: its file is not served)."""
        check_name(name)
        if self.find(name):
            raise UsersError(f"{name} exists already, change its password with: tpot users passwd {name}")
        entries = [u.entry for u in self.users()] + [encode(self.hasher(name, password, self.repo_dir))]
        return self._write(entries, keep=keep)

    def passwd(self, name: str, password: str) -> str:
        user = self.find(name)
        if user is None:
            raise UsersError(f"there is no user {name}")
        if not NAME_RE.match(name):
            raise UsersError(f"{name} cannot be read, remove it with: tpot users remove '{name}'")
        new = encode(self.hasher(name, password, self.repo_dir))
        return self._write([new if u.name == name else u.entry for u in self.users()])

    def remove(self, name: str) -> str:
        users = self.users()
        if not any(u.name == name for u in users):
            raise UsersError(f"there is no user {name}")
        rest = [u for u in users if u.name != name]
        if not any(u.ok for u in rest):
            raise UsersError("this is the last working user, without one the web UI cannot be used and a HIVE "
                             "does not start; add another user first")
        return self._write([u.entry for u in rest])

    def _write(self, entries: List[str], keep: bool = False) -> str:
        value = " ".join(entries)
        # parse() applies the WEB_USER rules of env.schema.yml (base64 of name:htpasswd hash)
        if not any(u.ok for u in parse(value)):
            raise UsersError("no working web user would be left")
        names = {u.name for u in self.users()} | {u.name for u in parse(value)}
        if not keep:
            why = refusal(self.passwd_path, names, "web", "tpot users import")
            if why:
                raise UsersError(why)
        # what the rewrite drops and nobody takes over goes into .lost first, as tpotinit does: with
        # keep every line of a name the new value lacks, else the lines T-Pot does not accept (the
        # others were refused above)
        dropped = foreign_lines(self.passwd_path, {u.name for u in parse(value)} if keep else names)
        kept = keep_or_refuse(self.passwd_path, dropped, UsersError)
        try:
            self.env.set("WEB_USER", value)
            self.env.save()
        except EnvError as err:
            raise UsersError(str(err))
        also = f", {kept} line(s) of nginxpasswd are kept in nginxpasswd.lost" if kept else ""
        try:
            # in place: the file is bind mounted on its own, see the module docstring
            with open(self.passwd_path, "w", encoding="utf-8") as handle:
                for user in parse(value):
                    if user.line:
                        handle.write(user.line + "\n")
            return "this counts right away" + also
        except OSError:
            return "it is used from the next start of T-Pot on (tpot restart)" + also


def passwd_path(repo_dir: str, env: EnvFile) -> str:
    data = env.get("TPOT_DATA_PATH") or "./data"
    return os.path.normpath(os.path.join(repo_dir, data, "nginx", "conf", "nginxpasswd"))


def load(repo_dir: str = REPO_DIR, hasher: Callable = hash_line) -> Store:
    try:
        env = EnvFile(os.path.join(repo_dir, ".env"))
    except OSError as err:
        raise UsersError(f"cannot read the .env of T-Pot: {err}")
    if env.get("TPOT_TYPE") == "SENSOR":
        raise UsersError("a SENSOR has no web UI and no web users")
    return Store(repo_dir, env, passwd_path(repo_dir, env), hasher)
