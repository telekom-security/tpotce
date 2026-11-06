"""Collect sudo-mode answers before starting the noninteractive task."""

from dataclasses import dataclass, field
from typing import Callable, Optional

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Select, Static

from tpotctl import theme, users
from tpotctl.widgets.fields import TpotSelect
from tpotctl.widgets.nav import DialogTitle, NavInput, NavModal, NavScroll


@dataclass
class SudoChoice:
    action: str
    password: str = field(default="", repr=False)
    user_password: str = field(default="", repr=False)


class SudoDialog(NavModal):
    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, name: str, account: Callable[[], bool], *, remote: bool = False,
                 passwordless: bool = False):
        super().__init__()
        self.name_text, self.account_check = name, account
        self.remote, self.passwordless = remote, passwordless
        self.account: Optional[bool] = None
        self.account_error = ""
        self.checking = True

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog compact"):
            yield DialogTitle(f"Sudo mode: {self.name_text}")
            with NavScroll():
                yield Static("Foreign sudo rules stay. The task verifies access before removing T-Pot's rule. "
                             "An added sudo group needs a new local login.", classes="hint")
                yield TpotSelect([("Check the current mode", "status"), ("More secure (sudo asks for a password)", "secure"),
                                  ("More convenient (passwordless sudo)", "convenient")],
                                 value="status", allow_blank=False, id="sudo-action")
                yield Checkbox("sudo needs no password on this sensor", id="sudo-nopass")
                yield NavInput(placeholder="sudo password", password=True, id="sudo-password")
                yield Static("Checking the login account ...", id="sudo-account", classes="hint")
                yield NavInput(placeholder="new user password", password=True, id="sudo-user-password")
                yield NavInput(placeholder="repeat the new user password", password=True, id="sudo-user-repeat")
                yield Static("", id="sudo-hint", classes="hint")
            with Horizontal(classes="actions"):
                yield Button("Back", id="sudo-back")
                yield Button("Run", id="sudo-run", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#sudo-nopass", Checkbox).display = self.remote
        self.refresh_fields()
        self.query_one("#sudo-back", Button).focus()
        self.check_account()

    @work(thread=True, exclusive=True, group="sudo-account")
    def check_account(self) -> None:
        try:
            account, problem = self.account_check(), ""
        except OSError as err:
            account, problem = None, str(err)
        if self.is_attached:
            self.app.call_from_thread(self.checked_account, account, problem)

    def checked_account(self, account: Optional[bool], problem: str) -> None:
        if not self.is_attached:
            return
        self.account, self.account_error, self.checking = account, problem, False
        self.refresh_fields()

    def action_value(self) -> str:
        value = self.query_one("#sudo-action", Select).value
        return value if isinstance(value, str) else ""

    def needs_password(self) -> bool:
        return not self.query_one("#sudo-nopass", Checkbox).value if self.remote else not self.passwordless

    def needs_user_password(self) -> bool:
        return self.action_value() == "secure" and self.account is False

    def problem(self) -> str:
        if self.action_value() not in ("status", "secure", "convenient"):
            return "Choose a mode."
        if self.action_value() == "secure" and self.account is None:
            return "Checking the login account ..." if self.checking else "Cannot verify the login password state."
        password = self.query_one("#sudo-password", Input).value
        if self.needs_password() and (not password or any(c in password for c in "\r\n\0")):
            return "Enter the sudo password."
        if self.needs_user_password():
            password = self.query_one("#sudo-user-password", Input).value
            if not password or any(c in password for c in "\r\n\0"):
                return "Enter a new, single-line user password."
            if password != self.query_one("#sudo-user-repeat", Input).value:
                return "The new passwords do not match."
            weak = users.weakness(password)
            if weak:
                return "Choose a stronger user password: " + weak
        return ""

    def refresh_fields(self) -> None:
        self.query_one("#sudo-password", Input).display = self.needs_password()
        for key in ("sudo-user-password", "sudo-user-repeat"):
            self.query_one("#" + key, Input).display = self.needs_user_password()
        account = self.account_error or (
            "Checking the login account ..." if self.checking else
            "The login account has a usable password." if self.account else
            "More secure needs a new user password. SSH also uses it when password logins are enabled.")
        self.query_one("#sudo-account", Static).update(Text(account))
        problem = self.problem()
        self.query_one("#sudo-hint", Static).update(Text(problem, style=theme.color("warn")))
        self.query_one("#sudo-run", Button).disabled = bool(problem)
        self.call_after_refresh(self.fit_dialog)

    def on_select_changed(self, _event: Select.Changed) -> None:
        self.refresh_fields()

    def on_checkbox_changed(self, _event: Checkbox.Changed) -> None:
        self.refresh_fields()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self.refresh_fields()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "sudo-back":
            self.dismiss(None)
        else:
            self.refresh_fields()
            if not self.problem():
                self.dismiss(SudoChoice(
                    self.action_value(), self.query_one("#sudo-password", Input).value if self.needs_password() else "",
                    self.query_one("#sudo-user-password", Input).value if self.needs_user_password() else ""))

    def action_cancel(self) -> None:
        self.dismiss(None)
