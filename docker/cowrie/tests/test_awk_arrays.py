"""awk of the persona with arrays, split() and match(): the texts, the exit status and the order of
`for (k in a)` of the real awk (fixtures/awk_arrays.json: gawk 4.0.2 and 5.1.0, mawk 1.3.3 and 1.3.4, the awk of
the busybox images). The order of a for-in loop is the order of each awk's hash table (tpot_awk_hash_order of
the patch, with the hash of gawk 4.0 (sdbm, 13 buckets), gawk 4.1 and later (the ELF hash, 16 buckets, integers
first), mawk 1.3.3 (its ahash, 64), mawk 1.3.4 (FNV-1a, 64) and BusyBox (hashidx, 61))."""
import json
import re
import shlex
import unittest
from pathlib import Path

from tests.test_awk import awk
from tests.test_awk_regex import STYLES, style_of

REFS = json.loads((Path(__file__).parent / "fixtures" / "awk_arrays.json").read_text(encoding="utf-8"))["images"]
# what the persona's awk leaves out or does another way, with the reason: (case, style) -> what it answers
#   instead (None: the recorded answer, but only its first line of stderr)
DIFFERENT = {
    # a function of the program: outside the persona's awk, which ends with status 2 without a word
    ("awk 'function f(x) {return x+1} BEGIN{print f(1)}'", "gawk"): ("", "", 2),
    ("awk 'function f(x) {return x+1} BEGIN{print f(1)}'", "mawk"): ("", "", 2),
    ("awk 'function f(x) {return x+1} BEGIN{print f(1)}'", "busybox"): ("", "", 2),
    # BusyBox takes `for ((k) in a)` and says "Not an array" to `delete a[1], a[2]`; the persona's awk has the
    #   syntax errors of gawk and mawk there
    ("awk 'BEGIN{a[1]=1; for ((k) in a) print k}'", "busybox"): ("", "awk: cmd. line:1: Unexpected token\n", 1),
    ("awk 'BEGIN{a[1]=1; delete a[1], a[2]}'", "busybox"): ("", "awk: cmd. line:1: Unexpected token\n", 1),
    # mawk names every clash of a name used as an array and as a scalar, the persona's awk the first
    ("awk 'BEGIN{a[1]; delete a[2]; print length(a); print length(b); b[1]=1}'", "mawk"): None,
    ("awk 'BEGIN{a[1]=1; a[2]=2; delete a[1]; print length(a), (1 in a); delete a; print length(a)}'", "mawk"): None,
}


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
                other = DIFFERENT.get((case, style), "")
                with self.subTest(image=key, case=case):
                    got = awk(style, args, data, version=version, libc=libc)
                    if other is None:
                        self.assertEqual((got[0], got[2]), (expected[0], expected[2]))
                        self.assertEqual(got[1], "".join(expected[1].splitlines(keepends=True)[:1]))
                    elif other:
                        self.assertEqual(got, tuple(other))
                    else:
                        self.assertEqual(got, tuple(expected))
                count += 1
        self.assertGreater(count, 1000)


if __name__ == "__main__":
    unittest.main()
