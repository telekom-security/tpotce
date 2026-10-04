"""What the user chose for the look of tpot: the icon set.

Kept in ${XDG_CONFIG_HOME:-~/.config}/tpotce/tpot.json, per user, outside of the
checkout. A file that cannot be read gives the defaults. TPOT_ICONS in the environment
wins over the file (i.e. for a terminal without the font).
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass

ICONS = ("unicode", "nerd", "ascii")


@dataclass
class Prefs:
    icons: str = "unicode"


def path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "tpotce", "tpot.json")


def load() -> Prefs:
    out = Prefs()
    try:
        with open(path(), encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("icons") in ICONS:
            out.icons = data["icons"]
    except (OSError, ValueError, AttributeError):
        pass
    if os.environ.get("TPOT_ICONS") in ICONS:
        out.icons = os.environ["TPOT_ICONS"]
    return out


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
