"""The splash of tpot as frames: honey drips into the t-pot wordmark.

About two seconds, frame(t) gives the picture at t seconds (no UI here, the screen in
screens/splash.py only shows the frames):

  0.00 - 0.45  a honeycomb lights up from the middle outwards, some cells keep honey
  0.15 - 0.80  the letters appear as empty glass and fill with honey from below
  0.70 - 1.50  drops fall from the letters, a light runs over them, the tagline types
  1.50 - 2.00  everything crumbles cell by cell into the dark

Colours are the tokens of theme.py, glyphs follow the icon set (ascii has its own).
"""

import math
from typing import Dict, List, Optional, Tuple

from rich.style import Style
from rich.text import Text

from tpotctl import glyphs, theme

DURATION = 2.0
OUT = 1.5                   # the crumbling starts here

# the letters as pixels, one character per cell: ascender row 0, x-height 2..6, descender 7
LETTERS = {
    "t": ["  ##  ", "  ##  ", "######", "  ##  ", "  ##  ", "  ##  ", "   ###", "      "],
    "-": ["     ", "     ", "     ", "     ", "#####", "     ", "     ", "     "],
    "p": ["      ", "      ", "##### ", "##  ##", "##  ##", "##  ##", "##### ", "##    "],
    "o": ["      ", "      ", " #### ", "##  ##", "##  ##", "##  ##", " #### ", "      "],
}
WORD = "t-pot"
TAGLINE = "honeypot platform"

Cell = Tuple[str, Optional[str]]          # character, colour token (None: no colour)


def wordmark_pixels(scale: int = 1) -> List[str]:
    """The pixels of the wordmark plus its shadow ('s', one right and one down), each pixel
    scale x scale cells."""
    rows = ["  ".join(LETTERS[c][r] for c in WORD) for r in range(len(LETTERS["t"]))]
    rows = ["".join(c * scale for c in row) for row in rows for _ in range(scale)]
    height, width = len(rows) + 1, len(rows[0]) + 1
    grid = [[" "] * width for _ in range(height)]
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c == "#":
                grid[y][x] = "#"
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c == "#" and grid[y + 1][x + 1] == " ":
                grid[y + 1][x + 1] = "s"
    return ["".join(r) for r in grid]


def _noise(x: int, y: int, salt: int = 0) -> float:
    """A fixed pseudo random number in [0, 1) per cell, the same in every frame."""
    value = math.sin(x * 12.9898 + y * 78.233 + salt * 37.719) * 43758.5453
    return value - math.floor(value)


def _ease(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def _span(t: float, start: float, end: float) -> float:
    return min(max((t - start) / (end - start), 0.0), 1.0)


class Splash:

    def __init__(self, version: str = "", scale: int = 1):
        self.version, self.scale = version, scale
        self.mark = wordmark_pixels(scale)
        self.mark_w = len(self.mark[0])
        self.width = self.mark_w + 8
        self.comb_rows = 2
        # rows: comb (2), gap, wordmark, drip room (3), tagline, gap, comb (2)
        self.mark_y = self.comb_rows + 1
        self.drip_y = self.mark_y + len(self.mark)
        self.tag_y = self.drip_y + 3
        self.height = self.tag_y + 2 + self.comb_rows
        self.mark_x = (self.width - self.mark_w) // 2
        self.drops = self._drops()

    # -- the parts -----------------------------------------------------------

    def _comb_cells(self) -> List[Tuple[int, int]]:
        cells = []
        for band in (0, self.height - self.comb_rows):
            for row in range(self.comb_rows):
                y = band + row
                for x in range(1 + row % 2, self.width - 1, 2):
                    cells.append((x, y))
        return cells

    def _drops(self) -> List[Tuple[int, int, float]]:
        """(x, y of the lowest honey pixel, start time) under some letters."""
        bottoms: Dict[int, int] = {}
        for y, row in enumerate(self.mark):
            for x, c in enumerate(row):
                if c == "#":
                    bottoms[x] = y
        # the stem of the first t, the descender of p, the bottom of o, the hook of the last t
        wanted = [x * self.scale for x in (3, 15, 24, 34)]
        drops = []
        for index, x in enumerate(wanted):
            column = min(bottoms, key=lambda c: abs(c - x))
            drops.append((self.mark_x + column, self.mark_y + bottoms[column], 0.72 + 0.11 * index))
        return drops

    def _grid(self) -> List[List[Cell]]:
        return [[(" ", None)] * self.width for _ in range(self.height)]

    def _paint_comb(self, grid, t: float) -> None:
        cx, cy = self.width / 2, self.height / 2
        reach = math.hypot(cx, cy)
        wave = _ease(_span(t, 0.0, 0.45)) * 1.15
        on, off = glyphs.g("on"), glyphs.g("off")
        for x, y in self._comb_cells():
            distance = math.hypot((x - cx) / 2, y - cy) / (reach / 1.6)
            if distance > wave:
                continue
            if wave - distance < 0.12 and t < 0.6:
                grid[y][x] = (on, "glass")                  # the front of the wave
            elif _noise(x, y) < 0.28:
                grid[y][x] = (on, "magenta")                # cells with honey
            else:
                grid[y][x] = (off, "wax")

    def _paint_mark(self, grid, t: float) -> None:
        if t < 0.15:
            return
        ascii_mode = glyphs.mode() == "ascii"
        full, empty, shade = ("#", ".", ":") if ascii_mode else ("█", "░", "░")
        rows = len(self.mark)
        level = rows - _ease(_span(t, 0.25, 0.80)) * (rows + 0.5)        # honey surface, from below
        light = -4 + _span(t, 0.85, 1.25) * (self.mark_w + 8)              # a light running over it
        for y, row in enumerate(self.mark):
            for x, c in enumerate(row):
                if c == " ":
                    continue
                gx, gy = self.mark_x + x, self.mark_y + y
                filled = y >= level
                if c == "s":
                    if filled:
                        grid[gy][gx] = (shade, "wax")
                    continue
                if not filled:
                    grid[gy][gx] = (empty, "wax")
                elif y < level + 1 and t < 0.85:
                    grid[gy][gx] = (full, "glass")                         # the surface shines
                elif abs(x - light) < 1.5:
                    grid[gy][gx] = (full, "glass")
                else:
                    grid[gy][gx] = (full, "magenta")

    def _paint_drops(self, grid, t: float) -> None:
        ascii_mode = glyphs.mode() == "ascii"
        trail, head, splash = ("|", "o", ".") if ascii_mode else ("┃", "•", "·")
        for x, y0, start in self.drops:
            if t < start or t >= OUT:
                continue
            fall = (t - start) / 0.06                                      # rows fallen
            bottom = self.tag_y - 1
            y = y0 + 1 + int(fall)
            if y > bottom:
                if fall < (bottom - y0) + 3:
                    grid[bottom][x] = (splash, "magenta")
                continue
            if y - 1 > y0:
                grid[y - 1][x] = (trail, "magenta")
            grid[y][x] = (head, "magenta")

    def _paint_tagline(self, grid, t: float) -> None:
        typed = int(_span(t, 0.6, 1.0) * (len(TAGLINE) + len(self.version) + 2))
        text = f"{TAGLINE}  {self.version}".rstrip()
        x0 = (self.width - len(text)) // 2
        for index, char in enumerate(text[:typed]):
            colour = "ash" if index < len(TAGLINE) else "magenta"
            grid[self.tag_y][x0 + index] = (char, colour)

    def _crumble(self, grid, t: float) -> None:
        if t < OUT:
            return
        progress = _span(t, OUT, DURATION) * 1.15
        off = glyphs.g("off")
        for y, row in enumerate(grid):
            for x, (char, colour) in enumerate(row):
                if char == " ":
                    continue
                h = _noise(x, y, 7) * 0.85 + (abs(x - self.width / 2) / self.width) * 0.15
                if h < progress - 0.12:
                    row[x] = (" ", None)
                elif h < progress:
                    row[x] = (off, "wax")                                  # crumbles into a cell

    # -- the frame -----------------------------------------------------------

    def cells(self, t: float) -> List[List[Cell]]:
        grid = self._grid()
        self._paint_comb(grid, t)
        self._paint_mark(grid, t)
        self._paint_drops(grid, t)
        self._paint_tagline(grid, t)
        self._crumble(grid, t)
        return grid

    def frame(self, t: float) -> Text:
        out = Text()
        styles: Dict[Optional[str], Style] = {}
        for y, row in enumerate(self.cells(t)):
            run, run_colour = "", None
            for char, colour in row:
                if colour != run_colour and run:
                    out.append(run, styles.get(run_colour))
                    run = ""
                if colour not in styles:
                    styles[colour] = Style(color=theme.color(colour), bold=colour == "glass") if colour else Style()
                run_colour = colour
                run += char
            out.append(run, styles.get(run_colour))
            if y < self.height - 1:
                out.append("\n")
        return out

    def fits(self, width: int, height: int) -> bool:
        return width >= self.width + 2 and height >= self.height + 2


def best(version: str, width: int, height: int) -> Optional[Splash]:
    """The largest splash that fits the terminal, None if not even the small one does."""
    for scale in (2, 1):
        splash = Splash(version, scale)
        if splash.fits(width, height):
            return splash
    return None
