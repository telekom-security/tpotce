"""The hold of ewsposter while T-Pot checks itself, and the lock that lets one check run at a time.

ewsposter (docker/ewsposter/dist/entrypoint.sh) holds while `<data>/ews/hold` is a regular file
younger than 30 minutes: after one normal pass for the lines from before the hold, its passes
are idle, nothing is built, spooled or sent, its indexes move on. `hold.ack` says that an idle
round ended cleanly with no module behind; only an ack written after the marker counts. A module
whose idle passes keep failing (an ewsposter module that cannot read its honeypot's log) is named
in it, one line `failed: <module> ...` (the sections of ews.cfg): the others are held, the check
leaves the honeypots of those modules alone (`honeypots_of`). `hold.state` is one line
`<normal|held|stuck> <epoch seconds>`, written after every round.

    with EwsHold(data_dir, ops.compose_names()) as hold:
        ...                       # probes and injected lines; leave only after the last of them

Every file is reached through the descriptor of `<data>/ews` and never through a link
(tpotctl.kibana_objects.Folder): the folder is writable for the group tpot, and tpotinit, which
runs as root, cleans it up on every start.
"""

import os
import re
import stat
import threading
import time
from typing import Callable, Iterable, List, Optional, Tuple

try:
    import fcntl
except ImportError:          # Windows: checks run on a Linux T-Pot host only
    fcntl = None

from tpotctl import kibana_objects
from tpotctl.checks import CheckError, HoldError
from tpotctl.kibana_objects import Folder, KibanaError

EWS_DIR = "ews"
MARKER = "hold"
ACK = "hold.ack"
STATE = "hold.state"
LOCK = "check.lock"
CONTAINER = "ewsposter"
# the first round of a hold is one normal pass (10 to 60 s apart) plus the idle passes
TIMEOUT = 180.0
# ewsposter ignores a marker older than 30 minutes; it is written anew every 5 minutes
REFRESH = 300.0
# how often the renewal looks whether the marker is still there (seconds)
WATCH = 30.0
# the end of a hold waits this long for the last log lines of the check (as ews_hold.sh)
SETTLE = 10.0
# hold.state is written after every round; an older one says nothing about ewsposter
STATE_MAX_AGE = 120.0
STATES = ("normal", "held", "stuck")
UNKNOWN = "unknown"
NOT_HERE = "ewsposter does not run here, nothing to hold"
NOT_RUNNING = ("ewsposter is not running; the hold marker is set, so it starts held if it comes up "
               "during the check")
REFRESH_FAILED = "the hold marker could not be refreshed, the check stops"
MARKER_GONE = "the hold marker was removed (T-Pot restarted?), the check stops"
UNKNOWN_RUNS = "docker cannot say whether ewsposter runs; the check waits for its confirmation as if it ran"
# a renewal that fails this many times in a row stops the check (check())
REFRESH_FAILURES = 2
MARKER_MODE = 0o660
# the most of hold.ack that is read (it holds one line of module names)
ACK_MAX = 4096
# why the check leaves a honeypot alone whose ewsposter module cannot be held
CANNOT_HOLD = "ewsposter cannot hold its module (its idle pass fails; docker logs ewsposter)"
# the modules of ews.cfg (its sections, lower case) whose honeypots have other service names;
# conpot is every template (conpot_*), every other module is the honeypot of its name
MODULE_HONEYPOTS = {"citrix": ("citrixhoneypot",), "gopot": ("go-pot",)}


def failed_modules(text: Optional[str]) -> List[str]:
    """The modules a hold.ack names (`failed: <module> ...`), sorted; none for an empty one."""
    found = set()
    for line in (text or "")[:ACK_MAX].splitlines():
        key, _, rest = line.partition(":")
        if key.strip() != "failed":
            continue
        found.update(word.lower() for word in rest.split() if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", word))
    return sorted(found)


def honeypots_of(modules: Iterable[str], names: Iterable[str]) -> List[str]:
    """The honeypots among `names` (service names) that the ewsposter modules `modules` read."""
    names = list(names)
    found = set()
    for module in modules:
        if module == "conpot":
            found.update(name for name in names if name.startswith("conpot_"))
        else:
            found.update(name for name in MODULE_HONEYPOTS.get(module, (module,)) if name in names)
    return sorted(found)


def _say(kind: str, text: str) -> None:
    from tpotctl import say
    getattr(say, kind)(text)


def ews_state(data_dir: str, now: Callable[[], float] = time.time) -> Tuple[str, Optional[float]]:
    """What `<data>/ews/hold.state` says: (`normal`, `held` or `stuck`, its age in seconds).
    (`unknown`, None) when it is missing, no regular file or not of its form; (`unknown`, age)
    when it is older than STATE_MAX_AGE (ewsposter does not run its loop) or dated ahead."""
    try:
        folder = Folder.walk(data_dir, (EWS_DIR,), False)
    except (KibanaError, OSError):
        return UNKNOWN, None
    if folder is None:
        return UNKNOWN, None
    try:
        text = folder.read(STATE)
    except (KibanaError, OSError, ValueError):
        return UNKNOWN, None
    finally:
        folder.close()
    match = re.fullmatch(r"(normal|held|stuck) ([0-9]{1,12})\n?", text or "")
    if match is None:
        return UNKNOWN, None
    age = now() - int(match.group(2))
    if age > STATE_MAX_AGE or age < -60:
        return UNKNOWN, age
    return match.group(1), max(age, 0.0)


def ewsposter_runs() -> Optional[bool]:
    """Whether the ewsposter container runs (docker ps); None when docker cannot say, which
    EwsHold takes as running (it waits for the ack)."""
    from tpotctl import ops
    try:
        return any(c.name == CONTAINER and c.state == "running" for c in ops.containers())
    except ops.OpsError:
        return None


class EwsHold:
    """The lock of the checks and the hold of ewsposter, as a context manager.

    `__enter__` takes the lock (flock on `<data>/ews/check.lock`, without waiting: CheckError
    "another check is running"). When ewsposter is in the compose file in use (`compose_names`,
    the container names) and runs (`running`), it writes the marker `hold` and waits up to
    `timeout` seconds for a `hold.ack` written after it (HoldError, the marker removed and the
    lock given back: nothing may be probed then). When it is in the compose file but its
    container does not run, the marker is written all the same and no ack is awaited: ewsposter
    starts held when it comes up during the check (`note` and the log say so). When docker
    cannot say (`running` returns None), ewsposter counts as running. Without ewsposter in the
    compose file there is no marker, `note` and the log say so, and the lock is held all the
    same. While the hold lasts, a thread looks every `watch` seconds whether the marker is still
    there and writes it anew every `refresh` seconds, so the 30 minutes of ewsposter never end a
    long check.

    `check()` raises HoldError when the marker is gone or no regular file any more and this hold
    did not remove it (a start of T-Pot removes `hold` and `check.lock`, tpotinit's
    clear_ews_hold), or once the renewal failed REFRESH_FAILURES times in a row. The runner
    calls it between its probes and stops the check then (it leaves the `with` block).

    `__exit__` (also on an exception or Ctrl+C) waits `settle` seconds (10, as ews_hold.sh: the
    honeypots write lines when a connection closes), removes the marker and gives the lock back.
    The contract of the caller: it leaves the `with` block only after the last log line of the
    check is written: its probes answered or timed out, its injected lines appended. A line
    written after the marker is gone may be sent. A SIGTERM or SIGHUP that Python does not turn
    into an exception skips `__exit__`; the runner installs handlers for them (ewsposter would
    end the hold only after 30 minutes otherwise).
    """

    def __init__(self, data_dir: str, compose_names: Iterable[str], timeout: float = TIMEOUT,
                 ack_poll: float = 1.0, *, running: Optional[Callable[[], Optional[bool]]] = None,
                 refresh: float = REFRESH, watch: float = WATCH, settle: float = SETTLE,
                 log: Callable[[str, str], None] = _say,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.data_dir = data_dir
        self.compose_names = list(compose_names)
        self.timeout = timeout
        self.ack_poll = ack_poll
        self.refresh = refresh
        self.watch = watch
        self.settle = settle
        self._running = running or ewsposter_runs
        self._log = log
        self._clock = clock
        self._sleep = sleep
        self.held = False           # the marker is there
        self.acked = False          # ewsposter confirmed it
        # the modules ewsposter could not hold (hold.ack): at the ack, and every one named later on
        self.failed_at_ack: List[str] = []
        self._failed_modules: List[str] = []
        self.note = ""
        self._folder: Optional[Folder] = None
        self._lock: Optional[int] = None
        self._marker_ns: Optional[int] = None
        self._stop = threading.Event()
        self._writing = threading.Lock()
        self._refresher: Optional[threading.Thread] = None
        self._failed = ""           # why the hold is over before the check (check())

    def check(self) -> None:
        """HoldError when the hold is over before the check: the marker was removed by someone
        else (MARKER_GONE) or could not be renewed REFRESH_FAILURES times in a row
        (REFRESH_FAILED). The runner calls it between probes."""
        if self._failed:
            raise HoldError(self._failed)

    @property
    def failed_modules(self) -> List[str]:
        """Every module a hold.ack of this hold named: those of the ack and those of a later round
        (read with the look at the marker, every `watch` seconds)."""
        return list(self._failed_modules)

    def _read_failed(self) -> List[str]:
        """The modules of hold.ack now (a missing or unreadable one names none)."""
        try:
            text = self._folder.read(ACK) if self._folder is not None else None
        except (KibanaError, OSError, ValueError):
            return []
        found = failed_modules(text)
        self._failed_modules = sorted(set(self._failed_modules) | set(found))
        return found

    @property
    def ews_dir(self) -> str:
        return os.path.join(self.data_dir, EWS_DIR)

    def __enter__(self) -> "EwsHold":
        if fcntl is None or not kibana_objects.DIR_FD:
            raise CheckError("this system cannot lock a check or hold ewsposter, checks need a Linux T-Pot host")
        self._stop = threading.Event()
        self._failed = ""
        self.failed_at_ack, self._failed_modules = [], []
        try:
            self._open()
            if CONTAINER not in self.compose_names:
                self.note = NOT_HERE
                self._log("info", NOT_HERE)
                return self
            runs = self._running()
            if runs is None:
                self._log("warn", UNKNOWN_RUNS)
            if runs is False:
                self._write_marker()
                self.note = NOT_RUNNING
                self._log("info", NOT_RUNNING)
                self._start_refresh()
                return self
            self._write_marker()
            self._log("info", f"Holding ewsposter: it sends nothing until the check is over "
                              f"(up to {self.timeout:g} s for its confirmation).")
            self._wait()
            self._start_refresh()
        except KibanaError as error:
            self._release()
            raise CheckError(str(error)) from None
        except OSError as error:
            self._release()
            raise CheckError(f"{self.ews_dir}: {error.strerror or error}; nothing was probed") from None
        except BaseException:
            self._release()
            raise
        return self

    def __exit__(self, *exc) -> bool:
        try:
            if self.held and self.settle > 0:
                self._sleep(self.settle)
        finally:
            self._release()
        return False

    # -- the lock -----------------------------------------------------------------------------

    def _open(self) -> None:
        try:
            base = Folder.walk(self.data_dir, (), False)
        except OSError as error:
            raise CheckError(f"the data folder {self.data_dir} cannot be opened ({error.strerror or error})") from None
        if base is None:
            raise CheckError(f"the data folder {self.data_dir} does not exist")
        try:
            self._folder = base.sub(EWS_DIR, True)
        except OSError as error:
            raise CheckError(f"{self.ews_dir} cannot be opened ({error.strerror or error})") from None
        finally:
            base.close()
        handle = self._folder.open_lock(LOCK)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(handle)
            raise CheckError("another check is running") from None
        except BaseException:
            os.close(handle)
            raise
        self._lock = handle

    # -- the marker ---------------------------------------------------------------------------

    def _put_marker(self) -> None:
        text = f"tpot check {os.getpid()} {int(time.time())}\n"
        self._folder.write(MARKER, text, MARKER_MODE, MARKER + ".")

    def _write_marker(self) -> None:
        with self._writing:
            # held before the write: Ctrl+C during it still ends with the marker removed
            self.held = True
            try:
                self._put_marker()
            except KibanaError:
                self.held = False       # a link or no regular file there: refused, nothing written
                raise
            info = self._folder.lstat(MARKER)
        if info is None or not stat.S_ISREG(info.st_mode):
            raise CheckError(f"{self.ews_dir}/{MARKER} was removed or replaced while it was written")
        self._marker_ns = info.st_mtime_ns

    def acknowledged(self) -> bool:
        """hold.ack is a regular file written at or after the marker (ns mtimes, one clock)."""
        info = self._folder.lstat(ACK) if self._folder is not None else None
        return (info is not None and stat.S_ISREG(info.st_mode) and self._marker_ns is not None
                and info.st_mtime_ns >= self._marker_ns)

    def _wait(self) -> None:
        deadline = self._clock() + self.timeout
        while True:
            if self.acknowledged():
                self.acked = True
                self.failed_at_ack = self._read_failed()
                self._log("ok", "ewsposter holds, nothing leaves this host during the check.")
                if self.failed_at_ack:
                    self._log("warn", f"ewsposter cannot hold its module(s) {', '.join(self.failed_at_ack)} (their "
                                      f"idle passes fail; docker logs ewsposter): the check leaves their honeypots "
                                      f"alone.")
                return
            if self._clock() >= deadline:
                raise HoldError(self._timeout_text())
            self._sleep(self.ack_poll)

    def _timeout_text(self) -> str:
        text = f"ewsposter did not confirm the hold within {self.timeout:g} s; nothing was probed"
        state, _age = ews_state(self.data_dir)
        if state == "stuck":
            text += (f". {self.ews_dir}/{STATE} says stuck: after an earlier hold the idle rounds of "
                     f"ewsposter do not end cleanly or a module is still behind, it sends nothing until "
                     f"one does (docker logs ewsposter)")
        elif state == "held":
            text += ". ewsposter holds, but its idle rounds have not caught up yet (docker logs ewsposter)"
        elif state == "normal":
            text += ". ewsposter has not taken up the hold yet (docker logs ewsposter)"
        else:
            text += (f". {self.ews_dir}/{STATE} is missing or older than {STATE_MAX_AGE:g} s, "
                     f"ewsposter may not run its loop (docker logs ewsposter)")
        return text

    def _start_refresh(self) -> None:
        self._refresher = threading.Thread(target=self._refresh_loop, name="ews-hold", daemon=True)
        self._refresher.start()

    def _fail(self, text: str) -> None:
        self._log("error", text)
        self._failed = text

    def _refresh_loop(self) -> None:
        """Every `watch` seconds: the marker still a regular file (else MARKER_GONE); every
        `refresh` seconds it is written anew (only one that is there, so the end of the hold is
        never undone). Both under the lock of the writes, which the end takes before it removes
        the marker."""
        failures = 0
        due = self._clock() + self.refresh
        while not self._stop.wait(max(0.0, min(self.watch, due - self._clock()))):
            with self._writing:
                if self._stop.is_set():
                    return
                try:
                    info = self._folder.lstat(MARKER)
                except OSError:
                    info = None
                if info is None or not stat.S_ISREG(info.st_mode):
                    self._fail(MARKER_GONE)
                    return
                if self.acked:
                    self._read_failed()
                if self._clock() < due:
                    continue
                due = self._clock() + self.refresh
                try:
                    self._put_marker()
                    failures = 0
                except (KibanaError, OSError) as error:
                    failures += 1
                    self._log("warn", f"{self.ews_dir}/{MARKER} could not be written anew: {error}")
                    if failures >= REFRESH_FAILURES:
                        self._fail(REFRESH_FAILED)
                        return

    # -- the end ------------------------------------------------------------------------------

    def _release(self) -> None:
        """Stops the renewal, removes the marker, gives the lock back; each step also when the
        one before failed. The marker goes under the lock of the renewal, so no renewal brings
        it back."""
        self._stop.set()
        try:
            with self._writing:
                self._remove_marker()
        finally:
            refresher, self._refresher = self._refresher, None
            if refresher is not None:
                refresher.join(timeout=5)
            if self._lock is not None:
                os.close(self._lock)
                self._lock = None
            if self._folder is not None:
                self._folder.close()
                self._folder = None

    def _remove_marker(self) -> None:
        if not self.held:
            return
        self.held = False
        try:
            if self._folder.remove(MARKER):
                self._log("info", "The hold of ewsposter is lifted, it sends again after its next round.")
        except (KibanaError, OSError) as error:
            self._log("warn", f"{self.ews_dir}/{MARKER} could not be removed ({error}); ewsposter "
                              f"ignores it after 30 minutes")
