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

    def test_missing_command(self):
        from tpotctl.engine import Engine
        lines = []
        self.assertEqual(Engine(["/nonexistent/tool"]).run(lines.append), 127)
        self.assertTrue(lines and "/nonexistent/tool" in lines[0])
