"""T-Pot customizer user interfaces: curses checklist and a plain text fallback.

Both work on a customizer_core.Selection and give it back on save, None on quit.
Writing the file is left to customizer.py.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import customizer_core as core

HELP = "up/down move  space toggle  enter fold  p ports  b base  s save  q quit"


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
# curses
# ---------------------------------------------------------------------------

def run_curses(catalog: core.Catalog, selection: core.Selection, max_networks: int) -> Optional[core.Selection]:
    import curses

    state = State(catalog, selection, max_networks)

    def colors():
        curses.use_default_colors()
        for pair, color in ((1, curses.COLOR_RED), (2, curses.COLOR_GREEN), (3, curses.COLOR_CYAN),
                            (4, curses.COLOR_YELLOW)):
            curses.init_pair(pair, color, -1)

    def attr(pair: int) -> int:
        return curses.color_pair(pair) if curses.has_colors() else 0

    def put(win, y: int, x: int, text: str, style: int = 0) -> None:
        height, width = win.getmaxyx()
        if 0 <= y < height and x < width:
            try:
                win.addnstr(y, x, text, max(0, width - x - 1), style)
            except curses.error:
                pass

    def ask(win, text: str) -> str:
        height, _width = win.getmaxyx()
        win.move(height - 1, 0)
        win.clrtoeol()
        put(win, height - 1, 0, text, curses.A_BOLD)
        curses.echo()
        curses.curs_set(1)
        try:
            value = win.getstr(height - 1, min(len(text), win.getmaxyx()[1] - 2), 20)
        finally:
            curses.noecho()
            curses.curs_set(0)
        return value.decode("utf-8", "replace").strip()

    def choose(win, title: str, options: List[str], current: int = 0) -> Optional[int]:
        cursor = current
        while True:
            win.erase()
            put(win, 0, 0, title, curses.A_BOLD)
            for i, option in enumerate(options):
                put(win, 2 + i, 2, option, curses.A_REVERSE if i == cursor else 0)
            put(win, win.getmaxyx()[0] - 1, 0, "up/down move  enter choose  q back", attr(3))
            key = win.getch()
            if key in (curses.KEY_UP, ord("k")):
                cursor = max(0, cursor - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                cursor = min(len(options) - 1, cursor + 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                return cursor
            elif key in (ord("q"), 27):
                return None

    def show(win, title: str, lines: List[str], prompt: str) -> int:
        win.erase()
        put(win, 0, 0, title, curses.A_BOLD)
        for i, line in enumerate(lines):
            style = attr(1) if line.startswith("ERROR") else attr(4) if line.startswith("WARNING") else 0
            put(win, 2 + i, 2, line, style)
        put(win, win.getmaxyx()[0] - 1, 0, prompt, attr(3))
        return win.getch()

    def ports_screen(win, name: str) -> None:
        cursor = 0
        while True:
            ports = state.original_ports(name)
            if not ports:
                state.message = f"{name} publishes no host ports"
                return
            cursor = min(cursor, len(ports) - 1)
            win.erase()
            put(win, 0, 0, f"Host ports of {name}", curses.A_BOLD)
            for i, port in enumerate(ports):
                problems = [f for f in state.result.problems_of(name, "port") if f.text.startswith(
                    f"port {state.current_key(name, port)} ")]
                line = state.mapping(name, port) + ("  ! " + problems[0].text if problems else "")
                style = curses.A_REVERSE if i == cursor else attr(1) if problems else 0
                put(win, 2 + i, 2, line, style)
            put(win, win.getmaxyx()[0] - 2, 0, state.message, attr(4))
            put(win, win.getmaxyx()[0] - 1, 0,
                "up/down move  enter change  r reset  v suggestions  q back", attr(3))
            state.message = ""
            key = win.getch()
            port = ports[cursor]
            if key in (curses.KEY_UP, ord("k")):
                cursor = max(0, cursor - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                cursor = min(len(ports) - 1, cursor + 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                value = ask(win, f"New host port for {port.key} (- removes it): ")
                if value == "-":
                    state.set_port(name, port, None)
                elif value.isdigit() and 1 <= int(value) <= 65535:
                    state.set_port(name, port, int(value))
                elif value:
                    state.message = f"'{value}' is not a port"
            elif key == ord("r"):
                state.set_port(name, port, None, reset=True)
            elif key == ord("v"):
                options = core.suggestions(catalog, state.selection, name, max_networks)
                if not options:
                    state.message = "no conflict-free suggestion, enter a port by hand"
                    continue
                pick = choose(win, f"Suggestions for {name}",
                              [f"{label}: {core.format_overrides(o)}" for label, o in options])
                if pick is not None:
                    state.apply_overrides(name, options[pick][1])
            elif key in (ord("q"), 27):
                return

    def main(win) -> Optional[core.Selection]:
        if curses.has_colors():
            colors()
        curses.curs_set(0)
        win.keypad(True)
        cursor, top = 0, 0
        while True:
            height, width = win.getmaxyx()
            rows = state.rows()
            cursor = max(0, min(cursor, len(rows) - 1))
            list_height = max(1, height - 9)
            top = min(max(top, cursor - list_height + 1), cursor)
            res = state.result
            win.erase()
            if height < 14 or width < 60:
                put(win, 0, 0, "The terminal is too small, make it at least 60x14 or use --text.")
                if win.getch() in (ord("q"), 27):
                    return None
                continue
            status = (f" T-Pot Customizer | base {state.selection.base} ({res.role()}) | "
                      f"{len(res.services)} services | networks {len(res.networks)}/{max_networks} | "
                      f"{len(res.errors)} errors ")
            put(win, 0, 0, status.ljust(width), curses.A_REVERSE | (attr(1) if res.errors else 0))
            for i, (kind, name) in enumerate(rows[top:top + list_height]):
                y = 1 + i
                current = top + i == cursor
                if kind == "group":
                    members = state.visible(name)
                    count = sum(state.selected(n) for n in members)
                    fold = "+" if name in state.folded else "-"
                    text = f"[{fold}] {core.GROUP_TITLES.get(name, name)} {count}/{len(members)}"
                    style = curses.A_BOLD | attr(3)
                else:
                    mark = "x" if state.selected(name) else " "
                    if state.selected(name) and state.locked(name):
                        mark = "*"
                    flag = "!" if res.problems_of(name) else " "
                    text = f"   {flag}[{mark}] {name:<28} {catalog.description(name)}"
                    style = attr(1) if res.problems_of(name) else attr(2) if state.selected(name) else 0
                put(win, y, 0, text.ljust(width - 1), style | (curses.A_REVERSE if current else 0))
            detail_y = height - 8
            put(win, detail_y, 0, "-" * (width - 1))
            if rows:
                kind, name = rows[cursor]
                if kind == "service":
                    source = res.sources.get(name) or catalog.source(state.selection.base, name)
                    origin = f"{source.edition} edition" if source.edition else "catalog"
                    put(win, detail_y + 1, 0, f"{name}: {catalog.description(name)} ({origin})", curses.A_BOLD)
                    ports = ", ".join(state.mapping(name, p).split(" (")[0] for p in state.original_ports(name))
                    put(win, detail_y + 2, 0, "Ports: " + (ports or "none"))
                    why = state.locked(name) if state.selected(name) else ""
                    put(win, detail_y + 3, 0, why)
                    for j, finding in enumerate(res.problems_of(name)[:2]):
                        put(win, detail_y + 4 + j, 0, "! " + finding.text, attr(1))
                else:
                    put(win, detail_y + 1, 0, "space switches the whole group, enter folds it")
            others = [f for f in res.errors if not f.services] + res.warnings
            for j, finding in enumerate(others[:1]):
                put(win, height - 3, 0, f"{finding.level}: {finding.text}", attr(1 if finding.level == "error" else 4))
            put(win, height - 2, 0, state.message, attr(4))
            put(win, height - 1, 0, HELP, attr(3))
            state.message = ""

            key = win.getch()
            kind, name = rows[cursor] if rows else ("", "")
            if key in (curses.KEY_UP, ord("k")):
                cursor -= 1
            elif key in (curses.KEY_DOWN, ord("j")):
                cursor += 1
            elif key == curses.KEY_PPAGE:
                cursor -= list_height
            elif key == curses.KEY_NPAGE:
                cursor += list_height
            elif key == ord(" "):
                if kind == "group":
                    state.toggle_group(name)
                elif kind == "service":
                    state.toggle(name)
            elif key in (curses.KEY_ENTER, 10, 13) and kind == "group":
                state.folded ^= {name}
            elif key == ord("p") and kind == "service":
                if state.selected(name):
                    ports_screen(win, name)
                else:
                    state.message = f"select {name} first"
            elif key == ord("b"):
                choices = base_choices(catalog)
                pick = choose(win, "Start from which edition? This drops the current changes.", choices,
                              choices.index(state.selection.base) if state.selection.base in choices else 0)
                if pick is not None:
                    state.set_selection(core.Selection(choices[pick]))
                    cursor = 0
            elif key == ord("s"):
                if res.errors:
                    show(win, "Fix these first, nothing is written with errors:", state.summary(), "any key: back")
                elif show(win, "Write this configuration?", state.summary(), "y: write  any other key: back") \
                        in (ord("y"), ord("Y")):
                    return state.selection
            elif key in (ord("q"), 27):
                if show(win, "Quit without writing?", [], "y: quit  any other key: back") in (ord("y"), ord("Y")):
                    return None

    return curses.wrapper(main)


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
