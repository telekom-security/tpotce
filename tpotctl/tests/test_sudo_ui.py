"""Sudo controls ask before running the shared CLI in the task screen."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

from tpotctl.tests import current_configuration, isolate

isolate()
textual = importlib.util.find_spec("textual")


@unittest.skipUnless(textual, "run with the T-Pot Manager venv")
class SudoUiTest(unittest.IsolatedAsyncioTestCase):
    def test_debug_representations_do_not_disclose_collected_passwords(self):
        from rich.pretty import pretty_repr
        from tpotctl.screens.sudo import SudoChoice
        from tpotctl.screens.task import Task
        choice = SudoChoice("secure", "private-existing-password", "private-new-password")
        task = Task("Sudo mode", ["tpot", "sudo", "secure"],
                    secrets={"--become-file": "private-existing-password",
                             "--user-password-file": "private-new-password"})
        for text in (repr(choice), pretty_repr(choice), repr(task), pretty_repr(task)):
            self.assertNotIn("private-existing-password", text)
            self.assertNotIn("private-new-password", text)

    def setUp(self):
        from tpotctl import app, sensors, settings
        from tpotctl.tests.test_app import FakeBackend
        self.repo = current_configuration(self)
        self.commands, self.secrets, self.secret_paths = [], [], []
        repo = self.repo

        class Backend(FakeBackend):
            account = True
            sudo = "passwordless"

            def settings(self):
                return settings.load(repo)

            def sudo_mode(self):
                return self.sudo

            def sudo_account(self, sensor=None):
                return self.account

            def sensors(self):
                registry = sensors.Registry(repo)
                registry.records["edge"] = sensors.Sensor(
                    name="edge", host="edge.test", ssh_user="operator", ssh_port=2222)
                return registry

        outer = self

        class Engine:
            def __init__(self, command, **kwargs):
                outer.commands.append(command)
                values = {}
                for option in ("--become-file", "--user-password-file"):
                    if option in command:
                        path = Path(command[command.index(option) + 1])
                        outer.secret_paths.append(path)
                        values[option] = path.read_text().rstrip("\n")
                        outer.assertEqual(path.stat().st_mode & 0o777, 0o600)
                outer.secrets.append(values)

            def run(self, line):
                outer.backend.sudo = "password"
                line("Sudo password required\n")
                return 0

        self.backend = Backend()
        self.app = app.TpotApp(backend=self.backend, engine=Engine)
        patch = mock.patch.dict("os.environ", {"XDG_RUNTIME_DIR": self.repo})
        patch.start()
        self.addCleanup(patch.stop)

    async def test_host_block_is_only_on_settings_and_locked_off_linux(self):
        from textual.widgets import Button
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertTrue(self.app.query("#settings-sudo-change"), "Settings needs the Host control.")
            self.assertFalse(self.app.query("#llm #settings-sudo-change"))
            self.app.goto("settings")
            await pilot.pause()
            self.assertFalse(self.app.query_one("#settings-sudo-change", Button).disabled)
            self.backend.host = False
            self.app.query_one("#settings").refresh_sudo()
            await pilot.pause()
            self.assertTrue(self.app.query_one("#settings-sudo-change", Button).disabled)
        self.assertFalse(self.commands)

    async def test_secure_collects_secrets_before_start_and_cancel_does_nothing(self):
        from textual.widgets import Button, Input, Select
        self.backend.account = False
        self.backend.sudo = "password"
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.app.goto("settings")
            await pilot.pause()
            self.assertTrue(self.app.query("#settings-sudo-change"))
            await pilot.click("#settings-sudo-change")
            await pilot.pause()
            self.assertEqual(self.app.focused.id, "sudo-back")
            await pilot.press("escape")
            self.assertFalse(self.commands)
            await pilot.click("#settings-sudo-change")
            await pilot.pause()
            self.app.screen.query_one("#sudo-action", Select).value = "secure"
            await pilot.pause()
            self.assertTrue(self.app.screen.query_one("#sudo-run", Button).disabled)
            self.app.screen.query_one("#sudo-password", Input).value = "  old-password!  "
            self.app.screen.query_one("#sudo-user-password", Input).value = "Distant-Otters!swim-47"
            self.app.screen.query_one("#sudo-user-repeat", Input).value = "mismatch"
            await pilot.pause()
            self.assertTrue(self.app.screen.query_one("#sudo-run", Button).disabled)
            self.app.screen.query_one("#sudo-user-repeat", Input).value = "Distant-Otters!swim-47"
            await pilot.pause()
            self.assertFalse(self.app.screen.query_one("#sudo-run", Button).disabled)
            self.app.screen.query_one("#sudo-run", Button).press()
            await pilot.pause(0.5)
            self.assertEqual(len(self.commands), 1)
            self.assertEqual(self.commands[0][1:4], ["sudo", "secure", "-y"])
            self.assertEqual(self.secrets, [{"--become-file": "  old-password!  ",
                                            "--user-password-file": "Distant-Otters!swim-47"}])
            self.assertTrue(all(not path.exists() for path in self.secret_paths))
            await pilot.click("#task-back")
            await pilot.pause()
            text = str(self.app.query_one("#settings-sudo-state").render()).lower()
            self.assertIn("unknown", text)

    async def test_sensor_action_is_not_a_column_or_a_drift_gated_edit(self):
        from textual.widgets import Button, Checkbox, Select
        from tpotctl import ops, sensors
        async with self.app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            self.app.goto("sensors")
            await pilot.pause(0.2)
            self.assertTrue(self.app.query("#sensor-sudo"), "A sensor needs a Sudo mode action.")
            table = self.app.query_one("#sensors-table")
            self.assertEqual(len(table.columns), len(sensors.COLUMNS))
            with mock.patch.object(ops, "require_config_current", side_effect=AssertionError("sudo allows drift")):
                self.app.query_one("#sensor-sudo", Button).press()
                await pilot.pause()
                self.app.screen.query_one("#sudo-action", Select).value = "convenient"
                self.app.screen.query_one("#sudo-nopass", Checkbox).value = True
                await pilot.pause()
                self.app.screen.query_one("#sudo-run", Button).press()
                await pilot.pause(0.5)
            self.assertEqual(self.commands[0][1:], ["sensors", "sudo", "edge", "convenient", "-y"])

    async def test_secure_dialog_arrows_reach_every_field_and_button_at_80x24(self):
        from textual.widgets import Input, Select
        from tpotctl.screens.sudo import SudoDialog
        from tpotctl.tests.test_nav import check, edges
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.app.push_screen(SudoDialog("edge", lambda: False, remote=True))
            await pilot.pause()
            self.app.screen.query_one("#sudo-action", Select).value = "secure"
            for name in ("sudo-password", "sudo-user-password", "sudo-user-repeat"):
                self.app.screen.query_one("#" + name, Input).value = "Distant-Otters!swim-47"
            await pilot.pause()
            start = self.app.focused
            graph = await edges(pilot, self.app)
            check(self, graph, start, "sudo secure at 80x24")
            card = self.app.screen.query_one(".dialog")
            self.assertGreaterEqual(card.region.y, 0)
            self.assertLessEqual(card.region.bottom, 24)
            self.assertLessEqual(card.region.width, 76)

    async def test_unknown_account_disables_secure_and_shows_the_reason(self):
        from textual.widgets import Button, Select
        from tpotctl.screens.sudo import SudoDialog

        def unavailable():
            raise OSError("SSH account state could not be verified.")

        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.app.push_screen(SudoDialog("edge", unavailable, passwordless=True))
            await pilot.pause()
            self.app.screen.query_one("#sudo-action", Select).value = "secure"
            await pilot.pause()
            self.assertTrue(self.app.screen.query_one("#sudo-run", Button).disabled)
            self.assertIn("SSH account state", str(self.app.screen.query_one("#sudo-account").render()))
            self.assertFalse(self.commands)

    async def test_sensor_sudo_is_disabled_when_no_target_is_available(self):
        from textual.widgets import Button
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            pane = self.app.query_one("#sensors")
            pane.show(None, None, "Cannot read the sensor registry.")
            self.assertTrue(pane.query_one("#sensor-sudo", Button).disabled)
            pane.show(self.backend.sensors(), self.backend.sensor_status(), "")
            self.assertFalse(pane.query_one("#sensor-sudo", Button).disabled)
            self.assertFalse(self.commands)
