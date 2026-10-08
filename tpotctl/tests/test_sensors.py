"""tpot sensors: registry, access, SSH, certificate and status, on a temporary checkout."""

import base64
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import sensors, users  # noqa: E402
from tpotctl.bootstrap import REPO_DIR  # noqa: E402
from tpotctl.envfile import EnvFile  # noqa: E402

# the UI needs Textual, the CLI Rich
textual = importlib.util.find_spec("textual") and importlib.util.find_spec("rich")

WEB_USER = "dHNlYzokYXByMSRYUnE2SC5rbiRVRjZQM1VVQmJVNWJUQmNmSGRuUFQxCgo="
# two sensors of 24.04.1, deployed with deploy.sh (apr1)
OLD_SENSORS = [users.encode("sensor-old-otter:$apr1$abc$def"), users.encode("sensor-old-lynx:$apr1$ghi$jkl")]
OPENSSL_TEXT = """        X509v3 extensions:
            X509v3 Subject Alternative Name:
                IP Address:192.168.5.15, DNS:hive.example.org
"""


def fake_hash(name, password, repo_dir=None):
    return f"{name}:$2y$05$" + base64.b64encode(password.encode()).decode().rstrip("=")


def checkout(test, ls_web_user=" ".join(OLD_SENSORS)):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    with open(os.path.join(REPO_DIR, "env.example"), encoding="utf-8") as handle:
        text = handle.read().replace("WEB_USER=\n", f"WEB_USER={WEB_USER}\n", 1)
    text = text.replace("LS_WEB_USER=\n", f"LS_WEB_USER={ls_web_user}\n", 1)
    with open(os.path.join(tmp.name, ".env"), "w", encoding="utf-8") as handle:
        handle.write(text)
    for folder in ("conf", "cert"):
        os.makedirs(os.path.join(tmp.name, "data", "nginx", folder))
    open(os.path.join(tmp.name, "data", "nginx", "conf", "lswebpasswd"), "w").close()
    for name in ("nginx.crt", "nginx.key"):
        with open(os.path.join(tmp.name, "data", "nginx", "cert", name), "w") as handle:
            handle.write(f"old {name}\n")
    shutil.copytree(os.path.join(REPO_DIR, "installer"), os.path.join(tmp.name, "installer"))
    return tmp.name


class Child:
    """A child of subprocess.Popen: wait() takes the next step, an exit code or an exception to raise."""

    def __init__(self, command, kwargs, steps):
        self.args, self.kwargs, self.steps, self.returncode = command, kwargs, list(steps), None

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        step = self.steps.pop(0) if self.steps else 0
        if isinstance(step, BaseException) or (isinstance(step, type) and issubclass(step, BaseException)):
            raise step
        self.returncode = step
        return step

    def kill(self):
        self.returncode = self.returncode if self.returncode is not None else -9

    def __enter__(self):            # subprocess.call and run use it so
        return self

    def __exit__(self, *_exc):
        return False


def fake_popen(*steps):
    """A Popen that records its children; each child waits through `steps`."""
    children = []

    def popen(command, **kwargs):
        children.append(Child(command, kwargs, steps))
        return children[-1]
    return popen, children


class TTY(io.StringIO):
    """stdin of a person at a terminal."""

    def isatty(self):
        return True


class HelpersTest(unittest.TestCase):

    def test_names_and_passwords(self):
        name = sensors.new_name({"sensor-a-b"})
        self.assertRegex(name, r"^sensor-[a-z]+-[a-z]+$")
        password = sensors.new_password()
        self.assertEqual(len(password), 32)
        self.assertTrue(password.isalnum())
        self.assertEqual(base64.b64decode(sensors.hive_user("sensor-x-y", "pw")).decode(), "sensor-x-y:pw")

    def test_addresses_and_users(self):
        for good in ("192.168.1.10", "fe80::1", "hive.example.org", "sensor1"):
            self.assertEqual(sensors.check_address(good), good)
        for bad in ("", "a b", "a;reboot", "-oProxyCommand=x", "host:22", "x" * 300):
            with self.assertRaises(sensors.SensorsError):
                sensors.check_address(bad)
        sensors.check_user("tpot_admin")
        for bad in ("", "-l", "root;x", "Admin User"):
            with self.assertRaises(sensors.SensorsError):
                sensors.check_user(bad)

    def test_ssh(self):
        self.assertIn("2222", sensors.ssh_command("10.0.0.5", "debian", port=2222))
        with self.assertRaises(sensors.SensorsError):
            sensors.check_port("0")
        command = sensors.ssh_command("10.0.0.5", "debian", "true")
        self.assertEqual(command[:3], ["ssh", "-p", "64295"])
        self.assertIn("BatchMode=yes", command)
        self.assertIn("debian@10.0.0.5", command)
        for code, stderr, expected in ((0, "", "ok"), (255, "debian@10.0.0.5: Permission denied (publickey).", "key"),
                                       (255, "ssh: connect to host 10.0.0.5 port 64295: Connection refused",
                                        "unreachable")):
            fake = mock.Mock(return_value=mock.Mock(returncode=code, stderr=stderr))
            self.assertEqual(sensors.check_ssh("10.0.0.5", "debian", run=fake), expected)

    def test_deploy_command_and_environment(self):
        command = sensors.deploy_command("10.0.0.5", "debian", repo_dir="/r")
        self.assertEqual(command[:2], ["ansible-playbook", "/r/installer/install/deploy.yml"])
        self.assertIn("10.0.0.5,", command)
        self.assertIn("--ask-become-pass", command)
        self.assertNotIn("--ask-become-pass", sensors.deploy_command("10.0.0.5", "debian", become_pass=False))
        env = sensors.deploy_env("dXNlcjpwdw==", "192.168.1.2", repo_dir="/r")
        self.assertEqual((env["myTPOT_HIVE_USER"], env["myTPOT_HIVE_IP"]), ("dXNlcjpwdw==", "192.168.1.2"))
        self.assertEqual(env["ANSIBLE_LOG_PATH"], "/r/data/deploy_sensor.log")

    def test_distribute_asks_for_sudo(self):
        self.assertIn("--ask-become-pass", sensors.distribute_command("/tmp/inv"))
        self.assertNotIn("--ask-become-pass", sensors.distribute_command("/tmp/inv", become_pass=False))

    def test_inventory(self):
        text = sensors.inventory_text([sensors.Sensor("sensor-a-b", host="10.0.0.5", ssh_user="debian")])
        self.assertIn("sensor-a-b ansible_host=10.0.0.5 ansible_user=debian ansible_port=64295", text)

    def test_certificate_sans(self):
        sans = sensors.parse_sans(OPENSSL_TEXT)
        self.assertEqual(sans, ["IP:192.168.5.15", "DNS:hive.example.org"])
        self.assertTrue(sensors.covers("hive.example.org", sans))
        self.assertFalse(sensors.covers("192.168.64.41", sans))
        command = sensors.renew_command(sans + ["IP:192.168.64.41"], "k", "c")
        self.assertIn("subjectAltName = IP:192.168.5.15,DNS:hive.example.org,IP:192.168.64.41", command)
        self.assertIn("rsa:8192", command)

    @unittest.skipUnless(shutil.which("openssl"), "openssl is not installed")
    def test_certificate_with_openssl(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, cert = os.path.join(tmp, "k"), os.path.join(tmp, "c")
            command = sensors.renew_command(["IP:10.1.2.3", "DNS:hive.test"], key, cert)
            command[command.index("rsa:8192")] = "rsa:2048"                  # quicker, same command otherwise
            self.assertEqual(subprocess.call(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), 0)
            self.assertEqual(sensors.cert_sans(cert), ["IP:10.1.2.3", "DNS:hive.test"])

    def test_status(self):
        def bucket(key, host, ip):
            return {"key": key, "latest": {"hits": {"hits": [{"_source": {
                "@timestamp": "2026-10-04T10:00:00.000Z", "t-pot_hostname": host, "t-pot_ip_ext": ip}}]}}}
        answer = {"aggregations": {"sensors": {"buckets": [bucket("sensor-old-otter", "edge1", "1.2.3.4")]},
                                   "unlinked": {"hosts": {"buckets": [bucket("edge2", "edge2", "5.6.7.8"),
                                                                      bucket("hive", "hive", "9.9.9.9")]}}}}
        status = sensors.parse_status(answer, own_hostname="hive")
        self.assertEqual(status.sensors["sensor-old-otter"].hostname, "edge1")
        self.assertEqual(list(status.unlinked), ["edge2"])
        query = sensors.status_query(7)
        self.assertEqual(query["aggs"]["sensors"]["terms"]["field"], "t-pot_sensor.keyword")
        failing = mock.Mock(side_effect=OSError("connection refused"))
        self.assertIn("cannot be asked", sensors.fetch_status(opener=failing).problem)


class RegistryTest(unittest.TestCase):

    def setUp(self):
        self.repo = checkout(self)
        self.lswebpasswd = os.path.join(self.repo, "data", "nginx", "conf", "lswebpasswd")
        self.inode = os.stat(self.lswebpasswd).st_ino

    def registry(self):
        return sensors.Registry(self.repo, hasher=fake_hash)

    def test_migration_from_ls_web_user(self):
        registry = self.registry()
        self.assertEqual(registry.migrated, ["sensor-old-lynx", "sensor-old-otter"])
        with open(os.path.join(self.repo, "data", "sensors.json")) as handle:
            saved = json.load(handle)
        self.assertEqual([s["name"] for s in saved["sensors"]], ["sensor-old-lynx", "sensor-old-otter"])
        self.assertTrue(all(s["source"] == "migrated" for s in saved["sensors"]))
        self.assertEqual(self.registry().migrated, [])            # only once

    def test_grant_record_revoke(self):
        registry = self.registry()
        self.assertIn("right away", registry.grant("sensor-new-fox", "pw"))
        registry.record(sensors.Sensor("sensor-new-fox", host="10.0.0.5", ssh_user="debian",
                                       hive_address="192.168.1.2", source="deployed"))
        again = self.registry()
        self.assertEqual(again.get("sensor-new-fox").host, "10.0.0.5")
        self.assertEqual(len(again.entries()), 3)
        with open(self.lswebpasswd) as handle:
            self.assertIn(fake_hash("sensor-new-fox", "pw"), handle.read())
        self.assertEqual(os.stat(self.lswebpasswd).st_ino, self.inode, "lswebpasswd has to keep its inode")
        again.revoke("sensor-old-otter")
        names = [u.name for u in self.registry().entries()]
        self.assertEqual(names, ["sensor-old-lynx", "sensor-new-fox"])
        self.assertNotIn("sensor-old-otter", [s.name for s in self.registry().sensors()])
        self.assertEqual(self.registry().hive_addresses(), ["192.168.1.2"])

    def test_refusals(self):
        registry = self.registry()
        with self.assertRaises(sensors.SensorsError):
            registry.grant("sensor-old-otter", "pw")
        with self.assertRaises(sensors.SensorsError):
            registry.revoke("sensor-nobody")
        env = EnvFile(os.path.join(self.repo, ".env"))
        env.set("TPOT_TYPE", "SENSOR")
        env.save()
        with self.assertRaises(sensors.SensorsError):
            self.registry()

    def test_access_flag_for_a_record_without_entry(self):
        registry = self.registry()
        registry.record(sensors.Sensor("sensor-gone-owl", host="10.0.0.9", ssh_user="debian", source="deployed"))
        self.assertFalse(self.registry().get("sensor-gone-owl").access)

    def test_renew_keeps_the_old_certificate(self):
        registry = self.registry()

        def fake_openssl(command, **_kwargs):
            for flag in ("-keyout", "-out"):
                with open(command[command.index(flag) + 1], "w") as handle:
                    handle.write("new\n")
            return mock.Mock(returncode=0, stderr="")
        stamp = sensors.renew_cert(registry, ["IP:10.0.0.1"], run=fake_openssl)
        with open(registry.cert) as handle:
            self.assertEqual(handle.read(), "new\n")
        with open(f"{registry.cert}.bak-{stamp}") as handle:
            self.assertEqual(handle.read(), "old nginx.crt\n")
        failing = mock.Mock(return_value=mock.Mock(returncode=1, stderr="boom"))
        with self.assertRaises(sensors.SensorsError):
            sensors.renew_cert(registry, ["IP:10.0.0.1"], run=failing)

    def test_link_hostnames(self):
        registry = self.registry()
        status = sensors.Status(sensors={"sensor-old-otter": sensors.Seen("2026-10-04T10:00:00Z", "edge1")})
        sensors.link_hostnames(registry, status)
        self.assertEqual(self.registry().get("sensor-old-otter").hostname, "edge1")


@unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
class CliTest(unittest.TestCase):

    def setUp(self):
        self.repo = checkout(self)
        original = self.Registry = sensors.Registry
        self.patches = [
            mock.patch.object(sensors, "Registry", lambda repo_dir=None, hasher=fake_hash:
                              original(repo_dir or self.repo, hasher=hasher)),
            mock.patch.object(sensors, "fetch_status", lambda days=7: sensors.Status(
                sensors={"sensor-old-otter": sensors.Seen("2026-10-04T10:00:00Z", "edge1", "1.2.3.4")})),
            mock.patch("tpotctl.cli.os.geteuid", return_value=1000, create=True),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def run_cli(self, *args, stdin=None):
        from tpotctl import cli
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", stdin or io.StringIO()):
            try:
                code = cli.main(list(args))
            except SystemExit as stop:
                code = stop.code if isinstance(stop.code, int) else 2
                err.write(str(stop.code))
            except KeyboardInterrupt:       # unittest would stop on it
                code = "KeyboardInterrupt"
        return code, out.getvalue() + err.getvalue()

    def test_list_set_remove(self):
        code, text = self.run_cli("sensors", "list")
        self.assertEqual(code, 0)
        self.assertIn("sensor-old-otter", text)
        self.assertIn("Taken over from LS_WEB_USER", text)
        self.assertEqual(self.run_cli("sensors", "set", "sensor-old-lynx", "--host", "10.0.0.7",
                                      "--ssh-user", "debian")[0], 0)
        self.assertEqual(sensors.Registry(self.repo).get("sensor-old-lynx").host, "10.0.0.7")
        self.assertNotEqual(self.run_cli("sensors", "set", "sensor-old-lynx", "--host", "a;b")[0], 0)
        self.assertEqual(self.run_cli("sensors", "remove", "sensor-old-lynx")[0], 2)         # no --yes, cannot ask
        self.assertEqual(self.run_cli("sensors", "remove", "sensor-old-lynx", "--yes")[0], 0)
        self.assertEqual([u.name for u in sensors.Registry(self.repo).entries()], ["sensor-old-otter"])

    def test_ctrl_c_at_the_question_of_remove(self):
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            code, text = self.run_cli("sensors", "remove", "sensor-old-lynx", stdin=TTY())
        self.assertEqual(code, 130, text)
        self.assertIn("Cancelled.", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # nothing revoked

    def test_add_asks_or_says_which_option_it_needs(self):
        code, text = self.run_cli("sensors", "add", "--ssh-user", "debian")
        self.assertEqual(code, 2, text)
        self.assertIn("--host", text)
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            code, text = self.run_cli("sensors", "add", stdin=TTY())
        self.assertEqual(code, 130, text)
        self.assertNotIn("Traceback", text)

    def test_an_ipv6_hive_address_is_refused_before_ssh(self):
        """The HIVE address goes into the .env of the SENSOR (TPOT_HIVE_IP): IPv6 is refused at once."""
        with mock.patch.object(sensors, "check_ssh") as ssh, mock.patch("subprocess.Popen") as popen, \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "fd00::1", stdin=TTY())
        self.assertNotEqual(code, 0, text)
        self.assertIn("fd00::1", text)
        ssh.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # nothing granted

    def test_no_proposal_of_the_hive_address_asks_for_it(self):
        """An SSH alias (sensor_1) has no address of this HIVE on the way to it: no "[]" in the question,
        without a terminal exit 2 (cannot ask) and the option to give it with, an empty answer exit 1."""
        with mock.patch.object(sensors, "check_ssh", return_value="unreachable") as ssh, \
                mock.patch.object(sensors, "default_hive_address", return_value=""):
            code, text = self.run_cli("sensors", "add", "--host", "sensor_1", "--ssh-user", "u")
            self.assertEqual(code, 2, text)
            self.assertIn("--hive-address", text)
            self.assertNotIn("''", text)
            ssh.assert_not_called()
            prompts = []
            for answer in ("", "192.168.1.2"):
                with self.subTest(answer=answer), \
                        mock.patch("builtins.input", side_effect=lambda prompt: prompts.append(prompt) or answer):
                    code, text = self.run_cli("sensors", "add", "--host", "sensor_1", "--ssh-user", "u",
                                              stdin=TTY())
                    self.assertNotIn("[]", prompts[-1])
                    if answer:
                        self.assertEqual(code, 1, text)            # on to SSH, which is not there
                        self.assertIn("cannot log in", text)
                    else:                                   # it did ask: an empty answer is 1, as for --host
                        self.assertEqual(code, 1, text)
                        self.assertIn("nothing given", text)
                        self.assertIn("--hive-address", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # nothing granted

    @unittest.skipIf(os.geteuid() == 0, "root writes a read-only file")
    def test_an_access_that_counts_after_a_restart_is_said(self):
        """lswebpasswd cannot be written in place: nginx knows the sensor only after tpot restart."""
        lswebpasswd = os.path.join(self.repo, "data", "nginx", "conf", "lswebpasswd")
        os.chmod(lswebpasswd, 0o444)
        self.addCleanup(os.chmod, lswebpasswd, 0o644)
        code, text, _children = self.add_with(0)
        self.assertEqual(code, 0, text)
        self.assertIn("tpot restart", text)
        self.assertNotIn("and sends to", text)

    def add_with(self, *steps, ssh="ok"):
        """sensors add at a terminal with the children of fake_popen(*steps); its password is known."""
        popen, children = fake_popen(*steps)
        with mock.patch.object(sensors, "check_ssh", return_value=ssh), \
                mock.patch.object(sensors, "has_ssh_key", return_value=True), \
                mock.patch.object(sensors, "cert_sans", return_value=["IP:192.168.1.2"]), \
                mock.patch.object(sensors, "new_password", return_value="Pw-of-the-sensor-1234"), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=popen):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2", stdin=TTY())
        return code, text, children

    def test_ctrl_c_during_the_playbook_takes_the_access_back(self):
        """The playbook ends by the ^C: the access goes, the password is never shown, exit code 130."""
        code, text, children = self.add_with(KeyboardInterrupt, -2)
        self.assertEqual(children[0].args[0], "ansible-playbook")
        self.assertEqual(code, 130, text)
        self.assertIn("taken back", text)
        self.assertIn("Cancelled.", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # only the migrated ones
        self.assertEqual([s.source for s in sensors.Registry(self.repo).sensors()], ["migrated", "migrated"])

    def test_a_second_ctrl_c_cannot_stop_taking_the_access_back(self):
        import signal
        before, seen = signal.getsignal(signal.SIGINT), []
        original = self.Registry.revoke

        def revoke(registry, name, forget=True):
            seen.append(signal.getsignal(signal.SIGINT))
            return original(registry, name, forget)
        for steps in ((KeyboardInterrupt, -2), (2,)):
            with mock.patch.object(self.Registry, "revoke", revoke):
                self.add_with(*steps)
        self.assertEqual(seen, [signal.SIG_IGN, signal.SIG_IGN])
        self.assertEqual(signal.getsignal(signal.SIGINT), before)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_that_the_playbook_survives_takes_the_access_back(self):
        """ansible-playbook catches the ^C itself and exits 99: still a ^C, 130 and Cancelled., the access goes."""
        code, text, _children = self.add_with(KeyboardInterrupt, 99)
        self.assertEqual(code, 130, text)
        self.assertIn("taken back", text)
        self.assertIn("Cancelled.", text)
        self.assertNotIn("Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def ctrl_c_in(self, method, *steps):
        """sensors add with a real SIGINT to this process right after Registry.<method>."""
        import signal
        original = getattr(self.Registry, method)

        def wrapped(registry, *args, **kwargs):
            result = original(registry, *args, **kwargs)
            os.kill(os.getpid(), signal.SIGINT)
            return result
        before = signal.getsignal(signal.SIGINT)
        with mock.patch.object(self.Registry, method, wrapped):
            code, text, children = self.add_with(*steps)
        self.assertEqual(signal.getsignal(signal.SIGINT), before)               # given back
        return code, text, children

    def test_ctrl_c_right_after_the_grant_starts_no_deployment(self):
        """No window between the grant and what takes it back: the access goes, nothing is deployed."""
        code, text, children = self.ctrl_c_in("grant", 0)
        self.assertEqual(code, 130, text)
        self.assertEqual(children, [])
        self.assertIn("taken back", text)
        self.assertNotIn("Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_as_the_playbook_fails_takes_the_access_back(self):
        """The ^C comes just as the playbook ends with an error, before the access is taken back."""
        import signal
        popen, children = fake_popen(2)

        def late_popen(*args, **kwargs):
            child = popen(*args, **kwargs)
            wait, sent = child.wait, []

            def wait_then_ctrl_c(timeout=None):
                code = wait(timeout)
                if not sent:
                    sent.append(True)
                    os.kill(os.getpid(), signal.SIGINT)
                return code
            child.wait = wait_then_ctrl_c
            return child
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_sans", return_value=["IP:192.168.1.2"]), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=late_popen):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2", stdin=TTY())
        self.assertEqual(len(children), 1)
        self.assertEqual(code, 130, text)
        self.assertIn("taken back", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_while_taking_the_access_back_cannot_stop_it(self):
        code, text, _children = self.ctrl_c_in("revoke", 2)
        self.assertNotEqual(code, 0, text)
        self.assertIn("taken back", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_while_noting_a_deployed_sensor_keeps_it(self):
        code, text, _children = self.ctrl_c_in("record", 0)
        self.assertEqual(code, 0, text)
        self.assertIn("shown only now: Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 3)

    def test_a_playbook_that_cannot_start_takes_the_access_back(self):
        code, text, _children = self.add_with(FileNotFoundError(2, "No such file or directory", "ansible-playbook"))
        self.assertEqual(code, 1, text)
        self.assertIn("taken back", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_any_other_break_before_the_deployment_ends_takes_the_access_back(self):
        with mock.patch.object(sensors, "deploy_env", side_effect=RuntimeError("a bug")), \
                self.assertRaises(RuntimeError):
            self.add_with(0)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_a_deployed_sensor_keeps_its_access_if_it_cannot_be_noted(self):
        """The sensor works and sends: a registry that cannot be written (or a .env that cannot be read for
        the version) only costs its details, the access stays and the password is shown."""
        from tpotctl import ops
        for target, name in ((self.Registry, "record"), (ops, "env_values")):
            with self.subTest(failing=name):
                before = {u.name for u in sensors.Registry(self.repo).entries()}
                failure = sensors.SensorsError("cannot write data/sensors.json")
                with mock.patch.object(target, name, side_effect=failure):
                    code, text, _children = self.add_with(0)
                self.assertEqual(code, 0, text)
                self.assertIn("shown only now: Pw-of-the-sensor-1234", text)
                self.assertIn("cannot write data/sensors.json", text)
                self.assertIn("tpot sensors set", text)
                self.assertNotIn("taken back", text)
                added = {u.name for u in sensors.Registry(self.repo).entries()} - before
                self.assertEqual(len(added), 1, text)

    def test_no_key_login_without_a_terminal_cannot_ask(self):
        """ssh-copy-id asks for the password of the sensor: without a terminal exit code 2."""
        popen, children = fake_popen(0)
        with mock.patch.object(sensors, "check_ssh", return_value="key"), \
                mock.patch.object(sensors, "has_ssh_key", return_value=True), \
                mock.patch.object(sensors, "cert_sans", return_value=["IP:192.168.1.2"]), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=popen):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2")
        self.assertEqual(code, 2, text)
        self.assertIn("ssh-copy-id", text)
        self.assertEqual(children, [])
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_at_ssh_copy_id_is_cancelled(self):
        code, text, children = self.add_with(KeyboardInterrupt, -2, ssh="key")
        self.assertEqual(children[0].args[0], "ssh-copy-id")
        self.assertEqual(code, 130, text)
        self.assertIn("Cancelled.", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # nothing granted

    def test_add_grants_first_and_takes_back_on_failure(self):
        popen, children = fake_popen(0)
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_sans", return_value=["IP:192.168.1.2"]), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=popen):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2", "--no-become-pass")
        self.assertEqual(code, 0, text)
        password = re.search(r"shown only now: (\S+)", text).group(1)
        deployed = [s for s in sensors.Registry(self.repo).sensors() if s.source == "deployed"]
        self.assertEqual(len(deployed), 1)
        self.assertEqual((deployed[0].host, deployed[0].hive_address), ("10.0.0.5", "192.168.1.2"))
        command, kwargs = children[0].args, children[0].kwargs
        self.assertEqual(command[0], "ansible-playbook")
        self.assertNotIn("--ask-become-pass", command)
        self.assertEqual(base64.b64decode(kwargs["env"]["myTPOT_HIVE_USER"]).decode(),
                         f"{deployed[0].name}:{password}")
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_sans", return_value=["IP:192.168.1.2"]), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=fake_popen(2)[0]):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.6", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2")
        self.assertEqual(code, 1)
        self.assertIn("taken back", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 3)      # 2 migrated + 1 deployed

    def test_add_stops_without_ssh_and_with_uncovered_certificate(self):
        with mock.patch.object(sensors, "check_ssh", return_value="unreachable"):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2")
        self.assertEqual(code, 1)
        self.assertIn("cannot log in", text)
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_sans", return_value=["IP:192.168.5.15"]):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2")
        self.assertNotEqual(code, 0)
        self.assertIn("does not cover 192.168.1.2", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)     # nothing granted

    def test_cert_distribute(self):
        registry = sensors.Registry(self.repo)
        registry.record(sensors.Sensor("sensor-new-fox", host="10.0.0.5", ssh_user="debian", source="deployed"))
        popen, children = fake_popen(0)
        with mock.patch.object(sensors, "cert_sans", return_value=["IP:192.168.5.15"]), \
                mock.patch("subprocess.Popen", side_effect=popen):
            code, text = self.run_cli("sensors", "cert", "--distribute")
        self.assertIn("skipped", text)                                        # migrated ones have no host
        self.assertEqual(code, 1)                                             # sensor-new-fox has no access
        self.assertEqual(children, [])

    def test_ctrl_c_during_the_distribution_is_cancelled(self):
        registry = sensors.Registry(self.repo)
        registry.grant("sensor-new-fox", "pw")
        registry.record(sensors.Sensor("sensor-new-fox", host="10.0.0.5", ssh_user="debian", source="deployed"))
        popen, children = fake_popen(KeyboardInterrupt, -2)
        with mock.patch.object(sensors, "cert_sans", return_value=["IP:192.168.5.15"]), \
                mock.patch("subprocess.Popen", side_effect=popen):
            code, text = self.run_cli("sensors", "cert", "--distribute", "sensor-new-fox", stdin=TTY())
        self.assertEqual(children[0].args[0], "ansible-playbook")
        self.assertEqual(code, 130, text)
        self.assertNotIn("Traceback", text)
        inventory = children[0].args[children[0].args.index("-i") + 1]
        self.assertFalse(os.path.exists(inventory))                           # the inventory goes as well


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class SensorsPaneTest(unittest.IsolatedAsyncioTestCase):

    async def test_list_deploy_remove(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import ConfirmDialog, SensorDialog
        repo = checkout(self)

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def images(self):
                return []

            def backups(self):
                return []

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={"sensor-old-otter": sensors.Seen("2026-10-04T10:00:00Z", "edge1")})

        commands = []
        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: commands.append(command) or 0)
        with mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"):
            async with app.run_test(size=(150, 45)) as pilot:
                await pilot.pause(0.3)
                app.query_one("ContentSwitcher").current = "sensors"
                await pilot.pause(0.5)
                table = app.query_one("#sensors-table")
                self.assertEqual(table.row_count, 2)
                await pilot.click("#sensor-add")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, SensorDialog)
                app.screen.query_one("#sensor-host").value = "10.0.0.5"
                app.screen.query_one("#sensor-user").value = "debian"
                await pilot.click("#sensor-deploy")
                await pilot.pause(0.3)
                self.assertEqual(commands[-1][1:6], ["sensors", "add", "--host", "10.0.0.5", "--ssh-user"])
                table.move_cursor(row=0)
                await pilot.click("#sensor-remove")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, ConfirmDialog)
                await pilot.click("#yes")
                await pilot.pause(0.5)
        self.assertEqual([u.name for u in sensors.Registry(repo).entries()], ["sensor-old-otter"])

    async def test_the_dialog_proposes_only_a_hive_address_it_found(self):
        from textual.app import App
        from tpotctl.screens.dialogs import SensorDialog
        for found in ("", "192.168.1.2"):
            with self.subTest(found=found):
                dialog = SensorDialog(sensors.check_address, sensors.check_user, lambda _host: found,
                                      sensors.check_hive_address)
                app = App()
                async with app.run_test() as pilot:
                    app.push_screen(dialog)
                    await pilot.pause()
                    dialog.query_one("#sensor-host").value = "sensor_1"
                    await pilot.pause()
                    placeholder = dialog.query_one("#sensor-hive").placeholder
                self.assertNotIn("[]", placeholder)
                if found:
                    self.assertIn(f"[{found}]", placeholder)

    async def test_edit_where_a_sensor_is(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import SensorEditDialog
        repo = checkout(self)

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={})

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(0.3)
            app.goto("sensors")
            await pilot.pause(0.5)
            table = app.query_one("#sensors-table")
            table.move_cursor(row=0)
            name = app.query_one("#sensors").selected()
            await pilot.click("#sensor-edit")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, SensorEditDialog)
            app.screen.query_one("#edit-host").value = "10.0.0.9"
            app.screen.query_one("#edit-port").value = "2222"
            await pilot.click("#edit-save")
            await pilot.pause(0.4)
        sensor = sensors.Registry(repo).get(name)
        self.assertEqual((sensor.host, sensor.ssh_port), ("10.0.0.9", 2222))

    async def test_enter_on_a_sensor_row_offers_its_actions(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import ChoiceDialog, SensorEditDialog
        repo = checkout(self)

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={})

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(0.3)
            app.goto("sensors")
            await pilot.pause(0.5)
            table = app.query_one("#sensors-table")
            table.focus()
            table.move_cursor(row=1)
            name = app.query_one("#sensors").selected()
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ChoiceDialog)
            self.assertTrue(name in app.screen.title_text, app.screen.title_text)
            await pilot.press("escape")
            await pilot.pause(0.3)
            self.assertIs(app.focused, table)
            await pilot.press("enter")
            await pilot.pause(0.3)
            await pilot.press("enter")              # the first one: Edit
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, SensorEditDialog)

    async def test_row_action_stays_on_its_row(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import ChoiceDialog, SensorEditDialog
        repo = checkout(self)
        notes = []

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={})

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        app.notify = lambda message, **kwargs: notes.append(message)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(0.3)
            app.goto("sensors")
            await pilot.pause(0.6)
            pane = app.query_one("#sensors")
            table = app.query_one("#sensors-table")
            table.focus()
            table.move_cursor(row=1)
            name = pane.selected()
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ChoiceDialog)
            table.move_cursor(row=0)                    # as if a reload had moved the cursor
            await pilot.press("enter")                  # Edit
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, SensorEditDialog)
            self.assertEqual(app.screen.sensor.name, name)
            await pilot.press("escape")
            await pilot.pause(0.3)
            table.focus()
            table.move_cursor(row=1)
            name = pane.selected()
            await pilot.press("enter")
            await pilot.pause(0.3)
            sensors.Registry(repo, hasher=fake_hash).revoke(name)       # gone meanwhile
            pane.load()
            await pilot.pause(0.5)
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertNotIsInstance(app.screen, SensorEditDialog)
            self.assertTrue(any("no longer there" in n for n in notes), notes)

    async def test_revoke_loads_the_sensors_once(self):
        from tpotctl import app as tapp, ops
        repo = checkout(self)
        calls = []

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def config_stamp(self):
                import threading
                import time
                if threading.current_thread() is not threading.main_thread():
                    time.sleep(0.3)             # a slow worker: the stamp it notes comes late
                return ops.config_stamp(repo)

            def sensors(self):
                calls.append(1)
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={})

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)

        async def no_settings_refresh():        # it takes long enough to hide the race otherwise
            return None
        app.refresh_config = no_settings_refresh
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(0.3)
            app.goto("sensors")
            await pilot.pause(0.8)
            before = len(calls)
            app.query_one("#sensors-table").move_cursor(row=0)
            await pilot.click("#sensor-remove")
            await pilot.pause(0.3)
            await pilot.click("#yes")
            await pilot.pause(1.0)
        self.assertEqual(len(calls) - before, 1, calls)

    def test_sensor_pages_only_on_a_hive(self):
        from tpotctl import app as tapp

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "SENSOR"

        app = tapp.TpotApp(backend=Backend(), runner=None)
        # on a SENSOR both pages stay in the menu, locked with the reason
        self.assertIn("sensors", app.locked)
        self.assertIn("users", app.locked)
        self.assertNotIn("status", app.locked)


if __name__ == "__main__":
    unittest.main()
