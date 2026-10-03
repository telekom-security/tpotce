"""Edition & services: the customizer as a Textual screen.

All logic is in compose/customizer_core.py and the State of compose/customizer_tui.py,
this screen only shows it. It is dismissed with the chosen Selection or None.
"""

import os
import sys
from typing import Dict, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Label, Static, Tree

from tpotctl.bootstrap import REPO_DIR
from tpotctl.screens.dialogs import ChoiceDialog, ConfirmDialog, PortInputDialog, finding_lines

sys.path.insert(0, os.path.join(REPO_DIR, "compose"))
import customizer_core as core  # noqa: E402
from customizer_tui import State  # noqa: E402

MARK_ON, MARK_OFF, MARK_LOCKED = "☑", "☐", "▣"


class ServiceTree(Tree):
    """space switches a service (or a whole group), enter folds a group."""

    BINDINGS = [Binding("space", "screen.toggle", "Switch", show=True)]


class CustomizerScreen(Screen):

    BINDINGS = [
        Binding("p", "ports", "Ports"),
        Binding("b", "base", "Edition"),
        Binding("s", "save", "Save"),
        Binding("escape,q", "leave", "Back"),
    ]

    def __init__(self, catalog: core.Catalog, selection: core.Selection, max_networks: int):
        super().__init__()
        self.state = State(catalog, selection, max_networks)
        self.catalog = catalog
        self.nodes: Dict[str, object] = {}
        self.groups: Dict[str, object] = {}

    def compose(self) -> ComposeResult:
        yield Static("", id="cz-status", classes="bar")
        with Horizontal(id="cz-body"):
            tree = ServiceTree("services", id="cz-tree")
            tree.show_root = False
            yield tree
            yield Static("", id="cz-detail")
        yield Static("", id="cz-message")
        yield Footer()

    def on_mount(self) -> None:
        self.build_tree()
        self.query_one(ServiceTree).focus()

    # -- drawing -------------------------------------------------------------

    def build_tree(self) -> None:
        tree = self.query_one(ServiceTree)
        tree.clear()
        self.nodes, self.groups = {}, {}
        for group, title in core.GROUPS:
            members = self.state.visible(group)
            if not members:
                continue
            node = tree.root.add("", data=("group", group), expand=group not in self.state.folded)
            self.groups[group] = node
            for name in members:
                self.nodes[name] = node.add_leaf("", data=("service", name))
        self.refresh_view()
        tree.move_cursor(tree.root.children[0] if tree.root.children else None)

    def service_label(self, name: str) -> Text:
        state, result = self.state, self.state.result
        selected = state.selected(name)
        mark = MARK_LOCKED if selected and state.locked(name) else MARK_ON if selected else MARK_OFF
        problems = result.problems_of(name)
        style = "bold red" if problems else "green" if selected else ""
        label = Text()
        label.append(f"{mark} ", style=style)
        label.append(f"{name:<27}", style=style)
        label.append(self.catalog.description(name), style="dim" if not selected else "")
        if problems:
            label.append("  !", style="bold red")
        return label

    def refresh_view(self) -> None:
        state, result = self.state, self.state.result
        for group, node in self.groups.items():
            node.set_label(Text(f"{core.GROUP_TITLES.get(group, group)}  "
                                f"{sum(state.selected(n) for n in state.visible(group))}/"
                                f"{len(state.visible(group))}", style="bold"))
        for name, node in self.nodes.items():
            node.set_label(self.service_label(name))
        bar = self.query_one("#cz-status", Static)
        bar.update(f"Edition & services  ·  base {state.selection.base} ({result.role()})  ·  "
                   f"{len(result.services)} services  ·  networks {len(result.networks)}/{state.max_networks}  ·  "
                   f"{len(result.errors)} errors")
        bar.set_class(bool(result.errors), "-errors")
        self.show_detail()
        message = self.query_one("#cz-message", Static)
        message.update(Text(state.message, style="yellow") if state.message else "")
        state.message = ""

    def current(self):
        node = self.query_one(ServiceTree).cursor_node
        return node.data if node is not None and node.data else ("", "")

    def show_detail(self) -> None:
        kind, name = self.current()
        state, result = self.state, self.state.result
        text = Text()
        if kind == "service":
            source = result.sources.get(name) or self.catalog.source(state.selection.base, name)
            origin = f"{source.edition} edition" if source.edition else "catalog"
            text.append(f"{name}\n", style="bold")
            text.append(f"{self.catalog.description(name)}\n", style="")
            text.append(f"from the {origin}\n\n", style="dim")
            ports = [state.mapping(name, p).split(" (")[0] for p in state.original_ports(name)]
            text.append("Ports\n", style="bold")
            text.append(("\n".join(f"  {p}" for p in ports) or "  none") + "\n\n")
            why = state.locked(name) if state.selected(name) else ""
            if why:
                text.append(why + "\n\n", style="dim")
            for finding in result.problems_of(name):
                text.append(f"! {finding.text}\n", style="bold red")
        elif kind == "group":
            text.append("space switches the whole group, enter folds it\n", style="dim")
        others = [f for f in result.errors if not f.services] + result.warnings
        for finding in others:
            text.append(f"\n{finding.level}: {finding.text}", style="red" if finding.level == "error" else "yellow")
        self.query_one("#cz-detail", Static).update(text)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self.show_detail()

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        if event.node.data and event.node.data[0] == "group":
            self.state.folded.discard(event.node.data[1])

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed) -> None:
        if event.node.data and event.node.data[0] == "group":
            self.state.folded.add(event.node.data[1])

    # -- actions -------------------------------------------------------------

    def action_toggle(self) -> None:
        kind, name = self.current()
        if kind == "group":
            self.state.toggle_group(name)
        elif kind == "service":
            self.state.toggle(name)
        self.refresh_view()

    def action_ports(self) -> None:
        kind, name = self.current()
        if kind != "service":
            return
        if not self.state.selected(name):
            self.state.message = f"switch {name} on first"
            self.refresh_view()
            return
        if not self.state.original_ports(name):
            self.state.message = f"{name} publishes no host ports"
            self.refresh_view()
            return
        self.app.push_screen(PortsDialog(self.state, name), lambda _result: self.refresh_view())

    def action_base(self) -> None:
        choices = self.catalog.edition_names()
        current = choices.index(self.state.selection.base) if self.state.selection.base in choices else 0

        def chosen(index: Optional[int]) -> None:
            if index is not None and choices[index] != self.state.selection.base:
                self.state.set_selection(core.Selection(choices[index]))
                self.build_tree()

        self.app.push_screen(ChoiceDialog("Start from which edition? This drops the current changes.",
                                          choices, current), chosen)

    def action_save(self) -> None:
        errors = bool(self.state.result.errors)
        title = "Fix these first, nothing is written with errors" if errors else "Write this configuration?"

        def answered(yes: bool) -> None:
            if yes:
                self.dismiss(self.state.selection)

        self.app.push_screen(ConfirmDialog(title, finding_lines(self.state.summary()), yes="Write",
                                           allow_yes=not errors), answered)

    def action_leave(self) -> None:
        self.app.push_screen(ConfirmDialog("Leave without writing?", yes="Leave"),
                             lambda yes: self.dismiss(None) if yes else None)


class PortsDialog(ModalScreen):
    """Host ports of one service: change, remove, reset or take a suggestion."""

    BINDINGS = [
        Binding("enter", "change", "Change"),
        Binding("r", "reset", "Reset"),
        Binding("v", "suggest", "Suggestions"),
        Binding("escape,q", "close", "Back"),
    ]

    def __init__(self, state: State, name: str):
        super().__init__()
        self.state, self.service = state, name

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"Host ports of {self.service}", classes="dialog-title")
            yield DataTable(id="ports", cursor_type="row")
            yield Static("", id="ports-message")
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("HOST PORT", "NOW", "CONTAINER", "PROBLEM")
        self.fill()
        table.focus()

    @property
    def ports(self):
        return self.state.original_ports(self.service)

    def fill(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        for port in self.ports:
            key = self.state.current_key(self.service, port)
            new = self.state.selection.ports.get((self.service, port.host, port.proto), port.host)
            problems = [f.text for f in self.state.result.problems_of(self.service, "port")
                        if f.text.startswith(f"port {key} ")]
            table.add_row(port.key, "removed" if new is None else key, f"{port.container}/{port.proto}",
                          Text(problems[0] if problems else "", style="red"))
        if self.ports:
            table.move_cursor(row=min(row, len(self.ports) - 1))

    def selected_port(self):
        table = self.query_one(DataTable)
        return self.ports[table.cursor_row] if self.ports else None

    def action_change(self) -> None:
        port = self.selected_port()
        if port is None:
            return

        def entered(value: str) -> None:
            if value == "-":
                self.state.set_port(self.service, port, None)
            elif value:
                self.state.set_port(self.service, port, int(value))
            self.fill()

        self.app.push_screen(PortInputDialog(f"New host port for {port.key}"), entered)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_change()

    def action_reset(self) -> None:
        port = self.selected_port()
        if port is not None:
            self.state.set_port(self.service, port, None, reset=True)
            self.fill()

    def action_suggest(self) -> None:
        options = core.suggestions(self.state.catalog, self.state.selection, self.service, self.state.max_networks)
        message = self.query_one("#ports-message", Static)
        if not options:
            message.update(Text("no conflict-free suggestion, enter a port by hand", style="yellow"))
            return

        def picked(index: Optional[int]) -> None:
            if index is not None:
                self.state.apply_overrides(self.service, options[index][1])
                self.fill()

        self.app.push_screen(ChoiceDialog(f"Suggestions for {self.service}",
                                          [f"{label}: {core.format_overrides(o)}" for label, o in options]),
                             picked)

    def action_close(self) -> None:
        self.dismiss(None)
