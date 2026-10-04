"""Time zones: the list to choose from, the one of this host, and searching them."""

import os
import re
import shutil
import subprocess
from typing import Callable, List

ZONEINFO = "/usr/share/zoneinfo"
# names of UTC hosts report (Debian: Etc/UTC), the list offers UTC
UTC_ALIASES = {"Etc/UTC", "Etc/Universal", "Etc/Zulu", "Etc/UCT", "Etc/GMT", "UCT", "Universal", "Zulu", "GMT"}
_SKIP = {"posix", "right", "Etc", "SystemV", "US", "Canada", "Mexico", "Brazil", "Chile"}


def zones(root: str = ZONEINFO) -> List[str]:
    """UTC first, then the Area/City zones, sorted."""
    found = set()
    try:
        import zoneinfo
        found = set(zoneinfo.available_timezones())
    except ImportError:
        pass
    if not found and os.path.isdir(root):
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("posix", "right")]
            for name in files:
                rel = os.path.relpath(os.path.join(folder, name), root)
                if re.match(r"^[A-Z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$", rel):
                    found.add(rel)
    # the old aliases (US/Eastern, Etc/GMT+1, ...) are valid, but no help in a list
    listed = sorted(z for z in found if "/" in z and z.split("/")[0] not in _SKIP)
    return ["UTC"] + [z for z in listed if z != "UTC"]


def _from_localtime(path: str) -> str:
    try:
        target = os.path.realpath(path)
    except OSError:
        return ""
    match = re.search(r"zoneinfo/(.+)$", target)
    return match.group(1) if match else ""


def detect(run: Callable = subprocess.run, etc: str = "/etc") -> str:
    """The time zone of this host, "" if it cannot be told."""
    found = _detect(run, etc)
    return "UTC" if found in UTC_ALIASES else found


def _detect(run: Callable, etc: str) -> str:
    if shutil.which("timedatectl"):
        try:
            proc = run(["timedatectl", "show", "-p", "Timezone", "--value"], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        with open(os.path.join(etc, "timezone"), encoding="utf-8") as handle:
            value = handle.read().strip()
        if value:
            return value
    except OSError:
        pass
    return _from_localtime(os.path.join(etc, "localtime"))


def search(query: str, choices: List[str]) -> List[str]:
    """Case does not matter, a space matches the underscore ("new york" -> America/New_York).
    Zones whose city starts with the query come first."""
    words = query.strip().lower().replace("_", " ").split()
    if not words:
        return list(choices)
    hits = [z for z in choices if all(w in z.lower().replace("_", " ") for w in words)]
    joined = " ".join(words)
    return sorted(hits, key=lambda z: (not z.lower().replace("_", " ").split("/")[-1].startswith(joined),
                                       choices.index(z)))
