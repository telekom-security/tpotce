"""Sensor-side fleet preparation against synthetic old installations and local Git."""

import base64
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tpotctl import fleet_remote
from tpotctl.tests import test_scripts as base


RUN_ID = "1234567890abcdef1234567890abcdef"
SENSOR = "sensor-quiet-otter"
VERSION = "fleet-target"
REMOTE = Path(base.REPO, "tpotctl", "fleet_remote.py")


class PrepareTest(base.Harness):

    def setUp(self):
        super().setUp()
        self.home = Path(self.home)
        self.checkout = self.home / "tpotce"
        self.origin = self.home / "origin"
        for path in (self.checkout, self.origin):
            path.mkdir()
            self.git(path, "init", "-q", "-b", "master")
        (self.checkout / "version").write_text("old-release\n", encoding="utf-8")
        (self.checkout / "update.sh").write_text('echo old > "$HOME/old-ran"; exit 99\n', encoding="utf-8")
        self.commit(self.checkout)
        self.set_identity()
        (self.origin / "version").write_text(VERSION + "\n", encoding="utf-8")
        (self.origin / "update.sh").write_text(
            'test "$1" = --check-data-loss || exit 99\n'
            'if [ -f "$HOME/loss" ]; then cat "$HOME/loss"; exit 3; fi\nexit 0\n', encoding="utf-8")
        (self.origin / "restore.sh").write_text("exit 0\n", encoding="utf-8")
        self.commit(self.origin)
        self.stub("uname", '#!/bin/sh\necho Linux\n')
        self.stub("sudo", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/sudo-args"\n'
                  'if [ "$1" = -S ]; then\n IFS= read -r password\n'
                  ' [ "$password" = "  private sudo  " ] || exit 1\n'
                  ' touch "$HOME/sudo-validated"\nfi\n'
                  '[ ! -f "$HOME/need-password" ] || [ -f "$HOME/sudo-validated" ]\n')
        self.stub("systemctl", '#!/bin/sh\n'
                  'if [ -f "$HOME/active-unit" ]; then echo LoadState=loaded; echo ActiveState=active\n'
                  'else echo LoadState=not-found; echo ActiveState=inactive; fi\n')
        self.stub("ps", '#!/bin/sh\n[ ! -f "$HOME/processes" ] || cat "$HOME/processes"\n')
        self.stub("docker", '#!/bin/sh\necho "$HOME"\n')
        self.stub("systemd-run", '#!/bin/sh\necho started > "$HOME/unit-started"; exit 99\n')
        self.request = dict(action="prepare", run_id=RUN_ID, name=SENSOR, version=VERSION,
                            repo=str(self.origin), branch="master")
        self.state = self.home / ".local/share/tpotce/fleet" / RUN_ID

    def stub(self, name, text):
        path = Path(self.bin, name)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def git(self, path, *args):
        return subprocess.run(
            ["git", "-C", str(path), "-c", "core.hooksPath=/dev/null", "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", *args],
            check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, path):
        self.git(path, "add", "version", "update.sh")
        if (path / "restore.sh").exists():
            self.git(path, "add", "restore.sh")
        self.git(path, "commit", "-qm", "fixture")

    def set_identity(self, kind="SENSOR", name=SENSOR):
        auth = base64.b64encode((name + ":ingest-secret").encode()).decode()
        (self.checkout / ".env").write_text(
            f'TPOT_TYPE="{kind}"\nTPOT_HIVE_USER: "{auth}"\n', encoding="utf-8")

    def prepare(self, **changes):
        request = dict(self.request, **changes)
        env = dict(os.environ, HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}",
                   TPOT_GUM="off", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, str(REMOTE)], input=json.dumps(request),
                                capture_output=True, text=True, env=env, timeout=60, cwd=self.home)
        self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_prepare_fetches_target_without_running_or_replacing_old_updater(self):
        before = self.git(self.checkout, "rev-parse", "HEAD")
        code, reply = self.prepare()
        self.assertEqual(code, 0, reply)
        self.assertTrue(reply["ok"])
        state = reply["result"]
        self.assertEqual((state["name"], state["version"], state["phase"]), (SENSOR, VERSION, "prepared"))
        self.assertEqual(state["commit"], self.git(self.origin, "rev-parse", "HEAD"))
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD"), before)
        self.assertFalse((self.home / "old-ran").exists())
        self.assertFalse((self.home / "unit-started").exists())
        self.assertEqual((self.state / "target/version").read_text().strip(), VERSION)
        self.assertEqual((self.state / "target/update.sh").read_text(), (self.origin / "update.sh").read_text())
        self.assertEqual(json.loads((self.state / "state.json").read_text()), state)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.state / "state.json").stat().st_mode & 0o777, 0o600)

    def test_another_fetch_cannot_replace_this_runs_target(self):
        git = shutil.which("git")
        previous = self.git(self.checkout, "rev-parse", "HEAD")
        self.stub("git", f"#!{sys.executable}\n"
                  "import os, pathlib, subprocess, sys\n"
                  f"result = subprocess.run([{git!r}, *sys.argv[1:]])\n"
                  "if result.returncode == 0 and 'fetch' in sys.argv[1:]:\n"
                  f"    pathlib.Path(os.environ['HOME'], 'tpotce/.git/FETCH_HEAD').write_text({previous!r} + '\\n')\n"
                  "sys.exit(result.returncode)\n")
        code, reply = self.prepare()
        self.assertEqual(code, 0, reply)
        self.assertEqual(reply["result"]["version"], VERSION)
        self.assertEqual(reply["result"]["commit"], self.git(self.origin, "rev-parse", "HEAD"))

    def test_identity_mismatch_stops_before_sudo_fetch_and_state(self):
        for kind, name in (("HIVE", SENSOR), ("SENSOR", "sensor-someone-else")):
            with self.subTest(kind=kind, name=name):
                self.set_identity(kind, name)
                code, reply = self.prepare()
                self.assertEqual(code, 1, reply)
                self.assertFalse(reply["ok"])
                self.assertIn("identity", reply["error"].lower())
                self.assertNotIn("ingest-secret", json.dumps(reply))
                self.assertFalse((self.home / "sudo-args").exists())
                self.assertFalse(self.state.exists())
                self.assertFalse((self.checkout / ".git/FETCH_HEAD").exists())

    def test_bad_credentials_fail_explicitly_without_returning_the_value(self):
        (self.checkout / ".env").write_text("TPOT_TYPE=SENSOR\nTPOT_HIVE_USER=broken-secret\n")
        code, reply = self.prepare()
        self.assertEqual(code, 1, reply)
        self.assertIn("identity", reply["error"].lower())
        self.assertNotIn("broken-secret", json.dumps(reply))

    def test_password_is_only_used_on_stdin_and_temporary_private_file(self):
        (self.home / "need-password").touch()
        (self.origin / "update.sh").write_text(
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = -B ]; then file="$2"; shift; fi\n  shift\ndone\n'
            f'{sys.executable} - "$file" <<\'PY\'\n'
            "import json, os, pathlib, stat, sys\n"
            "path = pathlib.Path(sys.argv[1])\n"
            "modes = [stat.S_IMODE(path.stat().st_mode), stat.S_IMODE(path.parent.stat().st_mode)]\n"
            "pathlib.Path(os.environ['HOME'], 'secret-modes').write_text(json.dumps(modes))\n"
            "PY\n", encoding="utf-8")
        self.commit(self.origin)
        code, reply = self.prepare(password="  private sudo  ")
        self.assertEqual(code, 0, reply)
        self.assertTrue((self.home / "sudo-validated").exists())
        self.assertEqual(json.loads((self.home / "secret-modes").read_text()), [0o600, 0o700])
        for path in self.state.rglob("*"):
            if path.is_file():
                self.assertNotIn("  private sudo  ", path.read_text(), str(path))
        self.assertNotIn("private sudo", (self.home / "sudo-args").read_text())
        self.assertFalse((self.state / "become").exists())
        self.assertNotIn("password", reply["result"])

    def test_sudo_failure_blocks_preparation(self):
        (self.home / "need-password").touch()
        code, reply = self.prepare()
        self.assertEqual(code, 1, reply)
        self.assertIn("sudo", reply["error"])
        self.assertFalse(self.state.exists())

    def test_running_unit_or_old_manual_update_blocks_preparation(self):
        for filename, contents in (("active-unit", ""), ("processes", "501 98765 bash /home/x/tpotce/update.sh -y\n")):
            with self.subTest(filename=filename):
                marker = self.home / filename
                marker.write_text(contents)
                code, reply = self.prepare()
                marker.unlink()
                self.assertEqual(code, 1, reply)
                self.assertIn("already running", reply["error"])
                self.assertFalse(self.state.exists())

    def test_unrelated_process_arguments_are_not_parsed_as_shell_source(self):
        (self.home / "processes").write_text("501 98765 python -c print('unfinished\n")
        code, reply = self.prepare()
        self.assertEqual(code, 0, reply)

    def test_held_update_lock_blocks_preparation_without_replacing_inode(self):
        lock = self.home / ".local/share/tpotce/update.lock"
        lock.parent.mkdir(parents=True, mode=0o700)
        with lock.open("w") as stream:
            lock.chmod(0o600)
            inode = lock.stat().st_ino
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, reply = self.prepare()
            self.assertEqual(code, 1, reply)
            self.assertIn("already running", reply["error"])
            self.assertEqual(lock.stat().st_ino, inode)
            self.assertFalse(self.state.exists())

    def test_target_version_mismatch_leaves_installed_checkout_untouched(self):
        code, reply = self.prepare(version="different")
        self.assertEqual(code, 1, reply)
        self.assertIn("version", reply["error"])
        self.assertEqual((self.checkout / "version").read_text(), "old-release\n")
        self.assertFalse((self.state / "state.json").exists())

    def test_target_loss_report_is_retained_but_not_accepted(self):
        loss = "@@tpot loss images These images cannot be pulled: private/image:old."
        (self.home / "loss").write_text(loss + "\n")
        code, reply = self.prepare()
        self.assertEqual(code, 0, reply)
        self.assertEqual(reply["result"]["losses"], [loss])
        self.assertFalse((self.home / "unit-started").exists())

    def test_failed_loss_check_removes_the_password_and_does_not_save_success(self):
        (self.origin / "update.sh").write_text("exit 19\n")
        self.commit(self.origin)
        code, reply = self.prepare(password="  private sudo  ")
        self.assertEqual(code, 1, reply)
        self.assertIn("data-loss check failed", reply["error"])
        self.assertFalse((self.state / "become").exists())
        self.assertFalse((self.state / "state.json").exists())

    def test_controller_runs_with_only_its_env_reader_outside_the_checkout(self):
        control = self.home / "control"
        control.mkdir(mode=0o700)
        for name in ("fleet_remote.py", "envfile.py"):
            shutil.copyfile(Path(base.REPO, "tpotctl", name), control / name)
        with mock.patch(__name__ + ".REMOTE", control / "fleet_remote.py"):
            code, reply = self.prepare()
        self.assertEqual(code, 0, reply)
        self.assertEqual(reply["result"]["name"], SENSOR)

    def test_invalid_repo_url_returns_a_protocol_error_not_a_traceback(self):
        code, reply = self.prepare(repo="ssh://[invalid")
        self.assertEqual(code, 1, reply)
        self.assertIn("repository", reply["error"])

    def test_unsafe_run_id_or_state_directory_is_rejected(self):
        for run_id in ("../escape", "", "a" * 33):
            with self.subTest(run_id=run_id):
                code, reply = self.prepare(run_id=run_id)
                self.assertEqual(code, 1, reply)
                self.assertIn("run", reply["error"].lower())
                self.assertFalse(self.state.exists())
        outside = self.home / "outside"
        outside.mkdir()
        fleet = self.state.parent
        fleet.parent.mkdir(parents=True)
        fleet.symlink_to(outside, target_is_directory=True)
        code, reply = self.prepare()
        self.assertEqual(code, 1, reply)
        self.assertIn("directory", reply["error"].lower())
        self.assertEqual(list(outside.iterdir()), [])


class PrivateFilesTest(unittest.TestCase):

    def test_failed_secret_write_removes_the_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "become")
            with mock.patch.object(fleet_remote.os, "fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    fleet_remote.write_private(path, "secret\n")
            self.assertFalse(path.exists())

    def test_failed_exclusive_write_keeps_an_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "become")
            path.write_text("someone else's input\n")
            with self.assertRaises(FileExistsError):
                fleet_remote.write_private(path, "secret\n")
            self.assertEqual(path.read_text(), "someone else's input\n")

    def test_docker_storage_warning_uses_the_ten_gb_boundary(self):
        for available, warning in ((9_999_999_999, True), (10_000_000_000, False)):
            with self.subTest(available=available), \
                    mock.patch.object(fleet_remote, "checked", return_value="/var/lib/docker"), \
                    mock.patch.object(fleet_remote.os, "statvfs",
                                      return_value=SimpleNamespace(f_bavail=available, f_frsize=1)):
                self.assertEqual(bool(fleet_remote.storage_warnings()), warning)
