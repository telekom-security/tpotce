"""A script or a tpot command run from the menu: its phases, its log, how it ended.

The script stays the engine. It runs as a child with TPOT_MARKS=1 and prints the
@@tpot marks of installer/lib/ui.sh (fuMARK), which become the list of phases here.
stdin is closed, so a script that needs sudo gets the password as a file only this
user can read (-B / --become-file), asked for here, removed when it ends.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Union

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, RichLog, Static

from tpotctl import glyphs, installer, runlog, theme
from tpotctl.bootstrap import REPO_DIR
from tpotctl.engine import Engine
from tpotctl.widgets.nav import NavInput, NavRichLog, NavScroll

SPINNER = {"unicode": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "nerd": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "ascii": "|/-\\"}


@dataclass
class Task:
    title: str
    command: List[str]
    cwd: str = REPO_DIR
    # how the command takes a sudo password file: "" none needed, "-B" (scripts),
    # "--become-file" (tpot commands)
    become: str = ""
    intro: Union[Text, str] = ""
    done: str = ""
    # more secrets the command reads from files: {option: text}, each one appended as
    # "option file" (i.e. the password of a new web user)
    secrets: Dict[str, str] = field(default_factory=dict)
    restart_tpot: bool = False          # the checkout changed, offer to start the T-Pot Manager anew
    autostart: bool = False             # confirmed before, start without asking


class TaskScreen(Screen):
    """Dismissed with the exit code, "restart", or None when it did not run."""

    # q of the menu would quit tpot and kill the script half way (a stopped T-Pot, a half
    # reset checkout), so here it only goes back, and not while the script runs
    BINDINGS = [Binding("escape", "leave", "Back"), Binding("q", "leave", "Back", show=False),
                Binding("l", "toggle_log", "Log")]

    def __init__(self, task: Task, engine: Callable[..., Engine] = Engine, sudo_mode: Optional[str] = None,
                 password_ok: Optional[Callable[[str], bool]] = None):
        super().__init__(id="task")
        self.job = task
        self.engine_factory = engine
        self.sudo = sudo_mode
        self.password_ok = password_ok or installer.sudo_password_ok
        self.run_state = runlog.Run()
        self.busy = False
        self.checking = False
        self.code: Optional[int] = None
        self.started = 0.0
        self.frame = 0

    def compose(self) -> ComposeResult:
        with NavScroll(id="task-body"):
            yield Static(Text(self.job.title, style=f"bold {theme.color('magenta')}"), id="task-title")
            yield Static(self.intro_text(), id="task-intro")
            yield NavInput(placeholder="your sudo password", password=True, id="task-sudo")
            yield Static("", id="task-hint")
            yield Static("", id="task-phases")
            yield NavRichLog(id="task-log", max_lines=2000, wrap=False, markup=False, highlight=False)
            yield Static("", id="task-result")
        with Horizontal(id="task-nav"):
            yield Static("", classes="task-spacer")
            yield Button("Back", id="task-back")
            yield Button("Restart the Manager", id="task-restart", variant="primary")
            yield Button("Run", id="task-run", variant="primary")
        yield Footer()

    def intro_text(self) -> Text:
        intro = self.job.intro
        text = intro.copy() if isinstance(intro, Text) else Text(intro, style=theme.color("glass"))
        if text.plain:
            text.append("\n")
        text.append(" ".join(self.job.command).replace(REPO_DIR + os.sep, ""), style=theme.color("ash"))
        return text

    def on_mount(self) -> None:
        if self.sudo is None:
            self.sudo = installer.sudo_mode()
        self.query_one("#task-sudo", Input).display = self.needs_password()
        self.query_one("#task-restart", Button).display = False
        self.query_one("#task-log", RichLog).display = False
        if self.job.autostart and not self.needs_password():
            self.start()
        elif self.needs_password():
            self.query_one("#task-sudo", Input).focus()
        else:
            self.query_one("#task-run", Button).focus()

    def needs_password(self) -> bool:
        return bool(self.job.become) and self.sudo == "password"

    # -- running -----------------------------------------------------------------

    def start(self) -> None:
        if self.busy or self.checking or self.code is not None:
            return
        if not self.needs_password():
            self.launch("")
            return
        password = self.query_one("#task-sudo", Input).value
        if not password:
            self.wrong_password()
            return
        # sudo takes a moment, longer for a wrong password: not in the UI thread
        self.checking = True
        self.query_one("#task-run", Button).disabled = True
        self.query_one("#task-hint", Static).update(Text("checking the sudo password ...", style=theme.color("mist")))
        self.check_password(password)

    @work(thread=True, exclusive=True, group="task-check")
    def check_password(self, password: str) -> None:
        ok = self.password_ok(password)
        self.app.call_from_thread(self.checked, password, ok)

    def checked(self, password: str, ok: bool) -> None:
        self.checking = False
        if ok:
            self.launch(password)
        else:
            self.query_one("#task-run", Button).disabled = False
            self.wrong_password()

    def wrong_password(self) -> None:
        self.query_one("#task-hint", Static).update(
            Text(f"{glyphs.g('fail')} sudo does not accept this password", style=theme.color("error")))

    def launch(self, password: str) -> None:
        self.query_one("#task-hint", Static).update("")
        self.query_one("#task-sudo", Input).display = False
        self.query_one("#task-run", Button).display = False
        self.query_one("#task-back", Button).disabled = True
        self.query_one("#task-log", RichLog).display = True
        self.busy = True
        self.started = time.monotonic()
        self.set_interval(0.1, self.tick)
        self.run_engine(password)

    @work(thread=True, exclusive=True, group="task")
    def run_engine(self, password: str) -> None:
        env = dict(os.environ, TPOT_MARKS="1", TPOT_GUM="off", PYTHONUNBUFFERED="1")
        try:
            options = list(self.job.secrets)
            texts = {f"secret{number}": self.job.secrets[option] for number, option in enumerate(options)}
            with installer.secret_files(become=password, **texts) as files:
                command = list(self.job.command)
                if "become" in files:
                    command += [self.job.become, files["become"]]
                for number, option in enumerate(options):
                    if f"secret{number}" in files:
                        command += [option, files[f"secret{number}"]]
                code = self.engine_factory(command, env=env, cwd=self.job.cwd).run(
                    lambda line: self.app.call_from_thread(self.feed, line))
        except OSError as err:
            self.app.call_from_thread(self.feed, f"{err}\n")
            code = 127
        self.app.call_from_thread(self.finished, code)

    def feed(self, line: str) -> None:
        if runlog.parse_mark(line) is None:
            self.query_one("#task-log", RichLog).write(Text(line.rstrip("\n")))
        self.run_state.feed(line)
        self.show_phases()

    def tick(self) -> None:
        if self.busy:
            self.frame += 1
            self.show_phases()

    def show_phases(self) -> None:
        text = Text()
        phases = self.run_state.phases
        spinner = SPINNER.get(glyphs.mode(), SPINNER["unicode"])
        for number, (key, title) in enumerate(phases):
            last = number == len(phases) - 1
            if key in self.run_state.failed:
                text.append(f"{glyphs.g('fail')} {title}\n", style=theme.color("error"))
            elif not last or (self.code == 0 and not self.busy):
                text.append(f"{glyphs.g('ok')} {title}\n", style=theme.color("ok"))
            elif self.busy:
                text.append(f"{spinner[self.frame % len(spinner)]} {title}", style=f"bold {theme.color('glass')}")
                minutes, seconds = divmod(int(time.monotonic() - self.started), 60)
                text.append(f"   {minutes}:{seconds:02d}\n", style=theme.color("ash"))
            else:
                text.append(f"{glyphs.g('fail')} {title}\n", style=theme.color("error"))
        if not phases and self.busy:
            text.append(f"{spinner[self.frame % len(spinner)]} running ...", style=theme.color("glass"))
        self.query_one("#task-phases", Static).update(text)

    def finished(self, code: int) -> None:
        self.busy = False
        self.code = code
        self.show_phases()
        text = Text()
        if code == 0:
            text.append(f"{glyphs.g('ok')} {self.job.done or self.job.title + ': done'}",
                        style=f"bold {theme.color('ok')}")
        else:
            text.append(f"{glyphs.g('fail')} {self.job.title} ended with exit code {code}",
                        style=f"bold {theme.color('error')}")
            for line in list(self.run_state.lines)[-15:]:
                text.append(f"\n  {line}", style=theme.color("ash"))
        for warning in self.run_state.warnings:
            text.append(f"\n{glyphs.g('warn')} {warning}", style=theme.color("warn"))
        # a run that failed after it changed the checkout still leaves new code behind
        restart_anyway = code != 0 and self.job.restart_tpot and self.run_state.checkout_changed
        if restart_anyway:
            text.append("\nThe checkout changed all the same, Restart the Manager to run the code that is there now.",
                        style=theme.color("warn"))
        self.query_one("#task-result", Static).update(text)
        back = self.query_one("#task-back", Button)
        back.disabled = False
        restart = self.query_one("#task-restart", Button)
        restart.display = self.job.restart_tpot and code == 0 or restart_anyway
        (restart if restart.display and code == 0 else back).focus()

    # -- leaving -----------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "task-run":
            self.start()
        elif event.button.id == "task-back":
            self.action_leave()
        elif event.button.id == "task-restart":
            self.dismiss("restart")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.start()

    def action_leave(self) -> None:
        if self.busy:
            self.notify("It is still running, wait until it ends.", title=self.job.title, timeout=4)
            return
        self.dismiss(self.code)

    def action_toggle_log(self) -> None:
        log = self.query_one("#task-log", RichLog)
        log.display = not log.display
