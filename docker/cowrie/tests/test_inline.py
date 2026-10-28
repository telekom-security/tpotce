"""perl -e of the patched Cowrie: exit with a scalar set in the code, and die. The expected texts are
the recorded ones of fixtures/perl_exit_die.json (official images, docker run --network none), except
where a comment says otherwise."""
import ast
import json
import re
import time
import tracemalloc
import types
import unittest
from pathlib import Path

from tests import test_patch
from tests.test_patch import FakeCommand, FakeConfig, module_all

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "perl_exit_die.json").read_text(encoding="utf-8"))
_SOURCES = {}
PERL = {("shell", "perl_version"): "5.36.0", ("shell", "perl_archname"): "x86_64-linux-gnu-thread-multi",
        ("shell", "perl_copyright"): "2022"}


def source(module):
    if module not in _SOURCES:
        case = test_patch.PatchTest()
        case.setUp = lambda: None
        try:
            root = case.patched()
            _SOURCES[module] = (root / f"src/cowrie/commands/{module}.py").read_text()
        finally:
            case.doCleanups()
    return _SOURCES[module]


def perl(args, values=PERL):
    """Command_perl started with args: (stdout, stderr, exit code)."""
    namespace = module_all(source("perl"), {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(values)})
    cmd = namespace["Command_perl"](list(args))
    cmd.start()
    return bytes(cmd.out).decode("latin-1"), cmd.err, cmd.code


def model_status(code):
    """'unknown' when tpot_perl_code raises TpotPerlUnknown (the persona falls back), else its (stdout, stderr, status)."""
    namespace = module_all(source("perl"), {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(PERL)})
    try:
        return namespace["tpot_perl_code"](code, set())
    except namespace["TpotPerlUnknown"]:
        return "unknown"


class PerlExitTest(unittest.TestCase):
    def test_exit_takes_the_value_of_a_scalar(self):
        self.assertEqual(perl(["-e", "$x=3; exit $x"]), ("", "", 3))
        self.assertEqual(perl(["-e", "$x = 3; exit($x)"]), ("", "", 3))
        self.assertEqual(perl(["-e", "my $x = '7abc'; exit $x"]), ("", "", 7))
        self.assertEqual(perl(["-e", "$x = 300; exit $x"]), ("", "", 44))
        self.assertEqual(perl(["-e", "exit $nothing"]), ("", "", 0))
        self.assertEqual(perl(["-e", "$x = 'abc'; exit $x"]), ("", "", 0))
        self.assertEqual(perl(["-e", "$x = -1; exit $x"]), ("", "", 255))
        self.assertEqual(perl(["-e", "exit -1"]), ("", "", 255))
        self.assertEqual(perl(["-e", "$x = '9007199254740993'; exit $x"]), ("", "", 1))

    def test_exit_with_a_scalar_keeps_the_output_before_it(self):
        self.assertEqual(perl(["-e", 'print "a\\n"; $x = 4; exit $x; print "b"']), ("a\n", "", 4))

    def test_exit_with_an_unknown_status_falls_back(self):
        # perl prints "a", "b" and exits 4 (recorded in not_modelled); the status is not known, so the code falls back
        self.assertEqual(model_status('print "a\\n"; $x = 3; $x++; print "b\\n"; exit $x'), "unknown")

    def test_a_scalar_changed_by_an_unknown_statement_is_not_taken(self):
        # perl gives 4 (recorded in not_modelled); $x++ is not read, so the status is not known
        self.assertEqual(model_status("$x = 3; $x++; exit $x"), "unknown")

    def test_a_scalar_changed_through_a_sub_or_a_reference_is_not_taken(self):
        # perl gives 5 for each (recorded in not_modelled)
        self.assertEqual(model_status("$x = 3; $f = sub { $x = 5 }; $f->(); exit $x"), "unknown")
        self.assertEqual(model_status("$x = 3; $r = \\$x; $x = 3; $$r = 5; exit $x"), "unknown")
        self.assertEqual(model_status("$x = 3; my $y = do { $x = 5 }; exit $x"), "unknown")

    def test_a_call_of_a_declared_sub_forgets_the_scalars(self):
        # perl gives 5 (recorded in not_modelled)
        self.assertEqual(model_status("sub f { $x = 5 }; $x = 3; f(); exit $x"), "unknown")

    def test_a_builtin_call_or_a_logical_and_keeps_the_scalars(self):
        self.assertEqual(perl(["-e", "$x = 3; 1 && 2; exit $x"]), ("", "", 3))
        # perl prints 1 (recorded in not_modelled, the print is not modelled): the status is checked
        self.assertEqual(perl(["-e", '$x = 3; print join(",", 1); exit $x'])[2], 3)

    def test_exit_with_an_expression_is_not_read(self):
        # perl gives 3 (recorded in not_modelled)
        self.assertEqual(perl(["-e", "exit 1 + 2"]), ("", "", 0))

    def test_exit_with_an_infinite_scalar_is_not_read(self):
        # perl gives 255 on debian:12, debian:13, ubuntu:24.04 and 0 on ubuntu:22.04 (version_dependent)
        self.assertEqual(perl(["-e", "$x = 'inf'; exit $x"]), ("", "", 0))

    def test_a_shift_assignment_and_a_four_argument_substr_forget_the_scalar(self):
        # perl gives 12 and 9 (recorded in not_modelled)
        self.assertEqual(model_status("$x = 3; $x <<= 2; exit $x"), "unknown")
        self.assertEqual(model_status('$x = "5"; substr($x,0,1,"9"); exit $x'), "unknown")

    def test_chop_changes_the_topic_scalar(self):
        # perl gives 0 (recorded in cases)
        self.assertEqual(perl(["-e", '$_ = "5"; chop; exit $_']), ("", "", 0))

    def test_a_builtin_call_in_an_assignment_keeps_the_other_scalars(self):
        # perl gives 3 (recorded in cases)
        self.assertEqual(perl(["-e", '$x = 3; $y = lc("A"); exit $x']), ("", "", 3))

    def test_a_block_with_exit_is_not_read(self):
        # perl gives 4 (recorded in not_modelled): the block is not read, so no status is taken from it
        self.assertEqual(perl(["-e", "$x = 3; if (1) { exit 4 }; exit $x"]), ("", "", 0))

    def test_the_rest_after_a_regex_is_checked_for_exit(self):
        # perl gives 3 (recorded in not_modelled): the regex ends the reading, the rest is not read
        self.assertEqual(perl(["-e", "$x = 3; /a/; exit $x"]), ("", "", 0))

    def test_a_call_of_an_undeclared_sub_is_not_read(self):
        # perl exits 255 with "Undefined subroutine &main::foo called at -e line 1." (recorded in not_modelled)
        self.assertEqual(model_status("$x = 3; foo(1); exit $x"), "unknown")

    def test_an_undeclared_call_after_a_print_falls_back_without_the_output(self):
        # perl prints "a" and exits 255 (recorded in not_modelled); the fallback keeps no output
        self.assertEqual(perl(["-e", 'print "a\\n"; $x = 3; foo(1); print "b\\n"']), ("", "", 0))

    def test_a_method_call_of_an_undeclared_name_is_not_read(self):
        # perl exits 255 for a package and for an undefined value (recorded in not_modelled)
        self.assertEqual(model_status("$x = 3; Foo->bar(1); exit $x"), "unknown")
        self.assertEqual(model_status("$x = 3; $o->bar(1); exit $x"), "unknown")

    def test_a_method_call_without_parentheses_is_not_read(self):
        # perl exits 255 (derived from the call with parentheses, recorded in not_modelled)
        self.assertEqual(model_status("$x = 3; Foo->bar; exit 3"), "unknown")
        self.assertEqual(model_status("$x = 3; $o->bar; exit 3"), "unknown")
        self.assertEqual(model_status("$x = 3; Foo::Bar->baz; exit 3"), "unknown")


class PerlDieTest(unittest.TestCase):
    def test_die_texts(self):
        self.assertEqual(perl(["-e", "die"]), ("", "Died at -e line 1.\n", 255))
        self.assertEqual(perl(["-e", 'die "x"']), ("", "x at -e line 1.\n", 255))
        self.assertEqual(perl(["-e", 'die "x\\n"']), ("", "x\n", 255))
        self.assertEqual(perl(["-e", '$m = "y"; die $m']), ("", "y at -e line 1.\n", 255))
        self.assertEqual(perl(["-e", 'print "a\\n"; die "x\\n"']), ("a\n", "x\n", 255))

    def test_die_with_an_unknown_list_keeps_the_output_and_the_status(self):
        # perl prints "a at -e line 1." (recorded in not_modelled): the message is not known, the status is 255
        self.assertEqual(perl(["-e", 'die "a", $nothing']), ("", "", 255))
        self.assertEqual(perl(["-e", 'print "a\\n"; die "a", $nothing']), ("a\n", "", 255))
        # perl prints CODE(0x...) (status_only in the fixture); the message is not modelled
        out, err, status = perl(["-e", "$m = sub {1}; die $m"])
        self.assertEqual((out, err, status), ("", "", 255))

    def test_a_block_with_die_is_not_read(self):
        # perl prints "x" and exits 255 (recorded in not_modelled)
        self.assertEqual(perl(["-e", '$x = 3; if (1) { die "x\\n" }; exit $x']), ("", "", 0))


class StatusOnlyTest(unittest.TestCase):
    def test_recorded_status_matches_in_every_image(self):
        # the stderr of these cases holds an address (CODE(0x...)), so only the status is compared
        for entry in RECORDED["status_only"]:
            with self.subTest(image=entry["image"], code=entry["code"]):
                self.assertEqual(perl(["-e", entry["code"]])[2], entry["rc"])


class RecordedTest(unittest.TestCase):
    def test_recorded_cases_match_in_every_image(self):
        self.assertEqual(len(RECORDED["_source"]["images"]), 4)
        self.assertEqual(len(RECORDED["version_dependent"][0]["images"]), 4)
        for entry in RECORDED["cases"]:
            with self.subTest(image=entry["image"], code=entry["code"]):
                out, err, status = perl(["-e", entry["code"]])
                self.assertEqual((out, err, status), (entry["stdout"], entry["stderr"], entry["rc"]))

    def test_recorded_not_modelled_cases_still_differ(self):
        # the persona keeps its fallback here; once a case matches, it moves to "cases"
        for entry in RECORDED["not_modelled"]:
            with self.subTest(image=entry["image"], code=entry["code"]):
                answer = perl(["-e", entry["code"]])
                self.assertNotEqual(answer, (entry["stdout"], entry["stderr"], entry["rc"]))


PY = json.loads((Path(__file__).parent / "fixtures" / "python_reference.json").read_text(encoding="utf-8"))
# the Python of each persona: key of the fixture -> version string of [shell] python_version
PY_VERSIONS = {"2.7": "2.7.5", "3.9": "3.9.21", "3.10": "3.10.12", "3.11": "3.11.2", "3.12": "3.12.3", "3.13": "3.13.5"}
_PYTHON = []


def python_run(code, version, colors=False):
    """tpot_python_code of the patched python.py: (stdout, stderr, exit status)."""
    if not _PYTHON:
        _PYTHON.append(module_all(source("python"), {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig({})}))
    return _PYTHON[0]["tpot_python_code"](code, version, colors)


def hint_as_if_sure(code, version):
    """The error line of the code with every statement taken as followed."""
    python_run("1", version)
    namespace = _PYTHON[0]
    module = ast.parse(code)
    scope = namespace["TpotScope"](module, version, False, code)
    try:
        for node in module.body:
            scope.sure = True
            namespace["tpot_exec"](node, scope)
    except namespace["TpotPyError"] as error:
        return f"{error.kind}: {error.text}"
    return ""


def python_key(version):
    return ".".join(version.split(".")[:2])


def recorded_answer(entry):
    """What the persona must answer for a recorded run: a terminal run has both streams in stdout (stdout lines
    first, stderr at the exit), a pipe run keeps them apart."""
    if entry["tty"]:
        return None, entry["stdout"].replace("\r\n", "\n"), entry["rc"]
    return entry["stdout"], entry["stderr"], entry["rc"]


def terminal_text(events):
    """What a terminal shows of the writes: stdout is line buffered (its last line without newline comes at the
    exit), stderr is written at once."""
    text, tail = "", ""
    for stream, piece in events:
        if stream == "err":
            text += piece
            continue
        piece = tail + piece
        cut = piece.rfind("\n") + 1
        text, tail = text + piece[:cut], piece[cut:]
    return text + tail


def python_answer(code, version, colors=False, tty=False):
    if tty:
        python_run("1", version)
        events, status = _PYTHON[0]["tpot_python_events"](code, version, colors)
        return None, terminal_text(events), status
    return python_run(code, version, colors)


class NameErrorTest(unittest.TestCase):
    def test_every_recorded_name_has_the_hint_of_its_python(self):
        wrong = []
        for key, names in PY["names"].items():
            for name, hint in names.items():
                out, err, status = python_run(name, PY_VERSIONS[key])
                line = err.rstrip("\n").split("\n")[-1]
                if (out, status, line) != ("", 1, f"NameError: name '{name}' is not defined{hint}"):
                    wrong.append((key, name, line))
        self.assertEqual(wrong, [])

    def test_names_bound_in_the_code_give_the_hint_of_the_names_it_binds(self):
        # a name of the code is offered like a name of the module (derived: the loop that binds nothing is not run)
        # (not followed: the bodies of the one-line compound statements, which are no names of the module)
        skipped = {"class Foo: pass; print(Fooo)", "for item in [1]: pass; print(itme)"}
        wrong = []
        for key, cases in PY["bound"].items():
            for code, hint in cases.items():
                if code in skipped:
                    continue
                out, err, status = python_run(code, PY_VERSIONS[key])
                line = err.rstrip("\n").split("\n")[-1]
                if status == 0 and not err:
                    # silent after a statement this does not follow (SureTest): look at the hint as if it had
                    line, status = hint_as_if_sure(code, PY_VERSIONS[key]), 1
                found = re.fullmatch(r"NameError: name '(\w+)' is not defined(.*)", line)
                if status != 1 or not found or found.group(2) != hint:
                    wrong.append((key, code, line))
        self.assertEqual(wrong, [])

    def test_recorded_runs_match(self):
        # syntax errors and the digit limit have their own tests; codes of the other tests are skipped here
        wrong = []
        for entry in PY["errors"]:
            version = PY_VERSIONS[python_key(entry["python"])]
            answer = python_answer(entry["code"], version, colors=entry["tty"], tty=entry["tty"])
            if answer != recorded_answer(entry):
                wrong.append((entry["python"], entry["tty"], entry["code"], answer))
        self.assertEqual(wrong, [])

    def test_recorded_colours_follow_the_environment(self):
        for entry in PY["env"]:
            if "9" in entry["code"]:
                continue  # the digit limit has its own test
            with self.subTest(label=entry["label"], code=entry["code"]):
                environ = entry["env"]
                python_run("1", "3.13.5")
                colors = _PYTHON[0]["tpot_colors"](environ, entry["tty"])
                out, err, status = python_run(entry["code"], "3.13.5", colors)
                text = entry["stdout"] if entry["tty"] else entry["stderr"]
                self.assertEqual(err, text.replace("\r\n", "\n"))
                self.assertEqual((colors, status), ("\x1b[" in text, entry["rc"]))

    def test_python_2_names(self):
        # only the NameErrors: the other recorded runs print values of Python 2 builtins this does not know
        for entry in PY["py2"]:
            if "NameError" not in entry["stderr"]:
                continue
            with self.subTest(code=entry["code"]):
                self.assertEqual(python_run(entry["code"], "2.7.5"), (entry["stdout"], entry["stderr"], entry["rc"]))

    def test_the_names_of_the_code_are_no_errors(self):
        for code in ("import sys; print(sys.argv)", "from os import path; print(path)", "def f(): pass\nf()",
                     "for i in [1]: print(i)", "class A: pass\nprint(A)",
                     "try:\n import json\nexcept ImportError:\n json = 0\nprint(json)",
                     "from os import *\nprint(path)", "exec('x = 1')\nprint(x)", "print(__name__, __doc__)",
                     "if 1:\n x = 2\nprint(x)", "print(x)\nx = 1", "import a.b.c\nprint(a)",
                     "with open('x') as h: pass\nprint(h)"):
            with self.subTest(code=code):
                self.assertEqual(python_run(code, "3.12.3")[1].count("NameError"), 0)

    def test_the_first_name_that_nothing_binds_ends_the_code_after_its_output(self):
        self.assertEqual(python_run("print(1)\nprint(2, x)\nprint(3)", "3.12.3"),
                         ("1\n", "Traceback (most recent call last):\n  File \"<string>\", line 2, in <module>\n"
                                 "NameError: name 'x' is not defined\n", 1))
        self.assertEqual(python_run("print(a, b)", "3.12.3")[1].splitlines()[-1], "NameError: name 'a' is not defined")
        self.assertEqual(python_run("f(g(h))", "3.12.3")[1].splitlines()[-1], "NameError: name 'f' is not defined")
        self.assertEqual(python_run("if 1:\n  y = zz\nprint(1)", "3.12.3")[1].splitlines()[-1],
                         "NameError: name 'zz' is not defined")

    def test_python_3_13_shows_the_line_and_colours_it_on_a_terminal(self):
        plain = python_run("print(1); undefined_name", "3.13.5")[1]
        self.assertEqual(plain.splitlines()[2:4], ["    print(1); undefined_name", "              ^^^^^^^^^^^^^^"])
        coloured = python_run("print(undefined_name)", "3.13.5", True)[1]
        self.assertEqual(coloured.splitlines()[2:4], ["    print(\x1b[1;31mundefined_name\x1b[0m)",
                                                      "          \x1b[1;31m^^^^^^^^^^^^^^\x1b[0m"])
        self.assertTrue(coloured.endswith(
            "\x1b[1;35mNameError\x1b[0m: \x1b[35mname 'undefined_name' is not defined\x1b[0m\n"))
        # no markers under a whole line
        self.assertEqual(python_run("undefined_name", "3.13.5")[1].splitlines()[2:3], ["    undefined_name"])
        self.assertEqual(len(python_run("undefined_name", "3.13.5")[1].splitlines()), 4)


def python_command(args, version, environ=None, targets=None):
    """Command_python started with args on a persona with this Python: (stdout, stderr, exit code, upstream), upstream
    when tpot_python_inline left the arguments to the code of Cowrie. targets is the fd table of the command."""
    values = {("shell", "python_version"): version}
    namespace = module_all(source("python"), {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(values)})
    cmd = namespace["Command_python"](list(args))
    if environ is not None:
        cmd.environ = environ
    if targets is not None:
        cmd.pp = types.SimpleNamespace(targets=targets)
    handled = namespace["tpot_python_inline"](cmd)
    return bytes(cmd.out).decode(), cmd.err, cmd.code, not handled


class HelpTest(unittest.TestCase):
    def recorded(self, key):
        # recorded as /usr/bin/python of CentOS 7, the persona answers with the name its usage error has
        return PY["help"][key]["stdout"].replace("usage: /usr/bin/python ", "usage: python ")

    def test_help_is_the_recorded_text_of_the_python_of_the_persona(self):
        for key, version in PY_VERSIONS.items():
            with self.subTest(python=key):
                self.assertEqual(python_command(["-h"], version), (self.recorded(key), "", 0, False))

    def test_the_help_options_are_read_as_python_reads_them(self):
        # recorded on 3.10, 3.11 and 3.13; the other Pythons are derived
        for args in (["-?"], ["--help"], ["-Eh"], ["-hc"], ["-V", "-h"], ["--version", "-h"], ["-h", "-V"],
                     ["-h", "-c", "print(1)"], ["-h", "-z"], ["-h", "x.py"], ["-h", "--version"]):
            with self.subTest(args=args):
                self.assertEqual(python_command(args, "3.11.2"), (self.recorded("3.11"), "", 0, False))

    def test_options_before_or_after_the_code_decide(self):
        # -c ends the option list; an unknown option before -h is Cowrie's usage error
        self.assertEqual(python_command(["-c", "print(1)", "-h"], "3.11.2"), ("1\n", "", 0, False))
        self.assertEqual(python_command(["-z", "-h"], "3.11.2")[3], True)
        self.assertEqual(python_command(["-V", "-c", "print(1)"], "3.11.2")[3], True)
        self.assertEqual(python_command(["-V"], "3.11.2")[3], True)


class DigitLimitTest(unittest.TestCase):
    def test_recorded_runs_match(self):
        wrong = []
        for key, entries in PY["digits"].items():
            for entry in entries:
                answer = python_run(entry["code"], PY_VERSIONS[key])
                if answer != (entry["stdout"], entry["stderr"], entry["rc"]):
                    wrong.append((key, entry["code"], answer[0][:30], answer[1][-120:], answer[2]))
        self.assertEqual(wrong, [])

    def test_python_2_has_no_limit(self):
        # derived: the limit came with 3.9.14, 3.10.7 and 3.11
        self.assertEqual(python_run("print(len(str(10**5000)))", "2.7.5"), ("5001\n", "", 0))
        self.assertEqual(python_run("print(len(str(int('9'*5000))))", "2.7.5"), ("5000\n", "", 0))

    def test_numbers_and_texts_that_would_take_long_are_not_computed(self):
        for code in ("print(10**10**9)", "print(2**(10**12))", "print('9'*10**10)", "x = 9**9**9\nprint(x)",
                     "print(len(str(1<<10**9)))", "print(len('ab'*10**9))", "print(int('9'*10**8))",
                     "import sys; sys.set_int_max_str_digits(0); print(len(str(10**300000)))",
                     "import sys; sys.set_int_max_str_digits(0); print(int('9'*10**6) > 0)"):
            with self.subTest(code=code):
                started = time.monotonic()
                self.assertEqual(python_run(code, "3.12.3"), ("", "", 0))
                self.assertLess(time.monotonic() - started, 2)

    def test_a_long_text_of_digits_is_past_the_limit(self):
        out, err, status = python_run("print(int('9'*10**6))", "3.12.3")
        self.assertEqual((out, status), ("", 1))
        self.assertTrue(err.endswith(
            "ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 1000000 digits; "
            "use sys.set_int_max_str_digits() to increase the limit\n"))

    def test_small_values_are_printed(self):
        for code, text in (("print(2**70)", "1180591620717411303424\n"), ("print('%d items' % 5)", "5 items\n"),
                           ("print('{} and {}'.format(1, 'b'))", "1 and b\n"), ("print(f'{1 + 1}!')", "2!\n"),
                           ("print(len('ab' * 3), -2**3, 7 // 2, 7 % 3, 1 << 4)", "6 -8 3 1 16\n"),
                           ("print(int(' 42 '), str(5) + 'x', repr(7), abs(-3))", "42 5x 7 3\n"),
                           ("print(1 < 2, 'a' == 'b', 3 >= 3)", "True False True\n"),
                           ("import sys; print(sys.get_int_max_str_digits())", "4300\n")):
            with self.subTest(code=code):
                self.assertEqual(python_run(code, "3.12.3"), (text, "", 0))

    def test_the_limit_option_of_the_command_line(self):
        digits = "print(len(str(10**5000)))"
        # derived: -X int_max_str_digits=N sets the limit, as sys.set_int_max_str_digits does
        self.assertEqual(python_command(["-X", "int_max_str_digits=0", "-c", digits], "3.11.2"),
                         ("5001\n", "", 0, False))
        error = python_command(["-X", "int_max_str_digits=1000", "-c", digits], "3.11.2")
        self.assertEqual(error[3:], (False,))
        self.assertTrue(error[1].endswith("ValueError: Exceeds the limit (1000 digits) for integer string conversion;"
                                          " use sys.set_int_max_str_digits() to increase the limit\n"))
        # a limit this does not know is no claim: what it would change is not printed
        self.assertEqual(python_command(["-X", "int_max_str_digits=bad", "-c", digits], "3.11.2"),
                         ("", "", 0, False))


class BudgetTest(unittest.TestCase):
    """The work of one python -c run is bounded as a whole: past it the run ends with the output it has (status 0,
    as for code this does not follow)."""

    def limited(self, code, version="3.12.3"):
        started = time.monotonic()
        answer = python_run(code, version)
        self.assertLess(time.monotonic() - started, 2)
        return answer

    def test_many_operations_on_big_numbers_share_one_budget(self):
        head = "print('a')\nimport sys;sys.set_int_max_str_digits(0);a=2**40000;b=a*a;"
        # the code is at most 64 KiB
        for body in ("s=str(b);" * 6500, "c=b%a;" * 9000, "c=b*b;" * 9000, "c=b**3;" * 9000, "c=b<<9;" * 9000):
            with self.subTest(body=body[:8]):
                self.assertEqual(self.limited(head + body), ("a\n", "", 0))

    def test_big_values_held_in_names_share_one_budget(self):
        code = "print('a')\n" + "".join(f"a{index}='a'*999999;" for index in range(3000))
        tracemalloc.start()
        try:
            answer = self.limited(code)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(answer, ("a\n", "", 0))
        self.assertLess(peak, 64 << 20)

    def test_containers_of_names_do_not_grow_without_bound(self):
        code = "x = (1, 1)\n" + "x = (x, x)\n" * 40 + "print(x)"
        tracemalloc.start()
        try:
            answer = self.limited(code)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(answer, ("", "", 0))
        self.assertLess(peak, 64 << 20)

    def test_the_work_of_a_normal_run_is_within_the_budget(self):
        code = "a = 2**5000\nb = a * a\nprint(len(str(b)))\nprint(10**1000 % 7, 3**500)\n" * 20
        out, err, status = self.limited(code)
        self.assertEqual((err, status), ("", 0))
        self.assertEqual(out.count("\n"), 40)

    def test_a_long_text_that_is_no_number_is_read_in_linear_time(self):
        for text in ("'1'*999999+'x'", "'1.'+'1'*999999+'x'", "'1'+' '*999999+'x'", "' '*999999+'x'",
                     "'1e'+'1'*999999+'x'"):
            with self.subTest(text=text[:12]):
                self.assertEqual(self.limited(f"print('a')\nprint(float({text}))"), ("a\n", "", 0))

    def test_float_of_a_text_still_prints_the_number(self):
        self.assertEqual(python_run("print(float('1.5'), float(' 2e3 '), float('.5'), float('7.'))", "3.12.3"),
                         ("1.5 2000.0 0.5 7.0\n", "", 0))

    def test_the_nearest_name_is_charged_by_the_size_of_each_candidate(self):
        python_run("1", "3.12.3")
        charged = []

        class Scope:
            def step(self):
                charged.append(200)

            def charge(self, units):
                charged.append(units)

        candidates = ["a_name_of_thirty_characters_xx" + str(index) for index in range(100)]
        _PYTHON[0]["tpot_nearest"]("another_name_of_thirty_chars_y", candidates, Scope())
        self.assertGreater(sum(charged), 100 * (200 + 29 * 30))

    def test_a_session_without_time_left_runs_a_floor_of_steps(self):
        python_run("1", "3.12.3")
        events = _PYTHON[0]["tpot_python_events"]
        self.assertEqual(events("x = 1\n" * 600 + "print(1)", "3.12.3", False, 4300, time.monotonic() - 1), ([], 0))
        written, status = events("print(1)", "3.12.3", False, 4300, time.monotonic() + 60)
        self.assertEqual(("".join(text for _, text in written), status), ("1\n", 0))

    def test_the_command_takes_the_deadline_of_the_session(self):
        values = {("shell", "python_version"): "3.12.3"}
        namespace = module_all(source("python"), {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(values)})
        asked = []

        def deadline(protocol, seconds=None):
            asked.append(protocol)
            return time.monotonic() - 1

        namespace["tpot_deadline"] = deadline
        cmd = namespace["Command_python"](["-c", "print(1)"])
        self.assertTrue(namespace["tpot_python_inline"](cmd))
        self.assertEqual((bytes(cmd.out), cmd.err, cmd.code, len(asked)), (b"1\n", "", 0, 1))
        self.assertIs(asked[0], cmd.protocol)


class SureTest(unittest.TestCase):
    """A NameError is the answer only while every statement before it was followed; where one that could have ended
    the run was not, the run stops there without a report (status 0, the output so far)."""

    def test_names_after_code_that_may_have_ended_the_run_are_silent(self):
        for code in ("import sys\ntry:\n    sys.exit(3)\nexcept SystemExit:\n    pass\nprint(zz)",
                     "import os\nos.system('x')\nprint(zz)", "import nosuchmodule\nprint(zz)",
                     "import sys\nif sys.argv[1:]:\n    sys.exit(2)\nprint(zz)", "for i in [1]:\n    pass\nprint(zz)",
                     "while 0:\n    pass\nprint(zz)", "with open('x'):\n    pass\nprint(zz)",
                     "class A:\n    pass\nprint(zz)", "x = [1][5]\nprint(zz)",
                     "import os\nprint(os.nothing)\nprint(zz)",
                     "def f(a: undefined): pass\nprint(zz)", "from os import path\nprint(zz)", "del zz\nprint(1)"):
            with self.subTest(code=code):
                self.assertEqual(python_run(code, "3.12.3")[1:], ("", 0))

    def test_the_output_before_the_unsure_statement_stays(self):
        self.assertEqual(python_run("print(1)\nimport os\nos.system('x')\nprint(zz)", "3.12.3"), ("1\n", "", 0))

    def test_followed_statements_keep_the_error(self):
        for code in ("import sys\nsys.stdout.write('a')\nprint(zz)", "import os\nx = 1\nprint(x, zz)",
                     "def f(): pass\nprint(zz)", "from __future__ import print_function\nprint(zz)",
                     "if 1:\n    y = 2\nprint(zz)", "import sys\nprint(sys.argv, zz)"):
            with self.subTest(code=code):
                self.assertIn("NameError: name 'zz' is not defined", python_run(code, "3.12.3")[1])

    def test_the_other_errors_of_the_evaluator_need_it_too(self):
        # the digit limit and a write UTF-8 cannot carry stop the run only if CPython got there
        self.assertEqual(python_run("import os\nos.system('x')\nprint(10**5000)", "3.12.3"), ("", "", 0))
        self.assertEqual(python_run("import os\nos.system('x')\nprint('\\ud800')", "3.12.3"), ("", "", 0))
        self.assertEqual(python_run("print(10**5000)", "3.12.3")[2], 1)


class AugAssignTest(unittest.TestCase):
    def test_recorded_runs(self):
        for entry in PY["augassign"]:
            version = PY_VERSIONS[entry["python"]]
            with self.subTest(python=entry["python"], code=entry["code"]):
                answer = python_run(entry["code"], version)
                if "TypeError" in entry["stderr"]:
                    # the operands are builtins of CPython this does not hold: nothing is claimed
                    self.assertEqual(answer, ("", "", 0))
                else:
                    self.assertEqual(answer, (entry["stdout"], entry["stderr"], entry["rc"]))


class LongNameTest(unittest.TestCase):
    def test_recorded_names_of_more_than_forty_characters(self):
        # the common start and end are cut off before the limit of 40 characters applies
        for key, rows in PY["long_names"].items():
            for row in rows:
                with self.subTest(python=key, code=row["code"][-24:]):
                    self.assertEqual(python_run(row["code"], PY_VERSIONS[key]),
                                     (row["stdout"], row["stderr"], row["rc"]))

    def test_the_candidate_limit_is_open_from_3_13(self):
        # derived from suggestions.c (3.10 - 3.12: 750 names are too many) and traceback.py (3.13: 751), not recorded
        python_run("1", "3.12.3")
        nearest = _PYTHON[0]["tpot_nearest"]
        names = ["abd"] * 750
        self.assertIsNone(nearest("abc", names, None))
        self.assertEqual(nearest("abc", names, None, True), "abd")
        self.assertIsNone(nearest("abc", names + ["abd"], None, True))


class StderrColourTest(unittest.TestCase):
    """Python 3.13 colours its report where stderr is a terminal, whatever stdout is (recorded: split)."""

    TARGETS = {
        "stdout_devnull": {1: ("devnull", None), 2: ("terminal", None)},
        "stdout_pipe": {1: ("capture", bytearray()), 2: ("terminal", None)},
        "stderr_pipe": {1: ("capture", bytearray()), 2: ("capture", bytearray())},
        "stderr_pipe_stdout_devnull": {1: ("devnull", None), 2: ("capture", bytearray())},
        "stderr_file": {1: ("terminal", None), 2: ("file", {})},
        "both_terminal": {1: ("terminal", None), 2: ("terminal", None)},
    }

    def test_recorded_redirections(self):
        self.assertEqual({entry["label"] for entry in PY["split"]}, set(self.TARGETS))
        for entry in PY["split"]:
            with self.subTest(label=entry["label"]):
                out, err, status, upstream = python_command(
                    ["-c", "print(undefined_name)"], "3.13.5", {"TERM": "xterm"}, self.TARGETS[entry["label"]])
                self.assertEqual((out, err.replace("\r\n", "\n"), status, upstream),
                                 ("", entry["stdout"].replace("\r\n", "\n"), 1, False))
                self.assertEqual("\x1b[" in err, entry["label"] in ("stdout_devnull", "stdout_pipe", "both_terminal"))

    def test_recorded_flags_drop_python_colors_only(self):
        for entry in PY["flags"]:
            with self.subTest(label=entry["label"]):
                kind = "terminal" if entry["tty"] else "capture"
                targets = {1: (kind, None), 2: (kind, None)}
                out, err, status, upstream = python_command(
                    [entry["flag"], "-c", "print(undefined_name)"], "3.13.5", dict(entry["env"]), targets)
                text = (entry["stdout"] if entry["tty"] else entry["stderr"]).replace("\r\n", "\n")
                self.assertEqual((err, status, upstream), (text, entry["rc"], False))

    def test_a_flag_of_the_scan_counts_wherever_it_stands(self):
        targets = {1: ("terminal", None), 2: ("terminal", None)}
        for args in (["-Ec", "print(undefined_name)"], ["-B", "-E", "-c", "print(undefined_name)"]):
            with self.subTest(args=args):
                err = python_command(args, "3.13.5", {"PYTHON_COLORS": "0"}, targets)[1]
                self.assertIn("\x1b[", err)
        err = python_command(["-c", "print(undefined_name)"], "3.13.5", {"PYTHON_COLORS": "0"}, targets)[1]
        self.assertNotIn("\x1b[", err)


class RecordedOptionsTest(unittest.TestCase):
    def test_option_order_of_the_command_line(self):
        for entry in PY["options"]:
            version = {"3.11": "3.11.2", "3.13": "3.13.5"}[entry["python"]]
            with self.subTest(python=entry["python"], args=entry["args"]):
                out, err, status, upstream = python_command(entry["args"], version)
                if upstream:
                    # left to the code of Cowrie: an unknown option, -V with -c
                    self.assertIn(entry["args"], (["-z", "-h"], ["-V", "-c", "print(1)"]))
                else:
                    self.assertEqual((out, err, status), (entry["stdout"], entry["stderr"], entry["rc"]))


if __name__ == "__main__":
    unittest.main()
