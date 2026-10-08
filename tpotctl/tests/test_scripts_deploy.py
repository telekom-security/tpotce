"""deploy.sh and genuser.sh without tpot: the steps they take when the T-Pot Manager cannot be set
up (their fallback) and their help there.

The harness of test_scripts.FallbackTest: a temporary HOME with a ~/tpotce (.env, installer/lib/ui.sh)
and stubs for docker, ansible-playbook, htpasswd and shuf, an awk that reads $HOME/os-release instead
of the host's and a gum for the runs at a terminal. Nothing calls the docker, ssh, Ansible or htpasswd
of the host, nothing touches the real checkout, its .env or ~/.config/tpotce.
"""

import base64
import os
import re
import shutil
import stat
import subprocess
import unittest

from tpotctl.tests import test_scripts as base
from tpotctl.tests import test_ui as ui
from tpotctl.tests.test_sensor_checks import CANDIDATES, tpotinit_takes

REPO = base.REPO
AWK = shutil.which("awk") or "/usr/bin/awk"
GUM_VERSION = re.search(r'myUI_GUM_VERSION="([^"]+)"', base.read("installer/lib/ui.sh")).group(1)
HASH = "$2y$05$hashofthenewsensor"
STUBS = {
    # the os-release of the test, not the one of the host
    "awk": "#!/bin/sh\nn=$#\nwhile [ \"$n\" -gt 0 ]; do\n  a=$1; shift; n=$((n - 1))\n"
           "  [ \"$a\" = /etc/os-release ] && a=\"$HOME/os-release\"\n  set -- \"$@\" \"$a\"\ndone\n"
           f"exec {AWK} \"$@\"\n",
    "shuf": "#!/bin/sh\necho honey\n",
    # the password on stdin, the name last, as deploy.sh calls it
    "htpasswd": "#!/bin/sh\nread -r pw\necho \"$*\" >> \"$HOME/htpasswd.args\"\n"
                "for a in \"$@\"; do name=$a; done\nprintf '%s:%s\\n' \"$name\" '" + HASH + "'\n",
    # one argument per line, and the two values deploy.yml reads from the environment
    "ansible-playbook": "#!/bin/sh\n{ echo ansible-playbook; for a in \"$@\"; do echo \"  arg $a\"; done\n"
                        "  echo \"  env myTPOT_HIVE_IP=$myTPOT_HIVE_IP\"\n"
                        "  echo \"  env myTPOT_HIVE_USER=$myTPOT_HIVE_USER\"; } >> \"$HOME/calls\"\n"
                        "exit \"$(cat \"$HOME/ansible.rc\" 2>/dev/null || echo 0)\"\n",
}
# a gum of the pinned version that styles plain text: the T-Pot logo shows at a terminal
GUM = ("#!/bin/sh\ncase \"$1\" in\n"
       f"  --version) echo 'gum version v{GUM_VERSION}' ;;\n"
       "  style) while [ \"$#\" -gt 0 ] && [ \"$1\" != \"--\" ]; do shift; done; shift\n"
       "         for a in \"$@\"; do echo \"$a\"; done ;;\n"
       "  confirm) exit 1 ;;\n"
       "esac\nexit 0\n")
# the answers of a deployment: SENSOR installed, its user, its address, SSH key there, this HIVE
ANSWERS = ("y", "admin", "10.0.0.2", "y", "10.0.0.1")
SENSOR = "sensor-honey-honey"


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        out.write(text)
    os.chmod(path, mode)


def body(path):
    """The script without its plain fallback block: what the script itself does."""
    text = base.read(path)
    return text[:text.index("# >>> plain fallback")] + text[text.index("# <<< plain fallback"):]


def array(path, name):
    """The items of a bash array literal name=("a" "b" ...) of a script."""
    match = re.search(rf"^\s*{name}=\((.*)\)\s*$", base.read(path), re.M)
    return re.findall(r'"([^"]*)"', match.group(1)) if match else None


class Harness(base.Harness):
    """A HIVE in a temporary HOME: ~/tpotce with .env and ui.sh, a Debian 13, the stubs, no tpot."""

    def setUp(self):
        super().setUp()
        for name, text in STUBS.items():
            write(os.path.join(self.bin, name), text, 0o755)
        self.tpotce = os.path.join(self.home, "tpotce")
        self.env_file = os.path.join(self.tpotce, ".env")
        write(os.path.join(self.tpotce, "installer", "lib", "ui.sh"), base.read("installer/lib/ui.sh"))
        write(self.env_file, "TPOT_TYPE=HIVE\nTPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\n")
        os.makedirs(os.path.join(self.tpotce, "data", "nginx", "conf"))
        self.os_release("Debian GNU/Linux")
        self.data = os.path.join(self.home, ".local", "share")
        write(os.path.join(self.data, "tpotce", "bin", "gum"), GUM, 0o755)

    def os_release(self, name):
        write(os.path.join(self.home, "os-release"), f'NAME="{name}"\nVERSION_ID="13"\n')

    def without_ui_sh(self):
        os.remove(os.path.join(self.tpotce, "installer", "lib", "ui.sh"))

    def calls(self):
        path = os.path.join(self.home, "calls")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def env_text(self):
        with open(self.env_file, encoding="utf-8") as handle:
            return handle.read()

    def lswebpasswd(self):
        with open(os.path.join(self.tpotce, "data", "nginx", "conf", "lswebpasswd"), encoding="utf-8") as handle:
            return handle.read()

    def run_with(self, script, answers=(), *args):
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off")
        environment.pop("TPOT_MARKS", None)
        return subprocess.run(["bash", os.path.join(REPO, script)] + list(args),
                              input="".join(a + "\n" for a in answers), capture_output=True,
                              universal_newlines=True, env=environment, cwd=self.home, timeout=60)

    def deploy(self, *answers, rc=0):
        """deploy.sh without tpot, its answers on stdin, a playbook that ends with rc."""
        write(os.path.join(self.home, "ansible.rc"), f"{rc}\n")
        return self.run_with("deploy.sh", answers or ANSWERS)

    def both(self):
        """Each test with installer/lib/ui.sh and with the plain fallback block."""
        for ui_sh in (True, False):
            with self.subTest(ui_sh=ui_sh):
                if not ui_sh:
                    self.without_ui_sh()
                yield ui_sh

    def assert_deployed(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("### The SENSOR is deployed", result.stdout)

    def assert_asked_again(self, result):
        """A wrong answer was asked again: the next one went on, the playbook ran."""
        self.assert_deployed(result)
        self.assertIn("ansible-playbook", self.calls())

    def args(self):
        """The arguments of the playbook run, one per item."""
        return re.findall(r"^  arg (.*)$", self.calls(), re.M)


class DeploySummaryTest(Harness):
    """S1: the summary names the HIVE the SENSOR sends its logs to."""

    def test_the_summary_names_the_hive_address(self):
        for _ in self.both():
            result = self.deploy()
            self.assert_deployed(result)
            self.assertIn(f"### [OK] - 10.0.0.2 sends its logs to 10.0.0.1 as {SENSOR}", result.stdout)
            self.assertIn("env myTPOT_HIVE_IP=10.0.0.1", self.calls())

    def test_a_failed_playbook_changes_nothing(self):
        for _ in self.both():
            result = self.deploy(rc=2)
            self.assertEqual(result.returncode, 1)
            self.assertIn("### [FAILED] - The deployment playbook failed", result.stdout)
            self.assertNotIn("LS_WEB_USER", self.env_text())
            self.assertFalse(os.path.exists(os.path.join(self.tpotce, "data", "nginx", "conf", "lswebpasswd")))


class DeployAddressTest(Harness):
    """N13/N5: what goes to Ansible is an address and a user name as sensors.check_address /
    check_user take them, each one argument."""

    def answers(self, user="admin", sensor="10.0.0.2", hive="10.0.0.1"):
        return "y", user, sensor, "y", hive

    def test_an_address_with_more_in_it_is_asked_again(self):
        for wrong in ("example.com -e x=1", "x;rm 1.2.3.4", "1.2.3.4;id", "-e.example.com", "999.1.1.1",
                      "1.2.3", "a..b", "[::1]", "1:2:3:4:5:6:7:8:9", "1::2::3",
                      "a" * 64 + ".example.com", ".".join(["a" * 63] * 4) + ".com", "h\u00f6st.example.com",
                      "fe80::1%eth0", "010.0.0.1", "123"):
            with self.subTest(address=wrong):
                result = self.deploy("y", "admin", wrong, "10.0.0.2", "y", "10.0.0.1")
                self.assertIn("### [WARNING] - Invalid IP/domain", result.stdout)
                self.assert_asked_again(result)
                self.assertIn("-i\n10.0.0.2,", "\n".join(self.args()))
                self.assertNotIn(wrong, self.calls())
                os.remove(os.path.join(self.home, "calls"))
        for wrong in ("example.com -e x=1", "x;rm 1.2.3.4", "999.1.1.1", "1.2.3", "123", "fe80::1%eth0",
                      "host_name.example.com", "sensor_1", "fd00::1", "::1", "::ffff:192.0.2.1"):
            with self.subTest(hive=wrong):
                result = self.deploy("y", "admin", "10.0.0.2", "y", wrong, "10.0.0.1")
                self.assertIn("### [WARNING] - Invalid IP/domain", result.stdout)
                self.assert_asked_again(result)
                self.assertIn("env myTPOT_HIVE_IP=10.0.0.1", self.calls())
                os.remove(os.path.join(self.home, "calls"))

    def test_an_ipv6_hive_is_asked_again_with_the_way_out(self):
        """RA6: Logstash on the SENSOR sends to https://${TPOT_HIVE_IP}:64294, an IPv6 address breaks that
        URL (and tpotinit rejects ::ffff:192.0.2.1): the question says IPv4 or a name, the warning no IPv6."""
        result = self.deploy("y", "admin", "10.0.0.2", "y", "fd00::1", "hive.example.org")
        self.assert_asked_again(result)
        # r2-RA 3: a name works only with an A record (no IPv6 in the containers of the SENSOR)
        self.assertIn("### [WARNING] - Invalid IP/domain: an IPv4 address or a name with an A record, no IPv6.",
                      result.stdout)
        self.assertIn('fuASK_VALID "Enter the IPv4 address or the domain name of this HIVE:"', body("deploy.sh"))
        self.assertIn("env myTPOT_HIVE_IP=hive.example.org", self.calls())

    def test_host_names_and_addresses_go_through(self):
        for good in ("my-host.example.com", "sensor1", "10.0.0.2", "255.255.255.255", "a" * 63 + ".example.com",
                     ".".join(["a" * 63] * 3) + ".example"):
            with self.subTest(address=good):
                result = self.deploy(*self.answers(sensor=good, hive=good))
                self.assertNotIn("Invalid IP/domain", result.stdout)
                self.assert_deployed(result)
                self.assertIn(f"{good},", self.args())
                self.assertIn(f"env myTPOT_HIVE_IP={good}", self.calls())
                self.assertIn(f"### [OK] - {good} sends its logs to {good} as", result.stdout)
                os.remove(os.path.join(self.home, "calls"))

    def test_a_sensor_on_ipv6_or_an_ssh_alias_goes_through(self):
        """RA7: the SENSOR address goes to ssh and Ansible only: IPv6 works there, and an alias of
        ~/.ssh/config or /etc/hosts may have an _ (sensor_1)."""
        for good in ("fd00::2", "::1", "2001:db8:0:0:0:0:0:1", "::ffff:192.0.2.1", "sensor_1", "my_host.lan"):
            with self.subTest(address=good):
                result = self.deploy(*self.answers(sensor=good))
                self.assertNotIn("Invalid IP/domain", result.stdout)
                self.assert_deployed(result)
                self.assertIn(f"{good},", self.args())
                self.assertIn("env myTPOT_HIVE_IP=10.0.0.1", self.calls())
                os.remove(os.path.join(self.home, "calls"))

    def test_spaces_around_an_answer_are_left_out(self):
        result = self.deploy(*self.answers(sensor="  10.0.0.2 ", hive=" 10.0.0.1  ", user=" admin "))
        self.assert_deployed(result)
        self.assertEqual(self.args()[self.args().index("-i") + 1], "10.0.0.2,")
        self.assertEqual(self.args()[self.args().index("-u") + 1], "admin")
        self.assertIn("env myTPOT_HIVE_IP=10.0.0.1\n", self.calls())

    def test_a_user_name_with_more_in_it_is_asked_again(self):
        for wrong in ("a b", "-oProxyCommand=x", "root;id", "1admin", "a" * 33, "a@b"):
            with self.subTest(user=wrong):
                result = self.deploy("y", wrong, "admin", "10.0.0.2", "y", "10.0.0.1")
                self.assertIn("### [WARNING] - Invalid user name", result.stdout)
                self.assert_asked_again(result)
                self.assertEqual(self.args()[self.args().index("-u") + 1], "admin")
                os.remove(os.path.join(self.home, "calls"))

    def test_user_names_with_dots_and_dashes_go_through(self):
        for good in ("tpot.admin-1", "_svc", "a" * 32, "Marco", "Admin"):
            with self.subTest(user=good):
                result = self.deploy(*self.answers(user=good))
                self.assert_deployed(result)
                self.assertEqual(self.args()[self.args().index("-u") + 1], good)
                os.remove(os.path.join(self.home, "calls"))

    def test_an_empty_answer_ends_it(self):
        """No endless questions at the end of the input (Ctrl+D, Esc in gum): it stops before Ansible."""
        for answers in (("y",), ("y", "admin"), ("y", "admin", "10.0.0.2", "y")):
            with self.subTest(answers=answers):
                result = self.deploy(*answers)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("### [ERROR] - ", result.stderr)
                self.assertNotIn("ansible-playbook", self.calls())

    def bash_checks(self, check, values):
        """fuCHECK_* of deploy.sh (with the functions it calls) for each value: True when it passes."""
        functions = "\n".join(re.findall(r"^fu(?:TRIM|IS_\w+|CHECK_\w+) \(\) \{\n.*?^\}$", body("deploy.sh"),
                                         re.M | re.S))
        script = functions + "\nwhile IFS= read -r a; do if \"$0\" \"$a\"; then echo 1; else echo 0; fi; done\n"
        result = subprocess.run(["bash", "-c", script, check], input="".join(v + "\n" for v in values),
                                capture_output=True, universal_newlines=True, timeout=60)
        self.assertEqual(result.stderr, "")
        return [line == "1" for line in result.stdout.splitlines()]

    def test_the_checks_are_the_ones_of_tpot_sensors_add(self):
        """fuCHECK_ADDRESS / fuCHECK_HIVE_ADDRESS / fuCHECK_USER of deploy.sh say what
        sensors.check_address / check_hive_address / check_user say, every case (K, RA6, RA7)."""
        from tpotctl import sensors
        addresses = CANDIDATES + ["1:2:3:4:5:6:7::", "1:2:3:4:5:6:7:8::", "1:2:3:4:5:6:7", "::ffff:1.2.3.4",
                                  "::ffff:1.2.3.256", "1:::2", ":1::2", "12345::1", "a_b", "host_name.example.com",
                                  "_sensor"]
        users = ["admin", "tpot.admin-1", "_svc", "a" * 32, "a" * 33, "Admin", "Marco", "A" * 32, "1admin", "a b",
                 "-o", "root;id", "a@b", ""]

        def python(check, value):
            try:
                check(value)
                return True
            except sensors.SensorsError:
                return False

        for function, check, values in (("fuCHECK_ADDRESS", sensors.check_address, addresses),
                                        ("fuCHECK_HIVE_ADDRESS", sensors.check_hive_address, addresses),
                                        ("fuCHECK_USER", sensors.check_user, users)):
            for value, ok in zip(values, self.bash_checks(function, values)):
                with self.subTest(check=function, value=value):
                    # fuASK_VALID hands the answer over without the spaces around it
                    self.assertEqual(ok, python(check, value) and value.strip() == value)

    def test_every_hive_address_it_takes_passes_tpotinit(self):
        """RA6: what deploy.sh writes as TPOT_HIVE_IP, tpotinit on the SENSOR starts with (fuHOST)."""
        taken = [v for v, ok in zip(CANDIDATES, self.bash_checks("fuCHECK_HIVE_ADDRESS", CANDIDATES)) if ok]
        self.assertIn("10.0.0.2", taken)
        self.assertIn("hive.example.org", taken)
        self.assertNotIn("fd00::1", taken)
        self.assertEqual([v for v, ok in zip(taken, tpotinit_takes(taken)) if not ok], [])

    def test_the_arguments_are_quoted(self):
        # the command with its continuation lines
        commands = body("deploy.sh").replace("\\\n", " ").splitlines()
        line = next(line for line in commands if re.match(r"\s*(\S+=\S+\s+)*ansible-playbook\b", line))
        self.assertIn('-i "${mySENSOR_IP},"', line)
        self.assertIn('-u "${mySSHUSER}"', line)
        # every expansion within double quotes: one argument each, whatever it holds
        self.assertNotIn("$", re.sub(r'"(?:[^"\\]|\\.)*"', '""', line))


class DeployDistributionTest(Harness):
    """N2: the HIVE runs on the distributions install.sh supports, the message names them all."""

    def test_the_distributions_are_the_ones_of_install_sh(self):
        self.assertEqual(array("deploy.sh", "mySUPPORTED_DISTRIBUTIONS"),
                         array("install.sh", "mySUPPORTED_DISTRIBUTIONS"))

    def test_red_hat_enterprise_linux_is_supported(self):
        self.os_release("Red Hat Enterprise Linux")
        result = self.deploy()
        self.assert_deployed(result)

    def test_the_message_names_every_distribution(self):
        self.os_release("Arch Linux")
        result = self.deploy()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("ansible-playbook", self.calls())
        for name in array("install.sh", "mySUPPORTED_DISTRIBUTIONS"):
            self.assertIn(name, result.stderr)

    def test_the_same_sentence_as_install_and_uninstall(self):
        """RA10: deploy.sh, install.sh and uninstall.sh name the distributions in one sentence, "..., Rocky
        Linux and Ubuntu.", for an unsupported one (Gentoo)."""
        names = array("install.sh", "mySUPPORTED_DISTRIBUTIONS")
        sentence = f"Only the following distributions are supported: {', '.join(names[:-1])} and {names[-1]}."
        self.os_release("Gentoo")
        write(os.path.join(self.bin, "uname"), base.UNAME, 0o755)
        for script in ("install.sh", "uninstall.sh"):
            write(os.path.join(self.tpotce, script), base.read(script), 0o755)
        runs = {"deploy.sh": self.deploy(),
                "install.sh": self.run_with(os.path.join(self.tpotce, "install.sh"), (), "-s", "-t", "s"),
                "uninstall.sh": self.run_with(os.path.join(self.tpotce, "uninstall.sh"), (), "-y")}
        for script, result in runs.items():
            with self.subTest(script=script):
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"### [ERROR] - {sentence}", result.stdout + result.stderr)


class DeployCredentialsTest(Harness):
    """N3: no htpasswd hash in the output, neither the new one nor the ones of other sensors; the
    password of the new sensor is shown once and never goes into an argument."""

    OLD = "sensor-old-one:$apr1$oldsalt$oldhashoftheoldsensor"

    def test_no_hash_in_the_output(self):
        old = base64.b64encode(self.OLD.encode()).decode()
        write(self.env_file, self.env_text() + f"LS_WEB_USER={old}\n")
        result = self.deploy()
        self.assert_deployed(result)
        out = result.stdout + result.stderr
        new = base64.b64encode(f"{SENSOR}:{HASH}".encode()).decode()
        for secret in (HASH, "oldhashoftheoldsensor", new, old):
            self.assertNotIn(secret, out)
        self.assertEqual(self.lswebpasswd(), f"{self.OLD}\n{SENSOR}:{HASH}\n")

    def test_the_password_is_shown_once_and_stays_out_of_argv(self):
        result = self.deploy()
        self.assert_deployed(result)
        password = re.search(r"New SENSOR password: (\S+)", result.stdout).group(1)
        self.assertRegex(password, r"^[A-Za-z0-9]{32}$")
        self.assertEqual((result.stdout + result.stderr).count(password), 1)
        self.assertNotIn(password, "\n".join(self.args()))
        with open(os.path.join(self.home, "htpasswd.args"), encoding="utf-8") as handle:
            self.assertNotIn(password, handle.read())
        hive_user = base64.b64encode(f"{SENSOR}:{password}".encode()).decode()
        self.assertIn(f"env myTPOT_HIVE_USER={hive_user}", self.calls())
        self.assertNotIn(hive_user, result.stdout + result.stderr)


class DeployEnvTest(Harness):
    """N4: only a HIVE (not a comment that says so), LS_WEB_USER is written even when it is missing."""

    def entry(self):
        return base64.b64encode(f"{SENSOR}:{HASH}".encode()).decode()

    def test_a_commented_hive_is_no_hive(self):
        for text in ("#TPOT_TYPE=HIVE\nTPOT_TYPE=SENSOR\n", "# TPOT_TYPE=HIVE\n", "MY_TPOT_TYPE=HIVE\n",
                     "TPOT_TYPE=HIVEX\n"):
            with self.subTest(env=text):
                write(self.env_file, text)
                result = self.deploy()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("only supported on HIVE", result.stderr)
                self.assertNotIn("ansible-playbook", self.calls())

    def test_a_quoted_hive_is_a_hive(self):
        for text in ('TPOT_TYPE="HIVE"\n', "TPOT_TYPE='HIVE'\n", "TPOT_TYPE=HIVE # the hive\n"):
            with self.subTest(env=text):
                write(self.env_file, text)
                self.assert_deployed(self.deploy())

    def test_a_missing_ls_web_user_line_is_added(self):
        for _ in self.both():
            write(self.env_file, "TPOT_TYPE=HIVE\nWEB_USER=x\n")
            self.assert_deployed(self.deploy())
            self.assertEqual(self.env_text(), f"TPOT_TYPE=HIVE\nWEB_USER=x\nLS_WEB_USER={self.entry()}\n")
            self.assertEqual(self.lswebpasswd(), f"{SENSOR}:{HASH}\n")

    def test_an_empty_ls_web_user_is_filled_in_place(self):
        write(self.env_file, "TPOT_TYPE=HIVE\nLS_WEB_USER=\nWEB_USER=x\n")
        self.assert_deployed(self.deploy())
        self.assertEqual(self.env_text(), f"TPOT_TYPE=HIVE\nLS_WEB_USER={self.entry()}\nWEB_USER=x\n")

    def test_the_quotes_mode_and_the_rest_of_the_env_stay(self):
        old = base64.b64encode(b"sensor-old-one:$apr1$x$y").decode()
        text = f'# the HIVE\nTPOT_TYPE=HIVE\nLS_WEB_USER="{old}"\nWEB_USER=\'a b\'\n\nTPOT_BLACKHOLE=DISABLED'
        write(self.env_file, text, 0o640)
        self.assert_deployed(self.deploy())
        self.assertEqual(self.env_text(), text.replace(f'"{old}"', f'"{old} {self.entry()}"') + "\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.env_file).st_mode), 0o640)


class DeployLookTest(Harness):
    """Cosmetics: the indentation of the file, plain quotes, 'an SSH key', the canonical y/n question."""

    def test_no_tabs_and_no_typographic_quotes(self):
        text = body("deploy.sh")
        self.assertEqual([line for line in text.splitlines() if line.startswith("\t")], [])
        self.assertEqual(re.findall(r"[‘’“”]", text), [])
        self.assertNotRegex(text, r"\ba SSH\b")
        self.assertIn("an SSH key", text)
        self.assertIn("'BECOME password'", text)
        self.assertIn("'sudo'", text)

    def test_the_blocks_are_indented_like_the_rest(self):
        """then / else two spaces in from their if, their lines two more: as in the other T-Pot scripts."""
        lines = body("deploy.sh").splitlines()
        opened = then = None
        for number, line in enumerate(lines, 1):
            match = re.match(r"^(.*?)\b(?:if|elif)\s", line)
            if match and not line.lstrip().startswith("#") and line.rstrip().endswith(";"):
                opened = len(match.group(1))
            if line.strip() not in ("then", "else"):
                continue
            indent = len(line) - len(line.lstrip())
            following = lines[number]
            with self.subTest(line=number, text=line):
                if line.strip() == "then":
                    self.assertEqual(indent, opened + 2)
                    then = indent
                else:
                    self.assertEqual(indent, then)
                if not following.lstrip().startswith("#"):
                    self.assertEqual(len(following) - len(following.lstrip()), indent + 2, following)

    def test_yes_is_asked_again(self):
        """y or n only: 'yes' is not an answer of fuUI_CONFIRM, the question comes again."""
        for _ in self.both():
            result = self.deploy("yes", "y", "admin", "10.0.0.2", "y", "10.0.0.1")
            self.assert_asked_again(result)
            os.remove(os.path.join(self.home, "calls"))
            result = self.deploy("yes", "n")
            self.assertEqual(result.returncode, 1)
            self.assertIn("A T-Pot SENSOR must be installed to continue.", result.stderr)

    def test_the_questions_fit_the_terminal(self):
        """Every literal of a question has 72 characters at most (${...} / $(...) count as 12)."""
        for script in ("deploy.sh", "genuser.sh"):
            for match in re.finditer(r"\b(?:fuUI_(?:CONFIRM|INPUT|CHOOSE(?:_MANY)?)|fuASK_VALID)\s+\"((?:[^\"\\]|\\.)*)\"",
                                     body(script)):
                text = re.sub(r"\$\{[^}]*\}|\$\([^)]*\)", "x" * 12, match.group(1))
                with self.subTest(script=script, question=match.group(1)):
                    self.assertLessEqual(len(text), 72)


def tpot_options(*command):
    """The options of a sub-command of tpot from its argparse parser: flags, options with a value, the
    ones with a number and how many arguments it takes."""
    import argparse
    from tpotctl import cli
    parser = cli.build_parser()
    for word in command:
        parser = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices[word]
    flags, values, numbers, names = set(), set(), set(), 0
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if not action.option_strings:
            names += 1 if action.nargs in (None, "?") else 99
        elif action.nargs == 0:
            flags.update(action.option_strings)
        else:
            values.update(action.option_strings)
            if action.type is int:
                numbers.update(action.option_strings)
    return flags, values, numbers, names


def wrapper_options(script):
    """The same from the tables of the wrapper (myTPOT_FLAGS, myTPOT_VALUES, myTPOT_NUMBERS, myTPOT_NAMES)."""
    text = body(script)

    def words(name):
        return set(re.search(rf'^{name}="([^"]*)"$', text, re.M).group(1).split())
    return words("myTPOT_FLAGS"), words("myTPOT_VALUES"), words("myTPOT_NUMBERS"), int(re.search(r"^myTPOT_NAMES=(\d+)$", text,
                                                                                   re.M).group(1))


class WrapperOptionsTest(Harness):
    """RA11 and K: deploy.sh and genuser.sh check the options of the command of the T-Pot Manager they hand
    over to: a wrong one is a usage error with exit 1 before the logo, as in the other T-Pot scripts;
    without tpot an option meant for it is an error naming that command, not left out silently."""

    CASES = (("deploy.sh", ("sensors", "add")), ("genuser.sh", ("users", "add")))

    def with_tpot(self):
        write(os.path.join(self.tpotce, "tpot"),
              "#!/bin/sh\n[ \"$1\" = setup ] && exit 0\necho \"tpot $*\" >> \"$HOME/calls\"\n", 0o755)

    def test_the_options_are_the_ones_of_tpot(self):
        for script, command in self.CASES:
            with self.subTest(script=script):
                self.assertEqual(wrapper_options(script), tpot_options(*command))

    def test_a_wrong_option_is_a_usage_error(self):
        self.with_tpot()
        cases = (("deploy.sh", ["--bogus"], "Unknown option --bogus."),
                 ("deploy.sh", ["--host"], "Option --host requires a value."),
                 ("deploy.sh", ["--host", "-y"], "Option --host requires a value."),
                 ("deploy.sh", ["--ssh-port", "abc"], "--ssh-port takes a number, not abc."),
                 ("deploy.sh", ["--ssh-port=22x"], "--ssh-port takes a number, not 22x."),
                 # r2-RA 6: the empty value is named, not "not ."
                 ("deploy.sh", ["--ssh-port="], "--ssh-port takes a number, not an empty value."),
                 ("deploy.sh", ["--ssh-port", ""], "--ssh-port takes a number, not an empty value."),
                 ("deploy.sh", ["10.0.0.2"], "Unexpected argument 10.0.0.2."),
                 ("deploy.sh", ["-x"], "Unknown option -x."),
                 ("genuser.sh", ["--bogus"], "Unknown option --bogus."),
                 ("genuser.sh", ["alice", "bob"], "Unexpected argument bob."),
                 ("genuser.sh", ["--password"], "Unknown option --password."))
        for script, args, text in cases:
            with self.subTest(script=script, args=args):
                result = self.run_with(script, (), *args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"### [ERROR] - {text}", result.stderr)
                self.assertIn(f"{script} -h shows the options.", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(self.calls(), "")

    def test_both_wrappers_name_an_empty_number_the_same_way(self):
        """genuser.sh has no number option today, its parser is the one of deploy.sh (r2-RA 6)."""
        for script in ("deploy.sh", "genuser.sh"):
            with self.subTest(script=script):
                self.assertTrue('takes a number, not ${myVALUE:-an empty value}.' in base.read(script), script)

    def test_no_logo_before_a_usage_error(self):
        self.with_tpot()
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.home, "LANG": "en_US.UTF-8",
               "XDG_CONFIG_HOME": os.path.join(self.home, "config"), "XDG_DATA_HOME": self.data,
               "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        for script, _command in self.CASES:
            with self.subTest(script=script):
                out = ui.plain(ui.at_terminal(f"bash '{os.path.join(REPO, script)}' --bogus; echo \"rc=$?\"", env,
                                              120, 49, source="/dev/null", timeout=60))
                self.assertIn("Unknown option --bogus.", out)
                self.assertIn("rc=1", out)
                self.assertNotIn("telekom security", out)
                self.assertNotIn("T-Pot Sensor deploy", out)
                self.assertNotIn("T-Pot Web user", out)
                self.assertEqual(self.calls(), "")

    def test_good_options_are_handed_over_unchanged(self):
        self.with_tpot()
        cases = (("deploy.sh", ["--host", "10.0.0.2", "--ssh-user=admin", "-y", "--no-become-pass", "--ssh-port",
                                "2222", "--hive-address", "hive.example.org", "--yes"]),
                 ("deploy.sh", []), ("genuser.sh", ["alice", "--password-stdin", "--allow-weak"]),
                 ("genuser.sh", ["--", "-alice"]), ("genuser.sh", []))
        for script, args in cases:
            with self.subTest(script=script, args=args):
                result = self.run_with(script, (), *args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                command = " ".join(dict(self.CASES)[script])
                self.assertEqual(self.calls(), " ".join(["tpot", command] + args) + "\n")
                os.remove(os.path.join(self.home, "calls"))

    def test_without_tpot_options_for_it_are_a_usage_error(self):
        """No tpot (cannot be set up): deploy.sh / genuser.sh ask as before, an option or a name for tpot is
        not left out silently: an error naming the command, before any question, docker or Ansible."""
        for _ in self.both():
            for script, args, command in (("deploy.sh", ["--host", "10.0.0.2"], "tpot sensors add"),
                                          ("deploy.sh", ["-y"], "tpot sensors add"),
                                          ("genuser.sh", ["alice"], "tpot users add"),
                                          ("genuser.sh", ["--password-stdin"], "tpot users add")):
                with self.subTest(script=script, args=args):
                    result = self.run_with(script, ANSWERS, *args)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn(f"### [ERROR] - {args[0]} needs {command}, which cannot be set up here.",
                                  result.stderr)
                    self.assertIn(f"{script} -h shows the options.", result.stderr)
                    self.assertNotIn("(y/n)", result.stdout + result.stderr)
                    self.assertEqual(self.calls(), "")
                    self.assertNotIn("Sensor deploy", result.stdout)


class ReadmeTest(unittest.TestCase):
    """The README says what tpot sensors add / deploy.sh take as addresses, and that the wrappers check
    the options of tpot like the other scripts (RA6, RA11)."""

    def test_the_hive_address_is_ipv4_or_a_host_name(self):
        readme = base.read("README.md")
        section = readme[readme.index("### Deploying Sensors"):]
        section = section[:section.index("\n### ", 1)]
        self.assertIn("IPv4 address or a host name", section)
        self.assertIn("IPv6", section)
        self.assertIn("https://<address>:64294", section)
        # r2-RA 3: a name with only an AAAA record does not work either (compose/sensor.yml: the default
        # bridge without enable_ipv6, the containers do not read the /etc/hosts of the host)
        self.assertNotIn("for a **Hive** on IPv6 give its host name", section)
        self.assertIn("IPv4 (A) record", section)
        self.assertIn("IPv6-only **Hive** cannot take sensors yet", section)

    def test_a_wrong_option_of_the_wrappers(self):
        readme = base.read("README.md")
        section = readme[readme.index("\n## The T-Pot Scripts\n"):]
        section = section[:section.index("\n## ", 1)]
        self.assertIn("`genuser.sh` and `deploy.sh` check the options of the `tpot` command they hand over to",
                      section)


class FallbackHelpTest(Harness):
    """N1: without tpot, -h / --help of deploy.sh and genuser.sh is a short help that names the
    command of the T-Pot Manager; no question, no docker, no Ansible."""

    CASES = (("deploy.sh", "tpot sensors add", "T-Pot Sensor deploy"),
             ("genuser.sh", "tpot users add", "T-Pot Web user"))

    def test_help_without_tpot(self):
        for _ in self.both():
            for script, command, title in self.CASES:
                for option in ("-h", "--help"):
                    with self.subTest(script=script, option=option):
                        result = self.run_with(script, ("y",) * 6, option)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn("Usage:", result.stdout)
                        self.assertIn(title, result.stdout)
                        self.assertIn(command, result.stdout)
                        self.assertNotIn("(y/n)", result.stdout + result.stderr)
                        self.assertNotIn("###", result.stdout + result.stderr)
                        self.assertEqual(result.stderr, "")
                        self.assertEqual(self.calls(), "")
                        self.assertFalse(os.path.exists(os.path.join(self.home, "htpasswd.args")))

    def test_help_fits_80_columns(self):
        for _ in self.both():
            for script, _command, _title in self.CASES:
                with self.subTest(script=script):
                    result = self.run_with(script, (), "-h")
                    self.assertEqual([line for line in result.stdout.splitlines() if len(line) > 80], [])

    def test_help_at_a_terminal_shows_no_logo(self):
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.home, "LANG": "en_US.UTF-8",
               "XDG_CONFIG_HOME": os.path.join(self.home, "config"), "XDG_DATA_HOME": self.data,
               "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        for script, command, _title in self.CASES:
            with self.subTest(script=script):
                out = ui.plain(ui.at_terminal(f"exec bash '{os.path.join(REPO, script)}' -h", env, 120, 49,
                                              source="/dev/null", timeout=60))
                self.assertIn("Usage:", out)
                self.assertIn(command, out)
                self.assertNotIn("telekom security", out)
                self.assertEqual(self.calls(), "")


if __name__ == "__main__":
    unittest.main()
