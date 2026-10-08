"""The symbols of tpot in three sets: unicode (default), nerd (Nerd Font icons), ascii.

The font is the one of the terminal the user looks at, often on the other end of an
SSH connection, so the default is plain unicode every font has. Every unicode glyph
is one cell wide (tests check it), Nerd Font icons come from its Material Design set.
"""

from typing import Dict

from tpotctl import prefs

MODES = ("unicode", "nerd", "ascii")

_UNICODE = {
    "on": "⬢", "off": "⬡", "locked": "◆", "ok": "✓", "fail": "✗", "warn": "!",
    "bullet": "▸", "changed": "▌", "bar_on": "▰", "bar_off": "▱", "running": "●", "stopped": "○",
    "search": "/", "show": "◉", "hide": "◎",
    # the header: what this T-Pot is and how it is doing
    "branch": "⎇", "host": "⌂", "web": "↗", "hive_link": "⇢", "uptime": "◷", "containers": "⬢",
    "machine": "▣", "load": "≈", "last_attack": "◷",
    "pane_status": "⬢", "pane_edition": "⬢", "pane_settings": "⬢", "pane_users": "⬢",
    "pane_sensors": "⬢", "pane_images": "⬢", "pane_update": "⬢", "pane_llm": "⬢", "pane_checks": "⬢",
}

_NERD = dict(_UNICODE, **{
    "on": "\U000F02D8", "off": "\U000F02D9", "locked": "\U000F033E", "ok": "\U000F012C",
    "fail": "\U000F0156", "warn": "\U000F0026", "running": "\U000F0765",
    "branch": "\U000F062C", "host": "\U000F048B", "web": "\U000F059F", "hive_link": "\U000F06F6",
    "uptime": "\U000F0150", "containers": "\U000F0868", "machine": "\U000F01C5", "load": "\U000F029A",
    "last_attack": "\U000F05CE",
    "pane_status": "\U000F056E", "pane_edition": "\U000F06E1", "pane_settings": "\U000F0493",
    "pane_users": "\U000F0849", "pane_sensors": "\U000F0003", "pane_images": "\U000F0868",
    "pane_update": "\U000F006F", "pane_llm": "\U000F06A9", "pane_checks": "\U000F08A8",
})

_ASCII = {
    "on": "*", "off": "o", "locked": "#", "ok": "+", "fail": "x", "warn": "!",
    "bullet": ">", "changed": "|", "bar_on": "#", "bar_off": "-", "running": "*", "stopped": "o",
    "search": "/", "show": "+", "hide": "-",
    "branch": "", "host": "", "web": "web", "hive_link": "->", "uptime": "up", "containers": "",
    "machine": "host", "load": "load", "last_attack": "",
    "pane_status": "*", "pane_edition": "*", "pane_settings": "*", "pane_users": "*",
    "pane_sensors": "*", "pane_images": "*", "pane_update": "*", "pane_llm": "*", "pane_checks": "*",
}

SETS: Dict[str, Dict[str, str]] = {"unicode": _UNICODE, "nerd": _NERD, "ascii": _ASCII}
SPARK = {"unicode": "▁▂▃▄▅▆▇█", "nerd": "▁▂▃▄▅▆▇█", "ascii": "_.-~=*#@"}

_mode = None


def mode() -> str:
    global _mode
    if _mode is None:
        _mode = prefs.load().icons
    return _mode


def set_mode(name: str) -> None:
    global _mode
    if name not in MODES:
        raise ValueError(f"unknown icon set {name}, use one of {', '.join(MODES)}")
    _mode = name


def g(name: str) -> str:
    return SETS[mode()][name]


def spark() -> str:
    return SPARK[mode()]


def icon_pane(key: str) -> str:
    """The icon in front of a page in the menu: its own icon with a Nerd Font, else the hexagon."""
    return SETS[mode()].get(f"pane_{key}", g("on"))
