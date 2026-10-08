"""The T-Pot logo has one source: the pixels and the palette of the ANSI template in splash_art.py
(imported with python3 -m tpotctl.splash_art --import, never by hand), the geometry of its design
and its colours there too, the wordmark in logo.py. The scripts carry generated copies only (the
data block of installer/lib/ui.sh and install.sh, python3 -m tpotctl.ui_logo), no other file
carries any of it.

The templates here are built from the data of splash_art.py in a temporary folder: the template of
the user (ansi/, kept out of git) is never read, and no template is ever run.
"""

import base64
import builtins
import contextlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()

from tpotctl import splash_art, ui_logo  # noqa: E402

try:
    import rich  # noqa: F401
except ImportError:
    rich = None

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SIZES = {"120": (120, 94), "80": (80, 62), "80x24": (80, 48)}


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def grid_data(variant):
    """The zlib + base64 data of a variant as the template has it (one string)."""
    return "".join(splash_art.GRIDS[variant][2])


def encode(pixels):
    return base64.b64encode(zlib.compress(bytes(pixels), 9)).decode("ascii")


def template_text(data, prelude="", tail=""):
    """A template like t-pot-animate.py: a DATA line, code around it."""
    return (f'#!/usr/bin/env python3\n"""A template for the tests."""\nimport os\n{prelude}'
            f"DATA = {data!r}\nPALETTE=DATA['palette']\n\ndef frame():\n    return PALETTE\n{tail}")


def template_data(palette=None, grids=None):
    palette = [tuple(c) for c in (palette or splash_art.PALETTE)]
    grids = grids or {}
    main = {key: {"width": SIZES[key][0], "height": SIZES[key][1], "data": grids.get(key, grid_data(key))}
            for key in ("80", "120")}
    tight = {"80": {"width": 80, "height": 48, "data": grids.get("80x24", grid_data("80x24"))}}
    return {"palette": palette, "grids": main}, {"palette": palette, "grids": tight}


class Template:
    """t-pot-animate.py and 80x24/t-pot-animate-80x24.py in a temporary folder."""

    def __init__(self, case, main=None, tight=None, prelude="", main_text=None, tight_text=None):
        self.folder = tempfile.mkdtemp(prefix="tpot-template-")
        case.addCleanup(shutil.rmtree, self.folder, True)
        self.marker = os.path.join(self.folder, "it-ran")
        # a template that runs leaves this file: none of them may
        prelude = prelude or f"open({self.marker!r}, 'w').close()\n"
        want_main, want_tight = template_data()
        self.path = os.path.join(self.folder, "t-pot-animate.py")
        self.tight = os.path.join(self.folder, "80x24", "t-pot-animate-80x24.py")
        os.makedirs(os.path.dirname(self.tight))
        with open(self.path, "w", encoding="utf-8") as out:
            out.write(main_text if main_text is not None else template_text(main or want_main, prelude))
        if tight is not False:
            with open(self.tight, "w", encoding="utf-8") as out:
                out.write(tight_text if tight_text is not None else template_text(tight or want_tight, prelude))

    def ran(self):
        return os.path.exists(self.marker)


def quiet_main(argv, target):
    """splash_art.main with its output kept (the tests do not show it)."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return splash_art.main(argv, target=target)


def splash_art_copy(case):
    """A copy of splash_art.py to write into."""
    folder = tempfile.mkdtemp(prefix="tpot-splash-art-")
    case.addCleanup(shutil.rmtree, folder, True)
    copy = os.path.join(folder, "splash_art.py")
    shutil.copy2(splash_art.__file__, copy)
    return copy


class DesignTest(unittest.TestCase):
    """The geometry of the design is the template's (splash_art.DESIGN), splash_anim reads it there."""

    def test_geometry_lies_in_the_design(self):
        design = splash_art.DESIGN
        left, top, width, height = design["frame"]

        def inside(x=None, y=None):
            if x is not None:
                self.assertTrue(left <= x <= left + width, x)
            if y is not None:
                self.assertTrue(top <= y <= top + height, y)
        for key in ("word_x",):
            low, high = design[key]
            self.assertLess(low, high, key)
            inside(x=low), inside(x=high)
        for key in ("word_y", "waves_y"):
            low, high = design[key]
            self.assertLess(low, high, key)
            inside(y=low), inside(y=high)
        cx, cy = design["pot_centre"]
        rx, ry = design["pot_radii"]
        inside(cx - rx, cy - ry), inside(cx + rx, cy + ry)
        inside(y=design["pool_top"]), inside(y=design["pool_y"])
        self.assertLess(design["pool_top"], design["pool_y"])
        for x, y in design["drops"] + design["stars"]:
            inside(x, y)
        for x, y in design["drops"]:                        # under the lettering, above the pool
            self.assertTrue(design["word_y"][0] < y < design["pool_y"], (x, y))
        inside(x=design["syrup_x"])
        start, end, step = design["syrup_y"]
        self.assertTrue(step > 0 and start < end)
        inside(y=start), inside(y=end)
        # the colours of honey and lettering: entries of the palette, all of them magenta
        for index in design["magentas"]:
            r, g, b = splash_art.PALETTE[index]
            self.assertTrue(r > g and b > g, (index, r, g, b))

    def test_the_names_of_the_splash_are_the_design(self):
        design = splash_art.DESIGN
        self.assertEqual(splash_art.DROPS, design["drops"])
        self.assertEqual(splash_art.STARS, design["stars"])
        self.assertEqual((splash_art.SYRUP_X, splash_art.POOL_Y), (design["syrup_x"], design["pool_y"]))
        self.assertEqual(splash_art.SYRUP_Y, range(*design["syrup_y"]))
        frame = design["frame"]
        self.assertEqual(splash_art.design_xy(120, 94, frame[0], frame[1]), (0, 0))
        self.assertEqual(splash_art.design_xy(120, 94, frame[0] + frame[2], frame[1] + frame[3]), (120, 94))

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_parts_give_every_pixel_a_part(self):
        from tpotctl import splash_anim
        for variant in SIZES:
            with self.subTest(variant=variant):
                splash = splash_anim.Splash("", variant)
                for i, pixel in enumerate(splash.base):
                    self.assertEqual(splash.parts[i] is None, pixel == 0, (variant, i))
                found = {part for part in splash.parts if part is not None}
                self.assertEqual(found, {splash_anim.COMBS, splash_anim.POT, splash_anim.HONEY,
                                         splash_anim.POOL, splash_anim.LETTERS}, variant)

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_the_splash_reads_the_design(self):
        """A change of DESIGN reaches the splash: no copy of the geometry in splash_anim."""
        from tpotctl import splash_anim
        for name in ("WORD_Y", "WORD_X", "POT_CENTRE", "POT_RADII", "POOL_TOP", "MAGENTAS"):
            self.assertFalse(hasattr(splash_anim, name), name)
        with mock.patch.dict(splash_art.DESIGN, {"pool_top": 2000}):
            parts = splash_anim.Splash("", "80").parts
        self.assertNotIn(splash_anim.POOL, parts)
        with mock.patch.dict(splash_art.DESIGN, {"word_x": (0, 1)}):
            parts = splash_anim.Splash("", "80").parts
        self.assertNotIn(splash_anim.LETTERS, parts)
        self.assertIn(splash_anim.LETTERS, splash_anim.Splash("", "80").parts)


class ColourTablesTest(unittest.TestCase):
    """The colour tables of the logo in the scripts are generated from the one of the T-Pot Manager
    (splash_art.COLOURS with the tokens of theme.py, splash_anim.colours())."""

    def test_the_tables_are_in_the_generated_block(self):
        for path in ui_logo.FILES:
            text = read(path)
            block = ui_logo.data_block(text)
            outside = text.replace(block, "")
            for name in ("myUI_LOGO_RGB", "myUI_LOGO_256", "myUI_LOGO_16"):
                with self.subTest(path=os.path.basename(path), name=name):
                    self.assertIn(f"\n{name}=(", block)
                    self.assertNotIn(f"{name}=(", outside)

    def test_the_tables_are_the_colours(self):
        block = ui_logo.generate()

        def array(name):
            return re.search(rf"^{name}=\((.*?)\)", block, re.M | re.S).group(1).replace('"', "").split()
        self.assertEqual(array("myUI_LOGO_RGB"),
                         ["{};{};{}".format(*(int(c[i:i + 2], 16) for i in (1, 3, 5)))
                          for c in ui_logo.logo_colours("truecolor")])
        self.assertEqual(array("myUI_LOGO_256"), "16 53 89 125 162 205 218 231 240 248".split())
        self.assertEqual(array("myUI_LOGO_16"), "30 35 35 35 95 95 97 97 90 37".split())

    def test_changed_colours_make_check_fail(self):
        """A colour of the Manager's table changes: the copies of the scripts are stale."""
        self.assertEqual(ui_logo.check(), [])
        for system in ("truecolor", "256", "16"):
            changed = list(splash_art.COLOURS[system])
            changed[3] = changed[4]
            with self.subTest(system=system), mock.patch.dict(splash_art.COLOURS, {system: tuple(changed)}):
                self.assertEqual(ui_logo.check(), list(ui_logo.FILES))
        palettes = ui_logo.theme_palettes()
        palettes["truecolor"] = dict(palettes["truecolor"], MAGENTA="#E20075")
        with mock.patch.object(ui_logo, "theme_palettes", return_value=palettes):
            self.assertEqual(ui_logo.check(), list(ui_logo.FILES))

    def test_colours_must_be_entries_of_the_reduced_palettes(self):
        for system, wrong in (("256", "#5f0050"), ("16", "#ff0000"), ("truecolor", "NO_SUCH_TOKEN")):
            changed = list(splash_art.COLOURS[system])
            changed[2] = wrong
            with self.subTest(system=system), mock.patch.dict(splash_art.COLOURS, {system: tuple(changed)}):
                with self.assertRaises(ValueError):
                    ui_logo.generate()
        self.assertEqual(ui_logo.xterm_index("#000000"), 16)
        self.assertEqual(ui_logo.xterm_index("#ffffff"), 231)
        self.assertEqual(ui_logo.xterm_index("#585858"), 240)
        self.assertEqual(ui_logo.sgr16("#AAAAAA"), 37)

    def test_theme_is_read_as_text(self):
        """ui_logo is standard library only: it reads theme.py (Textual) as text."""
        palettes = ui_logo.theme_palettes()
        self.assertEqual(palettes["truecolor"]["MAGENTA"], "#E20074")
        self.assertEqual(palettes["256"]["MAGENTA"], "#d70087")
        self.assertEqual(set(palettes), {"truecolor", "256", "16"})
        probe = ("import sys\nfrom tpotctl import ui_logo, splash_art\nui_logo.generate()\n"
                 "print(sorted(m for m in sys.modules if m.split('.')[0] in ('rich', 'textual')))")
        result = subprocess.run([sys.executable, "-c", probe], cwd=REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "[]"), result.stdout)

    @unittest.skipUnless(rich, "Rich is not installed")
    def test_the_manager_takes_the_same_colours(self):
        from tpotctl import splash_anim, theme
        for system in ("truecolor", "256", "16"):
            self.assertEqual(splash_anim.colours(system), ui_logo.logo_colours(system), system)
        self.assertEqual(ui_logo.theme_palettes(), theme.PALETTES)


class ImportTest(unittest.TestCase):
    """python3 -m tpotctl.splash_art --import <template> [--import-80x24 <file>] [--check]."""

    def test_round_trip_changes_nothing(self):
        template = Template(self)
        palette, grids = splash_art.import_template(template.path)
        self.assertEqual(palette, splash_art.PALETTE)
        self.assertEqual(grids, {variant: (w, h, grid_data(variant)) for variant, (w, h) in SIZES.items()})
        self.assertEqual(splash_art.render(palette, grids), splash_art.data_block(read(splash_art.__file__)))
        copy = splash_art_copy(self)
        before = read(copy)
        self.assertEqual(quiet_main(["--import", template.folder, "--check"], target=copy), 0)
        self.assertEqual(quiet_main(["--import", template.path], target=copy), 0)
        self.assertEqual(read(copy), before)
        self.assertFalse(template.ran())

    def test_check_of_the_checkout(self):
        template = Template(self)
        result = subprocess.run([sys.executable, "-m", "tpotctl.splash_art", "--import", template.folder,
                                 "--check"], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("is the template", result.stdout)
        self.assertFalse(template.ran())

    def test_a_changed_template_rewrites_splash_art_and_the_scripts_are_stale(self):
        """In a copy of the checkout: the import writes splash_art.py, names the next steps, and
        ui_logo --check then finds the copies of the scripts stale until ui_logo writes them."""
        checkout = read(splash_art.__file__)
        root = tempfile.mkdtemp(prefix="tpot-logo-checkout-")
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(os.path.join(REPO, "tpotctl"), os.path.join(root, "tpotctl"),
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        os.makedirs(os.path.join(root, "installer", "lib"))
        shutil.copy2(os.path.join(REPO, "installer", "lib", "ui.sh"), os.path.join(root, "installer", "lib"))
        shutil.copy2(os.path.join(REPO, "install.sh"), root)
        os.chmod(os.path.join(root, "tpotctl", "splash_art.py"), 0o640)
        pixels = list(zlib.decompress(base64.b64decode(grid_data("80"))))
        pixels[30 * 80 + 2] = 9 if pixels[30 * 80 + 2] != 9 else 8
        main, tight = template_data(grids={"80": encode(pixels)})
        template = Template(self, main=main, tight=tight)
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

        def tool(*args):
            return subprocess.run([sys.executable, "-m", *args], cwd=root, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(tool("tpotctl.ui_logo", "--check").returncode, 0)
        checked = tool("tpotctl.splash_art", "--import", template.path, "--check")
        self.assertEqual(checked.returncode, 1, checked.stdout)
        self.assertIn("--import", checked.stdout)
        done = tool("tpotctl.splash_art", "--import", template.path)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("python3 -m tpotctl.ui_logo", done.stdout)
        for name in ("DESIGN", "word_x", "pot_centre", "pool_top"):
            self.assertIn(name, done.stdout)
        copied = os.path.join(root, "tpotctl", "splash_art.py")
        self.assertEqual(stat.S_IMODE(os.stat(copied).st_mode), 0o640)
        self.assertNotEqual(read(copied), read(splash_art.__file__))
        self.assertEqual(read(splash_art.__file__), checkout)          # the real one stays
        self.assertEqual(tool("tpotctl.splash_art", "--import", template.path, "--check").returncode, 0)
        stale = tool("tpotctl.ui_logo", "--check")
        self.assertEqual(stale.returncode, 1, stale.stdout)
        self.assertIn(os.path.join("installer", "lib", "ui.sh"), stale.stdout)
        self.assertIn("install.sh", stale.stdout)
        self.assertEqual(tool("tpotctl.ui_logo").returncode, 0)
        self.assertEqual(tool("tpotctl.ui_logo", "--check").returncode, 0)
        self.assertFalse(template.ran())

    def test_code_instead_of_data_is_refused(self):
        """The template is parsed, never run: DATA must be literal data."""
        cases = {
            "a call": "DATA = __import__('os').system('touch {marker}')\n",
            "a name": "PAL = []\nDATA = {{'palette': PAL, 'grids': {{}}}}\n",
            "a comprehension": "DATA = {{'palette': [(i, i, i) for i in range(10)], 'grids': {{}}}}\n",
            "a lambda": "DATA = {{'palette': (lambda: 1)(), 'grids': {{}}}}\n",
            "no DATA": "VALUE = 1\n",
            "DATA twice": "DATA = {{}}\nDATA = {{}}\n",
            "not python": "DATA = {{'palette': [\n",
            "a list": "DATA = [1, 2, 3]\n",
            "deep": "DATA = " + "[" * 300 + "]" * 300 + "\n",
        }
        copy = splash_art_copy(self)
        before = read(copy)
        for name, text in cases.items():
            with self.subTest(case=name):
                template = Template(self)
                with open(template.path, "w", encoding="utf-8") as out:
                    out.write(text.format(marker=template.marker))
                with self.assertRaises(ValueError) as caught:
                    splash_art.import_template(template.path)
                self.assertIn(template.path, str(caught.exception))
                self.assertEqual(quiet_main(["--import", template.path], target=copy), 1)
                self.assertFalse(template.ran())
        self.assertEqual(read(copy), before)

    def test_bad_data_is_refused(self):
        pixels = list(zlib.decompress(base64.b64decode(grid_data("80"))))
        bomb = base64.b64encode(zlib.compress(bytes(50_000_000), 9)).decode("ascii")
        main, tight = template_data()

        def changed(original, key, **fields):
            copy = {"palette": list(original["palette"]),
                    "grids": {k: dict(v) for k, v in original["grids"].items()}}
            copy["grids"][key].update(fields)
            return copy
        cases = {
            "nine colours": ({"palette": main["palette"][:9], "grids": main["grids"]}, None),
            "eleven colours": ({"palette": main["palette"] + [(1, 2, 3)], "grids": main["grids"]}, None),
            "a colour out of range": ({"palette": main["palette"][:9] + [(256, 0, 0)], "grids": main["grids"]},
                                      None),
            "a colour of two parts": ({"palette": main["palette"][:9] + [(1, 2)], "grids": main["grids"]}, None),
            "a wrong width": (changed(main, "80", width=81), None),
            "a wrong height": (changed(main, "120", height=93), None),
            "a height as text": (changed(main, "120", height="94"), None),
            "too few pixels": (changed(main, "80", data=encode(pixels[:-1])), None),
            "too many pixels": (changed(main, "80", data=encode(pixels + [0])), None),
            "a colour index of 10": (changed(main, "80", data=encode(pixels[:-1] + [10])), None),
            "not base64": (changed(main, "80", data="not base64!"), None),
            "not zlib": (changed(main, "80", data=base64.b64encode(b"plain").decode()), None),
            "a zlib bomb": (changed(main, "80", data=bomb), None),
            "no 120 grid": ({"palette": main["palette"], "grids": {"80": main["grids"]["80"]}}, None),
            "another palette in 80x24": (main, {"palette": [(1, 1, 1)] + tight["palette"][1:],
                                                "grids": tight["grids"]}),
            "80x24 of a wrong height": (main, changed(tight, "80", height=62)),
        }
        copy = splash_art_copy(self)
        before = read(copy)
        for name, (one, two) in cases.items():
            with self.subTest(case=name):
                template = Template(self, main=one, tight=two)
                with self.assertRaises(ValueError):
                    splash_art.import_template(template.path)
                self.assertEqual(quiet_main(["--import", template.path], target=copy), 1)
        self.assertEqual(read(copy), before)

    def test_a_huge_wrong_value_gives_a_short_message_fast(self):
        """A wrong value is named in brief: no repr of a huge integer (quadratic on Python 3.9) or string in
        the message, and a number of more digits than Python 3.11 parses is refused before parsing."""
        import time
        main, tight = template_data()
        colour_ok = list(main["palette"][:9])
        grids = main["grids"]

        def with_grid(**fields):
            changed = {k: dict(v) for k, v in grids.items()}
            changed["80"].update(fields)
            return {"palette": main["palette"], "grids": changed}
        texts = {
            "a palette of a huge number": "DATA = {'palette': " + "9" * 900_000 + ", 'grids': {}}\n",
            "a palette of a long number": "DATA = {'palette': " + "9" * 4000 + ", 'grids': {}}\n",
            "a palette of a long string": template_text({"palette": "x" * 900_000, "grids": grids}, ""),
            "a colour of a long string": template_text({"palette": colour_ok + ["x" * 900_000], "grids": grids}, ""),
            "a colour of long numbers": template_text({"palette": colour_ok + [[int("9" * 4000)] * 3],
                                                       "grids": grids}, ""),
            "a width of a long number": template_text(with_grid(width=int("9" * 4000)), ""),
            "a height of a long list": template_text(with_grid(height=list(range(100_000))), ""),
        }
        for name, text in texts.items():
            with self.subTest(case=name):
                template = Template(self, main_text=text, tight_text=template_text(tight, ""))
                started = time.monotonic()
                with self.assertRaises(ValueError) as caught:
                    splash_art.import_template(template.path)
                message = str(caught.exception).replace(template.path, "<template>")
                self.assertLess(time.monotonic() - started, 3.0)
                self.assertLess(len(message), 400, message[:400])
                self.assertIn("<template>", message)

    def test_a_huge_template_is_refused_unread(self):
        template = Template(self, main_text="DATA = {'palette': []}\n" + "#" * (splash_art.MAX_TEMPLATE + 1))
        with self.assertRaises(ValueError) as caught:
            splash_art.import_template(template.path)
        self.assertIn("bytes", str(caught.exception))

    def test_the_80x24_template_is_needed(self):
        template = Template(self, tight=False)
        with self.assertRaises(ValueError) as caught:
            splash_art.import_template(template.path)
        self.assertIn("--import-80x24", str(caught.exception))
        self.assertIn(os.path.join("80x24", "t-pot-animate-80x24.py"), str(caught.exception))
        elsewhere = Template(self)
        palette, grids = splash_art.import_template(template.path, elsewhere.tight)
        self.assertEqual(grids["80x24"], (80, 48, grid_data("80x24")))
        copy = splash_art_copy(self)
        self.assertEqual(quiet_main(["--import", template.path], target=copy), 1)
        self.assertEqual(quiet_main(["--import", template.path, "--import-80x24", elsewhere.tight,
                                          "--check"], target=copy), 0)

    def test_only_the_given_files_are_read(self):
        """Nothing of the checkout's ansi/ or anything else but the template and the target."""
        template = Template(self)
        copy = splash_art_copy(self)
        opened = []
        real_open = builtins.open

        def spy(path, *args, **kwargs):
            opened.append(os.path.realpath(path))
            return real_open(path, *args, **kwargs)
        with mock.patch("builtins.open", spy):
            self.assertEqual(quiet_main(["--import", template.folder, "--check"], target=copy), 0)
        self.assertEqual(set(opened), {os.path.realpath(p) for p in (template.path, template.tight, copy)})
        self.assertFalse(any(os.sep + "ansi" + os.sep in path for path in opened))

    def test_usage(self):
        with self.assertRaises(SystemExit) as caught:
            quiet_main([], target=splash_art_copy(self))
        self.assertEqual(caught.exception.code, 2)


# -- only one source ----------------------------------------------------------------------------

def logo_prints():
    """The fingerprints of the logo data: windows of the zlib + base64 grids, the rows of the
    wordmark's pixel map, the lines of myUI_WORDMARK and the encoded rows of the data block."""
    windows = []
    for variant in SIZES:
        data = grid_data(variant)
        windows += [data[k:k + 32] for k in range(0, len(data) - 32, 160)]
    texts = [row for row in ui_logo.manager_wordmark() if "#" in row]
    texts += [line.strip() for line in ui_logo.wordmark_lines() if line.strip()]
    block = ui_logo.generate()
    for variant in SIZES:
        texts += [row for row in ui_logo.bash_strings(block, ui_logo.array_name(variant)) if len(row) >= 16]
    return windows, sorted(set(texts))


BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{16,}")


def carriers(paths, root):
    """The files of paths (relative to root) that carry logo data; the data blocks of ui.sh and
    install.sh are left out of them, splash_art.py and logo.py are the source."""
    windows, texts = logo_prints()
    allowed = {os.path.join("tpotctl", "splash_art.py"), os.path.join("tpotctl", "logo.py")}
    blocks = {os.path.relpath(path, REPO) for path in ui_logo.FILES}
    found = {}
    for name in paths:
        path = os.path.join(root, name)
        if name in allowed or not os.path.isfile(path) or os.path.islink(path):
            continue
        with open(path, "rb") as handle:
            raw = handle.read()
        if b"\0" in raw[:8192]:
            continue                                        # a binary file
        text = raw.decode("utf-8", "replace")
        if name in blocks and ui_logo.BEGIN in text:
            text = text.replace(ui_logo.data_block(text), "")
        joined = "".join(BASE64_RUN.findall(text))
        hits = [w for w in windows if w in joined] + [t for t in texts if t in text]
        if hits:
            found[name] = hits[0]
    return found


class OneSourceTest(unittest.TestCase):

    def tracked(self):
        git = shutil.which("git")
        if not git or not os.path.exists(os.path.join(REPO, ".git")):
            self.skipTest("not a git checkout")
        result = subprocess.run([git, "-C", REPO, "ls-files", "-z"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        if result.returncode:
            self.skipTest("git ls-files failed")
        return [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]

    def test_no_logo_pixels_elsewhere(self):
        """No tracked file but splash_art.py, logo.py and the data blocks carries logo data."""
        names = self.tracked()
        self.assertIn(os.path.join("tpotctl", "splash_art.py"), names)
        self.assertEqual(carriers(names, REPO), {})

    def test_the_search_finds_a_copy(self):
        """The search of test_no_logo_pixels_elsewhere finds every kind of copy, chunked otherwise."""
        folder = tempfile.mkdtemp(prefix="tpot-carriers-")
        self.addCleanup(shutil.rmtree, folder, True)
        data = grid_data("120")
        _windows, texts = logo_prints()
        block = ui_logo.generate()
        files = {
            "grid.py": "GRID = (\n" + "".join(f'    "{data[k:k + 64]}"\n' for k in range(0, len(data), 64)) + ")\n",
            "part.txt": data[1000:1400],
            "wordmark.py": 'ROWS = ["' + ui_logo.manager_wordmark()[2] + '"]\n',
            "banner.sh": "echo '" + ui_logo.wordmark_lines()[1] + "'\n",
            "encoded.sh": "myROWS=('" + ui_logo.bash_strings(block, "myUI_LOGO_80")[10] + "')\n",
            "copy.sh": "x\n" + block + "\n",
            "clean.sh": "echo T-Pot\n",
        }
        for name, text in files.items():
            with open(os.path.join(folder, name), "w", encoding="utf-8") as out:
                out.write(text)
        self.assertEqual(set(carriers(list(files), folder)), set(files) - {"clean.sh"})

    def test_the_tpotinit_scripts_show_the_plain_banner(self):
        """hptest.sh and the pipeline test run in the tpotinit image without ui.sh: their banner is
        the plain one, no logo and no logo data of their own."""
        for name in ("hptest.sh", "attackmap_pipeline_test.sh"):
            with self.subTest(name=name):
                text = read(os.path.join(REPO, "docker", "tpotinit", "dist", "bin", name))
                fallback = text[text.index(ui_logo.FALLBACK_BEGIN):text.index(ui_logo.FALLBACK_END)]
                banner = ui_logo.fallback_functions(fallback)["fuUI_BANNER"]
                self.assertIn('echo "### T-Pot $1"', banner)
                for sign in ("myUI_LOGO", "fuUI_LOGO", "myUI_WORDMARK", "▀", "▄", "█", "\x1b"):
                    self.assertNotIn(sign, fallback, sign)
                self.assertNotRegex(text, r"(?m)^\s*myUI_LOGO=1")


if __name__ == "__main__":
    unittest.main()
