"""One row of the Settings page per .env key, with the control that fits the key.

The schema says which: widget switch / choice / interface / timezone / llm_model / llm_url,
else the type (enum -> Select, int / number -> Input for numbers, secret -> masked
Input with a button to show it). Every control is #set-<KEY>; whatever it is, a
change arrives as SettingRow.Changed(key, value) with the text written to .env.
"""

from typing import Dict, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Label, Select, Static, Switch

from tpotctl import envschema, glyphs, theme

OTHER = "\x00other"


class TpotSelect(Select, inherit_bindings=False):
    """A Select that opens with enter or space only: up and down move between the settings."""

    BINDINGS = [Binding("enter,space", "show_overlay", "Choose", show=False)]


class SettingRow(Vertical):
    """Title and key, the control, help, the problems of the key."""

    class Changed(Message):
        def __init__(self, key: str, value: str):
            super().__init__()
            self.key, self.value = key, value

    class Pick(Message):
        """The row wants a picker (interface, time zone, models), the page opens it."""

        def __init__(self, row: "SettingRow", detect: bool = False):
            super().__init__()
            self.row, self.detect = row, detect

    class Unlock(Message):
        """The row of a fixed key asks to be unlocked, the page shows the warning first."""

        def __init__(self, row: "SettingRow"):
            super().__init__()
            self.row = row

    def __init__(self, rule: envschema.Rule, value: str, fixed: str, unlockable: bool = False,
                 unlocked: bool = False, note: str = ""):
        super().__init__(classes="setting", id=f"row-{rule.key}")
        self.rule, self.value, self.fixed = rule, value, fixed
        self.note = note                # why the key does not apply here (not in your edition, ...)
        self.unlockable, self.unlocked = unlockable and not rule.editable, unlocked
        # a row without a field can take the focus itself, so the arrows reach every setting
        self.can_focus = not rule.editable and not self.unlockable

    def controls(self):
        """What takes the focus in this row, in order."""
        if self.can_focus:
            return [self]
        return [w for w in self.query("Input, Select, Switch, Button") if w.focusable]

    # -- building ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        rule = self.rule
        with Horizontal(classes="setting-line"):
            with Vertical(classes="setting-name"):
                yield Label(self.title_text(False), classes="setting-title")
                yield Label(Text(rule.key), classes="setting-key")
                if self.note:
                    yield Label(Text(self.note, style=theme.color("ash")), classes="setting-absent")
            with Horizontal(classes="setting-control"):
                yield from self.control()
            yield Static(Text(rule.help), classes="help")
        if rule.help:
            yield Static(Text(rule.help), classes="help-below")
        yield Static("", id=f"err-{rule.key}", classes="setting-problems")

    def control(self) -> ComposeResult:
        rule, value, key = self.rule, self.value, self.rule.key
        if not rule.editable:
            from tpotctl import settings as tsettings
            shown = tsettings.shown(rule, value) or "(empty)"
            text = Text(f"{glyphs.g('locked')} ", style=theme.color("ash"))
            text.append(shown, style=theme.color("glass"))
            if self.fixed.startswith("managed"):
                text.append(f"   {self.fixed}", style=theme.color("ash"))
            yield Static(text, classes="fixed")
            if self.unlockable:
                yield Button("Unlock", id=f"unlock-{key}", classes="small unlock", compact=True)
        elif rule.widget == "switch":
            yield Switch((value or rule.default) == rule.values[0], id=f"set-{key}")
            yield Label(self.switch_label(value or rule.default), id=f"state-{key}", classes="switch-state")
        elif rule.widget == "choice":
            listed = [c["value"] for c in rule.choices]
            custom = value not in listed and bool(value)
            options = [(c["label"], c["value"]) for c in rule.choices]
            if rule.custom:
                options.append(("other ...", OTHER))
            current = OTHER if custom else (value if value in listed else Select.NULL)
            yield TpotSelect(options, value=current, id=f"set-{key}", allow_blank=current is Select.NULL, compact=True)
            if rule.custom:
                own = Input(value if custom else "", id=f"own-{key}", placeholder="your value", classes="own",
                        compact=True)
                own.display = custom
                yield own
        elif rule.type == "enum":
            current = value if value in rule.values else Select.NULL
            yield TpotSelect([(v, v) for v in rule.values], value=current, id=f"set-{key}",
                         allow_blank=rule.optional or current is Select.NULL, compact=True)
        elif rule.widget in ("interface", "timezone", "llm_model", "llm_url"):
            placeholder = {"interface": "automatic", "timezone": rule.default or "UTC"}.get(rule.widget, rule.default)
            yield Input(value, id=f"set-{key}", placeholder=placeholder, compact=True)
            yield Button("Find" if rule.widget == "llm_url" else "Choose", id=f"pick-{key}", classes="small",
                         compact=True)
            if rule.widget in ("interface", "timezone"):
                yield Button("Detect", id=f"detect-{key}", classes="small", compact=True)
        elif rule.secret:
            yield Input(value, password=True, id=f"set-{key}", placeholder=rule.default, compact=True)
            yield Button(glyphs.g("show"), id=f"reveal-{key}", classes="small reveal", tooltip="show / hide",
                         compact=True)
        elif rule.type in ("int", "number"):
            yield Input(value, type="integer" if rule.type == "int" else "number", id=f"set-{key}",
                        placeholder=rule.default, compact=True)
        else:
            yield Input(value, id=f"set-{key}", placeholder=rule.default, compact=True)

    def switch_label(self, value: str) -> Text:
        on = value == self.rule.values[0]
        return Text(value, style=f"bold {theme.color('glass')}" if on else theme.color("ash"))

    # -- changes -------------------------------------------------------------

    def on_switch_changed(self, event: Switch.Changed) -> None:
        event.stop()
        value = self.rule.values[0] if event.value else self.rule.values[1]
        self.query_one(f"#state-{self.rule.key}", Label).update(self.switch_label(value))
        self.post_message(self.Changed(self.rule.key, value))

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        value = "" if event.value is Select.NULL else str(event.value)
        if self.rule.widget == "choice" and self.rule.custom:
            own = self.query_one(f"#own-{self.rule.key}", Input)
            own.display = value == OTHER
            if value == OTHER:
                own.focus()
                value = own.value
        self.post_message(self.Changed(self.rule.key, value))

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.post_message(self.Changed(self.rule.key, event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button.id or ""
        if button.startswith("reveal-"):
            field = self.query_one(f"#set-{self.rule.key}", Input)
            field.password = not field.password
            event.button.label = glyphs.g("show") if field.password else glyphs.g("hide")
        elif button.startswith("unlock-"):
            self.post_message(self.Unlock(self))
        elif button.startswith("pick-"):
            self.post_message(self.Pick(self))
        elif button.startswith("detect-"):
            self.post_message(self.Pick(self, detect=True))

    def set_value(self, value: str) -> None:
        """From a picker: into the field, which reports the change."""
        self.query_one(f"#set-{self.rule.key}", Input).value = value

    def current(self) -> str:
        field = self.query_one(f"#set-{self.rule.key}")
        return field.value if isinstance(field, Input) else ""

    def title_text(self, changed: bool) -> Text:
        text = Text(self.rule.title, style="bold")
        if self.unlocked:
            text.append("  ")
            text.append(f"{glyphs.g('warn')} unlocked", style=f"bold {theme.color('warn')}")
        if changed:
            text.append("  ")
            text.append(f"{glyphs.g('changed')}changed", style=f"bold {theme.color('magenta')}")
        return text

    def mark(self, changed: bool, problems) -> None:
        if changed != self.has_class("-changed"):
            self.query_one(".setting-title", Label).update(self.title_text(changed))
        self.set_class(changed, "-changed")
        text = Text()
        for problem in problems:
            error = problem.level == "error"
            text.append(f"{glyphs.g('fail') if error else glyphs.g('warn')} {problem.text}\n",
                        style=theme.color("error") if error else theme.color("warn"))
        text.rstrip()
        self.query_one(f"#err-{self.rule.key}", Static).update(text)
        self.set_class(any(p.level == "error" for p in problems), "-problem")
        self.set_class(bool(problems) and not self.has_class("-problem"), "-warning")


def llm_settings(rule: envschema.Rule, draft: Dict[str, str], schema) -> Optional[Dict[str, str]]:
    """provider, url, api_key for the model picker of an llm_model key, defaults filled in."""
    from tpotctl import llm
    return llm.settings_of(rule, draft, schema)
