"""grep of the patched Cowrie reads its patterns as GNU grep does: BRE by default, ERE with -E, fixed strings with -F
(-P keeps Python's syntax); the BusyBox personas (grep_style busybox) read them as the regcomp of their C library
(glibc, uClibc, musl). The reference (fixtures/grep_reference.json) is the output of the real programs in containers,
in their default C locale; its -P rows are not used. fixtures/grep_output.json holds the output options, the context
options, the numbers beyond them and the status of -L, recorded the same way."""
import collections
import getopt
import json
import re
import shlex
import time
import unittest
import warnings
from pathlib import Path

from tests import test_awk
from tests.test_patch import FakeCommand, FakeConfig, module_parts

REFS = json.loads((Path(__file__).parent / "fixtures" / "grep_reference.json").read_text(encoding="utf-8"))
OUTPUT = json.loads((Path(__file__).parent / "fixtures" / "grep_output.json").read_text(encoding="utf-8"))
FILES = {path: text.encode("utf-8") for path, text in REFS["files"].items()}
DIRS = ("/", "/w", "/tmp", "/root")
_CLASSES = {}


def grep_classes(version, style="gnu", libc="glibc", banner="BusyBox"):
    """The grep classes of the patched fs.py, for a persona whose grep has this version, style and C library."""
    if (version, style, libc, banner) not in _CLASSES:
        source = (test_awk.patched_root() / "src/cowrie/commands/fs.py").read_text()
        _CLASSES[(version, style, libc, banner)] = module_parts(
            source, ["Command_grep", "Command_egrep", "Command_fgrep", "TpotGrepMatcher", "TpotGrepSyntax"],
            {"HoneyPotCommand": FakeCommand, "collections": collections, "getopt": getopt,
             "CowrieConfig": FakeConfig({("shell", "grep_version"): version, ("shell", "grep_style"): style,
                                         ("shell", "text_libc"): libc, ("shell", "text_banner"): banner})})
    return _CLASSES[(version, style, libc, banner)]


def run(version, args, input_data=None, command="Command_grep", files=None, style="gnu", libc="glibc",
        banner="BusyBox"):
    """(stdout, stderr, status) of the patched grep (egrep, fgrep) of a persona with that version and style."""
    cmd = grep_classes(version, style, libc, banner)[command](
        args, input_data=input_data, files=dict(FILES if files is None else files), dirs=DIRS, cwd="/w")
    cmd.start()
    return bytes(cmd.out).decode("utf-8", "surrogateescape"), cmd.err, cmd.code


def has_back_reference(pattern):
    return any(f"\\{n}" in pattern for n in "123456789")


def gnu_rows():
    """(mode, pattern, version, name, rc, selected lines, stderr) of every recorded row of a GNU grep."""
    for mode, pattern, groups in REFS["matrix"]:
        if mode == "P":
            continue
        for impls, rc, selected, err in groups:
            for name in impls:
                impl = REFS["implementations"][name]
                if impl["style"] == "gnu":
                    yield mode, pattern, impl["version"], name, rc, selected, err


def busybox_rows():
    """(mode, pattern, libc, name, rc, selected lines, stderr) of every recorded row of a BusyBox grep."""
    for mode, pattern, groups in REFS["matrix"]:
        if mode == "P":
            continue
        for impls, rc, selected, err in groups:
            for name in impls:
                impl = REFS["implementations"][name]
                if impl["style"] == "busybox":
                    yield mode, pattern, impl["libc"], name, rc, selected, err


class ReferenceTest(unittest.TestCase):
    def test_patterns_select_the_lines_of_the_real_grep(self):
        # every pattern of the matrix as BRE (no option), -E and -F on the same lines, for each GNU grep
        checked = 0
        for mode, pattern, version, name, rc, selected, err in gnu_rows():
            if rc == 0 and has_back_reference(pattern):
                continue  # a known gap, test_back_references_select_the_lines_of_the_real_grep
            args = ([] if mode == "G" else [f"-{mode}"]) + ["-e", pattern, "/w/lines.txt"]
            out, got_err, code = run(version, args)
            expected = "".join(REFS["lines"][i] + "\n" for i in selected)
            with self.subTest(impl=name, mode=mode, pattern=pattern):
                self.assertEqual((code, out, got_err), (rc, expected, err))
            checked += 1
        self.assertGreater(checked, 1000)

    @unittest.expectedFailure
    def test_back_references_select_the_lines_of_the_real_grep(self):
        # a known gap (OPEN-ITEMS): the automaton cannot run a back reference and backtracking re has no deadline
        wrong = []
        for mode, pattern, version, name, rc, selected, err in gnu_rows():
            if rc == 0 and has_back_reference(pattern):
                args = ([] if mode == "G" else [f"-{mode}"]) + ["-e", pattern, "/w/lines.txt"]
                expected = "".join(REFS["lines"][i] + "\n" for i in selected)
                if run(version, args)[0] != expected:
                    wrong.append((name, mode, pattern))
        self.assertEqual(wrong, [])

    # every recorded command line but the -P patterns (PCRE is not emulated: -P keeps Python's syntax)
    OPTIONS_COVERED = tuple(line for line, _ in REFS["options"] if not re.match(r"grep -P (-i )?'", line))

    @staticmethod
    def command_line(line):
        """(command, arguments, standard input) of a recorded command line; the one with a printf in front."""
        stdin = None
        if line.startswith("printf "):
            text, line = line.split(" | ", 1)
            stdin = shlex.split(text)[1].encode().decode("unicode_escape").encode()
        words = shlex.split(line)
        if stdin is None and not any(word.startswith("/w") or word == "-" for word in words[1:]):
            stdin = b""  # no file: the standard input, empty
        return {"grep": "Command_grep", "egrep": "Command_egrep", "fgrep": "Command_fgrep"}[words[0]], words[1:], stdin

    def check_options(self, style):
        seen = 0
        for line, groups in REFS["options"]:
            if line not in self.OPTIONS_COVERED:
                continue
            seen += 1
            command, words, stdin = self.command_line(line)
            for impls, rc, stdout, stderr in groups:
                for name in impls:
                    impl = REFS["implementations"][name]
                    if impl["style"] != style:
                        continue
                    with self.subTest(impl=name, line=line):
                        got = run(impl["version"] if style == "gnu" else "3.11", words, stdin, command, style=style,
                                  libc=impl["libc"], banner=impl["version"] if style == "busybox" else "BusyBox")
                        self.assertEqual(got, (stdout, stderr, rc))
        self.assertEqual(seen, len(self.OPTIONS_COVERED))
        self.assertEqual(len(REFS["options"]) - seen, 13)  # the -P rows

    def test_the_recorded_command_lines_of_gnu_grep(self):
        self.check_options("gnu")

    def test_busybox_patterns_select_the_lines_of_the_real_busybox_grep(self):
        # every pattern of the matrix as BRE, -E and -F for each recorded BusyBox: glibc, uClibc and musl read them
        # differently (the texts of the errors, \\d \\t \\b of musl, a ** and a leading * of the others)
        checked = 0
        for mode, pattern, libc, name, rc, selected, err in busybox_rows():
            if rc == 0 and has_back_reference(pattern):
                continue  # a known gap, test_busybox_back_references_select_the_lines_of_the_real_busybox_grep
            args = ([] if mode == "G" else [f"-{mode}"]) + ["-e", pattern, "/w/lines.txt"]
            out, got_err, code = run("3.11", args, style="busybox", libc=libc)
            expected = "".join(REFS["lines"][i] + "\n" for i in selected)
            with self.subTest(impl=name, mode=mode, pattern=pattern):
                self.assertEqual((code, out, got_err), (rc, expected, err))
            checked += 1
        self.assertGreater(checked, 1000)

    @unittest.expectedFailure
    def test_busybox_back_references_select_the_lines_of_the_real_busybox_grep(self):
        # the same known gap as for GNU grep (OPEN-ITEMS): glibc and uClibc select lines for \1, the matcher cannot
        wrong = []
        for mode, pattern, libc, name, rc, selected, err in busybox_rows():
            if rc == 0 and has_back_reference(pattern):
                args = ([] if mode == "G" else [f"-{mode}"]) + ["-e", pattern, "/w/lines.txt"]
                expected = "".join(REFS["lines"][i] + "\n" for i in selected)
                if run("3.11", args, style="busybox", libc=libc)[0] != expected:
                    wrong.append((name, mode, pattern))
        self.assertEqual(wrong, [])

    def test_the_recorded_command_lines_of_busybox(self):
        self.check_options("busybox")

    def test_busybox_has_no_warnings_and_the_fixed_string_wins(self):
        self.assertEqual(run("3.11", ["-E", "a)"], b"a)\n", style="busybox"), ("a)\n", "", 0))
        self.assertEqual(run("3.11", ["-F", "-E", "a+b"], b"a+b\naab\n", style="busybox"), ("a+b\n", "", 0))
        self.assertEqual(run("3.11", ["a+b"], b"a+b\naab\n", "Command_egrep", style="busybox"), ("aab\n", "", 0))
        self.assertEqual(run("3.11", ["-e", "a", "-e", "\\("], b"a\n", style="busybox")[1:],
                         ("grep: bad regex '\\(': Unmatched ( or \\(\n", 2))


class SyntaxTest(unittest.TestCase):
    def test_the_pattern_is_basic_unless_the_option_says_otherwise(self):
        data = b"a+b\naab\nx|y\nx\n(a)\n"
        self.assertEqual(run("3.11", ["a+b"], data)[0], "a+b\n")
        self.assertEqual(run("3.11", ["-G", "a+b"], data)[0], "a+b\n")
        self.assertEqual(run("3.11", ["-E", "a+b"], data)[0], "aab\n")
        self.assertEqual(run("3.11", ["x\\|y"], data)[0], "x|y\nx\n")
        self.assertEqual(run("3.11", ["(a)"], data)[0], "(a)\n")
        self.assertEqual(run("3.11", ["-F", "a+b"], data)[0], "a+b\n")
        self.assertEqual(run("3.11", ["-F", "-e", "x|y", "-e", "(a)"], data)[0], "x|y\n(a)\n")

    def test_egrep_and_fgrep_are_grep_with_e_and_f(self):
        self.assertEqual(run("3.11", ["x|y"], b"x\nz\n", "Command_egrep")[0], "x\n")
        self.assertEqual(run("3.11", ["x|y"], b"x\nx|y\n", "Command_fgrep")[0], "x|y\n")

    def test_the_patterns_of_several_options_are_read_one_by_one(self):
        # each pattern keeps its own anchors and its own syntax: a * at the start of one is a character
        data = b"xb\nab\ncx\nxc\n*d\nd\n"
        self.assertEqual(run("3.11", ["-E", "-e", "^a|b", "-e", "c$"], data)[0], "xb\nab\nxc\n")
        self.assertEqual(run("3.11", ["-e", "*d", "-e", "^c"], data)[0], "cx\n*d\n")
        self.assertEqual(run("3.11", ["-e", "a\nd"], data)[0], "ab\n*d\nd\n")
        self.assertEqual(run("3.11", ["a\nd"], data)[0], "ab\n*d\nd\n")

    def test_perl_patterns_keep_python_syntax(self):
        # -P is not touched
        self.assertEqual(run("3.11", ["-P", "a+b"], b"aab\na+b\n")[0], "aab\n")
        self.assertEqual(run("3.11", ["-P", "x(?=y)"], b"xy\nxz\n")[0], "")

    def test_warnings_of_grep_3_8_and_later(self):
        self.assertEqual(run("3.8", ["-E", "*a"], b"a\n")[1], "grep: warning: * at start of expression\n")
        self.assertEqual(run("3.7", ["-E", "*a"], b"a\n")[1], "")

    def test_a_parenthesis_that_is_closed_but_not_opened(self):
        self.assertEqual(run("2.20", ["-E", "a)"], b"a)\n")[1:], ("grep: Unmatched ) or \\)\n", 2))
        self.assertEqual(run("2.27", ["-E", "a)"], b"a)\n")[0], "a)\n")

    def test_a_pattern_beyond_the_syntax_is_an_error_not_a_crash(self):
        for pattern in ("a\\{1,\\}\\{", "a\\{2,1\\}", "a\\{x\\}", "[[.ab.]]"):
            with self.subTest(pattern=pattern):
                out, err, code = run("3.11", [pattern], b"a\n")
                self.assertEqual((out, code), ("", 2))
                self.assertTrue(err.startswith("grep: "), err)
        self.assertEqual(run("3.11", ["-E", "a{99999}"], b"a\n")[1:], ("grep: Regular expression too big\n", 2))
        self.assertEqual(run("3.11", ["-E", "a{" + "9" * 5000 + "}"], b"a\n")[1:],
                         ("grep: Regular expression too big\n", 2))
        self.assertEqual(run("2.20", ["a\\{40000\\}"], b"a\n")[1:], ("grep: Invalid content of \\{\\}\n", 2))

    def test_a_deep_nest_of_groups_is_too_big(self):
        self.assertEqual(run("3.11", ["-E", "(" * 5000 + ")" * 5000], b"a\n")[1:],
                         ("grep: regular expression too big\n", 2))
        self.assertEqual(run("3.11", ["-E", "(" * 255 + "a" + ")" * 255], b"a\n")[0], "a\n")

    def test_word_boundaries_of_gnu_grep(self):
        data = b"foo\nfoobar\nbar foo\n_foo\nfoo.bar\n"
        self.assertEqual(run("3.11", ["\\<foo\\>"], data)[0], "foo\nbar foo\nfoo.bar\n")
        self.assertEqual(run("3.11", ["\\bfoo\\b"], data)[0], "foo\nbar foo\nfoo.bar\n")
        self.assertEqual(run("3.11", ["foo\\>"], data)[0], "foo\nbar foo\n_foo\nfoo.bar\n")
        self.assertEqual(run("3.11", ["\\<foo"], data)[0], "foo\nfoobar\nbar foo\nfoo.bar\n")
        self.assertEqual(run("3.11", ["-E", "\\<(foo|bar)\\>"], data)[0], "foo\nbar foo\nfoo.bar\n")

    def test_two_different_matchers_conflict(self):
        conflict = ("", "grep: conflicting matchers specified\n", 2)
        for args, command in ((["-E", "-F", "a"], "Command_grep"), (["-F", "-E", "a"], "Command_grep"),
                              (["-G", "-E", "a"], "Command_grep"), (["-P", "-E", "a"], "Command_grep"),
                              (["-F", "a"], "Command_egrep"), (["-E", "a"], "Command_fgrep")):
            with self.subTest(args=args, command=command):
                self.assertEqual(run("3.11", args, b"a\n", command), conflict)
        # the same matcher twice, and egrep -E, are no conflict
        self.assertEqual(run("3.11", ["-E", "-E", "a|b"], b"a\n")[2], 0)
        self.assertEqual(run("3.11", ["-E", "a|b"], b"a\n", "Command_egrep")[2], 0)

    def test_the_default_basic_pattern_reads_python_constructs_as_characters(self):
        data = b"(?=p)p\np++\n(a)?(?(1)a|p)\nxyz\ntpot-warn\n"
        for pattern, want in (("(?=p)p", "(?=p)p\n"), ("p++", "p++\n"), ("(a)?(?(1)a|p)", "(a)?(?(1)a|p)\n"),
                              ("[[t]pot-warn", "tpot-warn\n")):
            with self.subTest(pattern=pattern), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.assertEqual(run("3.11", [pattern], data), (want, "", 0))
                self.assertEqual(run("3.11", ["-c", pattern], data)[0], "1\n")
                self.assertEqual([w for w in caught if w.category is FutureWarning], [])

    @unittest.expectedFailure
    def test_a_back_reference_of_the_default_basic_pattern(self):
        # a known gap (OPEN-ITEMS, as above): GNU selects the lines of a doubled a
        self.assertEqual(run("3.11", ["\\(a\\)\\1"], b"aa\nab\n")[0], "aa\n")

    def test_repeats_of_repeats_fold_and_stay_linear(self):
        data = b"b\nab\naab\nxc\n"
        for pattern, want in (("a+?b", "b\nab\naab\n"), ("a?+b", "b\nab\naab\n"), ("a++b", "ab\naab\n"),
                              ("a??b", "b\nab\naab\n"), ("a**b", "b\nab\naab\n"), ("(a*)*b", "b\nab\naab\n")):
            with self.subTest(pattern=pattern):
                self.assertEqual(run("3.11", ["-E", pattern], data)[0], want)
        syntax = grep_classes("3.11")["TpotGrepSyntax"]
        begin = time.monotonic()
        for pattern in ("a" + "*" * 60000, "a" + "+?" * 30000):
            self.assertEqual(syntax.regex([pattern], "E", (3, 11), lambda text: None), "a*")
        # repeats that do not fold nest: past 64 of them the pattern is too big, at once
        self.assertEqual(run("3.11", ["-E", "a" + "{1}" * 60], b"a\n"), ("a\n", "", 0))
        too_big = ("grep: regular expression too big\n", 2)
        self.assertEqual(run("3.11", ["-E", "a" + "{1}" * 20000], b"a\n")[1:], too_big)
        self.assertLess(time.monotonic() - begin, 2.0)

    def test_the_parser_stops_at_the_deadline_of_the_session(self):
        syntax = grep_classes("3.11")["TpotGrepSyntax"]
        with self.assertRaises(syntax.Timeout):
            syntax.regex(["a" * 5000], "E", (3, 11), lambda text: None, until=time.monotonic() - 1)
        self.assertEqual(syntax.regex(["a" * 5000], "E", (3, 11), lambda text: None, until=time.monotonic() + 60),
                         "a" * 5000)

    def test_an_interval_up_to_32767_is_beyond_the_automaton(self):
        # derived, not recorded: GNU accepts up to RE_DUP_MAX (32767), here the automaton limit refuses the pattern
        self.assertEqual(run("3.11", ["a\\{1,32767\\}"], b"a\n")[1:], ("grep: regular expression too big\n", 2))

    def test_the_matcher_reads_lookarounds_of_one_set(self):
        matcher = grep_classes("3.11")["TpotGrepMatcher"]
        for text, hit, miss in ((rb"(?<!\w)(?=\w)f", b"x foo", b"xfoo"), (rb"o\b(?<=\w)", b"foo bar", b"fox"),
                                (rb"a(?!\W)", b"ab", b"a b"), (rb"a(?=\W|$)", b"a b", b"ab")):
            found = matcher(text)
            self.assertEqual(found.regular, text != rb"a(?=\W|$)", text)
            if found.regular:
                self.assertTrue(found.search(hit), text)
                self.assertFalse(found.search(miss), text)
        # a lookaround of a set that splits a class of bytes is beyond the automaton
        self.assertFalse(matcher(rb"(?=a)a").regular)


def recorded_cases(kind):
    """(image, its facts, stdin, arguments, (stdout, stderr, status)) of every recorded case of the set `kind` on
    every image that ran it, from fixtures/grep_output.json."""
    for case in OUTPUT["cases"]:
        if case["set"] != kind:
            continue
        args = []
        for arg in case["args"]:
            number = arg.get("number", arg) if isinstance(arg, dict) else arg
            digits = "9" * number["digits"] if isinstance(number, dict) else number
            args.append((arg.get("prefix", "") if isinstance(arg, dict) else "") + digits)
        for image, (stdout, stderr, status) in case["results"].items():
            for index, arg in enumerate(args):
                stdout, stderr = (text.replace(f"<ARG:{index}>", arg.lstrip("-")) for text in (stdout, stderr))
            yield image, OUTPUT["images"][image], case["stdin"].encode(), args, (stdout, stderr, status)


def run_recorded(info, args, stdin):
    """The patched grep as the persona of a recorded image: GNU grep of its version, or its BusyBox on its C library."""
    if info["style"] == "gnu":
        return run(info["version"], args, stdin)
    return run("3.11", args, stdin, style="busybox", libc=info["libc"], banner=info["version"])


class OutputOptionsTest(unittest.TestCase):
    """-o, -n, -c, -L, the context options and -m with their output and the numbers beyond them, as recorded in
    fixtures/grep_output.json (the images and their digests are in its _source)."""

    # the recorded BusyBox prints a third foo where a word is part of another, not modelled
    NOT_MODELLED = (("busybox", ["-ow", "foo"]),)

    def test_the_recorded_output_options(self):
        checked = 0
        for image, info, stdin, args, want in recorded_cases("output"):
            if (info["style"], args) in self.NOT_MODELLED:
                continue
            with self.subTest(image=image, args=[a[:30] for a in args]):
                self.assertEqual(run_recorded(info, args, stdin), (want[0], want[1], want[2]))
            checked += 1
        self.assertGreater(checked, 400)

    def test_the_recorded_status_of_l_upper(self):
        # GNU 2.20 to 3.11 and BusyBox 1.27 to 1.32 end with the status of the selected lines, BusyBox 1.33 and later
        # with 0 where a file is listed
        checked = 0
        for image, info, stdin, args, want in recorded_cases("status"):
            with self.subTest(image=image, args=args):
                self.assertEqual(run_recorded(info, args, stdin), (want[0], want[1], want[2]))
            checked += 1
        self.assertEqual(checked, 2 * len(OUTPUT["images"]))

    def test_no_recorded_number_crashes_the_command(self):
        # the recorded numbers around the limits of an int, an unsigned int and 64 bits are in the recorded cases above
        for digits in (30, 5000, 100000):
            for args in (["-A", "9" * digits, "x"], ["-B", "9" * digits, "x"], ["-C", "9" * digits, "x"],
                         ["-m", "9" * digits, "x"]):
                with self.subTest(args=[args[0], len(args[1])]):
                    self.assertEqual(run("3.11", args, b"a\nx\nb\n")[1:], ("", 0))
                    self.assertEqual(run("3.11", args, b"a\nx\nb\n", style="busybox")[2], 2)

    def test_a_huge_number_bundled_with_other_options(self):
        # recorded for 30 digits as a word of its own; bundled with other options and the cut at 21 digits derived
        # from the GNU source, not recorded
        for args, digits in ((["-1" + "0" * 30 + "n", "x"], "1" + "0" * 20), (["-n" + "7" * 30, "x"], "7" * 21)):
            self.assertEqual(run("3.11", args, b"a\nx\nb\n")[1:],
                             (f"grep: {digits}...: invalid context length argument\n", 2))
        self.assertEqual(run("3.11", ["-n" + "7" * 21, "x"], b"a\nx\nb\n"), ("1-a\n2:x\n3-b\n", "", 0))

    def test_a_selected_line_after_m_is_context_for_gnu_grep(self):
        # recorded without -n (x\nb\nx); its mark "-" with -n is derived from GNU's output of context, not recorded
        self.assertEqual(run("3.8", ["-n", "-m1", "-A2", "x"], b"a\nx\nb\nx\nc\n")[0], "2:x\n3-b\n4-x\n")

    def test_the_deadline_stops_a_long_run_of_o_writes(self):
        namespace = grep_classes("3.11")
        matcher, clock = namespace["TpotGrepMatcher"], [0.0]

        def spans(self, line, until=None):
            clock[0] = 1000.0  # the time passes while the matches are found
            return [(i, i + 1) for i in range(len(line))]

        original = matcher.spans, namespace["tpot_deadline"], time.monotonic
        matcher.spans, namespace["tpot_deadline"], time.monotonic = spans, (lambda protocol: 10.0), (lambda: clock[0])
        try:
            out, err, code = run("3.11", ["-o", "."], b"a" * 20000 + b"\n")
        finally:
            matcher.spans, namespace["tpot_deadline"], time.monotonic = original
        self.assertEqual((err, code), ("grep: memory exhausted\n", 2))
        self.assertEqual(len(out), 2 * 4096)

    def test_the_matcher_finds_the_longest_match_from_the_left(self):
        matcher = grep_classes("3.11")["TpotGrepMatcher"]
        self.assertEqual(matcher(rb"a|ab").spans(b"xabab"), [(1, 3), (3, 5)])
        self.assertEqual(matcher(rb"x*").spans(b"axxb"), [(1, 3)])
        self.assertEqual(matcher(rb"^a").spans(b"aaa"), [(0, 1)])
        self.assertEqual(matcher(rb"\bab").spans(b"ab abab"), [(0, 2), (3, 5)])
        self.assertEqual(matcher(rb"(?<!\w)(?:a)(?!\w)").spans(b"a ab a"), [(0, 1), (5, 6)])

    def test_a_huge_before_context_ends_within_the_deadline_and_the_cap(self):
        # the buffer of -B holds at most 8 MiB, the oldest lines go first (a user decision, real grep keeps all)
        data = b"a\n" * 524000 + b"b\nx\n"
        for args in (["-B", "100000", "x"], ["-B", "1000000000", "x"], ["-C", "1000000000", "x"]):
            with self.subTest(args=args):
                started = time.monotonic()
                out, err, code = run("3.11", args, data)
                self.assertLess(time.monotonic() - started, 10.0)
                self.assertEqual((err, code), ("", 0))
                self.assertTrue(out.endswith("a\nb\nx\n"))
                self.assertLessEqual(len(out), 8 * 1024 * 1024 + 16)
        grep = grep_classes("3.11")["Command_grep"](
            ["-B", "1000000000", "x"], input_data=data, files={}, dirs=DIRS, cwd="/w")
        grep.start()
        self.assertLessEqual(grep.tpot_held, 8 * 1024 * 1024)

    def test_o_stops_at_the_deadline_on_a_long_line(self):
        matcher = grep_classes("3.11")["TpotGrepMatcher"](rb"a*b|c")
        started = time.monotonic()
        self.assertIsNone(matcher.spans(b"a" * 200000 + b"c", time.monotonic() + 0.2))
        self.assertTrue(matcher.spent)
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
