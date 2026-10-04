"""tpot env and the Settings page, on a copy of env.example in a temporary checkout."""

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None
try:
    import textual  # noqa: F401
except ImportError:
    textual = None
try:
    import rich  # noqa: F401
except ImportError:
    rich = None

from tpotctl.bootstrap import REPO_DIR  # noqa: E402

WEB_USER = "dHNlYzokYXByMSRYUnE2SC5rbiRVRjZQM1VVQmJVNWJUQmNmSGRuUFQxCgo="


def make_checkout(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    with open(os.path.join(REPO_DIR, "env.example"), encoding="utf-8") as handle:
        text = handle.read().replace("WEB_USER=\n", f"WEB_USER={WEB_USER}\n", 1)
    with open(os.path.join(tmp.name, ".env"), "w", encoding="utf-8") as handle:
        handle.write(text)
    shutil.copy(os.path.join(REPO_DIR, "compose", "standard.yml"), os.path.join(tmp.name, "docker-compose.yml"))
    return tmp.name


@unittest.skipUnless(yaml, "PyYAML is not installed")
class SettingsTest(unittest.TestCase):

    def setUp(self):
        from tpotctl import settings
        self.settings = settings
        self.repo = make_checkout(self)

    def load(self):
        return self.settings.load(self.repo)

    def test_relevant_keys_of_a_standard_hive(self):
        keys = [r.key for r in self.load().relevant()]
        self.assertIn("WEB_USER", keys)
        self.assertIn("OINKCODE", keys)                 # suricata runs in standard
        self.assertNotIn("TPOT_HIVE_IP", keys)          # SENSOR only
        self.assertNotIn("GALAH_LLM_PROVIDER", keys)    # galah is not in standard
        self.assertIn("GALAH_LLM_PROVIDER", [r.key for r in self.load().relevant(include_all=True)])

    def test_change_writes_valid_values(self):
        warnings = self.load().change({"TPOT_BLACKHOLE": "ENABLED", "TPOT_PERSISTENCE_CYCLES": "60"})
        self.assertEqual(warnings, [])
        values = self.load().values
        self.assertEqual((values["TPOT_BLACKHOLE"], values["TPOT_PERSISTENCE_CYCLES"]), ("ENABLED", "60"))

    def test_change_refuses_invalid_fixed_and_unknown_keys(self):
        for changes in ({"TPOT_PERSISTENCE": "true"}, {"WEB_USER": WEB_USER}, {"TPOT_VERSION": "1"},
                        {"NOT_A_SETTING": "1"}, {"TPOT_PERSISTENCE_CYCLES": "5000"}):
            with self.subTest(changes=changes):
                with self.assertRaises(self.settings.SettingsError):
                    self.load().change(changes)
        self.assertEqual(self.load().values["TPOT_PERSISTENCE"], "on")

    def test_unrelated_errors_do_not_block(self):
        from tpotctl.envfile import EnvFile
        env = EnvFile(os.path.join(self.repo, ".env"))
        env.set("TPOT_ATTACKMAP_TEXT", "maybe")
        env.save()
        self.load().change({"TPOT_BLACKHOLE": "ENABLED"})
        self.assertEqual(self.load().values["TPOT_BLACKHOLE"], "ENABLED")

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_cli(self):
        from tpotctl import cli
        original = self.settings.load
        with mock.patch.object(self.settings, "load", lambda: original(self.repo)):
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["env", "set", "TPOT_ATTACKMAP_TEXT=DISABLED", "OINKCODE=abc123"]), 0)
                self.assertEqual(cli.main(["env", "get", "OINKCODE"]), 0)
                self.assertEqual(cli.main(["env", "check"]), 0)
                self.assertEqual(cli.main(["env", "set", "TPOT_ATTACKMAP_TEXT=yes"]), 1)
                self.assertEqual(cli.main(["env", "set", "nonsense"]), 2)
                self.assertEqual(cli.main(["env", "list"]), 0)
            text = out.getvalue()
            self.assertIn("abc123\n", text)
            self.assertIn("OINKCODE=••••••", text)          # list masks secrets
        self.assertEqual(self.load().values["TPOT_ATTACKMAP_TEXT"], "DISABLED")


@unittest.skipUnless(yaml, "PyYAML is not installed")
class UnlockTest(unittest.TestCase):

    def setUp(self):
        from tpotctl import settings
        self.settings = settings
        self.repo = make_checkout(self)

    def test_fixed_keys_need_an_unlock(self):
        current = self.settings.load(self.repo)
        self.assertTrue(current.can_unlock("TPOT_VERSION"))
        self.assertFalse(current.can_unlock("WEB_USER"))          # managed with tpot users
        self.assertFalse(current.can_unlock("TPOT_BLACKHOLE"))    # not fixed at all
        with self.assertRaises(self.settings.SettingsError) as caught:
            current.change({"TPOT_VERSION": "24.04.1"})
        self.assertIn("--unlock", str(caught.exception))
        with self.assertRaises(self.settings.SettingsError):
            current.change({"WEB_USER": "x"}, unlocked=["WEB_USER"])
        current.change({"TPOT_VERSION": "24.04.1"}, unlocked=["TPOT_VERSION"])
        self.assertEqual(self.settings.load(self.repo).values["TPOT_VERSION"], "24.04.1")

    def test_unlocked_values_are_still_checked(self):
        current = self.settings.load(self.repo)
        with self.assertRaises(self.settings.SettingsError):
            current.change({"TPOT_OSTYPE": "linux; rm -rf /"}, unlocked=["TPOT_OSTYPE"])

    def test_every_fixed_system_key_has_a_warning(self):
        current = self.settings.load(self.repo)
        for key, rule in current.schema.items():
            if not rule.editable and key not in self.settings.MANAGED_BY:
                self.assertTrue(rule.unlock, f"{key} needs an unlock warning")

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_cli_unlock(self):
        from tpotctl import cli
        original = self.settings.load
        with mock.patch.object(self.settings, "load", lambda: original(self.repo)):
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                refused = cli.main(["env", "set", "TPOT_DATA_PATH=/srv/tpot"])
            self.assertEqual(refused, 1)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                done = cli.main(["env", "set", "--unlock", "TPOT_DATA_PATH=/srv/tpot"])
            self.assertEqual(done, 0, out.getvalue())
            self.assertIn("unlocked", out.getvalue())
        self.assertEqual(self.settings.load(self.repo).values["TPOT_DATA_PATH"], "/srv/tpot")


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class SettingsPaneTest(unittest.IsolatedAsyncioTestCase):

    async def test_unlock_a_fixed_key(self):
        from tpotctl import app as tapp, events, ops, settings
        from tpotctl.screens.dialogs import ConfirmDialog
        repo = make_checkout(self)

        class Backend(tapp.Backend):
            def linux_host(self):
                return False

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "n/a", repo)

            def settings(self):
                return settings.load(repo)

            def attacks(self):
                return events.Attacks(problem="none")

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app.goto_setting("TPOT_DATA_PATH")
            await pilot.pause(0.4)
            self.assertFalse(app.query("#set-TPOT_DATA_PATH"))         # fixed
            self.assertEqual(app.focused.id, "unlock-TPOT_DATA_PATH")
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIn("empty one", str(app.screen.query("Static").last().render()))
            await pilot.click("#yes")
            await pilot.pause(0.4)
            field = app.query_one("#set-TPOT_DATA_PATH")
            self.assertEqual(app.focused, field)
            self.assertIn("unlocked", str(app.query_one("#row-TPOT_DATA_PATH .setting-title").render()))
            field.value = "/srv/tpot"
            await pilot.pause(0.3)
            await pilot.click("#settings-save")
            await pilot.pause(0.4)
            self.assertFalse(app.query("#set-TPOT_DATA_PATH"))         # fixed again after saving
        self.assertEqual(settings.load(repo).values["TPOT_DATA_PATH"], "/srv/tpot")

    async def test_edit_save_restart(self):
        from tpotctl import app as tapp, ops, settings
        from tpotctl.screens.dialogs import ConfirmDialog
        repo = make_checkout(self)

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def images(self):
                return []

            def backups(self):
                return []

            def settings(self):
                return settings.load(repo)

            def system(self):
                return None

            def attacks(self):
                from tpotctl import events
                return events.Attacks(problem="no Elasticsearch in the tests")

        commands = []
        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: commands.append(command) or 0)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause(0.3)
            app.query_one("ContentSwitcher").current = "settings"
            await pilot.pause(0.3)
            pane = app.query_one("#settings")
            save = app.query_one("#settings-save")
            self.assertTrue(save.disabled)
            field = app.query_one("#set-TPOT_PERSISTENCE_CYCLES")
            field.value = "5000"
            await pilot.pause(0.3)
            self.assertTrue(save.disabled)                     # out of range
            self.assertIn("default", str(app.query_one("#err-TPOT_PERSISTENCE_CYCLES").render()))
            field.value = "45"
            app.query_one("#set-TPOT_BLACKHOLE").value = True        # a switch, it writes ENABLED
            await pilot.pause(0.3)
            self.assertFalse(save.disabled)
            self.assertEqual(set(pane.changes()), {"TPOT_PERSISTENCE_CYCLES", "TPOT_BLACKHOLE"})
            await pilot.click("#settings-save")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ConfirmDialog)
            await pilot.click("#yes")
            await pilot.pause(0.3)
        values = settings.load(repo).values
        self.assertEqual((values["TPOT_PERSISTENCE_CYCLES"], values["TPOT_BLACKHOLE"]), ("45", "ENABLED"))
        self.assertEqual(commands, [["sudo", "systemctl", "restart", "tpot"]])


if __name__ == "__main__":
    unittest.main()
