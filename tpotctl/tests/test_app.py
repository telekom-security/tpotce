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
        def __init__(self, host=True, kind="HIVE"):
            self.host, self.kind = host, kind
            self.calls = []

        def linux_host(self):
            return self.host

        def status(self):
            self.calls.append("status")
            return ops.Status("24.04.2", "dev", "abc1234", "STANDARD", self.kind, "active", "/home/t/tpotce")

        def containers(self):
            self.calls.append("containers")
            return [ops.Container("cowrie", "running", "Up 1 hour (healthy)", "healthy", "22->22/tcp", "c"),
                    ops.Container("conpot_ipmi", "exited", "Exited (1)", "", "", "c")]

        def images(self):
            return [ops.Image("ghcr.io/telekom-security/cowrie", "24.04.2", "abc", "90MB", "2 days ago")]

        def backups(self):
            return []

        def tpot_type(self):
            return self.kind

        def system(self):
            from tpotctl import system
            return system.System(12.0, system.Usage(1, 4), system.Usage(1, 10), "/data")

        def attacks(self):
            from tpotctl import events
            return events.Attacks([0, 3, 9, 4] * 15, 1234, [("Cowrie", 900), ("Dionaea", 334)])

        def sudo_mode(self):
            return "passwordless"

        def top_sources(self):
            from tpotctl import events
            return events.Sources([events.Source("203.0.113.7", 912, "Netherlands", "mass scanner"),
                                   events.Source("198.51.100.2", 40)])

        def host_address(self):
            return "192.0.2.5"

        def backup_infos(self):
            return [ops.BackupInfo("/b/new_tpot_backup.tar", "new_tpot_backup.tar", 2 ** 20, "regular",
                                   ["version: 24.04.2"], ["git", "config"]),
                    ops.BackupInfo("/b/old_tpot_backup_full.tar", "old_tpot_backup_full.tar", 2 ** 30, "full",
                                   ["version: 24.04.1"], ["config", "data"])]

        def editions(self):
            from tpotctl import editions
            return [editions.Choice("standard", "Hive", "Everything.", "HIVE", 16, 256, "/x/standard.yml"),
                    editions.Choice("sensor", "Sensor", "Honeypots only.", "SENSOR", 8, 128, "/x/sensor.yml"),
                    editions.Choice("mini", "Mini", "Fewer daemons.", "HIVE", 16, 256, "/x/mini.yml")]

        def edition_current(self):
            return "STANDARD", ""

        def edition_plan(self, key):
            from tpotctl import editions
            choice = next(c for c in self.editions() if c.key == key)
            warnings = ["Kibana stops."] if key == "sensor" else []
            return editions.SwitchPlan(choice, "STANDARD", "/x/keep.yml", warnings)

    class FakeEngine:
        seen = []

        def __init__(self, command, env=None, cwd=None):
            FakeEngine.seen.append(list(command))

        def run(self, line):
            line("@@tpot phase done Done\n")
            return 0

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
            await pilot.press("down", "down", "down", "down", "down", "down")
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

    async def test_update_runs_in_the_task_screen_and_restarts_the_app(self):
        from tpotctl.screens.task import TaskScreen
        FakeEngine.seen.clear()
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("update")
            await pilot.pause(0.2)
            app.query_one("#update-full").value = True
            await pilot.click("#run-update")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, TaskScreen)
            await pilot.click("#task-run")
            await pilot.pause(0.4)
            await pilot.click("#task-restart")
            await pilot.pause(0.3)
        self.assertEqual(FakeEngine.seen, [[os.path.join(cli.REPO_DIR, "update.sh"), "-y", "--full"]])
        self.assertEqual(app.return_value, "restart")

    async def test_restore_chooses_the_archive_and_the_groups(self):
        from tpotctl.screens.restore import RestoreScreen
        from tpotctl.screens.task import TaskScreen
        FakeEngine.seen.clear()
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("update")
            await pilot.pause(0.2)
            await pilot.click("#run-restore")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, RestoreScreen)
            screen = app.screen
            self.assertFalse(screen.query_one("#group-data").display)          # not in the newest archive
            self.assertTrue(screen.query_one("#group-config").display)
            self.assertTrue(screen.query_one("#group-config").value)
            self.assertFalse(screen.query_one("#group-git").value)             # git reset --hard: only on request
            await pilot.click("#restore-go")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, TaskScreen)
            self.assertEqual(FakeEngine.seen, [])                               # Run is the last yes
            self.assertIn("configuration", str(app.screen.query_one("#task-intro").render()))
            await pilot.click("#task-run")
            await pilot.pause(0.4)
        self.assertEqual(FakeEngine.seen,
                         [[os.path.join(cli.REPO_DIR, "restore.sh"), "-f", "/b/new_tpot_backup.tar", "-g", "config"]])

    async def test_status_shows_the_top_attackers(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.5)
            text = str(app.query_one("#top-attackers").render())
        self.assertIn("203.0.113.7", text)
        self.assertIn("Netherlands", text)

    async def test_checks_probe_the_honeypots(self):
        FakeEngine.seen.clear()
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("checks")
            await pilot.pause(0.2)
            self.assertEqual(app.query_one("#check-host").value, "192.0.2.5")
            await pilot.click("#check-honeypots")
            await pilot.pause(0.3)
            await pilot.click("#task-run")
            await pilot.pause(0.4)
        self.assertEqual(FakeEngine.seen, [[cli.HPTEST, "192.0.2.5"]])

    async def test_checks_pipeline_asks_first(self):
        FakeEngine.seen.clear()
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("checks")
            await pilot.pause(0.2)
            await pilot.click("#check-pipeline")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ConfirmDialog)
            await pilot.click("#no")
            await pilot.pause(0.2)
            self.assertEqual(FakeEngine.seen, [])
            await pilot.click("#check-pipeline")
            await pilot.pause(0.3)
            await pilot.click("#yes")
            await pilot.pause(0.5)
        self.assertEqual(FakeEngine.seen, [[cli.PIPELINE]])

    async def test_quit_waits_for_a_running_task(self):
        import threading
        from tpotctl.screens.task import Task, TaskScreen
        release = threading.Event()

        class SlowEngine:
            def __init__(self, command, env=None, cwd=None):
                pass

            def run(self, line):
                line("@@tpot phase backup Writing the backup\n")
                release.wait(5)
                return 0

        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=SlowEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.run_task(Task("Update T-Pot", ["/x/update.sh", "-y"], autostart=True))
            await pilot.pause(0.3)
            for key in ("q", "ctrl+q"):
                await pilot.press(key)
                await pilot.pause(0.2)
                self.assertTrue(app.is_running, key)
                self.assertIsInstance(app.screen, TaskScreen)
            app.action_quit()          # the palette's Quit
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, TaskScreen)
            release.set()
            await pilot.pause(0.3)
            await pilot.press("q")     # not running any more: q goes back
            await pilot.pause(0.2)
            self.assertNotIsInstance(app.screen, TaskScreen)

    async def test_restore_of_data_says_what_goes(self):
        from tpotctl.screens.restore import RestoreScreen
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("update")
            await pilot.pause(0.2)
            await pilot.click("#run-restore")
            await pilot.pause(0.3)
            screen = app.screen
            self.assertIsInstance(screen, RestoreScreen)
            screen.query_one("#restore-list").highlighted = 1                 # the full archive
            await pilot.pause(0.2)
            self.assertFalse(screen.query_one("#group-data").value)
            screen.query_one("#group-data").value = True
            await pilot.click("#restore-go")
            await pilot.pause(0.3)
            intro = str(app.screen.query_one("#task-intro").render())
        self.assertIn("data/", intro)
        self.assertIn("replaced", intro)

    async def test_refresh_runs_tpot_setup(self):
        FakeEngine.seen.clear()
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("update")
            await pilot.pause(0.2)
            await pilot.click("#run-setup")
            await pilot.pause(0.3)
            await pilot.click("#task-run")
            await pilot.pause(0.4)
        self.assertEqual(FakeEngine.seen, [[tapp.LAUNCHER, "setup"]])

    async def test_edition_switch_runs_tpot_edition_set(self):
        from tpotctl.screens.task import TaskScreen
        FakeEngine.seen.clear()
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("edition")
            await pilot.pause(0.2)
            listing = app.query_one("#edition-list")
            self.assertEqual(listing.option_count, 3)
            self.assertTrue(app.query_one("#switch-edition").disabled)       # the one in use
            listing.highlighted = 2
            await pilot.pause(0.1)
            self.assertFalse(app.query_one("#switch-edition").disabled)
            await pilot.click("#switch-edition")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ConfirmDialog)
            await pilot.click("#yes")
            await pilot.pause(0.4)
            self.assertIsInstance(app.screen, TaskScreen)
        self.assertEqual(FakeEngine.seen, [[tapp.LAUNCHER, "edition", "set", "mini", "-y"]])

    async def test_palette_switches_the_edition(self):
        from tpotctl.commands import TpotCommands
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            names = [name for name, _help, _cb in TpotCommands(app.screen).commands()]
        self.assertIn("Switch to the Mini edition", names)
        for name in ("Find Ollama", "Test the LLM of Beelzebub", "Test the LLM of Galah"):
            self.assertIn(name, names)
        self.assertNotIn("Switch to the Hive edition", names)       # in use

    async def test_edition_switch_off_host_is_disabled(self):
        app = tapp.TpotApp(backend=FakeBackend(host=False), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("edition")
            await pilot.pause(0.2)
            app.query_one("#edition-list").highlighted = 2
            await pilot.pause(0.1)
            self.assertTrue(app.query_one("#switch-edition").disabled)
            self.assertIn("MAC_WIN", str(app.query_one("#edition-note").render()))

    async def test_host_pages_are_shown_locked_off_host(self):
        backend = FakeBackend(host=False)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            self.assertEqual([p[0] for p in app.panes], [p[0] for p in tapp.PANES])     # nothing hidden
            for key in ("status", "sensors", "images", "checks", "update"):
                with self.subTest(page=key):
                    self.assertIsInstance(app.query_one(f"#{key}"), tapp.LockedPane)
                    self.assertIn("needs a T-Pot host", str(app.query_one(f"#{key}").query_one(".locked-why").render()))
            for key in ("edition", "settings", "llm", "users"):
                self.assertNotIsInstance(app.query_one(f"#{key}"), tapp.LockedPane)
            label = str(app.query_one("#menu-status").query_one(".menu-long").render())
            self.assertIn(tapp.glyphs.g("locked"), label)
        self.assertNotIn("containers", backend.calls)          # no docker call off host

    async def test_web_users_and_sensors_are_locked_on_a_sensor(self):
        app = tapp.TpotApp(backend=FakeBackend(kind="SENSOR"), runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            self.assertIn("SENSOR", str(app.query_one("#users").query_one(".locked-why").render()))
            self.assertIn("HIVE", str(app.query_one("#sensors").query_one(".locked-why").render()))
            self.assertNotIsInstance(app.query_one("#status"), tapp.LockedPane)

    async def test_palette_host_action_off_host_only_tells(self):
        from tpotctl.commands import TpotCommands
        runner = Recorder()
        app = tapp.TpotApp(backend=FakeBackend(host=False), runner=runner)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            found = {name: (help_text, callback) for name, help_text, callback in TpotCommands(app.screen).commands()}
            self.assertIn("needs a T-Pot host", found["Start T-Pot"][0])
            found["Start T-Pot"][1]()
            await pilot.pause(0.2)
        self.assertEqual(runner.commands, [])

    def test_readme_matrix_names_every_command_and_where(self):
        import re
        with open(os.path.join(cli.REPO_DIR, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        start = readme.index("## The tpot Command")
        section = readme[start:readme.index("\n## ", start + 10)]
        self.assertIn("| Command | Menu | Where | Does |", section)
        names = re.findall(r"^    (\w+)\s", cli.build_parser().format_help(), re.M)
        self.assertGreater(len(names), 15)
        for name in names:
            self.assertTrue(f"`tpot {name}" in section or f"`{name}`" in section, name)
        self.assertNotIn("only `tpot customize` and `tpot setup` are available", readme)
        self.assertIn("### What depends on the host and the edition", section)

    def test_every_menu_pane_has_a_command(self):
        commands = {"status": "status", "edition": "edition", "settings": "env", "llm": "llm", "users": "users",
                    "sensors": "sensors", "images": "images", "checks": "check", "update": "update"}
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
