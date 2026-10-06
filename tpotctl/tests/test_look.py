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
        for rows in (logo.WORDMARK,):
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
        self.assertIn("honeypot platform", middle)
        self.assertIn("█", middle)
        self.assertFalse(end.strip())                                     # and ends dark
        glyphs.set_mode("ascii")
        try:
            self.assertTrue(splash.frame(1.2).plain.isascii())
        finally:
            glyphs.set_mode("unicode")
        self.assertTrue(splash.fits(80, 24))
        self.assertFalse(splash.fits(40, 24))

    def test_splash_with_a_long_version_keeps_its_frame(self):
        from tpotctl import splash_anim
        for version in ("24.04.2-elk9.5.4", "24.04.2-a-very-long-test-tag-of-a-fork"):
            for scale in (1, 2):
                splash = splash_anim.Splash(version, scale)
                for step in range(0, 21):
                    rows = splash.frame(step / 10).plain.split("\n")
                    self.assertEqual({len(row) for row in rows}, {splash.width}, (version, scale, step))
                bottom = splash.frame(1.2).plain.split("\n")[-1]
                self.assertTrue(bottom.startswith("╚") and bottom.endswith("╝"), bottom)
                self.assertTrue("telekom security" in bottom, bottom)

    def test_splash_looks_like_a_bbs_logo(self):
        """A CP437 frame with the credits in it, letters shaded in steps, a colour cycle on the edges."""
        from tpotctl import splash_anim
        splash = splash_anim.Splash("24.04.2")
        early, filling, credits = (splash.frame(t).plain for t in (0.2, 0.5, 1.2))
        self.assertTrue("╔" in early and "═" in early, early)
        self.assertGreaterEqual(sum(shade in filling for shade in "░▒▓"), 2, filling)
        self.assertTrue("t-pot 24.04.2" in credits and "telekom security" in credits, credits)
        edge_colours = {colour for row in splash.cells(1.1) for char, colour in row if char == "█"}
        self.assertGreaterEqual(len(edge_colours & {"magenta", "comb", "glass"}), 2, edge_colours)
        glyphs.set_mode("ascii")
        try:
            for step in range(0, 21):
                self.assertTrue(splash.frame(step / 10).plain.isascii(), step)
        finally:
            glyphs.set_mode("unicode")

    def test_rendered_sizes(self):
        from tpotctl import logo
        glyphs.set_mode("unicode")
        self.assertEqual(len(logo.wordmark().plain.split("\n")), 3)
        glyphs.set_mode("ascii")
        try:
            self.assertEqual(logo.wordmark().plain, "t-pot")
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
            self.assertIn("Colours: 256", names)
            app.set_colors("256")
            with open(prefs.path(), encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["colors"], "256")
            app.set_colors("auto")
            self.assertNotIn("Theme", [c.title for c in app.get_system_commands(app.screen)])


@unittest.skipUnless(sys.modules.get("textual") or __import__("importlib").util.find_spec("textual"),
                     "Textual is not installed, run with the venv of tpot")
class CreditTest(unittest.IsolatedAsyncioTestCase):

    async def credit(self, width, branch="dev", chips=None):
        from tpotctl import app as tapp, ops
        from tpotctl.tests.test_app import FakeBackend, Recorder
        backend = FakeBackend()
        backend.status = lambda: ops.Status("24.04.2", branch, "abc1234", "STANDARD", "HIVE", "active",
                                            "/home/t/tpotce")
        app = tapp.TpotApp(backend=backend, runner=Recorder())
        async with app.run_test(size=(width, 40)) as pilot:
            await pilot.pause(0.6)
            widget = app.query_one("#header-credit")
            if chips is not None:
                chips.append(str(app.query_one("#header-chips").render()))
            return str(widget.render()) if widget.display else ""

    async def test_a_long_branch_gets_the_short_credit(self):
        chips = []
        short = await self.credit(130, "feature/maplibre-attack-map", chips)
        self.assertTrue("by Telekom Security" in short and "Deutsche" not in short, short)
        self.assertTrue("feature/maplibre-attack-map abc1234" in chips[0], chips[0])
        self.assertTrue("Deutsche Telekom Security GmbH" in await self.credit(130, "dev"))

    async def test_header_names_telekom_security(self):
        self.assertIn("Powered by Deutsche Telekom Security GmbH", await self.credit(150))
        short = await self.credit(110)
        self.assertIn("by Telekom Security", short)
        self.assertNotIn("Deutsche", short)
        self.assertEqual(await self.credit(80), "")

    async def test_header_repaint_while_the_manager_ends(self):
        """A late status update while the T-Pot Manager ends meets a header whose parts are gone."""
        from textual.css.query import NoMatches
        from tpotctl import app as tapp, ops
        from tpotctl.tests.test_app import FakeBackend, Recorder
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder())
        state = ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", "/x")
        async with app.run_test(size=(150, 40)) as pilot:
            await pilot.pause(0.4)
            await app.query_one("#wordmark").remove()
            with self.assertRaises(NoMatches):          # a broken header while it runs still shows
                app.update_header(state)
            app._closing = True
            try:
                app.update_header(state)                 # no crash on the way out
            finally:
                app._closing = False
            app._exit = True                             # exit() says so before anything closes
            try:
                app.update_header(state)
            finally:
                app._exit = False
            app._running = False                         # _shutdown says so first (run_test ends that way)
            try:
                app.update_header(state)
            finally:
                app._running = True


def pure_red(hexcolour: str) -> bool:
    """A colour that the xterm 256 palette turns into a pure red (the maroon of an SSH session)."""
    from rich.color import Color, ColorSystem
    rgb = Color.parse(hexcolour).downgrade(ColorSystem.EIGHT_BIT).get_truecolor()
    return rgb.red > 0 and rgb.green == 0 and rgb.blue == 0


class ColoursPrefTest(unittest.TestCase):
    """TPOT_COLORS / the colours of prefs.py: auto, truecolor or 256, before Textual is imported."""

    def test_colors_pref_and_env(self):
        with mock.patch.dict(os.environ, {"TPOT_COLORS": "256"}):
            self.assertEqual(prefs.load().colors, "256")
        with mock.patch.dict(os.environ, {"TPOT_COLORS": "plaid"}):
            self.assertEqual(prefs.load().colors, "auto")
        environ = {"TPOT_COLORS": "256"}
        with mock.patch.dict(os.environ, environ):
            target = {}
            prefs.apply_color_system(target)
            self.assertEqual(target, {"TEXTUAL_COLOR_SYSTEM": "256", "TPOT_COLORS_SET": "256"})
            target = {"TEXTUAL_COLOR_SYSTEM": "truecolor"}         # the user's own choice wins
            prefs.apply_color_system(target)
            self.assertEqual(target, {"TEXTUAL_COLOR_SYSTEM": "truecolor"})
        with mock.patch.dict(os.environ, {"TPOT_COLORS": "auto"}):
            target = {}
            prefs.apply_color_system(target)
            self.assertEqual(target, {})

    def test_a_restart_takes_the_new_choice(self):
        """Restart the Manager keeps the environment of the first start: what tpot set there is not the user's."""
        import tempfile
        with tempfile.TemporaryDirectory() as config, \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config}):
            os.environ.pop("TPOT_COLORS", None)
            prefs.save(prefs.Prefs(colors="truecolor"))
            target = {"TEXTUAL_COLOR_SYSTEM": "256", "TPOT_COLORS_SET": "256"}
            prefs.apply_color_system(target)
            self.assertEqual(target, {"TEXTUAL_COLOR_SYSTEM": "truecolor", "TPOT_COLORS_SET": "truecolor"})
            prefs.save(prefs.Prefs(colors="auto"))
            prefs.apply_color_system(target)
            self.assertEqual(target, {})

    def test_palette_and_textual_agree(self):
        """The palette follows what Textual renders with, whoever decided it."""
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        code = ("import os, sys; sys.path.insert(0, %r); from tpotctl import prefs; "
                "prefs.apply_color_system(os.environ); from tpotctl import theme; "
                "from textual import constants; print(constants.COLOR_SYSTEM, theme.color('comb'))" % root)
        for extra, expected in (({"TPOT_COLORS": "256"}, "256 #5f005f"),
                                ({"TPOT_COLORS": "truecolor", "TEXTUAL_COLOR_SYSTEM": "256"}, "256 #5f005f"),
                                ({"TPOT_COLORS": "256", "TEXTUAL_COLOR_SYSTEM": "truecolor"}, "truecolor #38001D")):
            env = {k: v for k, v in os.environ.items() if k not in ("TEXTUAL_COLOR_SYSTEM", "TPOT_COLORS")}
            env.update(extra)
            out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                                 universal_newlines=True)
            if "No module named 'textual'" in out.stderr:
                self.skipTest("Textual is not installed, run with the venv of tpot")
            self.assertEqual(out.stdout.strip(), expected, (extra, out.stderr))

    def test_the_customizer_sets_it_too(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "compose", "customizer.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue("prefs.apply_color_system(os.environ)" in text)
        self.assertLess(text.index("prefs.apply_color_system(os.environ)"), text.index("bootstrap.reexec("))

    def test_the_launcher_sets_it_before_textual(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "tpot"), encoding="utf-8") as handle:
            launcher = handle.read()
        self.assertTrue("prefs.apply_color_system(os.environ)" in launcher)
        self.assertLess(launcher.index("prefs.apply_color_system(os.environ)"), launcher.index("from tpotctl import cli"))


@unittest.skipUnless(sys.modules.get("textual") or __import__("importlib").util.find_spec("textual"),
                     "Textual is not installed, run with the venv of tpot")
class ColoursTest(unittest.IsolatedAsyncioTestCase):
    """Over SSH or in tmux the terminal says 256 colours: the T-Pot Manager brings a palette for it."""

    def setUp(self):
        from tpotctl import theme
        self.addCleanup(theme.build, "truecolor")

    async def colours(self, size=(170, 45)):
        import re
        from tpotctl import app as tapp
        from tpotctl.tests.test_app import FakeBackend, Recorder
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.5)
            found = set(re.findall(r"#[0-9a-fA-F]{6}\b", app.export_screenshot()))
            from textual.command import CommandPalette
            from tpotctl.screens.dialogs import ConfirmDialog
            for opener, screen in ((lambda: pilot.click("#svc-restart"), ConfirmDialog),   # dialogs dim the page
                                   (lambda: pilot.press("ctrl+p"), CommandPalette)):
                await opener()
                for _ in range(40):
                    await pilot.pause(0.05)
                    if isinstance(app.screen, screen):
                        break
                self.assertIsInstance(app.screen, screen)
                await pilot.pause(0.2)
                found |= set(re.findall(r"#[0-9a-fA-F]{6}\b", app.export_screenshot()))
                await pilot.press("escape")
                await pilot.pause(0.3)
            await pilot.press("down", "down")                   # Settings: rows, tabs, fields
            await pilot.pause(0.5)
            found |= set(re.findall(r"#[0-9a-fA-F]{6}\b", app.export_screenshot()))
            await pilot.press("right")                          # into the page: a focused field
            await pilot.pause(0.5)
            found |= set(re.findall(r"#[0-9a-fA-F]{6}\b", app.export_screenshot()))
        return found

    async def test_256_colours_show_no_red_surfaces(self):
        from tpotctl import theme
        theme.build("256")
        reds = {colour for colour in await self.colours() if pure_red(colour)}
        self.assertEqual(reds, set())

    async def test_a_choice_keeps_the_environment_out_of_the_file(self):
        """TPOT_COLORS=truecolor tpot once, then f2: the file keeps its own colours."""
        from tpotctl import app as tapp
        from tpotctl.tests.test_app import FakeBackend, Recorder
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder())
        with mock.patch.dict(os.environ, {"TPOT_COLORS": "truecolor", "TPOT_ICONS": "ascii"}):
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.3)
                app.set_icons("nerd")
                with open(prefs.path(), encoding="utf-8") as handle:
                    saved = json.load(handle)
                app.set_icons("unicode")
        self.assertEqual((saved["icons"], saved["colors"]), ("nerd", "auto"))

    def test_256_tokens_are_palette_entries(self):
        from rich.color import Color, ColorSystem
        from tpotctl import theme
        for name, value in theme.PALETTE_256.items():
            rgb = Color.parse(value).downgrade(ColorSystem.EIGHT_BIT).get_truecolor()
            shown = f"#{rgb.red:02x}{rgb.green:02x}{rgb.blue:02x}"
            if len(set(value[1:].lower()[i:i + 2] for i in (0, 2, 4))) == 1 and value.lower() not in ("#ffffff", "#000000"):
                # Rich takes a grey one step down its grey ramp: the shown grey is the one meant
                self.assertNotEqual(shown[1:3], "00", name)
                self.assertEqual(len({shown[1:3], shown[3:5], shown[5:7]}), 1, name)
            else:
                self.assertEqual(shown, value.lower(), name)

    def test_truecolor_look_is_unchanged(self):
        from tpotctl import theme
        theme.build("truecolor")
        self.assertEqual((theme.color("magenta"), theme.color("comb"), theme.TPOT_THEME.surface),
                         ("#E20074", "#38001D", "#1C000E"))
        theme.build("256")
        self.assertEqual(theme.color("comb"), theme.PALETTE_256["COMB_LIT"])


class NamesTest(unittest.TestCase):
    """T-Pot is the honeypot platform, the T-Pot Manager is this tool (the command tpot), the service is
    tpot.service: no text may mix them up."""

    WRONG = ("Restart tpot", "Quit tpot", "tpot starts", "start tpot anew", "the tpot service",
             "Rebuild tpot's", "return to tpot", "packages of tpot", "tpot ends", "page of tpot",
             "tpot does not change", "the tpot menu", "venv of tpot", "tpot could not", ". the T-Pot Manager")
    # the scripts and playbooks a person sees running: their texts, not their comments
    SCRIPTS = ("install.sh", "update.sh", "restore.sh", "uninstall.sh", "genuser.sh", "deploy.sh",
               "installer/install/tpot.yml", "installer/remove/tpot.yml")

    def test_names_say_what_they_mean(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        found = []
        for folder, _dirs, files in os.walk(root):
            if os.sep + "tests" in folder:
                continue
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8") as handle:
                        for number, line in enumerate(handle, 1):
                            found += [f"{os.path.relpath(path, root)}:{number}: {w}" for w in self.WRONG if w in line]
        repo = os.path.dirname(root)
        for script in self.SCRIPTS:
            with open(os.path.join(repo, script), encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    found += [f"{script}:{number}: {w}" for w in self.WRONG if w in line]
        self.assertEqual(found, [])

    def test_the_menu_is_the_t_pot_manager(self):
        try:
            from tpotctl import app as tapp
        except ImportError:
            self.skipTest("Textual is not installed, run with the venv of tpot")
        self.assertEqual(tapp.TpotApp.TITLE, "T-Pot Manager")


if __name__ == "__main__":
    unittest.main()
