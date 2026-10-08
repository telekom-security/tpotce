"""tpotctl.say: the messages of tpot look like the ones of installer/lib/ui.sh."""

import io
import os
import re
import subprocess
import unittest

from tpotctl import say
from tpotctl.tests import isolate

isolate()

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
UI = os.path.join(REPO, "installer", "lib", "ui.sh")
THEME = os.path.join(REPO, "tpotctl", "theme.py")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def theme_colours():
    """The hex values of theme.py, read as text: theme.py needs Textual."""
    text = read(THEME)
    colours = dict(re.findall(r'^(MAGENTA|GLASS|ASH) = "(#[0-9A-Fa-f]{6})"', text, re.M))
    ok, warn, error = re.search(r'^OK, WARN, ERROR = "(#\w+)", "(#\w+)", "(#\w+)"', text, re.M).groups()
    colours.update(OK=ok, WARN=warn, ERROR=error)
    return {k.lower(): v.upper() for k, v in colours.items()}


class Tty(io.StringIO):
    def isatty(self):
        return True


class SayTest(unittest.TestCase):

    def bash(self, call):
        env = dict(os.environ, TPOT_GUM="off")
        return subprocess.run(["bash", "-c", f'source "{UI}"; fuUI_INIT; {call} 2>&1'], env=env,
                              capture_output=True, universal_newlines=True).stdout.rstrip("\n")

    def test_plain_text_is_the_one_of_ui_sh(self):
        for kind, function in (("info", "fuUI_INFO"), ("ok", "fuUI_OK"), ("warn", "fuUI_WARN"),
                               ("error", "fuUI_ERROR")):
            with self.subTest(kind=kind):
                self.assertEqual(say.plain(kind, "a b"), self.bash(f'{function} "a b"'))

    def test_hint_plain_is_the_one_of_ui_sh(self):
        out = io.StringIO()
        say.hint("one", "two", stream=out)
        self.assertEqual(out.getvalue().rstrip("\n"), self.bash('fuUI_HINT one two'))

    def test_colours_match_theme_and_ui_sh(self):
        ui = read(UI)
        theme = theme_colours()
        for name, variable in (("magenta", "myUI_MAGENTA"), ("glass", "myUI_GLASS"), ("ash", "myUI_ASH"),
                               ("ok", "myUI_OK_COLOUR"), ("warn", "myUI_WARN_COLOUR"),
                               ("error", "myUI_ERROR_COLOUR")):
            with self.subTest(colour=name):
                self.assertEqual(say.COLOURS[name].upper(), theme[name])
                self.assertIn(f'{variable}="{say.COLOURS[name].upper()}"', ui)

    def test_styled_has_glyph_and_colour(self):
        for kind, glyph in (("info", "⬢"), ("ok", "✓"), ("warn", "!"), ("error", "✗")):
            text = say.styled(kind, "done", colors="truecolor")
            self.assertTrue(text.startswith("\x1b[38;2;"), kind)
            self.assertIn(f"{glyph}", text)
            self.assertIn("done", text)

    # the SGR of magenta (the sign of info) and glass (its text) per depth
    DEPTHS = {"truecolor": ("38;2;226;0;116", "38;2;236;239;249"), "256": ("38;5;162", "38;5;231"),
              "16": ("95", "97")}

    def test_styled_follows_the_colour_rule(self):
        """The depth of prefs.detect_colors (the rule of the scripts): 38;2 for true colour, the entries
        of theme.PALETTE_256 for 256, the ANSI colours of theme.PALETTE_16 for 16; a dumb terminal has
        the glyphs without colours, as gum there."""
        for env, depth in (({"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2"}, "truecolor"),
                           ({"TERM": "xterm-256color"}, "256"),
                           ({"TERM": "xterm"}, "16"),
                           ({"TERM": "linux"}, "16"),
                           ({"TERM": "xterm-256color", "COLORTERM": "truecolor", "TPOT_COLORS": "16"}, "16"),
                           ({"TERM": "xterm-256color", "STY": "1.pts-0.h", "COLORTERM": "truecolor"}, "256")):
            with self.subTest(env=env):
                environ = dict(env, HOME=os.environ["TPOT_TEST_CONFIG"],
                               XDG_CONFIG_HOME=os.environ["TPOT_TEST_CONFIG"])
                self.assertEqual(say.depth(environ), depth)
                magenta, glass = self.DEPTHS[depth]
                self.assertEqual(say.styled("info", "done", colors=say.depth(environ)),
                                 f"\x1b[{magenta}m⬢\x1b[0m \x1b[{glass}mdone\x1b[0m")
                if depth != "truecolor":
                    self.assertNotIn("38;2;", say.styled("warn", "x", colors=depth))
        for term in ("dumb", "unknown"):
            environ = {"TERM": term, "COLORTERM": "truecolor", "HOME": os.environ["TPOT_TEST_CONFIG"]}
            self.assertIsNone(say.depth(environ))
            self.assertEqual(say.styled("ok", "done", colors=None), "✓ done")
            self.assertEqual(say.styled("error", "bad", colors=None), "✗ bad")

    def test_tables_are_the_palettes_of_theme(self):
        """The 256 and 16 colours of say are the entries of theme.PALETTE_256 / PALETTE_16 (read as text:
        theme.py needs Textual)."""
        from tpotctl import ui_logo
        palettes = ui_logo.theme_palettes()
        for name in say.COLOURS:
            with self.subTest(colour=name):
                token = name.upper()
                self.assertEqual(palettes["truecolor"][token].upper(), say.COLOURS[name].upper())
                self.assertEqual(say.COLOURS_256[name], ui_logo.xterm_index(palettes["256"][token]))
                self.assertEqual(say.COLOURS_16[name], ui_logo.sgr16(palettes["16"][token]))

    def test_a_terminal_gets_the_depth_of_the_rule(self):
        """say.warn / hint to a terminal with TERM=xterm (PuTTY, no COLORTERM): no true colour."""
        keep = {k: os.environ.get(k) for k in ("TERM", "COLORTERM", "LC_TERMINAL", "TERM_PROGRAM", "TPOT_COLORS",
                                               "NO_COLOR", "TPOT_GUM", "STY", "TMUX", "VTE_VERSION", "WT_SESSION",
                                               "KONSOLE_VERSION")}
        try:
            for key in keep:
                os.environ.pop(key, None)
            os.environ["TERM"] = "xterm"
            out = Tty()
            say.warn("careful", stream=out)
            say.hint("a hint", stream=out)
            self.assertEqual(out.getvalue(), "\x1b[93m! careful\x1b[0m\n\x1b[37m    a hint\x1b[0m\n")
            os.environ["TERM"] = "dumb"
            out = Tty()
            say.warn("careful", stream=out)
            say.hint("a hint", stream=out)
            self.assertEqual(out.getvalue(), "! careful\n    a hint\n")
        finally:
            for key, value in keep.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_terminal_gets_the_styled_form(self):
        out = Tty()
        os.environ.pop("NO_COLOR", None)
        os.environ.pop("TPOT_GUM", None)
        say.ok("done", stream=out)
        self.assertIn("✓", out.getvalue())

    def test_no_color_and_gum_off_stay_plain(self):
        for variable, value in (("NO_COLOR", "1"), ("TPOT_GUM", "off")):
            with self.subTest(variable=variable):
                os.environ[variable] = value
                try:
                    out = Tty()
                    say.ok("done", stream=out)
                    self.assertEqual(out.getvalue(), "### [OK] - done\n")
                finally:
                    os.environ.pop(variable)

    def test_error_goes_to_stderr_by_default(self):
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            say.error("broken")
        self.assertEqual(err.getvalue(), "### [ERROR] - broken\n")


if __name__ == "__main__":
    unittest.main()


class UsersOfSayTest(unittest.TestCase):
    """tpot and the customizer write their messages through say."""

    def test_cli_error(self):
        import contextlib
        from tpotctl import cli
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cli.error("broken")
        self.assertEqual(err.getvalue(), "### [ERROR] - broken\n")

    def test_customizer_error(self):
        result = subprocess.run(["python3", os.path.join(REPO, "compose", "customizer.py"), "--base", "nosuch",
                                 "--output", os.path.join(os.environ["TPOT_TEST_CONFIG"], "out.yml")],
                                capture_output=True, universal_newlines=True,
                                env=dict(os.environ, TPOT_GUM="off"))
        self.assertTrue(result.stderr.startswith("### [ERROR] - "), result.stderr)

    def test_no_bare_ok_prints_left(self):
        for path in ("tpot", "tpotctl/cli.py", "compose/customizer.py"):
            with self.subTest(file=path):
                found = re.findall(r'.*"\[(?:OK|ERROR|WARNING)\] - .*', read(os.path.join(REPO, path)))
                self.assertEqual(found, [])
