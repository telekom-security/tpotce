"""Restore a backup: which archive, which of its parts. restore.sh -g does the work.

The parts are the groups of restore.sh (ops.GROUP_TEXT), only the ones the archive
holds are offered. The rollback of the checkout (git reset --hard) and data/ (with a
full archive the Elasticsearch data, every event since the backup) are left unchecked:
they replace more than a person expects from "restore".
"""

from typing import Callable, List

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, OptionList, Static
from textual.widgets.option_list import Option

from tpotctl import glyphs, ops, theme
from tpotctl.widgets.nav import NavOptionList, NavScroll

# checked to begin with; git and data only on request
DEFAULT_GROUPS = ("patch", "config", "untracked", "elastic")


class RestoreScreen(Screen):
    """Dismissed with (archive path, [groups]) or None."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, load: Callable[[], List[ops.BackupInfo]]):
        super().__init__(id="restore")
        self.load = load
        self.backups: List[ops.BackupInfo] = []

    def compose(self) -> ComposeResult:
        with NavScroll(id="restore-body"):
            yield Static(Text("Restore a backup", style=f"bold {theme.color('magenta')}"), id="restore-title")
            yield Static(Text("Choose the archive and what of it comes back. restore.sh stops T-Pot for "
                              "everything but the Kibana objects and starts it again for them.",
                              style=theme.color("glass")), classes="restore-intro")
            yield NavOptionList(id="restore-list")
            yield Static("", id="restore-manifest")
            with Vertical(id="restore-groups"):
                for group, question in ops.GROUP_TEXT.items():
                    yield Checkbox(question, group in DEFAULT_GROUPS, id=f"group-{group}")
            yield Static("", id="restore-hint")
        with Horizontal(id="restore-nav"):
            yield Static("", classes="task-spacer")
            yield Button("Back", id="restore-back")
            yield Button("Restore", id="restore-go", variant="primary")
        yield Footer()

    def option_text(self, info: ops.BackupInfo) -> Text:
        text = Text()
        text.append(info.name, style=f"bold {theme.color('glass')}")
        text.append(f"  {info.size / 1024 / 1024:.0f} MB, {info.kind}", style=theme.color("ash"))
        if info.problem:
            text.append(f"  {glyphs.g('fail')} {info.problem}", style=theme.color("error"))
        return text

    def on_mount(self) -> None:
        # reading the headers of a full archive takes a while: in a thread
        self.query_one("#restore-go", Button).disabled = True
        for box in self.query(Checkbox):
            box.display = False
        self.query_one("#restore-hint", Static).update(Text("reading the backups ...", style=theme.color("mist")))
        self.fetch()

    @work(thread=True, exclusive=True, group="restore")
    def fetch(self) -> None:
        try:
            infos = self.load()
        except OSError:
            infos = []
        self.app.call_from_thread(self.loaded, infos)

    def loaded(self, infos: List[ops.BackupInfo]) -> None:
        self.backups = infos
        self.query_one("#restore-hint", Static).update("")
        listing = self.query_one("#restore-list", OptionList)
        listing.add_options([Option(self.option_text(info), id=str(index)) for index, info in enumerate(infos)])
        if not self.backups:
            self.query_one("#restore-hint", Static).update(
                Text("There is no backup in ~/tpot_backups yet, update.sh writes one before every update.",
                     style=theme.color("mist")))
            self.query_one("#restore-go", Button).disabled = True
            for box in self.query(Checkbox):
                box.display = False
            return
        listing.highlighted = 0
        listing.focus()
        self.show(0)

    def selected(self):
        index = self.query_one("#restore-list", OptionList).highlighted
        return self.backups[index] if index is not None and index < len(self.backups) else None

    def show(self, index: int) -> None:
        info = self.backups[index]
        manifest = Text()
        for line in info.manifest:
            manifest.append(f"{line}\n", style=theme.color("mist"))
        self.query_one("#restore-manifest", Static).update(manifest)
        for group in ops.GROUP_TEXT:
            self.query_one(f"#group-{group}", Checkbox).display = group in info.groups
        self.refresh_go()

    def chosen(self) -> List[str]:
        info = self.selected()
        if info is None:
            return []
        return [g for g in ops.GROUP_TEXT if g in info.groups and self.query_one(f"#group-{g}", Checkbox).value]

    def refresh_go(self) -> None:
        self.query_one("#restore-go", Button).disabled = not self.chosen()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "restore-list" and event.option_index is not None:
            self.show(event.option_index)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self.refresh_go()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "restore-go":
            info, groups = self.selected(), self.chosen()
            if info is not None and groups:
                self.dismiss((info.path, groups))
        elif event.button.id == "restore-back":
            self.dismiss(None)

    def action_back(self) -> None:
        self.dismiss(None)
