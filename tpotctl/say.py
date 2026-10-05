"""Messages of tpot and the customizer in the look of installer/lib/ui.sh.

At a terminal the glyph and the colours of gum's form there, otherwise the same plain
text as ui.sh (`### [OK] - ...`). Standard library only: the launcher and the
headless customizer use it before (or without) the venv. TPOT_GUM=off and NO_COLOR
keep the plain text.
"""

import os
import sys

# the colours of tpot (theme.py) and of ui.sh, tests keep the three in sync
COLOURS = {"magenta": "#E20074", "glass": "#ECEFF9", "ash": "#A2A2AD",
           "ok": "#3FA34D", "warn": "#F4B400", "error": "#E8453C"}
_PLAIN = {"info": "### {}", "ok": "### [OK] - {}", "warn": "### [WARNING] - {}", "error": "### [ERROR] - {}"}
_GLYPH = {"info": ("⬢", "magenta"), "ok": ("✓", "ok"), "warn": ("!", "warn"), "error": ("✗", "error")}
_RESET = "\x1b[0m"


def _rgb(name: str) -> str:
    value = COLOURS[name].lstrip("#")
    return f"\x1b[38;2;{int(value[0:2], 16)};{int(value[2:4], 16)};{int(value[4:6], 16)}m"


def plain(kind: str, text: str) -> str:
    return _PLAIN[kind].format(text)


def styled(kind: str, text: str) -> str:
    # as fuUI_*: info is a magenta hexagon and glass text, ok a green tick and plain
    # text, warn and error the whole line in their colour
    glyph, colour = _GLYPH[kind]
    if kind == "info":
        return f"{_rgb(colour)}{glyph}{_RESET} {_rgb('glass')}{text}{_RESET}"
    if kind == "ok":
        return f"{_rgb(colour)}{glyph}{_RESET} {text}"
    return f"{_rgb(colour)}{glyph} {text}{_RESET}"


def fancy(stream) -> bool:
    return (getattr(stream, "isatty", lambda: False)() and os.environ.get("TPOT_GUM", "on") != "off"
            and "NO_COLOR" not in os.environ)


def _say(kind: str, text: str, stream) -> None:
    print(styled(kind, text) if fancy(stream) else plain(kind, text), file=stream, flush=True)


def info(text: str, stream=None) -> None:
    _say("info", text, stream or sys.stdout)


def ok(text: str, stream=None) -> None:
    _say("ok", text, stream or sys.stdout)


def warn(text: str, stream=None) -> None:
    _say("warn", text, stream or sys.stdout)


def error(text: str, stream=None) -> None:
    _say("error", text, stream or sys.stderr)


def hint(*lines: str, stream=None) -> None:
    """Commands or details below a message, indented."""
    out = stream or sys.stdout
    for line in lines:
        print(f"{_rgb('ash')}    {line}{_RESET}" if fancy(out) else f"###   {line}", file=out, flush=True)
