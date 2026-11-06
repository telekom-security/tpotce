"""Sudo-mode frontends use the shared play and report its effective results."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tpotctl.tests import isolate

isolate()

from tpotctl import cli, sensors

textual = importlib.util.find_spec("textual") and importlib.util.find_spec("rich")


@unittest.skipUnless(textual, "run with the T-Pot Manager venv")
class CliTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        (self.directory / ".env").write_text("TPOT_TYPE=HIVE\nTPOT_DATA_PATH=./data\n")
        registry = sensors.Registry(str(self.directory))
        registry.records = {
            "alpha": sensors.Sensor(name="alpha", host="alpha.test", ssh_user="operator", ssh_port=2222),
            "beta": sensors.Sensor(name="beta", host="beta.test", ssh_user="other", ssh_port=64295),
        }
        registry.save()
        self.calls = []
        self.status = dict(user="operator", uid=1000, own_rule="absent", temporary_rule="absent",
                           password_usable=True, alternative=True, password_required=True,
                           changed=False, changes=[], warnings=[])
        self.missing = set()
        self.paths = []
        for patch in (
            mock.patch.object(sensors, "Registry", return_value=registry),
            mock.patch.object(cli.ops, "linux_host", return_value=True),
            mock.patch.object(cli.ops, "require_config_current", side_effect=AssertionError("sudo must allow drift")),
            mock.patch("tpotctl.installer.sudo_mode", return_value="passwordless"),
            mock.patch.object(cli, "call", side_effect=self.play),
            mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.directory)}),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def play(self, command, **kwargs):
        self.assertEqual(command[0], "ansible-playbook")
        self.assertTrue(command[1].endswith("/installer/install/sudo_mode.yml"))
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
        variables = json.loads(command[command.index("-e") + 1])
        inventory = command[command.index("-i") + 1]
        if inventory == "localhost,":
            self.assertIn("local", command)
            names, text = ["localhost"], ""
        else:
            text = Path(inventory).read_text()
            self.assertEqual(Path(inventory).stat().st_mode & 0o777, 0o600)
            names = [line.split()[0] for line in text.splitlines() if line and not line.startswith("[")]
        self.calls.append((variables["tpot_sudo_action"], names, text, command))
        result = Path(variables["tpot_sudo_result_file"])
        self.paths.append(result)
        for key in ("tpot_become_password_file", "tpot_user_password_file"):
            if key in variables:
                path = Path(variables[key])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.paths.append(path)
                self.assertNotIn(path.read_text().strip(), " ".join(command))
        with result.open("w") as out:
            for name in names:
                if name not in self.missing:
                    status = dict(self.status)
                    if variables["tpot_sudo_action"] == "convenient":
                        status.update(own_rule="owned", password_required=False, changed=True)
                    out.write(json.dumps(dict(schema=1, host=name, result=status, error="")) + "\n")
        return 1 if self.missing.intersection(names) else 0

    def run_cli(self, *args, stdin=None):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out), mock.patch("sys.stdin", stdin or io.StringIO()):
            try:
                code = cli.dispatch(list(args))
            except SystemExit as err:
                code = err.code
        return code, out.getvalue()

    def password_file(self):
        path = self.directory / "provided password"
        path.write_text("  Distant-Otters!swim-47  \n")
        path.chmod(0o600)
        return path

    def test_local_status_is_default_and_reports_effective_access(self):
        code, text = self.run_cli("sudo")
        self.assertEqual(code, 0, text)
        self.assertIn("password required", text.lower())
        self.assertEqual([action for action, *_ in self.calls], ["status"])
        self.assertTrue(all(not path.exists() for path in self.paths))

    def test_a_change_needs_confirmation_but_not_current_configuration(self):
        code, _ = self.run_cli("sudo", "convenient")
        self.assertEqual(code, 2)
        self.assertFalse(self.calls)
        code, text = self.run_cli("sudo", "convenient", "-y")
        self.assertEqual(code, 0, text)
        self.assertIn("password not required", text.lower())
        self.assertEqual([action for action, *_ in self.calls], ["status", "convenient"])

    def test_unattended_secure_refuses_a_locked_user_before_changes(self):
        self.status["password_usable"] = False
        code, text = self.run_cli("sudo", "secure", "-y")
        self.assertEqual(code, 2, text)
        self.assertIn("--user-password-file", text)
        self.assertEqual([action for action, *_ in self.calls], ["status"])

    def test_a_supplied_password_stays_private_and_its_original_is_kept(self):
        path = self.password_file()
        self.status["password_usable"] = False
        code, text = self.run_cli("sudo", "secure", "-y", "--user-password-file", str(path),
                                  "--become-file", str(path))
        self.assertEqual(code, 0, text)
        self.assertIn(str(path), text)
        self.assertTrue(path.exists())
        self.assertNotIn("Distant-Otters!swim-47", text)
        self.assertTrue(all(not p.exists() for p in self.paths))

    def test_foreign_passwordless_policy_is_not_reported_as_secure(self):
        self.status.update(password_required=False, warnings=["A foreign policy still allows passwordless sudo."])
        code, text = self.run_cli("sudo", "secure", "-y")
        self.assertEqual(code, 0, text)
        self.assertIn("password not required", text.lower())
        self.assertIn("foreign policy", text)

    def test_unknown_policy_is_not_reported_as_a_password_requirement(self):
        self.status["password_required"] = None
        code, text = self.run_cli("sudo")
        self.assertEqual(code, 0, text)
        self.assertIn("unknown", text.lower())
        self.assertNotIn("password required", text.lower())

    def test_unsafe_or_weak_password_files_are_refused_before_an_ssh_call(self):
        path = self.password_file()
        path.chmod(0o644)
        code, text = self.run_cli("sudo", "secure", "-y", "--become-file", str(path))
        self.assertEqual(code, 1, text)
        self.assertIn("chmod 600", text)
        path.chmod(0o600)
        path.write_text("short\n")
        code, text = self.run_cli("sudo", "secure", "-y", "--user-password-file", str(path))
        self.assertEqual(code, 1, text)
        self.assertIn("weak", text)
        self.assertFalse(self.calls)

    def test_missing_sudo_credentials_do_not_turn_into_an_ansible_prompt(self):
        with mock.patch("tpotctl.installer.sudo_mode", return_value="password"):
            code, text = self.run_cli("sudo")
        self.assertEqual(code, 2, text)
        self.assertIn("--become-file", text)
        self.assertFalse(self.calls)

    def test_interactive_credentials_are_collected_before_the_change_and_cleaned(self):
        terminal = mock.Mock(isatty=lambda: True)
        self.status["password_usable"] = False
        password = "  Distant-Otters!swim-47  "
        with mock.patch("tpotctl.installer.sudo_mode", return_value="password"), \
                mock.patch("getpass.getpass", side_effect=[password, password, password]), \
                mock.patch("os.unlink", wraps=os.unlink) as unlink:
            code, text = self.run_cli("sudo", "secure", "-y", stdin=terminal)
        self.assertEqual(code, 0, text)
        self.assertEqual([action for action, *_ in self.calls], ["status", "secure"])
        self.assertIn("SSH", text)
        self.assertNotIn(password.strip(), text)
        self.assertTrue(all(not p.exists() for p in self.paths))
        for path in {str(p) for p in self.paths if p.name.startswith(".tpot-install-")}:
            self.assertEqual(sum(call.args[0] == path for call in unlink.call_args_list), 1,
                             "Each private-file context must clean only the files it created.")

    def test_all_or_named_sensors_use_the_registry_ports_and_login_users(self):
        path = self.password_file()
        code, text = self.run_cli("sensors", "sudo", "status", "--become-file", str(path))
        self.assertEqual(code, 0, text)
        inventories = "\n".join(item[2] for item in self.calls)
        self.assertIn("alpha ansible_host=alpha.test ansible_user=operator ansible_port=2222", inventories)
        self.assertIn("beta ansible_host=beta.test ansible_user=other ansible_port=64295", inventories)
        self.calls.clear()
        code, text = self.run_cli("sensors", "sudo", "beta", "convenient", "-y", "--become-file", str(path))
        self.assertEqual(code, 0, text)
        self.assertEqual([(action, names) for action, names, *_ in self.calls],
                         [("status", ["beta"]), ("convenient", ["beta"])])

    def test_a_missing_sensor_result_is_a_failure_and_is_not_changed(self):
        self.missing.add("alpha")
        code, text = self.run_cli("sensors", "sudo", "convenient", "-y",
                                  "--become-file", str(self.password_file()))
        self.assertEqual(code, 1, text)
        self.assertIn("alpha", text)
        self.assertIn("SSH", text)
        changes = [(action, names) for action, names, *_ in self.calls if action != "status"]
        self.assertEqual(changes, [("convenient", ["beta"])])

    def test_host_actions_are_refused_on_macos_before_any_probe(self):
        with mock.patch.object(cli.ops, "linux_host", return_value=False):
            for args in (("sudo",), ("sensors", "sudo", "status")):
                code, text = self.run_cli(*args)
                self.assertNotEqual(code, 0)
                self.assertIn("Linux", text)
        self.assertFalse(self.calls)
