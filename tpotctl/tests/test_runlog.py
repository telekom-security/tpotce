"""tpotctl.runlog: the @@tpot marks of the T-Pot scripts, and fuMARK of ui.sh."""

import os
import subprocess
import unittest

from tpotctl import runlog
from tpotctl.tests import isolate

isolate()

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI = os.path.join(REPO, "installer", "lib", "ui.sh")


class ParseMarkTest(unittest.TestCase):

    def test_mark(self):
        self.assertEqual(runlog.parse_mark("@@tpot phase backup Writing the backup\n"),
                         ("phase", ["backup", "Writing", "the", "backup"]))

    def test_plain_line(self):
        self.assertIsNone(runlog.parse_mark("hello @@tpot phase x\n"))
        self.assertIsNone(runlog.parse_mark("@@tpot\n"))


class RunTest(unittest.TestCase):

    def test_phases_with_and_without_title(self):
        run = runlog.Run()
        for line in ["@@tpot phase backup Writing the backup\n", "tar ...\n", "@@tpot phase pull\n",
                     "@@tpot phase backup again\n"]:
            run.feed(line)
        self.assertEqual(run.phases, [("backup", "Writing the backup"), ("pull", "pull")])
        self.assertEqual(run.phase, "backup")

    def test_warnings(self):
        run = runlog.Run()
        run.feed("@@tpot warn pull Not all images could be pulled\n")
        self.assertEqual(run.warnings, ["Not all images could be pulled"])

    def test_changed_checkout_and_failed_phases(self):
        run = runlog.Run()
        for line in ("@@tpot phase git Rolling the checkout back", "@@tpot changed checkout",
                     "@@tpot phase config Restoring the configuration", "@@tpot fail config"):
            run.feed(line)
        self.assertTrue(run.checkout_changed)
        self.assertEqual(run.failed, {"config"})
        self.assertEqual([key for key, _title in run.phases], ["git", "config"])

    def test_changed_back(self):
        run = runlog.Run()
        for line in ("@@tpot changed checkout", "@@tpot changed back"):
            run.feed(line)
        self.assertFalse(run.checkout_changed)
        run.feed("@@tpot changed checkout")
        self.assertTrue(run.checkout_changed)

    def test_plain_lines_are_kept_marks_are_not(self):
        run = runlog.Run()
        run.feed("hello\n")
        run.feed("@@tpot phase x\n")
        self.assertEqual(list(run.lines), ["hello"])

    def test_lines_are_bounded(self):
        run = runlog.Run(keep=3)
        for number in range(10):
            run.feed(f"{number}\n")
        self.assertEqual(list(run.lines), ["7", "8", "9"])


class FuMarkTest(unittest.TestCase):

    def mark(self, env):
        return subprocess.run(["bash", "-c", f'source "{UI}"; fuMARK phase x Y z'], capture_output=True,
                              universal_newlines=True, env=dict(os.environ, TPOT_GUM="off", **env)).stdout

    def test_marks_with_tpot_marks(self):
        self.assertEqual(self.mark({"TPOT_MARKS": "1"}), "@@tpot phase x Y z\n")

    def test_silent_otherwise(self):
        os.environ.pop("TPOT_MARKS", None)
        self.assertEqual(self.mark({}), "")

    def test_install_sh_marks_with_its_option(self):
        out = subprocess.run(["bash", "-c", f'source "{UI}"; myMARKS=y; fuMARK phase x'], capture_output=True,
                             universal_newlines=True, env=dict(os.environ, TPOT_GUM="off")).stdout
        self.assertEqual(out, "@@tpot phase x\n")


if __name__ == "__main__":
    unittest.main()


class EngineTest(unittest.TestCase):

    def test_lines_and_exit_code_of_a_child(self):
        from tpotctl.engine import Engine
        lines = []
        code = Engine(["bash", "-c", 'echo one; echo two >&2; read -r x || echo "no stdin"; exit 3'],
                      env=dict(os.environ, X="1"), cwd=REPO).run(lines.append)
        self.assertEqual(code, 3)
        self.assertEqual([line.rstrip("\n") for line in lines], ["one", "two", "no stdin"])

    def test_bytes_that_are_not_utf8(self):
        """nmap prints the banners of honeypots as they come."""
        from tpotctl.engine import Engine
        lines = []
        code = Engine(["bash", "-c", r"printf 'banner \xff\xfe end\nnext\n'"]).run(lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2)
        self.assertIn("end", lines[0])

    def test_children_do_not_inherit_the_venv_guard(self):
        """tpot (or update.sh calling it) run from the menu starts anew: TPOT_VENV of the menu stays out."""
        from tpotctl import bootstrap
        from tpotctl.engine import Engine
        lines = []
        env = dict(os.environ, **{bootstrap.GUARD: "1"})
        Engine(["python3", "-c", f"import os; print(os.environ.get('{bootstrap.GUARD}', 'none'))"],
               env=env).run(lines.append)
        self.assertEqual(lines[0].strip(), "none")
        os.environ[bootstrap.GUARD] = "1"
        self.addCleanup(os.environ.pop, bootstrap.GUARD, None)
        lines.clear()
        Engine(["python3", "-c", f"import os; print(os.environ.get('{bootstrap.GUARD}', 'none'))"]).run(lines.append)
        self.assertEqual(lines[0].strip(), "none")

    def test_child_runs_in_a_session_of_its_own(self):
        """sudo of a child must not reach the terminal of the menu (no /dev/tty)."""
        from tpotctl.engine import Engine
        lines = []
        Engine(["python3", "-c", "import os; print(os.getsid(0))"]).run(lines.append)
        self.assertNotEqual(int(lines[0]), os.getsid(0))

    def test_missing_command(self):
        from tpotctl.engine import Engine
        lines = []
        self.assertEqual(Engine(["/nonexistent/tool"]).run(lines.append), 127)
        self.assertTrue(lines and "/nonexistent/tool" in lines[0])
