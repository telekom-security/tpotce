"""dist/cpu_check.py: the healthcheck measures the main thread of dionaea only.

The main thread runs the event loop; a loop at 100 % of a core is what the check
looks for. The emu runs in up to four worker threads: a container that is busy and
works reads about 100 % for the process as a whole at cpus 1, so the check reads
/proc/<pid>/task/<pid>/stat, not the process.

Run: python3 -m unittest discover -s docker/dionaea/tests
"""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"


def load():
    spec = importlib.util.spec_from_file_location("cpu_check", DIST / "cpu_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cpu_check = load()


def stat_line(pid, utime, stime, comm="dionaea"):
    # fields 1-13, then utime (14) and stime (15), then the rest
    return ("%d (%s) S 1 %d %d 0 -1 4194560 100 0 0 0 %d %d 0 0 20 0 6 0 100 0 0\n"
            % (pid, comm, pid, pid, utime, stime))


class FakeProc:
    """A /proc with the process 7 and its threads; each tick of the clock adds what the steps say."""

    def __init__(self, root, main, workers):
        self.root = Path(root)
        self.main = main        # (utime, stime) per second of the main thread
        self.workers = workers  # the same, per worker thread
        self.now = 0.0
        self.write()

    def write(self):
        threads = {7: self.main}
        for i, w in enumerate(self.workers):
            threads[8 + i] = w
        for tid, (u, s) in threads.items():
            d = self.root / "7" / "task" / str(tid)
            d.mkdir(parents=True, exist_ok=True)
            (d / "stat").write_text(stat_line(tid, int(u * self.now), int(s * self.now)))
        # the stat of the process: all threads together
        total_u = sum(int(u * self.now) for u, _ in threads.values())
        total_s = sum(int(s * self.now) for _, s in threads.values())
        (self.root / "7" / "stat").write_text(stat_line(7, total_u, total_s))

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.write()


def run(proc, threshold="99"):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cpu_check.main(["cpu_check.py", "7", threshold], proc=str(proc.root), hz=100,
                            clock=proc.clock, sleep=proc.sleep)
    return rc, out.getvalue()


class CpuCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_busy_workers_with_an_idle_loop_are_healthy(self):
        # four emu workers at 25 % each = one core; the event loop at 2 %
        proc = FakeProc(self.tmp.name, main=(1, 1), workers=[(20, 5)] * 4)
        rc, out = run(proc)
        self.assertEqual(rc, 0, out)
        self.assertIn("2.0%", out)

    def test_a_spinning_event_loop_is_unhealthy(self):
        proc = FakeProc(self.tmp.name, main=(80, 20), workers=[])
        rc, out = run(proc)
        self.assertEqual(rc, 1, out)
        self.assertIn("100.0%", out)

    def test_below_the_threshold(self):
        proc = FakeProc(self.tmp.name, main=(90, 8), workers=[(100, 0)])
        self.assertEqual(run(proc)[0], 0)

    def test_three_seconds_are_measured(self):
        proc = FakeProc(self.tmp.name, main=(1, 0), workers=[])
        run(proc)
        self.assertEqual(proc.now, 3.0)

    def test_a_comm_with_spaces_and_parentheses(self):
        d = Path(self.tmp.name) / "9" / "task" / "9"
        d.mkdir(parents=True)
        (d / "stat").write_text(stat_line(9, 123, 45, comm="a) b (c"))
        self.assertEqual(cpu_check.thread_ticks(9, proc=self.tmp.name), 168)

    def test_no_such_process(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cpu_check.main(["cpu_check.py", "4242", "99"], proc=self.tmp.name, hz=100)
        self.assertEqual(rc, 1)
        self.assertIn("4242", out.getvalue())

    def test_usage_and_arguments(self):
        for argv in (["cpu_check.py"], ["cpu_check.py", "x", "99"], ["cpu_check.py", "7", "y"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cpu_check.main(argv, proc=self.tmp.name, hz=100), 1, argv)

    def test_the_dockerfile_keeps_the_threshold_of_99(self):
        dockerfile = (DIST.parent / "Dockerfile").read_text()
        self.assertIn("cpu_check.py $(pgrep -of dionaea) 99", dockerfile)

    def test_stdlib_only(self):
        source = (DIST / "cpu_check.py").read_text()
        self.assertNotIn("psutil", source)


if __name__ == "__main__":
    unittest.main()
