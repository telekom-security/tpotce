"""One time budget per session for the commands T-Pot emulates (TPOT_BUDGET in the patched shell/command.py):
the commands of a line run back to back on the reactor, so a session that chains many of them gets the time of
its window and no more; every command ends as at its own bound, and the budget comes back after the window."""
import time
import unittest

from tests import test_awk, test_packages, test_patch
from tests.test_patch import module_all

LIMIT = 1.5  # seconds a whole line of the tests may take (the window of a session and its calls)


def budget_module(clock=None):
    """The patched shell/command.py; with clock, its time is that of the clock."""
    source = (test_awk.patched_root() / "src/cowrie/shell/command.py").read_text()
    namespace = module_all(source, {"Logger": lambda: None, "error": None, "failure": None})
    if clock is not None:
        namespace["time"] = clock
    return namespace


class Clock:
    """time.monotonic and time.process_time that move only when told."""

    def __init__(self):
        self.now, self.cpu = 1000.0, 50.0

    def monotonic(self):
        return self.now

    def process_time(self):
        return self.cpu

    def run(self, seconds, cpu=None):
        self.now += seconds
        self.cpu += seconds if cpu is None else cpu


class Session:
    """A protocol: what the budget of a session lives on."""


class BudgetTest(unittest.TestCase):
    def test_the_calls_of_a_session_share_its_window(self):
        clock = Clock()
        ns = budget_module(clock)
        session = Session()
        self.assertEqual(ns["tpot_deadline"](session), clock.now + ns["TPOT_BUDGET_CPU"])
        with ns["tpot_metered"](session):
            clock.run(0.25)
        # another call of the same session gets what is left (its start counts too)
        left = ns["tpot_deadline"](session) - clock.now
        self.assertAlmostEqual(left, ns["TPOT_BUDGET_CPU"] - 0.25 - ns["TPOT_BUDGET_CALL"])
        with ns["tpot_metered"](session):
            clock.run(0.5)
            # while it runs, its own time is taken already
            self.assertEqual(ns["tpot_deadline"](session), clock.now)
        self.assertEqual(ns["tpot_deadline"](Session()), clock.now + ns["TPOT_BUDGET_CPU"])
        # wall-clock time counts where the process waits (another process has the CPU)
        session = Session()
        with ns["tpot_metered"](session):
            clock.run(0.45, cpu=0.1)
        self.assertAlmostEqual(ns["tpot_deadline"](session) - clock.now,
                               ns["TPOT_BUDGET_WALL"] - 0.45 - ns["TPOT_BUDGET_CALL"])

    def test_the_start_of_every_call_counts(self):
        clock = Clock()
        ns = budget_module(clock)
        session = Session()
        for _ in range(int(ns["TPOT_BUDGET_CPU"] / ns["TPOT_BUDGET_CALL"]) + 1):
            with ns["tpot_metered"](session):
                pass
        self.assertEqual(ns["tpot_deadline"](session), clock.now)

    def test_a_call_inside_a_call_counts_with_it(self):
        clock = Clock()
        ns = budget_module(clock)
        session = Session()
        with ns["tpot_metered"](session):
            clock.run(0.1)
            with ns["tpot_metered"](session):
                clock.run(0.2)
            clock.run(0.1)
        self.assertAlmostEqual(ns["tpot_budget"](session).used(clock.now)[0], 0.4 + 2 * ns["TPOT_BUDGET_CALL"])

    def test_the_budget_comes_back_after_the_window(self):
        clock = Clock()
        ns = budget_module(clock)
        session = Session()
        with ns["tpot_metered"](session):
            clock.run(3.0)
        self.assertEqual(ns["tpot_deadline"](session), clock.now)
        clock.run(ns["TPOT_BUDGET_WINDOW"] / 2, cpu=0)
        self.assertEqual(ns["tpot_deadline"](session), clock.now)
        clock.run(ns["TPOT_BUDGET_WINDOW"] / 2 + 0.01, cpu=0)
        self.assertEqual(ns["tpot_deadline"](session), clock.now + ns["TPOT_BUDGET_CPU"])

    def test_a_command_class_counts_once(self):
        clock = Clock()
        ns = budget_module(clock)

        class Base:
            protocol = None
            calls = 0

            def start(self):
                Base.calls += 1
                clock.run(0.01)

        class Child(Base):
            pass

        ns["tpot_meter_class"](Base)
        ns["tpot_meter_class"](Child)
        self.assertIs(Child.start, Base.start)
        cmd = Child()
        cmd.protocol = Session()
        cmd.start()
        self.assertEqual(Base.calls, 1)
        self.assertEqual(cmd.protocol.tpot_budget.depth, 0)
        self.assertAlmostEqual(sum(slot[1] for slot in cmd.protocol.tpot_budget.slots),
                               0.01 + ns["TPOT_BUDGET_CALL"], delta=1e-9)

    def test_the_channels_of_one_connection_share_its_budget(self):
        clock = Clock()
        ns = budget_module(clock)

        class User:
            """The avatar of a connection: every channel's protocol has it as user."""

        user = User()
        first, second = Session(), Session()
        first.user = second.user = user
        with ns["tpot_metered"](first):
            clock.run(0.3)
        self.assertAlmostEqual(ns["tpot_deadline"](second) - clock.now,
                               ns["TPOT_BUDGET_CPU"] - 0.3 - ns["TPOT_BUDGET_CALL"])
        # another connection has its own
        other = Session()
        other.user = User()
        self.assertEqual(ns["tpot_deadline"](other), clock.now + ns["TPOT_BUDGET_CPU"])
        # a user that takes no attribute (a dict): the budget is the protocol's
        third = Session()
        third.user = {"uid": 0}
        with ns["tpot_metered"](third):
            clock.run(0.2)
        self.assertIs(ns["tpot_budget"](third), third.tpot_budget)

    def test_a_scan_counts_where_it_runs(self):
        """A scan that runs outside a metered call (after a command's sleep, in a callback of the reactor)
        charges what it uses; inside one it counts with it."""
        clock = Clock()
        ns = budget_module(clock)
        session = Session()

        def slow(items):
            for item in items:
                clock.run(0.05)
                yield item

        self.assertEqual(list(ns["tpot_scanned"](session, slow(range(3)))), [0, 1, 2])
        self.assertAlmostEqual(ns["tpot_budget"](session).used(clock.now)[0], 0.15 + ns["TPOT_BUDGET_CALL"])
        with ns["tpot_metered"](session):
            list(ns["tpot_scanned"](session, slow(range(2))))
        self.assertAlmostEqual(ns["tpot_budget"](session).used(clock.now)[0], 0.25 + 3 * ns["TPOT_BUDGET_CALL"])
        self.assertEqual(ns["tpot_budget"](session).depth, 0)
        # a scan left early (break) closes its call too
        for _ in ns["tpot_scanned"](session, slow(range(5))):
            break
        self.assertEqual(ns["tpot_budget"](session).depth, 0)

    def test_a_scan_past_the_session_time_finds_nothing(self):
        clock = Clock()
        ns = budget_module(clock)
        session = Session()
        self.assertEqual(list(ns["tpot_scanned"](session, range(5))), [0, 1, 2, 3, 4])
        with ns["tpot_metered"](session):
            clock.run(1.0)
        self.assertEqual(list(ns["tpot_scanned"](session, range(5))), [])


class ImportTest(unittest.TestCase):
    def test_every_patched_module_imports_the_budget_names_it_uses(self):
        """A command module that uses a name of TPOT_BUDGET imports it from cowrie.shell.command (the tests
        give a module only what it imports)."""
        import ast
        budget = set(test_patch.budget_names())
        root = test_awk.patched_root() / "src/cowrie"
        checked = 0
        for path in sorted(root.rglob("*.py")):
            if path == root / "shell/command.py":
                continue
            try:
                module = ast.parse(path.read_text())
            except SyntaxError:
                continue  # an excerpt of the fixture (method bodies only)
            used = {node.id for node in ast.walk(module) if isinstance(node, ast.Name)} & budget
            imported = {alias.asname or alias.name for node in ast.walk(module)
                        if isinstance(node, ast.ImportFrom) and node.module == "cowrie.shell.command"
                        for alias in node.names}
            self.assertEqual(used - imported, set(), path.relative_to(root))
            checked += bool(used)
        self.assertGreaterEqual(checked, 10)

    def test_a_module_gets_only_the_names_it_imports(self):
        namespace = test_patch.module_all("from cowrie.shell.command import tpot_deadline\n"
                                          "def f():\n    return tpot_metered\n", {})
        self.assertIn("tpot_deadline", namespace)
        with self.assertRaises(NameError):
            namespace["f"]()


class DeferredScanTest(test_packages.SessionCase):
    def test_a_dnf_scan_after_its_sleep_counts_for_the_session(self):
        """dnf scans after its first sleep, in a callback of the reactor after start() returned."""
        run = self.upstream_session("rhel-9", uid=0, deferred=True)
        budget = budget_module()
        run("dnf install -y nm*")
        # the budget of the connection (on its avatar)
        session = test_patch.budget_names()["tpot_budget"](run.protocol)
        self.assertIs(session, run.protocol.user.tpot_budget)
        self.assertEqual(session.depth, 0)
        before = session.used(time.monotonic())[0]
        self.assertNotIn("Complete!", bytes(run.protocol.pp.out).decode())
        run.settle()
        out = bytes(run.protocol.pp.out).decode()
        self.assertIn("Complete!", out)
        self.assertIn("nmap", out)
        # the scan charged its call (and its time) though no start() was running
        self.assertGreaterEqual(session.used(time.monotonic())[0] - before, budget["TPOT_BUDGET_CALL"])
        self.assertEqual(session.depth, 0)
        # past the session's time the same scan finds nothing
        session.charge(time.monotonic(), 1.0, 1.0)
        run("dnf install -y nm*")
        run.settle()
        self.assertIn("No match for argument: nm*\n", bytes(run.protocol.pp.out).decode())


class GrepSessionTest(unittest.TestCase):
    def grep(self, *args, budget_used=None, **kwargs):
        fs = (test_awk.patched_root() / "src/cowrie/commands/fs.py").read_text()
        command_grep = test_patch.module_parts(fs, ["Command_grep", "TpotGrepMatcher"],
                                               {"HoneyPotCommand": test_patch.FakeCommand})["Command_grep"]
        command = command_grep(list(args), **kwargs)
        if budget_used is not None:
            budget = test_patch.budget_names()["tpot_budget"](command.protocol)
            budget.charge(time.monotonic(), budget_used, budget_used)
        command.start()
        return command

    def test_typed_lines_take_the_session_time_of_their_own(self):
        """grep reading the terminal: a line typed after a pause is matched; it takes the time the session has
        then, not that of the first line."""
        names = test_patch.budget_names()
        done = self.grep("x")
        done.lineReceived("x1")
        time.sleep(names["TPOT_BUDGET_WALL"] + 0.05)
        done.lineReceived("x2")
        done.eofReceived()
        self.assertEqual((bytes(done.out), done.err, done.code), (b"x1\nx2\n", "", 0))

    def test_grep_past_the_session_time_stops_as_at_its_work_limit(self):
        exhausted = "grep: memory exhausted\n"
        done = self.grep("-c", "a", input_data=b"a\nb\n", budget_used=1.0)
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", exhausted, 2))
        done = self.grep("a", "/proc/cpuinfo", budget_used=1.0)
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", exhausted, 2))
        # typed lines: the message once, and grep ends at once, as GNU grep
        done = self.grep("a", budget_used=1.0)
        self.assertEqual(done.code, "running")
        done.lineReceived("a")
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", exhausted, 2))
        # with the session's time it matches
        done = self.grep("-c", "a", input_data=b"a\nb\n")
        self.assertEqual((bytes(done.out), done.err, done.code), (b"1\n", "", 0))


class ParseTest(unittest.TestCase):
    def test_the_parse_of_a_line_is_not_charged(self):
        """A script (sh x, ./x) is one line for Cowrie's parser: its parse counts for no command's time, so the
        commands of the script answer as in any other session."""
        source = (test_awk.patched_root() / "src/cowrie/shell/bashparse.py").read_text()
        self.assertNotIn("tpot_metered", source)
        self.assertNotIn("TPOT_BUDGET", source)


class SessionBase(test_awk.Base):
    """test_awk's command base with the protocol of a session."""

    protocol = None


def awk_of(session, args, data=None):
    """(stdout, stderr, status) of the patched awk (mawk) in a session."""
    if not hasattr(awk_of, "namespace"):
        root = test_awk.patched_root()
        fs = module_all((root / "src/cowrie/commands/fs.py").read_text(),
                        {"HoneyPotCommand": SessionBase, "fs": object(), "CowrieConfig": test_awk.FakeConfig({})})
        awk_of.namespace = module_all((root / "src/cowrie/commands/awk.py").read_text(), {
            "HoneyPotCommand": SessionBase, "FileNotFound": FileNotFoundError, "TpotGrepMatcher": fs["TpotGrepMatcher"],
            "CowrieConfig": test_awk.FakeConfig({("shell", "awk_style"): "mawk",
                                                 ("shell", "awk_version"): "1.3.4 20200120"}),
            "log": test_awk.FakeLog()})
    cmd = awk_of.namespace["Command_tpot_awk"](args, None if data is None else data.encode())
    cmd.protocol = session
    cmd.start()
    return cmd.out, cmd.err, cmd.exit_code


class AwkSessionTest(unittest.TestCase):
    def test_a_line_of_3000_endless_awk_loops_ends_in_bounded_time(self):
        awk_of(Session(), ["BEGIN{}"])  # the patched module, loaded before the clock starts
        session = Session()
        start, codes = time.perf_counter(), []
        for _ in range(3000):
            codes.append(awk_of(session, ["BEGIN{while(1){}}"])[2])
            if time.perf_counter() - start > LIMIT:
                break
        self.assertEqual((len(codes), set(codes)), (3000, {2}))
        self.assertLess(time.perf_counter() - start, LIMIT)
        # past the session's time awk ends at its bound: status 2, nothing printed
        self.assertEqual(awk_of(session, ["BEGIN{print 1}"]), ("", "", 2))
        # another session is not affected
        self.assertEqual(awk_of(Session(), ["BEGIN{print 1}"]), ("1\n", "", 0))

    def test_the_session_has_its_time_again_after_the_window(self):
        session = Session()
        for _ in range(10):
            awk_of(session, ["BEGIN{while(1){}}"])
        self.assertEqual(awk_of(session, ["BEGIN{print 1}"])[2], 2)
        for slot in session.tpot_budget.slots:
            slot[0] -= len(session.tpot_budget.slots)
        self.assertEqual(awk_of(session, ["BEGIN{print 1}"]), ("1\n", "", 0))


class MixedLineTest(test_packages.SessionCase):
    def test_a_line_of_installs_awk_loops_sort_and_seq_ends_in_bounded_time(self):
        """20 times: an apt-get install of 16 hard regular expressions, an endless awk loop, a seq of about 990
        million numbers and a sort of about 1.3 MB of numbers, in one session."""
        hard = [f"lib[^x]*[^x]*[^x]*n{chr(97 + n)}" for n in range(8)] + [f".*.*.*[0-9]{chr(97 + n)}$" for n in range(8)]
        run = self.session("debian-bookworm", extra_modules=("fs", "awk", "wc"))
        numbers = "".join(f"{n}\n" for n in range(1, 200000)).encode()
        start, done = time.perf_counter(), 0
        for _ in range(20):
            self.assertEqual(run(["apt-get", "install", "-y", *hard])[2], 100)
            self.assertEqual(run(["awk", "BEGIN{while(1){}}"])[2], 2)
            run(["seq", "1", "0.0000001", "100"])
            run(["sort", "-r"], input_data=numbers)
            done += 1
            if time.perf_counter() - start > LIMIT:
                break
        self.assertEqual(done, 20)
        self.assertLess(time.perf_counter() - start, LIMIT)
        # past the session's time: the scans find nothing, awk, sort and seq stop at their bounds
        self.assertEqual(run(["apt-get", "install", "-y", "^tre.$"])[1].splitlines()[0],
                         "E: Unable to locate package ^tre.$")
        self.assertEqual(run(["sort"], input_data=b"b\na\n")[1:], ("sort: memory exhausted\n", 2))
        self.assertEqual(run(["seq", "3"])[0], "1")


if __name__ == "__main__":
    unittest.main()
