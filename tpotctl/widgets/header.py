"""The head of the T-Pot Manager: the wordmark on its magenta plate, what this T-Pot is and how it is
doing, and who makes it in the rule below, as in the credits of the splash."""

import os
import socket
from typing import List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from tpotctl import glyphs, logo, ops, theme

CREDIT = "telekom security"
# the plate covers the columns 1-24 (the header's padding is column 0), the credit 3-22: two cells of
# the rule on each side of it under the plate
CREDIT_INDENT = 3
DISK_WARN = 90          # percent of the data disk from which on the header tells it


def chip(text: str, background: str, foreground: str = "glass") -> Text:
    """A label with its own background, the separate pieces of the head line."""
    return Text(f" {text} ", style=f"bold {theme.color(foreground)} on {theme.color(background)}")


def lead(name: str) -> str:
    """The glyph in front of a fact and its space; nothing where the icon set has none."""
    glyph = glyphs.g(name)
    return f"{glyph} " if glyph else ""


def service_text(service: str) -> Text:
    if service == "active":
        return Text(f"{glyphs.g('running')} running", style=f"bold {theme.color('ok')}")
    if service == "n/a":
        return Text("")
    return Text(f"{glyphs.g('stopped')} {service}", style=f"bold {theme.color('error')}")


def credit_text() -> Text:
    """── [ telekom security ] in the colours of the splash credits: the rule and brackets in wax."""
    wax = theme.color("wax")
    # Textual draws the first cell of the rule before a left label itself; the rule is the border
    # of the header, its line stays the same in every icon set
    text = Text("─" * (CREDIT_INDENT - 1), style=wax)
    text.append("[ ", style=wax)
    text.append(CREDIT, style=theme.color("magenta"))
    text.append(" ]", style=wax)
    return text


def what_text(state: Optional[ops.Status], narrow: bool) -> Text:
    """The middle: type, edition, version and the checkout's branch; host and checkout; where to go."""
    text = Text(no_wrap=True, overflow="ellipsis")
    if state is not None:
        for part, background in ((state.tpot_type, "petrol"), (state.edition, "comb"), (state.version, "comb")):
            if part and not part.startswith("?"):
                text.append_text(chip(part, background))
                text.append(" " if narrow else "  ")
        branch = f"{state.branch} {state.commit}".strip(" ?")
        if branch and not narrow:
            text.append(f"{lead('branch')}{branch}", style=theme.color("mist"))
        text.rstrip()
    text.append("\n")
    home = os.path.expanduser("~")
    repo = ops.REPO_DIR.replace(home, "~", 1) if ops.REPO_DIR.startswith(home) else ops.REPO_DIR
    text.append(f"{lead('host')}{socket.gethostname()}", style=theme.color("glass"))
    text.append(f"  {repo}", style=theme.color("mist"))
    text.append("\n")
    if state is not None and state.address:
        if state.tpot_type == "SENSOR":
            text.append(lead("hive_link"), style=theme.color("mist"))
            text.append(state.address, style=theme.color("glass"))
        else:
            url = f"https://{state.address}:{state.web_port}"
            text.append(lead("web"), style=theme.color("mist"))
            text.append(url[len("https://"):] if narrow else url, style=f"{theme.color('glass')} link {url}")
    return text


def load_colour(load: float, cpus: int) -> str:
    return theme.color("ok" if load < 0.7 * cpus else "warn" if load < cpus else "error")


def live_text(state: Optional[ops.Status], containers: Optional[List[ops.Container]], machine) -> Text:
    """The right: the service and how long it runs, the containers, the host's uptime and load."""
    text = Text(no_wrap=True, overflow="ellipsis")
    if state is not None:
        text.append_text(service_text(state.service))
        if state.since is not None:
            text.append(f"  {lead('uptime')}{ops.duration(state.since)}", style=theme.color("mist"))
    text.append("\n")
    if containers:
        running = sum(c.state == "running" for c in containers)
        colour = "ok" if running == len(containers) else "warn" if running else "error"
        text.append(lead("containers"), style=theme.color(colour))
        text.append(f"{running} of {len(containers)} up", style=f"bold {theme.color(colour)}")
    disk = getattr(machine, "disk", None)
    if disk is not None and disk.percent >= DISK_WARN:
        text.append(f"  disk {disk.percent:.0f} %", style=f"bold {theme.color('error')}")
    text.append("\n")
    uptime, load = getattr(machine, "uptime", None), getattr(machine, "load", None)
    if uptime is not None:
        text.append(f"{lead('machine')}up {ops.duration(uptime, short=True)}", style=theme.color("mist"))
    if load is not None:
        text.append(f"  {lead('load')}" if uptime is not None else lead("load"), style=theme.color("mist"))
        text.append(f"{load:.2f}", style=f"bold {load_colour(load, getattr(machine, 'cpus', 1) or 1)}")
    return text


class TpotHeader(Horizontal):
    """Wordmark, what this T-Pot is, how it is doing; the credit in the rule below."""

    def compose(self) -> ComposeResult:
        yield Static(id="wordmark")
        yield Static(id="header-chips")
        yield Static(id="header-live")

    def on_mount(self) -> None:
        self.repaint()

    def on_resize(self) -> None:
        self.repaint()

    def repaint(self, state: Optional[ops.Status] = None, containers: Optional[List[ops.Container]] = None,
                machine=None) -> None:
        """Draw anew, i.e. after a change of theme or icons; the rest from the status polling."""
        if state is not None:
            self.state = state
        if containers is not None:
            self.containers = containers
        if machine is not None:
            self.machine = machine
        state = getattr(self, "state", None)
        self.query_one("#wordmark", Static).update(logo.wordmark(theme.color("glass")))
        narrow = self.app.size.width < 100
        self.query_one("#header-chips", Static).update(what_text(state, narrow))
        self.query_one("#header-live", Static).update(
            live_text(state, getattr(self, "containers", None), getattr(self, "machine", None)))
        self.border_subtitle = credit_text()
