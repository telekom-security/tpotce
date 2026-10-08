"""The T-Pot ANSI logo as pixels: the honey pot, the honeycombs and the T-Pot lettering.

The one source of the logo (logo.py has the wordmark): the scripts carry generated copies only
(python3 -m tpotctl.ui_logo). Taken from the elite BBS ANSI template of the logo (ansi/, kept out
of git): t-pot-animate.py (80 x 62 and 120 x 94 pixels) and 80x24/t-pot-animate-80x24.py (80 x 48
pixels, set tighter for a terminal of 80 x 24). Two pixels make one character (half blocks), every
pixel is an index into PALETTE. The grids are zlib + base64 as in the template.

PALETTE and GRIDS are imported from the template, never edited by hand:

  python3 -m tpotctl.splash_art --import <template> [--import-80x24 <file>] [--check]

<template> is the folder of t-pot-animate.py or the file itself; the 80x24 template is
80x24/t-pot-animate-80x24.py next to it unless --import-80x24 names it. The import parses the DATA
of the templates (ast.literal_eval), it never runs them; it checks sizes, palette and pixels and
writes PALETTE and GRIDS between the marks `# >>> splash art data >>>` and `# <<< splash art data
<<<` of this file; --check only compares. DESIGN (where the parts and effects of the logo are) and
COLOURS (its colours in the T-Pot Manager and the scripts) are this file's own, check them after an
import. Standard library only.
"""

import argparse
import ast
import base64
import binascii
import io
import os
import re
import sys
import tokenize
import zlib
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

# the colours of the template: 0 black, 1-6 magenta from dark to light (4 is Telekom magenta),
# 7 the white of the highlights, 8 and 9 the greys of the outlines
# >>> splash art data >>>
# imported from the ANSI template by python3 -m tpotctl.splash_art --import, do not edit
PALETTE = (
    (0, 0, 0), (55, 0, 28), (104, 0, 53), (162, 0, 83), (226, 0, 116),
    (255, 76, 167), (255, 157, 208), (245, 240, 243), (91, 88, 90), (172, 168, 171),
)

GRIDS = {
    "120": (120, 94, (
        "eNrtmu3W4ygIgAVNqvd/wyuoCEqS9p2+c+bHunt2ZtvURxD5MiH8P/7pARjHgF+Z358VIkIf+CtgdCeFpD7+DTDJ5WHRrC19"
        "H+vuH2GHmunb9Jf0XLUMA1z/9htc165iAi0vxr91imISc672jL/Owwqp/0ZSw1AzsBngd+BwYWP92AZlVx2c4he0nfOyrzGl"
        "NA0bm9wysK0r/anGIZ8lg3GK2KSsf+UR90FPIPyZ/UIpYKxISXqBTayRKvNPvT1vntZz+wyYhquKLTqGH+0y1hUD/xK0c6KN"
        "g74uZMEux48sG1M7kNb7JjqitKB+XFnaC2yF1oV/7ItDP45mk9g3Yd9AHGNnHkeJbHwfc8XlGodbuTkPk5JYv0p8Nv3mEj6W"
        "F8UXWG6EzODVVabjFFn74a7n/mNvPeW13JAiONEB0hGxAhk7DCJ34/8CF2I/QLvE3Y8RFrAfw/QxmPZl31+eBuh0kU3B/iOy"
        "tvpY5rOPFCg+A3N+NmKMpE98pprLhGbV2lH0bcYWM8aqPgL3jKZFlzSOMKRqUPklTnOmNtO8DkOpnu4DMPkKchicv/AfiVcf"
        "D8bmEGxCZbi4hrIAb3pLaKKyfqEHM9pM+v/8euUl3oKJGQcssYzB8JZ/HEc3qPSxgsliXl3L4IPr/oLaA3Yd+U2B05w1CJb2"
        "+YBdyxu3yyufQwPjcwbjcgMfjipuDivX7u+p5W0CF1rzGtd335OUzSgur7lxr6R1uS1pWLieO7ngImnwVcK1MTP3lYMj8Cbv"
        "B1ya0KrZy6RP9LhhwQA6elZFR5x/J5ecX/kWW0/4ASsX6FdrOISbc9S00SM7c0u2h8QBozWeeZTe9ht3j8I7jkBWl/Ob4Z/I"
        "lP0Wb9igD6/1ezBkxR1P3oMJm2EfwYDz/swrr3ohLoA8WeC25qoPhnKs46wTg8aG5QEx+mk/HNVkNtQTuFVRNc89UTz1eku1"
        "guWZMxiJB5+wPQHDXO655XST8ay58bUtLGSPW5fTH6iavOf6+bjlOhqJLlewNfPfuLqa9LnV0g03XXAp25XB3iMpLlm9rVuH"
        "n0BwubFx4Ylb93OOurf1H/mepq7y9xoWltqSvuwFkjzfDxJIHBMuf6O49dAozVXwIgMlQJZrcjL+c2zhiHB5+iLh8jf9wchn"
        "dYKqCMXh7lldX0c394Wb9bHPLpc+18cr7ns25LVtBMqLKQyw21PcvPhBSm1dLp6HLUhhqZQrF1WSvXv2uq7JZVeXy2sMMNxh"
        "OZHC4Xqmg6pVqf3gnDUb6MrKVaMEZUtliBvHZsZZkke9XzT31iMaHbFRewm3Bsdq/uccR5hcc9Q6l0NfEa7aBp4Wr9o9VNfh"
        "Im82m+Rz0eeC5pb8UBGGl0xNT8dHbnziJkox830lSqmcbCG8wW1u75H7VAErbly5dR+VXRlv+6Dn+8jvyqsDB2qutHZmIPO5"
        "VCuRni+7l5MbJ3d4s0aYXNSpT5AfhHwMW5vyIoWFCo7wxG2FPXCNpQYtQxySwsr5LSWmldtbl/W3qK9hgrRMDJfrfw5uBqu5"
        "I+jR/sbN2oI1wVaQKzVxCjvOr+VyppdNQtviQutYlpFw1v2NK5a5rLYmcPdXpmODd1w7KA5y5xVV8pKpb2WpkdsFqlXLXNOh"
        "Gnomc8KWug1jdbnS5tZcwKUJXjUDpvezyjta3XQvwgVLERMGjzsWpZM1MoJp9pF7I3RkUZ0FzuW3qyaMrV1XwYVuqxA4RpeR"
        "asx8w+O28yatYmAsgVXVsem5YaUkNEl2sV2GILbEU9ssfdHL8lnNWpwGE6KqcSeYHzbVvprLBnLrCZueTH0zun8XXCr559Fp"
        "tanuMuSWdea1NwxeCak+4VsEJ+ybls4EtTtW3VLJS3Xq9YjdblW7HYE77pyxqwauGiv2E+jGIm3iYHrL+wyWK1cY7AGvm4RC"
        "A2V5zQtTx7OmA+qX0r3BYO/Kx60qdTu4W9kjK960zbwGYkz8KXlfhxsAnYPEK+BFBBzXfiXcSbxhR9Bpetbc0PwAbtc4478k"
        "sgTLeMANeMOaS6BHPfe4JK8mxBkpe6fN7xTun0YdY5P6snfJAqDXx5opre1TugLD3re0jVjk9nnTPrZw692ZwnV/2gWDY1OL"
        "NHItAu1ofHR7KNcNlrIvZHeErIDxCgDGp5tp0HePMMWQVycCzP+beqeUdRYRctVjPNHzTXDpRR5dziZ8Ry1RyR5TGre3H9x6"
        "Q5f64DoE9KLvsOPqsv0e4/sX7+wp51sEDKb0AfVtlfRFzG03yvUW31TihfX63YZ5QVInzrNiCU2Wvm+wXgHPLex2cbL/eOdN"
        "A/XCTb8p6W0k7aDWi/ZqAspIRf6cEUaODNdd8+C81hR1TqocFE5L556nZ5Ht9ZLl9YNH+6gT9xBxSOGH6+1cGheDfUNVR0WQ"
        "P3rvClt6ObJ66yRA1o96UZjSH797hq3WIBMaK5A3RMY5s8lGE/wbL70tJYB2Zkg9q6FWe5/y1de/cL/mj2faitKvv3eGuFnX"
        "ODYpxb/w7leTXo2vK/fJtQn48xn+A/APO24="
    )),
    "80": (80, 62, (
        "eNrdWFuCmzAM9EhOV9z/wtXLtkwgSdN+lX40GBiPHh5J29r/cBExMTN9/f1+O4DoS0DameQd9AH+BT82LPvX8CUeTnSR17f8"
        "FjDZNeCgHqSvmUFD6oQQqI481j685JDCLMD0F5eL8sEHSaZfS74KRmLRDsfcmfp7QOjGjGEvUXev6Vp/vui9zUpOP595x4rf"
        "zThnuGM+ugXmXRybB5AHHkTcheEyLmBKTZ99htd4Wg+pz3sPSDdAncx4i9cqHp/ywvYzPOf1gTSc+Onr8PzjmXkK0zWsthEc"
        "83VwHaHly3ojh7PDOhqKx+MEqzP4BaCxtwAbqALq3sePuAigKAD15VKNyK0Lkcc9dtQzr7H4OU7kFp7lZcOxcuGJXdvFQ43Z"
        "4XZ+sSLHpq54gaf/G95Eu8Bzgo2rot3j6WsHJpFyhB7hv1hX4eAt3RYe8mwgNIUjuDjj4UFtM1hwVy42Pw0Ln8R93gfBWzzX"
        "Fqj0xXXYjwMrNcpVkPUM5zcXsPpINpnDMeHWOiqg+FZ2hI4rnkK7KOWuCvdYq9g+VWa/YvUKbxc5nniPurzZlnB9k6JRaG7x"
        "+glP/euX6mM+g/lwqIZpgbuGphAHfqaFhOwRJZ7IIKAqmHszuSM3EXM+sWHcR+IEHrmkBpXhZw1DsUZqtrjuaujTADVEpoux"
        "47XlFlouYud3CjOQzrLADTfB8XhQB9dop8Fm76lDiRJ9DDysjMMWpWCrBzNYT/8I6KmlIiy8SeESL9kmHjeIXLZoB73EMw9d"
        "4PV2dUAWnhlpIbKCPvCydWnX/CwXX+NleiKpcsqqjOgcFU+zT0q9Q8FLUtMfkvHFrELBNms7rXyZJT5/IPEiqCZEeqTUMYmX"
        "ciW8cs7xvOFyvCHXgeetUOLVbNKUrEtWd92G7JVGTyjmomKvSrRV3WxvNzz2Rm7KQ1sdQ7ZMeZhLiwT2Zo8ldd8/9qrS8vjP"
        "LURm3XMHzC9quZuVcbAA5mJGRoqIxrpIvWm15QKNihmndjS7UWhjaRaorZbkT+9ET3h6T9kDRfd0Lph78YzeiHLKYXfPmV/U"
        "TVPZUZNPJXh6xl/QzMq2FtETUO2eI1bN4RrLc/9SV8Bt8LvCi3xBRLqOC1udLxvMiXPhta1JH609UP1DCxFlhltzQGRa6ufL"
        "IYK5bQwr2Vp4KHrQu8kOeUA528satfFbo0954Kpld82MVwV9iW86WgtrVB+8nuSs1Y0BI0Qz1SE5rYEwZk9TmNsJe5X3Fm/m"
        "jY9IazzK6I8JMfLw1diAgTfHmTkS+sIMMvmWdTy5psk+L/RIgzUp9JyL4uPQ66fDczdpUuyM/NpQymQcc/ufDf1hEzPGkKTN"
        "cxbMv/xjxB6xv/gT0cmr7C54/+5vIT0b9g=="
    )),
    "80x24": (80, 48, (
        "eNrFVwlSIzEMtKTJRv7/h1e35cEhwFK1BioTj926W2KM/7HA14/v4+0rOh7+EBEJd7h6gt/QjyDtRfgVz4W59vnd++1RzDbP"
        "yR/Gsr1vYPLkchqqMgqH1JdJ+YrPTDBD6gbD1aL7ui6gtyqab7DOkTxTGokCsS21+x2ce54Kj3lAagcNkMQK5i/ijcQDQt5S"
        "hlxJjwXjO4NveEjhvMwUDY0AojrCdj8HBJFvCUZ2TsqDJ7icxBQhl8Uo0orwEzTUi/qrn1qu8wnFApnDeHFlqHj3pYZpk0u0"
        "oMwnf4ArPN2B+RqQxs4eouFzjs/xBHAFT/0Nr/Hk2Ez1NjsehQd2BHs47+wmXrSQComod+zi3c1QHKGVmbV04jvVjre1Srqt"
        "DXDklWPxMm+VCrPg1j7UXcNTUUoQ84TIvNdo6ML8eLTNzUSBs3clO2jFcpb3oqfCu0uBsj3gBI+TIbMEBA+dixRLmWXDq1fc"
        "vAxJE3KevZAWp3tMXRnb04zRH9tSua6KuDNJi6FZwy25PE1MpuP1MEPg+WXAPwsEsOFtbSB0nYlXVkEoEeZe4BQY5BX2qHUf"
        "CFFoABZeCoZmlKkU2nLDo3HKQcWfGO/PeEpmR7z5ooUHnoBEDmPiBQkyn/BA+R9vLTfw6LIygEpZ9xhYGijrLW8Kv7VYL3oK"
        "3jO8KKsVDxA8MhU8hVevI8WLBqr5UvSFjio6TPQe2arKKtRE2Nc5WjXLZvZ4xcMbZSmOkxsb2Q9P8yifEMGwlqVnPnZ6rYGH"
        "/Tg7mD8HzoG++pfR8xlqXoyX6tLk+sVxXlHQ2TylWiuD2zirsNFuaG2eJjhrMtYS4xjt7cHTywjHRDUFO+LaMe7XAXPDw96C"
        "k7xWr4ANsX+rVlK9Fna8bZD0Lodjs7nDIfXMMJNs6DwOMpFgcGH1HshPyOkmSB28HuDlrO5qk41ldB4AdD9HpZeTakyiHnej"
        "YA+N5r4rXUWmepjIx0W3Tn6Y2sRxD7sGZRytSbIahAmy0RqrEZ1naPTDhK2wdPaFaoUo6kMl4vv/GNbYHFlJKh5L5T5nf/Pf"
        "rjY0ejslHP++lsbv1foLr7AW5Q=="
    )),
}
# <<< splash art data <<<

# the variants and their size in pixels (the terminal sizes of splash_anim.VARIANTS and of
# fuUI_LOGO_ON in installer/lib/ui.sh depend on them, so an import keeps them)
SIZES = {"120": (120, 94), "80": (80, 62), "80x24": (80, 48)}

# The colours of the logo per colour system, an entry per colour of PALETTE: splash_anim.colours()
# for the T-Pot Manager, and python3 -m tpotctl.ui_logo writes them as the tables myUI_LOGO_RGB /
# myUI_LOGO_256 / myUI_LOGO_16 of the scripts. A token of theme.py (its palette of that colour
# system) or #rrggbb; with 256 colours an exact entry of the xterm palette (16-255), with 16 one of
# the VGA colours Rich downgrades to (the scripts take its SGR code).
COLOURS = {
    "truecolor": ("INK", "COMB_LIT", "WAX", "#A20053", "MAGENTA", "#FF4CA7", "#FF9DD0", "GLASS",
                  "#5B585A", "ASH"),
    "256": ("#000000", "#5f005f", "#87005f", "#af005f", "#d70087", "#ff5faf", "#ffafd7", "#ffffff",
            "#585858", "#a8a8a8"),
    # the template's classic mapping (black, magenta, bright magenta, white, greys) on the VGA set
    "16": ("#000000", "#aa00aa", "#aa00aa", "#aa00aa", "#ff55ff", "#ff55ff", "#ffffff", "#ffffff",
           "#555555", "#aaaaaa"),
}

# The geometry of the design, in the coordinates of the template (its coords(): the design is 1236 x
# 960 from x 8, y 100). splash_anim takes the parts of the logo (when they come together) and the
# places of its effects from it; check it against the picture after an import of a new template
# (tests/test_logo_source.py checks that it lies in the frame and every part keeps its pixels).
DESIGN = {
    "frame": (8, 100, 1236, 960),                   # x, y, width, height of the design
    "word_x": (147, 1131), "word_y": (495, 798),    # the lettering T-Pot, the light sweeps over it
    "pot_centre": (623, 524), "pot_radii": (330, 400),  # the pot, an ellipse: its rim, the honey in it
    "pool_top": 900,                                # below: the honey pool
    "waves_y": (924, 1035),                         # the light waves on the pool
    "pool_y": 964,                                  # where the drops land
    "drops": ((565, 824), (695, 832), (790, 824)),  # the three drops under the lettering
    "stars": ((264, 251), (357, 172), (1104, 438), (59, 735), (950, 835)),
    "syrup_x": 686, "syrup_y": (340, 465, 10),      # the long drip above the lettering: x, y from, to, step
    "magentas": (3, 4, 5, 6),                       # the entries of PALETTE of honey and lettering
}
# the names the splash had before DESIGN
DROPS, STARS = DESIGN["drops"], DESIGN["stars"]
SYRUP_X, SYRUP_Y = DESIGN["syrup_x"], range(*DESIGN["syrup_y"])
POOL_Y = DESIGN["pool_y"]


@lru_cache(maxsize=None)
def grid(variant: str) -> Tuple[int, int, List[int]]:
    """Width and height in pixels and the pixels, row by row."""
    width, height, chunks = GRIDS[variant]
    return width, height, list(zlib.decompress(base64.b64decode("".join(chunks))))


def design_xy(width: int, height: int, x: float, y: float) -> Tuple[int, int]:
    """A point of the design as the pixel of a grid (the template's coords())."""
    left, top, w, h = DESIGN["frame"]
    return round((x - left) / w * width), round((y - top) / h * height)


def design_of(width: int, height: int, x: int, y: int) -> Tuple[float, float]:
    """The point of the design a pixel shows (its centre)."""
    left, top, w, h = DESIGN["frame"]
    return left + (x + .5) / width * w, top + (y + .5) / height * h


# -- the import of the template ---------------------------------------------------------------

BEGIN = "# >>> splash art data >>>"
END = "# <<< splash art data <<<"
TEMPLATE = "t-pot-animate.py"
TEMPLATE_80X24 = os.path.join("80x24", "t-pot-animate-80x24.py")
MAX_TEMPLATE = 1 << 20              # bytes of a template (the real one has 11 kB)
CHUNK = 96                          # characters of the grid data per line
# Python >= 3.11 parses no int literal of more digits (sys.int_info.default_max_str_digits), 3.9 takes
# seconds for a longer one: refused before parsing
_LONG_NUMBER = re.compile(rb"[0-9][0-9_]{4300}")
# the tokens of a template (the real ones have about 2 200): a chain of more (-----1, 1-1-1, a.a.a,
# lambda:lambda:) nests too deep for the parser of Python 3.9, which crashes (SIGSEGV) from about
# 150 000 on, counted before parsing
MAX_TOKENS = 50_000
# the grids of a template: the variant of splash_art and its key in DATA["grids"]
FROM_TEMPLATE = (("120", "120"), ("80", "80"))
FROM_TEMPLATE_80X24 = (("80x24", "80"),)

Palette = Tuple[Tuple[int, int, int], ...]
Grids = Dict[str, Tuple[int, int, str]]


def _read_data(path: str) -> dict:
    """The DATA of a template, parsed and never run: a single assignment DATA = <literal> at the top
    level of the file. ValueError (naming the file) for anything else."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_TEMPLATE + 1)
    except OSError as error:
        raise ValueError(f"{path}: cannot be read ({error.strerror or error})") from None
    if len(raw) > MAX_TEMPLATE:
        raise ValueError(f"{path}: more than {MAX_TEMPLATE} bytes, not a template of the logo")
    if _LONG_NUMBER.search(raw):
        raise ValueError(f"{path}: a number of more than 4300 digits, not a template of the logo")
    try:
        text = raw.decode("utf-8")
        for count, _token in enumerate(tokenize.generate_tokens(io.StringIO(text).readline)):
            if count >= MAX_TOKENS:
                raise ValueError(f"{path}: more than {MAX_TOKENS} tokens (the template has about 2 200), "
                                 "not a template of the logo")
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError) as error:
        raise ValueError(f"{path}: not Python source of a template ({error.__class__.__name__})") from None
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError, UnicodeDecodeError, RecursionError, MemoryError) as error:
        raise ValueError(f"{path}: not Python source of a template ({error.__class__.__name__})") from None
    found = [node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1
             and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "DATA"]
    if len(found) != 1:
        raise ValueError(f"{path}: {'no' if not found else 'more than one'} assignment DATA = {{...}} "
                         "at the top level")
    try:
        data = ast.literal_eval(found[0].value)
    except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
        raise ValueError(f"{path}: DATA is code, not data (only literals are read, nothing is run)") from None
    if not isinstance(data, dict) or not isinstance(data.get("grids"), dict) or "palette" not in data:
        raise ValueError(f"{path}: DATA is not {{'palette': [...], 'grids': {{...}}}}")
    return data


def _brief(value, depth: int = 0) -> str:
    """A wrong value of a template for a message: short, and never the repr of a huge number (that is
    quadratic on Python 3.9) or string."""
    if value is None or isinstance(value, (bool, float)):
        return repr(value)
    if isinstance(value, int):
        return repr(value) if value.bit_length() <= 64 else f"a number of {value.bit_length()} bits"
    if isinstance(value, str):
        return repr(value) if len(value) <= 40 else f"{value[:30]!r}... ({len(value)} characters)"
    if isinstance(value, (list, tuple)) and depth < 2:
        items = [_brief(item, depth + 1) for item in value[:4]] + (["..."] if len(value) > 4 else [])
        return ("[{}]" if isinstance(value, list) else "({})").format(", ".join(items))
    return f"a {type(value).__name__}" + (f" of {len(value)}" if isinstance(value, (list, tuple, dict)) else "")


def _palette(data: dict, path: str) -> Palette:
    palette = data["palette"]
    if not isinstance(palette, (list, tuple)) or len(palette) != len(PALETTE):
        raise ValueError(f"{path}: the palette needs {len(PALETTE)} colours (COLOURS, the tables of the "
                         f"scripts and the pairs of ui_logo count on them), not "
                         f"{len(palette) if isinstance(palette, (list, tuple)) else _brief(palette)}")
    out = []
    for colour in palette:
        if (not isinstance(colour, (list, tuple)) or len(colour) != 3
                or not all(type(c) is int and 0 <= c <= 255 for c in colour)):
            raise ValueError(f"{path}: {_brief(colour)} of the palette is not (r, g, b) of 0-255")
        out.append(tuple(colour))
    return tuple(out)


def _grid(data: dict, key: str, variant: str, path: str) -> Tuple[int, int, str]:
    entry = data["grids"].get(key)
    if not isinstance(entry, dict):
        raise ValueError(f"{path}: no grid {key!r} (the variant {variant})")
    width, height, text = entry.get("width"), entry.get("height"), entry.get("data")
    want = SIZES[variant]
    if (type(width) is not int or type(height) is not int) or (width, height) != want:
        raise ValueError(f"{path}: the grid {key!r} is {_brief(width)} x {_brief(height)} pixels, the variant "
                         f"{variant} is {want[0]} x {want[1]} (another size also needs the terminal sizes of "
                         "splash_anim.VARIANTS and fuUI_LOGO_ON changed)")
    if not isinstance(text, str):
        raise ValueError(f"{path}: the data of the grid {key!r} is not a string")
    try:
        packed = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise ValueError(f"{path}: the data of the grid {key!r} is not base64") from None
    size = width * height
    inflate = zlib.decompressobj()
    try:
        pixels = inflate.decompress(packed, size + 1)       # no more than one pixel too many
    except zlib.error:
        raise ValueError(f"{path}: the data of the grid {key!r} is not zlib") from None
    if len(pixels) != size or not inflate.eof or inflate.unused_data:
        raise ValueError(f"{path}: the grid {key!r} has {'more' if len(pixels) > size else 'other'} data "
                         f"than its {width} x {height} = {size} pixels")
    wrong = sorted({p for p in pixels if p >= len(PALETTE)})
    if wrong:
        raise ValueError(f"{path}: the grid {key!r} has colours {wrong} the palette has not")
    return width, height, text


def import_template(template: str, file_80x24: Optional[str] = None) -> Tuple[Palette, Grids]:
    """PALETTE and the grids (variant: width, height, data) of a template: the folder of
    t-pot-animate.py or the file, and its 80x24 template (default: 80x24/t-pot-animate-80x24.py next
    to it). ValueError for a template that is not one."""
    path = os.path.join(template, TEMPLATE) if os.path.isdir(template) else template
    if file_80x24 is None:
        file_80x24 = os.path.join(os.path.dirname(path), TEMPLATE_80X24)
        if not os.path.isfile(file_80x24):
            raise ValueError(f"no 80x24 template: {file_80x24} is missing, name it with --import-80x24 <file>")
    data, tight = _read_data(path), _read_data(file_80x24)
    palette = _palette(data, path)
    if _palette(tight, file_80x24) != palette:
        raise ValueError(f"{file_80x24}: its palette is not the one of {path} (the logo has one palette)")
    grids: Grids = {}
    for source, keys in ((data, FROM_TEMPLATE), (tight, FROM_TEMPLATE_80X24)):
        for variant, key in keys:
            grids[variant] = _grid(source, key, variant, path if source is data else file_80x24)
    return palette, grids


def render(palette: Sequence[Sequence[int]], grids: Grids) -> str:
    """PALETTE and GRIDS as the text between the marks, marks included."""
    out = [BEGIN, "# imported from the ANSI template by python3 -m tpotctl.splash_art --import, do not edit",
           "PALETTE = ("]
    colours = [f"({r}, {g}, {b})" for r, g, b in palette]
    out += ["    " + ", ".join(colours[k:k + 5]) + "," for k in range(0, len(colours), 5)]
    out += [")", "", "GRIDS = {"]
    for variant in SIZES:
        width, height, text = grids[variant]
        out.append(f'    "{variant}": ({width}, {height}, (')
        out += [f'        "{text[k:k + CHUNK]}"' for k in range(0, len(text), CHUNK)]
        out.append("    )),")
    out += ["}", END]
    return "\n".join(out)


def data_block(text: str) -> str:
    """The data between the marks, marks included: the lines that are only a mark."""
    start = text.index("\n" + BEGIN + "\n") + 1
    return text[start:text.index("\n" + END + "\n", start) + 1 + len(END)]


NEXT = """next:
  1. check the geometry of the logo, DESIGN in tpotctl/splash_art.py, against the new picture:
     word_x / word_y (the lettering), pot_centre / pot_radii (the pot and the honey in it),
     pool_top / waves_y / pool_y (the honey pool), drops, stars, syrup_x / syrup_y (the effects),
     magentas (the colours of honey and lettering){colours}
  2. python3 -m tpotctl.ui_logo writes the copies of the scripts (installer/lib/ui.sh, install.sh)
  3. the tests: python -m unittest tpotctl.tests.test_logo_source tpotctl.tests.test_look
     tpotctl.tests.test_ui (with the venv of the T-Pot Manager for the splash)"""


def main(argv: Optional[List[str]] = None, target: Optional[str] = None) -> int:
    """The command line; target is the file to write (this one, the tests give a copy)."""
    parser = argparse.ArgumentParser(
        prog="python3 -m tpotctl.splash_art",
        description="Imports PALETTE and GRIDS of the T-Pot logo from its ANSI template (parsed, never run).")
    parser.add_argument("--import", dest="template", required=True, metavar="TEMPLATE",
                        help="the folder of t-pot-animate.py or the file")
    parser.add_argument("--import-80x24", dest="tight", metavar="FILE",
                        help="the 80x24 template (default: 80x24/t-pot-animate-80x24.py next to it)")
    parser.add_argument("--check", action="store_true", help="only compare, exit 1 when it differs")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    target = target or os.path.abspath(__file__)
    name = os.path.relpath(target)
    if name.startswith(os.pardir):
        name = target
    try:
        palette, grids = import_template(args.template, args.tight)
    except ValueError as error:
        print(f"the template is refused: {error}", file=sys.stderr)
        return 1
    want = render(palette, grids)
    with open(target, encoding="utf-8", newline="") as handle:
        text = handle.read()
    try:
        block = data_block(text)
        old_palette, old_grids = _current(block)
    except ValueError:
        print(f"{name} has no data between the marks {BEGIN} / {END}", file=sys.stderr)
        return 1
    if block == want:
        print(f"{name} is the template already, nothing to do")
        return 0
    if args.check:
        print(f"the data of {name} is not the template's, run python3 -m tpotctl.splash_art --import "
              f"{args.template}" + (f" --import-80x24 {args.tight}" if args.tight else ""))
        return 1
    with open(target, "w", encoding="utf-8", newline="") as out:     # in place: mode and owner stay
        out.write(text.replace(block, want))
    changed = [f"the grid {v}" for v in SIZES if grids[v] != old_grids.get(v)]
    if palette != old_palette:
        changed.append("the palette")
    print(f"wrote {name}: {', '.join(changed) or 'the layout of the data'}")
    print(NEXT.format(colours="" if palette == old_palette else
                      "\n     and COLOURS: the palette changed, these are its colours in the T-Pot Manager"
                      "\n     and the scripts"))
    return 0


def _current(block: str) -> Tuple[Palette, Grids]:
    """PALETTE and the grids of the data between the marks (literals, read as data)."""
    values = {}
    try:
        for node in ast.parse(block).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        palette = tuple(tuple(colour) for colour in values["PALETTE"])
        grids = {variant: (w, h, "".join(chunks)) for variant, (w, h, chunks) in values["GRIDS"].items()}
    except (SyntaxError, KeyError, TypeError, ValueError):
        raise ValueError("no PALETTE / GRIDS between the marks") from None
    return palette, grids


if __name__ == "__main__":
    sys.exit(main())
