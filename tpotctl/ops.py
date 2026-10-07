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
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from tpotctl.bootstrap import REPO_DIR
from tpotctl.envfile import read_values

SERVICE = "tpot"
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


def status(repo_dir: str = REPO_DIR, run: Callable = subprocess.run) -> Status:
    env = env_values(repo_dir)
    service = "n/a"
    if shutil.which("systemctl"):
        proc = run(["systemctl", "is-active", SERVICE], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                   universal_newlines=True)
        service = (proc.stdout or "").strip() or "unknown"
    return Status(
        version=env.get("TPOT_VERSION", "?"),
        branch=git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD") or "?",
        commit=git(repo_dir, "rev-parse", "--short", "HEAD") or "?",
        edition=edition(repo_dir),
        tpot_type=env.get("TPOT_TYPE", "?"),
        service=service,
        repo_dir=repo_dir,
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
