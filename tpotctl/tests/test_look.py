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

    def test_wordmark_is_capital_t_pot(self):
        from tpotctl import logo
        self.assertEqual((len(logo.WORDMARK), len(logo.WORDMARK[0])), (6, 21))
        top = logo.WORDMARK[0]
        self.assertTrue(top.startswith("#####"))            # the bar of the capital T
        self.assertEqual(top[5:].count("#"), 3)             # the bowl of the capital P, nothing else
        self.assertEqual({row[2] for row in logo.WORDMARK}, {"#"})   # the stem of the T: full height
        self.assertEqual({row[9] for row in logo.WORDMARK}, {"#"})   # the stem of the P: full height
        self.assertEqual("".join(row[14:] for row in logo.WORDMARK[:1]).count("#"), 0)  # o, t below

    def test_splash_sizes(self):
        """120 x 49 and more the large logo, 80 x 33 the middle one, 80 x 24 the tight one, below none."""
        from tpotctl import splash_anim
        cases = {(120, 49): "120", (200, 60): "120", (119, 49): "80", (120, 48): "80", (80, 33): "80",
                 (80, 32): "80x24", (100, 30): "80x24", (80, 24): "80x24", (79, 24): None, (80, 23): None}
        for (width, height), variant in cases.items():
            splash = splash_anim.best("24.04.2", width, height)
            self.assertEqual(splash.variant if splash else None, variant, (width, height))
        for variant, size in (("120", (120, 49)), ("80", (80, 33)), ("80x24", (80, 24))):
            splash = splash_anim.Splash("24.04.2", variant)
            self.assertEqual((splash.width, splash.height), size, variant)

    def test_splash_frames_keep_their_size(self):
        from tpotctl import splash_anim
        for variant in ("120", "80", "80x24"):
            splash = splash_anim.Splash("24.04.2", variant)
            for step in range(0, int(splash_anim.DURATION * 4) + 1):
                rows = splash.frame(step / 4).plain.split("\n")
                self.assertEqual(len(rows), splash.height, (variant, step))
                self.assertEqual({len(row) for row in rows}, {splash.width}, (variant, step))

    def test_splash_starts_and_ends_dark(self):
        from tpotctl import splash_anim
        splash = splash_anim.Splash("24.04.2", "80")
        for t in (0.0, splash_anim.DURATION):
            cells = {cell for row in splash.cells(t) for cell in row}
            self.assertEqual(cells, {(" ", None, None)}, t)

    def test_splash_assembles_the_whole_logo(self):
        """The honeycombs pop up, the pot draws itself, the honey rises, then the lettering lands."""
        from tpotctl import splash_anim, splash_art
        splash = splash_anim.Splash("", "80")
        width, height, base = splash_art.grid("80")
        self.assertEqual(splash.assembly(splash_anim.ASSEMBLE), base)
        early = splash.assembly(0.3)
        letters = [i for i, part in enumerate(splash.parts) if part == splash_anim.LETTERS]
        combs = [i for i, part in enumerate(splash.parts) if part == splash_anim.COMBS]
        self.assertTrue(letters and combs)
        self.assertEqual({early[i] for i in letters}, {0})
        self.assertTrue(any(early[i] for i in combs))
        self.assertTrue(all(pixel == 0 for pixel in splash.assembly(0.0)))

    def test_splash_lettering_has_no_ghost_before_it_lands(self):
        """The dark shadow of the lettering comes with it, not before it with the pot."""
        from tpotctl import splash_anim, splash_art
        for variant in ("120", "80", "80x24"):
            splash = splash_anim.Splash("", variant)
            w, h, base = splash_art.grid(variant)
            letters = {i for i, part in enumerate(splash.parts) if part == splash_anim.LETTERS}
            shadow = [i for i in letters if base[i] in (1, 2)]
            self.assertTrue(shadow, variant)
            for i in range(len(base)):
                x, y = i % w, i // w
                near = any(base[(y + dy) * w + x + dx] >= 3 and (y + dy) * w + x + dx in letters
                           for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                           if 0 <= x + dx < w and 0 <= y + dy < h)
                if base[i] in (1, 2) and near:
                    self.assertIn(i, letters, (variant, x, y))
            early = splash.assembly(1.05)
            self.assertEqual({early[i] for i in letters}, {0}, variant)
            landed = splash.assembly(1.205)                 # landed, the letters still flash white
            self.assertEqual([landed[i] for i in shadow], [base[i] for i in shadow], variant)  # no flash

    def test_splash_credits_type_with_afterglow(self):
        from tpotctl import splash_anim
        splash = splash_anim.Splash("24.04.2", "80")
        typing = splash.frame(1.9).plain.split("\n")[-1].strip()
        done = splash.frame(3.0).plain.split("\n")[-1]
        self.assertTrue("t-pot 24.04.2" in done and "telekom security" in done, done)
        self.assertTrue(0 < len(typing) < len(done.strip()), typing)
        glass, wax, magenta = (splash.colour(index) for index in (7, 2, 4))
        row = splash.cells(splash_anim.TYPE_END)[-1]
        last = max(x for x, (char, fg, bg) in enumerate(row) if char == "]")
        self.assertEqual(row[last][1], glass)                    # just typed: it glows
        settled = splash.cells(splash_anim.TYPE_END + 0.6)[-1]
        self.assertEqual(settled[last][1], wax)                  # then it takes its own colour
        text = "".join(char for char, fg, bg in settled)
        self.assertEqual(settled[text.index("telekom")][1], magenta)
        tight = splash_anim.Splash("24.04.2", "80x24").frame(3.0).plain.split("\n")[-1]
        self.assertTrue("t-pot 24.04.2" in tight and "telekom security" in tight, tight)
        long = splash_anim.Splash("24.04.2-a-very-long-test-tag-of-a-fork", "80x24").frame(3.0).plain
        bottom = long.split("\n")[-1]
        self.assertTrue("[ t-pot ]" in bottom and "telekom security" in bottom, bottom)

    def test_splash_colours_fit_the_manager(self):
        from rich.color import Color, ColorSystem
        from tpotctl import splash_anim, theme
        truecolor = splash_anim.colours("truecolor")
        for index, token in ((1, "COMB_LIT"), (2, "WAX"), (4, "MAGENTA"), (7, "GLASS"), (9, "ASH")):
            self.assertEqual(truecolor[index], theme.TRUECOLOR[token], token)
        self.assertEqual([c for c in splash_anim.colours("256") if pure_red(c)], [])
        self.assertEqual([c for c in splash_anim.colours("16")
                          if Color.parse(c).downgrade(ColorSystem.STANDARD).number in (1, 9)], [])

    def test_splash_ascii_is_ascii(self):
        from tpotctl import splash_anim
        glyphs.set_mode("ascii")
        try:
            for variant in ("80", "80x24"):
                splash = splash_anim.Splash("24.04.2", variant)
                for step in range(0, int(splash_anim.DURATION * 2) + 1):
                    self.assertTrue(splash.frame(step / 2).plain.isascii(), (variant, step))
        finally:
            glyphs.set_mode("unicode")

    def test_rendered_sizes(self):
        from tpotctl import logo
        glyphs.set_mode("unicode")
        self.assertEqual(len(logo.wordmark().plain.split("\n")), 3)
        glyphs.set_mode("ascii")
        try:
            self.assertEqual(logo.wordmark().plain, "T-Pot")
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

    async def test_ctrl_p_does_not_open_the_palette_over_the_splash(self):
        """ctrl+p is a key like any other while the splash runs: it ends the splash, no palette over it."""
        from textual.command import CommandPalette
        from tpotctl import app as tapp
        from tpotctl.screens.splash import SplashScreen
        from tpotctl.tests.test_app import FakeBackend, Recorder
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), splash=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, SplashScreen)
            await pilot.press("ctrl+p")
            for _ in range(20):
                await pilot.pause(0.05)
                if not isinstance(app.screen, SplashScreen):
                    break
            self.assertNotIsInstance(app.screen, SplashScreen)
            self.assertNotIsInstance(app.screen, CommandPalette)

    async def test_splash_for_a_second_or_a_key(self):
        from tpotctl import app as tapp
        from tpotctl.screens.splash import SplashScreen
        from tpotctl.tests.test_app import FakeBackend, Recorder
        from tpotctl import splash_anim
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), splash=True)
        with mock.patch.object(splash_anim, "DURATION", 1.0):          # its end, without waiting 8 s
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.1)
                self.assertIsInstance(app.screen, SplashScreen)
                for _ in range(60):
                    await pilot.pause(0.05)
                    if not isinstance(app.screen, SplashScreen):
                        break
                self.assertNotIsInstance(app.screen, SplashScreen)
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), splash=True)
        async with app.run_test(size=(120, 40)) as pilot:              # the full 8 s: a key ends it at once
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, SplashScreen)
            await pilot.press("x")
            await pilot.pause(0.3)
            self.assertNotIsInstance(app.screen, SplashScreen)
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), splash=True)
        async with app.run_test(size=(79, 24)) as pilot:     # no room for it
            await pilot.pause(0.1)
            self.assertNotIsInstance(app.screen, SplashScreen)

    async def test_splash_redraws_only_the_rows_that_change(self):
        """Over SSH every byte counts: a frame updates the rows whose cells changed, nothing else."""
        from tpotctl import app as tapp, splash_anim
        from tpotctl.screens.splash import SplashScreen
        from tpotctl.tests.test_app import FakeBackend, Recorder
        app = tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), splash=True)
        with mock.patch.object(splash_anim, "DURATION", 60.0):
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause(0.2)
                screen = app.screen
                self.assertIsInstance(screen, SplashScreen)
                screen.ticker.stop()
                self.assertEqual(len(screen.query(".splash-row")), screen.splash.height)
                screen.show(4.0)
                before, after = screen.splash.cells(4.0), screen.splash.cells(4.05)
                changed = sum(1 for a, b in zip(before, after) if a != b)
                self.assertEqual(screen.show(4.05), changed)
                self.assertLess(changed, screen.splash.height // 2)
                self.assertEqual(screen.show(4.05), 0)

    async def poll(self, pilot, done, seconds=4.0):
        """Wait until done() (the full suite runs under load), fail after seconds."""
        for _ in range(int(seconds / 0.05)):
            if done():
                return
            await pilot.pause(0.05)
        self.fail(f"not reached in {seconds} s")

    def splash_app(self):
        from tpotctl import app as tapp
        from tpotctl.tests.test_app import FakeBackend, Recorder
        return tapp.TpotApp(backend=FakeBackend(), runner=Recorder(), splash=True)

    async def test_splash_follows_a_resize(self):
        """Smaller, the splash takes the variant that fits now; below 80 x 24 it ends."""
        from tpotctl import splash_anim
        from tpotctl.screens.splash import SplashScreen
        app = self.splash_app()
        with mock.patch.object(splash_anim, "DURATION", 60.0):
            async with app.run_test(size=(120, 49)) as pilot:
                await self.poll(pilot, lambda: isinstance(app.screen, SplashScreen)
                                and len(app.screen.query(".splash-row")) == 49)
                screen = app.screen
                self.assertEqual(screen.splash.variant, "120")
                await pilot.resize_terminal(80, 30)
                await self.poll(pilot, lambda: screen.splash.variant == "80x24"
                                and len(screen.query(".splash-row")) == 24)
                self.assertIs(app.screen, screen)
                self.assertGreater(screen.show(3.0), 0)                  # the new rows take the frames
                await pilot.resize_terminal(79, 30)
                await self.poll(pilot, lambda: not isinstance(app.screen, SplashScreen))

    async def test_toasts_wait_for_the_splash(self):
        """A notice while the splash runs (a start problem of .env, a refresh) comes after it, in its order,
        with its options, and its time counts from then."""
        import time
        from tpotctl import splash_anim
        from tpotctl.screens.splash import SplashScreen
        app = self.splash_app()
        with mock.patch.object(splash_anim, "DURATION", 60.0):
            async with app.run_test(size=(120, 40)) as pilot:
                await self.poll(pilot, lambda: isinstance(app.screen, SplashScreen))
                asked = time.time()
                app.notify("T-Pot would not start: WEB_USER is empty [/x]", title="T-Pot would not start",
                           severity="error", timeout=30)
                app.notify("second", timeout=20)
                await pilot.pause(0.5)
                self.assertEqual(len(app._notifications), 0)
                self.assertIsInstance(app.screen, SplashScreen)
                # the Settings page may have told its own start problems (the .env of this checkout) meanwhile
                held = [message for message, _args, _kwargs in app.held_notices]
                self.assertEqual(held[-2:], ["T-Pot would not start: WEB_USER is empty [/x]", "second"])
                await pilot.press("x")
                await self.poll(pilot, lambda: len(app._notifications) == len(held))
                notes = list(app._notifications)
                self.assertEqual([n.message for n in notes], held)
                self.assertEqual([(n.title, n.severity, n.timeout, n.markup) for n in notes[-2:]],
                                 [("T-Pot would not start", "error", 30, False), ("", "information", 20, False)])
                self.assertGreaterEqual(min(n.raised_at for n in notes), asked + 0.5)
                self.assertIsNone(app.held_notices)
                app.notify("later")                                      # after the splash: at once
                await self.poll(pilot, lambda: "later" in [n.message for n in app._notifications])
        app = self.splash_app()
        with mock.patch.object(splash_anim, "DURATION", 1.0):            # its own end lets them out too
            async with app.run_test(size=(120, 40)) as pilot:
                await self.poll(pilot, lambda: isinstance(app.screen, SplashScreen))
                app.notify("one")
                app.notify("two")
                await pilot.pause(0.1)
                self.assertEqual(len(app._notifications), 0)
                await self.poll(pilot, lambda: not isinstance(app.screen, SplashScreen))
                await self.poll(pilot, lambda: "two" in [n.message for n in app._notifications])
                messages = [n.message for n in app._notifications]
                self.assertEqual(messages.index("one") + 1, messages.index("two"))

    async def test_a_resize_too_small_lets_the_held_notices_out(self):
        """Below 80 x 24 the splash ends: the notices of its time come too, in their order, with their
        options, and nothing is held any more."""
        from tpotctl import splash_anim
        from tpotctl.screens.splash import SplashScreen
        app = self.splash_app()
        with mock.patch.object(splash_anim, "DURATION", 60.0):
            async with app.run_test(size=(120, 40)) as pilot:
                await self.poll(pilot, lambda: isinstance(app.screen, SplashScreen))
                app.notify("first", title="One", severity="warning", timeout=25)
                app.notify("second [/x]", title="Two", severity="error", timeout=35)
                await pilot.pause(0.2)
                self.assertEqual(len(app._notifications), 0)
                held = [message for message, _args, _kwargs in app.held_notices]
                await pilot.resize_terminal(79, 30)
                await self.poll(pilot, lambda: not isinstance(app.screen, SplashScreen)
                                and len(app._notifications) == len(held))
                notes = list(app._notifications)
                self.assertEqual([n.message for n in notes], held)
                self.assertEqual([(n.message, n.title, n.severity, n.timeout, n.markup) for n in notes[-2:]],
                                 [("first", "One", "warning", 25, False), ("second [/x]", "Two", "error", 35, False)])
                self.assertIsNone(app.held_notices)

    async def test_a_splash_that_ends_under_a_dialog_leaves_when_the_dialog_closes(self):
        """A dialog over the splash (a worker that asks): ctrl+p works over the dialog, the splash's time runs
        out under it, and when it closes the menu is there, not a splash that stands still."""
        from textual.command import CommandPalette
        from tpotctl import splash_anim
        from tpotctl.screens.dialogs import ConfirmDialog
        from tpotctl.screens.splash import SplashScreen
        app = self.splash_app()
        with mock.patch.object(splash_anim, "DURATION", 1.0):
            async with app.run_test(size=(120, 40)) as pilot:
                await self.poll(pilot, lambda: isinstance(app.screen, SplashScreen))
                splash = app.screen
                dialog = ConfirmDialog("A question while the splash runs")
                app.push_screen(dialog)
                await self.poll(pilot, lambda: app.screen is dialog)
                self.assertTrue(app.check_action("command_palette", ()))     # the splash is not in front
                await self.poll(pilot, lambda: getattr(splash, "done", False))
                self.assertIs(app.screen, dialog)
                self.assertIsNone(app.held_notices)
                dialog.dismiss(False)
                await self.poll(pilot, lambda: len(app.screen_stack) == 1)
                self.assertNotIsInstance(app.screen, SplashScreen)
                self.assertNotIn(splash, app.screen_stack)
                await pilot.press("ctrl+p")
                await self.poll(pilot, lambda: isinstance(app.screen, CommandPalette))

    async def test_a_resize_too_small_under_a_dialog_ends_the_splash(self):
        """Below 80 x 24 while a dialog is over the splash: it ends there, and when the dialog closes the
        menu is there."""
        from tpotctl import splash_anim
        from tpotctl.screens.dialogs import ConfirmDialog
        from tpotctl.screens.splash import SplashScreen
        app = self.splash_app()
        with mock.patch.object(splash_anim, "DURATION", 60.0):
            async with app.run_test(size=(120, 40)) as pilot:
                await self.poll(pilot, lambda: isinstance(app.screen, SplashScreen))
                splash = app.screen
                dialog = ConfirmDialog("A question while the splash runs")
                app.push_screen(dialog)
                await self.poll(pilot, lambda: app.screen is dialog)
                await pilot.resize_terminal(79, 30)
                await pilot.pause(0.3)
                dialog.dismiss(False)
                await self.poll(pilot, lambda: len(app.screen_stack) == 1)
                self.assertNotIn(splash, app.screen_stack)
                self.assertIsNone(app.held_notices)

    def test_a_notice_while_the_held_ones_are_told_comes_after_them(self):
        """A worker's notice while the held ones are being told waits for them (the lock covers the
        telling); a notice told from within the telling, on the same thread, does not lock up."""
        import threading
        from textual.app import App
        app = self.splash_app()
        app.notify("one")
        app.notify("two")
        told, workers = [], []

        def telling(_self, message, *args, **kwargs):
            told.append(message)
            if message == "one":
                worker = threading.Thread(target=app.notify, args=("late",), daemon=True)
                workers.append(worker)
                worker.start()
                worker.join(0.3)                     # it waits for the lock, or it is told now
                app.notify("again")                  # the same thread, under the lock it holds
        with mock.patch.object(App, "notify", telling):
            releasing = threading.Thread(target=app.release_notices, daemon=True)   # a lock-up fails, no hang
            releasing.start()
            releasing.join(5)
            self.assertFalse(releasing.is_alive(), "the telling locked up")
            for worker in workers:
                worker.join(5)
        self.assertEqual(told, ["one", "two", "again", "late"])
        self.assertIsNone(app.held_notices)

    async def test_ctrl_q_during_the_splash_quits(self):
        """ctrl+q while the splash runs: the splash ends and the quit goes on as from the menu, no
        "Close this screen first" that would wait for the splash."""
        from tpotctl import splash_anim
        from tpotctl.screens.splash import SplashScreen
        app = self.splash_app()
        with mock.patch.object(splash_anim, "DURATION", 60.0):
            async with app.run_test(size=(120, 40)) as pilot:
                await self.poll(pilot, lambda: isinstance(app.screen, SplashScreen))
                await pilot.press("ctrl+q")
                await self.poll(pilot, lambda: not app.is_running or not isinstance(app.screen, SplashScreen))
                messages = [n.message for n in app._notifications] + \
                    [message for message, _args, _kwargs in (app.held_notices or [])]
                self.assertFalse([m for m in messages if "Close this screen first" in m], messages)
                if app.is_running:                                 # values T-Pot would not start with: asked
                    self.assertIs(app.screen, app.quit_dialog)

    async def test_toasts_without_a_splash_come_at_once(self):
        from tpotctl.screens.splash import SplashScreen
        for app, size in ((self.make_app(), (120, 40)), (self.splash_app(), (79, 24))):    # off, no room
            async with app.run_test(size=size) as pilot:
                await pilot.pause(0.1)
                self.assertNotIsInstance(app.screen, SplashScreen)
                app.notify("now", title="Settings")
                await self.poll(pilot, lambda: "now" in [n.message for n in app._notifications])
                self.assertIsNone(app.held_notices)

    async def test_splash_credits_name_the_version_of_the_checkout(self):
        import tempfile
        from tpotctl import ops
        from tpotctl.screens.splash import SplashScreen
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "version"), "w", encoding="utf-8") as handle:
                handle.write("99.1.0\n")
            with open(os.path.join(repo, ".env"), "w", encoding="utf-8") as handle:
                handle.write("TPOT_VERSION=24.04.2\n")
            app = self.splash_app()
            with mock.patch.object(ops, "REPO_DIR", repo):
                async with app.run_test(size=(120, 49)) as pilot:
                    await self.poll(pilot, lambda: isinstance(app.screen, SplashScreen))
                    self.assertEqual(app.screen.version, "99.1.0")
                    self.assertIn("[ t-pot 99.1.0 ]", app.screen.splash.frame(3.0).plain)

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
        # the second chip line is host name and checkout: the same on every machine that runs the tests
        with mock.patch("tpotctl.widgets.header.socket.gethostname", return_value="tpot"), \
                mock.patch.object(ops, "REPO_DIR", "/home/t/tpotce"):
            async with app.run_test(size=(width, 40)) as pilot:
                for _ in range(40):                 # the status comes from a worker: wait for its version
                    await pilot.pause(0.05)              # (narrow headers leave the branch out)
                    if "24.04.2" in str(app.query_one("#header-chips").render()):
                        break
                else:
                    self.fail("no status in the header after 2 s")
                await pilot.pause(0.1)
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

    @staticmethod
    def applied(**env):
        """What apply_color_system gives Textual for this environment (its tpot.json: the isolated one)."""
        target = {"XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"]}
        target.update(env)
        prefs.apply_color_system(target)
        return {key: value for key, value in target.items() if key in ("TEXTUAL_COLOR_SYSTEM", "TPOT_COLORS_SET")}

    def test_colors_pref_and_env(self):
        with mock.patch.dict(os.environ, {"TPOT_COLORS": "256"}):
            self.assertEqual(prefs.load().colors, "256")
        with mock.patch.dict(os.environ, {"TPOT_COLORS": "plaid"}):
            self.assertEqual(prefs.load().colors, "auto")
        self.assertEqual(self.applied(TPOT_COLORS="256"), {"TEXTUAL_COLOR_SYSTEM": "256", "TPOT_COLORS_SET": "256"})
        # the user's own choice wins
        self.assertEqual(self.applied(TPOT_COLORS="256", TEXTUAL_COLOR_SYSTEM="truecolor"),
                         {"TEXTUAL_COLOR_SYSTEM": "truecolor"})
        self.assertEqual(self.applied(TPOT_COLORS="16"),
                         {"TEXTUAL_COLOR_SYSTEM": "standard", "TPOT_COLORS_SET": "standard"})
        # auto: the rule of the scripts (prefs.detect_colors), iTerm2 over SSH too
        self.assertEqual(self.applied(TPOT_COLORS="auto", TERM="xterm-256color"),
                         {"TEXTUAL_COLOR_SYSTEM": "256", "TPOT_COLORS_SET": "256"})
        self.assertEqual(self.applied(TPOT_COLORS="auto", TERM="xterm-256color", LC_TERMINAL="iTerm2"),
                         {"TEXTUAL_COLOR_SYSTEM": "truecolor", "TPOT_COLORS_SET": "truecolor"})

    def test_a_restart_takes_the_new_choice(self):
        """Restart the Manager keeps the environment of the first start: what tpot set there is not the user's."""
        import tempfile
        with tempfile.TemporaryDirectory() as config, \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config}):
            os.environ.pop("TPOT_COLORS", None)
            prefs.save(prefs.Prefs(colors="truecolor"))
            target = {"XDG_CONFIG_HOME": config, "TERM": "xterm-256color",
                      "TEXTUAL_COLOR_SYSTEM": "256", "TPOT_COLORS_SET": "256"}
            prefs.apply_color_system(target)
            self.assertEqual((target["TEXTUAL_COLOR_SYSTEM"], target["TPOT_COLORS_SET"]), ("truecolor", "truecolor"))
            prefs.save(prefs.Prefs(colors="auto"))
            prefs.apply_color_system(target)                    # auto: the terminal, TERM xterm-256color
            self.assertEqual((target["TEXTUAL_COLOR_SYSTEM"], target["TPOT_COLORS_SET"]), ("256", "256"))

    def test_palette_and_textual_agree(self):
        """The palette follows what Textual renders with, whoever decided it."""
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        code = ("import os, sys; sys.path.insert(0, %r); from tpotctl import prefs; "
                "prefs.apply_color_system(os.environ); from tpotctl import theme; "
                "from textual import constants; print(constants.COLOR_SYSTEM, theme.color('comb'))" % root)
        for extra, expected in (({"TPOT_COLORS": "256"}, "256 #5f005f"),
                                ({"TPOT_COLORS": "truecolor", "TEXTUAL_COLOR_SYSTEM": "256"}, "256 #5f005f"),
                                ({"TPOT_COLORS": "256", "TEXTUAL_COLOR_SYSTEM": "truecolor"}, "truecolor #38001D"),
                                ({"TPOT_COLORS": "16"}, "standard #555555")):
            env = {k: v for k, v in os.environ.items() if k not in ("TEXTUAL_COLOR_SYSTEM", "TPOT_COLORS")}
            env.update(extra)
            out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                                 universal_newlines=True)
            if "No module named 'textual'" in out.stderr:
                self.skipTest("Textual is not installed, run with the venv of tpot")
            self.assertEqual(out.stdout.strip(), expected, (extra, out.stderr))

    def test_the_legacy_windows_console_does_not_count(self):
        """Textual builds its console with legacy_windows=False: so does the colour detection."""
        try:
            from textual import constants
        except ImportError:
            self.skipTest("Textual is not installed, run with the venv of tpot")
        from tpotctl import theme

        class FakeConsole:
            def __init__(self, **options):
                self.color_system = "windows" if options.get("legacy_windows") is None else "256"
        with mock.patch.object(constants, "COLOR_SYSTEM", "auto"), mock.patch("rich.console.Console", FakeConsole):
            self.assertEqual(theme.color_system(), "256")

    def test_a_16_colour_terminal_is_recognised(self):
        """TERM=xterm (PuTTY) or screen without COLORTERM: Rich and Textual see 16 colours."""
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        code = "import sys; sys.path.insert(0, %r); from tpotctl import theme; print(theme.color_system())" % root
        env = {k: v for k, v in os.environ.items()
               if k not in ("TEXTUAL_COLOR_SYSTEM", "TPOT_COLORS", "COLORTERM", "TERM_PROGRAM", "NO_COLOR")}
        for term, expected in (("xterm", "16"), ("screen", "16"), ("xterm-256color", "256")):
            env["TERM"] = term
            out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, universal_newlines=True)
            if "No module named 'textual'" in out.stderr:
                self.skipTest("Textual is not installed, run with the venv of tpot")
            self.assertEqual(out.stdout.strip(), expected, (term, out.stderr))

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

    @staticmethod
    def generated(system):
        """Every colour Textual makes of the theme (primary-muted, ...), as its hex value."""
        import re
        from tpotctl import theme
        theme.build(system)
        values = theme.TPOT_THEME.to_color_system().generate()
        return {key: match.group(0) for key, value in values.items()
                if isinstance(value, str) and (match := re.match(r"#[0-9a-fA-F]{6}", value))}

    def test_256_generated_variables_have_no_red(self):
        reds = {key: value for key, value in self.generated("256").items()
                if pure_red(value) and not key.startswith(("error", "warning"))}
        self.assertEqual(reds, {})

    def test_16_generated_variables_have_no_maroon(self):
        from rich.color import Color, ColorSystem
        maroon = {key: value for key, value in self.generated("16").items()
                  if Color.parse(value).downgrade(ColorSystem.STANDARD).number in (1, 9)
                  and "error" not in key}           # with 16 colours an error is simply red
        self.assertEqual(maroon, {})

    def test_16_colours_keep_comb_wax_magenta_apart(self):
        from rich.color import Color, ColorSystem
        from tpotctl import theme
        theme.build("16")
        numbers = [Color.parse(theme.color(name)).downgrade(ColorSystem.STANDARD).number
                   for name in ("comb", "wax", "magenta")]
        self.assertEqual(len(set(numbers)), 3, numbers)

    def test_16_tokens_are_palette_entries(self):
        """The VGA entries Rich downgrades to (STANDARD_PALETTE), not xterm's: #808080 would be light grey."""
        from rich._palettes import STANDARD_PALETTE
        from rich.color import Color, ColorSystem
        from tpotctl import theme
        for name, value in theme.PALETTE_16.items():
            number = Color.parse(value).downgrade(ColorSystem.STANDARD).number
            self.assertEqual("#%02x%02x%02x" % tuple(STANDARD_PALETTE[number]), value.lower(), name)

    def test_16_colour_text_stands_out(self):
        """Text and what it sits on are different ANSI colours: panels, fields, cursor, selection, stripes."""
        from rich.color import Color, ColorSystem
        from tpotctl import theme
        values = self.generated("16")
        variables = theme.TPOT_THEME.variables

        def n(value):
            return Color.parse(value.split()[0]).downgrade(ColorSystem.STANDARD).number
        panel = n(theme.color("comb"))
        for text in ("glass", "mist", "ash", "key"):
            self.assertNotEqual(n(theme.color(text)), panel, text)
        self.assertNotEqual(n(variables["input-selection-background"]), n(variables["focus-tint"]))
        self.assertNotEqual(n(variables["block-cursor-background"]), n(variables["block-cursor-foreground"]))
        self.assertNotEqual(n(values["block-cursor-blurred-background"]), n(variables["stripe"]))

    async def test_16_colours_show_no_maroon(self):
        from rich.color import Color, ColorSystem
        from tpotctl import theme
        errors = {value.lower() for key, value in self.generated("16").items() if "error" in key}
        errors.add(theme.color("error").lower())
        maroon = {colour for colour in await self.colours()
                  if Color.parse(colour).downgrade(ColorSystem.STANDARD).number == 1 and colour.lower() not in errors}
        self.assertEqual(maroon, set())

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


class SplashArtTest(unittest.TestCase):
    """The pixels of the ANSI logo (the template in ansi/, kept out of git) as data of the T-Pot Manager."""

    def test_splash_art_is_the_template(self):
        import ast
        import re
        from tpotctl import splash_art
        sizes = {"120": (120, 94), "80": (80, 62), "80x24": (80, 48)}
        for variant, (w, h) in sizes.items():
            width, height, pixels = splash_art.grid(variant)
            self.assertEqual((width, height, len(pixels)), (w, h, w * h), variant)
            self.assertTrue(set(pixels) <= set(range(10)), variant)
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sources = {"120": ("t-pot-animate.py", "120"), "80": ("t-pot-animate.py", "80"),
                   "80x24": (os.path.join("80x24", "t-pot-animate-80x24.py"), "80")}
        for variant, (name, key) in sources.items():
            path = os.path.join(root, "ansi", name)
            if not os.path.exists(path):
                self.skipTest("the template in ansi/ is not in this checkout")
            with open(path, encoding="utf-8") as handle:
                data = ast.literal_eval(re.search(r"^DATA = (\{.*\})$", handle.read(), re.M).group(1))
            entry = data["grids"][key]
            import base64
            import zlib
            expected = list(zlib.decompress(base64.b64decode(entry["data"])))
            self.assertEqual(splash_art.grid(variant)[2], expected, variant)
            self.assertEqual([tuple(c) for c in data["palette"]], list(splash_art.PALETTE))


@unittest.skipUnless(rich, "Rich is not installed")
class SplashCycleTest(unittest.TestCase):
    """The cycle after the assembly: no jump where it begins, and only the rows that change are new."""

    def test_the_cycle_starts_without_a_jump(self):
        """At ASSEMBLE the whole logo stands; drops come when their cycle begins anew, syrup and the
        waves on the pool fade in."""
        from tpotctl import splash_anim, splash_art
        for variant in ("120", "80", "80x24"):
            splash = splash_anim.Splash("", variant)
            base = splash.base

            def off(t):
                return sum(1 for a, b in zip(splash.pixels(t), base) if a != b)
            self.assertLess(off(splash_anim.ASSEMBLE), 10, variant)
            # while it fades in, a frame changes less than a frame of the running cycle does
            times = [splash_anim.ASSEMBLE + n / 15 for n in range(int(splash_anim.CYCLE * 15))]
            steps = [sum(1 for a, b in zip(splash.pixels(t), splash.pixels(t + 1 / 15)) if a != b)
                     for t in times]
            median = sorted(steps)[len(steps) // 2]
            self.assertLess(max(steps[:int(splash_anim.FADE * 15)]), median, variant)
            self.assertGreater(off(splash_anim.ASSEMBLE + 0.5), off(splash_anim.ASSEMBLE), variant)
            # drop 1 begins anew at phase 1.35: before that it is nowhere, then it hangs at its start
            x, y = splash_art.design_xy(splash.w, splash.h, *splash_art.DROPS[1])
            self.assertEqual(splash.cycle(1.4)[(y + 1) * splash.w + x], 6, variant)

    def test_cells_reuse_the_rows_that_stay(self):
        from tpotctl import splash_anim
        for variant in ("120", "80x24"):
            splash, fresh = splash_anim.Splash("24.04.2", variant), splash_anim.Splash("24.04.2", variant)
            for before, after in ((4.0, 4.05), (1.0, 1.05), (2.0, 2.05), (7.6, 7.65), (4.05, 2.0)):
                one, two = splash.cells(before), splash.cells(after)
                self.assertEqual(two, fresh.cells(after), (variant, after))     # nothing stale in it
                same = [r for r in range(len(one)) if one[r] == two[r]]
                if after < splash_anim.OUT:                 # the crumbling may reach every row
                    self.assertTrue(same, (variant, before, after))
                for r in same:
                    self.assertTrue(one[r] is two[r], (variant, before, after, r))
            self.assertEqual(splash.cells(1.0), fresh.cells(1.0), variant)    # the credits left no trace
        self.assertFalse(hasattr(splash_anim.Splash, "fits"))                 # unused, gone


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
