"""Choose a value from a list you can search: interfaces, time zones, LLM models.

One dialog for all of them. The items are loaded in a thread (an interface list
runs ip, a model list asks a server), typing filters them, enter takes the
highlighted one; with `free` a value that is not in the list is taken as typed.
Dismissed with the value or None.

ManyPicker is the one for a list setting (widget choice_many): tick any of its
values, under the headings of its groups.
"""

from typing import Callable, Dict, List, Optional, Tuple

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, OptionList, Static
from textual.widgets.option_list import Option

from tpotctl import glyphs, theme
from tpotctl.widgets.nav import DialogTitle, NavInput, NavModal, NavOptionList, navigate

Item = Tuple[str, Text, bool]      # value, label, hidden unless "show all"


def substring(query: str, items: List[Item]) -> List[Item]:
    words = query.lower().split()
    return [i for i in items if all(w in f"{i[0]} {i[1].plain}".lower() for w in words)]


class Picker(NavModal):

    BINDINGS = [Binding("escape", "cancel", "Back"), Binding("down", "to_list", "List", show=False)]

    def __init__(self, title: str, load: Callable[[], List[Item]], current: str = "",
                 detect: Optional[Callable[[], str]] = None, search: Callable = substring, free: bool = False,
                 show_all: str = "", note: str = ""):
        super().__init__()
        self.title_text, self.load_items, self.current = title, load, current
        self.detect, self.search, self.free, self.show_all, self.note = detect, search, free, show_all, note
        self.items: List[Item] = []
        self.shown: List[Item] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog picker"):
            yield DialogTitle(self.title_text)
            if self.note:
                yield Static(Text(self.note), classes="hint")
            yield NavInput(placeholder=f"{glyphs.g('search')} type to search" + (", or enter a value" if self.free else ""),
                        id="picker-filter")
            yield NavOptionList(id="picker-list")
            yield Static(Text("loading ...", style=theme.color("mist")), id="picker-message")
            with Horizontal(classes="actions"):
                if self.show_all:
                    yield Checkbox(self.show_all, id="picker-all")
                if self.detect:
                    yield Button("Detect", id="picker-detect")
                yield Button("Take it", variant="primary", id="picker-take")
                yield Button("Back", id="picker-back")

    def on_mount(self) -> None:
        self.query_one("#picker-filter", Input).focus()
        self.fetch()

    @work(thread=True, exclusive=True, group="picker")
    def fetch(self) -> None:
        try:
            items, problem = self.load_items(), ""
        except Exception as err:      # LLMError and friends, the text is for the user
            items, problem = [], str(err)
        self.app.call_from_thread(self.loaded, items, problem)

    def loaded(self, items: List[Item], problem: str) -> None:
        self.items = items
        message = self.query_one("#picker-message", Static)
        if problem:
            message.update(Text(f"{glyphs.g('fail')} {problem}", style=theme.color("error")))
        else:
            message.update("")
        self.refill(highlight=self.current)

    def refill(self, highlight: str = "") -> None:
        query = self.query_one("#picker-filter", Input).value
        show_all = bool(self.show_all) and self.query_one("#picker-all", Checkbox).value
        candidates = [i for i in self.items if show_all or not i[2] or i[0] == self.current]
        self.shown = self.search(query, candidates)
        options = self.query_one("#picker-list", OptionList)
        options.clear_options()
        options.add_options([Option(self.mark(item), id=str(index)) for index, item in enumerate(self.shown)])
        values = [i[0] for i in self.shown]
        if highlight in values:
            options.highlighted = values.index(highlight)
        elif self.shown:
            options.highlighted = 0
        if self.items and not self.shown:
            self.query_one("#picker-message", Static).update(
                Text("nothing matches" + (", enter takes what you typed" if self.free else ""),
                     style=theme.color("mist")))
        elif self.items:
            self.query_one("#picker-message", Static).update("")

    def mark(self, item: Item) -> Text:
        value, label, _hidden = item
        text = Text()
        text.append(f"{glyphs.g('on') if value == self.current else ' '} ", style=f"bold {theme.color('magenta')}")
        text.append_text(label)
        return text

    def on_input_changed(self, event: Input.Changed) -> None:
        self.refill()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self.refill(highlight=self.highlighted_value())

    def highlighted_value(self) -> Optional[str]:
        options = self.query_one("#picker-list", OptionList)
        if options.highlighted is None or not self.shown:
            return None
        return self.shown[options.highlighted][0]

    def take(self) -> None:
        typed = self.query_one("#picker-filter", Input).value.strip()
        value = self.highlighted_value()
        if value is None and self.free and typed:
            value = typed
        if value is not None:
            self.dismiss(value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.take()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.take()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "picker-take":
            self.take()
        elif event.button.id == "picker-detect":
            self.run_detect()
        else:
            self.dismiss(None)

    @work(thread=True, exclusive=True, group="picker-detect")
    def run_detect(self) -> None:
        found = self.detect() if self.detect else ""
        self.app.call_from_thread(self.detected, found)

    def detected(self, found: str) -> None:
        message = self.query_one("#picker-message", Static)
        if not found:
            message.update(Text(f"{glyphs.g('warn')} nothing detected", style=theme.color("warn")))
            return
        self.query_one("#picker-filter", Input).value = ""
        if self.show_all and found not in [i[0] for i in self.items if not i[2]]:
            self.query_one("#picker-all", Checkbox).value = True
        self.refill(highlight=found)
        message.update(Text(f"{glyphs.g('ok')} detected {found}", style=theme.color("ok")))
        self.query_one("#picker-list", OptionList).focus()

    def action_to_list(self) -> None:
        """down: from the search field into the list, everywhere else the arrows of every dialog."""
        if self.focused is self.query_one("#picker-filter", Input):
            self.query_one("#picker-list", OptionList).focus()
        else:
            navigate(self.app, "down", menu=False)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ManyList(NavOptionList):
    """The list of a ManyPicker: enter or space ticks the highlighted line."""

    BINDINGS = [Binding("space", "select", "Tick", show=False)]


class ManyPicker(NavModal):
    """Tick any of the values of a list setting (choice_list), under the headings of its groups.

    enter / space ticks the highlighted value, on a heading every value of the group (or none when
    all of them are ticked already). Nothing ticked is the empty value, which means all of them; so
    is everything ticked, and it is saved empty, values that come later are then in it too.
    Dismissed with the ticked values in the order of `values`, separated by spaces, or None."""

    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, title: str, values: List[str], groups: Optional[Dict[str, List[str]]] = None,
                 current: str = "", note: str = ""):
        super().__init__()
        self.title_text, self.values, self.note = title, list(values), note
        self.groups = dict(groups) if groups else {"": list(values)}
        self.chosen = {v for v in current.split() if v in self.values}
        # typed into the field but not one of the values: Take it leaves them out, the status says so
        self.dropped = list(dict.fromkeys(v for v in current.split() if v not in self.values))
        # what each line of the list is: ("group", title) or ("value", value)
        self.lines: List[Tuple[str, str]] = []
        for group, members in self.groups.items():
            if group:
                self.lines.append(("group", group))
            self.lines.extend(("value", member) for member in members)

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog compact picker many"):
            yield DialogTitle(self.title_text)
            if self.note:
                yield Static(Text(self.note), classes="hint")
            yield ManyList(*[Option(self.line_text(i), id=str(i)) for i in range(len(self.lines))],
                           id="many-list")
            yield Static(self.status_text(), id="many-status")
            with Horizontal(classes="actions"):
                yield Button("Take it", variant="primary", id="many-take")
                yield Button("Clear", id="many-clear", tooltip="nothing ticked: all of them")
                yield Button("Back", id="many-back")

    def fit_dialog(self) -> None:
        """The list as tall as its lines, in a small terminal what is left beside the rest of the card."""
        super().fit_dialog()
        for listing in self.query("#many-list"):
            listing.styles.max_height = max(4, min(len(self.lines), self.app.size.height - 14))

    def on_mount(self) -> None:
        listing = self.query_one("#many-list", OptionList)
        first = next((i for i, (kind, value) in enumerate(self.lines)
                      if kind == "value" and value in self.chosen), 0)
        listing.highlighted = first
        listing.focus()

    def mark(self, on: bool) -> Text:
        return Text(f"{glyphs.g('on') if on else glyphs.g('off')} ",
                    style=f"bold {theme.color('magenta')}" if on else theme.color("ash"))

    def line_text(self, index: int) -> Text:
        kind, value = self.lines[index]
        if kind == "group":
            members = self.groups[value]
            ticked = sum(1 for m in members if m in self.chosen)
            text = self.mark(ticked == len(members))
            text.append(value, style=f"bold {theme.color('glass')}")
            text.append(f"  {ticked} of {len(members)}", style=theme.color("ash"))
            return text
        text = Text("" if "" in self.groups else "  ")       # under a heading: indented
        text.append_text(self.mark(value in self.chosen))
        text.append(value, style=theme.color("glass") if value in self.chosen else theme.color("mist"))
        return text

    def status_text(self) -> Text:
        count, total = len(self.chosen), len(self.values)
        if not count:
            text = Text(f"Nothing ticked: all {total}, the setting stays empty.", style=theme.color("mist"))
        elif count == total:
            text = Text(f"All {total} ticked: saved empty, the same as nothing ticked.", style=theme.color("mist"))
        else:
            text = Text(f"{count} of {total} ticked.", style=theme.color("glass"))
        if self.dropped:
            one = len(self.dropped) == 1
            text.append(f"\n{glyphs.g('warn')} {', '.join(self.dropped)} {'is' if one else 'are'} not on the list, "
                        f"Take it leaves {'it' if one else 'them'} out.", style=theme.color("warn"))
        return text

    def value(self) -> str:
        if len(self.chosen) == len(self.values):
            return ""
        return " ".join(v for v in self.values if v in self.chosen)

    def toggle(self, index: int) -> None:
        kind, value = self.lines[index]
        members = self.groups[value] if kind == "group" else [value]
        if all(m in self.chosen for m in members):
            self.chosen -= set(members)
        else:
            self.chosen |= set(members)
        self.show_chosen()

    def show_chosen(self) -> None:
        """The marks, the counts of the headings and the line below, in place: nothing scrolls."""
        listing = self.query_one("#many-list", OptionList)
        for i in range(len(self.lines)):
            listing.replace_option_prompt_at_index(i, self.line_text(i))
        self.query_one("#many-status", Static).update(self.status_text())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.toggle(event.option_index)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "many-take":
            self.dismiss(self.value())
        elif event.button.id == "many-clear":
            self.chosen = set()
            self.show_chosen()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def many_picker(rule, current: str) -> ManyPicker:
    """The ManyPicker of a choice_many setting; an earlier name of a value (legacy) is ticked as that value."""
    legacy = getattr(rule, "legacy", None) or {}
    current = " ".join(dict.fromkeys(legacy.get(entry, entry) for entry in current.split()))
    return ManyPicker(rule.title, rule.values, rule.groups, current,
                      note="Enter or space ticks a line, a heading its whole group. Nothing ticked is all of them.")


# -- the three pickers -------------------------------------------------------

def interface_items(automatic: str, interfaces) -> List[Item]:
    mist = theme.color("mist")
    label = Text("Automatic", style="bold")
    label.append(f"   the interface of the route to the internet, now {automatic or 'none'}", style=mist)
    items: List[Item] = [("", label, False)]
    for interface in interfaces:
        text = Text(f"{interface.name:<16}", style="bold")
        state = ("up", theme.color("ok")) if interface.up else ("down", theme.color("error"))
        text.append(f"{glyphs.g('running') if interface.up else glyphs.g('stopped')} {state[0]:<5}", style=state[1])
        # IPv4 first, link-local addresses say nothing about where it leads
        shown = [a for a in interface.addresses if "." in a] + \
            [a for a in interface.addresses if ":" in a and not a.lower().startswith("fe80")]
        text.append("  " + "  ".join(shown[:2]), style=mist)
        items.append((interface.name, text, interface.internal))
    return items


def interface_picker(current: str) -> Picker:
    from tpotctl import netinfo

    def load() -> List[Item]:
        found = netinfo.interfaces()
        return interface_items(netinfo.detect(found=found), found)

    return Picker("Capture interface of Suricata, P0f and Glutton", load, current, detect=netinfo.detect,
                  show_all="show lo, docker and veth", free=True,
                  note="Automatic is the right choice for most hosts, it follows the route to the internet.")


def timezone_picker(current: str) -> Picker:
    from tpotctl import tz

    def load() -> List[Item]:
        return [(zone, Text(zone), False) for zone in tz.zones()]

    def search(query: str, items: List[Item]) -> List[Item]:
        by_value = {i[0]: i for i in items}
        return [by_value[z] for z in tz.search(query, [i[0] for i in items])]

    return Picker("Time zone", load, current or "UTC", detect=tz.detect, search=search)


def model_picker(current: str, provider: str, url: str, api_key: str) -> Picker:
    from tpotctl import llm

    def load() -> List[Item]:
        return [(name, Text(name), False) for name in llm.list_models(provider, url, api_key)]

    where = url or (llm.OPENAI if provider == "openai" else "")
    return Picker(f"Models of {provider}", load, current, free=True,
                  note=f"Asked from {where}." if where else "")


def ollama_items(found) -> List[Item]:
    items = []
    for hit in found:
        label = Text()
        label.append(hit.url, style=f"bold {theme.color('glass')}")
        label.append(f"  Ollama {hit.version}, {hit.models} model{'s' if hit.models != 1 else ''}",
                     style=theme.color("ash"))
        if hit.note:
            label.append(f"\n  {hit.note}", style=theme.color("warn" if not hit.reachable_from_docker else "ash"))
        items.append((hit.url, label, not hit.reachable_from_docker))
    return items


def ollama_picker(current: str, service: str, env: dict, find: Optional[Callable] = None,
                  title: str = "Ollama") -> Picker:
    """The Ollama servers found (or scanned), the value is the URL setting of the service."""
    from tpotctl import llm

    def load() -> List[Item]:
        found = (find or (lambda: llm.discover(env)))()
        items = ollama_items(found)
        if not items:
            raise llm.LLMError("no Ollama answers on port 11434 in the usual places, enter its URL")
        return [(llm.url_for(service, value), label, hidden) for value, label, hidden in items]

    return Picker(title, load, current, free=True,
                  show_all="Also the ones the honeypots cannot reach",
                  note="Asked on port 11434: the URLs of the settings, this host, its Docker bridges and "
                       "gateways. The honeypots run in Docker, localhost there is the container itself.")


# -- for a row of the settings ----------------------------------------------------

def picker_for(row, draft: dict, schema: dict) -> Picker:
    """The picker of a SettingRow (a list setting, interface, time zone, models)."""
    from tpotctl.widgets.fields import llm_settings
    rule = row.rule
    current = draft.get(rule.key, "")
    if rule.widget == "choice_many":
        return many_picker(rule, current)
    if rule.widget == "interface":
        return interface_picker(current)
    if rule.widget == "timezone":
        return timezone_picker(current)
    if rule.widget == "llm_url":
        return ollama_picker(current, rule.llm.get("service", ""), draft)
    llm = llm_settings(rule, draft, schema)
    return model_picker(current, llm["provider"], llm["url"], llm["api_key"])


def detect_for(rule) -> str:
    """What Detect finds for a row, "" for nothing; runs commands, call it from a worker."""
    from tpotctl import netinfo, tz
    return netinfo.detect() if rule.widget == "interface" else tz.detect()


def detected_note(rule, found: str) -> str:
    if rule.widget == "interface":
        return (f"{found} has the route to the internet. Empty picks it automatically, and follows when the "
                f"route changes.")
    return f"{found} is the time zone of this host."
