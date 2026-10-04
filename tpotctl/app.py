"""The tpot menu: status, edition & services, images, update & backup.

Data comes from a Backend (tpotctl.ops on a host, fakes in the tests). Commands that
need the real terminal (sudo, update.sh, restore.sh) go through a Runner, which
suspends the app while they run.
"""

import os
import subprocess
import sys
from typing import Callable, List, Optional

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, ContentSwitcher, DataTable, Footer, Input, Label, ListItem, ListView, Select, Static

from tpotctl import ops
from tpotctl.bootstrap import REPO_DIR
from tpotctl.screens.dialogs import ConfirmDialog
from tpotctl.theme import apply as apply_theme

LAUNCHER = os.path.join(REPO_DIR, "tpot")
HEALTH_STYLE = {"healthy": "green", "unhealthy": "bold red", "starting": "yellow"}
CUSTOM_OUTPUT = os.path.join(REPO_DIR, "docker-compose-custom.yml")


class Backend:
    """What the app reads, tpotctl.ops on a T-Pot host."""

    def linux_host(self) -> bool:
        return ops.linux_host()

    def status(self) -> ops.Status:
        return ops.status()

    def containers(self) -> List[ops.Container]:
        return ops.containers()

    def images(self) -> List[ops.Image]:
        return ops.images()

    def backups(self) -> List[str]:
        return ops.backups()

    def settings(self):
        from tpotctl import settings
        return settings.load()


class Runner:
    """Run a command in the normal terminal, the app is suspended meanwhile."""

    def __init__(self, app: App):
        self.app = app

    def __call__(self, command: List[str], cwd: Optional[str] = None) -> int:
        with self.app.suspend():
            print(f"\n$ {' '.join(command)}\n", flush=True)
            try:
                code = subprocess.call(command, cwd=cwd)
            except OSError as err:
                print(err)
                code = 127
            try:
                input(f"\n[exit code {code}] Press Enter to return to tpot ... ")
            except EOFError:
                pass
        return code


def container_rows(containers: List[ops.Container]):
    for c in containers:
        style = HEALTH_STYLE.get(c.health) or ("red" if c.state != "running" else "")
        yield c.name, (c.name, Text(c.status, style=style), c.ports)


class StatusPane(Vertical):

    def compose(self) -> ComposeResult:
        yield Label("Status", classes="pane-title")
        yield Static("", id="status-info", classes="info")
        yield DataTable(id="containers", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="actions"):
            yield Button("Start", id="svc-start")
            yield Button("Stop", id="svc-stop")
            yield Button("Restart", id="svc-restart", variant="primary")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("NAME", "STATUS", "PORTS")
        self.load()
        self.set_interval(2.0, self.load)

    @work(thread=True, exclusive=True, group="status")
    def load(self) -> None:
        backend = self.app.backend
        try:
            state, containers, problem = backend.status(), backend.containers(), ""
        except ops.OpsError as err:
            state, containers, problem = None, [], str(err)
        self.app.call_from_thread(self.show, state, containers, problem)

    def show(self, state: Optional[ops.Status], containers: List[ops.Container], problem: str) -> None:
        info = Text()
        if state is not None:
            for label, value in (("Version", f"{state.version} ({state.branch} {state.commit})"),
                                 ("Edition", state.edition), ("Type", state.tpot_type),
                                 ("Service", state.service)):
                info.append(f"{label:<9}", style="bold #E20074")
                info.append(f"{value}\n", style="red" if label == "Service" and state.service != "active" else "")
        running = sum(c.state == "running" for c in containers)
        unhealthy = sum(c.health == "unhealthy" for c in containers)
        restarting = sum(c.state == "restarting" for c in containers)
        info.append(f"{'Running':<9}", style="bold #E20074")
        info.append(f"{running}/{len(containers)} containers", style="")
        if restarting:
            info.append(f", {restarting} restarting", style="bold red")
        if unhealthy:
            info.append(f", {unhealthy} unhealthy", style="bold red")
        if problem:
            info.append(f"\n{problem}", style="red")
        self.query_one("#status-info", Static).update(info)
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        for key, cells in container_rows(containers):
            table.add_row(*cells, key=key)
        if containers:
            table.move_cursor(row=min(row, len(containers) - 1))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("svc-"):
            self.app.service(event.button.id[4:])


class ImagesPane(Vertical):

    def compose(self) -> ComposeResult:
        yield Label("Images", classes="pane-title")
        yield DataTable(id="images-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="actions"):
            yield Button("Refresh", id="images-refresh")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("REPOSITORY", "TAG", "IMAGE ID", "SIZE", "CREATED")
        self.load()

    @work(thread=True, exclusive=True, group="images")
    def load(self) -> None:
        try:
            images = self.app.backend.images()
        except ops.OpsError:
            images = []
        self.app.call_from_thread(self.show, images)

    def show(self, images: List[ops.Image]) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for i in images:
            table.add_row(i.repository, i.tag, i.id, i.size, i.created)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "images-refresh":
            self.load()


class EditionPane(Vertical):

    def compose(self) -> ComposeResult:
        yield Label("Edition & services", classes="pane-title")
        yield Static("", id="edition-info", classes="info")
        with Horizontal(classes="actions"):
            yield Button("Open the customizer", id="open-customizer", variant="primary")

    def on_mount(self) -> None:
        self.show()

    def show(self) -> None:
        info = Text()
        info.append("Installed  ", style="bold #E20074")
        info.append(f"{ops.edition()}\n\n")
        info.append("Choose an edition to start from, switch services on and off and move host ports. "
                    "The customizer writes docker-compose-custom.yml and only does so without errors.")
        self.query_one("#edition-info", Static).update(info)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "open-customizer":
            self.app.action_customize()


class SettingRow(Vertical):
    """One setting: title, the field for it, help and its problem."""

    def __init__(self, rule, value: str, fixed: str):
        super().__init__(classes="setting")
        self.rule, self.value, self.fixed = rule, value, fixed

    def compose(self) -> ComposeResult:
        from tpotctl import settings as tsettings
        rule = self.rule
        title = Text(rule.title, style="bold")
        title.append(f"  {rule.key}", style="dim")
        yield Label(title)
        if not rule.editable:
            shown = tsettings.shown(rule, self.value) or "(empty)"
            yield Static(Text(f"{shown}  ({self.fixed})", style="dim"))
        elif rule.type == "enum":
            current = self.value if self.value in rule.values else Select.NULL
            yield Select([(v, v) for v in rule.values], value=current, id=f"set-{rule.key}",
                         allow_blank=rule.optional or current is Select.NULL)
        else:
            yield Input(self.value, password=rule.secret, id=f"set-{rule.key}", placeholder=rule.default)
        if rule.help:
            yield Static(Text(rule.help, style="dim"))
        yield Static("", id=f"err-{rule.key}")


class SettingsPane(Vertical):
    """The settings of this T-Pot in .env, checked against the schema while you type."""

    def compose(self) -> ComposeResult:
        yield Label("Settings", classes="pane-title")
        yield Static("", id="settings-status", classes="info")
        yield VerticalScroll(id="settings-form")
        with Horizontal(classes="actions"):
            yield Button("Save", id="settings-save", variant="primary", disabled=True)
            yield Button("Revert", id="settings-revert")

    def on_mount(self) -> None:
        self.reload()

    def reload(self) -> None:
        from tpotctl import envschema
        from tpotctl.settings import SettingsError
        form = self.query_one("#settings-form", VerticalScroll)
        form.remove_children()
        try:
            self.current = self.app.backend.settings()
        except (SettingsError, OSError) as err:
            self.current = None
            self.query_one("#settings-status", Static).update(Text(str(err), style="red"))
            return
        self.draft = dict(self.current.values)
        rows, section = [], None
        for rule in self.current.relevant():
            if rule.section != section:
                section = rule.section
                rows.append(Label(dict(envschema.SECTIONS).get(section, section), classes="settings-section"))
            rows.append(SettingRow(rule, self.draft.get(rule.key, ""), self.current.why_fixed(rule.key)))
        form.mount(*rows)
        self.call_after_refresh(self.check)

    def changes(self):
        if self.current is None:
            return {}
        saved = self.current.values
        return {key: value for key, value in self.draft.items()
                if key in self.current.schema and self.current.schema[key].editable and saved.get(key, "") != value}

    def check(self) -> None:
        if self.current is None:
            return
        problems = self.current.problems(self.draft)
        by_key = {}
        for problem in problems:
            by_key.setdefault(problem.key, []).append(problem)
        for widget in self.query(".setting Static"):
            if widget.id and widget.id.startswith("err-"):
                key = widget.id[4:]
                text = Text()
                for problem in by_key.get(key, []):
                    text.append(f"! {problem.text}\n", style="bold red" if problem.level == "error" else "yellow")
                widget.update(text)
        changes = self.changes()
        blocking = self.current.blocking(problems, changes)
        self.query_one("#settings-save", Button).disabled = not changes or bool(blocking)
        status = Text()
        status.append(self.current.path, style="dim")
        if changes:
            status.append(f"\n{len(changes)} change(s): {', '.join(changes)}", style="bold #E20074")
        others = [p for p in problems if p.level == "error" and p.key not in changes]
        if others:
            status.append(f"\n{len(others)} error(s) T-Pot would not start with, see below", style="red")
        self.query_one("#settings-status", Static).update(status)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("set-"):
            self.draft[event.input.id[4:]] = event.value
            self.check()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id and event.select.id.startswith("set-"):
            self.draft[event.select.id[4:]] = "" if event.value is Select.NULL else str(event.value)
            self.check()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from tpotctl.settings import SettingsError
        if event.button.id == "settings-revert":
            self.reload()
        elif event.button.id == "settings-save":
            try:
                self.current.change(self.changes())
            except SettingsError as err:
                self.app.notify(str(err), title="Not saved", severity="error", timeout=10)
                return
            self.reload()
            if self.app.backend.linux_host():
                self.app.push_screen(ConfirmDialog("Saved. Restart T-Pot now, so that it uses the new settings?",
                                                   yes="Restart", no="Later"),
                                     lambda yes: self.app.runner(ops.service_command("restart")) if yes else None)
            else:
                self.app.notify("Saved, restart T-Pot to use the new settings.", title="Settings")


class UpdatePane(Vertical):

    def compose(self) -> ComposeResult:
        yield Label("Update & backup", classes="pane-title")
        yield Static("", id="update-info", classes="info")
        with Horizontal(classes="actions"):
            yield Button("Update", id="run-update", variant="primary")
            yield Button("Update and start", id="run-update-start")
            yield Button("Restore a backup", id="run-restore")
        yield DataTable(id="backups", cursor_type="row")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("BACKUPS IN ~/tpot_backups", "SIZE")
        self.show()

    def show(self) -> None:
        info = Text()
        info.append("update.sh stops T-Pot, writes a backup, pulls the release of your branch and puts your "
                    "edition and settings back. restore.sh brings a backup back.", style="")
        self.query_one("#update-info", Static).update(info)
        table = self.query_one(DataTable)
        table.clear()
        for path in self.app.backend.backups():
            try:
                size = f"{os.path.getsize(path) / 1024 / 1024:.0f} MB"
            except OSError:
                size = "?"
            table.add_row(os.path.basename(path), size)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-update":
            self.app.script("update.sh", ["-y"])
        elif event.button.id == "run-update-start":
            self.app.script("update.sh", ["-y", "-s"])
        elif event.button.id == "run-restore":
            self.app.script("restore.sh", [])


PANES = [
    ("status", "Status", StatusPane, True),
    ("edition", "Edition & services", EditionPane, False),
    ("settings", "Settings", SettingsPane, False),
    ("images", "Images", ImagesPane, True),
    ("update", "Update & backup", UpdatePane, True),
]


class TpotApp(App):
    """The tpot menu."""

    CSS_PATH = "tpot.tcss"
    TITLE = "T-Pot"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("c", "customize", "Customizer"),
        Binding("r", "restart_service", "Restart T-Pot", show=False),
    ]

    def __init__(self, backend: Optional[Backend] = None, runner: Optional[Callable] = None):
        super().__init__()
        self.backend = backend or Backend()
        self.runner = runner or Runner(self)
        self.panes = [p for p in PANES if self.backend.linux_host() or not p[3]]

    def compose(self) -> ComposeResult:
        yield Static("T-Pot", id="title", classes="bar")
        with Horizontal():
            yield ListView(*[ListItem(Label(title), id=f"menu-{key}") for key, title, _cls, _host in self.panes],
                           id="sidebar")
            with ContentSwitcher(initial=self.panes[0][0], id="panes"):
                for key, _title, cls, _host in self.panes:
                    yield cls(id=key, classes="pane")
        yield Footer()

    def on_mount(self) -> None:
        apply_theme(self)
        self.query_one("#sidebar", ListView).focus()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None and event.item.id:
            self.query_one(ContentSwitcher).current = event.item.id[len("menu-"):]

    # -- actions -------------------------------------------------------------

    def service(self, action: str) -> None:
        def confirmed(yes: bool) -> None:
            if yes:
                self.runner(ops.service_command(action))
        if action == "start":
            confirmed(True)
        else:
            self.push_screen(ConfirmDialog(f"{action.capitalize()} T-Pot? The honeypots are down meanwhile.",
                                           yes=action.capitalize()), confirmed)

    def action_restart_service(self) -> None:
        if self.backend.linux_host():
            self.service("restart")

    def script(self, name: str, args: List[str]) -> None:
        what = {"update.sh": "Update T-Pot? update.sh stops T-Pot and writes a backup first.",
                "restore.sh": "Restore a backup? restore.sh asks what to bring back."}[name]

        def confirmed(yes: bool) -> None:
            if yes:
                self.runner(ops.script_command(name, args), cwd=REPO_DIR)
                # the checkout below this app may have changed, start it anew
                self.exit("restart")

        self.push_screen(ConfirmDialog(what, yes="Run"), confirmed)

    def action_customize(self) -> None:
        from tpotctl.screens.customizer import CustomizerScreen, core
        catalog = core.Catalog()
        edition, selection = core.current_edition()
        if not (selection and selection.base in catalog.editions):
            selection = core.Selection(edition if edition in catalog.editions else "STANDARD")
        self.push_screen(CustomizerScreen(catalog, selection, core.DEFAULT_MAX_NETWORKS),
                         lambda chosen: self.customized(catalog, chosen))

    def customized(self, catalog, selection) -> None:
        from tpotctl.screens.customizer import core
        if selection is None:
            return
        try:
            result = core.resolve(catalog, selection, core.DEFAULT_MAX_NETWORKS, strict=True)
            if result.errors:
                raise core.CustomizerError(result.errors[0].text)
            core.write_output(core.render(catalog, result), CUSTOM_OUTPUT)
        except (core.CustomizerError, OSError) as err:
            self.notify(str(err), title="Not written", severity="error", timeout=10)
            return
        if not self.backend.linux_host():
            self.notify(f"{CUSTOM_OUTPUT} is written.", title="Customizer")
            return

        def replace(yes: bool) -> None:
            if yes:
                self.runner(["bash", "-c", "sudo systemctl stop tpot && mv -f docker-compose-custom.yml "
                                           "docker-compose.yml && sudo systemctl start tpot"], cwd=REPO_DIR)
            else:
                self.notify("Test it with: docker compose -f docker-compose-custom.yml up, then replace "
                            "docker-compose.yml with it.", title="docker-compose-custom.yml is written", timeout=15)

        self.push_screen(ConfirmDialog(
            "docker-compose-custom.yml is written. Replace docker-compose.yml with it and restart T-Pot now?",
            Text("Not sure? Choose 'Not now' and test it first with docker compose -f "
                 "docker-compose-custom.yml up.", style="dim"), yes="Replace and restart", no="Not now"), replace)


class CustomizerApp(App):
    """compose/customizer.py on its own: the customizer screen, returns the Selection."""

    CSS_PATH = "tpot.tcss"
    TITLE = "T-Pot customizer"

    def __init__(self, catalog, selection, max_networks: int):
        super().__init__()
        self.catalog, self.selection, self.max_networks = catalog, selection, max_networks

    def on_mount(self) -> None:
        from tpotctl.screens.customizer import CustomizerScreen
        apply_theme(self)
        self.push_screen(CustomizerScreen(self.catalog, self.selection, self.max_networks), self.exit)


def run_customizer(catalog, selection, max_networks: int):
    return CustomizerApp(catalog, selection, max_networks).run()


def run_app() -> int:
    result = TpotApp().run()
    if result == "restart":
        os.execv(sys.executable, [sys.executable, LAUNCHER])
    return 0
