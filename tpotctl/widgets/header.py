"""The head of the T-Pot Manager: the wordmark on its magenta plate, what this T-Pot is, who makes it."""

import os
import socket
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from tpotctl import glyphs, logo, ops, theme


CREDIT = "Powered by Deutsche Telekom Security GmbH"
CREDIT_SHORT = "by Telekom Security"


def chip(text: str, background: str, foreground: str = "glass") -> Text:
    """A label with its own background, the separate pieces of the head line."""
    return Text(f" {text} ", style=f"bold {theme.color(foreground)} on {theme.color(background)}")


def service_text(service: str) -> Text:
    if service == "active":
        return Text(f"{glyphs.g('running')} running", style=f"bold {theme.color('ok')}")
    if service == "n/a":
        return Text("")
    return Text(f"{glyphs.g('stopped')} {service}", style=f"bold {theme.color('error')}")


class TpotHeader(Horizontal):
    """Wordmark, chips for type, edition and version, the state of the service."""

    def compose(self) -> ComposeResult:
        yield Static(id="wordmark")
        yield Static(id="header-chips")
        with Vertical(id="header-right"):
            yield Static(id="header-service")
            yield Static(id="header-credit")

    def on_mount(self) -> None:
        self.repaint()

    def on_resize(self) -> None:
        self.repaint()

    def repaint(self, state: Optional[ops.Status] = None) -> None:
        """Draw anew, i.e. after a change of theme or icons; state from the status polling."""
        if state is not None:
            self.state = state
        state = getattr(self, "state", None)
        self.query_one("#wordmark", Static).update(logo.wordmark(theme.color("glass")))
        narrow = self.app.size.width < 100
        chips, second = Text(), Text()
        if state is not None:
            version = "  ".join(part for part in (state.version, f"{state.branch} {state.commit}".strip(" ?"))
                                if part and part != "?")
            for text, background in ((state.tpot_type, "petrol"), (state.edition, "comb"),
                                     ("" if narrow else version, "comb")):
                if text and not text.startswith("?"):
                    chips.append_text(chip(text, background))
                    chips.append("  ")
            if narrow:
                second.append(f"{state.version}  ", style="bold")
        home = os.path.expanduser("~")
        repo = ops.REPO_DIR.replace(home, "~", 1) if ops.REPO_DIR.startswith(home) else ops.REPO_DIR
        second.append(f"{socket.gethostname()}  {repo}", style=theme.color("mist"))
        chips.append("\n")
        chips.append_text(second)
        self.query_one("#header-chips", Static).update(chips)
        self.query_one("#header-service", Static).update(service_text(state.service) if state else "")
        width = self.app.size.width
        credit = self.query_one("#header-credit", Static)
        credit.display = width >= 90
        text = CREDIT if width >= 120 else CREDIT_SHORT
        credit.update(Text(text, style=theme.color("ash")))
