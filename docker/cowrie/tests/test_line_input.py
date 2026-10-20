"""Interactive line input of the patched Cowrie (core/ttylog.py, shell/protocol.py).

- The tty log of a session stays open from ttylog_open to ttylog_close: a record is a write into its
  buffer, the buffers are written out once per reactor turn and at the close. Its records and its
  input hash stay those of upstream; the input hash is read in linear time.
- A line of the interactive shell holds at most 64 KiB. Beyond it, a key is echoed as bash echoes it
  but not kept, so the line runs (and is logged) cut to 64 KiB and the session goes on.
- Below the bound, the echo, the line and the cursor are those of upstream.
"""
import ast
import hashlib
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, method, module_parts
from tpot_personas import catalog, system

TTYLOG = "src/cowrie/core/ttylog.py"
PROTOCOL = "src/cowrie/shell/protocol.py"
LINE_MAX = 65536
# the open and close of a file on a Docker Desktop bind mount (log/tty of the smoke test), measured
SLOW_OPEN_SECONDS = 0.002
# input records of the input hash test (and four times as many); the largest ratio of their times
HASH_KEYS = 25000
HASH_RATIO_MAX = 8.0
# the buffer of an open tty log (Python 3.14 would take its io.DEFAULT_BUFFER_SIZE of 128 KiB)
TTYLOG_BUFFER = 8192


def load_ttylog(root):
    """core/ttylog.py of root as a module of its own; the reactor turns it asks for are collected in
    `turns` (run them with run_turns)."""
    module = types.ModuleType("ttylog")
    exec(compile((root / TTYLOG).read_text(), "<ttylog>", "exec"), module.__dict__)  # noqa: S102
    module.turns = []
    if hasattr(module, "_tpot_schedule"):
        module._tpot_schedule = module.turns.append
    return module


def run_turns(module):
    while module.turns:
        module.turns.pop(0)()


def counting_open(module, delay=0.0):
    """Counts (and delays) every open() of the module."""
    calls = []
    module.open_kwargs = []

    def opener(*args, **kwargs):
        calls.append(args[0])
        module.open_kwargs.append(kwargs)
        if delay:
            time.sleep(delay)
        return open(*args, **kwargs)

    module.open = opener
    return calls


def records(path):
    """The records of a tty log without their time: (op, tty, length, direction, data)."""
    size = struct.calcsize("<iLiiLL")
    out = []
    with open(path, "rb") as handle:
        while True:
            head = handle.read(size)
            if len(head) < size:
                return out
            op, tty, length, direction, _sec, _usec = struct.unpack("<iLiiLL", head)
            out.append((op, tty, length, direction, handle.read(length)))


def class_constants(source, cls):
    """The literal class attributes of class cls."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {item.targets[0].id: ast.literal_eval(item.value) for item in node.body
                    if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name)}
    raise AssertionError(f"class {cls} not found")


def class_methods(source, cls):
    """{name: source} of the methods of class cls."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {item.name: ast.get_source_segment(source, item) for item in node.body
                    if isinstance(item, ast.FunctionDef)}
    raise AssertionError(f"class {cls} not found")


class Terminal:
    """insults.LoggingServerProtocol.write: the record of the tty log, then the bytes to the client."""

    def __init__(self, ttylog, path):
        self.ttylog = ttylog
        self.path = path
        self.out = bytearray()

    def write(self, data):
        self.ttylog.ttylog_write(self.path, len(data), self.ttylog.TYPE_OUTPUT, time.time(), data)
        self.out += data


def protocol_class(source):
    """HoneyPotInteractiveProtocol.characterReceived of source on a stand-in with recvline's state."""
    character_received = method(source, "HoneyPotInteractiveProtocol", "characterReceived")
    constants = class_constants(source, "HoneyPotInteractiveProtocol")

    class Protocol:
        mode = "insert"
        password_input = False
        characterReceived = character_received

        def __init__(self, terminal):
            self.terminal = terminal
            self.lineBuffer = []
            self.lineBufferIndex = 0
            self.cmdstack = []

        def resetTimeout(self):
            pass

    for name, value in constants.items():
        setattr(Protocol, name, value)
    return Protocol


def type_in(ttylog, protocol, path, data, packet):
    """insults.LoggingServerProtocol.dataReceived: one input record per packet of `packet` bytes, the
    bytes one by one to characterReceived, the reactor turn ends after each packet."""
    for start in range(0, len(data), packet):
        chunk = data[start:start + packet]
        ttylog.ttylog_write(path, len(chunk), ttylog.TYPE_INPUT, time.time(), chunk)
        for value in chunk:
            protocol.characterReceived(bytes([value]), False)
        run_turns(ttylog)


class Patched:
    root = None

    @classmethod
    def get(cls):
        if cls.root is None:
            handle = tempfile.TemporaryDirectory()
            unittest.addModuleCleanup(handle.cleanup)
            root = Path(handle.name) / "cowrie"
            shutil.copytree(FIXTURE, root)
            subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                           check=True, capture_output=True, text=True)
            cls.root = root
        return cls.root


class LineInputTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        self.dir = Path(handle.name)
        self.patched = Patched.get()

    def session(self, root, name="session.log", open_delay=0.0):
        ttylog = load_ttylog(root)
        opens = counting_open(ttylog, open_delay)
        path = str(self.dir / name)
        ttylog.ttylog_open(path, time.time())
        terminal = Terminal(ttylog, path)
        protocol = protocol_class((root / PROTOCOL).read_text())(terminal)
        return ttylog, protocol, terminal, path, opens

    # -- 10,000 characters -------------------------------------------------------------------------

    def test_ten_thousand_characters_are_taken_in_under_a_second(self):
        line = b"echo " + b"a" * 10000
        for packet in (1, len(line)):
            with self.subTest(packet=packet):
                ttylog, protocol, terminal, path, _ = self.session(
                    self.patched, f"timed-{packet}.log", open_delay=SLOW_OPEN_SECONDS)
                started = time.perf_counter()
                type_in(ttylog, protocol, path, line, packet)
                ttylog.ttylog_close(path, time.time())
                ttylog.ttylog_inputhash(path)
                elapsed = time.perf_counter() - started
                self.assertLess(elapsed, 1.0)
                self.assertEqual(bytes(terminal.out), line)
                self.assertEqual(b"".join(protocol.lineBuffer), line)
                self.assertEqual(protocol.lineBufferIndex, len(line))

    def test_the_tty_log_is_opened_once_per_session(self):
        ttylog, protocol, _, path, opens = self.session(self.patched)
        type_in(ttylog, protocol, path, b"echo " + b"a" * 2000, 1)
        ttylog.ttylog_close(path, time.time())
        self.assertEqual(opens, [path])

    def test_the_tty_log_has_a_buffer_of_8_kib(self):
        ttylog, _, terminal, path, opens = self.session(self.patched)
        self.assertEqual(ttylog.open_kwargs, [{"buffering": TTYLOG_BUFFER}])
        # a record of twice the buffer is on disk before the reactor turn ends
        terminal.write(b"b" * (2 * TTYLOG_BUFFER))
        self.assertEqual(records(path)[-1][4], b"b" * (2 * TTYLOG_BUFFER))
        ttylog.ttylog_close(path, time.time())
        run_turns(ttylog)

    def test_echo_line_and_tty_log_records_stay_upstream(self):
        rng = random.Random(7)
        line = bytes(rng.choice(b"abcdefghijklmnopqrstuvwxyz0123456789 -./|;$") for _ in range(600))
        logs = []
        for root in (FIXTURE, self.patched):
            for packet in (1, 7, len(line)):
                ttylog, protocol, terminal, path, _ = self.session(root, f"{root.name}-{packet}.log")
                type_in(ttylog, protocol, path, line, packet)
                ttylog.ttylog_close(path, time.time())
                logs.append((bytes(terminal.out), b"".join(protocol.lineBuffer), records(path),
                             ttylog.ttylog_inputhash(path)))
        self.assertEqual(logs[:3], logs[3:])

    # -- the tty log -------------------------------------------------------------------------------

    def test_records_are_written_out_once_per_reactor_turn(self):
        ttylog, _, terminal, path, _ = self.session(self.patched)
        for value in b"abcdef":
            terminal.write(bytes([value]))
        self.assertEqual(len(ttylog.turns), 1)
        run_turns(ttylog)
        self.assertEqual([r[4] for r in records(path)], [b"", b"a", b"b", b"c", b"d", b"e", b"f"])
        terminal.write(b"g")
        self.assertEqual(len(ttylog.turns), 1)
        ttylog.ttylog_close(path, time.time())
        self.assertEqual([r[0] for r in records(path)], [1, 3, 3, 3, 3, 3, 3, 3, 2])
        run_turns(ttylog)

    def test_close_writes_out_and_closes_the_log(self):
        ttylog, _, terminal, path, _ = self.session(self.patched)
        terminal.write(b"x" * 100000)
        ttylog.ttylog_close(path, time.time())
        self.assertEqual(records(path)[-2:], [(3, 0, 100000, 2, b"x" * 100000), (2, 0, 0, 0, b"")])
        self.assertEqual(ttylog._tpot_files, {})
        run_turns(ttylog)

    def test_a_log_ttylog_open_did_not_open_is_written_as_upstream(self):
        logs = []
        for root in (FIXTURE, self.patched):
            ttylog = load_ttylog(root)
            path = str(self.dir / f"{root.name}-plain.log")
            ttylog.ttylog_write(path, 3, ttylog.TYPE_INPUT, time.time(), b"abc")
            ttylog.ttylog_close(path, time.time())
            run_turns(ttylog)
            logs.append(records(path))
        self.assertEqual(logs[0], logs[1])

    def test_two_sessions_keep_their_own_logs(self):
        ttylog = load_ttylog(self.patched)
        first, second = str(self.dir / "first.log"), str(self.dir / "second.log")
        ttylog.ttylog_open(first, time.time())
        ttylog.ttylog_open(second, time.time())
        ttylog.ttylog_write(first, 1, ttylog.TYPE_INPUT, time.time(), b"1")
        ttylog.ttylog_write(second, 1, ttylog.TYPE_INPUT, time.time(), b"2")
        ttylog.ttylog_close(first, time.time())
        ttylog.ttylog_write(second, 1, ttylog.TYPE_OUTPUT, time.time(), b"3")
        run_turns(ttylog)
        self.assertEqual([r[4] for r in records(first)], [b"", b"1", b""])
        self.assertEqual([r[4] for r in records(second)], [b"", b"2", b"3"])
        ttylog.ttylog_close(second, time.time())
        self.assertEqual(ttylog._tpot_files, {})

    def test_input_hash_stays_upstream(self):
        rng = random.Random(3)
        path = str(self.dir / "mixed.log")
        upstream = load_ttylog(FIXTURE)
        upstream.ttylog_open(path, 1.5)
        for _ in range(500):
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
            direction = rng.choice((upstream.TYPE_INPUT, upstream.TYPE_OUTPUT, upstream.TYPE_INTERACT))
            upstream.ttylog_write(path, len(data), direction, time.time(), data)
        upstream.ttylog_close(path, time.time())
        self.assertEqual(load_ttylog(self.patched).ttylog_inputhash(path), upstream.ttylog_inputhash(path))

    def hash_seconds(self, ttylog, keys):
        """The best of three times ttylog_inputhash takes for `keys` keys of 64 bytes and their echo."""
        path = str(self.dir / f"many-{keys}.log")
        key = bytes(range(64))
        head_in = struct.pack("<iLiiLL", 3, 0, len(key), 1, 0, 0)
        head_out = struct.pack("<iLiiLL", 3, 0, len(key), 2, 0, 0)
        with open(path, "wb") as handle:
            handle.write(struct.pack("<iLiiLL", 1, 0, 0, 0, 0, 0))
            handle.write((head_in + key + head_out + key) * keys)
            handle.write(struct.pack("<iLiiLL", 2, 0, 0, 0, 0, 0))
        best = None
        for _ in range(3):
            started = time.perf_counter()
            digest = ttylog.ttylog_inputhash(path)
            elapsed = time.perf_counter() - started
            best = elapsed if best is None else min(best, elapsed)
        self.assertEqual(digest, hashlib.sha256(key * keys).hexdigest())
        return best

    def test_input_hash_reads_many_records_in_linear_time(self):
        # four times the records take about four times as long (a linear read); building the input
        # by concatenation takes about sixteen times as long
        ttylog = load_ttylog(self.patched)
        small = self.hash_seconds(ttylog, HASH_KEYS)
        large = self.hash_seconds(ttylog, 4 * HASH_KEYS)
        self.assertLess(large / small, HASH_RATIO_MAX, (small, large))

    def test_input_hash_of_a_log_still_open_has_every_record(self):
        ttylog, _, _, path, _ = self.session(self.patched)
        ttylog.ttylog_write(path, 4, ttylog.TYPE_INPUT, time.time(), b"ls\r\n")
        self.assertEqual(ttylog.ttylog_inputhash(path), hashlib.sha256(b"ls\r\n").hexdigest())
        ttylog.ttylog_close(path, time.time())

    # -- the bound of a line -----------------------------------------------------------------------

    def test_a_line_holds_at_most_64_kib_and_keeps_taking_keys(self):
        ttylog, protocol, terminal, path, _ = self.session(self.patched)
        line = b"echo " + b"a" * 70000
        type_in(ttylog, protocol, path, line, 4096)
        ttylog.ttylog_close(path, time.time())
        self.assertEqual(protocol.TPOT_LINE_MAX, LINE_MAX)
        self.assertEqual(b"".join(protocol.lineBuffer), line[:LINE_MAX])
        self.assertEqual(protocol.lineBufferIndex, LINE_MAX)
        # the echo goes on as bash's
        self.assertEqual(bytes(terminal.out), line)

    def test_typeover_at_the_bound_still_replaces(self):
        ttylog, protocol, terminal, path, _ = self.session(self.patched)
        type_in(ttylog, protocol, path, b"b" * LINE_MAX, LINE_MAX)
        protocol.mode = "typeover"
        protocol.lineBufferIndex = 10
        protocol.characterReceived(b"x", False)
        self.assertEqual(len(protocol.lineBuffer), LINE_MAX)
        self.assertEqual(protocol.lineBuffer[10], b"x")
        self.assertEqual(protocol.lineBufferIndex, 11)
        # at the end of a full line, typeover would add: not kept
        protocol.lineBufferIndex = LINE_MAX
        protocol.characterReceived(b"y", False)
        self.assertEqual(len(protocol.lineBuffer), LINE_MAX)
        self.assertEqual(protocol.lineBufferIndex, LINE_MAX)
        self.assertEqual(bytes(terminal.out[-2:]), b"xy")
        ttylog.ttylog_close(path, time.time())

    def test_insert_into_a_full_line_is_not_kept(self):
        ttylog, protocol, terminal, path, _ = self.session(self.patched)
        type_in(ttylog, protocol, path, b"c" * LINE_MAX, LINE_MAX)
        protocol.lineBufferIndex = 5
        protocol.characterReceived(b"z", False)
        self.assertEqual(b"".join(protocol.lineBuffer), b"c" * LINE_MAX)
        self.assertEqual(protocol.lineBufferIndex, 5)
        self.assertEqual(bytes(terminal.out[-1:]), b"z")
        ttylog.ttylog_close(path, time.time())

    def test_a_password_is_bounded_and_not_echoed(self):
        ttylog, protocol, terminal, path, _ = self.session(self.patched)
        protocol.password_input = True
        type_in(ttylog, protocol, path, b"p" * (LINE_MAX + 100), 1024)
        self.assertEqual(len(protocol.lineBuffer), LINE_MAX)
        self.assertEqual(bytes(terminal.out), b"")
        ttylog.ttylog_close(path, time.time())

    def test_a_full_screen_command_still_takes_the_keys_first(self):
        ttylog, protocol, terminal, path, _ = self.session(self.patched)
        taken = []

        class Top:
            def tpot_key(self, key):
                taken.append(key)
                return True

        protocol.cmdstack = [object(), Top()]
        type_in(ttylog, protocol, path, b"q", 1)
        self.assertEqual((taken, protocol.lineBuffer, bytes(terminal.out)), ([b"q"], [], b""))
        ttylog.ttylog_close(path, time.time())

    def test_below_the_bound_keys_act_as_upstream(self):
        """Random keys with moves of the cursor and both modes: the same echo, line and cursor as
        upstream (with the patches before this one)."""
        rng = random.Random(11)
        steps = []
        for _ in range(1200):
            choice = rng.random()
            if choice < 0.05:
                steps.append(("mode", rng.choice(("insert", "typeover"))))
            elif choice < 0.1:
                steps.append(("move", rng.random()))
            elif choice < 0.12:
                steps.append(("password", rng.random() < 0.2))
            else:
                steps.append(("key", bytes([rng.randrange(32, 127)])))
        results = []
        for root in (FIXTURE, self.patched):
            ttylog, protocol, terminal, path, _ = self.session(root, f"{root.name}-keys.log")
            for kind, value in steps:
                if kind == "mode":
                    protocol.mode = value
                elif kind == "move":
                    protocol.lineBufferIndex = int(value * len(protocol.lineBuffer))
                elif kind == "password":
                    protocol.password_input = value
                else:
                    protocol.characterReceived(value, False)
            ttylog.ttylog_close(path, time.time())
            results.append((bytes(terminal.out), list(protocol.lineBuffer), protocol.lineBufferIndex))
        self.assertEqual(results[0], results[1])

    # -- the history of the shell -----------------------------------------------------------------

    def history_protocol(self, root, values, uid=0):
        """HoneyPotInteractiveProtocol.handle_RETURN of root on a stand-in; recvline's RETURN takes the
        line (lineReceived) and empties it."""
        source = (root / PROTOCOL).read_text()
        namespace = {"CowrieConfig": FakeConfig(values)}
        if root != FIXTURE:
            module_parts(source, ["tpot_history_size", "tpot_history_control"], namespace)

        class RecvLine:
            @staticmethod
            def handle_RETURN(protocol):
                protocol.received.append(b"".join(protocol.lineBuffer))
                protocol.lineBuffer = []
                protocol.lineBufferIndex = 0

        namespace["recvline"] = types.SimpleNamespace(RecvLine=RecvLine)
        handle_return = method(source, "HoneyPotInteractiveProtocol", "handle_RETURN", namespace)

        class Protocol:
            def __init__(self):
                self.cmdstack = [object()]
                self.historyLines = []
                self.historyPosition = 0
                self.lineBuffer = []
                self.lineBufferIndex = 0
                self.received = []
                self.user = types.SimpleNamespace(uid=uid, username="root" if uid == 0 else "user")

            def enter(self, line):
                self.lineBuffer = [bytes([value]) for value in line]
                self.lineBufferIndex = len(line)
                handle_return(self)

        return Protocol()

    def test_the_history_keeps_the_last_histsize_lines(self):
        values = {("shell", "history_size"): "1000", ("shell", "history_size_root"): "500"}
        for uid, size in ((0, 500), (1000, 1000)):
            with self.subTest(uid=uid):
                protocol = self.history_protocol(self.patched, values, uid)
                lines = [f"echo {n}".encode() for n in range(1, 1201)]
                for line in lines:
                    protocol.enter(line)
                self.assertEqual(protocol.historyLines, lines[-size:])
                self.assertEqual(protocol.historyPosition, size)
                self.assertEqual(protocol.received, lines)

    def test_the_history_holds_at_most_1000_lines(self):
        """HISTSIZE=10000 of EdgeOS: the variable says 10000, the line editor holds the last 1000 lines and
        numbers on from those it dropped."""
        protocol = self.history_protocol(self.patched, {("shell", "history_size_root"): "10000"})
        lines = [f"echo {n}".encode() for n in range(1, 1501)]
        for line in lines:
            protocol.enter(line)
        self.assertEqual(protocol.historyLines, lines[-1000:])
        self.assertEqual(protocol.tpot_history_base, 500)
        self.assertEqual(len(protocol.tpot_history_times), 1000)

    def test_below_the_cap_the_history_is_upstream(self):
        rng = random.Random(5)
        lines = [b"" if rng.random() < 0.1 else f"ls {rng.randrange(50)}".encode() for _ in range(300)]
        states = []
        for root in (FIXTURE, self.patched):
            protocol = self.history_protocol(root, {("shell", "history_size"): "1000",
                                                    ("shell", "history_size_root"): "500"})
            for index, line in enumerate(lines):
                # a command that reads the terminal (two shells on the stack) keeps its lines out
                protocol.cmdstack = [object()] * (2 if index % 37 == 5 else 1)
                protocol.enter(line)
                states.append((root.name, index, list(protocol.historyLines), protocol.historyPosition))
        half = len(states) // 2
        self.assertEqual([s[1:] for s in states[:half]], [s[1:] for s in states[half:]])

    def test_without_a_size_the_history_is_upstream(self):
        for values in ({}, {("shell", "history_size"): "0", ("shell", "history_size_root"): "0"},
                       {("shell", "history_size"): "x", ("shell", "history_size_root"): "-3"}):
            with self.subTest(values=values):
                protocol = self.history_protocol(self.patched, values)
                for n in range(700):
                    protocol.enter(b"a%d" % n)
                self.assertEqual(len(protocol.historyLines), 700)

    def test_history_sizes_of_the_personas(self):
        """HISTSIZE of the login shell, (root, other users): Ubuntu's /root/.bashrc and /etc/skel/.bashrc
        set 1000; Debian's /root/.bashrc sets none (bash's 500), its /etc/skel/.bashrc 1000; EdgeOS 10000
        (vyatta-cfg of the ER-e50 image); RHEL and CentOS set 1000 in /etc/profile; BusyBox keeps FEATURE_EDITING_HISTORY lines (15 up to 1.16,
        255 from 1.17, OpenWrt's build 256)."""
        expected = {"ubuntu-noble": (1000, 1000), "ubuntu-jammy": (1000, 1000), "gpu-ubuntu": (1000, 1000),
                    "debian-trixie": (500, 1000), "debian-bookworm": (500, 1000), "raspios": (500, 1000),
                    "ubiquiti-edgerouter-x": (10000, 10000), "rhel-9": (1000, 1000), "centos-7": (1000, 1000),
                    "synology-dsm": (500, 500), "dlink-dir859": (15, 15), "tplink-archer-c7": (255, 255),
                    "zyxel-nas326": (255, 255), "qnap-qts": (255, 255), "ipcam": (255, 255),
                    "openwrt": (256, 256)}
        self.assertEqual(set(expected), set(catalog.ids()))
        for pid, sizes in expected.items():
            persona = catalog.by_id(pid)
            for release in persona.releases:
                with self.subTest(persona=pid, release=release.label):
                    self.assertEqual(system.history_sizes(persona, release), sizes)

    def test_the_other_keys_of_the_line_editor_stay_upstream(self):
        upstream = class_methods((FIXTURE / PROTOCOL).read_text(), "HoneyPotInteractiveProtocol")
        patched = class_methods((self.patched / PROTOCOL).read_text(), "HoneyPotInteractiveProtocol")
        # tpot_one_write and _deliverBuffer: the line of the history in one write
        self.assertEqual(set(upstream) | {"tpot_one_write", "_deliverBuffer", "handle_UP", "handle_DOWN"}, set(patched))
        # characterReceived: the bound (and top's keys); timeoutConnection: top gives the cursor back;
        # handle_RETURN: the history keeps HISTSIZE lines; handle_CTRL_U: one write
        for name in sorted(set(upstream) - {"characterReceived", "timeoutConnection", "handle_RETURN",
                                            "handle_CTRL_U"}):
            with self.subTest(name=name):
                self.assertEqual(patched[name], upstream[name])


    # -- HISTCONTROL and the numbers of `history` -----------------------------------------------

    def test_ignoredups_and_ignorespace_as_bash(self):
        # bash 5.2 / 4.2 with HISTCONTROL=ignoreboth: "echo a", "echo a", " echo b", "history" keep
        #   "echo a" and "history"; ignoredups (RHEL's /etc/profile) keeps " echo b"
        lines = [b"echo a", b"echo a", b" echo b", b"history"]
        for control, kept in (("ignoreboth", [b"echo a", b"history"]),
                              ("ignoredups:ignorespace", [b"echo a", b"history"]),
                              ("ignoredups", [b"echo a", b" echo b", b"history"]),
                              ("ignorespace", [b"echo a", b"echo a", b"history"]),
                              ("", lines), ("erasedups", lines)):
            with self.subTest(control=control):
                protocol = self.history_protocol(self.patched, {("shell", "history_control_root"): control})
                for line in lines:
                    protocol.enter(line)
                self.assertEqual(protocol.historyLines, kept)
                self.assertEqual(protocol.historyPosition, len(kept))
                # every line still runs
                self.assertEqual(protocol.received, lines)

    def test_history_control_of_root_and_users(self):
        values = {("shell", "history_control_root"): "", ("shell", "history_control"): "ignoreboth"}
        for uid, kept in ((0, 3), (1000, 1)):
            with self.subTest(uid=uid):
                protocol = self.history_protocol(self.patched, values, uid)
                for line in (b"ls", b"ls", b" ls"):
                    protocol.enter(line)
                self.assertEqual(len(protocol.historyLines), kept)

    def test_the_numbers_go_on_past_the_cap(self):
        protocol = self.history_protocol(self.patched, {("shell", "history_size_root"): "3"})
        for line in (b"echo 1", b"echo 2", b"echo 3", b"echo 4", b"echo 5", b"history"):
            protocol.enter(line)
        self.assertEqual(protocol.historyLines, [b"echo 4", b"echo 5", b"history"])
        self.assertEqual(protocol.tpot_history_base, 3)

    def history_output(self, protocol, args=(), style="bash"):
        source = (self.patched / "src/cowrie/commands/base.py").read_text()
        config = FakeConfig({("shell", "not_found_style"): style})
        call = method(source, "Command_history", "call", {"CowrieConfig": config})
        out = []

        class Command:
            pass

        command = Command()
        command.args, command.protocol, command.write = list(args), protocol, out.append
        call(command)
        return "".join(out)

    def test_history_numbers_as_bash(self):
        # bash 5.2 (debian:12) with HISTSIZE=3 after five lines: "    4  echo 4" ... "    6  history"
        protocol = self.history_protocol(self.patched, {("shell", "history_size_root"): "3"})
        for line in (b"echo 1", b"echo 2", b"echo 3", b"echo 4", b"echo 5", b"history"):
            protocol.enter(line)
        self.assertEqual(self.history_output(protocol), "    4  echo 4\n    5  echo 5\n    6  history\n")
        for line in (b"echo 6", b"history"):
            protocol.enter(line)
        self.assertEqual(self.history_output(protocol), "    6  history\n    7  echo 6\n    8  history\n")
        # history -c: bash numbers from 1 again ("    1  echo 3", "    2  history")
        self.history_output(protocol, ["-c"])
        for line in (b"echo 3", b"history"):
            protocol.enter(line)
        self.assertEqual(self.history_output(protocol), "    1  echo 3\n    2  history\n")

    def test_history_takes_a_number_of_bash(self):
        """history N: a count bash takes (intmax_t), else "history: N: numeric argument required" and status 1 (refs
        final-wave debian-12-ffw-history-*: bash -c writes "bash: line 1: ", the login shell "-bash: ")."""
        import json
        refs = [e for e in json.loads((Path(__file__).parent / "fixtures" / "final_wave.json").read_text())["entries"]
                if e["id"].startswith("debian-12-ffw-history-")]
        self.assertEqual(len(refs), 7)
        protocol = self.history_protocol(self.patched, {})
        for line in (b"echo 1", b"history"):
            protocol.enter(line)
        source = (self.patched / "src/cowrie/commands/base.py").read_text()
        call = method(source, "Command_history", "call", {"CowrieConfig": FakeConfig({})})
        for ref in refs:
            word = ref["cmd"].split(" ", 1)[1]
            with self.subTest(word=word[:24]):
                out, err = [], []

                class Command:
                    exit_code = 0

                command = Command()
                command.args, command.protocol, command.write, command.errorWrite = [word], protocol, out.append, \
                    err.append
                command.shell = type("Shell", (), {"error_prefix": staticmethod(lambda: "-bash: ")})()
                call(command)
                self.assertEqual(("".join(err), command.exit_code),
                                 (ref["stderr"].replace("bash: line 1: ", "-bash: "), ref["rc"]))
                self.assertEqual("".join(out), "" if ref["rc"] else "    1  echo 1\n    2  history\n"[
                    : None if int(word) > 1 else 0] or "    2  history\n" * (int(word) == 1))

    def test_history_with_histtimeformat(self):
        """HISTTIMEFORMAT='%FT%T%z ' of EdgeOS (vyatta-cfg, in the shell's environment): bash writes the time each line
        was typed before it ("    1  2026-10-20T01:26:35+0000 echo a", bash 4.4); a line without its time "??"
        (histtime of bash 4.4); history N keeps the times with their lines; without the variable no times."""
        protocol = self.history_protocol(self.patched, {("shell", "history_size_root"): "3"})
        for line in (b"echo 1", b"echo 2", b"echo 3", b"history"):
            protocol.enter(line)
        protocol.tpot_history_times = [1792462282.0, 1792462283.0, 1792462342.0]
        source = (self.patched / "src/cowrie/commands/base.py").read_text()
        call = method(source, "Command_history", "call",
                      {"CowrieConfig": FakeConfig({("shell", "not_found_style"): "bash"})})

        def history(args=(), environ=None):
            out = []
            call(types.SimpleNamespace(args=list(args), protocol=protocol, write=out.append,
                                       environ={"HISTTIMEFORMAT": "%FT%T%z "} if environ is None else environ))
            return "".join(out)

        self.assertEqual(history(), "    2  2026-10-20T02:11:22+0000 echo 2\n"
                                    "    3  2026-10-20T02:11:23+0000 echo 3\n"
                                    "    4  2026-10-20T02:12:22+0000 history\n")
        self.assertEqual(history(["1"]), "    4  2026-10-20T02:12:22+0000 history\n")
        protocol.tpot_history_times = [1792462342.0]
        self.assertEqual(history(), "    2  ??echo 2\n    3  ??echo 3\n    4  2026-10-20T02:12:22+0000 history\n")
        # after `unset HISTTIMEFORMAT` (and without it) as before
        self.assertEqual(history(environ={}), "    2  echo 2\n    3  echo 3\n    4  history\n")
        self.assertEqual(self.history_output(protocol), "    2  echo 2\n    3  echo 3\n    4  history\n")

    def test_history_n_prints_the_last_n_lines(self):
        # bash 5.2: `history 2` gives the last two lines with their numbers (the history 2 line itself
        #   among them); BusyBox ash's history takes no count and lists every line
        protocol = self.history_protocol(self.patched, {("shell", "history_size_root"): "3"})
        for line in (b"echo 1", b"echo 2", b"echo 3", b"echo 4", b"history 2"):
            protocol.enter(line)
        self.assertEqual(self.history_output(protocol, ["2"]), "    4  echo 4\n    5  history 2\n")
        self.assertEqual(self.history_output(protocol, ["9"]), "    3  echo 3\n    4  echo 4\n    5  history 2\n")
        self.assertEqual(self.history_output(protocol, ["0"]), "")
        self.assertEqual(self.history_output(protocol, ["2"], style="ash"),
                         "   0 echo 3\n   1 echo 4\n   2 history 2\n")

    def test_history_of_busybox_ash(self):
        # BusyBox 1.27.2 / 1.31.1 / 1.36.1 ash: "%4d %s" from 0, a repeated line is not kept, a line
        #   with a leading space is
        protocol = self.history_protocol(self.patched, {("shell", "history_control_root"): "ignoredups"})
        for line in (b"echo 1", b"echo 1", b" echo 2", b"echo 3", b"history"):
            protocol.enter(line)
        self.assertEqual(self.history_output(protocol, style="ash"),
                         "   0 echo 1\n   1  echo 2\n   2 echo 3\n   3 history\n")

    def test_history_without_tpot_keys_is_upstream(self):
        protocol = self.history_protocol(self.patched, {})
        for line in (b"a", b"a", b" b"):
            protocol.enter(line)
        source = (FIXTURE / "src/cowrie/commands/base.py").read_text()
        out = []
        command = types.SimpleNamespace(args=[], protocol=protocol, write=out.append)
        method(source, "Command_history", "call")(command)
        self.assertEqual(self.history_output(protocol), "".join(out))

    def test_history_control_of_the_personas(self):
        """(root, other users): Ubuntu's /root/.bashrc ignoredups:ignorespace and /etc/skel/.bashrc
        ignoreboth; Debian's /root/.bashrc none, its /etc/skel/.bashrc ignoreboth; EdgeOS none; RHEL / CentOS
        ignoredups of /etc/profile (a login shell); BusyBox ash keeps no repeated line."""
        ubuntu = ("ignoredups:ignorespace", "ignoreboth")
        expected = {"ubuntu-noble": ubuntu, "ubuntu-jammy": ubuntu, "gpu-ubuntu": ubuntu, "debian-trixie": ("", "ignoreboth"),
                    "debian-bookworm": ("", "ignoreboth"), "raspios": ("", "ignoreboth"),
                    "ubiquiti-edgerouter-x": ("", ""), "rhel-9": ("ignoredups", "ignoredups"),
                    "centos-7": ("ignoredups", "ignoredups"), "synology-dsm": ("", ""),
                    **{pid: ("ignoredups", "ignoredups") for pid in ("dlink-dir859", "tplink-archer-c7",
                                                                     "zyxel-nas326", "qnap-qts", "ipcam", "openwrt")}}
        self.assertEqual(set(expected), set(catalog.ids()))
        for pid, controls in expected.items():
            with self.subTest(persona=pid):
                self.assertEqual(system.history_controls(catalog.by_id(pid)), controls)

    def test_history_file_sizes_and_exports_of_the_personas(self):
        """HISTFILESIZE (root, other users): Ubuntu's /root/.bashrc and /etc/skel/.bashrc 2000 (ubuntu:24.04),
        Debian's /etc/skel/.bashrc 2000 and its /root/.bashrc none, so bash's HISTSIZE (debian:12); Red Hat
        none, so bash's HISTSIZE of /etc/profile; /etc/profile of Red Hat exports HISTSIZE and HISTCONTROL
        (centos:7), the .bashrc of Debian and Ubuntu exports nothing."""
        expected = {"ubuntu-noble": (2000, 2000), "debian-bookworm": (500, 2000), "raspios": (500, 2000),
                    "ubiquiti-edgerouter-x": (10000, 10000), "centos-7": (1000, 1000), "rhel-9": (1000, 1000),
                    "synology-dsm": (500, 500)}
        for pid, sizes in expected.items():
            persona = catalog.by_id(pid)
            with self.subTest(persona=pid):
                self.assertEqual(system.history_file_sizes(persona, persona.releases[0]), sizes)
                exports = {"centos-7": ("HISTSIZE", "HISTCONTROL"), "rhel-9": ("HISTSIZE", "HISTCONTROL"),
                           "ubiquiti-edgerouter-x": ("HISTSIZE", "HISTFILESIZE", "HISTCONTROL", "HISTTIMEFORMAT")}
                self.assertEqual(system.history_exports(persona), exports.get(pid, ()))

    # -- one write for a line of the history and for ctrl-U -----------------------------------------

    def editor(self, root, writes):
        source = (root / PROTOCOL).read_text()
        names = {"recvline": types.SimpleNamespace(HistoricRecvLine=types.SimpleNamespace(
            _deliverBuffer=lambda protocol, buf: [protocol.characterReceived(buf[i:i + 1], i < len(buf) - 1)
                                                  for i in range(len(buf))]))}
        cls = protocol_class(source)
        for name in ("tpot_one_write", "_deliverBuffer", "handle_CTRL_U"):
            try:
                setattr(cls, name, method(source, "HoneyPotInteractiveProtocol", name, names))
            except AssertionError:
                pass

        class Terminal:
            def write(self, data):
                writes.append(data)

            def cursorBackward(self, n=1):
                self.write(b"\x1b[%dD" % n)

            def deleteCharacter(self, n=1):
                self.write(b"\x1b[%dP" % n)

        return cls(Terminal())

    def test_a_line_of_the_history_is_one_write(self):
        for root, count in ((FIXTURE, 12), (self.patched, 1)):
            writes = []
            protocol = self.editor(root, writes)
            if root == FIXTURE:
                protocol._deliverBuffer = lambda buf: [protocol.characterReceived(buf[i:i + 1], True)
                                                       for i in range(len(buf))]
            protocol._deliverBuffer(b"echo history")
            self.assertEqual(len(writes), count, root)
            self.assertEqual(b"".join(writes), b"echo history")
            self.assertEqual(b"".join(protocol.lineBuffer), b"echo history")

    def test_up_and_down_are_one_write_with_upstreams_bytes(self):
        # recvline.HistoricRecvLine.handle_UP / handle_DOWN of Twisted 26.4: HOME, erase to the end of the
        #   line, the line of the history (each a write of its own upstream)
        def up(protocol):
            protocol.terminal.cursorBackward(protocol.lineBufferIndex)
            protocol.terminal.write(b"\x1b[K")
            protocol.historyPosition -= 1
            protocol.lineBuffer, protocol.lineBufferIndex = [], 0
            for i, ch in enumerate(protocol.historyLines[protocol.historyPosition]):
                protocol.characterReceived(bytes([ch]), True)

        source = (self.patched / PROTOCOL).read_text()
        names = {"recvline": types.SimpleNamespace(HistoricRecvLine=types.SimpleNamespace(
            handle_UP=up, handle_DOWN=up, _deliverBuffer=lambda protocol, buf: None))}
        results = []
        for wrapped in (False, True):
            writes = []
            protocol = self.editor(self.patched, writes)
            protocol.historyLines, protocol.historyPosition = [b"ls -la", b"uname -a"], 2
            for value in b"pwd":
                protocol.characterReceived(bytes([value]), False)
            del writes[:]
            if wrapped:
                for name in ("handle_UP", "handle_DOWN", "tpot_one_write"):
                    setattr(type(protocol), name, method(source, "HoneyPotInteractiveProtocol", name, names))
                protocol.handle_UP()
            else:
                up(protocol)
            results.append((b"".join(writes), len(writes), b"".join(protocol.lineBuffer)))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(results[0][2], results[1][2])
        self.assertEqual(results[1][1], 1)
        self.assertGreater(results[0][1], 1)

    def test_ctrl_u_is_one_write_with_upstreams_bytes(self):
        results = []
        for root in (FIXTURE, self.patched):
            writes = []
            protocol = self.editor(root, writes)
            for value in b"ls -la /tmp":
                protocol.characterReceived(bytes([value]), False)
            protocol.lineBufferIndex = 6
            del writes[:]
            protocol.handle_CTRL_U()
            results.append((b"".join(writes), len(writes), b"".join(protocol.lineBuffer), protocol.lineBufferIndex))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(results[0][2:], results[1][2:])
        self.assertEqual((results[0][1], results[1][1]), (12, 1))


    def test_fails_loudly_on_unknown_upstream(self):
        cases = (("src/cowrie/shell/protocol.py",
                  "        for _ in range(self.lineBufferIndex):\n            self.terminal.cursorBackward()\n",
                  "        for _i in range(self.lineBufferIndex):\n            self.terminal.cursorBackward()\n"),
                 ("src/cowrie/commands/base.py", "            count = 1\n            for line in self.protocol.historyLines:\n",
                  "            count = 0\n            for line in self.protocol.historyLines:\n"),
                 ("src/cowrie/commands/base.py", "                self.protocol.historyPosition = 0\n                return\n",
                  "                self.protocol.historyPosition = 0\n                return None\n"))
        for rel, old, new in cases:
            with self.subTest(rel=rel, needle=old):
                root = self.dir / f"fail-{len(old)}" / "cowrie"
                shutil.copytree(FIXTURE, root)
                path = root / rel
                text = path.read_text()
                self.assertEqual(text.count(old), 1)
                path.write_text(text.replace(old, new))
                done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                      capture_output=True, text=True)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("Could not find expected text", done.stderr)


if __name__ == "__main__":
    unittest.main()
