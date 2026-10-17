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

        # the Checks page: last.json (None: no check yet; an exception: it cannot be read) and hold.state
        check_result_value = None
        ews = ("normal", 4.0)

        def check_result(self):
            if isinstance(self.check_result_value, BaseException):
                raise self.check_result_value
            return self.check_result_value

        def ews_state(self):
            return self.ews

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

        def check_data_loss(self, args):
            self.calls.append(("loss", list(args)))
            return getattr(self, "_loss", (True, [], ""))

        # the Kibana objects: a is to update, b is new, c the same, d yours ("Mine"); _kibana_errors names
        # the methods that raise, _kibana a cli.KibanaState of its own
        def kibana_plan(self):
            from tpotctl import kibana_objects as ko
            return getattr(self, "_kibana_plan", ko.Plan([
                ko.Item("dashboard:a", "dashboard", "a", "Attacks", "update"),
                ko.Item("dashboard:b", "dashboard", "b", "Brand new", "new"),
                ko.Item("dashboard:c", "dashboard", "c", "Cowrie", "current"),
                ko.Item("dashboard:d", "dashboard", "d", "Mine", "yours")]))

        def kibana_raise(self, name):
            error = getattr(self, "_kibana_errors", {}).get(name)
            if error is not None:
                raise error

        def kibana_status(self):
            self.calls.append("kibana-status")
            self.kibana_raise("status")
            return getattr(self, "_kibana", None) or cli.KibanaState("update", plan=self.kibana_plan())

        def kibana_preview(self):
            self.calls.append("kibana-preview")
            self.kibana_raise("preview")
            return self.kibana_plan()

        def kibana_update(self, overwrite, confirmed=None):
            from tpotctl import kibana_objects as ko
            self.calls.append(("kibana-update", overwrite, confirmed))
            self.kibana_raise("update")
            chosen = self.kibana_plan()
            refused = getattr(self, "_kibana_refused", [])      # (type, id, title, reason) Kibana refused
            done = [(i.type, i.id) for i in chosen.to_import("overwrite" if overwrite else "update")
                    if (i.type, i.id) not in {(t, n) for t, n, _title, _why in refused}]
            return chosen, ko.Result(imported=done, errors=list(refused)), "/b/kibana_objects_20261016120000.ndjson"

        def kibana_export(self):
            self.calls.append("kibana-export")
            self.kibana_raise("export")
            return "/b/kibana_objects_20261016120001.ndjson", 4

        def kibana_check_export(self, path):
            self.calls.append(("kibana-check", path))
            self.kibana_raise("check")
            return 4

        def kibana_restore(self, path):
            from tpotctl import kibana_objects as ko
            self.calls.append(("kibana-restore", path))
            self.kibana_raise("restore")
            return "/b/kibana_objects_20261016120002.ndjson", ko.Result(imported=[("dashboard", "d")])

        def kibana_exports(self):
            self.calls.append("kibana-exports")
            return getattr(self, "_kibana_exports", [
                ops.KibanaExport("/b/kibana_objects_20261016100000.ndjson", "Manager", 1792144800, 2 ** 20, 349),
                ops.KibanaExport("/d/kibana_objects/exports/auto_20261015100000.ndjson", "T-Pot", 1792058400,
                                 2 ** 19, 300)])

        def set_kibana_mode(self, value):
            """Through Settings.change, as on a host, when the test gives a FakeSettings; else only recorded."""
            from unittest import mock
            self.calls.append(("kibana-mode", value))
            if getattr(self, "_settings", None) is not None:
                with mock.patch("tpotctl.settings.load", return_value=self._settings):
                    super().set_kibana_mode(value)

    class FakeEngine:
        seen = []
        # the file of --accept-data-loss-file while the command runs: (mode, text); in seen as FILE
        files = []

        def __init__(self, command, env=None, cwd=None):
            command = list(command)
            if "--accept-data-loss-file" in command:
                at = command.index("--accept-data-loss-file") + 1
                with open(command[at], encoding="utf-8") as handle:
                    FakeEngine.files.append((os.stat(command[at]).st_mode & 0o777, handle.read()))
                command[at] = "FILE"
            FakeEngine.seen.append(command)

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

    async def update_with(self, loss, answer, start=False, full=False):
        """Update pressed with the check giving loss: the dialog (or None) and the commands run."""
        from tpotctl.screens.task import TaskScreen
        FakeEngine.seen.clear()
        backend = FakeBackend()
        backend._loss = loss
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        shown = None
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("update")
            await pilot.pause(0.2)
            app.query_one("#update-full").value = full
            await pilot.click("#run-update-start" if start else "#run-update")
            await pilot.pause(0.4)
            if isinstance(app.screen, ConfirmDialog):
                shown = (" ".join("".join(str(w.render()) for w in app.screen.query("Static")).split()),
                         app.focused.id if app.focused else None)
                await pilot.click(f"#{answer}")
                await pilot.pause(0.3)
            if isinstance(app.screen, TaskScreen):
                await pilot.click("#task-run")
                await pilot.pause(0.4)
        return shown, FakeEngine.seen, backend.calls

    async def test_update_with_findings_asks_and_passes_accept(self):
        update = os.path.join(cli.REPO_DIR, "update.sh")
        loss = (True, [("elastic", "Elasticsearch 8 -> 9 upgrades the data in /d/elk on the next start."),
                       ("backups", "2 older backups go to make room.")], "")
        shown, seen, calls = await self.update_with(loss, "no")
        self.assertIn("Elasticsearch 8 -> 9 upgrades the data", shown[0])
        self.assertIn("2 older backups go to make room.", shown[0])
        self.assertEqual(shown[1], "no")                                  # the safe answer has the focus
        self.assertEqual(seen, [])                                        # no starts nothing
        self.assertIn(("loss", []), calls)
        FakeEngine.files.clear()
        shown, seen, calls = await self.update_with(loss, "yes", start=True, full=True)
        self.assertIn(("loss", ["--full"]), calls)                        # -y and -s are no part of the check
        # only what the dialog showed is confirmed: the lines of the check in a file of this user
        self.assertEqual(seen, [[update, "-y", "-s", "--full", "--accept-data-loss-file", "FILE"]])
        self.assertNotIn("--accept-data-loss", seen[0])
        self.assertEqual(FakeEngine.files, [(0o600, "@@tpot loss elastic Elasticsearch 8 -> 9 upgrades the data in "
                                                    "/d/elk on the next start.\n"
                                                    "@@tpot loss backups 2 older backups go to make room.\n")])
        self.assertIn("--accept-data-loss-file", shown[0])

    async def test_update_with_the_elastic_step_names_it_and_passes_its_key(self):
        update = os.path.join(cli.REPO_DIR, "update.sh")
        step = ("The data of Elasticsearch 8.18.2 cannot be read by 9.5.4: the update first runs Elasticsearch and "
                "Kibana 8.19.23 once on it (in /d/elk), then the new release.")
        loss = (True, [("elastic-step", step)], "")
        shown, seen, _calls = await self.update_with(loss, "no")
        self.assertIn("cannot be read by 9.5.4", shown[0])
        self.assertIn("Yes lets update.sh take the step through Elasticsearch 8.19", shown[0])   # the step named
        self.assertIn("Kibana 8.19 where the edition has it", shown[0])
        self.assertEqual(shown[1], "no")
        self.assertEqual(seen, [])
        FakeEngine.files.clear()
        shown, seen, _calls = await self.update_with(loss, "yes")
        self.assertEqual(seen, [[update, "-y", "--accept-data-loss-file", "FILE"]])
        self.assertEqual(FakeEngine.files, [(0o600, f"@@tpot loss elastic-step {step}\n")])

    async def test_update_with_a_compose_file_of_your_own_offers_adapt(self):
        """The compose finding: Adapt passes --adapt-compose (never the file for it), Back runs nothing."""
        update = os.path.join(cli.REPO_DIR, "update.sh")
        own = ("Your docker-compose.yml is a compose file of your own: the update adapts it to the release only "
               "after a yes (--adapt-compose), the backup keeps your file. Not carried over: kibana: mem_limit 1g.")
        loss = (True, [("compose", own)], "")
        shown, seen, calls = await self.update_with(loss, "no")
        self.assertIn("Not carried over: kibana: mem_limit 1g.", shown[0])
        self.assertIn("Adapt lets update.sh back up your compose file and adapt it to this release", shown[0])
        self.assertIn("update.sh --adapt-compose", shown[0])
        self.assertEqual(shown[1], "no")                                  # Back has the focus
        self.assertEqual(seen, [])
        FakeEngine.files.clear()
        shown, seen, _calls = await self.update_with(loss, "yes")
        self.assertEqual(seen, [[update, "-y", "--adapt-compose"]])
        self.assertEqual(FakeEngine.files, [])
        self.assertNotIn("--accept-data-loss-file", shown[0])
        # with another finding: Adapt confirms that one through the file, as ever, and adapts
        elastic = "Elasticsearch 8 -> 9 upgrades the data in /d/elk on the next start."
        loss = (True, [("compose", own), ("elastic", elastic)], "")
        shown, seen, _calls = await self.update_with(loss, "yes", start=True)
        self.assertIn(elastic, shown[0])
        self.assertIn("Adapt confirms these and nothing else", shown[0])
        self.assertEqual(seen, [[update, "-y", "-s", "--adapt-compose", "--accept-data-loss-file", "FILE"]])
        self.assertEqual(FakeEngine.files, [(0o600, f"@@tpot loss elastic {elastic}\n")])

    async def test_the_compose_dialog_says_adapt_and_back(self):
        """Adapt / Back, Back focused; the title names the compose file alone, a neutral one where Adapt
        also confirms another loss."""
        own = ("compose", "Your docker-compose.yml is a compose file of your own.")
        elastic = ("elastic", "Elasticsearch 8 -> 9 upgrades the data.")
        for items, title in (([own], "Adapt your compose file?"), ([own, elastic], "Go on with these changes?")):
            with self.subTest(title=title):
                backend = FakeBackend()
                backend._loss = (True, items, "")
                app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.pause(0.2)
                    app.goto("update")
                    await pilot.pause(0.2)
                    await pilot.click("#run-update")
                    await pilot.pause(0.4)
                    self.assertIsInstance(app.screen, ConfirmDialog)
                    self.assertEqual(app.screen.title_text, title)
                    self.assertEqual(str(app.screen.query_one("#yes").label), "Adapt")
                    self.assertEqual(str(app.screen.query_one("#no").label), "Back")
                    self.assertEqual(app.focused.id, "no")

    async def test_update_without_findings_runs_as_before(self):
        shown, seen, calls = await self.update_with((True, [], ""), "yes")
        self.assertIsNone(shown)
        self.assertEqual(seen, [[os.path.join(cli.REPO_DIR, "update.sh"), "-y"]])
        self.assertIn(("loss", []), calls)

    async def test_update_when_the_check_fails_runs_without_accept(self):
        shown, seen, _calls = await self.update_with((False, [], "Could not fetch the branch master follows."), "no")
        self.assertIn("could not check for a loss of data; update.sh refuses any then", shown[0])
        self.assertIn("Could not fetch the branch master follows.", shown[0])       # why, from update.sh
        self.assertEqual(shown[1], "no")
        self.assertEqual(seen, [])
        shown, seen, _calls = await self.update_with((False, [], ""), "yes")
        self.assertEqual(seen, [[os.path.join(cli.REPO_DIR, "update.sh"), "-y"]])

    async def test_the_update_buttons_wait_for_the_check(self):
        import threading
        release = threading.Event()
        backend = FakeBackend()

        def slow(args):
            release.wait(5)
            return True, [], ""
        backend.check_data_loss = slow
        FakeEngine.seen.clear()
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("update")
            await pilot.pause(0.2)
            await pilot.click("#run-update")
            await pilot.pause(0.2)
            self.assertTrue(app.query_one("#run-update").disabled)
            self.assertTrue(app.query_one("#run-update-start").disabled)
            self.assertIn("Checking for a loss of data …", [str(note.message) for note in app._notifications])
            app.run_update(["-y"])                                        # the palette meanwhile: ignored
            release.set()
            await pilot.pause(0.4)
            from tpotctl.screens.task import TaskScreen
            self.assertIsInstance(app.screen, TaskScreen)
            self.assertEqual(len(app.screen_stack), 2)

    async def test_a_check_that_ends_under_another_dialog_only_tells(self):
        # a dialog opened while the check ran (the palette, a question of another page): the question of
        # the update, or the task screen, does not land on top of it; Update asks anew
        import threading
        loss = (True, [("backups", "2 older backups go to make room.")], "")
        for found in (loss, (True, [], "")):
            with self.subTest(findings=bool(found[1])):
                release = threading.Event()
                backend = FakeBackend()

                def slow(args, found=found, release=release):
                    release.wait(5)
                    return found
                backend.check_data_loss = slow
                FakeEngine.seen.clear()
                app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
                try:
                    async with app.run_test(size=(120, 40)) as pilot:
                        await pilot.pause(0.2)
                        app.goto("update")
                        await pilot.pause(0.2)
                        await pilot.click("#run-update")
                        await pilot.pause(0.2)
                        other = ConfirmDialog("Something else?")
                        app.push_screen(other)
                        await pilot.pause(0.2)
                        release.set()
                        await pilot.pause(0.4)
                        self.assertIs(app.screen, other)
                        self.assertEqual(len(app.screen_stack), 2)
                        notes = [str(note.message) for note in app._notifications]
                        self.assertIn("The check for a loss of data is done, but another screen was open: press "
                                      "Update again.", notes)
                        self.assertFalse(app.query_one("#run-update").disabled)
                    self.assertEqual(FakeEngine.seen, [])
                finally:
                    release.set()

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
            self.assertIsInstance(app.screen, ConfirmDialog)                 # a loss of data: asked first
            await pilot.click("#yes")
            await pilot.pause(0.3)
            intro = str(app.screen.query_one("#task-intro").render())
        self.assertIn("data/", intro)
        self.assertIn("replaced", intro)

    async def restore_with(self, backend, index, groups, answer=None):
        """The restore page: archive index, these groups checked (the rest not), Restore; answer the
        question of a loss of data if one comes (None: none expected), then Run in the task screen.
        (what the question said, what had the focus), the commands run."""
        from tpotctl.screens.restore import RestoreScreen
        from tpotctl.screens.task import TaskScreen
        FakeEngine.seen.clear()
        shown = None
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("update")
            await pilot.pause(0.2)
            await pilot.click("#run-restore")
            await pilot.pause(0.3)
            screen = app.screen
            self.assertIsInstance(screen, RestoreScreen)
            screen.query_one("#restore-list").highlighted = index
            await pilot.pause(0.2)
            for group in ops.GROUP_TEXT:
                screen.query_one(f"#group-{group}").value = group in groups
            await pilot.pause(0.1)
            await pilot.click("#restore-go")
            await pilot.pause(0.3)
            if isinstance(app.screen, ConfirmDialog):
                shown = (app.screen.title_text,
                         " ".join("".join(str(w.render()) for w in app.screen.query("Static")).split()),
                         app.focused.id if app.focused else None)
                self.assertIsNotNone(answer, shown)
                await pilot.click(f"#{answer}")
                await pilot.pause(0.3)
            if isinstance(app.screen, TaskScreen):
                shown = shown + (str(app.screen.query_one("#task-intro").render()),) if shown else \
                    (None, None, None, str(app.screen.query_one("#task-intro").render()))
                await pilot.click("#task-run")
                await pilot.pause(0.4)
        return shown, list(FakeEngine.seen)

    def full_backups(self):
        backend = FakeBackend()
        backend.backup_infos = lambda: [
            ops.BackupInfo("/b/new_tpot_backup_full.tar", "20261005101010_tpot_backup_full.tar", 2 ** 30, "full",
                           ["Created:      2026-10-05 10:10:10 +0200"], ["config", "data", "elastic"], es_data=True),
            ops.BackupInfo("/b/old_tpot_backup.tar", "20261001101010_tpot_backup.tar", 2 ** 20, "regular",
                           [], ["data"])]
        return backend

    async def test_restore_of_data_asks_and_no_starts_nothing(self):
        shown, seen = await self.restore_with(self.full_backups(), 0, ["data"], answer="no")
        title, body, focus = shown[:3]
        self.assertEqual(title, "Replace the current data with the backup?")
        self.assertEqual(focus, "no")                                     # the safe answer has the focus
        self.assertIn("are replaced by those of the archive", body)
        self.assertIn("newer files and log lines since the backup are lost", body)
        self.assertIn("The current Elasticsearch data is deleted and replaced, events since 2026-10-05 10:10:10 "
                      "+0200 are lost", body)
        self.assertNotIn("ILM policy", body)                              # elastic is not chosen
        self.assertIn("--accept-data-loss-file", body)
        self.assertEqual(seen, [])

    async def test_restore_of_data_with_yes_confirms_data_only(self):
        restore = os.path.join(cli.REPO_DIR, "restore.sh")
        FakeEngine.files.clear()
        shown, seen = await self.restore_with(self.full_backups(), 0, ["data", "elastic"], answer="yes")
        # the yes is for data/: a shorter ILM policy of the archive is not put back, the dialog says so
        self.assertIn("not put back", shown[1])
        self.assertNotIn("goes back too", shown[1])
        self.assertEqual(seen, [[restore, "-f", "/b/new_tpot_backup_full.tar", "-g", "data,elastic",
                                 "--accept-data-loss-file", "FILE"]])
        self.assertNotIn("--accept-data-loss", seen[0])
        self.assertEqual(len(FakeEngine.files), 1)
        mode, text = FakeEngine.files[0]
        self.assertEqual(mode, 0o600)
        self.assertEqual([line.split()[2] for line in text.splitlines()], ["data"])   # never ilm
        self.assertTrue(text.startswith("@@tpot loss data ") and text.endswith("\n"), text)
        self.assertIn("not put back", shown[3])                           # the intro says the same

    async def test_restore_of_data_without_elasticsearch_data_does_not_name_it(self):
        shown, seen = await self.restore_with(self.full_backups(), 1, ["data"], answer="yes")
        self.assertNotIn("Elasticsearch", shown[1])
        self.assertIn("are replaced by those of the archive", shown[1])
        self.assertEqual(seen[0][-2:], ["--accept-data-loss-file", "FILE"])

    async def test_restore_of_data_names_the_size_of_the_elasticsearch_data(self):
        backend = self.full_backups()
        infos = backend.backup_infos()
        infos[0].es_size = 3 * 2 ** 30
        backend.backup_infos = lambda: infos
        shown, _seen = await self.restore_with(backend, 0, ["data"], answer="no")
        self.assertIn("The archive holds 3.0 GB of Elasticsearch data.", shown[1])
        # without a size (read from the table of contents of the archive) the text stays as it was
        shown, _seen = await self.restore_with(self.full_backups(), 0, ["data"], answer="no")
        self.assertIn("The current Elasticsearch data is deleted and replaced", shown[1])
        self.assertNotIn("The archive holds", shown[1])

    def test_the_date_of_a_backup(self):
        info = ops.BackupInfo("/b/x.tar", "20261001101010_tpot_backup.tar", 1, "regular", [], [])
        self.assertEqual(tapp.backup_written(info), "2026-10-01 10:10")              # from its name
        info.manifest = ["Created:      2026-10-05 10:10:10 +0200"]
        self.assertEqual(tapp.backup_written(info), "2026-10-05 10:10:10 +0200")     # from its MANIFEST
        self.assertEqual(tapp.backup_written(ops.BackupInfo("/b/x.tar", "x.tar", 1, "regular", [], [])),
                         "the backup")

    async def test_restore_without_data_asks_nothing_and_passes_no_flag(self):
        restore = os.path.join(cli.REPO_DIR, "restore.sh")
        shown, seen = await self.restore_with(self.full_backups(), 0, ["config", "elastic"])
        self.assertIsNone(shown[0])                                       # no question
        self.assertIn("ILM policy", shown[3])                             # the intro says it is left out if shorter
        self.assertIn("not put back", shown[3])
        self.assertEqual(seen, [[restore, "-f", "/b/new_tpot_backup_full.tar", "-g", "config,elastic"]])

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

    async def test_elastic_take_over_that_shortens_without_indices_asks_first(self):
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("30", "120", "foreign")
        # the policy changed again in Kibana meanwhile: taking 120 over shortens it, no index is old enough
        backend._plan = elastic.Plan(elastic.Decision("update", {}, "200", False), [], True, shortening=True)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            await pilot.click("#elastic-adopt")
            await pilot.pause(0.3)
            self.assertIn(("plan", "120", True), backend.calls)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            body = " ".join("".join(str(w.render()) for w in app.screen.query("Static")).split())
            self.assertIn("No index is deleted right away.", body)
            self.assertIn("From now on, events older than 120 days are deleted. This cannot be undone.", body)
            await pilot.click("#no")
            await pilot.pause(0.3)
            done = [call[0] for call in backend.calls if isinstance(call, tuple)]
            self.assertNotIn("set", done)
            self.assertNotIn("apply", done)
            self.assertFalse(app.query_one("#elastic-adopt").disabled)          # not busy after the no
            await pilot.click("#elastic-adopt")
            await pilot.pause(0.3)
            await pilot.click("#yes")
            await pilot.pause(0.3)
        self.assertIn(("set", "120"), backend.calls)
        self.assertIn(("apply", "update"), backend.calls)

    async def test_elastic_no_on_the_apply_dialog_writes_nothing(self):
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("30", "90", "managed", pending=True)
        backend._plan = elastic.Plan(elastic.Decision("update", {}, "90", True),
                                     [elastic.Index("logstash-2026.04.01", 0, 500, 2 ** 20)], True, shortening=True)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            await pilot.click("#elastic-apply")
            await pilot.pause(0.3)
            self.assertIn(("plan", "30", True), backend.calls)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            await pilot.click("#no")
            await pilot.pause(0.3)
            self.assertNotIsInstance(app.screen, ConfirmDialog)
            done = [call[0] for call in backend.calls if isinstance(call, tuple)]
            self.assertNotIn("set", done)
            self.assertNotIn("apply", done)
            self.assertFalse(app.query_one("#elastic-apply").disabled)          # still pending, can be asked again
            self.assertIn("TPOT_ES_RETENTION=30 waits", " ".join(str(app.query_one("#elastic-status").render()).split()))

    async def test_elastic_apply_while_elasticsearch_is_unreachable(self):
        """The page read a pending value, Elasticsearch is gone by the time Apply plans: nothing is applied,
        no question, the value stays in .env and waits; the page says why. An unreachable status has no
        Apply at all."""
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("30", "90", "managed", pending=True)
        backend._plan = elastic.Plan(elastic.Decision("keep", None, "", False), [], False,
                                     problem="no answer from http://127.0.0.1:64298")
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            backend._retention = elastic.Status("30", "", "unreachable", "no answer from http://127.0.0.1:64298")
            await pilot.click("#elastic-apply")
            await pilot.pause(0.4)
            self.assertNotIsInstance(app.screen, ConfirmDialog)
            notes = " ".join(str(note.message) for note in app._notifications)
            self.assertIn("no answer from http://127.0.0.1:64298", str(app.query_one("#elastic-status").render()))
            self.assertTrue(app.query_one("#elastic-apply").disabled)           # read anew: unreachable
        self.assertIn(("plan", "30", True), backend.calls)
        self.assertNotIn("apply", [call[0] for call in backend.calls if isinstance(call, tuple)])
        # .env keeps the value it has: written once, with that value, no other one
        self.assertEqual([call for call in backend.calls if isinstance(call, tuple) and call[0] == "set"], [("set", "30")])
        self.assertIn("TPOT_ES_RETENTION=30 is set in .env, but Elasticsearch is not reachable: T-Pot does not "
                      "shorten the ILM policy tpot on its own, the value waits. Once Elasticsearch runs, Apply .env "
                      "to the policy applies it here (it asks first). Why: no answer from http://127.0.0.1:64298.",
                      notes)

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

    async def test_unreadable_list_of_indices_shows_why_and_writes_env_only(self):
        from tpotctl.tests.test_elastic import MalformedAnswerTest, unreadable_plan
        backend = FakeBackend()
        backend._plan = unreadable_plan("7")
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
        self.assertIn(f"Why: {MalformedAnswerTest.UNREADABLE}.", notes)
        self.assertIn(("set", "7"), backend.calls)
        self.assertNotIn("apply", [call[0] for call in backend.calls if isinstance(call, tuple)])

    def test_a_closed_index_is_shown_as_closed(self):
        from tpotctl import elastic
        text = tapp.doomed_text([elastic.Index("logstash-a", 0, 1200, 5 * 2 ** 20),
                                 elastic.Index("logstash-b", 0, 0, 0, True)]).plain
        self.assertIn("logstash-a  1200 events  5.0 MB", text)
        self.assertIn("logstash-b  closed, size unknown", text)
        self.assertIn("2 indices (1200 events and 5.0 MB in the one with a known size; 1 more of unknown size, "
                      "closed) are deleted right away.", text)
        self.assertNotIn("closed  0", text)

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

    async def test_elastic_a_load_while_reading_reads_once_more_afterwards(self):
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
                pane = app.query_one("#elastic", tapp.ElasticPane)
                self.assertEqual(Slow.reads, 1)                 # the read of on_mount, it waits
                pane.load()                                     # .env changed meanwhile (a save elsewhere)
                pane.load()
                await pilot.pause(0.2)
                self.assertEqual(Slow.reads, 1)                 # one read at a time
                self.assertTrue(pane.reload_wanted)
                release.set()
                await pilot.pause(0.5)
                self.assertEqual(Slow.reads, 2)                 # the dropped loads: exactly one read more
                self.assertFalse(pane.reading)
                self.assertFalse(pane.reload_wanted)
        finally:
            release.set()

    async def test_elastic_an_older_read_never_overwrites_a_newer_one(self):
        import asyncio
        import threading
        from tpotctl import elastic
        gate = threading.Event()

        class Racing(FakeBackend):
            answers = []            # (wait for gate, status) for the next reads

            def retention_status(self):
                if Racing.answers:
                    wait, state = Racing.answers.pop(0)
                    if wait:
                        gate.wait(5)
                    return state
                return super().retention_status()

        backend = Racing()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.3)
                pane = app.query_one("#elastic", tapp.ElasticPane)
                self.assertFalse(pane.reading)
                Racing.answers[:] = [(True, elastic.Status("30", "30", "managed")),
                                     (False, elastic.Status("365", "365", "managed"))]
                older = asyncio.ensure_future(pane.show())
                await pilot.pause(0.2)                          # it waits in its thread
                await pane.show()                               # a newer one, done first
                self.assertEqual(pane.status.value, "365")
                gate.set()
                await older
                await pilot.pause(0.1)
                self.assertEqual(pane.status.value, "365")      # the older answer came last, it is dropped
                self.assertIn("365 days", str(app.query_one("#elastic-status").render()))
                self.assertEqual(app.query_one("#elastic-value").value, "365")
        finally:
            gate.set()

    async def test_elastic_set_on_a_foreign_policy_writes_env_and_leaves_the_policy(self):
        # as the Settings page does: .env takes the value, the policy stays as Kibana left it
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("30", "120", "foreign")
        backend._plan = elastic.Plan(elastic.Decision("foreign", None, "120", False), [], True)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            app.query_one("#elastic-value").value = "60"
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            self.assertNotIsInstance(app.screen, ConfirmDialog)
            notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn(("set", "60"), backend.calls)
        self.assertNotIn("apply", [call[0] for call in backend.calls if isinstance(call, tuple)])
        self.assertIn("TPOT_ES_RETENTION=60 is set in .env.", notes)
        self.assertIn("The ILM policy tpot was changed in Kibana, T-Pot leaves it as it is", notes)
        # both buttons ask first where they shorten the retention (Take over too: Kibana may have changed it)
        self.assertIn("Take over from Kibana keeps it, Apply .env to the policy applies .env to it; either asks "
                      "first if that shortens the retention.", notes)
        self.assertNotIn("(it asks first)", notes)

    async def test_settings_on_a_foreign_policy_names_both_buttons_alike(self):
        from tpotctl import elastic
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        backend._plan = elastic.Plan(elastic.Decision("foreign", None, "120", False), [], True)
        app, _pilot = await self.save_retention(backend, "365")
        notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn("On the Elastic page, Take over from Kibana keeps it, Apply .env to the policy applies .env "
                      "to it; either asks first if that shortens the retention.", notes)

    async def test_elastic_a_question_never_lands_on_another_screen(self):
        """Another screen opened while the plan was made (the palette, a question of another page): no question
        on top of it, a note to press the button again, nothing written, the buttons free again."""
        import threading
        from tpotctl import elastic
        for button, value, again in (("#elastic-set", "30", "Set"), ("#elastic-apply", None, "Apply .env to the policy"),
                                     ("#elastic-adopt", None, "Take over from Kibana")):
            with self.subTest(button=button):
                release = threading.Event()

                class Slow(FakeBackend):
                    def retention_plan(self, value, force=False):
                        self.calls.append(("plan", value, force))
                        release.wait(5)
                        return elastic.Plan(elastic.Decision("update", {}, "120", False),
                                            [elastic.Index("logstash-2026.08.01", 0, 1200, 5 * 2 ** 20)], True,
                                            shortening=True)

                backend = Slow()
                backend._retention = elastic.Status("30", "120", "foreign")
                app = tapp.TpotApp(backend=backend, runner=Recorder())
                try:
                    async with app.run_test(size=(120, 40)) as pilot:
                        await pilot.pause(0.2)
                        app.goto("elastic")
                        await pilot.pause(0.3)
                        if value:
                            app.query_one("#elastic-value").value = value
                        await pilot.click(button)
                        await pilot.pause(0.2)
                        self.assertTrue(any(call[0] == "plan" for call in backend.calls if isinstance(call, tuple)))
                        other = ConfirmDialog("Something else?")
                        app.push_screen(other)
                        await pilot.pause(0.2)
                        release.set()
                        await pilot.pause(0.4)
                        self.assertIs(app.screen, other)
                        self.assertEqual(len(app.screen_stack), 2)
                        notes = [str(note.message) for note in app._notifications]
                        self.assertIn(f"The check of the ILM policy is done, but another screen was open: press "
                                      f"{again} again.", notes)
                        pane = app.query_one("#elastic", tapp.ElasticPane)
                        self.assertFalse(pane.busy)
                        self.assertFalse(app.query_one("#elastic-set").disabled)
                    done = [call[0] for call in backend.calls if isinstance(call, tuple)]
                    self.assertNotIn("set", done)
                    self.assertNotIn("apply", done)
                finally:
                    release.set()

    async def test_settings_a_question_never_lands_on_another_screen(self):
        import threading
        from tpotctl import elastic, settings
        repo = self.retention_checkout()
        release = threading.Event()

        class Slow(FakeBackend):
            def settings(self):
                return settings.load(repo)

            def retention_plan(self, value, force=False):
                self.calls.append(("plan", value, force))
                release.wait(5)
                return elastic.Plan(elastic.Decision("update", {}, "90", True),
                                    [elastic.Index("logstash-2026.08.01", 0, 1200, 5 * 2 ** 20)], True,
                                    shortening=True)

        backend = Slow()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        try:
            async with app.run_test(size=(150, 50)) as pilot:
                await pilot.pause(0.3)
                app.goto("settings")
                await pilot.pause(0.4)
                app.query_one("#set-TPOT_ES_RETENTION").value = "30"
                await pilot.pause(0.3)
                await pilot.click("#settings-save")
                await pilot.pause(0.2)
                other = ConfirmDialog("Something else?")
                app.push_screen(other)
                await pilot.pause(0.2)
                release.set()
                await pilot.pause(0.4)
                self.assertIs(app.screen, other)
                notes = [str(note.message) for note in app._notifications]
                self.assertIn("The check of the ILM policy is done, but another screen was open: press Save again.",
                              notes)
                self.assertFalse(app.query_one("#settings").saving)
                self.assertFalse(app.query_one("#settings-save").disabled)
            self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "90")
            self.assertNotIn("apply", [call[0] for call in backend.calls if isinstance(call, tuple)])
        finally:
            release.set()

    async def test_settings_an_error_after_the_yes_releases_save_and_names_it(self):
        """save_after_yes: whatever goes wrong, the T-Pot Manager stays, Save is free again, the note names the
        error and nothing more is written (an error in the write itself: nothing at all)."""
        from unittest import mock
        from tpotctl import settings
        for where in ("write", "apply"):
            with self.subTest(where=where):
                repo = self.retention_checkout()
                backend = self.settings_backend(repo)
                applied = []

                def apply(plan):
                    applied.append(plan)
                    raise RuntimeError("the policy went away")

                backend.apply_retention = apply
                patch = (mock.patch.object(settings.Settings, "change", side_effect=PermissionError("read-only"))
                         if where == "write" else mock.patch.object(settings.Settings, "change", autospec=True,
                                                                     side_effect=settings.Settings.change))
                with patch:
                    app, pilot = await self.save_retention(backend, "30")
                    self.assertIsInstance(app.screen, ConfirmDialog)
                    await pilot.click("#yes")
                    await pilot.pause(0.5)
                    self.assertTrue(app.is_running)
                    notes = " ".join(str(note.message) for note in app._notifications)
                    pane = app.query_one("#settings")
                    self.assertFalse(pane.saving)
                    # Save as the changes say: free while the value is not written, nothing to save once it is
                    self.assertEqual(app.query_one("#settings-save").disabled, not pane.changes())
                    if where == "write":
                        self.assertFalse(app.query_one("#settings-save").disabled)
                        self.assertIn("read-only", notes)
                        self.assertEqual(applied, [])
                        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "90")
                    else:
                        self.assertIn("the policy went away", notes)
                        self.assertEqual(len(applied), 1)
                        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "30")
                        # .env holds it, the policy does not: the value waits, and the note says how to apply it
                        self.assertIn("TPOT_ES_RETENTION=30 is in .env, the ILM policy tpot may not have taken it: "
                                      "if the policy still keeps the old value, the value waits until it is applied "
                                      "(Apply .env to the policy on the Elastic "
                                      "page, or tpot elastic retention --force).", notes)
                    if where == "write":
                        self.assertNotIn("is in .env", notes)
                    self.assertIn("The save stopped with an error", notes)
                    self.assertIn("Nothing more was written", notes)

    async def test_elastic_unset_env_and_no_elasticsearch_does_not_promise_a_wait(self):
        # an unset .env shortens nothing (cli._shortens_env): the text of an unknown policy, as tpot env set
        from tpotctl import elastic
        backend = FakeBackend()
        backend._retention = elastic.Status("", "", "unreachable", problem="Elasticsearch is not reachable")
        backend._plan = elastic.Plan(elastic.Decision("keep", None, "", False), [], False)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.goto("elastic")
            await pilot.pause(0.3)
            app.query_one("#elastic-value").value = "30"
            await pilot.click("#elastic-set")
            await pilot.pause(0.3)
            notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn(("set", "30"), backend.calls)
        self.assertIn("TPOT_ES_RETENTION=30 is set. T-Pot applies it on its next start if the ILM policy tpot "
                      "keeps the events less long", notes)
        self.assertNotIn("the value waits", notes)

    def test_instead_of_the_elastic_page_names_kibana_as_elsewhere(self):
        self.assertIn(tapp.KIBANA_ILM, tapp.INSTEAD["elastic"])
        self.assertNotIn(">", tapp.INSTEAD["elastic"])

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

    async def test_settings_unreachable_off_a_tpot_host_names_the_tpot_host(self):
        # tpot elastic only runs on the T-Pot host: off it, the text says where it applies a shorter value
        from tpotctl import elastic
        for host, wanted, unwanted in ((False, "apply it on the T-Pot host with 'tpot elastic retention --force'",
                                        "waits for 'tpot elastic retention --force'"),
                                       (True, "waits for 'tpot elastic retention --force'", "on the T-Pot host")):
            with self.subTest(host=host):
                repo = self.retention_checkout()
                backend = self.settings_backend(repo)
                backend.host = host
                backend._plan = elastic.Plan(elastic.Decision("keep", None, "", False), [], False)
                app, _pilot = await self.save_retention(backend, "365")
                notes = " ".join(str(note.message) for note in app._notifications)
                self.assertIn(wanted, notes)
                self.assertNotIn(unwanted, notes)

    async def test_settings_a_plan_that_fails_in_any_way_saves_nothing_and_enables_save(self):
        from tpotctl import settings
        for error in (ValueError("not an answer"), KeyError("phases"), AttributeError("get")):
            with self.subTest(error=type(error).__name__):
                repo = self.retention_checkout()
                backend = self.settings_backend(repo)

                def failing(value, force=False, error=error):
                    raise error

                backend.retention_plan = failing
                app, _pilot = await self.save_retention(backend, "365")
                notes = " ".join(str(note.message) for note in app._notifications)
                self.assertIn("Nothing was saved: the check of the ILM policy failed", notes)
                self.assertNotIn("TPOT_ES_RETENTION is not saved", notes)      # no other change was saved either
                self.assertIn(str(error), notes)
                self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "90")      # nothing written
                self.assertNotIn("apply", [call[0] for call in backend.calls if isinstance(call, tuple)])
                self.assertFalse(app.query_one("#settings").saving)
                self.assertFalse(app.query_one("#settings-save").disabled)

    async def test_settings_unset_value_and_no_elasticsearch_does_not_promise_a_wait(self):
        # .env has no value: nothing to shorten, the policy is unknown (the text of tpot elastic)
        from tpotctl import elastic
        repo = self.retention_checkout()
        path = os.path.join(repo, ".env")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace("TPOT_ES_RETENTION=90\n", "TPOT_ES_RETENTION=\n", 1))
        backend = self.settings_backend(repo)
        backend._plan = elastic.Plan(elastic.Decision("keep", None, "", False), [], False)
        app, _pilot = await self.save_retention(backend, "30")
        notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn("TPOT_ES_RETENTION=30 is set. T-Pot applies it on its next start if the ILM policy tpot "
                      "keeps the events less long", notes)
        self.assertNotIn("the value waits", notes)

    async def test_settings_unreadable_list_of_indices_shows_why_and_writes_env_only(self):
        from tpotctl import settings
        from tpotctl.tests.test_elastic import MalformedAnswerTest, unreadable_plan
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        backend._plan = unreadable_plan("30")
        app, _pilot = await self.save_retention(backend, "30")
        self.assertNotIsInstance(app.screen, ConfirmDialog)
        notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn("the value waits", notes)
        self.assertIn(f"Why: {MalformedAnswerTest.UNREADABLE}.", notes)
        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "30")
        self.assertNotIn("apply", [call[0] for call in backend.calls if isinstance(call, tuple)])

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

    async def test_settings_only_retention_on_a_foreign_policy_offers_no_restart(self):
        """A restart would not change a policy changed outside of T-Pot either: the notice says what does."""
        from tpotctl import elastic, settings
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        backend.host = True
        backend._plan = elastic.Plan(elastic.Decision("foreign", None, "120", False), [], True)
        app, pilot = await self.save_retention(backend, "30")
        await pilot.pause(0.3)
        self.assertNotIsInstance(app.screen, ConfirmDialog)
        self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "30")
        notes = " ".join(str(note.message) for note in app._notifications)
        self.assertIn("Take over from Kibana", notes)
        self.assertNotIn("restart T-Pot", notes)

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

    async def test_settings_save_stays_disabled_after_the_yes_until_applied(self):
        import threading
        from tpotctl import settings
        release = threading.Event()
        repo = self.retention_checkout()
        applied = []

        class Slow(type(self.settings_backend(repo))):
            def apply_retention(self, plan):
                release.wait(5)
                applied.append(plan.decision.action)

        backend = Slow()
        try:
            app, pilot = await self.save_retention(backend, "30")
            pane = app.query_one("#settings")
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertTrue(pane.saving)                        # the question is part of the save
            await pilot.click("#yes")
            await pilot.pause(0.4)
            self.assertEqual(applied, [])                       # the apply still runs
            self.assertTrue(pane.saving)
            self.assertTrue(app.query_one("#settings-save").disabled)
            pane.check()                                        # a refresh meanwhile keeps it disabled
            self.assertTrue(app.query_one("#settings-save").disabled)
            pane.action_save()                                  # ctrl+s meanwhile: nothing
            await pilot.pause(0.2)
            self.assertFalse(isinstance(app.screen, ConfirmDialog))
            self.assertEqual([c for c in backend.calls if isinstance(c, tuple) and c[0] == "plan"],
                             [("plan", "30", False)])
            release.set()
            for _wait in range(100):
                if not pane.saving:
                    break
                await pilot.pause(0.1)
            self.assertFalse(pane.saving)
            self.assertEqual(applied, ["update"])
            self.assertEqual(settings.load(repo).values["TPOT_ES_RETENTION"], "30")
        finally:
            release.set()

    async def test_settings_no_to_the_question_enables_save_again(self):
        repo = self.retention_checkout()
        backend = self.settings_backend(repo)
        app, pilot = await self.save_retention(backend, "30")
        self.assertIsInstance(app.screen, ConfirmDialog)
        await pilot.click("#no")
        await pilot.pause(0.3)
        self.assertFalse(app.query_one("#settings").saving)
        self.assertFalse(app.query_one("#settings-save").disabled)      # the change is still there

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


def flat(widget) -> str:
    return " ".join(str(widget.render()).split())


def sample_check(**over):
    """A last.json as tpot check honeypots writes it (run.Report), read back as the page reads it."""
    import json
    from datetime import datetime, timezone
    from tpotctl.checks import run as trun, stages as tstages
    cowrie = tstages.Row("cowrie", "honeypots", "cowrie", True, False, "Cowrie", ["22/tcp", "23/tcp"])
    for name in tstages.STAGES:
        cowrie.set(name, tstages.OK, f"{name} of cowrie")
    cowrie.log_line = '{"eventid": "cowrie.session.connect", "src_ip": "192.0.2.5"}'
    cowrie.es_id, cowrie.es_index = "abc123", "logstash-2026.10.17"
    cowrie.geo = tstages.GeoEvent("8.8.8.8", 51234, "cowrie/log/cowrie.json", '{"src_ip": "8.8.8.8"}', "geo1",
                                  "logstash-2026.10.17", {"lat": 37.75, "lon": -97.8, "country": "United States",
                                                          "ip_rep": "known attacker"})
    dionaea = tstages.Row("dionaea", "honeypots", "dionaea", True, False, "Dionaea", ["21/tcp"])
    dionaea.set("port", tstages.OK, "21/tcp open")
    dionaea.set("answer", tstages.FAILED, "no [bold]banner[/bold] in 10 s")
    dionaea.set("log", tstages.NOT_APPLICABLE, "the knock left no trace")
    glutton = tstages.Row("glutton", "honeypots", "glutton", False, True, "Glutton", [])
    glutton.set("port", tstages.NOT_APPLICABLE, "not running")
    report = trun.Report(run_id="20261017080000a1b2c3", token="tpot-check-20261017080000a1b2c3",
                         started=datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc),
                         ended=datetime(2026, 10, 17, 8, 4, tzinfo=timezone.utc), host="192.0.2.5",
                         options={"only": [], "geo": True, "keep": False, "timeout": 90.0},
                         rows=[cowrie, dionaea, glutton], exit_code=1, result="failed",
                         ewsposter={"state": "held", "detail": ""},
                         cleanup={"state": "done", "deleted": 12, "passes": [10, 2], "left": 3,
                                  "left_kibana": "https://192.0.2.5:64297/kibana/app/discover#/?left",
                                  "detail": ""},
                         kibana="https://192.0.2.5:64297/kibana/app/discover#/?_g=(run)")
    data = report.as_json()
    data.update(over)
    return trun.result_view(json.loads(json.dumps(data)))


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class ChecksPageTest(unittest.IsolatedAsyncioTestCase):
    """The Checks page: the switches, the question, tpot check in the task screen, the table of last.json."""

    SIZE = (150, 50)

    def setUp(self):
        FakeEngine.seen.clear()
        self.addCleanup(tapp.glyphs.set_mode, "unicode")

    async def open(self, pilot, app):
        await pilot.pause(0.2)
        app.goto("checks")
        await pilot.pause(0.3)
        return app.query_one("#checks")

    async def until(self, pilot, test):
        for _ in range(40):
            if test():
                return
            await pilot.pause(0.05)

    async def test_no_check_yet(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            await self.open(pilot, app)
            self.assertEqual(flat(app.query_one("#check-summary")), tapp.NO_CHECK_YET)
            self.assertFalse(app.query_one("#check-results").display)
            info = flat(app.query_one("#checks-info"))
        for words in ("ewsposter sends nothing", "test event", "removes the check's own events", "tpot check"):
            self.assertIn(words, info)
        self.assertNotIn("RDPy", info)

    async def test_a_result_that_cannot_be_read_is_one_line(self):
        from tpotctl.checks import run as trun
        for problem in (trun.ResultError("/x/last.json: no JSON"), RuntimeError("anything else")):
            backend = FakeBackend()
            backend.check_result_value = problem
            app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
            async with app.run_test(size=self.SIZE) as pilot:
                await self.open(pilot, app)
                with self.subTest(problem=problem):
                    self.assertEqual(flat(app.query_one("#check-summary")), f"{tapp.CHECK_UNREADABLE} {problem}")
                    self.assertFalse(app.query_one("#check-results").display)

    async def test_an_error_before_the_hold_shows_its_text(self):
        backend = FakeBackend()
        backend.check_result_value = sample_check(result="error", exit_code=1, honeypots=[],
                                                  error="no address of this host found to probe")
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            await self.open(pilot, app)
            self.assertIn("no address of this host found to probe", flat(app.query_one("#check-summary")))
            self.assertFalse(app.query_one("#check-results").display)

    async def test_the_table_has_a_row_per_honeypot_and_a_column_per_stage(self):
        from tpotctl import theme
        from tpotctl.checks import stages as tstages
        backend = FakeBackend()
        backend.check_result_value = sample_check()
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            await self.open(pilot, app)
            table = app.query_one("#check-results")
            self.assertTrue(table.display)
            self.assertEqual([str(c.label) for c in table.ordered_columns],
                             ["Honeypot"] + [tstages.SHORT_TITLES[name] for name in tstages.STAGES])
            self.assertEqual(table.fixed_columns, 1)
            legend = flat(app.query_one("#check-legend"))
            for name in tstages.STAGES:
                if tstages.SHORT_TITLES[name] != tstages.TITLES[name]:
                    self.assertIn(f"{tstages.SHORT_TITLES[name]}: {tstages.TITLES[name]}.", legend)
            self.assertEqual([row.key.value for row in table.ordered_rows], ["cowrie", "dionaea", "glutton"])
            cells = table.get_row("dionaea")
            self.assertEqual([cell.plain for cell in cells[1:4]], ["✓", "✗", "–"])
            self.assertEqual(cells[4].plain, "·")
            self.assertIn(theme.color("error"), str(cells[2].style))
            self.assertEqual(str(cells[1].style), theme.color("ok"))
            summary = flat(app.query_one("#check-summary"))
        self.assertIn("1 honeypot(s) with a failed stage: dionaea", summary)
        self.assertIn("Removed 12 test events from Elasticsearch (10 + 2 in two passes).", summary)
        self.assertIn("3 probe events could not be told apart from real ones and stay; Kibana: "
                      "https://192.0.2.5:64297/kibana/app/discover#/?left", summary)

    async def test_the_glyphs_follow_the_icon_set(self):
        backend = FakeBackend()
        backend.check_result_value = sample_check()
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            await self.open(pilot, app)
            # set_icons saves the choice: the other tests take unicode, also when this one fails
            self.addCleanup(tapp.prefs.save, tapp.prefs.Prefs("unicode"))
            app.set_icons("ascii")
            await pilot.pause(0.3)
            cells = app.query_one("#check-results").get_row("dionaea")
        self.assertEqual([cell.plain for cell in cells[1:5]], ["+", "x", "-", "."])

    async def test_a_reload_keeps_the_cursor_and_changes_only_the_cells(self):
        from tpotctl.checks import stages as tstages
        backend = FakeBackend()
        backend.check_result_value = sample_check()
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            pane = await self.open(pilot, app)
            table = app.query_one("#check-results")
            table.move_cursor(row=2)
            later = sample_check()
            later["honeypots"][2]["stages"]["port"]["state"] = tstages.OK
            backend.check_result_value = later
            cleared = []
            table.clear = lambda *args, **kwargs: cleared.append(args)
            pane.load()
            await pilot.pause(0.2)
            self.assertEqual(cleared, [])
            self.assertEqual(table.cursor_row, 2)
            self.assertEqual(table.get_row("glutton")[1].plain, "✓")

    async def test_the_table_fits_at_80_columns(self):
        """80 x 24 next to the menu: every stage column in view, the name stays when it scrolls sideways."""
        backend = FakeBackend()
        backend.check_result_value = sample_check()
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(80, 24)) as pilot:
            await self.open(pilot, app)
            table = app.query_one("#check-results")
            table.focus()
            await pilot.pause(0.3)
            self.assertEqual(table.max_scroll_x, 0, (table.virtual_size, table.size))
            self.assertEqual(table.fixed_columns, 1)

    async def test_a_change_of_running_repaints_the_name(self):
        from tpotctl import theme
        backend = FakeBackend()
        backend.check_result_value = sample_check()
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            pane = await self.open(pilot, app)
            table = app.query_one("#check-results")
            self.assertIn(theme.color("ash"), str(table.get_row("glutton")[0].spans))
            later = sample_check()
            later["honeypots"][2]["running"] = True
            backend.check_result_value = later
            pane.load()
            await pilot.pause(0.2)
            self.assertIn(theme.color("glass"), str(table.get_row("glutton")[0].spans))
            self.assertNotIn(theme.color("ash"), str(table.get_row("glutton")[0].spans))

    async def test_enter_on_a_row_shows_its_details(self):
        backend = FakeBackend()
        backend.check_result_value = sample_check()
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            await self.open(pilot, app)
            table = app.query_one("#check-results")
            table.focus()
            table.move_cursor(row=0)
            await pilot.press("enter")
            await self.until(pilot, lambda: isinstance(app.screen, tapp.CheckDetailDialog))
            self.assertIsInstance(app.screen, tapp.CheckDetailDialog)
            body = app.screen.query_one("#check-detail-body").render().plain
            self.assertEqual(app.screen.query_one("#check-detail").border_title, "cowrie")
            for words in ("22/tcp, 23/tcp", "elasticsearch of cowrie", "Attack Map", "cowrie.session.connect",
                          "logstash-2026.10.17 / abc123", "8.8.8.8:51234", "cowrie/log/cowrie.json",
                          "country United States", "ip_rep known attacker",
                          "https://192.0.2.5:64297/kibana/app/discover#/?_g=(run)",
                          "Removed 12 test events", "3 probe events", "discover#/?left"):
                self.assertIn(words, body)
            await pilot.press("escape")
            await pilot.pause(0.2)
            self.assertNotIsInstance(app.screen, tapp.CheckDetailDialog)
            table.focus()
            table.move_cursor(row=1)
            await pilot.press("enter")
            await self.until(pilot, lambda: isinstance(app.screen, tapp.CheckDetailDialog))
            body = app.screen.query_one("#check-detail-body").render().plain
        self.assertIn("no [bold]banner[/bold] in 10 s", body)     # data, no markup
        self.assertIn("not reached", body)

    async def test_the_check_asks_first_and_runs_tpot_check_with_the_switches(self):
        from tpotctl.checks import run as trun
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
        loads = []
        async with app.run_test(size=self.SIZE) as pilot:
            pane = await self.open(pilot, app)
            original = pane.load
            pane.load = lambda: (loads.append(1), original())
            self.assertTrue(app.query_one("#check-geo").value)
            self.assertTrue(app.query_one("#check-cleanup").value)
            await pilot.click("#check-honeypots")
            await self.until(pilot, lambda: isinstance(app.screen, ConfirmDialog))
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertEqual(app.focused.id, "no")
            self.assertEqual(app.screen.body.plain, trun.question(trun.Options()))
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertEqual(FakeEngine.seen, [])
            app.query_one("#check-geo").value = False
            app.query_one("#check-cleanup").value = False
            await pilot.click("#check-honeypots")
            await self.until(pilot, lambda: isinstance(app.screen, ConfirmDialog))
            self.assertEqual(app.screen.body.plain, trun.question(trun.Options(keep=True, geo=False)))
            await pilot.click("#yes")
            await pilot.pause(0.5)
            self.assertEqual(FakeEngine.seen, [[tapp.LAUNCHER, "check", "honeypots", "-y", "--no-geo", "--keep"]])
            self.assertEqual(loads, [])
            await pilot.click("#task-back")
            await pilot.pause(0.3)
        self.assertEqual(loads, [1])                # last.json anew after the task screen

    async def test_the_task_takes_a_sudo_password_file(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        tasks = []
        async with app.run_test(size=self.SIZE) as pilot:
            await self.open(pilot, app)
            app.run_task = lambda task, then=None: tasks.append((task, then))
            for button in ("#check-honeypots", "#check-pipeline"):
                await pilot.click(button)
                await self.until(pilot, lambda: isinstance(app.screen, ConfirmDialog))
                await pilot.click("#yes")
                await pilot.pause(0.2)
            await pilot.click("#check-dry")
            await pilot.pause(0.2)
        self.assertEqual([task.command for task, _then in tasks],
                         [[tapp.LAUNCHER, "check", "honeypots", "-y"], [tapp.LAUNCHER, "check", "pipeline", "-y"],
                          [tapp.LAUNCHER, "check", "honeypots", "--dry-run"]])
        self.assertEqual([task.become for task, _then in tasks], ["--become-file", "--become-file", ""])
        self.assertTrue(all(task.autostart and then is not None for task, then in tasks))

    async def test_the_pipeline_asks_and_keeps_with_cleanup_off(self):
        from tpotctl.checks import run as trun
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            await self.open(pilot, app)
            app.query_one("#check-cleanup").value = False
            await pilot.click("#check-pipeline")
            await self.until(pilot, lambda: isinstance(app.screen, ConfirmDialog))
            self.assertEqual(app.screen.body.plain, trun.question(trun.Options(keep=True, geo_only=True)))
            await pilot.click("#yes")
            await pilot.pause(0.5)
        self.assertEqual(FakeEngine.seen, [[tapp.LAUNCHER, "check", "pipeline", "-y", "--keep"]])

    async def test_probe_with_nmap_runs_hptest(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            await self.open(pilot, app)
            self.assertEqual(app.query_one("#check-host").value, "192.0.2.5")
            await pilot.click("#check-nmap")
            await pilot.pause(0.3)
            await pilot.click("#task-run")
            await pilot.pause(0.4)
        self.assertEqual(FakeEngine.seen, [[cli.HPTEST, "--nmap", "192.0.2.5"]])

    async def test_a_sensor_checks_three_stages(self):
        from tpotctl.checks import run as trun
        app = tapp.TpotApp(backend=FakeBackend(kind="SENSOR"), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=self.SIZE) as pilot:
            await self.open(pilot, app)
            self.assertNotIsInstance(app.query_one("#checks"), tapp.LockedPane)
            self.assertTrue(app.query_one("#check-pipeline").disabled)
            self.assertTrue(app.query_one("#check-geo").disabled)
            self.assertTrue(app.query_one("#check-cleanup").disabled)
            self.assertIn("HIVE", str(app.query_one("#check-cleanup").tooltip))
            self.assertIn("SENSOR", flat(app.query_one("#checks-info")))
            app.query_one("#check-cleanup").value = False       # as it may be from before: no --keep there
            await pilot.click("#check-honeypots")
            await self.until(pilot, lambda: isinstance(app.screen, ConfirmDialog))
            self.assertEqual(app.screen.body.plain, trun.question(trun.Options(), sensor=True))
            await pilot.click("#yes")
            await pilot.pause(0.5)
        self.assertEqual(FakeEngine.seen, [[tapp.LAUNCHER, "check", "honeypots", "-y"]])

    async def test_the_state_of_ewsposter(self):
        from tpotctl import theme
        for state, words, colour in (("normal", "sends as usual", "mist"), ("held", "is held", "warn"),
                                     ("stuck", "is stuck after a hold", "warn"), ("unknown", "does not say", "ash")):
            backend = FakeBackend()
            backend.ews = (state, 10.0 if state != "unknown" else None)
            app = tapp.TpotApp(backend=backend, runner=Recorder(), engine=FakeEngine)
            async with app.run_test(size=self.SIZE) as pilot:
                await self.open(pilot, app)
                rendered = app.query_one("#check-hold").render()
                with self.subTest(state=state):
                    self.assertIn(words, rendered.plain)
                    self.assertIn(theme.color(colour), str(rendered.spans))


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class KibanaObjectsPageTest(unittest.IsolatedAsyncioTestCase):
    """The Kibana objects on the Elastic page: the state, the mode, Export, Update, Overwrite all, Restore."""

    SIZE = (120, 60)

    async def page(self, pilot, app):
        await pilot.pause(0.2)
        app.goto("elastic")
        await pilot.pause(0.4)
        return app.query_one("#elastic")

    def body(self, app) -> str:
        return " ".join(" ".join(str(w.render()) for w in app.screen.query("Static")).split())

    def notes(self, app):
        return [(str(note.message), note.severity) for note in app._notifications]

    def kibana_calls(self, backend, name):
        return [c for c in backend.calls if isinstance(c, tuple) and c[0] == name]

    async def press(self, pilot, app, button, wait=0.4):
        app.query_one(button).press()
        await pilot.pause(wait)

    async def test_kibana_status_line(self):
        from tpotctl import kibana_objects as ko
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            status = flat(app.query_one("#kibana-status"))
            self.assertIn("TPOT_KIBANA_OBJECTS update", status)
            self.assertIn("1 new · 1 to update · 1 the same · 1 of yours", status)
            table = app.query_one("#kibana-exports")
            self.assertEqual(table.row_count, 2)
            self.assertEqual([str(c) for c in table.get_row_at(0)][1:], ["Manager", "1.0 MB", "349"])
            self.assertEqual([str(c) for c in table.get_row_at(1)][1:], ["T-Pot", "512.0 KB", "300"])
            # the pros and cons of the mode, as the Settings page shows them, and what it would do now
            shown = flat(app.query_one("#kibana-mode-help"))
            self.assertIn(dict((v, h) for v, _l, h in tapp.kibana_modes())["update"], shown)
            self.assertIn("On the next start: 2 Kibana objects are imported after an export (1 new), 1 object of "
                          "yours is kept.", shown)
            for state, expected in ((cli.KibanaState("keep", pending=True), cli.KIBANA_PENDING),
                                    (cli.KibanaState("update", kibana=False), "no Kibana in this edition"),
                                    (cli.KibanaState("update", problem="Kibana at http://127.0.0.1:64296 is not "
                                                                       "reachable (refused)"),
                                     "Kibana at http://127.0.0.1:64296 is not reachable (refused)")):
                with self.subTest(expected=expected):
                    backend._kibana = state
                    app.goto("status")
                    await pilot.pause(0.2)
                    app.goto("elastic")
                    await pilot.pause(0.4)
                    self.assertIn(expected, flat(app.query_one("#kibana-status")))
        self.assertIn("the first import has not finished yet", cli.KIBANA_PENDING)
        self.assertEqual(ko.STATUS_TIMEOUT, 5)

    async def test_update_asks_and_lists_yours(self):
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            await self.press(pilot, app, "#kibana-update")
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            self.assertEqual(str(app.screen.query_one("#yes").label), "Update")
            body = self.body(app)
            self.assertIn("Kibana objects: 1 new · 1 to update · 1 the same · 1 of yours", body)
            self.assertIn("Mode update imports 2, an export comes first.", body)
            self.assertIn("These objects of yours stay as they are: Mine (dashboard:d)", body)
            self.assertIn("This runs once: TPOT_KIBANA_OBJECTS stays update.", body)
            await pilot.click("#yes")
            await pilot.pause(0.4)
            self.assertEqual(self.kibana_calls(backend, "kibana-update"), [("kibana-update", False, None)])
            self.assertIsInstance(app.screen, ConfirmDialog)            # the result
            result = self.body(app)
            self.assertIn("1 new, 1 updated, 1 the same, 1 of yours kept (export: "
                          "/b/kibana_objects_20261016120000.ndjson).", result)
            self.assertIn("These objects of yours stay as they are: Mine (dashboard:d)", result)
            self.assertFalse(app.screen.query("#yes"))                  # only Close
            await pilot.click("#no")
            await pilot.pause(0.3)
        self.assertEqual(self.kibana_calls(backend, "kibana-mode"), [])     # the mode stays

    async def test_update_back_writes_nothing(self):
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            for button in ("#kibana-update", "#kibana-overwrite"):
                with self.subTest(button=button):
                    await self.press(pilot, app, button)
                    self.assertIsInstance(app.screen, ConfirmDialog)
                    await pilot.press("enter")                      # #no has the focus
                    await pilot.pause(0.3)
                    self.assertNotIsInstance(app.screen, ConfirmDialog)
                    self.assertFalse(app.query_one(button).disabled)
        self.assertEqual(self.kibana_calls(backend, "kibana-update"), [])

    async def test_overwrite_names_what_is_replaced(self):
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            await self.press(pilot, app, "#kibana-overwrite")
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            self.assertEqual(app.screen.title_text, "Overwrite 1 object of yours with the T-Pot version?")
            self.assertEqual(str(app.screen.query_one("#yes").label), "Overwrite")
            body = self.body(app)
            self.assertIn("Mode overwrite imports 3, an export comes first.", body)
            self.assertIn("These objects of yours are replaced by the T-Pot version, the export keeps them: Mine "
                          "(dashboard:d)", body)
            await pilot.click("#yes")
            await pilot.pause(0.4)
            self.assertIn("1 of yours replaced", self.body(app))
        self.assertEqual(self.kibana_calls(backend, "kibana-update"), [("kibana-update", True, {"dashboard:d"})])
        self.assertEqual(self.kibana_calls(backend, "kibana-mode"), [])

    async def test_nothing_to_import_is_a_note(self):
        from tpotctl import kibana_objects as ko
        backend = FakeBackend()
        backend._kibana_plan = ko.Plan([ko.Item("dashboard:c", "dashboard", "c", "Cowrie", "current")])
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            await self.press(pilot, app, "#kibana-update")
            self.assertNotIsInstance(app.screen, ConfirmDialog)
            self.assertIn(("Nothing to import (0 new · 0 to update · 1 the same · 0 of yours).", "information"),
                          self.notes(app))
        self.assertEqual(self.kibana_calls(backend, "kibana-update"), [])

    async def test_export_notifies_and_refreshes(self):
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            before = backend.calls.count("kibana-exports")
            await self.press(pilot, app, "#kibana-export")
            self.assertIn(("4 Kibana objects exported to /b/kibana_objects_20261016120001.ndjson.", "information"),
                          self.notes(app))
            self.assertGreater(backend.calls.count("kibana-exports"), before)       # the table read anew

    async def test_restore_uses_the_chosen_export(self):
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        chosen = "/d/kibana_objects/exports/auto_20261015100000.ndjson"
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            table = app.query_one("#kibana-exports")
            table.focus()
            await pilot.press("down")
            await pilot.press("enter")                          # enter_goes_on: on to Restore
            await pilot.pause(0.1)
            self.assertIs(app.focused, app.query_one("#kibana-restore"))
            await pilot.press("enter")
            await pilot.pause(0.4)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            self.assertIn(cli.kibana_restore_question(chosen, 4), self.body(app))
            await pilot.click("#yes")
            await pilot.pause(0.4)
            self.assertIn("1 Kibana object restored (export: /b/kibana_objects_20261016120002.ndjson).",
                          self.body(app))
        self.assertEqual(self.kibana_calls(backend, "kibana-check"), [("kibana-check", chosen)])
        self.assertEqual(self.kibana_calls(backend, "kibana-restore"), [("kibana-restore", chosen)])

    async def test_kibana_error_is_a_notify(self):
        from tpotctl import kibana_objects as ko
        cases = [("preview", "#kibana-update", ko.KibanaError("Kibana at http://127.0.0.1:64296 answered 503"),
                  ("Kibana at http://127.0.0.1:64296 answered 503.", "error")),
                 ("preview", "#kibana-overwrite", OSError("no space left"), ("No space left.", "error")),
                 ("export", "#kibana-export", ko.KibanaError(ko.BUSY), (ko.BUSY[:1].upper() + ko.BUSY[1:] + ".",
                                                                         "warning")),
                 ("check", "#kibana-restore", ko.KibanaError("/b/x.ndjson, line 3: not a JSON object"),
                  ("/b/x.ndjson, line 3: not a JSON object.", "error"))]
        for name, button, error, expected in cases:
            with self.subTest(name=name, button=button):
                backend = FakeBackend()
                backend._kibana_errors = {name: error}
                app = tapp.TpotApp(backend=backend, runner=Recorder())
                async with app.run_test(size=self.SIZE) as pilot:
                    await self.page(pilot, app)
                    await self.press(pilot, app, button)
                    self.assertNotIsInstance(app.screen, ConfirmDialog)
                    self.assertIn(expected, self.notes(app))
                    self.assertFalse(app.query_one(button).disabled)
                self.assertEqual([c for c in backend.calls if isinstance(c, tuple) and c[0] in
                                  ("kibana-update", "kibana-restore", "kibana-mode")], [])

    async def test_an_error_of_the_run_is_a_notify(self):
        from tpotctl import kibana_objects as ko
        backend = FakeBackend()
        backend._kibana_errors = {"update": ko.KibanaError("Kibana holds 1 more object of yours than when the "
                                                           "plan was shown (Mine too); nothing was changed, run "
                                                           "it again to see it")}
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            await self.press(pilot, app, "#kibana-overwrite")
            await pilot.click("#yes")
            await pilot.pause(0.4)
            self.assertNotIsInstance(app.screen, ConfirmDialog)
            self.assertIn(("Kibana holds 1 more object of yours than when the plan was shown (Mine too); nothing "
                           "was changed, run it again to see it.", "error"), self.notes(app))

    async def test_no_kibana_edition_disables_the_buttons(self):
        backend = FakeBackend()
        backend._kibana = cli.KibanaState("update", kibana=False)
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            for name in ("#kibana-mode", "#kibana-mode-save", "#kibana-export", "#kibana-update",
                         "#kibana-overwrite", "#kibana-restore"):
                self.assertTrue(app.query_one(name).disabled, name)
            self.assertIn("no Kibana in this edition", flat(app.query_one("#kibana-status")))
            self.assertEqual(app.query_one("#kibana-exports").row_count, 0)
            app.query_one("#elastic").kibana_pressed("kibana-update")       # a press that came in anyway
            await pilot.pause(0.2)
        self.assertNotIn("kibana-exports", backend.calls)
        self.assertNotIn("kibana-preview", backend.calls)

    async def test_buttons_disabled_while_busy(self):
        import threading
        release = threading.Event()

        class Slow(FakeBackend):
            def kibana_preview(self):
                plan = super().kibana_preview()
                release.wait(5)
                return plan

        backend = Slow()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        try:
            async with app.run_test(size=self.SIZE) as pilot:
                await self.page(pilot, app)
                await self.press(pilot, app, "#kibana-update", 0.3)
                controls = ("#kibana-mode", "#kibana-mode-save", "#kibana-export", "#kibana-update",
                            "#kibana-overwrite", "#kibana-restore")
                for name in controls:
                    self.assertTrue(app.query_one(name).disabled, name)
                self.assertFalse(app.query_one("#elastic-set").disabled)       # the retention is a part of its own
                pane = app.query_one("#elastic")
                pane.kibana_pressed("kibana-update")                            # a second press while it runs
                pane.kibana_pressed("kibana-overwrite")
                await pilot.pause(0.2)
                self.assertEqual(backend.calls.count("kibana-preview"), 1)
                release.set()
                await pilot.pause(0.4)
                self.assertIsInstance(app.screen, ConfirmDialog)
                await pilot.click("#no")
                await pilot.pause(0.3)
                for name in controls:
                    if name != "#kibana-mode-save":                     # the mode of .env is chosen
                        self.assertFalse(app.query_one(name).disabled, name)
        finally:
            release.set()
        self.assertEqual(backend.calls.count("kibana-preview"), 1)

    async def test_a_dialog_over_another_screen_is_not_pushed(self):
        import threading
        release = threading.Event()

        class Slow(FakeBackend):
            def kibana_check_export(self, path):
                count = super().kibana_check_export(path)
                release.wait(5)
                return count

            def kibana_preview(self):
                plan = super().kibana_preview()
                release.wait(5)
                return plan

        for button, label in (("#kibana-update", "Update"), ("#kibana-overwrite", "Overwrite all"),
                              ("#kibana-restore", "Restore")):
            with self.subTest(button=button):
                release.clear()
                backend = Slow()
                app = tapp.TpotApp(backend=backend, runner=Recorder())
                try:
                    async with app.run_test(size=self.SIZE) as pilot:
                        await self.page(pilot, app)
                        await self.press(pilot, app, button, 0.2)
                        other = ConfirmDialog("Something else?")
                        app.push_screen(other)
                        await pilot.pause(0.1)
                        release.set()
                        await pilot.pause(0.4)
                        self.assertIs(app.screen, other)
                        self.assertTrue(any(f"press {label} again" in message for message, _s in self.notes(app)))
                finally:
                    release.set()
                self.assertEqual([c for c in backend.calls if isinstance(c, tuple) and c[0] in
                                  ("kibana-update", "kibana-restore")], [])

    async def test_mode_select_saves_through_settings(self):
        from tpotctl.tests.test_cli_kibana import FakeSettings
        backend = FakeBackend()
        backend._settings = FakeSettings({"TPOT_KIBANA_OBJECTS": "update"})
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            select = app.query_one("#kibana-mode")
            self.assertEqual(select.value, "update")                 # the mode of .env
            self.assertTrue(app.query_one("#kibana-mode-save").disabled)
            select.value = "add"
            await pilot.pause(0.2)
            self.assertFalse(app.query_one("#kibana-mode-save").disabled)
            shown = flat(app.query_one("#kibana-mode-help"))
            self.assertIn(dict((v, h) for v, _l, h in tapp.kibana_modes())["add"], shown)
            self.assertIn("On the next start: 1 new Kibana object is imported, nothing is replaced.", shown)
            self.assertIn("Save writes TPOT_KIBANA_OBJECTS=add into .env, T-Pot uses it from its next start.", shown)
            self.assertEqual(backend._settings.changes, [])          # choosing alone writes nothing
            backend._kibana = cli.KibanaState("add", plan=backend.kibana_plan())
            await self.press(pilot, app, "#kibana-mode-save")
            self.assertEqual(backend._settings.changes, [{"TPOT_KIBANA_OBJECTS": "add"}])
            self.assertIn(("TPOT_KIBANA_OBJECTS=add is saved in .env. T-Pot uses it from its next start.",
                           "information"), self.notes(app))
            self.assertIn("TPOT_KIBANA_OBJECTS add", flat(app.query_one("#kibana-status")))
            self.assertTrue(app.query_one("#kibana-mode-save").disabled)
        self.assertEqual(self.kibana_calls(backend, "kibana-update"), [])

    async def test_overwrite_mode_asks_first(self):
        from tpotctl.tests.test_cli_kibana import FakeSettings
        backend = FakeBackend()
        backend._settings = FakeSettings({"TPOT_KIBANA_OBJECTS": "update"})
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            select = app.query_one("#kibana-mode")
            select.value = "overwrite"
            await pilot.pause(0.2)
            await self.press(pilot, app, "#kibana-mode-save")
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIs(app.focused, app.screen.query_one("#no"))
            body = self.body(app)
            self.assertIn("On every start T-Pot brings each of its Kibana objects back to the shipped version: your "
                          "changes to T-Pot objects are reset each time. An export comes first each time", body)
            await pilot.click("#no")
            await pilot.pause(0.3)
            self.assertEqual(backend._settings.changes, [])
            self.assertEqual(select.value, "update")                 # back to the mode of .env
            select.value = "overwrite"
            await pilot.pause(0.2)
            await self.press(pilot, app, "#kibana-mode-save")
            await pilot.click("#yes")
            await pilot.pause(0.4)
        self.assertEqual(backend._settings.changes, [{"TPOT_KIBANA_OBJECTS": "overwrite"}])

    def test_every_mode_names_the_first_import_when_kibana_holds_none_of_the_objects(self):
        from tpotctl import kibana_objects as ko
        empty = ko.Plan([ko.Item("dashboard:a", "dashboard", "a", "Attacks", "new"),
                         ko.Item("dashboard:b", "dashboard", "b", "Brand new", "new")])
        for mode in ko.MODES:
            with self.subTest(mode=mode):
                self.assertEqual(tapp.mode_effect(mode, empty), "Kibana holds none of T-Pot's objects: the next "
                                                                "start imports them, without your advanced settings.")
        for mode in ko.MODES:                   # it gave up: nothing comes on its own any more
            with self.subTest(mode=mode, given_up=True):
                self.assertEqual(tapp.mode_effect(mode, empty, ["dashboard:a"]),
                                 "On the next start: nothing is imported, the first import gave up on 1 object; "
                                 "tpot elastic import retries them.")
        some = ko.Plan(empty.items[:1] + [ko.Item("dashboard:c", "dashboard", "c", "Cowrie", "current")])
        self.assertEqual(tapp.mode_effect("add", some, ["dashboard:a"]),
                         "On the next start: 1 new Kibana object is imported, nothing is replaced.")
        self.assertEqual(tapp.mode_effect("keep", some), "On the next start: nothing is imported.")
        self.assertEqual(tapp.mode_effect("add", some),
                         "On the next start: 1 new Kibana object is imported, nothing is replaced.")

    async def test_a_result_with_errors_lists_them(self):
        backend = FakeBackend()
        backend._kibana_refused = [("dashboard", "a", "Attacks", "missing_references")]
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            await self.press(pilot, app, "#kibana-update")
            await pilot.click("#yes")
            await pilot.pause(0.4)
            self.assertIsInstance(app.screen, ConfirmDialog)
            result = self.body(app)
            self.assertIn(f"{tapp.glyphs.g('warn')} 1 new, 0 updated, 1 the same, 1 of yours kept, 1 failed "
                          "(export: /b/kibana_objects_20261016120000.ndjson).", result)
            self.assertIn('not imported: dashboard:a "Attacks" (missing_references)', result)

    async def test_the_note_instead_of_the_result_names_what_was_refused(self):
        import threading
        release = threading.Event()

        class Slow(FakeBackend):
            def kibana_update(self, overwrite, confirmed=None):
                answer = super().kibana_update(overwrite, confirmed)
                release.wait(5)
                return answer

        backend = Slow()
        backend._kibana_refused = [("dashboard", f"x{n}", f"X {n}", "unknown") for n in range(7)]
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        try:
            async with app.run_test(size=self.SIZE) as pilot:
                await self.page(pilot, app)
                await self.press(pilot, app, "#kibana-update")
                await pilot.click("#yes")
                await pilot.pause(0.2)
                other = ConfirmDialog("Something else?")
                app.push_screen(other)
                await pilot.pause(0.1)
                release.set()
                await pilot.pause(0.4)
                self.assertIs(app.screen, other)
                note, severity = self.notes(app)[-1]
                self.assertEqual(severity, "warning")
                lines = note.split("\n")
                self.assertTrue(lines[0].startswith("1 new, 1 updated, 1 the same, 1 of yours kept, 7 failed"), note)
                self.assertEqual(lines[1:], [f'not imported: dashboard:x{n} "X {n}" (unknown)' for n in range(5)]
                                 + ["… and 2 more"])
        finally:
            release.set()

    async def test_two_saves_of_env_at_once_keep_both(self):
        """Set of the retention and Save of the mode load .env and write what they loaded: one at a time."""
        import threading
        import time as ttime

        class Env:
            def __init__(self):
                self.values, self.lock = {"TPOT_ES_RETENTION": "90", "TPOT_KIBANA_OBJECTS": "update"}, threading.Lock()

            def load(self, *args, **kwargs):
                env, snapshot = self, dict(self.values)

                class Loaded:
                    values = snapshot

                    def change(self, changes, unlocked=()):
                        ttime.sleep(0.3)                    # another write in between would be lost
                        with env.lock:
                            env.values = {**snapshot, **changes}
                        return []
                return Loaded()

        env = Env()

        class Real(FakeBackend):
            # what Backend.set_retention / set_kibana_mode do: load .env, change one key
            def set_retention(self, value):
                self.calls.append(("set", value))
                env.load().change({"TPOT_ES_RETENTION": value})

            def set_kibana_mode(self, value):
                self.calls.append(("kibana-mode", value))
                env.load().change({"TPOT_KIBANA_OBJECTS": value})

        backend = Real()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=self.SIZE) as pilot:
            await self.page(pilot, app)
            app.query_one("#kibana-mode").value = "add"
            await pilot.pause(0.2)
            app.query_one("#elastic-value").value = "365"              # longer: no question
            app.query_one("#kibana-mode-save").press()
            app.query_one("#elastic-set").press()
            await pilot.pause(1.2)
        self.assertEqual(env.values, {"TPOT_ES_RETENTION": "365", "TPOT_KIBANA_OBJECTS": "add"})

    async def test_kibana_rows_fit_80_columns(self):
        backend = FakeBackend()
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(80, 24)) as pilot:
            await self.page(pilot, app)
            app.query_one("#kibana-mode").value = "add"             # Save can take the focus
            await pilot.pause(0.2)
            for name in ("#kibana-mode", "#kibana-mode-save", "#kibana-export", "#kibana-update",
                         "#kibana-overwrite", "#kibana-exports", "#kibana-restore"):
                with self.subTest(widget=name):
                    widget = app.query_one(name)
                    widget.focus()
                    await pilot.pause(0.2)
                    self.assertGreater(widget.region.width, 0, name)            # scrolled into view
                    self.assertLessEqual(widget.region.right, 80, name)
                    self.assertGreaterEqual(widget.region.y, 4, name)          # below the header
            table = app.query_one("#kibana-exports")
            self.assertGreaterEqual(table.region.width, sum(c.get_render_width(table) for c in table.columns.values()))


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
