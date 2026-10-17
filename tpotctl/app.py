"""The tpot menu: status, edition & services, settings, users, sensors, images, update.

Data comes from a Backend (tpotctl.ops on a host, fakes in the tests). Commands that
need the real terminal (sudo, update.sh, restore.sh) go through a Runner, which
suspends the app while they run. The look (theme, icons, logo) is in theme.py,
glyphs.py and logo.py, the user's choice of it in prefs.py.
"""

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Button, Checkbox, ContentSwitcher, DataTable, Footer, Input, Label, ListItem, ListView,
                             OptionList, Static, Switch, TabbedContent, TabPane)
from textual.widgets.option_list import Option

from tpotctl.widgets import nav
from tpotctl.widgets.nav import DialogTitle, NavDataTable, NavInput, NavModal, NavOptionList, NavScroll
from tpotctl import glyphs, logo, ops, prefs, theme
from tpotctl.bootstrap import REPO_DIR
from tpotctl.commands import TpotCommands
from tpotctl.screens.dialogs import ConfirmDialog, QuitDialog, SensorDialog, UserDialog
from tpotctl.theme import apply as apply_theme
from tpotctl.ops import cell_state
from tpotctl.widgets.comb import Honeycomb
from tpotctl.widgets.header import TpotHeader, lead

LAUNCHER = os.path.join(REPO_DIR, "tpot")
CUSTOM_OUTPUT = os.path.join(REPO_DIR, "docker-compose-custom.yml")


class Backend:
    """What the app reads, tpotctl.ops on a T-Pot host."""

    def linux_host(self) -> bool:
        return ops.linux_host()

    def host_ostype(self) -> str:
        return ops.host_ostype()

    def config_stamp(self):
        return ops.config_stamp()

    def status(self) -> ops.Status:
        return ops.status()

    def containers(self) -> List[ops.Container]:
        # T-Pot's services only, not started ones as placeholders; other containers of the host
        # stay out (tpot ps lists them)
        return ops.tpot_containers(ops.containers(), ops.compose_names())

    def images(self) -> List[ops.Image]:
        return ops.images()

    def backups(self) -> List[str]:
        return ops.backups()

    def backup_infos(self):
        return [ops.backup_info(path) for path in ops.backups()]

    def settings(self):
        from tpotctl import settings
        # TPOT_OSTYPE is compared with the host, as tpotinit does (one docker call per run)
        return settings.load(host_ostype=self.host_ostype())

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
        return events.top_sources(hours=24, size=10)

    def host_address(self) -> str:
        """The address of this host towards the internet, the default target of the honeypot probe."""
        return ops.host_address()

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

    def retention_status(self):
        from tpotctl import elastic, settings as tsettings
        return elastic.status(tsettings.load().values.get("TPOT_ES_RETENTION", ""))

    def retention_plan(self, value: str, force: bool = False):
        from tpotctl import elastic
        return elastic.plan(value, force=force)

    def apply_retention(self, plan) -> None:
        from tpotctl import elastic
        elastic.apply(plan)

    def set_retention(self, value: str) -> None:
        from tpotctl import settings as tsettings
        tsettings.load().change({"TPOT_ES_RETENTION": value})

    # the Kibana objects (tpotctl.kibana_objects, as tpot elastic export / import / restore do it); each may
    # wait up to a minute for another import (kibana_objects.BUSY), the page calls them in a worker

    def kibana_status(self):
        """cli.KibanaState: the mode, a pending first import, the plan or why Kibana cannot be read (5 s)."""
        from tpotctl import cli
        return cli.kibana_state(ops.env_values())

    def kibana_preview(self):
        """The plan of Kibana as it is now, before the question of Update and Overwrite all."""
        from tpotctl import kibana_objects as ko
        return ko.preview(*ops.kibana_dirs())

    def kibana_update(self, overwrite: bool, confirmed=None):
        """A one-off import as the mode update or overwrite does it (the mode in .env stays): (plan, result,
        export path). confirmed: the objects of yours the question named, for overwrite."""
        from tpotctl import kibana_objects as ko
        data, objects = ops.kibana_dirs()
        return ko.run_apply("overwrite" if overwrite else "update", data, objects, ops.kibana_export_dir(),
                            confirmed=confirmed)

    def kibana_export(self):
        """Every object into ~/tpot_backups: (path, count)."""
        from tpotctl import cli
        return cli.kibana_export()

    def kibana_check_export(self, path: str) -> int:
        """The objects of an export as a restore reads them, before its question (KibanaError if none)."""
        from tpotctl import kibana_objects as ko
        return len(ko.read_export(path))

    def kibana_restore(self, path: str):
        """(export path, result) of a restore of `path`."""
        from tpotctl import kibana_objects as ko
        return ko.run_restore(path, ops.kibana_dirs()[0], ops.kibana_export_dir())

    def kibana_exports(self):
        """The exports of the T-Pot Manager and of tpotinit (ops.KibanaExport), newest first."""
        return ops.kibana_export_infos(data_dir=ops.kibana_dirs()[0])

    def set_kibana_mode(self, value: str) -> None:
        from tpotctl import settings as tsettings
        tsettings.load().change({"TPOT_KIBANA_OBJECTS": value})

    def check_data_loss(self, args: List[str]):
        return ops.check_data_loss(args)

    def check_result(self):
        """The last check (tpotctl.checks.run.read_result): None before the first, ResultError when it
        cannot be read."""
        from tpotctl.checks import run as trun
        return trun.read_result()

    def ews_state(self):
        """(state, age) of the hold of ewsposter, ewshold.ews_state of the data folder."""
        from tpotctl import system
        from tpotctl.checks import ewshold
        return ewshold.ews_state(system.data_path(ops.env_values(), REPO_DIR))


class Runner:
    """Run a command in the normal terminal, the app is suspended meanwhile."""

    def __init__(self, app: App):
        self.app = app

    def __call__(self, command: List[str], cwd: Optional[str] = None) -> int:
        """ctrl+c reaches the child as well and it decides (bootstrap.wait_child waits on, 128 + n when
        signal n ended it); at the question afterwards it counts as Enter, no traceback in the menu.

        While suspended the T-Pot Manager takes SIGINT itself: on Python >= 3.11 Textual runs on
        asyncio.run, whose handler would cancel the main task of the app, so the first ctrl+c during
        a command would end the T-Pot Manager instead of coming back to the menu."""
        from tpotctl import bootstrap
        asking = [False]

        def on_sigint(_signum, _frame) -> None:
            if asking[0]:                       # once: a second ctrl+c cannot escape the except below
                asking[0] = False
                raise KeyboardInterrupt
        with bootstrap.sigint_to(on_sigint), self.app.suspend():
            print(f"\n$ {' '.join(command)}\n", flush=True)
            try:
                code = bootstrap.wait_child(subprocess.Popen(command, cwd=cwd))
            except OSError as err:
                print(err)
                code = 127
            try:
                asking[0] = True
                input(f"\n[exit code {code}] Press Enter to return to the T-Pot Manager ... ")
            except (EOFError, KeyboardInterrupt):
                print(flush=True)
            asking[0] = False
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
    text.append_text(meter("Disk", disk.percent if disk else None,
                           f"{human(disk.used)} of {human(disk.total)}" if disk else ""))
    return text


def ago(seconds: float) -> str:
    return "just now" if seconds < 1 else f"{ops.duration(seconds, short=True)} ago"


def honeypot_bars(top, width: int) -> Text:
    """A bar per honeypot, by its share of the busiest one: name, bar, count."""
    text = Text()
    if not top:
        return text
    names = min(max(len(name) for name, _count in top), 14)
    counts = max(len(f"{count:,}") for _name, count in top)
    room = max(width - names - counts - 2, 4)
    most = max(count for _name, count in top) or 1
    for name, count in top:
        text.append(f"{name[:names]:<{names}} ", style=theme.color("glass"))
        filled = max(1, round(count / most * room)) if count else 0
        text.append(glyphs.g("bar_on") * filled, style=theme.color("magenta"))
        text.append(" " * (room - filled + 1))
        text.append(f"{count:>{counts},}".replace(",", " "), style="bold")
        text.append("\n")
    text.rstrip()
    return text


def attacks_text(attacks, width: int, top: int = 5, now: Optional[float] = None, rows: int = 2) -> Text:
    """The Attacks widget: the last hour per minute (in `rows` rows), the totals, the newest attack, the
    busiest honeypots of 24 hours (the block's subtitle says so)."""
    from tpotctl import events
    text = Text()
    if attacks is None:
        return Text("asking Elasticsearch ...", style=theme.color("mist"))
    if attacks.problem:
        return Text(attacks.problem, style=theme.color("mist"))
    values = attacks.per_minute[-max(10, width):]
    if not any(values):
        text.append("a quiet hour, no attacks\n\n", style=theme.color("mist"))
    rows = 1 if glyphs.mode() == "ascii" else rows
    for line in [] if not any(values) else events.sparkline(values, glyphs.spark(), rows=rows):
        text.append(line, style=theme.color("magenta"))
        text.append("\n")

    def figure(count: int, label: str) -> Text:
        return Text.assemble((f"{count:,}".replace(",", " "), "bold"), (label, theme.color("mist")))
    totals = Text("   ").join([figure(sum(attacks.per_minute), " last hour"), figure(attacks.last_day, " in 24 hours")])
    sources = figure(attacks.sources, " sources" if attacks.sources != 1 else " source") if attacks.sources else None
    latest = None
    if attacks.latest is not None:
        now = time.time() if now is None else now
        latest = Text.assemble((f"{lead('last_attack')}last attack ", theme.color("mist")),
                               (ago(now - attacks.latest), "bold"))
    # the sources go with the totals where the line has room, else with the newest attack
    if sources is not None and totals.cell_len + 3 + sources.cell_len <= width:
        totals.append("   ")
        totals.append_text(sources)
    elif sources is not None:
        latest = sources if latest is None else Text("   ").join([latest, sources])
    text.append_text(totals)
    if latest is not None:
        text.append("\n")
        text.append_text(latest)
    if attacks.top[:top]:
        text.append("\n")
        text.append_text(honeypot_bars(attacks.top[:top], width))
    return text


# from this terminal height on the Attacks block has two rows of sparkline and five honeypots and Top
# attackers ten sources; below one row, three and three: the containers table keeps its rows
SOURCES_TALL = 50


def sources_text(sources, limit: int = 10) -> Text:
    """The top attackers of 24 hours (tpot attackers), the first `limit` of them."""
    text = Text()
    if sources is None or sources.problem or not sources.sources:
        return text
    for source in sources.sources[:limit]:
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
                with Horizontal(id="feed"):
                    with Vertical(id="attacks-block", classes="block"):
                        yield Static(attacks_text(None, 40), id="attacks")
                    with Vertical(id="sources-block", classes="block"):
                        yield Static("", id="top-attackers")
                with Vertical(id="system-block", classes="block"):
                    yield Static("", id="system")
        yield Static("", id="status-info", classes="info")
        yield NavDataTable(id="containers", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="actions"):
            yield Button("Start T-Pot", id="svc-start")
            yield Button("Stop T-Pot", id="svc-stop")
            yield Button("Restart T-Pot", id="svc-restart", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#hive-block").border_title = "Hive"
        self.query_one("#attacks-block").border_title = "Attacks"
        self.query_one("#sources-block").border_title = "Top attackers, 24 hours"
        self.query_one("#system-block").border_title = "System"
        self.hive = self.app.backend.tpot_type() != "SENSOR"
        self.query_one("#attacks-block").display = self.hive
        self.query_one("#sources-block").display = self.hive
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
        stamp = backend.config_stamp()      # before the read: the header is from this configuration
        try:
            state, containers, problem = backend.status(), backend.containers(), ""
        except ops.OpsError as err:
            state, containers, problem = None, [], str(err)
        try:
            machine = backend.system()
        except Exception:      # nothing in /proc is worth a crash of the menu
            machine = None
        self.app.call_from_thread(self.show, state, containers, problem, machine, stamp)

    @work(thread=True, exclusive=True, group="attacks")
    def load_attacks(self) -> None:
        attacks = self.app.backend.attacks()
        sources = self.app.backend.top_sources() if not attacks.problem else None
        self.app.call_from_thread(self.show_attacks, attacks, sources)

    def show_attacks(self, attacks, sources=None) -> None:
        self.attacks, self.sources = attacks, sources
        width = self.query_one("#attacks").content_region.width or 40
        tall = self.app.size.height >= SOURCES_TALL
        self.query_one("#attacks", Static).update(attacks_text(attacks, width, 5 if tall else 3,
                                                               rows=2 if tall else 1))
        busiest = attacks is not None and not attacks.problem and attacks.top
        self.query_one("#attacks-block").border_subtitle = \
            Text("busiest honeypots, 24 hours", style=theme.color("mist")) if busiest else None
        self.query_one("#top-attackers", Static).update(sources_text(sources, 10 if tall else 3))

    def on_resize(self) -> None:
        if getattr(self, "attacks", None) is not None:
            self.show_attacks(self.attacks, getattr(self, "sources", None))

    def repaint(self) -> None:
        """After a change of theme or icons."""
        self.query_one(Honeycomb).repaint()
        if self.attacks is not None:
            self.show_attacks(self.attacks, getattr(self, "sources", None))
        if getattr(self, "shown", None):
            self.show(*self.shown)

    def show(self, state: Optional[ops.Status], containers: List[ops.Container], problem: str,
             machine=None, stamp=None) -> None:
        self.shown = (state, containers, problem, machine)
        if state is not None:
            self.app.update_header(state, stamp, containers, machine)
        if getattr(self, "attacks", None) is not None:     # the age of the newest attack goes on
            self.show_attacks(self.attacks, getattr(self, "sources", None))
        self.query_one(Honeycomb).show(containers)
        if machine is not None:
            self.query_one("#system", Static).update(system_text(machine))
        info = Text()
        running = sum(c.state == "running" for c in containers)
        info.append(f"{running} of {len(containers)} containers running", style="bold")
        if state is not None and state.service not in ("active", "n/a"):
            info.append(f"   the T-Pot service (tpot.service) is {state.service}", style=theme.color("error"))
        if problem:
            info.append(f"   {problem}", style=theme.color("error"))
        self.query_one("#status-info", Static).update(info)
        # every 2 s: only what changed, so the table neither jumps to the top nor flickers
        self.query_one(NavDataTable).sync_rows(container_rows(containers))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("svc-"):
            self.app.service(event.button.id[4:])


class ImagesPane(Vertical):

    def compose(self) -> ComposeResult:
        yield NavDataTable(id="images-table", cursor_type="row", zebra_stripes=True)
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
        # a ref can be on several images (<none>:<none>), an image ID under several refs: both are the key
        self.query_one(NavDataTable).sync_rows((f"{i.ref}@{i.id}", (i.repository, i.tag, i.id, i.size, i.created))
                                               for i in images)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "images-refresh":
            self.load()


class EditionPane(Vertical):
    """The editions of T-Pot: switch to one (tpot edition set), or build your own."""

    def compose(self) -> ComposeResult:
        yield Static("", id="edition-info", classes="info")
        yield NavOptionList(id="edition-list")
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
        if not self.app.backend.linux_host() and self.in_use == "mac_win":
            note.append("macOS and Windows run the MAC_WIN edition, there is no other to switch to.",
                        style=theme.color("ash"))
        button.disabled = choice is None or choice.key == self.in_use
        button.label = f"Switch to {choice.title}" if choice is not None else "Switch"
        self.query_one("#edition-note", Static).update(note)

    def on_option_list_option_highlighted(self, event) -> None:
        if event.option_list.id == "edition-list":
            self.refresh_button()

    def on_option_list_option_selected(self, event) -> None:
        """enter on an edition: on to Switch, or say that it runs already."""
        event.stop()
        choice = self.selected()
        if choice is None:
            return
        button = self.query_one("#switch-edition", Button)
        if choice.key == self.in_use:
            self.app.notify(f"The {choice.title} edition runs already, the customizer changes it.", title="Edition",
                            timeout=5)
        elif button.disabled:
            # not reachable today: refresh_button disables Switch only for the edition in use (said
            # above); it stays for a rule that keeps an edition from a host
            note = str(self.query_one("#edition-note", Static).render()).strip()
            self.app.notify(note or f"The {choice.title} edition cannot be switched to here.", title="Edition",
                            timeout=5)
        else:
            button.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "open-customizer":
            self.app.action_customize()
        elif event.button.id == "switch-edition":
            choice = self.selected()
            if choice is not None:
                self.app.switch_edition(choice.key)


# the pages that show what .env and the compose file say
CONFIG_PAGES = ("settings", "llm", "users", "sensors")


@dataclass
class StartProblem:
    """A value of the saved .env T-Pot would not start with, where to fix it and on which page."""
    key: str
    text: str
    fix: str
    page: str


class SettingsForm(VerticalScroll, inherit_bindings=False):
    """The settings of one tab; the arrows move between the settings (widgets/nav.py), the form
    scrolls to the one with the focus."""

    can_focus = False

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

    # in the footer while the focus is on the page; the Save button does the work
    BINDINGS = [Binding("ctrl+s", "save", "Save", show=True)]
    saving = False              # a save asks Elasticsearch first: Save stays disabled meanwhile

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
        self.fix_told = False           # the notice about a value that does not fit the host, once
        self.declined = set()           # proposals (TPOT_OSTYPE) taken back with Revert
        await self.reload()

    async def reload(self, keep: Optional[Dict[str, str]] = None, keep_unlocked: Iterable[str] = ()) -> None:
        """.env anew; keep: unsaved changes that stay (a save on the other settings page), keep_unlocked:
        the keys of them that were unlocked. One after the other: a second reload while the rows of
        the first one still mount would remove them under their feet."""
        if not hasattr(self, "_reloading"):
            import asyncio
            self._reloading = asyncio.Lock()
        from textual.css.query import NoMatches
        async with self._reloading:
            try:
                await self._reload(keep, keep_unlocked)
            except NoMatches:
                if self.is_attached and not self.app._closing and not self._closing:
                    raise
                # the T-Pot Manager ends meanwhile (i.e. q right after a save), its widgets are going

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
        # before the read: a change while it reads shows on the next look
        self.stamp = self.app.backend.config_stamp()
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
        shown = [rule for rule in self.current.relevant(include_all=self.include_all, offered=True)
                 if self.wanted(rule)]
        # a fixed value that does not fit this host (TPOT_OSTYPE): the one that fits, ready to save
        fixes = {key: value for key, value in self.current.fixes().items()
                 if key in {rule.key for rule in shown} and key not in (keep or {}) and key not in self.declined}
        self.draft.update(fixes)
        for rule in shown:
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
        for key in list(keep_unlocked) + [key for key in fixes if key in self.rows]:
            if key in self.rows and key not in self.unlocked:
                # unlocked again in the background: the focus stays where the person is
                await self.unlock(self.rows[key], focus=False)
        if self.id == "settings" and not self.fix_told:
            # once a run: every value T-Pot would not start with, and where to fix it
            self.fix_told = True
            for problem in self.app.start_problems():
                self.app.notify(f"{problem.key} {problem.text}. {problem.fix}.", title="T-Pot would not start",
                                severity="error", timeout=12)
        self.call_after_refresh(self.check)
        self.loaded()

    def loaded(self) -> None:
        """After a reload, for subclasses."""

    def action_save(self) -> None:
        """ctrl+s: the Save button, or why it does not save."""
        if self.current is None:
            self.app.notify("Not saved: .env could not be read, see the top of the page.", title="Settings",
                            severity="error", timeout=8)
            return
        self.check()                    # the state of the button as of now, not of the last refresh
        button = self.query_one(f"#{self.PREFIX}-save", Button)
        if not button.disabled:
            button.press()
            return
        changes = self.changes()
        if not changes:
            self.app.notify("Nothing to save, no value was changed.", title="Settings", timeout=4)
            return
        blocking = self.current.blocking(self.current.problems(self.draft, offered=True), changes)
        # check() just set the button from the same changes and blockers: without a blocker, Save is
        # disabled only while a save asks Elasticsearch (saving)
        why = "; ".join(f"{p.key}: {p.text}" for p in blocking[:3]) or (
            "a save is under way" if self.saving else "the page is still loading, try again")
        self.app.notify(f"Not saved: {why}", title="Settings", severity="error", timeout=8)

    def stale(self) -> bool:
        """.env or the compose file changed since this page read them (elsewhere in tpot or outside);
        after a load error always, so that the page recovers once the file can be read again (a fixed
        permission does not change the stamp)."""
        if not hasattr(self, "stamp"):
            return False            # not loaded yet, the mount does that
        return self.current is None or self.app.backend.config_stamp() != self.stamp

    def changes(self):
        if self.current is None:
            return {}
        saved = self.current.values
        return {key: value for key, value in self.draft.items()
                if key in self.current.schema and (self.current.schema[key].editable or key in self.unlocked)
                and saved.get(key, "") != value}

    def check(self) -> None:
        from tpotctl import envschema
        if self.current is None or not self.is_attached:     # not while the T-Pot Manager ends
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
                # a badge: readable on the magenta of the focused tab too
                label.append(" ")
                label.append(f" {glyphs.g('fail')} {errors_in[section]} ",
                             style=f"bold {theme.color('glass')} on {theme.color('error')}")
            tab.label = label
        blocking = self.current.blocking(problems, changes)
        self.query_one(f"#{self.PREFIX}-save", Button).disabled = not changes or bool(blocking) or self.saving
        self.query_one(f"#{self.PREFIX}-revert", Button).disabled = not changes
        status = Text()
        if changes:
            status.append(f"{glyphs.g('changed')} {len(changes)} change{'s' if len(changes) > 1 else ''}",
                          style=f"bold {theme.color('magenta')}")
            status.append(f"  {', '.join(changes)}", style=theme.color("mist"))
        else:
            status.append(self.current.path.replace(os.path.expanduser("~"), "~", 1), style=theme.color("mist"))
        others = list(dict.fromkeys(p.key for p in problems if p.level == "error" and p.key not in changes))
        if others:
            names = ", ".join(others[:4]) + (", ..." if len(others) > 4 else "")
            status.append(f"\n{glyphs.g('fail')} T-Pot would not start: {names}", style=theme.color("error"))
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

    async def unlock(self, row, focus: bool = True) -> None:
        import dataclasses
        from tpotctl.widgets.fields import SettingRow
        key = row.rule.key
        self.unlocked.add(key)
        fix = self.current.fixes().get(key) if self.current is not None else None
        # short: it sits under the title in the narrow label column, the notice says the rest
        note = f"{fix} fits this host, Save it" if fix in ops.OSTYPE_TEXT else ""
        editable = SettingRow(dataclasses.replace(row.rule, editable=True), self.draft.get(key, ""), "",
                              unlocked=True, note=note)
        parent = row.parent
        place = list(parent.children).index(row)
        await row.remove()
        if place < len(parent.children):
            await parent.mount(editable, before=place)
        else:
            await parent.mount(editable)
        self.rows[key] = editable
        if focus:
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
        """up / down from setting to setting: the arrows of every page (widgets/nav.py)."""
        nav.navigate(self.app, "down" if step > 0 else "up")

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
        if event.button.id == f"{self.PREFIX}-revert":
            # Revert takes the proposal back too, it stays away until asked for again (palette: Fix ...)
            if self.current is not None:
                self.declined |= set(self.current.fixes())
            await self.reload()
        elif event.button.id == f"{self.PREFIX}-save":
            if self.saving:             # a press that came in before Save was disabled
                return
            saved = self.changes()
            if saved.get("TPOT_KIBANA_OBJECTS") == "overwrite" and \
                    self.current.values.get("TPOT_KIBANA_OBJECTS") != "overwrite":
                # as on the Elastic page: overwrite resets your changes to T-Pot objects on every start
                def answered(yes) -> None:
                    if yes:
                        self.run_worker(self.save_checked(saved), group=f"{self.PREFIX}-save")
                self.app.push_screen(overwrite_mode_question(), answered)
                return
            await self.save_checked(saved)

    async def save_checked(self, saved: Dict[str, str]) -> None:
        """The save after the question of overwrite (if any): a changed retention plans first."""
        if "TPOT_ES_RETENTION" in saved and self.current.values.get("TPOT_TYPE", "HIVE") != "SENSOR":
            # Elasticsearch may take seconds: in a worker, Save disabled meanwhile
            self.saving = True
            self.query_one(f"#{self.PREFIX}-save", Button).disabled = True
            self.run_worker(self.save_retention(saved), group=f"{self.PREFIX}-save")
            return
        await self.save_confirmed(saved)

    async def save_retention(self, saved: Dict[str, str]) -> None:
        """A changed TPOT_ES_RETENTION: a shorter retention asks first, before anything is written, whether
        indices go right away or not (as tpot elastic; a policy kept longer than .env says counts too). Off
        a T-Pot host as well (Docker Desktop publishes Elasticsearch on 127.0.0.1 too). An Elasticsearch that
        does not answer: only .env is written, a shorter value waits, T-Pot never shortens on its own.
        Save stays disabled until the save is done: after a yes, until the apply and the reload are."""
        import asyncio
        from tpotctl import cli as tcli
        new = saved["TPOT_ES_RETENTION"]
        asked = False
        try:
            try:
                chosen = await asyncio.to_thread(self.app.backend.retention_plan, new, False)
            except Exception as err:        # whatever Elasticsearch answers, the worker must not end the Manager
                # the whole save is dropped, the other changes of the page as well
                self.app.notify(f"Nothing was saved: the check of the ILM policy failed ({err}).",
                                title="Not saved", severity="error", timeout=10)
                return
            if chosen.reachable and chosen.shortening:
                if another_screen(self.app, "Save"):      # nothing written, Save is free again (finally)
                    return

                def answered(yes) -> None:
                    if yes:
                        self.run_worker(self.save_after_yes(saved, chosen), group=f"{self.PREFIX}-save")
                    else:
                        self.save_done()

                self.app.push_screen(ConfirmDialog(f"Keep the Elasticsearch events {tcli._retention_shown(new)}?",
                                                   shortening_text(chosen.doomed, new),
                                                   yes="Delete and save", no="Back"), answered)
                asked = True
                return
            await self.save_confirmed(saved, chosen)
        finally:
            if not asked:           # the answer to the question ends the save
                self.save_done()

    async def save_after_yes(self, saved: Dict[str, str], chosen) -> None:
        try:
            await self.save_confirmed(saved, chosen)
        except Exception as err:        # noqa: BLE001 - whatever breaks, the worker must not end the Manager
            new = saved.get("TPOT_ES_RETENTION", "")
            try:                        # did .env take the value before the error?
                written = self.app.backend.settings().values.get("TPOT_ES_RETENTION") == new
            except Exception:           # noqa: BLE001 - unknown: nothing is claimed
                written = False
            waits = ""
            if written:
                how = ("Apply .env to the policy on the Elastic page, or tpot elastic retention --force"
                       if self.app.backend.linux_host() else f"change the ILM policy tpot in {KIBANA_ILM}")
                # the error may come after the apply as well (the reload): the policy is not known
                waits = (f" TPOT_ES_RETENTION={new} is in .env, the ILM policy tpot may not have taken it: if the "
                         f"policy still keeps the old value, the value waits until it is applied ({how}).")
            self.app.notify(f"The save stopped with an error ({err}).{waits} Nothing more was written.",
                            title="Not saved", severity="error", timeout=12)
        finally:
            self.save_done()

    def save_done(self) -> None:
        """A save is done (or was not made): Save as the changes say."""
        self.saving = False
        from textual.css.query import NoMatches
        try:
            self.check()
        except NoMatches:       # the T-Pot Manager ends meanwhile, its widgets are going
            if self.is_attached and not self.app._closing and not self._closing:
                raise

    async def save_confirmed(self, saved: Dict[str, str], chosen=None) -> None:
        """Write the changes; chosen: the plan of a changed TPOT_ES_RETENTION, applied to the ILM policy
        tpot right away where it can be (a shorter one only after the user's yes)."""
        import asyncio
        from tpotctl import cli as tcli, elastic
        from tpotctl.settings import SettingsError
        old = self.current.values.get("TPOT_ES_RETENTION", "")
        try:
            self.current.change(saved, unlocked=self.unlocked)
        except SettingsError as err:
            self.app.notify(str(err), title="Not saved", severity="error", timeout=10)
            return
        host = self.app.backend.linux_host()
        # what applies a waiting value: the Elastic page on a T-Pot host, Kibana elsewhere
        later = ("Once Elasticsearch runs, Apply .env to the policy on the Elastic page applies it (it asks first)."
                 if host else f"Once Elasticsearch runs, change the ILM policy tpot in {KIBANA_ILM}.")
        applied = waiting = foreign = False
        if chosen is not None and chosen.reachable and chosen.decision.action in ("create", "update", "keep"):
            try:
                await asyncio.to_thread(self.app.backend.apply_retention, chosen)
                applied = True
            except elastic.ElasticError as err:
                then = (f"T-Pot does not shorten it on its own, the value waits. {later}" if chosen.shortening
                        else "T-Pot applies it on its next start.")
                self.app.notify(f"TPOT_ES_RETENTION is saved, but the ILM policy tpot was not changed ({err}). "
                                f"{then}", title="Not applied", severity="warning", timeout=12)
        elif chosen is not None and chosen.decision.action == "foreign":
            foreign = True
            where = (f"On the Elastic page, {FOREIGN_BUTTONS}" if host else f"Its retention is changed in {KIBANA_ILM}.")
            self.app.notify(foreign_kept(saved["TPOT_ES_RETENTION"], where), title="Not applied", severity="warning",
                            timeout=12)
        elif chosen is not None and not chosen.reachable:
            new = saved["TPOT_ES_RETENTION"]
            waiting = chosen.shortening or tcli._shortens_env(old, new)
            self.app.notify(because(retention_waits(new, later) if waiting else retention_unknown(new, host),
                                    chosen.problem),
                            title="Not applied" if waiting else "Settings", severity="warning", timeout=12)
        await self.reload()
        await self.app.settings_saved(self, set(saved))
        only_retention = set(saved) == {"TPOT_ES_RETENTION"}
        if applied and only_retention:
            # nothing else changed and the policy has it already: no restart needed
            self.app.notify(f"Saved, Elasticsearch keeps the events "
                            f"{tcli._retention_shown(saved['TPOT_ES_RETENTION'])}.", title="Settings")
        elif (waiting or foreign) and only_retention:
            return                  # a restart would not apply it either, the notice above says what does
        elif host:
            self.app.push_screen(ConfirmDialog("Saved. Restart T-Pot now, so that it uses the new settings?",
                                               yes="Restart T-Pot", no="Later"),
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
            return      # Textual calls SettingsPane.on_button_pressed after this one (MRO), once
        event.prevent_default()
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

def offer_row_actions(pane, name: str, kind: str, actions) -> None:
    """A ChoiceDialog with the actions of the row: the choice presses its button, so the checks and
    questions stay where they are; esc does nothing."""
    from tpotctl.screens.dialogs import ChoiceDialog
    if not name:
        return

    def chosen(index) -> None:
        if index is None:
            return
        # the row it was opened for: a reload under the dialog may have moved the cursor or removed it
        table = pane.query_one(DataTable)
        if pane.selected() != name:
            try:
                table.move_cursor(row=table.get_row_index(name))
            except Exception:       # RowDoesNotExist
                pane.app.notify(f"{name} is no longer there.", title=kind, timeout=5)
                return
        pane.query_one(actions[index][1], Button).press()
    pane.app.push_screen(ChoiceDialog(f"{kind} {name}", [f"{text} {name}" for text, _button in actions]), chosen)


class UsersPane(Vertical):
    """Users of the web UI (WEB_USER), changes count at once."""

    def compose(self) -> ComposeResult:
        yield Static("", id="users-info", classes="info")
        yield NavDataTable(id="users-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="actions"):
            yield Button("Add", id="user-add", variant="primary")
            yield Button("Change password", id="user-passwd")
            yield Button("Remove", id="user-remove")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("User", "Hash", "State")
        self.show()

    def stale(self) -> bool:
        return self.app.backend.config_stamp() != getattr(self, "stamp", None)

    def show(self) -> None:
        from tpotctl.users import UsersError
        table = self.query_one(DataTable)
        table.clear()
        info = Text()
        self.stamp = self.app.backend.config_stamp()
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

    def on_data_table_row_selected(self, event) -> None:
        """enter on a user: the actions of that row."""
        event.stop()
        offer_row_actions(self, self.selected(), "Web user",
                          [("Change the password of", "#user-passwd"), ("Remove", "#user-remove")])

    def run_change(self, change, done: str) -> None:
        from tpotctl.users import UsersError
        try:
            note = change()
        except UsersError as err:
            self.app.notify(str(err), title="Not changed", severity="error", timeout=10)
            return
        self.app.notify(f"{done}, {note}.", title="Web users")
        self.show()
        self.app.config_maybe_changed()         # WEB_USER: the settings pages and the start problems


class SensorsPane(Vertical):
    """Sensors of this HIVE: access, where they are, when they were last seen."""

    def compose(self) -> ComposeResult:
        yield Static("", id="sensors-info", classes="info")
        yield NavDataTable(id="sensors-table", cursor_type="row", zebra_stripes=True)
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

    def stale(self) -> bool:
        return self.app.backend.config_stamp() != getattr(self, "stamp", None)

    def load(self) -> None:
        """The sensors anew; the stamp is noted here, on the UI thread, so that a refresh right after
        does not start a second read (a thread worker cannot be stopped once it runs)."""
        self.stamp = self.app.backend.config_stamp()
        self.fetch()

    @work(thread=True, exclusive=True, group="sensors")
    def fetch(self) -> None:
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
                                              tsensors.default_hive_address, tsensors.check_hive_address),
                                 self.deploy)
        elif button == "sensor-edit" and self.selected() and self.registry is not None:
            from tpotctl.screens.dialogs import SensorEditDialog
            name, registry = self.selected(), self.registry
            self.app.push_screen(SensorEditDialog(registry.get(name), lambda **values: registry.update(name, **values),
                                                  tsensors.check_hive_address),
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

    def on_data_table_row_selected(self, event) -> None:
        """enter on a sensor: the actions of that row."""
        event.stop()
        offer_row_actions(self, self.selected(), "Sensor",
                          [("Edit", "#sensor-edit"), ("Revoke the access of", "#sensor-remove")])

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
        self.app.config_maybe_changed()         # LS_WEB_USER

    def revoke(self, name: str) -> None:
        from tpotctl.sensors import SensorsError
        try:
            note = self.registry.revoke(name)
        except SensorsError as err:
            self.app.notify(str(err), title="Not removed", severity="error", timeout=10)
            return
        self.app.notify(f"{name} is removed, {note}.", title="Sensors")
        self.load()
        self.app.config_maybe_changed()         # LS_WEB_USER



# the stages of a check on the Checks page: glyph and colour per state (tpotctl.checks.stages.STATES)
CHECK_STATES = {"ok": ("stage_ok", "ok"), "failed": ("stage_failed", "error"), "n/a": ("stage_na", "ash"),
                "not_reached": ("stage_not_reached", "mist")}
CHECK_STATE_WORDS = {"ok": "ok", "failed": "failed", "n/a": "does not apply", "not_reached": "not reached"}
NO_CHECK_YET = "No check has run yet."
CHECK_UNREADABLE = "The last result cannot be read."


def check_cell(state: str) -> Text:
    glyph, colour = CHECK_STATES[state]
    return Text(glyphs.g(glyph), style=f"bold {theme.color(colour)}" if state == "failed" else theme.color(colour),
                justify="center")


def check_when(stamp: str) -> str:
    """A time of last.json (UTC, ISO) in the time of this host, to the minute; as it is if it is none."""
    from datetime import datetime
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    if moment.tzinfo is None:
        return stamp
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def check_cleanup_text(cleanup: Dict) -> Text:
    """What the cleanup of a check did, as tpot check says it (cli.check_summary); values from last.json
    stay plain text."""
    text = Text()
    state = cleanup.get("state")
    if state == "done":
        passes = cleanup.get("passes") or []
        text.append(f"{glyphs.g('ok')} Removed {cleanup.get('deleted', 0)} test events from Elasticsearch"
                    + (f" ({' + '.join(str(n) for n in passes)} in two passes)." if len(passes) > 1 else "."),
                    style=theme.color("mist"))
        if cleanup.get("left"):
            text.append(f"\n{glyphs.g('warn')} {cleanup['left']} probe events could not be told apart from real "
                        f"ones and stay; Kibana: ", style=theme.color("warn"))
            text.append(cleanup.get("left_kibana") or "-", style=theme.color("glass"))
    elif state == "kept":
        text.append("The test events stay in Elasticsearch (no cleanup).", style=theme.color("mist"))
    elif state == "skipped":
        text.append("Nothing to clean up here: the events went to the HIVE.", style=theme.color("mist"))
    elif state == "failed":
        text.append(f"{glyphs.g('fail')} The test events are still in Elasticsearch: ", style=theme.color("error"))
        text.append(cleanup.get("detail") or "-", style=theme.color("glass"))
    return text


def check_summary(result: Dict, sensor: bool) -> Text:
    """The lines above the table: which check ran when, how it ended, the cleanup."""
    text = Text()
    what = "pipeline test" if result.get("mode") == "pipeline" else "check of the honeypots"
    text.append(f"Last {what}: {check_when(result.get('started', ''))}", style=f"bold {theme.color('glass')}")
    if result.get("host"):
        text.append(" on ", style=theme.color("mist"))
        text.append(result["host"], style=theme.color("glass"))
    text.append("\n")
    outcome = result.get("result")
    failed = [row["name"] for row in result["honeypots"]
              if any(stage["state"] == "failed" for stage in row["stages"].values())]
    if outcome == "ok":
        text.append(f"{glyphs.g('ok')} Every stage of every honeypot is ok.", style=f"bold {theme.color('ok')}")
    elif outcome == "failed":
        text.append(f"{glyphs.g('fail')} {len(failed)} honeypot(s) with a failed stage: ",
                    style=f"bold {theme.color('error')}")
        text.append(", ".join(failed) or "the cleanup", style=theme.color("glass"))
    elif outcome == "hold failed":
        text.append(f"{glyphs.g('fail')} Nothing was probed, ewsposter did not confirm its hold: ",
                    style=f"bold {theme.color('error')}")
        text.append(result.get("error") or "-", style=theme.color("glass"))
    elif outcome == "cancelled":
        text.append(f"{glyphs.g('warn')} The check was cancelled; the hold of ewsposter is lifted.",
                    style=theme.color("warn"))
    else:
        text.append(f"{glyphs.g('fail')} The check ended with an error: ", style=f"bold {theme.color('error')}")
        text.append(result.get("error") or "-", style=theme.color("glass"))
    if result["ewsposter"].get("state") == "failed":
        text.append(f"\n{glyphs.g('fail')} The hold of ewsposter: ", style=theme.color("error"))
        text.append(result["ewsposter"].get("detail") or "-", style=theme.color("glass"))
    cleanup = check_cleanup_text(result["cleanup"])
    if cleanup.plain:
        text.append("\n")
        text.append_text(cleanup)
    if sensor or result.get("tpot_type") == "SENSOR":
        text.append("\nA SENSOR checks the port, the answer and the log line; the other stages: check on the HIVE.",
                    style=theme.color("ash"))
    if result["honeypots"]:
        text.append("\nenter on a honeypot shows what each stage found and the Kibana link.",
                    style=theme.color("ash"))
    return text


def check_detail(result: Dict, row: Dict) -> Text:
    """The detail of one honeypot of a check: every stage with what it found, the ports, its log line and
    event, the geo event, the Kibana link and the cleanup of the run. Everything from last.json is plain
    text (Text.append, no markup)."""
    text = Text()

    def head(title: str) -> None:
        text.append(f"\n{title}\n", style=f"bold {theme.color('magenta')}")

    def pair(label: str, value: str, width: int = 16) -> None:
        text.append(f"  {label:<{width}}", style=theme.color("ash"))
        text.append(f"{value or '-'}\n", style=theme.color("glass"))

    pair("Container", f"{row['container'] or row['name']}, " + ("running" if row["running"] else "not running"))
    pair("Ports", ", ".join(row["ports"]))
    pair("Logstash type", row["logstash_type"])
    if row["external"]:
        pair("Probed", "by its own tool, not by this check")
    head("Stages")
    titles = result["titles"]
    width = max(len(titles[name]) for name in result["stages"]) + 2
    for name in result["stages"]:
        stage = row["stages"][name]
        glyph, colour = CHECK_STATES[stage["state"]]
        text.append(f"  {glyphs.g(glyph)} ", style=theme.color(colour))
        text.append(f"{titles[name]:<{width}}", style=theme.color("glass"))
        text.append(f"{stage['detail'] or CHECK_STATE_WORDS[stage['state']]}\n", style=theme.color("mist"))
    head("Log line and event")
    pair("Log line", row["log_line"])
    pair("Elasticsearch", " / ".join(part for part in (row["es_index"], row["es_id"]) if part))
    geo = row["geo"]
    if geo is not None:
        head("Geo event")
        pair("Source", f"{geo['src_ip']}:{geo['src_port']}")
        pair("Written to", geo["file"])
        pair("Line", geo["line"] or "nothing was written")
        pair("Elasticsearch", " / ".join(part for part in (geo["es_index"], geo["es_id"]) if part))
        location = geo["location"]
        pair("Location", ", ".join(f"{key} {value}" for key, value in location.items()))
    head("Kibana")
    text.append(f"  {result['kibana'] or '-'}\n", style=theme.color("glass"))
    cleanup = check_cleanup_text(result["cleanup"])
    if cleanup.plain:
        head("Cleanup of this check")
        for line in cleanup.split("\n"):
            text.append("  ")
            text.append_text(line)
            text.append("\n")
    return _trim(text)


def _trim(text: Text) -> Text:
    """Without the newline before the first heading and after the last line."""
    plain = text.plain
    start = len(plain) - len(plain.lstrip("\n"))
    end = len(plain.rstrip("\n"))
    return text[start:end]


class CheckDetailDialog(NavModal):
    """One honeypot of the last check: its stages and what they found; esc or Back closes it."""

    BINDINGS = [Binding("escape", "close", "Back")]

    def __init__(self, result: Dict, row: Dict):
        super().__init__()
        self.result, self.row = result, row

    def compose(self) -> ComposeResult:
        from rich.markup import escape
        with Vertical(classes="dialog", id="check-detail") as dialog:
            yield DialogTitle(escape(self.row["name"]))
            with NavScroll():
                yield Static(check_detail(self.result, self.row), id="check-detail-body")
            with Horizontal(classes="actions"):
                yield Button("Back", id="no")
        dialog.border_subtitle = "esc back"

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class ChecksPane(Vertical):
    """Checks of a running T-Pot: tpot check honeypots / pipeline in the task screen, the stages of the last
    check from last.json; the service requests and the nmap scan of hptest.sh as an extra."""

    result = None           # the last check as run.result_view gives it, None without one
    problem = ""            # why last.json cannot be read ("" when it can or there is none)
    sensor = False
    row_keys: Dict[str, Dict] = {}
    columns_for = ()        # the stages the columns of the table were made for

    def compose(self) -> ComposeResult:
        # the table of the last check and the extra below it do not fit into 24 rows: the page scrolls
        with NavScroll(id="checks-scroll"):
            yield Static("", id="checks-info", classes="info")
            yield Static("", id="check-hold")
            with Horizontal(classes="actions check-switches"):
                yield Switch(True, id="check-geo")
                yield Label("Geo probe", classes="switch-state")
                yield Switch(True, id="check-cleanup")
                yield Label("Clean up afterwards", classes="switch-state")
            # rows of their own: the three do not fit next to the menu at 80 columns
            with Horizontal(classes="actions"):
                yield Button("Check the honeypots", id="check-honeypots", variant="primary")
                yield Button("Dry run", id="check-dry")
            with Horizontal(classes="actions"):
                yield Button("Test the Attack Map pipeline", id="check-pipeline")
            yield Static("", id="check-summary")
            yield NavDataTable(id="check-results", cursor_type="row", zebra_stripes=True)
            yield Static("", id="check-extra", classes="info")
            with Horizontal(classes="actions"):
                yield NavInput(placeholder="host to probe", id="check-host", compact=True)
                yield Button("Probe with nmap", id="check-nmap")

    def on_mount(self) -> None:
        self.row_keys = {}
        self.query_one("#check-host", Input).value = self.app.backend.host_address()
        self.load()
        # the state of ewsposter changes with a check started elsewhere (tpot check, hptest.sh)
        self.set_interval(30, self.show_hold)

    def load(self) -> None:
        """REPAINT, on_mount, page_shown and the end of a task: last.json and the state of ewsposter anew."""
        self.sensor = self.app.backend.tpot_type().upper() == "SENSOR"
        try:
            self.result, self.problem = self.app.backend.check_result(), ""
        except Exception as err:            # noqa: BLE001 - a broken file is a line on the page, never a traceback
            self.result, self.problem = None, str(err) or type(err).__name__
        self.show()

    def show(self) -> None:
        info = Text()
        info.append("Check the honeypots", style=f"bold {theme.color('glass')}")
        info.append("  probes every honeypot of this T-Pot on the address of this host and follows it from the "
                    "port through its answer, its log line, Elasticsearch and GeoIP to the Attack Map (tpot check "
                    "honeypots). ewsposter sends nothing during the check, so real attacks of those minutes are "
                    "not reported either. The geo probe writes one test event with a fixed test IP into the log "
                    "of every honeypot; cleaning up removes the check's own events from Elasticsearch afterwards, "
                    "the lines in the logs stay.\n\n", style=theme.color("mist"))
        info.append("Test the Attack Map pipeline", style=f"bold {theme.color('glass')}")
        info.append("  the test events only, without the probes (tpot check pipeline).", style=theme.color("mist"))
        if self.sensor:
            info.append("\n\nThis is a SENSOR: the check covers the port, the answer and the log line, the rest is "
                        "checked on its HIVE.", style=theme.color("ash"))
        self.query_one("#checks-info", Static).update(info)
        self.query_one("#check-geo", Switch).disabled = self.sensor
        self.query_one("#check-pipeline", Button).disabled = self.sensor
        extra = Text()
        extra.append("Probe with nmap", style=f"bold {theme.color('glass')}")
        extra.append("  the service requests and the nmap scan of hptest.sh, of this T-Pot or another one; "
                     "ewsposter of this host sends nothing meanwhile, the probes stay in Kibana.",
                     style=theme.color("mist"))
        self.query_one("#check-extra", Static).update(extra)
        self.show_hold()
        self.show_result()

    def show_hold(self) -> None:
        """What ewsposter says of its hold (<data>/ews/hold.state): stuck is a warning."""
        try:
            state, age = self.app.backend.ews_state()
        except Exception:                   # noqa: BLE001 - the line only informs
            state, age = "unknown", None
        if state == "normal":
            text = Text(f"{glyphs.g('ok')} ewsposter sends as usual.", style=theme.color("mist"))
        elif state == "held":
            text = Text(f"{glyphs.g('warn')} ewsposter is held: a check runs, nothing is sent meanwhile.",
                        style=theme.color("warn"))
        elif state == "stuck":
            text = Text(f"{glyphs.g('warn')} ewsposter is stuck after a hold: it sends nothing until one of its "
                        f"idle rounds ends cleanly (docker logs ewsposter says why).",
                        style=f"bold {theme.color('warn')}")
        else:
            text = Text("ewsposter does not say how it is: it does not run here, or its state is older than two "
                        "minutes.", style=theme.color("ash"))
        self.query_one("#check-hold", Static).update(text)

    def show_result(self) -> None:
        table = self.query_one("#check-results", NavDataTable)
        summary = self.query_one("#check-summary", Static)
        result = self.result
        if result is None or not result["honeypots"]:
            if self.problem:
                summary.update(Text(CHECK_UNREADABLE, style=theme.color("warn")))
            elif result is None:
                summary.update(Text(NO_CHECK_YET, style=theme.color("mist")))
            else:
                summary.update(check_summary(result, self.sensor))
            self.row_keys = {}
            table.clear()
            table.display = False
            return
        summary.update(check_summary(result, self.sensor))
        stages = tuple(result["stages"])
        if stages != self.columns_for:
            table.clear(columns=True)
            table.add_column(Text("Honeypot"), key="name")
            for name in stages:
                table.add_column(Text(result["titles"][name]), key=name)
            self.columns_for = stages
        rows, self.row_keys = [], {}
        for row in result["honeypots"]:
            key, number = row["name"], 1
            while key in self.row_keys:
                number += 1
                key = f"{row['name']} ({number})"
            self.row_keys[key] = row
            name = Text(row["name"], style=theme.color("glass" if row["running"] else "ash"))
            rows.append((key, [name] + [check_cell(row["stages"][stage]["state"]) for stage in stages]))
        table.display = True
        table.sync_rows(rows)

    def enter(self) -> None:
        self.query_one("#check-honeypots", Button).focus()

    def on_input_submitted(self, event) -> None:
        """enter in the host field: on to the probe, a second enter starts it."""
        if event.input.id == "check-host":
            event.stop()
            self.query_one("#check-nmap", Button).focus()

    def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id != "check-results" or self.result is None:
            return
        event.stop()
        row = self.row_keys.get(str(event.row_key.value))
        if row is not None:
            self.app.push_screen(CheckDetailDialog(self.result, row))

    def check_command(self, what: str, dry_run: bool = False) -> List[str]:
        """tpot check honeypots / pipeline with the switches: -y (the page asked), --no-geo, --keep."""
        geo = self.query_one("#check-geo", Switch).value
        cleanup = self.query_one("#check-cleanup", Switch).value
        command = [LAUNCHER, "check", what, "--dry-run" if dry_run else "-y"]
        if what == "honeypots" and not geo and not self.sensor:
            command.append("--no-geo")
        if not cleanup:
            command.append("--keep")
        return command

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from tpotctl import cli
        from tpotctl.checks import run as trun
        from tpotctl.screens.task import Task
        button = event.button.id
        if button == "check-nmap":
            host = self.query_one("#check-host", Input).value.strip()
            # the service requests and the nmap scan of hptest.sh, under the hold of ewsposter
            self.app.run_task(Task(f"Probe with nmap{' on ' + host if host else ''}",
                                   [cli.HPTEST, "--nmap"] + ([host] if host else []), cwd=os.path.expanduser("~"),
                                   become="-B", done="The probes are through, Kibana shows them."),
                              lambda _code: self.load())
        elif button == "check-dry":
            self.app.run_task(Task("Check the honeypots, dry run", self.check_command("honeypots", dry_run=True),
                                   cwd=REPO_DIR, autostart=True), lambda _code: self.load())
        elif button in ("check-honeypots", "check-pipeline"):
            what = "honeypots" if button == "check-honeypots" else "pipeline"
            options = trun.Options(keep=not self.query_one("#check-cleanup", Switch).value,
                                   geo=self.query_one("#check-geo", Switch).value, geo_only=what == "pipeline")
            title = "Check the honeypots" if what == "honeypots" else "Test the Attack Map pipeline"
            command = self.check_command(what)
            done = ("Every stage of every honeypot is ok." if what == "honeypots"
                    else "Every test event reached the Attack Map.")

            def confirmed(yes: bool) -> None:
                if yes:
                    self.app.run_task(Task(title, command, cwd=REPO_DIR, become="--become-file", autostart=True,
                                           done=done), lambda _code: self.load())
            self.app.push_screen(ConfirmDialog(f"{title}?", Text(trun.question(options, self.sensor),
                                                                  style=theme.color("glass")),
                                               yes="Start the check", no="Back"), confirmed)


class ElasticPane(Vertical):
    """How long Elasticsearch keeps the events (TPOT_ES_RETENTION and the ILM policy tpot), and T-Pot's
    Kibana objects: the mode TPOT_KIBANA_OBJECTS, Export, Update, Overwrite all and Restore."""

    status = None           # the elastic.Status the page shows, None before the first read or after an error
    busy = False            # a Set, Apply or Take over is under way: its buttons are disabled
    reading = False         # a read of the status is under way: a load meanwhile starts none
    reload_wanted = False   # a load came in while a read ran: one more read after it
    shows = 0               # the number of the latest show() begun, so that an older answer stays out
    shown = 0               # the number of the show() whose answer the page shows
    # the Kibana objects, the same in their own part: the cli.KibanaState shown, a job (Export, Update,
    # Overwrite all, Restore, Save) under way, a read under way and one wanted after it, the reads begun
    # and the one shown; the mode of .env as read last (empty is keep)
    kibana = None
    kibana_busy = False
    kibana_reading = False
    kibana_reload_wanted = False
    kibana_shows = 0
    kibana_shown = 0
    kibana_mode = None

    def compose(self) -> ComposeResult:
        from tpotctl.widgets.fields import TpotSelect
        from tpotctl.widgets.nav import NavScroll
        # the two parts do not fit into 24 rows: the page scrolls to the widget with the focus
        with NavScroll(id="elastic-scroll"):
            yield Static("", id="elastic-info", classes="info")
            yield Static("", id="elastic-status")
            with Horizontal(classes="actions"):
                yield NavInput(placeholder="days or off", id="elastic-value", compact=True)
                yield Button("Set", id="elastic-set", variant="primary")
            # a row of their own: the four do not fit next to the menu at 80 or 100 columns
            with Horizontal(classes="actions"):
                yield Button("Take over from Kibana", id="elastic-adopt", disabled=True)
                yield Button("Apply .env to the policy", id="elastic-apply", disabled=True)
            yield Static("", id="kibana-info", classes="info")
            yield Static("", id="kibana-status")
            with Horizontal(classes="actions"):
                yield TpotSelect([(label, value) for value, label, _help in kibana_modes()], value="keep",
                                 allow_blank=False, id="kibana-mode", compact=True, disabled=True)
                yield Button("Save", id="kibana-mode-save", disabled=True)
            yield Static("", id="kibana-mode-help")
            with Horizontal(classes="actions"):
                yield Button("Export", id="kibana-export", disabled=True)
                yield Button("Update", id="kibana-update", variant="primary", disabled=True)
                yield Button("Overwrite all", id="kibana-overwrite", disabled=True)
            yield NavDataTable(id="kibana-exports", cursor_type="row", zebra_stripes=True, enter_goes_on=True)
            with Horizontal(classes="actions"):
                yield Button("Restore", id="kibana-restore", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#kibana-exports", DataTable).add_columns("Exported", "From", "Size", "Objects")
        self.load()

    def load(self) -> None:
        """REPAINT, on_mount and page_shown: the status anew, in a worker (Elasticsearch may take seconds);
        one read at a time, a load while one runs reads once more after it (.env may have changed)."""
        text = Text()
        text.append("Retention", style=f"bold {theme.color('glass')}")
        text.append("  how many days Elasticsearch keeps the events (TPOT_ES_RETENTION, the ILM policy tpot), or "
                    "off to keep them all. A shorter time always asks first and lists the indices it deletes. T-Pot "
                    "never shortens the policy on its own. A policy changed in Kibana stays as it is until you keep "
                    "it (Take over from Kibana) or apply .env to it (Apply .env to the policy).",
                    style=theme.color("mist"))
        from textual.css.query import NoMatches
        try:
            self.query_one("#elastic-info", Static).update(text)
        except NoMatches:           # a save's worker while the T-Pot Manager ends: its widgets are going
            if self.is_attached and not self.app._closing and not self._closing:
                raise
            return
        self.query_one("#kibana-info", Static).update(kibana_info())
        if self.kibana_reading:
            self.kibana_reload_wanted = True
        else:
            self.kibana_reading = True
            self.run_worker(self.read_kibana(), group="kibana")
        if self.reading:
            self.reload_wanted = True
            return
        self.reading = True
        self.run_worker(self.read(), group="elastic")

    async def read_kibana(self) -> None:
        try:
            while True:
                self.kibana_reload_wanted = False
                await self.show_kibana()
                if not self.kibana_reload_wanted:
                    break
        finally:
            self.kibana_reading = False

    async def read(self) -> None:
        try:
            while True:
                self.reload_wanted = False
                await self.show()
                if not self.reload_wanted:
                    break
        finally:
            self.reading = False

    async def show(self) -> None:
        """The status as read now. A read and the one after a change may overlap: the answer of a show
        begun earlier never replaces that of one begun later."""
        import asyncio
        from tpotctl.cli import _retention_shown as shown
        from tpotctl.settings import SettingsError
        self.shows += 1
        mine = self.shows
        try:
            state = await asyncio.to_thread(self.app.backend.retention_status)
        except (SettingsError, OSError) as err:     # .env cannot be read
            if mine < self.shown:
                return
            self.shown = mine
            self.status = None
            self.query_one("#elastic-status", Static).update(Text(str(err), style=theme.color("error")))
            self.buttons()
            return
        if mine < self.shown:
            return
        self.shown = mine
        previous, self.status = self.status, state
        text = Text()
        text.append("TPOT_ES_RETENTION  ", style=theme.color("mist"))
        text.append(f"{shown(state.value)}\n", style=f"bold {theme.color('glass')}")
        text.append("ILM policy tpot    ", style=theme.color("mist"))
        note = ""
        if state.state == "unreachable":
            text.append(state.problem, style=theme.color("wax"))
        elif state.state == "missing":
            if state.value in ("", "off"):
                text.append("missing, T-Pot creates it on its next start", style=theme.color("wax"))
            else:
                text.append("missing", style=theme.color("wax"))
                note = (f"T-Pot creates the policy on its next start: for {state.value} days if no older events "
                        "exist, otherwise forever (nothing is deleted) and the value waits.")
        elif state.state == "foreign" and state.adopts:
            text.append(f"{shown(state.current)} · changed in Kibana, T-Pot takes it over on its next start "
                        "(same duration)", style=theme.color("wax"))
        elif state.state == "foreign":
            text.append(f"{shown(state.current) if state.current else 'not in days'} · changed in Kibana, "
                        "T-Pot leaves it alone", style=theme.color("wax"))
        else:
            text.append(f"{shown(state.current)} · set by T-Pot", style=theme.color("glass"))
        if state.pending:
            # a shorter value in .env (tpot env set, a restore, an unreachable Elasticsearch): T-Pot waits
            note = (f"TPOT_ES_RETENTION={state.value} waits: T-Pot does not shorten the ILM policy tpot "
                    f"({shown(state.current)}) on its own; Apply .env to the policy applies it (asks first), "
                    f"Set {state.current} keeps the policy.")
        if note:
            text.append(f"\n{note}", style=f"bold {theme.color('wax')}")
        self.query_one("#elastic-status", Static).update(text)
        self.buttons()
        field = self.query_one("#elastic-value", Input)
        if not field.value or (previous is not None and field.value == previous.value):
            field.value = state.value           # not typed in: the value of .env as it is now

    async def write_env(self, change, value: str) -> None:
        """A write of .env of this page (Set of the retention, Save of the mode), one at a time: each loads
        .env and writes the text it loaded with its change, two at once would lose one of them."""
        import asyncio
        if getattr(self, "_env_lock", None) is None:
            self._env_lock = asyncio.Lock()
        async with self._env_lock:
            await asyncio.to_thread(change, value)

    # -- the Kibana objects ---------------------------------------------------------------------------

    async def show_kibana(self) -> None:
        """The Kibana objects as read now: the status line, the mode, the exports. As with show(), the answer
        of a read begun earlier never replaces that of one begun later."""
        import asyncio
        from textual.css.query import NoMatches
        from tpotctl import cli, kibana_objects as ko
        self.kibana_shows += 1
        mine = self.kibana_shows
        try:
            state = await asyncio.to_thread(self.app.backend.kibana_status)
        except (ko.KibanaError, ops.OpsError, OSError) as err:
            state = cli.KibanaState("", problem=str(err))
        exports = []
        if state.kibana:
            try:
                exports = await asyncio.to_thread(self.app.backend.kibana_exports)
            except (ko.KibanaError, ops.OpsError, OSError):
                exports = []
        if mine < self.kibana_shown:
            return
        self.kibana_shown = mine
        try:
            self.paint_kibana(state, exports)
        except NoMatches:           # the T-Pot Manager ends meanwhile, its widgets are going
            if self.is_attached and not self.app._closing and not self._closing:
                raise

    def paint_kibana(self, state, exports) -> None:
        from tpotctl.cli import _human_size
        from tpotctl.widgets.fields import TpotSelect
        previous, self.kibana = self.kibana_mode, state
        text = Text()
        text.append(f"{'TPOT_KIBANA_OBJECTS':<21}", style=theme.color("mist"))
        text.append(f"{state.mode or 'keep'}\n", style=f"bold {theme.color('glass')}")
        text.append(f"{'In Kibana now':<21}", style=theme.color("mist"))
        text.append(state.text(), style=theme.color("glass" if state.plan is not None else "wax"))
        self.query_one("#kibana-status", Static).update(text)
        if state.mode:
            self.kibana_mode = state.mode
            select = self.query_one("#kibana-mode", TpotSelect)
            # not chosen anew on the page: the mode of .env as it is now
            if not self.kibana_busy and (previous is None or select.value == previous):
                select.value = state.mode
        rows = [(item.path, (time.strftime("%Y-%m-%d %H:%M", time.localtime(item.modified)), item.origin,
                             _human_size(item.size), "?" if item.objects is None else str(item.objects)))
                for item in exports]
        self.query_one("#kibana-exports", NavDataTable).sync_rows(rows)
        self.kibana_help()
        self.kibana_buttons()

    def chosen_mode(self) -> str:
        from textual.widgets import Select
        from tpotctl.widgets.fields import TpotSelect
        value = self.query_one("#kibana-mode", TpotSelect).value
        return "keep" if value is Select.NULL else str(value)

    def kibana_help(self) -> None:
        """The pros and cons of the mode chosen in the list (the help of env.schema.yml, as the Settings page
        shows it), what it would do on the next start, and that Save writes it."""
        mode = self.chosen_mode()
        helps = {value: help_text for value, _label, help_text in kibana_modes()}
        text = Text(helps.get(mode, ""), style=theme.color("mist"))
        state = self.kibana
        if state is not None and state.plan is not None:
            text.append(f"\n{mode_effect(mode, state.plan, state.given_up)}", style=theme.color("glass"))
        if state is not None and state.kibana and self.kibana_mode is not None and mode != self.kibana_mode:
            text.append(f"\nSave writes TPOT_KIBANA_OBJECTS={mode} into .env, T-Pot uses it from its next start.",
                        style=f"bold {theme.color('wax')}")
        self.query_one("#kibana-mode-help", Static).update(text)

    def kibana_buttons(self) -> None:
        """Every control of the Kibana objects as of the state read last: none without Kibana in the edition,
        none while a job runs; Save only for a mode other than the one of .env, Restore with an export."""
        state = self.kibana
        ready = state is not None and state.kibana and not self.kibana_busy
        for name in ("#kibana-mode", "#kibana-export", "#kibana-update", "#kibana-overwrite"):
            self.query_one(name).disabled = not ready
        self.query_one("#kibana-mode-save", Button).disabled = not (
            ready and self.kibana_mode is not None and self.chosen_mode() != self.kibana_mode)
        self.query_one("#kibana-restore", Button).disabled = not (
            ready and self.query_one("#kibana-exports", DataTable).row_count > 0)

    def on_select_changed(self, event) -> None:
        if event.select.id == "kibana-mode":
            event.stop()
            self.kibana_help()
            self.kibana_buttons()

    def selected_export(self) -> str:
        table = self.query_one("#kibana-exports", DataTable)
        if not table.row_count:
            return ""
        return str(table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value)

    def kibana_pressed(self, button: str) -> None:
        if self.kibana_busy or self.kibana is None or not self.kibana.kibana:
            return              # a press that came in before the buttons were disabled
        if button == "kibana-mode-save":
            mode = self.chosen_mode()
            if mode == self.kibana_mode:
                return
            if mode == "overwrite":
                self.app.push_screen(overwrite_mode_question(),
                                     lambda yes: self.start_kibana(self.save_mode(mode)) if yes
                                     else self.revert_mode())
                return
            self.start_kibana(self.save_mode(mode))
        elif button == "kibana-export":
            self.start_kibana(self.export_kibana())
        elif button in ("kibana-update", "kibana-overwrite"):
            self.start_kibana(self.ask_import(button == "kibana-overwrite"))
        elif button == "kibana-restore" and self.selected_export():
            self.start_kibana(self.ask_restore(self.selected_export()))

    def revert_mode(self) -> None:
        """Back on the question of overwrite: the list shows the mode of .env again."""
        from tpotctl.widgets.fields import TpotSelect
        if self.kibana_mode is not None:
            self.query_one("#kibana-mode", TpotSelect).value = self.kibana_mode

    def start_kibana(self, job) -> None:
        """One job of the Kibana objects at a time: their controls stay disabled until it is done."""
        if self.kibana_busy:
            job.close()
            return
        self.kibana_busy = True
        self.kibana_buttons()
        self.run_worker(self.run_kibana_job(job), group="kibana-change")

    async def run_kibana_job(self, job) -> None:
        try:
            await job
        finally:
            self.kibana_busy = False
            from textual.css.query import NoMatches
            try:
                self.kibana_buttons()
            except NoMatches:       # the T-Pot Manager ends meanwhile, its widgets are going
                if self.is_attached and not self.app._closing and not self._closing:
                    raise

    def kibana_failed(self, err: Exception, title: str = "Kibana objects") -> None:
        """A KibanaError or OSError: its reason as a note, nothing more is written. Another import that runs
        (kibana_objects.BUSY, after a minute of waiting) is a warning."""
        from tpotctl import kibana_objects as ko
        reason = str(err)
        self.app.notify(reason[:1].upper() + reason[1:] + ("" if reason.endswith(".") else "."), title=title,
                        severity="warning" if reason == ko.BUSY else "error", timeout=12)

    async def save_mode(self, mode: str) -> None:
        from tpotctl.settings import SettingsError
        try:
            await self.write_env(self.app.backend.set_kibana_mode, mode)
        except (SettingsError, OSError) as err:
            self.app.notify(str(err), title="Not saved", severity="error", timeout=10)
            self.revert_mode()
            return
        self.app.config_maybe_changed()             # the Settings page shows TPOT_KIBANA_OBJECTS too
        self.app.notify(f"TPOT_KIBANA_OBJECTS={mode} is saved in .env. T-Pot uses it from its next start.",
                        title="Elastic")
        await self.show_kibana()

    async def export_kibana(self) -> None:
        import asyncio
        from tpotctl import cli, kibana_objects as ko
        try:
            path, count = await asyncio.to_thread(self.app.backend.kibana_export)
        except (ko.KibanaError, OSError) as err:
            self.kibana_failed(err, "Not exported")
            return
        self.app.notify(f"{cli._objects(count)} exported to {path}.", title="Kibana objects", timeout=10)
        await self.show_kibana()

    async def ask_import(self, overwrite: bool) -> None:
        """Update / Overwrite all: the plan of Kibana as it is now, then the question with the counts and the
        objects of yours (kept or replaced). A one-off run: TPOT_KIBANA_OBJECTS stays as it is."""
        import asyncio
        from tpotctl import cli, kibana_objects as ko
        mode = "overwrite" if overwrite else "update"
        try:
            chosen = await asyncio.to_thread(self.app.backend.kibana_preview)
        except (ko.KibanaError, OSError) as err:
            self.kibana_failed(err)
            return
        if not chosen.to_import(mode):
            self.app.notify(f"Nothing to import ({cli._kibana_counts(chosen)}).", title="Kibana objects")
            return
        button = "Overwrite all" if overwrite else "Update"
        if another_screen(self.app, button, "The plan of the Kibana objects is ready"):
            return
        text = Text("\n".join(cli.kibana_plan_lines(mode, chosen)), style=theme.color("mist"))
        kept = (self.kibana.mode if self.kibana is not None else "") or "keep"
        text.append(f"\n\nThis runs once: TPOT_KIBANA_OBJECTS stays {kept}.", style=theme.color("ash"))
        if not overwrite:
            title = "Update the Kibana objects?"
        elif chosen.yours:
            title = f"Overwrite {cli._objects(len(chosen.yours), 'objects of yours')} with the T-Pot version?"
        else:
            title = "Overwrite the T-Pot objects with the T-Pot version?"
        confirmed = {item.key for item in chosen.yours} if overwrite else None
        self.app.push_screen(ConfirmDialog(title, text, yes="Overwrite" if overwrite else "Update", no="Back"),
                             lambda yes: self.start_kibana(self.run_import(overwrite, confirmed)) if yes else None)

    async def run_import(self, overwrite: bool, confirmed) -> None:
        import asyncio
        from tpotctl import cli, kibana_objects as ko
        mode = "overwrite" if overwrite else "update"
        try:
            chosen, result, path = await asyncio.to_thread(self.app.backend.kibana_update, overwrite, confirmed)
        except (ko.KibanaError, OSError) as err:
            self.kibana_failed(err, "Not imported")
            await self.show_kibana()
            return
        if chosen.to_import(mode):
            lines = cli.kibana_result_lines(ko.summary(mode, chosen, result, path))
            if mode == "update" and chosen.yours:
                lines += [cli.KIBANA_KEPT] + cli._kibana_titles(chosen.yours)
        else:
            lines = [f"Nothing to import ({cli._kibana_counts(chosen)})."]
        self.kibana_result("Kibana objects", lines, not result.errors)
        await self.show_kibana()

    async def ask_restore(self, path: str) -> None:
        """Restore: the chosen export is read as the restore reads it, then the question of tpot elastic
        restore."""
        import asyncio
        from tpotctl import cli, kibana_objects as ko
        try:
            count = await asyncio.to_thread(self.app.backend.kibana_check_export, path)
        except (ko.KibanaError, OSError) as err:
            self.kibana_failed(err, "Not restored")
            return
        if another_screen(self.app, "Restore", "The check of the export is done"):
            return
        self.app.push_screen(ConfirmDialog("Restore this export?",
                                           Text(cli.kibana_restore_question(path, count), style=theme.color("mist")),
                                           yes="Restore", no="Back"),
                             lambda yes: self.start_kibana(self.run_restore(path)) if yes else None)

    async def run_restore(self, path: str) -> None:
        import asyncio
        from tpotctl import cli, kibana_objects as ko
        try:
            export_path, result = await asyncio.to_thread(self.app.backend.kibana_restore, path)
        except (ko.KibanaError, OSError) as err:
            self.kibana_failed(err, "Not restored")
            await self.show_kibana()
            return
        self.kibana_result("Restore", cli.kibana_result_lines(cli.kibana_restore_lines(export_path, result)),
                           not result.errors)
        await self.show_kibana()

    def kibana_result(self, title: str, lines: List[str], ok: bool) -> None:
        """What a run did, in a dialog; with another screen open a note instead: its first line and the
        objects Kibana refused (at most RESULT_REFUSED_SHOWN)."""
        if len(self.app.screen_stack) > 1:
            refused = [line for line in lines[1:] if line.startswith("not imported:")]
            note = [lines[0]] + refused[:RESULT_REFUSED_SHOWN]
            if len(refused) > RESULT_REFUSED_SHOWN:
                note.append(f"… and {len(refused) - RESULT_REFUSED_SHOWN} more")
            self.app.notify("\n".join(note), title=title, severity="information" if ok else "warning",
                            timeout=12 if ok else 30)
            return
        text = Text()
        text.append(f"{glyphs.g('ok') if ok else glyphs.g('warn')} {lines[0]}\n",
                    style=f"bold {theme.color('ok' if ok else 'warn')}")
        for line in lines[1:]:
            text.append(f"{line}\n", style=theme.color("mist"))
        text.rstrip()
        self.app.push_screen(ConfirmDialog(title, text, no="Close", allow_yes=False))

    # -- the retention -------------------------------------------------------------------------------

    def buttons(self) -> None:
        """Set, Take over and Apply as of the status read last; all three disabled while a change runs."""
        state = self.status
        self.query_one("#elastic-set", Button).disabled = self.busy
        self.query_one("#elastic-adopt", Button).disabled = self.busy or not (
            state is not None and state.state == "foreign" and state.current)
        self.query_one("#elastic-apply", Button).disabled = self.busy or not (
            state is not None and (state.state == "foreign" or state.pending) and state.value)

    def enter(self) -> None:
        self.query_one("#elastic-value", Input).focus()

    def on_input_submitted(self, event) -> None:
        """enter in the field: Set."""
        if event.input.id == "elastic-value":
            event.stop()
            self.query_one("#elastic-set", Button).press()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if (event.button.id or "").startswith("kibana-"):
            self.kibana_pressed(event.button.id)
            return
        if self.busy:               # a press that came in before the buttons were disabled
            return
        if event.button.id == "elastic-set":
            value = self.query_one("#elastic-value", Input).value.strip()
            if not value:           # never TPOT_ES_RETENTION= from an empty field
                self.app.notify("Enter days (1-3650) or off.", title="Not set", severity="warning", timeout=8)
                return
            self.start(self.change(value, force=False))
        elif event.button.id == "elastic-apply" and self.status is not None and self.status.value:
            self.start(self.change(self.status.value, force=True))
        elif event.button.id == "elastic-adopt" and self.status is not None and self.status.current:
            # the policy's own value as the page read it, T-Pot owns the policy from now on; write() plans
            # it anew and asks if that shortens it (Kibana may have changed it again meanwhile)
            self.start(self.write(self.status.current, None))

    def start(self, job) -> None:
        """One change at a time: the buttons stay disabled until job (change or write) is done."""
        if self.busy:
            job.close()
            return
        self.busy = True
        self.buttons()
        self.run_worker(self.run_job(job), group="elastic-change")

    async def run_job(self, job) -> None:
        try:
            await job
        finally:
            self.busy = False
            from textual.css.query import NoMatches
            try:
                self.buttons()
            except NoMatches:       # the T-Pot Manager ends meanwhile, its widgets are going
                if self.is_attached and not self.app._closing and not self._closing:
                    raise

    def ask(self, value: str, chosen, button: str) -> None:
        """A shorter retention: what it deletes, before anything is written; #no has the focus. Another
        screen opened while the plan was made: no question on top of it, button is pressed again."""
        from tpotctl.cli import _retention_shown as shown
        if another_screen(self.app, button):
            return
        self.app.push_screen(ConfirmDialog(f"Keep the events {shown(value)}?", shortening_text(chosen.doomed, value),
                                           yes="Delete and set", no="Back"),
                             lambda yes: self.start(self.write(value, chosen)) if yes else None)

    async def change(self, value: str, force: bool) -> None:
        import asyncio
        from tpotctl import cli
        problem = cli.retention_value_problem(value)
        if problem:
            self.app.notify(f"TPOT_ES_RETENTION: {problem}", title="Not set", severity="error", timeout=8)
            return
        chosen = await asyncio.to_thread(self.app.backend.retention_plan, value, force)
        old = self.status.value if self.status else ""
        if not chosen.reachable:
            # nothing can be shown or applied: only .env changes, a shorter value waits for a yes here;
            # Apply (force) is a value that waits already. An unset .env shortens nothing (the text only,
            # what asks is chosen.shortening)
            await self.write(value, chosen, waits=force or cli._shortens_env(old, value))
            return
        if chosen.shortening:           # deletes events the policy keeps now: always asked, indices or not
            self.ask(value, chosen, "Apply .env to the policy" if force else "Set")
            return
        await self.write(value, chosen)

    async def write(self, value: str, chosen, waits: Optional[bool] = None) -> None:
        """value into .env, then the plan into the policy where it can be. chosen None (Take over): the
        plan is made here, forced, and a shorter retention is asked first, before anything is written
        (the policy may have changed in Kibana since the page read it). waits: an unreachable
        Elasticsearch leaves a shorter value waiting (None: shorter than .env). A policy changed in Kibana
        (foreign) stays as it is, as on the Settings page: only .env takes the value."""
        import asyncio
        from tpotctl import elastic
        from tpotctl.cli import _retention_shown as shown, _shortens_env
        from tpotctl.settings import SettingsError
        if chosen is None:
            chosen = await asyncio.to_thread(self.app.backend.retention_plan, value, True)
            if chosen.reachable and chosen.shortening:
                self.ask(value, chosen, "Take over from Kibana")
                return
        old = self.status.value if self.status else ""
        if waits is None:
            waits = _shortens_env(old, value)
        try:
            await self.write_env(self.app.backend.set_retention, value)
        except (SettingsError, OSError) as err:
            self.app.notify(str(err), title="Not set", severity="error", timeout=10)
            return
        self.app.config_maybe_changed()             # the Settings page shows TPOT_ES_RETENTION too
        try:
            if chosen.reachable and chosen.decision.action == "foreign":
                self.app.notify(foreign_kept(value, FOREIGN_BUTTONS),
                                title="Not applied", severity="warning", timeout=12)
            elif chosen.reachable:
                await asyncio.to_thread(self.app.backend.apply_retention, chosen)
                self.app.notify(f"The ILM policy tpot keeps the events {shown(value)}.", title="Elastic")
            elif waits or chosen.shortening:
                self.app.notify(because(retention_waits(value, "Once Elasticsearch runs, Apply .env to the policy "
                                                               "applies it here (it asks first)."),
                                        chosen.problem),
                                title="Not applied", severity="warning", timeout=12)
            else:
                self.app.notify(because(retention_unknown(value), chosen.problem), title="Elastic",
                                severity="warning", timeout=12)
        except elastic.ElasticError as err:
            undo = f" To undo it, set {old} again." if old and old != value else ""
            later = ("T-Pot does not shorten it on its own: Apply .env to the policy applies it (it asks first)."
                     if chosen.shortening else "T-Pot applies it on its next start.")
            self.app.notify(f"TPOT_ES_RETENTION={value} is set in .env, but the ILM policy tpot was not changed "
                            f"({err}). {later}{undo}", title="Not applied", severity="warning", timeout=12)
        self.query_one("#elastic-value", Input).value = ""
        await self.show()


# indices listed in the question before a shorter retention, the rest as a count
DOOMED_SHOWN = 12
# where the ILM policy tpot is changed where the T-Pot Manager has no Elastic page (Docker Desktop)
KIBANA_ILM = "Kibana, Stack Management › Index Lifecycle Policies"


def retention_waits(value: str, later: str) -> str:
    """A shorter TPOT_ES_RETENTION written while Elasticsearch does not answer: it waits for a yes."""
    return (f"TPOT_ES_RETENTION={value} is set in .env, but Elasticsearch is not reachable: T-Pot does not shorten "
            f"the ILM policy tpot on its own, the value waits. {later}")


# what the two buttons of the Elastic page do with a policy changed in Kibana; each asks first where it shortens
FOREIGN_BUTTONS = ("Take over from Kibana keeps it, Apply .env to the policy applies .env to it; either asks first "
                   "if that shortens the retention.")


def another_screen(app, button: str, done: str = "The check of the ILM policy is done") -> bool:
    """Another screen opened while a check ran (the palette, a question of another page): the question of the
    check does not land on top of it, a note says to press the button again. True when one is open."""
    if len(app.screen_stack) <= 1:
        return False
    app.notify(f"{done}, but another screen was open: press {button} again.",
               title="Not saved" if button == "Save" else "Elastic", severity="warning", timeout=10)
    return True


def kibana_modes():
    """(value, label, help) of the modes of TPOT_KIBANA_OBJECTS: the choices of env.schema.yml, whose help
    (the pros and cons) the Settings page shows as well."""
    from tpotctl import envschema
    if not hasattr(kibana_modes, "cached"):
        rule = envschema.load_schema()["TPOT_KIBANA_OBJECTS"]
        kibana_modes.cached = [(c["value"], c["label"], c.get("help", "")) for c in rule.choices]
    return kibana_modes.cached


def kibana_info() -> Text:
    text = Text()
    text.append("Kibana objects", style=f"bold {theme.color('glass')}")
    text.append("  T-Pot's dashboards, visualizations and searches. Update brings in the ones of this release and "
                "keeps the ones you changed, Overwrite all replaces those too, Restore brings an export back; each "
                "asks first and runs once. An export always comes first (Manager: ~/tpot_backups, T-Pot: "
                "data/kibana_objects/exports). The mode is what T-Pot does on every start.",
                style=theme.color("mist"))
    return text


# the refused objects a note names when the result dialog cannot be shown (another screen is open)
RESULT_REFUSED_SHOWN = 5

def overwrite_mode_question() -> ConfirmDialog:
    """The question before TPOT_KIBANA_OBJECTS=overwrite is saved, on the Elastic and the Settings page; #no has
    the focus."""
    from tpotctl import cli
    return ConfirmDialog(cli.KIBANA_OVERWRITE_QUESTION,
                         Text(cli.KIBANA_OVERWRITE_EVERY_START, style=theme.color("glass")),
                         yes="Choose overwrite", no="Back")


def mode_effect(mode: str, chosen, given_up=None) -> str:
    """What `mode` would do on the next start, for Kibana as the plan `chosen` read it; given_up: the
    objects the first import gave up on (kibana_objects.given_up), None without its marker."""
    from tpotctl.cli import _objects, kibana_given_up

    def are(items, what: str) -> str:
        return f"{_objects(len(items), what)} {'is' if len(items) == 1 else 'are'}"
    if chosen.new and not (chosen.update or chosen.current or chosen.yours):
        # kibana_objects.needs_first_import: the first import runs again, in every mode, unless it gave up
        if given_up is not None:
            return f"On the next start: nothing is imported, {kibana_given_up(given_up)}."
        return ("Kibana holds none of T-Pot's objects: the next start imports them, without your advanced "
                "settings.")
    if mode == "keep":
        return "On the next start: nothing is imported."
    todo = chosen.to_import(mode)
    if not todo:
        return "On the next start: nothing to import."
    if mode == "add":
        return f"On the next start: {are(todo, 'new Kibana objects')} imported, nothing is replaced."
    kept = f", {are(chosen.yours, 'objects of yours')} kept" if mode == "update" and chosen.yours else ""
    replaced = (f", {are(chosen.yours, 'objects of yours')} replaced" if mode == "overwrite" and chosen.yours
                else "")
    return (f"On the next start: {are(todo, 'Kibana objects')} imported after an export ({len(chosen.new)} new)"
            f"{kept}{replaced}.")


def foreign_kept(value: str, where: str) -> str:
    """TPOT_ES_RETENTION written while the ILM policy tpot was changed in Kibana: the policy stays as it is
    until it is taken over or .env is applied to it (where says how)."""
    return (f"TPOT_ES_RETENTION={value} is set in .env. The ILM policy tpot was changed in Kibana, T-Pot leaves it "
            f"as it is: {where}")


def because(text: str, problem: str) -> str:
    """text, then why Elasticsearch could not be used: no answer, or one T-Pot cannot read."""
    return f"{text} Why: {problem}." if problem else text


def retention_unknown(value: str, host: bool = True) -> str:
    """A TPOT_ES_RETENTION that is not shorter than .env, written while Elasticsearch does not answer (as
    tpot elastic says it): the policy is unknown, it may still keep the events longer. Off the T-Pot host
    there is no tpot elastic: the text says where it runs."""
    then = ("a shorter value waits for 'tpot elastic retention --force' (it asks first)." if host else
            "a shorter value waits: apply it on the T-Pot host with 'tpot elastic retention --force' (it asks "
            "first).")
    return (f"TPOT_ES_RETENTION={value} is set. T-Pot applies it on its next start if the ILM policy tpot keeps "
            f"the events less long; {then}")


# What a yes does for a finding beyond its line (ops.LOSS_KEYS)
LOSS_NOTES = {
    "compose": "Adapt lets update.sh back up your compose file and adapt it to this release (update.sh "
               "--adapt-compose): what the line names as not carried over is not in the new file, the backup "
               "keeps your file. Back changes nothing; adapt it yourself (tpot edition set, compose/customizer.py "
               "or by hand), then update again.",
    "elastic-step": "Yes lets update.sh take the step through Elasticsearch 8.19: the data goes into the backup "
                    "of the update first, then Elasticsearch 8.19 (and Kibana 8.19 where the edition has it) runs "
                    "once on it, then the new release. Should the step not go through, the update says how that "
                    "backup puts the data from before back.",
}


def data_loss_text(items) -> Text:
    """The body of the question before an update that loses data: one line per finding of
    update.sh --check-data-loss (with what yes does where LOSS_NOTES says more), then what yes means."""
    text = Text()
    for key, line in items:
        text.append(f"{glyphs.g('fail')} ", style=theme.color("error"))
        text.append(f"{line}\n", style=theme.color("mist"))
        if key in LOSS_NOTES:
            text.append(f"  {LOSS_NOTES[key]}\n", style=theme.color("ash"))
    if any(key != "compose" for key, _line in items):
        yes = "Adapt" if any(key == "compose" for key, _line in items) else "Yes"
        text.append(f"\nThe backup of the update cannot bring this back. {yes} confirms these and nothing else "
                    "(update.sh --accept-data-loss-file): a loss the run finds beyond them is refused.",
                    style=f"bold {theme.color('wax')}")
    else:
        text.append("\nAdapt confirms the adaptation of your compose file and nothing else (update.sh "
                    "--adapt-compose).", style=f"bold {theme.color('wax')}")
    return text


def loss_lines(items) -> str:
    """The confirmed findings for --accept-data-loss-file, as update.sh --check-data-loss printed them."""
    return "\n".join(f"{ops.LOSS_MARK}{key} {line}".rstrip() for key, line in items)


def backup_written(info) -> str:
    """When an archive of update.sh was written: Created: of its MANIFEST, else the date in its name."""
    for line in (info.manifest if info else []):
        if line.startswith("Created:"):
            return line.split(":", 1)[1].strip()
    name = info.name if info else ""
    if len(name) >= 12 and name[:12].isdigit():
        return f"{name[0:4]}-{name[4:6]}-{name[6:8]} {name[8:10]}:{name[10:12]}"
    return "the backup"


def restore_losses(info, groups) -> List[str]:
    """What a restore of data/ loses (the text of restore.sh fuDO_DATA): the files it replaces, the
    Elasticsearch data of a full archive (with its size where the table of contents of the archive names it)."""
    from tpotctl import system
    from tpotctl.cli import _human_size
    where = ("the data path (TPOT_DATA_PATH of the restored .env)" if "config" in groups
             else system.data_path(ops.env_values()))
    losses = [f"The files under {where} are replaced by those of the archive, newer files and log lines "
              "since the backup are lost."]
    if info is not None and info.es_data:
        held = f" The archive holds {_human_size(info.es_size)} of Elasticsearch data." if info.es_size else ""
        losses.append(f"The current Elasticsearch data is deleted and replaced, events since {backup_written(info)} "
                      f"are lost.{held}")
    return losses


def restore_loss_text(info, groups) -> Text:
    """The body of the question before a restore of data/: its losses, and with the elastic group that a
    shorter ILM policy of the archive stays out (the yes is for data/ only)."""
    text = Text()
    for line in restore_losses(info, groups):
        text.append(f"{glyphs.g('fail')} ", style=theme.color("error"))
        text.append(f"{line}\n", style=theme.color("mist"))
    if "elastic" in groups:
        text.append(f"{glyphs.g('bullet')} ", style=theme.color("mist"))
        text.append("An ILM policy of the archive that keeps the events less long than the current one is not put "
                    "back, restore.sh names the command for it.\n", style=theme.color("mist"))
    text.append("\nThe backup cannot bring this back. Yes confirms the restore of data/ only "
                "(restore.sh --accept-data-loss-file).", style=f"bold {theme.color('wax')}")
    return text


def restore_loss_line(info, groups) -> str:
    """The confirmed loss for restore.sh --accept-data-loss-file: data/ only, never the ILM policy."""
    return f"{ops.LOSS_MARK}data " + " ".join(restore_losses(info, groups))


def shortening_text(doomed, value: str) -> Text:
    """The body of the question before a shorter retention: the indices it deletes right away (or none),
    then the rule from now on."""
    text = doomed_text(doomed) if doomed else Text("No index is deleted right away.", style=theme.color("mist"))
    text.append(f"\n\nFrom now on, events older than {value} days are deleted. This cannot be undone.",
                style=f"bold {theme.color('wax')}")
    return text


def doomed_text(doomed, closing: str = "") -> Text:
    """The indices a shorter retention deletes right away, for the question before it."""
    from tpotctl.cli import _doomed_summary, _index_line
    text = Text()
    for index in doomed[:DOOMED_SHOWN]:
        text.append(f"{_index_line(index)}\n", style=theme.color("mist"))
    if len(doomed) > DOOMED_SHOWN:
        text.append(f"... and {len(doomed) - DOOMED_SHOWN} more\n", style=theme.color("mist"))
    text.append(f"\n{_doomed_summary(doomed)} {'is' if len(doomed) == 1 else 'are'} deleted right away.{closing}",
                style=f"bold {theme.color('wax')}")
    return text


class UpdatePane(Vertical):

    def compose(self) -> ComposeResult:
        yield Static("", id="update-info", classes="info")
        with Horizontal(classes="actions"):
            yield Button("Update", id="run-update", variant="primary")
            yield Button("Update and start T-Pot", id="run-update-start")
            yield Button("Restore a backup", id="run-restore")
            yield Button("Uninstall ...", id="run-uninstall", variant="error")
        with Horizontal(classes="actions"):
            yield Checkbox("Full backup with data/ (for a newer Elastic Stack)", False, id="update-full")
            yield Button("Rebuild the Manager's packages", id="run-setup")
        yield NavDataTable(id="backups", cursor_type="row")

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
            self.app.run_task(Task("Rebuild the Python packages of the T-Pot Manager", [LAUNCHER, "setup", "--force"],
                                   intro="tpot setup --force builds the venv of the T-Pot Manager anew from requirements.txt "
                                         "(needs pypi.org), next to the old one, and swaps them when it works. "
                                         "The T-Pot Manager starts anew afterwards.",
                                   done="The packages of the T-Pot Manager are rebuilt.", restart_tpot=True))
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
    ("elastic", "Elastic", ElasticPane, "host+hive"),
    ("update", "Update & backup", UpdatePane, "host"),
]
NOT_HERE = "needs a T-Pot host: Linux with systemd. On macOS and Windows T-Pot runs in Docker Desktop."
ON_A_SENSOR = {"users": "a SENSOR has no web UI, its events go to its HIVE.",
               "sensors": "sensors are managed on their HIVE, this is a SENSOR.",
               "elastic": "a SENSOR has no Elasticsearch, its events go to its HIVE."}
# what does the job of a locked page on the command line or elsewhere
INSTEAD = {"status": "docker compose ps, or tpot status on the T-Pot host",
           "sensors": "tpot sensors on the HIVE", "images": "docker images",
           "checks": "tpot check on the T-Pot host", "update": "git pull in ~/tpotce, then tpot customize",
           "users": "tpot users on the HIVE",
           "elastic": f"tpot elastic on the HIVE, or {KIBANA_ILM}"}
SHORT = {"edition": "Edition", "users": "Users", "update": "Update"}
REPAINT = {"status": "repaint", "edition": "show", "settings": "check", "llm": "check", "users": "show", "sensors": "load",
           "images": "load", "checks": "show", "elastic": "load", "update": "show"}


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
    """The T-Pot Manager: the menu of the command tpot."""

    CSS_PATH = "tpot.tcss"
    TITLE = "T-Pot Manager"
    COMMANDS = App.COMMANDS | {TpotCommands}
    COMMAND_PALETTE_BINDING = nav.FIND_KEY
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (100, "-normal"), (150, "-wide")]
    VERTICAL_BREAKPOINTS = [(0, "-short"), (34, "-tall")]
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("c", "customize", "Customizer"),
        Binding("r", "restart_service", "Restart T-Pot", show=False),
        Binding("f2", "next_icons", "Icons"),
        Binding("escape", "menu", "Menu", show=False),
        nav.FIND,
        *nav.BINDINGS,
    ]

    def __init__(self, backend: Optional[Backend] = None, runner: Optional[Callable] = None, splash: bool = False,
                 engine: Optional[Callable] = None):
        super().__init__()
        self.backend = backend or Backend()
        self.runner = runner or Runner(self)
        self.engine = engine
        self.splash = splash
        # the notices while the splash runs (a start problem of .env, a refresh): told when it ends
        self.held_notices: Optional[List[tuple]] = [] if splash else None
        # an RLock: a notice told from within the telling of the held ones (same thread) must not lock up
        self._held_lock = threading.RLock()
        apply_theme(self)
        self.panes = list(PANES)
        self.locked = {key: why for key, *_rest in PANES for why in [self.why_locked(key)] if why}
        self.quit_dialog = None         # "Quit the T-Pot Manager?" while it is open
        self.update_checking = False    # update.sh --check-data-loss runs before an update
        self.header_stamp = None        # the configuration the header was read from
        self.read_error_stamp = None    # the configuration a refresh could not read, told once

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
            self.push_screen(SplashScreen(ops.tpot_version(ops.REPO_DIR)))
        else:
            self.release_notices()          # no room for the splash: nothing to wait for
        if "status" in self.locked:
            self.load_header()

    def load_header(self) -> None:
        """The header anew; the stamp of the configuration it reads is noted here, on the UI thread."""
        self.header_stamp = self.backend.config_stamp()
        self.fetch_header()

    @work(thread=True, exclusive=True, group="header")
    def fetch_header(self) -> None:
        try:
            state = self.backend.status()
        except ops.OpsError:
            return
        self.call_from_thread(self.update_header, state)

    def update_header(self, state: ops.Status, stamp=None, containers=None, machine=None) -> None:
        header = self.query(TpotHeader)
        if not header:                      # the T-Pot Manager ends, a late status worker delivers anyway
            return
        if stamp is not None:               # what it was read from (the status page brings it)
            self.header_stamp = stamp
        from textual.css.query import NoMatches
        try:
            header.first().repaint(state, containers, machine)
        except NoMatches:
            if self.is_running and not (self._exit or self._closing):
                raise
            # the T-Pot Manager ends meanwhile (exit(), or the shutdown that stops running first), the
            # header is going

    def paint_menu(self) -> None:
        current = self.query_one(ContentSwitcher).current
        for key, title, _cls, _needs in self.panes:
            item = self.query_one(f"#menu-{key}", ListItem)
            locked = key in self.locked
            item.query_one(".menu-long", Label).update(menu_text(key, title, key == current, locked))
            item.query_one(".menu-short", Label).update(menu_text(key, SHORT.get(key, title), key == current, locked))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None and event.item.id:
            key = event.item.id[len("menu-"):]
            self.query_one(ContentSwitcher).current = key
            self.paint_menu()
            self.page_shown(key)

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
        """Not while a script runs: quitting would kill it half way. Unsaved changes and values
        T-Pot would not start with are named first."""
        if self.task_running():
            self.notify("A script is still running, the T-Pot Manager ends when it is through.", title="Not now",
                        severity="warning", timeout=5)
            return
        if self.quit_dialog is not None and self.screen is self.quit_dialog:
            self.exit()             # ctrl+q on "Quit the T-Pot Manager?": the second ask is the yes
            return
        from tpotctl.screens.splash import SplashScreen
        if isinstance(self.screen, SplashScreen):
            self.screen.leave()     # ctrl+q ends the splash too, then quits as from the menu
        if len(self.screen_stack) > 1:
            self.notify("Close this screen first (esc), then quit.", title="Not now", timeout=4)
            return
        await self.refresh_config()             # what the files say now, not when the page read them
        pending = self.quit_pending()
        if not pending:
            await super().action_quit()
            return
        body = Text()
        for number, (_page, line, style) in enumerate(pending):
            body.append(("\n" if number else "") + f"{glyphs.g('bullet')} {line}", style=theme.color(style))

        def answered(yes: bool) -> None:
            self.quit_dialog = None
            if yes:
                self.exit()
            else:
                self.goto(pending[0][0])
        self.quit_dialog = QuitDialog("Quit the T-Pot Manager?", body, yes="Quit anyway", no="Back")
        self.push_screen(self.quit_dialog, answered)

    def quit_pending(self):
        """[(page, line, colour)]: the unsaved changes of the settings pages, then the values of the
        saved .env T-Pot would not start with."""
        titles = dict((key, title) for key, title, *_rest in PANES)
        pending = []
        for pane in self.query(SettingsPane):
            changes = pane.changes()
            if changes:
                pending.append((pane.id, f"Unsaved on the {titles.get(pane.id, pane.id)} page: "
                                         f"{', '.join(changes)}", "warn"))
        for problem in self.start_problems():
            pending.append((problem.page, f"T-Pot would not start: {problem.key} {problem.text}. {problem.fix}.",
                            "error"))
        return pending

    def check_action(self, action: str, parameters):
        # the customizer and the dialogs are screens of their own, c there would open a second one
        if action in ("customize", "restart_service") and len(self.screen_stack) > 1:
            return False
        # ctrl+f is a priority binding: over the splash it would open the palette on top of it, and the
        # splash would freeze under it; there the key ends the splash like any other. Over a dialog on top
        # of the splash the palette is the dialog's as anywhere else
        if action == "command_palette":
            from tpotctl.screens.splash import SplashScreen
            if isinstance(self.screen, SplashScreen):
                return False
        return True

    def action_menu(self) -> None:
        """esc: back to the menu on the left."""
        if len(self.screen_stack) == 1:
            self.query_one("#sidebar", ListView).focus()

    def notify(self, message, *args, markup: bool = False, **kwargs):
        """Notices carry paths and error texts: never read them as markup (a [/...] in a path would
        raise while the toast renders). While the splash runs they wait (release_notices)."""
        with self._held_lock:
            if self.held_notices is not None:
                self.held_notices.append((message, args, dict(kwargs, markup=markup)))
                return None
        return super().notify(message, *args, markup=markup, **kwargs)

    def release_notices(self) -> None:
        """The splash is gone: the notices of its time in their order, with their options; their time
        counts from now. They are told under the lock: a worker's notice meanwhile waits and comes after
        them, one told from within the telling joins the end of the list."""
        with self._held_lock:
            held = self.held_notices or []
            told = 0
            while told < len(held):
                message, args, kwargs = held[told]
                told += 1
                super().notify(message, *args, **kwargs)
            self.held_notices = None

    def action_nav(self, direction: str) -> None:
        """The arrows: in the menu right opens the page (up / down are the menu's own), elsewhere
        widgets/nav.py."""
        if self.focused is self.query("#sidebar").first() and len(self.screen_stack) == 1:
            if direction == "right":
                self.action_enter_page()
            return
        nav.navigate(self, direction)

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
            self.page_shown(key)

    def page_shown(self, key: str) -> None:
        """A page comes into view: the pages that show .env read what changed meanwhile; the Elastic
        page reads its status anew (unless a read is under way: one at a time, ElasticPane.load), the
        Checks page the last check and the state of ewsposter."""
        if key in CONFIG_PAGES:
            self.config_maybe_changed()
        if key == "elastic":
            for pane in self.query(ElasticPane):
                pane.load()
        if key == "checks":                     # a check run elsewhere (tpot check) meanwhile
            for pane in self.query(ChecksPane):
                pane.load()

    def setting_keys(self):
        pane = self.query("#settings")
        current = getattr(pane.first(), "current", None) if pane else None
        if current is None:
            return []
        return [(rule.key, rule.title) for rule in current.relevant()]

    def start_problems(self) -> List[StartProblem]:
        """The errors of the saved .env (what tpotinit would refuse), each with the way to fix it."""
        from tpotctl.settings import MANAGED_BY
        pane = self.query("#settings")
        current = getattr(pane.first(), "current", None) if pane else None
        if current is not None and pane.first().stale():
            current = None          # read anew below; refresh_config brings the page along
        try:
            current = current if current is not None else self.backend.settings()
            problems = current.problems()
        except Exception:      # SettingsError, OSError: the Settings page says so itself
            return []
        fixes, found = current.fixes(), {}
        for problem in problems:
            if problem.level != "error" or problem.key in found:
                continue
            key, page = problem.key, "settings"
            if key in fixes:
                value, page_pane = fixes[key], pane.first() if pane else None
                if page_pane is not None and getattr(page_pane, "draft", {}).get(key) == value:
                    fix = f"Settings has {value} ready, Save it"
                elif key in getattr(page_pane, "declined", ()):
                    fix = f"Set it to {value} on the Settings page (palette: Fix {key})"
                else:           # not loaded yet, or a value of your own in the draft
                    fix = f"Set it to {value} on the Settings page"
                if value != "linux":
                    try:        # (name, base): CUSTOM from MAC_WIN runs MAC_WIN too
                        name, base = (part.lower() for part in self.backend.edition_current())
                    except Exception:
                        name, base = "unknown", ""
                    if "mac_win" in (name, base):
                        pass
                    elif name in ("none", "unknown"):
                        fix += " (Docker Desktop runs the MAC_WIN edition)"
                    else:
                        fix += " and switch to the MAC_WIN edition on Edition & services"
            elif key == "WEB_USER":
                fix, page = "Add one on the Web users page (tpot users add)", "users"
            elif key in MANAGED_BY:
                fix, page = f"It is managed with {MANAGED_BY[key]}", "sensors"
            else:
                fix = "Fix it on the Settings page"
            found[key] = StartProblem(key, problem.text.rstrip(". "), fix, page)     # the callers add ". "
        return list(found.values())

    def setting_fixes(self):
        """{key: value} of fixed settings that do not fit this host (TPOT_OSTYPE)."""
        pane = self.query("#settings")
        current = getattr(pane.first(), "current", None) if pane else None
        return current.fixes() if current is not None else {}

    def fix_setting(self, key: str) -> None:
        """The palette's Fix entry: the proposal again (also after a Revert), then the setting."""
        pane = self.query_one("#settings", SettingsPane)
        pane.declined.discard(key)

        async def again() -> None:
            await pane.reload(keep=pane.changes(), keep_unlocked=pane.unlocked)
            self.goto_setting(key)
        self.call_later(again)

    def goto_setting(self, key: str) -> None:
        self.goto("settings")
        pane = self.query_one("#settings", SettingsPane)
        if not pane.stale():
            pane.focus_setting(key)
            return

        async def after_the_refresh() -> None:  # the rows are built anew, the jump goes to the new one
            await self.refresh_config()
            pane.focus_setting(key)
        self.call_later(after_the_refresh)

    # -- look ----------------------------------------------------------------

    def action_next_icons(self) -> None:
        self.set_icons(glyphs.MODES[(glyphs.MODES.index(glyphs.mode()) + 1) % len(glyphs.MODES)])

    def set_icons(self, mode: str) -> None:
        glyphs.set_mode(mode)
        chosen = prefs.load(environment=False)
        chosen.icons = mode
        self.remember(chosen, f"Icons {mode}" + (", they need a Nerd Font in your terminal" if mode == "nerd" else ""))

    def set_colors(self, mode: str) -> None:
        """Textual picks its colour system when it starts: the choice counts from the next start."""
        chosen = prefs.load(environment=False)
        chosen.colors = mode
        self.remember(chosen, f"Colours {mode}, from the next start of the T-Pot Manager")

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
        """update.sh, after its check for a loss of data: the task screen has no terminal for update.sh to
        ask on, so the question is asked here first. One check at a time, the Update buttons wait for it."""
        if self.update_checking:
            return
        self.update_checking = True
        self.update_buttons()
        # the check fetches and asks the registry, it may take a while
        self.notify("Checking for a loss of data …", title="Update", timeout=6)
        self.run_worker(self.checked_update(args), group="update-check")

    def update_buttons(self) -> None:
        for button in self.query("#run-update, #run-update-start"):
            button.disabled = self.update_checking

    async def checked_update(self, args: List[str]) -> None:
        import asyncio
        # the check reads what the run would do: the backup of --full, not -y or -s
        check = [arg for arg in args if arg in ("--full", "-F")]
        try:
            ok, items, why = await asyncio.to_thread(self.backend.check_data_loss, check)
        except Exception as err:       # noqa: BLE001 - a broken check is told as one, the app goes on
            ok, items, why = False, [], str(err)
        finally:
            self.update_checking = False
            self.update_buttons()
        if len(self.screen_stack) > 1:
            # another screen opened while the check ran (the palette, a question of another page): neither
            # the question nor the task screen lands on top of it, Update checks anew
            self.notify("The check for a loss of data is done, but another screen was open: press Update again.",
                        title="Update", severity="warning", timeout=10)
            return
        if ok and not items:
            self.start_update(args)
            return
        # a yes confirms what the dialog shows and nothing else: the lines of the check, in a file
        # (--accept-data-loss-file); never the bare --accept-data-loss, which confirms any loss
        confirmed = ""
        if ok and any(key == "compose" for key, _line in items):
            # a compose file of your own: Adapt is --adapt-compose, the other findings go into the file as ever
            others = [(key, line) for key, line in items if key != "compose"]
            # Adapt also confirms the other losses: a title that names them all
            title = "Go on with these changes?" if others else "Adapt your compose file?"
            dialog = ConfirmDialog(title, data_loss_text(items), yes="Adapt", no="Back")
            confirmed = loss_lines(others)
            args = list(args) + ["--adapt-compose"]
        elif ok:
            dialog = ConfirmDialog("Accept this loss of data?", data_loss_text(items), yes="Update and lose it",
                                   no="Back")
            confirmed = loss_lines(items)
        else:
            body = Text("The T-Pot Manager could not check for a loss of data; update.sh refuses any then.",
                        style=theme.color("mist"))
            if why:
                body.append(f"\n\n{why}", style=theme.color("ash"))
            dialog = ConfirmDialog("Update without the check?", body, yes="Update", no="Back")
        self.push_screen(dialog, lambda yes: self.start_update(args, confirmed) if yes else None)

    def start_update(self, args: List[str], confirmed: str = "") -> None:
        """update.sh in the task screen; the checkout below this app changes, so the T-Pot Manager starts anew.
        confirmed: the @@tpot loss lines the user said yes to, handed over as --accept-data-loss-file."""
        from tpotctl.screens.task import Task
        intro = Text("update.sh stops T-Pot, writes a backup to ~/tpot_backups, pulls the release of your "
                     "branch and puts your edition and settings back.", style=theme.color("glass"))
        if "-s" in args:
            intro.append(" T-Pot starts again afterwards.", style=theme.color("glass"))
        intro.append("\nupdate.sh asks before it deletes anything it cannot restore.", style=theme.color("glass"))
        self.run_task(Task("Update T-Pot", ops.script_command("update.sh", args), become="-B", intro=intro,
                           done="T-Pot is updated. Restart the T-Pot Manager to use its new version.",
                           secrets={"--accept-data-loss-file": confirmed} if confirmed else {},
                           restart_tpot=True))

    def run_restore(self) -> None:
        """restore.sh -g in the task screen. It has no terminal to ask on there: the restore of data/
        (it replaces the current files and the Elasticsearch data) is asked here first, a yes runs it
        with --accept-data-loss-file and a data line only (a shorter ILM policy stays out)."""
        from tpotctl.screens.restore import RestoreScreen
        screen = RestoreScreen(self.backend.backup_infos)

        def chosen(choice) -> None:
            if not choice:
                return
            path, groups = choice
            if "data" not in groups:
                self.start_restore(path, groups)
                return
            info = next((backup for backup in screen.backups if backup.path == path), None)
            confirmed = restore_loss_line(info, groups)
            self.push_screen(ConfirmDialog("Replace the current data with the backup?",
                                           restore_loss_text(info, groups), yes="Restore and lose it", no="Back"),
                             lambda yes: self.start_restore(path, groups, confirmed) if yes else None)

        self.push_screen(screen, chosen)

    def start_restore(self, path: str, groups: List[str], confirmed: str = "") -> None:
        from tpotctl.screens.task import Task
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
        if "elastic" in groups:
            intro.append("An ILM policy of the archive that keeps the events less long than the current one is "
                         "not put back, restore.sh names the command for it.\n", style=theme.color("mist"))
        intro.append("T-Pot is stopped for it.", style=theme.color("mist"))
        args = ["-f", path, "-g", ",".join(groups)]
        self.run_task(Task("Restore a backup", ops.script_command("restore.sh", args),
                           become="-B", intro=intro, done="The backup is restored.",
                           secrets={"--accept-data-loss-file": confirmed} if confirmed else {},
                           restart_tpot=bool({"git", "patch"} & set(groups))),
                      lambda _code: self.query_one("#update", UpdatePane).show())

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
        unsaved changes stay, but a value just saved replaces an unsaved one of the same key (said
        only when the two differ)."""
        for pane in self.query(SettingsPane):
            if pane is not source:
                await self.reload_keeping(pane, saved_keys, "the value just saved replaces")
        if "TPOT_ES_RETENTION" in saved_keys:
            for pane in self.query(ElasticPane):
                pane.load()

    def config_maybe_changed(self) -> None:
        """After an action of tpot that may have written .env or the compose file, and when a page that
        shows them is opened: the pages read anew what changed (no polling)."""
        self.call_later(self._config_changed)

    async def _config_changed(self) -> None:
        await self.refresh_config()
        for pane in self.query(UsersPane):
            if pane.stale():
                pane.show()
        for pane in self.query(SensorsPane):
            if pane.stale():
                pane.load()
        if self.header_stamp is None or self.backend.config_stamp() != self.header_stamp:
            self.load_header()          # it notes the stamp it reads from

    async def refresh_config(self) -> None:
        """.env or the compose file changed elsewhere (another page of the T-Pot Manager, a task, a terminal): the
        settings pages read them anew, keeping what is unsaved like after a save on the other page."""
        for pane in self.query(SettingsPane):
            if not pane.stale():
                continue
            if pane.current is None:            # it could not read them before: nothing to keep
                await pane.reload()
                continue
            old = dict(pane.current.values)
            try:
                fresh = self.backend.settings().values
            except Exception as err:        # SettingsError, OSError, docker: keep the pages and their drafts
                stamp = self.backend.config_stamp()
                if stamp != self.read_error_stamp:      # once for every page, once per outage
                    self.read_error_stamp = stamp
                    self.notify(f"{str(err).rstrip('. ')}. The pages keep what they show until it can be read "
                                f"again.",
                                title="Settings could not be read", severity="warning", timeout=8)
                continue
            self.read_error_stamp = None
            changed = {key for key in set(old) | set(fresh) if old.get(key) != fresh.get(key)}
            await self.reload_keeping(pane, changed, "the value in .env changed meanwhile and replaces")

    async def reload_keeping(self, pane, changed: Set[str], why: str) -> None:
        """Reload a settings page: its unsaved changes stay, a key changed in the file takes the file's
        value, and a notice names it when that differs from the unsaved one."""
        titles = dict((key, title) for key, title, *_rest in PANES)
        own = pane.changes()
        keep = {key: value for key, value in own.items() if key not in changed}
        await pane.reload(keep=keep, keep_unlocked=pane.unlocked & set(keep))
        now = pane.current.values if pane.current is not None else {}
        lost = sorted(key for key in set(own) & changed if own[key] != now.get(key, ""))
        if lost:
            self.notify(f"{', '.join(lost)}: {why} the unsaved one on the {titles.get(pane.id, pane.id)} page",
                        title="Settings", timeout=8)

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
                return
            self.config_maybe_changed()         # a script or tpot command may have written .env / compose
            if then is not None:
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
                ["--web-user", secrets[0]] if secrets else []),
                become="--become-file" if self.backend.linux_host() else "",
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
        self.config_maybe_changed()

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
                self.config_maybe_changed()     # other services: other settings apply
            else:
                self.notify("Test it with: docker compose -f docker-compose-custom.yml up, then replace "
                            "docker-compose.yml with it.", title="docker-compose-custom.yml is written", timeout=15)

        self.push_screen(ConfirmDialog(
            "docker-compose-custom.yml is written. Replace docker-compose.yml with it and restart T-Pot now?",
            Text("Not sure? Choose 'Not now' and test it first with docker compose -f "
                 "docker-compose-custom.yml up.", style=theme.color("mist")), yes="Replace and restart", no="Not now"), replace)


class CustomizerApp(nav.ArrowNav, App):
    """compose/customizer.py on its own: the customizer screen, returns the Selection."""

    CSS_PATH = "tpot.tcss"
    TITLE = "T-Pot customizer"
    COMMAND_PALETTE_BINDING = nav.FIND_KEY
    BINDINGS = [nav.FIND, *nav.BINDINGS]

    def notify(self, message, *args, markup: bool = False, **kwargs):
        """Notices carry paths and error texts: never read them as markup."""
        return super().notify(message, *args, markup=markup, **kwargs)

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
