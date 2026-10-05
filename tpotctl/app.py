"""The tpot menu: status, edition & services, settings, users, sensors, images, update.

Data comes from a Backend (tpotctl.ops on a host, fakes in the tests). Commands that
need the real terminal (sudo, update.sh, restore.sh) go through a Runner, which
suspends the app while they run. The look (theme, icons, logo) is in theme.py,
glyphs.py and logo.py, the user's choice of it in prefs.py.
"""

import os
import subprocess
import sys
from typing import Callable, Dict, Iterable, List, Optional, Set

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Button, Checkbox, ContentSwitcher, DataTable, Footer, Input, Label, ListItem, ListView,
                             OptionList, Static, TabbedContent, TabPane)
from textual.widgets.option_list import Option

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

    def backup_infos(self):
        return [ops.backup_info(path) for path in ops.backups()]

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

    def top_sources(self):
        from tpotctl import events
        return events.top_sources(hours=24, size=5)

    def host_address(self) -> str:
        """The address of this host towards the internet, the default target of the honeypot probe."""
        out = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True) if ops.linux_host() else None
        words = out.stdout.split() if out is not None and out.returncode == 0 else []
        return words[words.index("src") + 1] if "src" in words[:-1] else ""

    def sudo_mode(self) -> str:
        from tpotctl import installer
        return installer.sudo_mode()

    def installable(self) -> bool:
        """A Linux host without T-Pot: tpot install would run here."""
        from tpotctl import installer
        return not installer.installed()

    def editions(self):
        from tpotctl import editions
        return editions.available()

    def edition_current(self):
        from tpotctl import editions
        return editions.current()

    def edition_plan(self, key: str):
        from tpotctl import editions, users
        try:
            users_ok = any(user.ok for user in users.load().users())
        except (users.UsersError, OSError):
            users_ok = False
        return editions.plan(key, users_ok=users_ok)


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


def sources_text(sources) -> Text:
    """The top attackers of 24 hours (tpot attackers)."""
    text = Text()
    if sources is None or sources.problem or not sources.sources:
        return text
    text.append("\nTop attackers, 24 hours\n", style=f"bold {theme.color('glass')}")
    for source in sources.sources:
        text.append(f"{source.ip:<16}", style=theme.color("magenta"))
        text.append(f"{source.count:>7,}".replace(",", " "), style="bold")
        if source.country:
            text.append(f"  {source.country}", style=theme.color("mist"))
        text.append("\n")
    text.rstrip()
    return text


class StatusPane(Vertical):

    def compose(self) -> ComposeResult:
        with Horizontal(id="dash"):
            with Vertical(id="hive-block", classes="block"):
                yield Honeycomb(id="comb")
            with Vertical(id="side-blocks"):
                with Vertical(id="attacks-block", classes="block"):
                    yield Static(attacks_text(None, 40), id="attacks")
                    yield Static("", id="top-attackers")
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
        sources = self.app.backend.top_sources() if not attacks.problem else None
        self.app.call_from_thread(self.show_attacks, attacks, sources)

    def show_attacks(self, attacks, sources=None) -> None:
        self.attacks, self.sources = attacks, sources
        width = self.query_one("#attacks").content_region.width or 40
        self.query_one("#attacks", Static).update(attacks_text(attacks, width))
        self.query_one("#top-attackers", Static).update(sources_text(sources))

    def repaint(self) -> None:
        """After a change of theme or icons."""
        self.query_one("#pot", Static).update(logo.pot())
        self.query_one(Honeycomb).repaint()
        if self.attacks is not None:
            self.show_attacks(self.attacks, getattr(self, "sources", None))
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
    """The editions of T-Pot: switch to one (tpot edition set), or build your own."""

    def compose(self) -> ComposeResult:
        yield Static("", id="edition-info", classes="info")
        yield OptionList(id="edition-list")
        yield Static("", id="edition-note")
        with Horizontal(classes="actions"):
            yield Button("Switch", id="switch-edition", variant="primary", disabled=True)
            yield Button("Open the customizer", id="open-customizer")

    def on_mount(self) -> None:
        self.choices = []
        self.show()

    def show(self) -> None:
        name, base = self.app.backend.edition_current()
        self.in_use = name.lower()
        info = Text()
        info.append("Installed   ", style=theme.color("mist"))
        info.append(f"{name}{' from ' + base if base else ''}\n\n", style=f"bold {theme.color('magenta')}")
        info.append("Switch to another edition here: T-Pot stops, your docker-compose.yml is kept in "
                    "~/tpot_backups, the edition takes its place and T-Pot starts again. Or build your own "
                    "from an edition with the customizer.", style=theme.color("glass"))
        self.query_one("#edition-info", Static).update(info)
        listing = self.query_one("#edition-list", OptionList)
        highlighted = listing.highlighted
        self.choices = self.app.backend.editions()
        listing.clear_options()
        for choice in self.choices:
            listing.add_option(Option(self.option_text(choice), id=choice.key))
        if self.choices:
            listing.highlighted = highlighted if highlighted is not None and highlighted < len(self.choices) else 0
        self.refresh_button()

    def option_text(self, choice) -> Text:
        here = choice.key == self.in_use
        text = Text()
        text.append(f"{glyphs.g('on') if here else glyphs.g('off')} ",
                    style=theme.color("magenta") if here else theme.color("mist"))
        text.append(choice.title, style=f"bold {theme.color('glass')}")
        text.append(f"  {choice.key}{'  in use' if here else ''}\n", style=theme.color("ash"))
        text.append(f"  {choice.description}\n", style=theme.color("glass"))
        text.append(f"  RAM {choice.ram} GB or more, disk {choice.disk} GB or more", style=theme.color("ash"))
        return text

    def selected(self):
        index = self.query_one("#edition-list", OptionList).highlighted
        return self.choices[index] if index is not None and index < len(self.choices) else None

    def refresh_button(self) -> None:
        choice = self.selected()
        button = self.query_one("#switch-edition", Button)
        note = Text()
        if not self.app.backend.linux_host():
            note.append("macOS and Windows run the MAC_WIN edition, there is no other to switch to.",
                        style=theme.color("ash"))
            button.disabled = True
        else:
            button.disabled = choice is None or choice.key == self.in_use
        button.label = f"Switch to {choice.title}" if choice is not None else "Switch"
        self.query_one("#edition-note", Static).update(note)

    def on_option_list_option_highlighted(self, event) -> None:
        if event.option_list.id == "edition-list":
            self.refresh_button()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "open-customizer":
            self.app.action_customize()
        elif event.button.id == "switch-edition":
            choice = self.selected()
            if choice is not None:
                self.app.switch_edition(choice.key)


class SettingsForm(VerticalScroll, inherit_bindings=False):
    """The settings of one tab; up and down go to the page, they move between the settings."""

    BINDINGS = [
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("home", "scroll_home", show=False),
        Binding("end", "scroll_end", show=False),
    ]


class SettingsPane(Vertical):
    """The settings of this T-Pot in .env, checked against the schema while you type.

    One tab per group (the sections of the schema here, the honeypots on the LLM page);
    a subclass picks its keys with wanted() and groups them with group_of().
    """

    PREFIX = "settings"
    ALL_TOGGLE = True           # the checkbox for tpot env list --all

    BINDINGS = [
        Binding("down", "move(1)", "Next setting", show=False),
        Binding("up", "move(-1)", "Previous setting", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id=f"{self.PREFIX}-head", classes="settings-head"):
            yield Static("", id=f"{self.PREFIX}-status", classes="settings-status")
            if self.ALL_TOGGLE:
                yield Checkbox("All settings", False, id=f"{self.PREFIX}-all",
                               tooltip="also the ones of services that are not in your edition and of the other "
                                       "T-Pot type (tpot env list --all)")
            yield Button("Revert", id=f"{self.PREFIX}-revert")
            yield Button("Save", id=f"{self.PREFIX}-save", variant="primary", disabled=True)
        yield from self.extra()
        yield TabbedContent(id=f"{self.PREFIX}-tabs", classes="settings-tabs")

    def extra(self) -> ComposeResult:
        """Widgets between the head and the tabs, for subclasses."""
        return iter(())

    def wanted(self, rule) -> bool:
        return True

    def group_of(self, rule) -> str:
        return rule.section

    def groups(self):
        from tpotctl import envschema
        return envschema.SECTIONS

    async def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == f"{self.PREFIX}-all":
            event.stop()
            self.include_all = event.value
            await self.reload()

    async def on_mount(self) -> None:
        self.include_all = False
        self.current = None
        self.rows = {}
        self.unlocked = set()
        await self.reload()

    async def reload(self, keep: Optional[Dict[str, str]] = None, keep_unlocked: Iterable[str] = ()) -> None:
        """.env anew; keep: unsaved changes that stay (a save on the other settings page), keep_unlocked:
        the keys of them that were unlocked. One after the other: a second reload while the rows of
        the first one still mount would remove them under their feet."""
        if not hasattr(self, "_reloading"):
            import asyncio
            self._reloading = asyncio.Lock()
        async with self._reloading:
            await self._reload(keep, keep_unlocked)

    async def _reload(self, keep: Optional[Dict[str, str]], keep_unlocked: Iterable[str]) -> None:
        from tpotctl.settings import SettingsError
        from tpotctl.widgets.fields import SettingRow
        import asyncio
        from textual.widgets import Select
        tabs = self.query_one(f"#{self.PREFIX}-tabs", TabbedContent)
        active = tabs.active
        # a Select still mounting (the reload before) cannot be removed under its feet
        for _wait in range(200):
            if all(select.is_mounted for select in tabs.query(Select)):
                break
            await asyncio.sleep(0.01)
        await tabs.clear_panes()
        # the tab headers go a moment after the panes, a new one of the same id would clash
        for _wait in range(100):
            if not tabs.query("ContentTab"):
                break
            await asyncio.sleep(0.01)
        self.rows = {}
        try:
            self.current = self.app.backend.settings()
        except (SettingsError, OSError) as err:
            self.current = None
            self.query_one(f"#{self.PREFIX}-status", Static).update(Text(str(err), style=theme.color("error")))
            return
        self.draft = dict(self.current.values)
        self.draft.update(keep or {})
        self.unlocked = set()
        by_section = {}
        for rule in self.current.relevant(include_all=self.include_all, offered=True):
            if not self.wanted(rule):
                continue
            row = SettingRow(rule, self.draft.get(rule.key, ""), self.current.why_fixed(rule.key),
                             unlockable=self.current.can_unlock(rule.key), note=self.current.not_here(rule))
            self.rows[rule.key] = row
            by_section.setdefault(self.group_of(rule), []).append(row)
        for section, title in self.groups():
            if by_section.get(section):
                await tabs.add_pane(TabPane(title, SettingsForm(*by_section[section], classes="settings-form"),
                                            id=f"tab-{section}"))
        if active and active in [f"tab-{section}" for section in by_section]:
            tabs.active = active
        for key in keep_unlocked:
            if key in self.rows:
                await self.unlock(self.rows[key])
        self.call_after_refresh(self.check)
        self.loaded()

    def loaded(self) -> None:
        """After a reload, for subclasses."""

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
        problems = self.current.problems(self.draft, offered=True)
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
                group = self.group_of(row.rule)
                errors_in[group] = errors_in.get(group, 0) + 1
        tabs = self.query_one(f"#{self.PREFIX}-tabs", TabbedContent)
        for section, title in self.groups():
            try:
                tab = tabs.get_tab(f"tab-{section}")
            except Exception:      # no keys of that section here
                continue
            label = Text(title)
            if errors_in.get(section):
                label.append(f" {glyphs.g('fail')} {errors_in[section]}", style=f"bold {theme.color('error')}")
            tab.label = label
        blocking = self.current.blocking(problems, changes)
        self.query_one(f"#{self.PREFIX}-save", Button).disabled = not changes or bool(blocking)
        self.query_one(f"#{self.PREFIX}-revert", Button).disabled = not changes
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
        self.query_one(f"#{self.PREFIX}-status", Static).update(status)

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
        row = event.row
        if event.detect:
            self.detect(row)
            return
        self.app.push_screen(pickers.picker_for(row, self.draft, self.current.schema),
                             lambda value: row.set_value(value) if value is not None else None)

    @work(thread=True, exclusive=True, group="detect")
    def detect(self, row) -> None:
        from tpotctl.screens import pickers
        found = pickers.detect_for(row.rule)
        self.app.call_from_thread(self.detected, row, found)

    def detected(self, row, found: str) -> None:
        from tpotctl.screens import pickers
        if not found:
            self.app.notify("Nothing detected on this host.", title=row.rule.title, severity="warning")
            return
        row.set_value(found)
        self.app.notify(pickers.detected_note(row.rule, found), title=row.rule.title)

    def visible_rows(self):
        active = self.query_one(f"#{self.PREFIX}-tabs", TabbedContent).active
        return [row for row in self.rows.values()
                if row.is_mounted and row.display and f"tab-{self.group_of(row.rule)}" == active]

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
            self.query_one(f"#{self.PREFIX}-tabs", TabbedContent).query_one(Tabs).focus()
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
        self.query_one(f"#{self.PREFIX}-tabs", TabbedContent).active = f"tab-{self.group_of(row.rule)}"

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
        if event.button.id == f"{self.PREFIX}-revert":
            await self.reload()
        elif event.button.id == f"{self.PREFIX}-save":
            saved = self.changes()
            try:
                self.current.change(saved, unlocked=self.unlocked)
            except SettingsError as err:
                self.app.notify(str(err), title="Not saved", severity="error", timeout=10)
                return
            await self.reload()
            await self.app.settings_saved(self, set(saved))
            if self.app.backend.linux_host():
                self.app.push_screen(ConfirmDialog("Saved. Restart T-Pot now, so that it uses the new settings?",
                                                   yes="Restart", no="Later"),
                                     lambda yes: self.app.runner(ops.service_command("restart")) if yes else None)
            else:
                self.app.notify("Saved, restart T-Pot to use the new settings.", title="Settings")



class LlmPane(SettingsPane):
    """The LLM backends of Beelzebub and Galah: find an Ollama, choose the model, test it.

    The same keys as on the Settings page, one tab per honeypot; shown for every
    edition, a honeypot that is not in it can be added with the customizer.
    """

    PREFIX = "llm"
    ALL_TOGGLE = False
    SERVICES = [("beelzebub", "Beelzebub"), ("galah", "Galah")]
    URL_KEY = {"beelzebub": "BEELZEBUB_LLM_HOST", "galah": "GALAH_LLM_SERVER_URL"}

    def wanted(self, rule) -> bool:
        return rule.key.startswith(("BEELZEBUB_LLM_", "GALAH_LLM_"))

    def group_of(self, rule) -> str:
        return rule.services[0] if rule.services else "beelzebub"

    def groups(self):
        return self.SERVICES

    def extra(self) -> ComposeResult:
        yield Static("", id="llm-notice")
        with Horizontal(id="llm-tools", classes="actions"):
            yield Button("Find Ollama", id="llm-find")
            yield Button("Scan the network", id="llm-scan")
            yield Button("Test the model", id="llm-test", variant="primary")
            yield Button("Add to the edition", id="llm-add")
        yield Static("", id="llm-result")

    def service(self) -> str:
        active = self.query_one("#llm-tabs", TabbedContent).active or "tab-beelzebub"
        return active[len("tab-"):]

    def title_of(self, service: str) -> str:
        return dict(self.SERVICES).get(service, service)

    def loaded(self) -> None:
        self.call_after_refresh(self.show_service)

    def on_tabbed_content_tab_activated(self, event) -> None:
        if event.tabbed_content.id == "llm-tabs":
            self.query_one("#llm-result", Static).update("")
            self.show_service()

    def show_service(self) -> None:
        if self.current is None:
            return
        service = self.service()
        title = self.title_of(service)
        absent = service not in self.current.services
        notice = Text()
        if absent:
            notice.append(f"{title} is not in your edition. ", style=theme.color("warn"))
            notice.append("Its settings are kept for when you add it, i.e. with the customizer.",
                          style=theme.color("mist"))
        else:
            notice.append(f"{title} runs in your edition and uses these settings after a restart of T-Pot.",
                          style=theme.color("mist"))
        self.query_one("#llm-notice", Static).update(notice)
        add = self.query_one("#llm-add", Button)
        add.display = absent
        add.label = f"Add {title} to the edition"

    def llm_of(self, service: str):
        from tpotctl import llm
        return llm.service_settings(service, self.draft, self.current.schema)

    def result(self, text: Text) -> None:
        self.query_one("#llm-result", Static).update(text)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        if not button.startswith("llm-") or button in ("llm-save", "llm-revert"):
            await super().on_button_pressed(event)
            return
        event.stop()
        if self.current is None:
            return
        service = self.service()
        if button == "llm-find":
            self.pick_ollama(service)
        elif button == "llm-scan":
            self.ask_scan(service)
        elif button == "llm-test":
            self.result(Text(f"Asking the model of {self.title_of(service)} ...", style=theme.color("mist")))
            self.run_test(service)
        elif button == "llm-add":
            self.app.customize_with(service)

    def pick_ollama(self, service: str, find=None, title: str = "Ollama") -> None:
        from tpotctl.screens import pickers
        key = self.URL_KEY[service]
        row = self.rows.get(key)
        picker = pickers.ollama_picker(self.draft.get(key, ""), service, self.draft, find=find, title=title)

        def chosen(value) -> None:
            if value is None:
                return
            if row is not None and row.is_mounted:
                row.display = True
                row.set_value(value)
            else:
                self.draft[key] = value
                self.check()
        self.app.push_screen(picker, chosen)

    def ask_scan(self, service: str) -> None:
        from tpotctl import llm
        try:
            address = llm.scan_network()
        except Exception:      # no network to scan here
            address = ""
        targets = llm.scan_targets(address) if address else []
        if not targets:
            self.result(Text(f"{glyphs.g('fail')} This host has no IPv4 network to scan.", style=theme.color("error")))
            return
        import ipaddress
        own = ipaddress.ip_interface(address)
        network = own.network if own.network.prefixlen >= 24 else ipaddress.ip_network(f"{own.ip}/24", strict=False)

        def answered(yes: bool) -> None:
            if yes:
                self.pick_ollama(service, find=lambda: llm.scan(targets), title=f"Ollama in {network}")

        self.app.push_screen(ConfirmDialog(
            f"Scan {network} on port 11434?",
            Text(f"This host connects to all {len(targets)} addresses of {network} on port 11434. On a honeypot "
                 f"that can look like an attack to the IDS of your network.", style=theme.color("glass")),
            yes="Scan", no="Back"), answered)

    @work(thread=True, exclusive=True, group="llm-test")
    def run_test(self, service: str) -> None:
        from tpotctl import llm
        settings = self.llm_of(service)
        found = llm.test(settings["provider"], settings["url"], settings["model"], settings["api_key"])
        self.app.call_from_thread(self.tested, service, settings, found)

    def tested(self, service: str, settings, found) -> None:
        text = Text()
        if found.ok:
            text.append(f"{glyphs.g('ok')} {settings['model']} answered in {found.seconds:.1f} s: ",
                        style=f"bold {theme.color('ok')}")
            text.append(found.answer, style=theme.color("glass"))
        else:
            text.append(f"{glyphs.g('fail')} {found.problem}", style=theme.color("error"))
        text.append("\nAsked from this host; the honeypot asks from its container, where localhost is the "
                    "container itself.", style=theme.color("ash"))
        self.result(text)

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
            yield Button("Edit", id="sensor-edit")
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
        elif button == "sensor-edit" and self.selected() and self.registry is not None:
            from tpotctl.screens.dialogs import SensorEditDialog
            name, registry = self.selected(), self.registry
            self.app.push_screen(SensorEditDialog(registry.get(name), lambda **values: registry.update(name, **values)),
                                 lambda values: self.load() if values else None)
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



class ChecksPane(Vertical):
    """Checks of a running T-Pot: probe the honeypots, test the Attack Map pipeline."""

    def compose(self) -> ComposeResult:
        yield Static("", id="checks-info", classes="info")
        with Horizontal(classes="actions"):
            yield Input(placeholder="host to probe", id="check-host", compact=True)
            yield Button("Probe the honeypots", id="check-honeypots", variant="primary")
        with Horizontal(classes="actions"):
            yield Button("Test the Attack Map pipeline", id="check-pipeline")
            yield Button("Dry run", id="check-pipeline-dry")

    def on_mount(self) -> None:
        self.show()
        self.query_one("#check-host", Input).value = self.app.backend.host_address()

    def show(self) -> None:
        text = Text()
        text.append("Probe the honeypots", style=f"bold {theme.color('glass')}")
        text.append("  sends a few service requests and scans every published port with nmap (hptest.sh). Probe "
                    "this host or another T-Pot; the probes show up in Kibana as attacks.\n\n",
                    style=theme.color("mist"))
        text.append("Test the Attack Map pipeline", style=f"bold {theme.color('glass')}")
        text.append("  appends test events to the logs of Cowrie, Dionaea, Honeytrap and RDPy and follows them "
                    "through Logstash, Elasticsearch and Redis to the WebSocket of the Attack Map "
                    "(attackmap_pipeline_test.sh). The events stay in Kibana; the dry run only shows them.",
                    style=theme.color("mist"))
        self.query_one("#checks-info", Static).update(text)

    def enter(self) -> None:
        self.query_one("#check-host", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from tpotctl import cli
        from tpotctl.screens.task import Task
        if event.button.id == "check-honeypots":
            host = self.query_one("#check-host", Input).value.strip()
            self.app.run_task(Task(f"Probe the honeypots{' on ' + host if host else ''}",
                                   [cli.HPTEST] + ([host] if host else []), cwd=os.path.expanduser("~"),
                                   become="-B", done="The probes are through, Kibana shows them."))
        elif event.button.id == "check-pipeline-dry":
            self.app.run_task(Task("Attack Map pipeline, dry run", [cli.PIPELINE, "--dry-run"],
                                   cwd=os.path.expanduser("~"), autostart=True))
        elif event.button.id == "check-pipeline":
            def confirmed(yes: bool) -> None:
                if yes:
                    self.app.run_task(Task("Test the Attack Map pipeline", [cli.PIPELINE],
                                           cwd=os.path.expanduser("~"), become="--become-file", autostart=True,
                                           done="Every test event reached the Attack Map."))
            self.app.push_screen(ConfirmDialog(
                "Inject test events into the honeypot logs?",
                Text("They become ordinary events in Kibana and the Attack Map and stay there.",
                     style=theme.color("glass")), yes="Inject and test", no="Back"), confirmed)

class UpdatePane(Vertical):

    def compose(self) -> ComposeResult:
        yield Static("", id="update-info", classes="info")
        with Horizontal(classes="actions"):
            yield Button("Update", id="run-update", variant="primary")
            yield Button("Update and start", id="run-update-start")
            yield Button("Restore a backup", id="run-restore")
            yield Button("Uninstall ...", id="run-uninstall", variant="error")
        with Horizontal(classes="actions"):
            yield Checkbox("Full backup with data/ (for a newer Elastic Stack)", False, id="update-full")
            yield Button("Refresh tpot's packages", id="run-setup")
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
        full = ["--full"] if self.query_one("#update-full", Checkbox).value else []
        if event.button.id == "run-update":
            self.app.run_update(["-y"] + full)
        elif event.button.id == "run-update-start":
            self.app.run_update(["-y", "-s"] + full)
        elif event.button.id == "run-restore":
            self.app.run_restore()
        elif event.button.id == "run-setup":
            from tpotctl.screens.task import Task
            self.app.run_task(Task("Refresh the Python packages of tpot", [LAUNCHER, "setup"],
                                   intro="tpot setup installs the pinned packages of tpot into its venv again.",
                                   done="The packages of tpot are fresh, start tpot anew to use them.",
                                   restart_tpot=True))
        elif event.button.id == "run-uninstall":
            from tpotctl.screens.uninstall import UninstallScreen
            # uninstall.sh removes tpot itself, the app ends and hands over to it
            self.app.push_screen(UninstallScreen(), lambda target: self.app.exit(("uninstall", target))
                                 if target else None)



class LockedPane(Vertical):
    """A page that does not work here: why, and what does instead."""

    def __init__(self, key: str, title: str, why: str, **kwargs):
        super().__init__(**kwargs)
        self.key, self.title, self.why = key, title, why

    def compose(self) -> ComposeResult:
        yield Static("", classes="locked-title")
        yield Static("", classes="locked-why")
        yield Static("", classes="locked-instead")

    def on_mount(self) -> None:
        self.show()

    def show(self) -> None:
        self.query_one(".locked-title", Static).update(
            Text(f"{glyphs.g('locked')} {self.title}", style=f"bold {theme.color('ash')}"))
        self.query_one(".locked-why", Static).update(Text(f"{self.title} {self.why}", style=theme.color("glass")))
        text = Text()
        if self.key in INSTEAD:
            text.append("Instead: ", style=theme.color("mist"))
            text.append(f"{INSTEAD[self.key]}\n\n", style=theme.color("key"))
        working = [title for key, title, *_rest in PANES if key not in self.app.locked]
        text.append("What works here: ", style=theme.color("mist"))
        text.append(", ".join(working), style=theme.color("glass"))
        self.query_one(".locked-instead", Static).update(text)

# the 4th field says where a page works: "any" host, a T-Pot "host" (Linux with systemd),
# a "hive" (not a SENSOR) or both; elsewhere it stays in the menu, locked (LockedPane)
PANES = [
    ("status", "Status", StatusPane, "host"),
    ("edition", "Edition & services", EditionPane, "any"),
    ("settings", "Settings", SettingsPane, "any"),
    ("llm", "LLM", LlmPane, "any"),
    ("users", "Web users", UsersPane, "hive"),
    ("sensors", "Sensors", SensorsPane, "host+hive"),
    ("images", "Images", ImagesPane, "host"),
    ("checks", "Checks", ChecksPane, "host"),
    ("update", "Update & backup", UpdatePane, "host"),
]
NOT_HERE = "needs a T-Pot host: Linux with systemd. On macOS and Windows T-Pot runs in Docker Desktop."
ON_A_SENSOR = {"users": "a SENSOR has no web UI, its events go to its HIVE.",
               "sensors": "sensors are managed on their HIVE, this is a SENSOR."}
# what does the job of a locked page on the command line or elsewhere
INSTEAD = {"status": "docker compose ps, or tpot status on the T-Pot host",
           "sensors": "tpot sensors on the HIVE", "images": "docker images",
           "checks": "tpot check on the T-Pot host", "update": "git pull in ~/tpotce, then tpot customize",
           "users": "tpot users on the HIVE"}
SHORT = {"edition": "Edition", "users": "Users", "update": "Update"}
REPAINT = {"status": "repaint", "edition": "show", "settings": "check", "llm": "check", "users": "show", "sensors": "load",
           "images": "load", "checks": "show", "update": "show"}


def menu_text(key: str, title: str, active: bool, locked: bool = False) -> Text:
    text = Text()
    if locked:
        # dimmed, but readable on the magenta of the page you are on
        style = f"bold {theme.color('glass')}" if active else theme.color("ash")
        text.append(f"{glyphs.g('locked')} ", style=style)
        text.append(title, style=style)
        return text
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

    def __init__(self, backend: Optional[Backend] = None, runner: Optional[Callable] = None, splash: bool = False,
                 engine: Optional[Callable] = None):
        super().__init__()
        self.backend = backend or Backend()
        self.runner = runner or Runner(self)
        self.engine = engine
        self.splash = splash
        apply_theme(self)
        self.panes = list(PANES)
        self.locked = {key: why for key, *_rest in PANES for why in [self.why_locked(key)] if why}

    def compose(self) -> ComposeResult:
        yield TpotHeader(id="header")
        with Horizontal(id="body"):
            yield ListView(*[ListItem(Label(menu_text(key, title, False, key in self.locked), classes="menu-long"),
                                      Label(menu_text(key, SHORT.get(key, title), False, key in self.locked),
                                            classes="menu-short"),
                                      id=f"menu-{key}")
                             for key, title, _cls, _needs in self.panes], id="sidebar")
            with ContentSwitcher(initial=self.panes[0][0], id="panes"):
                for key, title, cls, _needs in self.panes:
                    if key in self.locked:
                        # the page itself is not built: no docker, systemctl or Elasticsearch here
                        yield LockedPane(key, title, self.locked[key], id=key, classes="pane")
                    else:
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
        if "status" in self.locked:
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
        for key, title, _cls, _needs in self.panes:
            item = self.query_one(f"#menu-{key}", ListItem)
            locked = key in self.locked
            item.query_one(".menu-long", Label).update(menu_text(key, title, key == current, locked))
            item.query_one(".menu-short", Label).update(menu_text(key, SHORT.get(key, title), key == current, locked))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None and event.item.id:
            self.query_one(ContentSwitcher).current = event.item.id[len("menu-"):]
            self.paint_menu()

    def why_locked(self, key: str) -> str:
        """"" where the page works, else why it does not."""
        needs = dict((k, n) for k, _t, _c, n in PANES)[key]
        if "host" in needs and not self.backend.linux_host():
            return NOT_HERE
        if "hive" in needs and self.backend.tpot_type() == "SENSOR":
            return ON_A_SENSOR.get(key, "this is a SENSOR.")
        return ""

    def check_settings(self) -> None:
        """tpot env check: the Settings page with what T-Pot would say about .env."""
        self.goto("settings")
        try:
            current = self.backend.settings()
            problems = current.problems()
        except Exception as err:      # SettingsError, OSError: the text is for the user
            self.notify(str(err), title="Settings", severity="error", timeout=8)
            return
        errors = [p for p in problems if p.level == "error"]
        if errors:
            self.notify("; ".join(f"{p.key}: {p.text}" for p in errors[:3]) + (" ..." if len(errors) > 3 else ""),
                        title=f"T-Pot would not start, {len(errors)} problem{'s' if len(errors) > 1 else ''}",
                        severity="error", timeout=10)
        else:
            warnings = len(problems)
            self.notify(f"T-Pot would start with these settings{f', {warnings} warnings' if warnings else ''}.",
                        title="Settings", timeout=6)

    def start_install(self) -> None:
        """tpot install from the palette: the assistant needs the terminal, the menu ends for it."""
        self.exit(("install", None))

    def not_here(self, what: str) -> None:
        self.notify(f"{what} {NOT_HERE}", title="Not on this host", severity="warning", timeout=6)

    def task_running(self) -> bool:
        from tpotctl.screens.task import TaskScreen
        return any(isinstance(screen, TaskScreen) and screen.busy for screen in self.screen_stack)

    async def action_quit(self) -> None:
        """Not while a script runs: quitting would kill it half way."""
        if self.task_running():
            self.notify("A script is still running, tpot ends when it is through.", title="Not now",
                        severity="warning", timeout=5)
            return
        await super().action_quit()

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
            getattr(self.query_one(f"#{key}"), "show" if key in self.locked else REPAINT[key])()

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

    def run_update(self, args: List[str]) -> None:
        """update.sh in the task screen; the checkout below this app changes, so tpot starts anew."""
        from tpotctl.screens.task import Task
        intro = Text("update.sh stops T-Pot, writes a backup to ~/tpot_backups, pulls the release of your "
                     "branch and puts your edition and settings back.", style=theme.color("glass"))
        if "-s" in args:
            intro.append(" T-Pot starts again afterwards.", style=theme.color("glass"))
        self.run_task(Task("Update T-Pot", ops.script_command("update.sh", args), become="-B", intro=intro,
                           done="T-Pot is updated, start tpot anew for its new version.", restart_tpot=True))

    def run_restore(self) -> None:
        from tpotctl.screens.restore import RestoreScreen
        from tpotctl.screens.task import Task

        def chosen(choice) -> None:
            if not choice:
                return
            path, groups = choice
            # the task screen shows what is replaced, Run is the last yes
            intro = Text(f"restore.sh brings back from {os.path.basename(path)}:\n", style=theme.color("glass"))
            for group in groups:
                intro.append(f"  {glyphs.g('bullet')} {ops.GROUP_TEXT[group].rstrip('?')}\n",
                             style=theme.color("glass"))
            if "git" in groups:
                intro.append("The checkout is reset to the commit of the backup (git reset --hard).\n",
                             style=theme.color("warn"))
            if "data" in groups:
                intro.append("The files in data/ are replaced by the ones of the archive; with a full archive "
                             "that is the Elasticsearch data too, the events since the backup are gone.\n",
                             style=theme.color("warn"))
            intro.append("T-Pot is stopped for it.", style=theme.color("mist"))
            self.run_task(Task("Restore a backup", ops.script_command("restore.sh", ["-f", path, "-g",
                                                                                      ",".join(groups)]),
                               become="-B", intro=intro, done="The backup is restored.",
                               restart_tpot=bool({"git", "patch"} & set(groups))),
                          lambda _code: self.query_one("#update", UpdatePane).show())

        self.push_screen(RestoreScreen(self.backend.backup_infos), chosen)

    def llm_action(self, what: str, service: str) -> None:
        """From the palette: the LLM page, the tab of the honeypot, then find or test."""
        self.goto("llm")
        pane = self.query_one("#llm", LlmPane)
        pane.query_one("#llm-tabs", TabbedContent).active = f"tab-{service}"

        def run() -> None:
            if what == "find":
                pane.pick_ollama(service)
            else:
                pane.query_one("#llm-test", Button).press()
        self.call_after_refresh(run)

    async def settings_saved(self, source, saved_keys: Set[str]) -> None:
        """The Settings and the LLM page show the same .env, a save on one reloads the other. Its
        unsaved changes stay, but a value just saved replaces an unsaved one of the same key."""
        titles = dict((key, title) for key, title, *_rest in PANES)
        for pane in self.query(SettingsPane):
            if pane is source:
                continue
            own = pane.changes()
            keep = {key: value for key, value in own.items() if key not in saved_keys}
            lost = sorted(set(own) & saved_keys)
            await pane.reload(keep=keep, keep_unlocked=pane.unlocked & set(keep))
            if lost:
                self.notify(f"{', '.join(lost)}: the value just saved replaces the unsaved one on the "
                            f"{titles.get(pane.id, pane.id)} page", title="Settings", timeout=8)

    def customize_with(self, service: str) -> None:
        """The customizer with a service added to the edition in use."""
        from tpotctl.screens.customizer import CustomizerScreen, core
        catalog = core.Catalog()
        edition, selection = core.current_edition()
        if not (selection and selection.base in catalog.editions):
            selection = core.Selection(edition if edition in catalog.editions else "STANDARD")
        if service not in selection.add:
            selection.add.append(service)
        if service in selection.remove:
            selection.remove.remove(service)
        self.push_screen(CustomizerScreen(catalog, selection, core.DEFAULT_MAX_NETWORKS),
                         lambda chosen: self.customized(catalog, chosen))

    def run_task(self, task, then: Optional[Callable] = None) -> None:
        """A script or tpot command in the task screen; "restart" ends the app to start it anew."""
        from tpotctl.engine import Engine
        from tpotctl.screens.task import TaskScreen

        def ended(result) -> None:
            if result == "restart":
                self.exit("restart")
            elif then is not None:
                then(result)

        self.push_screen(TaskScreen(task, engine=self.engine or Engine, sudo_mode=self.backend.sudo_mode()), ended)

    def switch_edition(self, key: str) -> None:
        from tpotctl import editions
        from tpotctl.screens.task import Task
        try:
            plan = self.backend.edition_plan(key)
        except editions.EditionError as err:
            self.notify(str(err), title="Edition", severity="error", timeout=8)
            return
        command = [LAUNCHER, "edition", "set", key, "-y"]
        body = Text()
        for warning in plan.warnings:
            body.append(f"{glyphs.g('warn')} {warning}\n", style=theme.color("warn"))
        body.append("T-Pot is down while it switches, the images of the new edition are pulled when it starts.",
                    style=theme.color("mist"))

        def run(secrets) -> None:
            task = Task(f"Switch to the {plan.target.title} edition", command + (
                ["--web-user", secrets[0]] if secrets else []), become="--become-file",
                secrets={"--password-file": secrets[1]} if secrets else {}, autostart=True,
                done=f"T-Pot runs the {plan.target.title} edition.", restart_tpot=plan.target.role != "HIVE"
                or bool(plan.env_changes))
            self.run_task(task, lambda _code: self.after_switch())

        def confirmed(yes: bool) -> None:
            if not yes:
                return
            if plan.needs_web_user:
                from tpotctl import users
                self.push_screen(UserDialog("A HIVE needs a web user", check_name=users.check_name,
                                            weakness=users.weakness),
                                 lambda result: run(result) if result else None)
            else:
                run(None)

        self.push_screen(ConfirmDialog(f"Switch to the {plan.target.title} edition?", body,
                                       yes=f"Switch to {plan.target.title}"), confirmed)

    def after_switch(self) -> None:
        self.query_one("#edition", EditionPane).show()
        self.load_header()

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
    if isinstance(result, tuple) and result[0] == "uninstall":
        from tpotctl.screens.uninstall import run_handover
        run_handover(result[1])
    if isinstance(result, tuple) and result[0] == "install":
        os.execv(sys.executable, [sys.executable, LAUNCHER, "install"])
    return 0
