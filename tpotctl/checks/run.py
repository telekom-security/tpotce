"""`tpot check honeypots` / `tpot check pipeline`: every honeypot from its port to the Attack Map.

The run:
    1. the inventory: the honeypots of the compose file in use (tpotctl.checks.inventory);
    2. the hold of ewsposter and the lock of the checks (EwsHold): nothing leaves the host from
       here on, and a hold that is not confirmed ends the run before the first probe (exit 3); a
       honeypot whose ewsposter module cannot be held (hold.ack names it) gets neither a probe nor
       a geo event, its row says why and its ewsposter stage fails;
    3. the probes against the host's own address (not 127.0.0.1: docker-proxy would change the
       source port), then one geo event per running honeypot (not the passive tools), a fixture
       with a fixed test IP (IPv4) and the marker port 51234 + n appended to its real log;
    4. the stages, polled until each one is decided or its time is up (tpotctl.checks.stages);
    5. END_SETTLE seconds for the last lines (written when a connection closes), then the end of
       the hold (its settle is this one);
    6. the cleanup unless --keep, Elasticsearch only: _delete_by_query on logstash-*, twice
       (SECOND_PASS later for events indexed late), for the events that are the run's own beyond
       doubt (stages.cleanup_query: its window and types, and its token, or a test IP with the
       marker port of its injection, or the probe source with a recorded source port); the probe
       events none of these names stay and are counted (cleanup "left");
    7. the result in ~/.local/share/tpotce/checks/last.json; only then the output of steps 5 and 6.
       Every line also goes into last.log next to it; a closed stdout never stops the run. One
       line ("Ending the check: ...") goes out at once before the silent end; a signal there is
       noted once ("Cancel noted, finishing the cleanup first."). Any other fault still writes
       last.json (result "error", exit 1, the traceback in last.log).

Pending cleanups: before the first probe or geo event, the run writes what its cleanup needs into
checks/pending/<run id>.json (0600, the folder 0700, written whole) and keeps it up to date; the
file goes after a cleanup that ran through (or with --keep, or when nothing was sent) and stays
when the cleanup failed or was cut off (Elasticsearch down, a reboot); it is fsynced, and one
that cannot be written before the first probe stops the check (exit 3, nothing probed). `tpot
check cleanup` runs those (one pass, the window up to the end of the run in last.json or the
file's time + 2 min, capped), and every check runs them first, before it takes the hold; both
take the lock of the checks first (a check that runs keeps its own file). A file is bound to its
run (read_pending, own_from_json): its start to its run id, its source to its host, its types to
the inventory, its ports and test IPs to what a run records; anything wider is refused, kept and
named, nothing of it deleted.

SIGTERM and SIGHUP become an exception like Ctrl+C, so the end of the hold and the cleanup run on
every way out; the handlers before come back afterwards (they are set before the hold is taken).
Only the first signal is raised, a later one (and one during the end) is noted. Exit codes: 0
every stage ok, 1 a stage or the cleanup failed or an error before the hold (last.json then has
the error and no honeypots), 2 an option that does not fit (and the CLI: it cannot ask), 3 the
hold failed (nothing probed), 130 cancelled (Ctrl+C, SIGTERM, SIGHUP).

last.json (version 1), for the Checks page:
    {"version": 1, "run_id", "token", "mode": "honeypots"|"pipeline", "started", "ended" (UTC ISO),
     "host", "tpot_type", "options": {"only", "geo", "keep", "timeout"},
     "exit_code", "result": "ok"|"failed"|"hold failed"|"cancelled"|"error", "error",
     "ewsposter": {"state": "held"|"not_here"|"not_running"|"failed"|"none", "detail"},
     "cleanup": {"state": "done"|"kept"|"skipped"|"failed"|"none", "deleted" (the sum),
                 "passes": [first, second], "left" (probe events that stay), "left_no_source" (of them
                 the events of a probed type without a source address), "left_kibana", "detail"},
     "kibana": the Discover link of the run's window and token / sources,
     "stages": the names in order, "titles": name -> column title,
     "honeypots": [{"name", "group", "container", "running", "external", "logstash_type", "ports",
                    "stages": {name: {"state": "ok"|"failed"|"n/a"|"not_reached", "detail"}},
                    "log_line", "es_id", "es_index",
                    "geo": null | {"src_ip", "src_port", "file", "line", "es_id", "es_index",
                                   "location": {"lat", "lon", "country", "ip_rep"}}}]}
"""

import contextlib
import ipaddress
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from tpotctl.checks import CheckError, HoldError, fixtures, logfile, probes, stages
from tpotctl.checks.inventory import LOGS, PASSIVE_GROUPS, Honeypot
from tpotctl.checks.stages import (FAILED, NOT_APPLICABLE, NOT_REACHED, OK, ON_THE_HIVE, GeoEvent, Row,
                                   TEST_IPS)

EXIT_OK, EXIT_FAILED, EXIT_CANNOT_ASK, EXIT_HOLD, EXIT_CANCELLED = 0, 1, 2, 3, 130
ES_URL = "http://127.0.0.1:64298"       # as events.ES_URL and fuWAITES
TIMEOUT = 90.0          # seconds per stage for Elasticsearch, and again for the Attack Map
LOG_TIMEOUT = 30.0      # seconds for the log lines of the probes
POLL = 3.0              # seconds between two looks
# seconds between the last look and the cleanup: lines written when a connection closes, Logstash and
# the refresh of Elasticsearch (5 s); also the settle of the hold (EwsHold.SETTLE is 10 s), which ends
# right after the cleanup
END_SETTLE = 15.0
# seconds between the two passes of the cleanup: events Logstash indexes late
SECOND_PASS = 60.0
MAP_CONTAINERS = ("map_redis", "map_web")
RESULT_VERSION = 1

# the question before a check: what it does to this host, in words of behaviour (the page asks it too)
QUESTION_HOLD = ("During the check (a few minutes) ewsposter sends nothing; real attacks from this window are not "
                 "reported either.")
QUESTION_WRITTEN = {"probes": "The probes are written to the honeypot logs",
                    "both": "The probes and one test event per honeypot with a fixed test IP are written to the "
                            "honeypot logs",
                    "events": "One test event per honeypot with a fixed test IP is written to the honeypot logs"}
QUESTION_CLEANUP = "; afterwards the check removes its own events from Elasticsearch, the log lines stay."
QUESTION_KEPT = " and stay in Elasticsearch."
QUESTION = f"{QUESTION_HOLD} {QUESTION_WRITTEN['both']}{QUESTION_CLEANUP}"
QUESTION_SENSOR = ("During the check (a few minutes) ewsposter sends nothing; real attacks from this window are "
                   "not reported either. The probes are written to the honeypot logs and sent to the HIVE, where "
                   "they stay in Elasticsearch (a SENSOR checks the port, the answer and the log line).")

# the WebSocket client inside map_web (python3 and aiohttp are in the image), as in
# attackmap_pipeline_test.sh, with a time limit of its own: a docker exec that ends leaves its
# process in the container running otherwise
WS_MARK = "tpot-check-ws-client"
WS_CLIENT = """
# tpot-check-ws-client
import asyncio, os, aiohttp
async def listen():
    url = os.environ.get("WS_URL", "ws://127.0.0.1:64299/websocket")
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            print("WS-CONNECTED", flush=True)
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    print(message.data, flush=True)
                elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
async def main():
    try:
        await asyncio.wait_for(listen(), float(os.environ.get("WS_SECONDS", "300")))
    except asyncio.TimeoutError:
        pass
asyncio.run(main())
"""


@dataclass
class Options:
    only: List[str] = field(default_factory=list)   # these honeypots only (service names)
    geo: bool = True            # inject the geo events (stages 5 and 6)
    geo_only: bool = False      # tpot check pipeline: no probes, the geo events only
    keep: bool = False          # leave the test events in Elasticsearch
    become_file: str = ""       # the sudo password in a file, for a log only root may read or append to
    timeout: float = TIMEOUT
    dry_run: bool = False

    @property
    def mode(self) -> str:
        return "pipeline" if self.geo_only else "honeypots"


class UsageError(CheckError):
    """An option that does not fit (i.e. --only names a honeypot this T-Pot does not run)."""


class PendingError(CheckError):
    """The pending file of the cleanup could not be written before the first probe: nothing is probed."""


class Terminated(BaseException):
    """SIGTERM or SIGHUP, raised where the run is, so the cleanup and the end of the hold run."""

    def __init__(self, signum: int):
        super().__init__(f"signal {signum}")
        self.signum = signum


def question(options: Options, sensor: bool = False) -> str:
    """What the check does to this host, asked before it starts."""
    if sensor:
        return QUESTION_SENSOR
    written = QUESTION_WRITTEN["events" if options.geo_only else "both" if options.geo else "probes"]
    return f"{QUESTION_HOLD} {written}{QUESTION_KEPT if options.keep else QUESTION_CLEANUP}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def result_path(environ=None) -> str:
    env = os.environ if environ is None else environ
    data = env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "tpotce", "checks", "last.json")


def _say(kind: str, text: str) -> None:
    from tpotctl import say
    getattr(say, kind)(text)


# -- signals ------------------------------------------------------------------------------------

class Signals:
    """SIGINT, SIGTERM and SIGHUP as exceptions (KeyboardInterrupt, Terminated), the first one only;
    later ones and those inside `shield()` are only noted, so the cleanup and the end of the hold are
    not cut short. Outside the main thread nothing changes (Python sets handlers there only)."""

    WATCHED = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))

    def __init__(self, noted: Optional[Callable[[], None]] = None):
        self.received: List[int] = []
        self.shielded = False
        self.noted = noted              # called once, for the first signal inside the shield
        self._told = False
        self._previous: Dict[int, Any] = {}

    def handler(self, signum, _frame) -> None:
        self.received.append(signum)
        if self.shielded:
            if self.noted is not None and not self._told:
                self._told = True
                self.noted()
            return
        # one exception per run: a second ^C must not cut short the end of the probes (the pool
        # waits for them), the cleanup or the end of the hold
        if len(self.received) > 1:
            return
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise Terminated(signum)

    def __enter__(self) -> "Signals":
        for signum in self.WATCHED:
            try:
                previous = signal.getsignal(signum)
                signal.signal(signum, self.handler)
            except (ValueError, OSError):       # not the main thread
                continue
            self._previous[signum] = previous
        return self

    def __exit__(self, *exc) -> bool:
        for signum, previous in self._previous.items():
            signal.signal(signum, previous if previous is not None else signal.SIG_DFL)
        self._previous = {}
        return False

    @contextlib.contextmanager
    def shield(self):
        self.shielded, before = True, self.shielded
        try:
            yield
        finally:
            self.shielded = before


# -- Elasticsearch ------------------------------------------------------------------------------

class ElasticError(CheckError):
    def __init__(self, text: str, status: int = 0, body: str = ""):
        super().__init__(text)
        self.status = status
        self.body = body

    @property
    def token_refused(self) -> bool:
        """Elasticsearch cannot run the token query over every field (it cannot parse it, a field
        is not searchable, or the fields are too many); only then the check goes on without it."""
        if self.status != 400:
            return False
        text = self.body.lower()
        return any(part in text for part in TOKEN_REFUSED)


# what Elasticsearch answers when the query_string of the token cannot run over every field
TOKEN_REFUSED = ("failed to create query", "cannot be parsed", "not searchable", "field expansion",
                 "too many fields", "maxclausecount", "too_many_nested_clauses", "too_many_clauses")
DELETE_TIMEOUT = 60.0       # seconds for one _delete_by_query; every other request has 5 s


class Elastic:
    """logstash-* of this HIVE (127.0.0.1:64298, nginx-free like fuWAITES and tpot attackers)."""

    def __init__(self, url: str = ES_URL, opener: Callable = urllib.request.urlopen, timeout: float = 5):
        self.url = url.rstrip("/")
        self.opener = opener
        self.timeout = timeout

    def request(self, method: str, path: str, body: Optional[dict] = None,
                timeout: Optional[float] = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            try:
                text = error.read().decode("utf-8", "replace")[:4000]
            except OSError:
                text = ""
            raise ElasticError(f"Elasticsearch answered {error.code} {text[:300]}".strip(), error.code,
                               text) from None
        except (OSError, ValueError) as error:
            raise ElasticError(f"Elasticsearch is not reachable ({getattr(error, 'reason', error)})") from None

    def search(self, body: dict) -> dict:
        return self.request("POST", "/logstash-*/_search", body)

    def count(self, body: dict) -> int:
        return int(self.request("POST", "/logstash-*/_count", body).get("count") or 0)

    def refresh(self) -> None:
        self.request("POST", "/logstash-*/_refresh")

    def delete_by_query(self, body: dict) -> int:
        answer = self.request("POST", "/logstash-*/_delete_by_query?conflicts=proceed&refresh=true"
                                      "&wait_for_completion=true", body, timeout=DELETE_TIMEOUT)
        return int(answer.get("deleted") or 0)


# -- the logs -----------------------------------------------------------------------------------

class Sudo:
    """root for a log the user may not read or append to. The password of --become-file goes to
    sudo on stdin (-S), never into an argument; without one sudo -n, after one sudo -v at a terminal."""

    def __init__(self, become_file: str = "", run: Callable = subprocess.run,
                 interactive: Optional[Callable[[], bool]] = None):
        self.become_file = become_file
        self._run = run
        self._interactive = interactive or (lambda: sys.stdin.isatty())
        self._asked = False

    def __call__(self, argv: List[str]) -> subprocess.CompletedProcess:
        if self.become_file:
            with open(self.become_file, "rb") as password:
                return self._run(["sudo", "-S", "-p", ""] + argv, stdin=password,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not self._asked and self._interactive():
            self._asked = True
            self._run(["sudo", "-v"])
        return self._run(["sudo", "-n"] + argv, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class Files:
    """The logs below the data folder (tpotctl.checks.logfile): as the user, as root through sudo
    where the user may not; never through a link, never a new file."""

    def __init__(self, data_dir: str, sudo: Optional[Callable] = None):
        self.data_dir = data_dir
        self.sudo = sudo or Sudo()

    def _root(self, *args: str) -> bytes:
        proc = self.sudo([sys.executable if os.path.isabs(sys.executable) else "python3", "-I",
                          os.path.abspath(logfile.__file__)] + list(args))
        if proc.returncode != 0:
            text = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise CheckError(f"not even root can reach {args[2] if len(args) > 2 else ''}: "
                             f"{text[-1] if text else 'sudo failed'}")
        return proc.stdout or b""

    def _do(self, local: Callable, *args: str):
        try:
            return local()
        except PermissionError:
            return self._root(*args)
        except logfile.LogFileError as error:
            raise CheckError(str(error)) from None

    def size(self, rel: str) -> Optional[int]:
        found = self._do(lambda: logfile.size(self.data_dir, rel), "size", self.data_dir, rel)
        if isinstance(found, bytes):
            text = found.decode().strip()
            return None if text in ("", "-") else int(text)
        return found

    def read_from(self, rel: str, offset: int) -> bytes:
        return self._do(lambda: logfile.read_from(self.data_dir, rel, offset),
                        "read", self.data_dir, rel, str(offset))

    def append(self, rel: str, text: str) -> None:
        data = text.encode("utf-8")
        self._do(lambda: logfile.append(self.data_dir, rel, data), "append", self.data_dir, rel, text)

    def listing(self, rel_dir: str, pattern: str) -> List[str]:
        found = self._do(lambda: logfile.listing(self.data_dir, rel_dir, pattern),
                         "list", self.data_dir, rel_dir, pattern)
        if isinstance(found, bytes):
            return [line for line in found.decode("utf-8", "replace").splitlines() if line]
        return found


def _is_glob(rel: str) -> bool:
    return any(char in os.path.basename(rel) for char in "*?[")


class LogWatch:
    """The lines a log got since `mark()`: read on from where the last look ended, only whole lines."""

    def __init__(self, files: Files):
        self.files = files
        self.position: Dict[str, int] = {}
        self.tail: Dict[str, bytes] = {}
        self.lines: Dict[str, List[str]] = {}
        self.errors: Dict[str, str] = {}

    def expand(self, rel: str) -> List[str]:
        if _is_glob(rel):
            return self.files.listing(os.path.dirname(rel), os.path.basename(rel))
        return [rel]

    def mark(self, rel: str) -> None:
        try:
            for name in self.expand(rel):
                self.position[name] = self.files.size(name) or 0
        except (CheckError, OSError) as error:
            self.errors[rel] = str(error)

    def new_lines(self, rel: str) -> List[str]:
        out: List[str] = []
        try:
            names = self.expand(rel)
        except (CheckError, OSError) as error:
            self.errors[rel] = str(error)
            return out
        for name in names:
            try:
                size = self.files.size(name)
                if size is None:
                    continue
                position = self.position.get(name, 0)
                if size < position:          # rotated: the new file from its start
                    position, self.tail[name] = 0, b""
                if size > position:
                    chunk = self.files.read_from(name, position)
                    self.position[name] = position + len(chunk)
                    *whole, rest = (self.tail.get(name, b"") + chunk).split(b"\n")
                    self.tail[name] = rest
                    self.lines.setdefault(name, []).extend(line.decode("utf-8", "replace") for line in whole)
            except (CheckError, OSError) as error:
                self.errors[rel] = str(error)
            out.extend(self.lines.get(name, []))
        return out


# -- the Attack Map -----------------------------------------------------------------------------

class MapListener:
    """redis-cli SUBSCRIBE in map_redis and a WebSocket client in map_web, each into a file."""

    def __init__(self, folder: str, seconds: float, popen: Callable = subprocess.Popen,
                 run: Callable = subprocess.run):
        self.folder = folder
        self.seconds = seconds
        self.popen = popen
        self.run = run
        self.procs: Dict[str, Any] = {}
        self.error = ""

    def path(self, which: str) -> str:
        return os.path.join(self.folder, f"{which}.log")

    def start(self) -> None:
        commands = {
            # bounded inside the container: a docker exec that ends leaves its process running there
            "redis": ["docker", "exec", "map_redis", "timeout", str(int(self.seconds)), "redis-cli", "SUBSCRIBE",
                      stages.REDIS_CHANNEL],
            "ws": ["docker", "exec", "-e", f"WS_SECONDS={int(self.seconds)}", "map_web", "python3", "-c", WS_CLIENT],
        }
        for which, command in commands.items():
            try:
                with open(self.path(which), "wb") as out:
                    self.procs[which] = self.popen(command, stdin=subprocess.DEVNULL, stdout=out,
                                                   stderr=subprocess.STDOUT)
            except OSError as error:
                self.error = f"cannot start the {which} listener: {error}"

    def lines(self, which: str) -> List[str]:
        try:
            with open(self.path(which), encoding="utf-8", errors="replace") as handle:
                return handle.read().splitlines()
        except OSError:
            return []

    def stop(self) -> None:
        """The docker exec clients, then the listeners inside the containers (one check at a time,
        so the patterns are the check's own)."""
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self.procs:
            for command in self.kill_commands():
                try:
                    self.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, timeout=10)
                except (OSError, subprocess.SubprocessError):
                    pass
        self.procs = {}

    @staticmethod
    def kill_commands() -> List[List[str]]:
        return [["docker", "exec", "map_redis", "pkill", "-f", f"redis-cli SUBSCRIBE {stages.REDIS_CHANNEL}"],
                ["docker", "exec", "map_web", "pkill", "-f", WS_MARK]]


# -- addresses ----------------------------------------------------------------------------------

def own_addresses(host: str = "", interfaces: Optional[Callable] = None) -> List[str]:
    """The addresses of this host (every interface, the Docker bridges too), loopback and the
    address the probes go to: the sources the probes come from."""
    from tpotctl import netinfo
    found = {"127.0.0.1", "::1"}
    if host:
        found.add(host)
    for interface in (interfaces or netinfo.interfaces)():
        for address in interface.addresses:
            found.add(address.split("/")[0].split("%")[0])
    return sorted(found)


def probe_address(host_address: Optional[Callable[[], str]] = None, interfaces: Optional[Callable] = None) -> str:
    """The host's own address (the route to the internet), else the first global IPv4 address."""
    from tpotctl import netinfo, ops
    address = (host_address or ops.host_address)()
    if address:
        return address
    for interface in (interfaces or netinfo.interfaces)():
        if interface.up and interface.global_v4 and not interface.internal:
            for entry in interface.addresses:
                candidate = entry.split("/")[0]
                if ":" not in candidate:
                    return candidate
    raise CheckError("no address of this host found to probe (no route to the internet, no global IPv4 address)")


def left_text(left: int, sourceless: int = 0) -> str:
    """The probe events a cleanup leaves, in words."""
    text = f"{left} probe events could not be told apart from real ones and stay"
    if sourceless:
        text += f" ({sourceless} of them without a source address)"
    return text


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + secrets.token_hex(3)


# -- the report ---------------------------------------------------------------------------------

@dataclass
class Report:
    run_id: str = ""
    token: str = ""
    mode: str = "honeypots"
    started: Optional[datetime] = None
    ended: Optional[datetime] = None
    host: str = ""
    tpot_type: str = "HIVE"
    options: Dict[str, Any] = field(default_factory=dict)
    rows: List[Row] = field(default_factory=list)
    exit_code: int = EXIT_OK
    result: str = "ok"
    error: str = ""
    ewsposter: Dict[str, Any] = field(default_factory=lambda: {"state": "none", "detail": ""})
    cleanup: Dict[str, Any] = field(default_factory=lambda: {"state": "none", "deleted": 0, "passes": [], "left": 0,
                                                             "detail": ""})
    kibana: str = ""
    written: str = ""

    @property
    def failed(self) -> List[Row]:
        return [row for row in self.rows if row.failed]

    def as_json(self) -> Dict[str, Any]:
        return {
            "version": RESULT_VERSION, "run_id": self.run_id, "token": self.token, "mode": self.mode,
            "started": stages.iso(self.started) if self.started else "",
            "ended": stages.iso(self.ended) if self.ended else "",
            "host": self.host, "tpot_type": self.tpot_type, "options": self.options,
            "exit_code": self.exit_code, "result": self.result, "error": self.error,
            "ewsposter": self.ewsposter, "cleanup": self.cleanup, "kibana": self.kibana,
            "stages": list(stages.STAGES), "titles": dict(stages.TITLES),
            "honeypots": [row.as_json() for row in self.rows],
        }


def write_result(report: Report, path: Optional[str] = None) -> str:
    """last.json of this user, written whole (temp file + replace), 0600."""
    path = path or result_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".last.", suffix=".json", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(report.as_json(), out, indent=1, ensure_ascii=False)
            out.write("\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise
    return path


# the results last.json may name, and the most it may hold (the Checks page reads it whole)
RESULTS = ("ok", "failed", "hold failed", "cancelled", "error")
RESULT_MAX = 8 * 2 ** 20


class ResultError(ValueError):
    """last.json cannot be read, is of another version or not of its form."""


def read_result(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """last.json as the Checks page shows it, checked and with every key in place (result_view);
    None when no check has run yet."""
    path = path or result_path()
    try:
        with open(path, "rb") as handle:
            raw = handle.read(RESULT_MAX + 1)
    except FileNotFoundError:
        return None
    except OSError as err:
        raise ResultError(f"{path}: {err.strerror or err}") from None
    if len(raw) > RESULT_MAX:
        raise ResultError(f"{path}: larger than {RESULT_MAX} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ResultError(f"{path}: no JSON") from None
    return result_view(data)


def _form(test: bool, what: str) -> None:
    if not test:
        raise ResultError(f"no result of version {RESULT_VERSION}: {what}")


def _text(data: Dict[str, Any], key: str, what: str) -> str:
    value = data.get(key, "")
    _form(isinstance(value, str), f"{what}{key}")
    return value


def _number(data: Dict[str, Any], key: str, what: str) -> int:
    value = data.get(key, 0)
    _form(isinstance(value, int) and not isinstance(value, bool), f"{what}{key}")
    return value


def _flag(data: Dict[str, Any], key: str, what: str) -> bool:
    value = data.get(key, False)
    _form(isinstance(value, bool), f"{what}{key}")
    return value


def _part(data: Dict[str, Any], key: str, what: str) -> Dict[str, Any]:
    value = data.get(key)
    value = {} if value is None else value
    _form(isinstance(value, dict), f"{what}{key}")
    return value


def result_view(data: Any) -> Dict[str, Any]:
    """A result as read from last.json, checked against version 1 of its form (ResultError if not):
    a copy with every key the page uses, of the type it has there; a stage a honeypot does not name
    is not_reached."""
    _form(isinstance(data, dict), "no object")
    _form(data.get("version") == RESULT_VERSION and isinstance(data.get("version"), int), "version")
    names = data.get("stages")
    _form(isinstance(names, list) and bool(names) and all(isinstance(n, str) and n for n in names)
          and len(set(names)) == len(names), "stages")
    titles = _part(data, "titles", "")
    _form(all(isinstance(v, str) for v in titles.values()), "titles")
    result = _text(data, "result", "")
    _form(result in RESULTS, "result")
    view: Dict[str, Any] = {key: _text(data, key, "") for key in
                            ("run_id", "token", "mode", "started", "ended", "host", "tpot_type", "error",
                             "kibana")}
    view.update(version=RESULT_VERSION, result=result, exit_code=_number(data, "exit_code", ""),
                stages=list(names), titles={name: titles.get(name) or name for name in names})
    options = _part(data, "options", "")
    view["options"] = {"geo": _flag(dict({"geo": True}, **options), "geo", "options."),
                       "keep": _flag(options, "keep", "options.")}
    ews = _part(data, "ewsposter", "")
    view["ewsposter"] = {"state": _text(ews, "state", "ewsposter."), "detail": _text(ews, "detail", "ewsposter.")}
    cleanup = _part(data, "cleanup", "")
    passes = cleanup.get("passes", [])
    _form(isinstance(passes, list) and all(isinstance(n, int) and not isinstance(n, bool) for n in passes),
          "cleanup.passes")
    view["cleanup"] = {"state": _text(cleanup, "state", "cleanup."), "deleted": _number(cleanup, "deleted", "cleanup."),
                       "passes": list(passes), "left": _number(cleanup, "left", "cleanup."),
                       "left_no_source": _number(cleanup, "left_no_source", "cleanup."),
                       "left_kibana": _text(cleanup, "left_kibana", "cleanup."),
                       "detail": _text(cleanup, "detail", "cleanup.")}
    rows = data.get("honeypots", [])
    _form(isinstance(rows, list), "honeypots")
    view["honeypots"] = [_row_view(row, names) for row in rows]
    return view


def _row_view(row: Any, names: List[str]) -> Dict[str, Any]:
    _form(isinstance(row, dict), "a honeypot")
    name = _text(row, "name", "honeypot.")
    _form(bool(name), "honeypot.name")
    what = f"{name}."
    view: Dict[str, Any] = {key: _text(row, key, what) for key in
                            ("name", "group", "container", "logstash_type", "log_line", "es_id", "es_index")}
    view.update(running=_flag(row, "running", what), external=_flag(row, "external", what))
    ports = row.get("ports", [])
    _form(isinstance(ports, list) and all(isinstance(p, (str, int)) and not isinstance(p, bool) for p in ports),
          f"{what}ports")
    view["ports"] = [str(port) for port in ports]
    found = _part(row, "stages", what)
    view["stages"] = {}
    for stage in names:
        entry = found.get(stage)
        if entry is None:
            view["stages"][stage] = {"state": NOT_REACHED, "detail": ""}
            continue
        _form(isinstance(entry, dict), f"{what}stages.{stage}")
        state = _text(entry, "state", f"{what}stages.{stage}.")
        _form(state in stages.STATES, f"{what}stages.{stage}.state")
        view["stages"][stage] = {"state": state, "detail": _text(entry, "detail", f"{what}stages.{stage}.")}
    geo = row.get("geo")
    if geo is None:
        view["geo"] = None
        return view
    _form(isinstance(geo, dict), f"{what}geo")
    view["geo"] = {key: _text(geo, key, f"{what}geo.") for key in ("src_ip", "file", "line", "es_id", "es_index")}
    view["geo"]["src_port"] = _number(geo, "src_port", f"{what}geo.")
    location = _part(geo, "location", f"{what}geo.")
    shown = {}
    for key, value in location.items():
        # what Elasticsearch had: a number, a text, a list of them (ip_rep)
        values = value if isinstance(value, list) else [value]
        _form(all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in values),
              f"{what}geo.location.{key}")
        shown[key] = ", ".join(str(v) for v in values)
    view["geo"]["location"] = shown
    return view


# -- pending cleanups ---------------------------------------------------------------------------

PENDING_VERSION = 1
PENDING_MAX = 2 ** 20
# the window of a run whose last.json is gone: up to the time of its file + this
PENDING_SLACK = 120.0


def pending_dir(result_file: Optional[str] = None) -> str:
    """checks/pending next to last.json."""
    return os.path.join(os.path.dirname(result_file or result_path()), "pending")


def own_json(own: stages.OwnEvents) -> Dict[str, Any]:
    return {"token": own.token, "source": own.source, "probes": {k: sorted(set(v)) for k, v in own.probes.items()},
            "probed": list(own.probed), "token_types": list(own.token_types),
            "geos": [[kind, ip, port] for kind, ip, port in own.geos]}


def portless_types() -> List[str]:
    """The Logstash types whose fixture carries no source port: only their geo events are matched
    by test IP and type alone (a geo entry without a port)."""
    return sorted({fixtures.logstash_type(name) for name in fixtures.names()
                   if name in LOGS and "{src_port}" not in fixtures.template(name)})


def known_types() -> List[str]:
    """Every type the check may name in a cleanup: those of inventory.LOGS but the passive tools."""
    return sorted({kind for kind, _path in LOGS.values()} - set(stages.NEVER_DELETED))


# the most source ports a pending file may name per type (a probe records one per connection)
PENDING_MAX_PORTS = 128


def own_from_json(data: Any, host: str = "", types: Optional[Iterable[str]] = None) -> stages.OwnEvents:
    """OwnEvents of a pending file, bound to what a run of this check can have sent: the types of
    `types` (default every known type, never Suricata / P0f), the source empty or the recorded host
    (IPv4), at most PENDING_MAX_PORTS ports of 1 to 65535 per type, geo events with a test IP and a
    marker port (no port only for the port-less fixtures); ValueError for anything else."""
    allowed = set(known_types() if types is None else types) - set(stages.NEVER_DELETED)
    portless = set(portless_types())
    markers = range(stages.MARKER_PORT, stages.MARKER_PORT + len(LOGS))

    def kinds(value: Any, what: str) -> List[str]:
        if not (isinstance(value, list) and all(isinstance(v, str) and v in allowed for v in value)):
            raise ValueError(f"{what}: a type this T-Pot does not run or the check never removes")
        return list(value)

    def port(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 65535

    if not isinstance(data, dict):
        raise ValueError("no object")
    token = data.get("token")
    if not (isinstance(token, str) and token.startswith(probes.TOKEN_PREFIX) and fixtures.TOKEN.fullmatch(token)):
        raise ValueError("token")
    source = data.get("source", "")
    if not isinstance(source, str):
        raise ValueError("source")
    if source:
        address = ipaddress.ip_address(source)
        if source != host or address.version != 4 or address.is_loopback:
            raise ValueError("source: not the recorded host (an IPv4 address of it)")
    found = data.get("probes", {})
    if not (isinstance(found, dict) and all(isinstance(v, list) and len(v) <= PENDING_MAX_PORTS
                                            and all(port(p) for p in v) for v in found.values())):
        raise ValueError(f"probes: lists of at most {PENDING_MAX_PORTS} ports")
    kinds(list(found), "probes")
    geos = []
    entries = data.get("geos", [])
    if not isinstance(entries, list) or len(entries) > len(LOGS):
        raise ValueError("geos")
    for entry in entries:
        if not (isinstance(entry, list) and len(entry) == 3 and isinstance(entry[0], str) and entry[0] in allowed
                and entry[1] in TEST_IPS):
            raise ValueError("geos: a type or a test IP the check never uses")
        if entry[2] is None:
            if entry[0] not in portless:
                raise ValueError(f"geos: {entry[0]} logs a source port, its event has a marker port")
        elif not (port(entry[2]) and entry[2] in markers):
            raise ValueError("geos: no marker port")
        geos.append(tuple(entry))
    return stages.OwnEvents(token, source, {k: list(v) for k, v in found.items()}, kinds(data.get("probed", []), "probed"),
                            kinds(data.get("token_types", []), "token_types"), geos)


@dataclass
class Pending:
    """The cleanup of an earlier run that did not run through."""
    run_id: str
    path: str
    started: datetime
    end: datetime
    own: stages.OwnEvents
    with_token: bool = True
    host: str = ""
    result_file: Optional[str] = None      # last.json of this user: its cleanup is noted there


def _parse_time(text: Any) -> datetime:
    if not isinstance(text, str) or not text:
        raise ValueError("time")
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# the most a run can last beyond its stages (the probes, the settle, both cleanup passes) plus a
# margin: the end of a pending window is never later than its start + this + 3 timeouts
PENDING_RUN_MAX = 120.0 + 15.0 + 60.0 + 2 * 3600.0
# the timeout a pending file may name at most (for the bound of its window)
PENDING_TIMEOUT_MAX = 3600.0


def run_time(run_id: str) -> datetime:
    """The UTC time a run id starts with (new_run_id: %Y%m%d%H%M%S + 6 hex digits)."""
    if not (isinstance(run_id, str) and len(run_id) == 20 and run_id[:14].isdigit()
            and all(c in "0123456789abcdef" for c in run_id[14:])):
        raise ValueError("run_id: not of the form of a run id")
    return datetime.strptime(run_id[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def read_pending(result_file: Optional[str] = None,
                 types: Optional[Iterable[str]] = None) -> Tuple[List[Pending], List[str]]:
    """The pending cleanups (oldest first) and the files that are refused (path: why), which stay
    and delete nothing. A file is bound to its run: its start within 2 minutes of the time of its run
    id, its host an address, its events those own_from_json takes (`types`: the Logstash types of
    the inventory). The end of its window is the end its last.json names, else the time of its file
    + PENDING_SLACK, at most its start + PENDING_RUN_MAX + 3 timeouts."""
    folder = pending_dir(result_file)
    try:
        names = sorted(name for name in os.listdir(folder) if name.endswith(".json") and not name.startswith("."))
    except FileNotFoundError:
        return [], []
    except OSError as error:
        return [], [f"{folder}: {error.strerror or error}"]
    try:
        last = read_result(result_file)
    except ResultError:
        last = None
    found: List[Pending] = []
    broken: List[str] = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            handle = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            try:
                info = os.fstat(handle)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("no regular file")
                raw = os.read(handle, PENDING_MAX + 1)
            finally:
                os.close(handle)
            if len(raw) > PENDING_MAX:
                raise ValueError(f"larger than {PENDING_MAX} bytes")
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("version") != PENDING_VERSION:
                raise ValueError(f"no pending cleanup of version {PENDING_VERSION}")
            run_id = data.get("run_id")
            if not (isinstance(run_id, str) and probes._RUN_ID.match(run_id) and name == f"{run_id}.json"):
                raise ValueError("run_id")
            started = _parse_time(data.get("started"))
            if abs((started - run_time(run_id)).total_seconds()) > 120:
                raise ValueError("started: not the time of its run id")
            timeout = data.get("timeout", TIMEOUT)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout:
                raise ValueError("timeout")
            if last is not None and last.get("run_id") == run_id and last.get("ended"):
                end = _parse_time(last["ended"])
            else:
                end = datetime.fromtimestamp(info.st_mtime, timezone.utc) + timedelta(seconds=PENDING_SLACK)
            bound = started + timedelta(seconds=PENDING_RUN_MAX + 3 * min(float(timeout), PENDING_TIMEOUT_MAX))
            end = min(end, bound)
            if end < started:
                raise ValueError("the end of its window lies before its start")
            with_token = data.get("with_token", True)
            host = data.get("host", "")
            if not isinstance(with_token, bool) or not isinstance(host, str):
                raise ValueError("with_token / host")
            ipaddress.ip_address(host)
            found.append(Pending(run_id, path, started, end, own_from_json(data.get("own"), host, types),
                                 with_token, host, result_file))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            broken.append(f"{path}: {getattr(error, 'strerror', None) or error}")
    found.sort(key=lambda pending: pending.started)
    return found, broken


def write_pending(path: str, data: Dict[str, Any]) -> None:
    """A pending file, written whole (temp file + replace), 0600 in a folder 0700."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    handle, temp = tempfile.mkstemp(prefix=".pending.", suffix=".json", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=1)
            out.write("\n")
            out.flush()
            os.fchmod(out.fileno(), 0o600)
            os.fsync(out.fileno())
        os.replace(temp, path)
        # the rename on disk too: a reboot is the case the file is for
        folder_fd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(folder_fd)
        finally:
            os.close(folder_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def delete_own(elastic: "Elastic", started: datetime, end: datetime, own: stages.OwnEvents, with_token: bool,
               warn: Callable[[str], None]) -> Tuple[int, bool]:
    """One _delete_by_query of a run's own events; (deleted, with_token): without the token clause
    once Elasticsearch refuses it (ElasticError.token_refused)."""
    body = stages.cleanup_query(started, end, own, with_token)
    if body is None:
        return 0, with_token
    try:
        return elastic.delete_by_query(body), with_token
    except ElasticError as error:
        if not (error.token_refused and with_token):
            raise
        warn("Elasticsearch refused the token query, the cleanup goes by test IP, marker port and source port only")
        body = stages.cleanup_query(started, end, own, False)
        return (elastic.delete_by_query(body) if body is not None else 0), False


def left_over(elastic: "Elastic", started: datetime, end: datetime, own: stages.OwnEvents,
              host: str) -> Tuple[int, int]:
    """(left, of them without a source): the probe events a cleanup could not tell apart."""
    query = stages.leftover_query(started, end, own, host)
    left = elastic.count(query) if query is not None else 0
    query = stages.sourceless_query(started, end, own)
    sourceless = elastic.count(query) if query is not None else 0
    return left + sourceless, sourceless


def run_pending(pending: Pending, elastic: "Elastic", warn: Callable[[str], None]) -> Tuple[int, int, int]:
    """The cleanup of an earlier run, one pass (its events are indexed long since); the file goes
    when it ran through. (deleted, left, of them without a source); ElasticError when it did not."""
    with contextlib.suppress(ElasticError):
        elastic.refresh()
    deleted, _with_token = delete_own(elastic, pending.started, pending.end, pending.own, pending.with_token, warn)
    left, sourceless = left_over(elastic, pending.started, pending.end, pending.own, pending.host)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(pending.path)
    try:
        note_cleanup(pending.result_file, pending.run_id, deleted, left, sourceless)
    except OSError as error:
        # the events are gone all the same; only last.json does not say so
        warn(f"last.json could not note the cleanup of {pending.run_id} ({error.strerror or error})")
    return deleted, left, sourceless


def note_cleanup(result_file: Optional[str], run_id: str, deleted: int, left: int, sourceless: int) -> bool:
    """The cleanup of a run done later (tpot check cleanup, the next check): last.json says so when it
    is that run's, so the Checks page no longer says the test events are still there. True when it
    was noted; an unreadable or another run's last.json stays as it is."""
    path = result_file or result_path()
    try:
        with open(path, "rb") as handle:
            raw = handle.read(RESULT_MAX + 1)
        data = json.loads(raw.decode("utf-8")) if len(raw) <= RESULT_MAX else None
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict) or data.get("run_id") != run_id:
        return False
    data["cleanup"] = {"state": "done", "deleted": deleted, "passes": [deleted], "left": left,
                       "left_no_source": sourceless, "left_kibana": "",
                       "detail": "removed later (tpot check cleanup or the next check)"}
    folder = os.path.dirname(path)
    handle, temp = tempfile.mkstemp(prefix=".last.", suffix=".json", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=1, ensure_ascii=False)
            out.write("\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise
    return True


# -- the run ------------------------------------------------------------------------------------

class Check:
    """One run of the check; every outside part can be handed in (the tests use fakes)."""

    def __init__(self, options: Options, repo_dir: Optional[str] = None, *,
                 env: Optional[Dict[str, str]] = None,
                 honeypots: Optional[Callable[[], List[Honeypot]]] = None,
                 compose_names: Optional[List[str]] = None,
                 hold: Optional[Callable[[str, List[str]], Any]] = None,
                 run_probes: Callable = probes.run_probes,
                 elastic: Optional[Elastic] = None,
                 files: Optional[Files] = None,
                 listener: Optional[Callable[[str, float], MapListener]] = None,
                 host: Optional[Callable[[], str]] = None,
                 addresses: Optional[Callable[[str], List[str]]] = None,
                 run_id: Optional[str] = None,
                 log: Callable[[str, str], None] = _say,
                 journal: Optional[str] = None,
                 mark: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime] = now_utc,
                 web_port: Optional[int] = None,
                 result_file: Optional[str] = None,
                 log_timeout: float = LOG_TIMEOUT, poll: float = POLL):
        from tpotctl import ops, system
        from tpotctl.bootstrap import REPO_DIR
        self.options = options
        self.repo_dir = repo_dir or REPO_DIR
        self.env = ops.env_values(self.repo_dir) if env is None else env
        self.data_dir = system.data_path(self.env, self.repo_dir)
        self.sensor = self.env.get("TPOT_TYPE", "HIVE").upper() == "SENSOR"
        self._honeypots = honeypots
        self._compose_names = compose_names
        self._hold = hold
        self._run_probes = run_probes
        self.elastic = elastic or Elastic()
        self.files = files or Files(self.data_dir, Sudo(options.become_file))
        self._listener = listener
        self._host = host
        self._addresses = addresses
        self.run_id = run_id or new_run_id()
        self.token = probes.token_for(self.run_id)
        self._say = log
        self._mark = mark
        self.journal_file = journal or os.path.join(os.path.dirname(result_file or result_path()), "last.log")
        self._journal = None
        self._deferred: Optional[List[tuple]] = None   # the end of the run: output after last.json
        self.entered = False            # the hold was taken: the run got past its start
        self.clock = clock
        self.sleep = sleep
        self.now = now
        self._web_port = web_port
        self.result_file = result_file
        self.log_timeout = log_timeout
        self.poll = poll
        self.with_token = True          # Elasticsearch takes the token query (else sources only)
        self.signals = Signals(noted=self._cancel_noted)
        self.watch = LogWatch(self.files)
        self.rows: Dict[str, Row] = {}
        self.results: Dict[str, probes.ProbeResult] = {}
        self.honeypots: List[Honeypot] = []
        self.addresses: List[str] = []
        self.host = ""
        self.sent = False               # a probe or a geo event went out: the cleanup has work
        self.map: Optional[MapListener] = None
        # the honeypots whose ewsposter module cannot be held: no probe, no geo event (named at the
        # ack, or later: before each probe and each geo event the check asks hold.ack again)
        self.not_held: List[str] = []
        self._late: List[str] = []
        self._late_lock = threading.Lock()
        # what the cleanup of this run needs, while it has not run through (checks/pending)
        self.pending_file = os.path.join(pending_dir(result_file), f"{self.run_id}.json")
        self._planned: Optional[stages.OwnEvents] = None
        self._pending_written = False
        self.earlier: Dict[str, Any] = {}       # the pending cleanups this run ran first

    # -- output --

    # Every line goes into last.log, then to stdout / the say of the CLI. A closed stdout (the
    # Manager killed, an SSH hang-up) never stops the run: the end of the hold and the cleanup go
    # on. During the end, output waits until last.json is written (flush()).

    def _open_journal(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.journal_file), mode=0o700, exist_ok=True)
            handle = os.open(self.journal_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                             0o600)
            self._journal = os.fdopen(handle, "w", encoding="utf-8", buffering=1)
        except OSError:
            self._journal = None

    def _close_journal(self) -> None:
        if self._journal is not None:
            with contextlib.suppress(OSError):
                self._journal.close()
            self._journal = None

    def _write_journal(self, line: str) -> None:
        if self._journal is not None:
            try:
                self._journal.write(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {line}\n")
            except (OSError, ValueError):
                self._journal = None

    def _out(self, what: str, *args: str) -> None:
        try:
            if what == "mark":
                if self._mark is not None:
                    self._mark(args[0])
                elif os.environ.get("TPOT_MARKS") == "1":
                    print(args[0], flush=True)
            else:
                self._say(*args)
        except (OSError, ValueError, RuntimeError):     # BrokenPipeError, EIO, a closed file, reentrant
            pass

    def _cancel_noted(self) -> None:
        text = "Cancel noted, finishing the cleanup first."
        self._write_journal(f"warn: {text}")
        self._out("log", "warn", text)

    def flush(self) -> None:
        deferred, self._deferred = self._deferred or [], None
        for item in deferred:
            self._out(*item)

    def log(self, kind: str, text: str) -> None:
        self._write_journal(f"{kind}: {text}")
        if self._deferred is not None:
            self._deferred.append(("log", kind, text))
        else:
            self._out("log", kind, text)

    def mark(self, *words: str) -> None:
        line = "@@tpot " + " ".join(words)
        self._write_journal(line)
        if self._deferred is not None:
            self._deferred.append(("mark", line))
        else:
            self._out("mark", line)

    def phase(self, key: str, title: str) -> None:
        self.mark("phase", key, title)

    # -- what runs --

    def compose_names(self) -> List[str]:
        if self._compose_names is None:
            from tpotctl import ops
            self._compose_names = ops.compose_names(self.repo_dir, self.env)
        return self._compose_names

    def inventory(self) -> List[Honeypot]:
        if self._honeypots is not None:
            found = self._honeypots()
        else:
            from tpotctl.checks.inventory import inventory
            found = inventory(self.repo_dir, self.env)
        if not self.options.only:
            return found
        names = {h.name for h in found}
        unknown = [name for name in self.options.only if name not in names]
        if unknown:
            raise UsageError(f"not a honeypot of this T-Pot: {', '.join(unknown)} (it runs "
                             f"{', '.join(sorted(names)) or 'none'})")
        return [h for h in found if h.name in self.options.only]

    def map_here(self) -> bool:
        return all(name in self.compose_names() for name in MAP_CONTAINERS)

    def geo_targets(self) -> List[Honeypot]:
        """The honeypots that get a geo event: running, with a fixture, on a HIVE, unless --no-geo.
        The passive tools (p0f, suricata) get none: they write their logs without a pause, a line
        of the check could land inside one of theirs."""
        if self.sensor or not self.options.geo:
            return []
        names = set(fixtures.names())
        return [h for h in self.honeypots
                if h.running and h.name in names and h.name in LOGS and h.group not in PASSIVE_GROUPS
                and h.name not in self.not_held]

    def geo_event(self, name: str) -> GeoEvent:
        """The test IP and the marker port of a honeypot's geo event: the n-th honeypot of the
        inventory (with a log) gets MARKER_PORT + n and the test IPs in turn."""
        names = sorted(h.name for h in self.honeypots if h.name in LOGS)
        index = names.index(name)
        return GeoEvent(TEST_IPS[index % len(TEST_IPS)], stages.MARKER_PORT + index, fixtures.log_file(name),
                        marker="{src_port}" in fixtures.template(name))

    # -- the run --

    def plan(self) -> List[str]:
        """--dry-run: what the check would do, nothing is touched (no hold, no probe, no line)."""
        self.honeypots = self.inventory()
        lines = [f"Run id {self.run_id}, token {self.token}"]
        found, _broken = read_pending(self.result_file) if not self.sensor else ([], [])
        if found:
            lines.append("first the test events of earlier checks whose cleanup did not run through: "
                         + ", ".join(pending.run_id for pending in found))
        for honeypot in self.honeypots:
            what = "external: " + stages.EXTERNAL_HINT if honeypot.external else (
                "not running" if not honeypot.running else
                ("probe " + ", ".join(str(p) for p in honeypot.ports) if not self.options.geo_only else "no probe"))
            lines.append(f"{honeypot.name}: {what}")
        start = self.now()
        for honeypot in self.geo_targets():
            event = self.geo_event(honeypot.name)
            line = self.render(honeypot, event, start)
            lines.append(f"{honeypot.name} geo event -> {event.file}: {line}")
        if not self.options.keep and not self.sensor:
            own = stages.OwnEvents(self.token)
            if not self.options.geo_only:
                own.probed = own.token_types = sorted({h.logstash_type for h in self.honeypots
                                                       if h.running and not h.external})
            for honeypot in self.geo_targets():
                event = self.geo_event(honeypot.name)
                own.geos.append((honeypot.logstash_type, event.src_ip, event.src_port if event.marker else None))
            body = stages.cleanup_query(start, start, own)
            lines.append("cleanup (with the source ports of the probes besides): POST logstash-*/_delete_by_query "
                         + json.dumps(body))
        return lines

    def render(self, honeypot: Honeypot, event: GeoEvent, moment: datetime) -> str:
        dst = honeypot.ports[0].target if honeypot.ports else 80
        return fixtures.render(honeypot.name, moment, event.src_ip, event.src_port, dst, self.token)

    def run(self) -> Report:
        report = Report(run_id=self.run_id, token=self.token, mode=self.options.mode,
                        tpot_type="SENSOR" if self.sensor else "HIVE",
                        options={"only": list(self.options.only), "geo": self.options.geo,
                                 "keep": self.options.keep, "timeout": self.options.timeout})
        report.started = self.now()
        self.started = report.started
        self._open_journal()
        usage: Optional[UsageError] = None
        try:
            # the handlers come first: a signal from here on ends the hold it may have taken
            with self.signals:
                self._run(report)
        except (KeyboardInterrupt, Terminated):
            report.exit_code, report.result = EXIT_CANCELLED, "cancelled"
        except CheckError as error:     # before the hold (inventory, address, hold set-up)
            report.exit_code, report.result, report.error = EXIT_FAILED, "error", str(error)
            if isinstance(error, UsageError):
                report.exit_code, usage = EXIT_CANNOT_ASK, error
            if not self.entered:
                self.rows = {}
        except Exception as error:      # a fault of the check itself; the hold ended in _end all the same
            import traceback
            self._write_journal("error: " + "".join(traceback.format_exception(type(error), error,
                                                                               error.__traceback__)).rstrip())
            report.exit_code, report.result = EXIT_FAILED, "error"
            report.error = f"{type(error).__name__}: {error}"
            self._deferred = self._deferred if self._deferred is not None else []
            self.log("error", f"The check ended with an error: {report.error} (details in {self.journal_file})")
            if not self.entered:
                self.rows = {}
        report.ended = report.ended or self.now()
        report.rows = list(self.rows.values())
        if report.exit_code == EXIT_OK and self.signals.received:
            report.exit_code, report.result = EXIT_CANCELLED, "cancelled"
        if report.exit_code == EXIT_OK and (report.failed or report.cleanup.get("state") == "failed"):
            report.exit_code, report.result = EXIT_FAILED, "failed"
        try:
            report.written = write_result(report, self.result_file)
        except OSError as error:
            self._deferred = self._deferred if self._deferred is not None else []
            self.log("warn", f"the result could not be written: {error}")
        self.flush()
        self.phase("done", "Done")
        self._close_journal()
        if usage is not None:
            raise usage
        return report

    def _run(self, report: Report) -> None:
        self.phase("inventory", "Reading the honeypots of this T-Pot")
        self.honeypots = self.inventory()
        self.rows = {h.name: stages.row_for(h) for h in self.honeypots}
        self.host = (self._host or probe_address)()
        report.host = self.host
        self.addresses = (self._addresses or own_addresses)(self.host)
        if self.sensor:
            for row in self.rows.values():
                for name in stages.STAGES[3:]:
                    row.set(name, NOT_APPLICABLE, ON_THE_HIVE)

        if not self.sensor:
            self._earlier_cleanups()

        self.phase("hold", "Holding ewsposter")
        hold = self._make_hold()
        held = False
        failure: Optional[BaseException] = None
        try:
            try:
                held = True     # from here on the end runs, however the enter ends (EwsHold gives back itself)
                hold.__enter__()
            except CheckError as error:
                held = False
                self.mark("fail", "hold")
                self.log("error", str(error))
                report.exit_code, report.result, report.error = EXIT_HOLD, "hold failed", str(error)
                report.ewsposter = {"state": "failed", "detail": str(error)}
                return
            self.entered = True
            self._held(hold, report)
        except HoldError as error:      # the hold ended during the check: the check stops
            failure = error
            report.error = str(error)
        except PendingError as error:   # before the first probe: nothing was probed (exit 3)
            self.mark("fail", "hold")
            self.log("error", str(error))
            report.exit_code, report.result, report.error = EXIT_HOLD, "error", str(error)
        finally:
            if held:
                try:
                    self._end(hold, report, failure)
                finally:
                    # also after a cancel: last.json says whether ewsposter held
                    self._ewsposter(hold, report, failure)

    def _end(self, hold, report: Report, failure: Optional[BaseException]) -> None:
        """The end of every run that took the hold: the settle for the last lines, the end of the
        hold, then the cleanup (Elasticsearch only, no log lines). Output waits for last.json, signals
        are only noted."""
        with self.signals.shield():
            if self.sent:
                # no result in it, so it may go out before the hold ends; never a problem for _out
                seconds = END_SETTLE + (0 if self.sensor or self.options.keep else SECOND_PASS + 5)
                text = (f"Ending the check: the hold of ewsposter ends, then the cleanup (up to about "
                        f"{seconds:g} s)." if seconds > END_SETTLE else
                        f"Ending the check: the hold of ewsposter ends (about {seconds:g} s).")
                self._write_journal(f"info: {text}")
                self._out("log", "info", text)
            self._deferred = self._deferred if self._deferred is not None else []
            if failure is not None:
                self.log("error", str(failure))
            try:
                if self.sent:
                    # the last lines of the honeypots (some write when a connection closes)
                    self.sleep(END_SETTLE)
            finally:
                try:
                    hold.__exit__(None, None, None)
                finally:
                    report.ended = self.now()
                    self.phase("release", "Lifting the hold of ewsposter")
                    self._cleanup(report)

    def _earlier_cleanups(self) -> None:
        """The cleanups earlier checks could not finish, before this one takes the hold (they are
        the check's own events by the same rules), under the lock of the checks: never those of a
        check that runs."""
        try:
            if not any(name.endswith(".json") and not name.startswith(".")
                       for name in os.listdir(pending_dir(self.result_file))):
                return
        except OSError:
            return
        lock = self._make_lock()
        try:
            lock.__enter__()
        except CheckError:
            # another check runs (its own file is pending): its hold fails as it does without this
            return
        try:
            found, broken = read_pending(self.result_file, self.inventory_types())
            for problem in broken:
                self.log("warn", f"a pending cleanup is refused and stays (nothing deleted): {problem}")
            if not found:
                return
            self.phase("pending", "Removing the test events of earlier checks")
            self.log("info", f"An earlier check could not remove its test events ({len(found)} run"
                             f"{'s' if len(found) != 1 else ''}): this check removes them first.")
            deleted = left = 0
            for pending in found:
                try:
                    removed, stay, sourceless = run_pending(pending, self.elastic, lambda text: self.log("warn", text))
                except ElasticError as error:
                    self.log("warn", f"The test events of the check {pending.run_id} stay for now: {error}")
                    continue
                deleted, left = deleted + removed, left + stay
                self.log("ok", f"Check {pending.run_id}: {removed} test events removed"
                               + (f"; {left_text(stay, sourceless)}" if stay else "."))
            self.earlier = {"runs": len(found), "deleted": deleted, "left": left}
        finally:
            lock.__exit__(None, None, None)

    def inventory_types(self) -> List[str]:
        """The Logstash types of the honeypots of this T-Pot: what a pending cleanup may name."""
        return sorted({h.logstash_type for h in self.honeypots if h.logstash_type})

    def _make_lock(self):
        """The lock of the checks alone (no marker): an EwsHold without ewsposter in its names."""
        if self._hold is not None:
            return self._hold(self.data_dir, [])
        from tpotctl.checks.ewshold import EwsHold
        return EwsHold(self.data_dir, [], log=lambda kind, text: None, settle=0)

    # -- the pending file of this run --

    def _plan_own(self) -> stages.OwnEvents:
        """What this run is about to send (its probes and geo events), before it sends anything: every
        probed type may carry the token; the source ports come with the probes."""
        own = stages.OwnEvents(self.token, self.host if self.ports_survive() else "")
        if not self.options.geo_only:
            kinds = {h.logstash_type for h in self.honeypots if h.running and not h.external and h.logstash_type
                     and h.name not in self.not_held}
            own.probed, own.token_types = sorted(kinds), sorted(kinds)
        for honeypot in self.geo_targets():
            event = self.geo_event(honeypot.name)
            own.geos.append((honeypot.logstash_type, event.src_ip, event.src_port if event.marker else None))
            if "{token}" in fixtures.template(honeypot.name):
                own.token_types = sorted(set(own.token_types) | {honeypot.logstash_type})
        return own

    def _save_pending(self) -> None:
        """checks/pending/<run id>.json with what the cleanup needs now: the plan and what was sent."""
        if self.sensor or self.options.keep:
            return
        if self._planned is None:
            self._planned = self._plan_own()
        own, planned = self.own_events(), self._planned
        own.probed = sorted(set(own.probed) | set(planned.probed))
        own.token_types = sorted(set(own.token_types) | set(planned.token_types))
        own.geos = sorted(set(own.geos) | set(planned.geos), key=lambda geo: (geo[0], geo[1], geo[2] or 0))
        own.source = own.source or planned.source
        data = {"version": PENDING_VERSION, "run_id": self.run_id, "started": stages.iso(self.started),
                "host": self.host, "with_token": self.with_token, "timeout": self.options.timeout,
                "written": stages.iso(self.now()), "own": own_json(own)}
        try:
            write_pending(self.pending_file, data)
        except OSError as error:
            text = (f"{self.pending_file} could not be written ({error.strerror or error}): a cleanup that does "
                    f"not run through could not be finished later")
            if not self._pending_written:
                # before the first probe: the check does not start
                raise PendingError(f"{text}; nothing was probed") from None
            self.log("warn", text)
            return
        self._pending_written = True

    def _drop_pending(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.pending_file)

    def _make_hold(self):
        if self._hold is not None:
            return self._hold(self.data_dir, self.compose_names())
        from tpotctl.checks.ewshold import EwsHold
        # the run settles itself before its cleanup (END_SETTLE), the hold ends right after it
        return EwsHold(self.data_dir, self.compose_names(), log=self.log, settle=0)

    def _leave_alone(self, hold) -> None:
        """The honeypots whose ewsposter module hold.ack names as not held: nothing of the check may
        land in their logs (ewsposter could send it later), so they get no probe and no geo event."""
        from tpotctl.checks import ewshold
        modules = list(getattr(hold, "failed_at_ack", None) or [])
        names = [h.name for h in self.honeypots]
        for module in modules:
            if not ewshold.honeypots_of([module], list(LOGS)):
                self.log("warn", f"ewsposter cannot hold its module {module}, which reads no honeypot the check "
                                 f"knows: the check leaves nothing alone for it")
        self.not_held = []
        for name in ewshold.honeypots_of(modules, names):
            self._mark_not_held(name)

    def _mark_not_held(self, name: str, probed: bool = False) -> None:
        """A honeypot the check leaves alone from now on: its stages say why; one whose probe ran
        before its module was named keeps its port and answer stages."""
        from tpotctl.checks import ewshold
        if name in self.not_held:
            return
        self.not_held.append(name)
        row = self.rows[name]
        for stage in stages.STAGES[2 if probed else 0:-1]:
            if not (self.sensor and stage in stages.STAGES[3:]):
                row.set(stage, NOT_APPLICABLE, ewshold.CANNOT_HOLD)

    def _not_held_now(self, hold, honeypot: Honeypot) -> str:
        """Why the check must leave `honeypot` alone now ("" for nothing): hold.ack read again names
        its ewsposter module (I2: a module that fails after the ack)."""
        from tpotctl.checks import ewshold
        refresh = getattr(hold, "refresh_failed", None)
        modules = refresh() if callable(refresh) else list(getattr(hold, "failed_modules", None) or [])
        if ewshold.honeypots_of(modules, [honeypot.name]):
            with self._late_lock:
                self._late.append(honeypot.name)
            return ewshold.CANNOT_HOLD
        return ""

    def _ewsposter(self, hold, report: Report, failure: Optional[BaseException]) -> None:
        from tpotctl.checks import ewshold
        if failure is not None:
            state, stage = {"state": "failed", "detail": str(failure)}, (FAILED, str(failure))
        elif getattr(hold, "acked", False):
            state, stage = {"state": "held", "detail": "hold.ack seen"}, (OK, "hold.ack seen during the run")
        elif getattr(hold, "note", "") == ewshold.NOT_RUNNING:
            state, stage = {"state": "not_running", "detail": hold.note}, (NOT_APPLICABLE, "ewsposter is not running")
        else:
            note = getattr(hold, "note", "") or ewshold.NOT_HERE
            state, stage = {"state": "not_here", "detail": note}, (NOT_APPLICABLE, note)
        if getattr(hold, "failed_modules", None):
            state["failed_modules"] = list(hold.failed_modules)
        report.ewsposter = state
        later = [name for name in ewshold.honeypots_of(getattr(hold, "failed_modules", None) or [], self.rows)
                 if name not in self.not_held]
        for row in self.rows.values():
            if row.name in self.not_held:
                row.set("ewsposter", FAILED, ewshold.CANNOT_HOLD)
            elif row.name in later:
                row.set("ewsposter", FAILED, "ewsposter could not hold its module during the check (its idle "
                                             "pass fails; docker logs ewsposter)")
            elif not self.sensor:
                row.set("ewsposter", *stage)

    def _held(self, hold, report: Report) -> None:
        self._leave_alone(hold)
        targets = [h for h in self.honeypots if h.running and not h.external and h.name in LOGS
                   and h.name not in self.not_held]
        for honeypot in targets:
            self.watch.mark(LOGS[honeypot.name][1])
        geo = self.geo_targets()
        if geo and self.map_here():
            self.map = self._make_listener()
            self.map.start()
        try:
            # before the first probe or geo event: a cleanup cut off later can be finished
            if not self.options.geo_only or geo:
                self._save_pending()
            if not self.options.geo_only:
                self.phase("probe", f"Probing the honeypots on {self.host}")
                self.sent = True
                # filled also when the probes end by an exception: the cleanup still knows what they sent
                self.results = {}
                try:
                    self.results = self._run_probes(self.host, [h for h in self.honeypots if h.name not in self.not_held],
                                                    self.token, check=hold.check, into=self.results,
                                                    skip=lambda honeypot: self._not_held_now(hold, honeypot))
                finally:
                    for name in list(self._late):
                        self._mark_not_held(name)
                    self._save_pending()        # the source ports of the probes
            self._probe_stages()
            if geo:
                self.phase("inject", "Writing the geo events")
                try:
                    self._inject(geo, hold)
                finally:
                    self._save_pending()
            self.phase("stages", "Following the events to the Attack Map" if not self.sensor else
                       "Looking for the log lines")
            self._poll(hold)
        finally:
            if self.map is not None:
                self.map.stop()
        for row in self.rows.values():
            if row.failed:
                failed = [f"{stages.TITLES[name]}: {row.stages[name].detail}".rstrip(": ")
                          for name in stages.STAGES if row.state(name) == FAILED]
                self.mark("warn", "stages", f"{row.name} {failed[0]}")
        if any(row.failed for row in self.rows.values()):
            self.mark("fail", "stages")

    def _make_listener(self) -> MapListener:
        seconds = self.options.timeout * 3 + 60
        if self._listener is not None:
            return self._listener(tempfile.mkdtemp(prefix="tpot-check-map-"), seconds)
        return MapListener(tempfile.mkdtemp(prefix="tpot-check-map-"), seconds)

    def _probe_stages(self) -> None:
        for honeypot in self.honeypots:
            if honeypot.name in self.not_held:
                continue                    # _leave_alone said why
            row = self.rows[honeypot.name]
            if self.options.geo_only:
                for name in ("port", "answer", "log", "elasticsearch"):
                    row.set(name, NOT_APPLICABLE, stages.EXTERNAL_HINT if honeypot.external else "no probe (pipeline)")
                if not honeypot.running:
                    row.set("port", FAILED, f"not running (container {honeypot.container})")
                continue
            result = self.results.get(honeypot.name)
            port = stages.port_stage(honeypot, result)
            row.stages["port"] = port
            row.stages["answer"] = stages.answer_stage(result, port)
            if port.state == NOT_APPLICABLE:
                row.set("log", NOT_APPLICABLE)
                if not self.sensor:
                    row.set("elasticsearch", NOT_APPLICABLE)

    def _inject(self, geo: List[Honeypot], hold) -> None:
        for honeypot in geo:
            hold.check()
            if honeypot.name in self.not_held or self._not_held_now(hold, honeypot):
                result = self.results.get(honeypot.name)
                self._mark_not_held(honeypot.name, probed=result is not None and result.state != probes.SKIPPED)
                continue
            row = self.rows[honeypot.name]
            event = self.geo_event(honeypot.name)
            row.geo = event
            try:
                line = self.render(honeypot, event, self.now())
                self.files.append(event.file, line + "\n")
            except (CheckError, ValueError, OSError) as error:
                row.set("geoip", FAILED, f"nothing written to {event.file}: {error}")
                row.set("attack_map", NOT_REACHED)
                continue
            event.line = line
            self.sent = True
        self.log("info", f"geo events written with the test IPs {', '.join(TEST_IPS)}")

    # -- polling --

    def _search(self, build: Callable[[bool], dict]) -> Optional[dict]:
        try:
            return stages.first_hit(self.elastic.search(build(self.with_token)))
        except ElasticError as error:
            if not (error.token_refused and self.with_token):
                raise
            self.with_token = False          # this Elasticsearch takes no query over every field
            self.log("warn", "Elasticsearch refused the token query, the check looks by source only")
            return stages.first_hit(self.elastic.search(build(False)))

    def _poll(self, hold) -> None:
        start = self.clock()
        log_end = start + self.log_timeout
        es_end = start + self.options.timeout
        map_end = start + 2 * self.options.timeout
        window = self.started
        pending_log, pending_es, pending_geo, pending_map = [], [], [], []
        for honeypot in self.honeypots:
            if honeypot.name in self.not_held:
                continue                    # _leave_alone said why
            row = self.rows[honeypot.name]
            result = self.results.get(honeypot.name)
            if row.state("port") == OK and result is not None and result.ignored and self.ports_survive():
                # the probe ran, but the honeypot does not log its source: the geo event covers the rest
                why = stages.ignored_text(honeypot.name, result.ignored)
                row.set("log", NOT_APPLICABLE, why)
                if not self.sensor:
                    row.set("elasticsearch", NOT_APPLICABLE, why)
            elif row.state("port") == OK and result is not None:
                pending_log.append(honeypot)
                if not self.sensor:
                    pending_es.append(honeypot)
            elif row.state("port") in (FAILED, NOT_REACHED) and not self.options.geo_only:
                row.set("log", NOT_REACHED)
                if not self.sensor:
                    row.set("elasticsearch", NOT_REACHED)
            if self.sensor:
                continue                    # stages 4 and later: check on the HIVE
            if row.geo is not None and row.geo.line:
                pending_geo.append(honeypot)
            elif row.geo is not None:
                continue                    # nothing written: _inject said why
            elif not self.options.geo:
                row.set("geoip", NOT_APPLICABLE, "--no-geo")
                row.set("attack_map", NOT_APPLICABLE, "--no-geo")
            elif not honeypot.running:
                row.set("geoip", NOT_REACHED, "not running")
                row.set("attack_map", NOT_REACHED, "not running")
            elif honeypot.group in PASSIVE_GROUPS:
                row.set("geoip", NOT_APPLICABLE, "a passive tool gets no test event")
                row.set("attack_map", NOT_APPLICABLE, "a passive tool gets no test event")
            else:
                row.set("geoip", NOT_APPLICABLE, "no sample event for it")
                row.set("attack_map", NOT_APPLICABLE, "no sample event for it")
        errors: Dict[str, str] = {}
        skip = [row.geo.line for row in self.rows.values() if row.geo is not None and row.geo.line]
        survive = self.ports_survive()
        # per honeypot on the way to the map: until when (clock), and how late Elasticsearch had it
        map_due: Dict[str, float] = {}
        map_lag: Dict[str, Optional[float]] = {}
        while pending_log or pending_es or pending_geo or pending_map:
            hold.check()
            now = self.clock()
            down = [""]     # Elasticsearch did not answer in this round: no more questions in it

            def lookup(key: str, build: Callable[[bool], dict]) -> Optional[dict]:
                if down[0]:
                    errors[key] = down[0]
                    return None
                try:
                    return self._search(build)
                except ElasticError as error:
                    errors[key] = str(error)
                    if not error.status:
                        down[0] = str(error)
                    return None

            for honeypot in list(pending_log):
                row, result = self.rows[honeypot.name], self.results[honeypot.name]
                lines = self.watch.new_lines(LOGS[honeypot.name][1]) if honeypot.name in LOGS else []
                line = stages.match_line(honeypot.name, result, lines, self.token, self.addresses, skip, survive)
                if line:
                    port = stages.line_port(line) if honeypot.name in stages.ADDRESS_ONLY else None
                    if port is not None and port not in result.src_ports:
                        # the source port the honeypot logs for this host: the probe's (Elasticsearch, cleanup)
                        result.src_ports.append(port)
                        self._save_pending()
                    row.log_line = line
                    row.set("log", OK, line[:200])
                    pending_log.remove(honeypot)
                elif now >= log_end:
                    error = self.watch.errors.get(LOGS.get(honeypot.name, ("", ""))[1], "")
                    if error:
                        row.set("log", FAILED, error)
                    elif stages.expects_trace(result):
                        row.set("log", FAILED, f"no line of the probe in {LOGS.get(honeypot.name, ('', '?'))[1]} "
                                               f"within {self.log_timeout:g} s")
                    else:
                        row.set("log", NOT_APPLICABLE, "only the port was checked, a knock may leave no line")
                    pending_log.remove(honeypot)
            for honeypot in list(pending_es):
                row, result = self.rows[honeypot.name], self.results[honeypot.name]
                hit = lookup("es:" + honeypot.name, lambda token, h=honeypot, r=result: stages.probe_query(
                    h, r, self.token, self.addresses, window, None, token))
                if hit is not None:
                    row.es_id, row.es_index = hit.get("_id", ""), hit.get("_index", "")
                    row.set("elasticsearch", OK, f"{row.es_index}/{row.es_id}")
                    pending_es.remove(honeypot)
                elif now >= es_end:
                    if errors.get("es:" + honeypot.name):
                        row.set("elasticsearch", FAILED, errors["es:" + honeypot.name])
                    elif stages.expects_trace(result):
                        row.set("elasticsearch", FAILED,
                                f"no event of the probe in Elasticsearch within {self.options.timeout:g} s")
                    else:
                        row.set("elasticsearch", NOT_APPLICABLE, "only the port was checked, a knock may leave no event")
                    pending_es.remove(honeypot)
            for honeypot in list(pending_geo):
                row = self.rows[honeypot.name]
                hit = lookup("geo:" + honeypot.name, lambda token, h=honeypot, e=row.geo: stages.geo_query(
                    h.logstash_type, e, self.token, window, None, token))
                if hit is not None or now >= es_end:
                    pending_geo.remove(honeypot)
                    if hit is None and errors.get("geo:" + honeypot.name):
                        row.set("geoip", FAILED, errors["geo:" + honeypot.name])
                    else:
                        row.stages["geoip"] = stages.geo_stage(hit, self.options.timeout)
                    if hit is not None:
                        row.geo.es_id, row.geo.es_index = hit.get("_id", ""), hit.get("_index", "")
                        row.geo.location = stages.location(hit)
                    if not stages.on_the_map(honeypot):
                        row.set("attack_map", NOT_APPLICABLE, "the Attack Map does not show this type")
                    elif honeypot.name in stages.NO_DEST_PORT:
                        row.set("attack_map", NOT_APPLICABLE, stages.no_dest_port_text(honeypot.name))
                    elif not self.map_here():
                        row.set("attack_map", NOT_APPLICABLE, "no Attack Map in this edition")
                    elif row.state("geoip") != OK:
                        row.set("attack_map", NOT_REACHED)
                    elif self.map is not None and self.map.error:
                        row.set("attack_map", FAILED, self.map.error)
                    else:
                        pending_map.append(honeypot)
                        map_due[honeypot.name] = min(map_end, now + stages.MAP_WAIT)
                        map_lag[honeypot.name] = self._lag(hit)
            for honeypot in list(pending_map):
                row = self.rows[honeypot.name]
                port = row.geo.src_port if row.geo.marker else None
                redis = stages.seen(self.map.lines("redis"), row.geo.src_ip, port, row.logstash_type)
                ws = stages.seen(self.map.lines("ws"), row.geo.src_ip, port, row.logstash_type)
                if redis and ws:
                    row.set("attack_map", OK, "on Redis and on the WebSocket")
                    pending_map.remove(honeypot)
                elif now >= map_due.get(honeypot.name, map_end):
                    row.set("attack_map", FAILED, self._map_miss(redis, map_lag.get(honeypot.name)))
                    pending_map.remove(honeypot)
            if pending_log or pending_es or pending_geo or pending_map:
                self.sleep(self.poll)

    def _lag(self, hit: Optional[dict]) -> Optional[float]:
        """How many seconds after its @timestamp the check found an event in Elasticsearch."""
        stamp = ((hit or {}).get("_source") or {}).get("@timestamp")
        if not isinstance(stamp, str):
            return None
        try:
            moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0.0, (self.now() - moment).total_seconds())

    def _map_miss(self, redis: bool, lag: Optional[float]) -> str:
        """Why a geo event in Elasticsearch did not reach the map in the time map_data needs."""
        if redis:
            return "on Redis, not on the WebSocket"
        if not any('"src_ip"' in line for line in self.map.lines("redis")):
            return stages.MAP_SILENT.format(channel=stages.REDIS_CHANNEL)
        text = stages.MAP_SKIPPED
        if lag is not None:
            text += f"; Elasticsearch had it {lag:.0f} s after its time"
        return text

    # -- the cleanup --

    def ports_survive(self) -> bool:
        """The source ports of the probes reach the honeypots: the probes go to an IPv4 address of
        this host through Docker's DNAT; through docker-proxy (loopback, IPv6) they do not."""
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            return False
        return address.version == 4 and not address.is_loopback

    def own_events(self) -> stages.OwnEvents:
        """What this run sent, by which (a) token, (b) test IP + marker port, (c) probe source +
        source port its events can be told apart from real ones."""
        reliable = self.ports_survive()
        own = stages.OwnEvents(self.token, self.host if reliable else "")
        by_name = {h.name: h for h in self.honeypots}
        for name, result in sorted(self.results.items()):
            honeypot = by_name.get(name)
            if honeypot is None or not honeypot.logstash_type:
                continue
            if result.state == probes.SKIPPED and not result.src_ports and not result.token_sent:
                continue            # nothing went out
            kind = honeypot.logstash_type
            own.probed.append(kind)
            if result.token_sent:
                own.token_types.append(kind)
            if reliable and result.src_ports:
                own.probes.setdefault(kind, []).extend(result.src_ports)
        for row in self.rows.values():
            if row.geo is not None and row.geo.line:
                own.geos.append((row.logstash_type, row.geo.src_ip, row.geo.src_port if row.geo.marker else None))
                if "{token}" in fixtures.template(row.name):
                    own.token_types.append(row.logstash_type)
        own.probed = sorted(set(own.probed))
        own.token_types = sorted(set(own.token_types))
        return own

    def _delete(self, own: stages.OwnEvents, end: datetime) -> int:
        deleted, self.with_token = delete_own(self.elastic, self.started, end, own, self.with_token,
                                              lambda text: self.log("warn", text))
        return deleted

    def _cleanup(self, report: Report) -> None:
        if self.sensor:
            report.cleanup = {"state": "skipped", "deleted": 0, "passes": [], "left": 0,
                              "detail": "Elasticsearch is on the HIVE, the probes stay there"}
            return
        if not self.sent:
            self._drop_pending()
            report.cleanup = {"state": "none", "deleted": 0, "passes": [], "left": 0, "detail": "nothing was sent"}
            return
        end = self.now()
        from tpotctl import ops
        port = self._web_port if self._web_port is not None else ops.web_port(self.repo_dir, self.env)
        report.kibana = stages.kibana_link(self.host, port, stages.run_kql(self.token, [self.host] + list(TEST_IPS)),
                                           self.started, end)
        if self.options.keep:
            self._drop_pending()
            report.cleanup = {"state": "kept", "deleted": 0, "passes": [], "left": 0, "detail": "--keep: the test events stay"}
            self.log("info", "The test events stay in Elasticsearch (--keep).")
            return
        self.phase("cleanup", "Removing the test events from Elasticsearch")
        own = self.own_events()
        passes: List[int] = []
        try:
            for second in (False, True):
                if second:
                    # events Logstash indexes late (a session that closed after the first pass)
                    self.sleep(SECOND_PASS)
                end = self.now()
                with contextlib.suppress(ElasticError):
                    self.elastic.refresh()
                passes.append(self._delete(own, end))
            # events of a probed type without a source (dicompot) are counted with them, never deleted
            left, sourceless = left_over(self.elastic, self.started, end, own, self.host)
        except ElasticError as error:
            self.mark("fail", "cleanup")
            self.log("error", f"The test events could not be removed: {error}")
            self._save_pending()            # the pending file stays: tpot check cleanup, or the next check
            self.log("info", "They are removed by the next check, or now with: tpot check cleanup")
            report.cleanup = {"state": "failed", "deleted": sum(passes), "passes": passes, "left": 0,
                              "detail": str(error)}
            return
        self._drop_pending()
        report.cleanup = {"state": "done", "deleted": sum(passes), "passes": passes, "left": left,
                          "left_no_source": sourceless, "detail": ""}
        self.log("ok", f"{sum(passes)} test events removed from Elasticsearch "
                       f"({' + '.join(str(n) for n in passes)}, the second pass {SECOND_PASS:g} s later).")
        if left:
            link = stages.kibana_link(self.host, port, stages.leftover_kql(own, self.host), self.started, end)
            report.cleanup["left_kibana"] = link
            self.log("warn", f"{left_text(left, sourceless)}; Kibana: {link}")
