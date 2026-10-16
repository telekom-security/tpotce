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
# "# T-Pot: STANDARD", also without the space (as update.sh fuEDITION_LINE takes it)
_EDITION_RE = re.compile(r"^# T-Pot:\s*(\S.*?)\s*$")


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


def edition_line(lines: List[str]) -> int:
    """The index of the line that names the edition: the first "# T-Pot:" line of the leading comment
    block (the lines above the first one that is neither empty nor a comment), -1 without one. A comment
    of your own above it keeps the edition. update.sh (fuEDITION_LINE) and compose/customizer_core.py
    (edition_line) have the same rule."""
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if not line.lstrip().startswith("#"):
            break
        if line.startswith("# T-Pot:"):
            return index
    return -1


def compose_head(path: str) -> List[str]:
    """The leading comment block of a compose file and the line after it; OSError when it cannot be read."""
    head = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            head.append(line)
            if line.strip() and not line.lstrip().startswith("#"):
                break
    return head


def edition_header(path: str) -> Tuple[str, str]:
    """(edition, the line after it) of a compose file: ("", "") without an edition line, OSError when it
    cannot be read."""
    head = compose_head(path)
    index = edition_line(head)
    match = _EDITION_RE.match(head[index]) if index >= 0 else None
    if not match:
        return "", ""
    return match.group(1), head[index + 1] if index + 1 < len(head) else ""


def edition(repo_dir: str = REPO_DIR) -> str:
    """STANDARD, SENSOR, ... or CUSTOM (from STANDARD) for a customizer file."""
    try:
        name, after = edition_header(compose_path(repo_dir))
    except OSError:
        return "none"
    if not name:
        return "unknown"
    base = re.search(r"base=(\S+)", after) if after.startswith("# customizer:") else None
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

ABSENT = "absent"       # a service of the compose file without a container: T-Pot did not start it


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
    if container.state == ABSENT:
        return "off", "ash"
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


_CONTAINER_NAME = re.compile(r"""^\s*container_name:\s*["']?([^"'\s#]+)["']?""", re.M)
_NAMES: Dict[str, Tuple[Tuple[int, int], List[str]]] = {}


def compose_names(repo_dir: str = REPO_DIR, env: Optional[Dict[str, str]] = None) -> List[str]:
    """The container names of the compose file in use: what T-Pot runs; [] without a readable file.
    The Status page asks every 2 s, so the names are kept until the file changes."""
    path = compose_path(repo_dir, env)
    try:
        info = os.stat(path)
        stamp = (info.st_mtime_ns, info.st_size)
        if path in _NAMES and _NAMES[path][0] == stamp:
            return list(_NAMES[path][1])
        with open(path, encoding="utf-8") as handle:
            names = sorted(set(_CONTAINER_NAME.findall(handle.read())))
    except (OSError, UnicodeDecodeError):
        return []
    _NAMES[path] = (stamp, names)
    return list(names)


def tpot_containers(found: List[Container], names: List[str]) -> List[Container]:
    """One entry per service of the compose file: its container, or a placeholder that is not
    started; containers of anything else on the host are left out. Without names (no readable
    compose file) everything found, so a broken checkout still shows what runs."""
    if not names:
        return list(found)
    by_name = {c.name: c for c in found}
    return [by_name.get(name) or Container(name, ABSENT, "not started", "", "", "") for name in sorted(names)]


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


# update.sh --check-data-loss: one line per loss of data, exit 0 none, 3 some, anything else unknown
LOSS_MARK = "@@tpot loss "
# its keys (fuLOSS_ADD): older backups deleted to make room, the Elasticsearch data an upgrade changes
# without a copy in the backup, the step of the data of an Elasticsearch before 8.19 through 8.19 on the
# way to 9, images that cannot be pulled again, a compose file of your own that the update adapts only
# after a yes (compose: confirmed with --adapt-compose, never with the file). A key not in here is passed
# on all the same: update.sh refuses what it does not find confirmed.
LOSS_KEYS = ("backups", "compose", "elastic", "elastic-step", "images")


def check_data_loss(args: List[str], timeout: int = 180, run: Callable = subprocess.run,
                    repo_dir: str = REPO_DIR) -> Tuple[bool, List[Tuple[str, str]], str]:
    """What an update (args as for update.sh: --full, --backup-only, -b, -r) would lose for good, read
    with update.sh --check-data-loss, which changes nothing and asks nothing. (True, [(key, text), ...], "")
    when it could tell (key one of LOSS_KEYS, e.g. elastic-step; a key with a dash is one word), (False, [], why) when it could not (another exit code, a timeout, no script): why is
    the last line update.sh wrote to stderr, without its marks."""
    try:
        proc = run(script_command("update.sh", ["--check-data-loss"] + list(args), repo_dir), cwd=repo_dir,
                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, [], f"no answer within {timeout} s"
    except (OSError, subprocess.SubprocessError) as err:
        return False, [], str(err)
    if proc.returncode not in (0, 3):
        said = [line.strip() for line in (proc.stderr or "").splitlines() if line.strip()]
        why = re.sub(r"^#+\s*(\[[A-Z]+\]\s*-\s*)?", "", said[-1]) if said else ""
        return False, [], why or f"update.sh ended with {proc.returncode}"
    items = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith(LOSS_MARK):
            key, _, text = line[len(LOSS_MARK):].strip().partition(" ")
            if key:
                items.append((key, text.strip()))
    return True, items, ""


def backups(home: Optional[str] = None) -> List[str]:
    """Archives update.sh wrote, newest first."""
    folder = os.path.join(home or os.path.expanduser("~"), "tpot_backups")
    try:
        names = [n for n in os.listdir(folder) if "_tpot_backup" in n and n.endswith(".tar")]
    except OSError:
        return []
    return sorted((os.path.join(folder, n) for n in names), key=os.path.getmtime, reverse=True)


def kibana_dirs(repo_dir: str = REPO_DIR, env: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """The data folder (TPOT_DATA_PATH as docker compose sees it) and the folder of the
    shipped Kibana objects and their manifest, for tpotctl.kibana_objects."""
    values = env_values(repo_dir) if env is None else env
    data = os.path.normpath(os.path.join(repo_dir, os.path.expanduser(values.get("TPOT_DATA_PATH") or "./data")))
    return data, os.path.join(repo_dir, "docker", "tpotinit", "dist", "etc", "objects")


def kibana_export_dir(home: Optional[str] = None) -> str:
    """Where the T-Pot Manager keeps its exports of the Kibana objects: ~/tpot_backups (0700,
    the files 0600, never rotated), next to the archives of update.sh."""
    return os.path.join(home or os.path.expanduser("~"), "tpot_backups")


def kibana_exports(home: Optional[str] = None, data_dir: Optional[str] = None) -> List[str]:
    """Exports of the Kibana objects, newest first: kibana_objects_*.ndjson of the T-Pot
    Manager in ~/tpot_backups and, with `data_dir`, auto_*.ndjson of tpotinit in
    <data>/kibana_objects/exports. Each place as kibana_objects.exports lists it: regular files
    only, no link followed (~/tpot_backups itself may be a link of yours)."""
    from tpotctl import kibana_objects as ko
    places = [(kibana_export_dir(home), ko.EXPORT_PREFIX)]
    if data_dir:
        places.append((ko.auto_exports_dir(data_dir), ko.AUTO_PREFIX))
    found = []
    for folder, prefix in places:
        for path in ko.exports(folder, prefix):
            try:
                found.append((os.lstat(path).st_mtime, path))
            except OSError:
                pass
    return [path for _, path in sorted(found, key=lambda f: (f[0], f[1]), reverse=True)]


@dataclass
class KibanaExport:
    """One export of the Kibana objects for the Elastic page: who wrote it (Manager: the T-Pot Manager or
    tpot elastic, T-Pot: tpotinit), its time, its size and its objects (None: no export that can be read)."""
    path: str
    origin: str
    modified: float
    size: int
    objects: Optional[int]


# exports larger than this are listed without a count ("?"): the page reads every listed export on each visit
KIBANA_COUNT_LIMIT = 50 * 2 ** 20


def kibana_export_infos(home: Optional[str] = None, data_dir: Optional[str] = None) -> List[KibanaExport]:
    """kibana_exports with what the table of the Elastic page shows: regular files only (a link, a pipe or a
    folder of that name is left out, as tpotinit's rotation does). The objects are counted as a restore
    reads the file (kibana_objects.read_export), up to KIBANA_COUNT_LIMIT bytes."""
    import stat
    from tpotctl import kibana_objects as ko
    infos = []
    for path in kibana_exports(home, data_dir):
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        count: Optional[int] = None
        if info.st_size <= KIBANA_COUNT_LIMIT:
            try:
                count = len(ko.read_export(path))
            except ko.KibanaError:
                count = None
        origin = "T-Pot" if os.path.basename(path).startswith(ko.AUTO_PREFIX) else "Manager"
        infos.append(KibanaExport(path, origin, info.st_mtime, info.st_size, count))
    return infos


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
    es_data: bool = False           # data/elk/data/: restoring data/ replaces the Elasticsearch data
    es_size: int = 0                # the bytes of the files under data/elk/data/ (its table of contents)


def _es_size(entries) -> int:
    """The bytes of the files under data/elk/data/ of a table of contents. tar stores a file with more names
    once, the later names as hard links to the first: a link whose first name is outside data/elk/data/
    counts with the size of that member (once per member), one to a file inside is that file (counted), one
    to a member the archive does not hold counts nothing."""
    inside = "data/elk/data/"
    sizes = {entry.name: entry.size for entry in entries if entry.isfile()}
    total = sum(size for name, size in sizes.items() if name.startswith(inside))
    outside = {entry.linkname for entry in entries
               if entry.islnk() and entry.name.startswith(inside) and not entry.linkname.startswith(inside)}
    return total + sum(sizes.get(name, 0) for name in outside)


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
            entries = archive.getmembers()
            members = [entry.name for entry in entries]
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
    es_data = any(m == "data/elk/data" or m.startswith("data/elk/data/") for m in members)
    es_size = _es_size(entries)
    return BackupInfo(path, name, size, kind, manifest, groups, es_data=es_data, es_size=es_size)
