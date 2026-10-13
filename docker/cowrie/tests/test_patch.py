import ast
import configparser
import json
import random
import re
import shutil
import subprocess
import sys
import time
import tracemalloc
import unittest
import warnings
from pathlib import Path

from tests.helpers import DIST, base_tree, tmpdir
from tpot_personas import catalog, system, tree

FIXTURE = Path(__file__).parent / "fixtures" / "v3.1.1"
PATCH = DIST / "patch_cowrie_persona_support.py"


def method(source, cls, name, namespace=None):
    """The function `name` of class `cls` in source, compiled on its own (with the
    module globals of namespace)."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    namespace = dict(namespace or {})
                    exec(compile(ast.Module([item], []), "<patched>", "exec"), namespace)  # noqa: S102
                    return namespace[name]
    raise AssertionError(f"{cls}.{name} not found")


def module_parts(source, names, namespace):
    """The imports (not those of cowrie or twisted) and the top-level classes / functions
    `names` of source, run in namespace (stubs for what they need)."""
    keep = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in ("cowrie", "twisted"):
            continue
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] in ("cowrie", "twisted") for a in node.names):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            keep.append(node)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names:
            keep.append(node)
    found = {n.name for n in keep if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    if found != set(names):
        raise AssertionError(f"missing in patched source: {sorted(set(names) - found)}")
    exec(compile(ast.Module(keep, []), "<patched>", "exec"), namespace)  # noqa: S102
    return namespace


CPUINFO = b"".join(b"processor\t: %d\nmodel name\t: Intel(R) Xeon(R)\n\n" % n for n in range(4))
MEMINFO = b"MemTotal:        8023548 kB\nMemFree:         1200000 kB\n"


class FileNotFound(Exception):  # noqa: N818 - as Cowrie's fs.FileNotFound: no OSError
    pass


class FakeFs:
    def __init__(self, files, dirs=("/", "/proc", "/root")):
        self.files = files
        self.dirs = set(dirs)

    def resolve_path(self, path, cwd):
        import posixpath
        return posixpath.normpath(posixpath.join(cwd, path))

    def isdir(self, path):
        return path in self.dirs

    def file_contents(self, path):
        if path not in self.files:
            raise FileNotFound(path)
        return self.files[path]


class FakeCommand:
    """The parts of cowrie.shell.command.HoneyPotCommand that Command_grep uses."""

    pp = None

    def __init__(self, args, input_data=None, files=None, cwd="/root"):
        self.args = list(args)
        self.input_data = input_data
        self.fs = FakeFs({"/proc/cpuinfo": CPUINFO, "/proc/meminfo": MEMINFO} if files is None else files)
        self.cwd = cwd
        self.out = bytearray()
        self.err = ""
        self.code = "running"

        class Events:
            def dispatch(self, *args, **kwargs):
                pass

        class Protocol:
            events = Events()
            terminal = object()

        self.protocol = Protocol()

    def writeBytes(self, data):
        self.out += data

    def write(self, text):
        self.out += text.encode("utf-8")

    def errorWrite(self, text):
        # as HoneyPotCommand.errorWrite: the text goes out as UTF-8
        text.encode("utf8")
        self.err += text

    def exit(self, code=None):
        self.code = code

    def check_arguments(self, application, args):
        # verbatim logic of HoneyPotCommand.check_arguments in v3.1.1
        files = []
        for arg in args:
            path = self.fs.resolve_path(arg, self.cwd)
            if self.fs.isdir(path):
                self.errorWrite(f"{application}: error reading `{arg}': Is a directory\n")
                continue
            files.append(path)
        return files


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, section, option, fallback=None):
        return self.values.get((section, option), fallback)

    def has_option(self, section, option):
        return (section, option) in self.values



def module_all(source, namespace):
    """The whole patched module without its cowrie / twisted imports, run in namespace."""
    keep = [node for node in ast.parse(source).body
            if not (isinstance(node, ast.ImportFrom) and node.module
                    and node.module.split(".")[0] in ("cowrie", "twisted"))]
    exec(compile(ast.Module(keep, []), "<patched>", "exec"), namespace)  # noqa: S102
    return namespace


class TreeFs:
    """The parts of Cowrie's HoneyPotFilesystem getCommand uses, on a tpot_personas tree."""

    def __init__(self, root):
        self.fs = root

    def resolve_path(self, path, cwd):
        import posixpath
        return posixpath.normpath(posixpath.join(cwd, path))

    def getfile(self, path, follow_symlinks=True):
        import posixpath
        node = tree.find_node(self.fs, path)
        if follow_symlinks and node is not None and node[tree.A_TYPE] == tree.T_LINK:
            # a relative target from the link's directory, as the patched fs.getfile resolves it (the
            # unrendered test tree has its targets from the root)
            target = node[tree.A_TARGET]
            node = tree.find_node(self.fs, posixpath.normpath(posixpath.join(posixpath.dirname(path), target))) \
                or tree.find_node(self.fs, target)
        return node

    def exists(self, path):
        return self.getfile(path) is not None

    def isfile(self, path):
        node = self.getfile(path)
        return node is not None and node[tree.A_TYPE] == tree.T_FILE

    def mkfile(self, path, uid, gid, size, mode):
        self.made = getattr(self, "made", []) + [(path, uid, gid, mode)]
        return True


class FsModule:
    A_NAME, A_TYPE, A_CONTENTS, A_TARGET = tree.A_NAME, tree.A_TYPE, tree.A_CONTENTS, tree.A_TARGET
    T_LINK, T_DIR, T_FILE = tree.T_LINK, tree.T_DIR, tree.T_FILE


PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin".split(":")


class PatchTest(unittest.TestCase):
    def patched(self):
        root = tmpdir(self) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                       check=True, capture_output=True, text=True)
        return root

    def test_applies_to_v3_1_1(self):
        protocol = (self.patched() / "src/cowrie/shell/protocol.py").read_text()
        self.assertIn("skip_python_commands", protocol)
        self.assertIn("self.fs.resolve_path(cmd, cwd)", protocol)
        self.assertIn("for x in paths if x", protocol)
        self.assertNotIn("self.cwd", protocol)
        self.assertIn("if spelling in self.commands and not skip_python_command(spelling):", protocol)
        # the smoke test looks for upstream's txtcmds_path lookup: it stays, the spellings follow it
        self.assertIn("operator_path = Path(txtcmds_path) / relpath", protocol)

    def lookup(self, txtcmds, commands, skip="", shipped=None):
        """getCommand of the patched protocol.py on base_tree() (bin -> usr/bin) with these
        txtcmds files and Python commands; returns a function name -> what it found."""
        root = tmpdir(self)
        for rel, text in txtcmds.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        protocol = (self.patched() / "src/cowrie/shell/protocol.py").read_text()

        def read_data_bytes(*parts):
            # the txtcmds Cowrie ships (src/cowrie/data/txtcmds)
            if parts[0] == "txtcmds" and "/".join(parts[1:]) in (shipped or {}):
                return shipped["/".join(parts[1:])].encode()
            raise FileNotFoundError("/".join(parts))

        config = FakeConfig({("honeypot", "txtcmds_path"): str(root), ("shell", "skip_python_commands"): skip})
        self.config = config
        skip_set = module_parts(protocol, ["tpot_skip_python_commands"], {"CowrieConfig": config})
        get_command = method(protocol, "HoneyPotBaseProtocol", "getCommand",
                             {"CowrieConfig": config, "Path": Path, "fs": FsModule, "read_data_bytes": read_data_bytes,
                              "tpot_skip_python_commands": skip_set["tpot_skip_python_commands"]})

        class Protocol:
            fs = TreeFs(base_tree())

            def txtcmd(self, txt):
                return ("txtcmd", txt.decode())

            def scriptcmd(self, path, name):
                return ("script", path)

            class _log:
                @staticmethod
                def info(*args, **kwargs):
                    pass

        Protocol.commands = commands
        protocol_obj = Protocol()
        self.protocol_obj = protocol_obj
        return lambda name, cwd="/root": get_command(protocol_obj, name, PATH, cwd)

    def test_txtcmd_answers_every_spelling_through_the_bin_link(self):
        # `df` finds /usr/bin/df on PATH (usr/bin before bin), the txtcmd is bin/df
        find = self.lookup({"bin/df": "df of the persona\n", "usr/bin/top": "top of the persona\n"}, {})
        for name in ("df", "/bin/df", "/usr/bin/df"):
            self.assertEqual(find(name), ("txtcmd", "df of the persona\n"), name)
        for name in ("top", "/bin/top", "/usr/bin/top"):
            self.assertEqual(find(name), ("txtcmd", "top of the persona\n"), name)
        # a program without txtcmd or command stays Cowrie's script / binary
        self.assertEqual(find("uname"), ("script", "/usr/bin/uname"))
        self.assertIsNone(find("nothere"))

    def test_python_command_answers_before_the_txtcmds_cowrie_ships(self):
        # Cowrie ships data/txtcmds/bin/df and usr/bin/top; the patched df and top answer every spelling
        shipped = {"bin/df": "df of Cowrie\n", "usr/bin/top": "top of Cowrie\n"}
        commands = {"df": "Command_df", "/bin/df": "Command_df", "/usr/bin/df": "Command_df",
                    "top": "Command_top", "/usr/bin/top": "Command_top"}
        find = self.lookup({}, commands, shipped=shipped)
        for name in ("df", "/bin/df", "/usr/bin/df"):
            self.assertEqual(find(name), "Command_df", name)
        for name in ("top", "/bin/top", "/usr/bin/top"):
            self.assertEqual(find(name), "Command_top", name)
        # a txtcmd of the persona at the path answers before a Python command of another spelling,
        # Cowrie's own txtcmds after it
        find = self.lookup({"bin/df": "df of the persona\n"}, {"/usr/bin/df": "Command_df"}, shipped=shipped)
        self.assertEqual(find("/bin/df"), ("txtcmd", "df of the persona\n"))
        find = self.lookup({}, {}, shipped=shipped)
        self.assertEqual(find("/bin/df"), ("txtcmd", "df of Cowrie\n"))
        # a skipped Python command leaves the path to the shipped txtcmd
        find = self.lookup({}, commands, skip="df", shipped=shipped)
        self.assertEqual(find("/bin/df"), ("txtcmd", "df of Cowrie\n"))

    def test_awk_of_the_persona_follows_the_commands_of_awk_py(self):
        root = self.patched()
        source = (root / "src/cowrie/commands/awk.py").read_text()
        self.assertIn('commands["/bin/awk"] = Command_awk\ncommands["awk"] = Command_awk\n\n\n# T-Pot: awk', source)
        fs = module_all((root / "src/cowrie/commands/fs.py").read_text(),
                        {"HoneyPotCommand": FakeCommand, "fs": object(), "CowrieConfig": FakeConfig({})})
        config = FakeConfig({("shell", "awk_style"): "mawk", ("shell", "awk_version"): "1.3.4 20240123"})
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "TpotGrepMatcher": fs["TpotGrepMatcher"],
                                        "CowrieConfig": config})
        commands = namespace["commands"]
        for name in ("/bin/awk", "/usr/bin/awk", "awk"):
            self.assertIs(commands[name], namespace["Command_tpot_awk"], name)
        self.assertTrue(issubclass(namespace["Command_tpot_awk"], namespace["Command_awk"]))
        command = commands["awk"](["NR==2{print $2}"], input_data=b"total used\nMem: 7834 1203\n")
        command.start()
        self.assertEqual((bytes(command.out), command.exit_code), (b"7834\n", 0))

    def test_text_tools_follow_the_commands_of_wc_py(self):
        root = self.patched()
        source = (root / "src/cowrie/commands/wc.py").read_text()
        self.assertIn('commands["/usr/bin/wc"] = Command_wc\ncommands["/bin/wc"] = Command_wc\n'
                      'commands["wc"] = Command_wc\n\n\n# T-Pot: wc, sort and seq', source)
        config = FakeConfig({("shell", "text_style"): "gnu", ("shell", "text_version"): "9.4"})
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": config})
        commands = namespace["commands"]
        for name in ("wc", "sort", "seq"):
            for spelling in (name, f"/bin/{name}", f"/usr/bin/{name}"):
                self.assertIs(commands[spelling], namespace[f"Command_tpot_{name}"], spelling)
        command = commands["wc"](["-l"], input_data=b"a\nb\n")
        command.start()
        self.assertEqual((bytes(command.out), command.code), (b"2\n", None))
        self.assertEqual(command.exit_code, 0)

    def test_links_resolve_from_their_directory(self):
        from tpot_personas.identity import draw
        from tpot_personas.render import apply_to_tree
        persona = catalog.by_id("debian-bookworm")
        ident = draw(persona, 1)
        rendered = apply_to_tree(base_tree(), persona, ident, persona.builder(persona, ident))
        source = (self.patched() / "src/cowrie/shell/fs.py").read_text()
        getfile = method(source, "HoneyPotFilesystem", "getfile",
                         {"A_NAME": tree.A_NAME, "A_TYPE": tree.A_TYPE, "A_CONTENTS": tree.A_CONTENTS,
                          "A_TARGET": tree.A_TARGET, "T_LINK": tree.T_LINK, "posixpath": __import__("posixpath"),
                          "Node": list})

        class Fs:
            fs = rendered

        Fs.getfile = getfile
        files = Fs()
        # python3 -> python3.11 (relative, in /usr/bin), also through the /bin link; dev/stdout -> /proc/self/fd/1
        self.assertIs(files.getfile("/usr/bin/python3"), tree.find_node(rendered, "usr/bin/python3.11"))
        self.assertIs(files.getfile("/bin/python3"), tree.find_node(rendered, "usr/bin/python3.11"))
        self.assertIs(files.getfile("/dev/stdout"), tree.find_node(rendered, "dev/pts/0"))
        self.assertIs(files.getfile("/proc/self/exe"), tree.find_node(rendered, "usr/bin/bash"))
        self.assertIsNone(files.getfile("/usr/bin/nothere"))
        source = (self.patched() / "src/cowrie/shell/protocol.py").read_text()
        self.assertIn("posixpath.normpath", source)

    def test_python_command_answers_every_spelling(self):
        # a command found by path is looked up as /usr/bin/x and /bin/x (Cowrie registers /bin/busybox)
        find = self.lookup({}, {"/bin/top": "top"})
        self.assertEqual(find("/usr/bin/top"), "top")
        self.assertEqual(find("top"), "top")

    def test_skip_list_is_parsed_once(self):
        find = self.lookup({}, {"/bin/top": "top", "top": "top"}, skip="top,gcc")
        asked = []
        get = self.config.get
        self.config.get = lambda section, option, fallback=None: (asked.append(option), get(section, option, fallback))[1]
        for _ in range(3):
            self.assertEqual(find("top"), ("script", "/usr/bin/top"))
            self.assertIsNone(find("nothere"))
        self.assertLessEqual(asked.count("skip_python_commands"), 1)

    def test_a_program_the_session_installed_answers_although_skipped(self):
        # apt / dnf install of a package whose program is on the skip list (git where it is not installed)
        find = self.lookup({}, {"/bin/top": "top", "top": "top", "git": "git"}, skip="top,git")
        self.assertEqual(find("top"), ("script", "/usr/bin/top"))
        self.protocol_obj.tpot_installed = {"top": "4.0.0"}
        self.assertEqual(find("top"), "top")
        self.assertEqual(find("/bin/top"), "top")
        self.assertEqual(find("/usr/bin/top"), "top")
        self.assertIsNone(find("git"))

    def test_a_faked_program_of_the_session_answers_for_a_skipped_command(self):
        # also before a txtcmd of the persona for a program it does not have (lspci of its hardware)
        find = self.lookup({"usr/bin/top": "top of the persona\n"}, {"/bin/top": "top", "top": "top"}, skip="top")
        self.assertEqual(find("top"), ("txtcmd", "top of the persona\n"))
        self.protocol_obj.tpot_faked = {"/usr/bin/top": "faked"}
        self.assertEqual(find("top"), "faked")
        self.assertEqual(find("/bin/top"), "faked")
        self.assertEqual(find("/usr/bin/top"), "faked")

    def test_skipped_python_command_stays_skipped_in_every_spelling(self):
        find = self.lookup({}, {"/bin/top": "top", "top": "top"}, skip="top")
        self.assertEqual(find("top"), ("script", "/usr/bin/top"))
        self.assertEqual(find("/bin/top"), ("script", "/bin/top"))

    def busybox(self, args, help_path=None):
        source = (self.patched() / "src/cowrie/commands/busybox.py").read_text()
        values = {("shell", "busybox_help"): str(help_path)} if help_path else {}
        dispatched, executed = [], []

        class Pipe:
            targets = ()

            def __init__(self, *args, **kwargs):
                self.stdin_from_pipe = False

        class BusyboxBase(FakeCommand):
            environ = {"PATH": ":".join(PATH)}
            user = None
            pp = Pipe()

            def exec_command(self, pp, cmdclass, *args):
                executed.append((cmdclass, args))

        namespace = module_all(source, {"HoneyPotCommand": BusyboxBase, "PipeProtocol": Pipe,
                                        "CowrieConfig": FakeConfig(values)})
        command = namespace["Command_busybox"](args)

        class Events:
            def dispatch(self, *args, **kwargs):
                dispatched.append(kwargs)

        class Protocol:
            events = Events()

            def getCommand(self, cmd, paths, cwd):
                return {"wget": "Command_wget", "cat": "Command_cat"}.get(cmd)

        command.protocol = Protocol()
        command.start()
        return command, dispatched, executed

    def test_busybox_runs_its_applets(self):
        command, dispatched, executed = self.busybox(["wget", "http://127.0.0.1:1/x"])
        self.assertEqual(executed, [("Command_wget", ("http://127.0.0.1:1/x",))])
        self.assertEqual(dispatched, [{"input": "wget http://127.0.0.1:1/x"}])

    def test_busybox_names_an_unknown_applet_on_stderr_with_127(self):
        command, _, executed = self.busybox(["ECCHI"])
        self.assertEqual((bytes(command.out), command.err, command.code, executed),
                         (b"", "ECCHI: applet not found\n", 127, []))

    def test_busybox_prints_the_help_of_the_persona(self):
        help_path = tmpdir(self) / "busybox-help.txt"
        help_path.write_text("BusyBox v1.19.2 (2025-03-28 14:13:40 CST) multi-call binary.\nhelp\n")
        for args in ([], ["--help"]):
            command, _, executed = self.busybox(args, help_path)
            self.assertEqual(bytes(command.out), help_path.read_bytes(), args)
            self.assertEqual((command.err, executed), ("", []))

    def test_busybox_without_the_key_keeps_upstream_help(self):
        command, _, _ = self.busybox([])
        self.assertIn("BusyBox v1.20.2 (Debian 1:1.20.0-7) multi-call binary.", command.err)

    def command(self, module, cls, values, args=()):
        source = (self.patched() / f"src/cowrie/commands/{module}.py").read_text()
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(values)})
        return namespace, namespace[cls](list(args))

    def test_perl_v_names_the_persona_perl(self):
        values = {("shell", "perl_version"): "5.36.0", ("shell", "perl_archname"): "x86_64-linux-gnu-thread-multi",
                  ("shell", "perl_copyright"): "2022"}
        _, perl = self.command("perl", "Command_perl", values)
        perl.version()
        text = bytes(perl.out).decode()
        self.assertIn("This is perl 5, version 36, subversion 0 (v5.36.0) built for x86_64-linux-gnu-thread-multi\n", text)
        self.assertIn("Copyright 1987-2022, Larry Wall\n", text)
        self.assertNotIn("5.14.2", text)
        _, perl = self.command("perl", "Command_perl", {})
        perl.version()
        self.assertIn("(v5.14.2) built for x86_64-linux-thread-multi", bytes(perl.out).decode())

    def test_python3_is_a_command_with_the_persona_version(self):
        namespace, python = self.command("python", "Command_python", {("shell", "python_version"): "3.12.3"})
        self.assertIs(namespace["commands"]["python3"], namespace["Command_python"])
        self.assertIs(namespace["commands"]["/usr/bin/python3"], namespace["Command_python"])
        python.version()
        self.assertEqual((bytes(python.out), python.err), (b"Python 3.12.3\n", ""))
        _, python = self.command("python", "Command_python", {("shell", "python_version"): "2.7.5"})
        python.version()
        # Python 2 prints its version on stderr
        self.assertEqual((bytes(python.out), python.err), (b"", "Python 2.7.5\n"))

    def python(self, args, version="3.12.3", quick=False):
        """Command_python started with args on a persona with this Python: (stdout, stderr, exit code).
        quick: start() must return within half a second."""
        _, python = self.command("python", "Command_python", {("shell", "python_version"): version}, args)
        self.start(python, quick)
        return bytes(python.out).decode(), python.err, python.code

    def start(self, command, quick):
        """command.start(); self.peak: the most memory it allocated at once, in bytes."""
        begin = time.monotonic()
        tracemalloc.start()
        try:
            command.start()
            self.peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        if quick:
            self.assertLess(time.monotonic() - begin, 0.5, command.args[-1][:20])

    def test_python_c_prints_a_literal_and_returns(self):
        self.assertEqual(self.python(["-c", "print(1)"]), ("1\n", "", 0))
        self.assertEqual(self.python(["-c", "print(1)"], "3.9.18"), ("1\n", "", 0))
        self.assertEqual(self.python(["-c", "print('a', 2.5, None, sep='-', end='')"]), ("a-2.5-None", "", 0))
        self.assertEqual(self.python(["-c", "import sys\nx = 'hi'\nprint(x)\nprint('e', file=sys.stderr)"]),
                         ("hi\n", "e\n", 0))
        self.assertEqual(self.python(["-c", "sys.stdout.write('w')"]), ("w", "", 0))

    def test_python_c_ends_the_option_list(self):
        # the words after the code are sys.argv, no options and no files
        self.assertEqual(self.python(["-c", "print(1)", "-x", "nofile"]), ("1\n", "", 0))
        self.assertEqual(self.python(["-u", "-c", "print(2)"]), ("2\n", "", 0))
        self.assertEqual(self.python(["-uc", "print(3)"]), ("3\n", "", 0))
        self.assertEqual(self.python(["-cprint(4)"]), ("4\n", "", 0))
        self.assertEqual(self.python(["-W", "ignore", "-c", "print(5)"]), ("5\n", "", 0))

    def test_python_c_other_code_prints_nothing(self):
        self.assertEqual(self.python(["-c", 'import os, platform; platform.system(); os.listdir("/")']), ("", "", 0))
        self.assertEqual(self.python(["-c", "import os; print(os.getcwd()); print(open('/etc/passwd').read())"]),
                         ("", "", 0))

    def test_python_c_code_is_parsed_and_never_run(self):
        marker = tmpdir(self) / "ran"
        code = f"open({str(marker)!r}, 'w').write('x'); __import__('os').system('touch {marker}')"
        self.assertEqual(self.python(["-c", code]), ("", "", 0))
        self.assertFalse(marker.exists())
        for module in ("python", "perl"):
            source = (self.patched() / f"src/cowrie/commands/{module}.py").read_text()
            # ast.literal_eval and re.compile only
            self.assertIsNone(re.search(r"(?<![\w.])(eval|exec|compile)\(", source), module)
            for word in ("subprocess", "os.system", "__import__", "importlib"):
                self.assertNotIn(word, source, (module, word))

    def test_python_c_exit_status(self):
        self.assertEqual(self.python(["-c", "import sys; print(1); sys.exit(3); print(2)"]), ("1\n", "", 3))
        self.assertEqual(self.python(["-c", "import sys; sys.exit('bye')"]), ("", "bye\n", 1))
        self.assertEqual(self.python(["-c", "exit()"]), ("", "", 0))
        self.assertEqual(self.python(["-c", ""]), ("", "", 0))

    def test_python_c_syntax_error(self):
        out, err, code = self.python(["-c", "print("])
        self.assertEqual((out, code), ("", 1))
        self.assertEqual(err, '  File "<string>", line 1\n    print(\n         ^\nSyntaxError: \'(\' was never closed\n')
        # the messages and caret of Python 3.9 (RHEL 9) and 2.7 (CentOS 7)
        self.assertEqual(self.python(["-c", "print("], "3.9.18")[1:],
                         ('  File "<string>", line 1\n    print(\n          ^\nSyntaxError: unexpected EOF while parsing\n', 1))
        self.assertEqual(self.python(["-c", 'print("a'], "3.9.18")[1:],
                         ('  File "<string>", line 1\n    print("a\n            ^\nSyntaxError: EOL while scanning string literal\n', 1))
        self.assertEqual(self.python(["-c", "x = = 1"], "3.9.18")[1:],
                         ('  File "<string>", line 1\n    x = = 1\n        ^\nSyntaxError: invalid syntax\n', 1))
        self.assertEqual(self.python(["-c", "if 1:"], "3.9.18")[1:],
                         ('  File "<string>", line 1\n    if 1:\n         ^\nIndentationError: expected an indented block\n', 1))
        self.assertEqual(self.python(["-c", "print 1"], "3.9.18")[1:],
                         ('  File "<string>", line 1\n    print 1\n          ^\n'
                          "SyntaxError: Missing parentheses in call to 'print'. Did you mean print(1)?\n", 1))
        self.assertEqual(self.python(["-c", "print("], "2.7.5")[1:],
                         ('  File "<string>", line 1\n    print(\n          ^\nSyntaxError: unexpected EOF while parsing\n', 1))

    def test_python_c_output_that_utf_8_cannot_carry(self):
        # stdout of CPython 3 is strict UTF-8: a lone surrogate ends the code with a traceback; stderr writes it
        # as an escape (backslashreplace), Python 2 prints the escape of its byte string
        def traceback(line, what="character '\\udcff' in position 0"):
            return ('Traceback (most recent call last):\n  File "<string>", line %d, in <module>\n'
                    "UnicodeEncodeError: 'utf-8' codec can't encode %s: surrogates not allowed\n" % (line, what))

        self.assertEqual(self.python(["-c", 'print("\\udcff"); print(2)']), ("", traceback(1), 1))
        self.assertEqual(self.python(["-c", 'print(1)\nprint("a", "\\udcff")\nprint(3)']), ("1\na ", traceback(2), 1))
        self.assertEqual(self.python(["-c", 'import sys; sys.stdout.write("ab\\udcff\\udcfe")'], "3.9.18"),
                         ("", traceback(1, "characters in position 2-3"), 1))
        self.assertEqual(self.python(["-c", 'import sys; print("\\udcff", file=sys.stderr); sys.stderr.write("x\\udcff\\n")']),
                         ("", "\\udcff\nx\\udcff\n", 0))
        self.assertEqual(self.python(["-c", 'import sys; sys.exit("\\udcff")']), ("", "\\udcff\n", 1))
        self.assertEqual(self.python(["-c", 'print "\\udcff"'], "2.7.5"), ("\\udcff\n", "", 0))
        # Python 3.13 shows the code of the call
        self.assertEqual(self.python(["-c", 'x=1;  print("\\u00e9", "\\udcff")  ;y=2'], "3.13.5"),
                         ("\u00e9 ", 'Traceback (most recent call last):\n  File "<string>", line 1, in <module>\n'
                          '    x=1;  print("\\u00e9", "\\udcff")  ;y=2\n          ~~~~~^^^^^^^^^^^^^^^^^^^^\n'
                          "UnicodeEncodeError: 'utf-8' codec can't encode character '\\udcff' in position 0: "
                          "surrogates not allowed\n", 1))
        self.assertEqual(self.python(["-c", 'print("a",\n"\\udcff")'], "3.13.5")[1].splitlines()[2:6],
                         ['    print("a",', "    ~~~~~^^^^^", '    "\\udcff")', "    ^^^^^^^^^"])

    def test_python_c_writes_complete_lines_before_its_stderr(self):
        # stdout of a terminal is line buffered: the last line without newline comes at the exit
        _, python = self.command("python", "Command_python", {("shell", "python_version"): "3.12.3"},
                                 ["-c", 'import sys; print("a"); print("b", end=""); sys.exit("bye")'])
        stream = []
        python.writeBytes = lambda data: stream.append(("out", data))
        python.errorWrite = lambda text: stream.append(("err", text))
        python.start()
        self.assertEqual(stream, [("out", b"a\n"), ("err", "bye\n"), ("out", b"b")])
        self.assertEqual(python.code, 1)

    def test_python_and_perl_exit_when_the_output_fails(self):
        for module, cls, args in (("python", "Command_python", ["-c", "print(1)"]),
                                  ("perl", "Command_perl", ["-e", "print 1"])):
            with self.subTest(module):
                _, command = self.command(module, cls, {}, args)

                def fail(*args):
                    raise UnicodeEncodeError("utf-8", "\udcff", 0, 1, "surrogates not allowed")

                command.write = command.writeBytes = command.errorWrite = fail
                command.start()
                self.assertEqual(command.code, 0)

    def test_python_2_print_statement(self):
        self.assertEqual(self.python(["-c", 'print "x"; print 1'], "2.7.5"), ("x\n1\n", "", 0))
        self.assertEqual(self.python(["-c", "print(1)"], "2.7.5"), ("1\n", "", 0))
        # Python 2 syntax the parser does not know stays silent
        self.assertEqual(self.python(["-c", "try:\n  pass\nexcept Exception, e:\n  pass"], "2.7.5"), ("", "", 0))

    def test_python_c_long_whitespace_is_read_in_bounded_time(self):
        spaces = " " * 60000
        self.assertEqual(self.python(["-c", "print(1)" + spaces], quick=True), ("1\n", "", 0))
        self.assertEqual(self.python(["-c", "print 1;" + " \t" * 30000 + "print 2"], "2.7.5", quick=True), ("1\n2\n", "", 0))
        self.assertEqual(self.python(["-c", "print" + spaces + "x"], "2.7.5", quick=True)[2], 0)

    def test_python_c_deep_or_large_values_are_silent(self):
        self.assertEqual(self.python(["-c", "a" + ".b" * 1000 + "()"]), ("", "", 0))
        self.assertEqual(self.python(["-c", "print(a" + ".b" * 1000 + ")"]), ("", "", 0))
        self.assertEqual(self.python(["-c", "import sys; sys.exit((0x" + "f" * 20000 + ",))"]), ("", "", 0))

    def test_python_c_separator_counts_against_the_output_limit(self):
        code = "print(" + "1," * 15000 + "sep='" + "a" * 32000 + "')"
        self.assertEqual(self.python(["-c", code]), ("", "", 0))
        # the syntax tree of 15000 arguments takes about 15 MB; separator times arguments would be 480 MB
        self.assertLess(self.peak, 32 << 20)

    def test_python_2_print_tuple_counts_against_the_output_limit(self):
        code = "a = '" + "a" * 6000 + "'; print(" + "a," * 19000 + ")"
        self.assertEqual(self.python(["-c", code], "2.7.5"), ("", "", 0))
        # the syntax tree of 19000 arguments takes about 19 MB; the whole tuple text would be 140 MB
        self.assertLess(self.peak, 32 << 20)

    def test_python_2_print_call_prints_a_tuple(self):
        self.assertEqual(self.python(["-c", 'print("a", "b")'], "2.7.5"), ("('a', 'b')\n", "", 0))
        self.assertEqual(self.python(["-c", 'print "a", "b"'], "2.7.5"), ("a b\n", "", 0))
        self.assertEqual(self.python(["-c", 'from __future__ import print_function; print("a", "b")'], "2.7.5"),
                         ("a b\n", "", 0))
        self.assertEqual(self.python(["-c", 'print("a", "b")']), ("a b\n", "", 0))

    def test_python_m_returns_at_once(self):
        self.assertEqual(self.python(["-m", "http.server"]), ("", "", 0))
        self.assertEqual(self.python(["-m", "http.server", "8000"]), ("", "", 0))
        self.assertEqual(self.python(["-mpip", "install", "x"]), ("", "", 0))

    def test_python_c_or_m_without_argument(self):
        out, err, code = self.python(["-c"])
        self.assertEqual((out, code), ("", 2))
        self.assertEqual(err, "Argument expected for the -c option\nusage: python3 [option] ... [-c cmd | -m mod | file | -] "
                         "[arg] ...\nTry `python -h' for more information.\n")
        self.assertTrue(self.python(["-m"])[1].startswith("Argument expected for the -m option\n"))

    def test_python_version_and_help_stay_upstream(self):
        self.assertEqual(self.python(["-V"]), ("Python 3.12.3\n", "", None))
        self.assertEqual(self.python(["--version"]), ("Python 3.12.3\n", "", None))
        self.assertTrue(self.python(["-h"])[0].startswith("usage: python [option]"))

    def perl(self, args, quick=False):
        """Command_perl started with args: (stdout bytes, stderr, exit code)."""
        values = {("shell", "perl_version"): "5.36.0", ("shell", "perl_archname"): "x86_64-linux-gnu-thread-multi",
                  ("shell", "perl_copyright"): "2022"}
        _, perl = self.command("perl", "Command_perl", values, args)
        self.start(perl, quick)
        return bytes(perl.out), perl.err, perl.code

    def test_perl_e_prints_and_returns(self):
        self.assertEqual(self.perl(["-e", 'print "x\\n"']), (b"x\n", "", 0))
        self.assertEqual(self.perl(["-e", "print 'a\\n', 1, \"\\n\""]), (b"a\\n1\n", "", 0))
        self.assertEqual(self.perl(["-le", "print 1"]), (b"1\n", "", 0))
        self.assertEqual(self.perl(["-l", "-e", "print"]), (b"\n", "", 0))
        self.assertEqual(self.perl(["-e", 'print("a", "b\\n")']), (b"ab\n", "", 0))
        self.assertEqual(self.perl(["-e", 'print STDERR "e\\n"; print STDOUT "o"']), (b"o", "e\n", 0))
        self.assertEqual(self.perl(["-e", 'print 0, " ", 1.50, " ", -7']), (b"0 1.5 -7", "", 0))
        self.assertEqual(self.perl(["-e", 'my $x = "v"; print "$x ${x} $0 \\$x\\x41\\n"']), (b"v v -e $xA\n", "", 0))
        self.assertEqual(self.perl(["-e", 'print "\\xe4 \u00e4"']), (b"\xe4 \xc3\xa4", "", 0))
        self.assertEqual(self.perl(["-E", 'say "s"']), (b"s\n", "", 0))
        self.assertEqual(self.perl(["-e", "print 1;", "-e", "print 2"]), (b"12", "", 0))
        self.assertEqual(self.perl(["-e", "use strict; print 1"]), (b"1", "", 0))

    def test_perl_e_other_code_prints_nothing(self):
        code = 'use POSIX; my @u = POSIX::uname(); $h = $u[1]; if (-d "/tmp") { chdir("/tmp"); }; print "$h\\n"'
        self.assertEqual(self.perl(["-e", code]), (b"", "", 0))
        self.assertEqual(self.perl(["-e", 'system("id")']), (b"", "", 0))
        # scalars not set to a literal, arrays, expressions and other number forms are not printed
        self.assertEqual(self.perl(["-e", '$x = `id`; print "$x\\n"']), (b"", "", 0))
        self.assertEqual(self.perl(["-e", 'print "$y a@b.com $x[0]"']), (b"", "", 0))
        self.assertEqual(self.perl(["-e", 'print "a" . "b"; print "c" x 3; print 010, 0x1F']), (b"", "", 0))
        # from a regex on, the code is not read
        self.assertEqual(self.perl(["-e", "print 1; s/'//; print 2"]), (b"1", "", 0))
        self.assertEqual(self.perl(["-ne", "print"]), (b"", "", 0))
        self.assertEqual(self.perl(["-e", 'say "s"']), (b"", "", 0))

    def test_perl_e_long_whitespace_is_read_in_bounded_time(self):
        spaces = " " * 60000
        for code in ("exit" + spaces + "x", "print 1" + spaces + "x", "$a =" + spaces + "x", "q" + spaces + "x",
                     "die" + spaces + "x", "my" + spaces + "$a" + spaces[:4000] + "x"):
            self.assertEqual(self.perl(["-e", code], quick=True)[0], b"", code[:8])
        self.assertEqual(self.perl(["-e", "exit ( 3 )"]), (b"", "", 3))
        self.assertEqual(self.perl(["-e", " print ( 1 , 2 ) ; "]), (b"12", "", 0))

    def test_perl_e_stored_scalars_are_limited(self):
        code = '$b = "' + "a" * 60000 + '";' + "".join(f'$c{i} = "$b";' for i in range(350)) + 'print "x"'
        self.assertLess(len(code), 65536)
        self.assertEqual(self.perl(["-e", code]), (b"x", "", 0))
        self.assertLess(self.peak, 4 << 20)

    def test_perl_e_output_is_limited(self):
        code = '$a = "' + "a" * 60000 + '"; $b = "' + "$a" * 20 + '"; print "x"; print ' + ",".join(["$a"] * 20)
        out, err, status = self.perl(["-e", code])
        self.assertEqual((out, err, status), (b"x", "", 0))

    def test_perl_e_exit_and_die(self):
        self.assertEqual(self.perl(["-e", "print 1; exit 3; print 2"]), (b"1", "", 3))
        self.assertEqual(self.perl(["-e", "exit(300)"]), (b"", "", 44))
        self.assertEqual(self.perl(["-e", "exit"]), (b"", "", 0))
        self.assertEqual(self.perl(["-e", 'die "oops"']), (b"", "oops at -e line 1.\n", 255))
        self.assertEqual(self.perl(["-e", 'die "oops\\n"']), (b"", "oops\n", 255))
        self.assertEqual(self.perl(["-e", "print 1;\ndie"]), (b"1", "Died at -e line 2.\n", 255))

    def test_perl_e_syntax_errors(self):
        self.assertEqual(self.perl(["-e", 'print "x']),
                         (b"", "Can't find string terminator '\"' anywhere before EOF at -e line 1.\n", 255))
        self.assertEqual(self.perl(["-e", 'print 1; print("x"']),
                         (b"", "syntax error at -e line 1, at EOF\nExecution of -e aborted due to compilation errors.\n", 255))
        self.assertEqual(self.perl(["-e", "print 1)"]),
                         (b"", 'syntax error at -e line 1, near ")"\nExecution of -e aborted due to compilation errors.\n', 255))
        self.assertEqual(self.perl(["-e"]), (b"", "No code specified for -e.\n", 29))

    def test_perl_numbers_are_ascii_digits(self):
        self.assertEqual(self.perl(["-e", "print 12, 3.5"]), (b"123.5", "", 0))
        for code in ("print \u0661\u0662", "print 1.\u0662", "print \uff11"):
            with self.subTest(code):
                out, _, _ = self.perl(["-e", code])
                self.assertNotIn("\u0661".encode(), out)
                self.assertNotIn("\uff11".encode(), out)
                self.assertNotIn(b"1", out)

    def test_perl_c_checks_the_syntax_only(self):
        self.assertEqual(self.perl(["-c", "-e", "print 1"]), (b"", "-e syntax OK\n", 0))
        self.assertEqual(self.perl(["-ce", 'print "x']),
                         (b"", "Can't find string terminator '\"' anywhere before EOF at -e line 1.\n", 255))
        self.assertEqual(self.perl(["-ce", "print(1"]),
                         (b"", "syntax error at -e line 1, at EOF\n-e had compilation errors.\n", 255))

    def test_perl_version_and_help_stay_upstream(self):
        out, err, code = self.perl(["-v"])
        self.assertIn(b"(v5.36.0) built for x86_64-linux-gnu-thread-multi", out)
        self.assertEqual(code, None)
        self.assertIn(b"Usage: perl [switches]", self.perl(["-h"])[0])

    def test_git_version_of_the_persona(self):
        _, git = self.command("git", "Command_git", {("shell", "git_version"): "2.43.0"}, ["--version"])
        git.call()
        self.assertEqual(bytes(git.out), b"git version 2.43.0\n")

    def test_git_version_of_a_git_the_session_installed(self):
        _, git = self.command("git", "Command_git", {}, ["--version"])
        git.protocol.tpot_installed = {"git": "2.39.5"}
        git.call()
        self.assertEqual(bytes(git.out), b"git version 2.39.5\n")

    def twisted_command(self, module, cls, values, args, uid=0, commands=None, present=(), tty=True, sleeps=None):
        """A command with inlineCallbacks: the generators run through at once, sleep returns."""
        def inline_callbacks(function):
            def run(*a, **k):
                result = function(*a, **k)
                if hasattr(result, "send"):
                    for _ in result:
                        pass
            return run

        source = (self.patched() / f"src/cowrie/commands/{module}.py").read_text()
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(values),
                                        "inlineCallbacks": inline_callbacks, "defer": None, "reactor": None,
                                        "Logger": lambda: type("L", (), {"info": lambda *a, **k: None})()})
        command = namespace[cls](list(args))
        command.user = {"uid": uid, "gid": uid}
        command.sleep = lambda *a, **k: (sleeps.append(a) if sleeps is not None else None)
        # stdout is the terminal of an interactive session (tty) or a pipe
        command.pp = type("Pipe", (), {"targets": {1: ("terminal" if tty else "pipe",)}})()

        class Fs:
            made = []
            present = set()

            def mkfile(self, path, *a, **k):
                self.made.append(path)
                return True

            def exists(self, path):
                return path in self.present

        command.fs = Fs()
        command.fs.present = set(present)
        command.protocol.commands = dict(commands or {})
        command.start()
        return namespace, command

    APT = {("shell", "apt_version"): "2.8.3", ("shell", "deb_arch"): "amd64",
           ("shell", "apt_sources"): "http://archive.ubuntu.com/ubuntu noble,http://archive.ubuntu.com/ubuntu "
                                     "noble-updates,http://security.ubuntu.com/ubuntu noble-security"}

    def test_apt_version_of_the_persona(self):
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["-v"])
        self.assertTrue(bytes(apt.out).startswith(b"apt 2.8.3 (amd64)\nSupported modules:"), bytes(apt.out)[:60])
        _, apt = self.twisted_command("apt", "Command_aptget", {}, ["-v"])
        self.assertTrue(bytes(apt.out).startswith(b"apt 1.0.9.8.1 for amd64 compiled on Jun 10 2015"))

    def test_apt_update_as_root_fetches_the_persona_sources(self):
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["update"])
        self.assertEqual(bytes(apt.out).decode(), "Hit:1 http://archive.ubuntu.com/ubuntu noble InRelease\n"
                         "Hit:2 http://archive.ubuntu.com/ubuntu noble-updates InRelease\n"
                         "Hit:3 http://security.ubuntu.com/ubuntu noble-security InRelease\n"
                         "Reading package lists... Done\n")
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["update"], uid=1000)
        self.assertIn("Could not open lock file /var/lib/apt/lists/lock", apt.err)

    def test_apt_install_takes_no_option_as_a_package(self):
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["install", "-y", "nmap"])
        text = bytes(apt.out).decode()
        self.assertIn("The following NEW packages will be installed:\n  nmap \n", text)
        self.assertIn("Get:1 http://archive.ubuntu.com/ubuntu noble/main amd64 nmap amd64 ", text)
        self.assertRegex(text, r"Preparing to unpack \.\.\./nmap_[^ ]+_amd64\.deb \.\.\.\n")
        self.assertNotIn("i386", text)
        self.assertNotIn("ftp.debian.org", text)
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["-y", "install", "wget"])
        self.assertIn("  wget \n", bytes(apt.out).decode())

    def test_apt_install_takes_package_names_only(self):
        # apt: a name of Debian's form (lower case, digits, + - .) or a .deb file by its path
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["install", "-y", "..", ".", "./x.deb",
                                                                          "Wget", "-x-", "../../tmp/a", "nmap"])
        self.assertEqual(bytes(apt.out).decode(), "Reading package lists... Done\nBuilding dependency tree... Done\n"
                         "Reading state information... Done\n")
        self.assertEqual(apt.err, "E: Unsupported file .. given on commandline\nE: Unsupported file . given on commandline\n"
                         "E: Unsupported file ./x.deb given on commandline\nE: Unable to locate package Wget\n"
                         "E: Unsupported file ../../tmp/a given on commandline\n")
        self.assertEqual((apt.code, apt.fs.made, apt.protocol.commands), (100, [], {}))
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["install", "-y", "g++", "libc6:amd64"])
        self.assertIn("  g++ libc6 \n", bytes(apt.out).decode())
        self.assertEqual((apt.err, apt.fs.made), ("", ["/usr/bin/g++", "/usr/bin/libc6"]))

    def test_apt_install_marks_the_programs_for_the_session(self):
        values = self.apt_with_packages({}, "debian", available={"git": "1:2.39.5-0+deb12u2", "net-tools": "2.10-0.1",
                                                                 "nmap": "7.93+dfsg1-1"},
                                        programs={"net-tools": ["/usr/bin/netstat", "/usr/sbin/ifconfig"],
                                                  "git": ["/usr/bin/git"], "nmap": ["/usr/bin/nmap"]},
                                        answers=["git", "ifconfig", "netstat"])
        cowrie = {"git": "Command_git", "/bin/git": "Command_git", "ifconfig": "Command_ifconfig",
                  "netstat": "Command_netstat"}
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "git", "net-tools", "nmap"],
                                      commands=cowrie)
        self.assertEqual(apt.code, None)
        self.assertEqual(apt.protocol.tpot_installed, {"git": "2.39.5", "netstat": "2.10", "ifconfig": "2.10"})
        self.assertEqual(apt.fs.made, ["/usr/bin/git", "/usr/bin/netstat", "/usr/sbin/ifconfig", "/usr/bin/nmap"])
        # Cowrie's own commands answer for theirs, a program Cowrie does not have is the faked one
        self.assertEqual(set(apt.protocol.commands), set(cowrie) | {"/usr/bin/nmap"})

    def test_apt_install_marks_only_the_programs_of_its_data(self):
        # a package apt has no data for is installed as before, its program stays skipped: Cowrie's dnf,
        # rpm and lspci do not answer on a Debian system
        # nor one whose Cowrie command shows another version than the package (gcc 4.7.2 of Cowrie)
        values = self.apt_with_packages({}, "debian", available={"gcc": "4:12.2.0-3", "net-tools": "2.10-0.1"},
                                        programs={"gcc": ["/usr/bin/gcc"], "net-tools": ["/usr/sbin/ifconfig"]},
                                        answers=["ifconfig"])
        cowrie = {"dnf": "Command_dnf", "/usr/bin/dnf": "Command_dnf", "rpm": "Command_rpm", "lspci": "Command_lspci",
                  "gcc": "Command_gcc", "ifconfig": "Command_ifconfig"}
        _, apt = self.twisted_command("apt", "Command_aptget", values,
                                      ["install", "-y", "dnf", "rpm", "lspci", "gcc", "net-tools"], commands=cowrie)
        self.assertEqual(apt.code, None)
        self.assertEqual(apt.protocol.tpot_installed, {"ifconfig": "2.10"})
        self.assertEqual(apt.protocol.commands, cowrie)
        self.assertEqual(sorted(apt.protocol.tpot_faked), ["/usr/bin/dnf", "/usr/bin/gcc", "/usr/bin/lspci",
                                                           "/usr/bin/rpm"])
        self.assertEqual(apt.fs.made, ["/usr/bin/dnf", "/usr/bin/rpm", "/usr/bin/lspci", "/usr/bin/gcc",
                                       "/usr/sbin/ifconfig"])

    def test_apt_names_a_package_the_release_does_not_have(self):
        values = self.apt_with_packages({}, "debian", unavailable=["yum"])
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "yum", "nmap"])
        self.assertEqual(bytes(apt.out).decode(), "Reading package lists... Done\nBuilding dependency tree... Done\n"
                         "Reading state information... Done\n"
                         "Package yum is not available, but is referred to by another package.\n"
                         "This may mean that the package is missing, has been obsoleted, or\n"
                         "is only available from another source\n\n")
        self.assertEqual((apt.err, apt.code, apt.fs.made), ("E: Package 'yum' has no installation candidate\n", 100, []))

    def test_apt_takes_the_values_of_its_options(self):
        for args in (["-o", "Dpkg::Options::=--force-confold"], ["--option", "Dpkg::Options::=--force-confold"],
                     ["-oAPT::Install-Recommends=0"], ["--option=APT::Install-Recommends=0"],
                     ["-c", "/etc/apt/apt.conf"], ["-t", "bookworm-backports"],
                     ["--target-release", "bookworm-backports"], ["-a", "arm64"]):
            with self.subTest(args):
                _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["install", "-y", *args, "nginx"])
                self.assertEqual(apt.err, "")
                self.assertIn("The following NEW packages will be installed:\n  nginx \n", bytes(apt.out).decode())
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["-o", "Debug::X=1", "install", "-y", "nginx"])
        self.assertIn("  nginx \n", bytes(apt.out).decode())

    def test_dnf_has_no_tools_of_debian(self):
        run = self.upstream_session("rhel-9", uid=0)
        for name in ("apt", "apt-get", "dpkg"):
            self.assertEqual(run(["dnf", "install", "-y", name])[1:], (f"Error: Unable to find a match: {name}\n", 1))
        self.assertFalse(getattr(run.protocol, "tpot_installed", {}))

    def test_apt_packages_name_the_programs_of_the_available_packages(self):
        persona = catalog.by_id("debian-bookworm")
        from tpot_personas.identity import draw
        programs = system.apt_packages(persona, draw(persona, 1))["programs"]
        self.assertEqual(programs["net-tools"], ["/usr/bin/netstat", "/usr/sbin/ifconfig"])
        self.assertEqual(programs["busybox"], ["/usr/bin/busybox", "/usr/bin/ftpget"])
        self.assertEqual(programs["php-cli"], ["/usr/bin/php"])
        self.assertEqual(programs["unzip"], ["/usr/bin/unzip"])
        self.assertEqual(programs["git"], ["/usr/bin/git"])
        self.assertFalse({"yum", "dnf", "rpm"} & set(programs))
        answers = system.apt_packages(persona, draw(persona, 1))["answers"]
        self.assertEqual(answers, ["dig", "ethtool", "ftpget", "git", "ifconfig", "iptables", "netstat", "php", "tftp",
                                   "unzip"])
        for pid, unavailable in (("debian-bookworm", ["yum"]), ("debian-trixie", []), ("ubuntu-noble", ["yum"]),
                                 ("ubuntu-jammy", []), ("raspios", ["yum"])):
            persona = catalog.by_id(pid)
            self.assertEqual(system.apt_packages(persona, draw(persona, 1))["unavailable"], unavailable, pid)

    def test_dnf_install_marks_the_programs_for_the_session(self):
        run = self.upstream_session("rhel-9", uid=0, extra_modules=("git",))
        evr = self.dnf_data(run)["available"]["git-core"]["evr"]
        self.assertEqual(run("dnf install -y git")[2], 0)
        self.assertEqual(run.protocol.tpot_installed["git"], evr.split(":")[-1].rsplit("-", 1)[0])
        self.assertNotIn("/usr/bin/git", run.protocol.commands)
        self.assertEqual(run("git --version"), (f"git version {evr.split(':')[-1].rsplit('-', 1)[0]}\n", "", 0))
        self.assertEqual(run("dnf remove -y git-core")[2], 0)
        self.assertNotIn("git", run.protocol.tpot_installed)

    def test_dnf_answers_only_with_commands_of_the_installed_version(self):
        run = self.upstream_session("rhel-9", uid=0)
        answers = self.dnf_data(run)["answers"]
        self.assertIn("git", answers)
        self.assertFalse({"gcc", "cc", "pip", "pip3", "nmap"} & set(answers), answers)
        self.assertEqual(run("dnf install -y gcc")[2], 0)
        self.assertNotIn("gcc", getattr(run.protocol, "tpot_installed", {}))

    def test_dnf_takes_no_path_as_a_package(self):
        run = self.upstream_session("rhel-9", uid=0)
        for word in ("..", ".", "./x.rpm", "../../usr/bin/x"):
            out, err, code = run(["dnf", "install", "-y", word])
            self.assertEqual((err, code), (f"Error: Unable to find a match: {word}\n", 1), word)
        self.assertEqual(getattr(run.protocol.fs, "made", []), [])

    def test_rpm_qf_reads_the_owners_once(self):
        run = self.upstream_session("rhel-9", uid=0)
        data = run.commands["rpm"].call.__globals__["tpot_dnf_data"]()
        reads = []

        from collections.abc import Mapping

        class Owners(Mapping):
            def __init__(self, owners):
                self.owners = owners

            def __getitem__(self, key):
                return self.owners[key]

            def __iter__(self):
                return iter(self.owners)

            def __len__(self):
                return len(self.owners)

            def keys(self):
                reads.append(1)
                return self.owners.keys()

        data["owners"] = Owners(data["owners"])
        out, err, code = run("rpm -qf /usr/bin/bash /usr/bin/ls /etc/passwd")
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(len(out.splitlines()), 3)
        self.assertLessEqual(len(reads), 1)

    def apt_with_packages(self, packages, fallback="ubuntu", available=None, programs=None, unavailable=None,
                          answers=None, names=None):
        path = tmpdir(self) / "apt-packages.json"
        path.write_text(json.dumps({"installed": packages, "available": available or {}, "programs": programs or {},
                                    "unavailable": unavailable or [], "answers": answers or []}))
        values = {**self.APT, ("shell", "apt_packages"): str(path), ("shell", "apt_fallback"): fallback}
        return {**values, **self.names_file(names)} if names is not None else values

    def names_file(self, names):
        path = tmpdir(self) / "package-names.txt"
        path.write_text("".join(f"{name}\n" for name in sorted(names)))
        return {("shell", "package_names"): str(path)}

    HEAD = "Reading package lists... Done\nBuilding dependency tree... Done\nReading state information... Done\n"

    def test_apt_does_not_find_a_name_the_release_does_not_have(self):
        values = self.apt_with_packages({}, "debian", names={"htop", "nmap", "man-db"})
        for args in (["nosuchpkg123"], ["nosuchpkg123", "htop"], ["htop", "nosuchpkg123"]):
            with self.subTest(args):
                _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", *args])
                self.assertEqual(bytes(apt.out).decode(), self.HEAD)
                self.assertEqual(apt.err, "E: Unable to locate package nosuchpkg123\n")
                self.assertEqual((apt.code, apt.fs.made, apt.protocol.commands), (100, [], {}))
                self.assertFalse(getattr(apt.protocol, "tpot_faked", None))

    def test_apt_installs_a_name_of_the_release_without_data(self):
        values = self.apt_with_packages({}, "debian", names={"htop", "nmap"})
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "htop"])
        text = bytes(apt.out).decode()
        self.assertIn("The following NEW packages will be installed:\n  htop \n", text)
        self.assertRegex(text, r"Setting up htop \(\d+\.\d+(\.\d+)?-\d(\+b1)?\) \.\.\.\n")
        self.assertEqual((apt.err, apt.code, apt.fs.made), ("", None, ["/usr/bin/htop"]))
        self.assertFalse(getattr(apt.protocol, "tpot_installed", None))

    def test_apt_takes_globs_and_names_dots_and_globs_it_does_not_find(self):
        values = self.apt_with_packages({}, "debian", names={"htop", "nmap", "nginx", "nginx-common"})
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "nginx*", "ht?p"])
        self.assertIn("  nginx nginx-common htop \n", bytes(apt.out).decode())
        self.assertEqual(apt.err, "")
        missing = {"nosuch.pkg": "E: Unable to locate package nosuch.pkg\n"
                                 "E: Couldn't find any package by glob 'nosuch.pkg'\n"
                                 "E: Couldn't find any package by regex 'nosuch.pkg'\n",
                   "nosuch*": "E: Unable to locate package nosuch*\n"
                              "E: Couldn't find any package by glob 'nosuch*'\n"
                              "E: Couldn't find any package by regex 'nosuch*'\n",
                   "Nginx": "E: Unable to locate package Nginx\n"}
        for word, err in missing.items():
            with self.subTest(word):
                _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", word])
                self.assertEqual((apt.err, apt.code, apt.fs.made), (err, 100, []))
        # apt tries no regular expression
        _, apt = self.twisted_command("apt", "Command_apt", values, ["install", "-y", "nosuch.pkg"], tty=False)
        self.assertEqual(apt.err, "\nWARNING: apt does not have a stable CLI interface. Use with caution in scripts.\n\n"
                                  "E: Unable to locate package nosuch.pkg\n"
                                  "E: Couldn't find any package by glob 'nosuch.pkg'\n")

    def test_apt_glob_matches_are_bounded(self):
        names = {f"lib{n}" for n in range(500)}
        values = self.apt_with_packages({}, "debian", names=names)
        namespace, _ = self.twisted_command("apt", "Command_aptget", values, ["-v"])
        self.assertEqual(namespace["tpot_apt_glob"]("lib*"), sorted(names)[:64])
        self.assertEqual(namespace["tpot_apt_glob"]("lib*" + "x" * 80), [])

    def names_lines(self, lines):
        path = tmpdir(self) / "package-names.txt"
        path.write_text("".join(f"{line}\n" for line in lines))
        return {("shell", "package_names"): str(path)}

    def test_apt_has_no_candidate_for_a_name_the_release_refers_to(self):
        # yum is no name of the list (a glob does not take it), apt names it as the release does
        values = {**self.apt_with_packages({}, "debian", unavailable=["yum"]), **self.names_lines(["tree", "yumex"])}
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "yum"])
        self.assertEqual(bytes(apt.out).decode(), self.HEAD
                         + "Package yum is not available, but is referred to by another package.\n"
                         "This may mean that the package is missing, has been obsoleted, or\n"
                         "is only available from another source\n\n")
        self.assertEqual((apt.err, apt.code), ("E: Package 'yum' has no installation candidate\n", 100))
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "yu*"])
        self.assertIn("  yumex \n", bytes(apt.out).decode())

    def test_apt_virtual_names(self):
        values = {**self.apt_with_packages({}, "debian"),
                  **self.names_lines(["ack", "netcat-openbsd", "netcat-traditional", "ack-grep ack",
                                      "netcat netcat-openbsd,netcat-traditional"])}
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "netcat"])
        namespace, _ = self.twisted_command("apt", "Command_aptget", values, ["-v"])
        version = namespace["tpot_apt_version"]
        self.assertEqual(bytes(apt.out).decode(), self.HEAD + "Package netcat is a virtual package provided by:\n"
                         f"  netcat-openbsd {version('netcat-openbsd')}\n"
                         f"  netcat-traditional {version('netcat-traditional')}\n"
                         "You should explicitly select one to install.\n\n")
        self.assertEqual((apt.err, apt.code, apt.fs.made), ("E: Package 'netcat' has no installation candidate\n", 100, []))
        # one provider: apt takes it
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "ack-grep"])
        self.assertIn("The following NEW packages will be installed:\n  ack \n", bytes(apt.out).decode())
        self.assertEqual((apt.err, apt.code), ("", None))

    def test_apt_needs_root_for_dpkg_lock(self):
        lock = ("Could not open lock file /var/lib/dpkg/lock-frontend - open (13: Permission denied)",
                "Unable to acquire the dpkg frontend lock (/var/lib/dpkg/lock-frontend), are you root?")
        values = self.apt_with_packages({}, "debian", names={"tree"})
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "tree"], uid=1000)
        self.assertEqual((bytes(apt.out), apt.err, apt.code, apt.fs.made),
                         (b"", "".join(f"E: {line}\n" for line in lock), 100, []))
        _, apt = self.twisted_command("apt", "Command_apt", values, ["install", "-y", "tree"], uid=1000)
        self.assertEqual(apt.err, "".join(f"\x1b[1;31mE: \x1b[0m{line}\x1b[0m\n" for line in lock))
        _, apt = self.twisted_command("apt", "Command_apt", values, ["install", "-y", "tree"], uid=1000, tty=False)
        self.assertEqual(apt.err, "\nWARNING: apt does not have a stable CLI interface. Use with caution in scripts.\n\n"
                         + "".join(f"E: {line}\n" for line in lock))
        trixie = {**values, ("shell", "apt_version"): "3.0.3"}
        _, apt = self.twisted_command("apt", "Command_apt", trixie, ["install", "-y", "tree"], uid=1000)
        self.assertEqual(apt.err, "".join(f"\x1b[1;31mError: \x1b[0m\x1b[1m{line}\x1b[0m\n" for line in lock))

    def test_apt_1_names_the_lock_and_its_lines_as_apt_1(self):
        old = ("E: Could not open lock file /var/lib/dpkg/lock - open (13: Permission denied)\n"
               "E: Unable to lock the administration directory (/var/lib/dpkg/), are you root?\n")
        for version in ("1.4.10", None):
            values = {**self.apt_with_packages({}, "debian", names={"tree"}), ("shell", "apt_version"): version or ""}
            _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "tree"], uid=1000)
            self.assertEqual((bytes(apt.out), apt.err, apt.code), (b"", old, 100), version)
        values = {**self.apt_with_packages({}, "debian", names={"tree"}), ("shell", "apt_version"): "1.4.10"}
        _, apt = self.twisted_command("apt", "Command_apt", values, ["install", "-y", "nosuch"])
        self.assertEqual((bytes(apt.out).decode(), apt.err), (
            "Reading package lists... Done\nBuilding dependency tree       \nReading state information... Done\n",
            "E: Unable to locate package nosuch\n"))
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "nosuch"], tty=False)
        self.assertEqual(bytes(apt.out).decode(),
                         "Reading package lists...\nBuilding dependency tree...\nReading state information...\n")

    def test_apt_writes_as_its_version_on_a_terminal_and_a_pipe(self):
        values = self.apt_with_packages({"bash": "5.2.15-2+b7"}, "debian", names={"tree", "bash"})
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "nosuchpkg123"], tty=False)
        self.assertEqual((bytes(apt.out).decode(), apt.err), (
            "Reading package lists...\nBuilding dependency tree...\nReading state information...\n",
            "E: Unable to locate package nosuchpkg123\n"))
        _, apt = self.twisted_command("apt", "Command_apt", values, ["install", "-y", "nosuchpkg123"])
        self.assertEqual((bytes(apt.out).decode(), apt.err), (
            self.HEAD, "\x1b[1;31mE: \x1b[0mUnable to locate package nosuchpkg123\x1b[0m\n"))
        # apt 3.0 (Debian 13): the apt program clears its progress lines on a terminal, writes Error: and the
        # Installing / Summary layout; apt-get stays as it was
        trixie = {**values, ("shell", "apt_version"): "3.0.3"}
        _, apt = self.twisted_command("apt", "Command_apt", trixie, ["install", "-y", "nosuchpkg123"])
        self.assertEqual((bytes(apt.out).decode(), apt.err), (
            "", "\x1b[1;31mError: \x1b[0m\x1b[1mUnable to locate package nosuchpkg123\x1b[0m\n"))
        _, apt = self.twisted_command("apt", "Command_apt", trixie, ["install", "-y", "nosuchpkg123"], tty=False)
        self.assertEqual((bytes(apt.out).decode(), apt.err), (
            "Reading package lists...\nBuilding dependency tree...\nReading state information...\n",
            "\nWARNING: apt does not have a stable CLI interface. Use with caution in scripts.\n\n"
            "Error: Unable to locate package nosuchpkg123\n"))
        _, apt = self.twisted_command("apt", "Command_aptget", trixie, ["install", "-y", "nosuchpkg123"])
        self.assertEqual((bytes(apt.out).decode(), apt.err), (self.HEAD, "E: Unable to locate package nosuchpkg123\n"))
        _, apt = self.twisted_command("apt", "Command_apt", trixie, ["install", "-y", "tree"])
        text = bytes(apt.out).decode()
        self.assertTrue(text.startswith("Installing:\n\x1b[32m  tree\n\x1b[0m\nSummary:\n  Upgrading: 0, Installing: 1, "
                                        "Removing: 0, Not Upgrading: 259\n  Download size: "), text[:160])
        self.assertRegex(text, r"\n  Space needed: \d+(\.\d)? [kM]B / \d+(\.\d)? [kMGT]B available\n\nGet:1 ")
        _, apt = self.twisted_command("apt", "Command_apt", trixie, ["install", "-y", "tree"], tty=False)
        self.assertIn("Reading state information...\nInstalling:\n  tree\n\nSummary:\n", bytes(apt.out).decode())
        _, apt = self.twisted_command("apt", "Command_apt", trixie, ["install", "-y", "bash"])
        self.assertEqual(bytes(apt.out).decode(), "bash is already the newest version (5.2.15-2+b7).\n"
                         "Summary:\n  Upgrading: 0, Installing: 0, Removing: 0, Not Upgrading: 259\n")

    def test_apt_sizes_as_apt_writes_them(self):
        namespace, _ = self.twisted_command("apt", "Command_aptget", self.APT, ["-v"])
        size = namespace["tpot_apt_bytes"]
        self.assertEqual([size(n) for n in (57, 57900, 173000, 2054000, 210e9, 9999)],
                         ["57 B", "57.9 kB", "173 kB", "2054 kB", "210 GB", "9999 B"])

    def test_apt_install_of_many_packages_stays_within_a_minute(self):
        names = {f"pkg{n}" for n in range(200)}
        values = self.apt_with_packages({}, "debian", names=names)
        sleeps = []
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", *sorted(names)], sleeps=sleeps)
        self.assertEqual(apt.code, None)
        self.assertLessEqual(sum(max(call) for call in sleeps), 70)

    def test_apt_answers_by_package_not_by_program_name(self):
        # ifconfig is the program of net-tools, no package: its Cowrie command stays skipped
        values = self.apt_with_packages({}, "debian", available={"net-tools": "2.10-0.1"},
                                        programs={"net-tools": ["/usr/sbin/ifconfig"]}, answers=["ifconfig"])
        cowrie = {"ifconfig": "Command_ifconfig"}
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "ifconfig"], commands=cowrie)
        self.assertFalse(getattr(apt.protocol, "tpot_installed", None))
        self.assertEqual(list(apt.protocol.tpot_faked), ["/usr/bin/ifconfig"])
        values = self.apt_with_packages({}, "debian", available={"net-tools": "2.10-0.1"},
                                        programs={"net-tools": ["/usr/sbin/ifconfig"]}, answers=["ifconfig"],
                                        names={"net-tools"})
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "ifconfig"], commands=cowrie)
        self.assertEqual((apt.err, apt.code), ("E: Unable to locate package ifconfig\n", 100))

    def test_apt_leaves_a_program_of_the_persona(self):
        values = self.apt_with_packages({}, "debian")
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "lspci"],
                                      commands={"lspci": "Command_lspci"}, present={"/usr/bin/lspci"})
        self.assertIn("Setting up lspci", bytes(apt.out).decode())
        self.assertEqual(apt.fs.made, [])
        self.assertEqual(apt.protocol.commands, {"lspci": "Command_lspci"})
        self.assertFalse(getattr(apt.protocol, "tpot_faked", None))

    def test_apt_install_shows_the_versions_of_the_persona(self):
        values = self.apt_with_packages({"curl": "8.5.0-2ubuntu10.6", "vim": "2:9.1.0016-1ubuntu7.9",
                                         "man-db": "2.12.0-4build2"})
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "curl"])
        self.assertEqual(bytes(apt.out).decode(), "Reading package lists... Done\nBuilding dependency tree... Done\n"
                         "Reading state information... Done\ncurl is already the newest version (8.5.0-2ubuntu10.6).\n"
                         "0 upgraded, 0 newly installed, 0 to remove and 259 not upgraded.\n")
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "curl", "netcat-openbsd"])
        text = bytes(apt.out).decode()
        self.assertIn("curl is already the newest version (8.5.0-2ubuntu10.6).\n", text)
        self.assertIn("The following NEW packages will be installed:\n  netcat-openbsd \n", text)
        self.assertIn("Processing triggers for man-db (2.12.0-4build2) ...\n", text)
        self.assertTrue(text.index("Setting up netcat-openbsd") < text.index("Processing triggers"))

    def test_apt_installs_the_release_version_of_a_known_package(self):
        values = self.apt_with_packages({"wget": "1.21.4-1ubuntu4.1"},
                                        available={"nmap": "7.94+git20230807.3be01efb1+dfsg-3build2"})
        _, apt = self.twisted_command("apt", "Command_aptget", values, ["install", "-y", "wget", "nmap"])
        text = bytes(apt.out).decode()
        self.assertIn("wget is already the newest version (1.21.4-1ubuntu4.1).\n", text)
        self.assertIn("Unpacking nmap (7.94+git20230807.3be01efb1+dfsg-3build2) ...\n", text)

    def test_apt_fallback_versions_fit_the_release_and_stay_the_same(self):
        seen = {}
        for fallback, pattern in (("ubuntu", r"^\d+\.\d+(\.\d+)?-\d(build|ubuntu)\d$"),
                                  ("debian", r"^\d+\.\d+(\.\d+)?-\d(\+b1)?$")):
            values = self.apt_with_packages({}, fallback)
            namespace, _ = self.twisted_command("apt", "Command_aptget", values, ["-v"])
            for name in ("nmap", "tmux", "masscan", "zmap", "socat", "hydra"):
                version = namespace["tpot_apt_version"](name)
                self.assertRegex(version, pattern, (fallback, name))
                self.assertEqual(version, namespace["tpot_apt_version"](name))
                seen.setdefault(name, set()).add(version)
                self.assertTrue(40 <= namespace["tpot_apt_size"](name) < 940)
        self.assertNotIn("1.34-1", {v for versions in seen.values() for v in versions})

    YUM = {("shell", "yum_plugins"): "fastestmirror"}

    def test_yum_install_y_of_centos(self):
        _, yum = self.twisted_command("yum", "Command_yum", self.YUM, ["install", "-y", "curl"])
        text = bytes(yum.out).decode()
        self.assertIn("Loaded plugins: fastestmirror\n", text)
        self.assertIn("---> Package curl.", text)
        self.assertNotIn("Package y.", text)
        self.assertNotIn("priority protections", text)
        self.assertNotIn("Kernel Module Plugin", text)
        self.assertNotIn("Is this ok", text)
        self.assertIn("Complete!\n", text)
        _, yum = self.twisted_command("yum", "Command_yum", self.YUM, ["install", "curl"])
        self.assertTrue(bytes(yum.out).decode().endswith("Is this ok [y/d/N]: "))

    def yum_with(self, names=None, installed=""):
        values = {**self.YUM, ("shell", "yum_installed"): installed}
        return {**values, **self.names_file(names)} if names is not None else values

    YUM_HEAD = "Loaded plugins: fastestmirror\nLoading mirror speeds from cached hostfile\n"

    def test_yum_has_no_package_for_a_name_the_release_does_not_have(self):
        values = self.yum_with({"curl", "bash", "python3-pip"})
        _, yum = self.twisted_command("yum", "Command_yum", values, ["install", "-y", "nosuchpkg123"], tty=False)
        self.assertEqual(bytes(yum.out).decode(), self.YUM_HEAD + "No package nosuchpkg123 available.\n")
        self.assertEqual((yum.err, yum.code), ("Error: Nothing to do\n", 1))
        _, yum = self.twisted_command("yum", "Command_yum", values, ["install", "-y", "BASH", "nosuch*"], tty=False)
        self.assertEqual(bytes(yum.out).decode(), self.YUM_HEAD + "No package BASH available.\n"
                         "  * Maybe you meant: bash\nNo package nosuch* available.\n")
        self.assertEqual((yum.err, yum.code), ("Error: Nothing to do\n", 1))
        # the names it has are installed, the others are named
        _, yum = self.twisted_command("yum", "Command_yum", values, ["install", "-y", "nosuchpkg123", "python3-pip"], tty=False)
        text = bytes(yum.out).decode()
        self.assertTrue(text.startswith(self.YUM_HEAD + "No package nosuchpkg123 available.\nResolving Dependencies\n"),
                        text[:200])
        self.assertRegex(text, r"---> Package python3-pip\.x86_64 0:[01]\.\d+\.\d-\d+\.el7 will be installed\n")
        self.assertRegex(text, r"\n  python3-pip\.x86_64 0:[01]\.\d+\.\d-\d+\.el7 ")
        self.assertNotIn("nosuchpkg123.", text)
        self.assertIn("Complete!\n", text)

    def test_yum_names_the_installed_packages_of_the_persona(self):
        values = self.yum_with({"wget", "yum", "vim-enhanced", "curl"},
                               "vim-enhanced 2:7.4.629-8.el7_9,wget 1.14-18.el7_6.1,yum 3.4.3-168.el7.centos")
        _, yum = self.twisted_command("yum", "Command_yum", values, ["install", "-y", "wget", "yum.noarch", "vim-enhanced"])
        self.assertEqual(bytes(yum.out).decode(), self.YUM_HEAD
                         + "Package wget-1.14-18.el7_6.1.x86_64 already installed and latest version\n"
                         "Package yum-3.4.3-168.el7.centos.noarch already installed and latest version\n"
                         "Package 2:vim-enhanced-7.4.629-8.el7_9.x86_64 already installed and latest version\n"
                         "Nothing to do\n")
        self.assertEqual((yum.err, yum.code), ("", None))

    def opkg(self, args, protocol=None):
        path = tmpdir(self) / "package-names.txt"
        path.write_text("htop packages\nkmod-mt7603 core\nbusybox base\n")
        help_path = tmpdir(self) / "opkg-help.txt"
        help_path.write_text("opkg must have one sub-command argument\nusage: opkg [options...] sub-command\n")
        values = {("shell", "package_names"): str(path), ("shell", "opkg_help"): str(help_path),
                  ("shell", "opkg_release"): "22.03.7", ("shell", "opkg_target"): "ramips/mt7621",
                  ("shell", "opkg_arch"): "mipsel_24kc", ("shell", "opkg_installed"): "busybox 1.35.0-5,opkg 2022-1"}
        namespace, opkg = self.twisted_command("apt", "Command_opkg", values, ["-v"])
        command = namespace["Command_opkg"](list(args))
        command.user = {"uid": 0, "gid": 0}
        command.sleep = lambda *a, **k: None
        if protocol is not None:
            command.protocol = protocol
        command.start()
        return bytes(command.out).decode(), command.err, command.code, command.protocol

    def test_opkg_knows_only_the_installed_packages_before_an_update(self):
        out, err, code, protocol = self.opkg(["install", "nosuchpkg123"])
        self.assertEqual((out, err, code), ("Unknown package 'nosuchpkg123'.\n", "Collected errors:\n"
                                            " * opkg_install_cmd: Cannot install package nosuchpkg123.\n", 255))
        out, err, code, _ = self.opkg(["install", "htop", "busybox"])
        self.assertEqual(out, "Unknown package 'htop'.\nPackage busybox (1.35.0-5) installed in root is up to date.\n")
        self.assertEqual(code, 255)
        self.assertEqual(self.opkg(["list-installed"])[0], "busybox - 1.35.0-5\nopkg - 2022-1\n")

    def test_opkg_installs_names_of_the_feeds_after_an_update(self):
        out, err, code, protocol = self.opkg(["update"])
        self.assertTrue(out.startswith("Downloading https://downloads.openwrt.org/releases/22.03.7/targets/ramips/"
                                       "mt7621/packages/Packages.gz\nUpdated list of available packages in "
                                       "/var/opkg-lists/openwrt_core\n"), out[:200])
        self.assertIn("/releases/22.03.7/packages/mipsel_24kc/telephony/Packages.sig\nSignature check passed.\n", out)
        self.assertEqual((err, code), ("", None))
        out, err, code, protocol = self.opkg(["install", "nosuchpkg123", "htop"], protocol)
        version = protocol.tpot_opkg_added["htop"]
        self.assertRegex(version, r"^\d+\.\d+(\.\d+)?-\d$")
        self.assertEqual(out, f"Unknown package 'nosuchpkg123'.\nInstalling htop ({version}) to root...\n"
                              f"Downloading https://downloads.openwrt.org/releases/22.03.7/packages/mipsel_24kc/"
                              f"packages/htop_{version}_mipsel_24kc.ipk\nConfiguring htop.\n")
        self.assertEqual((err, code), ("Collected errors:\n * opkg_install_cmd: Cannot install package nosuchpkg123.\n",
                                       255))
        self.assertEqual(self.opkg(["install", "htop"], protocol)[:3],
                         (f"Package htop ({version}) installed in root is up to date.\n", "", None))
        self.assertIn(f"htop - {version}\n", self.opkg(["list-installed"], protocol)[0])
        self.assertEqual(self.opkg(["remove", "htop"], protocol)[0], "Removing package htop from root...\n")
        self.assertEqual(self.opkg(["remove", "htop"], protocol)[0], "No packages removed.\n")

    def test_opkg_usage(self):
        self.assertEqual(self.opkg([])[:3], ("usage: opkg [options...] sub-command\n",
                                             "opkg must have one sub-command argument\n", 1))
        self.assertEqual(self.opkg(["foo"])[1:3], ("opkg: unknown sub-command foo\n", 1))
        self.assertEqual(self.opkg(["install"])[1:3],
                         ("opkg: the ``install'' command requires at least one argument\n", 1))
        self.assertEqual(self.opkg(["print-architecture"])[0], "arch all 1\narch noarch 1\narch mipsel_24kc 10\n")
        self.assertEqual(self.opkg(["info", "x"])[:3], ("", "", None))

    def test_yum_has_nothing_to_do_when_the_names_it_found_are_installed(self):
        values = self.yum_with({"wget"}, "wget 1.14-18.el7_6.1")
        _, yum = self.twisted_command("yum", "Command_yum", values, ["install", "-y", "wget", "nosuch"], tty=False)
        self.assertEqual(bytes(yum.out).decode(), self.YUM_HEAD
                         + "Package wget-1.14-18.el7_6.1.x86_64 already installed and latest version\n"
                         "No package nosuch available.\nNothing to do\n")
        self.assertEqual((yum.err, yum.code), ("", None))

    def test_yum_takes_the_package_that_provides_a_name(self):
        values = {**self.yum_with(None, "mariadb 1:5.5.68-1.el7"),
                  **self.names_lines(["mariadb", "nmap-ncat", "mysql mariadb", "nc nmap-ncat"])}
        _, yum = self.twisted_command("yum", "Command_yum", values, ["install", "-y", "nc"])
        text = bytes(yum.out).decode()
        self.assertRegex(text, r"---> Package nmap-ncat\.x86_64 0:\S+ will be installed\n")
        self.assertNotIn("Package nc.", text)
        _, yum = self.twisted_command("yum", "Command_yum", values, ["install", "-y", "mysql"])
        self.assertEqual(bytes(yum.out).decode(), self.YUM_HEAD
                         + "Package 1:mariadb-5.5.68-1.el7.x86_64 already installed and latest version\nNothing to do\n")

    def test_yum_needs_root(self):
        _, yum = self.twisted_command("yum", "Command_yum", self.yum_with({"tree"}), ["install", "-y", "tree"], uid=1000)
        self.assertEqual((bytes(yum.out), yum.err, yum.code),
                         (b"Loaded plugins: fastestmirror\n", "You need to be root to perform this command.\n", 1))

    def test_yum_marks_the_name_bold_on_a_terminal(self):
        values = self.yum_with({"tree"})
        _, yum = self.twisted_command("yum", "Command_yum", values, ["install", "-y", "nosuch"])
        self.assertIn("No package \x1b[1mnosuch\x1b(B\x1b[m available.\n", bytes(yum.out).decode())
        _, yum = self.twisted_command("yum", "Command_yum", values, ["install", "-y", "nosuch"], tty=False)
        self.assertIn("No package nosuch available.\n", bytes(yum.out).decode())

    def test_opkg_skips_the_values_of_its_options(self):
        out, err, code, _ = self.opkg(["-d", "ram", "--conf", "/etc/opkg.conf", "--verify-program", "/usr/sbin/opkg-key",
                                       "install", "busybox"])
        self.assertEqual((out, code), ("Package busybox (1.35.0-5) installed in root is up to date.\n", None))

    def test_yum_update_as_root(self):
        _, yum = self.twisted_command("yum", "Command_yum", self.YUM, ["-y", "update"])
        self.assertEqual(bytes(yum.out).decode(), "Loaded plugins: fastestmirror\n"
                         "Loading mirror speeds from cached hostfile\nNo packages marked for update\n")
        _, yum = self.twisted_command("yum", "Command_yum", self.YUM, ["update"], uid=1000)
        self.assertIn("You need to be root to perform this command.", yum.err)

    def test_sudo_version_of_the_persona(self):
        values = {("shell", "sudo_version"): "1.9.15p5", ("shell", "sudo_grammar"): "50"}
        namespace, sudo = self.command("sudo", "Command_sudo", values)
        sudo.version()
        self.assertEqual(bytes(sudo.out).decode(), "Sudo version 1.9.15p5\nSudoers policy plugin version 1.9.15p5\n"
                         "Sudoers file grammar version 50\nSudoers I/O plugin version 1.9.15p5\n"
                         "Sudoers audit plugin version 1.9.15p5\n")
        self.assertIs(namespace["commands"]["/usr/bin/sudo"], namespace["Command_sudo"])
        _, sudo = self.command("sudo", "Command_sudo", {("shell", "sudo_version"): "1.8.23",
                                                        ("shell", "sudo_grammar"): "46"})
        sudo.version()
        self.assertNotIn("audit", bytes(sudo.out).decode())

    def sudo(self, args, user="ubuntu", uid=1000, rules=None, commands=("ls",)):
        path = tmpdir(self) / "sudo-list.json"
        path.write_text(json.dumps(rules if rules is not None else system.sudo_list(catalog.by_id("ubuntu-noble"))))
        _, sudo = self.command("sudo", "Command_sudo", {("shell", "sudo_list"): str(path)}, args)
        sudo.user = {"username": user, "uid": uid, "gid": uid}
        sudo.environ = {"PATH": ":".join(PATH)}
        sudo.fs = TreeFs(base_tree())
        sudo.protocol.hostname = "ip-172-31-5-9.eu-central-1.compute.internal"
        sudo.protocol.getCommand = lambda cmd, paths, cwd: cmd if cmd in commands else None
        sudo.start()
        return bytes(sudo.out).decode(), sudo.err, sudo.code

    def test_sudo_l_lists_the_rules_of_the_persona(self):
        expected = ("Matching Defaults entries for ubuntu on ip-172-31-5-9:\n"
                    "    env_reset, mail_badpass,\n"
                    "    secure_path=/usr/local/sbin\\:/usr/local/bin\\:/usr/sbin\\:/usr/bin\\:/sbin\\:/bin\\:/snap/bin,\n"
                    "    use_pty\n\n"
                    "User ubuntu may run the following commands on ip-172-31-5-9:\n"
                    "    (ALL : ALL) ALL\n    (ALL) NOPASSWD: ALL\n")
        for args in (["-l"], ["-ll"], ["--list"], ["-nl"]):
            self.assertEqual(self.sudo(args), (expected, "", None), args)
        out, _, _ = self.sudo(["-l"], user="root", uid=0)
        self.assertTrue(out.endswith("User root may run the following commands on ip-172-31-5-9:\n"
                                     "    (ALL : ALL) ALL\n"), out)
        self.assertEqual(self.sudo(["-l"], user="guest", uid=1001),
                         ("", "Sorry, user guest may not run sudo on ip-172-31-5-9.\n", 1))
        self.assertEqual(self.sudo(["-l", "ls", "-la"]), ("/usr/bin/ls -la\n", "", None))
        out, _, _ = self.sudo(["-l"], user="cloud-user", rules=system.sudo_list(catalog.by_id("rhel-9")))
        self.assertIn("    !visiblepw, always_set_home, match_group_by_gid, always_query_group_plugin,\n", out)
        self.assertIn("    secure_path=/usr/local/sbin\\:/usr/local/bin\\:/usr/sbin\\:/usr/bin\n", out)
        self.assertTrue(out.endswith("    (ALL) ALL\n    (ALL) NOPASSWD: ALL\n"), out)
        self.assertTrue(all(len(line) <= 80 or "," not in line[:-1] for line in out.splitlines()), out)

    def test_sudo_l_counts_only_before_the_command(self):
        # the options end at the first word that is no option: -l after it belongs to that command
        self.assertEqual(self.sudo(["foo", "-l"]), ("", "sudo: foo: command not found\n", 1))
        self.assertEqual(self.sudo(["-n", "foo", "--list"]), ("", "sudo: foo: command not found\n", 1))
        self.assertEqual(self.sudo(["--", "-l"]), ("", "sudo: -l: command not found\n", 1))
        for args in (["-u", "root", "-l"], ["-uroot", "-l"], ["--user=root", "-l"], ["-n", "-l"], ["-p", "x", "-ln"]):
            out, err, code = self.sudo(args)
            self.assertEqual((err, code), ("", None), args)
            self.assertTrue(out.startswith("Matching Defaults entries for ubuntu on "), args)

    def test_sudo_without_l_keeps_its_options(self):
        _, err, _ = self.sudo(["-x"])
        self.assertIn("sudo: illegal option -- x", err)
        _, err, _ = self.sudo(["-V"])
        self.assertEqual(err, "")

    def systemctl(self, pid, args, uid=1000, seed=1, sudo=False, now_after_boot=3 * 86400 + 4 * 3600):
        from tpot_personas.identity import draw
        from tpot_personas.render import render
        persona = catalog.by_id(pid)
        out = tmpdir(self) / pid
        ident = draw(persona, seed)
        render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=out / "keys")
        source = (self.patched() / "src/cowrie/commands/service.py").read_text()
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(
            {("shell", "systemctl_units"): str(out / "systemd-units.json")})})
        cmd = FakeCommand(args)
        cmd.user = {"uid": uid, "gid": uid}
        boot = time.time() - now_after_boot
        cmd.protocol.boot_time = lambda: boot
        cmd.protocol.hostname = ident.hostname
        cmd.protocol.cmdstack = [type("Command_sudo", (), {})()] if sudo else []
        code = namespace["tpot_systemctl"](cmd, list(args))
        return bytes(cmd.out).decode(), cmd.err, code, namespace, ident

    def test_systemctl_status_of_a_unit_of_the_persona(self):
        out, err, code, _, ident = self.systemctl("debian-bookworm", ["status", "sshd"])
        self.assertEqual((err, code), ("", 0))
        lines = out.splitlines()
        self.assertEqual(lines[0], "\u25cf ssh.service - OpenBSD Secure Shell server")
        self.assertEqual(lines[1], "     Loaded: loaded (/lib/systemd/system/ssh.service; enabled; preset: enabled)")
        self.assertRegex(lines[2], r"^     Active: active \(running\) since \w{3} \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC; "
                                   r"3 days ago$")
        self.assertRegex(lines[3], r"^   Main PID: \d+ \(sshd\)$")
        self.assertRegex(lines[4], r"^      Tasks: 1 \(limit: \d+\)$")
        self.assertRegex(lines[5], r"^     Memory: \d+(\.\d)?M$")
        self.assertRegex(lines[6], r"^        CPU: \d")
        self.assertEqual(lines[7], "     CGroup: /system.slice/ssh.service")
        self.assertRegex(lines[8], r'^             \u2514\u2500\d+ "sshd: /usr/sbin/sshd -D \[listener\] 0 of 10-100 startups"$')
        self.assertIn(f" {ident.hostname.split('.')[0]} systemd[1]: Starting ssh.service - OpenBSD Secure Shell server...", out)
        self.assertRegex(out, r"sshd\[\d+\]: Server listening on 0\.0\.0\.0 port 22\.\n")
        self.assertTrue(out.endswith("systemd[1]: Started ssh.service - OpenBSD Secure Shell server.\n"))

    def test_systemctl_status_follows_the_systemd_of_the_release(self):
        out, _, code, _, _ = self.systemctl("centos-7", ["status", "sshd.service"], now_after_boot=9 * 86400 + 3600)
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[1], "   Loaded: loaded (/usr/lib/systemd/system/sshd.service; enabled; vendor preset: enabled)")
        self.assertTrue(lines[2].endswith("; 1 weeks 2 days ago"), lines[2])
        self.assertFalse(any(line.lstrip().startswith(("Tasks:", "Memory:", "CPU:")) for line in lines))
        self.assertRegex(out, r"\n           \u2514\u2500\d+ /usr/sbin/sshd -D\n")
        self.assertIn("systemd[1]: Starting OpenSSH server daemon...", out)
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["status", "ssh"])
        self.assertIn("; disabled; preset: enabled)", out)
        self.assertIn("TriggeredBy: \u25cf ssh.socket\n", out)
        self.assertRegex(out, r"Memory: [\d.]+M \(peak: [\d.]+M\)\n")
        out, err, code, _, _ = self.systemctl("ubuntu-noble", ["status", "sshd"])
        self.assertEqual((out, err, code), ("", "Unit sshd.service could not be found.\n", 4))
        out, _, _, _, _ = self.systemctl("ubuntu-jammy", ["status", "systemd-journald"])
        self.assertIn("(/lib/systemd/system/systemd-journald.service; static)", out)
        self.assertIn("Main PID: ", out)
        out, _, _, _, _ = self.systemctl("rhel-9", ["status", "kdump"])
        self.assertIn("Active: active (exited) since ", out)
        self.assertRegex(out, r"Main PID: \d+ \(code=exited, status=0/SUCCESS\)")
        self.assertIn("systemd[1]: Finished kdump.service - Crash recovery kernel arming.", out)

    def test_systemctl_is_active_and_is_enabled(self):
        self.assertEqual(self.systemctl("rhel-9", ["is-active", "sshd", "crond"])[:3], ("active\nactive\n", "", 0))
        self.assertEqual(self.systemctl("rhel-9", ["is-active", "cron"])[:3], ("inactive\n", "", 3))
        self.assertEqual(self.systemctl("ubuntu-noble", ["is-active", "--quiet", "cron"])[:3], ("", "", 0))
        self.assertEqual(self.systemctl("ubuntu-noble", ["is-enabled", "cron", "systemd-journald"])[:3],
                         ("enabled\nstatic\n", "", 0))
        self.assertEqual(self.systemctl("ubuntu-noble", ["is-enabled", "nosuch"])[1:3],
                         ("Failed to get unit file state for nosuch.service: No such file or directory\n", 1))
        self.assertEqual(self.systemctl("ubuntu-noble", ["is-active"])[1:3], ("Too few arguments.\n", 1))

    def test_systemctl_is_enabled_answers_with_the_state_of_the_unit_file(self):
        # systemd: 0 when one of the units is enabled, static, alias, indirect, generated or transient
        self.assertEqual(self.systemctl("ubuntu-noble", ["is-enabled", "ssh"])[:3], ("disabled\n", "", 1))
        self.assertEqual(self.systemctl("ubuntu-noble", ["is-enabled", "-q", "ssh"])[:3], ("", "", 1))
        self.assertEqual(self.systemctl("ubuntu-noble", ["is-enabled", "ssh", "cron"])[:3], ("disabled\nenabled\n", "", 0))
        _, _, _, namespace, _ = self.systemctl("ubuntu-noble", ["--version"])
        states = {"enabled": 0, "enabled-runtime": 0, "static": 0, "alias": 0, "indirect": 0, "generated": 0,
                  "transient": 0, "disabled": 1, "masked": 1, "masked-runtime": 1, "linked": 1, "linked-runtime": 1,
                  "bad": 1}
        namespace["tpot_units"] = lambda: {"version": 255, "units": [
            {"name": f"u-{state}.service", "state": state, "path": "/x"} for state in states]}
        for state, status in states.items():
            cmd = FakeCommand([])
            cmd.user = {"uid": 1000}
            cmd.protocol.boot_time = lambda: time.time() - 60
            cmd.protocol.hostname = "web1"
            with self.subTest(state):
                self.assertEqual(namespace["tpot_systemctl"](cmd, ["is-enabled", f"u-{state}"]), status)
                self.assertEqual(bytes(cmd.out), f"{state}\n".encode())

    def test_systemctl_is_active_when_one_of_the_units_is(self):
        # systemd's check_unit_generic: 0 when one unit is in the state asked for
        self.assertEqual(self.systemctl("rhel-9", ["is-active", "sshd", "cron"])[:3], ("active\ninactive\n", "", 0))
        self.assertEqual(self.systemctl("rhel-9", ["is-active", "cron", "nosuch"])[:3], ("inactive\ninactive\n", "", 3))
        self.assertEqual(self.systemctl("rhel-9", ["is-failed", "sshd"])[:3], ("active\n", "", 1))

    def test_systemctl_options_take_their_values(self):
        out, err, code, _, _ = self.systemctl("ubuntu-noble", ["status", "ssh", "-n", "20"])
        self.assertEqual((err, code), ("", 0))
        self.assertTrue(out.startswith("● ssh.service - "), out[:40])
        for args in (["status", "ssh", "--lines", "0"], ["status", "-n0", "ssh"], ["--lines=0", "status", "ssh"]):
            out, err, code, _, _ = self.systemctl("ubuntu-noble", args)
            self.assertEqual((err, code), ("", 0), args)
            self.assertNotIn("systemd[1]", out, args)
            self.assertTrue(out.endswith("CGroup: /system.slice/ssh.service\n") or "└─" in out.splitlines()[-1],
                            (args, out))
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["status", "ssh", "-n", "1"])
        self.assertEqual(len(out.split("\n\n", 1)[1].splitlines()), 1, out)
        for args in (["-o", "cat", "status", "ssh"], ["--output", "short", "status", "ssh"], ["-p", "Id", "status", "ssh"],
                     ["--property=Id", "status", "ssh"], ["-H", "web1", "status", "ssh"], ["-M", "c1", "status", "ssh"],
                     ["--host=web1", "status", "ssh"]):
            out, err, code, _, _ = self.systemctl("ubuntu-noble", args)
            self.assertEqual((err, code), ("", 0), args)
            self.assertTrue(out.startswith("● ssh.service"), args)
        self.assertEqual(self.systemctl("ubuntu-noble", ["status", "ssh", "-n"])[1:3],
                         ("systemctl: option requires an argument -- 'n'\n", 1))
        self.assertEqual(self.systemctl("ubuntu-noble", ["status", "ssh", "--lines"])[1:3],
                         ("systemctl: option '--lines' requires an argument\n", 1))
        self.assertEqual(self.systemctl("ubuntu-noble", ["status", "ssh", "-n", "many"])[1:3],
                         ("Failed to parse lines 'many'\n", 1))

    def test_systemctl_lines_are_a_number_of_systemd(self):
        # safe_atou: ASCII digits up to 2^32-1; str.isdigit and int take others or fail on them
        for value in ("\u00b2", "\u0663", "9" * 5000, "4294967296", "1_0", "+5", " 5"):
            with self.subTest(value=value[:12]):
                self.assertEqual(self.systemctl("ubuntu-noble", ["status", "ssh", "-n", value])[1:3],
                                 (f"Failed to parse lines '{value}'\n", 1))
        self.assertEqual(self.systemctl("ubuntu-noble", ["status", "ssh", "--lines=4294967295"])[1:3], ("", 0))

    def test_systemctl_exits_on_an_unexpected_error(self):
        _, _, _, namespace, _ = self.systemctl("ubuntu-noble", ["--version"])

        def fail():
            raise RuntimeError("unexpected")

        namespace["tpot_units"] = fail
        cmd = FakeCommand([])
        self.assertEqual(namespace["tpot_systemctl"](cmd, ["status", "ssh"]), 1)
        command = namespace["Command_systemctl"](["status", "ssh"])
        command.call()
        self.assertEqual(command.exit_code, 1)

    def test_dnf_and_rpm_exit_on_an_unexpected_error(self):
        run = self.upstream_session("rhel-9", uid=0)
        namespace = run.commands["rpm"].call.__globals__

        def fail(*args):
            raise RuntimeError("unexpected")

        namespace["tpot_dnf_data"] = fail
        self.assertEqual(run("rpm -q bash")[2], 1)
        self.assertEqual(run("dnf install -y nmap")[2], 1)
        self.assertEqual(run("dnf list installed")[2], 1)

    def test_systemctl_state_and_type_select_the_units(self):
        out, _, code, namespace, _ = self.systemctl("ubuntu-noble", ["list-units", "--state=running", "--no-legend"])
        rows = out.splitlines()
        self.assertEqual(code, 0)
        self.assertTrue(rows and all(row.split()[3] == "running" for row in rows), rows)
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["list-units", "--state", "running,listening", "--no-legend"])
        self.assertEqual({row.split()[3] for row in out.splitlines()}, {"running", "listening"})
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["list-units", "--state=failed"])
        self.assertEqual(out, "0 loaded units listed.\n")
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["list-units", "-t", "socket,service", "--state=listening",
                                                          "--no-legend"])
        self.assertTrue(out and all(row.split()[0].endswith(".socket") for row in out.splitlines()), out)
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["list-unit-files", "--state=disabled", "--no-legend"])
        self.assertIn("ssh.service ", out)
        self.assertTrue(all(row.split()[1] == "disabled" for row in out.splitlines()), out)
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["list-unit-files", "--type=socket", "--no-legend"])
        self.assertTrue(out and all(row.split()[0].endswith(".socket") for row in out.splitlines()), out)

    def test_systemctl_reads_its_units_once(self):
        _, _, _, namespace, _ = self.systemctl("ubuntu-noble", ["--version"])
        first = namespace["tpot_units"]()
        Path(namespace["CowrieConfig"].get("shell", "systemctl_units")).unlink()
        self.assertIs(namespace["tpot_units"](), first)
        self.assertTrue(first["units"])

    def test_systemctl_list_units(self):
        out, _, code, namespace, _ = self.systemctl("ubuntu-jammy", ["list-units", "--type=service", "--no-pager"])
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertRegex(lines[0], r"^  UNIT +LOAD   ACTIVE SUB     DESCRIPTION$")
        rows = [line for line in lines[1:] if line.startswith("  ") and ".service" in line]
        names = [row.split()[0] for row in rows]
        self.assertEqual(names, sorted(names, key=str.lower))
        self.assertIn("ssh.service", names)
        self.assertTrue(all(re.match(r"^  \S+ +loaded active (running|exited ) \S", row) for row in rows), rows)
        self.assertIn("\nLOAD   = Reflects whether the unit definition was properly loaded.\n", out)
        self.assertIn(f"\n{len(rows)} loaded units listed. Pass --all to see loaded but inactive units, too.\n", out)
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["list-units", "-t", "service", "--all"])
        self.assertIn("\nLegend: LOAD   \u2192 Reflects whether the unit definition was properly loaded.\n", out)
        self.assertIn(" loaded units listed.\nTo show all installed unit files", out)
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["--no-legend"])
        self.assertTrue(out.startswith("  apparmor.service "), out[:40])
        self.assertNotIn("Legend", out)
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["list-units", "--type=timer"])
        self.assertEqual(out, "0 loaded units listed.\n")
        out, _, _, _, _ = self.systemctl("rhel-9", ["list-unit-files"])
        self.assertRegex(out, r"^UNIT FILE +STATE +PRESET\n")
        self.assertRegex(out, r"\nsshd.service +enabled +enabled\n")
        self.assertRegex(out, r"\n\d+ unit files listed.\n$")

    def test_systemctl_cat_prints_the_unit_file(self):
        from tpot_personas.identity import draw
        from tpot_personas.render import render
        persona = catalog.by_id("ubuntu-noble")
        out = tmpdir(self) / "noble"
        ident = draw(persona, 1)
        render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=out / "keys")
        fs = tree.load_pickle(out / "fs.pickle")
        source = (self.patched() / "src/cowrie/commands/service.py").read_text()
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(
            {("shell", "systemctl_units"): str(out / "systemd-units.json")})})
        text = tree.node_get(tree.find_node(fs, "usr/lib/systemd/system/dbus.service"), tree.A_CONTENTS)
        cmd = FakeCommand([], files={"/usr/lib/systemd/system/dbus.service": text})
        cmd.user = {"uid": 1000}
        cmd.protocol.boot_time = lambda: time.time() - 60
        cmd.protocol.hostname = ident.hostname
        code = namespace["tpot_systemctl"](cmd, ["cat", "dbus"])
        self.assertEqual((bytes(cmd.out), code), (b"# /usr/lib/systemd/system/dbus.service\n" + text, 0))
        cmd = FakeCommand([], files={})
        cmd.user = {"uid": 1000}
        cmd.protocol.boot_time = lambda: time.time() - 60
        cmd.protocol.hostname = ident.hostname
        self.assertEqual(namespace["tpot_systemctl"](cmd, ["cat", "nosuch"]), 1)
        self.assertEqual(cmd.err, "No files found for nosuch.service.\n")
        # a unit whose file is gone (rm): Cowrie's fs raises its FileNotFound, no OSError
        cmd = FakeCommand([], files={})
        cmd.user = {"uid": 1000}
        cmd.protocol.boot_time = lambda: time.time() - 60
        cmd.protocol.hostname = ident.hostname
        self.assertEqual(namespace["tpot_systemctl"](cmd, ["cat", "dbus"]), 1)
        self.assertEqual((bytes(cmd.out), cmd.err), (b"", "No files found for dbus.service.\n"))

    def test_systemctl_cat_goes_on_after_a_missing_unit(self):
        from tpot_personas.identity import draw
        from tpot_personas.render import render
        persona = catalog.by_id("ubuntu-noble")
        out = tmpdir(self) / "noble"
        ident = draw(persona, 1)
        render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=out / "keys")
        fs = tree.load_pickle(out / "fs.pickle")
        source = (self.patched() / "src/cowrie/commands/service.py").read_text()
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(
            {("shell", "systemctl_units"): str(out / "systemd-units.json")})})
        files = {path: tree.node_get(tree.find_node(fs, path), tree.A_CONTENTS)
                 for path in ("usr/lib/systemd/system/dbus.service", "usr/lib/systemd/system/cron.service")}
        cmd = FakeCommand([], files={"/" + path: text for path, text in files.items()})
        cmd.user = {"uid": 1000}
        cmd.protocol.boot_time = lambda: time.time() - 60
        cmd.protocol.hostname = ident.hostname
        code = namespace["tpot_systemctl"](cmd, ["cat", "dbus", "nosuch", "cron"])
        self.assertEqual(code, 1)
        self.assertEqual(cmd.err, "No files found for nosuch.service.\n")
        self.assertEqual(bytes(cmd.out), b"# /usr/lib/systemd/system/dbus.service\n"
                         + files["usr/lib/systemd/system/dbus.service"]
                         + b"\n# /usr/lib/systemd/system/cron.service\n"
                         + files["usr/lib/systemd/system/cron.service"])
        cmd = FakeCommand([], files={})
        cmd.user = {"uid": 1000}
        cmd.protocol.boot_time = lambda: time.time() - 60
        cmd.protocol.hostname = ident.hostname
        self.assertEqual(namespace["tpot_systemctl"](cmd, ["cat", "nosuch", "other"]), 1)
        self.assertEqual(cmd.err, "No files found for nosuch.service.\nNo files found for other.service.\n")

    def test_systemctl_sockets_and_unit_files(self):
        out, err, code, _, _ = self.systemctl("ubuntu-noble", ["status", "ssh.socket"])
        self.assertEqual((err, code), ("", 0))
        self.assertIn("\u25cf ssh.socket - OpenBSD Secure Shell server socket\n", out)
        self.assertRegex(out, r"Active: active \(listening\) since ")
        self.assertIn("   Triggers: \u25cf ssh.service\n", out)
        self.assertIn("     Listen: [::]:22 (Stream)\n", out)
        self.assertIn("systemd[1]: Listening on ssh.socket - OpenBSD Secure Shell server socket.", out)
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["list-units", "--type=socket"])
        self.assertRegex(out, r"\n  ssh\.socket +loaded active listening OpenBSD Secure Shell server socket\n")
        out, _, _, _, _ = self.systemctl("ubuntu-noble", ["list-units"])
        names = [line.split()[0] for line in out.splitlines()[1:] if line.startswith("  ") and "." in line.split()[0]]
        self.assertLess(names.index("unattended-upgrades.service"), names.index("dbus.socket"))
        out, _, _, _, _ = self.systemctl("ubuntu-jammy", ["list-unit-files"])
        self.assertRegex(out, r"^UNIT FILE +STATE +VENDOR PRESET\n")
        self.assertRegex(out, r"\napport\.service +generated +-\n")
        out, _, _, _, _ = self.systemctl("ubuntu-jammy", ["status", "apport"])
        self.assertIn("Loaded: loaded (/etc/init.d/apport; generated)\n", out)
        out, _, _, _, _ = self.systemctl("centos-7", ["status", "network"])
        self.assertIn("Loaded: loaded (/etc/rc.d/init.d/network; bad; vendor preset: disabled)", out)

    def test_systemctl_overview_version_and_errors(self):
        out, _, _, _, ident = self.systemctl("rhel-9", ["status"])
        self.assertTrue(out.startswith(f"\u25cf {ident.hostname.split('.')[0]}\n      State: running\n"), out[:80])
        self.assertIn("           \u251c\u2500init.scope\n", out)
        self.assertRegex(out, r"\u2514\u2500\d+ \"sshd: /usr/sbin/sshd -D \[listener\] 0 of 10-100 startups\"\n")
        out, _, _, _, _ = self.systemctl("rhel-9", ["--version"])
        self.assertTrue(out.startswith("systemd 252 (252-"), out)
        out, _, _, _, _ = self.systemctl("centos-7", ["--version"])
        self.assertTrue(out.startswith("systemd 219\n+PAM"), out)
        self.assertEqual(self.systemctl("rhel-9", ["frobnicate"])[1:3], ("Unknown command verb frobnicate.\n", 1))
        self.assertEqual(self.systemctl("centos-7", ["frobnicate"])[1:3], ("Unknown operation 'frobnicate'.\n", 1))
        self.assertEqual(self.systemctl("rhel-9", ["--bogus"])[1:3], ("systemctl: unrecognized option '--bogus'\n", 1))

    def upstream_session(self, pid="ubuntu-noble", uid=1000, extra_modules=()):
        """A session on Cowrie's own HoneyPotCommand (exec_command, exit) and call_command of
        v3.1.1, with the patched sudo, systemctl, apt and yum of a rendered persona."""
        from tpot_personas.identity import draw
        from tpot_personas.render import render
        persona = catalog.by_id(pid)
        out = tmpdir(self) / pid
        ident = draw(persona, 1)
        render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=out / "keys")
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(out / "cowrie.cfg")
        config = FakeConfig({(section, key): value for section in cfg.sections() for key, value in cfg.items(section)})

        class Logger:
            def info(self, *a, **k):
                pass

            def failure(self, *a, **k):
                pass

        base = module_parts((FIXTURE / "src/cowrie/shell/command.py").read_text(), ["HoneyPotCommand"],
                            {"Logger": Logger, "error": None, "failure": None})["HoneyPotCommand"]
        root = self.patched()
        call_command = method((root / "src/cowrie/shell/protocol.py").read_text(), "HoneyPotBaseProtocol",
                              "call_command", {"HoneyPotExecProtocol": type("Exec", (), {})})

        class Pipe:
            def __init__(self, protocol, cmdclass, args, input_data, targets, *rest, **kw):
                self.input_data, self.targets, self.stdin_from_pipe = input_data, targets, False
                self.out, self.err = bytearray(), bytearray()

            def write_stdout(self, data):
                self.out += data

            def write_stderr(self, data):
                self.err += data

        def inline_callbacks(function):
            def run(*a, **k):
                result = function(*a, **k)
                if hasattr(result, "send"):
                    for _ in result:
                        pass
            return run

        commands = {}
        modules = {}
        for name in ("sudo", "service", "apt", "yum") + tuple(extra_modules):
            source = (root / f"src/cowrie/commands/{name}.py").read_text()
            modules[name] = module_all(source, {"HoneyPotCommand": base, "PipeProtocol": Pipe, "CowrieConfig": config,
                                                "inlineCallbacks": inline_callbacks, "defer": None, "reactor": None,
                                                "Logger": Logger})
            commands.update(modules[name]["commands"])
            for cls in modules[name]["commands"].values():
                if hasattr(cls, "sleep"):
                    cls.sleep = lambda self, *a, **k: None

        class Shell:
            environ = {"PATH": ":".join(PATH)}
            exported = set()
            cwd = "/root"
            user = {"uid": uid, "gid": uid, "username": persona.user if uid else "root", "home": "/root"}
            interactive = True
            last_exit_code = None

            def queue_line(self, line):
                pass

            def resume(self):
                pass

        class Protocol:
            hostname = ident.hostname
            terminal = None
            # the persona's own tree, as Cowrie loads it
            fs = TreeFs(tree.load_pickle(out / "fs.pickle"))

            def __init__(self):
                self.cmdstack = [Shell()]
                self.pp = Pipe(None, None, [], None, {})
                self.commands = commands

            def boot_time(self):
                return time.time() - 86400

            def getCommand(self, cmd, paths, cwd):
                return commands.get(cmd)

            def end_process(self, code):
                pass

        Protocol.call_command = call_command
        protocol = Protocol()

        def run(line, input_data=None, tty=False):
            words = line if isinstance(line, list) else line.split()
            # stdout on the terminal (an interactive session) or captured (a pipe, the default here)
            pp = Pipe(None, None, words[1:], input_data, {1: ("terminal", None)} if tty else {})
            protocol.call_command(pp, commands[words[0]], *words[1:])
            child = protocol.pp
            return bytes(child.out).decode(), bytes(child.err).decode(), protocol.cmdstack[0].last_exit_code

        def answer(line):
            """A line typed while the command of the last run waits for it."""
            child = protocol.pp
            start = (len(child.out), len(child.err))
            protocol.cmdstack[-1].lineReceived(line)
            return (bytes(child.out[start[0]:]).decode(), bytes(child.err[start[1]:]).decode(),
                    protocol.cmdstack[0].last_exit_code)

        run.answer = answer
        run.protocol = protocol
        run.commands = commands
        run.ident = ident
        run.out = out
        return run

    def test_sudo_runs_its_command_as_root(self):
        run = self.upstream_session()
        self.assertEqual(run("sudo systemctl restart ssh"), ("", "", 0))
        self.assertEqual(run("sudo systemctl stop cron"), ("", "", 0))
        out, err, code = run("systemctl restart ssh")
        self.assertIn("Interactive authentication required.", err)
        self.assertEqual(code, 1)
        self.assertIn("Unit nosuch.service not found.", run("sudo systemctl start nosuch")[1])

    def test_sudo_apt_update_runs_as_root(self):
        run = self.upstream_session("debian-trixie")
        out, err, _ = run("sudo apt-get update")
        self.assertIn("Reading package lists... Done", out)
        self.assertEqual(err, "")
        self.assertIn("Could not open lock file", run("apt-get update")[1])

    def dnf_data(self, run):
        return json.loads((run.out / "dnf-packages.json").read_text())

    def test_dnf_install_y_of_rhel(self):
        run = self.upstream_session("rhel-9", uid=0)
        data = self.dnf_data(run)
        nmap = data["available"]["nmap"]
        out, err, code = run("dnf install -y nmap")
        self.assertEqual((err, code), ("", 0))
        lines = out.splitlines()
        self.assertEqual(lines[0], "Updating Subscription Management repositories.")
        self.assertRegex(lines[1], r"^Last metadata expiration check: \d+:\d\d:\d\d ago on [A-Z][a-z]{2} \d\d "
                                   r"[A-Z][a-z]{2} \d{4} \d\d:\d\d:\d\d [AP]M UTC\.$")
        self.assertEqual(lines[2:4], ["Dependencies resolved.", "=" * 80])
        self.assertRegex(lines[4], r"^ Package +Arch(itecture)? +Version +Repository +Size$")
        self.assertEqual(lines[5:7], ["=" * 80, "Installing:"])
        self.assertRegex(lines[7], rf"^ nmap +x86_64 +{re.escape(nmap['evr'])} +{nmap['repo']} +5\.6 M$")
        for part in ("\nTransaction Summary\n" + "=" * 80 + "\nInstall  1 Package\n\nTotal download size: 5.6 M\n"
                     "Installed size: 24 M\nDownloading Packages:\n",
                     "Running transaction check\nTransaction check succeeded.\nRunning transaction test\n"
                     "Transaction test succeeded.\nRunning transaction\n",
                     f"  Installing       : nmap-{nmap['evr']}.x86_64",
                     f"  Verifying        : nmap-{nmap['evr']}.x86_64",
                     "Installed products updated.\n\nInstalled:\n  nmap-"):
            self.assertIn(part, out)
        self.assertTrue(out.endswith("\n\nComplete!\n"), out[-40:])
        self.assertNotIn("Is this ok", out)
        self.assertRegex(out, r"\nnmap-7\.92-[^ ]+\.x86_64\.rpm +[\d.]+ [kM]B/s \| 5\.6 MB +00:0\d    \n")
        self.assertTrue(all(len(line) <= 80 for line in lines if "Version" in line or line.startswith("  ")), out)
        # the session has it now: rpm and dnf know it, its program answers as those of apt do
        self.assertEqual(run("rpm -q nmap")[0], f"nmap-{nmap['evr'].split(':')[1]}.x86_64\n")
        self.assertIn(f"Package nmap-{nmap['evr']}.x86_64 is already installed.\n", run("dnf install -y nmap")[0])
        self.assertIn("/usr/bin/nmap", run.protocol.commands)
        self.assertIn(("/usr/bin/nmap", 0, 0, 0o100755), run.protocol.fs.made)

    def test_yum_of_rhel_is_dnf_and_asks(self):
        run = self.upstream_session("rhel-9", uid=0)
        data = self.dnf_data(run)
        wget = data["installed"]["wget"]
        out, err, code = run("yum install wget")
        self.assertTrue(out.endswith(f"Package wget-{wget['evr']}.x86_64 is already installed.\n"
                                     "Dependencies resolved.\nNothing to do.\nComplete!\n"), out)
        self.assertEqual(code, 0)
        out, err, code = run("yum install socat")
        self.assertTrue(out.endswith("Installed size: 1.1 M\nIs this ok [y/N]: "), out[-60:])
        self.assertEqual(run.answer("maybe")[0], "Is this ok [y/N]: ")
        out, err, code = run.answer("y")
        self.assertTrue(out.startswith("Downloading Packages:\nsocat-"), out[:60])
        self.assertTrue(out.endswith("Complete!\n"))
        self.assertEqual(code, 0)
        out, err, code = run("yum install gcc")
        self.assertIn("Installing dependencies:\n cpp ", out)
        self.assertIn("Install  7 Packages\n", out)
        self.assertEqual(run.answer("N"), ("", "Operation aborted.\n", 1))
        self.assertEqual(run("rpm -q gcc"), ("package gcc is not installed\n", "", 1))
        out, err, code = run("dnf --assumeno install gcc")
        self.assertEqual((err, code), ("Operation aborted.\n", 1))
        self.assertNotIn("Is this ok", out)
        out, err, code = run("dnf install telnet", input_data=b"y\n")
        self.assertIn("Is this ok [y/N]: Downloading Packages:\n", out)
        self.assertEqual(code, 0)

    def test_dnf_reads_every_piped_answer(self):
        run = self.upstream_session("rhel-9", uid=0)
        self.assertTrue(run.commands["dnf"].consumes_stdin)
        out, err, code = run("dnf install socat", input_data=b"maybe\n")
        self.assertTrue(out.endswith("Is this ok [y/N]: Is this ok [y/N]: "), out[-60:])
        self.assertEqual((err, code), ("Operation aborted.\n", 1))
        out, err, code = run("dnf install socat", input_data=b"maybe\nyes\n")
        self.assertTrue(out.endswith("Complete!\n"), out[-40:])
        self.assertEqual(code, 0)
        out, err, code = run("dnf install tcpdump", input_data=b"")
        self.assertEqual((err, code), ("Operation aborted.\n", 1))

    def test_dnf_without_a_match_and_without_root(self):
        run = self.upstream_session("rhel-9", uid=0)
        out, err, code = run("dnf install -y masscan nmap")
        self.assertTrue(out.endswith("No match for argument: masscan\n"), out)
        self.assertEqual((err, code), ("Error: Unable to find a match: masscan\n", 1))
        self.assertIn("No such command: frob. Please use /usr/bin/dnf --help", run("dnf frob")[1])
        self.assertEqual(run("dnf install")[2], 2)
        out, err, code = run("dnf update nmap")
        self.assertIn("Package nmap available, but not installed.\nNo match for argument: nmap\n", out)
        self.assertEqual((err, code), ("Error: No packages marked for upgrade.\n", 1))
        self.assertTrue(run("dnf upgrade -y")[0].endswith("Dependencies resolved.\nNothing to do.\nComplete!\n"))
        self.assertIn("protected packages: sudo", run("dnf remove -y sudo")[1])
        user = self.upstream_session("rhel-9", uid=1000)
        out, err, code = user("dnf install -y nmap")
        self.assertEqual(out, "Not root, Subscription Management repositories not updated\n")
        self.assertEqual((err, code), ("Error: This command has to be run with superuser privileges (under the root "
                                       "user on most systems).\n", 1))
        out, err, code = user("sudo dnf install -y nmap")
        self.assertTrue(out.startswith("Updating Subscription Management repositories.\n"), out[:60])
        self.assertEqual(code, 0)
        self.assertIn("Installed Packages\nnmap.x86_64 ", user("dnf list installed nmap")[0])

    def test_dnf_remove(self):
        run = self.upstream_session("rhel-9", uid=0)
        out, err, code = run("dnf remove -y wget")
        self.assertIn("Removing:\n wget ", out)
        self.assertIn("Remove  1 Package\n\nFreed space: ", out)
        self.assertIn("  Erasing          : wget-", out)
        self.assertIn("Removed:\n  wget-", out)
        self.assertNotIn("Downloading Packages", out)
        self.assertEqual(run("rpm -q wget"), ("package wget is not installed\n", "", 1))
        out, err, code = run("dnf remove -y nosuch")
        self.assertIn("No match for argument: nosuch\n", out)
        self.assertEqual(code, 0)

    def test_dnf_version_list_search_repolist(self):
        run = self.upstream_session("rhel-9", uid=0)
        data = self.dnf_data(run)
        out = run("dnf --version", tty=True)[0]
        self.assertTrue(out.startswith(f"{data['version']}\n  Installed: \x1b[1mdnf\x1b(B\x1b[m-0:"
                                       f"{data['installed']['dnf']['evr']}.noarch at "), out)
        self.assertIn("  Built    : Red Hat, Inc. <http://bugzilla.redhat.com/bugzilla> at ", out)
        self.assertIn(f"\n\n  Installed: \x1b[1mrpm\x1b(B\x1b[m-0:{data['installed']['rpm']['evr']}.x86_64 at ", out)
        out, err, code = run("dnf list installed")
        lines = out.splitlines()
        self.assertEqual(lines[:2], ["Updating Subscription Management repositories.", "Installed Packages"])
        self.assertTrue(lines[2].startswith("NetworkManager.x86_64 "), lines[2])
        names = [line.split(".")[0] for line in lines[2:] if not line.startswith(" ")]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), len(data["installed"]))
        self.assertRegex(out, r"\nopenssh-server\.x86_64 +" + re.escape(data["installed"]["openssh-server"]["evr"]))
        self.assertNotIn("Last metadata", out)
        out = run("dnf list available nmap")[0]
        self.assertIn("Last metadata expiration check", out)
        self.assertRegex(out, r"Available Packages\nnmap\.x86_64 +3:7\.92-\S+ +rhel-9-for-x86_64-appstream")
        self.assertEqual(run("dnf list installed nosuch")[1:], ("Error: No matching Packages to list\n", 1))
        out = run("dnf search nmap", tty=True)[0]
        self.assertIn("\n" + "=" * 26 + " Name Exactly Matched: nmap " + "=" * 26 + "\n"
                      "\x1b[1mnmap\x1b(B\x1b[m.x86_64 : Network exploration tool and security scanner\n", out)
        self.assertIn(" Name & Summary Matched: nmap ", out)
        self.assertIn("No matches found.\n", run("dnf search zzzzz")[0])
        out = run("dnf repolist")[0].splitlines()
        repos = sorted(repo["id"] for repo in data["repos"] if repo["enabled"])
        self.assertEqual([line.split()[0] for line in out[2:]], repos)
        self.assertTrue(out[1].startswith("repo id ") and out[1].endswith(" repo name"), out[1])
        self.assertEqual(len(out[1]), len(max(repos, key=len)) + 1 + len("repo name"))
        out = run("yum repolist all")[0]
        self.assertIn(" status\n", out)
        self.assertIn(" disabled\n", out)

    def test_dnf_marks_only_on_a_terminal(self):
        run = self.upstream_session("rhel-9", uid=0)
        for line in ("dnf --version", "dnf search nmap", "dnf install -y masscan"):
            self.assertNotIn("\x1b", "".join(run(line)[:2]), line)
        self.assertIn("No match for argument: \x1b[1mmasscan\x1b(B\x1b[m\n", run("dnf install -y masscan", tty=True)[0])
        self.assertIn("\x1b[1mdnf\x1b(B\x1b[m-0:", run("dnf --version", tty=True)[0])

    def test_dnf_search_matches_every_keyword(self):
        run = self.upstream_session("rhel-9", uid=0)
        out = run("dnf search nmap netcat")[0]
        self.assertIn(" Name & Summary Matched: netcat, nmap ", out)
        self.assertIn("nmap-ncat.x86_64 : ", out)
        self.assertNotIn("\nnmap.x86_64", out)
        out = run("dnf search --all nmap netcat")[0]
        self.assertIn("\nnmap.x86_64 : ", out)
        self.assertIn("nmap-ncat.x86_64 : ", out)
        self.assertEqual(run("dnf search", tty=True)[2], 2)
        # an empty keyword matches every package, nothing to mark
        text = run(["dnf", "search", ""], tty=True)[0]
        self.assertNotIn("\x1b", text)
        self.assertIn("\nbash.x86_64 : ", text)

    def test_dnf_install_fails_at_the_file_limit(self):
        run = self.upstream_session("rhel-9", uid=0)
        run.protocol.fs.mkfile = lambda *a, **k: False
        out, err, code = run("dnf install -y nmap")
        self.assertIn("Running transaction test\n", out)
        self.assertNotIn("Complete!", out)
        self.assertIn("Error: Transaction test error:\n  installing package nmap-3:7.92-", err)
        self.assertIn("more space on the / filesystem", err)
        self.assertEqual(code, 1)
        self.assertNotIn("/usr/bin/nmap", run.protocol.commands)
        self.assertEqual(run("rpm -q nmap")[2], 1)

    def test_rpm_of_rhel(self):
        run = self.upstream_session("rhel-9", uid=1000)
        data = self.dnf_data(run)
        ssh = data["installed"]["openssh-server"]
        self.assertEqual(run("rpm --version")[0], f"RPM version {data['rpm_version']}\n")
        self.assertEqual(run("rpm -q openssh-server nosuch"),
                         (f"openssh-server-{ssh['evr']}.x86_64\npackage nosuch is not installed\n", "", 1))
        rows = run("rpm -qa")[0].splitlines()
        self.assertEqual(len(rows), len(data["installed"]))
        self.assertIn(f"kernel-{run.ident.release.kernel_version}", rows)
        self.assertEqual(run("rpm -qf /usr/bin/curl")[0], f"curl-{data['installed']['curl']['evr']}.x86_64\n")
        self.assertEqual(run("rpm -qf /bin/bash")[0], f"bash-{data['installed']['bash']['evr']}.x86_64\n")
        self.assertEqual(run("rpm -qf /nonexistent")[1:], ("error: file /nonexistent: No such file or directory\n", 1))
        self.assertIn("can't create transaction lock", run("rpm -ivh x.rpm")[1])
        root = self.upstream_session("rhel-9", uid=0)
        self.assertEqual(root("rpm -ivh /tmp/x.rpm")[1:], ("error: open of /tmp/x.rpm failed: No such file or "
                                                          "directory\n", 1))
        self.assertEqual(root("rpm -e wget")[2], 0)
        self.assertEqual(root("rpm -q wget")[2], 1)

    def test_rpm_names_the_owner_of_the_system_files(self):
        run = self.upstream_session("rhel-9", uid=1000)
        installed = self.dnf_data(run)["installed"]

        def nvra(name):
            return f"{name}-{installed[name]['evr'].split(':')[-1]}.{installed[name]['arch']}\n"

        for path, package in (("/bin/ls", "coreutils"), ("/usr/bin/cat", "coreutils"), ("/usr/bin/which", "which"),
                              ("/etc/passwd", "setup"), ("/etc/group", "setup"),
                              ("/etc/redhat-release", "redhat-release"), ("/etc/os-release", "redhat-release"),
                              ("/usr/bin/ps", "procps-ng"), ("/usr/bin/mount", "util-linux"),
                              ("/usr/bin/awk", "gawk"), ("/usr/bin/sudo", "sudo")):
            self.assertEqual(run(f"rpm -qf {path}")[0], nvra(package), path)
        self.assertEqual(run("rpm -qf /etc/hostname"), ("file /etc/hostname is not owned by any package\n", "", 1))
        self.assertEqual(run("rpm -q coreutils")[0], nvra("coreutils"))

    def test_centos_yum_stays_yum_3(self):
        run = self.upstream_session("centos-7", uid=0)
        self.assertEqual(run.commands["yum"].__name__, "Command_yum")
        self.assertEqual(run.commands["dnf"].__name__, "Command_dnf")   # skipped by the persona's config
        out = run("yum install -y curl")[0]
        self.assertTrue(out.startswith("Loaded plugins: fastestmirror\n"), out[:40])
        rhel = self.upstream_session("rhel-9", uid=0)
        self.assertEqual(rhel.commands["yum"].__name__, "Command_dnf_yum")

    def test_package_managers_know_the_names_of_the_release(self):
        for pid in ("debian-bookworm", "ubuntu-noble", "raspios", "debian-trixie", "ubuntu-jammy"):
            with self.subTest(pid):
                run = self.upstream_session(pid, uid=0)
                for line in ("apt install -y nosuchpkg123", "apt-get install -y nosuchpkg123 tree"):
                    out, err, code = run(line)
                    # a session without a terminal: apt warns about its interface, apt 3.0 writes Error:
                    self.assertEqual(code, 100, line)
                    self.assertRegex(err, r"(^|\n)(E|Error): Unable to locate package nosuchpkg123\n$", line)
                    self.assertNotIn("Setting up", out)
                out, err, code = run("apt-get install -y tree")
                self.assertIn("Setting up tree (", out)
                self.assertEqual((err, code), ("", 0))
        run = self.upstream_session("centos-7", uid=0)
        out, err, code = run("yum install -y nosuchpkg123")
        self.assertEqual((out, err, code), ("Loaded plugins: fastestmirror\nLoading mirror speeds from cached hostfile\n"
                                            "No package nosuchpkg123 available.\n", "Error: Nothing to do\n", 1))
        self.assertIn("---> Package tree.", run("yum install -y tree")[0])
        self.assertEqual(self.upstream_session("centos-7")("yum install -y tree"),
                         ("Loaded plugins: fastestmirror\n", "You need to be root to perform this command.\n", 1))
        # EdgeOS: apt 1.4 of Debian 9 without sources
        edge = self.upstream_session("ubiquiti-edgerouter-x")
        self.assertEqual(edge("apt-get install -y tree"), ("", "E: Could not open lock file /var/lib/dpkg/lock - open "
                         "(13: Permission denied)\nE: Unable to lock the administration directory (/var/lib/dpkg/), "
                         "are you root?\n", 100))
        edge = self.upstream_session("ubiquiti-edgerouter-x", uid=0)
        self.assertEqual(edge("apt-get install -y tree")[1:], ("E: Unable to locate package tree\n", 100))
        self.assertEqual(edge("apt-get -v")[0].splitlines()[0], "apt 1.4.10 (mipsel)")
        # the running kernel of the persona is installed at the version of its build
        bookworm = self.upstream_session("debian-bookworm", uid=0)
        release = "6.1.0-50-amd64"
        self.assertIn(f"linux-image-{release} is already the newest version (6.1.176-1).",
                      bookworm(f"apt-get install -y linux-image-{release}")[0])
        self.assertIn(f"Setting up linux-headers-{release} (6.1.176-1) ...",
                      bookworm(f"apt-get install -y linux-headers-{release}")[0])
        out, err, code = self.upstream_session("debian-trixie")("apt-get install -y tree")
        self.assertEqual((out, code), ("", 100))
        self.assertIn("are you root?", err)
        self.assertIn("No package htop available.\n", run("yum install -y htop")[0])

    def test_opkg_of_openwrt_answers_for_its_release(self):
        run = self.upstream_session("openwrt", uid=0)
        self.assertEqual(run("opkg install nosuchpkg123"), ("Unknown package 'nosuchpkg123'.\n", "Collected errors:\n"
                         " * opkg_install_cmd: Cannot install package nosuchpkg123.\n", 255))
        self.assertEqual(run("opkg install htop")[2], 255)
        self.assertEqual(run("opkg update")[2], 0)
        out, err, code = run("opkg install htop")
        self.assertIn("/packages/mipsel_24kc/packages/htop_", out)
        self.assertEqual((err, code), ("", 0))
        self.assertIn("busybox - 1.3", run("/bin/opkg list-installed")[0])

    def test_dnf_columns_as_dnf_computes_them(self):
        source = (self.patched() / "src/cowrie/commands/yum.py").read_text()
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig({}),
                                        "inlineCallbacks": lambda f: f, "defer": None, "reactor": None,
                                        "Logger": lambda: None})
        columns, fmt, number = namespace["tpot_dnf_columns"], namespace["tpot_dnf_fmt"], namespace["tpot_dnf_number"]
        # one package of a long repository: the spaces left go to every column, the rest to the version
        self.assertEqual(columns([{4: 1}, {}, {12: 1}, {32: 1}, {}], [1, 6, 1, 1, 5], remainder_column=2,
                                 indent="  "), [7, 9, 18, 35, 5])
        self.assertEqual(sum(columns([{20: 3}, {12: 3}, {9: 3}], remainder_column=1)) + 2, 80)
        self.assertEqual(fmt((("a", -3), ("bbbbb", -2), ("c", 2)), " ", "\n"), " a   bbbbb\n" + " " * 9 + "c\n")
        self.assertEqual([number(n) for n in (500, 58_000, 5_872_025, 25_165_824, 1_536)],
                         ["500  ", "57 k", "5.6 M", "24 M", "1.5 k"])
        self.assertEqual(namespace["tpot_dnf_section"]("Name Matched: x"), "=" * 31 + " Name Matched: x " + "=" * 32)

    def test_systemctl_changes_only_as_root(self):
        _, err, code, _, _ = self.systemctl("ubuntu-noble", ["restart", "ssh"])
        self.assertEqual((err, code), ("Failed to restart ssh.service: Interactive authentication required.\n"
                                       "See system logs and 'systemctl status ssh.service' for details.\n", 1))
        self.assertEqual(self.systemctl("ubuntu-noble", ["restart", "ssh"], uid=0)[1:3], ("", 0))
        self.assertEqual(self.systemctl("ubuntu-noble", ["start", "nosuch"], uid=0)[1:3],
                         ("Failed to start nosuch.service: Unit nosuch.service not found.\n", 5))
        self.assertEqual(self.systemctl("ubuntu-noble", ["daemon-reload"], uid=0)[1:3], ("", 0))

    def test_systemctl_time_and_size_formats(self):
        _, _, _, namespace, _ = self.systemctl("rhel-9", ["is-active", "sshd"])
        ago, size, span = namespace["tpot_ago"], namespace["tpot_bytes"], namespace["tpot_timespan"]
        self.assertEqual([ago(n, 255) for n in (30, 90, 600, 7200 + 300, 7 * 3600, 26 * 3600, 3 * 86400,
                                                8 * 86400, 40 * 86400, 400 * 86400)],
                         ["30s ago", "1min 30s ago", "10min ago", "2h 5min ago", "7h ago", "1 day 2h ago",
                          "3 days ago", "1 week 1 day ago", "1 month 9 days ago", "1 year 1 month ago"])
        self.assertEqual(ago(8 * 86400, 219), "1 weeks 1 days ago")
        self.assertEqual([size(n) for n in (740 * 1024, 5099520, 3 << 30)], ["740K", "4.8M", "3G"])
        self.assertEqual([span(n) for n in (54, 1204, 2000, 123500, 3723000)],
                         ["54ms", "1.204s", "2s", "2min 3.5s", "1h 2min 3s"])

    def test_service_status_is_systemctl_status(self):
        from tpot_personas.identity import draw
        from tpot_personas.render import render
        for pid, redirect in (("rhel-9", True), ("debian-trixie", False)):
            persona = catalog.by_id(pid)
            out = tmpdir(self) / pid
            ident = draw(persona, 1)
            render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=out / "keys")
            source = (self.patched() / "src/cowrie/commands/service.py").read_text()
            namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(
                {("shell", "systemctl_units"): str(out / "systemd-units.json")})})
            service = namespace["Command_service"](["sshd" if redirect else "ssh", "status"])
            service.user, service.exit_code = {"uid": 1000}, 0
            service.protocol.boot_time = lambda: time.time() - 3600
            service.protocol.hostname = ident.hostname
            service.call()
            self.assertIn("Active: active (running)", bytes(service.out).decode())
            self.assertEqual(service.err, "Redirecting to /bin/systemctl status sshd.service\n" if redirect else "")
            self.assertIs(namespace["commands"]["systemctl"], namespace["Command_systemctl"])
            self.assertIs(namespace["commands"]["/usr/bin/systemctl"], namespace["Command_systemctl"])

    def test_service_status_all_of_the_persona(self):
        path = tmpdir(self) / "service-status.txt"
        path.write_text(" [ + ]  cron\n [ - ]  hwclock.sh\n")
        _, service = self.command("service", "Command_service", {("shell", "service_status_all"): str(path)})
        service.status_all()
        self.assertEqual(bytes(service.out), b" [ + ]  cron\n [ - ]  hwclock.sh\n")
        _, service = self.command("service", "Command_service", {})
        service.status_all()
        self.assertIn(b" [ + ]  ssh\n", bytes(service.out))
        self.assertNotIn(b"lightdm", bytes(service.out))

    def which(self, args):
        _, which = self.command("which", "Command_which", {}, args)
        which.environ, which.fs, which.exit_code = {"PATH": ":".join(PATH)}, TreeFs(base_tree()), 0
        which.call()
        return bytes(which.out).decode(), which.exit_code

    def test_which_a_prints_every_match(self):
        # bin -> usr/bin: /usr/bin/perl and /bin/perl are two names on PATH
        self.assertEqual(self.which(["-a", "perl"]), ("/usr/bin/perl\n/bin/perl\n", 0))
        self.assertEqual(self.which(["-a", "perl", "nothere"]), ("/usr/bin/perl\n/bin/perl\n", 1))
        self.assertEqual(self.which(["-s", "perl"]), ("", 0))
        self.assertEqual(self.which(["-as", "nothere"]), ("", 1))
        self.assertEqual(self.which(["/usr/bin/perl"]), ("/usr/bin/perl\n", 0))
        self.assertEqual(self.which(["--", "perl"]), ("/usr/bin/perl\n", 0))
        self.assertEqual(self.which(["-a"]), ("", 1))

    def test_which_prints_the_first_match_and_fails_for_a_missing_name(self):
        _, which = self.command("which", "Command_which", {}, ["python3", "nothere", "perl"])
        which.environ = {"PATH": ":".join(PATH)}
        which.fs = TreeFs(base_tree())
        which.exit_code = 0
        which.call()
        # bin -> usr/bin: /usr/bin/python3 and /bin/python3 are one file, which names the first
        self.assertEqual((bytes(which.out), which.exit_code), (b"/usr/bin/python3\n/usr/bin/perl\n", 1))
        _, which = self.command("which", "Command_which", {}, ["perl"])
        which.environ, which.fs, which.exit_code = {"PATH": ":".join(PATH)}, TreeFs(base_tree()), 0
        which.call()
        self.assertEqual((bytes(which.out), which.exit_code), (b"/usr/bin/perl\n", 0))

    def test_ssh_uname_is_persona_driven(self):
        ssh = (self.patched() / "src/cowrie/commands/ssh.py").read_text()
        self.assertNotIn("2.6.26-2-686", ssh)
        self.assertIn('CowrieConfig.get("shell", "kernel_version"', ssh)

    def test_netstat_and_service_lose_upstream_markers(self):
        root = self.patched()
        netstat = (root / "src/cowrie/commands/netstat.py").read_text()
        self.assertNotIn("com/ubuntu/upstart", netstat)
        self.assertNotIn("Phil Blundell", netstat)
        service = (root / "src/cowrie/commands/service.py").read_text()
        self.assertNotIn("whoopsie", service)
        self.assertIn("for line in output:", service)

    def test_free_reads_the_emulated_meminfo(self):
        free = (self.patched() / "src/cowrie/commands/free.py").read_text()
        self.assertNotIn('open("/proc/meminfo")', free)
        get_free_stats = method(free, "Command_free", "get_free_stats")
        meminfo = (b"MemTotal:          4028248 kB\nMemFree:           1200000 kB\n"
                   b"MemAvailable:      2500000 kB\nBuffers:             80000 kB\n"
                   b"Cached:            1300000 kB\nSwapCached:              0 kB\n"
                   b"SwapTotal:               0 kB\nSwapFree:                0 kB\n"
                   b"Shmem:               12000 kB\n")
        asked = []

        class FakeFs:
            def file_contents(self, path):
                asked.append(path)
                return meminfo

        class Command:
            fs = FakeFs()

        stats = get_free_stats(Command())
        self.assertEqual(asked, ["/proc/meminfo"])
        self.assertEqual(stats, {"MemTotal": 4028248, "MemFree": 1200000, "MemAvailable": 2500000,
                                 "Buffers": 80000, "Cached": 1300000, "SwapTotal": 0, "SwapFree": 0,
                                 "Shmem": 12000})

    def test_free_without_meminfo_prints_nothing(self):
        free = (self.patched() / "src/cowrie/commands/free.py").read_text()
        get_free_stats = method(free, "Command_free", "get_free_stats")

        class FakeFs:
            def file_contents(self, path):
                raise FileNotFound(path)

        class Command:
            fs = FakeFs()

        self.assertEqual(get_free_stats(Command()), {})

    def host_key_algorithms(self, value, keys):
        """tpot_host_key_algorithms of the patched factory.py with [ssh] host_key_algorithms = value
        (None: unset) and these loaded key types; Twisted 26.4's supportedPublicKeys."""
        source = (self.patched() / "src/cowrie/ssh/factory.py").read_text()

        class Transport:
            class SSHTransportBase:
                supportedPublicKeys = [b"ecdsa-sha2-nistp256", b"ecdsa-sha2-nistp384", b"ecdsa-sha2-nistp521",
                                       b"rsa-sha2-512", b"rsa-sha2-256", b"ssh-rsa", b"ssh-dss", b"ssh-ed25519"]

        class Factory:
            class SSHFactory:
                pass

        config = FakeConfig({} if value is None else {("ssh", "host_key_algorithms"): value})
        namespace = module_all(source, {"CowrieConfig": config, "transport": Transport, "factory": Factory,
                                        "Logger": lambda: None})
        return namespace["tpot_host_key_algorithms"]({k.encode(): object() for k in keys})

    def test_factory_offers_the_host_key_algorithms_of_the_persona(self):
        factory = (self.patched() / "src/cowrie/ssh/factory.py").read_text()
        self.assertIn("        t.supportedPublicKeys = tpot_host_key_algorithms(self.privateKeys)\n", factory)
        self.assertNotIn("list(self.privateKeys.keys())\n\n        if not self.primes", factory)
        keys = ("ssh-rsa", "ecdsa-sha2-nistp256", "ssh-ed25519")
        self.assertEqual(self.host_key_algorithms("rsa-sha2-512,rsa-sha2-256,ecdsa-sha2-nistp256,ssh-ed25519", keys),
                         [b"rsa-sha2-512", b"rsa-sha2-256", b"ecdsa-sha2-nistp256", b"ssh-ed25519"])
        self.assertEqual(self.host_key_algorithms("ssh-rsa, rsa-sha2-512,rsa-sha2-256", keys),
                         [b"ssh-rsa", b"rsa-sha2-512", b"rsa-sha2-256"])
        self.assertEqual(self.host_key_algorithms("ssh-ed25519,rsa-sha2-256,ssh-rsa", ("ssh-ed25519", "ssh-rsa")),
                         [b"ssh-ed25519", b"rsa-sha2-256", b"ssh-rsa"])

    def test_factory_leaves_out_what_it_cannot_sign(self):
        # no ed25519 key loaded, a name Twisted does not know, one named twice
        self.assertEqual(self.host_key_algorithms("rsa-sha2-512,ssh-ed25519,ssh-ed448,rsa-sha2-512", ("ssh-rsa",)),
                         [b"rsa-sha2-512"])
        # unset or nothing usable: one per loaded key, as upstream
        self.assertEqual(self.host_key_algorithms(None, ("ssh-rsa", "ssh-ed25519")), [b"ssh-rsa", b"ssh-ed25519"])
        self.assertEqual(self.host_key_algorithms("ssh-ed448", ("ssh-rsa",)), [b"ssh-rsa"])

    def send_banner(self, banner):
        """sendBanner of the patched ssh/userauth.py with /etc/issue.net = banner (None: missing);
        returns (what it logged, the packets it sent)."""
        import textwrap
        source = (self.patched() / "src/cowrie/ssh/userauth.py").read_text()
        body = textwrap.dedent(source[source.index("    def sendBanner(self):"):])
        logged, sent = [], []

        def read_honeyfs_bytes(rel):
            if banner is None:
                raise FileNotFoundError(rel)
            return banner

        class Log:
            def failure(self, *args, **kwargs):
                logged.append(("failure", args))

            def info(self, *args, **kwargs):
                logged.append(("info", args))

        class Userauth:
            MSG_USERAUTH_BANNER = 53

        namespace = {"read_honeyfs_bytes": read_honeyfs_bytes, "userauth": Userauth, "NS": lambda b: b"<" +
                     (b if isinstance(b, bytes) else b.encode()) + b">"}
        exec(compile(body, "<patched userauth>", "exec"), namespace)  # noqa: S102

        class Auth:
            bannerSent = False
            _log = Log()

            class transport:
                @staticmethod
                def sendPacket(kind, payload):
                    sent.append((kind, payload))

        namespace["sendBanner"](Auth())
        return logged, sent

    def test_ssh_without_issue_net_logs_no_traceback(self):
        # routers and cameras have no /etc/issue.net: no SSH banner, as upstream, and no failure
        self.assertEqual(self.send_banner(None), ([("info", ("No /etc/issue.net, no SSH banner",))], []))
        self.assertEqual(self.send_banner(b"Ubuntu 24.04.4 LTS\n"), ([], [(53, b"<Ubuntu 24.04.4 LTS\n><en>")]))
        self.assertEqual(self.send_banner(b"  \n"), ([], []))

    def test_telnet_without_issue_net_logs_no_traceback(self):
        # routers and cameras have no /etc/issue.net: an empty banner, as upstream, but no failure
        factory = (self.patched() / "src/cowrie/telnet/factory.py").read_text()
        self.assertNotIn("self._log.failure", factory)
        self.assertIn('self._log.info("No /etc/issue.net, empty telnet banner")', factory)
        self.assertIn('self.banner = b""', factory)

    def grep(self, *args, **kwargs):
        fs = (self.patched() / "src/cowrie/commands/fs.py").read_text()
        command_grep = module_parts(fs, ["Command_grep", "TpotGrepMatcher"],
                                    {"HoneyPotCommand": FakeCommand})["Command_grep"]
        command = command_grep(args, **kwargs)
        command.start()
        return command

    def test_grep_c_counts_the_matching_lines(self):
        # miner bots ask `grep -c processor /proc/cpuinfo`; v3.1.1 printed the lines
        done = self.grep("-c", "^processor", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.err, done.code), (b"4\n", "", 0))
        done = self.grep("-c", "processor", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"4\n")

    def test_grep_c_without_a_match_prints_0_and_fails(self):
        done = self.grep("-c", "bogomips", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"0\n", 1))

    def test_grep_c_names_each_file_as_given(self):
        done = self.grep("-c", "processor", "/proc/cpuinfo", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"/proc/cpuinfo:4\n/proc/meminfo:0\n", 0))
        done = self.grep("-c", "MemTotal", "cpuinfo", "meminfo", cwd="/proc")
        self.assertEqual(bytes(done.out), b"cpuinfo:0\nmeminfo:1\n")

    def test_grep_c_skips_missing_files_and_directories(self):
        done = self.grep("-c", "processor", "/proc/cpuinfo", "/nope", "/proc")
        self.assertEqual(bytes(done.out), b"/proc/cpuinfo:4\n")
        self.assertIn("grep: /nope: No such file or directory", done.err)
        self.assertIn("Is a directory", done.err)

    def test_grep_c_counts_a_pipe(self):
        done = self.grep("-c", "processor", input_data=CPUINFO)
        self.assertEqual((bytes(done.out), done.code), (b"4\n", 0))

    def test_grep_c_counts_interactive_stdin_at_eof(self):
        done = self.grep("-c", "a")
        self.assertEqual(done.code, "running")
        for line in ("abc", "xyz", "bar"):
            done.lineReceived(line)
        self.assertEqual(bytes(done.out), b"")
        done.eofReceived()
        self.assertEqual((bytes(done.out), done.code), (b"2\n", 0))

    def test_grep_c_stops_at_max_count_per_file(self):
        done = self.grep("-c", "-m", "2", "processor", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"2\n")
        done = self.grep("-m1", "-c", "processor", "/proc/cpuinfo", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"/proc/cpuinfo:1\n/proc/cpuinfo:1\n")

    def test_grep_counts_no_line_after_the_last_newline(self):
        done = self.grep("-c", "", "/proc/meminfo")
        self.assertEqual(bytes(done.out), b"2\n")
        done = self.grep("^", "/proc/meminfo")
        self.assertEqual(bytes(done.out), MEMINFO)

    def test_grep_without_c_still_prints_the_lines(self):
        done = self.grep("processor", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"".join(b"processor\t: %d\n" % n for n in range(4)))
        self.assertEqual(done.code, 0)
        done = self.grep("-m", "1", "processor", input_data=CPUINFO)
        self.assertEqual(bytes(done.out), b"processor\t: 0\n")

    PS = (b"USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\n"
          b"root         1  0.0  0.1 167744 11788 ?        Ss   Oct09   0:04 /sbin/init\n"
          b"root      4211  0.0  0.0   6480  2280 pts/0    S+   10:02   0:00 grep --color=auto xmrig\n")

    def test_grep_v_inverts(self):
        # ps aux | grep -v grep
        done = self.grep("-v", "grep", input_data=self.PS)
        self.assertEqual((bytes(done.out), done.code), (self.PS.rsplit(b"root      4211", 1)[0], 0))
        done = self.grep("-vc", "processor", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.code), (b"8\n", 0))
        done = self.grep("-v", "-m", "1", "processor", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"model name\t: Intel(R) Xeon(R)\n")
        done = self.grep("-v", "", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"", 1))

    def test_grep_i_ignores_case(self):
        done = self.grep("-ci", "PROCESSOR", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.code), (b"4\n", 0))
        done = self.grep("-i", "memtotal", "/proc/meminfo")
        self.assertEqual(bytes(done.out), b"MemTotal:        8023548 kB\n")
        done = self.grep("-vi", "MODEL NAME", "/proc/meminfo")
        self.assertEqual(bytes(done.out), MEMINFO)
        done = self.grep("-c", "PROCESSOR", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.code), (b"0\n", 1))

    def test_grep_q_only_sets_the_status(self):
        # grep -qi "model name" /proc/cpuinfo && echo yes
        done = self.grep("-qi", "model name", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", "", 0))
        done = self.grep("-q", "model name", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"", 1))
        done = self.grep("-qc", "processor", "/proc/cpuinfo", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"", 0))
        done = self.grep("-q", "init", input_data=self.PS)
        self.assertEqual((bytes(done.out), done.code), (b"", 0))
        done = self.grep("-q", "a")
        done.lineReceived("abc")
        done.eofReceived()
        self.assertEqual((bytes(done.out), done.code), (b"", 0))

    def test_grep_exit_status_2_on_errors(self):
        done = self.grep("processor", "/nope")
        self.assertEqual((done.err, done.code), ("grep: /nope: No such file or directory\n", 2))
        # an error wins over a match, unless -q found one
        done = self.grep("processor", "/proc/cpuinfo", "/nope")
        self.assertEqual(done.code, 2)
        done = self.grep("-q", "processor", "/nope", "/proc/cpuinfo")
        self.assertEqual((done.code, done.err), (0, "grep: /nope: No such file or directory\n"))
        done = self.grep("-q", "nothing", "/proc/cpuinfo", "/nope")
        self.assertEqual(done.code, 2)
        done = self.grep("processor", "/proc")
        self.assertEqual(done.code, 2)
        done = self.grep("-c", "MemTotal", "nope", cwd="/proc")
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", "grep: nope: No such file or directory\n", 2))
        done = self.grep("-K", "x", "/proc/cpuinfo")
        self.assertEqual(done.code, 2)
        self.assertIn("grep: invalid option -- K", done.err)

    def grep_timed(self, *args, limit=1.0, **kwargs):
        """grep as above; start() must return within limit seconds."""
        fs = (self.patched() / "src/cowrie/commands/fs.py").read_text()
        command_grep = module_parts(fs, ["Command_grep", "TpotGrepMatcher"],
                                    {"HoneyPotCommand": FakeCommand})["Command_grep"]
        command = command_grep(args, **kwargs)
        begin = time.monotonic()
        command.start()
        self.assertLess(time.monotonic() - begin, limit, args)
        return command

    def matcher(self):
        fs = (self.patched() / "src/cowrie/commands/fs.py").read_text()
        return module_parts(fs, ["TpotGrepMatcher"], {})["TpotGrepMatcher"]

    KEY_LINE = "ssh-rsa " + "".join("AbCdEfGh0123" for _ in range(60)) + " backup@build39"

    def test_grep_takes_long_patterns_up_to_the_automaton_limit(self):
        # a bot that looks for its own authorized_keys line (about 740 characters)
        keys = {"/root/.ssh/authorized_keys": b"ssh-ed25519 AAAA other@host\n" + self.KEY_LINE.encode() + b"\n"}
        done = self.grep("-c", self.KEY_LINE, "/root/.ssh/authorized_keys", files=keys)
        self.assertEqual((bytes(done.out), done.err, done.code), (b"1\n", "", 0))
        done = self.grep("-c", "a" * 4000, "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.err, done.code), (b"0\n", "", 1))
        # 255 nested groups
        done = self.grep("(" * 255 + "M" + ")" * 255, "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.err, done.code), (MEMINFO, "", 0))
        done = self.grep("(", "/proc/cpuinfo")
        self.assertEqual((done.err, done.code), ("grep: Invalid regular expression\n", 2))

    def test_grep_rejects_a_pattern_too_big_for_the_automaton(self):
        too_big = ("grep: regular expression too big\n", 2)
        for pattern in ("a" * 5000, "(a{100}){100}", "(" * 5000 + ")" * 5000, "a" * (64 * 1024 + 1)):
            with self.subTest(pattern=pattern[:16], size=len(pattern)):
                done = self.grep(pattern, "/proc/cpuinfo")
                self.assertEqual((bytes(done.out), done.err, done.code), (b"",) + too_big)

    def test_grep_nested_repeats_return_at_once(self):
        line = b"a" * 50000
        done = self.grep_timed("(a+)+$", input_data=line + b"!\n")
        self.assertEqual((bytes(done.out), done.code), (b"", 1))
        done = self.grep_timed("-c", "(a+)+$", input_data=line + b"\n" + line + b"!\n")
        self.assertEqual((bytes(done.out), done.code), (b"1\n", 0))
        for pattern in ("(a*)*b", "(a|aa)+$", "(a|a)*b", "a*a*a*a*b", "(\\w+\\s?)+$", ".*.*=.*", "\\w+\\s"):
            done = self.grep_timed(pattern, input_data=line + b"!\n")
            self.assertEqual(done.code, 1, pattern)

    def test_grep_reads_at_most_1_mib(self):
        done = self.grep_timed("-c", "x", input_data=b"x\n" * (600 * 1024), limit=2.0)
        self.assertEqual(bytes(done.out), b"%d\n" % (512 * 1024))
        done = self.grep_timed("-c", "x", "/root/big", files={"/root/big": b"x\n" * (600 * 1024)}, limit=2.0)
        self.assertEqual(bytes(done.out), b"%d\n" % (512 * 1024))
        # interactive stdin: the same 1 MiB for all lines together
        done = self.grep("-c", "x")
        for _ in range(1100):
            done.lineReceived("x" * 1023)
        done.eofReceived()
        self.assertEqual((bytes(done.out), done.code), (b"1024\n", 0))

    def test_grep_matches_the_whole_line(self):
        line = b"a" * 70000 + b"needle"
        done = self.grep_timed("needle", input_data=line + b"\nother\n")
        self.assertEqual((bytes(done.out), done.code), (line + b"\n", 0))
        done = self.grep_timed("-v", "needle", input_data=line + b"\nother\n")
        self.assertEqual((bytes(done.out), done.code), (b"other\n", 0))
        done = self.grep_timed("-c", "needle$", input_data=b"x" * (1024 * 1024 - 7) + b"needle\n")
        self.assertEqual(bytes(done.out), b"1\n")

    def test_grep_stops_when_the_work_limit_is_reached(self):
        # a pattern whose automaton needs a new state at almost every byte
        rnd = random.Random(7)
        data = b"".join(bytes(rnd.choice(b"ab") for _ in range(64 * 1024 - 1)) + b"\n" for _ in range(16))
        exhausted = "grep: memory exhausted\n"
        done = self.grep_timed("-c", "a[ab]{20}c", input_data=data, limit=3.0)
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", exhausted, 2))
        # GNU grep stops at once: the lines before are printed, no count, exit status 2
        first = b"a" * 21 + b"c"
        done = self.grep_timed("a[ab]{20}c", input_data=first + b"\n" + data, limit=3.0)
        self.assertEqual((bytes(done.out), done.err, done.code), (first + b"\n", exhausted, 2))
        done = self.grep_timed("-c", "a[ab]{20}c", "/root/one", "/root/two", "/nope",
                               files={"/root/one": data, "/root/two": first + b"\n"}, limit=3.0)
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", exhausted, 2))
        # -q stops reading at the first selected line
        done = self.grep_timed("-q", "a[ab]{20}c", input_data=first + b"\n" + data, limit=3.0)
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", "", 0))
        # interactive stdin: the message once, the status at the end
        done = self.grep("a[ab]{20}c")
        for line in data.split(b"\n")[:16]:
            done.lineReceived(line.decode())
        done.lineReceived(first.decode())
        done.eofReceived()
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", exhausted, 2))
        fs = (self.patched() / "src/cowrie/commands/fs.py").read_text()
        command_grep = module_parts(fs, ["Command_grep", "TpotGrepMatcher"],
                                    {"HoneyPotCommand": FakeCommand})["Command_grep"]
        self.start(command_grep(["-c", "a[ab]{20}c"], input_data=data), quick=False)
        self.assertLess(self.peak, 32 * 1024 * 1024)

    GNU_SAMPLE = (b"processor\t: 0\nmodel name\t: Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz\ncpu MHz\t\t: 2399.998\n"
                  b"MemTotal:        8023548 kB\nSwapFree:              0 kB\n\nflags\t\t: fpu vme de pse tsc msr\n"
                  b"aaaa\naaaa!\nxababy xy xcdy\nroot:x:0:0:root:/root:/bin/bash\nbooted foo\ncpus 4\n")
    # line numbers selected by GNU grep 3.8 (debian:12) as `grep -E -n PATTERN`
    GNU_LINES = {"^processor": [1], "model name": [2], "[0-9]+ kB$": [4, 5], "^(Mem|Swap)(Total|Free)": [4, 5],
                 "\\bcpu\\b": [3], "(a+)+$": [8], "x(ab|cd)*y": [10], "^$": [6], "o{2,}": [11, 12],
                 "^[^:]+:x:0:": [11], "(\\w+\\s?)+$": [1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 13], "PROCESSOR": []}

    def test_grep_selects_the_lines_gnu_grep_selects(self):
        lines = self.GNU_SAMPLE.split(b"\n")
        for pattern, numbers in self.GNU_LINES.items():
            with self.subTest(pattern=pattern):
                done = self.grep(pattern, input_data=self.GNU_SAMPLE)
                self.assertEqual(bytes(done.out), b"".join(lines[n - 1] + b"\n" for n in numbers))
                self.assertEqual(done.code, 0 if numbers else 1)
        done = self.grep("-i", "PROCESSOR", input_data=self.GNU_SAMPLE)
        self.assertEqual(bytes(done.out), b"processor\t: 0\n")

    def test_grep_selects_the_lines_python_re_selects(self):
        # the automaton against Python's own matcher, on patterns and lines small enough for both
        matcher = self.matcher()
        rnd = random.Random(3)
        atoms = ["a", "b", "A", ".", "[ab]", "[^a]", "[a-c]", "\\d", "\\w", "\\s", "\\W", "\\S", "\\D", "[^\\s]",
                 "[A-Z]", "x", " ", "1", "_", "\\.", "[^A-Z_]"]

        def pattern(depth=0):
            r = rnd.random()
            if depth > 3 or r < 0.3:
                return rnd.choice(atoms)
            if r < 0.5:
                return pattern(depth + 1) + pattern(depth + 1)
            if r < 0.6:
                return "(" + pattern(depth + 1) + "|" + pattern(depth + 1) + ")"
            if r < 0.75:
                return "(" + pattern(depth + 1) + ")" + rnd.choice(["*", "+", "?", "*?", "+?", "{2}", "{1,3}", "{2,}"])
            if r < 0.85:
                return rnd.choice(["^", "$", "\\b", "\\B", "\\A", "\\Z"]) + pattern(depth + 1)
            if r < 0.9:
                return rnd.choice(["(?i:", "(?:", "(?P<n>"]) + pattern(depth + 1) + ")"
            return pattern(depth + 1) + rnd.choice(["^", "$", "\\b", "\\B"])

        checked = 0
        for _ in range(1500):
            text = pattern().encode()
            ignore_case = rnd.random() < 0.25
            try:
                expected = re.compile(text, re.IGNORECASE if ignore_case else 0)
            except re.error:
                continue  # a group name used twice
            found = matcher(text, ignore_case)
            for _ in range(10):
                line = bytes(rnd.choice(b"aAbB1 _x.\t-") for _ in range(rnd.randint(0, 8)))
                self.assertEqual(found.search(line) is not None, expected.search(line) is not None,
                                 (text, ignore_case, line))
                checked += 1
        self.assertGreater(checked, 10000)
        for text in (rb"\w", rb"\W", rb"\s", rb"\S", rb"\d", rb"\D", rb".", rb"(?i)[a-f]", rb"(?i)[^a-f]",
                     rb"(?i)[Z-a]", rb"\b", rb"\B", rb"(?i)k", rb"[^\x00-\x7f]"):
            found, expected = matcher(text), re.compile(text)
            for byte in range(256):
                for line in (bytes([byte]), bytes([byte, 97]), bytes([97, byte]), b""):
                    self.assertEqual(found.search(line) is not None, expected.search(line) is not None,
                                     (text, line))

    def self_test(self, root):
        return subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root), "--self-test"],
                              capture_output=True, text=True)

    def test_grep_self_test_passes_on_the_patched_source(self):
        done = self.self_test(self.patched())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stdout, r"^grep matcher self-test: \d+ patterns OK\n$")

    def test_grep_self_test_fails_loudly(self):
        # a Python whose re parser moved, a construct the matcher no longer knows, a wrong result, no size limit
        cases = (("        from re import _parser\n", "        from re import _parser_gone as _parser\n"),
                 ("        flags = parsed.state.flags\n", "        flags = parsed.flags\n"),
                 (", sre.AT_NON_BOUNDARY: 3}[av]", "}[av]"),
                 ("sre.CATEGORY_NOT_WORD: sre.CATEGORY_WORD}", "}"),
                 ("        return ((prev == 1) != (nxt == 1)) == (test == 2)\n", "        return test == 2\n"),
                 ("    MAX_STATES = 4096 ", "    MAX_STATES = 1 << 20 "))
        for old, new in cases:
            with self.subTest(old=old):
                root = self.patched()
                path = root / "src/cowrie/commands/fs.py"
                text = path.read_text()
                self.assertEqual(text.count(old), 1)
                path.write_text(text.replace(old, new))
                done = self.self_test(root)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("grep matcher self-test failed", done.stderr)

    def test_grep_self_test_covers_every_construct_of_the_matcher(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tpot_patch_script", PATCH)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        from re import _parser
        seen = set()

        def walk(items):
            for op, av in items:
                seen.add(str(op))
                if str(op) == "IN":
                    seen.update(f"IN {kind}" for kind, _ in av)
                    seen.update(str(value) for kind, value in av if str(kind) == "CATEGORY")
                elif str(op) == "AT":
                    seen.add(str(av))
                elif str(op) in ("MAX_REPEAT", "MIN_REPEAT"):
                    walk(av[2])
                elif str(op) == "SUBPATTERN":
                    seen.add("SUBPATTERN flags" if av[1] or av[2] else "SUBPATTERN")
                    walk(av[3])
                elif str(op) == "BRANCH":
                    for alternative in av[1]:
                        walk(alternative)

        for pattern, ignore_case in script.GREP_SELF_TEST:
            walk(_parser.parse(pattern, re.IGNORECASE if ignore_case else 0))
        self.assertTrue(any(ignore_case for _, ignore_case in script.GREP_SELF_TEST))
        self.assertLessEqual({"LITERAL", "NOT_LITERAL", "ANY", "IN", "IN LITERAL", "IN RANGE", "IN CATEGORY",
                              "IN NEGATE", "MAX_REPEAT", "MIN_REPEAT", "SUBPATTERN", "SUBPATTERN flags", "BRANCH",
                              "AT", "AT_BEGINNING", "AT_BEGINNING_STRING", "AT_END", "AT_END_STRING", "AT_BOUNDARY",
                              "AT_NON_BOUNDARY", "CATEGORY_DIGIT", "CATEGORY_NOT_DIGIT", "CATEGORY_SPACE",
                              "CATEGORY_NOT_SPACE", "CATEGORY_WORD", "CATEGORY_NOT_WORD"}, seen)

    def test_grep_warns_once_about_a_possible_nested_set(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            done = self.grep("[[t]pot-warn", "/proc/cpuinfo")
        self.assertEqual(done.code, 1)
        self.assertEqual([str(w.message) for w in caught if w.category is FutureWarning],
                         ["Possible nested set at position 1"])

    def test_grep_selects_no_line_for_patterns_beyond_regular_expressions(self):
        # back references, lookarounds, atomic groups, possessive repeats and conditionals
        for pattern in ("(a)\\1", "(?=p)p", "(?!x)p", "(?<=o)c", "(?>p)", "p++", "(a)?(?(1)a|p)"):
            with self.subTest(pattern=pattern):
                done = self.grep(pattern, "/proc/cpuinfo")
                self.assertEqual((bytes(done.out), done.err, done.code), (b"", "", 1))
                done = self.grep("-vc", pattern, "/proc/cpuinfo")
                self.assertEqual(bytes(done.out), b"12\n")

    def prompt(self, cwd, user="root", uid=0, home="/root", style="redhat", hostname="build39.corp.local",
               config=None):
        import posixpath
        honeypot = (self.patched() / "src/cowrie/shell/honeypot.py").read_text()
        values = {} if style is None else {("shell", "prompt_style"): style}
        show_prompt = method(honeypot, "HoneyPotShell", "showPrompt",
                             {"CowrieConfig": FakeConfig(values if config is None else config),
                              "posixpath": posixpath})
        written = []

        class Terminal:
            def write(self, data):
                written.append(data)

        class Protocol:
            terminal = Terminal()
            ps = None

        Protocol.hostname = hostname

        class Shell:
            interactive = True
            protocol = Protocol()

        shell = Shell()
        shell.user = {"username": user, "uid": uid, "home": home}
        shell.cwd = cwd
        show_prompt(shell)
        self.assertEqual(shell.protocol.ps, (written[0], b"> "))
        return written[0].decode("utf-8")

    SHELL_ERRORS = {"openwrt": "-ash: x: not found\n", "dlink-dir859": "-sh: x: not found\n",
                    "tplink-wr841n": "-sh: x: not found\n", "zyxel-nas326": "-sh: x: not found\n",
                    "qnap-qts": "-sh: x: not found\n", "ipcam": "-sh: x: not found\n",
                    "synology-dsm": "-sh: x: command not found\n", "ubiquiti-edgerouter-x": "-vbash: x: command not found\n",
                    "ubuntu-noble": "-bash: x: command not found\n", "rhel-9": "-bash: x: command not found\n"}

    def shell_error(self, pid, interactive=True):
        from tpot_personas.identity import draw
        from tpot_personas.render import render_cfg
        persona = catalog.by_id(pid)
        text = render_cfg(persona, draw(persona, 1), Path("/tmp/cowrie/run"), Path("/keys"))
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(text)
        config = FakeConfig({("shell", key): value for key, value in cfg.items("shell")})
        source = (self.patched() / "src/cowrie/shell/honeypot.py").read_text()
        names = {"CowrieConfig": config}
        message = method(source, "HoneyPotShell", "command_not_found_message", names)
        prefix = method(source, "HoneyPotShell", "error_prefix", names)
        login = config.get("shell", "shell_name", fallback="bash")

        class Shell:
            name = f"-{login}" if interactive else login
            line_numbers = not interactive
            syntax_label = None if interactive else "-c"
            current_line = 1
            error_prefix = prefix

        return message(Shell(), "x")

    def test_not_found_names_the_shell_of_the_persona(self):
        for pid, expected in self.SHELL_ERRORS.items():
            self.assertEqual(self.shell_error(pid), expected, pid)
        self.assertEqual(self.shell_error("openwrt", interactive=False), "ash: x: not found\n")
        self.assertEqual(self.shell_error("tplink-wr841n", interactive=False), "sh: x: not found\n")
        self.assertEqual(self.shell_error("rhel-9", interactive=False), "bash: line 1: x: command not found\n")
        source = (self.patched() / "src/cowrie/shell/honeypot.py").read_text()
        self.assertIn('login = CowrieConfig.get("shell", "shell_name", fallback="bash")', source)
        self.assertIn('self.name = f"-{login}" if interactive else login', source)

    def test_redhat_prompt(self):
        # bash PS1 of RHEL / CentOS: [\u@\h \W]\$ (short host name, basename of the cwd)
        self.assertEqual(self.prompt("/root"), "[root@build39 ~]# ")
        self.assertEqual(self.prompt("/"), "[root@build39 /]# ")
        self.assertEqual(self.prompt("/var/log"), "[root@build39 log]# ")
        self.assertEqual(self.prompt("/root/bin"), "[root@build39 bin]# ")
        self.assertEqual(self.prompt("/home/cloud-user", "cloud-user", 1000, "/home/cloud-user"),
                         "[cloud-user@build39 ~]$ ")
        self.assertEqual(self.prompt("/tmp", "cloud-user", 1000, "/home/cloud-user", hostname="rhel9-web12"),
                         "[cloud-user@rhel9-web12 tmp]$ ")

    def test_debian_prompt_stays_upstream(self):
        for style in (None, "debian", "anything else"):
            with self.subTest(style=style):
                self.assertEqual(self.prompt("/root", style=style), "root@build39.corp.local:~# ")
                self.assertEqual(self.prompt("/var/log", style=style), "root@build39.corp.local:/var/log# ")
                self.assertEqual(self.prompt("/home/pi/src", "pi", 1000, "/home/pi", style=style, hostname="pihole"),
                                 "pi@pihole:~/src$ ")

    def test_configured_prompt_still_wins(self):
        config = {("honeypot", "prompt"): "$", ("shell", "prompt_style"): "redhat"}
        self.assertEqual(self.prompt("/root", config=config), "$ ")

    def banner(self, issue, os_release=None, config=None):
        factory = (self.patched() / "src/cowrie/telnet/factory.py").read_text()
        values = {("honeypot", "hostname"): "build39.corp.local",
                  ("shell", "kernel_version"): "5.14.0-427.13.1.el9_4.x86_64",
                  ("shell", "kernel_build_string"): "#1 SMP PREEMPT_DYNAMIC Wed Apr 10 10:29:16 EDT 2024",
                  ("shell", "hardware_platform"): "x86_64"}
        files = {} if os_release is None else {"etc/os-release": os_release}
        asked = []

        def read_honeyfs_bytes(relpath):
            asked.append(relpath)
            if relpath not in files:
                raise FileNotFoundError(relpath)
            return files[relpath]

        namespace = module_parts(factory, ["tpot_expand_issue", "tpot_os_release"],
                                 {"CowrieConfig": FakeConfig(values if config is None else config),
                                  "read_honeyfs_bytes": read_honeyfs_bytes})
        return namespace["tpot_expand_issue"](issue), asked

    RHEL_OS_RELEASE = (b'NAME="Red Hat Enterprise Linux"\nVERSION="9.4 (Plow)"\nID="rhel"\n'
                       b'PRETTY_NAME="Red Hat Enterprise Linux 9.4 (Plow)"\nANSI_COLOR="0;31"\n')

    def test_telnet_banner_expands_the_issue_net_escapes(self):
        text, asked = self.banner("\\S\nKernel \\r on an \\m\n", self.RHEL_OS_RELEASE)
        self.assertEqual(text, "Red Hat Enterprise Linux 9.4 (Plow)\n"
                               "Kernel 5.14.0-427.13.1.el9_4.x86_64 on an x86_64\n")
        self.assertEqual(asked, ["etc/os-release"])

    def test_telnet_banner_knows_the_agetty_escapes(self):
        text, _ = self.banner("\\s|\\n|\\v|\\l|\\\\|\\S{ID}|\\S{NOPE}|\\d \\t \\O \\4|\\",
                              self.RHEL_OS_RELEASE)
        self.assertEqual(text, "Linux|build39.corp.local|#1 SMP PREEMPT_DYNAMIC Wed Apr 10 10:29:16 EDT 2024"
                               "|pts/0|\\|rhel||\\d \\t \\O \\4|\\")

    def test_telnet_banner_without_os_release_says_linux(self):
        text, asked = self.banner("\\S \\r", None)
        self.assertEqual(text, "Linux 5.14.0-427.13.1.el9_4.x86_64")
        self.assertEqual(asked, ["etc/os-release", "usr/lib/os-release"])
        text, _ = self.banner("\\S", b'NAME="CentOS Linux"\n')
        self.assertEqual(text, "Linux")

    def test_telnet_banner_without_escapes_reads_nothing(self):
        text, asked = self.banner("Ubuntu 24.04.4 LTS\n", self.RHEL_OS_RELEASE)
        self.assertEqual((text, asked), ("Ubuntu 24.04.4 LTS\n", []))

    def test_telnet_banner_is_expanded_once_at_start(self):
        factory = (self.patched() / "src/cowrie/telnet/factory.py").read_text()
        self.assertIn("from cowrie.core.config import CowrieConfig", factory)
        self.assertIn('tpot_expand_issue(\n                read_honeyfs_bytes("etc/issue.net")', factory)

    def test_fails_loudly_on_unknown_upstream(self):
        cases = (("src/cowrie/shell/protocol.py", "for x in paths if x", "for x in paths"),
                 ("src/cowrie/commands/yum.py", "f\"---> Package {p}.{self.packages[p]['version']} {arch}.",
                  "f\"---> Package {p}.{self.packages[p]['version']}  {arch}."),
                 ("src/cowrie/commands/yum.py", "{random.randint(1, 40)}-{random.randint(1, 10)}",
                  "{random.randint(1, 40)}-{random.randint(1, 9)}"),
                 ("src/cowrie/commands/yum.py", "f\"  {p}.{arch} {random.randint(0, 2)}:",
                  "f\"  {p}.{arch} {random.randint(0, 3)}:"),
                 ("src/cowrie/commands/yum.py", 'self.errorWrite("ovl: Error while doing RPMdb copy-up:\\n")',
                  'self.errorWrite("ovl: Error while doing copy-up:\\n")'),
                 ("src/cowrie/commands/apt.py", 'commands["/bin/apt"] = Command_aptget\n',
                  'commands["/bin/apt"] = Command_aptget  # apt\n'),
                 ("src/cowrie/commands/free.py", 'open("/proc/meminfo")', 'open("/proc/meminfo", "rb")'),
                 ("src/cowrie/telnet/factory.py", "Failed to load /etc/issue.net", "No /etc/issue.net"),
                 ("src/cowrie/commands/service.py", "        for line in output:", "        for row in output:"),
                 ("src/cowrie/commands/fs.py", "                self.writeBytes(line + b\"\\n\")",
                  "                self.write(line)"),
                 ("src/cowrie/commands/fs.py", "            for pname in self.check_arguments(\"grep\", files):",
                  "            for pname in files:"),
                 ("src/cowrie/commands/fs.py", "            self.grep_application(self.input_data, self.match)\n",
                  "            self.grep_application(self.input_data, match=self.match)\n"),
                 ("src/cowrie/commands/fs.py", "            if opt == \"-h\":", "            if opt in (\"-h\",):"),
                 ("src/cowrie/commands/fs.py", "self.grep_application(line.encode(\"utf8\"), self.match)",
                  "self.grep_application(line.encode(), self.match)"),
                 ("src/cowrie/commands/fs.py", "self.grep_application(f.read(), self.match)\n        self.exit(",
                  "self.grep_application(f.read(), self.match)\n        return self.exit("),
                 ("src/cowrie/commands/fs.py", "            self.grep_application(contents, match)\n",
                  "            self.grep_application(contents, match=match)\n"),
                 ("src/cowrie/telnet/factory.py", "from cowrie.shell.honeyfs import read_honeyfs_bytes",
                  "from cowrie.shell import honeyfs"),
                 ("src/cowrie/telnet/factory.py", "\nimport time\n", "\nimport time as clock\n"),
                 ("src/cowrie/commands/fs.py", "        return re.compile(bmatch)\n",
                  "        return re.compile(bmatch, 0)\n"),
                 ("src/cowrie/commands/fs.py", "    def compile_match(self, match: str) -> re.Pattern[bytes]:\n",
                  "    def compile_match(self, match: str) -> re.Pattern:\n"),
                 ("src/cowrie/commands/fs.py", "class Command_grep(HoneyPotCommand):\n",
                  "class Command_grep(HoneyPotCommand):  # grep\n"),
                 ("src/cowrie/commands/fs.py",
                  "            self.compile_match(self.match)\n        except re.error:\n",
                  "            self.compile_match(self.match)\n        except (re.error, ValueError):\n"),
                 ("src/cowrie/commands/fs.py", "            if matcher.search(line):\n",
                  "            if matcher.search(line) is not None:\n"),
                 ("src/cowrie/commands/fs.py",
                  "            self.errorWrite(f\"grep: invalid option -- {err.opt}\\n\")\n            self.help()\n",
                  "            self.errorWrite(f\"grep: invalid option -- {err.opt}\\n\")\n"),
                 ("src/cowrie/commands/fs.py",
                  "            self.interactive = True\n            return\n\n        self.exit(0 if",
                  "            self.interactive = True\n            return\n        self.exit(0 if"),
                 ("src/cowrie/shell/honeypot.py",
                  "            prompt = f\"{username}@{self.protocol.hostname}:{cwd}\"\n",
                  "            prompt = f\"{username}@{self.protocol.hostname}:{cwd} \"\n"),
                 ("src/cowrie/shell/protocol.py", "                return self.txtcmd(operator_path.read_bytes())",
                  "                return self.txtcmd(operator_path.read_text())"),
                 ("src/cowrie/commands/busybox.py", "        if len(self.args) == 0:\n",
                  "        if not self.args:\n"),
                 ("src/cowrie/commands/perl.py", "Copyright 1987-2014, Larry Wall", "Copyright 1987-2015, Larry Wall"),
                 ("src/cowrie/commands/python.py", 'ver = "Python 2.7.11+"', 'ver = "Python 2.7.18"'),
                 ("src/cowrie/commands/python.py", "import getopt\n", "import getopt as options\n"),
                 ("src/cowrie/commands/python.py", "commands = {}\n", "commands: dict = {}\n"),
                 ("src/cowrie/commands/python.py", '                self.args, "BdEhiORsStuvVx3c:m:Q:W:", ["help", "version"]\n',
                  '                self.args, "BdEhiORsStuvVx3c:m:Q:W:", ["help"]\n'),
                 ("src/cowrie/commands/perl.py", "import getopt\n", "import getopt as options\n"),
                 ("src/cowrie/commands/perl.py", '                self.args, "acfhnpsStTuUvwWXC:D:e:E:F:i:I:l:m:M:V:X:"\n',
                  '                self.args, "acfhnpsStTuUvwWXC:D:e:E:F:i:I:l:m:M:V:"\n'),
                 ("src/cowrie/commands/git.py", 'self.write("git version 2.34.1\\n")',
                  'self.write("git version 2.43.0\\n")'),
                 ("src/cowrie/commands/busybox.py", 'self.write(f"{cmd}: applet not found\\n")',
                  'self.errorWrite(f"{cmd}: applet not found\\n")'),
                 ("src/cowrie/commands/which.py", "                if self.fs.exists(resolved):\n",
                  "                if self.fs.isfile(resolved):\n"),
                 ("src/cowrie/commands/sudo.py", "import getopt\n", "import getopt as options\n"),
                 ("src/cowrie/commands/sudo.py", "commands = {}\n", "commands: dict = {}\n"),
                 ("src/cowrie/shell/honeypot.py", '            self.name = "-bash" if interactive else "bash"\n',
                  '            self.name = "-bash" if interactive else "sh"\n'),
                 ("src/cowrie/shell/honeypot.py", '        return f"{self.error_prefix()}{cmd}: command not found\\n"\n',
                  '        return f"{self.error_prefix()}{cmd}: not found\\n"\n'),
                 ("src/cowrie/shell/honeypot.py", '            return f"{self.name}: line {self.current_line}: "\n',
                  '            return f"{self.name}: {self.current_line}: "\n'),
                 ("src/cowrie/shell/fs.py", "            # cwd = '/'.join((cwd, piece))\n",
                  "            # cwd = posixpath.join(cwd, piece)\n"),
                 ("src/cowrie/shell/fs.py", '                                "/".join((cwd, x[A_TARGET])),\n',
                  '                                posixpath.join(cwd, x[A_TARGET]),\n'),
                 ("src/cowrie/commands/sudo.py", "                self.exec_command(pp, cmdclass, *parsed_arguments[1:])\n",
                  "                self.exec_command(pp, cmdclass, *parsed_arguments[1:])  # exec\n"),
                 ("src/cowrie/commands/sudo.py", 'self.args[0:start_value], "bEeHhKknPSVva:C:g:i:l:p:r:s:t:U:u:"',
                  'self.args[0:start_value], "bEeHhKknPSVva:C:g:i:l:p:r:s:t:U:u:R:"'),
                 ("src/cowrie/commands/sudo.py", "    def start(self) -> None:\n        start_value = None\n",
                  "    def start(self) -> None:\n        start_value: int | None = None\n"),
                 ("src/cowrie/commands/apt.py", "import random\n", "import random as rnd\n"),
                 ("src/cowrie/commands/yum.py", "import hashlib\nimport random\nimport re\n",
                  "import hashlib\nimport random\nimport re as regex\n"),
                 ("src/cowrie/commands/yum.py", 'commands["/usr/bin/yum"] = Command_yum\n',
                  'commands["/usr/bin/yum"] = Command_yum  # yum\n'),
                 ("src/cowrie/commands/apt.py", "from typing import TYPE_CHECKING, Any\n",
                  "from typing import Any, TYPE_CHECKING\n"),
                 ("src/cowrie/commands/apt.py", 'self.write("Processing triggers for man-db ...\\n")',
                  'self.write("Processing triggers for man-db...\\n")'),
                 ("src/cowrie/commands/service.py", "import getopt\n", "import getopt as options\n"),
                 ("src/cowrie/commands/service.py", 'self.args, "h", ["help", "status-all", "full-restart"]',
                  'self.args, "hv", ["help", "status-all", "full-restart"]'),
                 ("src/cowrie/commands/service.py", 'commands["service"] = Command_service\n',
                  'commands["service"] = Command_service  # service\n'),
                 ("src/cowrie/ssh/factory.py", "\n\nclass CowrieSSHFactory(factory.SSHFactory):\n",
                  "\n\nclass CowrieSSHFactory(factory.SSHFactory):  # factory\n"),
                 ("src/cowrie/ssh/factory.py", "        t.supportedPublicKeys = list(self.privateKeys.keys())\n",
                  "        t.supportedPublicKeys = list(self.privateKeys)\n"),
                 ("src/cowrie/ssh/userauth.py", '            self._log.failure("ERROR: Failed to load /etc/issue.net")\n',
                  '            self._log.failure("Failed to load /etc/issue.net")\n'),
                 ("src/cowrie/shell/protocol.py", 'self.terminal.write(b"timed out waiting for input: auto-logout\\n")\n',
                  'self.terminal.write(b"timed out waiting for input: auto-logout\\r\\n")\n'),
                 ("src/cowrie/shell/protocol.py", "import functools\nimport random\n",
                  "import functools\nimport os\nimport random\n"),
                 ("src/cowrie/shell/protocol.py", "        return time.time() - self.boot_time()\n",
                  "        return time.monotonic() - self.boot_time()\n"),
                 ("src/cowrie/shell/protocol.py", 'self.fs.generated_files["/proc/uptime"] = self.proc_uptime\n',
                  'self.fs.generated_files["/proc/uptime"] = self.proc_uptime  # uptime\n'),
                 ("src/cowrie/shell/protocol.py", "        self.resetTimeout()  # Reset the idle timeout\n",
                  "        self.resetTimeout()\n"),
                 ("src/cowrie/shell/protocol.py",
                  "    def keystrokeReceived(self, keyID, modifier):\n        if not self.stdin_line_mode:\n",
                  "    def keystrokeReceived(self, keyID, modifier):\n        if self.stdin_line_mode is False:\n"),
                 ("src/cowrie/commands/uptime.py", '"{}  up {},  1 user,  load average: 0.00, 0.00, 0.00\\n".format(',
                  '"{} up {},  1 user,  load average: 0.00, 0.00, 0.00\\n".format('),
                 ("src/cowrie/commands/base.py", "import getopt\nimport random\nimport re\n",
                  "import getopt\nimport random as rnd\nimport re\n"),
                 ("src/cowrie/commands/base.py", "from cowrie.core import utils\n", "from cowrie.core import utils as u\n"),
                 ("src/cowrie/commands/base.py", "\n\nclass Command_ps(HoneyPotCommand):\n",
                  "\n\nclass Command_ps(HoneyPotCommand):  # ps\n"),
                 ("src/cowrie/commands/base.py", "                s = s[: self.columns()]\n",
                  "                s = s[: self.columns() - 1]\n"),
                 ("src/cowrie/commands/base.py", 'commands["ps"] = Command_ps\n', 'commands["ps"] = Command_ps  # ps\n'),
                 ("src/cowrie/commands/fs.py", 'commands["/bin/fgrep"] = Command_grep\n',
                  'commands["/bin/fgrep"] = Command_grep  # fgrep\n'),
                 ("src/cowrie/commands/fs.py", "import copy\nimport getopt\n", "import copy\nimport getopt as g\n"),
                 ("src/cowrie/commands/awk.py", 'commands["awk"] = Command_awk\n',
                  'commands["awk"] = Command_awk  # awk\n'),
                 ("src/cowrie/commands/wc.py", 'commands["/bin/wc"] = Command_wc\n',
                  'commands["/bin/wc"] = Command_wc  # wc\n'),
                 ("src/cowrie/commands/fs.py", "from cowrie.shell import fs\n", "from cowrie.shell import fs as f\n"),
                 ("src/cowrie/commands/fs.py", "from typing import TYPE_CHECKING\n",
                  "from typing import TYPE_CHECKING, Any\n"),
                 ("src/cowrie/shell/protocol.py", "        if path in self.commands:\n            return self.commands[path]\n",
                  "        if path in self.commands:\n            return self.commands.get(path)\n"),
                 ("src/cowrie/commands/base.py", "1 user,  load average: 0.00, 0.00, 0.00\\n\"\n        )\n",
                  "1 user, load average: 0.00, 0.00, 0.00\\n\"\n        )\n"))
        for rel, old, new in cases:
            with self.subTest(rel=rel, needle=old):
                root = tmpdir(self) / "cowrie"
                shutil.copytree(FIXTURE, root)
                path = root / rel
                text = path.read_text()
                self.assertEqual(text.count(old), 1)
                path.write_text(text.replace(old, new))
                done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                      capture_output=True, text=True)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("Could not find expected text", done.stderr)

    def test_fails_loudly_on_a_needle_found_twice(self):
        root = tmpdir(self) / "cowrie"
        shutil.copytree(FIXTURE, root)
        path = root / "src/cowrie/commands/fs.py"
        path.write_text(path.read_text() + '#            self.grep_application(line.encode("utf8"), self.match)\n')
        done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                              capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("more than once", done.stderr)


if __name__ == "__main__":
    unittest.main()
