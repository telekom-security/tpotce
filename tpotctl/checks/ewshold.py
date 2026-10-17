"""The hold of ewsposter while T-Pot checks itself, and the lock that lets one check run at a time.

ewsposter (docker/ewsposter/dist/entrypoint.sh) holds while `<data>/ews/hold` is a regular file
younger than 30 minutes: after one normal pass for the lines from before the hold, its passes
are idle, nothing is built, spooled or sent, its indexes move on. `hold.ack` says that an idle
round ended cleanly with no module behind; only an ack written after the marker counts.
`hold.state` is one line `<normal|held|stuck> <epoch seconds>`, written after every round.

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
from typing import Callable, Iterable, Optional, Tuple

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
# hold.state is written after every round; an older one says nothing about ewsposter
STATE_MAX_AGE = 120.0
STATES = ("normal", "held", "stuck")
UNKNOWN = "unknown"
NOT_HERE = "ewsposter does not run here, nothing to hold"
NOT_RUNNING = ("ewsposter is not running; the hold marker is set, so it starts held if it comes up "
               "during the check")
REFRESH_FAILED = "the hold marker could not be refreshed, the check stops"
# a renewal that fails this many times in a row stops the check (check())
REFRESH_FAILURES = 2
MARKER_MODE = 0o660


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


def ewsposter_runs() -> bool:
    """The ewsposter container runs (docker ps)."""
    from tpotctl import ops
    try:
        return any(c.name == CONTAINER and c.state == "running" for c in ops.containers())
    except ops.OpsError as error:
        raise CheckError(f"docker cannot say whether ewsposter runs ({error}); nothing was probed") from None


class EwsHold:
    """The lock of the checks and the hold of ewsposter, as a context manager.

    `__enter__` takes the lock (flock on `<data>/ews/check.lock`, without waiting: CheckError
    "another check is running"). When ewsposter is in the compose file in use (`compose_names`,
    the container names) and runs (`running`), it writes the marker `hold` and waits up to
    `timeout` seconds for a `hold.ack` written after it (HoldError, the marker removed and the
    lock given back: nothing may be probed then). When it is in the compose file but its
    container does not run, the marker is written all the same and no ack is awaited: ewsposter
    starts held when it comes up during the check (`note` and the log say so). Without ewsposter
    in the compose file there is no marker, `note` and the log say so, and the lock is held all
    the same. While the hold lasts, the marker is written anew every `refresh` seconds, so the
    30 minutes of ewsposter never end a long check.

    `check()` raises HoldError once the renewal failed REFRESH_FAILURES times in a row: the
    runner calls it between its probes and stops the check then (leaving the `with` block).

    `__exit__` (also on an exception or Ctrl+C) waits `settle` seconds, removes the marker and
    gives the lock back. The contract of the caller: it leaves the `with` block only after the
    last log line of the check is written: its probes answered or timed out, its injected lines
    appended. A line written after the marker is gone may be sent.
    """

    def __init__(self, data_dir: str, compose_names: Iterable[str], timeout: float = TIMEOUT,
                 ack_poll: float = 1.0, *, running: Optional[Callable[[], bool]] = None,
                 refresh: float = REFRESH, settle: float = 0.0,
                 log: Callable[[str, str], None] = _say,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.data_dir = data_dir
        self.compose_names = list(compose_names)
        self.timeout = timeout
        self.ack_poll = ack_poll
        self.refresh = refresh
        self.settle = settle
        self._running = running or ewsposter_runs
        self._log = log
        self._clock = clock
        self._sleep = sleep
        self.held = False           # the marker is there
        self.acked = False          # ewsposter confirmed it
        self.note = ""
        self._folder: Optional[Folder] = None
        self._lock: Optional[int] = None
        self._marker_ns: Optional[int] = None
        self._stop = threading.Event()
        self._writing = threading.Lock()
        self._refresher: Optional[threading.Thread] = None
        self._refresh_failed = threading.Event()

    def check(self) -> None:
        """HoldError when the marker could not be renewed REFRESH_FAILURES times in a row: the
        hold may end before the check does, so the check stops. The runner calls it between
        probes."""
        if self._refresh_failed.is_set():
            raise HoldError(REFRESH_FAILED)

    @property
    def ews_dir(self) -> str:
        return os.path.join(self.data_dir, EWS_DIR)

    def __enter__(self) -> "EwsHold":
        if fcntl is None or not kibana_objects.DIR_FD:
            raise CheckError("this system cannot lock a check or hold ewsposter, checks need a Linux T-Pot host")
        self._stop = threading.Event()
        self._refresh_failed = threading.Event()
        try:
            self._open()
            if CONTAINER not in self.compose_names:
                self.note = NOT_HERE
                self._log("info", NOT_HERE)
                return self
            if not self._running():
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
                self._log("ok", "ewsposter holds, nothing leaves this host during the check.")
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

    def _refresh_loop(self) -> None:
        failures = 0
        while not self._stop.wait(self.refresh):
            with self._writing:
                if self._stop.is_set():
                    return
                try:
                    self._put_marker()
                    failures = 0
                except (KibanaError, OSError) as error:
                    failures += 1
                    self._log("warn", f"{self.ews_dir}/{MARKER} could not be written anew: {error}")
                    if failures >= REFRESH_FAILURES:
                        self._log("error", REFRESH_FAILED)
                        self._refresh_failed.set()
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
