"""What tpot sensors add (and deploy.sh, test_scripts_deploy.py) takes as the address of a SENSOR, the
address of this HIVE and the SSH user on the SENSOR.

- A SENSOR address goes to ssh and Ansible: an IPv4 or IPv6 address (no zone, %eth0 is no address
  of another host) or a host name, an alias of ~/.ssh/config with an _ too. Digits and dots only
  are a mistyped IPv4 address, no host name (999.1.1.1, 1.2.3, 123).
- The HIVE address is TPOT_HIVE_IP of the SENSOR: tpotinit checks it there (env_validate.sh fuHOST,
  envschema `host`) and Logstash sends to https://${TPOT_HIVE_IP}:64294, an IPv6 address without
  brackets in that URL cannot work. So an IPv4 address or a host name tpotinit takes, an IPv6
  HIVE is refused with the way out: its host name.
- The SSH user is a Linux user name, capitals included (useradd of Fedora, RHEL and openSUSE takes
  them), but never tpot: that name is the system user of the containers (uid 2000, /bin/false, no
  home) on every installed SENSOR, nobody logs in as tpot.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

from tpotctl.tests import isolate

isolate()

from tpotctl import sensors  # noqa: E402
from tpotctl.bootstrap import REPO_DIR  # noqa: E402
from tpotctl.tests.test_sensors import checkout, fake_hash  # noqa: E402

try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None

# addresses of every kind, the good and the bad ones of both checks
CANDIDATES = ["10.0.0.2", "0.0.0.0", "255.255.255.255", "256.1.1.1", "999.1.1.1", "1.2.3.04", "010.0.0.1",
              "1.2.3", "1.2.3.4.5", "123", "1.2.3.", ".1.2.3", "::", "::1", "1::", "fd00::1", "fd00::2",
              "1:2:3:4:5:6:7:8", "::ffff:192.0.2.1", "fe80::1%eth0", "[::1]", "1::2::3", "g::1", "sensor1",
              "sensor_1", "my_host.lan", "my-host.example.com", "hive.example.org", "-host", "host-", "a..b", ".a",
              "a.", "x" * 63, "x" * 64, ".".join(["x" * 63] * 3) + "." + "x" * 61, ".".join(["x" * 63] * 4),
              "1a.example", "example.com -e x=1", "x;rm 1.2.3.4", "host:22", "höst.example.com", "", " ",
              "a b", "-oProxyCommand=x"]


def accepts(check, value):
    try:
        check(value)
        return True
    except sensors.SensorsError:
        return False


def tpotinit_takes(values):
    """What fuHOST of env_validate.sh (tpotinit on the SENSOR) says for TPOT_HIVE_IP: its own regexes."""
    with open(os.path.join(REPO_DIR, "docker", "tpotinit", "dist", "bin", "env_validate.sh"),
              encoding="utf-8") as handle:
        text = handle.read()
    function = re.search(r"^fuHOST\(\) \{\n(.*?)\n\}", text, re.M | re.S).group(1)
    regexes = "\n".join(line.replace("local ", "", 1) for line in function.splitlines()
                        if re.match(r"\s*local my(IPV4|IPV6|DOMAIN)=", line))
    script = (regexes + '\nwhile IFS= read -r v; do\n  if [[ ${v} =~ ${myIPV4} ]] || [[ ${v} =~ ${myIPV6} ]] '
              '|| [[ ${v} =~ ${myDOMAIN} ]]; then echo 1; else echo 0; fi\ndone\n')
    result = subprocess.run(["bash", "-c", script], input="".join(v + "\n" for v in values),
                            capture_output=True, universal_newlines=True, timeout=60)
    assert result.stderr == "", result.stderr
    return [line == "1" for line in result.stdout.splitlines()]


class SensorAddressTest(unittest.TestCase):

    @unittest.skipUnless(yaml, "PyYAML is not installed, run with the venv of tpot")
    def test_deployment_stamps_the_remote_compose_after_copying_it(self):
        from tpotctl import generated
        with open(os.path.join(REPO_DIR, "installer", "install", "deploy.yml"), encoding="utf-8") as handle:
            tasks = yaml.safe_load(handle)[0]["tasks"]
        stamps = [(i, task["ansible.builtin.command"]["argv"]) for i, task in enumerate(tasks)
                  if "stamp-compose" in task.get("ansible.builtin.command", {}).get("argv", [])]
        self.assertEqual(len(stamps), 1, "deployment must stamp the compose file it replaces")
        index, argv = stamps[0]
        copy_index = next(i for i, task in enumerate(tasks)
                          if task.get("ansible.builtin.command", {}).get("cmd", "").startswith("cp "))
        self.assertGreater(index, copy_index)
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "compose"))
            text = "# T-Pot: SENSOR\nservices: {}\n"
            path = os.path.join(root, "docker-compose.yml")
            for filename in (path, os.path.join(root, "compose", "sensor.yml")):
                with open(filename, "w", encoding="utf-8") as out:
                    out.write(text)
            command = [arg.replace("{{ remote_generated_path }}", generated.__file__)
                       .replace("{{ remote_docker_compose_path }}", path).replace("~/tpotce", root)
                       for arg in argv]
            command[0] = sys.executable
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(path, encoding="utf-8") as handle:
                self.assertTrue(generated.configuration_is_current(handle.read(), root, "compose"))

    def test_ip_addresses_and_host_names(self):
        for good in ("10.0.0.2", "255.255.255.255", "fd00::2", "::1", "fe80::1", "::ffff:192.0.2.1",
                     "my-host.example.com", "sensor1", "1a.example", "x" * 63):
            with self.subTest(address=good):
                self.assertEqual(sensors.check_address(good), good)
                self.assertEqual(sensors.check_address(f"  {good} "), good)

    def test_digits_and_dots_only_are_no_host_name(self):
        """A mistyped IPv4 address, as deploy.sh says: no host name of digits and dots only."""
        for wrong in ("999.1.1.1", "256.1.1.1", "1.2.3", "123", "1.2.3.4.5", "010.0.0.1", "1.2.3.04", "1.2.3."):
            with self.subTest(address=wrong):
                with self.assertRaises(sensors.SensorsError):
                    sensors.check_address(wrong)

    def test_no_zone(self):
        """fe80::1%eth0 is an address of this host's link, Python takes it, deploy.sh does not."""
        with self.assertRaises(sensors.SensorsError):
            sensors.check_address("fe80::1%eth0")

    def test_an_ssh_alias_with_an_underscore(self):
        """A SENSOR named sensor_1 (a host name of ~/.ssh/config, a name in /etc/hosts) can be joined."""
        for good in ("sensor_1", "my_host.lan", "_sensor"):
            with self.subTest(address=good):
                self.assertEqual(sensors.check_address(good), good)
                self.assertIn(f"admin@{good}", sensors.ssh_command(good, "admin"))
                self.assertIn(f"{good},", sensors.deploy_command(good, "admin", repo_dir="/x"))

    def test_nothing_that_breaks_a_command(self):
        for wrong in ("", " ", "a b", "a;reboot", "-oProxyCommand=x", "-host", "host:22", "x" * 300, "[::1]",
                      "example.com -e x=1", "höst.example.com", "a..b", "x" * 64):
            with self.subTest(address=wrong):
                with self.assertRaises(sensors.SensorsError):
                    sensors.check_address(wrong)


class HiveAddressTest(unittest.TestCase):

    def test_ipv4_addresses_and_host_names(self):
        for good in ("10.0.0.1", "192.168.1.10", "hive.example.org", "hive", "x" * 63):
            with self.subTest(address=good):
                self.assertEqual(sensors.check_hive_address(good), good)
                self.assertEqual(sensors.check_hive_address(f" {good}  "), good)

    def test_ipv6_is_refused_with_the_way_out(self):
        """Logstash on the SENSOR sends to https://<address>:64294, without brackets an IPv6 address
        breaks that URL, and tpotinit rejects ::ffff:192.0.2.1: the host name of the HIVE instead."""
        for wrong in ("fd00::1", "::1", "2001:db8::1", "::ffff:192.0.2.1", "fe80::1"):
            with self.subTest(address=wrong):
                with self.assertRaises(sensors.SensorsError) as raised:
                    sensors.check_hive_address(wrong)
                self.assertIn("IPv6", str(raised.exception))
                # r2-RA 3: the containers of the SENSOR have no IPv6 (default bridge) and do not read the
                # /etc/hosts of the host: only a name with an A record works, an IPv6-only HIVE not at all
                self.assertIn("IPv4 (A) record", str(raised.exception))
                self.assertIn("IPv6-only HIVE cannot take sensors yet", str(raised.exception))

    def test_what_tpotinit_rejects_is_refused(self):
        for wrong in ("sensor_1", "999.1.1.1", "1.2.3", "a b", "-host", "fe80::1%eth0", "", "x" * 64):
            with self.subTest(address=wrong):
                with self.assertRaises(sensors.SensorsError):
                    sensors.check_hive_address(wrong)

    def test_every_hive_address_it_takes_passes_tpotinit(self):
        """Whatever tpot sensors add writes as TPOT_HIVE_IP, tpotinit on the SENSOR starts with it."""
        taken = [value for value in CANDIDATES if accepts(sensors.check_hive_address, value)]
        self.assertIn("hive.example.org", taken)
        self.assertIn("10.0.0.2", taken)
        for value, ok in zip(taken, tpotinit_takes(taken)):
            with self.subTest(address=value):
                self.assertTrue(ok, value)

    @unittest.skipUnless(yaml, "PyYAML is not installed, run with the venv of tpot")
    def test_every_hive_address_it_takes_passes_the_schema(self):
        from tpotctl import envschema
        rule = envschema.load_schema()["TPOT_HIVE_IP"]
        for value in CANDIDATES:
            if accepts(sensors.check_hive_address, value):
                with self.subTest(address=value):
                    values = {"TPOT_TYPE": "SENSOR", "TPOT_HIVE_IP": value}
                    self.assertEqual(envschema.check(rule, values, {}, check_host=False), [])

    def test_the_playbook_and_the_registry_take_the_hive_check(self):
        """deploy.yml writes TPOT_HIVE_IP from deploy_env; tpot sensors set / Edit keep it the same way."""
        with open(os.path.join(REPO_DIR, "installer", "install", "deploy.yml"), encoding="utf-8") as handle:
            playbook = handle.read()
        self.assertIn("Check the runtime configuration exists on the sensor", playbook)
        self.assertIn("my_sensor_env.stat.isreg", playbook)
        self.assertNotIn("create: yes", playbook)
        self.assertEqual(sensors.deploy_env("dXNlcg==", "10.0.0.1", repo_dir="/x")["myTPOT_HIVE_IP"], "10.0.0.1")
        for wrong in ("fd00::1", "sensor_1"):
            with self.subTest(address=wrong):
                with self.assertRaises(sensors.SensorsError):
                    sensors.deploy_env("dXNlcg==", wrong, repo_dir="/x")
                registry = sensors.Registry(checkout(self), hasher=fake_hash)
                with self.assertRaises(sensors.SensorsError):
                    registry.update("sensor-old-lynx", hive_address=wrong)
                self.assertEqual(registry.get("sensor-old-lynx").hive_address, "")
        registry = sensors.Registry(checkout(self), hasher=fake_hash)
        self.assertEqual(registry.update("sensor-old-lynx", hive_address="hive.example.org").hive_address,
                         "hive.example.org")
        # the SENSOR itself may be an IPv6 address or an alias
        self.assertEqual(registry.update("sensor-old-lynx", host="fd00::2").host, "fd00::2")
        self.assertEqual(registry.update("sensor-old-lynx", host="sensor_1").host, "sensor_1")


class UserTest(unittest.TestCase):

    def test_linux_user_names_with_capitals(self):
        """Marco: useradd of Fedora, RHEL and openSUSE takes capitals, such a SENSOR can be joined."""
        for good in ("Marco", "Admin", "Tpot", "tpot2", "mytpot", "tpot.admin-1", "_svc", "tpot_admin", "a" * 32,
                     "A" * 32):
            with self.subTest(user=good):
                self.assertEqual(sensors.check_user(good), good)

    def test_tpot_is_the_system_user_of_the_containers_and_no_login(self):
        """tpot is uid 2000 of the containers (/bin/false, no home) on every installed SENSOR: ssh as tpot
        can only end late, with the key refused. It is refused here, with the reason and the way out, and
        only that exact name: Linux names are case sensitive, Tpot or tpot2 are other users."""
        with self.assertRaises(sensors.SensorsError) as caught:
            sensors.check_user("tpot")
        for word in ("'tpot'", "system user", "2000", "T-Pot was installed with"):
            self.assertIn(word, str(caught.exception))

    def test_every_place_that_takes_the_user_refuses_tpot(self):
        sensor = sensors.Sensor("sensor-old-lynx", host="10.0.0.5", ssh_user="tpot")
        for place, call in (("ssh", lambda: sensors.ssh_command("10.0.0.5", "tpot", "true")),
                            ("ssh-copy-id", lambda: sensors.copy_id_command("10.0.0.5", "tpot")),
                            ("ansible", lambda: sensors.deploy_command("10.0.0.5", "tpot")),
                            ("inventory", lambda: sensors.inventory_text([sensor]))):
            with self.subTest(place=place):
                with self.assertRaisesRegex(sensors.SensorsError, "system user"):
                    call()
        registry = sensors.Registry(checkout(self), hasher=fake_hash)
        with self.assertRaisesRegex(sensors.SensorsError, "system user"):
            registry.update("sensor-old-lynx", ssh_user="tpot")
        self.assertNotEqual(registry.get("sensor-old-lynx").ssh_user, "tpot")

    def test_nothing_that_breaks_a_command(self):
        for wrong in ("", "a b", "-l", "-oProxyCommand=x", "root;id", "Admin User", "1admin", "a" * 33, "a@b",
                      "ärger", "tpot\n", "tpot\r", "\ntpot", "tpot\nroot"):
            with self.subTest(user=wrong):
                with self.assertRaises(sensors.SensorsError):
                    sensors.check_user(wrong)

    def test_a_trailing_newline_is_refused_like_deploy_sh_does(self):
        """r2-RA 4: $ of re.match matches before a final newline, so "admin\n" went through (tpot sensors
        set --ssh-user, a hand-edited sensors.json) and split the inventory line of sensors cert
        --distribute; fuCHECK_USER of deploy.sh ([[ =~ ]], $ only at the end) refuses it."""
        from tpotctl.tests.test_scripts_deploy import body
        functions = "\n".join(re.findall(r"^fu(?:TRIM|IS_\w+|CHECK_\w+) \(\) \{\n.*?^\}$", body("deploy.sh"),
                                         re.M | re.S))
        for value in ("admin\n", "admin", "admin\n\n"):
            with self.subTest(user=value):
                bash = subprocess.run(["bash", "-c", functions + '\nfuCHECK_USER "$1"', "x", value],
                                      capture_output=True, universal_newlines=True, timeout=60).returncode == 0
                self.assertEqual(accepts(sensors.check_user, value), bash)
        registry = sensors.Registry(checkout(self), hasher=fake_hash)
        with self.assertRaises(sensors.SensorsError):
            registry.update("sensor-old-lynx", ssh_user="admin\n")
        self.assertNotIn("\n", registry.get("sensor-old-lynx").ssh_user)

    def test_no_host_name_check_takes_a_trailing_newline(self):
        for value in ("host\n", "host.example.org\n", "sensor_1\n", "123\n"):
            with self.subTest(address=value):
                self.assertFalse(sensors._host_name(value))
                self.assertFalse(sensors._host_name(value, "_"))


if __name__ == "__main__":
    unittest.main()
