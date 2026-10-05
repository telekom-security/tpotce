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
            text = say.styled(kind, "done")
            self.assertTrue(text.startswith("\x1b[38;2;"), kind)
            self.assertIn(f"{glyph}", text)
            self.assertIn("done", text)

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
