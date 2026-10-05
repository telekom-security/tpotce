"""The task screen: scripts and tpot commands run from the menu, with their phases."""

import os
import unittest

from tpotctl.tests import isolate

isolate()

try:
    import textual  # noqa: F401
except ImportError:
    textual = None


class FakeEngine:
    """Prints marks like a T-Pot script; remembers what it was started with."""

    seen = []
    code = 0
    lines = ["@@tpot phase check Checking the source\n", "ok\n", "@@tpot warn pull Not all images\n",
             "@@tpot phase done Done\n"]

    def __init__(self, command, env=None, cwd=None):
        FakeEngine.seen.append({"command": list(command), "env": env, "cwd": cwd,
                                "files": [os.path.exists(part) for part in command]})

    def run(self, line):
        for text in FakeEngine.lines:
            line(text)
        return FakeEngine.code


def host(screen):
    from textual.app import App
    from tpotctl import theme

    class Host(App):
        CSS_PATH = "../tpot.tcss"

        def __init__(self):
            super().__init__()
            theme.apply(self)

        def get_theme_variable_defaults(self):
            return theme.variable_defaults()

        def on_mount(self):
            self.push_screen(screen, self.exit)

    return Host()


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class TaskScreenTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        FakeEngine.seen.clear()
        FakeEngine.code = 0

    def screen(self, sudo="passwordless", **task):
        from tpotctl.screens.task import Task, TaskScreen
        task = dict({"title": "Update T-Pot", "command": ["/x/update.sh", "-y"], "become": "-B"}, **task)
        return TaskScreen(Task(**task), engine=FakeEngine, sudo_mode=sudo,
                          password_ok=lambda password: password == "right")

    async def test_runs_and_shows_the_phases(self):
        app = host(self.screen())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#task-run")
            await pilot.pause(0.4)
            phases = str(app.screen.query_one("#task-phases").render())
            self.assertIn("Checking the source", phases)
            self.assertIn("Done", phases)
            result = str(app.screen.query_one("#task-result").render())
            self.assertIn("Not all images", result)
            await pilot.click("#task-back")
            await pilot.pause(0.2)
        self.assertEqual(app.return_value, 0)
        seen = FakeEngine.seen[-1]
        self.assertEqual(seen["command"], ["/x/update.sh", "-y"])       # no -B without a sudo password
        self.assertEqual(seen["env"]["TPOT_MARKS"], "1")
        self.assertEqual(seen["env"]["TPOT_GUM"], "off")

    async def test_password_goes_in_a_file_that_is_gone_after_a_failure(self):
        FakeEngine.code = 3
        app = host(self.screen(sudo="password"))
        async with app.run_test(size=(120, 40)) as pilot:
            self.assertTrue(app.screen.query_one("#task-sudo").display)
            app.screen.query_one("#task-sudo").value = "wrong"
            await pilot.click("#task-run")
            await pilot.pause(0.2)
            self.assertEqual(FakeEngine.seen, [])
            self.assertIn("does not accept", str(app.screen.query_one("#task-hint").render()))
            app.screen.query_one("#task-sudo").value = "right"
            await pilot.click("#task-run")
            await pilot.pause(0.4)
            self.assertIn("exit code 3", str(app.screen.query_one("#task-result").render()))
        seen = FakeEngine.seen[-1]
        command = seen["command"]
        self.assertEqual(command[-2], "-B")
        self.assertTrue(seen["files"][-1])                  # the file was there while it ran
        self.assertFalse(os.path.exists(command[-1]))       # and is gone now
        self.assertNotIn("right", " ".join(command))

    async def test_become_file_option_of_tpot_commands(self):
        app = host(self.screen(sudo="password", command=["/x/tpot", "edition", "set", "mini", "-y"],
                               become="--become-file"))
        async with app.run_test(size=(120, 40)) as pilot:
            app.screen.query_one("#task-sudo").value = "right"
            await pilot.click("#task-run")
            await pilot.pause(0.4)
        self.assertEqual(FakeEngine.seen[-1]["command"][-2], "--become-file")

    async def test_secrets_go_in_files_too(self):
        app = host(self.screen(command=["/x/tpot", "edition", "set", "standard", "-y"], become="",
                               secrets={"--password-file": "web-secret"}, autostart=True))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
        seen = FakeEngine.seen[-1]
        self.assertEqual(seen["command"][-2], "--password-file")
        self.assertTrue(seen["files"][-1])
        self.assertFalse(os.path.exists(seen["command"][-1]))
        self.assertNotIn("web-secret", " ".join(seen["command"]))

    async def test_autostart_without_a_password(self):
        app = host(self.screen(autostart=True))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            self.assertEqual(len(FakeEngine.seen), 1)
            self.assertFalse(app.screen.query_one("#task-run").display)

    async def test_escape_does_not_leave_a_running_task(self):
        from tpotctl.screens.task import TaskScreen
        screen = self.screen()
        app = host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            screen.busy = True
            await pilot.press("escape")
            await pilot.pause(0.1)
            self.assertIsInstance(app.screen, TaskScreen)
            screen.busy = False
            await pilot.press("escape")
            await pilot.pause(0.2)
        self.assertIsNone(app.return_value)

    async def test_restart_tpot_after_an_update(self):
        app = host(self.screen(restart_tpot=True))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#task-run")
            await pilot.pause(0.4)
            self.assertTrue(app.screen.query_one("#task-restart").display)
            await pilot.click("#task-restart")
            await pilot.pause(0.2)
        self.assertEqual(app.return_value, "restart")


if __name__ == "__main__":
    unittest.main()
