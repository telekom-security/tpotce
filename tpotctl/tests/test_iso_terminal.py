"""The ISO's local graphical terminal: isolated commands, no X server or host configuration."""

import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tpotctl.tests import ptyhelper
from tpotctl import ui_logo
from tpotctl.tests.test_iso import BASH, ID_STUB, ISO_DIR, REPO, X_TOOLS, write

TERMINAL = os.path.join(ISO_DIR, "terminal.sh")
UI_SH = os.path.join(REPO, "installer", "lib", "ui.sh")
# theme.py needs Textual; its true colours read as text
TRUECOLOR = ui_logo.theme_palettes()["truecolor"]
# what the child gets back exactly as the login had it: set, empty or unset
KEPT = ("XDG_CONFIG_HOME", "XDG_CONFIG_DIRS", "DBUS_SESSION_BUS_ADDRESS", "NO_AT_BRIDGE")


class TerminalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        self.runtime = os.path.join(self.tmp, "runtime")
        os.makedirs(self.runtime)
        self.result = os.path.join(self.tmp, "command.json")
        self.trace = os.path.join(self.tmp, "trace")
        self.stub("id", ID_STUB.split("\n", 1)[1])
        self.stub("tty", 'echo "${STUB_TTY:-/dev/tty2}"')
        self.stub("startx", '''
printf 'STARTX <%s>\\n' "$@" >> "$TRACE"
[ "${STARTX_FAIL:-0}" = 0 ] || exit "$STARTX_FAIL"
export DISPLAY=:99
"$1" "$2" "$3"
exit "${STARTX_EXIT:-0}"
''')
        self.stub("xterm", 'printf "XTERM <%s>\\n" "$@" >> "$TRACE"\nexit 99')
        self.stub("dbus-run-session", '''
printf 'DBUS <%s>\\n' "$@" >> "$TRACE"
[ "$1" = -- ] || exit 64
shift
export DBUS_SESSION_BUS_ADDRESS=unix:path=/private/bus
exec "$@"
''')
        # VTE: its own TERM / COLORTERM / VTE_VERSION for the command after --execute
        self.stub("xfce4-terminal", '''
if [ -e "$TRACE.wm" ]; then echo VTE_AFTER_WM >> "$TRACE"; else echo VTE_BEFORE_WM >> "$TRACE"; fi
printf 'VTE <%s>\\n' "$@" >> "$TRACE"
printf 'VTE_ENV XDG_CONFIG_HOME=%s XDG_CONFIG_DIRS=%s NO_AT_BRIDGE=%s DBUS=%s\\n' \\
  "${XDG_CONFIG_HOME-<unset>}" "${XDG_CONFIG_DIRS-<unset>}" "${NO_AT_BRIDGE-<unset>}" \\
  "${DBUS_SESSION_BUS_ADDRESS-<unset>}" >> "$TRACE"
cat "${XDG_CONFIG_HOME}/xfce4/xfconf/xfce-perchannel-xml/xfce4-terminal.xml" > "$TRACE.channel" 2>/dev/null
cat "${XDG_CONFIG_HOME}/xfce4/terminal/accels.scm" > "$TRACE.accels" 2>/dev/null
[ "${VTE_FAIL:-0}" = 0 ] || exit "$VTE_FAIL"
[ -z "${VTE_SIGNAL:-}" ] || kill -s "$VTE_SIGNAL" $$
while [ "$#" -gt 0 ] && [ "$1" != --execute ]; do shift; done
[ "$#" -gt 1 ] || exit 65
shift
export TERM=xterm-256color COLORTERM=truecolor VTE_VERSION=8001
exec "$@" </dev/null
''')
        # manages the screen (names itself on the root window) only after a moment, as the real one
        self.stub("matchbox-window-manager", '''
rm -f "$TRACE.wm"
printf 'MATCHBOX <%s>\\n' "$@" >> "$TRACE"
[ "$3" != -kbdconfig ] || cat "$4" > "$TRACE.keys"
[ -z "${WM_EXIT:-}" ] || exit "$WM_EXIT"
trap 'echo MATCHBOX_STOPPED >> "$TRACE"; exit 0' TERM
[ -n "${WM_NEVER_READY:-}" ] || { sleep "${WM_DELAY:-0.3}"; : > "$TRACE.wm"; }
while :; do sleep 0.1; done
''')
        self.stub("xprop", '''
{ printf XPROP; printf ' <%s>' "$@"; echo; } >> "$TRACE.xprop"
if [ -e "$TRACE.wm" ]; then echo "_NET_SUPPORTING_WM_CHECK(WINDOW): window id # 0x400001"
else echo "_NET_SUPPORTING_WM_CHECK:  not found."; fi
''')
        self.stub("fc-list", '{ printf FCLIST; printf " <%s>" "$@"; echo; } >> "$TRACE"\n[ -z "${FONT_MISSING:-}" ]')
        for name in ("Xorg", "xauth"):
            self.stub(name, "exit 0")
        # the size of the terminal, call by call (STTY_SIZES, the last one stays): VTE resizes it once
        # the window manager made the window fullscreen
        self.stub("stty", '''
[ "$1" = size ] || exit 0
n=$(cat "$TRACE.stty" 2>/dev/null || echo 0)
echo $((n + 1)) > "$TRACE.stty"
read -r -a sizes <<< "${STTY_SIZES:-56x213}"
[ "$n" -lt "${#sizes[@]}" ] || n=$((${#sizes[@]} - 1))
echo "${sizes[n]%x*} ${sizes[n]#*x}"
''')
        # the command: what it got, and the colours both rules (Manager, scripts) choose there
        write(os.path.join(self.bin, "tpot"), f"""#!{sys.executable}
import json, os, subprocess, sys
sys.path.insert(0, {REPO!r})
from tpotctl import prefs
size = subprocess.run(["stty", "size"], capture_output=True, text=True).stdout.strip()
ui = subprocess.run([{BASH!r}, "-c", 'source "$1" && fuUI_COLORS', "-", {UI_SH!r}],
                    capture_output=True, text=True).stdout.strip()
with open(os.environ["RESULT"], "w") as handle:
    json.dump({{"args": sys.argv[1:], "term": os.environ.get("TERM"),
               "color": os.environ.get("COLORTERM"),
               "active": os.environ.get("TPOT_TERMINAL_ACTIVE"),
               "checked": os.environ.get("TPOT_TERMINAL_CHECKED"),
               "kept": {{name: os.environ.get(name) for name in {KEPT!r}}},
               "colors": prefs.detect_colors(), "ui_colors": ui, "size": size}}, handle)
sys.exit(int(os.environ.get("COMMAND_EXIT", "0")))
""", 0o755)

    def stub(self, name, body):
        write(os.path.join(self.bin, name), f"#!{BASH}\n{body}\n", 0o755)

    def env(self, **overrides):
        return {"PATH": self.bin + os.pathsep + os.environ["PATH"], "HOME": self.tmp,
                "TERM": "linux", "XDG_RUNTIME_DIR": self.runtime, "TRACE": self.trace,
                "RESULT": self.result, "TPOT_GUM": "off", **overrides}

    def run_terminal(self, *args, tty=True, **env):
        if tty:
            code, out, _ = ptyhelper.run([BASH, TERMINAL, *args], self.env(**env), cwd=self.tmp, timeout=30)
            return code, out.decode("utf-8", "replace")
        result = subprocess.run([BASH, TERMINAL, *args], env=self.env(**env),
                                capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout + result.stderr

    def command_result(self):
        with open(self.result) as handle:
            return json.load(handle)

    def seen(self, **expected):
        """The keys of the command's record the test names."""
        found = self.command_result()
        return {key: found[key] for key in expected}

    def traced(self, suffix=""):
        with open(self.trace + suffix) as handle:
            return handle.read()

    def session_started(self):
        """The X server, bus or terminal lines of the trace: none where no session started."""
        if not os.path.exists(self.trace):
            return []
        return [line for line in self.traced().splitlines() if line.startswith(("STARTX", "DBUS", "VTE"))]

    def terminal_option(self, name):
        for line in self.traced().splitlines():
            if line.startswith(f"VTE <{name}="):
                return line[len(f"VTE <{name}="):-1]
        self.fail(f"xfce4-terminal got no {name}")

    def test_local_session_uses_private_vte_and_cascadia(self):
        code, out = self.run_terminal()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.seen(args=[], term="xterm-256color", color="truecolor", active="1", checked=None),
                         {"args": [], "term": "xterm-256color", "color": "truecolor", "active": "1", "checked": None})
        trace = self.traced()
        for flag in ("<vt2>", "<-keeptty>", "<-nolisten>", "<tcp>", "<-depth>", "<24>",
                     "DBUS <-->", "VTE <--disable-server>", "VTE <--fullscreen>", "VTE <--hide-menubar>",
                     "VTE <--hide-toolbar>", "VTE <--hide-scrollbar>", "VTE <--hide-borders>",
                     "VTE <--font=Cascadia Mono,monospace 12>", "VTE <--execute>"):
            self.assertIn(flag, trace)
        self.assertNotIn("<-ac>", trace)
        self.assertNotIn("XTERM", trace)
        # the terminal reads its preferences from a folder of its session, not from the user's
        private = trace.split("VTE_ENV XDG_CONFIG_HOME=", 1)[1].split(" ", 1)[0]
        self.assertTrue(private.startswith(self.runtime + os.sep), private)
        self.assertIn(f"XDG_CONFIG_DIRS={private} ", trace)
        self.assertIn("DBUS=unix:path=/private/bus", trace)
        channel = self.traced(".channel")
        for prop in ('name="shortcuts-no-menukey" type="bool" value="true"',
                     'name="shortcuts-no-mnemonics" type="bool" value="true"',
                     'name="misc-confirm-close" type="bool" value="false"'):
            self.assertIn(prop, channel)
        # the window manager grabs no key (its default closes the window on Alt+C, starts xterm on Ctrl+Alt+X)
        self.assertIn("MATCHBOX <-kbdconfig>", trace)
        keys = self.traced(".keys")
        self.assertEqual([line for line in keys.splitlines() if line.strip() and not line.startswith("#")], [])
        # and ends with the session, a window manager left running would serve the next one
        self.assertIn("MATCHBOX_STOPPED", trace)
        accels = self.traced(".accels")
        for action in ("contents", "new-tab", "new-window", "fullscreen"):
            self.assertIn(f'(gtk_accel_path "<Actions>/terminal-window/{action}" "")', accels)
        self.assertEqual(os.listdir(self.runtime), [])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, ".local/state/tpot/terminal.log")))

    def terminal_log(self):
        with open(os.path.join(self.tmp, ".local/state/tpot/terminal.log")) as handle:
            return handle.read()

    def test_terminal_opens_only_once_the_window_manager_manages_the_screen(self):
        # mapped before that, the terminal starts its command at 80 x 24 and goes fullscreen only later:
        # the command (the logo, the first layout) sees the wrong size
        code, out = self.run_terminal(WM_DELAY="0.6")
        self.assertEqual(code, 0, out)
        self.assertIn("VTE_AFTER_WM", self.traced())
        self.assertNotIn("VTE_BEFORE_WM", self.traced())
        self.assertIn("XPROP <-root> <_NET_SUPPORTING_WM_CHECK>", self.traced(".xprop"))
        self.assertNotIn("window manager", self.terminal_log())

    def test_the_command_starts_once_the_terminal_is_fullscreen(self):
        # VTE starts the command a moment after the window opened at 80 x 24, often before the window
        # manager made it fullscreen: the command (the logo, the first layout) would see 80 x 24
        code, out = self.run_terminal(STTY_SIZES="24x80 24x80 24x80 56x213")
        self.assertEqual(code, 0, out)
        self.assertIn("VTE <--geometry=80x24>", self.traced())
        self.assertEqual(self.seen(size="56 213"), {"size": "56 213"})

    def test_the_wait_for_fullscreen_is_bounded_and_needs_the_window_manager(self):
        # a screen of exactly 80 x 24 cells stays so: the wait ends after about 3 seconds
        started = time.monotonic()
        code, out = self.run_terminal(STTY_SIZES="24x80")
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0, out)
        self.assertEqual(self.seen(size="24 80"), {"size": "24 80"})
        self.assertGreater(int(self.traced(".stty")), 2)
        self.assertGreaterEqual(elapsed, 1.9)
        self.assertLess(elapsed, 6)
        # without a window manager nothing makes the window fullscreen: no wait (every startx is a new
        # X server, the window manager of the run before is not named on its root window)
        for suffix in (".stty", ".wm"):
            os.remove(self.trace + suffix)
        os.remove(self.result)
        code, out = self.run_terminal(STTY_SIZES="24x80", WM_EXIT="1")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.seen(size="24 80"), {"size": "24 80"})
        self.assertEqual(self.traced(".stty"), "1\n")

    def test_a_window_manager_that_fails_is_logged_and_the_terminal_still_opens(self):
        # an ended one is noticed at once (its own message, not the one of the timeout), the wait for
        # one that never manages the screen is bounded
        for env, message, limit in (({"WM_EXIT": "1"}, "the window manager ended at its start", None),
                                    ({"WM_NEVER_READY": "1"}, "the window manager did not start within 5 seconds", 9)):
            with self.subTest(env=env):
                started = time.monotonic()
                code, out = self.run_terminal(**env)
                self.assertEqual(code, 0, out)
                if limit:
                    self.assertLess(time.monotonic() - started, limit)
                self.assertIn("VTE_BEFORE_WM", self.traced())
                self.assertEqual(self.seen(active="1"), {"active": "1"})
                self.assertIn(f"T-Pot terminal: {message}", self.terminal_log())
                os.remove(self.trace)
                os.remove(self.result)

    def test_terminal_colours_are_the_theme_in_truecolor(self):
        self.assertEqual(self.terminal_colours(), (TRUECOLOR["INK"].lower(), TRUECOLOR["GLASS"].lower()))
        self.assertEqual(self.seen(color="truecolor", colors="truecolor", ui_colors="truecolor"),
                         {"color": "truecolor", "colors": "truecolor", "ui_colors": "truecolor"})
        # the user's own choice still wins, in tpot.json as in TPOT_COLORS
        write(os.path.join(self.tmp, "config", "tpotce", "tpot.json"), '{"colors": "256"}\n')
        code, out = self.run_terminal(XDG_CONFIG_HOME=os.path.join(self.tmp, "config"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.seen(colors="256", ui_colors="256"), {"colors": "256", "ui_colors": "256"})
        code, out = self.run_terminal(TPOT_COLORS="16")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.seen(colors="16", ui_colors="16"), {"colors": "16", "ui_colors": "16"})

    def terminal_colours(self):
        code, out = self.run_terminal()
        self.assertEqual(code, 0, out)
        return self.terminal_option("--color-bg").lower(), self.terminal_option("--color-text").lower()

    def test_terminal_preferences_do_not_replace_child_configuration(self):
        user_channel = os.path.join(self.tmp, "config", "xfce4", "xfconf", "xfce-perchannel-xml",
                                    "xfce4-terminal.xml")
        write(user_channel, '<channel name="xfce4-terminal"><property name="font-name" value="Mono 30"/></channel>\n')
        cases = ({"XDG_CONFIG_HOME": os.path.join(self.tmp, "config"),
                  "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "NO_AT_BRIDGE": ""},
                 {"XDG_CONFIG_DIRS": "/etc/xdg:/usr/local/etc/xdg"})
        for login in cases:
            with self.subTest(login=login):
                code, out = self.run_terminal(**login)
                self.assertEqual(code, 0, out)
                self.assertEqual(self.command_result()["kept"], {name: login.get(name) for name in KEPT})
                self.assertNotIn(os.path.join(self.tmp, "config"), self.traced().split("VTE_ENV", 1)[1])
                os.remove(self.trace)
        with open(user_channel) as handle:
            self.assertIn("Mono 30", handle.read())

    def test_ready_and_packages_name_the_graphical_terminal(self):
        result = subprocess.run([BASH, TERMINAL, "--ready"], env=self.env(), capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        self.assertIn("FCLIST <-q> <Cascadia Mono>", self.traced())
        result = subprocess.run([BASH, TERMINAL, "--ready"], env=self.env(FONT_MISSING="1"),
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout.strip()), (1, "the font Cascadia Mono"))
        for name in X_TOOLS:
            with self.subTest(missing=name):
                for helper in ("dirname", "basename"):
                    if not os.path.exists(os.path.join(self.bin, helper)):
                        os.symlink(shutil.which(helper), os.path.join(self.bin, helper))
                os.rename(os.path.join(self.bin, name), os.path.join(self.tmp, name))
                result = subprocess.run([BASH, TERMINAL, "--ready"], env=self.env(PATH=self.bin),
                                        capture_output=True, text=True)
                os.rename(os.path.join(self.tmp, name), os.path.join(self.bin, name))
                self.assertEqual((result.returncode, result.stdout.strip()), (1, name))
        result = subprocess.run([BASH, TERMINAL, "--packages"], env=self.env(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        packages = result.stdout.split()
        self.assertEqual(packages, sorted(set(packages)))
        self.assertLessEqual({"xfce4-terminal", "dbus-daemon", "fontconfig", "fonts-cascadia-code",
                              "fonts-dejavu-core", "matchbox-window-manager", "x11-utils", "xauth", "xinit",
                              "xserver-xorg-core"}, set(packages))
        self.assertNotIn("xterm", packages)
        self.assertFalse([name for name in packages if name.startswith(("xfce4-session", "task-", "xfdesktop"))])

    def test_vte_failure_or_cancellation_never_reexecutes_the_command(self):
        for env, message in (({"VTE_FAIL": "3"}, "ended before the command completed"),
                             ({"VTE_SIGNAL": "TERM"}, "ended before the command completed")):
            with self.subTest(env=env):
                code, out = self.run_terminal(**env)
                self.assertEqual(code, 1, out)
                self.assertIn(message, out)
                self.assertFalse(os.path.exists(self.result))
                self.assertEqual(os.listdir(self.runtime), [])
        for env, missing in (({"FONT_MISSING": "1"}, "the font Cascadia Mono"), ({}, "dbus-run-session")):
            with self.subTest(missing=missing):
                if missing == "dbus-run-session":
                    os.remove(os.path.join(self.bin, "dbus-run-session"))
                    for helper in ("dirname", "basename"):
                        if not os.path.exists(os.path.join(self.bin, helper)):
                            os.symlink(shutil.which(helper), os.path.join(self.bin, helper))
                    env = {"PATH": self.bin}
                code, out = self.run_terminal(**env)
                self.assertEqual(code, 1, out)
                self.assertIn(f"{missing} is missing", out)
                self.assertIn("xfce4-terminal", out, "the packages to install are named")
                self.assertFalse(os.path.exists(self.result))
                write(os.path.join(self.tmp, "dev", "fb0"), "")
                code, out = self.run_terminal("--auto", "--", "tpot", TPOT_ISO_DEV=os.path.join(self.tmp, "dev"),
                                              **env)
                self.assertEqual(code, 0, out)
                self.assertIn("text console", out)
                self.assertIn(missing, out)
                self.assertNotIn("ISO", out)
                self.assertIsNone(self.command_result()["active"])
                os.remove(self.result)

    def test_arguments_remain_arguments_including_double_dash_and_shell_characters(self):
        args = ("status", "--", "two words", "literal; echo not-a-command", "")
        code, out = self.run_terminal("--", "tpot", *args)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.command_result()["args"], list(args))

    def test_ssh_serial_graphical_and_explicit_text_mode_keep_their_terminal(self):
        for env in ({"SSH_CONNECTION": "remote"}, {"SSH_TTY": "/dev/pts/1"},
                    {"DISPLAY": ":1"}, {"WAYLAND_DISPLAY": "wayland-0"},
                    {"STUB_TTY": "/dev/ttyAMA0"}, {"STUB_TTY": "/dev/pts/2"},
                    {"TPOT_TERMINAL": "off"}):
            with self.subTest(env=env):
                code, out = self.run_terminal("status", COLORTERM="original", **env)
                self.assertEqual(code, 0, out)
                self.assertEqual(self.seen(args=1, term=1, color=1, active=1, checked=1),
                                 {"args": ["status"], "term": "linux", "color": "original", "active": None,
                                  "checked": None})
                self.assertFalse(os.path.exists(self.trace))
        code, out = self.run_terminal("status", tty=False)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(self.trace))

    def test_auto_route_checks_once_and_falls_back_without_graphics(self):
        os.makedirs(os.path.join(self.tmp, "dev"))
        code, out = self.run_terminal("--auto", "--", "tpot", TPOT_ISO_DEV=os.path.join(self.tmp, "dev"))
        self.assertEqual(code, 0, out)
        self.assertIn("text console", out)
        self.assertEqual(self.session_started(), [])
        self.assertEqual(self.command_result()["color"], None)
        self.assertEqual(self.command_result()["active"], None)
        self.assertEqual(self.command_result()["args"], [])
        self.assertEqual(self.command_result()["checked"], "1")

    def test_auto_route_uses_the_same_session_when_a_graphics_device_exists(self):
        write(os.path.join(self.tmp, "dev", "dri", "card0"), "")
        code, out = self.run_terminal("--auto", "--", "tpot", TPOT_ISO_DEV=os.path.join(self.tmp, "dev"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.command_result()["active"], "1")
        self.assertEqual(self.command_result()["color"], "truecolor")
        self.assertEqual(self.command_result()["args"], [])
        self.assertEqual(self.command_result()["checked"], "1")

    def test_auto_route_honors_the_text_console_chosen_for_this_boot(self):
        flag = os.path.join(self.tmp, "text")
        write(flag, "")
        write(os.path.join(self.tmp, "dev", "dri", "card0"), "")
        code, out = self.run_terminal("--auto", "--", "tpot", TPOT_ISO_TEXT=flag,
                                      TPOT_ISO_DEV=os.path.join(self.tmp, "dev"))
        self.assertEqual(code, 0, out)
        self.assertIn("chosen for this boot", out)
        self.assertFalse(os.path.exists(self.trace))
        self.assertIsNone(self.command_result()["active"])
        code, out = self.run_terminal(TPOT_ISO_TEXT=flag)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.command_result()["active"], "1", "an explicit graphical launch can retry")

    def test_auto_missing_packages_fall_back_but_failed_start_does_not_rerun(self):
        write(os.path.join(self.tmp, "dev", "fb0"), "")
        os.remove(os.path.join(self.bin, "xauth"))
        code, out = self.run_terminal("--auto", "--", "tpot", TPOT_ISO_DEV=os.path.join(self.tmp, "dev"))
        self.assertEqual(code, 0, out)
        self.assertIn("xauth", out)
        self.assertIn("text console", out)
        self.assertNotIn("ISO", out)
        self.assertEqual(self.command_result()["checked"], "1")
        self.stub("xauth", "exit 0")
        os.remove(self.result)
        code, out = self.run_terminal("--auto", "--", "tpot", TPOT_ISO_DEV=os.path.join(self.tmp, "dev"),
                                      STARTX_FAIL="23")
        self.assertEqual(code, 1, out)
        self.assertFalse(os.path.exists(self.result))

    def test_failed_graphics_are_reported_without_running_the_command_again(self):
        code, out = self.run_terminal(STARTX_FAIL="23")
        self.assertEqual(code, 1, out)
        self.assertIn("startx: 23", out)
        self.assertIn("TPOT_TERMINAL=off", out)
        self.assertFalse(os.path.exists(self.result))
        self.assertEqual(os.listdir(self.runtime), [])

    def test_child_failure_is_preserved_even_if_startx_returns_success(self):
        code, out = self.run_terminal(COMMAND_EXIT="17")
        self.assertEqual(code, 17, out)
        self.assertEqual(os.listdir(self.runtime), [])
        with open(os.path.join(self.tmp, ".local/state/tpot/terminal.log")) as handle:
            self.assertIn("command ended with status 17", handle.read())

    def test_server_shutdown_failure_is_not_reported_as_success(self):
        code, out = self.run_terminal(STARTX_EXIT="19")
        self.assertEqual(code, 19, out)
        self.assertIn("startx ended with status 19", out)

    def test_missing_packages_give_an_explicit_error(self):
        for name in ("dirname", "basename"):
            os.symlink(shutil.which(name), os.path.join(self.bin, name))
        os.remove(os.path.join(self.bin, "xauth"))
        code, out = self.run_terminal(PATH=self.bin)
        self.assertEqual(code, 1, out)
        self.assertIn("xauth is missing", out)
        self.assertFalse(os.path.exists(self.result))

    def test_root_unknown_mode_missing_command_and_help(self):
        for args, env, message in (((), {"STUB_UID": "0"}, "do not use sudo"),
                                   ((), {"TPOT_TERMINAL": "yes"}, "must be auto or off"),
                                   (("--",), {}, "command is required"),
                                   (("--", "/does/not/exist"), {}, "Command not found")):
            code, out = self.run_terminal(*args, **env)
            self.assertNotEqual(code, 0, out)
            self.assertIn(message, out)
            self.assertFalse(os.path.exists(self.result))
        code, out = self.run_terminal("-h")
        self.assertEqual(code, 0, out)
        self.assertIn("Usage: tpot-terminal", out)


class ManagerTerminalTest(unittest.TestCase):
    def setUp(self):
        from tpotctl import bootstrap, cli
        self.launcher = os.path.join(ISO_DIR, "..", "..", "tpot")
        self.run = runpy.run_path(self.launcher)["run"]
        for patch in (mock.patch.dict(os.environ, {}, clear=True),
                      mock.patch("sys.platform", "linux"),
                      mock.patch("os.geteuid", return_value=1000),
                      mock.patch("sys.stdin.isatty", return_value=True),
                      mock.patch("sys.stdout.isatty", return_value=True),
                      mock.patch.object(bootstrap, "ensure", return_value=None),
                      mock.patch.object(bootstrap, "ensure_link"),
                      mock.patch.object(cli, "main", return_value=71)):
            patch.start()
            self.addCleanup(patch.stop)
        self.child = mock.patch.object(bootstrap, "run_child", return_value=17)
        self.run_child = self.child.start()
        self.addCleanup(self.child.stop)

    def test_local_menu_enters_the_shared_terminal_and_preserves_its_exit_status(self):
        with self.assertRaises(SystemExit) as stopped:
            self.run([])
        self.assertEqual(stopped.exception.code, 17)
        self.run_child.assert_called_once_with(
            ["bash", TERMINAL, "--auto", "--", sys.executable, os.path.realpath(self.launcher)])

    def test_commands_pipes_other_platforms_and_an_already_checked_session_do_not_reenter(self):
        for argv, environment, platform, terminal in (
                (["-h"], {}, "linux", True), (["status"], {}, "linux", True),
                ([], {}, "linux", False), ([], {}, "darwin", True),
                ([], {"TPOT_TERMINAL_CHECKED": "1"}, "linux", True),
                ([], {"TPOT_TERMINAL_ACTIVE": "1"}, "linux", True)):
            with self.subTest(argv=argv, env=environment, platform=platform, terminal=terminal), \
                 mock.patch.dict(os.environ, environment, clear=True), \
                 mock.patch("sys.platform", platform), \
                 mock.patch("sys.stdin.isatty", return_value=terminal):
                with self.assertRaises(SystemExit) as stopped:
                    self.run(argv)
                self.assertEqual(stopped.exception.code, 71)
                self.run_child.assert_not_called()


if __name__ == "__main__":
    unittest.main()
