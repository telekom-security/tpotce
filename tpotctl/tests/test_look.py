"""The look of tpot: prefs, icon sets, logo, the theme; switching the icons in the app."""

import json
import os
import sys
import unicodedata
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import glyphs, prefs  # noqa: E402

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
try:
    import rich  # noqa: F401
except ImportError:
    rich = None


class PrefsTest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("TPOT_ICONS", None)

    def test_defaults_without_a_file(self):
        self.assertEqual(prefs.load(), prefs.Prefs("unicode"))

    def test_save_and_load(self):
        self.assertTrue(prefs.save(prefs.Prefs("nerd")))
        self.assertEqual(prefs.load(), prefs.Prefs("nerd"))
        self.assertEqual(prefs.path(), os.path.join(self.tmp.name, "tpotce", "tpot.json"))

    def test_broken_or_unknown_values_give_the_defaults(self):
        os.makedirs(os.path.dirname(prefs.path()))
        with open(prefs.path(), "w") as handle:
            handle.write("{not json")
        self.assertEqual(prefs.load(), prefs.Prefs())
        with open(prefs.path(), "w") as handle:
            json.dump({"theme": "tpot-terminal", "icons": "emoji"}, handle)    # older file, unknown set
        self.assertEqual(prefs.load(), prefs.Prefs())

    def test_environment_wins(self):
        prefs.save(prefs.Prefs("nerd"))
        with mock.patch.dict(os.environ, {"TPOT_ICONS": "ascii"}):
            self.assertEqual(prefs.load(), prefs.Prefs("ascii"))

    def test_not_writable_is_no_crash(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/proc/no/such/place"}):
            self.assertFalse(prefs.save(prefs.Prefs()))


class GlyphsTest(unittest.TestCase):

    def tearDown(self):
        glyphs.set_mode("unicode")

    def test_every_set_has_every_glyph(self):
        names = set(glyphs.SETS["unicode"])
        for mode, table in glyphs.SETS.items():
            with self.subTest(mode=mode):
                self.assertEqual(set(table), names)

    def test_unicode_glyphs_are_one_cell(self):
        for name, glyph in list(glyphs.SETS["unicode"].items()) + [("spark", c) for c in glyphs.SPARK["unicode"]]:
            with self.subTest(name=name):
                self.assertEqual(len(glyph), 1)
                self.assertNotIn(unicodedata.east_asian_width(glyph), ("W", "F"))
                if rich:
                    from rich.cells import cell_len
                    self.assertEqual(cell_len(glyph), 1)

    def test_ascii_is_ascii(self):
        for glyph in list(glyphs.SETS["ascii"].values()) + list(glyphs.SPARK["ascii"]):
            self.assertTrue(glyph.isascii())

    def test_every_page_has_an_icon(self):
        if not textual:
            self.skipTest("Textual is not installed")
        from tpotctl import app as tapp
        for key, *_rest in tapp.PANES:
            self.assertIn(f"pane_{key}", glyphs.SETS["nerd"], f"page {key} needs an icon")

    def test_switching(self):
        glyphs.set_mode("ascii")
        self.assertEqual(glyphs.g("on"), "*")
        with self.assertRaises(ValueError):
            glyphs.set_mode("emoji")


@unittest.skipUnless(rich, "Rich is not installed")
class LogoTest(unittest.TestCase):

    def test_pixel_maps_are_rectangles_of_known_colours(self):
        from tpotctl import logo
        for rows in (logo.WORDMARK, logo.POT):
            self.assertEqual(len({len(r) for r in rows}), 1)
            self.assertTrue(set("".join(rows)) <= set(".") | set(logo._PIXEL))

    def test_wordmark_is_lower_case(self):
        from tpotctl import logo
        top, ascender_row = logo.WORDMARK[0], logo.WORDMARK[-1]
        self.assertEqual(top.count("#"), 2)                 # only the two t reach above the x-height
        self.assertEqual(ascender_row.count("#"), 1)        # only the p reaches below

    def test_splash_frames(self):
        from tpotctl import splash_anim
        splash = splash_anim.Splash("24.04.2")
        sizes = set()
        for step in range(0, 21):
            frame = splash.frame(step / 10).plain.split("\n")
            sizes.add((len(frame), max(len(line) for line in frame)))
        self.assertEqual(sizes, {(splash.height, splash.width)})          # it never jumps
        start, middle, end = (splash.frame(t).plain for t in (0.0, 1.2, splash_anim.DURATION))
        self.assertFalse(start.strip())                                   # starts dark
        self.assertIn("honeypot platform  24.04.2", middle)
        self.assertIn("█", middle)
        self.assertFalse(end.strip())                                     # and ends dark
        glyphs.set_mode("ascii")
        try:
            self.assertTrue(splash.frame(1.2).plain.isascii())
        finally:
            glyphs.set_mode("unicode")
        self.assertTrue(splash.fits(80, 24))
        self.assertFalse(splash.fits(40, 24))

    def test_rendered_sizes(self):
        from tpotctl import logo
        glyphs.set_mode("unicode")
        self.assertEqual(len(logo.wordmark().plain.split("\n")), 3)
        self.assertEqual(len(logo.pot().plain.split("\n")), 15)
        glyphs.set_mode("ascii")
        try:
            self.assertEqual(logo.wordmark().plain, "t-pot")
            self.assertTrue(logo.pot().plain.isascii())
        finally:
            glyphs.set_mode("unicode")


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class LookInTheAppTest(unittest.IsolatedAsyncioTestCase):

    def tearDown(self):
        glyphs.set_mode("unicode")

    def make_app(self):
        from tpotctl import app as tapp
        from tpotctl.tests.test_app import FakeBackend, Recorder
        return tapp.TpotApp(backend=FakeBackend(), runner=Recorder())

    async def test_one_theme_and_icons_that_stay(self):
        app = self.make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            self.assertEqual(app.theme, "tpot")
            await pilot.press("f2")                          # the icon sets, the theme stays
            await pilot.pause(0.2)
            self.assertEqual(glyphs.mode(), "nerd")
            self.assertEqual(app.theme, "tpot")
        self.assertEqual(prefs.load(), prefs.Prefs("nerd"))
        app = self.make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            self.assertEqual(glyphs.mode(), "nerd")
            app.set_icons("unicode")
        self.assertEqual(prefs.load(), prefs.Prefs("unicode"))

    async def test_splash_for_a_second_or_a_key(self):
        from tpotctl import app as tapp
        from tpotctl.screens.splash import SplashScreen
        from tpotctl.tests.test_app import FakeBackend, Recorder
        for wait, key in ((2.4, None), (0.2, "x")):
            app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), splash=True)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.1)
                self.assertIsInstance(app.screen, SplashScreen)
                if key:
                    await pilot.press(key)
                await pilot.pause(wait)
                self.assertNotIsInstance(app.screen, SplashScreen)
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), splash=True)
        async with app.run_test(size=(60, 18)) as pilot:     # no room for it
            await pilot.pause(0.1)
            self.assertNotIsInstance(app.screen, SplashScreen)

    async def test_narrow_menu_uses_short_titles(self):
        app = self.make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            self.assertTrue(app.screen.has_class("-narrow"))
            item = app.query_one("#menu-update")
            self.assertFalse(item.query_one(".menu-long").display)
            self.assertIn("Update", str(item.query_one(".menu-short").render()))

    async def test_header_shows_what_this_tpot_is(self):
        app = self.make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            chips = str(app.query_one("#header-chips").render())
            for text in ("HIVE", "STANDARD", "24.04.2"):
                self.assertIn(text, chips)
            self.assertIn("running", str(app.query_one("#header-service").render()))

    async def test_palette_goes_to_a_setting(self):
        from tpotctl.commands import TpotCommands
        app = self.make_app()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause(0.4)
            names = [name for name, _help, _cb in TpotCommands(app.screen).commands()]
            self.assertIn("Go to Settings", names)
            self.assertIn("Icons: nerd", names)
            self.assertNotIn("Theme", [c.title for c in app.get_system_commands(app.screen)])


if __name__ == "__main__":
    unittest.main()
