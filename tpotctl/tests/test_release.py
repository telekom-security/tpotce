"""The release tool: the file `version` is the one source, `tpotctl.release` keeps every
other place that has to carry the number in step with it.

Nothing here writes the checkout the tests run from: set-version only runs in a
temporary copy of the files it touches.
"""

import contextlib
import errno
import io
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import release  # noqa: E402

ROOT = release.ROOT
NEW = "99.1.0"


def run(*args, cwd=None):
    """The command line of the tool, as a person runs it (set-version without the unit of the
    host: the tests never read /etc/systemd/system/tpot.service)."""
    if args and args[0] == "set-version":
        args = (*args, "--service-file", os.devnull)
    return subprocess.run([sys.executable, "-m", "tpotctl.release", *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=60)


def main_in_process(*args):
    """release.main with its output: (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = release.main(list(args))
    return rc, out.getvalue(), err.getvalue()


def contents(root, paths):
    return {rel: Path(root, rel).read_bytes() for rel in paths}


class FlakyOpen:
    """`open` of tpotctl.release: the handles opened for writing (a "+" mode) are counted, and
    `fail(number, call)` says whether write call `call` (1, 2, ...) of handle `number` (1, 2,
    ...) fails; it writes half of the data first, like a disk that runs full."""

    def __init__(self, fail):
        self.fail = fail
        self.opened = []        # the paths of the handles for writing, in order
        self.real = open

    def __call__(self, file, mode="r", *args, **kwargs):
        handle = self.real(file, mode, *args, **kwargs)
        if "+" not in mode:
            return handle
        self.opened.append(str(file))
        return _FlakyHandle(handle, len(self.opened), self.fail)


class _FlakyHandle:

    def __init__(self, handle, number, fail):
        self._handle, self._number, self._fail, self._calls = handle, number, fail, 0

    def write(self, data):
        self._calls += 1
        if self._fail(self._number, self._calls):
            data = bytes(data)
            self._handle.write(data[:len(data) // 2])
            raise OSError(errno.ENOSPC, "No space left on device")
        return self._handle.write(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._handle, name)


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
            html.write_text(html.read_text().replace('class="version"', 'class="other-text"'))
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
            html.write_text(html.read_text().replace('class="version"', 'class="other-text"'))
            before = {rel: Path(tmp, rel).read_bytes() for rel in paths}
            with self.assertRaises(release.ReleaseError) as caught:
                release.set_version(Path(tmp), NEW)
            self.assertIn("index.html", str(caught.exception))
            self.assertEqual({rel: Path(tmp, rel).read_bytes() for rel in paths}, before)

    def test_each_file_is_read_once(self):
        # CITATION.cff has four places: one read, not one per place plus two more for the write
        reads = {}
        real_read_bytes, real_open = pathlib.Path.read_bytes, open

        def count(path):
            key = Path(path).resolve()
            reads[key] = reads.get(key, 0) + 1

        def read_bytes(path):
            count(path)
            return real_read_bytes(path)

        def opener(file, mode="r", *args, **kwargs):
            if "+" not in mode and "w" not in mode:
                count(file)
            return real_open(file, mode, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            copy_of_the_places(tmp)
            with mock.patch.object(pathlib.Path, "read_bytes", read_bytes), \
                    mock.patch("tpotctl.release.open", opener, create=True):
                release.set_version(Path(tmp), NEW)
            self.assertEqual(release.check(Path(tmp)), [])
        self.assertIn(Path(tmp, "CITATION.cff").resolve(), reads)
        self.assertEqual({path.name: n for path, n in reads.items() if n != 1}, {})

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root opens a 0444 file for writing")
    def test_a_target_it_cannot_open_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = copy_of_the_places(tmp)
            target = Path(tmp, "genuserwin.ps1")       # late in the order, the files before it would be written
            os.chmod(str(target), 0o444)
            before = contents(tmp, paths)
            with self.assertRaises(release.ReleaseError) as caught:
                release.set_version(Path(tmp), NEW)
            self.assertIn("genuserwin.ps1", str(caught.exception))
            self.assertIn("nothing written", str(caught.exception))
            self.assertEqual(contents(tmp, paths), before)
            result = run("set-version", NEW, "--root", tmp)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("genuserwin.ps1", result.stderr)
            self.assertEqual(contents(tmp, paths), before)

    def test_a_file_replaced_after_reading_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = copy_of_the_places(tmp)
            target = Path(tmp, "genuserwin.ps1")
            swapped = target.read_bytes().replace(b"\n", b"\r\n")
            real_open = open

            def opener(file, mode="r", *args, **kwargs):
                if "+" in mode and Path(file).name == ".env":    # the first target: replace a later one
                    spare = Path(tmp, "spare")
                    spare.write_bytes(swapped)
                    os.replace(str(spare), str(target))
                return real_open(file, mode, *args, **kwargs)

            before = contents(tmp, paths)
            with mock.patch("tpotctl.release.open", opener, create=True):
                with self.assertRaises(release.ReleaseError) as caught:
                    release.set_version(Path(tmp), NEW)
            self.assertIn("genuserwin.ps1", str(caught.exception))
            self.assertIn("nothing written", str(caught.exception))
            before["genuserwin.ps1"] = swapped
            self.assertEqual(contents(tmp, paths), before)

    def test_a_failed_write_restores_what_was_written(self):
        flaky = FlakyOpen(lambda number, call: number == 3 and call == 1)
        with tempfile.TemporaryDirectory() as tmp:
            paths = copy_of_the_places(tmp)
            os.chmod(os.path.join(tmp, ".env"), 0o600)
            before = contents(tmp, paths)
            with mock.patch("tpotctl.release.open", flaky, create=True):
                with self.assertRaises(release.ReleaseError) as caught:
                    release.set_version(Path(tmp), NEW)
            self.assertGreater(len(flaky.opened), 3)     # every target was open before the first write
            first, second, third = (Path(p).relative_to(tmp).as_posix() for p in flaky.opened[:3])
            error = caught.exception
            self.assertEqual(error.failed, third)
            self.assertEqual(sorted(error.restored), sorted([first, second, third]))
            self.assertEqual(error.unrestored, [])
            self.assertIn(third, str(error))
            self.assertIn("No space left", str(error))
            self.assertEqual(contents(tmp, paths), before)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(tmp, ".env")).st_mode), 0o600)

    def test_a_failed_restore_is_reported(self):
        # handle 1 is written and cannot be put back, handle 2 is written and put back, handle 3 fails
        flaky = FlakyOpen(lambda number, call: (number == 1 and call == 2) or number == 3)
        with tempfile.TemporaryDirectory() as tmp:
            paths = copy_of_the_places(tmp)
            before = contents(tmp, paths)
            with mock.patch("tpotctl.release.open", flaky, create=True):
                with self.assertRaises(release.ReleaseError) as caught:
                    release.set_version(Path(tmp), NEW)
            first, second, third = (Path(p).relative_to(tmp).as_posix() for p in flaky.opened[:3])
            error = caught.exception
            self.assertEqual(error.failed, third)
            self.assertEqual(error.restored, [second])
            self.assertEqual(sorted(error.unrestored), sorted([first, third]))
            text = str(error)
            self.assertIn("not restored", text)
            for rel in (first, second, third):
                self.assertIn(rel, text)
            after = contents(tmp, paths)
            self.assertEqual(after[second], before[second])
            self.assertNotEqual(after[first], before[first])
            untouched = [rel for rel in paths if rel not in (first, second, third)]
            self.assertEqual({rel: after[rel] for rel in untouched}, {rel: before[rel] for rel in untouched})
            line = [part for part in text.split("\n") if "not restored" in part][0]
            self.assertIn(f"{first} (No space left on device)", line)        # why it could not be put back

    def test_ctrl_c_during_the_writes_restores_them(self):
        """A KeyboardInterrupt in the middle of the third write: every file as it was, rc 130 from main."""
        def fail(number, call):
            if number == 3 and call == 1:
                raise KeyboardInterrupt
            return False
        for in_process in (False, True):
            flaky = FlakyOpen(fail)
            with self.subTest(main=in_process), tempfile.TemporaryDirectory() as tmp:
                paths = copy_of_the_places(tmp)
                before = contents(tmp, paths)
                with mock.patch("tpotctl.release.open", flaky, create=True):
                    if in_process:
                        rc, _out, err = main_in_process("set-version", NEW, "--root", tmp)
                        self.assertEqual(rc, 130, err)
                        self.assertIn("interrupted", err)
                        self.assertNotIn("Traceback", err)
                    else:
                        with self.assertRaises(release.ReleaseError) as caught:
                            release.set_version(Path(tmp), NEW)
                        self.assertTrue(caught.exception.interrupted)
                        self.assertEqual(len(caught.exception.restored), 3)
                self.assertEqual(contents(tmp, paths), before)

    def test_a_real_ctrl_c_during_the_writes_restores_them(self):
        """SIGINT while the second file is written: the writes stop, every file is put back; a second one
        while they are put back cannot break that off."""
        import signal

        seen = []

        def fail(number, call):
            seen.append(signal.getsignal(signal.SIGINT))
            if (number == 2 and call == 1) or call == 2:      # call 2: putting a file back
                os.kill(os.getpid(), signal.SIGINT)
            return False
        flaky = FlakyOpen(fail)
        before_handler = signal.getsignal(signal.SIGINT)
        with tempfile.TemporaryDirectory() as tmp:
            paths = copy_of_the_places(tmp)
            before = contents(tmp, paths)
            with mock.patch("tpotctl.release.open", flaky, create=True), \
                    self.assertRaises(release.ReleaseError) as caught:
                release.set_version(Path(tmp), NEW)
            self.assertTrue(caught.exception.interrupted)
            self.assertEqual(caught.exception.unrestored, [])
            self.assertEqual(contents(tmp, paths), before)
        self.assertEqual(signal.getsignal(signal.SIGINT), before_handler)
        # from the first write on a ^C only counts: no window where it breaks off between two steps
        self.assertNotIn(signal.default_int_handler, seen)


class InstalledTest(unittest.TestCase):
    """The note of set-version for a .env that is the configuration of an installed T-Pot."""

    def unit(self, folder, root):
        path = Path(folder, "tpot.service")
        compose = Path(root, "docker-compose.yml")
        path.write_text("[Service]\n"
                        f"ExecStartPre=-/usr/bin/docker compose -f {compose} down -v\n"
                        f"ExecStart=/usr/bin/docker compose -f {compose} up\n"
                        f"ExecStop=/usr/bin/docker compose -f {compose} down -v\n")
        return path

    def test_with_a_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "data").mkdir()
            self.assertTrue(release.installed_t_pot(Path(tmp), service_file=Path(tmp, "no-such.service")))

    def test_with_a_unit_that_names_this_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as etc:
            self.assertTrue(release.installed_t_pot(Path(tmp), service_file=self.unit(etc, tmp)))
            # the root as the unit names it, the checkout as given (a link, a relative path)
            link = Path(etc, "link")
            link.symlink_to(tmp)
            self.assertTrue(release.installed_t_pot(link, service_file=self.unit(etc, tmp)))
            self.assertTrue(release.installed_t_pot(Path(tmp), service_file=self.unit(etc, link)))

    def test_without_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "data").write_text("a file, not the data folder\n")
            self.assertFalse(release.installed_t_pot(Path(tmp), service_file=Path(tmp, "no-such.service")))

    def test_with_a_unit_for_another_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as etc:
            for other in (tmp + "2", "/opt" + tmp, str(Path(tmp, "tpotce"))):
                with self.subTest(other=other):
                    self.assertFalse(release.installed_t_pot(Path(tmp), service_file=self.unit(etc, other)))

    def test_set_version_says_it_for_the_env_of_an_installed_t_pot(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as etc:
            copy_of_the_places(tmp)
            unit = self.unit(etc, tmp)
            rc, out, err = main_in_process("set-version", NEW, "--root", tmp, "--service-file", str(unit))
            self.assertEqual(rc, 0, out + err)
            self.assertIn("  .env\n", out)
            self.assertIn("note: .env is the configuration of the T-Pot installed here: its next start pulls"
                          f" the images {NEW} (TPOT_VERSION)", err)
            # .env not changed (the same version again): no note
            rc, out, err = main_in_process("set-version", NEW, "--root", tmp, "--service-file", str(unit))
            self.assertEqual(rc, 0, out + err)
            self.assertNotIn(".env", out)
            self.assertNotIn("note:", out + err)

    def test_set_version_says_nothing_for_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy_of_the_places(tmp)
            rc, out, err = main_in_process("set-version", NEW, "--root", tmp,
                                           "--service-file", str(Path(tmp, "no-such.service")))
            self.assertEqual(rc, 0, out + err)
            self.assertIn("  .env\n", out)
            self.assertNotIn("note:", out + err)


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
