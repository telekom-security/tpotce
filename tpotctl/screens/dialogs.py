"""Small modal dialogs shared by the screens."""

from typing import List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static


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
