"""The pixels of the ISO's graphical terminal (installer/iso/terminal.sh): its real session (window
manager, Xfce Terminal, Cascadia Mono) in an Xvfb of the console sizes, captured with xwd and read
here without smoothing. Explicit only, on Linux as a regular user:

    TPOT_TEST_TERMINAL_RASTER=1 python3 -m unittest tpotctl.tests.test_terminal_raster

It needs the packages of `installer/iso/terminal.sh --packages` plus xvfb and x11-apps; a missing
one fails the run (it never skips once asked for). TPOT_TEST_TERMINAL_RASTER_OUT=<folder> keeps the
captures as PNG. Standard library only."""

import json
import os
import platform
import secrets
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
import zlib
from typing import Dict, List, Optional, Tuple

from tpotctl import glyphs, ui_logo

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPER = os.path.join(REPO, "installer", "iso", "terminal.sh")
UI_SH = os.path.join(REPO, "installer", "lib", "ui.sh")
ENABLED = os.environ.get("TPOT_TEST_TERMINAL_RASTER") == "1"
TOOLS = ("Xvfb", "xauth", "xwd", "xwininfo")
TEST_PACKAGES = ("xvfb", "x11-apps")

RGB = Tuple[int, int, int]
TRUECOLOR = ui_logo.theme_palettes()["truecolor"]


def rgb(colour: str) -> RGB:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


INK = rgb(TRUECOLOR["INK"])
GLASS = rgb(TRUECOLOR["GLASS"])
MAGENTA = rgb(TRUECOLOR["MAGENTA"])
# the glyphs of the T-Pot Manager's default icon set, each once
GLYPHS = list(dict.fromkeys(glyphs._UNICODE.values()))
HEXAGONS = ("⬡", "⬢")
BOX = "─═"

# the session inside the terminal: one screen per phase, then it waits for the test to capture it
SESSION = r"""set -u
myOUT="$1"
myCHECKOUT="$2"
stty size > "${myOUT}/start-size"
printf '\033[?25l'
fuSHOWN () {
  : > "${myOUT}/ready$1"
  local myEND=$((SECONDS + 120))
  until [ -e "${myOUT}/go$1" ]; do [ "${SECONDS}" -lt "${myEND}" ] || exit 3; sleep 0.05; done
}
# shellcheck source=/dev/null
. "${myCHECKOUT}/installer/lib/ui.sh"
fuUI_COLORS > "${myOUT}/colors"
stty size > "${myOUT}/size"
read -r myROWS myCOLS < "${myOUT}/size"
printf '\033[0m\033[2J\033[H\033[38;2;226;0;116m█\033[%s;%sH█\033[0m' "${myROWS}" "${myCOLS}"
fuSHOWN 0
printf '\033[0m\033[2J\033[H'
fuUI_LOGO
echo "$?" > "${myOUT}/logo"
fuSHOWN 1
PYTHONPATH="${myCHECKOUT}" PYTHONDONTWRITEBYTECODE=1 python3 - "${myOUT}" <<'PYTHON'
import json, os, sys
from tpotctl import prefs, ui_logo
system = prefs.detect_colors(os.environ)
palette = ui_logo.theme_palettes()[system]
with open(os.path.join(sys.argv[1], "prefs"), "w") as handle:
    handle.write(system + "\n")
def sgr(layer, token):
    colour = palette[token]
    if system == "truecolor":
        return "%d;2;%d;%d;%d" % ((38 if layer == "fg" else 48),
                                  int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))
    if system == "256":
        return "%d;5;%d" % ((38 if layer == "fg" else 48), ui_logo.xterm_index(colour))
    return str(ui_logo.sgr16(colour) + (0 if layer == "fg" else 10))
out = "\033[0m\033[2J\033[H"
with open(os.path.join(sys.argv[1], "layout.json")) as handle:
    for row, col, text, layer, token, bold in json.load(handle):
        codes = ["0"] + (["1"] if bold else []) + ([sgr(layer, token)] if token else [])
        out += "\033[%d;%dH\033[%sm%s\033[0m" % (row, col, ";".join(codes), text)
sys.stdout.write(out)
sys.stdout.flush()
PYTHON
fuSHOWN 2
"""


def layout() -> List[list]:
    """Phase 2, 1-based (row, col, text, layer, token, bold): plain GLASS, every token of the theme
    as text and as background, the glyphs normal and bold, two blank cells after each."""
    cells: List[list] = [[1, 1, "█", None, None, False], [1, 3, "T-Pot", None, None, False]]
    for index, token in enumerate(TRUECOLOR):
        cells.append([3, 1 + 3 * index, "█", "fg", token, False])
        cells.append([5, 1 + 3 * index, " ", "bg", token, False])
    for index, glyph in enumerate(GLYPHS):
        cells.append([7, 1 + 3 * index, glyph, None, None, False])
        cells.append([9, 1 + 3 * index, glyph, None, None, True])
    return cells


class Raster:
    """An XWD (version 7, TrueColor) capture as planes of red, green and blue."""

    def __init__(self, raw: bytes):
        header = struct.unpack(">25I", raw[:100])
        size, version, fmt, depth, width, height = header[:6]
        order, bits, stride = header[7], header[11], header[12]
        masks = header[14:17]
        if version != 7 or fmt != 2 or depth not in (24, 32) or bits not in (24, 32):
            raise ValueError(f"unexpected XWD: version {version}, format {fmt}, depth {depth}, bits {bits}")
        if masks != (0xFF0000, 0xFF00, 0xFF):
            raise ValueError(f"unexpected XWD colour masks {masks}")
        step = bits // 8
        data = raw[size + header[19] * 12:]
        if len(data) != height * stride:
            raise ValueError("truncated XWD capture")
        if stride != width * step:
            data = b"".join(data[y * stride:y * stride + width * step] for y in range(height))

        def plane(mask: int) -> bytes:
            byte = (mask.bit_length() - 8) // 8
            return data[(byte if order == 0 else step - 1 - byte)::step]
        self.width, self.height = width, height
        self.r, self.g, self.b = plane(masks[0]), plane(masks[1]), plane(masks[2])

    def pixel(self, x: int, y: int) -> RGB:
        i = y * self.width + x
        return self.r[i], self.g[i], self.b[i]

    def row(self, x: int, y: int, length: int) -> List[RGB]:
        i = y * self.width + x
        return list(zip(self.r[i:i + length], self.g[i:i + length], self.b[i:i + length]))

    def ink_free(self, x: int, y: int, width: int, height: int) -> bool:
        """Every pixel of the box is INK (black)."""
        for line in range(y, y + height):
            i = line * self.width + x
            if max(self.r[i:i + width]) or max(self.g[i:i + width]) or max(self.b[i:i + width]):
                return False
        return True

    def not_ink(self) -> List[Tuple[int, int]]:
        found = []
        for y in range(self.height):
            if not self.ink_free(0, y, self.width, 1):
                found.extend((x, y) for x, colour in enumerate(self.row(0, y, self.width)) if colour != INK)
        return found

    def png(self) -> bytes:
        lines = bytearray()
        for y in range(self.height):
            lines.append(0)
            for colour in self.row(0, y, self.width):
                lines.extend(colour)

        def chunk(name: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(lines))) + chunk(b"IEND", b""))


Cell = Tuple[str, Optional[RGB], Optional[RGB], bool]


def parse_screen(text: str) -> Dict[Tuple[int, int], Cell]:
    """Cells (0-based row, col) -> (character, fg, bg, bold) of plain text with SGR and cursor
    position sequences (true colour only); None is the terminal's default colour."""
    cells: Dict[Tuple[int, int], Cell] = {}
    row = col = 0
    fg: Optional[RGB] = None
    bg: Optional[RGB] = None
    bold = False
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\033":
            end = i + 2
            while not text[end].isalpha():
                end += 1
            params, final = text[i + 2:end], text[end]
            i = end + 1
            if final == "H":
                parts = [int(p) for p in params.split(";")] if params else [1, 1]
                row, col = parts[0] - 1, parts[1] - 1
                continue
            if final != "m":
                continue
            codes = [int(p) for p in params.split(";")] if params else [0]
            k = 0
            while k < len(codes):
                code = codes[k]
                if code == 0:
                    fg = bg = None
                    bold = False
                elif code == 1:
                    bold = True
                elif code in (38, 48) and codes[k + 1] == 2:
                    colour = (codes[k + 2], codes[k + 3], codes[k + 4])
                    if code == 38:
                        fg = colour
                    else:
                        bg = colour
                    k += 4
                else:
                    raise ValueError(f"unexpected SGR {params!r}")
                k += 1
            continue
        if char == "\n":
            row, col = row + 1, 0
        else:
            cells[(row, col)] = (char, fg, bg, bold)
            col += 1
        i += 1
    return cells


def blended(colour: RGB, low: RGB, high: RGB) -> bool:
    """colour lies between low and high, at one place for all three channels (antialiasing)."""
    places = []
    for c, a, b in zip(colour, low, high):
        if abs(b - a) < 16:
            if abs(c - a) > 4 and abs(c - b) > 4:
                return False
            continue
        places.append((c - a) / (b - a))
    return all(-0.03 <= t <= 1.03 for t in places) and (not places or max(places) - min(places) <= 0.12)


def coverage(colour: RGB, low: RGB, high: RGB) -> float:
    """How far colour lies from low towards high, the most of the three channels."""
    return max((c - a) / (b - a) for c, a, b in zip(colour, low, high) if b != a)


def variant_for(cols: int, rows: int) -> str:
    """fuUI_LOGO_VARIANT of installer/lib/ui.sh."""
    if cols >= 120 and rows >= 49:
        return "120"
    if cols >= 80 and rows >= 33:
        return "80"
    return "80x24"


@unittest.skipUnless(ENABLED, "explicit only: TPOT_TEST_TERMINAL_RASTER=1 (Linux, regular user, Xvfb)")
class TerminalRasterTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        packages = subprocess.run(["bash", HELPER, "--packages"], capture_output=True, text=True).stdout.split()
        hint = ("sudo apt-get install --no-install-recommends "
                + " ".join(sorted(set(packages) | set(TEST_PACKAGES))))
        if platform.system() != "Linux":
            raise AssertionError("The terminal raster test runs on Linux only.")
        if os.geteuid() == 0:
            raise AssertionError("Run the terminal raster test as a regular user; the helper refuses root.")
        missing = [tool for tool in TOOLS if not shutil.which(tool)]
        ready = subprocess.run(["bash", HELPER, "--ready"], capture_output=True, text=True)
        if ready.returncode != 0:
            missing.append(ready.stdout.strip() or "a part of the graphical terminal")
        for glyph in GLYPHS:
            code = "%x" % ord(glyph[0])
            if subprocess.run(["fc-list", "-q", f":charset={code}"]).returncode != 0:
                missing.append(f"a font with {glyph} (U+{code.upper()})")
        if missing:
            raise AssertionError(f"Missing for the terminal raster test: {', '.join(missing)}. On Debian: {hint}")

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="tpot-raster-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.keep = os.environ.get("TPOT_TEST_TERMINAL_RASTER_OUT", "")

    # -- the X server ---------------------------------------------------------------------------------

    def start_xvfb(self, width: int, height: int) -> Dict[str, str]:
        display = next(n for n in range(40, 100)
                       if not os.path.exists(f"/tmp/.X11-unix/X{n}") and not os.path.exists(f"/tmp/.X{n}-lock"))
        auth = os.path.join(self.work, "Xauthority")
        subprocess.run(["xauth", "-q", "-f", auth, "source", "-"], check=True, capture_output=True,
                       input=f"add :{display} . {secrets.token_hex(16)}\n", text=True)
        # -noreset: xinit (startx) keeps a connection open the whole session, so the server never
        # resets under the helper; without it a reset would drop a client still connecting
        xvfb = subprocess.Popen(["Xvfb", f":{display}", "-auth", auth, "-nolisten", "tcp", "-noreset",
                                 "-screen", "0", f"{width}x{height}x24"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

        def stop():
            xvfb.terminate()
            xvfb.wait(10)
        self.addCleanup(stop)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "DISPLAY": f":{display}", "XAUTHORITY": auth}
        for _ in range(100):
            if subprocess.run(["xwininfo", "-root"], env=env, capture_output=True).returncode == 0:
                break
            time.sleep(0.1)
        else:
            self.fail(f"Xvfb :{display} did not start")
        refused = subprocess.run(["xwininfo", "-root"], env={**env, "XAUTHORITY": "/dev/null"}, capture_output=True)
        self.assertNotEqual(refused.returncode, 0, "the X server takes clients without its cookie")
        self.assertNotIn(6000 + display, self.listening(), "the X server listens on TCP")
        return env

    @staticmethod
    def listening() -> List[int]:
        ports = []
        for name in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(name, encoding="ascii") as handle:
                    lines = handle.readlines()[1:]
            except OSError:
                continue
            ports.extend(int(line.split()[1].rsplit(":", 1)[1], 16) for line in lines if line.split()[3] == "0A")
        return ports

    def capture(self, env: Dict[str, str], name: str) -> Raster:
        """The root window once two captures in a row are the same (the terminal has painted)."""
        before = b""
        for _ in range(100):
            raw = subprocess.run(["xwd", "-root", "-silent"], env=env, capture_output=True, check=True).stdout
            if raw == before:
                raster = Raster(raw)
                if self.keep:
                    os.makedirs(self.keep, exist_ok=True)
                    with open(os.path.join(self.keep, f"{name}.png"), "wb") as handle:
                        handle.write(raster.png())
                return raster
            before = raw
            time.sleep(0.15)
        self.fail(f"{name}: the screen did not settle")

    # -- the session ------------------------------------------------------------------------------------

    def session_env(self, xenv: Dict[str, str]) -> Dict[str, str]:
        home = os.path.join(self.work, "home")
        os.makedirs(home, exist_ok=True)
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or "tpot-test"
        # a login on a text console: no COLORTERM, no XDG folders of a desktop
        return {"HOME": home, "PATH": xenv["PATH"], "USER": user, "LOGNAME": user, "SHELL": "/bin/bash",
                "LANG": "C.UTF-8", "TERM": "linux", "DISPLAY": xenv["DISPLAY"], "XAUTHORITY": xenv["XAUTHORITY"]}

    def prepare_state(self, env: Dict[str, str], out: str) -> str:
        """The session folder as the helper makes it before startx: argv, the login's environment
        and the terminal's own preferences, by the helper's own functions."""
        script = os.path.join(self.work, "session.sh")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(SESSION)
        state = tempfile.mkdtemp(prefix="tpot-terminal.", dir=self.work)
        os.chmod(state, 0o700)
        with open(os.path.join(state, "argv"), "wb") as handle:
            handle.write(b"".join(arg.encode() + b"\0" for arg in ("bash", script, out, REPO)))
        prepare = ("set -e; eval \"$(sed -n '/^fuSAVE_ENV () {/,/^}/p; /^fuPREFERENCES () {/,/^}/p' \"$1\")\"; "
                   "fuSAVE_ENV > \"$2/env\"; fuPREFERENCES \"$2/config\"")
        subprocess.run(["bash", "-c", prepare, "_", HELPER, state], env=env, check=True)
        return state

    def wait_for(self, path: str, session: subprocess.Popen, seconds: float = 60) -> None:
        end = time.monotonic() + seconds
        while not os.path.exists(path):
            if session.poll() is not None:
                self.fail(f"the session ended (rc {session.returncode}) before {os.path.basename(path)}: "
                          + self.read(os.path.join(self.work, "helper.log")))
            if time.monotonic() > end:
                self.fail(f"no {os.path.basename(path)} within {seconds} s")
            time.sleep(0.05)

    @staticmethod
    def read(path: str) -> str:
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return ""

    @staticmethod
    def session_processes(sid: int, marker: bytes) -> Dict[int, str]:
        """The living processes of the session: its session id, or the marker in their environment
        (the command runs in a session of the pty, a daemon in one of its own)."""
        left = {}
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as handle:
                    stat = handle.read()
                fields = stat.rsplit(")", 1)[1].split()
                if fields[0] == "Z":
                    continue
                if int(fields[3]) != sid:
                    with open(f"/proc/{pid}/environ", "rb") as handle:
                        if marker not in handle.read().split(b"\0"):
                            continue
            except OSError:
                continue
            left[int(pid)] = stat.split(")", 1)[0] + ")"
        return left

    def run_session(self, width: int, height: int) -> Tuple[List[Raster], str, Dict[str, str]]:
        xenv = self.start_xvfb(width, height)
        env = self.session_env(xenv)
        out = os.path.join(self.work, "out")
        os.makedirs(out)
        with open(os.path.join(out, "layout.json"), "w", encoding="utf-8") as handle:
            json.dump(layout(), handle)
        state = self.prepare_state(env, out)
        token = secrets.token_hex(8)
        marker = f"TPOT_RASTER_SESSION={token}".encode()
        with open(os.path.join(self.work, "helper.log"), "w", encoding="utf-8") as log:
            session = subprocess.Popen(["bash", HELPER, "--session", state], env=dict(env, TPOT_RASTER_SESSION=token),
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)

        def stop():
            if session.poll() is None:
                try:
                    os.killpg(session.pid, 15)
                except ProcessLookupError:
                    pass
                session.wait(10)
            for pid in self.session_processes(session.pid, marker):
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
        self.addCleanup(stop)
        rasters = []
        for phase in range(3):
            self.wait_for(os.path.join(out, f"ready{phase}"), session)
            rasters.append(self.capture(xenv, f"{width}x{height}-phase{phase}"))
            open(os.path.join(out, f"go{phase}"), "w").close()
        try:
            rc = session.wait(30)
        except subprocess.TimeoutExpired:
            self.fail("the helper did not end after the command")
        log = self.read(os.path.join(self.work, "helper.log"))
        self.assertEqual(rc, 0, log)
        self.assertEqual(self.read(os.path.join(state, "result")), "0\n")
        # dbus-run-session ends its bus with SIGTERM and does not wait: the daemon may still be
        # exiting for a few milliseconds; a process that stays is left behind
        end = time.monotonic() + 5
        while self.session_processes(session.pid, marker) and time.monotonic() < end:
            time.sleep(0.02)
        self.assertEqual(list(self.session_processes(session.pid, marker).values()), [],
                         "processes of the session are left")
        self.assertNotIn("T-Pot terminal:", log)
        return rasters, out, env

    # -- the checks -------------------------------------------------------------------------------------

    def grid(self, raster: Raster, rows: int, cols: int) -> Tuple[int, int, int, int]:
        """Origin and cell size from the calibration: a magenta block in the first and the last cell,
        every other pixel INK."""
        found = raster.not_ink()
        self.assertTrue(found, "nothing painted")
        wrong = [p for p in found if raster.pixel(*p) != MAGENTA]
        self.assertEqual(wrong[:5], [], "other colours than the calibration blocks")
        first = [p for p in found if p[1] < raster.height // 2]
        last = [p for p in found if p[1] >= raster.height // 2]
        boxes = []
        for part in (first, last):
            xs, ys = [p[0] for p in part], [p[1] for p in part]
            box = (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
            self.assertEqual(len(part), box[2] * box[3], "a calibration block is not solid")
            boxes.append(box)
        (x0, y0, cw, ch), (x1, y1, cw1, ch1) = boxes
        self.assertEqual((cw, ch), (cw1, ch1))
        self.assertEqual(((x1 - x0) % cw, (y1 - y0) % ch), (0, 0))
        self.assertEqual(((y1 - y0) // ch + 1, (x1 - x0) // cw + 1), (rows, cols), "cells and stty size differ")
        self.assertLess(x1 + cw, raster.width + 1)
        self.assertLess(y1 + ch, raster.height + 1)
        return x0, y0, cw, ch

    def check_cells(self, raster: Raster, grid: Tuple[int, int, int, int], rows: int, cols: int,
                    cells: Dict[Tuple[int, int], Cell], name: str,
                    skip: Optional[set] = None) -> Dict[str, list]:
        """Every cell of the screen against the expected one (but those of skip); the rest of the
        screen INK."""
        x0, y0, cw, ch = grid
        self.assertTrue(raster.ink_free(0, 0, raster.width, y0) and raster.ink_free(0, 0, x0, raster.height))
        self.assertTrue(raster.ink_free(0, y0 + rows * ch, raster.width, raster.height - y0 - rows * ch))
        self.assertTrue(raster.ink_free(x0 + cols * cw, 0, raster.width - x0 - cols * cw, raster.height))
        splits: Dict[str, list] = {"▀": [], "▄": []}
        problems = []
        for row in range(rows):
            for col in range(cols):
                if skip and (row, col) in skip:
                    continue
                x, y = x0 + col * cw, y0 + row * ch
                char, fg, bg, _bold = cells.get((row, col), (" ", None, None, False))
                fg, bg = fg or GLASS, bg or INK
                if char == " " and bg == INK:
                    if not raster.ink_free(x, y, cw, ch):
                        problems.append(f"{row},{col}: blank cell has ink")
                    continue
                lines = [raster.row(x, line, cw) for line in range(y, y + ch)]
                colours = {colour for line in lines for colour in line}
                if char == " ":
                    ok = colours == {bg}
                elif char in "█":
                    ok = colours == {fg}
                elif char in "▀▄":
                    upper, lower = (fg, bg) if char == "▀" else (bg, fg)
                    tops = [k for k, line in enumerate(lines) if set(line) == {upper}]
                    ok = (bool(tops) and tops == list(range(len(tops)))
                          and all(set(line) == {lower} for line in lines[len(tops):]))
                    splits[char].append(len(tops))
                elif char in BOX:
                    ok = fg in colours and colours <= {fg, bg}
                else:
                    ok = colours != {bg} and all(blended(colour, bg, fg) for colour in colours)
                if not ok:
                    problems.append(f"{row},{col} {char!r} fg {fg} bg {bg}: {sorted(colours)[:6]}")
        self.assertEqual(problems[:10], [], f"{name}: {len(problems)} cells differ")
        for char, values in splits.items():
            self.assertLessEqual(len(set(values)), 1, f"{name}: {char} split at {sorted(set(values))}")
            if values:
                self.assertIn(values[0], (ch // 2, (ch + 1) // 2), f"{name}: {char} is no half block")
        return splits

    def check_logo(self, raster: Raster, grid, rows: int, cols: int, out: str, env: Dict[str, str]) -> None:
        variant = variant_for(cols, rows)
        self.assertEqual(self.read(os.path.join(out, "logo")), "0\n", "fuUI_LOGO showed no logo")
        render = subprocess.run(["bash", "-c", '. "$1"; fuUI_LOGO_RENDER "$2" truecolor "$(fuUI_VERSION)"',
                                 "_", UI_SH, variant], env=env, capture_output=True, text=True, check=True).stdout
        cells = parse_screen(render)
        used = {colour for cell in cells.values() for colour in cell[1:3] if colour}
        self.assertLessEqual(used, {rgb(c) for c in ui_logo.logo_colours("truecolor")})
        splits = self.check_cells(raster, grid, rows, cols, cells, f"logo {variant}")
        self.assertTrue(splits["▀"] and splits["▄"], "the logo has no half blocks")

    def check_colours_and_glyphs(self, raster: Raster, grid, rows: int, cols: int) -> None:
        x0, y0, cw, ch = grid
        cells: Dict[Tuple[int, int], Cell] = {}
        for row, col, text, layer, token, bold in layout():
            colour = rgb(TRUECOLOR[token]) if token else None
            for k, char in enumerate(text):
                cells[(row - 1, col - 1 + k)] = (char, colour if layer == "fg" else None,
                                                  colour if layer == "bg" else None, bold)
        glyph_cells = {key: cell for key, cell in cells.items() if cell[0] in GLYPHS}
        sides = {(row, col + side): side for row, col in glyph_cells for side in (-1, 1) if 0 <= col + side < cols}
        self.assertFalse(set(sides) & set(cells), "a glyph of the layout has no blank cell beside it")
        self.check_cells(raster, grid, rows, cols, cells, "colours", skip=set(glyph_cells) | set(sides))
        self.assertEqual(raster.pixel(x0, y0), GLASS, "plain text is not GLASS")
        # beside a glyph only the antialiasing of an outline on its cell edge: the one pixel column
        # next to it, less than half covered; a wider glyph (a proportional fallback) fails
        for (row, col), side in sides.items():
            x, y = x0 + col * cw, y0 + row * ch
            edge = cw - 1 if side < 0 else 0
            for j in range(ch):
                for i, colour in enumerate(raster.row(x, y + j, cw)):
                    if colour != INK and (i != edge or coverage(colour, INK, GLASS) >= 0.5):
                        self.fail(f"{row},{col - side}: the glyph {cells.get((row, col - side), ('?',))[0]!r} "
                                  f"spills into the cell beside it ({i},{j} {colour})")
        for (row, col), (char, _fg, _bg, bold) in glyph_cells.items():
            x, y = x0 + col * cw, y0 + row * ch
            ink = [(i, j) for j in range(ch) for i, colour in enumerate(raster.row(x, y + j, cw)) if colour != INK]
            self.assertTrue(ink, f"{char!r} (bold {bold}) has no ink")
            if char in HEXAGONS:
                left = min(i for i, _ in ink)
                right = cw - 1 - max(i for i, _ in ink)
                top = min(j for _, j in ink)
                self.assertLessEqual(abs(left - right), 1, f"{char!r} (bold {bold}) is cut: {left} / {right}")
                self.assertGreater(top, 0, f"{char!r} (bold {bold}) touches the top of its cell")

    def check_screen(self, width: int, height: int, variant: str) -> None:
        rasters, out, env = self.run_session(width, height)
        size = self.read(os.path.join(out, "size")).split()
        rows, cols = int(size[0]), int(size[1])
        self.assertEqual(self.read(os.path.join(out, "start-size")).split(), size,
                         "the command started before the terminal was fullscreen")
        self.assertEqual(variant_for(cols, rows), variant, f"{width}x{height} is {rows}x{cols} cells")
        self.assertEqual(self.read(os.path.join(out, "colors")), "truecolor\n", "fuUI_COLORS")
        self.assertEqual(self.read(os.path.join(out, "prefs")), "truecolor\n", "prefs.detect_colors")
        grid = self.grid(rasters[0], rows, cols)
        self.check_logo(rasters[1], grid, rows, cols, out, env)
        self.check_colours_and_glyphs(rasters[2], grid, rows, cols)

    def test_every_glyph_of_the_manager_has_a_monospaced_font(self):
        """A glyph only a proportional font has is drawn wider than its cell and runs into the next one."""
        for glyph in GLYPHS:
            code = "%x" % ord(glyph[0])
            with self.subTest(glyph=glyph):
                self.assertEqual(subprocess.run(["fc-list", "-q", f":spacing=mono:charset={code}"]).returncode, 0,
                                 f"{glyph} (U+{code.upper()}) is in no monospaced font of the terminal")

    def test_compact_console_shows_the_80x24_logo(self):
        self.check_screen(1024, 600, "80x24")

    def test_vm_console_shows_the_80_column_logo(self):
        self.check_screen(1280, 800, "80")

    def test_full_hd_console_shows_the_large_logo(self):
        self.check_screen(1920, 1080, "120")


if __name__ == "__main__":
    unittest.main()
