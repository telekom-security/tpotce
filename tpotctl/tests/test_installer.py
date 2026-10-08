"""The installer: install.sh and its gum block, tpot install (checks, engine, progress, assistant).

The assistant runs in a temporary checkout with a fake engine, nothing here touches
the checkout the tests run from or the host.
"""

import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error

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


def block(text):
    return text[text.index("# >>> tpot ui >>>"):text.index("# <<< tpot ui <<<")]


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
    "debian": ('NAME="Debian GNU/Linux"\nVERSION_ID="13"\n', "ok"),
    "ubuntu": ('NAME="Ubuntu"\nVERSION_ID="26.04"\n', "ok"),
    "old ubuntu": ('NAME="Ubuntu"\nVERSION_ID="24.04"\n', "fail"),
    "fedora": ('NAME="Fedora Linux"\nVERSION_ID=44\n', "ok"),
    "alma": ('NAME="AlmaLinux"\nVERSION_ID="10.1"\n', "ok"),
    "tumbleweed": ('NAME="openSUSE Tumbleweed"\nVERSION_ID="20261001"\n', "ok"),
    "arch": ('NAME="Arch Linux"\n', "fail"),
}


@unittest.skipUnless(yaml, "PyYAML is not installed")
class SshColortermTest(unittest.TestCase):
    """sshd takes COLORTERM from the client, so the T-Pot Manager shows true colours over SSH."""

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
        self.assertEqual(task["lineinfile"]["line"], "AcceptEnv COLORTERM")
        for name in ("AlmaLinux", "Debian", "Fedora", "openSUSE Tumbleweed", "Raspbian", "RedHat", "Rocky", "Ubuntu"):
            self.assertTrue(f'"{name}"' in task["when"], name)
        tasks = self.tasks("installer", "install", "tpot.yml")
        checks = [i for i, t in enumerate(tasks) if str(t.get("command", "")) == "/usr/sbin/sshd -t"]
        self.assertEqual(len(checks), 1)
        # sshd -t needs its privilege separation directory, which only a running ssh.service has on the
        # Debian family (Ubuntu stops ssh.socket before, Raspberry Pi OS ships with SSH off)
        privsep = [i for i, t in enumerate(tasks) if (t.get("file") or {}).get("path") == "/run/sshd"]
        self.assertTrue(privsep and privsep[0] < checks[0], privsep)
        for name in ("Debian", "Raspbian", "Ubuntu"):
            self.assertTrue(f'"{name}"' in tasks[privsep[0]]["when"], name)
        directory = [t for t in tasks if (t.get("file") or {}).get("path") == "/etc/ssh/sshd_config.d"]
        self.assertEqual(len(directory), 1)
        self.assertNotIn("mode", directory[0]["file"])     # RHEL-likes keep it 0700

    def test_uninstall_removes_it(self):
        found = [t for t in self.tasks("installer", "remove", "tpot.yml")
                 if (t.get("file") or {}).get("path") == self.DROPIN and t["file"].get("state") == "absent"]
        self.assertEqual(len(found), 1, found)


class ChecksTest(unittest.TestCase):

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
                self.assertEqual(installer.check_distro(installer.os_release(path)).state, state)

    def test_root(self):
        self.assertEqual(installer.check_root(0).state, "fail")
        self.assertEqual(installer.check_root(1000).state, "ok")

    def test_ports_ignore_the_resolved_stub(self):
        stub = "UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:*\nUNCONN 0 0 127.0.0.54:53 0.0.0.0:*\n"
        self.assertEqual(installer.occupied(stub), [])
        self.assertEqual(installer.occupied("LISTEN 0 100 0.0.0.0:25 0.0.0.0:*\n"), ["0.0.0.0:25"])
        self.assertEqual(installer.occupied("LISTEN 0 100 [::1]:25 [::]:*\n"), ["[::1]:25"])

    def test_reach_counts_any_answer(self):
        def opener(request, timeout):
            if "docker" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
            if "ghcr" in request.full_url:
                raise urllib.error.URLError("no route")
            return io.BytesIO(b"")
        check = installer.check_reach(opener)
        self.assertEqual(check.state, "fail")
        self.assertIn("GitHub Container Registry", check.detail)
        self.assertNotIn("Docker Hub", check.detail)

    def test_resources(self):
        hive = installer.EDITION_BY_LETTER["h"]
        self.assertEqual(installer.check_resources(hive, 15.6, 300).state, "ok")     # a 16 GB machine
        self.assertEqual(installer.check_resources(hive, 8, 300).state, "warn")
        self.assertEqual(installer.meminfo_gb("MemTotal:       16384000 kB\n"), 16384000 / 1024 / 1024)


class EngineTest(unittest.TestCase):

    def test_no_password_in_the_command_and_files_are_private(self):
        answers = installer.Answers(installer.EDITION_BY_LETTER["h"], web_user="alice", web_password="s3cret-pass",
                                    sudo_password="sudo-pass")
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
        sensor = installer.Answers(installer.EDITION_BY_LETTER["s"])
        with installer.secret_files(web="", become="") as files:
            command = installer.engine_command(sensor, files, "/x/install.sh")
        self.assertNotIn("-u", command)
        self.assertNotIn("-B", command)
        custom = installer.Answers(installer.EDITION_BY_LETTER["h"], custom_compose="/r/docker-compose-custom.yml",
                                   web_user="bob", web_password="pw")
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


def make_checkout(test):
    """A checkout to install from: env.example as .env, the compose folder."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    shutil.copy(os.path.join(REPO_DIR, "env.example"), os.path.join(tmp.name, ".env"))
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

    def make_app(self, sudo="password", checks=all_ok):
        from tpotctl.screens.install import InstallApp
        FakeEngine.runs, FakeEngine.code = [], 0
        self.repo = make_checkout(self)
        self.reboots = []
        return InstallApp(engine=FakeEngine, checks=checks, sudo_mode=sudo,
                          password_ok=lambda pw: pw == "right", run=lambda *a, **k: self.reboots.append(a[0]),
                          repo_dir=self.repo)

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
            await pilot.click("#ins-next")
            await pilot.pause(1.0)
            self.assertEqual(app.step, "done")
            self.assertIn("tpot sensors add", str(app.query_one("#done-text").render()))
        command = FakeEngine.runs[0]
        self.assertEqual(command[command.index("-t") + 1], "s")
        self.assertNotIn("-B", command)
        self.assertNotIn("-u", command)

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
            await pilot.click("#ins-next")
            await pilot.pause(1.0)
            self.assertIn("Install Docker Engine (All)", str(app.query_one("#install-phase").render()))
            self.assertFalse(app.busy)

    async def failed_text(self, lines):
        """The text of a sensor installation whose install.sh prints lines, then fails."""
        from tpotctl.screens.install import InstallApp

        class Failing:
            def __init__(self, command):
                self.command = command

            def run(self, line):
                for text in lines:
                    line(text + "\n")
                return 1
        self.repo = make_checkout(self)
        app = InstallApp(engine=Failing, checks=all_ok, sudo_mode="passwordless", password_ok=lambda pw: True,
                         run=lambda *a, **k: None, repo_dir=self.repo)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.4)
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            await pilot.press("down", "enter")                 # Sensor
            await pilot.pause(0.3)
            await pilot.click("#ins-next")
            await pilot.pause(0.2)
            await pilot.click("#ins-next")
            for _ in range(80):                                # under load the engine thread takes a while
                await pilot.pause(0.05)
                if not app.busy:
                    break
            self.assertFalse(app.busy)
            return str(app.query_one("#install-phase").render()).replace("\n", " ")

    async def test_a_failed_installation_names_the_log_of_its_step(self):
        """install.sh writes a log per step: the packages and checks to install_tpot_prepare.log, the
        playbook to install_tpot.log, the pull to install_tpot_pull.log; a phase failed mark after
        it does not hide which step it was."""
        logs = ("~/install_tpot_prepare.log", "~/install_tpot.log", "~/install_tpot_pull.log")
        cases = [(["@@tpot phase checks"], logs[0]),
                 (["@@tpot phase checks", "@@tpot phase packages"], logs[0]),
                 (["@@tpot phase checks", "@@tpot phase packages", "@@tpot tasks 2", "@@tpot phase playbook",
                   "TASK [Gathering Facts] ***", "fatal: [127.0.0.1]: FAILED! => {}", "@@tpot phase failed"],
                  logs[1]),
                 (["@@tpot phase playbook", "@@tpot phase compose"], logs[1]),
                 (["@@tpot phase playbook", "@@tpot phase compose", "@@tpot phase pull", "@@tpot images 3",
                   "@@tpot phase failed"], logs[2])]
        for lines, log in cases:
            with self.subTest(lines=" | ".join(lines)):
                text = await self.failed_text(lines)
                self.assertIn(log, text)
                self.assertEqual([other for other in logs if other in text], [log])

    async def test_quitting_before_the_install_asks(self):
        from tpotctl.screens.dialogs import ConfirmDialog
        app = self.make_app()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("q")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ConfirmDialog)


class UninstallHandoverTest(unittest.TestCase):

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
