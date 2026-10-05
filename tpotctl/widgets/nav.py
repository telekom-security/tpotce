"""The arrow keys of tpot, the same on every page and in every dialog.

up / down go to the line before or after; the widgets under one horizontal container
(a row of buttons, a setting with its buttons) are one line, and in that line the one
nearest to where the focus was. left / right move within the line; left at the start of
a line on a page goes back to the menu. Lists, tables, text fields and scrollers use the
keys themselves and hand them on at their edge, a text field never goes to the menu.

The apps take BINDINGS and ArrowNav, the dialogs NavModal (a modal screen does not see
the bindings of the App); these bindings only fire when the focused widget does not use
the key itself, so Buttons, Checkboxes, Switches and the up / down of an Input come here
on their own.
"""

from typing import List, Optional

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Input, OptionList, RichLog

BINDINGS = [Binding(key, f"nav('{key}')", show=False) for key in ("up", "down", "left", "right")]


def _area(app, widget: Optional[Widget]) -> Widget:
    """Where the arrows move: the open page of the menu (the child of #panes), else the screen."""
    screen = app.screen
    if widget is not None:
        for node in widget.ancestors:
            if node.parent is not None and node.parent.id == "panes" and node.parent.parent is not None \
                    and node.screen is screen:
                return node
    return screen


def _line_of(widget: Widget, area: Widget) -> Widget:
    for node in widget.ancestors:
        if node is area:
            break
        layout = node.styles.layout
        if layout is not None and layout.name == "horizontal":
            return node
    return widget


def _lines(widgets: List[Widget], area: Widget) -> List[List[Widget]]:
    lines: List[List[Widget]] = []
    last = None
    for widget in widgets:
        line = _line_of(widget, area)
        if lines and line is last:
            lines[-1].append(widget)
        else:
            lines.append([widget])
            last = line
    return lines


def _x(widget: Widget) -> int:
    region = widget.region
    return region.x if region.width else widget.virtual_region.x


def navigate(app, direction: str, menu: bool = True) -> bool:
    """Move the focus one step in a direction; True if it moved (or went to the menu)."""
    focused = app.focused
    area = _area(app, focused)
    widgets = [w for w in app.screen.focus_chain if area is app.screen or area in w.ancestors]
    if not widgets:
        return False
    if focused not in widgets:
        widgets[0].focus()
        return True
    lines = _lines(widgets, area)
    number = next(i for i, line in enumerate(lines) if focused in line)
    line = lines[number]
    place = line.index(focused)
    target = None
    if direction in ("down", "up"):
        other = number + (1 if direction == "down" else -1)
        if 0 <= other < len(lines):
            target = min(lines[other], key=lambda w: abs(_x(w) - _x(focused)))
    elif direction == "right":
        target = line[place + 1] if place + 1 < len(line) else None
    elif direction == "left":
        target = line[place - 1] if place > 0 else None
        if target is None and menu and area is not app.screen and hasattr(app, "action_menu"):
            app.action_menu()
            return True
    if target is None:
        return False
    target.focus()
    return True


class ArrowNav:
    """For the App classes: the action of BINDINGS."""

    def action_nav(self, direction: str) -> None:
        navigate(self, direction)


class NavModal(ModalScreen):
    """The base of every dialog: a modal screen does not see the bindings of the App, so it brings
    them itself (there is no menu behind a dialog, left stays in it)."""

    BINDINGS = list(BINDINGS)

    def action_nav(self, direction: str) -> None:
        navigate(self.app, direction, menu=False)


class NavInput(Input):
    """left / right move the cursor; at the start or end of the text to the next widget of the line."""

    def action_cursor_left(self, select: bool = False) -> None:
        if not select and self.selection.is_empty and self.cursor_position == 0:
            navigate(self.app, "left", menu=False)
            return
        super().action_cursor_left(select)

    def action_cursor_right(self, select: bool = False) -> None:
        if not select and self.selection.is_empty and self.cursor_at_end and not self._suggestion:
            navigate(self.app, "right", menu=False)
            return
        super().action_cursor_right(select)


class NavDataTable(DataTable):
    """up / down move the cursor, at the first or last row on to the line before or after; with a row
    cursor left / right go along the line (left to the menu). enter_goes_on: a table whose buttons act
    on the row, enter takes the row and goes on to them (down would move the cursor instead)."""

    def __init__(self, *args, enter_goes_on: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.enter_goes_on = enter_goes_on

    def action_select_cursor(self) -> None:
        super().action_select_cursor()
        if self.enter_goes_on:
            navigate(self.app, "down")

    def action_cursor_up(self) -> None:
        if self.cursor_type in ("row", "cell") and self.cursor_row <= 0:
            navigate(self.app, "up")
            return
        super().action_cursor_up()

    def action_cursor_down(self) -> None:
        if self.cursor_type in ("row", "cell") and self.cursor_row >= self.row_count - 1:
            navigate(self.app, "down")
            return
        super().action_cursor_down()

    def action_cursor_left(self) -> None:
        if self.cursor_type == "row" or self.cursor_column <= 0:
            if self.scroll_x <= 0:
                navigate(self.app, "left")
                return
        super().action_cursor_left()

    def action_cursor_right(self) -> None:
        if self.cursor_type == "row" or self.cursor_column >= len(self.columns) - 1:
            if self.scroll_x >= self.max_scroll_x:
                navigate(self.app, "right")
                return
        super().action_cursor_right()


def _first_last(options) -> tuple:
    enabled = [i for i, option in enumerate(options) if not option.disabled]
    return (enabled[0], enabled[-1]) if enabled else (None, None)


class NavOptionList(OptionList):
    """up / down move the highlight, at the first or last option on to the line before or after.
    enter_goes_on: a list to choose from for the buttons below, enter takes the choice and goes on."""

    def __init__(self, *args, enter_goes_on: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.enter_goes_on = enter_goes_on

    def action_select(self) -> None:
        super().action_select()
        if self.enter_goes_on:
            navigate(self.app, "down")

    def action_cursor_up(self) -> None:
        first, _last = _first_last(self.options)
        if first is None or (self.highlighted is not None and self.highlighted <= first):
            navigate(self.app, "up")        # OptionList itself would wrap around to the last one
            return
        super().action_cursor_up()

    def action_cursor_down(self) -> None:
        _first, last = _first_last(self.options)
        if last is None or (self.highlighted is not None and self.highlighted >= last):
            navigate(self.app, "down")
            return
        super().action_cursor_down()


class _EdgeScroll:
    """For scrollers. With the focus on the scroller itself the arrows scroll it and at its edge go on
    to the next line or widget; with the focus on a widget in it they move the focus (it scrolls
    along) and only scroll where nothing comes in that direction, so the rest can still be read."""

    def _arrow(self, direction: str, at_edge: bool, scroll) -> None:
        if self.app.focused is self:
            if at_edge:
                navigate(self.app, direction)
            else:
                scroll()
        elif not navigate(self.app, direction):
            scroll()

    def action_scroll_up(self) -> None:
        self._arrow("up", self.scroll_y <= 0, super().action_scroll_up)

    def action_scroll_down(self) -> None:
        self._arrow("down", self.scroll_y >= self.max_scroll_y, super().action_scroll_down)

    def action_scroll_left(self) -> None:
        self._arrow("left", self.scroll_x <= 0, super().action_scroll_left)

    def action_scroll_right(self) -> None:
        self._arrow("right", self.scroll_x >= self.max_scroll_x, super().action_scroll_right)


def _shown(widget: Widget, root: Widget) -> bool:
    node = widget
    while node is not None and node is not root:
        if not node.display or not node.visible:
            return False
        node = node.parent
    return True


class NavScroll(_EdgeScroll, VerticalScroll):
    """A scroller of a page or dialog. With fields or buttons shown in it, it takes no focus itself (the
    focus scrolls it to them); with only text (the body of a dialog) it does, the arrows scroll it."""

    def allow_focus(self) -> bool:
        return super().allow_focus() and not any(
            w.can_focus and not w.disabled and _shown(w, self) for w in self.query("*"))


class NavRichLog(_EdgeScroll, RichLog):
    """A log (the output of a task): scrolls, at its top or end on to the next line."""
