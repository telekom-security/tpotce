#!/usr/bin/env python3
"""Load for Logstash and Fluent Bit: the fixtures of the honeypots, appended to their own log files.

Runs on a T-Pot host (HIVE or SENSOR) while measure.py samples Logstash or Fluent Bit. Each line
is the fixture of a honeypot (tpotctl/checks/fixtures) with the time of its writing, a token of its
own and a source from a fixed pool of global IPv4 addresses (the same on every run, so the GeoIP
and iprep lookups see a realistic variety), appended round-robin to the log file the honeypot's
input reads (fixtures.log_file) at --rate lines per second for --seconds seconds.

A honeypot whose log folder is not under the data folder (not in this edition) is left out,
nothing is created for it. A missing log file in an existing folder is created with mode 0660
and the owner of its folder. Every line is one append (O_APPEND), so it never splits a line of
the honeypot itself; a log file is never reached through a link.

    sudo python3 docker/_tests/perf/load.py [--data DIR] --rate N --seconds S

--data is the data folder of T-Pot (default ~/tpotce/data of the user who runs sudo). Only for a
test host: the events go to Elasticsearch and the Attack Map like real attacks, and ewsposter
would send them on, so hold it for the run (docker/tpotinit/dist/bin/ews_hold.sh --data DIR
run -- python3 .../load.py ...). Exit 0, 1 an error, 2 a wrong option, 130 cancelled.
"""

import argparse
import ipaddress
import os
import random
import stat
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.append(str(REPO))

from tpotctl.checks import fixtures  # noqa: E402  (standard library only)

POOL_SIZE = 1000
POOL_SEED = 2000
FILE_MODE = 0o660
FIRST_SRC_PORT = 1024
FIRST_DST_PORT = 8080


def source_pool(size: int = POOL_SIZE, seed: int = POOL_SEED) -> List[str]:
    """A fixed list of distinct global unicast IPv4 addresses: the sources of the load."""
    rng = random.Random(seed)
    pool: List[str] = []
    seen = set()
    while len(pool) < size:
        address = ipaddress.IPv4Address(rng.getrandbits(32))
        if address.is_global and not address.is_multicast and address not in seen:
            seen.add(address)
            pool.append(str(address))
    return pool


def targets(data: Path) -> Tuple[List[Tuple[str, Path]], List[str]]:
    """The fixtures whose log folder is under `data` with their log files, and the names left out."""
    found: List[Tuple[str, Path]] = []
    skipped: List[str] = []
    for name in fixtures.names():
        path = Path(data) / fixtures.log_file(name)
        if path.parent.is_dir():
            found.append((name, path))
        else:
            skipped.append(name)
    return found, skipped


def open_log(path: Path) -> int:
    """A descriptor that appends to the log file; a missing one is created like the honeypot's own."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, FILE_MODE)
        except FileExistsError:
            # its honeypot wrote it in the meantime
            fd = os.open(path, flags)
        else:
            os.fchmod(fd, FILE_MODE)
            folder = os.stat(path.parent)
            try:
                os.fchown(fd, folder.st_uid, folder.st_gid)
            except PermissionError:
                pass
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{path} is no regular file")
    return fd


def run(data: Path, rate: int, seconds: int, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep) -> int:
    """Append rate * seconds rendered fixtures, paced to `rate` lines per second; the line count."""
    if rate < 1 or seconds < 1:
        raise ValueError(f"rate and seconds must be at least 1, not {rate} and {seconds}")
    found, _ = targets(data)
    if not found:
        raise FileNotFoundError(f"no log folder of a honeypot under {data}: is it the data folder of T-Pot?")
    pool = source_pool()
    run_id = uuid.uuid4().hex[:8]
    total = rate * seconds
    files: Dict[Path, int] = {}
    try:
        for _, path in found:
            if path not in files:
                files[path] = open_log(path)
        start = clock()
        for number in range(total):
            wait = start + number / rate - clock()
            if wait > 0:
                sleep(wait)
            index = number % len(found)
            name, path = found[index]
            line = fixtures.render(name, datetime.now(timezone.utc), pool[number % len(pool)],
                                   FIRST_SRC_PORT + number % (65536 - FIRST_SRC_PORT), FIRST_DST_PORT + index,
                                   f"tpotperf-{run_id}-{number}")
            os.write(files[path], (line + "\n").encode("utf-8"))
    finally:
        for fd in files.values():
            os.close(fd)
    return total


def default_data() -> Path:
    """~/tpotce/data of the user who runs the tool, also under sudo."""
    user = os.environ.get("SUDO_USER")
    home = os.path.expanduser("~" + user) if user and os.geteuid() == 0 else os.path.expanduser("~")
    return Path(home, "tpotce", "data")


def at_least_one(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"a whole number of at least 1, not {text!r}")
    return value


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="load.py", description="Append the honeypot fixtures to their log files at a fixed rate (test hosts).")
    parser.add_argument("--data", type=Path, help="the data folder of T-Pot (default ~/tpotce/data)")
    parser.add_argument("--rate", type=at_least_one, required=True, help="lines per second")
    parser.add_argument("--seconds", type=at_least_one, required=True, help="how long")
    args = parser.parse_args(argv)
    data = args.data or default_data()
    _, skipped = targets(data)
    began = time.monotonic()
    try:
        count = run(data, args.rate, args.seconds)
    except KeyboardInterrupt:
        print("load.py: cancelled", file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        print(f"load.py: {error}", file=sys.stderr)
        return 1
    elapsed = time.monotonic() - began
    print(f"{count} lines in {elapsed:.1f} s ({count / elapsed:.1f}/s) into {data}")
    if elapsed > args.seconds * 1.05:
        print(f"load.py: the load fell behind the rate of {args.rate}/s", file=sys.stderr)
    print("skipped (no log folder): " + (", ".join(skipped) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
