#!/usr/bin/env python3
"""Memory and CPU of Logstash and Fluent Bit under load, and the start marks of T-Pot.

Runs on a T-Pot host (HIVE or SENSOR) as a user docker answers to; standard library only.

    python3 docker/_tests/perf/measure.py --containers logstash[,fluent-bit] --minutes 15 --interval 10 --out FILE.csv
    python3 docker/_tests/perf/measure.py --startup [--out FILE.csv]

Sampling writes one CSV row per container and interval (timestamp, then the keys of sample()),
all sizes in MiB, an empty cell where a value could not be read:

    process_rss_mb       VmRSS of the container's largest process: the PIDs of `docker top <c> -eo pid`
                         (host PIDs), read from the host's /proc/<pid>/status (no shell in the image needed)
    container_memory_mb  MemUsage of `docker stats --no-stream` (the cgroup, without inactive page cache)
    cpu_pct              CPUPerc of the same call (100 = one core)
    heap_used_mb         logstash only: heap_used_in_bytes of its API, _node/stats/jvm

At the end it prints the mean and the maximum of each column per container (also after Ctrl+C).

--startup measures the last start of T-Pot. The time base is State.StartedAt of tpotinit (docker
inspect); each mark of STARTUP_MARKS is the first line of its container that matches its regex in
`docker logs --timestamps --since <that>` (stdout and stderr), in seconds since the time base;
logstash_start is State.StartedAt of logstash. A mark without a hit or of a container that is not
there (no Elasticsearch on a SENSOR) is None. It prints them with logstash_api - logstash_start
and writes them as one CSV row to --out if given.

Exit 0, 1 an error, 2 a wrong option, 130 cancelled.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional

STARTUP_MARKS = (
    ("elasticsearch", "elasticsearch", r'"message":\s*"started'),
    ("logstash_api", "logstash", r"Successfully started Logstash API endpoint"),
    ("logstash_pipelines", "logstash", r"Pipelines running"),
    ("templates", "tpotinit", r"# Index template logstash: written"),
    ("logstash_ready", "logstash", r"Index templates in place"),
)
TIME_BASE = "tpotinit"
LOGSTASH = "logstash"
HEAP_URL = "127.0.0.1:9600/_node/stats/jvm"
COLUMNS = ("container", "process_rss_mb", "container_memory_mb", "cpu_pct", "heap_used_mb")
API_AFTER_START = "logstash_api_minus_start"
# the most seconds one docker call may take
TIMEOUT = 60
MIB = 1024 * 1024
UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4,
         "kB": 1000, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4}
SIZE = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*$")
VMRSS = re.compile(r"^VmRSS:\s+([0-9]+)\s+kB", re.MULTILINE)
# RFC 3339 as docker writes it, with up to nine digits of the second
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$")


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def call(run: Callable, args: List[str]) -> Optional[subprocess.CompletedProcess]:
    """The finished docker call, or None when it could not run or took too long."""
    try:
        return run(args, capture_output=True, text=True, errors="replace", timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def mib(size: str) -> Optional[float]:
    """A size of docker (512MiB, 1.2GB, 300kB) in MiB."""
    match = SIZE.match(size or "")
    if not match or match.group(2) not in UNITS:
        return None
    return float(match.group(1)) * UNITS[match.group(2)] / MIB


def percent(text: str) -> Optional[float]:
    try:
        return float(str(text).strip().rstrip("%"))
    except ValueError:
        return None


def stats(containers: List[str], run: Callable) -> Dict[str, dict]:
    """The entries of `docker stats --no-stream` by name: one call for all, one each if that fails."""

    def entries(names: List[str]) -> Optional[Dict[str, dict]]:
        done = call(run, ["docker", "stats", "--no-stream", "--format", "{{json .}}", *names])
        if not done or done.returncode != 0:
            return None
        found: Dict[str, dict] = {}
        for line in done.stdout.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                for key in ("Name", "Container"):
                    if isinstance(entry.get(key), str):
                        found.setdefault(entry[key], entry)
        return found

    together = entries(containers)
    if together is not None or len(containers) < 2:
        return together or {}
    # docker fails the whole call when one of the names does not exist
    found: Dict[str, dict] = {}
    for container in containers:
        found.update(entries([container]) or {})
    return found


def process_rss(container: str, run: Callable, read: Callable[[str], str]) -> Optional[float]:
    """VmRSS of the container's largest process, in MiB."""
    done = call(run, ["docker", "top", container, "-eo", "pid"])
    if not done or done.returncode != 0:
        return None
    largest = None
    for line in done.stdout.splitlines():
        pid = line.strip()
        if not pid.isdigit():
            continue
        try:
            match = VMRSS.search(read(f"/proc/{pid}/status"))
        except OSError:
            continue
        if match:
            largest = max(largest or 0, int(match.group(1)))
    return None if largest is None else largest / 1024


def heap_used(container: str, run: Callable) -> Optional[float]:
    """The used heap of Logstash (its API), in MiB."""
    done = call(run, ["docker", "exec", container, "curl", "-s", HEAP_URL])
    if not done or done.returncode != 0:
        return None
    try:
        value = json.loads(done.stdout)["jvm"]["mem"]["heap_used_in_bytes"]
    except (ValueError, KeyError, TypeError):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value / MIB


def sample(containers: List[str], run: Callable = subprocess.run,
           read: Callable[[str], str] = read_text) -> List[dict]:
    """One row per container: process RSS, container memory, CPU and (logstash only) heap."""
    found = stats(containers, run)
    rows = []
    for container in containers:
        entry = found.get(container, {})
        rows.append({
            "container": container,
            "process_rss_mb": process_rss(container, run, read),
            "container_memory_mb": mib(str(entry.get("MemUsage", "")).split("/")[0]),
            "cpu_pct": percent(entry.get("CPUPerc", "")),
            "heap_used_mb": heap_used(container, run) if container == LOGSTASH else None,
        })
    return rows


def parse_time(text: str) -> Optional[datetime]:
    """A time of docker (RFC 3339, nanoseconds, Z) as an aware datetime; Python 3.9 takes microseconds."""
    match = STAMP.match((text or "").strip())
    if not match:
        return None
    whole, fraction, zone = match.groups()
    moment = datetime.fromisoformat(f"{whole}.{(fraction or '').ljust(6, '0')[:6]}"
                                    f"{'+00:00' if zone == 'Z' else zone}")
    # docker's zero time: a container that never started
    return moment if moment.year > 1 else None


def started_at(container: str, run: Callable) -> Optional[str]:
    """State.StartedAt of a container as docker writes it, None if there is none."""
    done = call(run, ["docker", "inspect", "--format", "{{.State.StartedAt}}", container])
    if not done or done.returncode != 0 or parse_time(done.stdout) is None:
        return None
    return done.stdout.strip()


def log_lines(container: str, since: str, run: Callable) -> List[str]:
    done = call(run, ["docker", "logs", "--timestamps", "--since", since, container])
    if not done or done.returncode != 0:
        return []
    return (done.stdout or "").splitlines() + (done.stderr or "").splitlines()


def first_hit(lines: Iterable[str], pattern: str) -> Optional[datetime]:
    regex = re.compile(pattern)
    hits = []
    for line in lines:
        stamp, _, message = line.partition(" ")
        if regex.search(message):
            moment = parse_time(stamp)
            if moment:
                hits.append(moment)
    return min(hits) if hits else None


def startup(run: Callable = subprocess.run) -> Dict[str, Optional[float]]:
    """Seconds from the start of tpotinit to each mark of STARTUP_MARKS and to the start of logstash."""
    base_text = started_at(TIME_BASE, run)
    if base_text is None:
        raise RuntimeError(f"no start time of {TIME_BASE} (docker inspect): does T-Pot run here?")
    base = parse_time(base_text)

    def since(moment: Optional[datetime]) -> Optional[float]:
        return None if moment is None else (moment - base).total_seconds()

    lines: Dict[str, List[str]] = {}
    marks: Dict[str, Optional[float]] = {}
    for mark, container, pattern in STARTUP_MARKS:
        if container not in lines:
            lines[container] = log_lines(container, base_text, run)
        marks[mark] = since(first_hit(lines[container], pattern))
    marks["logstash_start"] = since(parse_time(started_at(LOGSTASH, run) or ""))
    return marks


def cell(value) -> str:
    if value is None:
        return ""
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def summary(rows: List[dict]) -> List[str]:
    """Mean and maximum of each column per container."""
    lines = [f"{'container':<16} {'column':<20} {'mean':>10} {'max':>10} {'samples':>8}"]
    for container in dict.fromkeys(row["container"] for row in rows):
        for column in COLUMNS[1:]:
            values = [row[column] for row in rows if row["container"] == container and row[column] is not None]
            mean = f"{sum(values) / len(values):.2f}" if values else "-"
            top = f"{max(values):.2f}" if values else "-"
            lines.append(f"{container:<16} {column:<20} {mean:>10} {top:>10} {len(values):>8}")
    return lines


def record(containers: List[str], minutes: float, interval: float, out: str) -> int:
    """Sample every `interval` seconds for `minutes` into the CSV file `out`, then print the summary."""
    rows: List[dict] = []
    cancelled = False
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp",) + COLUMNS)
        start = time.monotonic()
        end = start + minutes * 60
        number = 0
        try:
            while start + number * interval < end:
                wait = start + number * interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for row in sample(containers):
                    writer.writerow([stamp] + [cell(row[column]) for column in COLUMNS])
                    rows.append(row)
                handle.flush()
                # a sample that took longer than the interval skips the slots it missed
                number = max(number + 1, int((time.monotonic() - start) // interval) + 1)
        except KeyboardInterrupt:
            cancelled = True
    print("\n".join(summary(rows)))
    print(f"{len(rows)} rows in {out}")
    return 130 if cancelled else 0


def report_startup(out: Optional[str]) -> int:
    try:
        marks = startup()
    except RuntimeError as error:
        print(f"measure.py: {error}", file=sys.stderr)
        return 1
    api, start = marks.get("logstash_api"), marks.get("logstash_start")
    marks[API_AFTER_START] = api - start if api is not None and start is not None else None
    for mark, value in marks.items():
        print(f"{mark:<28} {'-' if value is None else f'{value:.1f} s'}")
    if out:
        with open(out, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("timestamp",) + tuple(marks))
            writer.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds")]
                            + [cell(value) for value in marks.values()])
    return 0


def positive(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = 0.0
    if not value > 0:
        raise argparse.ArgumentTypeError(f"a number above 0, not {text!r}")
    return value


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="measure.py", description="Sample memory and CPU of containers, or the marks of the last start of T-Pot.")
    parser.add_argument("--containers", default=LOGSTASH, help="comma separated container names (default logstash)")
    parser.add_argument("--minutes", type=positive, default=15.0, help="how long to sample (default 15)")
    parser.add_argument("--interval", type=positive, default=10.0, help="seconds between samples (default 10)")
    parser.add_argument("--out", help="the CSV file (needed for sampling)")
    parser.add_argument("--startup", action="store_true", help="the marks of the last start instead of sampling")
    args = parser.parse_args(argv)
    if args.startup:
        return report_startup(args.out)
    containers = [name.strip() for name in args.containers.split(",") if name.strip()]
    if not containers:
        parser.error("--containers names no container")
    if not args.out:
        parser.error("sampling needs --out FILE.csv")
    return record(containers, args.minutes, args.interval, args.out)


if __name__ == "__main__":
    sys.exit(main())
