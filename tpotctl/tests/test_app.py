"""The tpot menu and the customizer screen, driven headless by Textual's pilot.

Skipped where Textual is missing; run them with the venv of tpot:
  ~/.local/share/tpotce/venv/bin/python -m unittest discover tpotctl/tests
"""

import os
import subprocess
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

        def retention_status(self):
            from tpotctl import elastic
            return getattr(self, "_retention", elastic.Status("90", "90", "managed"))

        def retention_plan(self, value, force=False):
            from tpotctl import elastic
            self.calls.append(("plan", value, force))
            doomed = [elastic.Index("logstash-2026.08.01", 0, 1200, 5 * 2 ** 20)] if elastic.shortens("90", value) else []
            action = "update" if value != "90" else "keep"
            return getattr(self, "_plan", elastic.Plan(elastic.Decision(action, {} if action == "update" else None,
                                                                         "90", True), doomed, True,
                                                       shortening=action == "update" and elastic.shortens("90", value)))

        def apply_retention(self, plan):
            self.calls.append(("apply", plan.decision.action))

        def set_retention(self, value):
            self.calls.append(("set", value))

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

    async def test_status_page_has_no_pot(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder())
        async with app.run_test(size=(170, 45)) as pilot:
            await pilot.pause(0.5)
            self.assertFalse(app.query("#status #pot"))

    def ten_sources(self):
        from tpotctl import events
        backend = FakeBackend()
        backend.top_sources = lambda: events.Sources([events.Source(f"203.0.113.{n}", 100 - n, "Netherlands")
                                                      for n in range(10)])
        backend.containers = lambda: [ops.Container(f"honeypot{n}", "running", "Up 2 hours (healthy)", "healthy", "", "c")
                                      for n in range(20)]
        return backend

    async def test_attacks_and_top_attackers_get_the_room(self):
        app = tapp.TpotApp(backend=self.ten_sources(), runner=Recorder())
        async with app.run_test(size=(170, 55)) as pilot:
            await pilot.pause(0.8)
            self.assertGreater(app.query_one("#attacks").content_region.width, 48)    # was 48 next to the pot
            block = app.query_one("#sources-block")
            self.assertTrue(block.display)
            text = str(app.query_one("#top-attackers").render())
            self.assertEqual(sum(f"203.0.113.{n}" in text for n in range(10)), 10)
            self.assertEqual(app.query_one("#svc-start").label.plain, "Start T-Pot")

    async def test_top_attackers_leave_the_containers_their_rows(self):
        """Below 50 lines a compact Attacks block and three sources, so the containers table keeps the rows
        it had next to the pot, with every line of the widget there (the newest attack, five honeypots)."""
        from tpotctl import events
        backend = self.ten_sources()
        backend.attacks = lambda: events.Attacks([0, 3, 9, 4] * 15, 1234,
                                                 [(f"Honeypot{n}", 900 - n) for n in range(5)],
                                                 latest=1.0, sources=99)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.8)
            text = str(app.query_one("#top-attackers").render())
            self.assertEqual(sum(f"203.0.113.{n}" in text for n in range(10)), 3)
            attacks = str(app.query_one("#attacks").render())
            self.assertEqual(sum(f"Honeypot{n}" in attacks for n in range(5)), 3)
            self.assertIn("last attack", attacks)
            self.assertIn("24 hours", str(app.query_one("#attacks-block").border_subtitle))
            self.assertGreaterEqual(app.query_one("#containers").content_region.height, 10)

    async def test_a_stopped_tpot_shows_its_services_not_started(self):
        backend = FakeBackend()
        backend.containers = lambda: ops.tpot_containers([], ["cowrie", "dionaea"])
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.8)
            table = app.query_one("#containers")
            self.assertEqual(table.row_count, 2)
            self.assertIn("not started", str(table.get_row_at(0)[1]))
            self.assertIn("0 of 2 up", str(app.query_one("#header-live").render()))
            self.assertIn("2 not started", str(app.query_one("#comb").render()))
            self.assertIn("0 of 2 containers running", str(app.query_one("#status-info").render()))

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
            said = len(app._notifications)
            await app.action_quit()    # the palette's Quit
            await pilot.pause(0.2)
            self.assertTrue(app.is_running)
            self.assertIsInstance(app.screen, TaskScreen)
            notes = [str(note.message) for note in app._notifications]
            self.assertEqual(len(notes), said + 1, notes)
            self.assertIn("still running", notes[-1])
            release.set()
            await pilot.pause(0.3)
            await pilot.press("q")     # not running any more: q goes back
            await pilot.pause(0.2)
            self.assertNotIsInstance(app.screen, TaskScreen)

    async def test_backups_load_in_a_thread(self):
        import threading
        from tpotctl.screens.restore import RestoreScreen
        gate = threading.Event()
        backend = FakeBackend()
        original = backend.backup_infos
        backend.backup_infos = lambda: gate.wait(2) and original()
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("update")
            await pilot.pause(0.2)
            await pilot.click("#run-restore")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, RestoreScreen)
            self.assertIn("reading the backups", str(app.screen.query_one("#restore-hint").render()))
            gate.set()
            await pilot.pause(0.4)
            self.assertEqual(app.screen.query_one("#restore-list").option_count, 2)

    async def test_no_backups_after_loading(self):
        from tpotctl.screens.restore import RestoreScreen
        backend = FakeBackend()
        backend.backup_infos = lambda: []
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("update")
            await pilot.pause(0.2)
            await pilot.click("#run-restore")
            await pilot.pause(0.4)
            self.assertIsInstance(app.screen, RestoreScreen)
            self.assertIn("no backup", str(app.screen.query_one("#restore-hint").render()))
            self.assertTrue(app.screen.query_one("#restore-go").disabled)

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
        self.assertEqual(FakeEngine.seen, [[tapp.LAUNCHER, "setup", "--force"]])

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

    def mac_backend(self, in_use):
        from tpotctl import editions
        backend = FakeBackend(host=False)
        backend.editions = lambda: [editions.Choice("mac_win", "macOS / Windows", "Docker Desktop.", "HIVE", 16,
                                                    256, "/x/mac_win.yml")]
        backend.edition_current = lambda: (in_use, "")
        backend.edition_plan = lambda key: editions.SwitchPlan(backend.editions()[0], in_use, "/x/keep.yml")
        return backend

    async def test_edition_switch_on_a_mac_to_mac_win(self):
        from tpotctl.screens.task import TaskScreen
        FakeEngine.seen.clear()
        app = tapp.TpotApp(backend=self.mac_backend("STANDARD"), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("edition")
            await pilot.pause(0.2)
            self.assertFalse(app.query_one("#switch-edition").disabled)
            await pilot.click("#switch-edition")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ConfirmDialog)
            await pilot.click("#yes")
            await pilot.pause(0.4)
            self.assertIsInstance(app.screen, TaskScreen)
            self.assertEqual(app.screen.job.become, "")              # no sudo on Docker Desktop
        self.assertEqual(FakeEngine.seen, [[tapp.LAUNCHER, "edition", "set", "mac_win", "-y"]])

    async def test_edition_switch_on_a_mac_running_mac_win(self):
        app = tapp.TpotApp(backend=self.mac_backend("MAC_WIN"), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("edition")
            await pilot.pause(0.2)
            self.assertTrue(app.query_one("#switch-edition").disabled)
            self.assertIn("MAC_WIN", str(app.query_one("#edition-note").render()))

    async def test_host_pages_are_shown_locked_off_host(self):
        backend = FakeBackend(host=False)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            self.assertEqual([p[0] for p in app.panes], [p[0] for p in tapp.PANES])     # nothing hidden
            for key in ("status", "sensors", "images", "checks", "elastic", "update"):
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
            self.assertIn("SENSOR", str(app.query_one("#elastic").query_one(".locked-why").render()))
            self.assertNotIsInstance(app.query_one("#status"), tapp.LockedPane)

    async def test_elastic_shorter_retention_asks_with_the_indices(self):
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            self.assertIn("90 days", str(app.query_one("#elastic-status").render()))
            app.query_one("#elastic-value").value = "30"
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            self.assertIn("logstash-2026.08.01", str(app.screen.query_one(".dialog").render()) +
                          "".join(str(w.render()) for w in app.screen.query("Static")))
            await pilot.click("#no")
            await pilot.pause(0.2)
            self.assertNotIn(("set", "30"), backend.calls)
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            await pilot.click("#yes")
            await pilot.pause(0.3)
        self.assertIn(("set", "30"), backend.calls)
        self.assertIn(("apply", "update"), backend.calls)

    async def test_elastic_longer_retention_needs_no_question(self):
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            app.query_one("#elastic-value").value = "365"
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            self.assertNotIsInstance(app.screen, ConfirmDialog)
        self.assertIn(("set", "365"), backend.calls)

    async def test_elastic_foreign_policy_offers_take_over(self):
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("30", "120", "foreign")
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            self.assertFalse(app.query_one("#elastic-adopt").disabled)
            self.assertIn("Kibana", str(app.query_one("#elastic-status").render()))
            await pilot.click("#elastic-adopt")
            await pilot.pause(0.3)
        self.assertIn(("set", "120"), backend.calls)

    async def test_elastic_take_over_shows_what_it_deletes_first(self):
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("30", "120", "foreign")
        # the policy changed again in Kibana meanwhile: taking 120 over deletes an index
        backend._plan = elastic.Plan(elastic.Decision("update", {}, "200", False),
                                     [elastic.Index("logstash-2026.05.01", 0, 800, 2 ** 20)], True, shortening=True)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            await pilot.click("#elastic-adopt")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            self.assertIn("logstash-2026.05.01", "".join(str(w.render()) for w in app.screen.query("Static")))
            await pilot.click("#no")
            await pilot.pause(0.3)
            done = [call[0] for call in backend.calls if isinstance(call, tuple)]
            self.assertNotIn("set", done)
            self.assertNotIn("apply", done)
            await pilot.click("#elastic-adopt")
            await pilot.pause(0.3)
            await pilot.click("#yes")
            await pilot.pause(0.3)
        self.assertIn(("set", "120"), backend.calls)
        self.assertIn(("apply", "update"), backend.calls)

    async def test_unreachable_set_writes_env_only(self):
        from tpotctl import elastic
        backend = FakeBackend()
        backend._plan = elastic.Plan(elastic.Decision("keep", None, "", False), [], False)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            app.query_one("#elastic-value").value = "7"
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            self.assertNotIsInstance(app.screen, ConfirmDialog)
            notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn(("set", "7"), backend.calls)
        self.assertNotIn("apply", [call[0] for call in backend.calls if isinstance(call, tuple)])
        self.assertIn("the value waits", notes)
        self.assertIn("does not shorten the ILM policy tpot on its own", notes)

    async def test_unreachable_longer_value_says_when_it_applies(self):
        from tpotctl import elastic
        backend = FakeBackend()
        backend._plan = elastic.Plan(elastic.Decision("keep", None, "", False), [], False)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            app.query_one("#elastic-value").value = "365"
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn(("set", "365"), backend.calls)
        self.assertIn("TPOT_ES_RETENTION=365 is set. T-Pot applies it on its next start if the ILM policy tpot "
                      "keeps the events less long; a shorter value waits for 'tpot elastic retention --force' "
                      "(it asks first).", notes)

    async def test_shortening_asks_even_without_indices(self):
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("365", "365", "managed")
        backend._plan = elastic.Plan(elastic.Decision("update", {}, "365", True), [], True, shortening=True)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            app.query_one("#elastic-value").value = "90"
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            body = " ".join("".join(str(w.render()) for w in app.screen.query("Static")).split())
            self.assertIn("No index is deleted right away.", body)
            self.assertIn("From now on, events older than 90 days are deleted. This cannot be undone.", body)
            self.assertEqual(app.screen.title_text, "Keep the events 90 days?")
            self.assertEqual(str(app.screen.query_one("#yes").label), "Delete and set")
            await pilot.click("#no")
            await pilot.pause(0.3)
            self.assertNotIn(("set", "90"), backend.calls)
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            await pilot.click("#yes")
            await pilot.pause(0.3)
        self.assertIn(("set", "90"), backend.calls)
        self.assertIn(("apply", "update"), backend.calls)

    async def test_pending_value_applied_from_the_page_asks_first(self):
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("30", "off", "managed", pending=True)
        backend._plan = elastic.Plan(elastic.Decision("update", {}, "off", True),
                                     [elastic.Index("logstash-2026.04.01", 0, 500, 2 ** 20)], True, shortening=True)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            self.assertFalse(app.query_one("#elastic-apply").disabled)
            await pilot.click("#elastic-apply")
            await pilot.pause(0.3)
            self.assertIn(("plan", "30", True), backend.calls)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            self.assertIn("logstash-2026.04.01", "".join(str(w.render()) for w in app.screen.query("Static")))
            self.assertNotIn(("set", "30"), backend.calls)
            await pilot.click("#yes")
            await pilot.pause(0.3)
        self.assertIn(("set", "30"), backend.calls)
        self.assertIn(("apply", "update"), backend.calls)

    async def test_buttons_are_busy_while_planning(self):
        import threading
        from tpotctl import elastic
        release = threading.Event()

        class Slow(FakeBackend):
            def retention_plan(self, value, force=False):
                plan = super().retention_plan(value, force)
                release.wait(5)
                return plan

        backend = Slow()
        backend._retention = elastic.Status("30", "120", "foreign")
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                app.goto("elastic")
                await pilot.pause(0.3)
                app.query_one("#elastic-value").value = "365"
                await pilot.click("#elastic-set")
                await pilot.pause(0.3)
                for button in ("#elastic-set", "#elastic-apply", "#elastic-adopt"):
                    self.assertTrue(app.query_one(button).disabled, button)
                await pilot.click("#elastic-set")                   # a second press while the first runs
                app.query_one("#elastic-set").press()
                app.query_one("#elastic-value").focus()
                await pilot.press("enter")
                await pilot.pause(0.3)
                self.assertEqual(len([c for c in backend.calls if isinstance(c, tuple) and c[0] == "plan"]), 1)
                release.set()
                await pilot.pause(0.4)
                self.assertFalse(app.query_one("#elastic-set").disabled)
                self.assertFalse(app.query_one("#elastic-apply").disabled)      # foreign, .env has a value
                self.assertFalse(app.query_one("#elastic-adopt").disabled)
        finally:
            release.set()
        self.assertEqual(len([c for c in backend.calls if isinstance(c, tuple) and c[0] == "plan"]), 1)

    async def test_status_shows_pending_and_adoption(self):
        from tpotctl import elastic
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        cases = [(elastic.Status("30", "90", "managed", pending=True),
                  "TPOT_ES_RETENTION=30 waits: T-Pot does not shorten the ILM policy tpot (90 days) on its own"),
                 (elastic.Status("120", "120", "foreign", adopts=True),
                  "T-Pot takes it over on its next start (same duration)"),
                 (elastic.Status("30", "", "missing"),
                  "T-Pot creates the policy on its next start: for 30 days if no older events exist, otherwise "
                  "forever (nothing is deleted) and the value waits.")]
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            for state, expected in cases:
                with self.subTest(state=state.state):
                    backend._retention = state
                    app.goto("status")
                    await pilot.pause(0.2)
                    app.goto("elastic")
                    await pilot.pause(0.3)
                    shown = " ".join(str(app.query_one("#elastic-status").render()).split())
                    self.assertIn(expected, shown)
                    if state.adopts:
                        self.assertNotIn("leaves it alone", shown)

    async def test_elastic_reads_once_while_a_read_is_in_flight(self):
        import threading
        release = threading.Event()

        class Slow(FakeBackend):
            reads = 0

            def retention_status(self):
                Slow.reads += 1
                release.wait(5)
                return super().retention_status()

        backend = Slow()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.2)
                app.goto("elastic")
                await pilot.pause(0.1)
                app.goto("status")
                app.goto("elastic")
                await pilot.pause(0.2)
                self.assertEqual(Slow.reads, 1)
                release.set()
                await pilot.pause(0.3)
                self.assertIn("90 days", str(app.query_one("#elastic-status").render()))
                app.goto("status")
                app.goto("elastic")
                await pilot.pause(0.3)
                # done: the next one reads anew (goto and the highlight of the menu it causes may each
                # read once, one after the other)
                self.assertGreater(Slow.reads, 1)
        finally:
            release.set()

    async def test_elastic_warns_of_a_shorter_value_and_reads_anew_when_shown(self):
        from tpotctl import elastic
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            self.assertNotIn("waits", str(app.query_one("#elastic-status").render()))
            self.assertTrue(app.query_one("#elastic-apply").disabled)
            self.assertEqual(app.query_one("#elastic-value").value, "90")
            # meanwhile .env got a shorter value (tpot env set, the Settings page): shown on the way back
            backend._retention = elastic.Status("30", "90", "managed", pending=True)
            app.goto("status")
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            status = " ".join(str(app.query_one("#elastic-status").render()).split())
            self.assertIn("TPOT_ES_RETENTION=30 waits", status)
            self.assertNotIn("deletes the events older than 30 days on its next start", status)
            self.assertFalse(app.query_one("#elastic-apply").disabled)
            self.assertEqual(app.query_one("#elastic-value").value, "30")      # not typed in: the new value
            backend._retention = elastic.Status("30", "", "missing")
            app.goto("status")
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            self.assertIn("for 30 days if no older events exist",
                          " ".join(str(app.query_one("#elastic-status").render()).split()))

    async def test_elastic_set_with_an_empty_field_does_nothing(self):
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            app.query_one("#elastic-value").value = "  "
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            notes = [str(note.message) for note in app._notifications]
        self.assertIn("Enter days (1-3650) or off.", notes)
        done = [call[0] for call in backend.calls if isinstance(call, tuple)]
        self.assertEqual([c for c in done if c in ("plan", "set", "apply")], [])

    async def test_elastic_apply_needs_a_value_in_env(self):
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("", "120", "foreign")
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            self.assertTrue(app.query_one("#elastic-apply").disabled)
            self.assertFalse(app.query_one("#elastic-adopt").disabled)

    async def test_settings_previews_a_shorter_retention_off_a_tpot_host_too(self):
        # Docker Desktop: no T-Pot host, but Elasticsearch on 127.0.0.1:64298 (MAC_WIN)
        from tpotctl import settings
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        backend.host = False
        app, pilot = await self.save_retention(backend, "30")
        self.assertIn(("plan", "30", False), backend.calls)
        self.assertIsInstance(app.screen, ConfirmDialog)
        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "90")

    def retention_checkout(self):
        """A checkout of env.example with TPOT_ES_RETENTION=90, the Settings page reads its .env."""
        from tpotctl.tests.test_settings import make_checkout
        repo = make_checkout(self)
        path = os.path.join(repo, ".env")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("TPOT_ES_RETENTION=30\n", text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace("TPOT_ES_RETENTION=30\n", "TPOT_ES_RETENTION=90\n", 1))
        return repo

    async def save_retention(self, backend, value):
        """The Settings page: TPOT_ES_RETENTION to value, then Save."""
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        pilot_cm = app.run_test(size=(150, 50))
        pilot = await pilot_cm.__aenter__()
        self.addAsyncCleanup(pilot_cm.__aexit__, None, None, None)
        await pilot.pause(0.3)
        app.goto("settings")
        await pilot.pause(0.4)
        app.query_one("#set-TPOT_ES_RETENTION").value = value
        await pilot.pause(0.3)
        await pilot.click("#settings-save")
        await pilot.pause(0.4)
        return app, pilot

    def settings_backend(self, repo):
        from tpotctl import settings

        class Backend(FakeBackend):
            def settings(self):
                return settings.load(repo)

        return Backend()

    async def test_settings_shorter_retention_shows_the_indices_first(self):
        from tpotctl import settings
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        app, pilot = await self.save_retention(backend, "30")
        self.assertIsInstance(app.screen, ConfirmDialog)
        self.assertIn("logstash-2026.08.01", "".join(str(w.render()) for w in app.screen.query("Static")))
        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "90")     # nothing written yet
        await pilot.click("#no")
        await pilot.pause(0.3)
        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "90")
        self.assertNotIn(("apply", "update"), backend.calls)
        await pilot.click("#settings-save")
        await pilot.pause(0.4)
        await pilot.click("#yes")
        await pilot.pause(0.4)
        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "30")
        self.assertIn(("apply", "update"), backend.calls)
        self.assertNotIsInstance(app.screen, ConfirmDialog)        # applied: no restart needed

    async def test_settings_shorter_retention_without_es_saves_and_says_it_waits(self):
        from tpotctl import elastic, settings
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        backend._plan = elastic.Plan(elastic.Decision("keep", None, "", False), [], False)
        app, pilot = await self.save_retention(backend, "30")
        self.assertNotIsInstance(app.screen, ConfirmDialog)
        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "30")
        self.assertNotIn("apply", [call[0] for call in backend.calls if isinstance(call, tuple)])
        notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn("the value waits", notes)

    async def test_settings_shortening_without_indices_asks(self):
        from tpotctl import elastic, settings
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        backend._plan = elastic.Plan(elastic.Decision("update", {}, "90", True), [], True, shortening=True)
        app, pilot = await self.save_retention(backend, "30")
        self.assertIsInstance(app.screen, ConfirmDialog)
        self.assertIs(app.focused, app.screen.query_one("#no"))
        body = " ".join("".join(str(w.render()) for w in app.screen.query("Static")).split())
        self.assertIn("No index is deleted right away.", body)
        self.assertIn("From now on, events older than 30 days are deleted. This cannot be undone.", body)
        await pilot.click("#no")
        await pilot.pause(0.3)
        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "90")
        self.assertNotIn("apply", [call[0] for call in backend.calls if isinstance(call, tuple)])

    async def test_settings_foreign_policy_off_host_names_kibana(self):
        from tpotctl import elastic
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        backend.host = False
        backend._plan = elastic.Plan(elastic.Decision("foreign", None, "120", False), [], True)
        app, pilot = await self.save_retention(backend, "30")
        notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn("Stack Management \u203a Index Lifecycle Policies", notes)
        self.assertNotIn("Elastic page", notes)

    async def test_settings_save_is_disabled_while_planning(self):
        import threading
        import time
        from tpotctl import settings
        release = threading.Event()
        repo = self.retention_checkout()
        seen = []

        class Slow(type(self.settings_backend(repo))):
            def retention_plan(self, value, force=False):
                seen.append(value)
                release.wait(5)
                return super().retention_plan(value, force)

        backend = Slow()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        try:
            async with app.run_test(size=(150, 50)) as pilot:
                await pilot.pause(0.3)
                app.goto("settings")
                await pilot.pause(0.4)
                app.query_one("#set-TPOT_ES_RETENTION").value = "365"
                await pilot.pause(0.3)
                began = time.monotonic()
                await pilot.click("#settings-save")
                await pilot.pause(0.3)
                self.assertLess(time.monotonic() - began, 3)    # the page did not wait for Elasticsearch
                self.assertTrue(app.query_one("#settings-save").disabled)
                app.query_one("#settings").check()              # a refresh meanwhile keeps it disabled
                self.assertTrue(app.query_one("#settings-save").disabled)
                app.query_one("#settings").action_save()        # ctrl+s meanwhile: no second plan
                await pilot.pause(0.2)
                self.assertEqual(seen, ["365"])
                self.assertFalse(release.is_set())              # still planning: no timeout made it pass
                self.assertTrue(app.query_one("#settings-save").disabled)
                release.set()
                for _wait in range(100):                        # the save reloads the page: until it is done
                    if not app.query_one("#settings").saving:
                        break
                    await pilot.pause(0.1)
                await pilot.pause(0.3)
                self.assertFalse(app.query_one("#settings").saving)
                self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "365")
        finally:
            release.set()
        self.assertEqual(seen, ["365"])

    async def test_settings_longer_retention_saves_and_applies(self):
        from tpotctl import settings
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        app, pilot = await self.save_retention(backend, "365")
        self.assertNotIsInstance(app.screen, ConfirmDialog)
        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "365")
        self.assertIn(("apply", "update"), backend.calls)

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
        start = readme.index("## The T-Pot Manager (tpot)")
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
                    "sensors": "sensors", "images": "images", "checks": "check", "elastic": "elastic",
                    "update": "update"}
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


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class RunnerTest(unittest.TestCase):
    """A command of the menu runs in the normal terminal (the app is suspended); ctrl+c reaches the
    child, which decides, and brings no traceback into the T-Pot Manager."""

    class Child:
        """A child that ctrl+c ends: the first wait is interrupted, then it is gone by SIGINT."""

        def __init__(self, *args, **kwargs):
            self.args, self.kwargs, self.waits, self.returncode = args, kwargs, 0, None

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            self.returncode = -2
            return self.returncode

        def kill(self):
            self.killed = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def run_command(self, popen, answer):
        import contextlib
        import io
        from types import SimpleNamespace
        from unittest import mock
        stub = SimpleNamespace(suspend=contextlib.nullcontext)
        out = io.StringIO()
        with mock.patch("subprocess.Popen", popen) as started, mock.patch("builtins.input", answer) as asked, \
                contextlib.redirect_stdout(out):
            code = tapp.Runner(stub)(["/x/update.sh", "-y"], cwd="/x")
        return code, out.getvalue(), started, asked

    def test_ctrl_c_ends_the_child_and_the_enter_too(self):
        from unittest import mock
        children = []

        def popen(*args, **kwargs):
            children.append(self.Child(*args, **kwargs))
            return children[-1]
        code, out, _started, asked = self.run_command(popen, mock.Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(code, 130)                                  # 128 + SIGINT
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].args, (["/x/update.sh", "-y"],))
        self.assertEqual(children[0].kwargs, {"cwd": "/x"})
        self.assertEqual(children[0].waits, 2)                       # the ctrl+c only waited on
        self.assertIn("$ /x/update.sh -y", out)
        self.assertEqual(asked.call_count, 1)
        self.assertIn("[exit code 130]", asked.call_args[0][0])

    def test_a_command_that_does_not_start(self):
        from unittest import mock
        code, _out, _started, asked = self.run_command(mock.Mock(side_effect=FileNotFoundError(2, "No such file")),
                                                       mock.Mock(side_effect=EOFError))
        self.assertEqual(code, 127)
        self.assertIn("[exit code 127]", asked.call_args[0][0])


RUNNER_APP = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from textual.app import App
from tpotctl import app as tapp
LOG = sys.argv[2]

def log(text):
    with open(LOG, "a", encoding="utf-8") as out:
        out.write(text + "\n")

class Menu(App):
    def on_mount(self):
        self.set_timer(0.3, self.command)

    def command(self):
        code = tapp.Runner(self)(["sh", "-c", "echo CHILD-READY; exec sleep 5"])
        log(f"runner {code}")
        self.set_timer(0.5, self.after)

    def after(self):
        log("menu is back")
        self.exit(0)

Menu().run()
log("run returned")
'''


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
@unittest.skipUnless(os.name == "posix", "needs a pty")
class RunnerPtyTest(unittest.TestCase):
    """A real ctrl+c (\\x03 into a pty) while a command of the menu runs: on Python >= 3.11 Textual runs on
    asyncio.run, whose own SIGINT handler cancels the app's main task; the T-Pot Manager must come back to
    the menu all the same (it runs on 3.9 too, where it passed before)."""

    def drive(self, steps):
        """Run the menu of RUNNER_APP in a pty, type each key once its text shows; the lines of its log."""
        import fcntl
        import pty
        import select
        import struct
        import tempfile
        import termios
        import time
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with tempfile.TemporaryDirectory() as tmp:
            script, log = os.path.join(tmp, "menu.py"), os.path.join(tmp, "log")
            with open(script, "w", encoding="utf-8") as out:
                out.write(RUNNER_APP)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

            def terminal():                         # the pty is the controlling terminal: \x03 is SIGINT
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            env = dict(os.environ, TERM="xterm-256color", TPOT_SPLASH="off")
            proc = subprocess.Popen([sys.executable, script, repo, log], stdin=slave, stdout=slave, stderr=slave,
                                    env=env, preexec_fn=terminal, close_fds=True)
            os.close(slave)
            out, steps = b"", list(steps)
            deadline = time.time() + 20
            try:
                while time.time() < deadline:
                    if steps and steps[0][0] in out:
                        time.sleep(0.2)
                        os.write(master, steps.pop(0)[1])
                    ready, _w, _x = select.select([master], [], [], 0.1)
                    if ready:
                        try:
                            data = os.read(master, 65536)
                        except OSError:
                            break
                        if not data:
                            break
                        out += data
                    elif proc.poll() is not None:
                        break
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
                os.close(master)
            with open(log, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        self.assertEqual(steps, [], out.decode("utf-8", "replace")[-2000:])
        self.assertEqual(proc.returncode, 0)
        return lines

    def test_ctrl_c_during_a_command_comes_back_to_the_menu(self):
        self.assertEqual(self.drive([(b"CHILD-READY", b"\x03"), (b"Press Enter", b"\r")]),
                         ["runner 130", "menu is back", "run returned"])

    def test_ctrl_c_at_the_question_counts_as_enter(self):
        self.assertEqual(self.drive([(b"CHILD-READY", b"\x03"), (b"Press Enter", b"\x03")]),
                         ["runner 130", "menu is back", "run returned"])

    def test_ctrl_c_twice_during_a_command(self):
        self.assertEqual(self.drive([(b"CHILD-READY", b"\x03\x03"), (b"Press Enter", b"\x03\x03")]),
                         ["runner 130", "menu is back", "run returned"])


if __name__ == "__main__":
    unittest.main()
