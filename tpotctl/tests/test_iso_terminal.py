"""The ISO's local graphical terminal: isolated commands, no X server or host configuration."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tpotctl.tests import ptyhelper
from tpotctl.tests.test_iso import BASH, ID_STUB, ISO_DIR, write

TERMINAL = os.path.join(ISO_DIR, "terminal.sh")


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
        self.stub("xterm", '''
printf 'XTERM <%s>\\n' "$@" >> "$TRACE"
while [ "$1" != -e ]; do shift; done
shift
export TERM=xterm-256color
unset COLORTERM
exec "$@" </dev/null
''')
        self.stub("matchbox-window-manager", 'trap "exit 0" TERM\nwhile :; do sleep 1; done')
        for name in ("Xorg", "xauth"):
            self.stub(name, "exit 0")
        write(os.path.join(self.bin, "tpot"), f"""#!{sys.executable}
import json, os, sys
with open(os.environ["RESULT"], "w") as handle:
    json.dump({{"args": sys.argv[1:], "term": os.environ.get("TERM"),
               "color": os.environ.get("COLORTERM"),
               "active": os.environ.get("TPOT_TERMINAL_ACTIVE")}}, handle)
sys.exit(int(os.environ.get("COMMAND_EXIT", "0")))
""", 0o755)

    def stub(self, name, body):
        write(os.path.join(self.bin, name), f"#!{BASH}\n{body}\n", 0o755)

    def env(self, **overrides):
        return {"PATH": self.bin + os.pathsep + os.environ["PATH"], "HOME": self.tmp,
                "TERM": "linux", "XDG_RUNTIME_DIR": self.runtime, "TRACE": self.trace,
                "RESULT": self.result, **overrides}

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

    def test_local_session_has_truecolor_font_fullscreen_and_private_authentication(self):
        code, out = self.run_terminal()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.command_result(),
                         {"args": [], "term": "xterm-256color", "color": "truecolor", "active": "1"})
        with open(self.trace) as handle:
            trace = handle.read()
        for flag in ("<vt2>", "<-keeptty>", "<-nolisten>", "<tcp>", "<-depth>", "<24>",
                     "<-fullscreen>", "<DejaVu Sans Mono>", "<XTerm*directColor: true>"):
            self.assertIn(flag, trace)
        self.assertNotIn("<-ac>", trace)
        self.assertEqual(os.listdir(self.runtime), [])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, ".local/state/tpot/terminal.log")))

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
                self.assertEqual(self.command_result(), {"args": ["status"], "term": "linux",
                                                        "color": "original", "active": None})
                self.assertFalse(os.path.exists(self.trace))
        code, out = self.run_terminal("status", tty=False)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(self.trace))

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


if __name__ == "__main__":
    unittest.main()
