"""State of the customizer dialogs, plus the plain text dialog.

State is shared with the full screen dialog of tpot (tpotctl/screens/customizer.py).
Both dialogs give the Selection back on save, None on quit; writing the file is left
to their callers.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import customizer_core as core

class State:
    """Selection plus everything derived from it, shared by both interfaces."""

    def __init__(self, catalog: core.Catalog, selection: core.Selection, max_networks: int):
        self.catalog = catalog
        self.max_networks = max_networks
        self.folded = {"conpot"}
        self.message = ""
        self.set_selection(selection)

    def set_selection(self, selection: core.Selection) -> None:
        self.selection = selection
        self.result = core.resolve(self.catalog, selection, self.max_networks, strict=False)
        self.selection = self.result.selection

    @property
    def base_services(self) -> dict:
        return self.catalog.editions[self.selection.base].services

    def visible(self, group: str) -> List[str]:
        return [n for n in self.catalog.order
                if self.catalog.group(n) == group and not self.catalog.flag(n, "hidden")
                and self.catalog.offered(self.selection.base, n)]

    def rows(self) -> List[Tuple[str, str]]:
        """(kind, name) with kind group | service, in display order."""
        rows = []
        for group, _title in core.GROUPS:
            members = self.visible(group)
            if not members:
                continue
            rows.append(("group", group))
            if group not in self.folded:
                rows.extend(("service", n) for n in members)
        return rows

    def selected(self, name: str) -> bool:
        return name in self.result.services

    def locked(self, name: str) -> str:
        """Why the service cannot be switched off, empty if it can."""
        if self.catalog.flag(name, "required"):
            return f"{name} is always part of T-Pot"
        users = [n for n in self.result.needed_by.get(name, []) if n in self.result.services]
        if users and name not in self.selection.add and name not in self.base_services:
            return f"{name} came along for {', '.join(users)}"
        if users:
            return f"{', '.join(users)} need{'s' if len(users) == 1 else ''} {name}"
        return ""

    def toggle(self, name: str, on: Optional[bool] = None) -> None:
        on = (not self.selected(name)) if on is None else on
        selection = self.selection.copy()
        if on:
            if name in selection.remove:
                selection.remove.remove(name)
            if name not in self.base_services and name not in selection.add:
                selection.add.append(name)
        else:
            why = self.locked(name)
            if why:
                self.message = why
                return
            if name in selection.add:
                selection.add.remove(name)
            if name in self.base_services and name not in selection.remove:
                selection.remove.append(name)
            selection.ports = {k: v for k, v in selection.ports.items() if k[0] != name}
        self.set_selection(selection)

    def toggle_group(self, group: str) -> None:
        members = self.visible(group)
        on = not all(self.selected(n) for n in members)
        for name in members:
            if self.selected(name) != on and (on or not self.locked(name)):
                self.toggle(name, on)

    def set_port(self, name: str, port: core.Port, new: Optional[int], reset: bool = False) -> None:
        selection = self.selection.copy()
        key = (name, port.host, port.proto)
        if reset:
            selection.ports.pop(key, None)
        else:
            selection.ports[key] = new
        self.set_selection(selection)

    def apply_overrides(self, name: str, overrides: core.PortOverrides) -> None:
        selection = self.selection.copy()
        selection.ports = {k: v for k, v in selection.ports.items() if k[0] != name}
        selection.ports.update(overrides)
        self.set_selection(selection)

    def original_ports(self, name: str) -> List[core.Port]:
        try:
            return core.service_ports(self.result.sources[name].services[name])
        except (KeyError, core.CustomizerError):
            return []

    def current_key(self, name: str, port: core.Port) -> str:
        new = self.selection.ports.get((name, port.host, port.proto), port.host)
        return f"{new}/{port.proto}"

    def mapping(self, name: str, port: core.Port) -> str:
        new = self.selection.ports.get((name, port.host, port.proto), port.host)
        target = "removed" if new is None else f"{new}"
        return f"{port.host}/{port.proto} -> {target} (container {port.container})"

    def summary(self) -> List[str]:
        sel, res = self.selection, self.result
        lines = [f"Base edition: {sel.base} ({res.role()}), {len(res.services)} services, "
                 f"{len(res.networks)}/{self.max_networks} networks"]
        lines.append("Added: " + (", ".join(sel.add) or "-"))
        lines.append("Removed: " + (", ".join(sel.remove) or "-"))
        lines.append("Ports: " + (core.format_overrides(sel.ports) or "-"))
        pulled = [n for n in res.services if n not in sel.add and n not in self.base_services]
        if pulled:
            lines.append("Came along: " + ", ".join(pulled))
        for finding in res.findings:
            lines.append(f"{finding.level.upper()}: {finding.text}")
        return lines


def base_choices(catalog: core.Catalog) -> List[str]:
    return catalog.edition_names()


# ---------------------------------------------------------------------------
# plain text
# ---------------------------------------------------------------------------

def run_text(catalog: core.Catalog, selection: core.Selection, max_networks: int) -> Optional[core.Selection]:
    state = State(catalog, selection, max_networks)
    state.folded = set()

    def read(prompt: str) -> Optional[str]:
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    def ports(name: str) -> None:
        while True:
            options = state.original_ports(name)
            if not options:
                print(f"{name} publishes no host ports.")
                return
            for i, port in enumerate(options, 1):
                print(f"  {i:2} {state.mapping(name, port)}")
            for finding in state.result.problems_of(name, "port"):
                print(f"  ! {finding.text}")
            suggestions = core.suggestions(catalog, state.selection, name, max_networks)
            for i, (label, overrides) in enumerate(suggestions, 1):
                print(f"  v{i} {label}: {core.format_overrides(overrides)}")
            answer = read("N=PORT changes, N=- removes, N= resets, vN takes a suggestion, empty returns: ")
            if not answer:
                return
            if answer.startswith("v") and answer[1:].isdigit() and 1 <= int(answer[1:]) <= len(suggestions):
                state.apply_overrides(name, suggestions[int(answer[1:]) - 1][1])
                continue
            number, _, value = answer.partition("=")
            if not number.isdigit() or not 1 <= int(number) <= len(options) or "=" not in answer:
                print("Not understood.")
                continue
            port = options[int(number) - 1]
            if value == "":
                state.set_port(name, port, None, reset=True)
            elif value == "-":
                state.set_port(name, port, None)
            elif value.isdigit() and 1 <= int(value) <= 65535:
                state.set_port(name, port, int(value))
            else:
                print(f"'{value}' is not a port.")

    choices = base_choices(catalog)
    print("Editions: " + ", ".join(f"{i}={e}" for i, e in enumerate(choices, 1)))
    answer = read(f"Start from which edition? [{state.selection.base}] ")
    if answer is None:
        return None
    if answer:
        pick = choices[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(choices) else answer.upper()
        if pick not in catalog.editions:
            print(f"Unknown edition {answer}.")
            return None
        if pick != state.selection.base:
            state.set_selection(core.Selection(pick))

    while True:
        numbered = [name for kind, name in state.rows() if kind == "service"]
        print()
        group = None
        for i, name in enumerate(numbered, 1):
            if catalog.group(name) != group:
                group = catalog.group(name)
                print(f"-- {core.GROUP_TITLES.get(group, group)}")
            mark = "x" if state.selected(name) else " "
            if state.selected(name) and state.locked(name):
                mark = "*"
            flag = "!" if state.result.problems_of(name) else " "
            print(f"{i:3} {flag}[{mark}] {name:<28} {catalog.description(name)}")
        print()
        for line in state.summary()[:1] + [line for line in state.summary() if line.startswith(("ERROR", "WARNING"))]:
            print(line)
        if state.message:
            print(state.message)
            state.message = ""
        answer = read("Numbers toggle (e.g. 3 7 12), pN edits ports, b changes the edition, s saves, q quits: ")
        if answer is None or answer == "q":
            return None
        if answer == "s":
            for line in state.summary():
                print(line)
            if state.result.errors:
                print("Fix the errors first, nothing is written with errors.")
                continue
            if (read("Write this configuration? [y/N] ") or "").lower() == "y":
                return state.selection
            continue
        if answer == "b":
            return run_text(catalog, core.Selection(state.selection.base), max_networks)
        if answer.startswith("p") and answer[1:].isdigit() and 1 <= int(answer[1:]) <= len(numbered):
            ports(numbered[int(answer[1:]) - 1])
            continue
        for token in answer.replace(",", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(numbered):
                state.toggle(numbered[int(token) - 1])
            else:
                state.message = f"'{token}' is not a number from the list"
