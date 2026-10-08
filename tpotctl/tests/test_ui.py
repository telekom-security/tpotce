"""The look of the T-Pot scripts (installer/lib/ui.sh and its copy in install.sh): the ANSI logo of
the T-Pot Manager rendered in bash, the wordmark, the colours, and the helpers for help, usage errors,
summaries, a choice of many and the spinner.

Every bash here runs with a temporary HOME and XDG folders and a clean environment: nothing reads the
tpot.json or .env of the user running the tests, nothing calls docker or downloads gum.
"""

import fcntl
import io
import itertools
import json
import os
import pty
import re
import select
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import splash_art, ui_logo  # noqa: E402

try:
    import rich  # noqa: F401
    import textual  # noqa: F401  the colours come from theme.py, which needs Textual as well
except ImportError:
    rich = None                   # i.e. the python3 of Debian has Rich (python3-rich) but no Textual

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_SH = os.path.join(REPO, "installer", "lib", "ui.sh")
INSTALL_SH = os.path.join(REPO, "install.sh")
BASH = shutil.which("bash")
VERSION = "24.04.2"


def _bash_major(bash):
    out = subprocess.run([bash, "-c", "echo ${BASH_VERSINFO[0]}"], stdout=subprocess.PIPE,
                         universal_newlines=True)
    return int(out.stdout) if out.stdout.strip().isdigit() else 0


# a bash 3 (/bin/bash of macOS): what a script runs of ui.sh before fuUI_LINUX_ONLY stops it there
# (fuUI_INIT, fuUI_COLORS, fuUI_HELP, fuUI_USAGE_ERROR, fuUI_LINUX_ONLY) works with it too
OLD_BASH = next((b for b in ("/bin/bash", BASH) if b and os.path.exists(b) and _bash_major(b) == 3), None)
BASHES = tuple(b for b in (BASH, OLD_BASH) if b)
# the locale of the tests, and the C locale of a host without a UTF-8 one (LC_ALL wins over LANG)
LOCALES = ({}, {"LC_ALL": "C"})

SGR = re.compile(r"\x1b\[([0-9;]*)m")
# the 16 colour SGR codes as the VGA colours of splash_anim.colours("16")
VGA = {30: "#000000", 31: "#aa0000", 32: "#00aa00", 33: "#aa5500", 34: "#0000aa", 35: "#aa00aa",
       36: "#00aaaa", 37: "#aaaaaa", 90: "#555555", 91: "#ff5555", 92: "#55ff55", 93: "#ffff55",
       94: "#5555ff", 95: "#ff55ff", 96: "#55ffff", 97: "#ffffff"}


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def xterm(number):
    """The colour of an entry of the xterm 256 palette, as #rrggbb."""
    if number < 16:
        return VGA[(30 + number) if number < 8 else (90 + number - 8)]
    if number < 232:
        number -= 16
        steps = [0, 95, 135, 175, 215, 255]
        r, g, b = steps[number // 36], steps[number // 6 % 6], steps[number % 6]
    else:
        r = g = b = 8 + (number - 232) * 10
    return f"#{r:02x}{g:02x}{b:02x}"


def bash_array(text, name):
    """The elements of a bash array assignment name=(...) in text (quoted or bare words)."""
    body = re.search(rf"^{name}=\((.*?)\)", text, re.M | re.S).group(1)
    return [a or b or c for a, b, c in re.findall(r"'([^']*)'|\"([^\"]*)\"|([^\s'\"]+)", body)]


def cells(text):
    """Rows of (character, foreground, background) from text with SGR sequences; a colour is
    #rrggbb (true colour) or 256:n / 16:n, None for the terminal's own."""
    rows = []
    fg = bg = None
    for line in text.split("\n"):
        row = []
        pos = 0
        for match in SGR.finditer(line + "\x1b[m"):
            row.extend((char, fg, bg) for char in line[pos:match.start()])
            pos = match.end()
            codes = [int(c) for c in match.group(1).split(";") if c] or [0]
            i = 0
            while i < len(codes):
                code = codes[i]
                if code == 0:
                    fg = bg = None
                elif code in (38, 48) and codes[i + 1] == 2:
                    value = "#{:02x}{:02x}{:02x}".format(*codes[i + 2:i + 5])
                    fg, bg = (value, bg) if code == 38 else (fg, value)
                    i += 4
                elif code in (38, 48) and codes[i + 1] == 5:
                    value = f"256:{codes[i + 2]}"
                    fg, bg = (value, bg) if code == 38 else (fg, value)
                    i += 2
                elif 30 <= code <= 37 or 90 <= code <= 97:
                    fg = f"16:{code}"
                elif 40 <= code <= 47 or 100 <= code <= 107:
                    bg = f"16:{code - 10}"
                i += 1
        rows.append(row)
    return rows


def plain(text):
    return SGR.sub("", text)


class Sandbox:
    """A temporary HOME with XDG folders, and a clean environment for bash."""

    def __init__(self, case):
        self.home = tempfile.mkdtemp(prefix="tpot-ui-")
        case.addCleanup(shutil.rmtree, self.home, True)
        self.config = os.path.join(self.home, "config")
        os.makedirs(os.path.join(self.config, "tpotce"))

    def env(self, **extra):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home,
               "XDG_CONFIG_HOME": self.config, "XDG_DATA_HOME": os.path.join(self.home, "data"),
               "TERM": "xterm-256color", "LANG": os.environ.get("LANG", "en_US.UTF-8")}
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def prefs(self, text):
        """The tpot.json as it is; a lone surrogate \\udcXX is the byte XX (surrogateescape): a file
        that is no UTF-8."""
        with open(os.path.join(self.config, "tpotce", "tpot.json"), "w", encoding="utf-8", newline="",
                  errors="surrogateescape") as out:
            out.write(text)

    def checkout(self, version=None, env=None):
        path = tempfile.mkdtemp(prefix="checkout-", dir=self.home)
        if version is not None:
            with open(os.path.join(path, "version"), "w", encoding="utf-8") as out:
                out.write(version)
        if env is not None:
            with open(os.path.join(path, ".env"), "w", encoding="utf-8") as out:
                out.write(env)
        return path


def run(script, env, source=UI_SH, stdin=None, timeout=20, bash=None):
    return subprocess.run([bash or BASH, "-c", f'source "{source}"\n{script}'], env=env, input=stdin,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          timeout=timeout)


def at_terminal(script, env, cols=120, rows=49, source=UI_SH, stdout_tty=True, timeout=20, keys=None,
                bash=None):
    """Runs the script in a pty of cols x rows (stdin and stderr, stdout too unless stdout_tty is
    False: then a pipe); the output of the pty with the line ends of the script. keys are typed
    into the pty after a second (for a real gum). bash: another bash than the one of PATH."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    attrs = termios.tcgetattr(slave)
    attrs[1] &= ~termios.ONLCR                       # \n stays \n, so a CR of the script shows
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    proc = subprocess.Popen([bash or BASH, "-c", f'source "{source}"\n{script}'], env=env, stdin=slave,
                            stdout=slave if stdout_tty else subprocess.PIPE, stderr=slave, close_fds=True)
    os.close(slave)
    out = b""
    deadline = time.time() + timeout
    typed_at = time.time() + 1.0
    while time.time() < deadline:
        if keys and time.time() >= typed_at:
            os.write(master, keys)
            keys = None
        ready, _w, _x = select.select([master], [], [], 0.1)
        if ready:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            out += data
        elif proc.poll() is not None:
            # drained and done
            ready, _w, _x = select.select([master], [], [], 0.2)
            if not ready:
                break
    proc.wait(timeout=5)
    os.close(master)
    piped = ""
    if proc.stdout:
        piped = proc.stdout.read().decode("utf-8", "replace")
        proc.stdout.close()
    return out.decode("utf-8", "replace") + piped


def selected(args):
    """The values of every --selected in an argv."""
    return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "--selected"]


def logo_pixels(rows, width, palette):
    """The (top, bottom) pixel indices of rendered rows, a colour mapped back to its index."""
    index = {colour: i for i, colour in enumerate(palette)}
    out = []
    for row in rows:
        pixels = []
        for char, fg, bg in row:
            if char == " ":
                pixels.append((index[bg], index[bg]) if bg else (0, 0))
            elif char == "▀":
                pixels.append((index[fg], index[bg] if bg else 0))
            elif char == "▄":
                pixels.append((0, index[fg]))
            else:
                pixels.append(None)                     # text (the credits)
        out.append(pixels + [(0, 0)] * (width - len(pixels)))
    return out


def grid_pairs(variant):
    width, height, base = splash_art.grid(variant)
    return [[(base[2 * r * width + x], base[(2 * r + 1) * width + x]) for x in range(width)]
            for r in range(height // 2)]


def truecolor_palette():
    return ["#{:02x}{:02x}{:02x}".format(*map(int, value.split(";")))
            for value in bash_array(read(UI_SH), "myUI_LOGO_RGB")]


@unittest.skipUnless(BASH, "no bash")
class LogoDataTest(unittest.TestCase):

    def test_embedded_data_decodes_to_splash_art(self):
        for path in (UI_SH, INSTALL_SH):
            text = read(path)
            for variant in ui_logo.VARIANTS:
                with self.subTest(path=os.path.basename(path), variant=variant):
                    decoded = ui_logo.decode(text, variant)
                    self.assertEqual(decoded, grid_pairs(variant))

    def test_logo_data_is_generated(self):
        self.assertEqual(ui_logo.check(), [], "run python3 -m tpotctl.ui_logo")
        for path in (UI_SH, INSTALL_SH):
            text = read(path)
            self.assertEqual(ui_logo.data_block(text), ui_logo.generate(), path)
        result = subprocess.run([sys.executable, "-m", "tpotctl.ui_logo", "--check"], cwd=REPO,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_check_finds_a_changed_copy(self):
        folder = tempfile.mkdtemp(prefix="tpot-ui-check-")
        self.addCleanup(shutil.rmtree, folder)
        copy = os.path.join(folder, "ui.sh")
        text = read(UI_SH)
        block = ui_logo.data_block(text)
        with open(copy, "w", encoding="utf-8") as out:
            out.write(text.replace(block, block.replace("myUI_LOGO_PAIRS='00", "myUI_LOGO_PAIRS='00 00")))
        self.assertEqual(ui_logo.check([copy]), [copy])
        ui_logo.write([copy])
        self.assertEqual(ui_logo.check([copy]), [])
        self.assertEqual(read(copy), text)

    def test_budget_and_charset(self):
        alphabet = ui_logo.ALPHABET
        self.assertEqual(len(alphabet), 87)
        self.assertEqual(len(set(alphabet)), 87)
        self.assertFalse(set(alphabet) & set("'\"\\$`[] "), alphabet)
        self.assertTrue(all(0x21 <= ord(c) <= 0x7e for c in alphabet))
        table = ui_logo.pair_table()
        self.assertEqual(table[0], (0, 0))
        self.assertLessEqual(len(table), 87)
        for path in (UI_SH, INSTALL_SH):
            block = ui_logo.data_block(read(path))
            self.assertLessEqual(len(block.encode("utf-8")), ui_logo.BUDGET, path)
            for variant in ui_logo.VARIANTS:
                for row in ui_logo.bash_strings(block, ui_logo.array_name(variant)):
                    self.assertEqual(len(row) % 2, 0)
                    self.assertTrue(set(row) <= set(alphabet), row)
                    if row:                                         # no run of empty cells at the end
                        self.assertNotEqual(row[-2], alphabet[0], row)

    def test_budget_leaves_room(self):
        """The budget is a guard against a template far larger than the logo of today, not a wall it
        stands at: the data of today takes 80 % of it at most (a template a little richer still fits;
        one much larger fails here first, then raise the budget or change the format)."""
        size = len(ui_logo.generate().encode("utf-8"))
        self.assertLessEqual(size, 0.8 * ui_logo.BUDGET, (size, ui_logo.BUDGET))
        # the generator stops at the budget, before it writes
        folder = tempfile.mkdtemp(prefix="tpot-ui-budget-")
        self.addCleanup(shutil.rmtree, folder)
        copy = os.path.join(folder, "ui.sh")
        with open(copy, "w", encoding="utf-8") as out:
            out.write(read(UI_SH))
        old = ui_logo.BUDGET
        try:
            ui_logo.BUDGET = size - 1
            with self.assertRaises(ValueError):
                ui_logo.write([copy])
        finally:
            ui_logo.BUDGET = old

    def test_too_many_pairs_fail_loudly(self):
        many = [[(a, b) for a in range(10) for b in range(10)]]
        with self.assertRaises(ValueError):
            ui_logo.pair_table([many])

    def test_bash_renders_the_grid(self):
        sandbox = Sandbox(self)
        palette = truecolor_palette()
        for variant in ui_logo.VARIANTS:
            with self.subTest(variant=variant):
                result = run(f"fuUI_LOGO_RENDER {variant} truecolor {VERSION}", sandbox.env())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("\r", result.stdout)
                lines = result.stdout.split("\n")
                self.assertEqual(lines[-1], "")
                for line in lines[:-1]:
                    self.assertTrue(line.endswith("\x1b[0m"), repr(line[-20:]))
                expected = grid_pairs(variant)
                width = len(expected[0])
                rendered = logo_pixels(cells(result.stdout)[:len(expected)], width, palette)
                for y, (got, want) in enumerate(zip(rendered, expected)):
                    for x, (a, b) in enumerate(zip(got, want)):
                        if a is not None:                       # under the credits of 80x24
                            self.assertEqual(a, b, (variant, x, y))

    def test_render_is_the_same_in_the_c_locale(self):
        sandbox = Sandbox(self)
        one = run(f"fuUI_LOGO_RENDER 80 256 {VERSION}", sandbox.env(LANG="en_US.UTF-8")).stdout
        two = run(f"fuUI_LOGO_RENDER 80 256 {VERSION}", sandbox.env(LC_ALL="C", LANG="C")).stdout
        self.assertEqual(one, two)
        self.assertIn("telekom security", one)

    def test_render_speed(self):
        sandbox = Sandbox(self)
        start = time.time()
        result = run(f"fuUI_LOGO_RENDER 120 truecolor {VERSION} >/dev/null", sandbox.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.time() - start, 1.5)

    def test_unknown_variant_or_mode(self):
        sandbox = Sandbox(self)
        self.assertEqual(run("fuUI_LOGO_RENDER 100 truecolor", sandbox.env()).returncode, 1)
        self.assertEqual(run("fuUI_LOGO_RENDER 80 sepia", sandbox.env()).returncode, 1)


@unittest.skipUnless(BASH, "no bash")
class ColoursTest(unittest.TestCase):

    def test_reduced_palettes(self):
        """No colour of the 256 or 16 tables is a red (the maroon an SSH session shows)."""
        text = read(UI_SH)
        for number in map(int, bash_array(text, "myUI_LOGO_256")):
            rgb = xterm(number)
            r, g, b = int(rgb[1:3], 16), int(rgb[3:5], 16), int(rgb[5:7], 16)
            self.assertFalse(r > 0 and g == 0 and b == 0, number)
        for code in map(int, bash_array(text, "myUI_LOGO_16")):
            self.assertNotIn(code, (31, 91))
        self.assertEqual(bash_array(text, "myUI_LOGO_256"),
                         "16 53 89 125 162 205 218 231 240 248".split())
        self.assertEqual(bash_array(text, "myUI_LOGO_16"), "30 35 35 35 95 95 97 97 90 37".split())

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_colours_match_the_manager(self):
        from tpotctl import splash_anim
        text = read(UI_SH)
        self.assertEqual(truecolor_palette(), [c.lower() for c in splash_anim.colours("truecolor")])
        self.assertEqual([xterm(int(n)) for n in bash_array(text, "myUI_LOGO_256")],
                         [c.lower() for c in splash_anim.colours("256")])
        self.assertEqual([VGA[int(n)] for n in bash_array(text, "myUI_LOGO_16")],
                         [c.lower() for c in splash_anim.colours("16")])

    def test_modes_paint_with_their_table(self):
        sandbox = Sandbox(self)
        out = {mode: cells(run(f"fuUI_LOGO_RENDER 80 {mode}", sandbox.env()).stdout)
               for mode in ("truecolor", "256", "16")}
        colours = {mode: {c for row in rows for cell in row for c in cell[1:] if c} for mode, rows in out.items()}
        self.assertTrue(all(c.startswith("#") for c in colours["truecolor"]))
        self.assertTrue(all(c.startswith("256:") for c in colours["256"]))
        self.assertTrue(all(c.startswith("16:") for c in colours["16"]))
        self.assertTrue({c.split(":")[1] for c in colours["16"]} <= {"30", "35", "37", "90", "95", "97"})

    def test_colour_detection(self):
        sandbox = Sandbox(self)

        def colours(prefs=None, **env):
            if prefs is None:
                path = os.path.join(sandbox.config, "tpotce", "tpot.json")
                if os.path.exists(path):
                    os.remove(path)
            else:
                sandbox.prefs(prefs)
            return run("fuUI_COLORS", sandbox.env(**env)).stdout.strip()

        self.assertEqual(colours(TERM="xterm"), "16")
        self.assertEqual(colours(TERM="xterm-256color"), "256")
        self.assertEqual(colours(TERM="xterm-kitty"), "truecolor")
        self.assertEqual(colours(TERM="screen.xterm-256color"), "256")
        self.assertEqual(colours(TERM="xterm", COLORTERM="truecolor"), "truecolor")
        self.assertEqual(colours(TERM="xterm", COLORTERM="24bit"), "truecolor")
        self.assertEqual(colours(TERM=None), "16")
        # tpot.json of the T-Pot Manager over the terminal, unless auto
        self.assertEqual(colours('{"icons": "unicode", "colors": "256"}', COLORTERM="truecolor"), "256")
        self.assertEqual(colours('{\n  "colors": "16"\n}\n', COLORTERM="truecolor"), "16")
        self.assertEqual(colours('{"colors": "auto"}', TERM="xterm-256color"), "256")
        self.assertEqual(colours('{"colors": "sepia"}', TERM="xterm"), "16")
        self.assertEqual(colours("not json", TERM="xterm-256color"), "256")
        # TPOT_COLORS over the file, as prefs.load(); auto there means the terminal decides
        self.assertEqual(colours('{"colors": "256"}', TPOT_COLORS="16", COLORTERM="truecolor"), "16")
        self.assertEqual(colours('{"colors": "16"}', TPOT_COLORS="truecolor", TERM="xterm"), "truecolor")
        self.assertEqual(colours('{"colors": "16"}', TPOT_COLORS="auto", COLORTERM="truecolor"), "truecolor")
        self.assertEqual(colours('{"colors": "16"}', TPOT_COLORS="bogus", COLORTERM="truecolor"), "16")

    def test_colour_detection_cases(self):
        """The rule of the colours, the same for fuUI_COLORS (bash) and the T-Pot Manager (Python),
        against the shared cases of tpotctl/tests/color_cases.json.

        The file is a JSON list of cases {"env": {...}, "expect": "truecolor" | "256" | "16",
        "dumb": true | false, "note": "...", "prefs": {...} | "prefs_text": "..."}: env is the whole
        environment that counts,
        every key not in it is unset (TERM too); prefs, when there, is the tpot.json of the T-Pot
        Manager (written as JSON), prefs_text a tpot.json as it is (not always JSON); without them there
        is no tpot.json. A tpot.json counts when it is one JSON object of strings without escapes and
        control characters (what the T-Pot Manager writes), a key twice: the last. The rule, first
        match wins:
          1. TPOT_COLORS truecolor / 256 / 16 (auto: on with 3., tpot.json does not count)
          2. "colors" of tpot.json truecolor / 256 / 16
          3. TERM (any case) dumb or unknown                                        -> 16
          4. not in GNU screen (STY set, or TERM begins with screen without TMUX and TERM_PROGRAM
             tmux): COLORTERM truecolor or 24bit (any case), or TERM (any case) ends in -direct, or is
             xterm-kitty, xterm-ghostty, alacritty, foot*, wezterm, contour or rio -> truecolor
          5. not in tmux or screen (TMUX set, GNU screen, or TERM begins with screen or tmux):
             TERM_PROGRAM iTerm.app, WezTerm, vscode, ghostty, Hyper, Tabby, rio or WarpTerminal, or
             LC_TERMINAL iTerm2, or VTE_VERSION a number of 3600 or more, or KONSOLE_VERSION or
             WT_SESSION not empty                                                   -> truecolor
          6. TERM ends in 256color, or (not in tmux or screen) TERM_PROGRAM Apple_Terminal -> 256
          7. anything else                                                          -> 16
        dumb, when there, says whether the terminal shows no colours at all (fuUI_DUMB, prefs.dumb_terminal):
        TERM (any case) dumb or unknown, an empty TERM or none (bash makes an unset TERM dumb itself; a
        console of Windows has no TERM and is not dumb, the scripts do not run there); NO_COLOR is no dumb
        terminal (the colours go, bold stays). The depth stays the one of the rule there.
        Any case means A-Z only. A lone surrogate \\udcXX in prefs_text is the byte XX (surrogateescape):
        a tpot.json that is no UTF-8 does not count. Every bash there is: 4 or newer and 3 (/bin/bash
        of macOS), each in the locale of the tests and in the C locale (a host without a UTF-8 locale,
        LC_ALL=C): the grammar of tpot.json is the same in every locale.
        """
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "color_cases.json"),
                  encoding="utf-8") as handle:
            cases = json.load(handle)
        self.assertGreaterEqual(len(cases), 25)
        sandbox = Sandbox(self)
        prefs = os.path.join(sandbox.config, "tpotce", "tpot.json")
        for case in cases:
            self.assertEqual(set(case) - {"env", "expect", "dumb", "note", "prefs", "prefs_text"}, set(), case)
            self.assertIn(case.get("dumb", False), (True, False), case)
            self.assertFalse({"prefs", "prefs_text"} <= set(case), case)
            self.assertIn(case["expect"], ("truecolor", "256", "16"), case)
            for bash, locale in itertools.product(BASHES, LOCALES):
                with self.subTest(note=case["note"], bash=bash, locale=locale):
                    if "prefs_text" in case:
                        sandbox.prefs(case["prefs_text"])
                    elif "prefs" in case:
                        sandbox.prefs(json.dumps(case["prefs"]))
                    elif os.path.exists(prefs):
                        os.remove(prefs)
                    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": sandbox.home,
                           "XDG_CONFIG_HOME": sandbox.config, "LANG": os.environ.get("LANG", "en_US.UTF-8")}
                    env.update(locale)
                    env.update(case["env"])
                    # bash sets TERM=dumb (not exported) when it starts without one: unset is unset
                    result = run(("" if "TERM" in case["env"] else "unset TERM; ")
                                 + "fuUI_COLORS; fuUI_DUMB && echo dumb || echo terminal", env, bash=bash)
                    depth, dumb = (result.stdout.split() + ["", ""])[:2]
                    self.assertEqual((depth, result.stderr), (case["expect"], ""), case["env"])
                    if "dumb" in case:
                        self.assertEqual(dumb, "dumb" if case["dumb"] else "terminal", case["env"])

    def test_init_tells_gum_of_true_colour(self):
        """fuUI_INIT exports COLORTERM=truecolor when the rule says truecolor and COLORTERM is empty,
        so gum (lipgloss) and what the script starts (i.e. tpot) take the same colours."""
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        shutil.move(fake_gum(sandbox.home), os.path.join(folder, "gum"))
        record = os.path.join(sandbox.home, "gum.env")
        script = 'fuUI_INIT; fuUI_INFO "hello"; bash -c \'echo "child=${COLORTERM-(unset)}"\''
        # the child, and gum (its COLORTERM is the one of the rule, empty for 256 and 16)
        cases = (
            ({"LC_TERMINAL": "iTerm2"}, "truecolor", "truecolor"),
            ({"TERM": "xterm-kitty"}, "truecolor", "truecolor"),
            ({"TPOT_COLORS": "truecolor", "TERM": "xterm"}, "truecolor", "truecolor"),
            ({}, "(unset)", ""),                                          # TERM xterm-256color: 256
            ({"COLORTERM": "24bit"}, "24bit", "24bit"),                   # one of its own stays
            ({"LC_TERMINAL": "iTerm2", "TMUX": "/tmp/tmux-1/default,1,0"}, "(unset)", ""),
        )
        for extra, want, gum in cases:
            with self.subTest(env=extra):
                if os.path.exists(record):
                    os.remove(record)
                out = at_terminal(script, sandbox.env(**extra), 100, 30)
                self.assertIn(f"child={want}", out)
                calls = read(record).split("\n")[1:-1]                   # after gum --version of fuUI_INIT
                self.assertEqual(calls, [f"COLORTERM={gum}"] * 2)         # INFO (sign and text)
                self.assertTrue(read(record))                             # gum ran (fuUI_INFO)
        # a dumb, unknown or empty TERM paints nothing: no true colour is handed to the children either
        for term in ("", "dumb", "unknown"):
            with self.subTest(TERM=term):
                out = at_terminal(script, sandbox.env(TERM=term, LC_TERMINAL="iTerm2"), 100, 30)
                self.assertIn("child=(unset)", out)
        # without a terminal or with TPOT_GUM=off there is no gum: nothing is exported
        result = run(script, sandbox.env(LC_TERMINAL="iTerm2"))
        self.assertIn("child=(unset)", result.stdout)
        out = at_terminal(script, sandbox.env(LC_TERMINAL="iTerm2", TPOT_GUM="off"), 100, 30)
        self.assertIn("child=(unset)", out)

    # what gum is given (COLORTERM, TERM) for the depth of the rule: lipgloss takes true colour from any
    # COLORTERM, 256 from a TERM with 256color, 16 from xterm (a dumb TERM: no colours at all)
    GUM_ENV = (
        ({"TPOT_COLORS": "256", "COLORTERM": "truecolor", "TERM": "xterm"}, "", "xterm-256color"),
        ({"TPOT_COLORS": "256", "TERM": "xterm-kitty"}, "", "xterm-256color"),
        ({"TPOT_COLORS": "256", "TERM": "tmux-256color"}, "", "tmux-256color"),
        ({"TPOT_COLORS": "16", "COLORTERM": "truecolor", "TERM": "xterm-256color"}, "", "xterm"),
        ({"TPOT_COLORS": "16", "TERM": "screen"}, "", "xterm"),
        ({"TPOT_COLORS": "16", "TERM": "linux"}, "", "linux"),
        ({"COLORTERM": "truecolor", "STY": "1.pts-0.host", "TERM": "xterm-256color"}, "", "xterm-256color"),
        ({"COLORTERM": "truecolor", "TERM": "xterm"}, "truecolor", "xterm"),
        ({"LC_TERMINAL": "iTerm2", "TERM": "xterm-256color"}, "truecolor", "xterm-256color"),
        ({"COLORTERM": "24bit", "TERM": "xterm-256color"}, "24bit", "xterm-256color"),
        ({"TPOT_COLORS": "truecolor", "COLORTERM": "yes", "TERM": "xterm"}, "truecolor", "xterm"),
        ({"COLORTERM": "truecolor", "TERM": "dumb"}, "", "dumb"),
    )

    def test_gum_paints_in_the_colours_of_the_rule(self):
        """COLORTERM=truecolor of the terminal, but TPOT_COLORS / tpot.json / GNU screen say 256 or 16:
        gum gets the depth of the rule (only gum: what the script starts keeps its environment)."""
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        shutil.move(fake_gum(sandbox.home), os.path.join(folder, "gum"))
        record = os.path.join(sandbox.home, "gum.term")
        script = ('fuUI_INIT; fuUI_INFO "hello"; fuUI_WARN "careful"; fuUI_PAINT "#E20074" x >/dev/null; '
                  'echo "after=${COLORTERM-(unset)} ${TERM}"')
        cases = self.GUM_ENV + (({"COLORTERM": "truecolor", "TERM": "xterm-256color"}, "", "xterm-256color"),)
        for i, (extra, colorterm, term) in enumerate(cases):
            with self.subTest(env=extra):
                if os.path.exists(record):
                    os.remove(record)
                if i == len(cases) - 1:
                    sandbox.prefs('{"colors": "256"}')                     # tpot.json says 256
                out = at_terminal(script, sandbox.env(**extra), 100, 30)
                calls = read(record).split("\n")[:-1]
                self.assertEqual(len(calls), 5, calls)                     # --version, INFO (2), WARN, PAINT
                self.assertEqual(calls[1:], [f"COLORTERM={colorterm} TERM={term}"] * 4)
                own = extra.get("COLORTERM", "truecolor" if colorterm == "truecolor" else "(unset)")
                self.assertIn(f"after={own} {extra['TERM']}", out)

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_real_gum_paints_in_the_colours_of_the_rule(self):
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        os.symlink(shutil.which("gum"), os.path.join(folder, "gum"))
        script = 'fuUI_INIT; [ -n "${myUI_GUM}" ] && fuUI_WARN "careful"'
        for extra, sgr in (({"TPOT_COLORS": "256", "COLORTERM": "truecolor", "TERM": "xterm"}, "38;5;"),
                           ({"TPOT_COLORS": "16", "COLORTERM": "truecolor", "TERM": "xterm-256color"}, "\x1b[9"),
                           ({"COLORTERM": "truecolor", "STY": "1.pts-0.host", "TERM": "xterm"}, "\x1b[9"),
                           ({"LC_TERMINAL": "iTerm2", "TERM": "xterm-256color"}, "38;2;")):
            with self.subTest(env=extra):
                out = at_terminal(script, sandbox.env(**extra), 100, 30)
                self.assertIn("careful", out)
                self.assertIn(sgr, out)
                for other in {"38;5;", "\x1b[9", "38;2;"} - {sgr}:
                    self.assertNotIn(other, out)

    # the colours of tpot for gum by the depth of the rule: the hex values (true colour), the entries of
    # theme.PALETTE_256 and the ANSI colours of theme.PALETTE_16 as gum takes them (an index)
    GUM_COLOURS = {"truecolor": {"magenta": "#E20074", "glass": "#ECEFF9", "ash": "#A2A2AD", "warn": "#F4B400",
                                 "error": "#E8453C", "ok": "#3FA34D", "petrol": "#014463"},
                   "256": {"magenta": "162", "glass": "231", "ash": "248", "warn": "214", "error": "167",
                           "ok": "71", "petrol": "23"},
                   "16": {"magenta": "13", "glass": "15", "ash": "7", "warn": "11", "error": "9", "ok": "10",
                          "petrol": "6"}}

    def test_gum_gets_the_palette_of_the_rule(self):
        """With 256 or 16 colours gum gets the entries of the palettes of the T-Pot Manager, not the hex
        values (lipgloss would take the nearest: magenta and the yellow of a warning turn bright red
        with 16); only the values of colour options, a text stays as it is."""
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        shutil.move(fake_gum(sandbox.home), os.path.join(folder, "gum"))
        calls = os.path.join(sandbox.home, "gum.calls")
        script = ('fuUI_INIT; fuUI_INFO "#E20074"; fuUI_WARN "careful"; fuUI_ERROR "bad"; fuUI_HINT "a hint"; '
                  'fuUI_OK "fine"; fuUI_CONFIRM "Go?"')
        for extra, depth in (({"TERM": "xterm"}, "16"), ({"TERM": "xterm-256color"}, "256"),
                             ({"TERM": "xterm-256color", "COLORTERM": "truecolor"}, "truecolor"),
                             ({"TERM": "xterm", "TPOT_COLORS": "256"}, "256")):
            with self.subTest(env=extra):
                if os.path.exists(calls):
                    os.remove(calls)
                at_terminal(script, sandbox.env(**extra), 100, 30)
                blocks = [block.split("\n")[:-1] for block in read(calls).split("--- call\n")[2:]]
                colours = {}
                for args in blocks:
                    for i, arg in enumerate(args[:args.index("--")]):
                        if arg.endswith(("foreground", "background")):
                            colours.setdefault(args[i + 1], []).append(arg)
                want = self.GUM_COLOURS[depth]
                self.assertEqual(set(colours), {want[name] for name in
                                                ("magenta", "glass", "ash", "warn", "error", "ok", "petrol")})
                self.assertEqual(blocks[1][blocks[1].index("--") + 1:], ["#E20074"])   # the text of INFO

    def test_texts_after_the_double_dash_stay(self):
        """Only the colour options before -- get the entries of the palette: texts after it that look
        like a colour option and its value stay as they are (fuUI_GUM forgets the table at --)."""
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        shutil.move(fake_gum(sandbox.home), os.path.join(folder, "gum"))
        calls = os.path.join(sandbox.home, "gum.calls")
        texts = ["--foreground", "#E20074", "--border-foreground", "#014463", "--background", "#3FA34D"]
        script = 'fuUI_INIT; fuUI_BANNER "T" ' + " ".join(f'"{text}"' for text in texts)
        for extra in ({"TERM": "xterm"}, {"TERM": "xterm-256color"}):
            with self.subTest(env=extra):
                if os.path.exists(calls):
                    os.remove(calls)
                at_terminal(script, sandbox.env(**extra), 100, 30)
                blocks = [block.split("\n")[:-1] for block in read(calls).split("--- call\n")[2:]]
                self.assertEqual(blocks[-1][blocks[-1].index("--") + 1:], texts)
                self.assertEqual(blocks[-1][blocks[-1].index("--foreground") + 1],
                                 self.GUM_COLOURS["16" if extra["TERM"] == "xterm" else "256"]["ash"])

    def test_gum_colour_tables_are_the_palettes_of_the_manager(self):
        ui = read(UI_SH)
        palettes = ui_logo.theme_palettes()
        for depth, table in (("256", "myUI_GUM_256"), ("16", "myUI_GUM_16")):
            pairs = dict(item.split("=") for item in re.search(rf'^{table}="([^"]*)"', ui, re.M).group(1).split())
            with self.subTest(depth=depth):
                self.assertEqual(set(pairs), set(self.GUM_COLOURS["truecolor"].values()))
                for name, colour in self.GUM_COLOURS["truecolor"].items():
                    entry = palettes[depth][name.upper()]
                    index = ui_logo.xterm_index(entry) if depth == "256" else ui_logo.sgr16(entry)
                    if depth == "16":
                        index = index - 30 if index < 90 else index - 82
                    self.assertEqual(pairs[colour], str(index), name)
                    self.assertEqual(self.GUM_COLOURS[depth][name], str(index), name)

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_real_gum_keeps_magenta_with_16_colours(self):
        """The real gum 2.0.2 at TERM=xterm (PuTTY): magenta is bright magenta (95) and a warning
        yellow (93), nothing turns bright red (91) but an error; 256 colours: the entries of the
        palette (glass 231)."""
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        os.symlink(shutil.which("gum"), os.path.join(folder, "gum"))
        script = 'fuUI_INIT; [ -n "${myUI_GUM}" ] && fuUI_INFO "hello" && fuUI_WARN "careful" && fuUI_HINT "x"'
        out = at_terminal(script, sandbox.env(TERM="xterm"), 100, 30)
        self.assertIn("hello", out)
        for sgr in ("\x1b[95m", "\x1b[97m", "\x1b[93m", "\x1b[37m"):
            self.assertIn(sgr, out)
        self.assertNotIn("91m", out)
        out = at_terminal(script, sandbox.env(TERM="xterm-256color"), 100, 30)
        for sgr in ("38;5;162", "38;5;231", "38;5;214", "38;5;248"):
            self.assertIn(sgr, out)
        self.assertNotIn("38;5;255", out)
        out = at_terminal('fuUI_INIT; fuUI_ERROR "bad"', sandbox.env(TERM="xterm"), 100, 30)
        self.assertIn("91m", out)

    def test_gum_follows_no_color(self):
        """NO_COLOR counts when it is not empty (no-color.org, Rich, say): gum gets NO_COLOR=1 then
        (gum itself only takes 1 or true), an empty one stays."""
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        shutil.move(fake_gum(sandbox.home), os.path.join(folder, "gum"))
        record = os.path.join(sandbox.home, "gum.nocolor")
        for value, want in ((None, "(unset)"), ("", ""), ("1", "1"), ("yes", "1"), ("0", "1"), ("false", "1")):
            with self.subTest(NO_COLOR=value):
                if os.path.exists(record):
                    os.remove(record)
                at_terminal('fuUI_INIT; fuUI_WARN "careful"', sandbox.env(NO_COLOR=value), 100, 30)
                self.assertEqual(read(record).split("\n")[1:-1], [f"NO_COLOR={want}"])

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_real_gum_follows_no_color(self):
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        os.symlink(shutil.which("gum"), os.path.join(folder, "gum"))
        script = 'fuUI_INIT; [ -n "${myUI_GUM}" ] && fuUI_WARN "careful"'
        for value, painted in (("yes", False), ("1", False), ("", True)):
            with self.subTest(NO_COLOR=value):
                out = at_terminal(script, sandbox.env(NO_COLOR=value), 100, 30)
                self.assertIn("careful", out)
                self.assertEqual("38;5;" in out, painted, repr(out))

    # every helper of ui.sh that paints with gum, fuUI_PAINT (in $(...)) among them
    PAINTERS = ('fuUI_INIT; [ -n "${myUI_GUM}" ] || echo "no gum"; fuUI_INFO "careful"$\'\\n\'"more"; '
                'fuUI_OK "done"; fuUI_WARN "warned"; fuUI_ERROR "bad"; fuUI_HINT "a hint"; '
                'fuUI_RESULT fail "failed"; fuUI_RESULT next "then"; fuUI_RESULT info "about"; '
                'fuUI_BANNER "Title" "a line"; fuUI_SUMMARY "Summary" "ok:fine" "warn:hm"')
    # where the rule says no colours: NO_COLOR that is not empty, a dumb TERM (dumb, unknown, any case,
    # empty or none: bash makes an unset TERM dumb itself)
    UNPAINTED = ({"NO_COLOR": "1"}, {"NO_COLOR": "yes", "CLICOLOR_FORCE": "1"}, {"TERM": "dumb"},
                 {"TERM": "unknown"}, {"TERM": "DUMB", "COLORTERM": "truecolor"},
                 {"TERM": "unknown", "CLICOLOR_FORCE": "1"}, {"TERM": ""}, {"TERM": None},
                 {"TERM": "", "COLORTERM": "truecolor", "LC_TERMINAL": "iTerm2"})

    def test_no_colour_where_the_rule_says_none(self):
        """NO_COLOR and a dumb TERM: no gum call may force colours (CLICOLOR_FORCE beats NO_COLOR in gum
        2.0.2, and gum paints at TERM=unknown), fuUI_PAINT in $(...) too; elsewhere fuUI_PAINT forces
        them, gum does not write to a terminal there."""
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        shutil.move(fake_gum(sandbox.home), os.path.join(folder, "gum"))
        record = os.path.join(sandbox.home, "gum.force")
        for extra in self.UNPAINTED + ({},):
            with self.subTest(env=extra):
                if os.path.exists(record):
                    os.remove(record)
                out = at_terminal(self.PAINTERS, sandbox.env(**extra), 100, 40)
                self.assertNotIn("no gum", out)
                calls = [line for line in read(record).split("\n")[:-1] if not line.startswith("--version")]
                self.assertGreater(len(calls), 10, calls)
                forced = [line for line in calls if line.endswith(" CLICOLOR_FORCE=1")]
                if extra:
                    self.assertEqual(forced, [])
                    self.assertEqual({line.split()[1] for line in calls}, {"NO_COLOR=1"})
                else:
                    self.assertGreater(len(forced), 5, calls)
                    self.assertEqual({line.split()[1] for line in calls}, {"NO_COLOR=(unset)"})

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_real_gum_paints_nothing_where_the_rule_says_none(self):
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        os.symlink(shutil.which("gum"), os.path.join(folder, "gum"))
        for extra in self.UNPAINTED + ({},):
            with self.subTest(env=extra):
                out = at_terminal(self.PAINTERS, sandbox.env(**extra), 100, 40)
                self.assertNotIn("no gum", out)
                for text in ("⬢ careful", "  more", "✓ done", "! warned", "✗ bad", "a hint", "✗ failed",
                             "→ then", "⬢ about", "T-Pot Title", "a line", "Summary", "✓ fine", "! hm"):
                    self.assertIn(text, plain(out))
                # no colour (gum keeps bold under NO_COLOR, as no-color.org allows), a dumb TERM nothing
                colours = {colour for row in cells(out) for _char, fg, bg in row for colour in (fg, bg)} - {None}
                self.assertEqual(not colours, bool(extra), repr(out))
                if "TERM" in extra:
                    self.assertNotIn("\x1b[", out)

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_say_writes_the_bytes_of_real_gum(self):
        """tpotctl.say and ui.sh with the real gum 2.0.2 at a terminal write the same bytes for every
        message, at every depth and where the rule says no colours: an error keeps gum's bold under
        NO_COLOR, a dumb TERM (dumb, unknown, empty, none) has no SGR at all. The one difference: gum
        puts an empty reset (ESC[m) before a warning and a hint under NO_COLOR, which shows nothing."""
        from unittest import mock
        from tpotctl import say

        class Tty(io.StringIO):
            def isatty(self):
                return True
        sandbox = Sandbox(self)
        folder = os.path.join(sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        os.symlink(shutil.which("gum"), os.path.join(folder, "gum"))
        script = ('fuUI_INIT; [ -n "${myUI_GUM}" ] || echo "no gum"; fuUI_ERROR "bad"; fuUI_INFO "step"; '
                  'fuUI_OK "done"; fuUI_WARN "careful"; fuUI_HINT "a hint"')
        for extra in ({"COLORTERM": "truecolor"}, {}, {"TERM": "xterm"}) + self.UNPAINTED:
            with self.subTest(env=extra):
                env = sandbox.env(**extra)
                gum = at_terminal(script, env, 100, 30)
                out = Tty()
                with mock.patch.dict(os.environ, env, clear=True):
                    say.error("bad", stream=out)
                    say.info("step", stream=out)
                    say.ok("done", stream=out)
                    say.warn("careful", stream=out)
                    say.hint("a hint", stream=out)
                ours = out.getvalue()
                error, rest = gum.split("\n", 1)
                self.assertEqual(error + "\n", ours.split("\n", 1)[0] + "\n")         # the error: the same bytes
                if extra.get("NO_COLOR") and "TERM" not in extra:
                    self.assertEqual(error, "\x1b[1m✗ bad\x1b[m")
                    rest = rest.replace("\x1b[m", "")
                self.assertEqual(rest, ours.split("\n", 1)[1])

    def test_pref_speed(self):
        """fuUI_PREF reads a tpot.json with a long value, many pairs or many spaces in about the time of
        one look at it (it was quadratic: 100 000 characters took a minute), in every bash; the last
        pair of a key still wins."""
        sandbox = Sandbox(self)
        texts = {"long": '{"name": "' + "a" * 100000 + '", "colors": "16"}',
                 "umlauts": '{"colors": "256", "name": "' + "ä" * 20000 + '", "colors": "16"}',
                 "pairs": "{" + ", ".join(f'"k{i}": "v{i}"' for i in range(3000)) + ', "colors": "16"}',
                 "spaces": " " * 50000 + '{"colors"' + " " * 50000 + ": " + '"16"' + " " * 50000 + "}",
                 "first": '{"colors": "16", "name": "' + "a" * 100000 + '"}'}
        for bash in BASHES:
            for name, text in texts.items():
                with self.subTest(bash=bash, case=name):
                    sandbox.prefs(text)
                    start = time.time()
                    result = run("fuUI_PREF colors", sandbox.env(), bash=bash, timeout=120)
                    self.assertEqual(result.stdout, "16\n")
                    self.assertLess(time.time() - start, 1.0)

    def test_pref_keeps_the_locale_of_the_caller(self):
        """fuUI_PREF reads tpot.json in the C locale (its bytes), the caller keeps its own: characters
        are counted as before (fuUI_HANG, fuUI_HELP), its LC_ALL is the one it had."""
        sandbox = Sandbox(self)
        sandbox.prefs('{"name": "Größe", "colors": "16"}')
        script = 'x="äö"; fuUI_PREF colors; echo "${#x} ${LC_ALL-unset}"'
        for bash in BASHES:
            for locale, want in (({}, "unset"), ({"LC_ALL": os.environ.get("LANG", "en_US.UTF-8")}, None)):
                with self.subTest(bash=bash, locale=locale):
                    result = run(script, sandbox.env(**locale), bash=bash)
                    self.assertEqual(result.stdout, f"16\n2 {want or locale['LC_ALL']}\n")

    def test_colour_detection_reads_the_config_of_xdg(self):
        sandbox = Sandbox(self)
        other = os.path.join(sandbox.home, ".config", "tpotce")
        os.makedirs(other)
        with open(os.path.join(other, "tpot.json"), "w", encoding="utf-8") as out:
            out.write('{"colors": "16"}')
        env = sandbox.env(COLORTERM="truecolor")
        self.assertEqual(run("fuUI_COLORS", env).stdout.strip(), "truecolor")    # XDG_CONFIG_HOME wins
        env.pop("XDG_CONFIG_HOME")
        self.assertEqual(run("fuUI_COLORS", env).stdout.strip(), "16")


@unittest.skipUnless(BASH, "no bash")
class SizeAndCreditsTest(unittest.TestCase):

    def test_variant_thresholds(self):
        sandbox = Sandbox(self)
        cases = {(120, 49): "120", (200, 60): "120", (119, 49): "80", (120, 48): "80", (80, 33): "80",
                 (80, 32): "80x24", (100, 30): "80x24", (80, 24): "80x24", (79, 24): "", (80, 23): "",
                 (0, 0): ""}
        for (cols, rows), variant in cases.items():
            result = run(f"fuUI_LOGO_VARIANT {cols} {rows}", sandbox.env())
            self.assertEqual(result.stdout.strip(), variant, (cols, rows))
            self.assertEqual(result.returncode, 0 if variant else 1, (cols, rows))
        self.assertEqual(run("fuUI_LOGO_VARIANT x 30", sandbox.env()).returncode, 1)

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_variants_are_the_ones_of_the_splash(self):
        from tpotctl import splash_anim
        sandbox = Sandbox(self)
        for cols, rows in ((120, 49), (119, 49), (80, 33), (80, 32), (80, 24), (79, 30), (80, 23)):
            out = run(f"fuUI_LOGO_VARIANT {cols} {rows}", sandbox.env()).stdout.strip()
            self.assertEqual(out or None, splash_anim.variant_for(cols, rows), (cols, rows))

    def test_credits_below_and_inline(self):
        sandbox = Sandbox(self)
        for variant, width, rows in (("120", 120, 47), ("80", 80, 31)):
            lines = plain(run(f"fuUI_LOGO_RENDER {variant} 256 {VERSION}", sandbox.env()).stdout).split("\n")
            self.assertEqual(len(lines), rows + 3, variant)              # logo, empty line, credits, end
            self.assertEqual(lines[rows].strip(), "")
            credits = f"──[ t-pot {VERSION} ]══[ telekom security ]──"
            self.assertEqual(lines[rows + 1], " " * ((width - len(credits)) // 2) + credits)
        lines = plain(run(f"fuUI_LOGO_RENDER 80x24 256 {VERSION}", sandbox.env()).stdout).split("\n")
        self.assertEqual(len(lines), 25)
        bottom = lines[23]
        self.assertEqual(len(bottom), 80)
        self.assertEqual(bottom[1:1 + len(f"──[ t-pot {VERSION} ]")], f"──[ t-pot {VERSION} ]")
        self.assertEqual(bottom[56:78], "[ telekom security ]──")
        # too long a version: only t-pot
        long = "24.04.2-a-very-long-test-tag-of-a-fork"
        bottom = plain(run(f"fuUI_LOGO_RENDER 80x24 16 {long}", sandbox.env()).stdout).split("\n")[23]
        self.assertIn("──[ t-pot ]", bottom)
        below = plain(run(f"fuUI_LOGO_RENDER 80 16 {long}{long}", sandbox.env()).stdout).split("\n")[32]
        self.assertIn("──[ t-pot ]══[ telekom security ]──", below)
        none = plain(run("fuUI_LOGO_RENDER 120 16", sandbox.env()).stdout).split("\n")[48]
        self.assertIn("──[ t-pot ]══[ telekom security ]──", none)
        odd = plain(run("fuUI_LOGO_RENDER 80 16 $'24\\e[31m'", sandbox.env()).stdout)
        self.assertNotIn("\x1b[31", odd)

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_credits_match_the_splash(self):
        from tpotctl import glyphs, splash_anim
        glyphs.set_mode("unicode")
        sandbox = Sandbox(self)
        palette = truecolor_palette()
        for version in (VERSION, "", "24.04.2-a-very-long-test-tag-of-a-fork"):
            for variant in ui_logo.VARIANTS:
                with self.subTest(variant=variant, version=version):
                    splash = splash_anim.Splash(version, variant)
                    out = run(f"fuUI_LOGO_RENDER {variant} truecolor '{version}'", sandbox.env()).stdout
                    rows = cells(out)
                    for x, y, char, colour in splash.credits():
                        got, fg, _bg = rows[y][x]
                        self.assertEqual(got, char, (x, y))
                        if char != " ":
                            self.assertEqual(fg, palette[colour], (x, y, char))


@unittest.skipUnless(BASH, "no bash")
class LogoAtATerminalTest(unittest.TestCase):

    BANNER = 'myUI_LOGO=1\nfuUI_BANNER "Test" "a line"\necho "shown=${TPOT_LOGO_SHOWN}"'

    def setUp(self):
        self.sandbox = Sandbox(self)
        self.checkout = self.sandbox.checkout(version="99.1.0")

    def env(self, **extra):
        return self.sandbox.env(COLORTERM="truecolor", **extra)

    def test_logo_at_a_terminal(self):
        for (cols, rows), variant in (((120, 49), "120"), ((100, 40), "80"), ((80, 24), "80x24"),
                                      ((79, 24), None)):
            with self.subTest(size=(cols, rows)):
                out = at_terminal(self.BANNER, self.env(), cols, rows)
                self.assertNotIn("\r", out)
                if variant is None:
                    self.assertNotIn("telekom security", out)
                    self.assertIn("### T-Pot Test", out)
                    self.assertIn("shown=\n", out)
                    continue
                lines = plain(out).split("\n")
                title = lines.index("### T-Pot Test")
                width, height, _base = splash_art.grid(variant)
                logo = height // 2 + (0 if variant == "80x24" else 2)
                self.assertEqual(title, logo + 1, variant)               # the logo, an empty line, the title
                self.assertIn("telekom security", lines[logo - 1])
                self.assertIn(max(len(line) for line in lines[:logo]), (width - 1, width))
                self.assertIn("### a line", lines[title + 1])
                self.assertIn("shown=1", out)

    def test_version_comes_from_the_checkout(self):
        out = plain(at_terminal(f'myUI_CHECKOUT="{self.checkout}"\n' + self.BANNER, self.env(), 80, 40))
        self.assertIn("[ t-pot 99.1.0 ]", out)
        out = plain(at_terminal(f'myUI_CHECKOUT="{self.checkout}"; myUI_VERSION=1.2.3\n' + self.BANNER,
                                self.env(), 80, 40))
        self.assertIn("[ t-pot 1.2.3 ]", out)

    def test_colours_of_the_terminal(self):
        out = at_terminal(self.BANNER, self.sandbox.env(TERM="xterm"), 80, 40)
        self.assertIn("\x1b[0;95m", out)
        self.assertNotIn("38;2;", out)
        out = at_terminal(self.BANNER, self.sandbox.env(TERM="xterm-256color"), 80, 40)
        self.assertIn("38;5;162", out)
        self.assertNotIn("38;2;", out)

    def test_no_logo_when(self):
        sandbox = self.sandbox
        cases = {
            "the caller does not ask": ('fuUI_BANNER "Test" "a line"', self.env(), (120, 49)),
            "gum is off": (self.BANNER, self.env(TPOT_GUM="off"), (120, 49)),
            "install.sh -M": ("myMARKS=1\n" + self.BANNER, self.env(), (120, 49)),
            "the task screen": (self.BANNER, self.env(TPOT_MARKS="1"), (120, 49)),
            "a dumb terminal": (self.BANNER, self.env(TERM="dumb"), (120, 49)),
            "a dumb terminal in capitals": (self.BANNER, self.env(TERM="DUMB"), (120, 49)),
            "an unknown terminal": (self.BANNER, self.env(TERM="unknown"), (120, 49)),
            "an empty TERM": (self.BANNER, self.env(TERM=""), (120, 49)),
            "no TERM": (self.BANNER, self.env(TERM=None), (120, 49)),
            "NO_COLOR": (self.BANNER, self.env(NO_COLOR="1"), (120, 49)),
            "NO_COLOR 0": (self.BANNER, self.env(NO_COLOR="0"), (120, 49)),
            "shown before": (self.BANNER, self.env(TPOT_LOGO_SHOWN="1"), (120, 49)),
            "the ascii icons": (self.BANNER, self.env(TPOT_ICONS="ascii"), (120, 49)),
            "too narrow": (self.BANNER, self.env(), (79, 49)),
            "too low": (self.BANNER, self.env(), (120, 23)),
        }
        for name, (script, env, (cols, rows)) in cases.items():
            with self.subTest(name):
                out = at_terminal(script, env, cols, rows)
                self.assertNotIn("telekom security", out)
                self.assertIn("T-Pot Test", out)
        with self.subTest("NO_COLOR empty is no NO_COLOR (no-color.org, Rich, say)"):
            self.assertIn("telekom security", at_terminal(self.BANNER, self.env(NO_COLOR=""), 120, 49))
        with self.subTest("the ascii icons of tpot.json"):
            sandbox.prefs('{"icons": "ascii", "colors": "auto"}')
            self.assertNotIn("telekom security", at_terminal(self.BANNER, self.env(), 120, 49))
            self.assertIn("telekom security", at_terminal(self.BANNER, self.env(TPOT_ICONS="unicode"), 120, 49))
            os.remove(os.path.join(sandbox.config, "tpotce", "tpot.json"))
        with self.subTest("stdout is no terminal"):
            out = at_terminal(self.BANNER, self.env(), 120, 49, stdout_tty=False)
            self.assertNotIn("telekom security", out)
            self.assertIn("### T-Pot Test", out)
        with self.subTest("fuUI_LOGO_ON says why not"):
            out = at_terminal('fuUI_LOGO_ON; echo "rc=$?"', self.env(), 79, 49)
            self.assertIn("rc=1", out)
            out = at_terminal('fuUI_LOGO_ON; echo "rc=$?"', self.env(), 80, 24)
            self.assertIn("rc=0", out)

    def test_logo_once(self):
        script = (self.BANNER + '\nfuUI_BANNER "Again"\n'
                  f'bash -c \'source "{UI_SH}"; myUI_LOGO=1; fuUI_BANNER "Child"\'')
        out = at_terminal(script, self.env(), 120, 49)
        self.assertEqual(out.count("telekom security"), 1)
        self.assertIn("### T-Pot Again", out)
        self.assertIn("### T-Pot Child", out)

    def test_fuUI_LOGO_alone(self):
        out = at_terminal('fuUI_LOGO 7.7.7; echo "rc=$? shown=${TPOT_LOGO_SHOWN}"; fuUI_LOGO; echo "rc=$?"',
                          self.env(), 80, 24)
        self.assertIn("[ t-pot 7.7.7 ]", plain(out))
        self.assertIn("rc=0 shown=1", out)
        self.assertTrue(out.rstrip().endswith("rc=1"), out[-40:])

    def test_term_size(self):
        out = at_terminal('fuUI_TERM_SIZE; echo "rc=$? ${myUI_COLS}x${myUI_ROWS}"', self.env(), 97, 31)
        self.assertIn("rc=0 97x31", out)
        result = run('fuUI_TERM_SIZE; echo "rc=$? ${myUI_COLS}x${myUI_ROWS}"', self.env(COLUMNS="90", LINES="30"))
        self.assertIn("x", result.stdout)                     # no terminal: tput or COLUMNS / LINES


@unittest.skipUnless(BASH, "no bash")
class BannerTest(unittest.TestCase):

    def test_wordmark_is_the_manager_wordmark(self):
        wordmark = bash_array(read(UI_SH), "myUI_WORDMARK")
        self.assertEqual(len(wordmark), 3)
        self.assertEqual(ui_logo.wordmark_pixels(wordmark), ui_logo.manager_wordmark())
        self.assertEqual(ui_logo.manager_wordmark()[0].count("#"), 8)        # T-Pot, capital T and P

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_manager_wordmark_is_logo_wordmark(self):
        from tpotctl import logo
        self.assertEqual(ui_logo.manager_wordmark(), logo.WORDMARK)

    def test_plain_banner_unchanged(self):
        sandbox = Sandbox(self)
        for logo in ("", "myUI_LOGO=1; "):
            out = run(logo + 'fuUI_INIT; fuUI_BANNER "Installer" "one" "two"', sandbox.env()).stdout
            self.assertEqual(out, "\n### T-Pot Installer\n### one\n### two\n\n")

    # 140 characters, words of 4 to 13 (the status line of the builder)
    LONG = ("Builder 'mybuilder': running, linux/arm64, linux/amd64 and 6 more platforms, QEMU for both, "
            "the log in /home/someone/tpotce/docker/x/log now")

    def assert_hanging(self, lines, first, rest, width=80):
        """The long line broken into lines no wider than width: the first starts with first, the
        others with rest (the hanging indent), the words all there in their order."""
        self.assertEqual(len(self.LONG), 140)
        lines = [line for line in lines if line.strip()]
        self.assertGreater(len(lines), 1, lines)
        self.assertLessEqual(max(len(line) for line in lines), width, lines)
        self.assertTrue(lines[0].startswith(first) and not lines[0].startswith(first + " "), lines)
        for line in lines[1:]:
            self.assertTrue(line.startswith(rest) and not line.startswith(rest + " "), lines)
        words = " ".join(line[len(first if i == 0 else rest):] for i, line in enumerate(lines)).split()
        self.assertEqual(words, self.LONG.split())

    def test_info_lines_wrap_at_the_terminal(self):
        sandbox = Sandbox(self)
        long = self.LONG.replace("'", "'\\''")
        # plain text: ### and a hanging indent of two
        out = at_terminal(f"fuUI_BANNER Builder '{long}' 'short one'", sandbox.env(TPOT_GUM="off"), 80, 24)
        lines = out.split("\n")
        start = lines.index("### T-Pot Builder") + 1
        end = lines.index("### short one")
        self.assert_hanging(lines[start:end], "### ", "###   ")
        out = at_terminal(f"fuUI_INFO '{long}'; echo end", sandbox.env(TPOT_GUM="off"), 80, 24)
        self.assert_hanging(out.split("\n")[:out.split("\n").index("end")], "### ", "###   ")
        # gum: the banner lines go to gum style broken (gum has a margin of two on each side), INFO
        # puts its sign before the first line
        gum = fake_gum(sandbox.home)
        at_terminal(f"myUI_GUM='{gum}'; fuUI_BANNER Builder '{long}' 'short one'", sandbox.env(), 80, 24)
        call = [c for c in read(os.path.join(sandbox.home, "gum.calls")).split("--- call\n") if "short one" in c][0]
        args = call.split("\n")
        texts = args[args.index("--") + 1:-1]
        self.assertEqual(texts[-1], "short one")
        self.assert_hanging(texts[:-1], "", "  ", width=76)
        out = at_terminal(f"myUI_GUM='{gum}'; fuUI_INFO '{long}'; echo end", sandbox.env(), 80, 24)
        lines = plain(out).split("\n")
        self.assert_hanging(lines[:lines.index("end")], "⬢ ", "    ")
        # a wider terminal takes more, a line that fits and the output without a terminal stay
        out = at_terminal(f"fuUI_INFO '{long}'", sandbox.env(TPOT_GUM="off"), 200, 24)
        self.assertEqual(out, f"### {self.LONG}\n")
        result = run(f"fuUI_BANNER Builder '{long}'; myUI_GUM='{gum}'; fuUI_INFO '{long}'", sandbox.env())
        self.assertEqual(result.stdout, f"\n### T-Pot Builder\n### {self.LONG}\n\n⬢ {self.LONG}\n")

    def test_given_lines_stay_lines(self):
        """A text of more lines: only the line that is too wide is broken (with its hanging indent),
        the given line breaks stay, a short line is not joined to the next one."""
        sandbox = Sandbox(self)
        long = self.LONG.replace("'", "'\\''")
        text = f"\"First line:\"$'\\n''{long}'$'\\n'\"Last one.\""
        out = at_terminal(f"fuUI_INFO {text}; echo end", sandbox.env(TPOT_GUM="off"), 60, 24)
        lines = out.split("\n")[:out.split("\n").index("end")]
        self.assertEqual(lines[0], "### First line:")
        self.assertEqual(lines[-1], "### Last one.")
        self.assert_hanging(lines[1:-1], "### ", "###   ", width=60)
        out = at_terminal(f"fuUI_BANNER Builder {text} 'short one'", sandbox.env(TPOT_GUM="off"), 60, 24)
        lines = out.split("\n")
        start = lines.index("### T-Pot Builder") + 1
        self.assertEqual(lines[start], "### First line:")
        end = lines.index("### Last one.")
        self.assert_hanging(lines[start + 1:end], "### ", "###   ", width=60)
        self.assertEqual(lines[end + 1], "### short one")
        # gum: the lines go to gum style the same way, INFO puts its sign before the first one
        gum = fake_gum(sandbox.home)
        at_terminal(f"myUI_GUM='{gum}'; fuUI_BANNER Builder {text} 'short one'", sandbox.env(), 60, 24)
        call = [c for c in read(os.path.join(sandbox.home, "gum.calls")).split("--- call\n") if "short one" in c][0]
        args = call.split("\n")
        texts = args[args.index("--") + 1:-1]
        self.assertEqual((texts[0], texts[-2:]), ("First line:", ["Last one.", "short one"]))
        self.assert_hanging(texts[1:-2], "", "  ", width=56)
        out = at_terminal(f"myUI_GUM='{gum}'; fuUI_INFO {text}; echo end", sandbox.env(), 60, 24)
        lines = plain(out).split("\n")
        lines = lines[:lines.index("end")]
        self.assertEqual((lines[0], lines[-1]), ("⬢ First line:", "  Last one."))
        self.assert_hanging(lines[1:-1], "  ", "    ", width=60)

    def test_hang_keeps_the_indent_of_a_line(self):
        """A too wide line that is indented keeps its indent, its continuations hang two further; the
        lines stay within the room, the words in their order."""
        sandbox = Sandbox(self)
        text = "First line:\n  indented line that is far too wide for the room here\nshort"
        result = run("fuUI_HANG 30 $'" + text.replace("\n", "\\n") + "'", sandbox.env())
        lines = result.stdout.split("\n")[:-1]
        self.assertEqual(lines, ["First line:", "  indented line that is far", "    too wide for the room here",
                                 "short"])
        # deeper, and at once too wide in INFO (### before every line, the indent after it)
        text = "      " + " ".join(["word"] * 20)
        out = at_terminal(f"fuUI_INFO '{text}'", sandbox.env(TPOT_GUM="off"), 40, 24)
        lines = out.split("\n")[:-1]
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(line) <= 40 for line in lines), lines)
        self.assertTrue(lines[0].startswith("###       word"), lines)
        self.assertTrue(all(line.startswith("###         word") for line in lines[1:]), lines)
        self.assertEqual(" ".join(line[4:] for line in lines).split(), text.split())

    def test_the_lines_of_a_text_look_alike(self):
        """A text of more lines has one layout, whether it fits or not and with or without a terminal:
        plain text puts ### before every line, gum the sign before the first and two spaces before the
        others (the indent of a line that is broken goes after that)."""
        sandbox = Sandbox(self)
        text = "\"First line:\"$'\\n'\"Second line.\""
        want = "### First line:\n### Second line.\n"
        self.assertEqual(run(f"fuUI_INFO {text}", sandbox.env()).stdout, want)
        self.assertEqual(at_terminal(f"fuUI_INFO {text}", sandbox.env(TPOT_GUM="off"), 80, 24), want)
        self.assertEqual(run(f"fuUI_BANNER Builder {text}", sandbox.env()).stdout,
                         "\n### T-Pot Builder\n" + want + "\n")
        self.assertEqual(at_terminal(f"fuUI_BANNER Builder {text}", sandbox.env(TPOT_GUM="off"), 80, 24),
                         "\n### T-Pot Builder\n" + want + "\n")
        gum = fake_gum(sandbox.home)
        self.assertEqual(plain(run(f"myUI_GUM='{gum}'; fuUI_INFO {text}", sandbox.env()).stdout),
                         "⬢ First line:\n  Second line.\n")
        out = at_terminal(f"myUI_GUM='{gum}'; fuUI_INFO {text}; echo end", sandbox.env(), 80, 24)
        self.assertEqual(plain(out), "⬢ First line:\n  Second line.\nend\n")
        out = at_terminal(f"myUI_GUM='{gum}'; fuUI_BANNER Builder {text} 'last'", sandbox.env(), 80, 24)
        self.assertIn("\nT-Pot Builder\nFirst line:\nSecond line.\nlast\n", plain(out))

    def test_gum_banner_shows_the_wordmark(self):
        sandbox = Sandbox(self)
        gum = fake_gum(sandbox.home)
        out = run(f'myUI_GUM="{gum}"; fuUI_BANNER "Installer" "one"', sandbox.env()).stdout
        calls = read(os.path.join(sandbox.home, "gum.calls"))
        for line in bash_array(read(UI_SH), "myUI_WORDMARK"):
            self.assertIn(line, calls)
        self.assertIn("T-Pot Installer", out)


def fake_gum(home, output=""):
    """A gum that writes its argv (one per line, a call per block) to gum.calls (COLORTERM to gum.env,
    COLORTERM and TERM to gum.term, NO_COLOR to gum.nocolor, its command with NO_COLOR and
    CLICOLOR_FORCE to gum.force, a line per call) and prints output
    for choose / filter; style prints its texts; spin runs its command like gum, or ends with
    FAKE_GUM_SPIN_RC as gum does on Ctrl+C (130) when its argv has FAKE_GUM_STOP (any spin without
    it): at once, or once the file FAKE_GUM_WAIT has something in it (the step is under way, 20 s
    at most)."""
    path = os.path.join(home, "gum")
    with open(path, "w", encoding="utf-8") as out:
        out.write("#!/bin/sh\n"
                  f'{{ echo "--- call"; for a in "$@"; do echo "$a"; done; }} >> "{home}/gum.calls"\n'
                  f'echo "COLORTERM=${{COLORTERM-(unset)}}" >> "{home}/gum.env"\n'
                  f'echo "COLORTERM=${{COLORTERM-(unset)}} TERM=${{TERM-(unset)}}" >> "{home}/gum.term"\n'
                  f'echo "NO_COLOR=${{NO_COLOR-(unset)}}" >> "{home}/gum.nocolor"\n'
                  f'echo "$1 NO_COLOR=${{NO_COLOR-(unset)}} CLICOLOR_FORCE=${{CLICOLOR_FORCE-(unset)}}" '
                  f'>> "{home}/gum.force"\n'
                  'case "$1" in\n'
                  '  --version) echo "gum version v2.0.2" ;;\n'
                  '  choose|filter) printf "%s" "$FAKE_GUM_OUT"; exit "${FAKE_GUM_RC:-0}" ;;\n'
                  '  style) while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done; shift\n'
                  '         for a in "$@"; do echo "$a"; done ;;\n'
                  '  spin) if [ -n "$FAKE_GUM_SPIN_RC" ]; then\n'
                  '          case "$*" in *"$FAKE_GUM_STOP"*)\n'
                  '            n=0; while [ -n "$FAKE_GUM_WAIT" ] && [ ! -s "$FAKE_GUM_WAIT" ] && [ "$n" -lt 400 ]; do\n'
                  '              sleep 0.05; n=$((n + 1)); done\n'
                  '            exit "$FAKE_GUM_SPIN_RC" ;;\n'
                  '          esac\n'
                  '        fi\n'
                  '        while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done; shift; exec "$@" ;;\n'
                  'esac\n')
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def fake_system(home):
    """A folder for the front of PATH with uname (uname -s says FAKE_UNAME_S, Linux without it;
    -m says arm64) and curl / wget that write their argv to net.calls and fail: nothing downloads."""
    folder = os.path.join(home, "fakebin")
    os.makedirs(folder, exist_ok=True)
    scripts = {
        "uname": '#!/bin/sh\ncase "$1" in -s) echo "${FAKE_UNAME_S-Linux}" ;; -m) echo arm64 ;; *) echo "${FAKE_UNAME_S-Linux}" ;; esac\n',
        "curl": f'#!/bin/sh\necho "curl $*" >> "{home}/net.calls"\nexit 22\n',
        "wget": f'#!/bin/sh\necho "wget $*" >> "{home}/net.calls"\nexit 8\n',
    }
    for name, text in scripts.items():
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)
    return folder


HELP = ('fuUI_HELP "Installer" "install.sh [-s] [-t <type>]"$\'\\n\'"           [-b <branch>]" '
        '--about "Installs T-Pot." --about $\'Without -s at a terminal\\nthe assistant asks.\' '
        '--opt "-s" "Unattended" --opt "-t <type>" $\'Type of installation\\n  h - hive\' '
        '--opt "--a-very-long-option <value>" "Long" --opt "-h" "This help" '
        '--example "install.sh -s -t h" "A HIVE without questions" --note "Needs sudo."')

HELP_TEXT = """T-Pot Installer

Usage: install.sh [-s] [-t <type>]
                  [-b <branch>]

Installs T-Pot.

Without -s at a terminal
the assistant asks.

Options:
  -s           Unattended
  -t <type>    Type of installation
                 h - hive
  --a-very-long-option <value>
               Long
  -h           This help

Examples:
  install.sh -s -t h
      A HIVE without questions

Notes:
  Needs sudo.
"""


@unittest.skipUnless(BASH, "no bash")
class HelpersTest(unittest.TestCase):

    def setUp(self):
        self.sandbox = Sandbox(self)

    def run_ui(self, script, stdin=None, **env):
        return run(script, self.sandbox.env(**env), stdin=stdin)

    def test_help_layout(self):
        result = self.run_ui(HELP)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, HELP_TEXT)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.run_ui('fuUI_HELP "Tool" "tool.sh"').stdout, "T-Pot Tool\n\nUsage: tool.sh\n")

    def test_help_at_a_terminal_is_coloured(self):
        gum = fake_gum(self.sandbox.home)
        out = at_terminal(f'myUI_GUM="{gum}"\n' + HELP, self.sandbox.env(COLORTERM="truecolor"), 100, 40)
        self.assertIn("\x1b[", out)
        self.assertEqual(plain(out), HELP_TEXT)
        out = at_terminal(f'myUI_GUM="{gum}"\n' + HELP, self.sandbox.env(NO_COLOR="1"), 100, 40)
        self.assertEqual(out, HELP_TEXT)
        out = at_terminal(f'myUI_GUM="{gum}"\n' + HELP, self.sandbox.env(NO_COLOR="no"), 100, 40)
        self.assertEqual(out, HELP_TEXT)                                 # any value but an empty one
        out = at_terminal(f'myUI_GUM="{gum}"\n' + HELP, self.sandbox.env(NO_COLOR=""), 100, 40)
        self.assertIn("\x1b[", out)                                       # empty: no NO_COLOR
        # a dumb terminal has no colours, whatever it inherited (gum leaves them out there too); an
        # empty TERM or none is one (bash makes an unset TERM dumb itself)
        for term in ("dumb", "unknown", "DUMB", "", None):
            with self.subTest(term=term):
                env = self.sandbox.env(TERM=term, COLORTERM="truecolor", LC_TERMINAL="iTerm2", TPOT_COLORS="truecolor")
                self.assertEqual(at_terminal(f'myUI_GUM="{gum}"\n' + HELP, env, 100, 40), HELP_TEXT)

    def test_usage_error(self):
        result = self.run_ui('fuUI_INIT; fuUI_USAGE_ERROR "Unknown option -Z." install.sh; echo "rc=$?"')
        self.assertEqual(result.stdout, "rc=1\n")
        self.assertEqual(result.stderr, "### [ERROR] - Unknown option -Z.\n###   install.sh -h shows the options.\n")
        result = run('fuUI_USAGE_ERROR "No." || echo "rc=$?"', self.sandbox.env(), source=UI_SH)
        self.assertIn("-h shows the options.", result.stderr)
        self.assertEqual(result.stdout, "rc=1\n")

    def test_summary(self):
        result = self.run_ui('fuUI_INIT; fuUI_SUMMARY "Installation" "ok:T-Pot is installed" '
                             '"warn:SSH moved: port 64295" "next:sudo reboot" "info:Log: /tmp/x" "odd"; '
                             'echo "rc=$?"')
        self.assertEqual(result.stdout, "\n### Installation\n### [OK] - T-Pot is installed\n"
                                        "### [WARNING] - SSH moved: port 64295\n### [NEXT] - sudo reboot\n"
                                        "### Log: /tmp/x\n### odd\n\nrc=0\n")
        result = self.run_ui('fuUI_SUMMARY "Update" "ok:pulled" "fail:start"; echo "rc=$?"')
        self.assertIn("### [FAILED] - start\n", result.stdout)
        self.assertTrue(result.stdout.endswith("rc=1\n"))
        result = self.run_ui('fuUI_RESULT next "tpot"; fuUI_RESULT fail "x"; echo "rc=$?"')
        self.assertEqual(result.stdout, "### [NEXT] - tpot\n### [FAILED] - x\nrc=0\n")

    def test_summary_with_gum_is_a_box(self):
        gum = fake_gum(self.sandbox.home)
        result = self.run_ui(f'myUI_GUM="{gum}"; fuUI_SUMMARY "Done" "ok:fine"; echo "rc=$?"')
        calls = read(os.path.join(self.sandbox.home, "gum.calls"))
        self.assertIn("--border\nrounded", calls)
        self.assertIn("Done", result.stdout)
        self.assertIn("fine", result.stdout)
        self.assertTrue(result.stdout.endswith("rc=0\n"))

    # the longest items of install.sh and update.sh
    LONG = ('"warn:The command tpot is not in your PATH, link it with: sudo ln -sfn /home/someone/tpotce/tpot '
            '/usr/local/bin/tpot" "next:Start T-Pot with \'sudo systemctl start tpot\' or \'docker compose up -d\' '
            '(update.sh -s does it for you)."')

    def box_width(self, script, cols=None, **env):
        """The --width of the box of fuUI_SUMMARY (None without one), in a pty of cols x 24 or without
        a terminal."""
        gum = fake_gum(self.sandbox.home)
        calls = os.path.join(self.sandbox.home, "gum.calls")
        if os.path.exists(calls):
            os.remove(calls)
        line = f'myUI_GUM="{gum}"; {script}'
        if cols:
            at_terminal(line, self.sandbox.env(**env), cols, 24)
        else:
            run(line, self.sandbox.env(**env))
        for call in read(calls).split("--- call\n"):
            args = call.split("\n")
            if "--border" in args:
                return int(args[args.index("--width") + 1]) if "--width" in args else None
        self.fail("no box")

    def test_summary_box_fits_the_terminal(self):
        """gum style does not wrap on its own: a long item makes the box wider than the terminal. The
        box is limited to the terminal (2 columns of margin on each side), 80 when its size is unknown;
        a box that fits keeps its own width."""
        self.assertEqual(self.box_width(f'fuUI_SUMMARY "Done" {self.LONG}', 80), 76)
        self.assertEqual(self.box_width(f'fuUI_SUMMARY "Done" {self.LONG}', 100), 96)
        self.assertEqual(self.box_width(f'fuUI_SUMMARY "Done" {self.LONG}', COLUMNS=None, LINES=None, TERM="dumb"),
                         76)
        self.assertIsNone(self.box_width('fuUI_SUMMARY "Done" "ok:fine" "next:sudo reboot"', 80))
        # the longest line that still fits: 72 characters + padding, border and margin = 80
        self.assertIsNone(self.box_width(f'fuUI_SUMMARY "Done" "ok:{"x" * 70}"', 80))
        self.assertEqual(self.box_width(f'fuUI_SUMMARY "Done" "ok:{"x" * 71}"', 80), 76)
        self.assertEqual(self.box_width(f'fuUI_SUMMARY "{"T" * 73}" "ok:fine"', 80), 76)

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_summary_box_with_gum_wraps_at_80_columns(self):
        out = at_terminal(f'myUI_GUM="{shutil.which("gum")}"; fuUI_SUMMARY "Done" {self.LONG}',
                          self.sandbox.env(COLORTERM="truecolor"), 80, 24)
        lines = [plain(line) for line in out.split("\n")]
        self.assertLessEqual(max(len(line) for line in lines), 80, "\n".join(lines))
        words = " ".join(line.strip(" │╭╮╰╯─") for line in lines)
        for word in ("/usr/local/bin/tpot", "(update.sh", "you).", "Done"):
            self.assertIn(word, words)

    CHOOSE = 'fuUI_CHOOSE_MANY {options} "Groups" "Git: the checkout:git" "Config:config" "Data:data" "Logs:logs"'

    def choose(self, answer, options=""):
        result = self.run_ui(self.CHOOSE.format(options=options) + '; echo "rc=$?"', stdin=answer)
        return result.stdout, result.stderr

    def test_choose_many_plain(self):
        self.assertEqual(self.choose("1,3-4\n")[0], "git\ndata\nlogs\nrc=0\n")
        self.assertEqual(self.choose("a\n")[0], "git\nconfig\ndata\nlogs\nrc=0\n")
        self.assertEqual(self.choose("n\n", "--all")[0], "rc=0\n")
        self.assertEqual(self.choose("\n", "--all")[0], "git\nconfig\ndata\nlogs\nrc=0\n")
        self.assertEqual(self.choose("\n", "--selected config --selected logs")[0], "config\nlogs\nrc=0\n")
        self.assertEqual(self.choose("\n", "--selected config,logs")[0], "rc=0\n")     # one value, not two
        self.assertEqual(self.choose("\n")[0], "rc=0\n")
        self.assertEqual(self.choose(" 4, 2 \n")[0], "config\nlogs\nrc=0\n")
        out, err = self.choose("7\n0\n2-1\nx\n2\n")
        self.assertEqual(out, "config\nrc=0\n")
        self.assertEqual(self.choose("")[0], "rc=1\n")                     # stdin ends
        self.assertEqual(self.choose("9\n")[0], "rc=1\n")                  # a wrong one, then the end
        _out, err = self.choose("1\n", "--selected data")
        self.assertIn("### Groups\n", err)
        self.assertIn("###   1) [ ] Git: the checkout\n", err)
        self.assertIn("###   3) [x] Data\n", err)
        self.assertEqual(self.choose("2\n", "--filter")[0], "config\nrc=0\n")
        # --selected once per value; a value (and a label) may have commas
        out = self.run_ui(self.COMMAS.format(options="--selected cowrie --selected tanner,redis,phpox")
                          + '; echo "rc=$?"', stdin="\n")
        self.assertEqual(out.stdout, "tanner,redis,phpox\ncowrie\nrc=0\n")
        self.assertIn("###   1) [x] Tanner stack (redis, phpox)\n", out.stderr)
        self.assertIn("###   3) [ ] Dionaea, the old one\n", out.stderr)
        out = self.run_ui(self.COMMAS.format(options="--selected tanner --selected redis") + '; echo "rc=$?"',
                          stdin="\n")
        self.assertEqual(out.stdout, "rc=0\n")                            # parts of a value are no value

    # labels and values with commas (split at the last colon)
    COMMAS = ('fuUI_CHOOSE_MANY {options} "Images" "Tanner stack (redis, phpox):tanner,redis,phpox" '
              '"Cowrie:cowrie" "Dionaea, the old one:dionaea"')

    def gum_choose(self, options, output, rc=0, choose=None):
        gum = fake_gum(self.sandbox.home)
        calls = os.path.join(self.sandbox.home, "gum.calls")
        if os.path.exists(calls):
            os.remove(calls)
        script = f'myUI_GUM="{gum}"\n' + (choose or self.CHOOSE).format(options=options) + '; echo "rc=$?"'
        out = at_terminal(script, self.sandbox.env(FAKE_GUM_OUT=output, FAKE_GUM_RC=str(rc)), 100, 40,
                          stdout_tty=False)
        args = read(calls).split("\n")[1:-1]
        return out, args

    def test_choose_many_gum(self):
        out, args = self.gum_choose("--selected logs --selected config", "Git: the checkout\nLogs\n")
        self.assertEqual(out, "git\nlogs\nrc=0\n")
        self.assertEqual(args[0], "choose")
        self.assertIn("--no-limit", args)
        self.assertNotIn("--label-delimiter", args)
        # one --selected per marked label, in the order of the items: gum splits a value at commas
        self.assertEqual(selected(args), ["Config", "Logs"])
        self.assertEqual(args[args.index("--header") + 1], "Groups")
        self.assertEqual(args[args.index("--") + 1:], ["Git: the checkout", "Config", "Data", "Logs"])
        out, args = self.gum_choose("--filter --all", "Logs\nData\n")
        self.assertEqual(out, "data\nlogs\nrc=0\n")                      # in the order of the items
        self.assertEqual(args[0], "filter")
        self.assertIn("--no-limit", args)
        self.assertEqual(args[args.index("--selected") + 1], "*")
        out, args = self.gum_choose("", "", rc=130)
        self.assertEqual(out, "rc=130\n")
        self.assertNotIn("--selected", args)
        # a comma of a label is \, for gum (gum 2.0.2 splits --selected at the others)
        out, args = self.gum_choose("--selected tanner,redis,phpox --selected dionaea",
                                    "Tanner stack (redis, phpox)\nDionaea, the old one\n", choose=self.COMMAS)
        self.assertEqual(out, "tanner,redis,phpox\ndionaea\nrc=0\n")
        self.assertEqual(selected(args), ["Tanner stack (redis\\, phpox)", "Dionaea\\, the old one"])
        self.assertEqual(args[args.index("--") + 1:],
                         ["Tanner stack (redis, phpox)", "Cowrie", "Dionaea, the old one"])
        # what gum cannot mark: a label * (gum: all of them) or with \, (gum has no escape for it)
        odd = 'fuUI_CHOOSE_MANY {options} "Odd" "*:star" "a\\\\,b:ab" "Plain:plain"'
        _out, args = self.gum_choose("--selected star --selected ab --selected plain", "", choose=odd)
        self.assertEqual(selected(args), ["Plain"])
        _out, args = self.gum_choose("--all", "", choose=odd)
        self.assertEqual(selected(args), ["*"])

    # fuUI_CHOOSE: label and value split at the last colon, as fuUI_CHOOSE_MANY (a label may have one)
    PICK = ('fuUI_CHOOSE "Pick" "One:1" "Two, too:2" "Three: a b:3" "Keep localhost:5000/tpot:keep" '
            '"One:other"')

    def test_choose_splits_at_the_last_colon(self):
        for answer, value in (("1\n", "1"), ("3\n", "3"), ("4\n", "keep"), ("5\n", "other"), ("x\n2\n", "2")):
            with self.subTest(answer=answer):
                result = self.run_ui(self.PICK + '; echo "rc=$?"', stdin=answer)
                self.assertEqual(result.stdout, f"{value}\nrc=0\n")
                self.assertIn("###   3) Three: a b\n###   4) Keep localhost:5000/tpot\n", result.stderr)
        self.assertEqual(self.run_ui(self.PICK + '; echo "rc=$?"', stdin="").stdout, "rc=1\n")
        # gum gets the labels (its --label-delimiter splits at the first colon) and the value is found here
        out, args = self.gum_choose("", "Three: a b\n", choose=self.PICK)
        self.assertEqual(out, "3\nrc=0\n")
        self.assertEqual(args[0], "choose")
        self.assertNotIn("--label-delimiter", args)
        self.assertEqual(args[args.index("--header") + 1], "Pick")
        self.assertEqual(args[args.index("--") + 1:],
                         ["One", "Two, too", "Three: a b", "Keep localhost:5000/tpot", "One"])
        out, _args = self.gum_choose("", "Keep localhost:5000/tpot\n", choose=self.PICK)
        self.assertEqual(out, "keep\nrc=0\n")
        out, _args = self.gum_choose("", "One\n", choose=self.PICK)
        self.assertEqual(out, "1\nrc=0\n")                               # the first of two equal labels
        out, _args = self.gum_choose("", "", rc=130, choose=self.PICK)
        self.assertEqual(out, "rc=130\n")
        out, _args = self.gum_choose("", "Not offered\n", choose=self.PICK)
        self.assertEqual(out, "rc=1\n")

    def test_choose_with_a_default_plain(self):
        """fuUI_CHOOSE --selected <value>: the item of that value is the default, shown in the question,
        enter takes it; another answer goes as without it. A value no item has: no default."""
        pick = self.PICK.replace("fuUI_CHOOSE ", "fuUI_CHOOSE {options} ", 1) + '; echo "rc=$?"'
        for options, answer, value in (("--selected 3", "\n", "3"), ("--selected 3", "1\n", "1"),
                                       ("--selected keep", "\n", "keep"), ("--selected keep", " \n", "keep"),
                                       ("--selected 3", "x\n\n", "3"), ("--selected other", "\n", "other"),
                                       ("--selected nope", "\n2\n", "2"), ("", "\n2\n", "2")):
            with self.subTest(options=options, answer=answer):
                result = self.run_ui(pick.format(options=options), stdin=answer)
                self.assertEqual(result.stdout, f"{value}\nrc=0\n")
                self.assertIn("###   3) Three: a b\n###   4) Keep localhost:5000/tpot\n", result.stderr)
        # the question names the default; without one it is the one of before
        script = 'fuUI_CHOOSE {options} "Jobs" "1:1" "2:2" "4:4" "8:8"; echo "rc=$?"'
        for options, prompt, value in (("--selected 2", "### Choice (1-4, enter = 2): ", "2"),
                                       ("--selected 8", "### Choice (1-4, enter = 4): ", "8"),
                                       ("", "### Choice (1-4): ", "4")):
            with self.subTest(options=options):
                # enter, then 3 (for the question without a default, which asks again)
                out = at_terminal(script.format(options=options), self.sandbox.env(TPOT_GUM="off"), 100, 30,
                                  keys=b"\r3\r")
                self.assertIn(prompt, out)
                self.assertRegex(out, rf"(^|\n| ){value}\r?\nrc=0\r?\n$")
        self.assertEqual(self.run_ui(pick.format(options="--selected 3"), stdin="").stdout, "rc=1\n")

    def sources(self):
        """ui.sh and the plain fallback of the scripts (ui_logo.FALLBACK) as files to source."""
        fallback = os.path.join(self.sandbox.home, "fallback.sh")
        with open(fallback, "w", encoding="utf-8") as out:
            out.write(ui_logo.FALLBACK)
        return (UI_SH, fallback)

    # twelve items: an answer whose spaces were dropped (1 2 -> 12) would be one of them
    TWELVE = 'fuUI_CHOOSE {options} "Jobs" ' + " ".join(f'"Item {n}:v{n}"' for n in range(1, 13)) + '; echo "rc=$?"'

    def test_choose_answers_plain(self):
        """The spaces around an answer go (a CR of CRLF too), the ones in it make it no number (asked
        again); leading zeros count as decimal (08 is 8); an answer of spaces only takes the default."""
        for source in self.sources():
            for options, answer, out in (("", "1 2\n3\n", "v3\nrc=0\n"), ("", "1 2\n", "rc=1\n"),
                                         ("", " 2 \n", "v2\nrc=0\n"), ("", "\t11\t\n", "v11\nrc=0\n"),
                                         ("", "08\n", "v8\nrc=0\n"), ("", "012\n", "v12\nrc=0\n"),
                                         ("", "009\n", "v9\nrc=0\n"), ("", " \n4\n", "v4\nrc=0\n"),
                                         ("--selected v5", " \n", "v5\nrc=0\n"),
                                         ("--selected v5", "\t \n", "v5\nrc=0\n"),
                                         ("", "2\r\n", "v2\nrc=0\n"), ("--selected v5", "\r\n", "v5\nrc=0\n"),
                                         ("--selected v5", "1 1\n\n", "v5\nrc=0\n"),
                                         ("", "13\n0\n-1\n+2\n2x\n1\n", "v1\nrc=0\n")):
                with self.subTest(source=source, options=options, answer=answer):
                    result = run(self.TWELVE.format(options=options), self.sandbox.env(TPOT_GUM="off"),
                                 source=source, stdin=answer)
                    self.assertEqual(result.stdout, out)
                    self.assertNotIn("value too great", result.stderr)

    def test_choose_many_answers_plain(self):
        """fuUI_CHOOSE_MANY as fuUI_CHOOSE: spaces around the numbers and commas go, a space between two
        digits makes it no choice (1 3 is not 13); leading zeros are decimal."""
        script = ('fuUI_CHOOSE_MANY "Images" ' + " ".join(f'"Item {n}:v{n}"' for n in range(1, 14))
                  + '; echo "rc=$?"')
        for source in self.sources():
            for answer, out in (("1 3\n2\n", "v2\nrc=0\n"), ("1 3\n", "rc=1\n"), (" 1 , 3 \n", "v1\nv3\nrc=0\n"),
                                ("08,09-010\n", "v8\nv9\nv10\nrc=0\n"), ("1 2-3\n", "rc=1\n"),
                                ("1\t3\n", "rc=1\n"), ("13\r\n", "v13\nrc=0\n")):
                with self.subTest(source=source, answer=answer):
                    result = run(script, self.sandbox.env(TPOT_GUM="off"), source=source, stdin=answer)
                    self.assertEqual(result.stdout, out)
                    if out == "rc=1\n":
                        self.assertIn("### [WARNING] - Not a choice: ", result.stderr)

    def test_choose_with_a_default_gum(self):
        """gum gets the label of the default as --selected (a comma as \\, as gum splits there); a label
        gum cannot take alone (*, or one with \\,) goes without one; without --selected none."""
        pick = self.PICK.replace("fuUI_CHOOSE ", "fuUI_CHOOSE {options} ", 1)
        for options, want in (("--selected 3", ["Three: a b"]), ("--selected 2", ["Two\\, too"]),
                              ("--selected keep", ["Keep localhost:5000/tpot"]), ("--selected other", ["One"]),
                              ("--selected nope", []), ("", [])):
            with self.subTest(options=options):
                out, args = self.gum_choose("", "Three: a b\n", choose=pick.replace("{options}", options))
                self.assertEqual(out, "3\nrc=0\n")
                self.assertEqual(args[0], "choose")
                self.assertEqual(selected(args), want)
                self.assertEqual(args[args.index("--") + 1:],
                                 ["One", "Two, too", "Three: a b", "Keep localhost:5000/tpot", "One"])
        odd = 'fuUI_CHOOSE {options} "Odd" "*:star" "a\\\\,b:ab" "Plain:plain"'
        for options, want in (("--selected star", []), ("--selected ab", []), ("--selected plain", ["Plain"])):
            with self.subTest(options=options):
                _out, args = self.gum_choose(options, "Plain\n", choose=odd)
                self.assertEqual(selected(args), want)

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_choose_with_a_default_real_gum(self):
        """The real gum 2.0.2 starts on the default: enter takes it, also with a comma in its label."""
        for options, value in (("--selected other", "other"), ("--selected two", "two"), ("", "keep")):
            with self.subTest(options=options):
                script = (f'myUI_GUM="{shutil.which("gum")}"\n'
                          f'fuUI_CHOOSE {options} "Pick" "Keep localhost:5000/tpot:keep" "Two, too:two" '
                          '"Other:other"; echo "rc=$?"')
                out = at_terminal(script, self.sandbox.env(), 100, 40, stdout_tty=False, keys=b"\r")
                self.assertTrue(out.endswith(f"{value}\nrc=0\n"), repr(out[-300:]))

    CONFIRM = 'fuUI_CONFIRM {options} "Push the images?" Push Skip; echo "rc=$?"'

    def test_confirm_with_a_default_plain(self):
        """fuUI_CONFIRM --default yes|no: enter takes the default, (Y/n) or (y/N) says which; y and n as
        before. Without --default the question of before: (y/n), enter is no answer."""
        for options, answer, rc in (("--default no", "\n", 1), ("--default no", "y\n", 0), ("--default no", "n\n", 1),
                                    ("--default yes", "\n", 0), ("--default yes", "n\n", 1),
                                    ("--default no", "x\n\n", 1), ("--default yes", "", 1),
                                    ("", "\ny\n", 0), ("", "\nn\n", 1), ("", "\n", 1)):
            with self.subTest(options=options, answer=answer):
                result = self.run_ui(self.CONFIRM.format(options=options), stdin=answer)
                self.assertEqual(result.stdout, f"rc={rc}\n")
        for options, prompt in (("--default no", "### Push the images? (y/N) "),
                                ("--default yes", "### Push the images? (Y/n) "),
                                ("", "### Push the images? (y/n) ")):
            with self.subTest(options=options):
                # enter, then n (for the question without a default, which asks again)
                out = at_terminal(self.CONFIRM.format(options=options), self.sandbox.env(TPOT_GUM="off"), 100, 30,
                                  keys=b"\rn\r")
                self.assertIn(prompt, out)
                self.assertRegex(out, "rc=1" if options != "--default yes" else "rc=0")

    def test_confirm_answers_plain(self):
        """y / n and yes / no in any case (the prompt shows Y or N), the spaces around them go (a CR of
        CRLF too); anything else asks again."""
        for source in self.sources():
            for options, answer, rc in (("--default yes", "Y\n", 0), ("--default yes", "yes\n", 0),
                                        ("--default yes", "YES\n", 0), ("--default yes", "Yes\n", 0),
                                        ("--default yes", "N\n", 1), ("--default yes", "No\n", 1),
                                        ("--default yes", "nO\n", 1), ("--default no", "N\ny\n", 1),
                                        ("--default no", "Y\n", 0), ("--default no", " YES \n", 0),
                                        ("", "Y\n", 0), ("", "NO\n", 1), ("", "yess\nn\n", 1), ("", "y\r\n", 0),
                                        ("", "ja\nyes\n", 0), ("", "y e s\n", 1), ("--default no", "\r\n", 1)):
                with self.subTest(source=source, options=options, answer=answer):
                    result = run(self.CONFIRM.format(options=options), self.sandbox.env(TPOT_GUM="off"),
                                 source=source, stdin=answer)
                    self.assertEqual(result.stdout, f"rc={rc}\n")

    def test_confirm_without_the_value_of_default(self):
        """fuUI_CONFIRM --default with its value forgotten: --default takes only yes or no, anything else
        is the question; so no default, the labels stay the labels (plain and gum)."""
        script = 'fuUI_CONFIRM --default "Push the images?" Push Skip; echo "rc=$?"'
        for source in self.sources():
            with self.subTest(source=source):
                out = at_terminal(script, self.sandbox.env(TPOT_GUM="off"), 100, 30, source=source, keys=b"\ry\r")
                self.assertIn("### Push the images? (y/n) ", out)
                self.assertRegex(out, r"rc=0\r?\n$")
        gum = fake_gum(self.sandbox.home)
        calls = os.path.join(self.sandbox.home, "gum.calls")
        at_terminal(f'myUI_GUM="{gum}"\n' + script, self.sandbox.env(), 100, 30)
        args = read(calls).split("\n")[1:-1]
        self.assertEqual([a for a in args if a.startswith("--default")], [])
        self.assertEqual(args[args.index("--affirmative") + 1:args.index("--affirmative") + 4],
                         ["Push", "--negative", "Skip"])
        self.assertEqual(args[args.index("--") + 1:], ["Push the images?"])

    def test_confirm_with_a_default_gum(self):
        gum = fake_gum(self.sandbox.home)
        calls = os.path.join(self.sandbox.home, "gum.calls")
        for options, flags in (("--default no", ["--default=false"]), ("--default yes", []), ("", [])):
            with self.subTest(options=options):
                if os.path.exists(calls):
                    os.remove(calls)
                at_terminal(f'myUI_GUM="{gum}"\n' + self.CONFIRM.format(options=options), self.sandbox.env(), 100, 30)
                args = read(calls).split("\n")[1:-1]
                self.assertEqual(args[0], "confirm")
                self.assertEqual([a for a in args if a.startswith("--default")], flags)
                self.assertEqual(args[args.index("--affirmative") + 1:args.index("--affirmative") + 4],
                                 ["Push", "--negative", "Skip"])
                self.assertEqual(args[args.index("--") + 1:], ["Push the images?"])

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_confirm_with_a_default_real_gum(self):
        """The real gum 2.0.2: enter takes the default button."""
        for options, rc in (("--default no", 1), ("--default yes", 0), ("", 0)):
            with self.subTest(options=options):
                script = f'myUI_GUM="{shutil.which("gum")}"\n' + self.CONFIRM.format(options=options)
                out = at_terminal(script, self.sandbox.env(), 100, 30, keys=b"\r")
                self.assertTrue(out.endswith(f"rc={rc}\r\n") or out.endswith(f"rc={rc}\n"), out[-200:])

    # where the rule says no colours (fuUI_GUM): NO_COLOR that is not empty, a dumb TERM (dumb, unknown, any
    # case, empty or none)
    NO_COLOURS = ({"NO_COLOR": "1"}, {"TERM": "dumb"}, {"TERM": "unknown"}, {"TERM": "DUMB"}, {"TERM": ""},
                  {"TERM": None}, {"TERM": "unknown", "COLORTERM": "truecolor", "LC_TERMINAL": "iTerm2"})

    def test_confirm_without_colours_asks_plainly(self):
        """gum confirm shows the active button by its colour only: where the rule says no colours the
        question is the plain one, (y/N) / (Y/n) / (y/n), at a terminal with gum too; gum is not asked."""
        gum = fake_gum(self.sandbox.home)
        calls = os.path.join(self.sandbox.home, "gum.calls")
        for extra in self.NO_COLOURS:
            for options, prompt, keys, rc in (("--default no", "(y/N)", b"\r", 1), ("--default yes", "(Y/n)", b"\r", 0),
                                              ("", "(y/n)", b"n\r", 1)):
                with self.subTest(env=extra, options=options):
                    if os.path.exists(calls):
                        os.remove(calls)
                    out = at_terminal(f'myUI_GUM="{gum}"\n' + self.CONFIRM.format(options=options),
                                      self.sandbox.env(**extra), 100, 30, keys=keys)
                    self.assertIn(f"### Push the images? {prompt} ", out)
                    self.assertRegex(out, rf"rc={rc}\r?\n$")
                    self.assertFalse(os.path.exists(calls), read(calls) if os.path.exists(calls) else "")
        with self.subTest("with colours gum asks"):
            out = at_terminal(f'myUI_GUM="{gum}"\n' + self.CONFIRM.format(options="--default no"),
                              self.sandbox.env(NO_COLOR=""), 100, 30)
            self.assertNotIn("(y/N)", out)
            self.assertEqual(read(calls).split("\n")[1], "confirm")

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_confirm_without_colours_real_gum(self):
        """The real gum 2.0.2 after fuUI_INIT: with colours its buttons, the active one on magenta; where
        the rule says none the plain question (gum would show "Yes  No" with nothing that marks one)."""
        folder = os.path.join(self.sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        os.symlink(shutil.which("gum"), os.path.join(folder, "gum"))
        script = 'fuUI_INIT; [ -n "${myUI_GUM}" ] || echo "no gum"; fuUI_CONFIRM --default no "Sure?"; echo "rc=$?"'
        for extra in self.NO_COLOURS:
            with self.subTest(env=extra):
                out = at_terminal(script, self.sandbox.env(**extra), 100, 30, keys=b"\r")
                self.assertNotIn("no gum", out)
                self.assertIn("### Sure? (y/N) ", out)
                self.assertNotIn("Yes", out)
                self.assertRegex(out, r"rc=1\r?\n$")
        with self.subTest("with colours"):
            out = at_terminal(script, self.sandbox.env(), 100, 30, keys=b"\r")
            self.assertNotIn("(y/N)", out)
            self.assertIn("Yes", plain(out))
            self.assertIn("\x1b[48;5;162m", out)                       # the active button, No, on magenta
            self.assertRegex(out, r"rc=1\r?\n$")

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_choose_marks_its_cursor_without_colours_real_gum(self):
        """gum choose and filter mark the cursor and the marked items with a character, so they stay as they
        are where the rule says no colours: choose "> " before the item it is on, choose --no-limit
        "> " and "✓", filter "•" and "◉"."""
        folder = os.path.join(self.sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        os.symlink(shutil.which("gum"), os.path.join(folder, "gum"))
        calls = (('fuUI_CHOOSE --selected b "Pick" "A:a" "B:b" "C:c"', ("> B",), "b"),
                 ('fuUI_CHOOSE_MANY --selected b "Many" "A:a" "B:b" "C:c"', ("> • A", "✓ B", "• C"), "b"),
                 ('fuUI_CHOOSE_MANY --filter --selected b "Filter" "A:a" "B:b" "C:c"', ("• ○ A", "◉ B"), "b"))
        for extra in self.NO_COLOURS:
            for call, marks, value in calls:
                with self.subTest(env=extra, call=call.split()[0] + (" --filter" if "--filter" in call else "")):
                    out = at_terminal(f'fuUI_INIT; [ -n "${{myUI_GUM}}" ] || echo "no gum"; {call}; echo "rc=$?"',
                                      self.sandbox.env(**extra), 100, 30, keys=b"\r")
                    self.assertNotIn("no gum", out)
                    self.assertNotIn("38;", out)                       # no colours
                    for mark in marks:
                        self.assertIn(mark, plain(out))
                    self.assertRegex(out, rf"{value}\r?\nrc=0\r?\n$")

    def test_input_under_set_u(self):
        """fuUI_INPUT without its second argument under set -u (ui.sh, the plain fallback, gum)."""
        fallback = os.path.join(self.sandbox.home, "fallback.sh")
        with open(fallback, "w", encoding="utf-8") as out:
            out.write(ui_logo.FALLBACK)
        script = 'set -u; myV=$(fuUI_INPUT "Name:"); echo "rc=$? [${myV}]"'
        for source in (UI_SH, fallback):
            with self.subTest(source=os.path.basename(source)):
                result = run(script, self.sandbox.env(), source=source, stdin="someone\n")
                self.assertEqual((result.stdout, result.stderr), ("rc=0 [someone]\n", ""))
        gum = fake_gum(self.sandbox.home)
        out = at_terminal(f'myUI_GUM="{gum}"; {script}', self.sandbox.env(), 100, 30)
        self.assertNotIn("unbound", out)
        self.assertIn("rc=0 []", out)
        self.assertIn("input\n--header\nName:\n", read(os.path.join(self.sandbox.home, "gum.calls")))

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_choose_real_gum_splits_at_the_last_colon(self):
        """The real gum 2.0.2: enter takes the first item, its value comes back whole."""
        script = (f'myUI_GUM="{shutil.which("gum")}"\n'
                  'fuUI_CHOOSE "Pick" "Keep localhost:5000/tpot:keep" "Other:other"; echo "rc=$?"')
        out = at_terminal(script, self.sandbox.env(), 100, 40, stdout_tty=False, keys=b"\r")
        self.assertTrue(out.endswith("keep\nrc=0\n"), out[-300:])
        self.assertNotIn(":keep\n", out)                               # not "5000/tpot:keep"

    @unittest.skipUnless(shutil.which("gum") and "2.0.2" in subprocess.run(
        [shutil.which("gum") or "true", "--version"], capture_output=True, text=True).stdout, "no gum 2.0.2")
    def test_choose_many_real_gum_keeps_commas(self):
        """The real gum 2.0.2 marks the labels with commas and gives them back whole: enter takes the
        marked ones."""
        for verb in ("", "--filter "):
            with self.subTest(verb=verb or "choose"):
                script = (f'myUI_GUM="{shutil.which("gum")}"\n'
                          + self.COMMAS.format(options=verb + "--selected dionaea --selected tanner,redis,phpox")
                          + '; echo "rc=$?"')
                out = at_terminal(script, self.sandbox.env(), 100, 40, stdout_tty=False, keys=b"\r")
                self.assertTrue(out.endswith("tanner,redis,phpox\ndionaea\nrc=0\n"), out[-300:])

    def test_spin_passes_through_with_marks(self):
        """In the marks mode the output goes through (the T-Pot Manager reads it) and into the log
        as well, stdout and stderr together; the rc is the one of the step."""
        log = os.path.join(self.sandbox.home, "spin.log")
        script = f'fuUI_SPIN "Pulling ..." "{log}" sh -c "echo Pulled one; echo oops >&2"; echo "rc=$?"'
        result = self.run_ui(script, TPOT_MARKS="1")
        self.assertEqual(result.stdout, "### Pulling ...\nPulled one\noops\n### [OK] - Pulling\nrc=0\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(read(log), "Pulled one\noops\n")
        os.remove(log)
        result = self.run_ui("myMARKS=1; " + script.replace("echo oops >&2", "exit 3"))
        self.assertEqual(result.stdout, "### Pulling ...\nPulled one\nrc=3\n")
        self.assertEqual(result.stderr, "### [ERROR] - Pulling failed\n")
        self.assertEqual(read(log), "Pulled one\n")
        # the log is added to, as without the marks
        self.run_ui(script, TPOT_MARKS="1")
        self.assertEqual(read(log), "Pulled one\nPulled one\noops\n")
        # a log that cannot be written, or none: the output still goes through, the rc stays
        for target in (os.path.join(self.sandbox.home, "no", "such", "spin.log"), "/dev/null", ""):
            with self.subTest(log=target):
                result = self.run_ui(f'fuUI_SPIN "Pulling ..." "{target}" sh -c "echo Pulled one; exit 4"; '
                                     'echo "rc=$?"', TPOT_MARKS="1")
                self.assertEqual(result.stdout, "### Pulling ...\nPulled one\nrc=4\n")
                self.assertEqual(result.stderr, "### [ERROR] - Pulling failed\n")
        # under set -e -o pipefail, a function as the step, a stdin that is closed for it
        result = self.run_ui(f'set -e -o pipefail; fuJOB () {{ read -r myX || echo "no stdin"; return 5; }}\n'
                             f'fuUI_SPIN "Job ..." "{log}" fuJOB || echo "rc=$?"; echo after', TPOT_MARKS="1")
        self.assertEqual(result.stdout, "### Job ...\nno stdin\nrc=5\nafter\n")
        # without marks: into the log only
        result = self.run_ui(script)
        self.assertEqual(result.stdout, "### Pulling ...\n### [OK] - Pulling\nrc=0\n")
        self.assertIn("Pulled one", read(log))

    def test_spin_under_set_e(self):
        log = os.path.join(self.sandbox.home, "spin.log")
        result = self.run_ui(f'set -e; fuUI_SPIN "Step ..." "{log}" false || echo "rc=$?"; echo after')
        self.assertIn("rc=1\nafter\n", result.stdout)

    def test_ctrl_c_under_the_spinner_stops_the_step(self):
        """gum reads Ctrl+C as a key in raw mode and ends with 130; the step in the background (it
        ignores SIGINT) and everything it started stop, fuUI_SPIN returns 130 at once."""
        gum = fake_gum(self.sandbox.home)
        home = self.sandbox.home
        log = os.path.join(home, "spin.log")
        # three levels below the job: its subshell, a shell, the shell with the sleep and the marker;
        # gum ends (Ctrl+C) once the sleep is there, so a slow machine cannot finish the step first
        job = os.path.join(home, "job.sh")
        with open(job, "w", encoding="utf-8") as out:
            out.write('sh -c \'echo $$ > "$1/inner"; sleep 30 & echo $! > "$1/sleep"; wait; touch "$1/marker"\' sh "$1"\n'
                      'true\n')
        script = (f'myUI_GUM="{gum}"\nfuJOB () {{ sh "{job}" "{home}"; }}\n'
                  f'fuUI_SPIN "Pulling the images ..." "{log}" fuJOB; echo "rc=$?"')
        start = time.time()
        result = self.run_ui(script, FAKE_GUM_SPIN_RC="130", FAKE_GUM_STOP="Pulling",
                             FAKE_GUM_WAIT=os.path.join(home, "sleep"))
        self.assertLess(time.time() - start, 20, result.stdout)            # the step takes 30 s
        self.assertTrue(result.stdout.endswith("rc=130\n"), result.stdout + result.stderr)
        self.assertIn("! Stopped: Pulling the images", result.stdout)
        self.assertNotIn("✓", result.stdout)
        # everything the step started is gone (a zombie is reaped soon)
        for name in ("inner", "sleep"):
            pid = int(read(os.path.join(home, name)))
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                self.fail(f"the {name} of the step went on after Ctrl+C")
        self.assertFalse(os.path.exists(os.path.join(home, "marker")), "the step went on after Ctrl+C")
        # gum ends with an error after the step is through: the result of the step counts
        result = self.run_ui(f'myUI_GUM="{gum}"; fuUI_SPIN "Step ..." "{log}" true; echo "rc=$?"',
                             FAKE_GUM_SPIN_RC="1")
        self.assertTrue(result.stdout.endswith("rc=0\n"), result.stdout + result.stderr)
        # only the spin of the stop ends early, the others wait for their step
        result = self.run_ui(f'myUI_GUM="{gum}"; fuUI_SPIN "Step ..." "{log}" sleep 0.5; echo "rc=$?"',
                             FAKE_GUM_SPIN_RC="130", FAKE_GUM_STOP="Pulling")
        self.assertTrue(result.stdout.endswith("rc=0\n"), result.stdout + result.stderr)

    def test_the_spinner_keeps_the_int_trap_of_the_caller(self):
        gum = fake_gum(self.sandbox.home)
        log = os.path.join(self.sandbox.home, "spin.log")
        spin = f'myUI_GUM="{gum}"; fuUI_SPIN "Step ..." "{log}" true >/dev/null; trap -p INT; echo end'
        self.assertEqual(self.run_ui(spin).stdout, "end\n")
        self.assertEqual(self.run_ui("trap 'echo caught' INT; " + spin).stdout, "trap -- 'echo caught' SIGINT\nend\n")

    def test_alive(self):
        """A process of root (sudo, the step it runs) cannot take kill -0 from a user, it is alive."""
        self.assertEqual(self.run_ui('fuUI_ALIVE 1; echo "$?"').stdout, "0\n")
        self.assertEqual(self.run_ui('fuUI_ALIVE "$$"; echo "$?"').stdout, "0\n")
        self.assertEqual(self.run_ui('sh -c "exit 0" & myP=$!; wait "${myP}"; fuUI_ALIVE "${myP}"; echo "$?"').stdout,
                         "1\n")
        # the spinner waits with the same test
        self.assertIn("/proc/", re.search(r"fuUI_SPIN \(\) \{.*?\n\}", read(UI_SH), re.S).group(0))

    def test_fold_breaks_only_what_is_too_wide(self):
        text = ("Short line\\nand its own break.\\n\\n"
                "A paragraph that is much too wide for twenty columns\\nand goes on here.\\n"
                "  - an indented item that stays as it is even if it is wide\\n\\nLast.")
        out = self.run_ui(f'fuUI_FOLD 20 $\'{text}\'').stdout
        self.assertEqual(out, "Short line\nand its own break.\n\n"
                              "A paragraph that is\nmuch too wide for\ntwenty columns and\ngoes on here.\n"
                              "  - an indented item that stays as it is even if it is wide\n\nLast.\n")

    def test_fold_counts_characters(self):
        """A width is characters, not bytes (mawk and the awk of macOS count bytes): umlauts, box
        characters and a hexagon take one column each, in any locale of the script."""
        words = ("Größenänderung über ein ──── Kästchen ⬢ Ölfässer äöü ÄÖÜ ß "
                 "groß grün weiß Übergröße").split()
        text = " ".join(words)

        def greedy(width):
            lines, line = [], ""
            for word in words:
                if line and len(line) + 1 + len(word) <= width:
                    line += " " + word
                elif line:
                    lines.append(line)
                    line = word
                else:
                    line = word
            return "\n".join(lines + [line]) + "\n"
        # the UTF-8 locales this system has (Debian has C.UTF-8 but no en_US.UTF-8 by default, macOS the other way)
        utf8 = [name for name in ("en_US.UTF-8", "C.UTF-8")
                if not run(":", self.sandbox.env(LC_ALL=name)).stderr]
        self.assertTrue(utf8, "no UTF-8 locale to test with")
        for locale in [{}, {"LC_ALL": "C"}] + [{"LC_ALL": name} for name in utf8]:
            for width in (12, 20, 33):
                with self.subTest(locale=locale, width=width):
                    out = run(f"fuUI_FOLD {width} '{text}'", self.sandbox.env(**locale))
                    self.assertEqual(out.stdout.encode("utf-8", "surrogateescape").decode("utf-8"), greedy(width))
                    self.assertEqual(out.stderr, "")
        # a paragraph that fits by characters stays as it is (it is wider in bytes)
        self.assertEqual(self.run_ui("fuUI_FOLD 10 'äöüäöüäöüä'").stdout, "äöüäöüäöüä\n")

    def test_help_follows_the_terminal(self):
        """At a terminal the help takes its width (100 at most); the texts never get narrower than
        26 columns past the flags."""
        script = f'fuUI_HELP "T" "t.sh" --opt "-x" "{"word " * 40}"'
        for cols, widest in ((60, 60), (200, 100)):
            text = at_terminal(script, self.sandbox.env(TPOT_GUM="off"), cols=cols, rows=24)
            lines = [line for line in text.replace("\r", "").splitlines() if "word" in line]
            self.assertLessEqual(max(len(line) for line in lines), widest, text)
            self.assertGreater(max(len(line) for line in lines), widest - 8, text)

    def test_tree(self):
        result = self.run_ui('sh -c \'sh -c "sleep 3; true"; true\' & myP=$!; sleep 0.5; myT=$(fuUI_TREE "${myP}"); echo ${myT}; kill ${myT}')
        pids = result.stdout.split()
        self.assertEqual(len(pids), 3, result.stdout)
        self.assertEqual(len(set(pids)), 3)

    def test_marks_on(self):
        self.assertEqual(self.run_ui('fuUI_MARKS_ON; echo "$?"').stdout, "1\n")
        self.assertEqual(self.run_ui('fuUI_MARKS_ON; echo "$?"', TPOT_MARKS="1").stdout, "0\n")
        self.assertEqual(self.run_ui('myMARKS=1; fuUI_MARKS_ON; echo "$?"').stdout, "0\n")
        self.assertEqual(self.run_ui('myMARKS=1; fuMARK phase pull').stdout, "@@tpot phase pull\n")
        self.assertEqual(self.run_ui('fuMARK phase pull').stdout, "")

    # uname -s and how fuUI_LINUX_ONLY names the system; None: it goes on
    SYSTEMS = (("Linux", None), ("Darwin", "macOS"), ("MINGW64_NT-10.0-19045", "Windows (MINGW64_NT-10.0-19045)"),
               ("MSYS_NT-10.0-19045", "Windows (MSYS_NT-10.0-19045)"), ("CYGWIN_NT-10.0", "Windows (CYGWIN_NT-10.0)"),
               ("FreeBSD", "FreeBSD"), ("", "an unknown system"))

    def test_linux_only(self):
        """Outside Linux a host script stops: an error, where it runs, the exit code (1 or its own).
        WSL2 is Linux (uname -s says so), it goes on."""
        path = fake_system(self.sandbox.home) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
        script = 'fuUI_LINUX_ONLY update.sh; echo "goes on"'
        for system, name in self.SYSTEMS:
            with self.subTest(system=system):
                result = self.run_ui(script, PATH=path, FAKE_UNAME_S=system)
                if name is None:
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "goes on\n", ""))
                    continue
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, f"### [ERROR] - update.sh does not run on {name}.\n"
                                                "###   update.sh runs on Linux: a T-Pot host, a build host or a "
                                                "VM, WSL2 on Windows.\n")
        result = self.run_ui('fuUI_LINUX_ONLY builder.sh 3; echo "goes on"', PATH=path, FAKE_UNAME_S="Darwin")
        self.assertEqual(result.returncode, 3)
        self.assertIn("builder.sh does not run on macOS.", result.stderr)
        # with gum the same words, the error on stderr
        gum = fake_gum(self.sandbox.home)
        result = self.run_ui(f'myUI_GUM="{gum}"; {script}', PATH=path, FAKE_UNAME_S="Darwin")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("✗ update.sh does not run on macOS.", result.stderr)
        self.assertIn("update.sh runs on Linux: a T-Pot host", result.stderr)

    def test_init_downloads_gum_on_linux_only(self):
        """Outside Linux fuUI_INIT never fetches the Linux gum; on Linux it tries (here it fails: the
        curl of the test)."""
        folder = fake_system(self.sandbox.home)
        calls = os.path.join(self.sandbox.home, "net.calls")
        env = dict(PATH=folder + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"))
        script = 'fuUI_INIT; echo "gum=[${myUI_GUM}]"'
        for system, name in self.SYSTEMS:
            with self.subTest(system=system):
                if os.path.exists(calls):
                    os.remove(calls)
                out = at_terminal(script, self.sandbox.env(FAKE_UNAME_S=system, **env), 100, 30)
                self.assertIn("gum=[]", out)
                self.assertEqual(os.path.exists(calls), name is None, out)

    # fuUI_VERSION_GE <version> <minimum>: rc 0 when the version is at least the minimum
    VERSION_GE = (
        ("24.04.10", "24.04.9", 0), ("24.04.9", "24.04.10", 1), ("v2.24.4-desktop.1", "2.24.4", 0),
        ("2.24.3", "2.24.4", 1), ("24.04", "24.04.0", 0), ("24.04.02", "24.04.2", 0), ("", "2.24.4", 1),
        ("abc", "2.24.4", 1), ("2.24.4", "2.24.4", 0), ("3", "2.24.4", 0), ("2.24.4+build.7", "2.24.5", 1),
        ("2.24.4-rc1", "2.24.4", 0), ("1.2.3.5", "1.2.3.4", 0), ("1.2.3.3", "1.2.3.4", 1),
        ("1.2.3", "1.2.3.1", 1), ("2.24.4\r", "2.24.4", 0), (" 2.24.5 ", "2.24.4", 0), ("08.09", "8.9", 0),
        ("2..4", "2.0.4", 1), ("2.x.4", "2.0.0", 1), ("2.24.4", "", 1), ("2.24.4", "abc", 1),
        ("", "", 1),
        # the minimum the same way as the version: a leading v, a suffix after - or + (update.sh hands
        # the version of the checkout in as the minimum, a release candidate there is that release)
        ("24.04.3", "24.04.2-rc1", 0), ("24.04.2", "24.04.2-rc1", 0), ("24.04.1", "24.04.2-rc1", 1),
        ("2.24.4", "v2.24.4", 0), ("2.24.3", "v2.24.4", 1), ("v2.24.4", "v2.24.4+build.1", 0),
        ("24.04.3", " v24.04.2-rc1\r", 0), ("24.04.3", "-rc1", 1), ("24.04.3", "v", 1),
        # a trailing (or leading) dot is no version, on either side
        ("24.04.", "24.04", 1), ("24.04", "24.04.", 1), (".24.04", "24.04", 1), ("24.04", ".24", 1),
        ("24.04.-rc1", "24.04", 1), ("vv24.04", "24.04", 1),
    )

    def test_version_ge(self):
        for version, minimum, rc in self.VERSION_GE:
            with self.subTest(version=version, minimum=minimum):
                result = self.run_ui(f'fuUI_VERSION_GE $\'{version}\' $\'{minimum}\'; echo "rc=$?"')
                self.assertEqual(result.stdout, f"rc={rc}\n", result.stderr)
                self.assertEqual(result.stderr, "")

    def test_version(self):
        sandbox = self.sandbox
        both = sandbox.checkout(version="99.1.0\n", env="TPOT_VERSION=24.04.1\n")
        env_only = sandbox.checkout(env="# T-Pot\nTPOT_TYPE=HIVE\nTPOT_VERSION: \"24.04.3\"\n")
        plain_env = sandbox.checkout(env="TPOT_VERSION=24.04.4 # the release\n")
        nothing = sandbox.checkout()
        odd = sandbox.checkout(version="$(reboot)\n", env="TPOT_VERSION=24.04.5\n")
        for folder, version in ((both, "99.1.0"), (env_only, "24.04.3"), (plain_env, "24.04.4"),
                                (nothing, ""), (odd, "24.04.5")):
            self.assertEqual(self.run_ui(f'fuUI_VERSION "{folder}"').stdout, version + "\n", folder)
        self.assertEqual(self.run_ui(f'myUI_VERSION=1.0; fuUI_VERSION "{both}"').stdout, "1.0\n")
        # the checkout ui.sh lies in, ~/tpotce for the copy in install.sh
        self.assertEqual(self.run_ui("fuUI_VERSION").stdout, read(os.path.join(REPO, "version")).strip() + "\n")
        self.assertEqual(self.run_ui('echo "${myUI_CHECKOUT}"').stdout.strip(), REPO)
        home_checkout = os.path.join(sandbox.home, "tpotce")
        os.makedirs(home_checkout)
        with open(os.path.join(home_checkout, "version"), "w", encoding="utf-8") as out:
            out.write("42.0.1\n")
        block = os.path.join(sandbox.home, "block.sh")
        text = read(INSTALL_SH)
        with open(block, "w", encoding="utf-8") as out:
            out.write(text[text.index("# >>> tpot ui >>>"):text.index("# <<< tpot ui <<<")])
        self.assertEqual(run("fuUI_VERSION", sandbox.env(), source=block).stdout, "42.0.1\n")


@unittest.skipUnless(BASH and OLD_BASH, "no bash 3 (/bin/bash of macOS)")
class OldBashTest(unittest.TestCase):
    """On macOS (/bin/bash 3.2) the scripts get through ui.sh as far as fuUI_LINUX_ONLY: -h shows the
    help everywhere, anything else stops there with its error. What they run of ui.sh on the way is
    bash 3.2 too (no ${x,,}, no [-1], no mapfile): fuUI_INIT (with fuUI_COLORS, fuUI_PREF), fuUI_HELP,
    fuUI_USAGE_ERROR, fuUI_LINUX_ONLY; and the same of the plain fallback."""

    SCRIPT = ('fuUI_INIT\n' + HELP + '\nfuUI_USAGE_ERROR "Unknown option -Z." update.sh\n'
              'fuUI_LINUX_ONLY update.sh\necho "goes on"')

    def setUp(self):
        self.sandbox = Sandbox(self)
        self.sandbox.prefs('{\n  "icons": "unicode",\n  "colors": "auto"\n}\n')
        folder = os.path.join(self.sandbox.home, "data", "tpotce", "bin")
        os.makedirs(folder)
        shutil.move(fake_gum(self.sandbox.home), os.path.join(folder, "gum"))
        self.path = fake_system(self.sandbox.home) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")

    def env(self, system, **extra):
        return self.sandbox.env(PATH=self.path, FAKE_UNAME_S=system, TERM="XTERM-256color", COLORTERM="TrueColor",
                                LC_TERMINAL="iTerm2", **extra)

    def test_the_way_to_linux_only_at_a_terminal(self):
        for system in ("Darwin", "Linux"):
            with self.subTest(system=system):
                out = at_terminal(self.SCRIPT, self.env(system), 100, 40, bash=OLD_BASH)
                self.assertNotIn("bad substitution", out)
                self.assertNotIn("ui.sh: line", out)
                self.assertIn(HELP_TEXT, plain(out))
                self.assertIn("\x1b[1;38;2;226;0;116mT-Pot Installer", out)          # the colour rule ran
                self.assertIn("✗ Unknown option -Z.", out)                             # with gum
                self.assertTrue(os.path.exists(os.path.join(self.sandbox.home, "gum.calls")))
                if system == "Darwin":
                    self.assertIn("✗ update.sh does not run on macOS.", out)
                    self.assertNotIn("goes on", out)
                else:
                    self.assertTrue(out.endswith("goes on\n"), out[-200:])
        # 16 colours: gum gets the ANSI colours of the palette (fuUI_GUM), bash 3.2 too
        calls = os.path.join(self.sandbox.home, "gum.calls")
        os.remove(calls)
        out = at_terminal(self.SCRIPT, self.env("Darwin", TPOT_COLORS="16"), 100, 40, bash=OLD_BASH)
        self.assertNotIn("ui.sh: line", out)
        self.assertIn("✗ update.sh does not run on macOS.", out)
        self.assertIn("style\n--foreground\n9\n--bold\n--\n✗ Unknown option -Z.\n", read(calls))

    def test_the_way_to_linux_only_without_a_terminal(self):
        fallback = os.path.join(self.sandbox.home, "fallback.sh")
        with open(fallback, "w", encoding="utf-8") as out:
            out.write(ui_logo.FALLBACK)
        for source in (UI_SH, fallback):
            with self.subTest(source=os.path.basename(source)):
                result = run(self.SCRIPT, self.env("Darwin"), source=source, bash=OLD_BASH)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, HELP_TEXT)
                self.assertEqual(result.stderr, "### [ERROR] - Unknown option -Z.\n###   update.sh -h shows the options.\n"
                                                "### [ERROR] - update.sh does not run on macOS.\n"
                                                "###   update.sh runs on Linux: a T-Pot host, a build host or a VM, "
                                                "WSL2 on Windows.\n")


def script_fallback(path):
    text = read(path)
    if "# >>> plain fallback" not in text:
        return None, text
    start = text.index("# >>> plain fallback")
    end = text.index("# <<< plain fallback")
    return text[start:end], text[:start] + text[end:]


def functions(text):
    """The functions of a fallback block (four spaces in, a one liner or up to its line "    }"),
    by name."""
    out = {}
    name, lines = None, []
    for line in text.split("\n"):
        match = re.match(r"    (fu\w+) \(\) \{(.*)$", line)
        if name is None and match:
            if match.group(2).strip():
                out[match.group(1)] = line
            else:
                name, lines = match.group(1), [line]
        elif name is not None:
            lines.append(line)
            if line == "    }":
                out[name] = "\n".join(lines)
                name = None
    return out


SCRIPTS = ("install.sh", "update.sh", "restore.sh", "uninstall.sh", "genuser.sh", "deploy.sh",
           "docker/_builder/builder.sh",
           "docker/tpotinit/dist/bin/hptest.sh", "docker/tpotinit/dist/bin/attackmap_pipeline_test.sh")


@unittest.skipUnless(BASH, "no bash")
class CheckoutTest(unittest.TestCase):
    """ui.sh finds the checkout it lies in, also with an exported CDPATH (a cd into a relative folder then
    prints the folder it found, and myUI_CHECKOUT would be two lines)."""

    def test_an_exported_cdpath_changes_nothing(self):
        with tempfile.TemporaryDirectory() as work:
            lib = os.path.join(work, "co", "installer", "lib")
            os.makedirs(lib)
            shutil.copy(UI_SH, lib)
            script = 'source co/installer/lib/ui.sh; printf "[%s]" "${myUI_CHECKOUT}"'
            env = dict(os.environ, HOME=work, TPOT_GUM="off")
            for cdpath in (None, ".", ".:/nonexistent-tpot-cdpath"):
                environment = dict(env)
                environment.pop("CDPATH", None)
                if cdpath is not None:
                    environment["CDPATH"] = cdpath
                with self.subTest(cdpath=cdpath):
                    out = subprocess.run([BASH, "-c", script], env=environment, cwd=work, capture_output=True,
                                         universal_newlines=True, timeout=20)
                    got = out.stdout
                    self.assertTrue(got.startswith("[") and got.endswith("]") and "\n" not in got, got)
                    self.assertEqual(os.path.realpath(got[1:-1]), os.path.realpath(os.path.join(work, "co")),
                                     out.stderr)


class FallbackTest(unittest.TestCase):
    """The plain fallback block of a script (a checkout without installer/lib/ui.sh) defines every
    fuUI_* the script calls; the canonical one (ui_logo.FALLBACK) speaks like ui.sh without gum."""

    def test_fallback_blocks_define_what_the_script_calls(self):
        names = set(re.findall(r"^\s*(fu(?:UI_\w+|MARK)) \(\)", read(UI_SH), re.M))
        for name in SCRIPTS:
            block, rest = script_fallback(os.path.join(REPO, name))
            if block is None:
                continue
            with self.subTest(script=name):
                defined = set(re.findall(r"(fu(?:UI_\w+|MARK))\s*\(\)", block))
                own = set(re.findall(r"^\s*(fu\w+)\s*\(\)", rest, re.M))
                called = set(re.findall(r"\b(fuUI_\w+|fuMARK)\b", rest)) - own
                self.assertEqual(sorted((called & names) - defined), [], name)

    def test_canonical_fallback_has_the_new_helpers(self):
        defined = set(re.findall(r"^\s*(fu(?:UI_\w+|MARK)) \(\)", ui_logo.FALLBACK, re.M))
        for name in ("fuUI_INIT", "fuUI_BANNER", "fuUI_INFO", "fuUI_OK", "fuUI_WARN", "fuUI_ERROR", "fuUI_HINT",
                     "fuUI_CONFIRM", "fuUI_CHOOSE", "fuUI_INPUT",
                     "fuUI_MARKS_ON", "fuMARK", "fuUI_LOGO", "fuUI_VERSION", "fuUI_VERSION_GE", "fuUI_HELP",
                     "fuUI_USAGE_ERROR", "fuUI_RESULT", "fuUI_SUMMARY", "fuUI_CHOOSE_MANY", "fuUI_SPIN",
                     "fuUI_LINUX_ONLY"):
            self.assertIn(name, defined)
        self.assertNotIn("# >>> plain fallback", ui_logo.FALLBACK)
        self.assertTrue(all(line.startswith("    ") or not line for line in ui_logo.FALLBACK.split("\n")))

    def test_fallback_blocks_run(self):
        sandbox = Sandbox(self)
        fallback = os.path.join(sandbox.home, "fallback.sh")
        with open(fallback, "w", encoding="utf-8") as out:
            out.write(ui_logo.FALLBACK)
        log = os.path.join(sandbox.home, "spin.log")
        checkout = sandbox.checkout(version="99.1.0\n")
        calls = [
            HELP,
            'fuUI_USAGE_ERROR "Unknown option -Z." install.sh; echo "rc=$?"',
            'fuUI_SUMMARY "Update" "ok:pulled" "next:tpot" "warn:a: b" "info:c" "fail:start"; echo "rc=$?"',
            'fuUI_RESULT ok "x"; fuUI_RESULT fail "y"; echo "rc=$?"',
            'fuUI_LOGO; echo "rc=$?"',
            f'fuUI_VERSION "{checkout}"; myUI_VERSION=2; fuUI_VERSION',
            'fuUI_MARKS_ON; echo "$?"; TPOT_MARKS=1 fuMARK phase x',
            f'fuUI_SPIN "Step ..." "{log}" sh -c "echo out"; echo "rc=$?"',
            f'TPOT_MARKS=1; fuUI_SPIN "Step ..." "{log}" sh -c "echo out; exit 2"; echo "rc=$?"',
            f'rm -f "{log}.marks"; TPOT_MARKS=1; fuUI_SPIN "Step ..." "{log}.marks" sh -c "echo out; echo err >&2"; '
            f'echo "rc=$?"; cat "{log}.marks"',
        ]
        calls += [f'fuUI_VERSION_GE $\'{version}\' $\'{minimum}\'; echo "rc=$?"'
                  for version, minimum, _rc in HelpersTest.VERSION_GE]
        # the base helpers, as ui.sh speaks without gum
        calls += [
            'fuUI_INIT; echo "rc=$? gum=[${myUI_GUM}]"',
            'myLINE=keep; fuUI_BANNER "Installer" "one" "two  spaces"; echo "${myLINE}"',
            'myUI_LOGO=1; fuUI_BANNER "Updater"; fuUI_BANNER "Only"',
            'fuUI_INFO "a  b" c; fuUI_OK done; fuUI_WARN "careful: x"; fuUI_ERROR "bad"; echo "rc=$?"',
            'fuUI_INFO "First:"$\'\\n\'"  second"; fuUI_BANNER "Builder" "one"$\'\\n\'"two" "three"',
            'fuUI_INFO ""; fuUI_INFO; fuUI_BANNER "Empty" ""',
            'myLINE=keep; fuUI_HINT "one" "two three"; fuUI_HINT; echo "${myLINE}"',
        ]
        stdin_calls = [
            ('fuUI_CONFIRM "Go on?"; echo "rc=$?"', ("y\n", "n\n", "yes\nY\nn\n", "x\ny\n", "")),
            ('fuUI_CONFIRM "Go on?" Sure Never; echo "rc=$?"', ("y\n",)),
            ('fuUI_CONFIRM --default no "Go on?" Sure Never; echo "rc=$?"', ("y\n", "\n", "x\n\n", "n\n", "")),
            ('fuUI_CONFIRM --default yes "Go on?"; echo "rc=$?"', ("\n", "n\n", "maybe\ny\n", "")),
            ('fuUI_CHOOSE "Pick" "One:1" "Two, too:2" "Three: a b:3"; echo "rc=$?"', ("2\n", "0\n9\nx\n3\n", "")),
            ('fuUI_CHOOSE --selected 2 "Pick" "One:1" "Two, too:2" "Three: a b:3"; echo "rc=$?"',
             ("\n", "3\n", " \n", "0\n\n", "")),
            ('fuUI_CHOOSE --selected 9 "Pick" "One:1" "Two, too:2"; echo "rc=$?"', ("\n1\n", "")),
            ('myV=$(fuUI_INPUT "Name:"); echo "rc=$? [${myV}]"', ("someone\n", "", "  a b \n")),
            ('myV=$(fuUI_INPUT "Password:" password); echo "rc=$? [${myV}]"', ("secret\n", "")),
        ]
        path = fake_system(sandbox.home) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
        for system, _name in HelpersTest.SYSTEMS:
            for call in ('fuUI_LINUX_ONLY update.sh; echo "goes on"', 'fuUI_LINUX_ONLY builder.sh 3; echo "goes on"'):
                with self.subTest(call=call, system=system):
                    env = sandbox.env(TPOT_GUM="off", PATH=path, FAKE_UNAME_S=system)
                    one = run(call, env, source=UI_SH)
                    two = run(call, env, source=fallback)
                    self.assertEqual((two.returncode, two.stdout, two.stderr), (one.returncode, one.stdout, one.stderr))
        for call, answers in stdin_calls:
            for answer in answers:
                with self.subTest(call=call[:40], answer=answer):
                    env = sandbox.env(TPOT_GUM="off")
                    one = run(call, env, source=UI_SH, stdin=answer)
                    two = run(call, env, source=fallback, stdin=answer)
                    self.assertEqual((two.stdout, two.stderr), (one.stdout, one.stderr))
        for call in calls:
            with self.subTest(call=call[:40]):
                env = sandbox.env(TPOT_GUM="off")
                one = run(call, env, source=UI_SH)
                two = run(call, env, source=fallback)
                self.assertEqual((two.stdout, two.stderr), (one.stdout, one.stderr))
        for answer in ("1,3-4\n", "a\n", "\n", "x\n2\n", ""):
            with self.subTest(answer=answer):
                for call in (HelpersTest.CHOOSE.format(options="--selected data"),
                             HelpersTest.COMMAS.format(options="--selected cowrie --selected tanner,redis,phpox")):
                    one = run(call + '; echo "rc=$?"', sandbox.env(), source=UI_SH, stdin=answer)
                    two = run(call + '; echo "rc=$?"', sandbox.env(), source=fallback, stdin=answer)
                    self.assertEqual((two.stdout, two.stderr), (one.stdout, one.stderr))

    def test_fallback_copies_are_the_canonical_ones(self):
        """Every helper in the fallback block of a script of ui_logo.FALLBACK_FILES is the one of
        FALLBACK, letter for letter, and the block is the generated one (python3 -m tpotctl.ui_logo
        writes them, --check finds a changed one)."""
        canonical = functions(ui_logo.FALLBACK)
        for path in ui_logo.FALLBACK_FILES:
            name = os.path.relpath(path, REPO)
            block, _rest = script_fallback(path)
            with self.subTest(script=name):
                self.assertIsNotNone(block, name)
                for function, text in functions(block).items():
                    self.assertEqual(text, canonical.get(function), f"{function} in {name}")
        self.assertEqual(ui_logo.check_fallback(), [], "run python3 -m tpotctl.ui_logo")

    def test_fallback_files_and_the_excluded(self):
        """Every script with a fallback block is generated, or left out with a reason: the scripts of
        the tpotinit image never have ui.sh, their small block is their look there."""
        found = set()
        for top, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("data", "node_modules")]
            for file in files:
                if file.endswith(".sh") and "\n# >>> plain fallback" in read(os.path.join(top, file)):
                    found.add(os.path.relpath(os.path.join(top, file), REPO))
        generated = {os.path.relpath(path, REPO) for path in ui_logo.FALLBACK_FILES}
        self.assertEqual(generated, {"update.sh", "restore.sh", "genuser.sh", "deploy.sh",
                                     "docker/_builder/builder.sh"})
        self.assertEqual(found, generated | set(ui_logo.FALLBACK_EXCLUDED))
        self.assertFalse(generated & set(ui_logo.FALLBACK_EXCLUDED))
        for name, reason in ui_logo.FALLBACK_EXCLUDED.items():
            self.assertTrue(name.startswith("docker/tpotinit/"), name)
            self.assertIn("tpotinit", reason)

    def test_the_blocks_of_the_tpotinit_image(self):
        """The small blocks of hptest.sh and the pipeline test (inside the tpotinit image): the helpers
        they share with FALLBACK are the canonical ones (no myLINE leaks out of fuUI_BANNER), fuMARK is
        their own (TPOT_MARKS only) and runs under set -u."""
        canonical = functions(ui_logo.FALLBACK)
        sandbox = Sandbox(self)
        for name in ui_logo.FALLBACK_EXCLUDED:
            block, _rest = script_fallback(os.path.join(REPO, name))
            with self.subTest(script=name):
                own = functions(block)
                self.assertIn("fuMARK", own)
                for function, text in own.items():
                    if function != "fuMARK":
                        self.assertEqual(text, canonical.get(function), f"{function} in {name}")
                path = os.path.join(sandbox.home, os.path.basename(name))
                with open(path, "w", encoding="utf-8") as out:
                    out.write(block)
                script = ('set -u; myLINE=keep; fuUI_BANNER "Checks" "one" "two"; echo "${myLINE}"; '
                          'fuMARK phase x; echo "rc=$?"; TPOT_MARKS=1 fuMARK phase y; echo "rc=$?"')
                result = run(script, sandbox.env(), source=path)
                self.assertEqual((result.stdout, result.stderr),
                                 ("\n### T-Pot Checks\n### one\n### two\n\nkeep\nrc=0\n@@tpot phase y\nrc=0\n", ""))

    def test_generator_writes_and_checks_the_fallback_blocks(self):
        folder = tempfile.mkdtemp(prefix="tpot-ui-fallback-")
        self.addCleanup(shutil.rmtree, folder)
        text = read(os.path.join(REPO, "deploy.sh"))
        copy = os.path.join(folder, "deploy.sh")

        def put(content):
            with open(copy, "w", encoding="utf-8") as out:
                out.write(content)
            os.chmod(copy, 0o750)

        # a changed helper is found and written anew; mode and the rest of the file stay
        changed = text.replace('    fuUI_OK () { echo "### [OK] - $*"; }', '    fuUI_OK () { echo "## [OK] - $*"; }')
        self.assertNotEqual(changed, text)
        put(changed)
        self.assertEqual(ui_logo.check_fallback([copy]), [copy])
        self.assertEqual(ui_logo.write_fallback([copy]), [copy])
        self.assertEqual(ui_logo.check_fallback([copy]), [])
        self.assertEqual(read(copy), text)
        self.assertEqual(stat.S_IMODE(os.stat(copy).st_mode), 0o750)
        self.assertEqual(ui_logo.write_fallback([copy]), [])
        # a helper the script calls comes into its block, with what it needs itself
        put(text + "\nfuUI_SPIN \"Step ...\" /tmp/x true\n")
        self.assertEqual(ui_logo.check_fallback([copy]), [copy])
        ui_logo.write_fallback([copy])
        block, _rest = script_fallback(copy)
        self.assertIn("fuUI_SPIN ()", block)
        self.assertIn("fuUI_MARKS_ON ()", block)
        # a helper of ui.sh the fallback does not have: the generator says which
        put(text + "\nfuUI_PAINT x y\n")
        with self.assertRaises(ValueError) as caught:
            ui_logo.write_fallback([copy])
        self.assertIn("fuUI_PAINT", str(caught.exception))
        result = subprocess.run([sys.executable, "-c", "import sys; from tpotctl import ui_logo; "
                                 f"sys.exit(1 if ui_logo.check_fallback([{copy!r}]) else 0)"],
                                cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True)
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_fallback_blocks_of_the_scripts_parse(self):
        for name in SCRIPTS:
            block, _rest = script_fallback(os.path.join(REPO, name))
            if block is None:
                continue
            with self.subTest(script=name):
                result = subprocess.run([BASH, "-n"], input=block, universal_newlines=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_generator_prints_the_fallback(self):
        result = subprocess.run([sys.executable, "-m", "tpotctl.ui_logo", "--fallback"], cwd=REPO,
                                stdout=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(result.stdout, ui_logo.FALLBACK)


if __name__ == "__main__":
    unittest.main()
