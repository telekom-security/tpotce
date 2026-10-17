"""One sample event per honeypot, in the format the honeypot writes into its log.

A check appends a rendered fixture to the honeypot's real log (the injected geo event): Logstash
reads it as one of the honeypot's own lines, so it shows whether the way from the log file to
Elasticsearch, GeoIP and the Attack Map works. Each fixture lies in tpotctl/checks/fixtures/ as
<name>.<ext>, one line, taken from the honeypot's source at the version T-Pot builds (or from its
smoke test). The placeholders are

    {ts:FORMAT}   the time, in the honeypot's own format, always UTC (the containers run in UTC):
                  strftime codes plus %3f (milliseconds) and %9f (nanoseconds); {ts:epoch} is
                  seconds since 1970 with six decimals
    {src_ip}      the source address, where the honeypot logs the source
    {src_port}    the source port (the marker port of a check where no free text is logged)
    {dst_port}    the port of the honeypot
    {token}       the token of the check, where the honeypot logs free text (a path, a user name,
                  a command, a header)

The names are those of inventory.LOGS (the service names); the log file a fixture goes to is
log_file(name), which is the path of LOGS or, for the inputs that read a glob, one file of it.
"""

import ipaddress
import os
import re
from datetime import datetime, timezone
from typing import Dict, List

from tpotctl.checks.inventory import LOGS

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# The inputs that read a glob: the file of the glob the fixture goes to. ddospot writes one file
# per pot (dnspot.log is the DNS pot, targets.log is retyped to DdospotTarget), honeypots one per
# server (http.log is its HTTP server).
GLOB_FILES = {
    "ddospot": "ddospot/log/dnspot.log",
    "honeypots": "honeypots/log/http.log",
}

PLACEHOLDER = re.compile(r"\{(ts|src_ip|src_port|dst_port|token)(?::([^{}\n]*))?\}")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")


def names() -> List[str]:
    """The honeypots that have a fixture, sorted."""
    found = []
    for entry in sorted(os.listdir(FIXTURE_DIR)):
        stem, ext = os.path.splitext(entry)
        if ext and not entry.startswith("."):
            found.append(stem)
    return found


def fixture_path(name: str) -> str:
    """The fixture file of a honeypot; KeyError when it has none (or more than one)."""
    matches = [entry for entry in os.listdir(FIXTURE_DIR) if os.path.splitext(entry)[0] == name]
    if len(matches) != 1:
        raise KeyError(f"no fixture for {name}" if not matches else f"more than one fixture for {name}")
    return os.path.join(FIXTURE_DIR, matches[0])


def template(name: str) -> str:
    """The fixture of a honeypot as it is stored, with its placeholders (one line, no newline)."""
    with open(fixture_path(name), encoding="utf-8") as handle:
        text = handle.read()
    return text[:-1] if text.endswith("\n") else text


def log_file(name: str) -> str:
    """The log file the fixture of a honeypot goes to, relative to the data folder."""
    if name in GLOB_FILES:
        return GLOB_FILES[name]
    path = LOGS[name][1]
    if any(char in path for char in "*?["):
        raise KeyError(f"{name} reads {path}: no file named for its fixture (GLOB_FILES)")
    return path


def logstash_type(name: str) -> str:
    """The type Logstash gives the events of the fixture."""
    return LOGS[name][0]


def _format_time(ts: datetime, spec: str) -> str:
    if spec == "epoch":
        return "%.6f" % ts.timestamp()
    micro = ts.microsecond
    spec = spec.replace("%3f", "%03d" % (micro // 1000)).replace("%9f", "%09d" % (micro * 1000))
    return ts.strftime(spec)


def render(name: str, ts: datetime, src_ip: str, src_port: int, dst_port: int, token: str) -> str:
    """The fixture of a honeypot with the values of a check filled in: one log line, no newline.

    `ts` is a datetime (a naive one counts as UTC). `token` is a plain word (letters, digits,
    . _ -), so it fits every format: a JSON string, a CSV field, a path, a DNS label."""
    if ipaddress.ip_address(src_ip).version != 4:
        # several formats take an IPv4 address only (dionaea writes ::ffff:<v4>, sentrypeer ip:port)
        raise ValueError(f"the source of an injected event is an IPv4 address (a test IP), not {src_ip}")
    for label, value in (("src_port", src_port), ("dst_port", dst_port)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
            raise ValueError(f"{label} is no port: {value!r}")
    if not TOKEN.fullmatch(token or ""):
        raise ValueError(f"the token must be a plain word (letters, digits, . _ -): {token!r}")
    ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    values: Dict[str, str] = {"src_ip": src_ip, "src_port": str(src_port), "dst_port": str(dst_port),
                              "token": token}

    def fill(match):
        key, spec = match.group(1), match.group(2)
        if key == "ts":
            return _format_time(ts, spec or "%Y-%m-%dT%H:%M:%S.%fZ")
        if spec is not None:
            raise ValueError(f"{name}: {{{key}}} takes no format")
        return values[key]

    return PLACEHOLDER.sub(fill, template(name))
