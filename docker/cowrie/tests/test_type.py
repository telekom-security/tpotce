"""type of BusyBox ash (commands/base.py of the patched Cowrie): what the ash of the Archer C7 v5
(BusyBox v1.19.4 of the 220715 image under qemu-mips-static) prints for keywords, special and regular
builtins, programs on PATH and names it has not; and type of bash on the other personas (debian:12, centos:7,
ubuntu:24.04: keywords, functions, builtins, the first program of PATH, -a / -p / -P / -t)."""
import configparser
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, module_parts
from tpot_personas import catalog, firmware, render
from tpot_personas.identity import draw


class Fs:
    def __init__(self, files):
        self.files = files

    def exists(self, path):
        return path in self.files

    def isdir(self, path):
        return self.files.get(path) == "dir"


class TypeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        cls.source = (root / "src/cowrie/commands/base.py").read_text()

    def run_type(self, args, builtins=None, which_dirs=None, path="/bin:/sbin:/usr/bin:/usr/sbin", bash=False,
                 functions=None, prefix="-bash: "):
        values = {("shell", "builtins"): builtins} if builtins is not None else {}
        values[("shell", "not_found_style")] = "bash" if bash else "ash"
        if which_dirs is not None:
            handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
            self.addCleanup(Path(handle.name).unlink)
            handle.write("".join(f"{name} {','.join(folders)}\n" for name, folders in which_dirs.items()))
            handle.close()
            values[("shell", "which_dirs")] = handle.name

        class HoneyPotCommand:
            def __init__(self):
                self.out, self.exit_code = [], 0

            def write(self, text):
                self.out.append(text)

            def errorWrite(self, text):
                self.out.append(f"<err>{text}")

        names = {"CowrieConfig": FakeConfig(values), "HoneyPotCommand": HoneyPotCommand, "commands": {}}
        module_parts(self.source, ["Command_tpot_type"], names)
        command = names["Command_tpot_type"]()
        command.args = list(args)
        command.shell = types.SimpleNamespace(functions=functions or {}, error_prefix=lambda: prefix)
        command.environ = {"PATH": path}
        command.fs = Fs({"/bin/ls": "file", "/usr/bin/wget": "file", "/bin": "dir", "/usr/bin/sbin": "dir",
                         "/bin/sh": "file", "/usr/bin/sh": "file", "/usr/bin/ls": "file", "/usr/bin/echo": "file",
                         "/bin/echo": "file"})
        command.call()
        return "".join(command.out), command.exit_code

    def test_the_recorded_answers(self):
        c7 = ",".join(firmware.TPLINK_ASH_BUILTINS)
        out, code = self.run_type([".", ":", "break", "continue", "eval", "exec", "exit", "export", "readonly",
                                   "return", "set", "shift", "source", "times", "trap", "unset", "cd", "echo",
                                   "help", "ulimit", "if", "then", "while", "history", "ls"], c7)
        expected = "".join(f"{name} is a special shell builtin\n" for name in (
            ".", ":", "break", "continue", "eval", "exec", "exit", "export", "readonly", "return", "set", "shift",
            "source", "times", "trap", "unset"))
        expected += "".join(f"{name} is a shell builtin\n" for name in ("cd", "echo", "help", "ulimit"))
        expected += "".join(f"{name} is a shell keyword\n" for name in ("if", "then", "while"))
        expected += "history: not found\nls is /bin/ls\n"
        self.assertEqual((out, code), (expected, 127))
        self.assertEqual(self.run_type(["ls", "wget"], c7), ("ls is /bin/ls\nwget is /usr/bin/wget\n", 0))
        self.assertEqual(self.run_type(["/bin/ls", "/bin/nosuch"], c7), ("/bin/ls is /bin/ls\n/bin/nosuch: not found\n",
                                                                         127))
        self.assertEqual(self.run_type([], c7), ("", 0))

    def test_the_folders_of_the_system_as_which(self):
        # a device with folders of its own in Cowrie's merged tree (D-Link): sh is in /bin, as which says
        debian = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        self.assertEqual(self.run_type(["sh"], path=debian), ("sh is /usr/bin/sh\n", 0))
        self.assertEqual(self.run_type(["sh"], which_dirs={"sh": ("bin",)}, path=debian), ("sh is /bin/sh\n", 0))

    def test_ash_without_the_key_has_history(self):
        # the BusyBox devices without their own builtin table: the 1.19.4 table with history (their ash keeps one)
        self.assertEqual(self.run_type(["history", "cd"]), ("history is a shell builtin\ncd is a shell builtin\n", 0))

    def test_bash_type_of_the_recordings(self):
        # bash 4.2 / 5.1 / 5.2 (centos:7, debian:12, ubuntu:24.04), root's PATH
        debian = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

        def bash(args, **kw):
            return self.run_type(args, path=debian, bash=True, **kw)

        self.assertEqual(bash(["ls", "cd", "if", "type", "echo", "[[", "exit"]), (
            "ls is /usr/bin/ls\ncd is a shell builtin\nif is a shell keyword\ntype is a shell builtin\n"
            "echo is a shell builtin\n[[ is a shell keyword\nexit is a shell builtin\n", 0))
        self.assertEqual(bash(["nosuchcmd"]), ("<err>-bash: type: nosuchcmd: not found\n", 1))
        self.assertEqual(bash(["ls", "nosuchcmd", "cd"], prefix="bash: line 1: "), (
            "ls is /usr/bin/ls\n<err>bash: line 1: type: nosuchcmd: not found\ncd is a shell builtin\n", 1))
        self.assertEqual(bash(["f"], functions={"f": []})[0].splitlines()[0], "f is a function")
        self.assertEqual(bash(["-t", "ls", "cd", "if", "f", "nosuchcmd"], functions={"f": []}),
                         ("file\nbuiltin\nkeyword\nfunction\n", 1))
        self.assertEqual(bash(["-p", "ls", "cd"]), ("/usr/bin/ls\n", 0))
        self.assertEqual(bash(["-P", "cd", "ls"]), ("/usr/bin/ls\n", 1))
        self.assertEqual(bash(["-a", "echo"]), ("echo is a shell builtin\necho is /usr/bin/echo\necho is /bin/echo\n", 0))
        self.assertEqual(bash(["-a", "ls"]), ("ls is /usr/bin/ls\nls is /bin/ls\n", 0))
        self.assertEqual(bash(["/bin/ls"]), ("/bin/ls is /bin/ls\n", 0))
        self.assertEqual(bash(["./nosuch"]), ("<err>-bash: type: ./nosuch: not found\n", 1))
        self.assertEqual(bash([]), ("", 0))
        self.assertEqual(bash(["-x"]), ("<err>-bash: type: -x: invalid option\n"
                                        "<err>type: usage: type [-afptP] name [name ...]\n", 2))
        # -f leaves the functions out, -- ends the options
        self.assertEqual(bash(["-f", "ls"], functions={"ls": []}), ("ls is /usr/bin/ls\n", 0))
        self.assertEqual(bash(["--", "ls"]), ("ls is /usr/bin/ls\n", 0))

    def test_type_answers_on_every_persona(self):
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render.render_cfg(persona, draw(persona, 0), Path("/tmp/r"), Path("/tmp/k")))
                skipped = cfg.get("shell", "skip_python_commands").split(",")
                self.assertNotIn("type", skipped)

    def test_fails_loudly_on_unknown_upstream(self):
        with tempfile.TemporaryDirectory() as handle:
            root = Path(handle) / "cowrie"
            shutil.copytree(FIXTURE, root)
            path = root / "src/cowrie/commands/base.py"
            path.write_text(path.read_text().replace('commands["history"] = Command_history\n',
                                                     'commands["history"] = Command_history  # history\n', 1))
            result = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], capture_output=True,
                                    text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Could not find expected text", result.stderr)


if __name__ == "__main__":
    unittest.main()


class LookupBoundTest(unittest.TestCase):
    """Final wave: type and which look through at most PATH_ENTRIES folders and LOOKUP_NAMES names per call and stop
    at the session's time; a name past them is not found."""

    setUpClass = classmethod(TypeTest.setUpClass.__func__)
    run_type = TypeTest.run_type

    PATH = ":".join(f"/opt/d{n}" for n in range(4000))
    NAMES = [f"name{n}" for n in range(4000)]

    def test_type_of_bash_and_ash_answers_4000_names_on_4000_folders_at_once(self):
        import time
        for bash in (True, False):
            with self.subTest(bash=bash):
                start = time.perf_counter()
                out, code = self.run_type(self.NAMES + ["ls"], path=self.PATH + ":/bin", bash=bash)
                self.assertLess(time.perf_counter() - start, 0.25)
                self.assertEqual(code, 1 if bash else 127)
                self.assertIn("name3999", out)
                # past the names of a call: not found, as a missing name
                self.assertIn("type: ls: not found\n" if bash else "ls: not found\n", out)
        # a folder past PATH_ENTRIES is not looked into
        out, code = self.run_type(["ls"], path=":".join(["/opt/x"] * 300 + ["/bin"]), bash=True)
        self.assertEqual((out, code), ("<err>-bash: type: ls: not found\n", 1))
        self.assertEqual(self.run_type(["ls"], path=":".join(["/opt/x"] * 255 + ["/bin"]), bash=True),
                         ("ls is /bin/ls\n", 0))

    def test_type_past_the_sessions_time_finds_nothing(self):
        import time
        from tests.test_patch import budget_names
        budget = budget_names()["TpotBudget"]()
        budget.charge(time.monotonic(), 10.0, 10.0)
        protocol = types.SimpleNamespace(tpot_budget=budget)

        def run(case, args):
            # the command of a session whose time is spent
            source = case.source
            names = {"CowrieConfig": FakeConfig({("shell", "not_found_style"): "bash"}), "commands": {}}

            class HoneyPotCommand:
                def __init__(self):
                    self.out, self.exit_code, self.protocol = [], 0, protocol

                def write(self, text):
                    self.out.append(text)

                def errorWrite(self, text):
                    self.out.append(f"<err>{text}")

            names["HoneyPotCommand"] = HoneyPotCommand
            module_parts(source, ["Command_tpot_type"], names)
            command = names["Command_tpot_type"]()
            command.args, command.environ = list(args), {"PATH": "/bin"}
            command.shell = types.SimpleNamespace(functions={}, error_prefix=lambda: "-bash: ")
            command.fs = Fs({"/bin/ls": "file"})
            command.call()
            return "".join(command.out), command.exit_code

        self.assertEqual(run(self, ["ls"]), ("<err>-bash: type: ls: not found\n", 1))

    def test_which_answers_4000_names_on_4000_folders_at_once(self):
        import time
        from tests import test_patch
        case = test_patch.PatchTest("test_which_a_prints_every_match")
        case.addCleanup = self.addCleanup
        _, which = case.command("which", "Command_which", {}, ["-a"] + self.NAMES + ["perl"])
        which.environ = {"PATH": self.PATH + ":/usr/bin"}
        which.fs, which.exit_code = test_patch.TreeFs(test_patch.base_tree()), 0
        start = time.perf_counter()
        which.call()
        self.assertLess(time.perf_counter() - start, 0.25)
        self.assertEqual((bytes(which.out), which.exit_code), (b"", 1))
        _, which = case.command("which", "Command_which", {}, ["perl"])
        which.environ = {"PATH": ":".join(["/opt/x"] * 255 + ["/usr/bin"])}
        which.fs, which.exit_code = test_patch.TreeFs(test_patch.base_tree()), 0
        which.call()
        self.assertEqual((bytes(which.out), which.exit_code), (b"/usr/bin/perl\n", 0))
