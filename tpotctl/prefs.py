"""What the user chose for the look of tpot: the icon set and the colours.

Kept in ${XDG_CONFIG_HOME:-~/.config}/tpotce/tpot.json, per user, outside of the
checkout. A file that cannot be read gives the defaults. TPOT_ICONS and TPOT_COLORS in
the environment win over the file (i.e. for a terminal without the font, or one that
can show true colours but does not say so over SSH).
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass

ICONS = ("unicode", "nerd", "ascii")
# auto: what the terminal says (COLORTERM, TERM); over SSH COLORTERM usually stays behind
COLORS = ("auto", "truecolor", "256", "16")
# the names Textual knows them by (TEXTUAL_COLOR_SYSTEM)
TEXTUAL_NAMES = {"16": "standard"}


@dataclass
class Prefs:
    icons: str = "unicode"
    colors: str = "auto"


def path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "tpotce", "tpot.json")


def load(environment: bool = True) -> Prefs:
    """The choices of the file, and (environment) TPOT_ICONS / TPOT_COLORS over them; a choice
    to save starts from load(environment=False), so a one-off variable does not end up in the file."""
    out = Prefs()
    try:
        with open(path(), encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("icons") in ICONS:
            out.icons = data["icons"]
        if data.get("colors") in COLORS:
            out.colors = data["colors"]
    except (OSError, ValueError, AttributeError):
        pass
    if not environment:
        return out
    if os.environ.get("TPOT_ICONS") in ICONS:
        out.icons = os.environ["TPOT_ICONS"]
    if os.environ.get("TPOT_COLORS") in COLORS:
        out.colors = os.environ["TPOT_COLORS"]
    return out


# TEXTUAL_COLOR_SYSTEM as tpot set it: a restart of the T-Pot Manager (exec) keeps the environment,
# and only a value of the user's own may win over a new choice
SET_MARK = "TPOT_COLORS_SET"


def apply_color_system(environ) -> None:
    """The colours chosen (not auto) for Textual, which reads TEXTUAL_COLOR_SYSTEM when it is
    imported; a TEXTUAL_COLOR_SYSTEM of the user's own stays."""
    own = environ.get("TEXTUAL_COLOR_SYSTEM")
    if own and own != environ.get(SET_MARK):
        return
    environ.pop("TEXTUAL_COLOR_SYSTEM", None)
    environ.pop(SET_MARK, None)
    colors = load().colors
    if colors != "auto":
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
