"""The smoke test harness of docker/_tests finds its files with any CDPATH (the tests themselves need docker
and are not run here: only the path handling of run.sh, lib/common.sh and the tests/*.sh)."""

import os
import re
import subprocess
import tempfile
import unittest

from tpotctl.tests import isolate

isolate()

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.join(REPO, "docker", "_tests")
# an exported CDPATH with "." turns `cd docker/_tests` into a search that prints the folder it found
CDPATH = ".:/nonexistent-tpot-cdpath"
# a cd in command position: at the start of a line, after ; & | ( $( && ||, maybe with CDPATH="" before it
CD = re.compile(r"(?:^|[;&|(]|\$\()\s*(CDPATH=(?:\"\"|'')?\s+)?cd\s")
# a $(cd ...) captures what cd prints: with CDPATH that is two lines (a bare cd only prints a line too much)
CAPTURED = re.compile(r"\$\(\s*(CDPATH=(?:\"\"|'')?\s+)?cd\s")
# not ours (git submodules or upstream sources copied in) - none so far
VENDORED = ()
# scripts outside this pass that still capture a cd with the caller's CDPATH, with the reason; the test
# fails when one of them is fixed and still listed here
PENDING = {
    "docker/heralding/validation/build_tpot.sh": "not owned by the stage 8f Fix-Rest pass: patch in its report",
}


def run(args, env=None, cwd=REPO):
    full = dict(os.environ)
    full.pop("CDPATH", None)
    full.update(env or {})
    return subprocess.run(["bash", *args], cwd=cwd, env=full, capture_output=True, text=True, timeout=60)


def code_lines(path):
    """The lines of a script, comments blanked (the line numbers stay)."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return ["" if line.lstrip().startswith("#") else line for line in handle.read().split("\n")]


def uncleared_cd(path, pattern=CD):
    """The first cd (of pattern) that runs with the CDPATH of the caller: before any `unset CDPATH` and
    without a CDPATH="" of its own. None when there is none."""
    for number, line in enumerate(code_lines(path), 1):
        if re.match(r"\s*unset\s+(?:-v\s+)?CDPATH\s*$", line):
            return None
        for match in pattern.finditer(line):
            if not match.group(1):
                return f"{number}: {line.strip()}"
    return None


def shell_scripts():
    """Every *.sh of the checkout, without hidden folders (.git, .claude worktrees) and vendored code."""
    for root, dirs, files in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("node_modules", "data"))
        for name in sorted(files):
            path = os.path.join(root, name)
            if name.endswith(".sh") and os.path.relpath(path, REPO) not in VENDORED:
                yield path


class CdpathTest(unittest.TestCase):
    """verify #2: `CDPATH=. bash docker/_tests/run.sh --list` printed "find: …/docker/_tests\\n…/tests: No such
    file or directory" and listed nothing (rc 0); a single test broke the same way."""

    def test_list_with_cdpath_is_the_list_without(self):
        without = run(["docker/_tests/run.sh", "--list"])
        found = run(["docker/_tests/run.sh", "--list"], env={"CDPATH": CDPATH})
        self.assertEqual(without.returncode, 0, without.stderr)
        self.assertIn("cowrie", without.stdout.split())
        self.assertNotIn("No such file", found.stderr)
        self.assertEqual((found.returncode, found.stdout), (0, without.stdout), found.stderr)

    def test_list_from_another_folder(self):
        """From the folder above the checkout, the relative path is the one CDPATH=. finds."""
        rel = os.path.join(os.path.basename(REPO), "docker", "_tests", "run.sh")
        found = run([rel, "--list"], env={"CDPATH": CDPATH}, cwd=os.path.dirname(REPO))
        self.assertNotIn("No such file", found.stderr)
        self.assertIn("cowrie", found.stdout.split())

    def test_one_test_finds_its_library(self):
        """A single test: -h comes after sourcing lib/common.sh, so it is there only when the path is."""
        without = run(["docker/_tests/tests/cowrie.sh", "-h"])
        found = run(["docker/_tests/tests/cowrie.sh", "-h"], env={"CDPATH": CDPATH})
        self.assertEqual(without.returncode, 0, without.stderr)
        self.assertNotIn("No such file", found.stderr)
        self.assertEqual((found.returncode, found.stdout), (0, without.stdout), found.stderr)

    def test_common_finds_the_checkout(self):
        """lib/common.sh sourced on its own: TEST_ROOT and REPO_ROOT are one folder each."""
        script = 'source docker/_tests/lib/common.sh; printf "%s|%s|%s" "$TEST_LIB_DIR" "$TEST_ROOT" "$REPO_ROOT"'
        found = run(["-c", script], env={"CDPATH": CDPATH})
        self.assertEqual(found.returncode, 0, found.stderr)
        real = os.path.realpath
        self.assertEqual([real(p) for p in found.stdout.split("|")],
                         [real(os.path.join(TESTS, "lib")), real(TESTS), real(REPO)])

    def test_every_script_of_the_harness_clears_cdpath(self):
        names = [os.path.join(TESTS, "run.sh"), os.path.join(TESTS, "lib", "common.sh")]
        names += sorted(os.path.join(TESTS, "tests", n) for n in os.listdir(os.path.join(TESTS, "tests"))
                        if n.endswith(".sh"))
        for path in names:
            with self.subTest(script=os.path.relpath(path, REPO)):
                self.assertIsNone(uncleared_cd(path))

    def test_every_script_of_the_checkout_clears_cdpath(self):
        """Every *.sh that captures a cd ($(cd ...) && pwd): `unset CDPATH` before it, or CDPATH="" on the
        cd (the builder's way)."""
        found = {os.path.relpath(path, REPO): where for path in shell_scripts()
                 for where in [uncleared_cd(path, CAPTURED)] if where}
        self.assertEqual(sorted(set(found) - set(PENDING)), [], found)
        self.assertEqual(sorted(set(PENDING) - set(found)), [], "fixed: take it out of PENDING")

    def test_the_check_sees_a_cd_without_cdpath(self):
        """The rule of the static check itself: a cd in each position counts, CDPATH="" and unset clear it."""
        cases = {
            'X="$(cd -- "$(dirname -- "$0")" && pwd)"': True,
            '(cd /tmp && ls)': True,
            'true && cd x': True,
            'cd "$x"': True,
            'X="$(CDPATH="" cd -- "$d" && pwd)"': False,
            "X=$(CDPATH='' cd x; pwd)": False,
            "(CDPATH= cd -P -- x)": False,
            'echo "next: cd $(printf %q x)"': False,
            "# cd x": False,
        }
        with tempfile.TemporaryDirectory() as folder:
            tmp = os.path.join(folder, "script.sh")
            for line, flagged in cases.items():
                with self.subTest(line=line):
                    with open(tmp, "w", encoding="utf-8") as handle:
                        handle.write(line + "\n")
                    self.assertEqual(uncleared_cd(tmp) is not None, flagged)
                    self.assertEqual(uncleared_cd(tmp, CAPTURED) is not None, flagged and "$(cd" in line)
                    with open(tmp, "w", encoding="utf-8") as handle:
                        handle.write("unset CDPATH\n" + line + "\n")
                    self.assertIsNone(uncleared_cd(tmp))


if __name__ == "__main__":
    unittest.main()
