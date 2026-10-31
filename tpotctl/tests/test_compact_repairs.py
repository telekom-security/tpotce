"""Small terminal layouts with an independent, synthetic configuration."""

from pathlib import Path
import tempfile
import unittest

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Footer, ListView

from tpotctl import app as tapp, envschema, ops, settings, theme
from tpotctl.envfile import EnvFile
from tpotctl.tests.test_app import FakeBackend


class CompactApp(App):
    CSS_PATH = str(Path(tapp.__file__).with_name("tpot.tcss"))
    HORIZONTAL_BREAKPOINTS = tapp.TpotApp.HORIZONTAL_BREAKPOINTS
    VERTICAL_BREAKPOINTS = tapp.TpotApp.VERTICAL_BREAKPOINTS

    def __init__(self, pane, current):
        super().__init__()
        theme.apply(self)
        self.backend = FakeBackend()
        self.backend.settings = lambda: current
        self.backend.config_stamp = lambda: 0
        self.pane = pane

    def get_theme_variable_defaults(self):
        return theme.variable_defaults()

    def compose(self) -> ComposeResult:
        yield Vertical(id="header")
        with Horizontal(id="body"):
            yield Vertical(id="sidebar")
            yield self.pane(id="status" if self.pane is tapp.StatusPane else "settings", classes="pane")
        yield Footer()

    def update_header(self, *args):
        pass

    def start_problems(self):
        return []


class CompactRepairTest(unittest.IsolatedAsyncioTestCase):
    def current(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "a-very-long-independent-test-settings.conf"
        path.write_text("EXAMPLE=value\nTPOT_TYPE=SENSOR\n")
        return settings.Settings(str(path), EnvFile(str(path)),
                                 {"EXAMPLE": envschema.Rule(key="EXAMPLE", type="text",
                                                           title="Example", section="base")},
                                 set())

    async def test_status_actions_are_visible_at_80_by_24(self):
        app = CompactApp(tapp.StatusPane, self.current())
        app.backend.containers = lambda: [
            ops.Container(f"honeypot{number}", "running", "healthy", "healthy", "", "")
            for number in range(38)]
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            for selector in ("#svc-start", "#svc-stop", "#svc-restart", "#system"):
                region = app.query_one(selector).region
                self.assertLessEqual(region.bottom, 23, selector)
                self.assertGreater(region.height, 0, selector)
            self.assertEqual(len(app.query_one("#comb").containers), 38)
            self.assertEqual(app.query_one("#system").region.height, 1)

    async def test_settings_head_has_two_rows_and_first_field_is_visible(self):
        app = CompactApp(tapp.SettingsPane, self.current())
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause(.3)
            self.assertLessEqual(app.query_one("#settings-head").region.height, 2)
            self.assertLess(app.query_one(".setting").region.y, 23)
            for selector in ("#settings-save", "#settings-revert", "#settings-all"):
                region = app.query_one(selector).region
                self.assertLessEqual(region.right, 60, selector)
                self.assertLessEqual(region.bottom, 23, selector)

    async def test_grouped_sidebar_keeps_ten_selectable_pages_at_80_by_24(self):
        backend = FakeBackend()
        current = self.current()
        backend.settings = lambda: current
        backend.config_stamp = lambda: 0
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        app.panes = [(key, title, cls if key in ("status", "settings") else Vertical, needs)
                     for key, title, cls, needs in app.panes]
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            self.assertEqual(len(app.query_one("#sidebar", ListView).children), 10)
            self.assertEqual(len(app.query(".menu-group")), 4)
            await pilot.press("down")
            self.assertEqual(app.query_one(ContentSwitcher).current, "checks")
            for _ in range(8):
                await pilot.press("down")
            self.assertEqual(app.query_one(ContentSwitcher).current, "update")
            self.assertLessEqual(app.query_one("#menu-update").region.bottom, 23)

    async def test_unchanged_customizer_leaves_without_confirmation(self):
        from tpotctl.screens.customizer import core
        from tpotctl.screens.dialogs import ConfirmDialog
        app = tapp.CustomizerApp(core.Catalog(), core.Selection("STANDARD"), core.DEFAULT_MAX_NETWORKS)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await pilot.press("escape")
            await pilot.pause(.1)
            self.assertFalse(any(isinstance(screen, ConfirmDialog) for screen in app.screen_stack))
            self.assertIsNone(app.return_value)

    async def test_changed_customizer_still_confirms_leaving(self):
        from tpotctl.screens.customizer import core
        from tpotctl.screens.dialogs import ConfirmDialog
        app = tapp.CustomizerApp(core.Catalog(), core.Selection("STANDARD"), core.DEFAULT_MAX_NETWORKS)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            app.screen.state.toggle("wordpot", False)
            await pilot.press("escape")
            await pilot.pause(.1)
            self.assertIsInstance(app.screen, ConfirmDialog)

    async def test_failed_child_is_not_shown_as_success_without_fail_mark(self):
        from tpotctl.screens.task import Task, TaskScreen
        task = Task("Update T-Pot", ["bash", "-c",
                                    "echo '@@tpot phase backup Writing backup'; "
                                    "echo 'backup failed'; exit 1"],
                    cwd="/tmp", autostart=True)
        screen = TaskScreen(task, sudo_mode="passwordless")

        class TaskApp(App):
            CSS_PATH = str(Path(tapp.__file__).with_name("tpot.tcss"))

            def __init__(self):
                super().__init__()
                theme.apply(self)

            def get_theme_variable_defaults(self):
                return theme.variable_defaults()

            def on_mount(self):
                self.push_screen(screen)

        app = TaskApp()
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(30):
                await pilot.pause(.05)
                if screen.code is not None:
                    break
            self.assertEqual(screen.code, 1)
            self.assertIn("exit code 1", str(screen.query_one("#task-result").render()))
            self.assertIn("Writing backup", str(screen.query_one("#task-phases").render()))
