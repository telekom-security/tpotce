"""`tpot check honeypots` / `tpot check pipeline`: every honeypot from its port to the Attack Map.

The run:
    1. the inventory: the honeypots of the compose file in use (tpotctl.checks.inventory);
    2. the hold of ewsposter and the lock of the checks (EwsHold): nothing leaves the host from
       here on, and a hold that is not confirmed ends the run before the first probe (exit 3);
    3. the probes against the host's own address (not 127.0.0.1: docker-proxy would change the
       source port), then one geo event per running honeypot (not the passive tools), a fixture
       with a fixed test IP (IPv4) and the marker port 51234 + n appended to its real log;
    4. the stages, polled until each one is decided or its time is up (tpotctl.checks.stages);
    5. END_SETTLE seconds for the last lines (written when a connection closes) and their way into
       Elasticsearch, then the cleanup unless --keep: _delete_by_query on logstash-* for the run's
       window and its own sources or token;
    6. the end of the hold, right after the cleanup (the settle of step 5 is the one of the hold);
    7. the result in ~/.local/share/tpotce/checks/last.json.

SIGTERM and SIGHUP become an exception like Ctrl+C, so the cleanup and the end of the hold run on
every way out; the handlers before come back afterwards. Only the first signal is raised, a later
one (and one during the end) is noted. Exit codes: 0 every stage ok, 1 a stage failed or the
cleanup failed, 3 the hold failed (nothing probed), 130 Ctrl+C (128 + n for signal n); 2 (cannot
ask, or an option that does not fit) is the CLI's.

last.json (version 1), for the Checks page:
    {"version": 1, "run_id", "token", "mode": "honeypots"|"pipeline", "started", "ended" (UTC ISO),
     "host", "tpot_type", "options": {"only", "geo", "keep", "timeout"},
     "exit_code", "result": "ok"|"failed"|"hold failed"|"cancelled", "error",
     "ewsposter": {"state": "held"|"not_here"|"not_running"|"failed"|"none", "detail"},
     "cleanup": {"state": "done"|"kept"|"skipped"|"failed"|"none", "deleted", "detail"},
     "kibana": the Discover link of the run's window and token / sources,
     "stages": the names in order, "titles": name -> column title,
     "honeypots": [{"name", "group", "container", "running", "external", "logstash_type", "ports",
                    "stages": {name: {"state": "ok"|"failed"|"n/a"|"not_reached", "detail"}},
                    "log_line", "es_id", "es_index",
                    "geo": null | {"src_ip", "src_port", "file", "line", "es_id", "es_index",
                                   "location": {"lat", "lon", "country", "ip_rep"}}}]}
"""

import contextlib
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

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
MAP_CONTAINERS = ("map_redis", "map_web")
RESULT_VERSION = 1

QUESTION = ("During the check (a few minutes) ewsposter sends nothing; real attacks from this window are not "
            "reported either. Test events are written to the honeypot logs and removed from Elasticsearch "
            "afterwards (--keep keeps them).")
QUESTION_KEEP = ("During the check (a few minutes) ewsposter sends nothing; real attacks from this window are "
                 "not reported either. Test events are written to the honeypot logs and stay in Elasticsearch "
                 "(--keep).")
QUESTION_SENSOR = ("During the check (a few minutes) ewsposter sends nothing; real attacks from this window are "
                   "not reported either. The probes are written to the honeypot logs and sent to the HIVE, where "
                   "they stay in Elasticsearch (a SENSOR checks the port, the answer and the log line).")

# the WebSocket client inside map_web (python3 and aiohttp are in the image), as in
# attackmap_pipeline_test.sh, with a time limit of its own: a docker exec that ends leaves its
# process in the container running otherwise
WS_CLIENT = """
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


class Terminated(BaseException):
    """SIGTERM or SIGHUP, raised where the run is, so the cleanup and the end of the hold run."""

    def __init__(self, signum: int):
        super().__init__(f"signal {signum}")
        self.signum = signum


def question(options: Options, sensor: bool = False) -> str:
    """What the check does to this host, asked before it starts."""
    if sensor:
        return QUESTION_SENSOR
    return QUESTION_KEEP if options.keep else QUESTION


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

    def __init__(self):
        self.received: List[int] = []
        self.shielded = False
        self._previous: Dict[int, Any] = {}

    def handler(self, signum, _frame) -> None:
        self.received.append(signum)
        # one exception per run: a second ^C must not cut short the end of the probes (the pool
        # waits for them), the cleanup or the end of the hold
        if self.shielded or len(self.received) > 1:
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
    def __init__(self, text: str, status: int = 0):
        super().__init__(text)
        self.status = status


class Elastic:
    """logstash-* of this HIVE (127.0.0.1:64298, nginx-free like fuWAITES and tpot attackers)."""

    def __init__(self, url: str = ES_URL, opener: Callable = urllib.request.urlopen, timeout: float = 5):
        self.url = url.rstrip("/")
        self.opener = opener
        self.timeout = timeout

    def request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            try:
                text = error.read().decode("utf-8", "replace")[:300]
            except OSError:
                text = ""
            raise ElasticError(f"Elasticsearch answered {error.code} {text}".strip(), error.code) from None
        except (OSError, ValueError) as error:
            raise ElasticError(f"Elasticsearch is not reachable ({getattr(error, 'reason', error)})") from None

    def search(self, body: dict) -> dict:
        return self.request("POST", "/logstash-*/_search", body)

    def refresh(self) -> None:
        self.request("POST", "/logstash-*/_refresh")

    def delete_by_query(self, body: dict) -> int:
        answer = self.request("POST", "/logstash-*/_delete_by_query?conflicts=proceed&refresh=true", body)
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

    def __init__(self, folder: str, seconds: float, popen: Callable = subprocess.Popen):
        self.folder = folder
        self.seconds = seconds
        self.popen = popen
        self.procs: Dict[str, Any] = {}
        self.error = ""

    def path(self, which: str) -> str:
        return os.path.join(self.folder, f"{which}.log")

    def start(self) -> None:
        commands = {
            "redis": ["docker", "exec", "map_redis", "redis-cli", "SUBSCRIBE", stages.REDIS_CHANNEL],
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
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.procs = {}


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
    cleanup: Dict[str, Any] = field(default_factory=lambda: {"state": "none", "deleted": 0, "detail": ""})
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
        self.log = log
        self._mark = mark
        self.clock = clock
        self.sleep = sleep
        self.now = now
        self._web_port = web_port
        self.result_file = result_file
        self.log_timeout = log_timeout
        self.poll = poll
        self.with_token = True          # Elasticsearch takes the token query (else sources only)
        self.signals = Signals()
        self.watch = LogWatch(self.files)
        self.rows: Dict[str, Row] = {}
        self.results: Dict[str, probes.ProbeResult] = {}
        self.honeypots: List[Honeypot] = []
        self.addresses: List[str] = []
        self.host = ""
        self.sent = False               # a probe or a geo event went out: the cleanup has work
        self.map: Optional[MapListener] = None

    # -- output --

    def mark(self, *words: str) -> None:
        line = "@@tpot " + " ".join(words)
        if self._mark is not None:
            self._mark(line)
        elif os.environ.get("TPOT_MARKS") == "1":
            print(line, flush=True)

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
                if h.running and h.name in names and h.name in LOGS and h.group not in PASSIVE_GROUPS]

    def geo_event(self, name: str) -> GeoEvent:
        """The test IP and the marker port of a honeypot's geo event: the n-th honeypot of the
        inventory (with a log) gets MARKER_PORT + n and the test IPs in turn."""
        names = sorted(h.name for h in self.honeypots if h.name in LOGS)
        index = names.index(name)
        return GeoEvent(TEST_IPS[index % len(TEST_IPS)], stages.MARKER_PORT + index, fixtures.log_file(name))

    # -- the run --

    def plan(self) -> List[str]:
        """--dry-run: what the check would do, nothing is touched (no hold, no probe, no line)."""
        self.honeypots = self.inventory()
        lines = [f"Run id {self.run_id}, token {self.token}"]
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
            body = stages.cleanup_query(start, start, (self._addresses or own_addresses)(self.host), self.token)
            lines.append("cleanup: POST logstash-*/_delete_by_query " + json.dumps(body))
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
        try:
            with self.signals:
                self._run(report)
        except KeyboardInterrupt:
            report.exit_code, report.result = EXIT_CANCELLED, "cancelled"
        except Terminated as stop:
            report.exit_code, report.result = 128 + stop.signum, "cancelled"
        report.ended = report.ended or self.now()
        report.rows = list(self.rows.values())
        if report.exit_code == EXIT_OK and self.signals.received:
            signum = self.signals.received[0]
            report.exit_code, report.result = 128 + signum, "cancelled"
        if report.exit_code == EXIT_OK and (report.failed or report.cleanup.get("state") == "failed"):
            report.exit_code, report.result = EXIT_FAILED, "failed"
        try:
            report.written = write_result(report, self.result_file)
        except OSError as error:
            self.log("warn", f"the result could not be written: {error}")
        self.phase("done", "Done")
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

        self.phase("hold", "Holding ewsposter")
        hold = self._make_hold()
        try:
            hold.__enter__()
        except CheckError as error:
            self.mark("fail", "hold")
            self.log("error", str(error))
            report.exit_code, report.result, report.error = EXIT_HOLD, "hold failed", str(error)
            report.ewsposter = {"state": "failed", "detail": str(error)}
            return
        failure: Optional[BaseException] = None
        try:
            self._held(hold, report)
        except HoldError as error:      # the hold ended during the check: the check stops
            failure = error
            self.log("error", str(error))
            report.error = str(error)
        finally:
            with self.signals.shield():
                try:
                    if self.sent:
                        # the last lines of the honeypots (some write when a connection closes) and
                        # their way into Elasticsearch, before the cleanup and the end of the hold
                        self.sleep(END_SETTLE)
                    self._cleanup(report)
                finally:
                    self.phase("release", "Lifting the hold of ewsposter")
                    report.ended = self.now()
                    hold.__exit__(None, None, None)
        self._ewsposter(hold, report, failure)

    def _make_hold(self):
        if self._hold is not None:
            return self._hold(self.data_dir, self.compose_names())
        from tpotctl.checks.ewshold import EwsHold
        # the run settles itself before its cleanup (END_SETTLE), the hold ends right after it
        return EwsHold(self.data_dir, self.compose_names(), log=self.log, settle=0)

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
        report.ewsposter = state
        if self.sensor:
            return
        for row in self.rows.values():
            row.set("ewsposter", *stage)

    def _held(self, hold, report: Report) -> None:
        targets = [h for h in self.honeypots if h.running and not h.external and h.name in LOGS]
        for honeypot in targets:
            self.watch.mark(LOGS[honeypot.name][1])
        geo = self.geo_targets()
        if geo and self.map_here():
            self.map = self._make_listener()
            self.map.start()
        try:
            if not self.options.geo_only:
                self.phase("probe", f"Probing the honeypots on {self.host}")
                self.sent = True
                self.results = self._run_probes(self.host, self.honeypots, self.token, check=hold.check)
            self._probe_stages()
            if geo:
                self.phase("inject", "Writing the geo events")
                self._inject(geo, hold)
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
            if error.status != 400 or not self.with_token:
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
            row = self.rows[honeypot.name]
            result = self.results.get(honeypot.name)
            if row.state("port") == OK and result is not None:
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
                line = stages.match_line(honeypot.name, result, lines, self.token, self.addresses, skip)
                if line:
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
                    elif not self.map_here():
                        row.set("attack_map", NOT_APPLICABLE, "no Attack Map in this edition")
                    elif row.state("geoip") != OK:
                        row.set("attack_map", NOT_REACHED)
                    elif self.map is not None and self.map.error:
                        row.set("attack_map", FAILED, self.map.error)
                    else:
                        pending_map.append(honeypot)
            for honeypot in list(pending_map):
                row = self.rows[honeypot.name]
                redis = stages.seen(self.map.lines("redis"), row.geo.src_ip, row.geo.src_port)
                ws = stages.seen(self.map.lines("ws"), row.geo.src_ip, row.geo.src_port)
                if redis and ws:
                    row.set("attack_map", OK, "on Redis and on the WebSocket")
                    pending_map.remove(honeypot)
                elif now >= map_end:
                    row.set("attack_map", FAILED, "on Redis, not on the WebSocket" if redis else
                            f"not on Redis ({stages.REDIS_CHANNEL}) within {2 * self.options.timeout:g} s")
                    pending_map.remove(honeypot)
            if pending_log or pending_es or pending_geo or pending_map:
                self.sleep(self.poll)

    # -- the cleanup --

    def _cleanup(self, report: Report) -> None:
        if self.sensor:
            report.cleanup = {"state": "skipped", "deleted": 0,
                              "detail": "Elasticsearch is on the HIVE, the probes stay there"}
            return
        if not self.sent:
            report.cleanup = {"state": "none", "deleted": 0, "detail": "nothing was sent"}
            return
        end = self.now()
        sources = [self.host] + list(TEST_IPS)
        from tpotctl import ops
        port = self._web_port if self._web_port is not None else ops.web_port(self.repo_dir, self.env)
        report.kibana = stages.kibana_link(self.host, port, self.token, sources, self.started, end)
        if self.options.keep:
            report.cleanup = {"state": "kept", "deleted": 0, "detail": "--keep: the test events stay"}
            self.log("info", "The test events stay in Elasticsearch (--keep).")
            return
        self.phase("cleanup", "Removing the test events from Elasticsearch")
        try:
            with contextlib.suppress(ElasticError):
                self.elastic.refresh()
            try:
                deleted = self.elastic.delete_by_query(
                    stages.cleanup_query(self.started, end, self.addresses, self.token, self.with_token))
            except ElasticError as error:
                if error.status != 400 or not self.with_token:
                    raise
                self.with_token = False
                deleted = self.elastic.delete_by_query(
                    stages.cleanup_query(self.started, end, self.addresses, self.token, False))
        except ElasticError as error:
            self.mark("fail", "cleanup")
            self.log("error", f"The test events could not be removed: {error}")
            report.cleanup = {"state": "failed", "deleted": 0, "detail": str(error)}
            return
        report.cleanup = {"state": "done", "deleted": deleted, "detail": ""}
        self.log("ok", f"{deleted} test events removed from Elasticsearch.")
