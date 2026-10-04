"""The tpot menu: status, edition & services, settings, users, sensors, images, update.

Data comes from a Backend (tpotctl.ops on a host, fakes in the tests). Commands that
need the real terminal (sudo, update.sh, restore.sh) go through a Runner, which
suspends the app while they run. The look (theme, icons, logo) is in theme.py,
glyphs.py and logo.py, the user's choice of it in prefs.py.
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
from textual.widgets import (Button, ContentSwitcher, DataTable, Footer, Label, ListItem, ListView, Static,
                             TabbedContent, TabPane)

from tpotctl import glyphs, logo, ops, prefs, theme
from tpotctl.bootstrap import REPO_DIR
from tpotctl.commands import TpotCommands
from tpotctl.screens.dialogs import ConfirmDialog, SensorDialog, UserDialog
from tpotctl.theme import apply as apply_theme
from tpotctl.ops import cell_state
from tpotctl.widgets.comb import Honeycomb
from tpotctl.widgets.header import TpotHeader

LAUNCHER = os.path.join(REPO_DIR, "tpot")
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

    def tpot_type(self) -> str:
        return ops.env_values().get("TPOT_TYPE", "HIVE")

    def users(self):
        from tpotctl import users
        return users.load()

    def sensors(self):
        from tpotctl import sensors
        return sensors.Registry()

    def sensor_status(self, days: int = 7):
        from tpotctl import sensors
        return sensors.fetch_status(days)

    def system(self):
        from tpotctl import system
        if not hasattr(self, "_meter"):
            self._meter = system.Meter()
        return self._meter.read(ops.env_values())

    def attacks(self):
        from tpotctl import events
        return events.fetch()


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
        glyph, colour = cell_state(c)
        status = Text()
        status.append(f"{glyphs.g(glyph)} ", style=f"bold {theme.color(colour)}")
        status.append(c.status, style=theme.color(colour) if colour != "ok" else "")
        yield c.name, (c.name, status, c.ports)


def meter(label: str, percent: Optional[float], detail: str = "", width: int = 12) -> Text:
    """CPU   ▰▰▰▰▱▱▱▱▱▱▱▱  41 %  detail"""
    text = Text()
    text.append(f"{label:<6}", style="bold")
    if percent is None:
        text.append("measuring ...", style=theme.color("mist"))
        return text
    filled = round(min(max(percent, 0), 100) / 100 * width)
    colour = theme.color("ok" if percent < 70 else "warn" if percent < 90 else "error")
    text.append(glyphs.g("bar_on") * filled, style=colour)
    text.append(glyphs.g("bar_off") * (width - filled), style=theme.color("wax"))
    text.append(f" {percent:3.0f} %", style="bold")
    if detail:
        text.append(f"  {detail}", style=theme.color("mist"))
    return text


def system_text(state) -> Text:
    from tpotctl.system import human
    text = Text()
    text.append_text(meter("CPU", state.cpu))
    text.append("\n")
    memory = state.memory
    text.append_text(meter("RAM", memory.percent if memory else None,
                           f"{human(memory.used)} of {human(memory.total)}" if memory else ""))
    text.append("\n")
    disk = state.disk
    text.append_text(meter("Data", disk.percent if disk else None,
                           f"{human(disk.used)} of {human(disk.total)}" if disk else ""))
    return text


def attacks_text(attacks, width: int) -> Text:
    from tpotctl import events
    text = Text()
    if attacks is None:
        return Text("asking Elasticsearch ...", style=theme.color("mist"))
    if attacks.problem:
        return Text(attacks.problem, style=theme.color("mist"))
    values = attacks.per_minute[-max(10, width):]
    if not any(values):
        text.append("a quiet hour, no attacks\n\n", style=theme.color("mist"))
    for line in [] if not any(values) else events.sparkline(values, glyphs.spark(), rows=1 if glyphs.mode() == "ascii" else 2):
        text.append(line, style=theme.color("magenta"))
        text.append("\n")
    text.append(f"{sum(attacks.per_minute):,}".replace(",", " "), style="bold")
    text.append(" in the last hour   ", style=theme.color("mist"))
    text.append(f"{attacks.last_day:,}".replace(",", " "), style="bold")
    text.append(" in 24 hours\n", style=theme.color("mist"))
    for name, count in attacks.top:
        text.append(f"{name} ", style=theme.color("glass"))
        text.append(f"{count:,}   ".replace(",", " "), style=theme.color("mist"))
    return text


class StatusPane(Vertical):

    def compose(self) -> ComposeResult:
        with Horizontal(id="dash"):
            with Vertical(id="hive-block", classes="block"):
                yield Honeycomb(id="comb")
            with Vertical(id="side-blocks"):
                with Vertical(id="attacks-block", classes="block"):
                    yield Static(attacks_text(None, 40), id="attacks")
                with Vertical(id="system-block", classes="block"):
                    yield Static("", id="system")
            yield Static(logo.pot(), id="pot")
        yield Static("", id="status-info", classes="info")
        yield DataTable(id="containers", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="actions"):
            yield Button("Start", id="svc-start")
            yield Button("Stop", id="svc-stop")
            yield Button("Restart", id="svc-restart", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#hive-block").border_title = "Hive"
        self.query_one("#attacks-block").border_title = "Attacks"
        self.query_one("#system-block").border_title = "System"
        self.hive = self.app.backend.tpot_type() != "SENSOR"
        self.query_one("#attacks-block").display = self.hive
        self.attacks = None
        self.query_one(DataTable).add_columns("Name", "Status", "Ports")
        self.load()
        self.set_interval(2.0, self.load)
        if self.hive:
            self.load_attacks()
            self.set_interval(30.0, self.load_attacks)

    @work(thread=True, exclusive=True, group="status")
    def load(self) -> None:
        backend = self.app.backend
        try:
            state, containers, problem = backend.status(), backend.containers(), ""
        except ops.OpsError as err:
            state, containers, problem = None, [], str(err)
        try:
            machine = backend.system()
        except Exception:      # nothing in /proc is worth a crash of the menu
            machine = None
        self.app.call_from_thread(self.show, state, containers, problem, machine)

    @work(thread=True, exclusive=True, group="attacks")
    def load_attacks(self) -> None:
        attacks = self.app.backend.attacks()
        self.app.call_from_thread(self.show_attacks, attacks)

    def show_attacks(self, attacks) -> None:
        self.attacks = attacks
        width = self.query_one("#attacks").content_region.width or 40
        self.query_one("#attacks", Static).update(attacks_text(attacks, width))

    def repaint(self) -> None:
        """After a change of theme or icons."""
        self.query_one("#pot", Static).update(logo.pot())
        self.query_one(Honeycomb).repaint()
        if self.attacks is not None:
            self.show_attacks(self.attacks)
        if getattr(self, "shown", None):
            self.show(*self.shown)

    def show(self, state: Optional[ops.Status], containers: List[ops.Container], problem: str,
             machine=None) -> None:
        self.shown = (state, containers, problem, machine)
        if state is not None:
            self.app.update_header(state)
        self.query_one(Honeycomb).show(containers)
        if machine is not None:
            self.query_one("#system", Static).update(system_text(machine))
        info = Text()
        running = sum(c.state == "running" for c in containers)
        info.append(f"{running} of {len(containers)} containers running", style="bold")
        if state is not None and state.service not in ("active", "n/a"):
            info.append(f"   the tpot service is {state.service}", style=theme.color("error"))
        if problem:
            info.append(f"   {problem}", style=theme.color("error"))
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
        yield DataTable(id="images-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="actions"):
            yield Button("Refresh", id="images-refresh")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Repository", "Tag", "Image ID", "Size", "Created")
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
        yield Static("", id="edition-info", classes="info")
        with Horizontal(classes="actions"):
            yield Button("Open the customizer", id="open-customizer", variant="primary")

    def on_mount(self) -> None:
        self.show()

    def show(self) -> None:
        info = Text()
        info.append("Installed   ", style=theme.color("mist"))
        info.append(f"{ops.edition()}\n\n", style=f"bold {theme.color('magenta')}")
        info.append("Choose an edition to start from, switch services on and off and move host ports. "
                    "The customizer writes docker-compose-custom.yml and only does so without errors.")
        self.query_one("#edition-info", Static).update(info)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "open-customizer":
            self.app.action_customize()


class SettingsForm(VerticalScroll, inherit_bindings=False):
    """The settings of one tab; up and down go to the page, they move between the settings."""

    BINDINGS = [
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("home", "scroll_home", show=False),
        Binding("end", "scroll_end", show=False),
    ]


class SettingsPane(Vertical):
    """The settings of this T-Pot in .env, checked against the schema while you type."""

    BINDINGS = [
        Binding("down", "move(1)", "Next setting", show=False),
        Binding("up", "move(-1)", "Previous setting", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="settings-head"):
            yield Static("", id="settings-status")
            yield Button("Revert", id="settings-revert")
            yield Button("Save", id="settings-save", variant="primary", disabled=True)
        yield TabbedContent(id="settings-tabs")

    async def on_mount(self) -> None:
        self.current = None
        self.rows = {}
        self.unlocked = set()
        await self.reload()

    async def reload(self) -> None:
        from tpotctl import envschema
        from tpotctl.settings import SettingsError
        from tpotctl.widgets.fields import SettingRow
        tabs = self.query_one("#settings-tabs", TabbedContent)
        active = tabs.active
        await tabs.clear_panes()
        self.rows = {}
        try:
            self.current = self.app.backend.settings()
        except (SettingsError, OSError) as err:
            self.current = None
            self.query_one("#settings-status", Static).update(Text(str(err), style=theme.color("error")))
            return
        self.draft = dict(self.current.values)
        self.unlocked = set()
        by_section = {}
        for rule in self.current.relevant():
            row = SettingRow(rule, self.draft.get(rule.key, ""), self.current.why_fixed(rule.key),
                             unlockable=self.current.can_unlock(rule.key))
            self.rows[rule.key] = row
            by_section.setdefault(rule.section, []).append(row)
        for section, title in envschema.SECTIONS:
            if by_section.get(section):
                await tabs.add_pane(TabPane(title, SettingsForm(*by_section[section], classes="settings-form"),
                                            id=f"tab-{section}"))
        if active and active in [f"tab-{section}" for section in by_section]:
            tabs.active = active
        self.call_after_refresh(self.check)

    def changes(self):
        if self.current is None:
            return {}
        saved = self.current.values
        return {key: value for key, value in self.draft.items()
                if key in self.current.schema and (self.current.schema[key].editable or key in self.unlocked)
                and saved.get(key, "") != value}

    def check(self) -> None:
        from tpotctl import envschema
        if self.current is None:
            return
        problems = self.current.problems(self.draft)
        by_key = {}
        for problem in problems:
            by_key.setdefault(problem.key, []).append(problem)
        changes = self.changes()
        errors_in = {}
        for key, row in self.rows.items():
            if not row.is_mounted:
                continue
            row.display = envschema.shown_now(row.rule, self.draft, self.current.schema) or bool(by_key.get(key))
            row.mark(key in changes, by_key.get(key, []))
            if any(p.level == "error" for p in by_key.get(key, [])):
                errors_in[row.rule.section] = errors_in.get(row.rule.section, 0) + 1
        tabs = self.query_one("#settings-tabs", TabbedContent)
        for section, title in envschema.SECTIONS:
            try:
                tab = tabs.get_tab(f"tab-{section}")
            except Exception:      # no keys of that section here
                continue
            label = Text(title)
            if errors_in.get(section):
                label.append(f" {glyphs.g('fail')} {errors_in[section]}", style=f"bold {theme.color('error')}")
            tab.label = label
        blocking = self.current.blocking(problems, changes)
        self.query_one("#settings-save", Button).disabled = not changes or bool(blocking)
        self.query_one("#settings-revert", Button).disabled = not changes
        status = Text()
        if changes:
            status.append(f"{glyphs.g('changed')} {len(changes)} change{'s' if len(changes) > 1 else ''}",
                          style=f"bold {theme.color('magenta')}")
            status.append(f"  {', '.join(changes)}", style=theme.color("mist"))
        else:
            status.append(self.current.path.replace(os.path.expanduser("~"), "~", 1), style=theme.color("mist"))
        others = [p for p in problems if p.level == "error" and p.key not in changes]
        if others:
            status.append(f"\n{glyphs.g('fail')} T-Pot would not start with {len(others)} of the values, "
                          f"they are marked", style=theme.color("error"))
        self.query_one("#settings-status", Static).update(status)

    def on_setting_row_changed(self, event) -> None:
        self.draft[event.key] = event.value
        self.check()

    def on_setting_row_unlock(self, event) -> None:
        row = event.row
        rule = row.rule

        async def answered(yes: bool) -> None:
            if yes:
                await self.unlock(row)

        self.app.push_screen(ConfirmDialog(f"Unlock {rule.title} ({rule.key})?",
                                           Text(f"{rule.unlock}\n\nIt stays unlocked until you save or revert, "
                                                f"T-Pot checks the value as always.", style=theme.color("glass")),
                                           yes="Unlock", no="Keep it fixed"), answered)

    async def unlock(self, row) -> None:
        import dataclasses
        from tpotctl.widgets.fields import SettingRow
        key = row.rule.key
        self.unlocked.add(key)
        editable = SettingRow(dataclasses.replace(row.rule, editable=True), self.draft.get(key, ""), "",
                              unlocked=True)
        parent = row.parent
        place = list(parent.children).index(row)
        await row.remove()
        if place < len(parent.children):
            await parent.mount(editable, before=place)
        else:
            await parent.mount(editable)
        self.rows[key] = editable
        self.focus_row(editable)
        self.check()

    def on_setting_row_pick(self, event) -> None:
        from tpotctl.screens import pickers
        from tpotctl.widgets.fields import llm_settings
        row, rule = event.row, event.row.rule
        if event.detect:
            self.detect(row)
            return
        current = self.draft.get(rule.key, "")
        if rule.widget == "interface":
            picker = pickers.interface_picker(current)
        elif rule.widget == "timezone":
            picker = pickers.timezone_picker(current)
        else:
            llm = llm_settings(rule, self.draft, self.current.schema)
            picker = pickers.model_picker(current, llm["provider"], llm["url"], llm["api_key"])
        self.app.push_screen(picker, lambda value: row.set_value(value) if value is not None else None)

    @work(thread=True, exclusive=True, group="detect")
    def detect(self, row) -> None:
        from tpotctl import netinfo, tz
        found = netinfo.detect() if row.rule.widget == "interface" else tz.detect()
        self.app.call_from_thread(self.detected, row, found)

    def detected(self, row, found: str) -> None:
        if not found:
            self.app.notify("Nothing detected on this host.", title=row.rule.title, severity="warning")
            return
        row.set_value(found)
        note = (f"{found} has the route to the internet. Empty picks it automatically, and follows "
                f"when the route changes." if row.rule.widget == "interface" else f"{found} is the time zone "
                f"of this host.")
        self.app.notify(note, title=row.rule.title)

    def visible_rows(self):
        active = self.query_one("#settings-tabs", TabbedContent).active
        return [row for row in self.rows.values()
                if row.is_mounted and row.display and f"tab-{row.rule.section}" == active]

    def action_move(self, step: int) -> None:
        """up / down: from setting to setting, above the first one is the tab bar."""
        from tpotctl.widgets.fields import SettingRow
        from textual.widgets import Tabs
        rows = self.visible_rows()
        focused = self.app.focused
        row = next((w for w in (focused.ancestors_with_self if focused else []) if isinstance(w, SettingRow)), None)
        if row is None:
            if focused is not None and any(isinstance(w, Tabs) for w in focused.ancestors_with_self) and step > 0:
                self.focus_row(rows[0] if rows else None)
            return
        index = rows.index(row) if row in rows else 0
        if index + step < 0:
            self.query_one("#settings-tabs", TabbedContent).query_one(Tabs).focus()
        elif index + step < len(rows):
            self.focus_row(rows[index + step])

    def focus_row(self, row) -> None:
        if row is None:
            return
        controls = row.controls()
        (controls[0] if controls else row).focus()
        row.scroll_visible()

    def enter(self) -> None:
        """From the menu into the page: the first setting of the open tab."""
        rows = self.visible_rows()
        if rows:
            self.focus_row(rows[0])

    def focus_setting(self, key: str) -> None:
        row = self.rows.get(key)
        if row is None:
            return
        self.query_one("#settings-tabs", TabbedContent).active = f"tab-{row.rule.section}"

        def focus() -> None:
            row.display = True
            control = row.query(f"#set-{key}")
            if control:
                control.first().focus()
                row.scroll_visible()
            else:
                self.focus_row(row)

        self.call_after_refresh(focus)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        from tpotctl.settings import SettingsError
        if event.button.id == "settings-revert":
            await self.reload()
        elif event.button.id == "settings-save":
            try:
                self.current.change(self.changes(), unlocked=self.unlocked)
            except SettingsError as err:
                self.app.notify(str(err), title="Not saved", severity="error", timeout=10)
                return
            await self.reload()
            if self.app.backend.linux_host():
                self.app.push_screen(ConfirmDialog("Saved. Restart T-Pot now, so that it uses the new settings?",
                                                   yes="Restart", no="Later"),
                                     lambda yes: self.app.runner(ops.service_command("restart")) if yes else None)
            else:
                self.app.notify("Saved, restart T-Pot to use the new settings.", title="Settings")


class UsersPane(Vertical):
    """Users of the web UI (WEB_USER), changes count at once."""

    def compose(self) -> ComposeResult:
        yield Static("", id="users-info", classes="info")
        yield DataTable(id="users-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="actions"):
            yield Button("Add", id="user-add", variant="primary")
            yield Button("Change password", id="user-passwd")
            yield Button("Remove", id="user-remove")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("User", "Hash", "State")
        self.show()

    def show(self) -> None:
        from tpotctl.users import UsersError
        table = self.query_one(DataTable)
        table.clear()
        info = Text()
        try:
            self.store = self.app.backend.users()
            entries = self.store.users()
        except UsersError as err:
            self.store, entries = None, []
            info.append(str(err), style=theme.color("error"))
        for user in entries:
            state = Text(f"{glyphs.g('ok')} ok", style=theme.color("ok")) if user.ok else \
                Text(f"{glyphs.g('fail')} {user.problem}", style=f"bold {theme.color('error')}")
            table.add_row(user.name, user.scheme or "-", state, key=user.name)
        if self.store is not None:
            if not entries:
                info.append("No web users yet. Add one, without a user nobody can open the T-Pot web UI.\n",
                            style=f"bold {theme.color('warn')}")
            info.append("New and changed passwords are bcrypt, nginx uses them right away.",
                        style=theme.color("mist"))
            if any(not u.ok for u in entries):
                info.append("\nT-Pot does not start with the marked entries: change their password or remove "
                            "them.", style=f"bold {theme.color('error')}")
        self.query_one("#users-info", Static).update(info)

    def selected(self) -> str:
        table = self.query_one(DataTable)
        if not table.row_count:
            return ""
        return str(table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from tpotctl import users as tusers
        if self.store is None:
            return
        name = self.selected()
        if event.button.id == "user-add":
            self.app.push_screen(UserDialog("Add a web user", check_name=tusers.check_name,
                                            weakness=tusers.weakness),
                                 lambda result: self.save("add", result))
        elif event.button.id == "user-passwd" and name:
            if not tusers.NAME_RE.match(name):
                self.app.notify(f"{name} cannot be read, remove it", severity="warning")
                return
            self.app.push_screen(UserDialog(f"New password for {name}", name=name, weakness=tusers.weakness),
                                 lambda result: self.save("passwd", result))
        elif event.button.id == "user-remove" and name:
            self.app.push_screen(ConfirmDialog(f"Remove the web user {name}?", yes="Remove"),
                                 lambda yes: self.run_change(lambda: self.store.remove(name),
                                                             f"{name} is removed") if yes else None)

    def save(self, action: str, result) -> None:
        from tpotctl import users as tusers
        if not result:
            return
        name, password = result
        weak = tusers.weakness(password)
        change = (lambda: self.store.add(name, password)) if action == "add" else \
            (lambda: self.store.passwd(name, password))
        done = f"{name} is added" if action == "add" else f"{name} has a new password"
        if weak:
            self.app.push_screen(ConfirmDialog(f"The password is weak ({weak}). Keep it anyway?", yes="Keep it",
                                               no="Back"),
                                 lambda yes: self.run_change(change, done) if yes else None)
        else:
            self.run_change(change, done)

    def run_change(self, change, done: str) -> None:
        from tpotctl.users import UsersError
        try:
            note = change()
        except UsersError as err:
            self.app.notify(str(err), title="Not changed", severity="error", timeout=10)
            return
        self.app.notify(f"{done}, {note}.", title="Web users")
        self.show()


class SensorsPane(Vertical):
    """Sensors of this HIVE: access, where they are, when they were last seen."""

    def compose(self) -> ComposeResult:
        yield Static("", id="sensors-info", classes="info")
        yield DataTable(id="sensors-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="actions"):
            yield Button("Deploy a sensor", id="sensor-add", variant="primary")
            yield Button("Remove", id="sensor-remove")
            yield Button("Renew certificate", id="sensor-cert-renew")
            yield Button("Send certificate", id="sensor-cert-send")
            yield Button("Refresh", id="sensor-refresh")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Sensor", "Host", "Hostname", "Last seen", "State")
        self.registry = None
        self.load()

    @work(thread=True, exclusive=True, group="sensors")
    def load(self) -> None:
        from tpotctl import sensors as tsensors
        try:
            registry = self.app.backend.sensors()
            status = self.app.backend.sensor_status()
            tsensors.link_hostnames(registry, status)
            problem = ""
        except tsensors.SensorsError as err:
            registry, status, problem = None, None, str(err)
        self.app.call_from_thread(self.show, registry, status, problem)

    def show(self, registry, status, problem: str) -> None:
        self.registry = registry
        table = self.query_one(DataTable)
        table.clear()
        info = Text()
        if registry is None:
            info.append(problem, style=theme.color("error"))
            self.query_one("#sensors-info", Static).update(info)
            return
        for sensor in registry.sensors():
            seen = status.sensors.get(sensor.name)
            if not sensor.access:
                state = Text(f"{glyphs.g('fail')} no access", style=theme.color("error"))
            elif seen:
                state = Text(f"{glyphs.g('running')} sending", style=theme.color("ok"))
            else:
                state = Text(f"{glyphs.g('stopped')} " + ("unknown" if status.problem else "nothing in 7 days"),
                             style=theme.color("warn"))
            table.add_row(sensor.name + ("" if sensor.source == "deployed" else " (migrated)"), sensor.host or "-",
                          (seen.hostname if seen else sensor.hostname) or "-",
                          seen.last[:19].replace("T", " ") if seen else "-", state, key=sensor.name)
        if not registry.sensors():
            info.append("No sensors yet. Deploy a sensor to send its events to this HIVE.",
                        style=theme.color("mist"))
        if registry.migrated:
            info.append(f"Taken over from LS_WEB_USER: {', '.join(registry.migrated)}. ", style=theme.color("magenta"))
        if status.problem:
            info.append(f"\n{status.problem}", style=theme.color("warn"))
        for hostname, seen in sorted(status.unlinked.items()):
            info.append(f"\nEvents from {hostname} carry no sensor name: older ones, or this HIVE does not pass it "
                        f"on yet (update T-Pot).", style=theme.color("mist"))
        if info.plain.startswith("\n"):
            info = info[1:]
        self.query_one("#sensors-info", Static).update(info)

    def selected(self) -> str:
        table = self.query_one(DataTable)
        if not table.row_count:
            return ""
        return str(table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from tpotctl import sensors as tsensors
        button = event.button.id
        if button == "sensor-refresh":
            self.load()
        elif button == "sensor-add":
            self.app.push_screen(SensorDialog(tsensors.check_address, tsensors.check_user,
                                              tsensors.default_hive_address), self.deploy)
        elif button == "sensor-remove" and self.selected() and self.registry is not None:
            name = self.selected()
            self.app.push_screen(ConfirmDialog(f"Revoke the access of {name}? It cannot send to this HIVE any more, "
                                               f"nothing is done on the sensor itself.", yes="Revoke"),
                                 lambda yes: self.revoke(name) if yes else None)
        elif button == "sensor-cert-renew":
            self.app.runner([LAUNCHER, "sensors", "cert", "--renew"], cwd=REPO_DIR)
            self.load()
        elif button == "sensor-cert-send":
            self.app.runner([LAUNCHER, "sensors", "cert", "--distribute"], cwd=REPO_DIR)
            self.load()

    def deploy(self, result) -> None:
        if not result:
            return
        command = [LAUNCHER, "sensors", "add", "--host", result["host"], "--ssh-user", result["user"]]
        if result["hive"]:
            command += ["--hive-address", result["hive"]]
        if result["nopass"]:
            command.append("--no-become-pass")
        self.app.runner(command, cwd=REPO_DIR)
        self.load()

    def revoke(self, name: str) -> None:
        from tpotctl.sensors import SensorsError
        try:
            note = self.registry.revoke(name)
        except SensorsError as err:
            self.app.notify(str(err), title="Not removed", severity="error", timeout=10)
            return
        self.app.notify(f"{name} is removed, {note}.", title="Sensors")
        self.load()


class UpdatePane(Vertical):

    def compose(self) -> ComposeResult:
        yield Static("", id="update-info", classes="info")
        with Horizontal(classes="actions"):
            yield Button("Update", id="run-update", variant="primary")
            yield Button("Update and start", id="run-update-start")
            yield Button("Restore a backup", id="run-restore")
        yield DataTable(id="backups", cursor_type="row")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Backups in ~/tpot_backups", "Size")
        self.show()

    def show(self) -> None:
        info = Text()
        info.append("update.sh stops T-Pot, writes a backup, pulls the release of your branch and puts your "
                    "edition and settings back. restore.sh brings a backup back.", style=theme.color("mist"))
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
    ("users", "Web users", UsersPane, False),
    ("sensors", "Sensors", SensorsPane, True),
    ("images", "Images", ImagesPane, True),
    ("update", "Update & backup", UpdatePane, True),
]
SHORT = {"edition": "Edition", "users": "Users", "update": "Update"}
REPAINT = {"status": "repaint", "edition": "show", "settings": "check", "users": "show", "sensors": "load",
           "images": "load", "update": "show"}


def menu_text(key: str, title: str, active: bool) -> Text:
    text = Text()
    if glyphs.mode() == "nerd":
        icon = glyphs.icon_pane(key)
    else:
        icon = glyphs.g("on") if active else glyphs.g("off")
    # bright on the page you are on: it sits on magenta while the menu has the focus
    text.append(f"{icon} ", style=f"bold {theme.color('glass')}" if active else theme.color("mist"))
    text.append(title, style="bold" if active else "")
    return text


class TpotApp(App):
    """The tpot menu."""

    CSS_PATH = "tpot.tcss"
    TITLE = "T-Pot"
    COMMANDS = App.COMMANDS | {TpotCommands}
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (100, "-normal"), (150, "-wide")]
    VERTICAL_BREAKPOINTS = [(0, "-short"), (34, "-tall")]
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("c", "customize", "Customizer"),
        Binding("r", "restart_service", "Restart T-Pot", show=False),
        Binding("f2", "next_icons", "Icons"),
        Binding("escape", "menu", "Menu", show=False),
        Binding("right", "enter_page", "Open", show=False),
    ]

    def __init__(self, backend: Optional[Backend] = None, runner: Optional[Callable] = None, splash: bool = False):
        super().__init__()
        self.backend = backend or Backend()
        self.runner = runner or Runner(self)
        self.splash = splash
        apply_theme(self)
        sensor = self.backend.tpot_type() == "SENSOR"
        self.panes = [p for p in PANES if (self.backend.linux_host() or not p[3])
                      and not (sensor and p[0] in ("users", "sensors"))]

    def compose(self) -> ComposeResult:
        yield TpotHeader(id="header")
        with Horizontal(id="body"):
            yield ListView(*[ListItem(Label(menu_text(key, title, False), classes="menu-long"),
                                      Label(menu_text(key, SHORT.get(key, title), False), classes="menu-short"),
                                      id=f"menu-{key}")
                             for key, title, _cls, _host in self.panes], id="sidebar")
            with ContentSwitcher(initial=self.panes[0][0], id="panes"):
                for key, _title, cls, _host in self.panes:
                    yield cls(id=key, classes="pane")
        yield Footer()

    def get_theme_variable_defaults(self):
        return theme.variable_defaults()

    def on_mount(self) -> None:
        from tpotctl.screens.splash import SplashScreen, fits
        self.paint_menu()
        self.query_one("#sidebar", ListView).focus()
        if self.splash and fits(self.size.width, self.size.height):
            self.push_screen(SplashScreen(ops.env_values().get("TPOT_VERSION", "")))
        if not any(key == "status" for key, *_rest in self.panes):
            self.load_header()

    @work(thread=True, exclusive=True, group="header")
    def load_header(self) -> None:
        try:
            state = self.backend.status()
        except ops.OpsError:
            return
        self.call_from_thread(self.update_header, state)

    def update_header(self, state: ops.Status) -> None:
        self.query_one(TpotHeader).repaint(state)

    def paint_menu(self) -> None:
        current = self.query_one(ContentSwitcher).current
        for key, title, _cls, _host in self.panes:
            item = self.query_one(f"#menu-{key}", ListItem)
            item.query_one(".menu-long", Label).update(menu_text(key, title, key == current))
            item.query_one(".menu-short", Label).update(menu_text(key, SHORT.get(key, title), key == current))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None and event.item.id:
            self.query_one(ContentSwitcher).current = event.item.id[len("menu-"):]
            self.paint_menu()

    def check_action(self, action: str, parameters):
        # the customizer and the dialogs are screens of their own, c there would open a second one
        if action in ("customize", "restart_service") and len(self.screen_stack) > 1:
            return False
        return True

    def action_menu(self) -> None:
        """esc: back to the menu on the left."""
        if len(self.screen_stack) == 1:
            self.query_one("#sidebar", ListView).focus()

    def action_enter_page(self) -> None:
        """right or enter in the menu: into the page."""
        if self.focused is not self.query_one("#sidebar", ListView):
            return
        pane = self.query_one(f"#{self.query_one(ContentSwitcher).current}")
        if hasattr(pane, "enter"):
            pane.enter()
            return
        target = next((w for w in pane.query("*") if w.focusable), None)
        if target is not None:
            target.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_enter_page()

    def get_system_commands(self, screen):
        # one theme, the T-Pot colours: no theme picker
        for command in super().get_system_commands(screen):
            if command.title != "Theme":
                yield command

    # -- navigation ----------------------------------------------------------

    def goto(self, key: str) -> None:
        keys = [k for k, *_rest in self.panes]
        if key in keys:
            self.query_one("#sidebar", ListView).index = keys.index(key)
            self.query_one(ContentSwitcher).current = key
            self.paint_menu()

    def setting_keys(self):
        pane = self.query("#settings")
        current = getattr(pane.first(), "current", None) if pane else None
        if current is None:
            return []
        return [(rule.key, rule.title) for rule in current.relevant()]

    def goto_setting(self, key: str) -> None:
        self.goto("settings")
        self.query_one("#settings", SettingsPane).focus_setting(key)

    # -- look ----------------------------------------------------------------

    def action_next_icons(self) -> None:
        self.set_icons(glyphs.MODES[(glyphs.MODES.index(glyphs.mode()) + 1) % len(glyphs.MODES)])

    def set_icons(self, mode: str) -> None:
        glyphs.set_mode(mode)
        chosen = prefs.load()
        chosen.icons = mode
        self.remember(chosen, f"Icons {mode}" + (", they need a Nerd Font in your terminal" if mode == "nerd" else ""))

    def remember(self, chosen: prefs.Prefs, what: str) -> None:
        saved = prefs.save(chosen)
        self.repaint()
        self.notify(what if saved else f"{what}, for this run only ({prefs.path()} cannot be written)",
                    title="Look", timeout=4)

    def repaint(self) -> None:
        """Rich texts carry their colours and glyphs, draw them anew."""
        self.query_one(TpotHeader).repaint()
        self.paint_menu()
        for key, *_rest in self.panes:
            getattr(self.query_one(f"#{key}"), REPAINT[key])()

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
                 "docker-compose-custom.yml up.", style=theme.color("mist")), yes="Replace and restart", no="Not now"), replace)


class CustomizerApp(App):
    """compose/customizer.py on its own: the customizer screen, returns the Selection."""

    CSS_PATH = "tpot.tcss"
    TITLE = "T-Pot customizer"

    def __init__(self, catalog, selection, max_networks: int):
        super().__init__()
        self.catalog, self.selection, self.max_networks = catalog, selection, max_networks
        apply_theme(self)

    def get_theme_variable_defaults(self):
        return theme.variable_defaults()

    def on_mount(self) -> None:
        from tpotctl.screens.customizer import CustomizerScreen
        self.push_screen(CustomizerScreen(self.catalog, self.selection, self.max_networks), self.exit)


def run_customizer(catalog, selection, max_networks: int):
    return CustomizerApp(catalog, selection, max_networks).run()


def splash_wanted() -> bool:
    """The logo for a person at a terminal, not with TPOT_SPLASH=off."""
    return sys.stdout.isatty() and os.environ.get("TPOT_SPLASH", "on").lower() not in ("off", "0", "no", "false")


def run_app() -> int:
    result = TpotApp(splash=splash_wanted()).run()
    if result == "restart":
        os.execv(sys.executable, [sys.executable, LAUNCHER])
    return 0
