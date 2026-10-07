"""The release tool: the file `version` is the one source, `tpotctl.release` keeps every
other place that has to carry the number in step with it.

Nothing here writes the checkout the tests run from: set-version only runs in a
temporary copy of the files it touches.
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import release  # noqa: E402

ROOT = release.ROOT
NEW = "99.1.0"


def run(*args, cwd=None):
    """The command line of the tool, as a person runs it."""
    return subprocess.run([sys.executable, "-m", "tpotctl.release", *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=60)


def copy_of_the_places(target):
    """The file `version` and every file a place or a pending reader names, at the same path."""
    paths = {"version"}
    for place in release.PLACES + release.PENDING_READERS:
        paths.update(release.files_of(place, ROOT))
    for rel in sorted(paths):
        dest = Path(target, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(ROOT / rel), str(dest))
    return sorted(paths)


class CheckTest(unittest.TestCase):

    def test_every_place_has_the_version(self):
        self.assertEqual(release.check(ROOT), [])
        result = run("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(release.read_version(ROOT), result.stdout)

    def test_the_release_tool_is_not_in_the_manager(self):
        # a tool for building releases like docker/_builder: no sub-command, no menu entry
        package = ROOT / "tpotctl"
        for path in sorted(package.rglob("*.py")):
            if "tests" in path.relative_to(package).parts or path.name == "release.py":
                continue
            with self.subTest(module=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"\brelease\b.*\bimport\b|\bimport\b.*\brelease\b|tpotctl\.release")

    def test_every_place_names_files(self):
        for place in release.PLACES:
            with self.subTest(place=place.what):
                self.assertTrue(release.files_of(place, ROOT), place.paths)

    def test_a_wrong_or_lost_place_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy_of_the_places(tmp)
            env = Path(tmp, "docker/_builder/.env")
            env.write_text(env.read_text().replace("TPOT_VERSION=" + release.read_version(ROOT), "TPOT_VERSION=1.0.0"))
            html = Path(tmp, "docker/nginx/dist/html/index.html")
            html.write_text(html.read_text().replace('class="dynamic-text"', 'class="other-text"'))
            problems = release.check(Path(tmp))
            self.assertEqual(len(problems), 2, problems)
            self.assertTrue(any("docker/_builder/.env" in p and "1.0.0" in p for p in problems), problems)
            self.assertTrue(any("index.html" in p and "not found" in p for p in problems), problems)
            result = run("check", "--root", tmp)
            self.assertEqual(result.returncode, 1)
            self.assertIn("docker/_builder/.env", result.stdout + result.stderr)


class SetVersionTest(unittest.TestCase):

    def test_set_version_in_a_copy(self):
        old = release.read_version(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            paths = copy_of_the_places(tmp)
            os.chmod(os.path.join(tmp, ".env"), 0o600)
            before = {rel: Path(tmp, rel).read_bytes() for rel in paths}
            modes = {rel: stat.S_IMODE(os.stat(os.path.join(tmp, rel)).st_mode) for rel in paths}
            expected = set()    # (path, line number) of every line that carries the version at a place
            for place in release.PLACES + release.PENDING_READERS:
                for rel in release.files_of(place, Path(tmp)):
                    for hit in release.hits(place, Path(tmp), rel):
                        expected.add((rel, hit.line))

            result = run("set-version", NEW, "--root", tmp)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(Path(tmp, "version").read_text(), NEW + "\n")
            self.assertEqual(release.check(Path(tmp)), [])
            self.assertEqual(run("check", "--root", tmp).returncode, 0)

            changed = set()
            for rel in paths:
                after = Path(tmp, rel).read_bytes()
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(tmp, rel)).st_mode), modes[rel], rel)
                self.assertEqual(after.count(b"\r\n"), before[rel].count(b"\r\n"), rel)
                old_lines, new_lines = before[rel].split(b"\n"), after.split(b"\n")
                self.assertEqual(len(old_lines), len(new_lines), rel)
                for number, (a, b) in enumerate(zip(old_lines, new_lines), 1):
                    if a != b:
                        changed.add((rel, number))
                        self.assertEqual(b, a.replace(old.encode(), NEW.encode()), f"{rel}:{number}")
            changed.discard(("version", 1))
            self.assertEqual(changed, expected)

    def test_line_endings_and_modes_are_kept(self):
        places = (release.Place(("a.txt",), r"^VERSION=" + release.V + r"$", "a test place"),)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "version").write_bytes(b"1.2.3\r\n")
            Path(tmp, "a.txt").write_bytes(b"x\r\nVERSION=1.2.3\r\ny\r\n")
            os.chmod(os.path.join(tmp, "a.txt"), 0o640)
            self.assertEqual(release.check(Path(tmp), places=places, pending=()), [])
            release.set_version(Path(tmp), "1.2.4", places=places, pending=())
            self.assertEqual(Path(tmp, "a.txt").read_bytes(), b"x\r\nVERSION=1.2.4\r\ny\r\n")
            self.assertEqual(Path(tmp, "version").read_bytes(), b"1.2.4\r\n")
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(tmp, "a.txt")).st_mode), 0o640)

    def test_only_x_y_z_is_taken_and_nothing_is_written_otherwise(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = copy_of_the_places(tmp)
            before = {rel: Path(tmp, rel).read_bytes() for rel in paths}
            for bad in ("24.04", "v24.04.3", "24.04.3-rc1", "24.04.3 ", "", "24..3", "a.b.c"):
                with self.subTest(version=bad):
                    with self.assertRaises(release.ReleaseError):
                        release.set_version(Path(tmp), bad)
                    result = run("set-version", bad, "--root", tmp)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("X.Y.Z", result.stderr)
            self.assertEqual({rel: Path(tmp, rel).read_bytes() for rel in paths}, before)

    def test_a_lost_place_stops_set_version_before_it_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = copy_of_the_places(tmp)
            html = Path(tmp, "docker/nginx/dist/html/index.html")
            html.write_text(html.read_text().replace('class="dynamic-text"', 'class="other-text"'))
            before = {rel: Path(tmp, rel).read_bytes() for rel in paths}
            with self.assertRaises(release.ReleaseError) as caught:
                release.set_version(Path(tmp), NEW)
            self.assertIn("index.html", str(caught.exception))
            self.assertEqual({rel: Path(tmp, rel).read_bytes() for rel in paths}, before)


def git_checkout():
    try:
        done = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.strip() == "true"


class HardCodedTest(unittest.TestCase):

    @unittest.skipUnless(git_checkout(), "needs the git checkout (git grep)")
    def test_no_unlisted_hardcoded_version(self):
        # a place that carries the version but is neither in PLACES nor in EXCLUDED: add it to
        # one of them (with the reason), or let the code read the file `version`
        self.assertEqual(release.unlisted(ROOT), [])

    def test_an_unlisted_place_is_found(self):
        version = release.read_version(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "version").write_text(version + "\n")
            Path(tmp, "env.example").write_text("TPOT_VERSION=" + version + "\n")
            Path(tmp, "new.sh").write_text('myVERSION="' + version + '"\n')
            subprocess.run(["git", "init", "-q", tmp], check=True, timeout=30)
            subprocess.run(["git", "-C", tmp, "add", "version", "env.example", "new.sh"], check=True, timeout=30)
            self.assertEqual(release.unlisted(Path(tmp)), ["new.sh:1: myVERSION=\"" + version + "\""])

    def test_scripts_read_the_version_centrally(self):
        found = release.literals_in_scripts(ROOT)
        pending = set(release.pending_hits(ROOT))
        # a new literal in a script is red at once, only the readers other work packages fix wait
        self.assertEqual([hit for hit in found if hit not in pending], [])
        if found:
            self.skipTest("green after integration of W2/W4: " + ", ".join(found))

    def test_pending_readers_are_still_pending(self):
        # once a reader reads the file `version`, it leaves PENDING_READERS (and the skip above ends)
        for reader in release.PENDING_READERS:
            with self.subTest(reader=reader.what):
                self.assertTrue(any(release.hits(reader, ROOT, rel) for rel in release.files_of(reader, ROOT)),
                                f"{reader.paths} reads the version now: remove it from PENDING_READERS")


if __name__ == "__main__":
    unittest.main()
