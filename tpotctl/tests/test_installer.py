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
