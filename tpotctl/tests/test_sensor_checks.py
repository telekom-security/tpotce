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
  them).
"""

import os
import re
import subprocess
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
                self.assertIn("host name", str(raised.exception))

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
        for good in ("Marco", "Admin", "tpot", "tpot.admin-1", "_svc", "tpot_admin", "a" * 32, "A" * 32):
            with self.subTest(user=good):
                self.assertEqual(sensors.check_user(good), good)

    def test_nothing_that_breaks_a_command(self):
        for wrong in ("", "a b", "-l", "-oProxyCommand=x", "root;id", "Admin User", "1admin", "a" * 33, "a@b",
                      "ärger"):
            with self.subTest(user=wrong):
                with self.assertRaises(sensors.SensorsError):
                    sensors.check_user(wrong)


if __name__ == "__main__":
    unittest.main()
