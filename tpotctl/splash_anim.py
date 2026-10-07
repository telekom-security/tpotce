"""The splash of the T-Pot Manager as frames: the T-Pot ANSI logo (splash_art.py).

About eight seconds, frame(t) gives the picture at t seconds (no UI here, the screen in
screens/splash.py only shows the frames, any key goes on at once):

  0.00 - 1.50  the parts come together: the honeycombs pop up, the pot draws itself, the honey
               rises, then the lettering lands with a white flash
  1.50 - 7.50  the full cycle of the template: a light sweeps over the lettering, syrup runs down
               the drip, three drops fall and splash, light waves on the honey pool, stars twinkle
  1.60 - 2.60  the credits type themselves under the logo, every character with an afterglow
  7.50 - 8.00  everything crumbles cell by cell into the dark

Three sizes: 120 x 49 and 80 x 33 (the logo and a line of credits below it) and 80 x 24 (the logo
set tighter, the credits in its bottom line); below 80 x 24 there is no splash. The colours follow
the colour system of the T-Pot Manager (theme.color_system()): the tokens of theme.py in true
colour, palette entries with 256 or 16 colours. The ascii icon set gets coloured spaces only.
"""

import math
from typing import Dict, List, Optional, Tuple

from rich.style import Style
from rich.text import Text

from tpotctl import glyphs, splash_art, theme

ASSEMBLE = 1.5                  # the parts are together
CYCLE = 6.0                     # one loop of the template
OUT = ASSEMBLE + CYCLE          # the crumbling starts
DURATION = OUT + 0.5
TYPE_START, TYPE_END = 1.6, 2.6
GLOW = 0.35                     # how long a typed character glows after

COMBS, POT, HONEY, POOL, LETTERS = range(5)
# when the parts come, seconds
TIMES = {COMBS: (0.0, 0.5), POT: (0.3, 0.8), HONEY: (0.6, 1.1), POOL: (0.6, 1.1), LETTERS: (1.1, 1.5)}
# where they are, in the coordinates of the design (splash_art.design_of)
WORD_Y, WORD_X = (495, 798), (147, 1131)
POT_CENTRE, POT_RADII = (623, 524), (330, 400)
POOL_TOP = 900
MAGENTAS = (3, 4, 5, 6)
# the variants and the terminal they need: the logo, an empty line and the credits
VARIANTS = (("120", 120, 49), ("80", 80, 33), ("80x24", 80, 24))

Cell = Tuple[str, Optional[str], Optional[str]]        # character, foreground, background


def colours(system: str) -> List[str]:
    """The ten colours of the logo for a colour system; the tokens of theme.py where there is one."""
    if system == "256":
        return ["#000000", "#5f005f", "#87005f", "#af005f", "#d70087", "#ff5faf", "#ffafd7", "#ffffff",
                "#585858", "#a8a8a8"]
    if system == "16":
        # the template's classic mapping (black, magenta, bright magenta, white, greys) on the VGA set
        return ["#000000", "#aa00aa", "#aa00aa", "#aa00aa", "#ff55ff", "#ff55ff", "#ffffff", "#ffffff",
                "#555555", "#aaaaaa"]
    t = theme.TRUECOLOR
    return [t["INK"], t["COMB_LIT"], t["WAX"], "#A20053", t["MAGENTA"], "#FF4CA7", "#FF9DD0", t["GLASS"],
            "#5B585A", t["ASH"]]


def variant_for(width: int, height: int) -> Optional[str]:
    """The largest variant that fits the terminal, None below 80 x 24."""
    for variant, w, h in VARIANTS:
        if width >= w and height >= h:
            return variant
    return None


def _noise(x: float, y: float, salt: int = 0) -> float:
    """A fixed pseudo random number in [0, 1) per cell, the same in every frame."""
    value = math.sin(x * 12.9898 + y * 78.233 + salt * 37.719) * 43758.5453
    return value - math.floor(value)


def _span(t: float, start: float, end: float) -> float:
    return min(max((t - start) / (end - start), 0.0), 1.0)


# how bright the colours of the template are, for the ascii set (one colour per cell)
_LIGHT = [r * .3 + g * .59 + b * .11 for r, g, b in splash_art.PALETTE]


class Splash:

    def __init__(self, version: str = "", variant: str = "80"):
        self.version, self.variant = version, variant
        self.w, self.h, self.base = splash_art.grid(variant)
        self.rows = self.h // 2
        self.width = self.w
        self.height = self.rows + (0 if variant == "80x24" else 2)
        self.palette = colours(theme.color_system())
        self.design_y = [splash_art.design_of(self.w, self.h, 0, y)[1] for y in range(self.h)]
        self.parts = [self._part(i) for i in range(len(self.base))]
        self.appear = [self._appear(i) for i in range(len(self.base))]
        self.letters = [i for i, part in enumerate(self.parts) if part == LETTERS]
        self._styles: Dict[Tuple[Optional[str], Optional[str]], Style] = {}

    def colour(self, index: int) -> str:
        return self.palette[index]

    # -- the parts and when they come --------------------------------------------

    def _part(self, i: int) -> Optional[int]:
        c = self.base[i]
        if c == 0:
            return None
        dx, dy = splash_art.design_of(self.w, self.h, i % self.w, i // self.w)
        if WORD_Y[0] < dy < WORD_Y[1] and WORD_X[0] < dx < WORD_X[1] and c >= 3:
            return LETTERS
        if dy > POOL_TOP:
            return POOL
        reach = ((dx - POT_CENTRE[0]) / POT_RADII[0]) ** 2 + ((dy - POT_CENTRE[1]) / POT_RADII[1]) ** 2
        if reach <= 1:
            # the rim of the pot draws itself, the magenta inside it is honey
            return HONEY if c in MAGENTAS and reach < .72 else POT
        return COMBS

    def _appear(self, i: int) -> float:
        part = self.parts[i]
        if part is None:
            return 0.0
        start, end = TIMES[part]
        x, y = i % self.w, i // self.w
        if part == COMBS:
            size = max(4, self.w // 14)               # a honeycomb pops up as a whole
            return start + _noise(x // size, y // size, 3) * (end - start - .15)
        if part == POT:
            dx, dy = splash_art.design_of(self.w, self.h, x, y)
            around = (math.atan2(dx - POT_CENTRE[0], POT_CENTRE[1] - dy) / (2 * math.pi)) % 1
            return start + around * (end - start - .15) + (0 if self.base[i] in (1, 2) else .1)
        if part in (HONEY, POOL):
            return start + (1 - y / (self.h - 1)) * (end - start - .1)
        return start

    def assembly(self, t: float) -> List[int]:
        """The pixels while the parts come together; at ASSEMBLE the whole logo."""
        out = [0] * len(self.base)
        for i, c in enumerate(self.base):
            part = self.parts[i]
            if part is None or part == LETTERS or t < self.appear[i]:
                continue
            age = t - self.appear[i]
            if part == COMBS:
                out[i] = 7 if age < .05 else c                  # pops up with a glint
            elif part == POT:
                out[i] = 6 if age < .04 and c in (1, 2) else c
            else:
                out[i] = 6 if age < .06 else c                  # the rising edge of the honey shines
        start = TIMES[LETTERS][0]
        if t >= start:
            age = t - start
            drop = 2 if age < .1 else 0                         # it lands from a line above
            for i in self.letters:
                c = self.base[i]
                if age < .12:
                    shade = 7                                   # the flash
                elif c in MAGENTAS and age < .22:
                    shade = 6
                elif c in MAGENTAS and age < .32:
                    shade = 5
                else:
                    shade = c
                y = i // self.w - drop
                if y >= 0:
                    out[y * self.w + i % self.w] = shade
        return out

    # -- the cycle of the template ------------------------------------------------

    def _put(self, a: List[int], x: int, y: int, colour: int) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            a[y * self.w + x] = colour

    def cycle(self, phase: float) -> List[int]:
        """One frame of the template's loop (its frame()), phase in seconds from 0 to CYCLE."""
        w, h, base = self.w, self.h, self.base
        a = list(base)
        sweep = -.15 + (phase / CYCLE) * 1.45                   # a light across the lettering
        for y in range(h):
            if 495 < self.design_y[y] < 798:
                for x in range(w):
                    i = y * w + x
                    if base[i] in MAGENTAS:
                        distance = abs(x / w - y / h * .12 - sweep)
                        if distance < .012:
                            a[i] = 7
                        elif distance < .034:
                            a[i] = 6
                        elif distance < .052:
                            a[i] = 5
        for yy in splash_art.SYRUP_Y:                           # syrup runs down the long drip
            x, y = splash_art.design_xy(w, h, splash_art.SYRUP_X, yy)
            if 0 <= x < w and 0 <= y < h and base[y * w + x] in MAGENTAS:
                self._put(a, x, y, 6 if (y - int(phase * 7)) % 7 < 2 else 4)
        for k, (xx, yy) in enumerate(splash_art.DROPS):         # three drops fall and splash
            p = ((phase + k * 1.65) % 3) / 3
            start_x, start_y = splash_art.design_xy(w, h, xx, yy)
            end_x, end_y = splash_art.design_xy(w, h, xx, splash_art.POOL_Y)
            if p < .16:
                length = 1 + int(p / .16 * 2)
                for dy in range(length):
                    self._put(a, start_x, start_y + dy, 4)
                self._put(a, start_x, start_y + length, 6)
            elif p < .83:
                q = (p - .16) / .67
                y = start_y + 2 + int((end_y - start_y - 2) * q * q)
                self._put(a, start_x, y - 1, 4)
                self._put(a, start_x, y, 5)
                if w >= 120:
                    self._put(a, start_x - 1, y, 4)
                    self._put(a, start_x, y + 1, 4)
            else:
                radius = 1 + int((p - .83) / .17 * 4)
                for dx in (-radius, radius):
                    self._put(a, end_x + dx, end_y, 5)
                    self._put(a, end_x + dx, end_y - 1, 4)
                if radius < 3:
                    self._put(a, end_x, end_y - 2, 6)
        for y in range(h):                                      # light waves on the honey pool
            if 924 < self.design_y[y] < 1035:
                for x in range(w):
                    i = y * w + x
                    if base[i] in MAGENTAS:
                        wave = math.sin(x * .48 - phase * math.pi * 2 / CYCLE * 2 + y * .7)
                        if wave > .93:
                            a[i] = 6
                        elif wave > .7:
                            a[i] = 5
        for k, (xx, yy) in enumerate(splash_art.STARS):         # the stars twinkle
            x, y = splash_art.design_xy(w, h, xx, yy)
            if int((phase + k * .73) * 3) % 9 == 0:
                self._put(a, x, y, 7)
                if w >= 120:
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        self._put(a, x + dx, y + dy, 6)
        return a

    def pixels(self, t: float) -> List[int]:
        if t < ASSEMBLE:
            return self.assembly(t)
        return self.cycle(min(t, OUT) - ASSEMBLE)

    # -- the credits ---------------------------------------------------------------

    def credits(self) -> List[Tuple[int, int, str, int]]:
        """x, y, character and its colour index, in the order they are typed."""
        ascii_set = glyphs.mode() == "ascii"
        line, double = ("-", "=") if ascii_set else ("─", "═")
        placed: List[Tuple[int, int, str, int]] = []

        def place(x: int, y: int, parts) -> None:
            for text, colour in parts:
                for char in text:
                    placed.append((x, y, char, colour))
                    x += 1

        def name_parts(room: int):
            for name in (f"t-pot {self.version}".strip(), "t-pot"):
                parts = [("[ ", 2), (name, 7), (" ]", 2)]
                if sum(len(text) for text, _c in parts) <= room:
                    break
            return parts
        credit = [("[ ", 2), ("telekom security", 4), (" ]", 2)]
        if self.variant == "80x24":
            # the bottom line of the logo has room left (columns 0-27) and right (49-79) of the pool
            y = self.rows - 1
            place(1, y, [(line * 2, 2)] + name_parts(24))
            right = credit + [(line * 2, 2)]
            place(self.width - 2 - sum(len(text) for text, _c in right), y, right)
        else:
            parts = [(line * 2, 2)] + name_parts(self.width - 34) + [(double * 2, 2)] + credit + [(line * 2, 2)]
            place((self.width - sum(len(text) for text, _c in parts)) // 2, self.height - 1, parts)
        return placed

    def _paint_credits(self, grid: List[List[Cell]], t: float) -> None:
        placed = self.credits()
        count = len(placed)
        typed = 0
        for n, (x, y, char, colour) in enumerate(placed):
            at = TYPE_START + n / count * (TYPE_END - TYPE_START)
            if t < at:
                break
            typed = n + 1
            age = t - at
            if age < GLOW / 3:
                shade = 7                                       # fresh: white hot
            elif age < GLOW * 2 / 3:
                shade = 6
            elif age < GLOW:
                shade = 5
            else:
                shade = colour
            grid[y][x] = (char, self.palette[shade] if char != " " else None, None)
        cursor_on = TYPE_START <= t < TYPE_END or (t < TYPE_END + .5 and int(t * 4) % 2 == 0)
        if cursor_on:
            if typed < count:
                x, y = placed[typed][0], placed[typed][1]
            else:
                x, y = placed[-1][0] + 1, placed[-1][1]
            if x < self.width:
                grid[y][x] = ("#" if glyphs.mode() == "ascii" else "█", self.palette[7], None)

    # -- the frame -----------------------------------------------------------------

    def _crumble(self, grid: List[List[Cell]], t: float) -> None:
        if t < OUT:
            return
        progress = _span(t, OUT, DURATION) * 1.15
        for y, row in enumerate(grid):
            for x, cell in enumerate(row):
                if cell == (" ", None, None):
                    continue
                h = _noise(x, y, 7) * .85 + (abs(x - self.width / 2) / self.width) * .15
                if h < progress - .12:
                    row[x] = (" ", None, None)
                elif h < progress:
                    row[x] = (" ", None, self.palette[2])            # an ember before it goes dark

    def cells(self, t: float) -> List[List[Cell]]:
        pixels = self.pixels(t)
        w, palette = self.w, self.palette
        ascii_set = glyphs.mode() == "ascii"
        grid: List[List[Cell]] = []
        for r in range(self.rows):
            row: List[Cell] = []
            for x in range(w):
                top, bottom = pixels[2 * r * w + x], pixels[(2 * r + 1) * w + x]
                if top == bottom == 0:
                    row.append((" ", None, None))
                elif ascii_set:
                    row.append((" ", None, palette[top if _LIGHT[top] >= _LIGHT[bottom] else bottom]))
                elif top == bottom:
                    row.append((" ", None, palette[top]))
                elif bottom == 0:
                    row.append(("▀", palette[top], None))
                elif top == 0:
                    row.append(("▄", palette[bottom], None))
                else:
                    row.append(("▀", palette[top], palette[bottom]))
            grid.append(row)
        for _ in range(self.height - self.rows):
            grid.append([(" ", None, None)] * self.width)
        if t >= TYPE_START:
            self._paint_credits(grid, t)
        self._crumble(grid, t)
        return grid

    def frame(self, t: float) -> Text:
        out = Text()
        for y, row in enumerate(self.cells(t)):
            run, key = "", None
            for char, fg, bg in row:
                if (fg, bg) != key and run:
                    out.append(run, self._style(key))
                    run = ""
                key = (fg, bg)
                run += char
            out.append(run, self._style(key))
            if y < self.height - 1:
                out.append("\n")
        return out

    def _style(self, key) -> Style:
        if key not in self._styles:
            fg, bg = key
            self._styles[key] = Style(color=fg, bgcolor=bg) if fg or bg else Style()
        return self._styles[key]

    def fits(self, width: int, height: int) -> bool:
        return width >= self.width and height >= self.height


def best(version: str, width: int, height: int) -> Optional[Splash]:
    """The largest splash that fits the terminal, None below 80 x 24."""
    variant = variant_for(width, height)
    return Splash(version, variant) if variant else None
