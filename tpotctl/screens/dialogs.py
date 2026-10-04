"""Small modal dialogs shared by the screens."""

from typing import List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, OptionList, Static


def finding_lines(lines: List[str]) -> Text:
    text = Text()
    for line in lines:
        style = "bold red" if line.startswith("ERROR") else "yellow" if line.startswith("WARNING") else ""
        text.append(line + "\n", style=style)
    return text


class ConfirmDialog(ModalScreen):
    """Yes / no, dismissed with True or False."""

    BINDINGS = [Binding("escape", "no", "Back"), Binding("y", "yes", "Yes", show=False)]

    def __init__(self, title: str, body=None, yes: str = "Yes", no: str = "Back", allow_yes: bool = True):
        super().__init__()
        self.title_text, self.body, self.yes_label, self.no_label = title, body, yes, no
        self.allow_yes = allow_yes

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            if self.body is not None:
                with VerticalScroll():
                    yield Static(self.body)
            with Horizontal(classes="actions"):
                if self.allow_yes:
                    yield Button(self.yes_label, variant="primary", id="yes")
                yield Button(self.no_label, id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        if self.allow_yes:
            self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class ChoiceDialog(ModalScreen):
    """Pick one of several options, dismissed with its index or None."""

    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, title: str, options: List[str], current: int = 0):
        super().__init__()
        self.title_text, self.options, self.current = title, options, current

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            yield OptionList(*self.options, id="choices")

    def on_mount(self) -> None:
        choices = self.query_one(OptionList)
        choices.highlighted = self.current
        choices.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_index)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PortInputDialog(ModalScreen):
    """A new host port: a number, '-' removes the mapping, empty keeps it."""

    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, title: str):
        super().__init__()
        self.title_text = title
        self.error: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            yield Input(placeholder="host port, - removes the mapping", id="port")
            yield Label("", id="port-error", classes="error-text")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value in ("", "-") or (value.isdigit() and 1 <= int(value) <= 65535):
            self.dismiss(value)
        else:
            self.query_one("#port-error", Label).update(f"'{value}' is not a port")

    def action_cancel(self) -> None:
        self.dismiss("")


class UserDialog(ModalScreen):
    """Name (for a new user) and password twice, dismissed with (name, password) or None."""

    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, title: str, name: str = "", check_name=None, weakness=None):
        super().__init__()
        self.title_text, self.fixed_name = title, name
        self.check_name, self.weakness = check_name, weakness

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            if not self.fixed_name:
                yield Input(placeholder="user name: letters, digits, _ . -", id="user-name")
            yield Input(placeholder="password", password=True, id="user-password")
            yield Input(placeholder="repeat the password", password=True, id="user-repeat")
            yield Label("", id="user-hint")
            with Horizontal(classes="actions"):
                yield Button("Save", variant="primary", id="user-save")
                yield Button("Back", id="user-back")

    def on_mount(self) -> None:
        self.query(Input).first().focus()

    def values(self):
        name = self.fixed_name or self.query_one("#user-name", Input).value.strip()
        return name, self.query_one("#user-password", Input).value, self.query_one("#user-repeat", Input).value

    def problem(self) -> str:
        name, password, repeat = self.values()
        if not self.fixed_name and self.check_name:
            try:
                self.check_name(name)
            except Exception as err:   # UsersError, kept generic to stay UI-only
                return str(err)
        if not password:
            return "enter a password"
        if password != repeat:
            return "the passwords do not match"
        return ""

    def on_input_changed(self, event: Input.Changed) -> None:
        hint = self.query_one("#user-hint", Label)
        problem = self.problem()
        if problem:
            hint.update(Text(problem, style="yellow"))
            return
        weak = self.weakness(self.values()[1]) if self.weakness else None
        hint.update(Text(f"weak: {weak}" if weak else "strong enough", style="yellow" if weak else "green"))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "user-repeat":
            self.save()
        else:
            self.focus_next()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "user-save":
            self.save()
        else:
            self.dismiss(None)

    def save(self) -> None:
        problem = self.problem()
        if problem:
            self.query_one("#user-hint", Label).update(Text(problem, style="bold red"))
            return
        name, password, _repeat = self.values()
        self.dismiss((name, password))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SensorDialog(ModalScreen):
    """Where a new sensor is, dismissed with a dict or None."""

    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, check_address=None, check_user=None, default_hive=None):
        super().__init__()
        self.check_address, self.check_user, self.default_hive = check_address, check_user, default_hive

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Deploy a sensor", classes="dialog-title")
            yield Static(Text("T-Pot has to be installed on it already. The deployment runs in the terminal "
                              "(SSH key, sudo password) and reboots the sensor.", style="dim"))
            yield Input(placeholder="IP or name of the sensor", id="sensor-host")
            yield Input(placeholder="user T-Pot was installed with on the sensor", id="sensor-user")
            yield Input(placeholder="IP or name the sensor reaches this HIVE on", id="sensor-hive")
            yield Checkbox("sudo on the sensor needs no password", id="sensor-nopass")
            yield Label("", id="sensor-hint")
            with Horizontal(classes="actions"):
                yield Button("Deploy", variant="primary", id="sensor-deploy")
                yield Button("Back", id="sensor-back")

    def on_mount(self) -> None:
        self.query_one("#sensor-host", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "sensor-host" and self.default_hive:
            hive = self.query_one("#sensor-hive", Input)
            if not hive.value:
                hive.placeholder = f"IP or name the sensor reaches this HIVE on [{self.default_hive(event.value)}]" \
                    if event.value else "IP or name the sensor reaches this HIVE on"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.focus_next()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "sensor-deploy":
            self.dismiss(None)
            return
        host = self.query_one("#sensor-host", Input).value.strip()
        user = self.query_one("#sensor-user", Input).value.strip()
        hive = self.query_one("#sensor-hive", Input).value.strip()
        try:
            if self.check_address:
                self.check_address(host)
                if hive:
                    self.check_address(hive)
            if self.check_user:
                self.check_user(user)
        except Exception as err:   # SensorsError, kept generic to stay UI-only
            self.query_one("#sensor-hint", Label).update(Text(str(err), style="bold red"))
            return
        self.dismiss({"host": host, "user": user, "hive": hive,
                      "nopass": self.query_one("#sensor-nopass", Checkbox).value})

    def action_cancel(self) -> None:
        self.dismiss(None)
