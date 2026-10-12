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
            raise FileNotFoundError(path)
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
        node = tree.find_node(self.fs, path)
        if follow_symlinks and node is not None and node[tree.A_TYPE] == tree.T_LINK:
            node = tree.find_node(self.fs, node[tree.A_TARGET])
        return node

    def exists(self, path):
        return self.getfile(path) is not None

    def isfile(self, path):
        node = self.getfile(path)
        return node is not None and node[tree.A_TYPE] == tree.T_FILE


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

    def lookup(self, txtcmds, commands, skip=""):
        """getCommand of the patched protocol.py on base_tree() (bin -> usr/bin) with these
        txtcmds files and Python commands; returns a function name -> what it found."""
        root = tmpdir(self)
        for rel, text in txtcmds.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        protocol = (self.patched() / "src/cowrie/shell/protocol.py").read_text()

        def read_data_bytes(*parts):
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

    def twisted_command(self, module, cls, values, args, uid=0):
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
        command.sleep = lambda *a, **k: None

        class Fs:
            def mkfile(self, *a, **k):
                pass

        command.fs = Fs()
        command.protocol.commands = {}
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

    def apt_with_packages(self, packages, fallback="ubuntu", available=None):
        path = tmpdir(self) / "apt-packages.json"
        path.write_text(json.dumps({"installed": packages, "available": available or {}}))
        return {**self.APT, ("shell", "apt_packages"): str(path), ("shell", "apt_fallback"): fallback}

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
            fs = TreeFs(base_tree())

            def __init__(self):
                self.cmdstack = [Shell()]
                self.pp = Pipe(None, None, [], None, {})

            def boot_time(self):
                return time.time() - 86400

            def getCommand(self, cmd, paths, cwd):
                return commands.get(cmd)

            def end_process(self, code):
                pass

        Protocol.call_command = call_command
        protocol = Protocol()

        def run(line):
            words = line.split()
            pp = Pipe(None, None, words[1:], None, {})
            protocol.call_command(pp, commands[words[0]], *words[1:])
            child = protocol.pp
            return bytes(child.out).decode(), bytes(child.err).decode(), protocol.cmdstack[-1].last_exit_code

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
                raise FileNotFoundError(path)

        class Command:
            fs = FakeFs()

        self.assertEqual(get_free_stats(Command()), {})

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
                 ("src/cowrie/commands/apt.py", "from typing import TYPE_CHECKING, Any\n",
                  "from typing import Any, TYPE_CHECKING\n"),
                 ("src/cowrie/commands/apt.py", 'self.write("Processing triggers for man-db ...\\n")',
                  'self.write("Processing triggers for man-db...\\n")'),
                 ("src/cowrie/commands/service.py", "import getopt\n", "import getopt as options\n"),
                 ("src/cowrie/commands/service.py", 'self.args, "h", ["help", "status-all", "full-restart"]',
                  'self.args, "hv", ["help", "status-all", "full-restart"]'),
                 ("src/cowrie/commands/service.py", 'commands["service"] = Command_service\n',
                  'commands["service"] = Command_service  # service\n'))
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
