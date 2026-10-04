"""The T-Pot logo for the terminal: the wordmark and the honey pot in its hexagon.

Both are pixel maps, one character per pixel and colour. Two pixel rows become one
text row of upper half blocks (foreground = upper pixel, background = lower one),
so the pixels are about square. The ascii icon set gets the figlet letters of
install.sh instead.
"""

from typing import List

from rich.style import Style
from rich.text import Text

from tpotctl import glyphs, theme

# t-pot in lower case: t with its ascender, p with its descender, o at x-height
WORDMARK = [
    ".#..............#.",
    "###....###.###.###",
    ".#..##.#.#.#.#..#.",
    ".#.....#.#.#.#..#.",
    ".##....###.###..##",
    ".......#..........",
]

POT = [
    "............MM............",
    "..........MMMMMM..........",
    "........MMM....MMM........",
    "......MMM........MMM......",
    "....MMM......PPPP..MMM....",
    "..MMM.......PMPMPP...MMM..",
    "MMM.........PMPMPMP....MMM",
    "MM..........PPMPMPP.....MM",
    "MM...........PPPPPP.....MM",
    "MM...........M...PP.....MM",
    "MM...........M.M..PMP...MM",
    "MM...........M.....PMP..MM",
    "MM.....PPPPPPPPPPPP..PP.MM",
    "MM......PWWWWMWWBP......MM",
    "MM.....PWWWWWMWWBBP.....MM",
    "MM....PWWWWWWWWWBBBP....MM",
    "MM....PWWWWWWWWWBBBP....MM",
    "MM....PPPPPPPPPPPPPP....MM",
    "MM....PMMMMMMMMMMMMP....MM",
    "MM....PMWMMWWMWWMWWP....MM",
    "MM....PMWMMWMMWMMWMP....MM",
    "MM....PMMMMMMMMMMMMP....MM",
    "MM.....PPPPPPPPPPPP.....MM",
    "MMM....................MMM",
    "..MMM................MMM..",
    "....MMM............MMM....",
    "......MMM........MMM......",
    "........MMM....MMM........",
    "..........MMMMMM..........",
    "............MM............",
]

FIGLET = [
    " _____     ____       _   ",
    "|_   _|   |  _ \\ ___ | |_ ",
    "  | |_____| |_) / _ \\| __|",
    "  | |_____|  __/ (_) | |_ ",
    "  |_|     |_|   \\___/ \\__|",
]

_PIXEL = {"M": "magenta", "P": "petrol", "W": "glass", "B": "mist", "#": "glass"}


def _pixels(rows: List[str], on: str = "") -> Text:
    """Half block rendering, '.' is transparent; `on` replaces '#' (the wordmark colour)."""
    if len(rows) % 2:
        rows = rows + ["." * len(rows[0])]
    out = Text()
    for top_row, bottom_row in zip(rows[0::2], rows[1::2]):
        for top, bottom in zip(top_row, bottom_row):
            fg = _colour(top, on)
            bg = _colour(bottom, on)
            if fg is None and bg is None:
                out.append(" ")
            elif fg is None:
                out.append("▄", Style(color=bg))
            elif bg is None or bg == fg:
                out.append("▀" if bg is None else "█", Style(color=fg))
            else:
                out.append("▀", Style(color=fg, bgcolor=bg))
        out.append("\n")
    out.rstrip()
    return out


def _colour(pixel: str, on: str):
    if pixel == ".":
        return None
    if pixel == "#" and on:
        return on
    return theme.color(_PIXEL[pixel])


def wordmark(colour: str = "") -> Text:
    """Three rows, 19 columns; colour overrides the letters (i.e. glass on a magenta plate)."""
    if glyphs.mode() == "ascii":
        return Text("t-pot", style="bold")
    return _pixels(WORDMARK, colour)


def pot() -> Text:
    """15 rows, 26 columns."""
    if glyphs.mode() == "ascii":
        return Text("\n".join(FIGLET), style=theme.color("magenta"))
    return _pixels(POT)
