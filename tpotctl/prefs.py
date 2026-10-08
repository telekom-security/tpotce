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


def load(environment: bool = True, environ=None) -> Prefs:
    """The choices of the file, and (environment) TPOT_ICONS / TPOT_COLORS over them; a choice
    to save starts from load(environment=False), so a one-off variable does not end up in the file.
    environ: the environment that counts, for the file and the variables (default os.environ)."""
    env = os.environ if environ is None else environ
    out = Prefs()
    try:
        with open(path(env), encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("icons") in ICONS:
            out.icons = data["icons"]
        if data.get("colors") in COLORS:
            out.colors = data["colors"]
    except (OSError, ValueError, AttributeError):
        pass
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


def terminal_colors(environ) -> str:
    """truecolor, 256 or 16 by what the terminal says of itself: COLORTERM truecolor / 24bit, a TERM of
    a true colour terminal, outside tmux and screen a terminal that says who it is (TERM_PROGRAM,
    LC_TERMINAL of iTerm2, which comes over SSH, VTE_VERSION, KONSOLE_VERSION, WT_SESSION); 256 for a
    TERM *256color or the Terminal of macOS, anything else 16. NO_COLOR is no depth: the callers
    leave the colours out themselves."""
    if (environ.get("COLORTERM") or "").lower() in ("truecolor", "24bit"):
        return "truecolor"
    term = (environ.get("TERM") or "").lower()
    if term.endswith("-direct") or term.startswith("foot") or term in TRUECOLOR_TERMS:
        return "truecolor"
    # in tmux or screen the terminal outside does not count, the multiplexer draws
    if not environ.get("TMUX") and not term.startswith(("screen", "tmux")):
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
