"""awk of the persona with arrays, split() and match(): the texts, the exit status and the order of
`for (k in a)` of the real awk (fixtures/awk_arrays.json: gawk 4.0.2 and 5.1.0, mawk 1.3.3 and 1.3.4, the awk of
the busybox images; fixtures/awk_big.json: large arrays in gawk 4.0.2, 4.1.4, 5.1.0 and 5.2.1, mawk 1.3.3 and
1.3.4, BusyBox 1.36.1). The order of a for-in loop is the order of each awk's hash table as the program built it
(the tables of the patch: gawk's string table with the sdbm hash, 13, 127, 1021 ... buckets and integers after
the strings from 4.1 on; mawk's lists; BusyBox's hashidx with 61, 251 ... buckets)."""
import json
import re
import shlex
import unittest
from pathlib import Path

from tests.test_awk import awk
from tests.test_awk_regex import STYLES, style_of

REFS = json.loads((Path(__file__).parent / "fixtures" / "awk_arrays.json").read_text(encoding="utf-8"))["images"]
# every clash of mawk (up to five) with its line, BusyBox's `for ((k) in a)` and its "Not an array" at run time
CLASHES = json.loads((Path(__file__).parent / "fixtures" / "awk_clashes.json").read_text(encoding="utf-8"))["images"]
# for-in with large arrays: growth of the table, the order within a bucket, deletes, mawk's move to the front
BIG = json.loads((Path(__file__).parent / "fixtures" / "awk_big.json").read_text(encoding="utf-8"))["images"]
# image of awk_big.json -> (awk_style, awk_version, text_libc); debian:12 has gawk 5.2.1 installed there
BIG_STYLES = {
    "centos_7": ("gawk", "4.0.2", None), "debian_9": ("gawk", "4.1.4", None),
    "rockylinux_9": ("gawk", "5.1.0", None), "debian_12": ("gawk", "5.2.1", None),
    "debian_10": ("mawk", "1.3.3", None), "ubuntu_22.04": ("mawk", "1.3.4 20200120", None),
    "ubuntu_24.04": ("mawk", "1.3.4 20240123", None), "debian_13": ("mawk", "1.3.4 20250131", None),
    "busybox_1.36.1-musl": ("busybox", "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)", "musl"),
}
# calls and builtin names where the persona answers another way than the real awk, recorded in containers
#   (fixtures/awk_departures.json: busybox 1.25.1 / 1.28 / 1.36.1, mawk 1.3.3 / 1.3.4, gawk 5.1.0)
DEPART = json.loads((Path(__file__).parent / "fixtures" / "awk_departures.json").read_text(encoding="utf-8"))
# image of awk_departures.json -> (awk_style, awk_version, text_libc, [shell] arch)
DEPART_STYLES = {
    "busybox_1.36.1-musl": ("busybox", "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)", "musl", "linux-aarch64-lsb"),
    "busybox_1.28": ("busybox", "BusyBox v1.28.4 (2018-05-22 16:59:59 UTC)", "uclibc", "linux-aarch64-lsb"),
    "busybox_1.25.1": ("busybox", "BusyBox v1.25.1 (2016-10-07 18:17:00 UTC)", "uclibc", "linux-x64-lsb"),
    "debian_10": ("mawk", "1.3.3", None, "linux-aarch64-lsb"),
    "ubuntu_22.04": ("mawk", "1.3.4 20200120", None, "linux-aarch64-lsb"),
    "ubuntu_24.04": ("mawk", "1.3.4 20240123", None, "linux-aarch64-lsb"),
    "debian_13": ("mawk", "1.3.4 20250131", None, "linux-aarch64-lsb"),
    "rockylinux_9": ("gawk", "5.1.0", None, "linux-aarch64-lsb"),
}
SILENT = ("", "", 2)  # a part the persona's awk leaves out
CALLS = ("strftime", "mktime", "systime", "and", "or", "xor", "compl", "lshift", "rshift", "gensub", "asort", "asorti",
         "patsplit", "strtonum", "isarray", "typeof")
# the builtins of each awk among CALLS, by the name of a recorded case `function_<name>` (the redefinition of a builtin)
BUILTINS = {"gawk": CALLS, "busybox": CALLS[:10], "mawk": CALLS[:3]}


def departure(style, version, case, real):
    """What the persona answers where it departs from the real awk of the case (None: it answers the same)."""
    if case.startswith("extra_args"):
        return SILENT  # mawk stops with its text, gawk warns and runs it, BusyBox runs it
    if case in ("undef_l3", "undef_l5_trailing", "undef_after_func") and style != "mawk":
        return (real[0], re.sub(r"line:\d+", "line:1", real[1]), real[2])  # the persona says line 1
    if style == "mawk" and case == "scalar_to_array":
        return ("ok\n", "", 0)  # no type error of a call that mixes an array and a scalar
    if style == "mawk" and case == "array_to_scalar":
        return ("1\n", "", 0)
    if style == "gawk" and case == "array_to_scalar":
        return (real[0], real[1].replace("`a (from b)'", "`a'"), real[2])
    name = case.removeprefix("function_")
    if style != "mawk" or version != "1.3.3":
        if case == "strftime" or (case.startswith("function_") and name in BUILTINS[style]):
            return SILENT  # a builtin of that awk the persona leaves out
    return None


def call(case):
    """(awk arguments, stdin) of a recorded line: `awk ...`, `echo X | awk ...` or `printf 'X' | awk ...`."""
    found = re.fullmatch(r"(?:echo (\S+|'[^']*') \| |printf '([^']*)' \| )?awk (.*)", case)
    data = ""
    if found.group(1) is not None:
        data = found.group(1).strip("'") + "\n"
    elif found.group(2) is not None:
        data = found.group(2).encode().decode("unicode_escape")
    return shlex.split(found.group(3)), data


class AwkArraysTest(unittest.TestCase):
    maxDiff = None

    def test_every_recorded_case(self):
        count = 0
        for key, entry in REFS.items():
            style, version, libc = style_of(key, entry)
            for case, expected in entry["cases"].items():
                args, data = call(case)
                with self.subTest(image=key, case=case):
                    self.assertEqual(awk(style, args, data, version=version, libc=libc), tuple(expected))
                count += 1
        self.assertGreater(count, 1000)

    def test_large_arrays_in_the_order_of_each_awk(self):
        count = 0
        for key, (style, version, libc) in BIG_STYLES.items():
            entry = BIG[key]
            for case, expected in entry["cases"].items():
                if case in entry["groups"]["version"]:
                    continue
                with self.subTest(image=key, case=entry["meta"][case]):
                    self.assertEqual(awk(style, call(case)[0], "", version=version, libc=libc,
                                         arch="linux-aarch64-lsb"), tuple(expected))
                count += 1
        self.assertEqual(count, 40 * len(BIG_STYLES))

    def test_every_clash_and_the_busybox_forms(self):
        count = 0
        for key, entry in CLASHES.items():
            style, version, libc = style_of(key, entry)
            for case in entry["cases"]:
                with self.subTest(image=key, program=case["program"]):
                    got = awk(style, [case["program"]], "", version=version, libc=libc)
                    self.assertEqual(got, (case["out"], case["err"], case["rc"]))
                count += 1
        self.assertGreater(count, 40)

    def test_departures_of_the_persona_and_every_other_call_as_recorded(self):
        count = departed = 0
        for key, (style, version, libc, arch) in DEPART_STYLES.items():
            for case, real in DEPART["images"][key]["cases"].items():
                ours = departure(style, version, case, real)
                with self.subTest(image=key, case=case):
                    got = awk(style, [DEPART["programs"][case]], "", version=version, libc=libc, arch=arch)
                    if ours is None:
                        self.assertEqual(got, tuple(real))
                    else:
                        self.assertNotEqual(ours, tuple(real))
                        self.assertEqual(got, ours)
                        departed += 1
                count += 1
        self.assertEqual((count, departed), (8 * 27, 107))  # 17 per BusyBox, 5 + 9 per mawk, 24 gawk

    def test_busybox_builtins_of_versions_without_an_image_follow_their_awk_c(self):
        # derived from awk.c of 1.14.1 (no mktime), 1.19.4 and 1.24.1 (as the recorded 1.25.1), not recorded
        recorded = DEPART["images"]["busybox_1.25.1"]["cases"]
        for version, ordinary in (("1.14.1", "mktime"), ("1.19.4", None), ("1.24.1", None)):
            for case, real in recorded.items():
                if case != "strftime" and not case.startswith("function_"):
                    continue
                if case == f"function_{ordinary}":  # a name of no builtin there: the function is defined and called
                    want = tuple(recorded["function_asort"])
                else:
                    want = departure("busybox", version, case, real) or tuple(real)
                with self.subTest(version=version, case=case):
                    got = awk("busybox", [DEPART["programs"][case]], "", libc="uclibc", arch="linux-x64-lsb",
                              version=f"BusyBox v{version} (2016-10-07 18:17:00 UTC)")
                    self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()
