"""The T-Pot wordmark for the header of the terminal.

A pixel map, one character per pixel and colour. Two pixel rows become one text row
of upper half blocks (foreground = upper pixel, background = lower one), so the
pixels are about square. The ascii icon set gets the plain word instead.
"""

from typing import List

from rich.style import Style
from rich.text import Text

from tpotctl import glyphs, theme

# T-Pot: the capital T and P at the full height of six pixels, the o and the t (with its
# ascender) below them; the scripts show the same pixels (tpotctl/ui_logo.py writes them into
# installer/lib/ui.sh), so keep it a plain list of strings
WORDMARK = [
    "#####....###.........",
    "..#......#..#......#.",
    "..#......#..#.###.###",
    "..#...##.###..#.#..#.",
    "..#......#....#.#..#.",
    "..#......#....###..##",
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
    """Three rows, 21 columns; colour overrides the letters (i.e. glass on a magenta plate)."""
    if glyphs.mode() == "ascii":
        return Text("T-Pot", style="bold")
    return _pixels(WORDMARK, colour)
