"""What tpot knows about the installation and does to it, no user interface.

Everything goes through the tools that are there anyway: docker, systemctl and the
scripts in ~/tpotce (update.sh, restore.sh), which stay the ones doing the work.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from tpotctl.bootstrap import REPO_DIR
from tpotctl.envfile import read_values

SERVICE = "tpot"
WEB_PORT = 64297       # nginx, the one web entry point of a HIVE
_EDITION_RE = re.compile(r"^# T-Pot: (\S.*?)\s*$")


class OpsError(Exception):
    """Something tpot cannot do here."""


def linux_host() -> bool:
    """A T-Pot host: Linux with systemd. mac_win installations only get the customizer."""
    return sys.platform.startswith("linux") and shutil.which("systemctl") is not None


# TPOT_OSTYPE and the Docker it stands for, see tpotinit's entrypoint.sh
OSTYPE_TEXT = {"linux": "Docker Engine on Linux", "mac": "Docker Desktop for macOS",
               "win": "Docker Desktop for Windows"}
_HOST_OSTYPE: List[str] = []


def host_ostype(run: Optional[Callable] = None) -> str:
    """linux, mac or win: what tpotinit compares TPOT_OSTYPE with when it starts (uname of the
    kernel Docker runs on: microsoft is Windows, linuxkit is macOS). Without an answer from Docker
    the platform of tpot decides. TPOT_HOST_OSTYPE wins (the tests set it); one Docker call per
    process unless run is given."""
    forced = os.environ.get("TPOT_HOST_OSTYPE", "")
    if forced in OSTYPE_TEXT:
        return forced
    if run is None and _HOST_OSTYPE:
        return _HOST_OSTYPE[0]
    kernel = ""
    if shutil.which("docker"):
        try:
            proc = (run or subprocess.run)(["docker", "info", "--format", "{{.KernelVersion}}"],
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           universal_newlines=True, timeout=2)
            kernel = (proc.stdout or "").strip().lower() if proc.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            kernel = ""
    if "microsoft" in kernel:
        found = "win"
    elif "linuxkit" in kernel:
        found = "mac"
    elif kernel:
        found = "linux"
    elif sys.platform == "darwin":
        found = "mac"
    elif sys.platform in ("win32", "cygwin") or "microsoft" in platform.release().lower():
        found = "win"
    else:
        found = "linux"
    if run is None:
        _HOST_OSTYPE.append(found)
    return found


def require_linux_host(what: str) -> None:
    if not linux_host():
        raise OpsError(f"'{what}' needs a T-Pot host (Linux with systemd), here only 'tpot customize' works")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def env_values(repo_dir: str = REPO_DIR) -> Dict[str, str]:
    """KEY=value and the KEY: "value" form of the LLM blocks, comments skipped."""
    return read_values(os.path.join(repo_dir, ".env"))


def tpot_version(repo_dir: str = REPO_DIR) -> str:
    """The version of T-Pot, from one place: the file version of the checkout, else TPOT_VERSION of its
    .env (an installed T-Pot without one), else "" (the credits then say only t-pot)."""
    try:
        with open(os.path.join(repo_dir, "version"), encoding="utf-8") as handle:
            version = handle.readline().strip()
    except (OSError, UnicodeDecodeError):
        version = ""
    return version or env_values(repo_dir).get("TPOT_VERSION", "").strip()


def config_stamp(repo_dir: str = REPO_DIR) -> Tuple:
    """(mtime_ns, size) of .env and of the compose file in use, None for a missing one: tells the
    pages of tpot that the configuration was changed elsewhere."""
    stamp = []
    for path in (os.path.join(repo_dir, ".env"), compose_path(repo_dir)):
        try:
            info = os.stat(path)
            stamp.append((info.st_mtime_ns, info.st_size))
        except OSError:
            stamp.append(None)
    return tuple(stamp)


def compose_path(repo_dir: str = REPO_DIR, env: Optional[Dict[str, str]] = None) -> str:
    env = env_values(repo_dir) if env is None else env
    path = env.get("TPOT_DOCKER_COMPOSE") or "./docker-compose.yml"
    return os.path.normpath(os.path.join(repo_dir, path))


def edition(repo_dir: str = REPO_DIR) -> str:
    """STANDARD, SENSOR, ... or CUSTOM (from STANDARD) for a customizer file."""
    try:
        with open(compose_path(repo_dir), encoding="utf-8") as handle:
            head = [handle.readline() for _ in range(3)]
    except OSError:
        return "none"
    match = _EDITION_RE.match(head[0])
    if not match:
        return "unknown"
    name = match.group(1)
    base = re.search(r"base=(\S+)", head[1]) if head[1].startswith("# customizer:") else None
    return f"{name} (from {base.group(1)})" if base else name


def git(repo_dir: str, *args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", repo_dir] + list(args), stderr=subprocess.DEVNULL,
                                       universal_newlines=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


@dataclass
class Status:
    version: str
    branch: str
    commit: str
    edition: str
    tpot_type: str
    service: str        # active, inactive, failed, ... or "n/a" without systemd
    repo_dir: str
    since: Optional[int] = None     # seconds the service is active, None when it is not or unknown
    address: str = ""               # HIVE: this host's address for the web UI, SENSOR: its HIVE
    web_port: int = WEB_PORT        # the host port of nginx (the customizer can move it)


def parse_route_src(text: str) -> str:
    """The source address of `ip route get`: "1.1.1.1 via 10.0.0.1 dev eth0 src 10.0.0.5 uid 1000"."""
    match = re.search(r"\bsrc\s+(\S+)", text)
    return match.group(1) if match else ""


_NGINX_PORT = re.compile(r"""^\s*-\s*["']?(?:[\d.]+:)?(\d+):64297["']?\s*$""", re.M)


def web_port(repo_dir: str = REPO_DIR, env: Optional[Dict[str, str]] = None) -> int:
    """The host port that reaches nginx' 64297 in the compose file in use (64297 unless moved)."""
    try:
        with open(compose_path(repo_dir, env), encoding="utf-8") as handle:
            match = _NGINX_PORT.search(handle.read())
    except OSError:
        match = None
    return int(match.group(1)) if match else WEB_PORT


# the address changes seldom, the header asks every 2 s: once a minute is enough
_ADDRESS: Dict[str, Tuple[float, str]] = {}


def host_address(run: Callable = subprocess.run, now: Optional[float] = None) -> str:
    """The address of the route to the internet, the interface capture-if.sh / netinfo.detect take."""
    now = time.monotonic() if now is None else now
    cached = _ADDRESS.get("v4")
    if cached and now - cached[0] < 60:
        return cached[1]
    address = ""
    if linux_host() and shutil.which("ip"):
        try:
            proc = run(["ip", "-4", "route", "get", "1.1.1.1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       universal_newlines=True, timeout=2)
            address = parse_route_src(proc.stdout or "") if proc.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            address = ""
    _ADDRESS["v4"] = (now, address)
    return address


def service_since(run: Callable = subprocess.run, uptime_file: str = "/proc/uptime") -> Optional[int]:
    """Seconds since tpot.service became active: its monotonic start against the uptime of the host."""
    try:
        proc = run(["systemctl", "show", SERVICE, "-p", "ActiveEnterTimestampMonotonic", "--value"],
                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=2)
        started = int((proc.stdout or "").strip()) / 1e6
        with open(uptime_file, encoding="utf-8") as handle:
            uptime = float(handle.read().split()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    if started <= 0 or uptime < started:
        return None
    return int(uptime - started)


def duration(seconds: float, short: bool = False) -> str:
    """3 d 4 h, 5 h 12 min, 12 min, 40 s; short: the larger unit only (3 d, 5 h, 12 min)."""
    seconds = int(max(seconds, 0))
    days, hours, minutes = seconds // 86400, seconds // 3600 % 24, seconds // 60 % 60
    if days:
        return f"{days} d" if short or not hours else f"{days} d {hours} h"
    if hours:
        return f"{hours} h" if short or not minutes else f"{hours} h {minutes} min"
    return f"{minutes} min" if minutes else f"{seconds} s"


def status(repo_dir: str = REPO_DIR, run: Callable = subprocess.run) -> Status:
    env = env_values(repo_dir)
    service, since = "n/a", None
    if shutil.which("systemctl"):
        proc = run(["systemctl", "is-active", SERVICE], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                   universal_newlines=True)
        service = (proc.stdout or "").strip() or "unknown"
        if service == "active":
            since = service_since(run)
    tpot_type = env.get("TPOT_TYPE", "?")
    address = env.get("TPOT_HIVE_IP", "") if tpot_type == "SENSOR" else host_address(run)
    return Status(
        version=env.get("TPOT_VERSION", "?"),
        branch=git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD") or "?",
        commit=git(repo_dir, "rev-parse", "--short", "HEAD") or "?",
        edition=edition(repo_dir),
        tpot_type=tpot_type,
        service=service,
        repo_dir=repo_dir,
        since=since,
        address=address,
        web_port=web_port(repo_dir, env),
    )


# ---------------------------------------------------------------------------
# containers and images (dps, dim)
# ---------------------------------------------------------------------------

@dataclass
class Container:
    name: str
    state: str          # running, exited, ...
    status: str         # "Up 3 hours (healthy)"
    health: str         # healthy, unhealthy, starting or ""
    ports: str
    image: str


@dataclass
class Image:
    repository: str
    tag: str
    id: str
    size: str
    created: str

    @property
    def ref(self) -> str:
        return f"{self.repository}:{self.tag}"


def cell_state(container: Container) -> Tuple[str, str]:
    """(glyph name, colour name) of a container, see tpotctl.glyphs and tpotctl.theme.STYLE."""
    if container.state == "restarting":
        return "warn", "error"
    if container.state != "running":
        return "off", "error" if container.state in ("exited", "dead") else "mist"
    return "on", {"unhealthy": "error", "starting": "warn"}.get(container.health, "ok")


def compact_ports(ports: str) -> str:
    """0.0.0.0:80->80/tcp, [::]:80->80/tcp -> 80->80/tcp, local ports keep their address."""
    seen, out = set(), []
    for part in filter(None, (p.strip() for p in ports.split(","))):
        short = re.sub(r"^(0\.0\.0\.0|\[::\]|::):", "", part)
        if short not in seen:
            seen.add(short)
            out.append(short)
    return ", ".join(out)


def health_of(status_text: str) -> str:
    match = re.search(r"\((healthy|unhealthy|health: starting)\)", status_text)
    if not match:
        return ""
    return "starting" if match.group(1) == "health: starting" else match.group(1)


def parse_ps(output: str) -> List[Container]:
    containers = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        containers.append(Container(
            name=item.get("Names", ""),
            state=item.get("State", ""),
            status=item.get("Status", ""),
            health=health_of(item.get("Status", "")),
            ports=compact_ports(item.get("Ports", "")),
            image=item.get("Image", ""),
        ))
    return sorted(containers, key=lambda c: c.name)


def parse_images(output: str) -> List[Image]:
    images = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        images.append(Image(item.get("Repository", ""), item.get("Tag", ""), item.get("ID", ""),
                            item.get("Size", ""), item.get("CreatedSince", "")))
    return sorted(images, key=lambda i: i.ref)


def docker(args: List[str], run: Callable = subprocess.run) -> str:
    if not shutil.which("docker"):
        raise OpsError("docker is not installed")
    proc = run(["docker"] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if proc.returncode != 0:
        raise OpsError((proc.stderr or "").strip() or f"docker {' '.join(args)} failed")
    return proc.stdout


def containers(run: Callable = subprocess.run) -> List[Container]:
    # all of them: dps only showed running and exited ones, which hid restart loops
    return parse_ps(docker(["ps", "--all", "--format", "{{json .}}"], run))


def images(run: Callable = subprocess.run) -> List[Image]:
    return parse_images(docker(["images", "--format", "{{json .}}"], run))


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

def service_command(action: str) -> List[str]:
    if action not in ("start", "stop", "restart"):
        raise OpsError(f"unknown action {action}")
    return ["sudo", "systemctl", action, SERVICE]


def script_command(name: str, args: List[str], repo_dir: str = REPO_DIR) -> List[str]:
    if name not in ("update.sh", "restore.sh"):
        raise OpsError(f"unknown script {name}")
    return [os.path.join(repo_dir, name)] + list(args)


def backups(home: Optional[str] = None) -> List[str]:
    """Archives update.sh wrote, newest first."""
    folder = os.path.join(home or os.path.expanduser("~"), "tpot_backups")
    try:
        names = [n for n in os.listdir(folder) if "_tpot_backup" in n and n.endswith(".tar")]
    except OSError:
        return []
    return sorted((os.path.join(folder, n) for n in names), key=os.path.getmtime, reverse=True)


# The groups of an archive restore.sh can bring back (-g), with the questions it
# asks for them; tests keep them the same as in restore.sh.
GROUP_TEXT = {
    "git": "Roll the checkout back to the commit before the update?",
    "patch": "Re-apply all your changes to tracked files (tracked.patch)?",
    "config": "Restore the configuration (.env and docker-compose.yml)?",
    "untracked": "Restore your untracked files?",
    "data": "Restore the files from data/ (certificates, uuid, host keys)?",
    "elastic": "Import the Kibana objects and the ILM policy? T-Pot has to run for that.",
}
_GROUP_MEMBER = {"git": "rollback.txt", "patch": "tracked.patch", "config": "env", "untracked": "untracked/",
                 "data": "data/", "elastic": "elastic/"}


@dataclass
class BackupInfo:
    path: str
    name: str
    size: int
    kind: str                       # full | regular
    manifest: List[str]
    groups: List[str]
    problem: str = ""


def backup_info(path: str) -> BackupInfo:
    """What an archive of update.sh holds, read like restore.sh does."""
    import tarfile
    name = os.path.basename(path)
    kind = "full" if "_full" in name else "regular"
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    try:
        with tarfile.open(path) as archive:
            members = archive.getnames()
            manifest = []
            if "MANIFEST" in members:
                handle = archive.extractfile("MANIFEST")
                manifest = handle.read().decode("utf-8", "replace").splitlines()[1:8] if handle else []
    except (OSError, EOFError, tarfile.TarError) as err:     # EOFError: a cut compressed archive
        return BackupInfo(path, name, size, kind, [], [], f"cannot read it: {err}")
    groups = []
    for group, member in _GROUP_MEMBER.items():
        if any(m == member or (member.endswith("/") and m.startswith(member)) for m in members):
            groups.append(group)
    return BackupInfo(path, name, size, kind, manifest, groups)
