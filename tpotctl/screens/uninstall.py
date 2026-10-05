"""Uninstall T-Pot: what goes, a backup first, the host name to confirm.

The screen only asks. uninstall.sh does the work and removes ~/tpotce, tpot with
it, so tpot hands over to it with exec (handover()). A sudo password goes into a
file only this user can read, uninstall.sh removes it when it ends.
"""

import os
import shutil
import socket
from typing import Callable, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Input, Static

from tpotctl import glyphs, installer, logo, theme
from tpotctl.theme import apply as apply_theme
from tpotctl.widgets.nav import BINDINGS as NAV_BINDINGS, ArrowNav, NavInput, NavScroll

REMOVED = [
    "all containers, images and the data of T-Pot (logs, Elasticsearch, certificates)",
    "Docker Engine and its data in /var/lib/docker",
    "tpot.service, the daily reboot, the tpot user and group",
    "the tpot command, its aliases and Python packages, and ~/tpotce",
]
REVERTED = [
    "SSH goes back to port 22",
    "the DNS stub listener, SELinux and the firewall get their defaults back",
]

Handover = installer.Handover
handover = installer.uninstall_handover


def run_handover(target: Handover) -> None:
    argv, env = target
    os.chdir(os.path.expanduser("~"))       # ~/tpotce goes away
    os.execve(argv[0], argv, env)


class UninstallScreen(Screen):
    """Dismissed with the Handover for uninstall.sh, or None."""

    BINDINGS = [Binding("escape", "keep", "Keep T-Pot")]

    def __init__(self, sudo_mode: Optional[str] = None, password_ok: Optional[Callable[[str], bool]] = None,
                 hostname: str = ""):
        super().__init__(id="uninstall")
        self.sudo = sudo_mode
        self.password_ok = password_ok or installer.sudo_password_ok
        self.hostname = hostname or socket.gethostname()

    def compose(self) -> ComposeResult:
        with NavScroll(id="un-body"):
            yield Static(logo.wordmark(theme.color("glass")), id="wordmark")
            yield Static(self.summary(), id="un-text")
            yield Checkbox("Write a full backup to ~/tpot_backups first (restore.sh brings it back)", True,
                           id="un-backup")
            yield Static(self.space(), id="un-space")
            yield Static(Text(f"Type the name of this host, {self.hostname}, to confirm:",
                              style=theme.color("glass")), classes="un-label")
            yield NavInput(placeholder=self.hostname, id="un-host")
            yield NavInput(placeholder="your sudo password", password=True, id="un-sudo")
            yield Static("", id="un-hint")
        with Horizontal(id="un-nav"):
            yield Static("", classes="un-spacer")
            yield Button("Keep T-Pot", id="un-keep")
            yield Button("Uninstall T-Pot", id="un-go", variant="error", disabled=True)
        yield Footer()

    def summary(self) -> Text:
        text = Text()
        text.append("Uninstall T-Pot\n\n", style=f"bold {theme.color('error')}")
        text.append("Removed\n", style=f"bold {theme.color('magenta')}")
        for line in REMOVED:
            text.append(f"  {glyphs.g('fail')} ", style=theme.color("error"))
            text.append(f"{line}\n", style=theme.color("glass"))
        text.append("\nReverted\n", style=f"bold {theme.color('magenta')}")
        for line in REVERTED:
            text.append(f"  {glyphs.g('bullet')} ", style=theme.color("magenta"))
            text.append(f"{line}\n", style=theme.color("glass"))
        text.append("\nThe backups in ~/tpot_backups stay. The host needs a reboot afterwards.",
                    style=theme.color("ash"))
        return text

    def space(self) -> Text:
        try:
            free = shutil.disk_usage(os.path.expanduser("~")).free / 1024 ** 3
        except OSError:
            return Text("")
        return Text(f"    {free:.0f} GB free; a full backup is about the size of ~/tpotce/data.",
                    style=theme.color("ash"))

    def on_mount(self) -> None:
        if self.sudo is None:
            self.sudo = installer.sudo_mode()
        self.query_one("#un-sudo", Input).display = self.sudo == "password"
        self.query_one("#un-host", Input).focus()

    def problem(self) -> str:
        if self.query_one("#un-host", Input).value.strip() != self.hostname:
            return f"type {self.hostname} to confirm"
        if self.sudo == "password" and not self.query_one("#un-sudo", Input).value:
            return "enter your sudo password"
        return ""

    def on_input_changed(self, event: Input.Changed) -> None:
        problem = self.problem()
        self.query_one("#un-go", Button).disabled = bool(problem)
        self.query_one("#un-hint", Static).update(Text(problem, style=theme.color("ash")) if problem else "")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self.problem():
            self.uninstall()
        else:
            self.focus_next()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "un-go":
            self.uninstall()
        elif event.button.id == "un-keep":
            self.dismiss(None)

    def uninstall(self) -> None:
        if self.problem():
            return
        password = self.query_one("#un-sudo", Input).value if self.sudo == "password" else ""
        if password and not self.password_ok(password):
            self.query_one("#un-hint", Static).update(
                Text(f"{glyphs.g('fail')} sudo does not accept this password", style=theme.color("error")))
            return
        self.dismiss(handover(self.query_one("#un-backup", Checkbox).value, password))

    def action_keep(self) -> None:
        self.dismiss(None)


class UninstallApp(ArrowNav, App):
    """tpot uninstall on its own."""

    CSS_PATH = "../tpot.tcss"
    TITLE = "T-Pot uninstaller"
    BINDINGS = [*NAV_BINDINGS]

    def __init__(self, **options):
        super().__init__()
        self.options = options
        apply_theme(self)

    def get_theme_variable_defaults(self):
        return theme.variable_defaults()

    def on_mount(self) -> None:
        self.push_screen(UninstallScreen(**self.options), self.exit)


def run_uninstall() -> int:
    target = UninstallApp().run()
    if target:
        run_handover(target)
    return 1
