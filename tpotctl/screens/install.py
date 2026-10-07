"""tpot install: the assistant that installs T-Pot.

Seven steps: the checks of the host, the edition (or one of the customizer), the
web user, first settings, a review, the installation with its progress and the
end. Everything is asked before anything changes; the installation is install.sh
-s -M with these answers (tpotctl.installer), read line by line.
"""

import os
import subprocess
import time
from typing import Callable, Dict, List, Optional

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Button, ContentSwitcher, Footer, Input, OptionList, ProgressBar, RichLog,
                             Static)
from textual.widgets.option_list import Option

from tpotctl import engine, glyphs, installer, logo, theme, users
from tpotctl.bootstrap import REPO_DIR
from tpotctl.screens.dialogs import ConfirmDialog
from tpotctl.theme import apply as apply_theme
from tpotctl.widgets.nav import BINDINGS as NAV_BINDINGS, ArrowNav, NavInput, NavOptionList, NavRichLog, NavScroll

STEPS = [("check", "System check"), ("edition", "Edition"), ("user", "Web user"), ("settings", "Settings"),
         ("review", "Review"), ("install", "Install"), ("done", "Done")]
FIRST_SETTINGS = ["TPOT_ATTACKMAP_TEXT_TIMEZONE", "TPOT_BLACKHOLE", "TPOT_CAPTURE_INTERFACE", "TPOT_PULL_POLICY"]
LLM_PREFIXES = ("BEELZEBUB_LLM_", "GALAH_LLM_")
CHANGES = [
    "SSH moves to port 64295, connect with ssh -p 64295 after the reboot",
    "Docker Engine from the Docker repository, distribution Docker packages are removed",
    "the DNS stub listener is turned off, SELinux is set to permissive, the firewall zone to ACCEPT",
    "a user and group tpot, tpot.service, the tpot command, a daily reboot",
    "packages known to cause trouble with T-Pot are removed",
]


def mark(state: str) -> Text:
    glyph, colour = {"ok": ("ok", "ok"), "warn": ("warn", "warn"), "fail": ("fail", "error")}[state]
    return Text(glyphs.g(glyph), style=f"bold {theme.color(colour)}")


class Engine(engine.Engine):
    """Runs install.sh; replaced by a fake in the tests."""

    def __init__(self, command: List[str]):
        super().__init__(command, env=installer.engine_env(), cwd=os.path.dirname(command[0]))


class Steps(Static):
    """1 System check  ▸  2 Edition  ▸ ...; the step you are at in magenta, done ones ticked."""

    def show(self, steps: List[str], current: str) -> None:
        text = Text()
        keys = [key for key, _title in STEPS if key in steps]
        at = keys.index(current) if current in keys else 0
        for number, key in enumerate(keys, 1):
            title = dict(STEPS)[key]
            if number - 1 < at:
                text.append(f"{glyphs.g('ok')} {title}", style=theme.color("ok"))
            elif number - 1 == at:
                text.append(f" {number} {title} ", style=f"bold {theme.color('glass')} on {theme.color('magenta')}")
            else:
                text.append(f"{number} {title}", style=theme.color("ash"))
            if number < len(keys):
                text.append(f"  {glyphs.g('bullet')}  ", style=theme.color("wax"))
        self.update(text)


class InstallApp(ArrowNav, App):
    """The assistant. answers collects what the steps ask, the Engine does the work."""

    CSS_PATH = "../tpot.tcss"
    TITLE = "T-Pot installer"
    BINDINGS = [Binding("q", "leave", "Quit"), Binding("escape", "back", "Back", show=False), *NAV_BINDINGS]

    def notify(self, message, *args, markup: bool = False, **kwargs):
        """Notices carry paths and error texts: never read them as markup."""
        return super().notify(message, *args, markup=markup, **kwargs)

    def __init__(self, engine: Callable[[List[str]], Engine] = Engine, checks: Optional[Callable] = None,
                 sudo_mode: Optional[str] = None, password_ok: Optional[Callable[[str], bool]] = None,
                 run: Callable = subprocess.run, repo_dir: str = REPO_DIR):
        super().__init__()
        self.repo_dir = repo_dir
        self.engine_factory = engine
        self.check_runner = checks or installer.run_checks
        self.sudo = sudo_mode
        self.password_ok = password_ok or installer.sudo_password_ok
        self.run_command = run
        self.answers = installer.Answers(installer.EDITIONS[0])
        self.step = "check"
        self.checks: List[installer.Check] = []
        self.busy = False
        self.started = 0.0
        apply_theme(self)

    def get_theme_variable_defaults(self):
        return theme.variable_defaults()

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="ins-head"):
            yield Static(logo.wordmark(theme.color("glass")), id="wordmark")
            yield Static(self.source_text(), id="ins-source")
        yield Steps(id="ins-steps")
        with ContentSwitcher(initial="step-check", id="ins-body"):
            with NavScroll(id="step-check", classes="ins-step"):
                yield Static("", id="check-list")
            with Vertical(id="step-edition", classes="ins-step"):
                yield Static(Text("Which T-Pot? You can change it later with tpot customize.",
                                  style=theme.color("ash")), classes="ins-lead")
                yield NavOptionList(id="edition-list")
                yield Static("", id="edition-note")
            with NavScroll(id="step-user", classes="ins-step"):
                yield Static(Text("The user of the T-Pot web UI (Kibana, Attack Map, CyberChef, ...). More users "
                                  "later with tpot users.", style=theme.color("ash")), classes="ins-lead")
                yield NavInput(placeholder="user name: letters, digits, _ . -", id="ins-user-name")
                yield NavInput(placeholder="password", password=True, id="ins-user-password")
                yield NavInput(placeholder="repeat the password", password=True, id="ins-user-repeat")
                yield Static("", id="ins-user-hint")
            with NavScroll(id="step-settings", classes="ins-step settings-form"):
                yield Static(Text("A few settings now, all of them later on the Settings page of the T-Pot Manager.",
                                  style=theme.color("ash")), classes="ins-lead")
            with NavScroll(id="step-review", classes="ins-step"):
                yield Static("", id="review-text")
                yield NavInput(placeholder="your sudo password", password=True, id="ins-sudo")
                yield Static("", id="ins-sudo-hint")
            with Vertical(id="step-install", classes="ins-step"):
                yield Static("", id="install-phase")
                yield ProgressBar(total=1000, show_eta=False, id="install-bar")
                yield Static("", id="install-task")
                yield NavRichLog(id="install-log", max_lines=2000, wrap=False, markup=False, highlight=False)
            with NavScroll(id="step-done", classes="ins-step"):
                yield Static("", id="done-text")
        with Horizontal(id="ins-nav"):
            yield Static("", id="ins-nav-note")
            yield Button("Back", id="ins-back")
            yield Button("Next", id="ins-next", variant="primary")
        yield Footer()

    def source_text(self) -> Text:
        text = Text()
        text.append("Installer", style=f"bold {theme.color('glass')}")
        repo = os.environ.get("TPOT_REPO_URL", "https://github.com/telekom-security/tpotce")
        text.append(f"\n{repo} at {os.environ.get('TPOT_BRANCH', 'master')}", style=theme.color("ash"))
        return text

    def on_mount(self) -> None:
        self.fill_editions()
        self.goto("check")
        self.load_checks()

    # -- navigation ----------------------------------------------------------

    def steps(self) -> List[str]:
        return [key for key, _title in STEPS if key != "user" or self.answers.needs_web_user]

    def goto(self, step: str) -> None:
        self.step = step
        self.query_one("#ins-body", ContentSwitcher).current = f"step-{step}"
        self.query_one(Steps).show(self.steps(), step)
        back, nxt = self.query_one("#ins-back", Button), self.query_one("#ins-next", Button)
        back.display = step not in ("check", "install", "done")
        nxt.display = step not in ("install",)
        nxt.label = {"review": "Install T-Pot", "done": "Close"}.get(step, "Next")
        getattr(self, f"enter_{step}", lambda: None)()
        self.refresh_nav()

    def refresh_nav(self) -> None:
        problem = getattr(self, f"problem_{self.step}", lambda: "")()
        nxt = self.query_one("#ins-next", Button)
        nxt.disabled = bool(problem)
        self.query_one("#ins-nav-note", Static).update(
            Text(problem, style=theme.color("warn")) if problem else "")

    def next_step(self) -> None:
        keys = self.steps()
        if self.step == "review":
            self.start_install()
            return
        if self.step == "done":
            self.exit(0)
            return
        if self.step == "settings":
            self.keep_settings()
        self.goto(keys[keys.index(self.step) + 1])

    def action_back(self) -> None:
        keys = self.steps()
        if self.step in ("check", "install", "done") or len(self.screen_stack) > 1:
            return
        self.goto(keys[keys.index(self.step) - 1])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        if button == "ins-next":
            self.next_step()
        elif button == "ins-back":
            self.action_back()
        elif button == "check-again":
            self.load_checks()
        elif button == "done-reboot":
            self.reboot()
        elif button == "done-later":
            self.exit(0)

    def action_leave(self) -> None:
        if self.busy:
            self.notify("T-Pot is being installed, stopping now would leave the host half installed.",
                        title="Installing", severity="warning")
            return
        if self.step == "done":
            self.exit(0)
            return
        self.push_screen(ConfirmDialog("Quit the installer?",
                                       Text("Nothing of T-Pot is installed yet. The packages the installer needed "
                                            "and ~/tpotce stay, run the installer again to go on.",
                                            style=theme.color("glass")), yes="Quit", no="Stay"),
                         lambda yes: self.exit(1) if yes else None)

    def check_action(self, action: str, parameters):
        if action == "leave" and len(self.screen_stack) > 1:
            return False
        return True

    # -- 1 system check ------------------------------------------------------

    @work(thread=True, exclusive=True, group="checks")
    def load_checks(self) -> None:
        self.call_from_thread(self.show_checks, None)
        checks = self.check_runner()
        if self.sudo is None:
            self.sudo = installer.sudo_mode()
        memory, free = installer.resources()
        self.call_from_thread(self.show_checks, checks, memory, free)

    def show_checks(self, checks, memory: float = 0.0, free: float = 0.0) -> None:
        listing = self.query_one("#check-list", Static)
        if checks is None:
            listing.update(Text("Checking this host ...", style=theme.color("ash")))
            return
        self.checks, self.memory, self.free = checks, memory, free
        text = Text()
        for check in checks + [installer.check_resources(self.answers.edition, memory, free)]:
            text.append_text(mark(check.state))
            text.append(f"  {check.title:<28}", style="bold")
            text.append(f"{check.detail}\n", style=theme.color("ash") if check.state == "ok" else
                        theme.color("warn" if check.state == "warn" else "error"))
        if any(c.state == "fail" for c in checks):
            text.append("\nFix what is marked, then check again.", style=theme.color("glass"))
        listing.update(text)
        if not self.query("#check-again"):
            self.query_one("#step-check").mount(Button("Check again", id="check-again"))
        self.refresh_nav()
        # enter goes on, the keyboard is enough for the whole assistant
        nxt = self.query_one("#ins-next", Button)
        (self.query_one("#check-again", Button) if nxt.disabled else nxt).focus()

    def problem_check(self) -> str:
        if not self.checks:
            return "checking ..."
        failed = [c.title for c in self.checks if c.state == "fail"]
        return f"{', '.join(failed)}: not ready for T-Pot" if failed else ""

    # -- 2 edition -----------------------------------------------------------

    def fill_editions(self) -> None:
        options = self.query_one("#edition-list", OptionList)
        options.clear_options()
        for edition in installer.EDITIONS:
            label = Text()
            label.append(f"{edition.title:<9}", style="bold")
            label.append(edition.description, style=theme.color("glass"))
            label.append(f"\n         {edition.ram_gb} GB RAM, {edition.disk_gb} GB disk recommended",
                         style=theme.color("ash"))
            options.add_option(Option(label, id=edition.letter))
        custom = Text()
        custom.append(f"{'Custom':<9}", style="bold")
        custom.append("Start from an edition, switch services on and off, move ports (the customizer).",
                      style=theme.color("glass"))
        options.add_option(Option(custom, id="custom"))
        options.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "edition-list":
            return
        if event.option.id == "custom":
            self.open_customizer()
            return
        self.answers.edition = installer.EDITION_BY_LETTER[event.option.id]
        self.answers.custom_compose = ""
        self.show_edition()
        self.next_step()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "edition-list" and event.option.id in installer.EDITION_BY_LETTER:
            if not self.answers.custom_compose:
                self.answers.edition = installer.EDITION_BY_LETTER[event.option.id]
            self.show_edition()

    def show_edition(self) -> None:
        text = Text()
        if self.answers.custom_compose:
            text.append(f"{glyphs.g('on')} Custom: ", style=f"bold {theme.color('magenta')}")
            text.append(self.answers.custom_compose, style=theme.color("glass"))
        else:
            check = installer.check_resources(self.answers.edition, getattr(self, "memory", 0.0),
                                              getattr(self, "free", 0.0))
            text.append_text(mark(check.state))
            text.append(f" {check.detail}", style=theme.color("ash"))
        self.query_one("#edition-note", Static).update(text)
        self.query_one(Steps).show(self.steps(), self.step)

    def enter_edition(self) -> None:
        self.query_one("#edition-list", OptionList).focus()
        self.show_edition()

    def open_customizer(self) -> None:
        from tpotctl.screens.customizer import CustomizerScreen, core
        catalog = core.Catalog()
        base = "SENSOR" if self.answers.edition.letter == "s" else "STANDARD"
        self.push_screen(CustomizerScreen(catalog, core.Selection(base), core.DEFAULT_MAX_NETWORKS),
                         lambda chosen: self.customized(catalog, chosen))

    def customized(self, catalog, selection) -> None:
        from tpotctl.screens.customizer import core
        if selection is None:
            return
        try:
            result = core.resolve(catalog, selection, core.DEFAULT_MAX_NETWORKS, strict=True)
            if result.errors:
                raise core.CustomizerError(result.errors[0].text)
            path = os.path.join(self.repo_dir, "docker-compose-custom.yml")
            core.write_output(core.render(catalog, result), path)
        except (core.CustomizerError, OSError) as err:
            self.notify(str(err), title="Not written", severity="error", timeout=10)
            return
        role = result.role()
        letter = "s" if role == "SENSOR" or selection.base == "MOBILE" else "h"
        base_edition = installer.EDITION_BY_LETTER[letter]
        self.answers.edition = installer.Edition(letter, base_edition.key, f"Custom ({selection.base})",
                                                 "your own selection of services", letter == "h",
                                                 base_edition.ram_gb, base_edition.disk_gb)
        self.answers.custom_compose = path
        self.show_edition()
        self.next_step()

    # -- 3 web user ----------------------------------------------------------

    def enter_user(self) -> None:
        self.query_one("#ins-user-name", Input).focus()

    def problem_user(self) -> str:
        name = self.query_one("#ins-user-name", Input).value.strip()
        password = self.query_one("#ins-user-password", Input).value
        repeat = self.query_one("#ins-user-repeat", Input).value
        try:
            users.check_name(name)
        except users.UsersError as err:
            return str(err) if name else "enter a user name"
        if not password:
            return "enter a password"
        if password != repeat:
            return "the passwords do not match"
        return ""

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("ins-user"):
            hint = self.query_one("#ins-user-hint", Static)
            problem = self.problem_user()
            password = self.query_one("#ins-user-password", Input).value
            if not problem:
                weak = users.weakness(password)
                self.answers.web_user = self.query_one("#ins-user-name", Input).value.strip()
                self.answers.web_password = password
                hint.update(Text(f"{glyphs.g('warn')} weak: {weak}, it is accepted after a question" if weak
                                 else f"{glyphs.g('ok')} strong enough",
                                 style=theme.color("warn") if weak else theme.color("ok")))
            else:
                hint.update("")
            self.refresh_nav()
        elif event.input.id == "ins-sudo":
            self.query_one("#ins-sudo-hint", Static).update("")
            self.refresh_nav()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id and event.input.id.startswith("ins-user") and event.input.id != "ins-user-repeat":
            self.screen.focus_next()
        elif not self.query_one("#ins-next", Button).disabled:
            self.next_step()

    # -- 4 settings ----------------------------------------------------------

    def enter_settings(self) -> None:
        from tpotctl import envschema
        from tpotctl.envfile import EnvFile
        from tpotctl.settings import Settings
        from tpotctl.widgets.fields import SettingRow
        pane = self.query_one("#step-settings", VerticalScroll)
        for row in pane.query(SettingRow):
            row.remove()
        compose = self.answers.custom_compose or os.path.join(self.repo_dir, "compose",
                                                              f"{self.answers.edition.key}.yml")
        env_path = os.path.join(self.repo_dir, ".env")
        try:
            self.env_settings = Settings(env_path, EnvFile(env_path), envschema.load_schema(),
                                     envschema.compose_services(compose))
        except OSError as err:
            self.env_settings = None
            self.notify(f"{env_path} cannot be read ({err}), the settings stay as they are.", severity="warning")
            return
        self.draft = dict(self.env_settings.values)
        self.draft.setdefault("TPOT_TYPE", "SENSOR" if self.answers.edition.letter == "s" else "HIVE")
        rows = []
        for rule in self.env_settings.relevant(values=self.draft):
            if rule.key in FIRST_SETTINGS or (rule.key.startswith(LLM_PREFIXES) and self.answers.edition.key == "llm"):
                if rule.editable:
                    rows.append(SettingRow(rule, self.draft.get(rule.key, ""), ""))
        pane.mount(*rows)
        self.call_after_refresh(self.check_settings)
        self.call_after_refresh(self.query_one("#ins-next", Button).focus)

    def on_setting_row_changed(self, event) -> None:
        self.draft[event.key] = event.value
        self.check_settings()

    def on_setting_row_pick(self, event) -> None:
        from tpotctl.screens import pickers
        row = event.row
        if event.detect:
            found = pickers.detect_for(row.rule)
            if found:
                row.set_value(found)
                self.notify(pickers.detected_note(row.rule, found), title=row.rule.title)
            return
        self.push_screen(pickers.picker_for(row, self.draft, self.env_settings.schema),
                         lambda value: row.set_value(value) if value is not None else None)

    def settings_changes(self) -> Dict[str, str]:
        if not getattr(self, "env_settings", None):
            return {}
        saved = self.env_settings.values
        return {k: v for k, v in self.draft.items()
                if k in self.env_settings.schema and self.env_settings.schema[k].editable and saved.get(k, "") != v}

    def check_settings(self) -> None:
        from tpotctl import envschema
        from tpotctl.widgets.fields import SettingRow
        if not getattr(self, "env_settings", None):
            return
        problems = self.env_settings.problems(self.draft)
        changes = self.settings_changes()
        for row in self.query(SettingRow):
            row.display = envschema.shown_now(row.rule, self.draft, self.env_settings.schema)
            row.mark(row.rule.key in changes, [p for p in problems if p.key == row.rule.key])
        self.refresh_nav()

    def problem_settings(self) -> str:
        if not getattr(self, "env_settings", None):
            return ""
        blocking = self.env_settings.blocking(self.env_settings.problems(self.draft), self.settings_changes())
        return f"{blocking[0].key}: {blocking[0].text}" if blocking else ""

    def keep_settings(self) -> None:
        self.answers.settings = self.settings_changes()

    # -- 5 review ------------------------------------------------------------

    def enter_review(self) -> None:
        answers = self.answers
        text = Text()
        text.append("You install\n", style=f"bold {theme.color('magenta')}")
        rows = [("Edition", answers.edition.title + (f"  {answers.custom_compose}" if answers.custom_compose else ""))]
        if answers.needs_web_user:
            rows.append(("Web user", answers.web_user))
        for key, value in answers.settings.items():
            rows.append((key, value or "(empty)"))
        for title, value in rows:
            text.append(f"  {title:<30}", style=theme.color("ash"))
            text.append(f"{value}\n", style=theme.color("glass"))
        text.append("\nThis host changes\n", style=f"bold {theme.color('magenta')}")
        for change in CHANGES:
            text.append(f"  {glyphs.g('bullet')} ", style=theme.color("magenta"))
            text.append(f"{change}\n", style=theme.color("glass"))
        text.append("\nThe installation takes 10 to 30 minutes, the host needs a reboot afterwards.",
                    style=theme.color("ash"))
        if self.sudo == "password":
            text.append("\n\nsudo needs your password, the installer hands it to Ansible in a file only you can "
                        "read and removes it afterwards.", style=theme.color("ash"))
        self.query_one("#review-text", Static).update(text)
        sudo = self.query_one("#ins-sudo", Input)
        sudo.display = self.sudo == "password"
        self.query_one("#ins-sudo-hint", Static).display = self.sudo == "password"
        if sudo.display:
            sudo.focus()
        else:
            self.query_one("#ins-next", Button).focus()

    def problem_review(self) -> str:
        if self.sudo == "password" and not self.query_one("#ins-sudo", Input).value:
            return "enter your sudo password"
        return ""

    # -- 6 install -----------------------------------------------------------

    def start_install(self) -> None:
        if self.sudo == "password":
            password = self.query_one("#ins-sudo", Input).value
            if not self.password_ok(password):
                self.query_one("#ins-sudo-hint", Static).update(
                    Text(f"{glyphs.g('fail')} sudo does not accept this password", style=theme.color("error")))
                return
            self.answers.sudo_password = password
        weak = users.weakness(self.answers.web_password) if self.answers.needs_web_user else ""
        if weak:
            self.push_screen(ConfirmDialog(f"The web password is weak ({weak}). Keep it anyway?", yes="Keep it",
                                           no="Back"),
                             lambda yes: self.really_install() if yes else self.goto("user"))
            return
        self.really_install()

    def really_install(self) -> None:
        if self.answers.settings and getattr(self, "env_settings", None):
            from tpotctl.settings import SettingsError
            try:
                self.env_settings.change(self.answers.settings)
            except SettingsError as err:
                self.notify(str(err), title="Settings not written", severity="error", timeout=10)
                return
        self.progress = installer.Progress()
        self.busy = True
        self.started = time.monotonic()
        self.goto("install")
        self.set_interval(1.0, self.tick)
        self.run_engine()

    @work(thread=True, exclusive=True, group="engine")
    def run_engine(self) -> None:
        answers = self.answers
        secrets = {"web": answers.web_password if answers.needs_web_user else "",
                   "become": answers.sudo_password}
        with installer.secret_files(**secrets) as files:
            command = installer.engine_command(answers, files, os.path.join(self.repo_dir, "install.sh"))
            code = self.engine_factory(command).run(lambda line: self.call_from_thread(self.feed, line))
        self.call_from_thread(self.finished, code)

    def feed(self, line: str) -> None:
        self.progress.feed(line)
        self.query_one("#install-log", RichLog).write(Text(line.rstrip("\n")))
        self.show_progress()

    def tick(self) -> None:
        if self.busy:
            self.show_progress()

    def show_progress(self) -> None:
        progress = self.progress
        minutes, seconds = divmod(int(time.monotonic() - self.started), 60)
        phase = Text()
        phase.append(progress.title, style=f"bold {theme.color('glass')}")
        phase.append(f"   {minutes}:{seconds:02d}", style=theme.color("ash"))
        if progress.phase == "playbook" and progress.tasks:
            phase.append(f"   task {min(progress.tasks_done, progress.tasks)} of {progress.tasks}",
                         style=theme.color("ash"))
        if progress.phase == "pull" and progress.images:
            phase.append(f"   image {min(progress.images_done, progress.images)} of {progress.images}",
                         style=theme.color("ash"))
        self.query_one("#install-phase", Static).update(phase)
        self.query_one("#install-bar", ProgressBar).update(progress=int(progress.fraction * 1000))
        self.query_one("#install-task", Static).update(
            Text(f"{glyphs.g('bullet')} {progress.task}", style=theme.color("magenta")) if progress.task else "")

    def finished(self, code: int) -> None:
        self.busy = False
        if code == 0:
            self.progress.phase = "done"
            self.show_progress()
            self.goto("done")
            return
        self.progress.phase = "failed"
        text = Text()
        text.append(f"{glyphs.g('fail')} The installation failed", style=f"bold {theme.color('error')}")
        if self.progress.failed_task:
            text.append(f" at: {self.progress.failed_task}", style=theme.color("error"))
        text.append("\nThe whole log is ~/install_tpot.log. Fix the cause and run the installer again.",
                    style=theme.color("glass"))
        self.query_one("#install-phase", Static).update(text)
        nxt = self.query_one("#ins-next", Button)
        nxt.display, nxt.label, nxt.disabled = True, "Close", False
        self.step = "done"

    # -- 7 done --------------------------------------------------------------

    def enter_done(self) -> None:
        answers = self.answers
        text = Text()
        text.append(f"{glyphs.g('ok')} T-Pot is installed.\n\n", style=f"bold {theme.color('ok')}")
        for warning in getattr(getattr(self, "progress", None), "warnings", []):
            text.append(f"{glyphs.g('warn')} {warning}\n\n", style=theme.color("warn"))
        lines = ["Reboot the host, T-Pot starts with it.",
                 "SSH is on port 64295 from now on: ssh -p 64295 <user>@<host>"]
        if answers.needs_web_user:
            lines.append(f"The web UI is https://<host>:64297, sign in as {answers.web_user}.")
        if answers.edition.letter == "s":
            lines.append("Join it to your HIVE: on the HIVE run tpot sensors add.")
        launcher = os.path.join(self.repo_dir, "tpot")       # the checkout this assistant installs
        if installer.tpot_on_path(self.repo_dir):
            lines.append("Afterwards run the T-Pot Manager: tpot (i.e. tpot status), it shows and changes everything.")
        else:
            text.append(f"{glyphs.g('warn')} The command tpot does not run this T-Pot Manager yet, link it with: "
                        f"sudo ln -sfn {launcher} /usr/local/bin/tpot\n\n",
                        style=theme.color("warn"))
            lines.append(f"Afterwards run the T-Pot Manager: {launcher}, it shows and changes everything.")
        for line in lines:
            text.append(f"  {glyphs.g('bullet')} ", style=theme.color("magenta"))
            text.append(f"{line}\n", style=theme.color("glass"))
        self.query_one("#done-text", Static).update(text)
        nxt = self.query_one("#ins-next", Button)
        nxt.display = False
        if not self.query("#done-reboot"):
            self.query_one("#ins-nav").mount(Button("Later", id="done-later"), before=nxt)
            self.query_one("#ins-nav").mount(Button("Reboot now", id="done-reboot", variant="primary"), before=nxt)
        self.query_one("#done-reboot", Button).focus()

    def reboot(self) -> None:
        if self.answers.sudo_password:
            self.run_command(["sudo", "-S", "-p", "", "reboot"], input=self.answers.sudo_password + "\n",
                             universal_newlines=True)
        else:
            self.run_command(["sudo", "reboot"])
        self.exit(0)


def run_install() -> int:
    result = InstallApp().run()
    return int(result or 0)
