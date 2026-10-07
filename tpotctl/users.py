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
"""

import base64
import binascii
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

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

    def add(self, name: str, password: str) -> str:
        check_name(name)
        if self.find(name):
            raise UsersError(f"{name} exists already, change its password with: tpot users passwd {name}")
        entries = [u.entry for u in self.users()] + [encode(self.hasher(name, password, self.repo_dir))]
        return self._write(entries)

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

    def _write(self, entries: List[str]) -> str:
        value = " ".join(entries)
        # parse() applies the WEB_USER rules of env.schema.yml (base64 of name:htpasswd hash)
        if not any(u.ok for u in parse(value)):
            raise UsersError("no working web user would be left")
        try:
            self.env.set("WEB_USER", value)
            self.env.save()
        except EnvError as err:
            raise UsersError(str(err))
        try:
            # in place: the file is bind mounted on its own, see the module docstring
            with open(self.passwd_path, "w", encoding="utf-8") as handle:
                for user in parse(value):
                    if user.line:
                        handle.write(user.line + "\n")
            return "this counts right away"
        except OSError:
            return "it is used from the next start of T-Pot on (tpot restart)"


def load(repo_dir: str = REPO_DIR, hasher: Callable = hash_line) -> Store:
    try:
        env = EnvFile(os.path.join(repo_dir, ".env"))
    except OSError as err:
        raise UsersError(f"cannot read the .env of T-Pot: {err}")
    if env.get("TPOT_TYPE") == "SENSOR":
        raise UsersError("a SENSOR has no web UI and no web users")
    data = env.get("TPOT_DATA_PATH") or "./data"
    passwd = os.path.normpath(os.path.join(repo_dir, data, "nginx", "conf", "nginxpasswd"))
    return Store(repo_dir, env, passwd, hasher)
