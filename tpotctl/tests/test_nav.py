"""The arrow keys: every focusable widget of a page or dialog is reachable with them alone,
none of them is a dead end, and left at the start of a line goes back to the menu."""

import unittest

from tpotctl.tests import isolate

isolate()

try:
    import textual  # noqa: F401
except ImportError:
    textual = None

KEYS = ("up", "down", "left", "right")


def tries_for(widget, key):
    """How often a key may be pressed before the focus has to move: lists, tables and text fields
    first run to their edge."""
    from textual.containers import ScrollableContainer
    from textual.scroll_view import ScrollView
    from textual.widgets import DataTable, Input, ListView, OptionList
    if isinstance(widget, Input):            # a ScrollView too, but its arrows move the cursor
        return len(widget.value) + 2 if key in ("left", "right") else 1
    if isinstance(widget, (ScrollableContainer, ScrollView)) and not isinstance(widget, (DataTable, OptionList,
                                                                                         ListView)):
        return int(widget.max_scroll_y if key in ("up", "down") else widget.max_scroll_x) + 2
    if isinstance(widget, DataTable) and key in ("up", "down"):
        return widget.row_count + 2
    if isinstance(widget, OptionList) and key in ("up", "down"):
        return widget.option_count + 2
    if isinstance(widget, ListView) and key in ("up", "down"):
        return len(widget) + 2
    return 1


async def edges(pilot, app, root=None, keys=KEYS):
    """{widget: {key: widget the key moves the focus to}} for the focusable widgets of the screen (or
    under root). left / right are not pressed on a tab bar, they switch the tab there."""
    from textual.widgets import Tabs
    widgets = [w for w in app.screen.focus_chain if root is None or root in w.ancestors]
    graph = {}
    for widget in widgets:
        graph[widget] = {}
        for key in keys:
            if key in ("left", "right") and isinstance(widget, Tabs):
                graph[widget][key] = widget
                continue
            widget.focus()
            await pilot.pause(0.01)
            for _ in range(tries_for(widget, key)):
                await pilot.press(key)
                if app.focused is not widget:
                    break
            graph[widget][key] = app.focused
    return graph


def reachable(graph, start):
    seen, todo = {start}, [start]
    while todo:
        for target in graph.get(todo.pop(), {}).values():
            if target is not None and target not in seen:
                seen.add(target)
                todo.append(target)
    return seen


def name(widget):
    return f"{type(widget).__name__}#{widget.id}" if widget is not None else "None"


def check(test, graph, start, label):
    page = list(graph)
    missing = [name(w) for w in page if w not in reachable(graph, start)]
    test.assertEqual(missing, [], f"{label}: not reachable with the arrows from {name(start)}")
    if len(page) > 1:
        stuck = [name(w) for w in page if all(graph[w][k] is w for k in KEYS)]
        test.assertEqual(stuck, [], f"{label}: no arrow moves away")


if textual:
    from tpotctl import app as tapp
    from tpotctl.tests.test_app import FakeBackend, FakeEngine, Recorder


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class PagesTest(unittest.IsolatedAsyncioTestCase):

    async def check_page(self, key, host=True):
        app = tapp.TpotApp(backend=FakeBackend(host=host), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app.goto(key)
            await pilot.pause(0.4)
            app.query_one("#sidebar").focus()
            await pilot.press("right")              # from the menu into the page
            await pilot.pause(0.2)
            pane = app.query_one(f"#{key}")
            graph = await edges(pilot, app, root=pane)
            if graph:
                start = app.focused if app.focused in graph else next(iter(graph))
                check(self, graph, start, key)

    async def test_every_page(self):
        for key, *_rest in tapp.PANES:
            with self.subTest(page=key):
                await self.check_page(key)

    async def test_every_page_off_host(self):
        # the pages that look different there: the locked ones and the edition page
        for key, _title, _cls, needs in tapp.PANES:
            if "host" in needs or key == "edition":
                with self.subTest(page=key):
                    await self.check_page(key, host=False)

    async def test_left_at_the_start_of_a_line_goes_to_the_menu(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app.goto("update")
            await pilot.pause(0.3)
            app.query_one("#run-update").focus()
            await pilot.press("left")
            await pilot.pause(0.1)
            self.assertIs(app.focused, app.query_one("#sidebar"))
            await pilot.press("right")
            await pilot.pause(0.1)
            self.assertIsNot(app.focused, app.query_one("#sidebar"))

    async def test_left_and_right_move_along_a_row_of_buttons(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app.goto("status")
            await pilot.pause(0.3)
            app.query_one("#svc-start").focus()
            await pilot.press("right")
            await pilot.pause(0.1)
            self.assertEqual(app.focused.id, "svc-stop")
            await pilot.press("left")
            await pilot.pause(0.1)
            self.assertEqual(app.focused.id, "svc-start")

    async def test_down_leaves_a_table_at_its_last_row(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app.goto("status")
            await pilot.pause(0.3)
            table = app.query_one("#containers")
            table.focus()
            await pilot.press("down")               # two containers: first the cursor moves
            await pilot.pause(0.1)
            self.assertIs(app.focused, table)
            await pilot.press("down")
            await pilot.pause(0.1)
            self.assertEqual(app.focused.id, "svc-start")
            await pilot.press("up")
            await pilot.pause(0.1)
            self.assertIs(app.focused, table)

    async def test_left_in_an_input_never_goes_to_the_menu(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app.goto("checks")
            await pilot.pause(0.3)
            field = app.query_one("#check-host")
            field.focus()
            await pilot.pause(0.1)
            field.value = "192.0.2.5"
            field.cursor_position = 0
            await pilot.press("left")
            await pilot.pause(0.1)
            self.assertIs(app.focused, field)
            field.cursor_position = len(field.value)
            await pilot.press("right")
            await pilot.pause(0.1)
            self.assertEqual(app.focused.id, "check-honeypots")

    async def test_the_menu_keeps_its_keys(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            sidebar = app.query_one("#sidebar")
            sidebar.focus()
            await pilot.press("down")
            await pilot.pause(0.2)
            self.assertEqual(app.query_one("#panes").current, tapp.PANES[1][0])
            await pilot.press("left")
            await pilot.pause(0.1)
            self.assertIs(app.focused, sidebar)


try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None

if textual:
    from tpotctl.tests.test_pickers import SettingsHelpersBase
else:
    SettingsHelpersBase = unittest.IsolatedAsyncioTestCase


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class SettingsNavTest(SettingsHelpersBase):
    """The Settings and the LLM page on an LLM checkout."""

    async def test_llm_page_every_control_reachable(self):
        from textual.widgets import Tabs
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_llm(pilot)
            for tab in ("tab-beelzebub", "tab-galah"):
                pane.query_one("#llm-tabs").active = tab
                await pilot.pause(0.3)
                graph = await edges(pilot, self.app, root=pane)
                check(self, graph, pane.query_one("#llm-tabs").query_one(Tabs), tab)
                for button in ("#llm-find", "#llm-test"):
                    widget = pane.query_one(button)
                    self.assertTrue(any(graph[widget][k] is not widget for k in KEYS), button)

    async def test_down_from_the_tools_reaches_the_rows_and_up_comes_back(self):
        from tpotctl.widgets.fields import SettingRow
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_llm(pilot)
            pane.query_one("#llm-test").focus()
            for _ in range(2):                      # the tab bar, then the first setting
                await pilot.press("down")
            await pilot.pause(0.1)
            self.assertTrue(any(isinstance(a, SettingRow) for a in self.app.focused.ancestors),
                            name(self.app.focused))
            for _ in range(2):
                await pilot.press("up")
            await pilot.pause(0.1)
            self.assertIn(self.app.focused.id or "", ("llm-find", "llm-scan", "llm-test", "llm-add"))

    async def test_right_from_a_field_to_its_button(self):
        from textual.widgets import Button
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_llm(pilot)
            pane.query_one("#llm-tabs").active = "tab-galah"
            await pilot.pause(0.3)
            field = pane.query_one("#set-GALAH_LLM_SERVER_URL")
            field.focus()
            await pilot.pause(0.1)                  # the focus selects all of it, the first arrow ends that
            field.cursor_position = len(field.value)
            await pilot.press("right")
            await pilot.pause(0.1)
            self.assertIsInstance(self.app.focused, Button)
            await pilot.press("left")
            await pilot.pause(0.1)
            self.assertIs(self.app.focused, field)

    async def test_settings_page_save_and_revert_reachable(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            settings_pane = await self.open_settings(pilot)
            settings_pane.query_one("#set-TPOT_ATTACKMAP_TEXT_TIMEZONE").value = "Europe/Berlin"
            await pilot.pause(0.3)                  # a change: Revert and Save are on
            graph = await edges(pilot, self.app, root=settings_pane)
            start = settings_pane.query_one("#set-TPOT_ATTACKMAP_TEXT_TIMEZONE")
            check(self, graph, start, "settings")
            self.assertIn(settings_pane.query_one("#settings-save"), graph)


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class ScreensTest(unittest.IsolatedAsyncioTestCase):
    """Every dialog and screen of tpotctl/screens, on top of the menu as in tpot."""

    @staticmethod
    def factories():
        from rich.text import Text
        from tpotctl import sensors
        from tpotctl.screens import customizer, dialogs, pickers, restore, task, uninstall
        from tpotctl.screens.customizer import State, core
        times = [("UTC", Text("UTC"), False), ("Europe/Berlin", Text("Europe/Berlin"), False)]

        def state():
            return State(core.Catalog(), core.Selection("MINI"), core.DEFAULT_MAX_NETWORKS)
        return {
            "confirm": lambda: dialogs.ConfirmDialog("Really?", body="It stops T-Pot."),
            "choice": lambda: dialogs.ChoiceDialog("Pick one", ["one", "two", "three"]),
            "port": lambda: dialogs.PortInputDialog("New host port"),
            "user": lambda: dialogs.UserDialog("Add a web user"),
            "sensor": lambda: dialogs.SensorDialog(),
            "sensor-edit": lambda: dialogs.SensorEditDialog(sensors.Sensor("s1", host="192.0.2.9", ssh_user="t")),
            "picker": lambda: pickers.Picker("Choose a time zone", lambda: times, current="UTC",
                                             detect=lambda: "UTC"),
            "task": lambda: task.TaskScreen(task.Task("Update T-Pot", ["/x/update.sh", "-y"], become="-B"),
                                            engine=FakeEngine, sudo_mode="password",
                                            password_ok=lambda password: True),
            # one archive: moving to another one rebuilds the checkboxes of its groups
            "restore": lambda: restore.RestoreScreen(lambda: FakeBackend().backup_infos()[:1]),
            "uninstall": lambda: uninstall.UninstallScreen(sudo_mode="password", password_ok=lambda pw: True,
                                                           hostname="honey"),
            "customizer": lambda: customizer.CustomizerScreen(core.Catalog(), core.Selection("MINI"),
                                                              core.DEFAULT_MAX_NETWORKS),
            "ports": lambda: customizer.PortsDialog(state(), "cowrie"),
        }

    async def check_screen(self, label, make):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(make())
            await pilot.pause(0.5)
            start = app.focused
            graph = await edges(pilot, app)
            if graph:
                check(self, graph, start if start in graph else next(iter(graph)), label)
                for widget in graph:            # no menu behind a dialog: left stays on the screen
                    self.assertIsNot(graph[widget]["left"], app.query_one("#sidebar"), label)

    async def test_every_screen(self):
        for label, make in self.factories().items():
            with self.subTest(screen=label):
                await self.check_screen(label, make)

    async def test_picker_down_from_its_buttons_stays_below(self):
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), engine=FakeEngine)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(self.factories()["picker"]())
            await pilot.pause(0.5)
            app.screen.query_one("#picker-take").focus()
            await pilot.pause(0.1)
            await pilot.press("down")
            await pilot.pause(0.1)
            self.assertIn(app.focused.id, ("picker-detect", "picker-take", "picker-back"))
            app.screen.query_one("#picker-filter").focus()
            await pilot.pause(0.1)
            await pilot.press("down")                   # from the search field into the list, as before
            await pilot.pause(0.1)
            self.assertEqual(app.focused.id, "picker-list")

    def test_every_screen_class_is_covered(self):
        import importlib
        import inspect
        import pkgutil
        from textual.screen import Screen
        from tpotctl import screens
        classes = set()
        for module in pkgutil.iter_modules(screens.__path__):
            loaded = importlib.import_module(f"tpotctl.screens.{module.name}")
            classes |= {c.__name__ for _n, c in inspect.getmembers(loaded, inspect.isclass)
                        if issubclass(c, Screen) and c.__module__ == loaded.__name__}
        covered = {type(make()).__name__ for make in self.factories().values()}
        # the splash ends on any key
        self.assertEqual(sorted(classes - covered - {"SplashScreen"}), [])


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class AppsTest(unittest.IsolatedAsyncioTestCase):
    """The installer and the uninstaller run as apps of their own."""

    async def test_installer(self):
        from tpotctl.screens.install import InstallApp
        from tpotctl.tests.test_installer import FakeEngine as InstallEngine, all_ok
        from tpotctl.tests.test_settings import make_checkout
        app = InstallApp(engine=InstallEngine, checks=all_ok, sudo_mode="password",
                         password_ok=lambda pw: pw == "right", run=lambda *a, **k: None, repo_dir=make_checkout(self))
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.5)
            graph = await edges(pilot, app)
            check(self, graph, app.focused if app.focused in graph else next(iter(graph)), "installer")
            self.assertIn(app.query_one("#ins-next"), graph)

    def installer(self, sudo):
        from tpotctl.screens.install import InstallApp
        from tpotctl.tests.test_installer import FakeEngine as InstallEngine, all_ok
        from tpotctl.tests.test_settings import make_checkout
        return InstallApp(engine=InstallEngine, checks=all_ok, sudo_mode=sudo, password_ok=lambda pw: pw == "right",
                          run=lambda *a, **k: None, repo_dir=make_checkout(self))

    async def test_installer_every_step(self):
        for sudo in ("password", "passwordless"):
            app = self.installer(sudo)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause(0.5)
                for step in app.steps() + ["failed"]:
                    with self.subTest(sudo=sudo, step=step):
                        if step == "failed":            # the install ended with an error: log and Close
                            from tpotctl import installer
                            app.goto("install")
                            await pilot.pause(0.2)
                            app.progress = installer.Progress()
                            app.finished(1)
                        else:
                            app.goto(step)
                        await pilot.pause(0.3)
                        graph = await edges(pilot, app)
                        if graph:
                            check(self, graph, app.focused if app.focused in graph else next(iter(graph)),
                                  f"installer {step}")
                        review = app.query_one("#step-review")
                        if step == "review" and review.max_scroll_y > 0:
                            # more than fits: the arrows reach all of it, with and without the sudo field
                            if review.focusable:
                                self.assertIn(review, graph)
                            else:
                                app.query_one("#ins-sudo").focus()
                                await pilot.pause(0.2)
                                before = review.scroll_y
                                await pilot.press("up")         # nothing above the field: it scrolls
                                await pilot.pause(0.2)
                                self.assertLess(review.scroll_y, before)

    async def test_uninstaller(self):
        from tpotctl.screens.uninstall import UninstallApp
        app = UninstallApp(sudo_mode="password", password_ok=lambda pw: pw == "right", hostname="honey")
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.5)
            graph = await edges(pilot, app)
            check(self, graph, app.focused if app.focused in graph else next(iter(graph)), "uninstaller")


if __name__ == "__main__":
    unittest.main()
