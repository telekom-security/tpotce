"""The patched HoneyPotShell of Cowrie v3.1.3 (the whole honeypot.py, bashparse.py and script.py of
tests/fixtures/v3.1.3-full, run through the patch file) with a small protocol and commands around it: the turn
the top-level shell gives the reactor between the commands of a line must keep order, statuses, EOF, Ctrl-C,
nested shells, scripts and the script depth limit as they were. Needs twisted and lark (the venv)."""

import importlib.util
import posixpath
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.test_patch import FIXTURE, PATCH

try:
    from twisted.internet.task import Clock
    import lark  # noqa: F401
except ImportError:
    Clock = None

FULL = Path(__file__).parent / "fixtures" / "v3.1.3-full"
MODULES = ("cowrie", "cowrie.core", "cowrie.core.config", "cowrie.shell", "cowrie.shell.fs", "cowrie.shell.parser",
           "cowrie.shell.pipe", "cowrie.shell.bashparse", "cowrie.shell.honeypot", "cowrie.shell.script")


class Config:
    def get(self, section, option, fallback=None):
        return fallback

    getint = getfloat = getboolean = get

    def has_option(self, section, option):
        return False


class Fs:
    def __init__(self, files):
        self.files = files

    def exists(self, path):
        return path == "/root" or path in self.files

    def isdir(self, path):
        return path == "/root"

    def resolve_path(self, name, cwd):
        return posixpath.normpath(posixpath.join(cwd, name))

    def file_contents(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class Cmd:
    """A command of the stub protocol: writes to protocol.out, ends like HoneyPotCommand.exit."""

    consumes_stdin = False

    def __init__(self, protocol, *args):
        self.protocol, self.args, self.exit_code, self.pp = protocol, list(args), 0, None
        self.fs, self.cwd, self.shell = protocol.fs, "/root", protocol.cmdstack[-1]
        self.environ, self.exported = dict(self.shell.environ), set(self.shell.exported)
        self.user, self.functions = dict(self.shell.user), {}

    def start(self):
        self.exit()

    def write(self, text):
        self.protocol.out.append(text)

    errorWrite = write

    def resume(self):
        pass

    def exit(self):
        self.protocol.cmdstack.pop()
        if self.protocol.cmdstack:
            self.protocol.cmdstack[-1].last_exit_code = self.exit_code
            self.protocol.cmdstack[-1].resume()


class Echo(Cmd):
    def start(self):
        self.write(" ".join(self.args) + "\n")
        self.exit()


class Fail(Cmd):
    def start(self):
        self.exit_code = 1
        self.exit()


class Exit(Cmd):
    def start(self):
        stack = self.protocol.cmdstack
        code = int(self.args[0]) if self.args else stack[-2].last_exit_code
        stack.pop()
        stack.pop().exit_shell(code)
        if stack:
            stack[-1].resume()
        else:
            self.protocol.end_process(code)


class Bash(Cmd):
    """bash -c / sh -c as commands/bash.py execute_commands does it."""

    def start(self):
        shell = self.hp.HoneyPotShell(self.protocol, interactive=False)
        shell.name = "bash"
        self.protocol.cmdstack.append(shell)
        shell.lineReceived(self.args[1])
        self.exit_code = shell.last_exit_code
        self.exit()


class Script(Cmd):
    """./x.sh as protocol.py hands it to shell/script.py."""

    def start(self):
        name = self.pp.argv0
        self.script.run_script_file(self, self.fs.resolve_path(name, self.cwd), name=name,
                                    not_found_message=f"bash: {name}: No such file or directory\n",
                                    binary_message=f"bash: {name}: cannot execute binary file\n")
        self.exit()


class Ask(Cmd):
    """A command that waits for one line or EOF, like rm -i."""

    def start(self):
        self.write("? ")

    def lineReceived(self, line):
        self.write(f"got {line}\n")
        self.exit()

    def eofReceived(self):
        self.write("eof\n")
        self.exit()


class Protocol:
    def __init__(self, files, hp, script):
        self.cmdstack, self.out, self.ends = [], [], []
        self.environ = {"PATH": "/bin", "HOME": "/root"}
        self.user = SimpleNamespace(uid=0, gid=0, username="root", avatar=SimpleNamespace(home="/root"))
        self.fs, self.hostname = Fs(files), "svr"
        self.events = SimpleNamespace(dispatch=lambda *args, **kwargs: None)
        self.terminal = SimpleNamespace(write=lambda data: self.out.append(data.decode()), redirFiles=set())
        self.lineBuffer, self.lineBufferIndex = [], 0
        self.commands = {"echo": Echo, "true": Cmd, "false": Fail, "exit": Exit, "bash": Bash, "sh": Bash, "ask": Ask}
        for cls in (Cmd, Bash, Script):
            cls.hp, cls.script = hp, script

    def getCommand(self, name, paths, cwd):
        return Script if name.startswith("./") else self.commands.get(name)

    def call_command(self, pp, cls, *args):
        command = cls(self, *args)
        command.pp = pp
        self.cmdstack.append(command)
        command.start()

    def end_process(self, code):
        self.ends.append(code)
        self.cmdstack.clear()

    def setInsertMode(self):
        pass


def load(root):
    """The patched modules of root as cowrie.shell.*, with the rest of cowrie stubbed; returns (modules, restore)."""
    saved = {name: sys.modules.get(name) for name in MODULES}
    package, core, config, shell = (types.ModuleType(name) for name in MODULES[:4])
    config.CowrieConfig = Config()
    fs, parser, pipe = (types.ModuleType(name) for name in MODULES[4:7])
    fs.FileNotFound = FileNotFoundError
    parser.Field = str
    parser.CommandParser = lambda: SimpleNamespace(parse_redirections=lambda tokens: (list(tokens), []))
    pipe.FD_CAPTURE, pipe.FD_TERMINAL, pipe.PipeProtocol = "capture", "terminal", object
    package.core, package.shell, core.config, shell.fs = core, shell, config, fs
    for module in (package, core, config, shell, fs, parser, pipe):
        sys.modules[module.__name__] = module
    loaded = {}
    for name in ("bashparse", "honeypot", "script"):
        path = root / "src" / "cowrie" / "shell" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"cowrie.shell.{name}", path)
        loaded[name] = module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        setattr(shell, name, module)
        spec.loader.exec_module(module)

    def restore():
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    return loaded, restore


@unittest.skipIf(Clock is None, "needs twisted and lark (the venv of the tests)")
class RealShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        for name in ("honeypot", "bashparse", "script"):
            shutil.copy(FULL / f"{name}.py", root / "src" / "cowrie" / "shell" / f"{name}.py")
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        loaded, restore = load(root)
        cls.addClassCleanup(restore)
        cls.hp, cls.script = loaded["honeypot"], loaded["script"]
        cls.hp.HoneyPotShell._make_pipe = lambda shell, cmdclass, args, stdin, ops: SimpleNamespace(
            targets={1: ("terminal", None), 2: ("terminal", None)}, redirect_real_files=[], has_redirection_error=False)

    def session(self, interactive=False, files=None, yield_commands=1):
        self.clock = self.hp.reactor = Clock()
        protocol = Protocol(files or {}, self.hp, self.script)
        shell = self.hp.HoneyPotShell(protocol, interactive=interactive)
        shell._tpot_yield_commands = yield_commands  # 1: a turn after each command; the default is 64
        protocol.cmdstack.append(shell)
        return protocol, shell

    def drain(self):
        for _ in range(10000):
            if not self.clock.getDelayedCalls():
                return
            self.clock.advance(0)
        self.fail("the line never ended")

    def run_line(self, line, **kwargs):
        protocol, shell = self.session(**kwargs)
        shell.lineReceived(line)
        self.drain()
        return protocol, shell

    def one_turn(self, shell):
        """The next reactor turn of a pending yield, by itself."""
        shell._tpot_advance_later.cancel()
        shell._tpot_advance_again()

    def test_a_short_line_runs_in_one_go_as_upstream_does(self):
        protocol, shell = self.session(yield_commands=64)
        shell.lineReceived("echo a; echo b; echo c")
        self.assertEqual((protocol.out, protocol.ends, self.clock.getDelayedCalls()), (["a\n", "b\n", "c\n"], [0], []))

    def test_the_commands_of_a_line_run_in_order_with_a_turn_between(self):
        protocol, shell = self.session()
        shell.lineReceived("echo a; echo b; echo c")
        self.assertEqual(protocol.out, ["a\n"])
        self.assertEqual(len(self.clock.getDelayedCalls()), 1)
        self.drain()
        self.assertEqual(protocol.out, ["a\n", "b\n", "c\n"])
        self.assertEqual(protocol.ends, [0])

    def test_statuses_and_short_circuits_carry_over_the_turn(self):
        for line, want in (("false; echo $?", ["1\n"]), ("true && echo y", ["y\n"]), ("false || echo z", ["z\n"]),
                           ("false && echo no; echo $?", ["1\n"]), ("true; false; echo $?; true; echo $?", ["1\n", "0\n"])):
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line)[0].out, want)

    def test_exit_in_the_middle_of_a_line_ends_it(self):
        protocol, _ = self.run_line("echo a; exit 3; echo b")
        self.assertEqual((protocol.out, protocol.ends), (["a\n"], [3]))

    def test_an_eof_in_the_same_burst_waits_for_the_line(self):
        protocol, shell = self.session()
        shell.lineReceived("echo a; false; echo c")
        shell.eofReceived()
        self.assertEqual((protocol.out, protocol.ends), (["a\n"], []))
        self.drain()
        self.assertEqual((protocol.out, protocol.ends), (["a\n", "c\n"], [0]))
        protocol, shell = self.session()
        shell.lineReceived("echo a; echo b; false")
        shell.eofReceived()
        self.drain()
        self.assertEqual((protocol.out, protocol.ends), (["a\n", "b\n"], [1]))

    def test_an_eof_with_nothing_pending_ends_at_once(self):
        protocol, shell = self.session()
        shell.last_exit_code = 7
        shell.eofReceived()
        self.assertEqual(protocol.ends, [7])

    def test_ctrl_c_ends_the_rest_of_the_line(self):
        protocol, shell = self.session(interactive=True)
        shell.lineReceived("echo a; echo b; echo c")
        shell.handle_CTRL_C()
        self.drain()
        self.assertEqual(protocol.out[0], "a\n")
        self.assertNotIn("b\n", protocol.out)
        # 130 is 128 + SIGINT as bash gives it (derived, not recorded)
        self.assertEqual(shell.last_exit_code, 130)
        self.assertTrue(protocol.out[-1].endswith("# "))

    def test_an_eof_during_the_turn_reaches_the_next_reader(self):
        protocol, shell = self.session()
        shell.lineReceived("true; ask")
        shell.eofReceived()
        self.drain()
        self.assertEqual((protocol.out, protocol.ends), (["? ", "eof\n"], [0]))

    def test_typeahead_after_a_line_answers_the_reader_of_that_line(self):
        protocol, shell = self.session(interactive=True)
        shell.lineReceived("true; ask")
        shell.lineReceived("y")
        shell.lineReceived("echo z")
        self.drain()
        text = "".join(protocol.out)
        self.assertIn("got y\n", text)
        self.assertIn("z\n", text)
        self.assertNotIn("not found", text)
        self.assertLess(text.index("got y"), text.index("z\n"))
        self.assertEqual(shell._tpot_typeahead, [])

    def test_ctrl_c_during_the_turn_drops_typeahead_and_gives_the_parameters_back(self):
        protocol, shell = self.session(interactive=True)
        shell.lineReceived("f() { echo a; echo b; }; f x y")
        self.one_turn(shell)
        self.assertEqual(shell.environ.get("1"), "x")
        shell.lineReceived("echo late")
        shell.handle_CTRL_C()
        self.drain()
        self.assertNotIn("1", shell.environ)
        self.assertNotIn("late\n", protocol.out)
        self.assertNotIn("b\n", protocol.out)
        self.assertEqual(shell.cmdpending, [])

    def test_ctrl_c_during_the_turn_gives_a_redirected_group_back(self):
        protocol, shell = self.session(interactive=True)
        before = shell.fds
        shell.lineReceived("echo a; echo b; echo c")
        shell.fds = {1: ("file", "/tmp/o")}
        shell.cmdpending.append(self.hp._Continuation(lambda: shell._restore_fds(before)))
        shell.handle_CTRL_C()
        self.drain()
        self.assertIs(shell.fds, before)
        self.assertNotIn("c\n", protocol.out)
        self.assertEqual(shell.cmdpending, [])

    def test_a_shell_a_command_starts_gives_its_status_back(self):
        for line, want in (("bash -c 'false; true'; echo $?", ["0\n"]), ("bash -c 'true; false'; echo $?", ["1\n"]),
                           ("sh -c 'true; false' || echo x", ["x\n"]), ("sh -c 'false; true' && echo y", ["y\n"]),
                           ("bash -c 'echo a; echo b'; echo c", ["a\n", "b\n", "c\n"])):
            with self.subTest(line=line):
                protocol, _ = self.run_line(line)
                self.assertEqual((protocol.out, protocol.ends), (want, [0]))

    def test_a_nested_shell_runs_to_its_end_without_a_turn(self):
        protocol, shell = self.session()
        shell.lineReceived("bash -c 'echo a; echo b'; echo c")
        self.assertEqual(protocol.out, ["a\n", "b\n"])
        self.drain()

    def test_a_group_gives_its_status_back(self):
        for line, want in (("(false); echo $?", ["1\n"]), ("(echo a; echo b); echo c", ["a\n", "b\n", "c\n"])):
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line)[0].out, want)

    def test_a_script_of_two_commands_gives_its_status(self):
        files = {"/root/x.sh": b"echo one\nfalse\n"}
        protocol, _ = self.run_line("./x.sh; echo $?", files=files)
        self.assertEqual((protocol.out, protocol.ends), (["one\n", "1\n"], [0]))
        protocol, _ = self.run_line("./x.sh || echo failed", files=files)
        self.assertEqual(protocol.out, ["one\n", "failed\n"])

    def test_a_script_that_runs_itself_stops_at_the_depth_limit(self):
        protocol, _ = self.run_line("./x.sh", files={"/root/x.sh": b"echo hi\n./x.sh\n"})
        self.assertEqual(protocol.out.count("hi\n"), self.script.MAX_SCRIPT_DEPTH)
        self.assertIn("too many levels of recursion", protocol.out[-1])
        self.assertEqual((protocol.cmdstack, self.clock.getDelayedCalls()), ([], []))


if __name__ == "__main__":
    unittest.main()
