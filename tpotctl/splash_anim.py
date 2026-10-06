"""The splash of the T-Pot Manager as frames: the t-pot wordmark as a BBS ANSI logo.

About two seconds, frame(t) gives the picture at t seconds (no UI here, the screen in
screens/splash.py only shows the frames):

  0.00 - 0.30  a CP437 double frame draws itself around the logo
  0.15 - 0.90  the letters fill from below in shading steps, a shadow of light shade
  0.60 - 1.00  the tagline and the credits in the bottom of the frame type themselves
  0.70 - 1.50  drops fall from the letters, a glint cycles over their edges
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
CREDIT = "telekom security"
# the frame (corners top left, top right, bottom left, bottom right, then the lines) and the
# shading steps from light to full, CP437 like the ANSI art of the BBS days, and plain ascii
FRAME = {"unicode": "╔╗╚╝═║", "ascii": "++++-|"}
SHADES = {"unicode": "░▒▓█", "ascii": ".:#@"}
CYCLE = ("mist", "glass", "glass", "mist")                  # the glint over the letter edges

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
        # rows: frame, gap, wordmark, drip room (3), tagline, gap, frame with the credits
        self.mark_y = 2
        self.drip_y = self.mark_y + len(self.mark)
        self.tag_y = self.drip_y + 3
        self.height = self.tag_y + 3
        self.mark_x = (self.width - self.mark_w) // 2
        self.drops = self._drops()

    # -- the parts -----------------------------------------------------------

    def _frame_cells(self) -> List[Tuple[int, int, int]]:
        """The border clockwise from the top left corner: x, y, index into FRAME."""
        right, bottom = self.width - 1, self.height - 1
        cells = [(0, 0, 0)] + [(x, 0, 4) for x in range(1, right)] + [(right, 0, 1)]
        cells += [(right, y, 5) for y in range(1, bottom)] + [(right, bottom, 3)]
        cells += [(x, bottom, 4) for x in range(right - 1, 0, -1)] + [(0, bottom, 2)]
        cells += [(0, y, 5) for y in range(bottom - 1, 0, -1)]
        return cells

    def _edge(self, x: int, y: int) -> bool:
        """A letter pixel next to the outside of its letter: where the colour cycle runs."""
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= ny < len(self.mark) and 0 <= nx < self.mark_w) or self.mark[ny][nx] != "#":
                return True
        return False

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

    @staticmethod
    def _set() -> str:
        return "ascii" if glyphs.mode() == "ascii" else "unicode"

    def _paint_frame(self, grid, t: float) -> None:
        chars = FRAME[self._set()]
        cells = self._frame_cells()
        for x, y, kind in cells[:int(_ease(_span(t, 0.0, 0.30)) * len(cells))]:
            grid[y][x] = (chars[kind], "magenta" if kind < 4 else "wax")

    def _paint_mark(self, grid, t: float) -> None:
        if t < 0.15:
            return
        light, medium, dark, full = SHADES[self._set()]
        rows = len(self.mark)
        level = rows - _span(t, 0.20, 0.90) * (rows + 3.5)              # the surface, from below
        cycle = -len(CYCLE) + _span(t, 0.90, 1.35) * (self.mark_w + 2 * len(CYCLE))
        for y, row in enumerate(self.mark):
            for x, c in enumerate(row):
                if c == " ":
                    continue
                gx, gy = self.mark_x + x, self.mark_y + y
                depth = y - level                                       # rows under the surface
                if c == "s":
                    if depth >= 3:
                        grid[gy][gx] = (light, "ash")                   # the shadow
                elif depth < 0:
                    grid[gy][gx] = (light, "wax")                       # still empty
                elif depth < 1:
                    grid[gy][gx] = (medium, "magenta")
                elif depth < 2:
                    grid[gy][gx] = (dark, "magenta")
                else:
                    band = math.floor(x - cycle) if self._edge(x, y) else -1
                    grid[gy][gx] = (full, CYCLE[band] if 0 <= band < len(CYCLE) else "magenta")

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
        x0 = (self.width - len(TAGLINE)) // 2
        for index, char in enumerate(TAGLINE[:int(_span(t, 0.6, 1.0) * len(TAGLINE))]):
            grid[self.tag_y][x0 + index] = (char, "ash")

    def credits(self) -> List[Cell]:
        """The credits in the bottom line of the frame: [ t-pot 24.04.2 ]==[ telekom security ]; a
        version too long for the frame is left out, the corners always stay."""
        line = FRAME[self._set()][4]
        for name in (f"t-pot {self.version}".strip(), "t-pot"):
            parts = [("[ ", "wax"), (name, "glass"), (" ]", "wax"), (line * 2, "wax"), ("[ ", "wax"),
                     (CREDIT, "magenta"), (" ]", "wax")]
            cells = [(char, colour) for text, colour in parts for char in text]
            if len(cells) <= self.width - 4:
                break
        return cells[:self.width - 4]

    def _paint_credits(self, grid, t: float) -> None:
        chars = self.credits()
        x0 = (self.width - len(chars)) // 2
        for index, cell in enumerate(chars[:int(_span(t, 0.6, 1.0) * len(chars))]):
            grid[self.height - 1][x0 + index] = cell

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
        self._paint_frame(grid, t)
        self._paint_mark(grid, t)
        self._paint_drops(grid, t)
        self._paint_tagline(grid, t)
        self._paint_credits(grid, t)
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
