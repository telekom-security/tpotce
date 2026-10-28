"""$(( )) of the shell of the persona (patch_shell_arith): the evaluator against the recorded shells
(fixtures/shell_arith.json: bash 5.1 and 5.2 and BusyBox ash 1.25, 1.33 and 1.36 in containers) and the hooks
that end a command list at an error."""
import asyncio
import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig

REF = json.loads((Path(__file__).parent / "fixtures" / "shell_arith.json").read_text(encoding="utf-8"))
MSH = json.loads((Path(__file__).parent / "fixtures" / "msh.json").read_text(encoding="utf-8"))
FULL_BASHPARSE = Path(__file__).parent / "fixtures" / "v3.1.3-full" / "bashparse.py"
BASH_IMAGES = ("debian_12", "debian_13", "ubuntu_22.04", "ubuntu_24.04", "rockylinux_9")
ASH_IMAGES = {"busybox_1.25.1": "BusyBox v1.25.1", "busybox_1.33.1": "BusyBox v1.33.1",
              "busybox_1.36.1-musl": "BusyBox v1.36.1"}
_SCRIPT = None


class Patched:
    """A patched root of this module only (a root another module caches would go with its cleanup)."""

    root = None

    @classmethod
    def get(cls):
        if cls.root is None:
            handle = tempfile.TemporaryDirectory()
            unittest.addModuleCleanup(handle.cleanup)
            unittest.addModuleCleanup(setattr, cls, "root", None)
            root = Path(handle.name) / "cowrie"
            shutil.copytree(FIXTURE, root)
            subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                           check=True, capture_output=True, text=True)
            cls.root = root
        return cls.root


def script():
    global _SCRIPT
    if _SCRIPT is None:
        spec = importlib.util.spec_from_file_location("tpot_patch_arith", PATCH)
        _SCRIPT = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_SCRIPT)
    return _SCRIPT


def arith_names(values=None):
    """The names the SHELL_ARITH block defines, run with a fake [shell] section."""
    namespace = {"re": re, "CowrieConfig": FakeConfig(values or {}), "tpot_shell_pid": lambda context: "4242"}
    exec(compile(script().SHELL_ARITH, "<arith>", "exec"), namespace)  # noqa: S102
    return namespace


class Unsupported(Exception):
    """A case the small shell of this test does not run (pipes, loops, files)."""


class MiniShell:
    """Just enough of a shell to run the recorded cases that are lists of assignments and echo with $(( )), $( ),
    $name and quotes; what is more raises Unsupported. A bash error ends the rest of its line, a BusyBox ash
    error the shell."""

    def __init__(self, names, prefix, ash, shared=None):
        self.names, self.prefix, self.ash = names, prefix, ash
        self.environ, self.status, self.line, self.base = {}, 0, 1, 1
        self.out, self.err, self.stopped = [], [], False
        if shared is not None:
            self.environ = dict(shared.environ)
            self.err, self.line, self.base = shared.err, shared.line, shared.line

    # the context of the evaluator
    def get_variable(self, name):
        return self.environ.get(name)

    def get_status(self):
        return str(self.status)

    async def command_substitution(self, source):
        child = MiniChild(self.names, self.prefix, self.ash, self)
        await child.run(source)
        self.substatus = child.status
        return "".join(child.out).rstrip("\n")

    async def run(self, text):
        if "<<" in text or self.operator(text):
            raise Unsupported
        for number, line in enumerate(text.split("\n")):
            self.line = self.base + number
            dropped = False
            for statement in self.split(line):
                if dropped or self.stopped:
                    break
                try:
                    await self.statement(statement)
                except self.names["TpotArithError"] as error:
                    self.err.append(f"{self.prefix(self.line)}{error.message}\n")
                    self.status = error.status
                    dropped = True
                    self.stopped = self.ash and not isinstance(self, MiniChild)

    @staticmethod
    def operator(text):
        """Whether text has a pipe, redirection, group or backslash outside quotes and $( )."""
        depth, quote = 0, ""
        for index, char in enumerate(text):
            if quote:
                quote = "" if char == quote else quote
            elif char in "'\"`":
                quote = char
            elif char == "(" and index and text[index - 1] == "$":
                depth += 1
            elif depth and char == "(":
                depth += 1
            elif depth and char == ")":
                depth -= 1
            elif not depth and char in "|&<>(){}\\":
                return True
        return False

    @staticmethod
    def split(line):
        statements, depth, quote, current, index = [], 0, "", [], 0
        while index < len(line):
            char = line[index]
            if quote:
                if char == quote:
                    quote = ""
            elif char in "'\"":
                quote = char
            elif char == "`":
                quote = "`"
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == ";" and depth == 0:
                statements.append("".join(current))
                current = []
                index += 1
                continue
            current.append(char)
            index += 1
        statements.append("".join(current))
        return [s.strip() for s in statements if s.strip()]

    async def statement(self, text):
        words = await self.words(text)
        assignments = []
        while words and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0][0]):
            assignments.append(words.pop(0))
        if not words:
            for word in assignments:
                name, _, value = word[0].partition("=")
                self.environ[name] = value
            self.status = self.substatus if self.substatus is not None else 0
            return
        if assignments:
            raise Unsupported
        command, args = words[0][0], [w[0] for w in words[1:]]
        if command == "echo":
            newline = True
            if args and args[0] == "-n":
                newline, args = False, args[1:]
            self.out.append(" ".join(args) + ("\n" if newline else ""))
            self.status = 0
        elif command in ("true", "false"):
            self.status = int(command == "false")
        else:
            raise Unsupported

    async def words(self, text):
        """The words of a statement as (text, quoted) after the expansions; assignments keep their value whole."""
        words, current, started, index = [], [], False, 0
        self.substatus = None
        while index < len(text):
            char = text[index]
            if char in " \t":
                if started:
                    words.append(("".join(current), True))
                    current, started = [], False
                index += 1
            elif char == "'":
                end = text.index("'", index + 1)
                current.append(text[index + 1:end])
                started, index = True, end + 1
            elif char == '"':
                end, piece = await self.double(text, index + 1)
                current.append(piece)
                started, index = True, end + 1
            elif char == "$" or char == "`":
                piece, index = await self.expansion(text, index)
                head = "".join(current)
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=", head) or not piece.strip():
                    current.append(piece)
                    started = started or bool(piece) or bool(head)
                else:
                    first = True
                    for field in re.split(r"[ \t\n]+", piece):
                        if not first and (current or started):
                            words.append(("".join(current), False))
                            current = []
                        if field:
                            current.append(field)
                            started = True
                        first = False
            elif char in "|&<>(){}\\":
                raise Unsupported
            else:
                current.append(char)
                started = True
                index += 1
        if started:
            words.append(("".join(current), True))
        return words

    async def double(self, text, index):
        piece = []
        while index < len(text) and text[index] != '"':
            if text[index] in "$`":
                value, index = await self.expansion(text, index)
                piece.append(value)
            elif text[index] == "\\":
                raise Unsupported
            else:
                piece.append(text[index])
                index += 1
        if index >= len(text):
            raise Unsupported
        return index, "".join(piece)

    async def expansion(self, text, index):
        if text.startswith("$((", index):
            end = self.names["tpot_arith_close"](text, index + 2, "(", ")")
            if end > 0 and text[end - 1] == ")":
                return await self.names["tpot_arith_value"](self, text[index + 3:end - 1]), end + 1
        if text.startswith("$(", index):
            end = self.names["tpot_arith_close"](text, index + 2, "(", ")")
            if end < 0:
                raise Unsupported
            return await self.command_substitution(text[index + 2:end]), end + 1
        if text[index] == "`":
            end = text.find("`", index + 1)
            if end < 0:
                raise Unsupported
            return await self.command_substitution(text[index + 1:end]), end + 1
        if text.startswith("$?", index):
            return str(self.status), index + 2
        found = re.compile(r"\$\{([^}]*)\}|\$([_a-zA-Z0-9]+)").match(text, index)
        if not found:
            raise Unsupported
        return self.environ.get(found.group(1) or found.group(2), ""), found.end()


class MiniChild(MiniShell):
    """A command substitution: its own shell, an error ends only it."""


MiniShell.child = MiniChild


def bash_prefix(line):
    return f"bash: line {line}: "


def ash_prefix(line):
    return "sh: "


def run_case(names, case, prefix, ash):
    shell = MiniShell(names, prefix, ash)
    shell.line = 1
    try:
        asyncio.run(shell.run(case))
    except Unsupported:
        return None
    return ["".join(shell.out), "".join(shell.err), shell.status]


class ReferenceTest(unittest.TestCase):
    """The evaluator against the recorded shells."""

    def compare(self, image, shell, values, prefix, ash, floor):
        names = arith_names(values)
        checked = 0
        for case, want in REF["images"][image]["shells"][shell].items():
            got = run_case(names, case, prefix, ash)
            if got is None:
                continue
            with self.subTest(image=image, case=case):
                self.assertEqual(got, want)
            checked += 1
        self.assertGreaterEqual(checked, floor)

    def test_bash_cases_of_the_recorded_bash_5(self):
        for image in BASH_IMAGES:
            self.compare(image, "bash", {}, bash_prefix, False, 171)

    def test_sh_cases_of_the_recorded_busybox_ash(self):
        for image, banner in ASH_IMAGES.items():
            values = {("shell", "not_found_style"): "ash", ("shell", "text_banner"): banner}
            self.compare(image, "sh", values, ash_prefix, True, 171)


class Statement:
    """A queued statement of the stub shell: the line it starts on; the rest is for the recorded cases below."""

    def __init__(self, lineno, name="", out=None, splice=None, child=False):
        self.lineno, self.name, self.splice, self.child = lineno, name, splice, child
        self.out = f"{name}\n" if out is None else out


class Cleanup:
    """An entry of the queue like _Continuation: fn is a callback named as the one of honeypot.py."""

    is_loop_cont = False

    def __init__(self, fn):
        self.fn = fn


def restore():
    pass


def decide():
    pass


class StubShell:
    """The part of HoneyPotShell the hooks of the patch read and write."""

    def __init__(self, interactive=False, done=None, prefix="bash: line 1: "):
        self.cmdpending, self.written, self.advances = [], [], 0
        self.last_exit_code, self._loop_signal, self._loop_depth = 0, "break", 3
        self.interactive, self.done, self.prefix = interactive, done, prefix

    def _write_shell_error(self, message):
        self.written.append(message)

    def error_prefix(self):
        return self.prefix

    def _advance(self):
        self.advances += 1


def hook_names():
    """The module code of the patched honeypot.py between the markers of the hooks."""
    text = (Patched.get() / "src/cowrie/shell/honeypot.py").read_text(encoding="utf-8")
    start = text.index("# >>> tpot shell arithmetic hooks")
    end = text.index("# <<< tpot shell arithmetic hooks")
    namespace = {}
    exec(compile(text[start:end], "<hooks>", "exec"), namespace)  # noqa: S102
    return namespace


def queue(names, shell, statements):
    """_queue_statements of the patched honeypot.py: the marks of its three needles."""
    names["tpot_arith_batch"](shell)
    for statement in statements:
        shell.cmdpending.append(statement)
        names["tpot_mark_top"](shell, statement)


def take(names, shell):
    """runCommand: the statement taken from the queue is noted."""
    command = shell.cmdpending.pop(0)
    names["tpot_note_top"](shell, command)
    return command


class HooksTest(unittest.TestCase):
    """What an error of $(( )) does to the queue of the shell (tpot_arith_failed)."""

    @classmethod
    def setUpClass(cls):
        cls.names = hook_names()
        cls.error = arith_names()["TpotArithError"]

    def fail(self, shell, ash=False, status=1):
        self.names["tpot_arith_failed"](shell, self.error("1/0: division by 0", status, ash))

    def test_bash_goes_on_with_the_next_line(self):
        shell, lines = StubShell(), [Statement(1), Statement(2), Statement(3)]
        queue(self.names, shell, lines)
        take(self.names, shell)
        self.fail(shell)
        self.assertEqual(shell.cmdpending, lines[1:])
        self.assertEqual((shell.last_exit_code, shell.advances), (1, 1))
        self.assertEqual(shell.written, [b"bash: line 1: 1/0: division by 0\n"])
        self.assertEqual((shell._loop_signal, shell._loop_depth), (None, 0))

    def test_bash_drops_the_rest_of_its_line(self):
        shell, lines = StubShell(), [Statement(1), Statement(1), Statement(2)]
        queue(self.names, shell, lines)
        take(self.names, shell)
        self.fail(shell)
        self.assertEqual(shell.cmdpending, lines[2:])

    def test_the_parts_a_loop_spliced_in_are_dropped_with_it(self):
        shell, lines = StubShell(), [Statement(1, "for"), Statement(2)]
        queue(self.names, shell, lines)
        take(self.names, shell)
        loop_end = Cleanup(decide)
        loop_end.is_loop_cont = True
        shell.cmdpending[0:0] = [Statement(0, "body"), Statement(0, "body"), loop_end]
        self.fail(shell)
        self.assertEqual(shell.cmdpending, lines[1:])

    def test_what_a_function_and_a_redirected_group_gave_back_stays(self):
        shell, lines = StubShell(), [Statement(1), Statement(2)]
        queue(self.names, shell, lines)
        take(self.names, shell)
        restore_fds = Cleanup(lambda: shell._restore_fds({}))
        shell.cmdpending[0:0] = [Statement(0), Cleanup(restore), Cleanup(decide), restore_fds]
        keep = [shell.cmdpending[1], restore_fds]
        self.fail(shell)
        self.assertEqual(shell.cmdpending, keep + lines[1:])

    def test_ash_ends_a_non_interactive_shell(self):
        shell, lines = StubShell(), [Statement(1), Statement(2)]
        queue(self.names, shell, lines)
        take(self.names, shell)
        self.fail(shell, ash=True, status=2)
        self.assertEqual((shell.cmdpending, shell.last_exit_code), ([], 2))

    def test_ash_at_a_terminal_ends_the_line_only(self):
        shell = StubShell(interactive=True)
        queue(self.names, shell, [Statement(1), Statement(2)])
        later = Statement(1)
        queue(self.names, shell, [later])
        take(self.names, shell)
        self.fail(shell, ash=True, status=2)
        self.assertEqual(shell.cmdpending, [later])

    def test_a_shell_of_a_substitution_ends_alone(self):
        shell = StubShell(done=object())
        queue(self.names, shell, [Statement(1), Statement(2)])
        take(self.names, shell)
        self.fail(shell)
        self.assertEqual(shell.cmdpending, [])

    def test_a_statement_that_was_not_queued_ends_the_queue(self):
        shell = StubShell()
        queue(self.names, shell, [Statement(1), Statement(2)])
        shell.tpot_top_now = None
        self.fail(shell)
        self.assertEqual(shell.cmdpending, [])

    def test_the_line_of_a_compound_statement_is_that_of_its_first_part(self):
        names = self.names

        class Group:
            def __init__(self, statements):
                self.statements = statements

        class Loop:
            def __init__(self, items, body):
                self.items, self.body = items, body

        class Words:
            def __init__(self, lineno):
                self.items, self.lineno = [], lineno

        self.assertEqual(names["tpot_first_line"](Group([Statement(4)])), 4)
        self.assertEqual(names["tpot_first_line"](Loop(Words(3), [Statement(4)])), 3)
        self.assertEqual(names["tpot_first_line"](Loop(None, [Statement(5)])), 5)
        self.assertEqual(names["tpot_first_line"](Group([])), 0)

    def test_the_needles_put_the_hooks_where_the_shell_queues_and_fails(self):
        text = (Patched.get() / "src/cowrie/shell/honeypot.py").read_text(encoding="utf-8")
        for needle in ("        tpot_arith_batch(self)\n        for statement in statements:\n",
                       "            tpot_mark_top(self, statement)\n        return True\n",
                       "                tpot_mark_top(self, error)\n                return False\n",
                       "            tpot_arith_failed(self, failure.value)\n            return\n",
                       "        command = self.cmdpending.pop(0)\n        tpot_note_top(self, command)\n"):
            with self.subTest(needle=needle):
                self.assertEqual(text.count(needle), 1)


def recorded_queues():
    """The queue of the shell for each recorded structured case: the statements of the input (line, name) and what
    a group, loop, function or redirected command splices in. The shapes follow honeypot.py, the cut of the queue
    for the entries that give back a function's or a group's changes is derived, not recorded."""
    def fail(line=0):
        return Statement(line, "fail", "")

    def loop_end():
        entry = Cleanup(decide)
        entry.is_loop_cont = True
        return entry

    def child(line, name="after"):
        return Statement(line, name, "", child=True)

    def printer(shell):
        return f"[] rc={shell.last_exit_code}\n"

    return {
        "echo $((1/0)); echo next": [fail(1), Statement(1, "next")],
        "echo first; echo $((1/0)); echo next": [Statement(1, "first"), fail(1), Statement(1, "next")],
        "echo $((1/0))\necho next": [fail(1), Statement(2, "next")],
        "echo $((1/0)) || echo failed": [fail(1), Statement(1, "failed")],
        "x=$((1/0)); echo rc=$?": [fail(1), Statement(1, "rc")],
        "{ echo $((1/0)); }; echo after": [Statement(1, "group", "", splice=lambda: [fail()]), Statement(1, "after")],
        "for i in 1 2; do echo $((1/0)); done; echo after": [
            Statement(1, "for", "", splice=lambda: [fail(), loop_end()]), Statement(1, "after")],
        "echo $((1/0)) 2>/dev/null; echo after": [
            Statement(1, "redirected", "", splice=lambda: [fail(), Cleanup(restore_for_test)]), Statement(1, "after")],
        "f() { echo $((1/0)); echo in; }; f; echo after": [
            Statement(1, "def", ""),
            Statement(1, "call", "", splice=lambda: [fail(), Statement(0, "in"), Cleanup(restore), Cleanup(decide)]),
            Statement(1, "after")],
        "echo $((1/0))\n\necho next\necho more": [fail(1), Statement(3, "next"), Statement(4, "more")],
        "( echo $((1/0)) ); echo after": [child(1, "subshell"), Statement(1, "after")],
        "echo $((1/0)) | cat; echo after": [child(1, "pipeline"), Statement(1, "after")],
        "echo $((1/0)) 2>&1 | cat; echo after": [child(1, "pipeline"), Statement(1, "after")],
        "echo $(echo $((1/0))); echo after": [Statement(1, "echo", "\n", child=True), Statement(1, "after")],
        "x=$(echo $((1/0))); echo [$x] rc=$?": [Statement(1, "assign", "", child=True), Statement(1, "print", printer)],
    }


def restore_for_test():
    """The callback of a redirected command: its code names _restore_fds."""
    shell = StubShell()
    shell._restore_fds({})


class RecordedAbortTest(unittest.TestCase):
    """tpot_arith_failed against the recorded structured cases of shell_arith.json (bash 5 and BusyBox ash):
    which statements of the queue still run, what they print and the status."""

    def play(self, case, style, prefix):
        names, arith = hook_names(), arith_names()
        shell, out, written = StubShell(prefix=prefix), [], []
        statements = recorded_queues()[case]

        def error():
            try:
                arith["tpot_arith"]("1/0", {}, style)
            except arith["TpotArithError"] as caught:
                return caught

        queue(names, shell, statements)
        while shell.cmdpending:
            item = take(names, shell)
            if isinstance(item, Cleanup):
                continue
            if item.splice:
                shell.cmdpending[0:0] = item.splice()
                continue
            if item.child:
                sub = StubShell(done=object(), prefix=prefix)
                queue(names, sub, [Statement(1, "fail", ""), Statement(2, "more")])
                take(names, sub)
                names["tpot_arith_failed"](sub, error())
                self.assertEqual(sub.cmdpending, [])
                written += sub.written
                shell.last_exit_code = sub.last_exit_code if item.name == "assign" else 0
            elif item.name == "fail":
                names["tpot_arith_failed"](shell, error())
                continue
            out.append(item.out(shell) if callable(item.out) else item.out)
            if not item.child:
                shell.last_exit_code = 0
        return ["".join(out), "".join(m.decode() for m in shell.written) + "".join(m.decode() for m in written),
                shell.last_exit_code]

    def compare(self, image, shell, style, prefix):
        for case in recorded_queues():
            want = REF["images"][image]["shells"][shell][case]
            if case.startswith("f()") and style == "bash":
                want = [want[0], want[1].replace("environment: line 1:", "bash: line 1:"), want[2]]  # persona prefix
            with self.subTest(image=image, case=case):
                self.assertEqual(self.play(case, style, prefix), want)

    def test_bash_ends_what_the_recorded_bash_5_ended(self):
        for image in BASH_IMAGES:
            self.compare(image, "bash", "bash", "bash: line 1: ")

    def test_ash_ends_what_the_recorded_busybox_ash_ended(self):
        for image in ASH_IMAGES:
            self.compare(image, "sh", "ash3", "sh: ")


class MshTest(unittest.TestCase):
    """msh has no arithmetic: the recorded answer is a syntax error (status 255). A real msh fails the whole line at
    parse time and runs nothing of it (not recorded); the persona runs the statements before the expression."""

    def test_an_expression_is_the_recorded_syntax_error(self):
        case = "x=1; echo $((x+1))"
        out, err, status = MSH["images"]["fw_dlink_dir859_105b03_msh_c"]["cases"][case]
        names = arith_names({("shell", "not_found_style"): "msh"})
        shell = MiniShell(names, bash_prefix, False)
        shell.environ["x"] = "1"
        with self.assertRaises(names["TpotArithError"]) as caught:
            asyncio.run(names["tpot_arith_value"](shell, "x+1"))
        self.assertEqual((out, caught.exception.message + "\n", caught.exception.status), ("", err, status))
        self.assertFalse(caught.exception.ash)

    def test_the_error_ends_the_statement_with_that_status(self):
        names, arith = hook_names(), arith_names({("shell", "not_found_style"): "msh"})
        shell = StubShell()
        queue(names, shell, [Statement(1), Statement(1)])
        take(names, shell)
        names["tpot_arith_failed"](shell, arith["TpotArithError"]("syntax error", 255))
        self.assertEqual((shell.last_exit_code, shell.cmdpending), (255, []))


class BoundsTest(unittest.TestCase):
    """The limits of the evaluator: an error or a wrapped value, and soon."""

    def evaluate(self, expression, env=None, style="bash", **limits):
        names = arith_names()
        names.update(limits)
        started = time.monotonic()
        try:
            value = names["tpot_arith"](expression, dict(env or {}), style)
        except names["TpotArithError"] as error:
            value = error
        self.assertLess(time.monotonic() - started, 1)
        return value, names["TpotArithError"]

    def assertFails(self, expression, message=None, env=None, **limits):
        value, error = self.evaluate(expression, env, **limits)
        self.assertIsInstance(value, error, value)
        if message:
            self.assertIn(message, value.message)

    def test_an_expression_over_the_length_is_cut_and_fails(self):
        self.assertEqual(self.evaluate("1+" * 2047 + "1")[0], "2048")
        self.assertFails("1+" * 2048 + "1", "recursion level exceeded")
        self.assertFails("1+1+1+1", "recursion level exceeded", TPOT_ARITH_MAX_LENGTH=6)

    def test_the_tokens_of_all_levels_together_are_counted(self):
        self.assertEqual(self.evaluate("1+1+1+1")[0], "4")
        self.assertFails("1+1+1+1", "recursion level exceeded", TPOT_ARITH_MAX_TOKENS=5)
        self.assertFails("x", "recursion level exceeded", env={"x": "y+y+y+y+y", "y": "z+z+z+z+z", "z": "1+1+1+1+1"},
                         TPOT_ARITH_MAX_TOKENS=40)

    def test_names_that_hold_expressions_nest_to_the_levels(self):
        self.assertFails("a", "recursion level exceeded", env={"a": "a"})
        self.assertFails("x", "recursion level exceeded", env={"x": "x+x+x+x+x+x+x+x+x+x"})
        self.assertEqual(self.evaluate("a", env={"a": "b", "b": "c", "c": "7"})[0], "7")
        self.assertFails("a", "recursion level exceeded", env={"a": "b", "b": "c", "c": "7"}, TPOT_ARITH_MAX_LEVELS=1)

    def test_nested_parentheses_and_operators_stop_at_the_depth(self):
        self.assertEqual(self.evaluate("(" * 14 + "1" + ")" * 14)[0], "1")
        self.assertFails("(" * 2000, "recursion level exceeded")
        self.assertFails("-" * 3000 + "1", "recursion level exceeded")
        self.assertFails("(((1)))", "recursion level exceeded", TPOT_ARITH_MAX_DEPTH=5)
        self.assertEqual(self.evaluate("-" * 20 + "1")[0], "1")

    def test_a_python_recursion_error_is_the_shell_error(self):
        self.assertFails("1?1:" * 5000 + "1", "recursion level exceeded", TPOT_ARITH_MAX_DEPTH=10 ** 6,
                         TPOT_ARITH_MAX_LENGTH=10 ** 6, TPOT_ARITH_MAX_TOKENS=10 ** 6)

    def test_a_huge_exponent_or_shift_wraps_as_bash_does(self):
        self.assertEqual(self.evaluate("2**9223372036854775807")[0], "0")
        self.assertEqual(self.evaluate("1<<9223372036854775807")[0], "-9223372036854775808")


try:
    import lark
except ImportError:  # the parser of Cowrie is not installed here: the grammar is checked where it is
    lark = None


@unittest.skipIf(lark is None, "lark is not installed")
class GrammarTest(unittest.TestCase):
    """The patched grammar of bashparse.py reads $(( )) as a term of its own and keeps $( ( ) ) a substitution."""

    @classmethod
    def setUpClass(cls):
        import typing

        text = (Patched.get() / "src/cowrie/shell/bashparse.py").read_text(encoding="utf-8")
        cls.module = {"Lark": lark.Lark, "Token": lark.Token, "Tree": lark.Tree, "Protocol": typing.Protocol,
                      "CowrieConfig": FakeConfig({})}
        exec(compile(text, "<bashparse>", "exec"), cls.module)  # noqa: S102

    def kinds(self, line):
        tree = self.module["_parser"].parse(line)
        return [node.data for node in tree.iter_subtrees_topdown() if node.data in ("arith", "cmdsub")]

    def test_an_expression_is_an_arith_term_in_a_word_and_in_double_quotes(self):
        for line in ("echo $((3*4))", 'echo "v=$((x*2))"', "y=$((n+1)); echo $y", "echo $(( (1+2)*3 ))",
                     "echo $(( $(echo 4) + 1 ))"):
            with self.subTest(line=line):
                self.assertIn("arith", self.kinds(line))

    def test_a_group_after_the_dollar_is_a_substitution(self):
        for line in ("echo $(echo hi)", "echo $( (echo hi) )", "echo $((echo hi); echo x)"):
            with self.subTest(line=line):
                self.assertEqual(self.kinds(line), ["cmdsub"])

    def test_the_loop_of_the_review_focus_reads_its_substitution_and_its_expression(self):
        kinds = self.kinds("for i in $(seq 1 3); do echo $((i*2)); done")
        self.assertEqual(sorted(kinds), ["arith", "cmdsub"])

    def test_the_loop_of_the_review_focus_counts_by_two(self):
        names = arith_names()
        out = []
        for value in ("1", "2", "3"):
            shell = MiniShell(names, bash_prefix, False)
            shell.environ["i"] = value
            out.append(asyncio.run(names["tpot_arith_value"](shell, "i*2")))
        self.assertEqual(out, ["2", "4", "6"])

    def test_parentheses_up_to_the_nesting_of_the_grammar_are_an_expression(self):
        for depth, kinds in ((14, ["arith"]), (15, ["cmdsub"])):
            with self.subTest(depth=depth):
                self.assertEqual(self.kinds("echo $((" + "(" * depth + "1" + ")" * depth + "))"), kinds)

    def test_every_line_has_one_reading(self):
        grammar = lark.Lark(self.module["_GRAMMAR"], start="start", parser="earley", lexer="dynamic",
                            propagate_positions=True, ambiguity="explicit")
        for line in ("echo $((3*4))", 'echo "v=$((x*2))" end', "echo $((1+2))$((3))a", "echo $(echo hi)",
                     "echo $((echo hi); echo x)", "echo $( (echo hi) )", "(echo a)", "((echo a); echo b)",
                     "for i in $(seq 1 3); do echo $((i*2)); done", "echo $(( (1+2)*3 )) $(( $(echo 4) + 1 ))"):
            with self.subTest(line=line):
                tree = grammar.parse(line)
                self.assertFalse([node for node in tree.iter_subtrees() if node.data == "_ambig"])


@unittest.skipIf(lark is None, "lark is not installed")
class EndToEndTest(unittest.TestCase):
    """The patched parser of Cowrie v3.1.3 (the whole bashparse.py, tests/fixtures/v3.1.3-full) parses lines and
    the evaluator expands them; the statements run in a small shell, and for an error through the hooks of the
    patched honeypot.py as the shell queues them."""

    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        shutil.copy(FULL_BASHPARSE, root / "src/cowrie/shell/bashparse.py")
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        tree = ast.parse((root / "src/cowrie/shell/bashparse.py").read_text(encoding="utf-8"))
        keep = [node for node in tree.body
                if not (isinstance(node, ast.ImportFrom) and node.module.split(".")[0] in ("cowrie", "twisted"))]

        class Config(FakeConfig):
            def getint(self, section, option, fallback=None):
                return fallback

            def getfloat(self, section, option, fallback=None):
                return fallback

        cls.config = Config({})
        module = types.ModuleType("tpot_bashparse_full")
        module.__dict__.update({"CowrieConfig": cls.config, "Logger": lambda *args, **kwargs: None, "Field": str})
        sys.modules[module.__name__] = module
        cls.addClassCleanup(sys.modules.pop, module.__name__, None)
        exec(compile(ast.Module(keep, []), "<bashparse>", "exec"), module.__dict__)  # noqa: S102
        cls.module = module.__dict__
        cls.hooks = hook_names()

    def setUp(self):
        self.config.values = {}

    def shell(self):
        return EndToEndShell(self.module)

    def test_the_loop_of_the_review_focus_prints_the_recorded_lines(self):
        line = "for i in $(seq 1 3); do echo $((i*2)); done"
        shell = self.shell()
        asyncio.run(shell.run(line))
        self.assertEqual("".join(shell.out), REF["images"]["debian_12"]["shells"]["bash"][line][0])
        self.assertEqual("".join(shell.out), "2\n4\n6\n")

    def test_the_same_line_as_a_command_of_the_exec_channel_prints_the_same(self):
        line = "for i in $(seq 1 3); do echo $((i*2)); done"
        shell, queued = self.shell(), StubShell(interactive=False)
        asyncio.run(shell.run_queued(self.hooks, queued, line))
        self.assertEqual(("".join(shell.out), queued.written, queued.last_exit_code), ("2\n4\n6\n", [], 0))

    def test_an_error_on_the_exec_channel_ends_the_line_as_recorded(self):
        for values, image, name, prefix in (({}, "debian_12", "bash", "bash: line 1: "),
                                            ({("shell", "not_found_style"): "ash",
                                              ("shell", "text_banner"): "BusyBox v1.36.1"},
                                             "busybox_1.36.1-musl", "sh", "sh: ")):
            self.config.values = values
            for line in ("echo $((1/0)); echo next", "echo first; echo $((1/0)); echo next",
                         "echo $((1/0))\necho next"):
                shell, queued = self.shell(), StubShell(interactive=False, prefix=prefix)
                asyncio.run(shell.run_queued(self.hooks, queued, line))
                got = ["".join(shell.out), b"".join(queued.written).decode(), queued.last_exit_code]
                with self.subTest(image=image, line=line):
                    self.assertEqual(got, REF["images"][image]["shells"][name][line])


class EndToEndShell:
    """A shell for the real parser: echo, seq and for over the statements it gives."""

    def __init__(self, module, parent=None):
        self.module = module
        self.environ = dict(parent.environ) if parent else {}
        self.status, self.out = 0, []
        self.parser = module["BashParser"](self)

    def get_variable(self, name):
        return self.environ.get(name)

    def get_status(self):
        return str(self.status)

    async def command_substitution(self, source):
        child = EndToEndShell(self.module, self)
        await child.run(source)
        return "".join(child.out).rstrip("\n")

    async def run(self, line):
        for statement in self.parser.parse(line):
            await self.execute(statement)

    async def run_queued(self, hooks, queued, line):
        """What the shell does with its queue: take a statement, run it, give an error to tpot_arith_failed."""
        queue(hooks, queued, self.parser.parse(line))
        while queued.cmdpending:
            statement = take(hooks, queued)
            try:
                await self.execute(statement)
                queued.last_exit_code = 0
            except self.module["TpotArithError"] as error:
                hooks["tpot_arith_failed"](queued, error)

    async def execute(self, statement):
        if isinstance(statement, self.module["ForClause"]):
            for value in await self.parser.evaluate(statement.items):
                self.environ[statement.var] = str(value)
                for inner in statement.body:
                    await self.execute(inner)
            return
        words = [str(word) for word in await self.parser.evaluate(statement)]
        if words[:1] == ["echo"]:
            self.out.append(" ".join(words[1:]) + "\n")
        elif words[:1] == ["seq"]:
            self.out.append("".join(f"{number}\n" for number in range(int(words[1]), int(words[2]) + 1)))
        else:
            raise Unsupported(words)


if __name__ == "__main__":
    unittest.main()
