"""Messages of tpot and the customizer in the look of installer/lib/ui.sh.

At a terminal the glyph and the colours of gum's form there, otherwise the same plain
text as ui.sh (`### [OK] - ...`). Standard library only: the launcher and the
headless customizer use it before (or without) the venv. TPOT_GUM=off keeps the plain
text. The colours have the depth of the one rule of the scripts and the T-Pot Manager
(prefs.detect_colors): true colour, the xterm 256 entries of theme.PALETTE_256 or the
ANSI colours of theme.PALETTE_16; a dumb terminal and NO_COLOR (when it is not empty,
as for Rich and ui.sh) get the glyphs without colours, as gum gives them there.
"""

import os
import sys

from tpotctl import prefs

# the colours of tpot (theme.py) and of ui.sh, tests keep the three in sync
COLOURS = {"magenta": "#E20074", "glass": "#ECEFF9", "ash": "#A2A2AD",
           "ok": "#3FA34D", "warn": "#F4B400", "error": "#E8453C"}
# the same as entries of the xterm 256 palette (theme.PALETTE_256) and as SGR codes of the
# 16 ANSI colours (theme.PALETTE_16)
COLOURS_256 = {"magenta": 162, "glass": 231, "ash": 248, "ok": 71, "warn": 214, "error": 167}
COLOURS_16 = {"magenta": 95, "glass": 97, "ash": 37, "ok": 92, "warn": 93, "error": 91}
_PLAIN = {"info": "### {}", "ok": "### [OK] - {}", "warn": "### [WARNING] - {}", "error": "### [ERROR] - {}"}
_GLYPH = {"info": ("⬢", "magenta"), "ok": ("✓", "ok"), "warn": ("!", "warn"), "error": ("✗", "error")}
_RESET = "\x1b[0m"


def depth(environ=None):
    """truecolor, 256 or 16 by the rule (prefs.detect_colors of the environment, default os.environ),
    None for a dumb terminal (TERM dumb or unknown) and with NO_COLOR (not empty): no colours there."""
    env = os.environ if environ is None else environ
    if prefs.ascii_lower(env.get("TERM") or "") in prefs.DUMB_TERMS or env.get("NO_COLOR"):
        return None
    return prefs.detect_colors(env)


def _sgr(name: str, colors, bold: bool = False) -> str:
    """The SGR of a colour by the depth; bold first, as gum writes it (1;...)."""
    start = "\x1b[1;" if bold else "\x1b["
    if colors == "256":
        return f"{start}38;5;{COLOURS_256[name]}m"
    if colors == "16":
        return f"{start}{COLOURS_16[name]}m"
    value = COLOURS[name].lstrip("#")
    return f"{start}38;2;{int(value[0:2], 16)};{int(value[2:4], 16)};{int(value[4:6], 16)}m"


def _paint(name: str, text: str, colors, bold: bool = False) -> str:
    return text if colors is None else f"{_sgr(name, colors, bold)}{text}{_RESET}"


def plain(kind: str, text: str) -> str:
    """The plain text of ui.sh; info of more lines has ### before each, as fuUI_INFO."""
    if kind == "info":
        return "\n".join(_PLAIN["info"].format(line) for line in text.split("\n"))
    return _PLAIN[kind].format(text)


def styled(kind: str, text: str, colors="rule") -> str:
    """The form of gum: info is a magenta hexagon and glass text, ok a green tick and plain text, warn
    and error the whole line in their colour, an error bold (fuUI_ERROR). colors: truecolor, 256, 16
    or None (no colours); by default the depth of the rule (depth())."""
    if colors == "rule":
        colors = depth()
    glyph, colour = _GLYPH[kind]
    if kind == "info":
        # more lines: the sign before the first, two spaces before the others (fuUI_INFO)
        first, *rest = text.split("\n")
        return "\n".join([f"{_paint(colour, glyph, colors)} {_paint('glass', first, colors)}"]
                         + [f"  {_paint('glass', line, colors)}" for line in rest])
    if kind == "ok":
        return f"{_paint(colour, glyph, colors)} {text}"
    return _paint(colour, f"{glyph} {text}", colors, bold=kind == "error")


def fancy(stream) -> bool:
    """The form of gum: a terminal, gum not off (NO_COLOR takes only the colours, depth())."""
    return getattr(stream, "isatty", lambda: False)() and os.environ.get("TPOT_GUM", "on") != "off"


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
        print(_paint("ash", f"    {line}", depth()) if fancy(out) else f"###   {line}", file=out, flush=True)
