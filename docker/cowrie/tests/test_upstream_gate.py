"""run_upstream_tests.py: Cowrie's own tests on the patched checkout as a gate of the image build (the
Dockerfile runs it after the patch). A failure that is not listed, a listed one that passes and a run without
tests fail; the test modules of skip_modules are left out."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import DIST

GATE = DIST / "run_upstream_tests.py"
EXCEPTIONS = DIST / "upstream_test_exceptions.json"


class GateTest(unittest.TestCase):
    def checkout(self, modules):
        root = Path(tempfile.mkdtemp()) / "cowrie"
        self.addCleanup(shutil.rmtree, root.parent, True)
        tests = root / "src/cowrie/test"
        tests.mkdir(parents=True)
        (root / "src/cowrie/__init__.py").write_text("")
        (tests / "__init__.py").write_text("")
        for name, text in modules.items():
            (tests / f"{name}.py").write_text(text)
        return root

    def run_gate(self, root, exceptions):
        path = root.parent / "exceptions.json"
        path.write_text(json.dumps(exceptions))
        return subprocess.run([sys.executable, str(GATE), "--cowrie-root", str(root), "--exceptions", str(path)],
                              capture_output=True, text=True)

    MODULES = {
        "test_ok": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n",
        "test_text": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_old_text(self):\n"
                     "        self.assertEqual('a', 'b')\n",
        "test_output_x": "import package_t_pot_does_not_install\n",
    }
    LISTED = {"skip_modules": {"cowrie.test.test_output_x": "plugin"},
              "expected_failures": {"cowrie.test.test_text.T.test_old_text": {
                  "reason": "persona text", "expect": "AssertionError: 'a' != 'b'"}}}

    def test_a_run_as_listed_passes(self):
        done = self.run_gate(self.checkout(self.MODULES), self.LISTED)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "upstream tests: 2 tests, 1 expected failures as listed\n")

    def test_a_failure_that_is_not_listed_fails(self):
        done = self.run_gate(self.checkout(self.MODULES), {"skip_modules": self.LISTED["skip_modules"]})
        self.assertEqual(done.returncode, 1)
        self.assertIn("fails and is not listed: cowrie.test.test_text.T.test_old_text", done.stderr)
        self.assertIn("AssertionError", done.stderr)

    def test_a_listed_failure_that_passes_fails(self):
        modules = dict(self.MODULES, test_text=self.MODULES["test_ok"].replace("test_a", "test_old_text"))
        done = self.run_gate(self.checkout(modules), self.LISTED)
        self.assertEqual(done.returncode, 1)
        self.assertIn("listed but passes (remove it from the list): cowrie.test.test_text.T.test_old_text",
                      done.stderr)

    def test_a_module_that_does_not_import_fails_unless_skipped(self):
        done = self.run_gate(self.checkout(self.MODULES),
                             {"expected_failures": self.LISTED["expected_failures"]})
        self.assertEqual(done.returncode, 1)
        self.assertIn("unittest.loader._FailedTest.cowrie.test.test_output_x", done.stderr)

    def test_a_listed_failure_of_another_kind_fails(self):
        # the failure has to hold the text of its entry: a listed test that breaks in another way is no pass
        modules = dict(self.MODULES, test_text=self.MODULES["test_text"].replace("'a'", "1 // 0 or 'a'"))
        done = self.run_gate(self.checkout(modules), self.LISTED)
        self.assertEqual(done.returncode, 1)
        self.assertIn("fails, but not as listed (without \"AssertionError: 'a' != 'b'\"): "
                      "cowrie.test.test_text.T.test_old_text", done.stderr)
        self.assertIn("ZeroDivisionError", done.stderr)

    def test_the_suite_runs_on_a_copy(self):
        # what the tests write in the checkout (downloads, tty logs, Twisted's dropin.cache) stays in the copy,
        #   and the copy is the one they import
        writer = ("import os\nimport unittest\n\nimport cowrie\n\n\nclass T(unittest.TestCase):\n"
                  "    def test_a(self):\n"
                  "        self.assertTrue(os.path.realpath(cowrie.__file__).startswith(os.path.realpath('src')))\n"
                  "        for name in ('var/lib/cowrie/downloads/x', 'var/lib/cowrie/tty/y',"
                  " 'src/twisted/plugins/dropin.cache'):\n"
                  "            os.makedirs(os.path.dirname(name), exist_ok=True)\n"
                  "            open(name, 'w').close()\n")
        root = self.checkout(dict(self.MODULES, test_ok=writer))
        done = self.run_gate(root, self.LISTED)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()),
                         ["src/cowrie/__init__.py", "src/cowrie/test/__init__.py", "src/cowrie/test/test_ok.py",
                          "src/cowrie/test/test_output_x.py", "src/cowrie/test/test_text.py"])

    def test_no_tests_fail(self):
        done = self.run_gate(self.checkout({}), {})
        self.assertEqual(done.returncode, 1)
        self.assertIn("no test ran", done.stderr)

    def test_every_entry_has_a_reason(self):
        listed = json.loads(EXCEPTIONS.read_text())
        for section in ("skip_modules", "expected_failures"):
            for test, entry in listed[section].items():
                reason = entry if section == "skip_modules" else entry["reason"]
                self.assertTrue(test.startswith("cowrie.test."), test)
                self.assertGreater(len(reason), 10, test)
                if section == "expected_failures":
                    # the text the failure must hold: the persona's answer, not just any failure
                    self.assertEqual(set(entry), {"reason", "expect"}, test)
                    self.assertGreater(len(entry["expect"]), 20, test)

    def test_the_dockerfile_runs_the_gate_after_the_patch(self):
        # the RUN line as the shell reads it: continuation lines joined
        dockerfile = re.sub(r" *\\\n\s*", " ", (DIST.parent / "Dockerfile").read_text())
        gate = "python3 /root/dist/run_upstream_tests.py --cowrie-root /home/cowrie/cowrie " \
               "--exceptions /root/dist/upstream_test_exceptions.json"
        self.assertIn(gate, dockerfile)
        self.assertLess(dockerfile.index("--self-test"), dockerfile.index(gate))


if __name__ == "__main__":
    unittest.main()
