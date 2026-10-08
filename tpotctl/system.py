"""CPU, memory, the disk of the T-Pot data, uptime and load, from /proc and statvfs (Linux, stdlib only)."""

import os
import shutil
from dataclasses import dataclass
from typing import Optional, Tuple

from tpotctl.bootstrap import REPO_DIR


@dataclass
class Usage:
    used: int
    total: int

    @property
    def percent(self) -> float:
        return 100.0 * self.used / self.total if self.total else 0.0


@dataclass
class System:
    cpu: Optional[float]        # percent since the previous reading, None on the first one
    memory: Optional[Usage]
    disk: Optional[Usage]
    disk_path: str
    uptime: Optional[float] = None      # seconds since the host booted
    load: Optional[float] = None        # load average of the last minute
    cpus: int = 1


def parse_stat(text: str) -> Optional[Tuple[int, int]]:
    """(busy, total) jiffies of the cpu line of /proc/stat."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            fields = [int(v) for v in line.split()[1:]]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)     # idle + iowait
            total = sum(fields[:8])                                       # without guest, it is in user
            return total - idle, total
    return None


def parse_meminfo(text: str) -> Optional[Usage]:
    values = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name] = int(parts[0]) * 1024
    if "MemTotal" not in values:
        return None
    available = values.get("MemAvailable", values.get("MemFree", 0))
    return Usage(values["MemTotal"] - available, values["MemTotal"])


def parse_uptime(text: str) -> Optional[float]:
    """Seconds since boot, the first field of /proc/uptime."""
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return None


def parse_loadavg(text: str) -> Optional[float]:
    """The load average of the last minute, the first field of /proc/loadavg."""
    return parse_uptime(text)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def data_path(env: dict, repo_dir: str = REPO_DIR) -> str:
    """TPOT_DATA_PATH as docker compose sees it: ~ expanded, relative to the checkout."""
    path = os.path.expanduser(env.get("TPOT_DATA_PATH") or "./data")
    return os.path.normpath(os.path.join(repo_dir, path))


def disk(path: str) -> Optional[Usage]:
    # the data folder may not be there yet (before the first start): its parents count then
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return Usage(usage.used, usage.total)


class Meter:
    """Readings over time, the CPU needs two of them."""

    def __init__(self, proc: str = "/proc"):
        self.proc = proc
        self.last: Optional[Tuple[int, int]] = None

    def read(self, env: dict, repo_dir: str = REPO_DIR) -> System:
        stat = parse_stat(_read(os.path.join(self.proc, "stat")))
        cpu = None
        if stat and self.last and stat[1] > self.last[1]:
            cpu = 100.0 * (stat[0] - self.last[0]) / (stat[1] - self.last[1])
        self.last = stat or self.last
        path = data_path(env, repo_dir)
        return System(cpu, parse_meminfo(_read(os.path.join(self.proc, "meminfo"))), disk(path), path,
                      parse_uptime(_read(os.path.join(self.proc, "uptime"))),
                      parse_loadavg(_read(os.path.join(self.proc, "loadavg"))), os.cpu_count() or 1)


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") or value >= 10 else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.0f} TB"
