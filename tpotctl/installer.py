"""The T-Pot installer assistant (tpot install), no user interface.

install.sh stays the engine: the assistant checks the host, collects the answers
and runs `install.sh -s -M ...` with them, reading the progress marks it prints
(@@tpot phase|tasks|images ...) and the TASK lines of Ansible. Passwords go into
files only this user can read (-P, -B), never into the argv, and are removed when
the assistant ends.
"""

import contextlib
import grp
import os
import platform
import pwd
import re
import shutil
import struct
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
# (tpotctl/tests/test_installer.py compares them); includes best-effort hosts,
# "" is a rolling release
SUPPORTED = {
    "AlmaLinux": "10",
    "Debian GNU/Linux": "13",
    "Fedora Linux": "44",
    "openSUSE Tumbleweed": "",
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

# the containers run as the user and group tpot with this uid and gid, the playbook makes them
CONTAINER_ID = 2000
# the one way out of an account named tpot or a group named tpot, the same in install.sh, the playbook
# and the ISO (tpotctl/tests/test_installer.py checks it)
REMOVE_USER = "sudo userdel -r tpot"
REMOVE_GROUP = "sudo groupdel tpot"

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
    sudo_mode: str = ""
    user_password: str = ""
    settings: Dict[str, str] = field(default_factory=dict)

    @property
    def needs_web_user(self) -> bool:
        return self.edition.web_user


# -- checks ------------------------------------------------------------------


def tpot_on_path(repo_dir: str = REPO_DIR) -> bool:
    """Whether the link of the playbook made `tpot` this T-Pot Manager (a file of its own there stays)."""
    found = shutil.which("tpot")
    return found is not None and os.path.realpath(found) == os.path.realpath(os.path.join(repo_dir, "tpot"))


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
        return Check("Distribution", "fail", f"{name or 'unknown'} is not accepted by the installer; "
                     "see the T-Pot README for host requirements and support tiers")
    wanted = SUPPORTED[name]
    current = version if name in FULL_VERSION else version.split(".")[0]
    if wanted and current != wanted:
        return Check("Distribution", "fail", f"The installer requires {name} {wanted}, this system runs {current}")
    policy = "Debian is the preferred T-Pot platform. Raspberry Pi OS remains supported for T-Pot Mobile."
    detail = f"{name} {version}".strip()
    if name != "Debian GNU/Linux":
        return Check("Distribution", "warn", f"{detail}; best effort only: no equal test coverage or guaranteed "
                     f"distribution-specific fixes. {policy}")
    return Check("Distribution", "ok", f"{detail}; {policy}")


# as install.sh: the images of T-Pot are amd64 and arm64 only
MACHINES_64 = ("x86_64", "amd64", "aarch64", "arm64")


def check_arch(machine: Optional[str] = None, bits: Optional[int] = None) -> Check:
    """A 64-bit system. The userland counts, not the kernel: a 32-bit Raspberry Pi OS boots a 64-bit
    kernel on newer Pis (machine aarch64), the Python running here is 32-bit there."""
    machine = platform.machine() if machine is None else machine
    bits = struct.calcsize("P") * 8 if bits is None else bits
    if machine in MACHINES_64 and bits == 64:
        return Check("Architecture", "ok", machine)
    return Check("Architecture", "fail", f"{machine or 'unknown'} with a {bits}-bit userland: T-Pot needs a 64-bit "
                 "system (x86-64 or arm64). For T-Pot Mobile use Raspberry Pi OS Lite (64-bit)")


def check_root(euid: Optional[int] = None) -> Check:
    euid = os.geteuid() if euid is None else euid
    if euid == 0:
        return Check("User", "fail", "runs as root, run the installer as a regular user")
    return Check("User", "ok", os.environ.get("USER", "") or "not root")


@dataclass
class Accounts:
    """What the host holds of the account of the containers; None is nothing, or no answer."""
    tpot_uid: Optional[int] = None      # the uid of a user named tpot
    tpot_gid: Optional[int] = None      # the gid of a group named tpot
    uid_owner: Optional[str] = None     # the user that holds the uid of the containers
    gid_owner: Optional[str] = None     # the group that holds the gid of the containers


def read_accounts(getpwnam: Optional[Callable] = None, getgrnam: Optional[Callable] = None,
                  getpwuid: Optional[Callable] = None, getgrgid: Optional[Callable] = None) -> Accounts:
    """The accounts of the host through its name service, as install.sh reads them with getent."""
    getpwnam, getgrnam = getpwnam or pwd.getpwnam, getgrnam or grp.getgrnam
    getpwuid, getgrgid = getpwuid or pwd.getpwuid, getgrgid or grp.getgrgid

    def ask(lookup: Callable, key, attribute: str):
        try:
            return getattr(lookup(key), attribute)
        except (KeyError, OSError):
            return None

    return Accounts(ask(getpwnam, "tpot", "pw_uid"), ask(getgrnam, "tpot", "gr_gid"),
                    ask(getpwuid, CONTAINER_ID, "pw_name"), ask(getgrgid, CONTAINER_ID, "gr_name"))


def current_login() -> str:
    """The user running this, as whoami says it (install.sh checks the same name)."""
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return os.environ.get("USER", "")


def check_account(login: str, accounts: Optional[Accounts] = None) -> Check:
    """The user and group tpot belong to the containers (uid and gid 2000), the playbook makes them. A login
    or account of that name that is something else would be turned into that account there (its login and
    shell gone, the files of its home left behind), a uid or gid 2000 of another name stops it late. Nothing
    is renamed, moved or removed here, only named. The first problem counts, in this order, with the words
    of install.sh (tpotctl/tests/account_cases.json)."""
    title = "Account tpot"
    accounts = read_accounts() if accounts is None else accounts
    foreign_user = accounts.tpot_uid not in (None, CONTAINER_ID)
    if login == "tpot" and foreign_user:
        return Check(title, "fail", f"You are logged in as tpot (uid {accounts.tpot_uid}), a name T-Pot keeps for its "
                     "containers. The installer would turn this account into it. Log in as another user with "
                     "sudo rights, remove this account from there (this deletes its home directory, copy what "
                     f"you need first) and check again: {REMOVE_USER}")
    if login == "tpot":
        return Check(title, "fail", "The assistant runs as tpot, the system user of the T-Pot containers. "
                     "Run it as a regular user with sudo rights.")
    if foreign_user:
        return Check(title, "fail", f"The user tpot exists (uid {accounts.tpot_uid}), a name T-Pot keeps for its "
                     "containers. The installer would turn it into that account, its login and shell would be "
                     "gone. Remove it (not while it is logged in; this deletes its home directory, copy what "
                     f"you need first) and check again: {REMOVE_USER}")
    if accounts.tpot_gid not in (None, CONTAINER_ID):
        return Check(title, "fail", f"The group tpot exists (gid {accounts.tpot_gid}), a name T-Pot keeps for its "
                     f"containers. The installer would change its gid. Remove it and check again: {REMOVE_GROUP}")
    if accounts.uid_owner not in (None, "tpot"):
        return Check(title, "fail", f"The uid {CONTAINER_ID} belongs to {accounts.uid_owner}, T-Pot needs it for its "
                     f"containers. Give {accounts.uid_owner} another uid or remove that account, then check again.")
    if accounts.gid_owner not in (None, "tpot"):
        return Check(title, "fail", f"The gid {CONTAINER_ID} belongs to the group {accounts.gid_owner}, T-Pot needs it "
                     "for its containers. Give that group another gid or remove it, then check again.")
    if accounts.tpot_uid == CONTAINER_ID:
        return Check(title, "ok", f"there, uid {CONTAINER_ID} for the containers")
    return Check(title, "ok", "free, the installer creates it")


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
        except urllib.error.HTTPError as answer:
            # an answer, 401 of a registry included, means reachable; it is a response as well, closed
            # here (Python 3.14 warns otherwise); one without a body cannot be closed on Python 3.9
            try:
                answer.close()
            except Exception:          # noqa: BLE001
                pass
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
    checks = [check_arch(), check_distro(os_release()), check_root(), check_account(current_login(), read_accounts())]
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


def user_password_usable(run: Callable = subprocess.run) -> bool:
    """Inspect the current account without reading shadow or requiring sudo."""
    try:
        proc = run(["passwd", "-S"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    except (OSError, UnicodeError) as err:
        raise OSError("Cannot verify the user password state with passwd -S.") from err
    fields = proc.stdout.split()
    if proc.returncode or len(fields) < 2 or fields[1] not in ("P", "L", "NP"):
        raise OSError("Cannot verify the user password state with passwd -S.")
    return fields[1] == "P"


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
            paths[name] = path
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                os.fchmod(out.fileno(), 0o600)
                out.write(text + "\n")
        yield paths
    finally:
        failures = []
        for path in paths.values():
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                failures.append(path)
        if failures:
            raise OSError("Could not remove temporary password files. Remove these manually: " + ", ".join(failures))


def engine_command(answers: Answers, files: Dict[str, str], install_sh: str = INSTALL_SH) -> List[str]:
    if answers.sudo_mode not in ("secure", "convenient"):
        raise ValueError("Choose a sudo mode before installing.")
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
    if answers.sudo_mode == "convenient":
        command += ["--sudo-nopasswd"]
    if "user" in files:
        command += ["--user-password-file", files["user"]]
    for option, variable in (("-b", "TPOT_BRANCH"), ("-r", "TPOT_REPO_URL")):
        if os.environ.get(variable):
            command += [option, os.environ[variable]]
    return command


def engine_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.update(PYTHONUNBUFFERED="1", ANSIBLE_FORCE_COLOR="0", ANSIBLE_NOCOLOR="1", TERM="dumb")
    return env


PHASES = ["checks", "packages", "sudo_prepare", "playbook", "compose", "user", "pull", "sudo", "done"]
PHASE_TITLES = {"checks": "Checking the host", "packages": "Installing the packages", "playbook": "Setting up the host",
                "compose": "Choosing the edition", "user": "Creating the web user", "pull": "Pulling the images",
                "sudo_prepare": "Preparing sudo access", "sudo": "Applying the sudo mode",
                "done": "Done", "failed": "Failed"}
# where a phase starts on the whole bar, the playbook and the pull fill most of it
SPANS = {"checks": (0.0, 0.02), "packages": (0.02, 0.05), "sudo_prepare": (0.05, 0.05),
         "playbook": (0.05, 0.78), "compose": (0.78, 0.79),
         "user": (0.79, 0.80), "pull": (0.80, 0.99), "sudo": (0.99, 1.0), "done": (1.0, 1.0)}

_TASK = re.compile(r"^TASK \[(.*)\]")
_FAILED = re.compile(r"^(fatal|failed): \[")
_PULLED = re.compile(r"\b(Pulled|Skipped)\b")


class Progress:
    """What the lines of install.sh -M say."""

    def __init__(self):
        self.phase = "checks"           # the last @@tpot phase: a step, done or failed
        self.last = "checks"            # the last step (not done / failed): where the bar is
        self.failed_phase = ""          # the step a @@tpot fail <phase> named
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
                if value not in ("failed", "done"):
                    self.last = value
            elif what == "fail" and value in SPANS and value != "done":
                self.failed_phase = value
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
            if self.phase == "playbook":
                self.tasks_done += 1
            self.task = match.group(1).rstrip("*").strip()
            return
        if _FAILED.match(line) and "...ignoring" not in line:
            self.failed_task = self.task
        if self.phase == "pull" and _PULLED.search(line):
            self.images_done += 1

    @property
    def step(self) -> str:
        """The step the run is in, done, or the one that failed (fail <phase>, else the last one)."""
        if self.phase == "failed":
            return self.failed_phase or self.last
        return self.phase

    @property
    def fraction(self) -> float:
        # a failed run keeps the bar where it was
        phase = self.last if self.phase == "failed" else self.phase
        start, end = SPANS.get(phase, (0.0, 0.0))
        part = 0.0
        if phase == "playbook" and self.tasks:
            part = min(self.tasks_done / self.tasks, 1.0)
        elif phase == "pull" and self.images:
            part = min(self.images_done / self.images, 1.0)
        return start + (end - start) * part

    @property
    def title(self) -> str:
        if self.phase == "failed":
            return f"{PHASE_TITLES.get(self.step, self.step)}: failed"
        return PHASE_TITLES.get(self.phase, self.phase)


# -- uninstall -------------------------------------------------------------------

Handover = Tuple[List[str], Dict[str, str]]


def uninstall_handover(backup: bool, sudo_password: str = "", script: str = UNINSTALL_SH,
                      *, keep_sudo_rule: bool = False) -> Handover:
    """argv and environment for uninstall.sh, which tpot execs; the password file is
    removed by uninstall.sh (TPOT_REMOVE_BECOME_FILE), tpot is gone by then."""
    argv = [script, "-y"]
    if backup:
        argv.append("-k")
    if keep_sudo_rule:
        argv.append("--keep-sudo-rule")
    env = dict(os.environ)
    if sudo_password:
        handle, path = tempfile.mkstemp(prefix=".tpot-uninstall-", dir=secret_dir())
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(sudo_password + "\n")
        argv += ["-B", path]
        env["TPOT_REMOVE_BECOME_FILE"] = "1"
    return argv, env
