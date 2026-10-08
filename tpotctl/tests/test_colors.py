"""The colours of the T-Pot Manager: one rule for bash (fuUI_COLORS of installer/lib/ui.sh) and Python
(prefs.detect_colors), the cases in color_cases.json; Textual and the palette of theme.py take what it
says, the launcher and the customizer alike; sshd lets the markers of the terminal come along.

Nothing here reads or writes the tpot.json of the user running the tests: every environment points
HOME and XDG_CONFIG_HOME to a temporary folder, os.environ is only changed under mock.patch.dict.
"""

import importlib.util
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import prefs  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_SH = os.path.join(REPO, "installer", "lib", "ui.sh")
CASES = os.path.join(REPO, "tpotctl", "tests", "color_cases.json")
TEXTUAL = importlib.util.find_spec("textual") is not None
# what a terminal may say of itself, and the choices of the user: the keys the rule looks at
RULE_KEYS = ("TPOT_COLORS", "COLORTERM", "TERM", "TMUX", "TERM_PROGRAM", "LC_TERMINAL", "VTE_VERSION",
             "KONSOLE_VERSION", "WT_SESSION", "NO_COLOR")


def cases():
    with open(CASES, encoding="utf-8") as handle:
        return json.load(handle)


def modern_bash():
    """A bash of 4 or newer (fuUI_COLORS lowercases with ${x,,}), None without one (macOS /bin/bash 3.2)."""
    bash = shutil.which("bash")
    if not bash:
        return None
    out = subprocess.run([bash, "-c", "echo ${BASH_VERSINFO[0]}"], stdout=subprocess.PIPE,
                         universal_newlines=True)
    return bash if out.stdout.strip().isdigit() and int(out.stdout) >= 4 else None


class Sandbox:
    """A temporary HOME and XDG_CONFIG_HOME, with the tpot.json of a case or none."""

    def __init__(self, case):
        self.home = tempfile.mkdtemp(prefix="tpot-colors-")
        case.addCleanup(shutil.rmtree, self.home, True)
        self.config = os.path.join(self.home, "config")
        os.makedirs(os.path.join(self.config, "tpotce"))
        self.file = os.path.join(self.config, "tpotce", "tpot.json")

    def base(self):
        return {"HOME": self.home, "XDG_CONFIG_HOME": self.config}

    def prefs(self, data):
        if data is None:
            if os.path.exists(self.file):
                os.remove(self.file)
            return
        with open(self.file, "w", encoding="utf-8") as out:
            json.dump(data, out)

    def python(self, env, data=None):
        self.prefs(data)
        environ = self.base()
        environ.update(env)
        return prefs.detect_colors(environ)

    def bash(self, bash, runs):
        """fuUI_COLORS for every (env, prefs) of runs, in one bash: before each run every key of any
        run is unset, then the keys of its env are set (the bash itself starts with none of them)."""
        keys = sorted(set(RULE_KEYS).union(*(env for env, _data in runs)))
        lines = [f'source "{UI_SH}"']
        last = ()
        for env, data in runs:
            if data != last:              # the file only changes between runs of another tpot.json
                if data is None:
                    lines.append(f'rm -f "{self.file}"')
                else:
                    lines.append(f"printf '%s' {quote(json.dumps(data))} > \"{self.file}\"")
                last = data
            lines.append("unset " + " ".join(keys))
            lines.extend(f"export {key}={quote(value)}" for key, value in env.items())
            lines.append("fuUI_COLORS")
        environ = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": os.environ.get("LANG", "en_US.UTF-8")}
        environ.update(self.base())
        out = subprocess.run([bash, "-c", "\n".join(lines)], env=environ, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
        return out.stdout.split(), out.stderr


def quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


class RuleTest(unittest.TestCase):
    """prefs.detect_colors: the rule of the docstring of test_ui.test_colour_detection_cases."""

    def test_the_cases(self):
        sandbox = Sandbox(self)
        for case in cases():
            with self.subTest(note=case["note"]):
                self.assertEqual(sandbox.python(case["env"], case.get("prefs")), case["expect"], case["env"])

    def test_only_the_environment_given_counts(self):
        """A TPOT_COLORS or a tpot.json of the process running it does not reach another environment."""
        sandbox = Sandbox(self)
        other = tempfile.mkdtemp(prefix="tpot-colors-other-")
        self.addCleanup(shutil.rmtree, other, True)
        os.makedirs(os.path.join(other, "tpotce"))
        with open(os.path.join(other, "tpotce", "tpot.json"), "w", encoding="utf-8") as out:
            json.dump({"colors": "16"}, out)
        with mock.patch.dict(os.environ, {"TPOT_COLORS": "16", "XDG_CONFIG_HOME": other, "COLORTERM": "truecolor"}):
            self.assertEqual(sandbox.python({"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2"}), "truecolor")
            self.assertEqual(sandbox.python({"TERM": "xterm-256color"}), "256")
            self.assertEqual(prefs.detect_colors(), "16")          # without one: os.environ

    def test_the_home_of_the_environment_holds_its_tpot_json(self):
        sandbox = Sandbox(self)
        sandbox.prefs({"colors": "256"})
        dotconfig = os.path.join(sandbox.home, ".config", "tpotce")
        os.makedirs(dotconfig)
        shutil.copy(sandbox.file, dotconfig)
        os.remove(sandbox.file)
        environ = {"HOME": sandbox.home, "TERM": "xterm", "COLORTERM": "truecolor"}
        self.assertEqual(prefs.detect_colors(environ), "256")
        self.assertEqual(prefs.path(environ), os.path.join(dotconfig, "tpot.json"))

    def test_review_cases(self):
        """The cases of the review focus: tmux with LC_TERMINAL, screen over SSH, NO_COLOR."""
        sandbox = Sandbox(self)
        for env, expected in (({"TERM": "xterm-256color", "TMUX": "x", "LC_TERMINAL": "iTerm2"}, "256"),
                              ({"TERM": "screen", "LC_TERMINAL": "iTerm2"}, "16"),
                              ({"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2", "NO_COLOR": "1"}, "truecolor"),
                              ({"TERM": "xterm-256color", "LC_TERMINAL": "iterm2"}, "256"),
                              ({"TERM": "xterm-256color", "VTE_VERSION": "٣٦٠٠"}, "256"),
                              ({"TERM": "xterm-256color", "VTE_VERSION": "0003600"}, "truecolor"),
                              ({"TERM": "xterm-256color", "TMUX": ""}, "256"),
                              ({"TERM": "xterm-256color", "TMUX": "", "LC_TERMINAL": "iTerm2"}, "truecolor")):
            with self.subTest(env=env):
                self.assertEqual(sandbox.python(env), expected)


class ParityTest(unittest.TestCase):
    """bash and Python say the same, for every case and a sample of what a terminal may say."""

    def setUp(self):
        self.bash = modern_bash()
        if not self.bash:
            self.skipTest("no bash 4 or newer")

    def agree(self, runs):
        sandbox = Sandbox(self)
        runs = sorted(runs, key=lambda run: json.dumps(run[1], sort_keys=True))     # by tpot.json
        said, errors = sandbox.bash(self.bash, runs)
        self.assertEqual(errors, "")
        self.assertEqual(len(said), len(runs))
        python = [sandbox.python(env, data) for env, data in runs]
        differ = [(env, data, b, p) for (env, data), b, p in zip(runs, said, python) if b != p]
        self.assertEqual(differ, [], "bash and Python differ (env, tpot.json, bash, Python)")

    def test_every_case(self):
        self.agree([(case["env"], case.get("prefs")) for case in cases()])

    def test_a_sample_of_terminals(self):
        values = {
            "TPOT_COLORS": (None, "auto", "256", "bogus"),
            "COLORTERM": (None, "truecolor", "24BIT", "yes", ""),
            "TERM": (None, "", "xterm", "xterm-256color", "XTERM-256COLOR", "screen", "screen.xterm-256color",
                     "tmux-256color", "xterm-kitty", "foot-extra", "xterm-direct", "-direct", "dumb", "rio",
                     "linux", "xterm-16color"),
            "TMUX": (None, "", "/tmp/tmux-1000/default,1,0"),
            "TERM_PROGRAM": (None, "", "iTerm.app", "Apple_Terminal", "vscode", "tmux", "ITERM.APP", "WarpTerminal"),
            "LC_TERMINAL": (None, "iTerm2", "iterm2", ""),
            "VTE_VERSION": (None, "3600", "3599", "abc", "0003600", "36OO", "99999999999", ""),
            "KONSOLE_VERSION": (None, "", "240802"),
            "WT_SESSION": (None, "", "x"),
            "NO_COLOR": (None, "1"),
        }
        tpot_json = (None, {"colors": "256"}, {"colors": "auto"}, {"colors": "16", "icons": "nerd"})
        rng = random.Random(8)
        keys = list(values)
        runs = []
        for _ in range(700):
            env = {}
            for key in keys:
                value = rng.choice(values[key])
                if value is not None:
                    env[key] = value
            runs.append((env, rng.choice(tpot_json)))
        # and every terminal without a choice of the user's, so the terminal part is covered fully
        for term, mux, program, lc in itertools.product(values["TERM"], values["TMUX"],
                                                        values["TERM_PROGRAM"], values["LC_TERMINAL"]):
            env = {k: v for k, v in (("TERM", term), ("TMUX", mux), ("TERM_PROGRAM", program),
                                     ("LC_TERMINAL", lc)) if v is not None}
            runs.append((env, None))
        self.agree(runs)

    def test_a_disagreement_is_seen(self):
        """The check itself: a Python that says otherwise for one case does not pass."""
        real = prefs.detect_colors

        def wrong(environ=None):
            return "16" if (environ or {}).get("LC_TERMINAL") == "iTerm2" else real(environ)
        with mock.patch.object(prefs, "detect_colors", wrong):
            with self.assertRaises(AssertionError):
                self.agree([({"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2"}, None)])


class ApplyTest(unittest.TestCase):
    """apply_color_system: what Textual gets, before it is imported."""

    def environ(self, **extra):
        sandbox = Sandbox(self)
        env = sandbox.base()
        env.update(extra)
        return env

    def told(self, env):
        return {k: v for k, v in env.items() if k in ("TEXTUAL_COLOR_SYSTEM", prefs.SET_MARK)}

    def test_auto_takes_the_rule(self):
        for extra, expected in (({"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2"}, "truecolor"),
                                ({"TERM": "xterm-256color", "TMUX": "x", "LC_TERMINAL": "iTerm2"}, "256"),
                                ({"TERM": "xterm"}, "standard"),
                                ({"TERM": "xterm", "TPOT_COLORS": "auto", "TERM_PROGRAM": "vscode"}, "truecolor")):
            with self.subTest(env=extra):
                env = self.environ(**extra)
                prefs.apply_color_system(env)
                self.assertEqual(self.told(env), {"TEXTUAL_COLOR_SYSTEM": expected, prefs.SET_MARK: expected})

    def test_the_choice_of_the_environment_given(self):
        """TPOT_COLORS and tpot.json of the environment handed in, not of os.environ."""
        with mock.patch.dict(os.environ, {"TPOT_COLORS": "16"}):
            env = self.environ(TERM="xterm-256color", TPOT_COLORS="256", LC_TERMINAL="iTerm2")
            prefs.apply_color_system(env)
            self.assertEqual(self.told(env), {"TEXTUAL_COLOR_SYSTEM": "256", prefs.SET_MARK: "256"})
        env = self.environ(TERM="xterm-256color", LC_TERMINAL="iTerm2")
        with open(prefs.path(env), "w", encoding="utf-8") as out:
            json.dump({"colors": "16"}, out)
        prefs.apply_color_system(env)
        self.assertEqual(self.told(env), {"TEXTUAL_COLOR_SYSTEM": "standard", prefs.SET_MARK: "standard"})

    def test_a_textual_color_system_of_the_users_own_wins(self):
        env = self.environ(TERM="xterm-256color", LC_TERMINAL="iTerm2", TEXTUAL_COLOR_SYSTEM="256")
        prefs.apply_color_system(env)
        self.assertEqual(self.told(env), {"TEXTUAL_COLOR_SYSTEM": "256"})
        env = self.environ(TERM="xterm", TPOT_COLORS="truecolor", TEXTUAL_COLOR_SYSTEM="standard")
        prefs.apply_color_system(env)
        self.assertEqual(self.told(env), {"TEXTUAL_COLOR_SYSTEM": "standard"})

    def test_auto_or_empty_of_textual_is_no_choice(self):
        """TEXTUAL_COLOR_SYSTEM=auto is what Textual does without it: the rule decides."""
        for own in ("auto", ""):
            with self.subTest(own=own):
                env = self.environ(TERM="xterm-256color", LC_TERMINAL="iTerm2", TEXTUAL_COLOR_SYSTEM=own)
                prefs.apply_color_system(env)
                self.assertEqual(self.told(env), {"TEXTUAL_COLOR_SYSTEM": "truecolor", prefs.SET_MARK: "truecolor"})

    def test_a_restart_asks_the_terminal_again(self):
        """Restart the Manager (exec) keeps the environment: what tpot set there is not the user's."""
        env = self.environ(TERM="xterm-256color", LC_TERMINAL="iTerm2")
        prefs.apply_color_system(env)
        env.pop("LC_TERMINAL")
        prefs.apply_color_system(env)
        self.assertEqual(self.told(env), {"TEXTUAL_COLOR_SYSTEM": "256", prefs.SET_MARK: "256"})

    def test_windows_asks_its_console(self):
        """A console of Windows has no TERM: Rich asks the console itself (true colour on Windows 10),
        the rule only adds what it knows for sure (Windows Terminal)."""
        with mock.patch.object(prefs, "CONSOLE_KNOWS", True):
            env = self.environ()
            prefs.apply_color_system(env)
            self.assertEqual(self.told(env), {})
            env = self.environ(WT_SESSION="0b7a3f5e")
            prefs.apply_color_system(env)
            self.assertEqual(self.told(env), {"TEXTUAL_COLOR_SYSTEM": "truecolor", prefs.SET_MARK: "truecolor"})
            env = self.environ(TPOT_COLORS="256")
            prefs.apply_color_system(env)
            self.assertEqual(self.told(env), {"TEXTUAL_COLOR_SYSTEM": "256", prefs.SET_MARK: "256"})
            env = self.environ(TEXTUAL_COLOR_SYSTEM="256", TPOT_COLORS_SET="256")     # a restart there
            prefs.apply_color_system(env)
            self.assertEqual(self.told(env), {})


class EntryPointsTest(unittest.TestCase):
    """The launcher and the customizer hand the rule to Textual; palette and output agree."""

    def run_python(self, code, extra):
        sandbox = Sandbox(self)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": os.environ.get("LANG", "en_US.UTF-8")}
        env.update(sandbox.base())
        env.update(extra)
        out = subprocess.run([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    @unittest.skipUnless(TEXTUAL, "Textual is not installed, run with the venv of tpot")
    def test_palette_and_output_agree_for_iterm2_over_ssh(self):
        code = ("import os, sys; sys.path.insert(0, %r); from tpotctl import prefs; "
                "prefs.apply_color_system(os.environ); from tpotctl import theme; from textual.app import App; "
                "print(theme.color_system(), App().console.color_system, theme.color('comb'))" % REPO)
        for extra, expected in (({"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2"}, "truecolor truecolor #38001D"),
                                ({"TERM": "xterm", "LC_TERMINAL": "iTerm2"}, "truecolor truecolor #38001D"),
                                ({"TERM": "tmux-256color", "TMUX": "x", "LC_TERMINAL": "iTerm2"}, "256 256 #5f005f"),
                                ({"TERM": "xterm", "TERM_PROGRAM": "Apple_Terminal"}, "256 256 #5f005f")):
            with self.subTest(env=extra):
                self.assertEqual(self.run_python(code, extra), expected)

    STOP = ("import os, sys, runpy; sys.path.insert(0, %r); from tpotctl import bootstrap\n"
            "def stop(*args, **kwargs):\n"
            "    print(os.environ.get('TEXTUAL_COLOR_SYSTEM'), os.environ.get('TPOT_COLORS_SET'))\n"
            "    sys.exit(0)\n"
            "bootstrap.ensure = stop\n"
            "sys.argv = [%r] + %r\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n")

    def test_the_customizer_gets_the_same_rule(self):
        code = self.STOP % (REPO, os.path.join(REPO, "compose", "customizer.py"), ["--help"])
        self.assertEqual(self.run_python(code, {"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2"}),
                         "truecolor truecolor")
        self.assertEqual(self.run_python(code, {"TERM": "screen", "LC_TERMINAL": "iTerm2"}), "standard standard")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "tpot refuses root")
    def test_the_launcher_gets_the_same_rule(self):
        code = self.STOP % (REPO, os.path.join(REPO, "tpot"), ["ps"])
        self.assertEqual(self.run_python(code, {"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2"}),
                         "truecolor truecolor")


class SshdTest(unittest.TestCase):
    """sshd takes COLORTERM and the LC_TERMINAL of iTerm2 (openSUSE only takes some LC_* of its own)."""

    DROPIN = "/etc/ssh/sshd_config.d/tpot.conf"
    LINE = "AcceptEnv COLORTERM LC_TERMINAL LC_TERMINAL_VERSION"

    def tasks(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed, run with the venv of tpot")
        with open(os.path.join(REPO, "installer", "install", "tpot.yml"), encoding="utf-8") as handle:
            plays = yaml.safe_load(handle)
        return [task for play in plays for task in play.get("tasks", [])]

    def test_the_dropin_accepts_the_markers_of_the_terminal(self):
        tasks = self.tasks()
        found = [i for i, t in enumerate(tasks) if (t.get("lineinfile") or {}).get("path") == self.DROPIN]
        self.assertEqual(len(found), 1)
        task = tasks[found[0]]["lineinfile"]
        self.assertEqual(task["line"], self.LINE)
        # an update replaces the line of an earlier T-Pot instead of adding a second one
        regexp = re.compile(task["regexp"])
        for line in ("AcceptEnv COLORTERM", self.LINE):
            self.assertTrue(regexp.search(line), line)
        for line in ("AcceptEnv LANG LC_*", "AcceptEnv COLORTERMX", "#AcceptEnv COLORTERM"):
            self.assertFalse(regexp.search(line), line)
        self.assertEqual((task.get("create"), task.get("mode")), (True, "0644"))
        # still checked by sshd -t afterwards, with its privilege separation directory before
        checks = [i for i, t in enumerate(tasks) if str(t.get("command", "")) == "/usr/sbin/sshd -t"]
        privsep = [i for i, t in enumerate(tasks) if (t.get("file") or {}).get("path") == "/run/sshd"]
        self.assertEqual(len(checks), 1)
        self.assertTrue(found[0] < privsep[0] < checks[0], (found, privsep, checks))
        for name in ("AlmaLinux", "Debian", "Fedora", "openSUSE Tumbleweed", "Raspbian", "RedHat", "Rocky", "Ubuntu"):
            self.assertIn(f'"{name}"', tasks[found[0]]["when"], name)
            self.assertIn(f'"{name}"', tasks[checks[0]]["when"], name)


if __name__ == "__main__":
    unittest.main()
