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

from tests.test_patch import FIXTURE, PATCH, method

TTYLOG = "src/cowrie/core/ttylog.py"
PROTOCOL = "src/cowrie/shell/protocol.py"
LINE_MAX = 65536
# the open and close of a file on a Docker Desktop bind mount (log/tty of the smoke test), measured
SLOW_OPEN_SECONDS = 0.002


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

    def opener(*args, **kwargs):
        calls.append(args[0])
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

    def test_input_hash_reads_many_records_in_linear_time(self):
        # 100,000 keys of 64 bytes each and their echo
        path = str(self.dir / "many.log")
        key = bytes(range(64))
        head_in = struct.pack("<iLiiLL", 3, 0, len(key), 1, 0, 0)
        head_out = struct.pack("<iLiiLL", 3, 0, len(key), 2, 0, 0)
        with open(path, "wb") as handle:
            handle.write(struct.pack("<iLiiLL", 1, 0, 0, 0, 0, 0))
            handle.write((head_in + key + head_out + key) * 100000)
            handle.write(struct.pack("<iLiiLL", 2, 0, 0, 0, 0, 0))
        ttylog = load_ttylog(self.patched)
        started = time.perf_counter()
        digest = ttylog.ttylog_inputhash(path)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(digest, hashlib.sha256(key * 100000).hexdigest())

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

    def test_the_other_keys_of_the_line_editor_stay_upstream(self):
        upstream = class_methods((FIXTURE / PROTOCOL).read_text(), "HoneyPotInteractiveProtocol")
        patched = class_methods((self.patched / PROTOCOL).read_text(), "HoneyPotInteractiveProtocol")
        self.assertEqual(set(upstream), set(patched))
        # characterReceived: the bound (and top's keys); timeoutConnection: top gives the cursor back
        for name in sorted(set(upstream) - {"characterReceived", "timeoutConnection"}):
            with self.subTest(name=name):
                self.assertEqual(patched[name], upstream[name])


if __name__ == "__main__":
    unittest.main()
