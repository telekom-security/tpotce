"""The shared sudo-mode backend; policy fixtures follow sudo's evaluated long listing."""

import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from installer.install.library import tpot_sudo


DEFAULTS = """Matching Defaults entries for operator on hive:
    env_reset, mail_badpass, secure_path=/usr/sbin\\:/usr/bin\\:/sbin\\:/bin, use_pty

User operator may run the following commands on hive:
"""
OWN = "/etc/sudoers.d/tpot"
TEMP = "/etc/sudoers.d/tpot-install"


def entry(path, commands="ALL", users="ALL", groups="ALL", options=""):
    text = f"\nSudoers entry: {path}\n    RunAsUsers: {users}\n"
    if groups:
        text += f"    RunAsGroups: {groups}\n"
    if options:
        text += f"    Options: {options}\n"
    return text + "    Commands:\n\t" + commands.replace("\n", "\n\t") + "\n"


class PolicyTest(unittest.TestCase):
    def test_owned_permanent_and_temporary_grants_are_not_an_alternative(self):
        text = DEFAULTS + entry(OWN, options="!authenticate") + entry(TEMP, options="!authenticate")
        self.assertIs(tpot_sudo.inspect_policy(text, (OWN, TEMP)).alternative, False)

    def test_a_real_group_grant_is_an_alternative(self):
        text = DEFAULTS + entry("/etc/sudoers") + entry(OWN, options="!authenticate")
        self.assertIs(tpot_sudo.inspect_policy(text, (OWN,)).alternative, True)

    def test_a_foreign_nopasswd_grant_stays_an_alternative(self):
        text = DEFAULTS + entry("/etc/sudoers.d/cloud-init", options="!authenticate")
        self.assertIs(tpot_sudo.inspect_policy(text, (OWN,)).alternative, True)

    def test_only_unrestricted_root_grants_count(self):
        cases = (
            (entry("/etc/sudoers", commands="/usr/bin/true"), False),
            (entry("/etc/sudoers", users="nobody"), False),
            (entry("/etc/sudoers", users="root", groups=""), True),
            (entry("/etc/sudoers", users="#0", groups="root"), True),
            (entry("/etc/sudoers", groups="operators"), False),
        )
        for rule, expected in cases:
            with self.subTest(rule=rule):
                self.assertIs(tpot_sudo.inspect_policy(DEFAULTS + rule, ()).alternative, expected)

    def test_restrictions_do_not_masquerade_as_a_safe_fallback(self):
        cases = (
            entry("/etc/sudoers.d/restricted", commands="ALL\n!/bin/sh"),
            entry("/etc/sudoers.d/restricted", users="ALL, !root"),
            entry("/etc/sudoers.d/restricted", options="noexec"),
            entry("/etc/sudoers.d/restricted") + "    NotAfter: 20270101000000Z\n",
        )
        for restriction in cases:
            with self.subTest(restriction=restriction):
                result = tpot_sudo.inspect_policy(DEFAULTS + entry("/etc/sudoers") + restriction, ())
                self.assertIsNone(result.alternative)
                self.assertTrue(result.reason)

    def test_unknown_formats_and_authentication_defaults_are_unverified(self):
        cases = (
            "",
            "unrecognized policy plugin output",
            DEFAULTS + entry("", options="!authenticate"),
            DEFAULTS.replace("env_reset", "rootpw") + entry("/etc/sudoers"),
            DEFAULTS.replace("env_reset", "targetpw") + entry("/etc/sudoers"),
            DEFAULTS.replace("env_reset", "requiretty") + entry("/etc/sudoers"),
        )
        for text in cases:
            with self.subTest(text=text):
                result = tpot_sudo.inspect_policy(text, ())
                self.assertIsNone(result.alternative)
                self.assertTrue(result.reason)

    def test_an_explicit_absence_is_not_an_unknown_plugin_format(self):
        text = "User operator is not allowed to run sudo on hive.\n"
        self.assertIs(tpot_sudo.inspect_policy(text, ()).alternative, False)


class RuleTest(unittest.TestCase):
    def test_rule_uses_uid_not_a_sudoers_keyword_username(self):
        self.assertEqual(tpot_sudo.rule(1001),
                         "# Managed by T-Pot: sudo mode\n#1001 ALL=(ALL:ALL) NOPASSWD: ALL\n")
        for uid in (0, -1, True, "1001"):
            with self.subTest(uid=uid), self.assertRaises(ValueError):
                tpot_sudo.rule(uid)

    def test_only_the_exact_generated_rule_of_this_user_is_owned(self):
        expected = tpot_sudo.rule(1001)
        self.assertTrue(tpot_sudo.owns_rule(expected, 1001))
        for text in (tpot_sudo.rule(1002), expected + "someone ALL=(ALL) ALL\n",
                     "# user file\n#1001 ALL=(ALL:ALL) NOPASSWD: ALL\n"):
            with self.subTest(text=text):
                self.assertFalse(tpot_sudo.owns_rule(text, 1001))


class ControllerTest(unittest.TestCase):
    def test_command_keeps_credentials_out_of_arguments_and_preserves_paths(self):
        from tpotctl import sudo_mode
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory, "a password file")
            secret.write_text("Distant-Otters!swim-47\n")
            secret.chmod(0o600)
            command = sudo_mode.command("secure", "/tmp/sensors inventory", become_file=str(secret),
                                        user_password_file=str(secret), result_file="/tmp/results",
                                        repo_dir="/tmp/a checkout")
        self.assertEqual(command[:4], ["ansible-playbook", "/tmp/a checkout/installer/install/sudo_mode.yml",
                                      "-i", "/tmp/sensors inventory"])
        self.assertNotIn("Distant-Otters!swim-47", " ".join(command))
        variables = json.loads(command[command.index("-e") + 1])
        self.assertEqual(variables["tpot_become_password_file"], str(secret))
        self.assertNotIn("--become-password-file", command)
        self.assertEqual(variables["tpot_sudo_action"], "secure")
        self.assertEqual(variables["tpot_user_password_file"], str(secret))
        self.assertEqual(variables["tpot_sudo_result_file"], "/tmp/results")

    def test_password_file_preserves_unicode_and_spaces_but_rejects_unsafe_files(self):
        from tpotctl import sudo_mode
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "secret")
            path.write_text("  G\u00fcltig:Passwort!  \n", encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(sudo_mode.read_password_file(str(path)), "  G\u00fcltig:Passwort!  ")
            path.chmod(0o644)
            with self.assertRaisesRegex(sudo_mode.SudoError, "chmod 600"):
                sudo_mode.read_password_file(str(path))
            path.chmod(0o700)
            with self.assertRaisesRegex(sudo_mode.SudoError, "chmod 600"):
                sudo_mode.read_password_file(str(path))
            path.chmod(0o600)
            link = Path(directory, "link")
            link.symlink_to(path)
            with self.assertRaises(sudo_mode.SudoError):
                sudo_mode.read_password_file(str(link))
            for value in ("", "first\nsecond\n", "a\0b", "two\n\n"):
                path.write_text(value)
                with self.subTest(value=value), self.assertRaises(sudo_mode.SudoError):
                    sudo_mode.read_password_file(str(path))

    def test_results_preserve_unknown_and_report_failed_hosts(self):
        from tpotctl import sudo_mode
        status = dict(user="operator", uid=1000, own_rule="absent", temporary_rule="absent",
                      password_usable=True, alternative=None, password_required=None,
                      changed=False, changes=[], warnings=["custom policy"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "results")
            path.write_text(json.dumps(dict(schema=1, host="sensor-a", result=status, error="")) + "\n"
                            + json.dumps(dict(schema=1, host="sensor-b", result=None, error="No alternative grant",
                                              changes=["Set the user password."])) + "\n")
            results = sudo_mode.read_results(str(path))
            self.assertIsNone(results["sensor-a"].password_required)
            self.assertIsNone(results["sensor-a"].alternative)
            self.assertEqual(results["sensor-b"].error, "No alternative grant")
            self.assertEqual(results["sensor-b"].changes, ["Set the user password."])
            self.assertTrue(results["sensor-b"].changed)
            status["password_required"] = "false"
            path.write_text(json.dumps(dict(schema=1, host="sensor-a", result=status, error="")) + "\n")
            with self.assertRaises(sudo_mode.SudoError):
                sudo_mode.read_results(str(path))


@unittest.skipUnless(
    os.environ.get("TPOT_TEST_SUDO") == "container-only" and sys.platform == "linux"
    and os.geteuid() == 0 and Path("/.dockerenv").exists(),
    "requires an explicitly opted-in disposable sudo test container",
)
class LinuxModeTest(unittest.TestCase):
    """Real accounts and policy files, only inside the dedicated disposable container."""

    user = "tpot_test_sudo"
    password = "  Distant-\u00d6tters!swim-47  "
    foreign = Path("/etc/sudoers.d/tpot-test-foreign")

    def run_command(self, *args, input=None, check=True):
        return subprocess.run(args, input=input, text=True, capture_output=True, check=check)

    def setUp(self):
        self.original = Path("/etc/sudoers").read_bytes()
        for path in (Path(OWN), Path(TEMP), self.foreign):
            self.assertFalse(path.exists() or path.is_symlink(), f"test path already in use: {path}")
        self.run_command("useradd", "-m", "-s", "/bin/bash", self.user)
        self.addCleanup(self.restore)
        self.uid = pwd.getpwnam(self.user).pw_uid

    def restore(self):
        Path("/etc/sudoers").write_bytes(self.original)
        for path in (Path(OWN), Path(TEMP), self.foreign):
            path.unlink(missing_ok=True)
        self.run_command("userdel", "-r", self.user)

    def grant(self, path=OWN):
        Path(path).write_text(f"# Managed by T-Pot: sudo mode\n#{self.uid} ALL=(ALL:ALL) NOPASSWD: ALL\n")
        Path(path).chmod(0o440)

    def set_password(self):
        self.run_command("chpasswd", input=f"{self.user}:{self.password}\n")

    def groups(self):
        return self.run_command("id", "-Gn", self.user).stdout.split()

    def probe(self, password=None):
        args = ("sudo", "-k", "-p", "", "-n") if password is None else ("sudo", "-k", "-p", "", "-S")
        return self.run_command("runuser", "-u", self.user, "--", *args, "--", "/usr/bin/true",
                                input=None if password is None else password + "\n", check=False)

    def test_status_reports_without_modifying_accounts_or_rules(self):
        before = {name: Path("/etc", name).read_bytes() for name in ("passwd", "shadow", "group", "sudoers")}
        status = tpot_sudo.apply(self.user, "status")
        self.assertEqual(status["own_rule"], "absent")
        self.assertFalse(status["password_usable"])
        self.assertFalse(status["alternative"])
        self.assertFalse(status["changed"])
        self.assertFalse(Path(OWN).exists())
        self.assertEqual(before, {name: Path("/etc", name).read_bytes() for name in before})

    def test_convenient_grants_real_passwordless_sudo_idempotently(self):
        result = tpot_sudo.apply(self.user, "convenient")
        self.assertTrue(result["changed"])
        self.assertEqual(result["own_rule"], "owned")
        self.assertFalse(result["password_required"])
        self.assertEqual(Path(OWN).stat().st_mode & 0o777, 0o440)
        self.assertEqual(Path(OWN).stat().st_uid, 0)
        self.assertEqual(self.probe().returncode, 0)
        before = Path(OWN).stat()
        self.assertFalse(tpot_sudo.apply(self.user, "convenient")["changed"])
        self.assertEqual(Path(OWN).stat().st_mtime_ns, before.st_mtime_ns)

    def test_secure_without_a_password_refuses_before_any_change(self):
        self.grant()
        before, groups = Path(OWN).read_bytes(), self.groups()
        with self.assertRaisesRegex(tpot_sudo.SudoError, "password"):
            tpot_sudo.apply(self.user, "secure")
        self.assertEqual(Path(OWN).read_bytes(), before)
        self.assertEqual(self.groups(), groups)
        self.assertEqual(self.probe().returncode, 0)

    def test_secure_establishes_password_and_group_before_removing_rule(self):
        self.grant()
        result = tpot_sudo.apply(self.user, "secure", password=self.password)
        self.assertEqual(result["own_rule"], "absent")
        self.assertTrue(result["password_usable"])
        self.assertTrue(result["alternative"])
        self.assertTrue(result["password_required"])
        self.assertIn("sudo", self.groups())
        self.assertNotEqual(self.probe().returncode, 0)
        self.assertEqual(self.probe(self.password).returncode, 0)

    def test_secure_does_not_change_a_foreign_nopasswd_grant(self):
        self.grant()
        self.set_password()
        text = f"{self.user} ALL=(ALL:ALL) NOPASSWD: ALL\n"
        self.foreign.write_text(text)
        self.foreign.chmod(0o440)
        result = tpot_sudo.apply(self.user, "secure")
        self.assertEqual(self.foreign.read_text(), text)
        self.assertEqual(result["own_rule"], "absent")
        self.assertFalse(result["password_required"])
        self.assertIn("foreign", " ".join(result["warnings"]).lower())
        self.assertEqual(self.probe().returncode, 0)

    def test_safe_remove_keeps_the_only_sudo_route(self):
        self.grant()
        self.set_password()
        before = Path(OWN).read_bytes()
        result = tpot_sudo.apply(self.user, "safe_remove")
        self.assertFalse(result["changed"])
        self.assertTrue(result["warnings"])
        self.assertEqual(Path(OWN).read_bytes(), before)
        self.assertEqual(self.probe().returncode, 0)

    def test_safe_remove_with_password_and_other_access_removes_rule(self):
        self.grant()
        self.set_password()
        self.run_command("usermod", "-aG", "sudo", self.user)
        result = tpot_sudo.apply(self.user, "safe_remove")
        self.assertTrue(result["changed"])
        self.assertFalse(Path(OWN).exists())
        self.assertEqual(self.probe(self.password).returncode, 0)

    def test_explicit_keep_leaves_the_rule_in_place(self):
        self.grant()
        self.set_password()
        self.run_command("usermod", "-aG", "sudo", self.user)
        result = tpot_sudo.apply(self.user, "safe_remove", keep=True)
        self.assertFalse(result["changed"])
        self.assertTrue(result["warnings"])
        self.assertEqual(self.probe().returncode, 0)

    def test_foreign_file_at_reserved_path_is_never_replaced(self):
        text = "# owned by an administrator\nroot ALL=(ALL) ALL\n"
        Path(OWN).write_text(text)
        Path(OWN).chmod(0o440)
        self.assertEqual(tpot_sudo.apply(self.user, "status")["own_rule"], "foreign")
        for action in ("secure", "convenient"):
            with self.subTest(action=action), self.assertRaisesRegex(tpot_sudo.SudoError, "foreign"):
                tpot_sudo.apply(self.user, action, password=self.password)
            self.assertEqual(Path(OWN).read_text(), text)

    def test_symlink_at_reserved_path_is_not_followed(self):
        text = "# foreign file\n"
        self.foreign.write_text(text)
        Path(OWN).symlink_to(self.foreign)
        with self.assertRaises(tpot_sudo.SudoError):
            tpot_sudo.apply(self.user, "convenient")
        self.assertTrue(Path(OWN).is_symlink())
        self.assertEqual(self.foreign.read_text(), text)

    def test_weak_or_multiline_password_leaves_rule_and_groups_unchanged(self):
        self.grant()
        before, groups = Path(OWN).read_bytes(), self.groups()
        for password in ("weak", "a\nb", "a\rb", "a\0b"):
            with self.subTest(password=password), self.assertRaisesRegex(tpot_sudo.SudoError, "password"):
                tpot_sudo.apply(self.user, "secure", password=password)
        self.assertEqual(Path(OWN).read_bytes(), before)
        self.assertEqual(self.groups(), groups)

    def test_a_group_without_an_actual_grant_cannot_remove_own_rule(self):
        self.grant()
        self.set_password()
        path = Path("/etc/sudoers")
        path.write_text(re.sub(r"(?m)^%sudo.*$", "", path.read_text()))
        before = self.groups()
        with self.assertRaisesRegex(tpot_sudo.SudoError, "alternative"):
            tpot_sudo.apply(self.user, "secure")
        self.assertEqual(self.groups(), before)
        self.assertEqual(self.probe().returncode, 0)

    def test_broken_sudoers_aborts_without_removing_own_rule(self):
        self.grant()
        self.set_password()
        with Path("/etc/sudoers").open("a") as stream:
            stream.write("\ninvalid syntax (\n")
        before = Path(OWN).read_bytes()
        with self.assertRaisesRegex(tpot_sudo.SudoError, "visudo"):
            tpot_sudo.apply(self.user, "secure")
        self.assertEqual(Path(OWN).read_bytes(), before)

    def test_explicit_include_is_restored_if_removal_breaks_validation(self):
        self.grant()
        self.set_password()
        self.run_command("usermod", "-aG", "sudo", self.user)
        with Path("/etc/sudoers").open("a") as stream:
            stream.write("\n@include /etc/sudoers.d/tpot\n")
        with self.assertRaisesRegex(tpot_sudo.SudoError, "visudo"):
            tpot_sudo.apply(self.user, "secure")
        self.assertEqual(self.run_command("visudo", "-c").returncode, 0)
        self.assertEqual(self.probe().returncode, 0)

    def test_temporary_install_grant_is_not_an_alternative_or_removed_early(self):
        self.grant()
        self.grant(TEMP)
        self.set_password()
        status = tpot_sudo.apply(self.user, "status")
        self.assertFalse(status["alternative"])
        result = tpot_sudo.apply(self.user, "secure")
        self.assertEqual(result["temporary_rule"], "owned")
        self.assertTrue(Path(TEMP).exists())
        self.assertFalse(Path(OWN).exists())
        self.assertTrue(result["alternative"])

    def test_root_and_service_accounts_are_refused(self):
        for user in ("root", "nobody"):
            with self.subTest(user=user), self.assertRaises(tpot_sudo.SudoError):
                tpot_sudo.apply(user, "convenient")
        self.assertFalse(Path(OWN).exists())

    def test_system_account_with_a_shell_is_not_a_normal_login_user(self):
        user = "tpot_test_service"
        self.run_command("useradd", "-r", "-s", "/bin/bash", user)
        self.addCleanup(self.run_command, "userdel", user)
        with self.assertRaisesRegex(tpot_sudo.SudoError, "normal"):
            tpot_sudo.apply(user, "convenient")
        self.assertFalse(Path(OWN).exists())

    def test_busy_lock_refuses_without_changing_rules(self):
        self.grant()
        before = Path(OWN).read_bytes()
        fd = os.open("/run/lock/tpot-sudo.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with self.assertRaisesRegex(tpot_sudo.SudoError, "Another"):
                tpot_sudo.apply(self.user, "secure", password=self.password)
        finally:
            os.close(fd)
        self.assertEqual(Path(OWN).read_bytes(), before)

    def test_term_during_rule_commit_finishes_validation_without_losing_access(self):
        self.grant()
        self.set_password()
        self.run_command("usermod", "-aG", "sudo", self.user)
        script = """
import os
from pathlib import Path
import signal
from installer.install.library import tpot_sudo
original = tpot_sudo._run
def interrupt_during_validation(args, **kwargs):
    if args == ["visudo", "-c"] and not Path("/etc/sudoers.d/tpot").exists():
        os.kill(os.getpid(), signal.SIGTERM)
    return original(args, **kwargs)
tpot_sudo._run = interrupt_during_validation
tpot_sudo.apply("tpot_test_sudo", "secure")
"""
        proc = self.run_command("python3", "-", input=script, check=False)
        self.assertEqual(proc.returncode, -15)
        self.assertFalse(Path(OWN).exists())
        self.assertEqual(self.run_command("visudo", "-c").returncode, 0)
        self.assertEqual(self.probe(self.password).returncode, 0)
        self.assertFalse(list(Path("/etc/sudoers.d").glob(".tpot-*")))

    def test_normal_controller_uses_the_password_file_for_become(self):
        self.normal_controller_become()

    def test_controller_can_authenticate_an_existing_whitespace_only_password(self):
        self.password = "    "
        self.normal_controller_become()

    def normal_controller_become(self):
        from tpotctl import sudo_mode
        self.set_password()
        self.run_command("usermod", "-aG", "sudo", self.user)
        with tempfile.TemporaryDirectory() as directory:
            os.chown(directory, self.uid, pwd.getpwnam(self.user).pw_gid)
            secret = Path(directory, "become")
            secret.write_text(self.password + "\n")
            secret.chmod(0o600)
            os.chown(secret, self.uid, pwd.getpwnam(self.user).pw_gid)
            output = Path(directory, "result")
            inventory = Path(directory, "inventory")
            inventory.write_text("localhost ansible_connection=local ansible_python_interpreter=/usr/bin/python3\n")
            play = Path(tpot_sudo.__file__).parent.parent / "sudo_mode.yml"
            script = """
import subprocess, sys
from tpotctl import sudo_mode
command = sudo_mode.command("convenient", sys.argv[1], become_file=sys.argv[2],
                            result_file=sys.argv[3], repo_dir=sys.argv[4])
raise SystemExit(subprocess.call(command + ["-vvv"]))
"""
            proc = self.run_command("runuser", "-u", self.user, "--", "python3", "-",
                                    str(inventory), str(secret), str(output), str(play.parents[2]),
                                    input=script, check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            result = sudo_mode.read_results(str(output))["localhost"]
            self.assertEqual(result.user, self.user)
            self.assertEqual(result.own_rule, "owned")
            self.assertFalse(result.password_required)
            self.assertEqual(output.stat().st_uid, self.uid)
            if self.password.strip():
                self.assertNotIn(self.password, proc.stdout + proc.stderr + output.read_text())
            self.assertEqual(self.probe().returncode, 0)

    def test_play_changes_and_reports_without_exposing_the_password(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory, "secret")
            secret.write_text(self.password + "\n")
            secret.chmod(0o600)
            output = Path(directory, "result")
            play = Path(tpot_sudo.__file__).parent.parent / "sudo_mode.yml"
            self.grant()
            variables = dict(tpot_sudo_user=self.user, tpot_sudo_action="secure",
                             tpot_user_password_file=str(secret), tpot_sudo_result_file=str(output),
                             ansible_python_interpreter="/usr/bin/python3")
            proc = self.run_command("ansible-playbook", str(play), "-i", "localhost,", "-c", "local",
                                    "-e", json.dumps(variables), "-vvv", check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            record = json.loads(output.read_text().strip())
            self.assertEqual(record["schema"], 1)
            self.assertEqual(record["host"], "localhost")
            self.assertEqual(record["result"]["own_rule"], "absent")
            self.assertTrue(record["result"]["password_required"])
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(self.password, proc.stdout + proc.stderr + output.read_text())
            self.assertEqual(self.probe(self.password).returncode, 0)

    def test_install_prepare_sets_password_and_group_without_revoking_access(self):
        self.grant()
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory, "secret")
            secret.write_text(self.password + "\n")
            secret.chmod(0o600)
            play = Path(tpot_sudo.__file__).parent.parent / "sudo_mode.yml"
            variables = dict(tpot_sudo_user=self.user, tpot_sudo_action="secure", tpot_sudo_prepare=True,
                             tpot_user_password_file=str(secret), ansible_python_interpreter="/usr/bin/python3")
            proc = self.run_command("ansible-playbook", str(play), "-i", "localhost,", "-c", "local",
                                    "-e", json.dumps(variables), check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(Path(OWN).exists(), "Preparation must not revoke access needed by the installer.")
        self.assertIn("sudo", self.groups())
        self.assertIn(" P ", self.run_command("passwd", "-S", self.user).stdout)
        self.assertEqual(self.probe().returncode, 0)
        tpot_sudo.apply(self.user, "secure")
        self.assertFalse(Path(OWN).exists())
        self.assertEqual(self.probe(self.password).returncode, 0)

    def test_install_tag_selects_the_shared_sudo_play_not_host_changes(self):
        play = Path(tpot_sudo.__file__).parent.parent / "tpot.yml"
        proc = self.run_command("ansible-playbook", str(play), "-i", "localhost,", "-c", "local",
                                "--tags", "tpot_sudo", "--list-tasks", check=False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Check or change the sudo mode", proc.stdout)
        self.assertNotIn("Install cron without recommends", proc.stdout)
        self.assertNotIn("Setup a randomized daily reboot (All)", proc.stdout)

    def test_play_rejects_readable_password_file_before_changing_account(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory, "secret")
            secret.write_text(self.password + "\n")
            secret.chmod(0o644)
            self.grant()
            before = Path(OWN).read_bytes()
            play = Path(tpot_sudo.__file__).parent.parent / "sudo_mode.yml"
            variables = dict(tpot_sudo_user=self.user, tpot_sudo_action="secure",
                             tpot_user_password_file=str(secret), ansible_python_interpreter="/usr/bin/python3")
            proc = self.run_command("ansible-playbook", str(play), "-i", "localhost,", "-c", "local",
                                    "-e", json.dumps(variables), check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("chmod 600", proc.stdout + proc.stderr)
            self.assertNotIn(self.password, proc.stdout + proc.stderr)
            self.assertEqual(Path(OWN).read_bytes(), before)
            self.assertFalse(tpot_sudo.apply(self.user, "status")["password_usable"])

    def test_play_rejects_an_explicit_empty_password_file_even_if_password_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            self.grant()
            self.set_password()
            secret = Path(directory, "secret")
            secret.touch(mode=0o600)
            variables = dict(tpot_sudo_user=self.user, tpot_sudo_action="secure",
                             tpot_user_password_file=str(secret), ansible_python_interpreter="/usr/bin/python3")
            play = Path(tpot_sudo.__file__).parent.parent / "sudo_mode.yml"
            proc = self.run_command("ansible-playbook", str(play), "-i", "localhost,", "-c", "local",
                                    "-e", json.dumps(variables), check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("nonempty", proc.stdout + proc.stderr)
            self.assertTrue(Path(OWN).exists())

    def test_play_failure_records_partial_changes_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            self.grant()
            path = Path("/etc/sudoers")
            path.write_text(re.sub(r"(?m)^%sudo.*$", "", path.read_text()))
            secret = Path(directory, "secret")
            secret.write_text(self.password + "\n")
            secret.chmod(0o600)
            output = Path(directory, "result")
            variables = dict(tpot_sudo_user=self.user, tpot_sudo_action="secure",
                             tpot_user_password_file=str(secret), tpot_sudo_result_file=str(output),
                             ansible_python_interpreter="/usr/bin/python3")
            play = Path(tpot_sudo.__file__).parent.parent / "sudo_mode.yml"
            proc = self.run_command("ansible-playbook", str(play), "-i", "localhost,", "-c", "local",
                                    "-e", json.dumps(variables), check=False)
            self.assertNotEqual(proc.returncode, 0)
            record = json.loads(output.read_text().strip())
            self.assertIn("alternative", record["error"])
            self.assertTrue(record.get("changes"))
            self.assertNotIn(self.password, proc.stdout + proc.stderr + output.read_text())
            self.assertTrue(Path(OWN).exists())

    def root_installer(self, code=0, extra=""):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        os.chmod(directory.name, 0o755)
        script = Path(directory.name, "install.sh")
        source = Path(__file__).parent / "install.sh"
        if not source.exists():
            source = Path(tpot_sudo.__file__).parents[3] / "install.sh"
        prefix = source.read_text().split("# Begin of Installer:", 1)[0]
        script.write_text(prefix + """
id -u > "$HOME/root-child-uid"
sudo -n -k true || exit 81
[ -f /etc/sudoers.d/tpot-install ] || exit 82
""" + extra + f"\nexit {code}\n")
        script.chmod(0o755)
        return script

    def test_root_handover_runs_as_the_user_and_cleans_its_temporary_rule(self):
        self.set_password()
        script = self.root_installer()
        proc = self.run_command("bash", str(script), "--user", self.user, "-s", "-t", "s", check=False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        marker = Path(pwd.getpwnam(self.user).pw_dir, "root-child-uid")
        self.assertEqual(marker.read_text().strip(), str(self.uid))
        self.assertFalse(Path(TEMP).exists())
        self.assertFalse(Path("/etc/tmpfiles.d/tpot-install.conf").exists())
        self.assertFalse(Path(OWN).exists())
        self.assertIn("sudo", self.groups())

    def test_root_handover_preserves_child_failure_and_removes_temporary_grant(self):
        self.set_password()
        script = self.root_installer(code=7)
        proc = self.run_command("bash", str(script), "--user", self.user, "-s", "-t", "s", check=False)
        self.assertEqual(proc.returncode, 7, proc.stdout + proc.stderr)
        self.assertFalse(Path(TEMP).exists())
        self.assertFalse(Path(OWN).exists())

    def test_root_inline_start_requests_a_saved_script_before_changing_access(self):
        self.set_password()
        script = self.root_installer()
        groups = self.groups()
        proc = self.run_command("bash", "-c", script.read_text(), "install-from-curl",
                                "--user", self.user, "-s", "-t", "s", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Save install.sh", proc.stdout + proc.stderr)
        self.assertEqual(self.groups(), groups)
        self.assertFalse(Path(TEMP).exists())

    def test_root_unattended_locked_user_stops_before_account_or_rule_changes(self):
        before = self.groups()
        script = self.root_installer()
        proc = self.run_command("bash", str(script), "--user", self.user, "-s", "-t", "s", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("user-password-file", proc.stdout + proc.stderr)
        self.assertEqual(self.groups(), before)
        self.assertFalse(Path(TEMP).exists())
        self.assertFalse(Path(OWN).exists())

    def test_root_handover_does_not_overwrite_a_foreign_temporary_rule(self):
        self.set_password()
        text = "root ALL=(ALL:ALL) ALL\n"
        Path(TEMP).write_text(text)
        Path(TEMP).chmod(0o440)
        script = self.root_installer()
        proc = self.run_command("bash", str(script), "--user", self.user, "-s", "-t", "s", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("foreign", (proc.stdout + proc.stderr).lower())
        self.assertEqual(Path(TEMP).read_text(), text)

    def test_root_cleanup_keeps_the_rule_if_removing_it_breaks_sudo(self):
        self.set_password()
        script = self.root_installer(extra="""
printf '\\n@include /etc/sudoers.d/tpot-install\\n' | sudo -n tee -a /etc/sudoers >/dev/null
""")
        proc = self.run_command("bash", str(script), "--user", self.user, "-s", "-t", "s", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(Path(TEMP).exists(), proc.stdout + proc.stderr)
        self.assertEqual(self.run_command("visudo", "-c", check=False).returncode, 0)
        self.assertFalse(Path("/etc/tmpfiles.d/tpot-install.conf").exists())

    def start_root_child(self):
        script = self.root_installer(extra="""
sleep 60 &
printf '%s\\n' "$!" > "$HOME/root-sleep-pid"
wait
""")
        proc = subprocess.Popen(["bash", str(script), "--user", self.user, "-s", "-t", "s"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        self.addCleanup(self.stop_root_child, proc)
        marker = Path(pwd.getpwnam(self.user).pw_dir, "root-sleep-pid")
        deadline = time.monotonic() + 5
        while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists(), "Root handover did not start the waiting child.")
        return proc, int(marker.read_text())

    def stop_root_child(self, proc):
        if proc.poll() is None:
            proc.terminate()
        proc.communicate(timeout=15)

    def test_root_term_stops_the_child_and_cleans_temporary_access(self):
        self.set_password()
        proc, sleeper = self.start_root_child()
        self.assertTrue(Path("/etc/tmpfiles.d/tpot-install.conf").exists())
        proc.send_signal(signal.SIGTERM)
        output, _ = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 130, output)
        self.assertFalse(Path(TEMP).exists())
        self.assertFalse(Path("/etc/tmpfiles.d/tpot-install.conf").exists())
        state = Path(f"/proc/{sleeper}/stat")
        if state.exists():
            self.assertEqual(state.read_text().split(") ", 1)[1][0], "Z")

    def test_a_second_root_installer_does_not_remove_the_active_grant(self):
        self.set_password()
        proc, _ = self.start_root_child()
        before = Path(TEMP).read_bytes()
        second = self.run_command("bash", str(self.root_installer()), "--user", self.user, "-s", "-t", "s",
                                  check=False)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("active", second.stdout + second.stderr)
        self.assertEqual(Path(TEMP).read_bytes(), before)
        self.assertIsNone(proc.poll())

    def test_root_boot_cleanup_removes_only_the_temporary_rule(self):
        self.set_password()
        self.grant()
        before = Path(OWN).read_bytes()
        self.start_root_child()
        self.run_command("systemd-tmpfiles", "--remove", "/etc/tmpfiles.d/tpot-install.conf")
        self.assertFalse(Path(TEMP).exists())
        self.assertEqual(Path(OWN).read_bytes(), before)

    def test_root_copies_private_inputs_without_exposing_them_to_other_users(self):
        self.set_password()
        script = self.root_installer(extra="""
for file in "${BASH_SOURCE[0]}" "$myBECOME_FILE" "$myUSER_PASSWORD_FILE" "$myWEB_PW_FILE"; do
  [ "$(stat -c '%a:%u:%h' "$file")" = "400:$(id -u):1" ] || exit 83
done
cmp "$myBECOME_FILE" "$myUSER_PASSWORD_FILE" || exit 84
printf '%s\\n' "$myBECOME_FILE" > "$HOME/root-copied-password"
""")
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory, "private password")
            secret.write_text(self.password + "\n")
            secret.chmod(0o600)
            proc = self.run_command("bash", str(script), "--user", self.user, "-s", "-t", "h", "-u", "webadmin",
                                    "-P", str(secret), "-B", str(secret), "--user-password-file", str(secret),
                                    check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(secret.read_text(), self.password + "\n")
        copied = Path(pwd.getpwnam(self.user).pw_dir, "root-copied-password").read_text().strip()
        self.assertFalse(Path(copied).exists())
        self.assertNotIn(self.password, proc.stdout + proc.stderr)

    def test_root_child_uses_the_selected_users_directories_not_roots(self):
        self.set_password()
        script = self.root_installer(extra="""
case "${XDG_DATA_HOME:-$HOME/.local/share}" in "$HOME"/*) ;; *) exit 85 ;; esac
case "${XDG_CONFIG_HOME:-$HOME/.config}" in "$HOME"/*) ;; *) exit 86 ;; esac
[ "$PWD" = "$HOME" ] || exit 87
""")
        proc = subprocess.run(["bash", str(script), "--user", self.user, "-s", "-t", "s"],
                              cwd="/root", env=dict(os.environ, XDG_DATA_HOME="/root/data",
                                                   XDG_CONFIG_HOME="/root/config"),
                              text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
