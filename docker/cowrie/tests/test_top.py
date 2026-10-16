"""ps, top, uptime, w and /proc/loadavg of the patched Cowrie (patch_protocol_load, patch_uptime,
patch_ps_top), run on stand-ins for Cowrie's protocol, pipe and reactor."""
import ast
import hashlib
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time as real_time
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, module_all

MEMINFO = (b"MemTotal:        4005216 kB\nMemFree:         1520000 kB\nMemAvailable:    3010000 kB\n"
           b"Buffers:           90000 kB\nCached:          1300000 kB\nSwapCached:            0 kB\n"
           b"SwapTotal:             0 kB\nSwapFree:              0 kB\nShmem:              9000 kB\n")
CPUINFO = b"".join(b"processor\t: %d\nmodel name\t: Intel Xeon Processor (Icelake)\n\n" % n for n in range(2))
NOW = 1_791_000_000.0  # 2026-10-03 04:00:00 UTC
PS = [
    {"USER": "root", "PID": 1, "CPU": 0.0, "MEM": 0.2, "VSZ": 168520, "RSS": 8140, "TTY": "?", "STAT": "Ss",
     "START": "Sep30", "TIME": 0.0, "COMMAND": "/sbin/init"},
    {"USER": "root", "PID": 344, "CPU": 0.0, "MEM": 0.1, "VSZ": 40632, "RSS": 5192, "TTY": "?", "STAT": "Ss",
     "START": "Sep30", "TIME": 0.0, "COMMAND": "/usr/lib/systemd/systemd-journald"},
    {"USER": "root", "PID": 1391, "CPU": 0.0, "MEM": 0.1, "VSZ": 15492, "RSS": 4980, "TTY": "?", "STAT": "Ss",
     "START": "Sep30", "TIME": 0.0, "COMMAND": "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups"},
    {"USER": "root", "PID": 1651, "CPU": 0.0, "MEM": 0.2, "VSZ": 27520, "RSS": 6420, "TTY": "?", "STAT": "Ssl",
     "START": "Sep30", "TIME": 0.0,
     "COMMAND": "/usr/bin/python3 /usr/share/unattended-upgrades/unattended-upgrade-shutdown --wait-for-signal"},
    {"USER": "root", "PID": 1700, "CPU": 0.0, "MEM": 0.1, "VSZ": 25012, "RSS": 2288, "TTY": "?", "STAT": "S<sl",
     "START": "Sep30", "TIME": 0.0, "COMMAND": "/sbin/auditd"},
    {"USER": "ubuntu", "PID": 3089, "CPU": 0.0, "MEM": 0.1, "VSZ": 8688, "RSS": 3376, "TTY": "pts/0", "STAT": "Ss",
     "START": "Oct01", "TIME": 0.0, "COMMAND": "-bash"},
]


class Config(FakeConfig):
    def getint(self, section, option, fallback=None):
        value = self.get(section, option, None)
        return fallback if value is None else int(value)


class Clock:
    """time for the patched modules: a fixed, settable now."""

    def __init__(self, now=NOW):
        self.now = now

    def time(self):
        return self.now

    def localtime(self, seconds=None):
        return real_time.gmtime(self.now if seconds is None else seconds)

    def strftime(self, fmt, when=None):
        return real_time.strftime(fmt, when if when is not None else real_time.gmtime(self.now))

    def gmtime(self, seconds=None):
        return real_time.gmtime(self.now if seconds is None else seconds)


class Reactor:
    """reactor.callLater that runs only when a test says so."""

    class Call:
        def __init__(self, delay, fn):
            self.delay, self.fn, self.live = delay, fn, True

        def active(self):
            return self.live

        def cancel(self):
            self.live = False

    def __init__(self):
        self.calls = []

    def callLater(self, delay, fn):  # noqa: N802 - Twisted's name
        call = self.Call(delay, fn)
        self.calls.append(call)
        return call

    def pending(self):
        return [c for c in self.calls if c.live]


def patched_root():
    root = Path(tempfile.mkdtemp()) / "cowrie"
    shutil.copytree(FIXTURE, root)
    subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True, text=True)
    return root


def protocol_class(source, namespace):
    """The T-Pot members of the patched HoneyPotBaseProtocol (and boot_time / uptime) as a class."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == "HoneyPotBaseProtocol":
            keep = [item for item in node.body
                    if (isinstance(item, ast.FunctionDef) and (item.name.startswith("tpot_") or item.name in
                                                               ("proc_loadavg", "uptime")))
                    or (isinstance(item, ast.Assign) and any(getattr(t, "id", "") == "TPOT_LOAD_WAVES"
                                                             for t in item.targets))]
            cls = ast.ClassDef(name="TpotProtocol", bases=[], keywords=[], body=keep, decorator_list=[])
            module = ast.Module([ast.ImportFrom("__future__", [ast.alias("annotations")], 0), cls], [])
            ast.fix_missing_locations(module)
            exec(compile(module, "<patched protocol>", "exec"), namespace)  # noqa: S102
            return namespace["TpotProtocol"]
    raise AssertionError("HoneyPotBaseProtocol not found")


class Fs:
    def __init__(self, files):
        self.files = files

    def file_contents(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class Base:
    """The parts of Cowrie's HoneyPotCommand that ps / top / uptime / w use."""

    def __init__(self, protocol, *args):
        self.protocol = protocol
        self.args = list(args)
        self.out = ""
        self.err = ""
        self.code = None
        self.exited = False
        self.queued = []
        self.environ = dict(protocol.environ)
        self.user = {"username": protocol.username}
        self.fs = protocol.fs
        self.pp = protocol.pp
        protocol.cmdstack.append(self)

    def write(self, text):
        self.out += text

    def errorWrite(self, text):  # noqa: N802 - Cowrie's name
        self.err += text

    def exit(self, code=None):
        self.exited = True
        self.code = 0 if code is None else code
        if self in self.protocol.cmdstack:
            self.protocol.cmdstack.remove(self)

    def lineReceived(self, line):  # noqa: N802 - Cowrie's name
        self.queued.append(line)

    def handle_CTRL_C(self):  # noqa: N802 - Cowrie's name
        self.write("^C\n")
        self.exit(130)

    def eofReceived(self):  # noqa: N802 - Cowrie's name
        pass


class Session:
    """A patched base.py / uptime.py / protocol with one session: config, clock, reactor, terminal."""

    def __init__(self, test, config=None, window=(24, 80), target="terminal", exec_mode=False, processes=None,
                 username="ubuntu", files=None, telnet=False):
        root = test.root
        values = {("shell", "top_style"): "procps", ("shell", "procps_version"): "4.0.4",
                  ("shell", "kernel_threads"): "77", ("shell", "load_base"): "0.06",
                  ("shell", "ssh_version"): "OpenSSH_9.6p1 Ubuntu-3ubuntu13.14, OpenSSL 3.0.13 30 Jan 2024"}
        values.update(config or {})
        self.config = Config(values)
        self.clock = Clock()
        self.reactor = Reactor()

        class Utils:
            @staticmethod
            def uptime(seconds):
                return "x"

        class LoopSignal:
            BREAK, CONTINUE = "break", "continue"

        common = {"CowrieConfig": self.config, "reactor": self.reactor, "utils": Utils, "Logger": lambda: None,
                  "A_MODE": 0, "A_SIZE": 0, "LoopSignal": LoopSignal}
        self.base = module_all((root / "src/cowrie/commands/base.py").read_text(),
                               dict(common, HoneyPotCommand=Base))
        self.uptime_module = module_all((root / "src/cowrie/commands/uptime.py").read_text(),
                                        dict(common, HoneyPotCommand=Base))
        protocol_ns = {"CowrieConfig": self.config, "hashlib": hashlib, "math": math}
        cls = protocol_class((root / "src/cowrie/shell/protocol.py").read_text(), protocol_ns)
        for namespace in (self.base, self.uptime_module, protocol_ns):
            namespace["time"] = self.clock
        clock = self.clock

        class Server:
            process = PS if processes is None else processes

        class User:
            server = Server()

        if window is not None:
            User.windowSize = list(window)

        class Pipe:
            targets = {1: (target, None), 2: ("terminal", None)}

        class Terminal:
            def __init__(self):
                self.written = b""

            def write(self, data):
                self.written += data

        resets = self.resets = []

        class Protocol(cls):
            hostname = "ubuntu-s-2vcpu-4gb-fra1-60"
            logintime = NOW - 600

            def resetTimeout(self):  # noqa: N802 - Twisted's name
                resets.append(clock.now)
            clientIP = "198.51.100.7"  # noqa: N815 - Cowrie's name
            terminal = object()

            def boot_time(self):
                return NOW - 1_000_000

        if telnet:
            Protocol = type("HoneyPotInteractiveTelnetProtocol", (Protocol,), {})  # noqa: N806 - Cowrie's name
        self.protocol = Protocol()
        self.protocol.terminal = Terminal()
        self.protocol.user = User()
        self.protocol.pp = Pipe()
        self.protocol.cmdstack = []
        self.protocol.environ = {} if window is None else {"COLUMNS": str(window[1]), "LINES": str(window[0])}
        self.protocol.username = username
        self.protocol.fs = Fs({"/proc/meminfo": MEMINFO, "/proc/cpuinfo": CPUINFO} if files is None else files)
        if exec_mode:
            self.protocol.execcmd = "top"
        self.clock_ref = clock

    def run(self, name, *args):
        command = self.base.get(name) or self.uptime_module.get(name)
        cmd = command(self.protocol, *args)
        if hasattr(cmd, "start") and name == "Command_top":
            cmd.start()
        else:
            cmd.call()
            cmd.exit()
        return cmd

    def advance(self, seconds):
        """Let the clock run on and the reactor fire what is due."""
        self.clock.now += seconds
        for call in list(self.reactor.pending()):
            call.live = False
            call.fn()


def strip(text):
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


class PatchedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = patched_root()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root.parent, ignore_errors=True)


class PsWidthTest(PatchedTest):
    def ps(self, *args, **session):
        return Session(self, **session).run("Command_ps", *args).out.splitlines()

    def test_a_pipe_gets_the_whole_line(self):
        lines = self.ps("aux", target="capture")
        self.assertTrue(any("sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups" in line for line in lines))
        self.assertTrue(any(line.endswith("--wait-for-signal") for line in lines))

    def test_a_terminal_cuts_at_its_width(self):
        lines = self.ps("aux", window=(24, 80))
        self.assertTrue(lines)
        self.assertEqual(max(len(line) for line in lines), 80)
        lines = self.ps("aux", window=(40, 200))
        self.assertTrue(any(len(line) > 80 for line in lines))
        self.assertLessEqual(max(len(line) for line in lines), 200)

    def test_w_makes_132_columns_and_ww_no_limit(self):
        self.assertEqual(max(len(line) for line in self.ps("auxw", window=(24, 80))), 132)
        self.assertTrue(any(line.endswith("--wait-for-signal") for line in self.ps("auxww", window=(24, 80))))

    def test_ssh_without_a_pty_and_a_file_get_the_whole_line(self):
        self.assertTrue(any(line.endswith("--wait-for-signal") for line in self.ps("aux", window=None)))
        self.assertTrue(any(line.endswith("--wait-for-signal") for line in self.ps("aux", target="file")))

    def test_an_exported_columns_wins_on_the_terminal(self):
        session = Session(self, window=(24, 200))
        session.protocol.environ["COLUMNS"] = "100"
        self.assertEqual(max(len(line) for line in session.run("Command_ps", "aux").out.splitlines()), 100)
        session.protocol.environ["COLUMNS"] = "wide"
        self.assertLessEqual(max(len(line) for line in session.run("Command_ps", "aux").out.splitlines()), 200)


class LoadTest(PatchedTest):
    def test_load_is_never_negative_and_moves_slowly(self):
        session = Session(self)
        protocol = session.protocol
        samples = [protocol.tpot_load_average(NOW + t) for t in range(0, 4 * 3600, 30)]
        self.assertTrue(all(min(sample) >= 0 for sample in samples))
        self.assertTrue(all(max(sample) < 0.2 for sample in samples))
        # the 1 minute average moves, the 15 minute one less, nothing jumps between two calls
        spread = [max(s[i] for s in samples) - min(s[i] for s in samples) for i in range(3)]
        self.assertGreater(spread[0], spread[2])
        self.assertGreater(spread[0], 0.01)
        for before, after in zip(samples, samples[1:]):
            self.assertLess(abs(after[0] - before[0]), 0.02)

    def test_no_load_without_a_base(self):
        session = Session(self, config={("shell", "load_base"): "0"})
        self.assertEqual(session.protocol.tpot_load_average(NOW), (0.0, 0.0, 0.0))

    def test_uptime_w_and_proc_loadavg_agree(self):
        session = Session(self)
        l1, l5, l15 = session.protocol.tpot_load_average(NOW)
        load = f"{l1:.2f}, {l5:.2f}, {l15:.2f}"
        uptime = session.run("Command_uptime").out
        self.assertEqual(uptime, f" 04:00:00 up 11 days, 13:46,  1 user,  load average: {load}\n")
        self.assertEqual(session.run("Command_w").out.splitlines()[0], uptime.rstrip("\n"))
        loadavg = session.protocol.proc_loadavg().decode()
        self.assertEqual(loadavg, f"{l1:.2f} {l5:.2f} {l15:.2f} 1/{len(PS) + 2 + 77} {3089 + 9 + 600 // 7}\n")

    def test_uptime_of_busybox_names_no_users(self):
        session = Session(self, config={("shell", "top_style"): "busybox"})
        self.assertRegex(session.run("Command_uptime").out,
                         r"^ 04:00:00 up 11 days, 13:46,  load average: \d\.\d\d, \d\.\d\d, \d\.\d\d\n$")

    def test_uptime_under_an_hour_and_a_day(self):
        session = Session(self)
        session.clock.now = NOW - 1_000_000 + 17 * 60 + 5
        self.assertIn(" up 17 min,  1 user,", session.run("Command_uptime").out)
        session.clock.now = NOW - 1_000_000 + 86400 + 4 * 3600 + 22 * 60
        self.assertIn(" up 1 day,  4:22,  1 user,", session.run("Command_uptime").out)


class TopProcpsTest(PatchedTest):
    def test_batch_frame(self):
        session = Session(self, target="capture")
        top = session.run("Command_top", "-b", "-n", "1")
        self.assertTrue(top.exited)
        self.assertEqual(top.code, 0)
        lines = top.out.split("\n")
        self.assertEqual(lines[0], "top -" + session.protocol.tpot_uptime_line(NOW))
        self.assertEqual(lines[1], f"Tasks:{len(PS) + 79:4d} total,   1 running,{len(PS) + 78:4d} sleeping,"
                                   "   0 stopped,   0 zombie")
        # since boot: 1 to 4 % busy, per host
        cpu = re.fullmatch(r"%Cpu\(s\):( +\d\.\d) us,( +\d\.\d) sy,  0\.0 ni,( 9\d\.\d) id,( +\d\.\d) wa,  0\.0 hi,"
                           r"( +\d\.\d) si,  0\.0 st", lines[2])
        self.assertIsNotNone(cpu, lines[2])
        self.assertAlmostEqual(sum(float(x) for x in cpu.groups()), 100.0, places=1)
        self.assertTrue(96.0 <= float(cpu.group(3)) <= 99.0)
        # used is total less free and buff/cache, as procps reads /proc/meminfo
        self.assertEqual(lines[3], "MiB Mem :   3911.3 total,   1484.4 free,   1069.5 used,   1357.4 buff/cache")
        self.assertEqual(lines[4], "MiB Swap:      0.0 total,      0.0 free,      0.0 used.   2939.5 avail Mem")
        self.assertEqual(lines[5], "")
        self.assertEqual(lines[6], "    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND")
        rows = [line for line in lines[7:] if line]
        self.assertEqual(len(rows), len(PS) + 2)
        self.assertRegex(rows[0], r"^ +\d+ ubuntu    20   0   11928   5376 +\d+ R +[\d.]+ +0\.1 +0:00\.01 top$")
        self.assertRegex(" ".join(rows), r"  1 root      20   0  168520   8140 +\d+ S   0\.0   0\.2 +\d+:\d\d\.\d\d init")
        self.assertIn(" unattended-upgr", " ".join(rows))
        self.assertRegex(" ".join(rows), r" 1700 root      16  -4 ")
        self.assertRegex(" ".join(rows), r" 3088 ubuntu .* sshd ")
        self.assertTrue(top.out.endswith("\n\n"))
        self.assertEqual(session.reactor.pending(), [])

    def test_two_calls_a_few_seconds_apart_show_the_time_passing(self):
        session = Session(self, target="capture")
        first = session.run("Command_top", "-bn1").out.split("\n")
        session.clock.now += 7
        second = session.run("Command_top", "-bn1").out.split("\n")
        self.assertRegex(first[0], r"^top - 04:00:00 up 11 days, 13:46,  1 user,  load average: ")
        self.assertRegex(second[0], r"^top - 04:00:07 up 11 days, 13:46,  1 user,  load average: ")
        # top itself got a newer PID; an hour later the daemons have more CPU time
        self.assertNotEqual([line for line in first if line.endswith(" top")],
                            [line for line in second if line.endswith(" top")])
        session.clock.now += 3600
        later = session.run("Command_top", "-bn1").out.split("\n")
        self.assertNotEqual([line for line in first if line.endswith(" init")],
                            [line for line in later if line.endswith(" init")])

    def test_old_procps_shows_kib_and_narrow_columns(self):
        session = Session(self, config={("shell", "procps_version"): "3.3.10"}, target="capture")
        lines = session.run("Command_top", "-b", "-n1").out.split("\n")
        self.assertEqual(lines[3], "KiB Mem :  4005216 total,  1520000 free,  1095216 used,  1390000 buff/cache")
        self.assertEqual(lines[4], "KiB Swap:        0 total,        0 free,        0 used.  3010000 avail Mem")
        self.assertEqual(lines[6], "  PID USER      PR  NI    VIRT    RES    SHR S %CPU %MEM     TIME+ COMMAND")
        self.assertRegex(lines[7], r"^ *\d+ ubuntu    20   0  162020   2228 +\d+ R +[\d.]+  0\.1 +0:00\.01 top$")

    def test_n_frames_with_the_delay_then_the_end(self):
        session = Session(self, target="capture")
        top = session.run("Command_top", "-b", "-n", "3", "-d", "2")
        self.assertEqual(top.out.count("top - "), 1)
        self.assertEqual([c.delay for c in session.reactor.pending()], [2.0])
        session.advance(2)
        session.advance(2)
        self.assertEqual(top.out.count("top - "), 3)
        self.assertTrue(top.exited)
        self.assertEqual(session.reactor.pending(), [])
        self.assertIn("top - 04:00:04 ", top.out)

    def test_batch_to_a_pipe_without_n_is_one_frame(self):
        session = Session(self, target="capture")
        top = session.run("Command_top", "-b")
        self.assertEqual(top.out.count("top - "), 1)
        self.assertTrue(top.exited)

    def test_batch_on_the_terminal_ends_by_itself(self):
        session = Session(self)
        top = session.run("Command_top", "-b")
        for _ in range(1000):
            if top.exited:
                break
            session.advance(3)
        self.assertTrue(top.exited)
        self.assertEqual(top.out.count("top - "), session.base["TPOT_TOP_FRAMES"])

    def test_interactive_screen_ends_with_q(self):
        session = Session(self, window=(24, 80))
        top = session.run("Command_top")
        self.assertTrue(top.out.startswith("\x1b[?25l\x1b[H\x1b[2J\x1b[H"))
        screen = strip(top.out).split("\n")
        self.assertEqual(len(screen), 7 + len(PS) + 2)
        self.assertIn("\x1b[7m    PID USER", top.out)
        self.assertTrue(all(len(line) <= 80 for line in screen))
        self.assertEqual(top.tpot_key(b"x"), True)
        self.assertFalse(top.exited)
        session.advance(10)
        self.assertEqual(top.out.count("top - "), 2)
        # the running top keeps its PID from frame to frame
        frames = strip(top.out).split("top - ")[1:]
        self.assertEqual(len({re.search(r"\n +(\d+) ubuntu .* top", f).group(1) for f in frames}), 1)
        top.tpot_key(b"q")
        self.assertTrue(top.exited)
        self.assertTrue(top.out.endswith("\x1b[24;1H\x1b[?25h\n"))
        self.assertEqual(session.reactor.pending(), [])

    def test_interactive_screen_fits_the_window(self):
        session = Session(self, window=(12, 50))
        top = session.run("Command_top")
        screen = strip(top.out).split("\n")
        self.assertEqual(len(screen), 12)
        self.assertTrue(all(len(line) <= 50 for line in screen))
        top.tpot_key(b"q")
        self.assertTrue(top.out.endswith("\x1b[12;1H\x1b[?25h\n"))

    def test_interactive_screen_ends_with_ctrl_c_and_by_itself(self):
        session = Session(self)
        top = session.run("Command_top")
        top.handle_CTRL_C()
        self.assertTrue(top.exited)
        self.assertEqual(top.code, 0)
        self.assertNotIn("^C", top.out)
        top = session.run("Command_top", "-n", "2")
        session.advance(3)
        self.assertTrue(top.exited)
        top = session.run("Command_top")
        top.tpot_key(b"\x03")
        self.assertTrue(top.exited)

    def test_enter_draws_now_and_is_no_command(self):
        session = Session(self)
        top = session.run("Command_top")
        top.lineReceived("")
        self.assertEqual(top.queued, [])
        self.assertEqual(top.out.count("top - "), 2)
        top.tpot_key(b"q")

    def test_a_batch_top_does_not_take_the_keys(self):
        session = Session(self)
        top = session.run("Command_top", "-b", "-n", "2")
        self.assertFalse(top.tpot_key(b"q"))
        top.lineReceived("ls")
        self.assertEqual(top.queued, ["ls"])
        session.advance(3)

    def test_ssh_without_a_pty(self):
        session = Session(self, window=None, exec_mode=True)
        top = session.run("Command_top")
        self.assertEqual((top.err, top.code), ("TERM environment variable not set.\n", 1))
        session.protocol.environ["TERM"] = "xterm"
        top = session.run("Command_top")
        self.assertEqual((top.err, top.code), ("top: failed tty get\n", 1))
        top = session.run("Command_top", "-bn1")
        self.assertEqual(top.code, 0)
        self.assertIn("Tasks:", top.out)

    def test_options(self):
        session = Session(self, target="capture")
        for args, err in ((["-x"], "top: unknown option 'x'\nUsage:\n  top -hv | -bcEeHiOSs1 -d secs -n max"),
                          (["-n", "x"], "top: bad iterations argument 'x'\n"),
                          (["-n", "-1"], "top: bad iterations argument '-1'\n"),
                          (["-d", "soon"], "top: bad delay interval 'soon'\n"),
                          (["-n"], "top: -n requires argument")):
            with self.subTest(args):
                top = session.run("Command_top", *args)
                self.assertTrue(top.err.startswith(err), top.err)
                self.assertEqual(top.code, 1)
        top = session.run("Command_top", "-v")
        self.assertTrue(top.out.startswith("  procps-ng 4.0.4\nUsage:\n"))
        old = Session(self, config={("shell", "procps_version"): "3.3.10"}, target="capture")
        self.assertTrue(old.run("Command_top", "-v").out.startswith("  procps-ng version 3.3.10\n"))
        top = session.run("Command_top", "-b", "-c", "-w", "-n1", "-p", "1,3089")
        rows = [line for line in top.out.split("\n")[7:] if line]
        self.assertEqual([int(r.split()[0]) for r in rows], [1, 3089])
        top = session.run("Command_top", "-bn1", "-u", "root")
        self.assertTrue(all(" root " in r for r in top.out.split("\n")[7:] if r))

    def test_numbers_of_options_are_ascii_digits(self):
        # str.isdigit and int take digits top does not (superscripts, other scripts, > 4300 digits)
        session = Session(self, target="capture")
        for value in ("\u00b2", "\u0663", "9" * 5000, "1_0", " 5", "2147483648"):
            with self.subTest(value=value[:12]):
                top = session.run("Command_top", "-b", "-n", value)
                self.assertEqual((top.err, top.code), (f"top: bad iterations argument '{value}'\n", 1))
        for value in ("\u00b2", "\u0663", "1_0", "9" * 5000):
            with self.subTest(delay=value[:12]):
                top = session.run("Command_top", "-b", "-d", value)
                self.assertEqual((top.err, top.code), (f"top: bad delay interval '{value}'\n", 1))
        for value in ("1.5", "0,5", ".5", "2"):
            self.assertEqual(session.run("Command_top", "-bn1", "-d", value).code, 0, value)
        top = session.run("Command_top", "-bn1", "-p", "\u00b2,1," + "9" * 5000)
        self.assertEqual(top.code, 0)
        self.assertEqual([int(r.split()[0]) for r in top.out.split("\n")[7:] if r], [1])
        # -w takes a following word only when it is a number: another word is no option of top
        top = session.run("Command_top", "-bn1", "-w", "\u00b2")
        self.assertEqual(top.code, 1)
        self.assertTrue(top.err.startswith("top: unknown option"), top.err)
        self.assertEqual(session.run("Command_top", "-bn1", "-w", "200").code, 0)

    def test_top_exits_on_an_unexpected_error(self):
        session = Session(self, target="capture")

        def fail(*args):
            raise RuntimeError("unexpected")

        session.base["Command_top"].tpot_frame = fail
        top = session.run("Command_top", "-bn1")
        self.assertEqual((top.exited, top.code), (True, 1))
        session.base["Command_top"].tpot_options = fail
        top = session.run("Command_top", "-bn1")
        self.assertEqual((top.exited, top.code), (True, 1))

    def test_without_a_ps_list_or_meminfo(self):
        session = Session(self, processes=[], files={}, target="capture")
        top = session.run("Command_top", "-bn1")
        self.assertEqual(top.code, 0)
        self.assertIn("MiB Mem :      0.0 total", top.out)


class TopSessionTest(PatchedTest):
    """What surrounds the screen of top: the session's processes, the idle timeout, the cursor."""

    DEVICE = {("shell", "top_style"): "busybox", ("shell", "ssh_version"): "Dropbear v2022.82",
              ("shell", "kernel_threads"): "38"}

    def rows(self, session):
        top = session.run("Command_top", "-bn1")
        return {int(line.split()[0]): line for line in top.out.split("\n") if re.match(r"^ *\d+ ", line)}

    def test_telnet_on_busybox_runs_the_shell_under_telnetd(self):
        # BusyBox telnetd starts login, which execs the shell: telnetd -> shell, no sshd or dropbear
        ps = [dict(PS[0], COMMAND="/sbin/init", STAT="S"),
              dict(PS[2], PID=640, COMMAND="/usr/sbin/telnetd", VSZ=1912),
              dict(PS[5], PID=900, USER="admin", COMMAND="-sh", VSZ=1500)]
        rows = self.rows(Session(self, config=self.DEVICE, processes=ps, username="admin", telnet=True,
                                 target="capture"))
        self.assertRegex(rows[900], r"^  900   640 admin ")
        self.assertNotIn("dropbear", " ".join(rows.values()))
        self.assertEqual(len(rows), 4)
        # a device whose ps list has no telnetd gets one for the session
        ps = [dict(PS[0], COMMAND="/sbin/procd", STAT="S"), dict(PS[5], PID=900, USER="root", COMMAND="-ash")]
        rows = self.rows(Session(self, config=self.DEVICE, processes=ps, username="root", telnet=True,
                                 target="capture"))
        self.assertRegex(rows[899], r"^  899     1 root     S .* /usr/sbin/telnetd$")
        self.assertRegex(rows[900], r"^  900   899 root ")

    def test_telnet_on_a_server_is_in_telnetd_and_login(self):
        rows = self.rows(Session(self, telnet=True, target="capture"))
        self.assertRegex(rows[3087], r" root .* in\.telnetd$")
        self.assertRegex(rows[3088], r" root .* login$")
        # only the listener of the ps list is an sshd
        self.assertEqual([pid for pid, line in rows.items() if line.endswith(" sshd")], [1391])

    def test_ssh_keeps_the_sshd_of_the_session(self):
        rows = self.rows(Session(self, target="capture"))
        self.assertRegex(rows[3088], r" ubuntu .* sshd$")
        self.assertNotIn("telnetd", " ".join(rows.values()))

    def ps_session(self, session):
        """The lines of ps aux that are not in the persona's list: the session's processes, the
        shell, ps (in the order of their PID)."""
        lines = session.run("Command_ps", "aux").out.splitlines()
        listed = {p["PID"] for p in session.protocol.user.server.process}
        return [[word.rstrip() for word in line.split(None, 10)]
                for line in lines[1:] if int(line.split()[1]) not in listed]

    def test_ps_lists_every_row_by_pid(self):
        """ps sorts its rows by PID, as real ps does: the session's server process comes before its
        shell, a daemon started after the login after the shell and ps."""
        ps = PS + [dict(PS[4], PID=5012, COMMAND="/usr/sbin/cron -f")]
        for args in ("aux", "ax"):
            session = Session(self, processes=ps, target="capture")
            lines = session.run("Command_ps", args).out.splitlines()[1:]
            pids = [int(line.split()["u" in args]) for line in lines]
            with self.subTest(args=args):
                self.assertEqual(pids, sorted(pids))
                self.assertIn(session.protocol.tpot_session_pid, pids)
                self.assertEqual(pids[-1], 5012)
        # without a or x the processes of the terminal only, in the same order
        lines = Session(self, processes=ps, target="capture").run("Command_ps").out.splitlines()[1:]
        self.assertEqual([int(line.split()[0]) for line in lines], [3089, 3091])

    def test_ps_and_top_name_one_session_process(self):
        """ps, top, ss and netstat show the session's server process with one PID (tpot_session_pid)
        and one name: just below the login shell of the ps list."""
        session = Session(self, target="capture")
        lines = self.ps_session(session)
        self.assertEqual(session.protocol.tpot_session_pid, 3088)
        self.assertEqual([(line[0], line[1], line[10]) for line in lines[:1]], [("ubuntu", "3088", "sshd: ubuntu@pts/0")])
        self.assertNotIn("-bash", [line[10] for line in lines])
        self.assertRegex(self.rows(session)[3088], r" ubuntu .* sshd$")
        # OpenSSH 9.8 and later: sshd-session
        session = Session(self, config={("shell", "ssh_version"): "OpenSSH_10.0p2 Debian-7, OpenSSL 3.5.4 30 Sep 2025"},
                          target="capture")
        self.assertEqual(self.ps_session(session)[0][10], "sshd-session: ubuntu@pts/0")
        self.assertRegex(self.rows(session)[3088], r" ubuntu .* sshd-session$")

    def test_ps_and_top_on_dropbear_and_busybox_telnetd(self):
        dropbear = "/usr/sbin/dropbear -F -P /var/run/dropbear.1.pid -p 22 -K 300 -T 3"
        ps = [dict(PS[0], COMMAND="/sbin/procd", STAT="S"), dict(PS[2], PID=1180, COMMAND=dropbear),
              dict(PS[5], PID=1520, USER="root", COMMAND="-ash")]
        session = Session(self, config=self.DEVICE, processes=ps, username="root", target="capture")
        lines = self.ps_session(session)
        self.assertEqual((lines[0][0], lines[0][1], lines[0][10]), ("root", "1519", dropbear))
        self.assertNotIn("sshd", " ".join(line[10] for line in lines))
        self.assertRegex(self.rows(session)[1519], r"^ 1519  1180 root .* /usr/sbin/dropbear -F ")
        # BusyBox telnetd serves the connection itself: no process of its own for the session
        ps = [dict(PS[0], COMMAND="/sbin/init", STAT="S"), dict(PS[2], PID=640, COMMAND="/usr/sbin/telnetd"),
              dict(PS[5], PID=900, USER="admin", COMMAND="-sh")]
        session = Session(self, config=self.DEVICE, processes=ps, username="admin", telnet=True, target="capture")
        lines = self.ps_session(session)
        self.assertEqual(session.protocol.tpot_session_pid, 640)
        self.assertEqual([line[10] for line in lines], ["ps aux"])

    def test_top_takes_the_session_pid(self):
        """Without a login shell in the ps list the session's PID is drawn once; top shows it too."""
        session = Session(self, processes=PS[:3], target="capture")
        session.protocol.tpot_session_pid = 5000
        self.assertRegex(self.rows(session)[5000], r" ubuntu .* sshd$")
        self.assertEqual(self.ps_session(session)[0][1], "5000")

    def test_the_screen_keeps_the_session_from_the_idle_timeout(self):
        session = Session(self)
        top = session.run("Command_top")
        for _ in range(5):
            session.advance(3)
        self.assertEqual(len(session.resets), 6)
        top.tpot_key(b"q")
        # a batch top is no screen: the idle timeout stays
        session = Session(self)
        session.run("Command_top", "-b", "-n", "3")
        session.advance(3)
        self.assertEqual(session.resets, [])

    def test_ctrl_d_ends_the_screen_and_brings_the_cursor_back(self):
        session = Session(self)
        top = session.run("Command_top")
        top.eofReceived()
        self.assertTrue(top.exited)
        self.assertTrue(top.out.endswith("\x1b[?25h\n"))
        self.assertEqual(session.reactor.pending(), [])

    def test_the_cursor_comes_back_when_the_shell_ends_under_top(self):
        session = Session(self)
        top = session.run("Command_top")
        session.protocol.cmdstack.remove(top)
        session.advance(3)
        self.assertEqual(session.protocol.terminal.written, b"\x1b[?25h")
        self.assertEqual(session.reactor.pending(), [])
        # once only, and nothing after the end of the screen
        top.tpot_restore()
        self.assertEqual(session.protocol.terminal.written, b"\x1b[?25h")
        top = session.run("Command_top")
        top.tpot_key(b"q")
        top.tpot_restore()
        self.assertEqual(session.protocol.terminal.written, b"\x1b[?25h")

    def test_the_idle_timeout_gives_the_cursor_back_first(self):
        protocol = (self.root / "src/cowrie/shell/protocol.py").read_text()
        self.assertRegex(protocol, r"def timeoutConnection\(self\) -> None:\n(.*\n){4}"
                                   r"        # T-Pot: .*\n        restore = getattr\(self\.cmdstack\[-1\], \"tpot_restore\"")


class TopBusyboxTest(PatchedTest):
    def test_busybox_frame(self):
        session = Session(self, config={("shell", "top_style"): "busybox", ("shell", "kernel_threads"): "38",
                                        ("shell", "ssh_version"): "Dropbear v2022.82"},
                          processes=[dict(PS[0], COMMAND="/sbin/procd", VSZ=1528, STAT="S"),
                                     dict(PS[2], PID=1180, USER="root", VSZ=1104,
                                          COMMAND="/usr/sbin/dropbear -F -P /var/run/dropbear.1.pid -p 22 -K 300 -T 3"),
                                     dict(PS[5], PID=1520, USER="root", COMMAND="-ash", VSZ=1500)],
                          username="root", target="capture")
        lines = session.run("Command_top", "-b", "-n1").out.split("\n")
        self.assertEqual(lines[0], "Mem: 2485216K used, 1520000K free, 9000K shrd, 90000K buff, 1300000K cached")
        self.assertRegex(lines[1], r"^CPU:   \d% usr   \d% sys   0% nic  9\d% idle   0% io   0% irq   \d% sirq$")
        self.assertRegex(lines[2], r"^Load average: \d\.\d\d \d\.\d\d \d\.\d\d 1/43 \d+$")
        self.assertEqual(lines[3], "  PID  PPID USER     STAT   VSZ %VSZ %CPU COMMAND")
        self.assertRegex(lines[4], r"^ *\d+  1520 root     R     1408   0% +\d+% top$")
        self.assertIn("    1     0 root     S     1528   0%   0% /sbin/procd", lines)
        self.assertIn(" 1519  1180 root     S     1104   0%   0% /usr/sbin/dropbear -F -P /var/run/dropbear.1.pid"
                      " -p 22 -K 300 -T 3", lines)
        self.assertIn(" 1520  1519 root     S     1500   0%   0% -ash", lines)

    def test_busybox_options(self):
        session = Session(self, config={("shell", "top_style"): "busybox"}, target="capture")
        top = session.run("Command_top", "-c")
        self.assertEqual((top.err, top.code), ("top: invalid option -- 'c'\nUsage: top [-b] [-nCOUNT] [-dSECONDS] [-m]\n", 1))
        top = Session(self, config={("shell", "top_style"): "busybox"}).run("Command_top")
        self.assertIn("\x1b[7m  PID  PPID USER", top.out)
        top.tpot_key(b"q")
        self.assertTrue(top.exited)
        # BusyBox refreshes every 5 seconds
        top = Session(self, config={("shell", "top_style"): "busybox"})
        top.run("Command_top")
        self.assertEqual([c.delay for c in top.reactor.pending()], [5.0])


class KeyHookTest(PatchedTest):
    def test_the_terminal_keys_reach_a_full_screen_command(self):
        protocol = (self.root / "src/cowrie/shell/protocol.py").read_text()
        self.assertIn('self.fs.generated_files["/proc/loadavg"] = self.proc_loadavg\n', protocol)
        # interactive: before the line buffer and the echo; exec with a pty: before the stdin lines
        self.assertRegex(protocol, r"def characterReceived\(self, ch, moreCharactersComing\):\n(.*\n){3}"
                                   r"        self\.resetTimeout\(\)  # Reset the idle timeout\n"
                                   r"        # T-Pot: .*\n        key = getattr\(self\.cmdstack\[-1\], \"tpot_key\"")
        self.assertRegex(protocol, r"def keystrokeReceived\(self, keyID, modifier\):\n        # T-Pot: .*\n"
                                   r"        key = getattr\(self\.cmdstack\[-1\], \"tpot_key\", None\)")


if __name__ == "__main__":
    unittest.main()
