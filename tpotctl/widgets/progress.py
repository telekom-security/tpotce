"""Shared, scrollable output of installer and Manager tasks."""

from rich.text import Text

from tpotctl import glyphs, theme
from tpotctl.widgets.nav import NavRichLog


CHECK_STATES = {"ok": ("stage_ok", "ok"), "failed": ("stage_failed", "error"), "n/a": ("stage_na", "ash"),
                "not_reached": ("stage_not_reached", "mist")}
CHECK_STATE_WORDS = {"ok": "ok", "failed": "failed", "n/a": "does not apply", "not_reached": "not reached"}


def check_cell(state: str) -> Text:
    glyph, colour = CHECK_STATES[state]
    return Text(glyphs.g(glyph), style=f"bold {theme.color(colour)}" if state == "failed" else theme.color(colour),
                justify="center")


class ProgressLog(NavRichLog):
    def __init__(self, *, id: str):
        super().__init__(id=id, classes="progress-log", wrap=True, markup=False,
                         highlight=False, auto_scroll=False)

    def on_mount(self) -> None:
        self.border_title = "Output (L: expand / shrink)"

    def append(self, line: str) -> None:
        following = self.scroll_y >= self.max_scroll_y
        self.write(Text(line.rstrip("\n")), scroll_end=following)
