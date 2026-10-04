"""The honeycomb of the Status page: one cell per container, coloured by its state.

Every second row is shifted by half a cell, that makes the honeycomb. Filled means
running (green healthy or without a health check, yellow starting, red unhealthy),
outlined means not running. With room for it every cell carries its name; when the
names would not fit, the cells are packed tight and only the containers that need
a look are named below.
"""

import math
from typing import List

from rich.text import Text
from textual.widgets import Static

from tpotctl import glyphs, ops, theme
from tpotctl.ops import cell_state

CELL = 14          # glyph, space, name and gap
NAME = CELL - 3
TIGHT = 3          # glyph and gap in the packed comb
MAX_ROWS = 8       # with names


WORDS = {("on", "ok"): "running", ("on", "warn"): "starting", ("on", "error"): "unhealthy",
         ("warn", "error"): "restarting", ("off", "error"): "stopped", ("off", "mist"): "created"}


def short(name: str, width: int = NAME) -> str:
    return name if len(name) <= width else name[:width - 1] + "…"


def fits_names(count: int, width: int, rows: int = MAX_ROWS) -> bool:
    per_row = max(1, (width - CELL // 2) // CELL)
    return math.ceil(count / per_row) <= rows


def render(containers: List[ops.Container], width: int, names: bool = True) -> Text:
    cell = CELL if names else TIGHT
    per_row = max(1, (width - cell // 2) // cell)
    if not names:
        # a comb, not a long ribbon: about three times as wide as high
        per_row = min(per_row, max(6, math.ceil(math.sqrt(len(containers) * 3))))
    out = Text()
    for start in range(0, len(containers), per_row):
        if (start // per_row) % 2:
            out.append(" " * (cell // 2))
        for container in containers[start:start + per_row]:
            glyph, colour = cell_state(container)
            out.append(glyphs.g(glyph), style=f"bold {theme.color(colour)}")
            if names:
                out.append(f" {short(container.name):<{NAME}} ",
                           style=theme.color("glass") if container.state == "running" else theme.color("mist"))
            else:
                out.append(" " * (cell - 1))
        out.append("\n")
    out.rstrip()
    return out


def legend(containers: List[ops.Container], name_problems: bool = False) -> Text:
    counts = {}
    for container in containers:
        counts[cell_state(container)] = counts.get(cell_state(container), 0) + 1
    out = Text()
    for state, word in WORDS.items():
        if counts.get(state):
            out.append(glyphs.g(state[0]), style=f"bold {theme.color(state[1])}")
            out.append(f" {counts[state]} {word}   ", style=theme.color("mist"))
    if name_problems:
        for container in containers:
            state = cell_state(container)
            if state != ("on", "ok"):
                out.append("\n")
                out.append(glyphs.g(state[0]), style=f"bold {theme.color(state[1])}")
                out.append(f" {container.name}", style="bold")
                out.append(f"  {WORDS[state]}", style=theme.color(state[1]))
    return out


class Honeycomb(Static):

    containers: List[ops.Container] = []

    def show(self, containers: List[ops.Container]) -> None:
        self.containers = containers
        self.repaint()

    def repaint(self) -> None:
        width = self.content_region.width or self.size.width or 60
        if not self.containers:
            self.update(Text("No containers, T-Pot is not started.", style=theme.color("mist")))
            return
        names = fits_names(len(self.containers), width) and not self.screen.has_class("-short")
        text = render(self.containers, width, names)
        text.append("\n\n")
        text.append_text(legend(self.containers, name_problems=not names))
        self.update(text)

    def on_resize(self) -> None:
        self.repaint()
