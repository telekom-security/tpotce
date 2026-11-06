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


class SensorHome(base.Harness):

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

    def seed_state(self, **changes):
        state = dict(run_id=RUN_ID, name=SENSOR, repo=str(self.origin), branch="master",
                     version=VERSION, previous_version="old-release", commit="a" * 40,
                     phase="prepared", losses=[], warnings=[])
        state.update(changes)
        self.state.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.state.parent.chmod(0o700)
        self.state.mkdir(mode=0o700, exist_ok=True)
        self.state.chmod(0o700)
        (self.state / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (self.state / "state.json").chmod(0o600)
        (self.state / "target").mkdir(mode=0o700, exist_ok=True)
        return state

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


class PrepareTest(SensorHome):

    @unittest.skipUnless(sys.platform == "linux", "the real updater needs GNU host tools")
    def test_real_target_data_loss_check_runs_outside_the_installed_checkout(self):
        source = Path(base.REPO)
        current_version = (source / "version").read_text().strip()
        for name in ("update.sh", "restore.sh", "version", "env.example"):
            shutil.copyfile(source / name, self.origin / name)
        (self.origin / "compose").mkdir()
        shutil.copyfile(source / "compose/sensor.yml", self.origin / "compose/sensor.yml")
        self.git(self.origin, "add", "env.example", "compose/sensor.yml")
        self.commit(self.origin)
        (self.checkout / "version").write_text("24.04.0\n")
        (self.checkout / "docker-compose.yml").write_text("# T-Pot: SENSOR\nservices: {}\n")
        with (self.checkout / ".env").open("a") as stream:
            stream.write("TPOT_VERSION=24.04.0\nTPOT_REPO=ghcr.io/telekom-security\nTPOT_DATA_PATH=./data\n")
        self.stub("sudo", '#!/bin/sh\n[ "$1" != -n ] || shift\n'
                  '[ "$1" != -v ] || exit 0\nexec "$@"\n')
        self.stub("docker", '#!/bin/sh\n[ "$1" != info ] || echo "$HOME"\nexit 0\n')
        self.stub("curl", "#!/bin/sh\nexit 7\n")
        self.stub("wget", "#!/bin/sh\nexit 7\n")
        code, reply = self.prepare(version=current_version)
        self.assertEqual(code, 0, reply)
        self.assertEqual(reply["result"]["version"], current_version)
        self.assertEqual((self.checkout / "version").read_text(), "24.04.0\n")
        self.assertFalse((self.home / "old-ran").exists())
        self.assertFalse((self.home / "unit-started").exists())

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


class StateReadTest(SensorHome):

    def test_reads_only_private_valid_state_bound_to_this_run(self):
        expected = self.seed_state()
        actual = fleet_remote.read_state(self.home, RUN_ID)
        self.assertEqual(actual, expected)

    def test_rejects_malformed_or_wrong_run_state(self):
        for data in ("{", "[]", "null", json.dumps(self.seed_state(run_id="f" * 32)),
                     json.dumps(self.seed_state(phase="complete", unknown="field")),
                     json.dumps(self.seed_state(losses="not a list")),
                     json.dumps(self.seed_state(losses=["@@tpot loss old", "@@tpot loss old"])),
                     json.dumps(self.seed_state(branch="../outside")),
                     json.dumps(self.seed_state(repo="https://user:fixture@host/repo")),
                     json.dumps(self.seed_state(warnings=[{"password": "do-not-read"}]))):
            with self.subTest(data=data):
                self.seed_state()
                (self.state / "state.json").write_text(data, encoding="utf-8")
                with self.assertRaises(fleet_remote.RemoteError):
                    fleet_remote.read_state(self.home, RUN_ID)


    def test_rejects_symlink_hardlink_and_non_private_state_file(self):
        for kind in ("symlink", "hardlink", "mode"):
            with self.subTest(kind=kind):
                self.seed_state()
                path = self.state / "state.json"
                if kind == "symlink":
                    path.unlink()
                    path.symlink_to(self.home / "outside-state")
                elif kind == "hardlink":
                    os.link(path, self.home / "state-copy")
                else:
                    path.chmod(0o644)
                with self.assertRaises(fleet_remote.RemoteError):
                    fleet_remote.read_state(self.home, RUN_ID)

    def test_rejects_linked_or_foreign_run_directory(self):
        self.seed_state()
        real = self.state
        moved = self.home / "real-run"
        real.rename(moved)
        real.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(fleet_remote.RemoteError):
            fleet_remote.read_state(self.home, RUN_ID)


class StartTest(SensorHome):

    def test_requires_exact_loss_approval_and_never_persists_credential(self):
        losses = ["@@tpot loss images: old image (12 MB)"]
        self.seed_state(losses=losses)
        request = dict(action="start", run_id=RUN_ID, name=SENSOR, accepted_losses=[],
                       password="fixture credential")
        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(fleet_remote, "sudo"), \
                mock.patch.object(fleet_remote, "command", return_value=SimpleNamespace(
                    returncode=0, stdout="", stderr="")) as run:
            with self.assertRaisesRegex(fleet_remote.RemoteError, "data-loss"):
                fleet_remote.start(request, self.home)
        self.assertFalse(any("systemd-run" in call.args[0] for call in run.call_args_list))
        self.assertNotIn("password", (self.state / "state.json").read_text(encoding="utf-8"))
        self.assertFalse((self.state / "become").exists())

    def test_start_rechecks_sensor_identity_and_version_after_preparation(self):
        self.seed_state()
        self.set_identity(kind="HIVE")
        request = dict(action="start", run_id=RUN_ID, name=SENSOR, accepted_losses=[])
        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(fleet_remote, "sudo"), \
                mock.patch.object(fleet_remote, "pin_source"), mock.patch.object(
                    fleet_remote, "command", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            with self.assertRaisesRegex(fleet_remote.RemoteError, "identity"):
                fleet_remote.start(request, self.home)
        self.assertFalse(any("systemd-run" in call.args[0] for call in run.call_args_list))

        self.set_identity()
        (self.checkout / "version").write_text("new-local-version\n", encoding="utf-8")
        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(fleet_remote, "sudo"), \
                mock.patch.object(fleet_remote, "pin_source"), mock.patch.object(
                    fleet_remote, "command", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            with self.assertRaisesRegex(fleet_remote.RemoteError, "changed"):
                fleet_remote.start(request, self.home)
        self.assertFalse(any("systemd-run" in call.args[0] for call in run.call_args_list))

    def test_stages_worker_and_starts_transient_service_with_approved_losses(self):
        losses = ["@@tpot loss images: old image (12 MB)"]
        self.seed_state(losses=losses)
        request = dict(action="start", run_id=RUN_ID, name=SENSOR, accepted_losses=losses,
                       password="fixture credential")
        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(fleet_remote, "sudo"), \
                mock.patch.object(fleet_remote, "pin_source"), \
                mock.patch.object(fleet_remote, "command", return_value=SimpleNamespace(
                    returncode=0, stdout="Unit started.\n", stderr="")) as run:
            result = fleet_remote.start(request, self.home)
        self.assertEqual(result["phase"], "running")
        self.assertEqual(result["unit"], "tpot-fleet-update.service")
        self.assertEqual((self.state / "become").read_text(encoding="utf-8"), "fixture credential\n")
        self.assertEqual((self.state / "accept-losses").read_text(encoding="utf-8"),
                         losses[0] + "\n")
        self.assertTrue((self.state / "fleet_remote.py").is_file())
        self.assertTrue((self.state / "envfile.py").is_file())
        args = run.call_args.args[0]
        self.assertIn("systemd-run", args)
        self.assertIn("--unit=tpot-fleet-update.service", args)
        self.assertNotIn("fixture credential", json.dumps(result))

    def test_failed_service_launch_removes_credential_and_marks_run_failed(self):
        self.seed_state()
        request = dict(action="start", run_id=RUN_ID, name=SENSOR, accepted_losses=[],
                       password="fixture credential")
        def fail_run(args, **kwargs):
            return SimpleNamespace(returncode=0 if args[0:2] == ["git", "check-ref-format"]
                                   or "reset-failed" in args else 1, stdout="", stderr="no service")
        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(fleet_remote, "sudo"), \
                mock.patch.object(fleet_remote, "pin_source"), \
                mock.patch.object(fleet_remote, "command", side_effect=fail_run):
            with self.assertRaisesRegex(fleet_remote.RemoteError, "systemd-run"):
                fleet_remote.start(request, self.home)
        state = fleet_remote.read_state(self.home, RUN_ID, name=SENSOR)
        self.assertEqual(state["phase"], "failed")
        self.assertFalse((self.state / "become").exists())

    def test_loss_file_write_failure_does_not_leave_the_sudo_credential(self):
        losses = ["@@tpot loss images: old image (12 MB)"]
        self.seed_state(losses=losses)
        request = dict(action="start", run_id=RUN_ID, name=SENSOR, accepted_losses=losses,
                       password="fixture credential")
        real_write = fleet_remote.write_private

        def fail_loss_file(path, content):
            if path.name == "accept-losses":
                raise OSError("disk full")
            return real_write(path, content)

        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(fleet_remote, "sudo"), \
                mock.patch.object(fleet_remote, "pin_source"), \
                mock.patch.object(fleet_remote, "write_private", side_effect=fail_loss_file):
            with self.assertRaisesRegex(fleet_remote.RemoteError, "stage"):
                fleet_remote.start(request, self.home)
        self.assertFalse((self.state / "become").exists())
        self.assertEqual(fleet_remote.read_state(self.home, RUN_ID)["phase"], "prepared")

    def test_pins_the_reviewed_git_commit_even_if_the_source_branch_moves(self):
        code, reply = self.prepare()
        self.assertEqual(code, 0, reply)
        state = reply["result"]
        reviewed = state["commit"]
        (self.origin / "version").write_text("different-target\n", encoding="utf-8")
        self.commit(self.origin)
        source = fleet_remote.pin_source(self.home, state, self.state)
        pinned = subprocess.run(
            ["git", "--git-dir", str(source), "rev-parse", f'refs/heads/{state["branch"]}^{{commit}}'],
            check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(pinned, reviewed)
        clone = self.home / "pinned-clone"
        subprocess.run(["git", "clone", "--branch", state["branch"], str(source), str(clone)],
                       check=True, capture_output=True, text=True)
        self.assertEqual((clone / "version").read_text(encoding="utf-8"), VERSION + "\n")

    def test_worker_records_the_update_result_and_exact_private_backup(self):
        archive_dir = self.home / "tpot_backups"
        archive_dir.mkdir(mode=0o700)
        archive = archive_dir / "20261010_tpot_backup.tar"
        archive.write_text("synthetic archive", encoding="utf-8")
        archive.chmod(0o600)
        ref = "refs/tpot-fleet/" + RUN_ID
        commit = self.git(self.checkout, "rev-parse", "HEAD")
        self.git(self.checkout, "update-ref", ref, commit)
        self.seed_state(phase="running", unit="tpot-fleet-update.service", started_at=1)
        (self.state / "become").write_text("\n", encoding="utf-8")
        (self.state / "become").chmod(0o600)
        script = (
            "#!/bin/bash\n"
            "python3 - \"$TPOT_UPDATE_BACKUP_FD\" \"$HOME/tpot_backups/20261010_tpot_backup.tar\" <<'PY'\n"
            "import json, os, sys\n"
            "os.write(int(sys.argv[1]), (json.dumps({'archive': sys.argv[2]}) + '\\n').encode())\n"
            "PY\n"
            "exit 0\n"
        )
        (self.state / "target/update.sh").write_text(script, encoding="utf-8")
        result = fleet_remote.worker(self.home, RUN_ID, "update")
        self.assertEqual(result, 0)
        state = fleet_remote.read_state(self.home, RUN_ID, name=SENSOR)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["archive"], str(archive))
        self.assertEqual(state["exit_code"], 0)
        self.assertFalse((self.state / "become").exists())
        self.assertTrue((self.state / "worker.log").is_file())
        remaining_ref = subprocess.run(
            ["git", "-C", str(self.checkout), "show-ref", "--verify", "--quiet", ref],
            check=False, capture_output=True)
        self.assertEqual(remaining_ref.returncode, 1)

    def test_status_preserves_live_credentials_and_marks_an_unrecorded_exit_failed(self):
        self.seed_state(phase="running", unit="tpot-fleet-update.service", started_at=1)
        for filename in ("become", "accept-losses"):
            (self.state / filename).write_text("private\n", encoding="utf-8")
            (self.state / filename).chmod(0o600)
        (self.state / "worker.log").write_text("first line\nsecond line\nlast line\n", encoding="utf-8")
        (self.state / "worker.log").chmod(0o600)
        active = "LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\nExecMainStatus=0"
        with mock.patch.object(fleet_remote, "checked", return_value=active):
            live = fleet_remote.status(self.home, RUN_ID, SENSOR)
        self.assertEqual(live["phase"], "running")
        self.assertEqual(live["log_tail"], "first line\nsecond line\nlast line\n")
        self.assertTrue((self.state / "become").exists())
        inactive = "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=exit-code\nExecMainStatus=1"
        with mock.patch.object(fleet_remote, "checked", return_value=inactive):
            stopped = fleet_remote.status(self.home, RUN_ID, SENSOR)
        self.assertEqual(stopped["phase"], "failed")
        self.assertEqual(stopped["exit_code"], 1)
        self.assertFalse((self.state / "become").exists())
        self.assertFalse((self.state / "accept-losses").exists())

    def test_explicit_rollback_stages_only_the_recorded_archive(self):
        archive_dir = self.home / "tpot_backups"
        archive_dir.mkdir(mode=0o700)
        archive = archive_dir / "20261010_tpot_backup.tar"
        archive.write_text("synthetic archive", encoding="utf-8")
        archive.chmod(0o600)
        self.seed_state(phase="completed", unit="tpot-fleet-update.service", started_at=1,
                        finished_at=2, exit_code=0, archive=str(archive))
        request = dict(action="rollback", run_id=RUN_ID, name=SENSOR, password="fixture credential")
        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(fleet_remote, "sudo"), \
                mock.patch.object(fleet_remote, "command", return_value=SimpleNamespace(
                    returncode=0, stdout="Unit started.\n", stderr="")) as run:
            result = fleet_remote.rollback(request, self.home)
        self.assertEqual(result["phase"], "rollback_running")
        self.assertEqual((self.state / "rollback-archive").read_text(encoding="utf-8"), str(archive) + "\n")
        self.assertIn("rollback", run.call_args.args[0])
        self.assertNotIn("--accept-data-loss", run.call_args.args[0])

    def test_explicit_rollback_can_be_retried_after_a_rollback_failure(self):
        archive_dir = self.home / "tpot_backups"
        archive_dir.mkdir(mode=0o700)
        archive = archive_dir / "20261010_tpot_backup.tar"
        archive.write_text("synthetic archive", encoding="utf-8")
        archive.chmod(0o600)
        self.seed_state(phase="rollback_failed", unit="tpot-fleet-update.service", started_at=1,
                        finished_at=2, exit_code=1, archive=str(archive))
        request = dict(action="rollback", run_id=RUN_ID, name=SENSOR, password="fixture-password")
        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(fleet_remote, "sudo"), \
                mock.patch.object(fleet_remote, "command", return_value=SimpleNamespace(
                    returncode=0, stdout="Unit started.\n", stderr="")):
            result = fleet_remote.rollback(request, self.home)
        self.assertEqual(result["phase"], "rollback_running")

    def test_rollback_worker_restores_checkout_and_restarts_tpot(self):
        archive_dir = self.home / "tpot_backups"
        archive_dir.mkdir(mode=0o700)
        archive = archive_dir / "20261010_tpot_backup.tar"
        archive.write_text("synthetic archive", encoding="utf-8")
        archive.chmod(0o600)
        self.seed_state(phase="rollback_running", unit="tpot-fleet-update.service", started_at=1,
                        archive=str(archive))
        (self.state / "become").write_text("fixture credential\n", encoding="utf-8")
        (self.state / "become").chmod(0o600)
        (self.state / "rollback-archive").write_text(str(archive) + "\n", encoding="utf-8")
        (self.state / "rollback-archive").chmod(0o600)
        marker = self.home / "restore-ran"
        (self.state / "target/restore.sh").write_text(
            f'printf "%s\\n" "$@" > {marker}\n', encoding="utf-8")
        (self.state / "target/restore.sh").chmod(0o600)
        with mock.patch.object(fleet_remote, "command", return_value=SimpleNamespace(
                returncode=0, stdout="", stderr="")) as run:
            result = fleet_remote.worker(self.home, RUN_ID, "rollback")
        self.assertEqual(result, 0, fleet_remote.read_state(self.home, RUN_ID, name=SENSOR))
        state = fleet_remote.read_state(self.home, RUN_ID, name=SENSOR)
        self.assertEqual(state["phase"], "rolled_back")
        self.assertIn("-c", marker.read_text(encoding="utf-8").splitlines())
        self.assertIn(str(archive), marker.read_text(encoding="utf-8"))
        self.assertTrue(any("systemctl" in call.args[0] and "start" in call.args[0]
                            for call in run.call_args_list))
        self.assertFalse((self.state / "become").exists())

    def test_backup_report_rejects_a_linked_backup_directory(self):
        actual_dir = self.home / "actual-backups"
        actual_dir.mkdir(mode=0o700)
        archive = actual_dir / "20261010_tpot_backup.tar"
        archive.write_text("synthetic archive", encoding="utf-8")
        archive.chmod(0o600)
        (self.home / "tpot_backups").symlink_to(actual_dir, target_is_directory=True)
        report = self.home / "backup-report.jsonl"
        report.write_text(json.dumps({"archive": str(self.home / "tpot_backups" / archive.name)}) + "\n",
                          encoding="utf-8")
        report.chmod(0o600)
        with self.assertRaises(fleet_remote.RemoteError):
            fleet_remote.read_backup_report(report, self.home)

    def test_discard_removes_only_a_stopped_run_and_its_private_target_ref(self):
        self.seed_state()
        request = dict(action="discard", run_id=RUN_ID, name=SENSOR)
        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(
                fleet_remote, "checked", return_value="") as checked:
            result = fleet_remote.discard(request, self.home)
            repeated = fleet_remote.discard(request, self.home)
        self.assertEqual(result, {"run_id": RUN_ID, "discarded": True})
        self.assertEqual(repeated, result)
        self.assertFalse(self.state.exists())
        checked.assert_called_once_with(["git", "-C", str(self.checkout), "update-ref", "-d",
                                         "refs/tpot-fleet/" + RUN_ID])

    def test_discard_is_idempotent_when_precheck_created_no_remote_state(self):
        request = dict(action="discard", run_id=RUN_ID, name=SENSOR)
        self.assertEqual(fleet_remote.discard(request, self.home),
                         {"run_id": RUN_ID, "discarded": True})

    def test_discard_preserves_unexpected_run_files(self):
        self.seed_state()
        unexpected = self.state / "preserve-me"
        unexpected.write_text("unowned by fleet", encoding="utf-8")
        request = dict(action="discard", run_id=RUN_ID, name=SENSOR)
        with mock.patch.object(fleet_remote, "idle"), mock.patch.object(
                fleet_remote, "checked", return_value=""):
            with self.assertRaisesRegex(fleet_remote.RemoteError, "unexpected"):
                fleet_remote.discard(request, self.home)
        self.assertEqual(unexpected.read_text(encoding="utf-8"), "unowned by fleet")

    def test_discard_refuses_a_running_worker_before_touching_state(self):
        self.seed_state(phase="running", unit="tpot-fleet-update.service", started_at=1)
        request = dict(action="discard", run_id=RUN_ID, name=SENSOR)
        with mock.patch.object(fleet_remote, "idle", side_effect=fleet_remote.RemoteError("still active")):
            with self.assertRaisesRegex(fleet_remote.RemoteError, "running"):
                fleet_remote.discard(request, self.home)
        self.assertTrue((self.state / "state.json").exists())


class PrivateFilesTest(unittest.TestCase):

    def test_external_commands_ignore_inherited_git_repository_and_config_overrides(self):
        variables = {
            "GIT_DIR": "/tmp/untrusted-git-dir",
            "GIT_WORK_TREE": "/tmp/untrusted-work-tree",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "/tmp/untrusted-hooks",
        }
        code = ("import os; print('|'.join(os.environ.get(key, '') for key in "
                "('GIT_DIR', 'GIT_WORK_TREE', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', "
                "'GIT_CONFIG_VALUE_0')))")
        with mock.patch.dict(os.environ, variables):
            result = fleet_remote.command([sys.executable, "-c", code])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "||||")

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


class RuntimeTest(SensorHome):

    def setUp(self):
        super().setUp()
        self.set_identity()
        (self.checkout / "version").write_text(VERSION + "\n", encoding="utf-8")
        with (self.checkout / ".env").open("a", encoding="utf-8") as stream:
            stream.write(f'TPOT_VERSION="{VERSION}"\n')
        self.seed_state(phase="completed")
        self.request = dict(action="runtime", run_id=RUN_ID, name=SENSOR)
        self.services = {
            "tpotinit": {"image": f"registry/tpotinit:{VERSION}"},
            "cowrie": {"image": f"registry/cowrie:{VERSION}"},
        }
        self.containers = [
            {"Name": "tpotinit", "Service": "tpotinit", "State": "running", "Health": "healthy"},
            {"Name": "cowrie", "Service": "cowrie", "State": "running", "Health": ""},
        ]
        self.service_state = "active"
        self.tpotinit_image = f"registry/tpotinit:{VERSION}"

    def runtime(self):
        def run(args, **kwargs):
            if args[:2] == ["git", "check-ref-format"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if args[:2] == ["systemctl", "is-active"]:
                return SimpleNamespace(returncode=0 if self.service_state == "active" else 3,
                                       stdout=self.service_state + "\n", stderr="")
            if args[:3] == ["docker", "compose", "--project-directory"]:
                if args[-3:] == ["config", "--format", "json"]:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"services": self.services}),
                                           stderr="")
                if args[-4:] == ["ps", "--all", "--format", "json"]:
                    output = "\n".join(json.dumps(item) for item in self.containers) + "\n"
                    return SimpleNamespace(returncode=0, stdout=output, stderr="")
            if args[:2] == ["docker", "inspect"]:
                return SimpleNamespace(returncode=0, stdout=self.tpotinit_image + "\n", stderr="")
            self.fail(f"unexpected command: {args}")

        with mock.patch.object(fleet_remote, "command", side_effect=run):
            return fleet_remote.runtime_status(self.request, self.home)

    def test_runtime_requires_target_version_service_and_every_container_ready(self):
        result = self.runtime()
        self.assertTrue(result["ready"])
        self.assertEqual(result["version"], VERSION)
        self.assertEqual(result["service"], "active")
        self.assertEqual(result["tpotinit_image"], f"registry/tpotinit:{VERSION}")

        self.containers[1]["Health"] = "unhealthy"
        result = self.runtime()
        self.assertFalse(result["ready"])
        self.assertTrue(any("cowrie" in item for item in result["problems"]))

        self.containers[1]["Health"] = ""
        self.containers[1]["State"] = "exited"
        result = self.runtime()
        self.assertFalse(result["ready"])
        self.assertTrue(any("cowrie" in item for item in result["problems"]))

        self.containers[1]["State"] = "running"
        self.tpotinit_image = "registry/tpotinit:old-release"
        result = self.runtime()
        self.assertFalse(result["ready"])
        self.assertTrue(any("tpotinit" in item for item in result["problems"]))

        self.tpotinit_image = f"registry/tpotinit:{VERSION}"
        self.service_state = "inactive"
        result = self.runtime()
        self.assertFalse(result["ready"])
        self.assertTrue(any("tpot.service" in item for item in result["problems"]))
