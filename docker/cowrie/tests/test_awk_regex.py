"""awk of the persona on an invalid regular expression: the texts and the exit status of the real awk. The
reference (fixtures/awk_regex.json) is gawk 4.0.2 of centos:7, gawk 5.1.0 of rockylinux:9, mawk 1.3.4 of
debian:12 / ubuntu:22.04 / ubuntu:24.04 / debian:13, mawk 1.3.3 of debian:9 / debian:10 (the awk of the EdgeRouter X)
and the awk of the busybox images, each fed `a[(b`. fixtures/awk_static.json holds when each awk compiles a regular
expression: with the program, before BEGIN runs (a /regex/; for mawk also a string constant where a regex goes)."""
import json
import re
import shlex
import unittest
from pathlib import Path

from tests.test_awk import awk

REFS = json.loads((Path(__file__).parent / "fixtures" / "awk_regex.json").read_text(encoding="utf-8"))
STATIC = json.loads((Path(__file__).parent / "fixtures" / "awk_static.json").read_text(encoding="utf-8"))
# image -> (awk_style, awk_version, text_libc) of the persona that has that awk
STYLES = {
    "centos_7": ("gawk", "4.0.2", None),
    "rockylinux_9": ("gawk", "5.1.0", None),
    "debian_12": ("mawk", "1.3.4 20200120", None),
    "ubuntu_22.04": ("mawk", "1.3.4 20200120", None),
    "ubuntu_24.04": ("mawk", "1.3.4 20240123", None),
    "debian_13": ("mawk", "1.3.4 20250131", None),
    # mawk 1.3.3 (system.MAWK["stretch"]: the EdgeRouter X)
    "debian_9": ("mawk", "1.3.3", None),
    "debian_10": ("mawk", "1.3.3", None),
}
# the version line is tested in test_awk (help and version of each persona)
LEFT_OUT = {"awk -W version 2>&1 | head -1", "awk --version | head -1",
            # match() and split() (with its array) are outside the persona's awk: it ends with status 2 there,
            #   as on every feature it leaves out (test_awk.test_left_out_awk_ends_without_output)
            """echo 'a[(b' | awk '{print match($0, "(")}'""",
            """echo 'a[(b' | awk '{print match($0, /a{2/)}'""",
            """echo 'a[(b' | awk '{split($0, x, "[("); print x[2]}'"""}


def style_of(key, entry):
    if key in STYLES:
        return STYLES[key]
    libc = entry["kind"].split()[1]
    version = re.match(r"busybox_([0-9.]+)", key).group(1)
    return "busybox", f"BusyBox v{version} (2023-05-18 22:34:17 UTC)", libc


def call(case):
    """(awk arguments, stdin) of a recorded line `echo 'a[(b' | awk ...`."""
    found = re.fullmatch(r"echo 'a\[\(b' \| awk (.*)", case)
    return shlex.split(found.group(1)), "a[(b\n"


def static_call(case):
    """(awk arguments, stdin) of a line `awk PROGRAM </dev/null` or `echo x | awk PROGRAM` of awk_static.json."""
    words, data = shlex.split(case), ""
    if words[:3] == ["echo", "x", "|"]:
        words, data = words[3:], "x\n"
    if words[-1] == "</dev/null":
        words = words[:-1]
    return words[1:], data


class AwkRegexTest(unittest.TestCase):
    def test_every_recorded_case(self):
        ran = 0
        for key, entry in sorted(REFS["images"].items()):
            style, version, libc = style_of(key, entry)
            for case, expected in sorted(entry["cases"].items()):
                if case in LEFT_OUT:
                    continue
                args, data = call(case)
                with self.subTest(image=key, case=case):
                    self.assertEqual(list(awk(style, args, data, version=version, libc=libc)), expected)
                ran += 1
        self.assertGreaterEqual(ran, 161)

    def test_a_regex_compiles_with_the_program(self):
        ran = 0
        for key, entry in sorted(STATIC["images"].items()):
            style, version, libc = style_of(key, entry)
            for case, expected in sorted(entry["cases"].items()):
                args, data = static_call(case)
                with self.subTest(image=key, case=case):
                    self.assertEqual(list(awk(style, args, data, version=version, libc=libc)), expected)
                ran += 1
        self.assertEqual(ran, 28)

    def test_every_error_of_the_program_is_named(self):
        # gawk and mawk name every regex of the program that does not compile, on its line, then end
        self.assertEqual(awk("gawk", ['BEGIN{print "x"}\n/a(/\n/b)/ && /(c/'], "", version="5.1.0"),
                         ("", "awk: cmd. line:2: error: Unmatched ( or \\(: /a(/\n"
                              "awk: cmd. line:3: error: Unmatched ( or \\(: /(c/\n", 1))
        # mawk 1.3.3 ends a /regex/ at its first / (/[c/ is one), and a ) without its ( is an error there
        program = 'BEGIN{print "x"}\n/a(/\n/b)/ && /[c/'
        self.assertEqual(awk("mawk", [program], "", version="1.3.3"),
                         ("", "awk: line 2: regular expression compile failed (missing ')')\na(\n"
                              "awk: line 3: regular expression compile failed (missing '(')\nb)\n"
                              "awk: line 3: regular expression compile failed (bad class -- [], [^] or [)\n[c\n", 2))
        # BusyBox ends at the first
        self.assertEqual(awk("busybox", [program], "", version="BusyBox v1.36.1", libc="musl"),
                         ("", "awk: bad regex 'a(': Missing ')'\n", 1))
        # a string constant: mawk compiles it where a regex goes (the right of ~, sub and gsub), not elsewhere
        self.assertEqual(awk("mawk", ['BEGIN{print "x"} $0 ~ "a(" {print}'], "", version="1.3.4 20200120"),
                         ("", "awk: line 1: regular expression compile failed (missing ')')\na(\n", 2))
        self.assertEqual(awk("mawk", ['BEGIN{print "a("}'], "", version="1.3.4 20200120"), ("a(\n", "", 0))


if __name__ == "__main__":
    unittest.main()
