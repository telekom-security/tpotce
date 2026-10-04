"""The tpot menu and the customizer screen, driven headless by Textual's pilot.

Skipped where Textual is missing; run them with the venv of tpot:
  ~/.local/share/tpotce/venv/bin/python -m unittest discover tpotctl/tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

try:
    import textual  # noqa: F401
except ImportError:
    textual = None

if textual:
    from tpotctl import app as tapp, cli, ops
    from tpotctl.screens.customizer import CustomizerScreen, PortsDialog, core
    from tpotctl.screens.dialogs import ChoiceDialog, ConfirmDialog

    class FakeBackend(tapp.Backend):
        def __init__(self, host=True):
            self.host = host

        def linux_host(self):
            return self.host

        def status(self):
            return ops.Status("24.04.2", "dev", "abc1234", "STANDARD", "HIVE", "active", "/home/t/tpotce")

        def containers(self):
            return [ops.Container("cowrie", "running", "Up 1 hour (healthy)", "healthy", "22->22/tcp", "c"),
                    ops.Container("conpot_ipmi", "exited", "Exited (1)", "", "", "c")]

        def images(self):
            return [ops.Image("ghcr.io/telekom-security/cowrie", "24.04.2", "abc", "90MB", "2 days ago")]

        def backups(self):
            return []

        def tpot_type(self):
            return "HIVE"

        def system(self):
            from tpotctl import system
            return system.System(12.0, system.Usage(1, 4), system.Usage(1, 10), "/data")

        def attacks(self):
            from tpotctl import events
            return events.Attacks([0, 3, 9, 4] * 15, 1234, [("Cowrie", 900), ("Dionaea", 334)])

    class Recorder:
        def __init__(self):
            self.commands = []

        def __call__(self, command, cwd=None):
            self.commands.append(command)
            return 0


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class MenuTest(unittest.IsolatedAsyncioTestCase):

    async def test_theme_and_panes(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            self.assertEqual(app.theme, "tpot")
            self.assertEqual(app.get_theme("tpot").primary, "#E20074")
            switcher = app.query_one("ContentSwitcher")
            self.assertEqual(switcher.current, "status")
            self.assertEqual(app.query_one("#containers").row_count, 2)
            await pilot.press("down", "down", "down", "down", "down")
            await pilot.pause(0.2)
            self.assertEqual(switcher.current, "images")
            self.assertEqual(app.query_one("#images-table").row_count, 1)

    async def test_restart_asks_then_runs_systemctl(self):
        runner = Recorder()
        app = tapp.TpotApp(backend=FakeBackend(), runner=runner)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            await pilot.click("#svc-restart")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ConfirmDialog)
            await pilot.click("#yes")
            await pilot.pause(0.2)
        self.assertEqual(runner.commands, [["sudo", "systemctl", "restart", "tpot"]])

    async def test_update_runs_update_sh_and_restarts_the_app(self):
        runner = Recorder()
        app = tapp.TpotApp(backend=FakeBackend(), runner=runner)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("down", "down", "down", "down", "down", "down")
            await pilot.pause(0.2)
            await pilot.click("#run-update")
            await pilot.pause(0.2)
            await pilot.click("#yes")
            await pilot.pause(0.2)
        self.assertEqual(runner.commands, [[os.path.join(cli.REPO_DIR, "update.sh"), "-y"]])
        self.assertEqual(app.return_value, "restart")

    async def test_mac_only_gets_the_customizer(self):
        app = tapp.TpotApp(backend=FakeBackend(host=False), runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            self.assertEqual([p[0] for p in app.panes], ["edition", "settings", "users"])

    def test_every_menu_pane_has_a_command(self):
        commands = {"status": "status", "edition": "customize", "settings": "env", "users": "users",
                    "sensors": "sensors", "images": "images", "update": "update"}
        help_text = cli.build_parser().format_help()
        for key, _title, _cls, _host in tapp.PANES:
            self.assertIn(key, commands, f"menu pane {key} needs a tpot command")
            self.assertIn(commands[key], help_text)


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class CustomizerScreenTest(unittest.IsolatedAsyncioTestCase):

    def make_app(self, selection):
        self.catalog = core.Catalog()
        return tapp.CustomizerApp(self.catalog, selection, core.DEFAULT_MAX_NETWORKS)

    @staticmethod
    def screen(app):
        return next(s for s in app.screen_stack if isinstance(s, CustomizerScreen))

    async def goto(self, pilot, app, name):
        screen = self.screen(app)
        tree = screen.query_one("#cz-tree")
        tree.move_cursor(screen.nodes[name])
        await pilot.pause(0.1)

    async def test_conflict_suggestion_save(self):
        app = self.make_app(core.Selection("MINI"))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause(0.3)
            await self.goto(pilot, app, "wordpot")
            await pilot.press("space")
            await pilot.pause(0.1)
            screen = self.screen(app)
            self.assertTrue(screen.state.result.problems_of("wordpot", "port"))
            await pilot.press("p")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, PortsDialog)
            await pilot.press("v")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ChoiceDialog)
            await pilot.press("enter")
            await pilot.pause(0.2)
            await pilot.press("escape")
            await pilot.pause(0.2)
            self.assertEqual(screen.state.result.errors, [])
            await pilot.press("s")
            await pilot.pause(0.2)
            await pilot.click("#yes")
            await pilot.pause(0.2)
        chosen = app.return_value
        self.assertEqual(chosen.add, ["wordpot"])
        self.assertEqual(list(chosen.ports), [("wordpot", 80, "tcp")])

    async def test_locked_and_save_blocked_with_errors(self):
        app = self.make_app(core.Selection("STANDARD"))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause(0.3)
            screen = self.screen(app)
            await self.goto(pilot, app, "tpotinit")
            await pilot.press("space")
            await pilot.pause(0.1)
            self.assertTrue(screen.state.selected("tpotinit"))
            await self.goto(pilot, app, "glutton")
            await pilot.press("space")
            await pilot.pause(0.1)
            self.assertTrue(screen.state.result.errors)
            await pilot.press("s")
            await pilot.pause(0.2)
            dialog = app.screen
            self.assertIsInstance(dialog, ConfirmDialog)
            self.assertEqual(len(dialog.query("#yes")), 0)
            await pilot.press("escape")
            await pilot.pause(0.1)
            await pilot.press("escape")
            await pilot.pause(0.1)
            await pilot.press("y")
            await pilot.pause(0.2)
        self.assertIsNone(app.return_value)

    async def test_change_edition(self):
        app = self.make_app(core.Selection("STANDARD"))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("b")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ChoiceDialog)
            await pilot.press("down", "down", "enter")    # STANDARD, SENSOR, MINI
            await pilot.pause(0.3)
            self.assertEqual(self.screen(app).state.selection.base, "MINI")
            self.assertNotIn("wordpot", self.screen(app).state.result.services)


if __name__ == "__main__":
    unittest.main()
