"""The installer: install.sh and its gum block, tpot install (checks, engine, progress, assistant).

The assistant runs in a temporary checkout with a fake engine, nothing here touches
the checkout the tests run from or the host.
"""

import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import contextmanager
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import installer  # noqa: E402
from tpotctl.bootstrap import REPO_DIR  # noqa: E402

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None

INSTALL_SH = os.path.join(REPO_DIR, "install.sh")
UI_SH = os.path.join(REPO_DIR, "installer", "lib", "ui.sh")
with open(os.path.join(REPO_DIR, "tpotctl", "tests", "account_cases.json"), encoding="utf-8") as handle:
    ACCOUNTS = json.load(handle)


def block(text):
    return text[text.index("# >>> tpot ui >>>"):text.index("# <<< tpot ui <<<")]


class FreshInstallEnvTest(unittest.TestCase):
    """A fresh install creates a private .env from env.example before writing installer settings."""

    ENV = os.path.join(REPO_DIR, "env.example")

    def kibana_block(self, path):
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        end = lines.index("TPOT_KIBANA_OBJECTS=update")
        start = max(i for i in range(end) if lines[i].startswith("# T-Pot Kibana Objects"))
        return lines[start:end + 1]

    def test_install_sh_writes_only_the_web_user_and_the_update_switch_into_env(self):
        with open(INSTALL_SH, encoding="utf-8") as handle:
            text = handle.read()
        setter = text[text.index("env_set() {"):text.index("\n}", text.index("env_set() {"))]
        writes = [line.strip() for line in setter.splitlines()
                  if "myTPOT_CONF_FILE" in line and not line.strip().startswith(("#", "myTPOT_CONF_FILE=",
                                                                                  "fuUI_INFO"))]
        # env_set: the line in place, or (a .env without it) at the end on a line of its own
        self.assertEqual(writes, ['if grep -q "^$1=" "${myTPOT_CONF_FILE}";',
                                  'sed -i "s|^$1=.*|$1=$2|" "${myTPOT_CONF_FILE}"',
                                  '[ -z "$(tail -c 1 "${myTPOT_CONF_FILE}")" ] || echo >> "${myTPOT_CONF_FILE}"',
                                  'echo "$1=$2" >> "${myTPOT_CONF_FILE}"'])
        self.assertEqual(re.findall(r"^\s*env_set (\S+) (.*)$", text, re.M),
                         [("TPOT_AUTO_UPDATES", "DISABLED"), ("WEB_USER", '"${myWEB_USER_ENC_B64}"')])
        self.assertIn('python3 "${HOME}/tpotce/tpotctl/git_hooks.py" install', text)
        self.assertIn('python3 "${HOME}/tpotce/tpotctl/generated.py" stamp-env', text)
        self.assertIn('python3 "${HOME}/tpotce/tpotctl/generated.py" stamp-compose', text)
        with open(os.path.join(REPO_DIR, "installer", "install", "tpot.yml"), encoding="utf-8") as handle:
            playbook = handle.read()
        self.assertNotIn("tpotce/.env", playbook)
        self.assertNotIn("env.example", playbook)

    def test_a_fresh_install_chooses_update_for_the_kibana_objects(self):
        # env.example, after the one change install.sh makes to a fresh .env
        with tempfile.TemporaryDirectory() as tmp:
            env = os.path.join(tmp, ".env")
            shutil.copy(self.ENV, env)
            subprocess.run(["sed", "-i.bak", "s|^WEB_USER=.*|WEB_USER=dXNlcjpwYXNz|", env], check=True)
            from tpotctl.envfile import EnvFile
            values = EnvFile(env).values()
        self.assertEqual(values["WEB_USER"], "dXNlcjpwYXNz")
        self.assertEqual(values["TPOT_KIBANA_OBJECTS"], "update")
        # with the comments of env.example, at the same place
        self.assertEqual(self.kibana_block(self.ENV), self.kibana_block(os.path.join(REPO_DIR, "env.example")))
        keys = list(values)
        self.assertEqual(keys[keys.index("TPOT_KIBANA_OBJECTS") + 1], "TPOT_TYPE")


class ScriptsInSyncTest(unittest.TestCase):

    def test_ui_block_is_the_same_in_install_sh(self):
        with open(INSTALL_SH, encoding="utf-8") as one, open(UI_SH, encoding="utf-8") as two:
            self.assertEqual(block(one.read()), block(two.read()), "install.sh and installer/lib/ui.sh differ")

    def test_supported_distributions_match_install_sh(self):
        with open(INSTALL_SH, encoding="utf-8") as handle:
            text = handle.read()
        names = re.findall(r'"([^"]+)"', re.search(r"mySUPPORTED_DISTRIBUTIONS=\((.*?)\)", text).group(1))
        self.assertEqual(sorted(names), sorted(installer.SUPPORTED))
        for match in re.finditer(r'((?:"[^"]+"\|?)+)\)\s*\n\s*mySUPPORTED_VERSION="([^"]*)"', text):
            for name in re.findall(r'"([^"]+)"', match.group(1)):
                self.assertEqual(installer.SUPPORTED[name], match.group(2), name)

    def test_editions_match_install_sh(self):
        with open(INSTALL_SH, encoding="utf-8") as handle:
            text = handle.read()
        for edition in installer.EDITIONS:
            self.assertIn(f'myEDITION="{edition.key}"', text)
            self.assertTrue(os.path.exists(os.path.join(REPO_DIR, "compose", f"{edition.key}.yml")))
        self.assertEqual({e.letter for e in installer.EDITIONS if e.web_user}, set("hlit"))

    @unittest.skipUnless(shutil.which("bash"), "no bash")
    def test_install_sh_parses_and_has_its_options(self):
        self.assertEqual(subprocess.run(["bash", "-n", INSTALL_SH]).returncode, 0)
        help_text = subprocess.run(["bash", INSTALL_SH, "-h"], stdout=subprocess.PIPE,
                                   universal_newlines=True).stdout
        for option in ("-s", "-t", "-u", "-p", "-P", "-B", "-c", "-n", "-b", "-r"):
            self.assertIn(f"  {option} ", help_text)

    @unittest.skipUnless(shutil.which("bash"), "no bash")
    def test_gum_is_pinned_and_falls_back_to_plain_text(self):
        with open(UI_SH, encoding="utf-8") as handle:
            text = handle.read()
        self.assertRegex(text, r'myUI_GUM_SHA256_x86_64="[0-9a-f]{64}"')
        self.assertRegex(text, r'myUI_GUM_SHA256_arm64="[0-9a-f]{64}"')
        # no terminal: no gum, plain text
        out = subprocess.run(["bash", "-c", f'source "{UI_SH}"; fuUI_INIT; echo "gum=[$myUI_GUM]"; fuUI_OK done'],
                             stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.assertIn("gum=[]", out)
        self.assertIn("### [OK] - done", out)


OS_RELEASES = {
    "debian": ('NAME="Debian GNU/Linux"\nID=debian\nVERSION_ID="13"\n', "ok"),
    "debian point release": ('NAME="Debian GNU/Linux"\nVERSION_ID="13.7"\n', "ok"),
    # 32-bit Raspberry Pi OS (armhf), the 64-bit one says Debian GNU/Linux (the next one)
    "raspbian": ('NAME="Raspbian GNU/Linux"\nID=raspbian\nVERSION_ID="13"\n', "fail"),
    "pi with debian identity": ('NAME="Debian GNU/Linux"\nID=debian\nVERSION_ID="13"\n'
                               'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n', "ok"),
    "old debian": ('NAME="Debian GNU/Linux"\nVERSION_ID="12"\n', "fail"),
    "old raspbian": ('NAME="Raspbian GNU/Linux"\nVERSION_ID="12"\n', "fail"),
    "missing version": ('NAME="Debian GNU/Linux"\n', "fail"),
    "ubuntu": ('NAME="Ubuntu"\nVERSION_ID="26.04"\n', "warn"),
    "old ubuntu": ('NAME="Ubuntu"\nVERSION_ID="24.04"\n', "fail"),
    "fedora": ('NAME="Fedora Linux"\nVERSION_ID=44\n', "warn"),
    "alma": ('NAME="AlmaLinux"\nVERSION_ID="10.1"\n', "warn"),
    "rhel": ('NAME="Red Hat Enterprise Linux"\nVERSION_ID="10.1"\n', "warn"),
    "rocky": ('NAME="Rocky Linux"\nVERSION_ID="10.1"\n', "warn"),
    "tumbleweed": ('NAME="openSUSE Tumbleweed"\nVERSION_ID="20261001"\n', "warn"),
    "arch": ('NAME="Arch Linux"\n', "fail"),
    "no os-release": ("", "fail"),
}

SUPPORT_POLICY = ("Debian is the preferred T-Pot platform. "
                  "Raspberry Pi OS remains supported for T-Pot Mobile.")
BEST_EFFORT = "best effort only: no equal test coverage or guaranteed distribution-specific fixes"


@unittest.skipUnless(yaml, "PyYAML is not installed")
class SupportPolicyPlaybookTest(unittest.TestCase):

    def test_policy_is_reported_after_version_checks_and_before_host_preparation(self):
        with open(os.path.join(REPO_DIR, "installer", "install", "tpot.yml"), encoding="utf-8") as handle:
            plays = yaml.safe_load(handle)
        tasks = plays[1]["tasks"]
        names = [t["name"] for t in tasks]
        policy = names.index("Show preferred host platforms (All)")
        warning = names.index("Warn about best-effort distribution (All)")
        self.assertLess(names.index("Check if supported distribution version (All)"), policy)
        self.assertLess(policy, warning)
        self.assertLess(warning, names.index("Count default routes (All)"))
        self.assertEqual(tasks[policy]["debug"]["msg"], SUPPORT_POLICY)
        self.assertNotIn("when", tasks[policy])
        self.assertEqual(tasks[warning]["when"], 'ansible_facts.distribution != "Debian"')
        self.assertEqual(tasks[warning]["debug"]["msg"],
                         "WARNING: {{ ansible_facts.distribution }} is " + BEST_EFFORT + ".")
        self.assertEqual(set(plays[1]["tags"]),
                         {"AlmaLinux", "Debian", "Fedora", "openSUSE Tumbleweed", "RedHat", "Rocky", "Ubuntu"})

    def test_no_32bit_raspbian(self):
        """Raspbian GNU/Linux is the 32-bit Raspberry Pi OS: no tag, no distribution, no version of it."""
        for parts in (("install", "tpot.yml"), ("remove", "tpot.yml")):
            with open(os.path.join(REPO_DIR, "installer", *parts), encoding="utf-8") as handle:
                self.assertNotIn("Raspbian", handle.read(), parts)
        for script in ("install.sh", "uninstall.sh", "deploy.sh"):
            with open(os.path.join(REPO_DIR, script), encoding="utf-8") as handle:
                self.assertNotIn('"Raspbian GNU/Linux"', handle.read(), script)


@unittest.skipUnless(yaml, "PyYAML is not installed")
class SshColortermTest(unittest.TestCase):
    """sshd takes COLORTERM and LC_TERMINAL from the client, so the T-Pot Manager shows true colours over SSH."""

    DROPIN = "/etc/ssh/sshd_config.d/tpot.conf"

    @staticmethod
    def tasks(*parts):
        import yaml as pyyaml
        with open(os.path.join(REPO_DIR, *parts), encoding="utf-8") as handle:
            plays = pyyaml.safe_load(handle)
        return [task for play in plays for task in play.get("tasks", [])]

    def test_install_writes_the_dropin_on_every_distribution(self):
        found = [t for t in self.tasks("installer", "install", "tpot.yml")
                 if (t.get("lineinfile") or {}).get("path") == self.DROPIN]
        self.assertEqual(len(found), 1, found)
        task = found[0]
        # LC_TERMINAL is how iTerm2 says who it is (tpotctl/tests/test_colors.py SshdTest has the rest)
        self.assertEqual(task["lineinfile"]["line"], "AcceptEnv COLORTERM LC_TERMINAL LC_TERMINAL_VERSION")
        for name in ("AlmaLinux", "Debian", "Fedora", "openSUSE Tumbleweed", "RedHat", "Rocky", "Ubuntu"):
            self.assertTrue(f'"{name}"' in task["when"], name)
        tasks = self.tasks("installer", "install", "tpot.yml")
        checks = [i for i, t in enumerate(tasks) if str(t.get("command", "")) == "/usr/sbin/sshd -t"]
        self.assertEqual(len(checks), 1)
        # sshd -t needs its privilege separation directory, which only a running ssh.service has on the
        # Debian family (Ubuntu stops ssh.socket before, Raspberry Pi OS ships with SSH off)
        privsep = [i for i, t in enumerate(tasks) if (t.get("file") or {}).get("path") == "/run/sshd"]
        self.assertTrue(privsep and privsep[0] < checks[0], privsep)
        for name in ("Debian", "Ubuntu"):
            self.assertTrue(f'"{name}"' in tasks[privsep[0]]["when"], name)
        directory = [t for t in tasks if (t.get("file") or {}).get("path") == "/etc/ssh/sshd_config.d"]
        self.assertEqual(len(directory), 1)
        self.assertNotIn("mode", directory[0]["file"])     # RHEL-likes keep it 0700

    def test_uninstall_removes_it(self):
        found = [t for t in self.tasks("installer", "remove", "tpot.yml")
                 if (t.get("file") or {}).get("path") == self.DROPIN and t["file"].get("state") == "absent"]
        self.assertEqual(len(found), 1, found)


class AccountCheckTest(unittest.TestCase):
    """The containers of T-Pot run as the user and group tpot (uid and gid 2000), the playbook makes them
    (installer/install/tpot.yml). A login or account of that name, a uid or gid 2000 of another name stops the
    assistant before the installer starts, with the cause and the way out. The cases are
    tpotctl/tests/account_cases.json, install.sh stops on the same ones."""

    @staticmethod
    def lookups(passwd, group):
        """pwd and grp for these lines of /etc/passwd and /etc/group (KeyError: no such entry)."""
        users = [line.split(":") for line in passwd]
        groups = [line.split(":") for line in group]

        def find(rows, key, column):
            for row in rows:
                if row[column] == str(key):
                    return row
            raise KeyError(key)

        return {
            "getpwnam": lambda name: SimpleNamespace(pw_name=name, pw_uid=int(find(users, name, 0)[2])),
            "getgrnam": lambda name: SimpleNamespace(gr_name=name, gr_gid=int(find(groups, name, 0)[2])),
            "getpwuid": lambda uid: SimpleNamespace(pw_name=find(users, uid, 2)[0], pw_uid=uid),
            "getgrgid": lambda gid: SimpleNamespace(gr_name=find(groups, gid, 2)[0], gr_gid=gid),
        }

    def check(self, case):
        return installer.check_account(case["login"], installer.read_accounts(
            **self.lookups(case["passwd"], case["group"])))

    def test_the_cases_install_sh_stops_on(self):
        for case in ACCOUNTS["cases"]:
            with self.subTest(case=case["case"]):
                check = self.check(case)
                self.assertEqual(check.title, "Account tpot")
                if not case["stop"]:
                    self.assertEqual(check.state, "ok", check.detail)
                    continue
                self.assertEqual(check.state, "fail")
                for text in [ACCOUNTS["words"][case["stop"]]] + case["says"]:
                    self.assertIn(text, check.detail)
                for tool in ("userdel", "groupdel"):
                    if case["fix"] and tool in case["fix"]:
                        self.assertIn(case["fix"], check.detail)
                    else:
                        self.assertNotIn(tool, check.detail)
                if case["fix"] == installer.REMOVE_USER:
                    self.assertIn(ACCOUNTS["home"], check.detail)
                else:
                    self.assertNotIn(ACCOUNTS["home"], check.detail)
                self.assertNotRegex(check.detail, r"home (would be )?gone")
                for text in case.get("silent", []):
                    self.assertNotIn(text, check.detail)

    def test_the_way_out_is_the_one_of_the_table(self):
        ways = {case.get("fix") for case in ACCOUNTS["cases"]} - {None}
        self.assertEqual(ways, {installer.REMOVE_USER, installer.REMOVE_GROUP})

    def test_only_the_login_is_known_where_the_host_gives_no_answer(self):
        def broken(key):
            raise OSError("no name service")
        accounts = installer.read_accounts(broken, broken, broken, broken)
        self.assertEqual(accounts, installer.Accounts())
        self.assertEqual(installer.check_account("analyst", accounts).state, "ok")
        self.assertEqual(installer.check_account("tpot", accounts).state, "fail")

    def test_the_host_is_read_through_pwd_and_grp(self):
        self.assertIsInstance(installer.read_accounts(), installer.Accounts)

    def test_the_assistant_runs_the_check(self):
        from unittest import mock
        regular = installer.read_accounts(**self.lookups(["tpot:x:1000:1000::/home/tpot:/bin/bash"],
                                                         ["tpot:x:1000:"]))
        done = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", "")
        with mock.patch.object(installer, "current_login", return_value="analyst"), \
                mock.patch.object(installer, "read_accounts", return_value=regular):
            checks = installer.run_checks(done, lambda request, timeout: io.BytesIO(b""))
        self.assertEqual([c.state for c in checks if c.title == "Account tpot"], ["fail"])


PLAYBOOK = os.path.join(REPO_DIR, "installer", "install", "tpot.yml")
ABORT_PLAY = "T-Pot - Abort if run as tpot, root or on unsupported distribution"
ACCOUNT_BLOCK = "Check the accounts of the T-Pot containers (All)"
# the stops of the table in the order they are asked: the login first, then the user, group, uid and gid
ACCOUNT_STOPS = list(ACCOUNTS["words"])


def account_block():
    """(the play that stops a run before it changes anything, its block that checks the accounts or None)."""
    import yaml as pyyaml
    with open(PLAYBOOK, encoding="utf-8") as handle:
        plays = pyyaml.safe_load(handle)
    play = next(p for p in plays if p.get("name") == ABORT_PLAY)
    found = [t for t in play["tasks"] if t.get("name") == ACCOUNT_BLOCK]
    return play, found[0] if found else None


@unittest.skipUnless(yaml, "PyYAML is not installed")
class AccountPlaybookTest(unittest.TestCase):
    """The playbook says no to the same accounts as install.sh and the assistant (the cases are
    tpotctl/tests/account_cases.json), for a run of ansible-playbook without them: in the play that stops a run
    before it installs a package or touches an account (the play before it only brings a missing python3), and
    it only reads and asserts. AccountPlaybookRunTest runs every case."""

    def setUp(self):
        self.play, self.block = account_block()
        self.assertIsNotNone(self.block, f"the play '{ABORT_PLAY}' has no block '{ACCOUNT_BLOCK}'")
        self.tasks = self.block["block"]

    def test_it_comes_after_the_check_for_root_and_takes_the_place_of_the_one_for_the_login(self):
        names = [t["name"] for t in self.play["tasks"]]
        self.assertLess(names.index("Check if running as root (All)"), names.index(ACCOUNT_BLOCK))
        self.assertLess(names.index(ACCOUNT_BLOCK), names.index("Check if supported distribution (All)"))
        self.assertNotIn("Check if running as tpot (All)", names)

    def test_it_only_reads_the_accounts_and_never_changes_one(self):
        for task in self.tasks:
            modules = set(task) - {"name", "register", "when", "vars", "tags", "failed_when", "changed_when"}
            self.assertEqual(len(modules), 1, task["name"])
            self.assertLessEqual(modules, {"assert", "ansible.builtin.getent"}, task["name"])

    def test_it_reads_the_user_group_uid_and_gid_and_a_missing_one_is_no_failure(self):
        reads = [t["ansible.builtin.getent"] for t in self.tasks if "ansible.builtin.getent" in t]
        self.assertEqual(sorted((r["database"], str(r["key"])) for r in reads),
                         [("group", "2000"), ("group", "tpot"), ("passwd", "2000"), ("passwd", "tpot")])
        for read in reads:
            self.assertIs(read["fail_key"], False, read)

    def test_the_stops_come_in_the_order_of_the_table_each_with_its_way_out(self):
        fixes = {}
        for case in ACCOUNTS["cases"]:
            if case["stop"]:
                self.assertEqual(fixes.setdefault(case["stop"], case["fix"]), case["fix"], case["case"])
        checks = [t["assert"] for t in self.tasks if "assert" in t]
        self.assertEqual(len(checks), len(ACCOUNT_STOPS))
        for key, check in zip(ACCOUNT_STOPS, checks):
            message = check["fail_msg"]
            self.assertIn(ACCOUNTS["words"][key], message, key)
            for tool in ("userdel", "groupdel"):
                if fixes[key] and tool in fixes[key]:
                    self.assertIn(fixes[key], message, key)
                else:
                    self.assertNotIn(tool, message, key)
            if fixes[key] == installer.REMOVE_USER:
                self.assertIn(ACCOUNTS["home"], message, key)
            else:
                self.assertNotIn(ACCOUNTS["home"], message, key)
        # "as tpot" is part of "logged in as tpot": the second check is the one without an account to remove
        self.assertNotIn(ACCOUNTS["words"]["login-account"], checks[1]["fail_msg"])


@unittest.skipUnless(yaml and shutil.which("ansible-playbook") and shutil.which("awk"),
                     "PyYAML and ansible-playbook are needed")
class AccountPlaybookRunTest(unittest.TestCase):
    """ansible-playbook runs the account tasks of the playbook for every case of the table: a getent that answers
    from files stands in for the host and the login comes from the environment, nothing of the host is read."""

    GETENT = ('#!/bin/sh\n[ -r "$TPOT_TEST_ETC/$1" ] || exit 2\n'
              "awk -F: -v key=\"$2\" '$1 == key || $3 == key { print; found = 1; exit } "
              "END { exit (found ? 0 : 2) }' \"$TPOT_TEST_ETC/$1\"\n")

    def run_case(self, case, home):
        etc, stubs = os.path.join(home, "etc"), os.path.join(home, "bin")
        os.makedirs(etc)
        os.makedirs(stubs)
        for database, lines in (("passwd", case["passwd"]), ("group", case["group"])):
            with open(os.path.join(etc, database), "w", encoding="utf-8") as handle:
                handle.write("".join(line + "\n" for line in lines))
        getent = os.path.join(stubs, "getent")
        with open(getent, "w", encoding="utf-8") as handle:
            handle.write(self.GETENT)
        os.chmod(getent, 0o755)
        _play, block = account_block()
        self.assertIsNotNone(block, f"the play '{ABORT_PLAY}' has no block '{ACCOUNT_BLOCK}'")
        play = os.path.join(home, "play.yml")
        with open(play, "w", encoding="utf-8") as handle:     # JSON is YAML
            json.dump([{"name": "accounts", "hosts": "all", "gather_facts": True, "gather_subset": ["!all"],
                        "tasks": [block]}], handle)
        env = dict(os.environ, HOME=home, PATH=stubs + os.pathsep + os.environ["PATH"], TPOT_TEST_ETC=etc,
                   LOGNAME=case["login"], USER=case["login"], ANSIBLE_INJECT_FACT_VARS="False",
                   ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False",
                   ANSIBLE_INVENTORY_UNPARSED_WARNING="False", ANSIBLE_CALLBACK_RESULT_FORMAT="json")
        return subprocess.run(["ansible-playbook", play, "-i", "127.0.0.1,", "-c", "local",
                               "-e", f"ansible_python_interpreter={sys.executable}"],
                              env=env, cwd=home, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=300)

    def test_every_case_of_the_table(self):
        for case in ACCOUNTS["cases"]:
            with self.subTest(case=case["case"]), tempfile.TemporaryDirectory() as home:
                result = self.run_case(case, home)
                output = result.stdout
                if not case["stop"]:
                    self.assertEqual(result.returncode, 0, output[-1500:])
                    continue
                self.assertEqual(result.returncode, 2, output[-1500:])
                self.assertIn("FAILED!", output)
                for text in [ACCOUNTS["words"][case["stop"]]] + case["says"]:
                    self.assertIn(text, output)
                for tool in ("userdel", "groupdel"):
                    if case["fix"] and tool in case["fix"]:
                        self.assertIn(case["fix"], output)
                    else:
                        self.assertNotIn(tool, output)
                if case["fix"] == installer.REMOVE_USER:
                    self.assertIn(ACCOUNTS["home"], output)
                else:
                    self.assertNotIn(ACCOUNTS["home"], output)
                for text in case.get("silent", []):
                    self.assertNotIn(text, output)


class AccountWaysOutTest(unittest.TestCase):
    """The way out of an account named tpot is one command wherever a text names it (the installer, the
    assistant, the playbook, the ISO, the docs): nobody is sent to a variant (deluser, userdel without -r)."""

    PLACES = {      # the places and the ways each one has to name
        "install.sh": (installer.REMOVE_USER, installer.REMOVE_GROUP),
        "tpotctl/installer.py": (installer.REMOVE_USER, installer.REMOVE_GROUP),
        "installer/install/tpot.yml": (installer.REMOVE_USER, installer.REMOVE_GROUP),
        "installer/iso/start.sh": (installer.REMOVE_USER,),
        "README.md": (installer.REMOVE_USER, installer.REMOVE_GROUP),
        "CHANGELOG.md": (),
    }

    def test_every_place_names_its_ways_and_no_other(self):
        for place, ways in self.PLACES.items():
            with self.subTest(place=place):
                with open(os.path.join(REPO_DIR, place), encoding="utf-8") as handle:
                    text = handle.read()
                for way in ways:
                    self.assertIn(way, text)
                for way in (installer.REMOVE_USER, installer.REMOVE_GROUP):
                    text = text.replace(way, "")
                for tool in ("userdel", "groupdel", "deluser", "delgroup"):
                    self.assertNotIn(tool, text)

    def test_every_text_with_the_way_to_remove_a_user_says_what_it_deletes(self):
        """userdel -r takes the home directory of tpot with it and nothing brings it back: whoever is sent to
        it is told to copy what is needed first, in every place that names it (the table says how)."""
        for place, ways in self.PLACES.items():
            if installer.REMOVE_USER not in ways:
                continue
            with self.subTest(place=place):
                with open(os.path.join(REPO_DIR, place), encoding="utf-8") as handle:
                    self.assertIn(ACCOUNTS["home"], handle.read())

    def test_nobody_is_told_that_the_installer_deletes_the_home(self):
        """The playbook turns the account into the container account: login and shell are changed, the files
        of its home stay behind (R2 of the stage 3 evidence). Only the way out deletes them."""
        for place in self.PLACES:
            with self.subTest(place=place):
                with open(os.path.join(REPO_DIR, place), encoding="utf-8") as handle:
                    self.assertNotRegex(handle.read(), r"home (would be )?gone")


class ChecksTest(unittest.TestCase):

    def test_undecodable_account_output_is_an_explicit_verification_error(self):
        def run(*args, **kwargs):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
        with self.assertRaisesRegex(OSError, "password state"):
            installer.user_password_usable(run=run)

    def test_password_state_is_read_without_sudo_and_unknown_is_an_error(self):
        for state, expected in (("P", True), ("L", False), ("NP", False)):
            with self.subTest(state=state):
                calls = []
                def run(command, **kwargs):
                    calls.append(command)
                    return subprocess.CompletedProcess(command, 0, f"alice {state} 01/01/2020 0 99999 7 -1\n", "")
                self.assertIs(installer.user_password_usable(run=run), expected)
                self.assertEqual(calls, [["passwd", "-S"]])
        for code, output in ((1, ""), (0, "alice unknown"), (0, "")):
            with self.subTest(code=code, output=output):
                with self.assertRaisesRegex(OSError, "password state"):
                    installer.user_password_usable(
                        run=lambda command, **kwargs: subprocess.CompletedProcess(command, code, output, ""))

    def test_tpot_on_path_is_this_manager(self):
        from unittest import mock
        mine = os.path.join(REPO_DIR, "tpot")
        with mock.patch.object(installer.shutil, "which", return_value=mine):
            self.assertTrue(installer.tpot_on_path())
        with mock.patch.object(installer.shutil, "which", return_value="/opt/other/bin/tpot"):
            self.assertFalse(installer.tpot_on_path())
        with mock.patch.object(installer.shutil, "which", return_value=None):
            self.assertFalse(installer.tpot_on_path())
        with open(INSTALL_SH, encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue('readlink -f "${HOME}/tpotce/tpot"' in text)

    def test_distributions(self):
        for name, (text, state) in OS_RELEASES.items():
            with self.subTest(name=name):
                handle, path = tempfile.mkstemp()
                with os.fdopen(handle, "w") as out:
                    out.write(text)
                self.addCleanup(os.unlink, path)
                check = installer.check_distro(installer.os_release(path))
                self.assertEqual(check.state, state)
                if state == "warn":
                    self.assertIn(BEST_EFFORT, check.detail)
                    self.assertIn(SUPPORT_POLICY, check.detail)
                elif state == "ok":
                    self.assertIn(SUPPORT_POLICY, check.detail)
                    self.assertNotIn("best effort", check.detail)
                else:
                    self.assertNotIn("best effort", check.detail)

    def test_root(self):
        self.assertEqual(installer.check_root(0).state, "fail")
        self.assertEqual(installer.check_root(1000).state, "ok")

    def test_architecture_64bit_only(self):
        """As install.sh: the userland counts, a 32-bit Raspberry Pi OS on a Pi 4/5 boots an aarch64 kernel."""
        for machine, bits, state in (("x86_64", 64, "ok"), ("aarch64", 64, "ok"), ("arm64", 64, "ok"),
                                     ("aarch64", 32, "fail"), ("armv7l", 32, "fail"), ("i686", 32, "fail"),
                                     ("x86_64", 32, "fail"), ("", 64, "fail")):
            with self.subTest(machine=machine, bits=bits):
                check = installer.check_arch(machine, bits)
                self.assertEqual((check.title, check.state), ("Architecture", state))
                if state == "fail":
                    self.assertIn("64-bit", check.detail)
                    self.assertIn("Raspberry Pi OS Lite (64-bit)", check.detail)
        self.assertEqual(installer.check_arch().state, "ok")       # the test host
        with open(INSTALL_SH, encoding="utf-8") as handle:
            line = re.search(r'myMACHINE\}" =~ \^\(([^)]+)\)\$', handle.read()).group(1)
        self.assertEqual(tuple(line.split("|")), installer.MACHINES_64)

    def test_ports_ignore_the_resolved_stub(self):
        stub = "UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:*\nUNCONN 0 0 127.0.0.54:53 0.0.0.0:*\n"
        self.assertEqual(installer.occupied(stub), [])
        self.assertEqual(installer.occupied("LISTEN 0 100 0.0.0.0:25 0.0.0.0:*\n"), ["0.0.0.0:25"])
        self.assertEqual(installer.occupied("LISTEN 0 100 [::1]:25 [::]:*\n"), ["[::1]:25"])

    def test_reach_counts_any_answer(self):
        import gc
        import warnings
        errors = []

        def opener(request, timeout):
            if "docker" in request.full_url:
                errors.append(urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"")))
                raise errors[-1]
            if "ghcr" in request.full_url:
                raise urllib.error.URLError("no route")
            return io.BytesIO(b"")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            check = installer.check_reach(opener)
            for error in errors:               # an HTTPError is a response too, Python 3.14 warns if not closed
                error.close()
            errors.clear()
            gc.collect()
        self.assertEqual(check.state, "fail")
        self.assertIn("GitHub Container Registry", check.detail)
        self.assertNotIn("Docker Hub", check.detail)
        self.assertEqual([str(w.message) for w in caught if issubclass(w.category, ResourceWarning)], [])

    def test_resources(self):
        hive = installer.EDITION_BY_LETTER["h"]
        self.assertEqual(installer.check_resources(hive, 15.6, 300).state, "ok")     # a 16 GB machine
        self.assertEqual(installer.check_resources(hive, 8, 300).state, "warn")
        self.assertEqual(installer.meminfo_gb("MemTotal:       16384000 kB\n"), 16384000 / 1024 / 1024)


class EngineTest(unittest.TestCase):

    def test_no_install_command_until_the_sudo_mode_is_chosen(self):
        answers = installer.Answers(installer.EDITION_BY_LETTER["s"])
        with self.assertRaisesRegex(ValueError, "sudo mode"):
            installer.engine_command(answers, {}, "/x/install.sh")

    def test_sudo_choice_and_new_password_reach_the_engine_without_secrets_in_argv(self):
        for mode in ("secure", "convenient"):
            with self.subTest(mode=mode):
                answers = installer.Answers(installer.EDITION_BY_LETTER["s"], sudo_mode=mode,
                                            user_password="  New-user secret 874!  ")
                with installer.secret_files(user=answers.user_password) as files:
                    command = installer.engine_command(answers, files, "/x/install.sh")
                    self.assertEqual("--sudo-nopasswd" in command, mode == "convenient")
                    self.assertEqual(command[command.index("--user-password-file") + 1], files["user"])
                    self.assertNotIn(answers.user_password, " ".join(command))
                self.assertFalse(os.path.exists(files["user"]))

    def test_password_cleanup_failure_is_reported_and_other_files_are_removed(self):
        from unittest import mock
        removed = []
        unlink = os.unlink
        def remove(path):
            removed.append(path)
            if len(removed) == 1:
                raise PermissionError("cleanup refused")
            unlink(path)
        try:
            with mock.patch.object(installer.os, "unlink", side_effect=remove):
                with self.assertRaisesRegex(OSError, "Remove.*manually"):
                    with installer.secret_files(web="web", become="become") as files:
                        pass
            self.assertEqual(len(removed), 2)
            self.assertFalse(os.path.exists(files["become"]))
        finally:
            for path in removed:
                if os.path.exists(path):
                    unlink(path)

    def test_no_password_in_the_command_and_files_are_private(self):
        answers = installer.Answers(installer.EDITION_BY_LETTER["h"], web_user="alice", web_password="s3cret-pass",
                                    sudo_password="sudo-pass", sudo_mode="secure")
        with installer.secret_files(web=answers.web_password, become=answers.sudo_password) as files:
            command = installer.engine_command(answers, files, "/x/install.sh")
            self.assertNotIn("s3cret-pass", " ".join(command))
            self.assertNotIn("sudo-pass", " ".join(command))
            self.assertEqual(command[:5], ["/x/install.sh", "-s", "-M", "-t", "h"])
            self.assertEqual(command[command.index("-u") + 1], "alice")
            for path in files.values():
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with open(files["web"]) as handle:
                self.assertEqual(handle.read(), "s3cret-pass\n")
        for path in files.values():
            self.assertFalse(os.path.exists(path))

    def test_sensor_and_custom(self):
        sensor = installer.Answers(installer.EDITION_BY_LETTER["s"], sudo_mode="secure")
        with installer.secret_files(web="", become="") as files:
            command = installer.engine_command(sensor, files, "/x/install.sh")
        self.assertNotIn("-u", command)
        self.assertNotIn("-B", command)
        custom = installer.Answers(installer.EDITION_BY_LETTER["h"], custom_compose="/r/docker-compose-custom.yml",
                                   web_user="bob", web_password="pw", sudo_mode="secure")
        with installer.secret_files(web="pw") as files:
            command = installer.engine_command(custom, files, "/x/install.sh")
        self.assertEqual(command[command.index("-c") + 1], "/r/docker-compose-custom.yml")

    def test_progress(self):
        progress = installer.Progress()
        for line in ["@@tpot phase checks", "@@tpot phase packages", "@@tpot tasks 4", "@@tpot phase playbook",
                     "PLAY [T-Pot - Bootstrapping Python] ****", "TASK [Gathering Facts] *****",
                     "ok: [127.0.0.1]", "TASK [Install Docker Engine packages (All)] ****"]:
            progress.feed(line)
        self.assertEqual((progress.phase, progress.tasks, progress.tasks_done), ("playbook", 4, 2))
        self.assertEqual(progress.task, "Install Docker Engine packages (All)")
        self.assertAlmostEqual(progress.fraction, 0.05 + 0.73 * 0.5)
        progress.feed("fatal: [127.0.0.1]: FAILED! => {}")
        self.assertEqual(progress.failed_task, "Install Docker Engine packages (All)")
        for line in ["@@tpot phase pull", "@@tpot images 2", " cowrie Pulled", " nginx Pulled"]:
            progress.feed(line)
        self.assertAlmostEqual(progress.fraction, 0.99)
        progress.feed("@@tpot warn pull")
        self.assertEqual(len(progress.warnings), 1)
        progress.feed("@@tpot phase done")
        self.assertEqual(progress.fraction, 1.0)

    def test_repeated_completion_does_not_count_an_image_twice(self):
        progress = installer.Progress()
        for line in ("@@tpot phase pull", "@@tpot images 2", " cowrie Pulled", " cowrie Pulled"):
            progress.feed(line)
        self.assertEqual(progress.images_done, 1)
        self.assertAlmostEqual(progress.fraction, 0.895)

    def test_structured_pull_status_uses_completed_images_not_bytes(self):
        progress = installer.Progress()
        progress.feed("@@tpot phase pull")
        progress.feed('@@tpot pull {"version":1,"done":2,"total":3,"activity":"example/a:tag: Extracting"}')
        self.assertEqual((progress.images_done, progress.images), (2, 3))
        self.assertIn("Extracting", progress.task)
        progress.feed('@@tpot pull {"version":1,"done":4,"total":3,"activity":"impossible"}')
        self.assertEqual((progress.images_done, progress.images), (2, 3))
        self.assertTrue(progress.warnings)

    def test_truncated_pull_status_warns_and_new_phases_drop_stale_task_text(self):
        progress = installer.Progress()
        progress.feed("@@tpot phase playbook")
        progress.feed("TASK [Ansible setup]")
        progress.feed("@@tpot phase pull")
        self.assertEqual(progress.task, "")
        progress.feed("@@tpot pull")
        self.assertTrue(progress.warnings)

    @contextmanager
    def pull_shell(self):
        with open(INSTALL_SH) as handle:
            source = handle.read()
        definition = re.search(r"^install_pull_images\(\) \{.*?^\}", source, re.M | re.S)
        self.assertIsNotNone(definition)
        pull = source.split("# Pull docker images\n", 1)[1].split("# Show running services", 1)[0]
        with tempfile.TemporaryDirectory() as home:
            helper_dir = os.path.join(home, "tpotce", "tpotctl")
            os.makedirs(helper_dir)
            shutil.copyfile(os.path.join(REPO_DIR, "tpotctl", "runlog.py"), os.path.join(helper_dir, "runlog.py"))
            bindir = os.path.join(home, "bin")
            os.mkdir(bindir)
            for name, body in {
                "sudo": "#!/bin/sh\nexec \"$@\"\n",
                "python3": f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
                "docker": f"""#!{sys.executable}
import json, os, subprocess, sys
args = sys.argv[1:]
if args == ["compose", "--help"]:
    print("--progress string   auto, tty, plain" + (", json" if os.environ["JSON_PROGRESS"] == "1" else ""))
elif args[-2:] == ["--format", "json"]:
    print(json.dumps({{"services": {{"first": {{"image": "example/a:tag"}}}}}}))
elif args[-1] == "--images":
    print("example/a:tag")
elif args[-1] == "pull":
    if os.environ.get("WAIT_FOR_INTERRUPT"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        with open(os.environ["WAIT_FOR_INTERRUPT"], "w") as out:
            json.dump([os.getpid(), child.pid], out)
        print("first Waiting for interrupt", flush=True)
        try:
            child.wait()
        except KeyboardInterrupt:
            child.wait(timeout=5)
            sys.exit(130)
    if "json" in args:
        for event in ({{"id": "Image example/a:tag", "text": "Pulling", "status": "Working"}},
                      {{"id": "abcdef012345", "text": "Downloading", "details": "12MB/40MB", "status": "Working"}},
                      {{"id": "Image example/a:tag", "text": "Pulled", "status": "Done"}}):
            print(json.dumps(event))
    else:
        print(" first Pulling\\n abcdef012345 Downloading 12MB/40MB\\n first Pulled")
    sys.exit(int(os.environ["PULL_RC"]))
else:
    sys.exit(9)
""",
            }.items():
                path = os.path.join(bindir, name)
                with open(path, "w") as handle:
                    handle.write(body)
                os.chmod(path, 0o755)
            script = (f'source "{UI_SH}"\n' + definition.group() +
                      '\nmark_phase() { fuMARK phase "$1"; }\n'
                      'sudo_password_required() { return 1; }\ninstall_stopped() { exit 130; }\n'
                      'myMARKS=y\nmyPULL_LOG="$HOME/pull.log"\n' + pull +
                      '\nexit "$myRC"\n')
            env = dict(os.environ, HOME=home, PATH=bindir + os.pathsep + os.environ["PATH"],
                       TPOT_GUM="off", TPOT_MARKS="1", JSON_PROGRESS="1", PULL_RC="0")
            yield home, script, env

    def test_shell_pull_preserves_exit_codes_and_complete_log_without_a_text_flood(self):
        with self.pull_shell() as (home, script, env):
            for json_progress in ("0", "1"):
                for code in (0, 1, 130):
                    with self.subTest(json_progress=json_progress, code=code):
                        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10,
                                                env=dict(env, JSON_PROGRESS=json_progress, PULL_RC=str(code)))
                        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                        self.assertNotIn("12MB", result.stdout)
                        self.assertIn("1 of 1 images ready", result.stdout)
                        with open(os.path.join(home, "pull.log")) as handle:
                            self.assertIn("12MB", handle.read())

    def test_shell_pull_does_not_hide_a_renderer_log_failure(self):
        with self.pull_shell() as (home, script, env):
            script = script.replace('myPULL_LOG="$HOME/pull.log"', 'myPULL_LOG="$HOME/missing/pull.log"')
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Image progress failed", result.stdout + result.stderr)
            self.assertNotIn("Images ready.", result.stdout)

    @unittest.skipIf(os.name == "nt", "a terminal is Unix-only")
    def test_ctrl_c_stops_the_pull_process_tree(self):
        import fcntl
        import pty
        import select
        import signal
        import struct
        import termios
        import time
        with self.pull_shell() as (home, script, env):
            marker = os.path.join(home, "pull-pids")
            env = dict(env, WAIT_FOR_INTERRUPT=marker, TPOT_MARKS="0", COLUMNS="80")
            script = "trap 'exit 130' INT TERM HUP\n" + script.replace("myMARKS=y", "myMARKS=")
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

            def terminal():
                # Ctrl+C as for a person: not ignored, even when the tests run as `... &` (SIG_IGN inherited)
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            proc = subprocess.Popen(["bash", "-c", script], stdin=slave, stdout=slave, stderr=slave,
                                    preexec_fn=terminal, env=env)
            output, interrupted = b"", False
            deadline = time.monotonic() + 10
            try:
                while time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 8192)
                    if not interrupted and b"for interrupt" in output:
                        os.write(master, b"\x03")
                        interrupted = True
                    if proc.poll() is not None:
                        break
                self.assertTrue(interrupted, output)
                self.assertEqual(proc.poll(), 130, output)
                with open(marker) as handle:
                    for pid in json.load(handle):
                        with self.assertRaises(ProcessLookupError, msg=f"pull process {pid} survived Ctrl+C"):
                            os.kill(pid, 0)
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
                os.close(slave)
                os.close(master)

    def test_final_sudo_phase_preserves_progress_and_names_the_failure(self):
        progress = installer.Progress()
        for line in ("@@tpot phase pull", "@@tpot images 1", " cowrie Pulled", "@@tpot phase sudo",
                     "@@tpot fail sudo", "@@tpot phase failed"):
            progress.feed(line)
        self.assertAlmostEqual(progress.fraction, 0.99)
        self.assertEqual(progress.failed_phase, "sudo")
        self.assertIn("sudo", progress.title.lower())
        progress = installer.Progress()
        progress.feed("@@tpot phase sudo_prepare")
        self.assertAlmostEqual(progress.fraction, 0.05)
        self.assertIn("sudo", progress.title.lower())

    def test_progress_keeps_the_phase_that_failed(self):
        """@@tpot fail <phase> and @@tpot phase failed: the bar stays where the run was, the title names
        the step that failed and that it failed."""
        def fed(lines):
            progress = installer.Progress()
            for line in lines:
                progress.feed(line)
            return progress
        playbook = ["@@tpot phase checks", "@@tpot phase packages", "@@tpot tasks 4", "@@tpot phase playbook",
                    "TASK [Gathering Facts] ***", "TASK [Install Docker Engine packages (All)] ***"]
        before = fed(playbook).fraction
        cases = [(playbook + ["fatal: [127.0.0.1]: FAILED! => {}", "@@tpot fail playbook", "@@tpot phase failed"],
                  "playbook", before),
                 # an older install.sh: no fail mark, the phase it was in failed
                 (playbook + ["@@tpot phase failed"], "playbook", before),
                 # fail names a step before the last phase mark: the bar stays, the title names the step
                 (["@@tpot phase compose", "@@tpot phase pull", "@@tpot images 2", " cowrie Pulled",
                   "@@tpot fail compose", "@@tpot phase failed"], "compose", 0.80 + 0.19 * 0.5),
                 (["@@tpot phase checks", "@@tpot fail checks", "@@tpot phase failed"], "checks", 0.0)]
        for lines, step, fraction in cases:
            with self.subTest(lines=" | ".join(lines)):
                progress = fed(lines)
                self.assertEqual(progress.phase, "failed")
                self.assertEqual(progress.step, step)
                self.assertAlmostEqual(progress.fraction, fraction)
                self.assertEqual(progress.title, f"{installer.PHASE_TITLES[step]}: failed")
        self.assertGreater(before, 0.05)
        done = fed(playbook + ["@@tpot phase done"])
        self.assertEqual((done.step, done.fraction, done.title), ("done", 1.0, "Done"))


def make_checkout(test):
    """A fresh checkout: sources only, no runtime configuration yet."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    shutil.copy(os.path.join(REPO_DIR, "env.example"), os.path.join(tmp.name, "env.example"))
    shutil.copytree(os.path.join(REPO_DIR, "compose"), os.path.join(tmp.name, "compose"),
                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return tmp.name


class FakeEngine:
    runs = []
    code = 0

    def __init__(self, command):
        self.command = command

    def run(self, line):
        FakeEngine.runs.append(self.command)
        # what install.sh -M prints, roughly
        if "-P" in self.command:
            with open(self.command[self.command.index("-P") + 1]) as handle:
                FakeEngine.password = handle.read()
        for text in ["@@tpot phase checks", "@@tpot phase packages", "@@tpot tasks 2", "@@tpot phase playbook",
                     "TASK [Gathering Facts] ***", "TASK [Install Docker Engine (All)] ***"]:
            line(text + "\n")
        if FakeEngine.code:
            line("fatal: [127.0.0.1]: FAILED! => {\"msg\": \"no\"}\n")
            return FakeEngine.code
        for text in ["@@tpot phase compose", "@@tpot phase pull", "@@tpot images 1", " cowrie Pulled",
                     "@@tpot phase done"]:
            line(text + "\n")
        return 0


def all_ok():
    return [installer.Check("Distribution", "ok", "Debian GNU/Linux 13"), installer.Check("sudo", "ok", "x")]


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class AssistantTest(unittest.IsolatedAsyncioTestCase):

    def make_app(self, sudo="password", checks=all_ok, password_state=lambda: True):
        from tpotctl.screens.install import InstallApp
        FakeEngine.runs, FakeEngine.code = [], 0
        self.repo = make_checkout(self)
        self.reboots = []
        return InstallApp(engine=FakeEngine, checks=checks, sudo_mode=sudo,
                          password_ok=lambda pw: pw == "right", run=lambda *a, **k: self.reboots.append(a[0]),
                          repo_dir=self.repo, password_state=password_state)

    async def test_log_and_actions_keep_geometry_during_progress(self):
        import time
        for size in ((80, 24), (120, 40)):
            with self.subTest(size=size):
                app = self.make_app(sudo="passwordless")
                async with app.run_test(size=size) as pilot:
                    await app.workers.wait_for_complete()
                    app.progress = installer.Progress()
                    app.started = time.monotonic()
                    app.goto("install")
                    app.feed("@@tpot phase playbook\n")
                    app.feed("TASK [One task]\n")
                    await pilot.pause(0.2)
                    log = app.query_one("#install-log")
                    before = (log.region, app.query_one("#ins-nav").region)
                    app.feed("TASK [" + "A long task description " * 10 + "]\n")
                    await pilot.pause(0.2)
                    self.assertEqual((log.region, app.query_one("#ins-nav").region), before)
                    self.assertGreaterEqual(log.content_region.height, 3)

    async def test_pull_labels_and_full_log_remain_usable_at_80_columns(self):
        import time
        app = self.make_app(sudo="passwordless")
        async with app.run_test(size=(80, 24)) as pilot:
            await app.workers.wait_for_complete()
            app.progress = installer.Progress()
            app.started = time.monotonic()
            app.goto("install")
            app.feed("@@tpot phase pull\n")
            await pilot.pause(0.2)
            self.assertIn("total unknown", str(app.query_one("#install-phase").render()))
            log = app.query_one("#install-log")
            before = (log.region, app.query_one("#ins-nav").region)
            app.feed('@@tpot pull {"version":1,"done":2,"total":3,"activity":"example/a:tag: Pulling"}\n')
            await pilot.pause(0.2)
            self.assertIn("2 of 3 images ready", str(app.query_one("#install-phase").render()))
            self.assertIn("Overall installation", str(app.query_one("#install-phase").render()))
            self.assertEqual((log.region, app.query_one("#ins-nav").region), before)
            await pilot.press("l")
            await pilot.pause(0.2)
            self.assertGreater(log.region.height, before[0].height)
            await pilot.press("l")
            await pilot.pause(0.2)
            self.assertEqual((log.region, app.query_one("#ins-nav").region), before)
            app.feed("@@tpot pull {\n")
            await pilot.pause(0.2)
            self.assertIn("Invalid image progress", "\n".join(line.text for line in log.lines))
            self.assertIn("2 of 3 images ready", str(app.query_one("#install-phase").render()))

    async def test_hive_and_sensor_require_a_deliberate_sudo_choice(self):
        for edition in ("h", "s"):
            with self.subTest(edition=edition):
                app = self.make_app(sudo="passwordless")
                async with app.run_test(size=(80, 24)) as pilot:
                    await pilot.pause(0.4)
                    app.answers.edition = installer.EDITION_BY_LETTER[edition]
                    app.goto("review")
                    await pilot.pause(0.2)
                    self.assertTrue(app.query_one("#ins-next").disabled)
                    app.start_install()
                    await pilot.pause(0.2)
                    self.assertEqual(app.step, "review")
                    self.assertEqual(FakeEngine.runs, [])
                    self.assertFalse(os.path.exists(os.path.join(self.repo, ".env")))

    async def test_secure_locked_account_needs_matching_strong_user_passwords(self):
        from unittest import mock
        with mock.patch.object(installer, "user_password_usable", return_value=False, create=True):
            app = self.make_app(sudo="passwordless", password_state=installer.user_password_usable)
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.pause(0.4)
                app.answers.edition = installer.EDITION_BY_LETTER["s"]
                app.goto("review")
                app.query_one("#ins-sudo-mode").value = "secure"
                await pilot.pause(0.2)
                self.assertTrue(app.query_one("#ins-next").disabled)
                self.assertIn("SSH", str(app.query_one("#ins-account-hint").render()))
                first, repeat = app.query_one("#ins-account-password"), app.query_one("#ins-account-repeat")
                first.value, repeat.value = "short", "short"
                await pilot.pause(0.2)
                self.assertTrue(app.query_one("#ins-next").disabled)
                first.value, repeat.value = "  A new strong password 974!  ", "other"
                await pilot.pause(0.2)
                self.assertTrue(app.query_one("#ins-next").disabled)
                repeat.value = first.value
                await pilot.pause(0.2)
                self.assertFalse(app.query_one("#ins-next").disabled)
                app.start_install()
                await pilot.pause(0.8)
                self.assertEqual(app.step, "done")
                self.assertEqual(app.answers.user_password, "  A new strong password 974!  ")
                command = FakeEngine.runs[0]
                self.assertIn("--user-password-file", command)
                self.assertNotIn(app.answers.user_password, " ".join(command))
                self.assertFalse(os.path.exists(command[command.index("--user-password-file") + 1]))

    async def test_convenient_does_not_require_a_new_user_password(self):
        from unittest import mock
        with mock.patch.object(installer, "user_password_usable", return_value=False, create=True):
            app = self.make_app(sudo="passwordless", password_state=installer.user_password_usable)
            async with app.run_test(size=(100, 36)) as pilot:
                await pilot.pause(0.4)
                app.answers.edition = installer.EDITION_BY_LETTER["s"]
                app.goto("review")
                app.query_one("#ins-sudo-mode").value = "convenient"
                await pilot.pause(0.2)
                self.assertFalse(app.query_one("#ins-next").disabled)
                app.start_install()
                await pilot.pause(0.8)
                self.assertEqual(app.step, "done")
                self.assertIn("--sudo-nopasswd", FakeEngine.runs[0])
                self.assertNotIn("--user-password-file", FakeEngine.runs[0])

    async def test_weak_supplied_user_password_blocks_configuration_changes(self):
        app = self.make_app(sudo="passwordless", password_state=lambda: False)
        app.answers.user_password = "short"
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.4)
            app.answers.edition = installer.EDITION_BY_LETTER["s"]
            app.goto("review")
            app.query_one("#ins-sudo-mode").value = "secure"
            await pilot.pause(0.2)
            self.assertTrue(app.query_one("#ins-next").disabled)
            app.start_install()
            await pilot.pause(0.2)
            self.assertEqual(FakeEngine.runs, [])
            self.assertFalse(os.path.exists(os.path.join(self.repo, ".env")))

    async def test_password_files_and_explicit_mode_survive_the_cli_handover(self):
        from unittest import mock
        from tpotctl import cli
        from tpotctl.screens.install import InstallApp
        with installer.secret_files(become="  Existing password 974!  ", user="  New password 947!  ") as files:
            captured = []
            def run(app):
                captured.append(app.answers)
                return 0
            with mock.patch.object(cli.sys, "platform", "linux"), \
                    mock.patch.object(installer, "installed", return_value=False), \
                    mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                    mock.patch.object(cli.sys.stdout, "isatty", return_value=True), \
                    mock.patch.object(InstallApp, "run", autospec=True, side_effect=run):
                code = cli.dispatch(["install", "--sudo-nopasswd", "--become-file", files["become"],
                                     "--user-password-file", files["user"]])
            self.assertEqual(code, 0)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].sudo_mode, "convenient")
            self.assertEqual(captured[0].sudo_password, "  Existing password 974!  ")
            self.assertEqual(captured[0].user_password, "  New password 947!  ")
            self.assertTrue(all(os.path.exists(path) for path in files.values()))

    async def walk_to_review(self, app, pilot):
        await pilot.pause(0.4)
        self.assertFalse(app.query_one("#ins-next").disabled)
        await pilot.click("#ins-next")                         # system check -> edition
        await pilot.pause(0.2)
        self.assertEqual(app.step, "edition")
        await pilot.press("enter")                             # Hive
        await pilot.pause(0.2)
        self.assertEqual(app.step, "user")
        app.query_one("#ins-user-name").value = "alice"
        app.query_one("#ins-user-password").value = "a long passphrase"
        app.query_one("#ins-user-repeat").value = "a long passphrase"
        await pilot.pause(0.2)
        await pilot.click("#ins-next")
        await pilot.pause(0.3)
        self.assertEqual(app.step, "settings")
        app.query_one("#set-TPOT_BLACKHOLE").value = True
        await pilot.pause(0.2)
        await pilot.click("#ins-next")
        await pilot.pause(0.3)
        self.assertEqual(app.step, "review")
        app.query_one("#ins-sudo-mode").value = "secure"
        await pilot.pause(0.2)

    async def test_best_effort_warning_is_visible_and_does_not_block_installation(self):
        check = installer.check_distro({"NAME": "Ubuntu", "VERSION_ID": "26.04"})
        self.assertEqual(check.state, "warn")
        app = self.make_app(checks=lambda: [check])
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.4)
            self.assertIn(BEST_EFFORT, str(app.query_one("#check-list").render()))
            self.assertFalse(app.query_one("#ins-next").disabled)
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            self.assertEqual(app.step, "edition")

    async def test_selected_edition_has_a_console_safe_background(self):
        from unittest import mock
        from rich.color import ColorSystem
        from textual import constants
        from tpotctl import theme

        original = theme.color_system()
        self.addCleanup(theme.build, original)
        theme.build("16")
        with mock.patch.object(constants, "COLOR_SYSTEM", "standard"):
            app = self.make_app()
            async with app.run_test(size=(110, 40)) as pilot:
                await pilot.pause(0.4)
                await pilot.click("#ins-next")
                await pilot.pause(0.2)
                options = app.query_one("#edition-list")
                for title in ("Hive", "Sensor"):
                    selected = [segment for y in range(options.size.height)
                                for segment in options.render_line(y)
                                if segment.text.strip() == title]
                    self.assertEqual(len(selected), 1)
                    style = selected[0].style
                    assert style is not None and style.bgcolor is not None and style.color is not None
                    # Linux VTs have only eight background colours: bright black becomes black.
                    self.assertEqual(style.bgcolor.downgrade(ColorSystem.STANDARD).number, 5)
                    self.assertEqual(style.color.downgrade(ColorSystem.STANDARD).number, 15)
                    await pilot.press("down")
                    await pilot.pause(0.1)

    async def test_install_a_hive(self):
        app = self.make_app()
        async with app.run_test(size=(140, 44)) as pilot:
            await self.walk_to_review(app, pilot)
            self.assertIn("SSH moves to port 64295", str(app.query_one("#review-text").render()))
            self.assertTrue(app.query_one("#ins-next").disabled)          # sudo password missing
            app.query_one("#ins-sudo").value = "wrong"
            await pilot.pause(0.1)
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            self.assertEqual(app.step, "review")
            self.assertIn("does not accept", str(app.query_one("#ins-sudo-hint").render()))
            app.query_one("#ins-sudo").value = "right"
            await pilot.pause(0.1)
            await pilot.click("#ins-next")
            await pilot.pause(1.0)
            self.assertEqual(app.step, "done")
            await pilot.click("#done-reboot")
            await pilot.pause(0.2)
        command = FakeEngine.runs[0]
        self.assertEqual(command[command.index("-t") + 1], "h")
        self.assertIn("-B", command)
        self.assertEqual(FakeEngine.password, "a long passphrase\n")
        self.assertNotIn("a long passphrase", " ".join(command))
        self.assertTrue(command[0].startswith(self.repo))
        self.assertEqual(self.reboots, [["sudo", "-S", "-p", "", "reboot"]])
        from tpotctl.envfile import EnvFile
        self.assertEqual(EnvFile(os.path.join(self.repo, ".env")).values()["TPOT_BLACKHOLE"], "ENABLED")

    async def test_fresh_checkout_offers_settings_without_creating_files_until_confirmed(self):
        app = self.make_app(sudo="passwordless")
        env_path = os.path.join(self.repo, ".env")
        compose_path = os.path.join(self.repo, "docker-compose.yml")
        self.assertFalse(os.path.exists(env_path))
        self.assertFalse(os.path.exists(compose_path))
        async with app.run_test(size=(140, 44)) as pilot:
            await self.walk_to_review(app, pilot)
            self.assertFalse(os.path.exists(env_path))
            self.assertFalse(os.path.exists(compose_path))
            await pilot.click("#ins-next")
            await pilot.pause(1.0)
            self.assertEqual(app.step, "done")
        from tpotctl.envfile import EnvFile
        self.assertEqual(EnvFile(env_path).values()["TPOT_BLACKHOLE"], "ENABLED")
        self.assertEqual(os.stat(env_path).st_mode & 0o777, 0o600)

    async def test_sensor_skips_the_web_user(self):
        app = self.make_app(sudo="passwordless")
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.4)
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            await pilot.press("down", "enter")                 # Sensor
            await pilot.pause(0.3)
            self.assertEqual(app.step, "settings")
            self.assertNotIn("user", app.steps())
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            self.assertFalse(app.query_one("#ins-sudo").display)       # passwordless
            app.query_one("#ins-sudo-mode").value = "secure"
            await pilot.pause(0.2)
            await pilot.click("#ins-next")
            await pilot.pause(1.0)
            self.assertEqual(app.step, "done")
            self.assertIn("tpot sensors add", str(app.query_one("#done-text").render()))
        command = FakeEngine.runs[0]
        self.assertEqual(command[command.index("-t") + 1], "s")
        self.assertNotIn("-B", command)
        self.assertNotIn("-u", command)

    async def test_the_automatic_updates_only_on_debian(self):
        """On Debian (Raspberry Pi OS 64-bit included, its ID is debian) the settings step offers the switch and
        the review names the updates while they are on; turned off, .env says so before install.sh runs.
        Elsewhere the playbook sets none up: no switch, nothing in the review."""
        from unittest import mock
        from textual.css.query import NoMatches
        from tpotctl.envfile import EnvFile
        for release, offered, turn_off in (({"ID": "debian"}, True, False), ({"ID": "debian"}, True, True),
                                           ({"ID": "fedora"}, False, False)):
            with self.subTest(release=release["ID"], turn_off=turn_off), \
                    mock.patch.object(installer, "os_release", return_value=release):
                app = self.make_app(sudo="passwordless")
                async with app.run_test(size=(140, 44)) as pilot:
                    await pilot.pause(0.4)
                    await pilot.click("#ins-next")
                    await pilot.pause(0.2)
                    await pilot.press("down", "enter")             # Sensor
                    await pilot.pause(0.3)
                    self.assertEqual(app.step, "settings")
                    if offered:
                        switch = app.query_one("#set-TPOT_AUTO_UPDATES")
                        self.assertTrue(switch.value)
                        switch.value = not turn_off
                        await pilot.pause(0.2)
                    else:
                        with self.assertRaises(NoMatches):
                            app.query_one("#set-TPOT_AUTO_UPDATES")
                    await pilot.click("#ins-next")
                    await pilot.pause(0.2)
                    self.assertEqual(app.step, "review")
                    app.query_one("#ins-sudo-mode").value = "secure"
                    await pilot.pause(0.2)
                    review = str(app.query_one("#review-text").render())
                    self.assertEqual("weekly security updates" in review, offered and not turn_off, review)
                    await pilot.click("#ins-next")
                    await pilot.pause(1.0)
                    self.assertEqual(app.step, "done")
                self.assertNotIn("-U", FakeEngine.runs[0])
                self.assertEqual(EnvFile(os.path.join(self.repo, ".env")).values()["TPOT_AUTO_UPDATES"],
                                 "DISABLED" if turn_off else "ENABLED")

    async def test_a_schema_that_does_not_hold_together_is_named(self):
        from unittest import mock
        from tpotctl import envschema
        app = self.make_app(sudo="passwordless")
        notes = []
        app.notify = lambda message, **kwargs: notes.append(message)
        broken = mock.patch.object(envschema, "load_schema", side_effect=envschema.SchemaError("LIST: x is in no group"))
        with broken:
            async with app.run_test(size=(140, 44)) as pilot:
                await pilot.pause(0.4)
                await pilot.click("#ins-next")
                await pilot.pause(0.2)
                await pilot.press("down", "enter")             # Sensor
                await pilot.pause(0.3)
                self.assertEqual(app.step, "settings")
        said = [n for n in notes if "LIST: x is in no group" in n]
        self.assertEqual(len(said), 1, notes)
        self.assertIn("env.schema.yml", said[0])
        self.assertNotIn(".env cannot be read", said[0])

    async def done_text(self, on_path):
        from unittest import mock
        app = self.make_app(sudo="passwordless")
        self.assertEqual(app.repo_dir, self.repo)
        with mock.patch.object(installer, "tpot_on_path", return_value=on_path) as on_path_of:
            async with app.run_test(size=(140, 44)) as pilot:
                await pilot.pause(0.4)
                await pilot.click("#ins-next")
                await pilot.pause(0.2)
                await pilot.press("down", "enter")             # Sensor
                await pilot.pause(0.3)
                await pilot.click("#ins-next")
                await pilot.pause(0.2)
                app.query_one("#ins-sudo-mode").value = "secure"
                await pilot.pause(0.2)
                await pilot.click("#ins-next")
                await pilot.pause(1.0)
                self.assertEqual(app.step, "done")
                on_path_of.assert_called_with(app.repo_dir)      # the checkout it installs, not the default
                return str(app.query_one("#done-text").render())

    async def test_done_says_how_the_manager_runs(self):
        text = await self.done_text(True)
        self.assertTrue("the T-Pot Manager: tpot" in text, text)
        text = await self.done_text(False)
        self.assertTrue("sudo ln -sfn" in text and "/usr/local/bin/tpot" in text, text)
        launcher = os.path.join(self.repo, "tpot")              # the checkout the assistant runs from
        self.assertTrue(launcher in text.replace("\n", ""), text)

    async def test_a_failed_check_blocks(self):
        app = self.make_app(checks=lambda: [installer.Check("Ports", "fail", "tcp/25 occupied")])
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.4)
            self.assertTrue(app.query_one("#ins-next").disabled)
            self.assertIn("tcp/25", str(app.query_one("#check-list").render()))

    async def test_a_failed_installation_says_where(self):
        app = self.make_app(sudo="passwordless")
        async with app.run_test(size=(140, 44)) as pilot:
            FakeEngine.code = 2
            await pilot.pause(0.4)
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            await pilot.press("down", "enter")
            await pilot.pause(0.3)
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            app.query_one("#ins-sudo-mode").value = "secure"
            await pilot.pause(0.2)
            await pilot.click("#ins-next")
            await pilot.pause(1.0)
            self.assertIn("Install Docker Engine (All)", str(app.query_one("#install-phase").render()))
            self.assertFalse(app.busy)

    async def failed_text(self, lines, code=1):
        """The text of a sensor installation whose install.sh prints lines, then ends with code."""
        from tpotctl.screens.install import InstallApp

        class Failing:
            def __init__(self, command):
                self.command = command

            def run(self, line):
                for text in lines:
                    line(text + "\n")
                return code
        self.repo = make_checkout(self)
        app = InstallApp(engine=Failing, checks=all_ok, sudo_mode="passwordless", password_ok=lambda pw: True,
                         run=lambda *a, **k: None, repo_dir=self.repo, password_state=lambda: True)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.4)
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            await pilot.press("down", "enter")                 # Sensor
            await pilot.pause(0.3)
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            app.query_one("#ins-sudo-mode").value = "secure"
            await pilot.pause(0.2)
            await pilot.click("#ins-next")
            for _ in range(80):                                # under load the engine thread takes a while
                await pilot.pause(0.05)
                if not app.busy:
                    break
            self.assertFalse(app.busy)
            self.bar = app.query_one("#install-bar").progress
            return str(app.query_one("#install-phase").render()).replace("\n", " ")

    async def test_a_failed_installation_names_the_log_of_its_step(self):
        """install.sh writes a log per step: the packages to install_tpot_prepare.log, the playbook to
        install_tpot.log, the pull to install_tpot_pull.log; the checks, the edition and the web user
        write none, their errors are only in the output of the assistant. A phase failed mark after it
        does not hide which step it was, a fail <phase> mark names it."""
        logs = ("~/install_tpot_prepare.log", "~/install_tpot.log", "~/install_tpot_pull.log")
        output = "The output below shows why"
        cases = [(["@@tpot phase checks"], output),
                 (["@@tpot phase checks", "@@tpot phase packages"], logs[0]),
                 (["@@tpot phase checks", "@@tpot phase packages", "@@tpot fail packages", "@@tpot phase failed"],
                  logs[0]),
                 (["@@tpot phase playbook", "@@tpot phase compose", "@@tpot phase failed"], output),
                 (["@@tpot phase playbook", "@@tpot phase compose", "@@tpot phase user"], output),
                 (["@@tpot phase playbook", "@@tpot phase compose", "@@tpot phase pull", "@@tpot fail compose"],
                  output),
                 (["@@tpot phase checks", "@@tpot phase packages", "@@tpot tasks 2", "@@tpot phase playbook",
                   "TASK [Gathering Facts] ***", "fatal: [127.0.0.1]: FAILED! => {}", "@@tpot phase failed"],
                  logs[1]),
                 (["@@tpot phase playbook", "TASK [Gathering Facts] ***", "@@tpot fail playbook"], logs[1]),
                 (["@@tpot phase playbook", "@@tpot phase compose", "@@tpot phase pull", "@@tpot images 3",
                   "@@tpot phase failed"], logs[2])]
        for lines, log in cases:
            with self.subTest(lines=" | ".join(lines)):
                text = await self.failed_text(lines)
                self.assertIn("The installation failed", text)
                self.assertIn(log, text)
                self.assertEqual([other for other in logs if other in text], [log] if log in logs else [])

    async def test_a_failed_installation_keeps_the_bar(self):
        lines = ["@@tpot phase checks", "@@tpot phase packages", "@@tpot tasks 2", "@@tpot phase playbook",
                 "TASK [Gathering Facts] ***", "fatal: [127.0.0.1]: FAILED! => {}", "@@tpot fail playbook",
                 "@@tpot phase failed"]
        text = await self.failed_text(lines)
        self.assertIn("The installation failed", text)
        self.assertGreater(self.bar, 50)            # of 1000: where the playbook was, not 0

    async def test_a_stopped_installation_says_stopped(self):
        """install.sh exits 130 when it was stopped (install_stopped): not a failure; during the pull T-Pot
        is installed already and pulls the missing images when it starts."""
        cases = [(["@@tpot phase checks", "@@tpot phase packages"], 130, "Run the installer again"),
                 (["@@tpot phase playbook", "@@tpot phase compose", "@@tpot phase pull"], 130,
                  "T-Pot is installed, it pulls the missing images when it starts"),
                 (["@@tpot phase playbook"], -2, "Run the installer again")]       # ended by SIGINT itself
        for lines, code, next_step in cases:
            with self.subTest(lines=" | ".join(lines), code=code):
                text = await self.failed_text(lines, code)
                self.assertIn("The installation was stopped", text)
                self.assertNotIn("failed", text)
                self.assertIn(next_step, text)

    async def test_quitting_before_the_install_asks(self):
        from tpotctl.screens.dialogs import ConfirmDialog
        app = self.make_app()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("q")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ConfirmDialog)


class UninstallHandoverTest(unittest.TestCase):

    @unittest.skipUnless(textual, "run with the T-Pot Manager venv")
    def test_exec_failure_removes_the_handover_password_file(self):
        from pathlib import Path
        from unittest import mock
        from tpotctl.screens.uninstall import run_handover
        target = installer.uninstall_handover(False, "not-a-real-password", script="/missing/uninstall.sh")
        argv, _ = target
        path = Path(argv[argv.index("-B") + 1])
        self.addCleanup(path.unlink, missing_ok=True)
        with mock.patch("os.chdir"), mock.patch("os.execve", side_effect=OSError("exec failed")):
            with self.assertRaisesRegex(OSError, "exec failed"):
                run_handover(target)
        self.assertFalse(path.exists(), "A failed exec must not abandon the collected sudo password.")

    def test_arguments_and_password_file(self):
        argv, env = installer.uninstall_handover(backup=True, sudo_password="pw", script="/x/uninstall.sh")
        self.assertEqual(argv[:3], ["/x/uninstall.sh", "-y", "-k"])
        path = argv[argv.index("-B") + 1]
        self.addCleanup(os.unlink, path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(env["TPOT_REMOVE_BECOME_FILE"], "1")
        self.assertNotIn("pw", argv)
        argv, env = installer.uninstall_handover(backup=False, script="/x/uninstall.sh")
        self.assertEqual(argv, ["/x/uninstall.sh", "-y"])
        self.assertNotIn("TPOT_REMOVE_BECOME_FILE", env)

    @unittest.skipUnless(shutil.which("bash"), "no bash")
    def test_uninstall_sh_has_its_options(self):
        script = os.path.join(REPO_DIR, "uninstall.sh")
        self.assertEqual(subprocess.run(["bash", "-n", script]).returncode, 0)
        help_text = subprocess.run(["bash", script, "-h"], stdout=subprocess.PIPE, universal_newlines=True).stdout
        for option in ("-y", "-k", "-B"):
            self.assertIn(f"  {option} ", help_text)
        with open(os.path.join(REPO_DIR, "update.sh"), encoding="utf-8") as handle:
            self.assertIn("--backup-only) myARGV+=(\"-o\")", handle.read())


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class UninstallScreenTest(unittest.IsolatedAsyncioTestCase):

    async def test_explicit_keep_sudo_rule_reaches_the_uninstaller(self):
        from tpotctl.screens.uninstall import UninstallApp
        app = UninstallApp(sudo_mode="passwordless", hostname="honey")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertTrue(app.screen.query("#un-keep-sudo"), "Uninstall must offer keeping the sudo rule.")
            self.assertFalse(app.screen.query_one("#un-keep-sudo").value)
            app.screen.query_one("#un-keep-sudo").value = True
            app.screen.query_one("#un-backup").value = False
            app.screen.query_one("#un-host").value = "honey"
            await pilot.pause()
            app.screen.query_one("#un-go").press()
            await pilot.pause()
        argv, _ = app.return_value
        self.assertIn("--keep-sudo-rule", argv)

    async def test_confirm_with_the_host_name(self):
        from tpotctl.screens.uninstall import UninstallApp
        app = UninstallApp(sudo_mode="password", password_ok=lambda pw: pw == "right", hostname="honey")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            go = app.screen.query_one("#un-go")
            self.assertTrue(go.disabled)
            app.screen.query_one("#un-host").value = "honey"
            await pilot.pause(0.1)
            self.assertTrue(go.disabled)                       # the sudo password is missing
            app.screen.query_one("#un-sudo").value = "wrong"
            await pilot.pause(0.1)
            await pilot.click("#un-go")
            await pilot.pause(0.2)
            self.assertIn("does not accept", str(app.screen.query_one("#un-hint").render()))
            app.screen.query_one("#un-sudo").value = "right"
            app.screen.query_one("#un-backup").value = False
            await pilot.pause(0.1)
            await pilot.click("#un-go")
            await pilot.pause(0.3)
        argv, env = app.return_value
        self.addCleanup(os.unlink, argv[argv.index("-B") + 1])
        self.assertEqual(argv[1:3], ["-y", "-B"])
        self.assertNotIn("-k", argv)

    async def test_keep_tpot(self):
        from tpotctl.screens.uninstall import UninstallApp
        app = UninstallApp(sudo_mode="passwordless", hostname="honey")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            self.assertFalse(app.screen.query_one("#un-sudo").display)
            await pilot.press("escape")
            await pilot.pause(0.2)
        self.assertIsNone(app.return_value)


if __name__ == "__main__":
    unittest.main()
