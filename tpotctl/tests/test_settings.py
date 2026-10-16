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

    def test_llm_keys_are_offered_without_their_services(self):
        current = self.load()
        offered = {r.key: r for r in current.relevant(offered=True)}
        for key in ("BEELZEBUB_LLM_PROVIDER", "BEELZEBUB_LLM_HOST", "GALAH_LLM_MODEL", "GALAH_LLM_SERVER_URL"):
            with self.subTest(key=key):
                self.assertIn(key, offered)
                self.assertTrue(offered[key].offer)
                self.assertTrue(current.absent(offered[key]))
        self.assertFalse(current.absent(offered["OINKCODE"]))
        self.assertNotIn("TPOT_HIVE_IP", offered)                 # not offered, SENSOR only

    def test_problems_of_offered_keys_only_warn(self):
        current = self.load()
        values = dict(current.values, GALAH_LLM_TEMPERATURE="9")
        self.assertEqual([p for p in current.problems(values) if p.key == "GALAH_LLM_TEMPERATURE"], [])
        found = [p for p in current.problems(values, offered=True) if p.key == "GALAH_LLM_TEMPERATURE"]
        self.assertEqual([p.level for p in found], ["warning"])
        self.assertEqual(current.blocking(found, {"GALAH_LLM_TEMPERATURE": "9"}), [])

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
        with mock.patch.object(self.settings, "load", lambda **kwargs: original(self.repo, **kwargs)):
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

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_cli_cowrie_personas_and_rotation(self):
        from tpotctl import cli
        original = self.settings.load
        env_path = os.path.join(self.repo, ".env")

        def run(*argv):
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                done = cli.main(list(argv))
            return done, out.getvalue()

        with mock.patch.object(self.settings, "load", lambda **kwargs: original(self.repo, **kwargs)):
            self.assertEqual(run("env", "set", "COWRIE_PERSONA=raspios ipcam", "COWRIE_PERSONA_ROTATION=7")[0], 0)
            self.assertEqual(run("env", "get", "COWRIE_PERSONA"), (0, "raspios ipcam\n"))
            self.assertEqual(run("env", "get", "COWRIE_PERSONA_ROTATION"), (0, "7\n"))
            self.assertEqual(run("env", "set", "COWRIE_PERSONA=", "COWRIE_PERSONA_ROTATION=0")[0], 0)
            self.assertEqual(run("env", "get", "COWRIE_PERSONA"), (0, "\n"))
            self.assertEqual(run("env", "get", "COWRIE_PERSONA_ROTATION"), (0, "0\n"))
            with open(env_path, encoding="utf-8") as handle:
                before = handle.read()
            self.assertEqual(run("env", "set", "COWRIE_PERSONA_ROTATION=366")[0], 1)
            self.assertEqual(run("env", "set", "COWRIE_PERSONA=bogus")[0], 1)
            with open(env_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), before)


@unittest.skipUnless(yaml, "PyYAML is not installed")
class ListSettingTest(unittest.TestCase):
    """A list setting (COWRIE_PERSONA): written and read back as the values separated by spaces."""

    def test_two_saved_and_loaded_none_is_empty(self):
        from tpotctl import settings
        repo = make_checkout(self)
        settings.load(repo).change({"COWRIE_PERSONA": "raspios ipcam"})
        self.assertEqual(settings.load(repo).values["COWRIE_PERSONA"], "raspios ipcam")
        settings.load(repo).change({"COWRIE_PERSONA": ""})
        self.assertEqual(settings.load(repo).values["COWRIE_PERSONA"], "")
        with self.assertRaises(settings.SettingsError):
            settings.load(repo).change({"COWRIE_PERSONA": "raspios raspios"})


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

    def test_a_mac_host_with_linux_is_an_error_with_a_fix(self):
        current = self.settings.load(self.repo, host_ostype="mac")
        self.assertEqual(current.values["TPOT_OSTYPE"], "linux")
        self.assertEqual(current.fixes(), {"TPOT_OSTYPE": "mac"})
        self.assertTrue(any(p.key == "TPOT_OSTYPE" and p.level == "error" and "macOS" in p.text
                            for p in current.problems()))

    def test_the_fix_needs_no_unlock_another_value_does(self):
        current = self.settings.load(self.repo, host_ostype="mac")
        with self.assertRaises(self.settings.SettingsError):
            current.change({"TPOT_OSTYPE": "win"})
        with self.assertRaises(self.settings.SettingsError):
            current.change({"TPOT_OSTYPE": "win"}, unlocked=["TPOT_OSTYPE"])     # tpotinit would not start
        current.change({"TPOT_OSTYPE": "mac"})
        self.assertEqual(self.settings.load(self.repo).values["TPOT_OSTYPE"], "mac")

    def test_a_matching_host_has_nothing_to_fix(self):
        current = self.settings.load(self.repo, host_ostype="linux")
        self.assertEqual(current.fixes(), {})
        self.assertFalse(any(p.key == "TPOT_OSTYPE" for p in current.problems()))

    def test_without_a_host_nothing_changes(self):
        current = self.settings.load(self.repo)
        self.assertEqual(current.fixes(), {})
        self.assertFalse(any(p.key == "TPOT_OSTYPE" for p in current.problems()))

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_cli_on_a_mac(self):
        from tpotctl import cli
        original = self.settings.load
        with mock.patch.object(self.settings, "load", lambda **kwargs: original(self.repo, **kwargs)), \
                mock.patch("tpotctl.ops.host_ostype", return_value="mac"):
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                self.assertEqual(cli.main(["env", "check"]), 1)
            self.assertIn("TPOT_OSTYPE", out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                self.assertEqual(cli.main(["env", "set", "TPOT_OSTYPE=mac"]), 0, out.getvalue())
        self.assertEqual(self.settings.load(self.repo).values["TPOT_OSTYPE"], "mac")

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_cli_unlock(self):
        from tpotctl import cli
        original = self.settings.load
        with mock.patch.object(self.settings, "load", lambda **kwargs: original(self.repo, **kwargs)):
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


def standard_backend(repo):
    from tpotctl import app as tapp, events, ops, settings

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

        def settings(self):
            return settings.load(repo)

        def config_stamp(self):
            return ops.config_stamp(repo)

        def system(self):
            return None

        def attacks(self):
            return events.Attacks(problem="none")

    return Backend()


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class ManyPickerTest(unittest.IsolatedAsyncioTestCase):
    """COWRIE_PERSONA on the Settings page: Choose opens the list to tick, Servers and Devices."""

    async def asyncSetUp(self):
        from tpotctl import app as tapp
        self.repo = make_checkout(self)
        self.app = tapp.TpotApp(backend=standard_backend(self.repo), runner=lambda command, cwd=None: 0)

    async def wait_for(self, pilot, test):
        for _ in range(60):             # polling: under the load of the full suite it takes longer
            await pilot.pause(0.05)
            if test():
                return True
        return False

    async def open_picker(self, pilot):
        from tpotctl.screens.pickers import ManyPicker
        self.app.goto_setting("COWRIE_PERSONA")
        await self.wait_for(pilot, lambda: self.app.focused is not None
                            and self.app.focused.id == "set-COWRIE_PERSONA")
        field = self.app.focused
        for _ in range(len(field.value) + 2):   # right runs through the text, then to Choose
            await pilot.press("right")
            if self.app.focused is not field:
                break
        self.assertEqual(self.app.focused.id, "pick-COWRIE_PERSONA")
        await pilot.press("enter")
        self.assertTrue(await self.wait_for(pilot, lambda: isinstance(self.app.screen, ManyPicker)))
        await pilot.pause(0.1)
        return self.app.screen

    async def tick(self, pilot, picker, value, key="space"):
        listing = picker.query_one("#many-list")
        listing.highlighted = picker.lines.index(("value", value))
        await pilot.pause(0.05)
        await pilot.press(key)
        await pilot.pause(0.05)

    async def save(self, pilot):
        await pilot.press("ctrl+s")
        self.assertTrue(await self.wait_for(pilot, lambda: bool(self.app.screen.query("#no"))))
        await pilot.click("#no")                # no restart of T-Pot
        await pilot.pause(0.4)

    async def test_two_ticked_saved_and_shown_again_none_is_empty(self):
        from tpotctl import settings
        async with self.app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            picker = await self.open_picker(pilot)
            self.assertEqual(picker.chosen, set())
            self.assertIn("Nothing ticked: all 16", str(picker.query_one("#many-status").render()))
            self.assertEqual([line for line in picker.lines if line[0] == "group"],
                             [("group", "Servers"), ("group", "Devices")])
            await self.tick(pilot, picker, "ipcam")              # a device first, the setting keeps the order
            await self.tick(pilot, picker, "raspios", key="enter")
            self.assertIn("2 of 16", str(picker.query_one("#many-status").render()))
            await pilot.click("#many-take")
            await pilot.pause(0.3)
            pane = self.app.query_one("#settings")
            self.assertEqual(pane.changes(), {"COWRIE_PERSONA": "raspios ipcam"})
            await self.save(pilot)
            self.assertEqual(settings.load(self.repo).values["COWRIE_PERSONA"], "raspios ipcam")
            self.assertEqual(self.app.query_one("#set-COWRIE_PERSONA").value, "raspios ipcam")
            picker = await self.open_picker(pilot)
            self.assertEqual(picker.chosen, {"raspios", "ipcam"})          # read back from .env
            await pilot.click("#many-clear")
            await pilot.pause(0.1)
            await pilot.click("#many-take")
            await pilot.pause(0.3)
            self.assertEqual(self.app.query_one("#settings").changes(), {"COWRIE_PERSONA": ""})
            await self.save(pilot)
        self.assertEqual(settings.load(self.repo).values["COWRIE_PERSONA"], "")

    async def test_a_heading_ticks_its_group_and_all_is_empty(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            picker = await self.open_picker(pilot)
            listing = picker.query_one("#many-list")
            listing.highlighted = picker.lines.index(("group", "Devices"))
            await pilot.press("enter")
            await pilot.pause(0.05)
            self.assertEqual(picker.value(), "dlink-dir859 tplink-wr841n zyxel-nas326 openwrt qnap-qts synology-dsm "
                                             "ubiquiti-edgerouter-x ipcam")
            self.assertIn("8 of 8", str(listing.get_option_at_index(listing.highlighted).prompt))
            await pilot.press("space")                          # all of the group ticked: none of it
            await pilot.pause(0.05)
            self.assertEqual(picker.value(), "")
            self.assertEqual(picker.chosen, set())
            for heading in ("Servers", "Devices"):
                listing.highlighted = picker.lines.index(("group", heading))
                await pilot.press("enter")
                await pilot.pause(0.05)
            self.assertEqual(len(picker.chosen), 16)
            self.assertEqual(picker.value(), "")                # all of them: the same as nothing ticked
            self.assertIn("saved empty", str(picker.query_one("#many-status").render()))
            await pilot.press("escape")
            await pilot.pause(0.3)
            self.assertEqual(self.app.query_one("#settings").changes(), {})     # Back changes nothing

    async def test_typed_entries_off_the_list_are_named_and_left_out(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            self.app.goto_setting("COWRIE_PERSONA")
            await pilot.pause(0.4)
            self.app.query_one("#set-COWRIE_PERSONA").value = "raspios bogus nope"
            await pilot.pause(0.2)
            picker = await self.open_picker(pilot)
            self.assertEqual(picker.chosen, {"raspios"})
            status = str(picker.query_one("#many-status").render())
            self.assertIn("bogus, nope are not on the list, Take it leaves them out.", status)
            await self.tick(pilot, picker, "ipcam")
            self.assertIn("leaves them out", str(picker.query_one("#many-status").render()))
            await pilot.click("#many-take")
            await pilot.pause(0.3)
            self.assertEqual(self.app.query_one("#settings").changes(), {"COWRIE_PERSONA": "raspios ipcam"})

    async def test_the_field_says_empty_is_all(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            self.app.goto_setting("COWRIE_PERSONA")
            await pilot.pause(0.4)
            self.assertEqual(self.app.query_one("#set-COWRIE_PERSONA").placeholder, "empty = all 16")
            self.assertIn(("COWRIE_PERSONA", "Cowrie personas"), self.app.setting_keys())      # ctrl+f finds it


if __name__ == "__main__":
    unittest.main()
