"""What the user chose for the look of tpot: the icon set and the colours.

Kept in ${XDG_CONFIG_HOME:-~/.config}/tpotce/tpot.json, per user, outside of the
checkout. A file that cannot be read gives the defaults. TPOT_ICONS and TPOT_COLORS in
the environment win over the file (i.e. for a terminal without the font, or one that
can show true colours but does not say so).

The colours for auto come from detect_colors, the one rule the T-Pot scripts follow too
(fuUI_COLORS of installer/lib/ui.sh; the cases are tpotctl/tests/color_cases.json).
"""

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass

ICONS = ("unicode", "nerd", "ascii")
# auto: what the terminal says (detect_colors)
COLORS = ("auto", "truecolor", "256", "16")
# the names Textual knows them by (TEXTUAL_COLOR_SYSTEM)
TEXTUAL_NAMES = {"16": "standard"}


@dataclass
class Prefs:
    icons: str = "unicode"
    colors: str = "auto"


def path(environ=None) -> str:
    """The tpot.json of the environment (default os.environ): its XDG_CONFIG_HOME, else its HOME."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_CONFIG_HOME")
    if not base:
        home = env.get("HOME") if os.name == "posix" else None     # as expanduser does there
        base = os.path.join(home or os.path.expanduser("~"), ".config")
    return os.path.join(base, "tpotce", "tpot.json")


# What tpot.json may hold: one JSON object of strings without escapes, as save() writes it. The
# T-Pot scripts read it with the same grammar (fuUI_PREF of installer/lib/ui.sh), so both take the
# same file or both leave it out (any other JSON, an escape, a nested object, a number, a byte order
# mark); a key twice: the last counts, as in JSON. No control characters in a string: the class
# cntrl of a UTF-8 locale (C0, DEL, C1, U+2028 and U+2029), stricter than JSON, as the scripts see
# them. The cases are in tests/color_cases.json.
_SPACE = "[ \t\n\r]*"
_STRING = '"([^"\\\\\x00-\x1f\x7f-\x9f\u2028\u2029]*)"'
_PAIR = _STRING + _SPACE + ":" + _SPACE + _STRING
_OBJECT = re.compile("\\A" + _SPACE + "\\{" + _SPACE + "(?:" + _PAIR + "(?:" + _SPACE + "," + _SPACE + _PAIR
                     + ")*)?" + _SPACE + "\\}" + _SPACE + "\\Z")
_PAIRS = re.compile(_PAIR)


def read_file(file: str) -> dict:
    """The keys and values of a tpot.json, {} for a file that is not one (or cannot be read)."""
    try:
        with open(file, encoding="utf-8", newline="") as handle:
            text = handle.read()
    except (OSError, ValueError):
        return {}
    if not _OBJECT.match(text):
        return {}
    return dict(_PAIRS.findall(text))


def load(environment: bool = True, environ=None) -> Prefs:
    """The choices of the file, and (environment) TPOT_ICONS / TPOT_COLORS over them; a choice
    to save starts from load(environment=False), so a one-off variable does not end up in the file.
    environ: the environment that counts, for the file and the variables (default os.environ)."""
    env = os.environ if environ is None else environ
    out = Prefs()
    data = read_file(path(env))
    if data.get("icons") in ICONS:
        out.icons = data["icons"]
    if data.get("colors") in COLORS:
        out.colors = data["colors"]
    if not environment:
        return out
    if env.get("TPOT_ICONS") in ICONS:
        out.icons = env["TPOT_ICONS"]
    if env.get("TPOT_COLORS") in COLORS:
        out.colors = env["TPOT_COLORS"]
    return out


# a TERM (lowercased) of a true colour terminal, besides *-direct and foot*
TRUECOLOR_TERMS = ("xterm-kitty", "xterm-ghostty", "alacritty", "wezterm", "contour", "rio")
# a TERM_PROGRAM (as written) of a true colour terminal; the Terminal of macOS has 256
TRUECOLOR_PROGRAMS = ("iTerm.app", "WezTerm", "vscode", "ghostty", "Hyper", "Tabby", "rio", "WarpTerminal")
VTE_TRUECOLOR = 3600          # VTE 0.36


# a TERM (lowercased) of a terminal without colours, as Rich sees it
DUMB_TERMS = ("dumb", "unknown")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def ascii_lower(text: str) -> str:
    """A-Z only, as the scripts lowercase (tr A-Z a-z in the C locale)."""
    return text.translate(_ASCII_LOWER)


def terminal_colors(environ) -> str:
    """truecolor, 256 or 16 by what the terminal says of itself: 16 for a dumb TERM (dumb, unknown),
    whatever else it says; outside GNU screen COLORTERM truecolor / 24bit and a TERM of a true colour
    terminal; outside tmux and screen a terminal that says who it is (TERM_PROGRAM, LC_TERMINAL of
    iTerm2, which comes over SSH, VTE_VERSION, KONSOLE_VERSION, WT_SESSION); 256 for a TERM *256color
    or the Terminal of macOS, anything else 16. GNU screen: STY, or a TERM screen* that is no tmux
    (TMUX, TERM_PROGRAM tmux); screen 4 has no true colour, what the terminal outside says (COLORTERM,
    TERM_PROGRAM, ... are inherited) does not count there. NO_COLOR is no depth: the callers leave
    the colours out themselves."""
    term = ascii_lower(environ.get("TERM") or "")
    if term in DUMB_TERMS:
        return "16"
    tmux = bool(environ.get("TMUX")) or environ.get("TERM_PROGRAM") == "tmux"
    screen = bool(environ.get("STY")) or (term.startswith("screen") and not tmux)
    if not screen:
        if ascii_lower(environ.get("COLORTERM") or "") in ("truecolor", "24bit"):
            return "truecolor"
        if term.endswith("-direct") or term.startswith("foot") or term in TRUECOLOR_TERMS:
            return "truecolor"
    # in tmux or screen the terminal outside does not count, the multiplexer draws
    if not screen and not environ.get("TMUX") and not term.startswith(("screen", "tmux")):
        program = environ.get("TERM_PROGRAM") or ""
        vte = environ.get("VTE_VERSION") or ""
        if (program in TRUECOLOR_PROGRAMS or environ.get("LC_TERMINAL") == "iTerm2"
                or environ.get("KONSOLE_VERSION") or environ.get("WT_SESSION")
                or (re.fullmatch(r"[0-9]{1,9}", vte) and int(vte) >= VTE_TRUECOLOR)):
            return "truecolor"
        if program == "Apple_Terminal":
            return "256"
    return "256" if term.endswith("256color") else "16"


def detect_colors(environ=None) -> str:
    """truecolor, 256 or 16: TPOT_COLORS, else "colors" of tpot.json, unless one of them says auto
    (TPOT_COLORS auto: tpot.json does not count), then the terminal (terminal_colors). Only the
    environment given counts (default os.environ), its tpot.json too."""
    env = os.environ if environ is None else environ
    chosen = load(environ=env).colors
    return chosen if chosen != "auto" else terminal_colors(env)


# TEXTUAL_COLOR_SYSTEM as tpot set it: a restart of the T-Pot Manager (exec) keeps the environment,
# and only a value of the user's own may win over a new choice
SET_MARK = "TPOT_COLORS_SET"
# a console of Windows has no TERM and says what it can itself (Rich asks it): there the rule
# only counts where it knows true colour (Windows Terminal, a choice of the user's)
CONSOLE_KNOWS = os.name == "nt"


def apply_color_system(environ) -> None:
    """The colours by detect_colors for Textual, which reads TEXTUAL_COLOR_SYSTEM when it is imported,
    so palette (theme.color_system) and output follow the rule of the scripts; a TEXTUAL_COLOR_SYSTEM
    of the user's own stays (auto is none, that is what Textual does without it)."""
    own = environ.get("TEXTUAL_COLOR_SYSTEM")
    if own and own != "auto" and own != environ.get(SET_MARK):
        return
    environ.pop("TEXTUAL_COLOR_SYSTEM", None)
    environ.pop(SET_MARK, None)
    chosen = load(environ=environ).colors
    colors = chosen if chosen != "auto" else terminal_colors(environ)
    if CONSOLE_KNOWS and chosen == "auto" and colors != "truecolor":
        return
    environ["TEXTUAL_COLOR_SYSTEM"] = environ[SET_MARK] = TEXTUAL_NAMES.get(colors, colors)


# the names of Rich (rich.console.Console(color_system=...))
RICH_NAMES = {"truecolor": "truecolor", "256": "256", "16": "standard"}


def console_color_system(stream=None, environ=None):
    """The color_system for a Rich Console that writes to stream (default sys.stdout): at a terminal
    the depth of the rule (detect_colors), so the tables of tpot status / ps / images have the colours
    of the Manager and the scripts (iTerm2 over SSH: truecolor); None at a dumb terminal (no colours,
    as Rich does there); "auto" when stream is no terminal (Rich then leaves the colours out, or takes
    FORCE_COLOR) and for a console of Windows that the rule knows nothing of (CONSOLE_KNOWS). NO_COLOR
    is Rich's own: it strips the colours of any depth."""
    import sys
    env = os.environ if environ is None else environ
    out = sys.stdout if stream is None else stream
    try:
        terminal = bool(out.isatty())
    except (AttributeError, OSError, ValueError):
        terminal = False
    if not terminal:
        return "auto"
    if ascii_lower(env.get("TERM") or "") in DUMB_TERMS:
        return None
    chosen = load(environ=env).colors
    colors = chosen if chosen != "auto" else terminal_colors(env)
    if CONSOLE_KNOWS and chosen == "auto" and colors != "truecolor":
        return "auto"
    return RICH_NAMES[colors]


def save(prefs: Prefs) -> bool:
    """Atomically; False if it cannot be written (the choice then lasts for this run)."""
    target = path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix=".tpot-", dir=os.path.dirname(target))
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(asdict(prefs), out, indent=2)
            out.write("\n")
        os.replace(temp, target)
        return True
    except OSError:
        return False
