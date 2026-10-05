"""The T-Pot installer assistant (tpot install), no user interface.

install.sh stays the engine: the assistant checks the host, collects the answers
and runs `install.sh -s -M ...` with them, reading the progress marks it prints
(@@tpot phase|tasks|images ...) and the TASK lines of Ansible. Passwords go into
files only this user can read (-P, -B), never into the argv, and are removed when
the assistant ends.
"""

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from tpotctl import runlog
from tpotctl.bootstrap import REPO_DIR

INSTALL_SH = os.path.join(REPO_DIR, "install.sh")
UNINSTALL_SH = os.path.join(REPO_DIR, "uninstall.sh")
SERVICE_FILE = "/etc/systemd/system/tpot.service"

# keep in sync with mySUPPORTED_DISTRIBUTIONS / mySUPPORTED_VERSION of install.sh
# (tpotctl/tests/test_installer.py compares them); "" is a rolling release
SUPPORTED = {
    "AlmaLinux": "10",
    "Debian GNU/Linux": "13",
    "Fedora Linux": "44",
    "openSUSE Tumbleweed": "",
    "Raspbian GNU/Linux": "13",
    "Red Hat Enterprise Linux": "10",
    "Rocky Linux": "10",
    "Ubuntu": "26.04",
}
# majors only, Ubuntu with its minor
FULL_VERSION = {"Ubuntu"}

# ports a distribution service is likely to hold, as in install.sh
CONFLICT_PORTS = [("tcp", 25), ("tcp", 53), ("udp", 53)]
# the resolved stub listener, the playbook turns it off
STUB_ADDRESSES = ("127.0.0.53", "127.0.0.54")

REACH = [
    ("GitHub", "https://github.com"),
    ("Docker Hub", "https://registry-1.docker.io/v2/"),
    ("GitHub Container Registry", "https://ghcr.io/v2/"),
]


@dataclass
class Edition:
    letter: str          # -t of install.sh
    key: str             # compose/<key>.yml
    title: str
    description: str
    web_user: bool       # a web UI that needs a user
    ram_gb: int
    disk_gb: int


# the texts of install.sh, the requirements of the README (System Requirements)
EDITIONS = [
    Edition("h", "standard", "Hive", "T-Pot Standard / HIVE, with everything a distributed setup with sensors needs.",
            True, 16, 256),
    Edition("s", "sensor", "Sensor", "Honeypots only, sends its data to a HIVE: no web UI, no Elastic Stack.",
            False, 8, 128),
    Edition("l", "llm", "LLM", "LLM based honeypots Beelzebub and Galah, needs Ollama (recommended) or ChatGPT.",
            True, 16, 256),
    Edition("i", "mini", "Mini", "30+ honeypots with just a couple of honeypot daemons.", True, 16, 256),
    Edition("m", "mobile", "Mobile", "Everything to run T-Pot Mobile (available separately).", False, 8, 128),
    Edition("t", "tarpit", "Tarpit", "Feeds data endlessly to attackers, bots and scanners, with a DoS honeypot "
            "(ddospot).", True, 16, 256),
]
EDITION_BY_LETTER = {e.letter: e for e in EDITIONS}


@dataclass
class Check:
    title: str
    state: str           # ok | warn | fail
    detail: str = ""


@dataclass
class Answers:
    edition: Edition
    custom_compose: str = ""        # a file of the customizer instead of the edition
    web_user: str = ""
    web_password: str = ""
    sudo_password: str = ""
    settings: Dict[str, str] = field(default_factory=dict)

    @property
    def needs_web_user(self) -> bool:
        return self.edition.web_user


# -- checks ------------------------------------------------------------------

def os_release(path: str = "/etc/os-release") -> Dict[str, str]:
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                key, sep, value = line.strip().partition("=")
                if sep:
                    values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def check_distro(release: Dict[str, str]) -> Check:
    name, version = release.get("NAME", ""), release.get("VERSION_ID", "")
    if name not in SUPPORTED:
        return Check("Distribution", "fail", f"{name or 'unknown'} is not supported, see the T-Pot README")
    wanted = SUPPORTED[name]
    current = version if name in FULL_VERSION else version.split(".")[0]
    if wanted and current != wanted:
        return Check("Distribution", "fail", f"T-Pot supports {name} {wanted}, this system runs {current}")
    return Check("Distribution", "ok", f"{name} {version}".strip())


def check_root(euid: Optional[int] = None) -> Check:
    euid = os.geteuid() if euid is None else euid
    if euid == 0:
        return Check("User", "fail", "runs as root, run the installer as a regular user")
    return Check("User", "ok", os.environ.get("USER", "") or "not root")


def sudo_mode(run: Callable = subprocess.run) -> str:
    """passwordless | password | missing"""
    if not shutil.which("sudo"):
        return "missing"
    proc = run(["sudo", "-n", "-k", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "passwordless" if proc.returncode == 0 else "password"


def is_sudo_rs(run: Callable = subprocess.run) -> bool:
    try:
        proc = run(["sudo", "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   universal_newlines=True)
    except OSError:
        return False
    return "sudo-rs" in (proc.stdout or "").lower()


def check_sudo(mode: str, sudo_rs: bool = False) -> Check:
    if mode == "missing":
        return Check("sudo", "fail", "not installed, the installer needs it")
    detail = "works without a password" if mode == "passwordless" else "needs your password, asked before the start"
    if sudo_rs:
        detail += " (sudo-rs, Ansible uses the traditional sudo next to it)"
    return Check("sudo", "ok", detail)


def occupied(ss_output: str) -> List[str]:
    """Local addresses of `ss -H -ln` lines that are not the resolved stub."""
    out = []
    for line in ss_output.splitlines():
        parts = line.split()
        local = next((p for p in parts if re.search(r":\d+$", p)), "")
        address = local.rsplit(":", 1)[0].strip("[]")
        if local and not address.startswith(STUB_ADDRESSES):
            out.append(local)
    return out


def check_ports(run: Callable = subprocess.run) -> Check:
    if not shutil.which("ss"):
        return Check("Ports", "fail", "ss is missing (iproute2), the ports cannot be checked")
    busy = []
    for proto, port in CONFLICT_PORTS:
        proc = run(["ss", "-H", "-ln", f"--{proto}", f"sport = :{port}"], stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL, universal_newlines=True)
        if occupied(proc.stdout or ""):
            busy.append(f"{proto}/{port}")
    if busy:
        return Check("Ports", "fail", f"{', '.join(busy)} occupied, disable the service (sudo ss -lntup)")
    return Check("Ports", "ok", "25 and 53 are free for the honeypots")


def check_reach(opener: Callable = urllib.request.urlopen) -> Check:
    missing = []
    for name, url in REACH:
        try:
            with opener(urllib.request.Request(url, method="HEAD"), timeout=6):
                pass
        except urllib.error.HTTPError:
            pass                      # an answer, 401 of a registry included, means reachable
        except (OSError, ValueError):
            missing.append(name)
    if missing:
        return Check("Internet", "fail", f"cannot reach {', '.join(missing)}")
    return Check("Internet", "ok", "GitHub, Docker Hub and GHCR are reachable")


def meminfo_gb(text: str) -> float:
    match = re.search(r"^MemTotal:\s+(\d+)", text, re.M)
    return int(match.group(1)) / 1024 / 1024 if match else 0.0


def check_resources(edition: Edition, memory_gb: float, free_disk_gb: float) -> Check:
    short = []
    # a little below the round number: 16 GB machines report 15.6
    if memory_gb and memory_gb < edition.ram_gb * 0.9:
        short.append(f"{memory_gb:.0f} GB RAM, {edition.ram_gb} GB recommended")
    if free_disk_gb and free_disk_gb < edition.disk_gb * 0.9:
        short.append(f"{free_disk_gb:.0f} GB free disk, {edition.disk_gb} GB recommended")
    if short:
        return Check(f"Resources for {edition.title}", "warn", "; ".join(short))
    return Check(f"Resources for {edition.title}", "ok",
                 f"{memory_gb:.0f} GB RAM, {free_disk_gb:.0f} GB free disk")


def resources() -> tuple:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            memory = meminfo_gb(handle.read())
    except OSError:
        memory = 0.0
    try:
        free = shutil.disk_usage(os.path.expanduser("~")).free / 1024 ** 3
    except OSError:
        free = 0.0
    return memory, free


def installed() -> bool:
    return os.path.exists(SERVICE_FILE)


def run_checks(run: Callable = subprocess.run, opener: Callable = urllib.request.urlopen) -> List[Check]:
    checks = [check_distro(os_release()), check_root()]
    if installed():
        checks.append(Check("T-Pot", "fail", "is installed already, use tpot update"))
    checks.append(check_sudo(sudo_mode(run), is_sudo_rs(run) if shutil.which("sudo") else False))
    checks.append(check_ports(run))
    checks.append(check_reach(opener))
    return checks


def sudo_password_ok(password: str, run: Callable = subprocess.run) -> bool:
    proc = run(["sudo", "-S", "-k", "-p", "", "-v"], input=password + "\n", stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, universal_newlines=True)
    return proc.returncode == 0


# -- running install.sh --------------------------------------------------------

def secret_dir() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    return runtime if runtime and os.path.isdir(runtime) and os.access(runtime, os.W_OK) \
        else os.path.expanduser("~")


@contextlib.contextmanager
def secret_files(**secrets: str) -> Iterator[Dict[str, str]]:
    """{name: path} of files 0600 holding the given texts, removed afterwards."""
    paths: Dict[str, str] = {}
    try:
        for name, text in secrets.items():
            if not text:
                continue
            handle, path = tempfile.mkstemp(prefix=".tpot-install-", dir=secret_dir())
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(text + "\n")
            paths[name] = path
        yield paths
    finally:
        for path in paths.values():
            with contextlib.suppress(OSError):
                os.unlink(path)


def engine_command(answers: Answers, files: Dict[str, str], install_sh: str = INSTALL_SH) -> List[str]:
    letter = answers.edition.letter
    if answers.custom_compose:
        letter = "h" if answers.needs_web_user else "s"
    command = [install_sh, "-s", "-M", "-t", letter]
    if answers.custom_compose:
        command += ["-c", answers.custom_compose]
    if answers.needs_web_user:
        command += ["-u", answers.web_user, "-P", files["web"]]
    if "become" in files:
        command += ["-B", files["become"]]
    for option, variable in (("-b", "TPOT_BRANCH"), ("-r", "TPOT_REPO_URL")):
        if os.environ.get(variable):
            command += [option, os.environ[variable]]
    return command


def engine_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.update(PYTHONUNBUFFERED="1", ANSIBLE_FORCE_COLOR="0", ANSIBLE_NOCOLOR="1", TERM="dumb")
    return env


PHASES = ["checks", "packages", "playbook", "compose", "user", "pull", "done"]
PHASE_TITLES = {"checks": "Checking the host", "packages": "Installing the packages", "playbook": "Setting up the host",
                "compose": "Choosing the edition", "user": "Creating the web user", "pull": "Pulling the images",
                "done": "Done", "failed": "Failed"}
# where a phase starts on the whole bar, the playbook and the pull fill most of it
SPANS = {"checks": (0.0, 0.02), "packages": (0.02, 0.05), "playbook": (0.05, 0.78), "compose": (0.78, 0.79),
         "user": (0.79, 0.80), "pull": (0.80, 0.99), "done": (1.0, 1.0)}

_TASK = re.compile(r"^TASK \[(.*)\]")
_FAILED = re.compile(r"^(fatal|failed): \[")
_PULLED = re.compile(r"\b(Pulled|Skipped)\b")


class Progress:
    """What the lines of install.sh -M say."""

    def __init__(self):
        self.phase = "checks"
        self.tasks = 0
        self.tasks_done = 0
        self.task = ""
        self.images = 0
        self.images_done = 0
        self.failed_task = ""
        self.warnings: List[str] = []
        self.lines: deque = deque(maxlen=500)

    def feed(self, line: str) -> None:
        line = line.rstrip("\n")
        self.lines.append(line)
        mark = runlog.parse_mark(line)
        if mark is not None:
            what, words = mark
            value = words[0] if words else ""
            if what == "phase":
                self.phase = value
            elif what == "tasks" and value.isdigit():
                self.tasks = int(value)
            elif what == "images" and value.isdigit():
                self.images = int(value)
            elif what == "warn" and value == "pull":
                self.warnings.append("Not all images could be pulled, T-Pot tries again when it starts. "
                                     "The log of the installation shows which.")
            return
        match = _TASK.match(line)
        if match:
            self.tasks_done += 1
            self.task = match.group(1).rstrip("*").strip()
            return
        if _FAILED.match(line) and "...ignoring" not in line:
            self.failed_task = self.task
        if self.phase == "pull" and _PULLED.search(line):
            self.images_done += 1

    @property
    def fraction(self) -> float:
        start, end = SPANS.get(self.phase, (0.0, 0.0))
        part = 0.0
        if self.phase == "playbook" and self.tasks:
            part = min(self.tasks_done / self.tasks, 1.0)
        elif self.phase == "pull" and self.images:
            part = min(self.images_done / self.images, 1.0)
        return start + (end - start) * part

    @property
    def title(self) -> str:
        return PHASE_TITLES.get(self.phase, self.phase)


# -- uninstall -------------------------------------------------------------------

Handover = Tuple[List[str], Dict[str, str]]


def uninstall_handover(backup: bool, sudo_password: str = "", script: str = UNINSTALL_SH) -> Handover:
    """argv and environment for uninstall.sh, which tpot execs; the password file is
    removed by uninstall.sh (TPOT_REMOVE_BECOME_FILE), tpot is gone by then."""
    argv = [script, "-y"]
    if backup:
        argv.append("-k")
    env = dict(os.environ)
    if sudo_password:
        handle, path = tempfile.mkstemp(prefix=".tpot-uninstall-", dir=secret_dir())
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(sudo_password + "\n")
        argv += ["-B", path]
        env["TPOT_REMOVE_BECOME_FILE"] = "1"
    return argv, env
