"""wc, seq and sort of the patched Cowrie (patch_text_tools): GNU coreutils on servers, BusyBox on
devices. The reference texts (fixtures/text_tools.json) are the outputs of the real programs in
containers of debian:12, debian:13, ubuntu:24.04, ubuntu:22.04, centos:7 and busybox images
(glibc, uClibc and musl builds), run on the files of FILES with LC_ALL=C.UTF-8."""
import ast
import codecs
import json
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, budget_names, module_all
from tpot_personas import catalog, system
from tpot_personas.identity import draw

REFS = json.loads((Path(__file__).parent / "fixtures" / "text_tools.json").read_text(encoding="utf-8"))
FILES = {
    "/tmp/p.txt": b"root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
                  b"bin:x:2:2:bin:/bin:/usr/sbin/nologin\n",
    "/tmp/w.txt": b"one two  three\n\tfour\nfive",
    "/tmp/s.txt": b"10 b\n9 a\n100 c\n9 a\n-3 d\n2.5 e\n b x\nB y\n",
    "/tmp/k.txt": b"x:3:z\ny:1:a\nz:2:m\nw:10:b\n",
    "/tmp/u.txt": "café naïve\n".encode(),
    "/tmp/h.txt": b"1K\n2M\n512\n3G\n1.5K\n",
    "/tmp/e.txt": b"",
}
DIRS = {"/", "/tmp", "/tmp/d", "/root", "/proc"}
ECHO = b"a b\n"
# how the case lines of the reference run reach one command: (command, args, stdin, lines to compare)
PIPES = {"echo a b | ": ECHO, "printf '' | ": b"", "cat p.txt | ": FILES["/tmp/p.txt"],
         "cat s.txt | ": FILES["/tmp/s.txt"]}
# a redirect gives a regular file on stdin (GNU wc pads to its size); Cowrie hands over the data
# as a pipe, and uniq is not part of this test
LEFT_OUT = {"wc - < p.txt", "wc -lc - < p.txt", "cut -d: -f1 p.txt | sort | uniq -c | sort -rn"}
# coreutils 8.22 prints 1 to 10 for `seq 1 -0` (a quirk of its parser that is not modelled)
LEFT_OUT_OF = {"centos_7": {"seq 1 -0"}}


def split_case(case):
    stdin, head = None, None
    for prefix, data in PIPES.items():
        if case.startswith(prefix):
            case, stdin = case[len(prefix):], data
    if case.endswith(" | head -3"):
        case, head = case[:-len(" | head -3")], 3
    import shlex
    words = shlex.split(case)
    return words[0], words[1:], stdin, head


class Fs:
    def __init__(self, files=None, dirs=None):
        self.files = dict(FILES if files is None else files)
        self.dirs = set(DIRS if dirs is None else dirs)

    def resolve_path(self, path, cwd):
        return posixpath.normpath(posixpath.join(cwd, path))

    def exists(self, path):
        return path in self.files or path in self.dirs

    def isdir(self, path):
        return path in self.dirs

    def file_contents(self, path):
        if path in self.dirs:
            raise IsADirectoryError(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    # the parts of Cowrie's HoneyPotFilesystem a file written by sort -o needs (as tee uses them)
    def mkfile(self, path, uid, gid, size, mode):
        if posixpath.dirname(path) not in self.dirs:
            raise FileNotFoundError(path)
        self.files[path] = b""
        self.nodes = getattr(self, "nodes", {})
        self.nodes[path] = [posixpath.basename(path), 2, uid, gid, size, mode, 0, [], None, None]
        return True

    def update_size(self, path, size):
        self.nodes[path][4] = size

    def getfile(self, path, follow_symlinks=True):
        return getattr(self, "nodes", {}).get(path)

    def node_size(self, path):
        return len(self.files.get(path, b""))


class Base:
    """The parts of HoneyPotCommand the commands use."""

    def __init__(self, args, input_data=None, fs=None, cwd="/tmp"):
        self.args, self.input_data, self.cwd = list(args), input_data, cwd
        self.fs = fs or Fs()
        self.out, self.err, self.exit_code, self.exited = b"", "", 0, False
        self.user = {"uid": 0, "gid": 0}

        class Events:
            def dispatch(self, *args, **kwargs):
                pass

        class Protocol:
            events = Events()

        self.protocol = Protocol()

    def write(self, text):
        self.out += text.encode("utf-8", "surrogateescape")

    def writeBytes(self, data):  # noqa: N802 - Cowrie's name
        self.out += data

    def errorWrite(self, text):  # noqa: N802 - Cowrie's name
        self.err += text

    def exit(self, code=None):
        if code is not None:
            self.exit_code = code
        self.exited = True


class Log:
    """twisted's log.err: an internal error of a command fails the test that did not expect it."""
    errors = []

    @classmethod
    def err(cls, *args, **kwargs):
        cls.errors.append(args)


class ShellFs:
    A_TYPE, A_UID, A_GID, A_SIZE, A_MODE, A_CTIME, A_CONTENTS, A_REALFILE = 1, 2, 3, 4, 5, 6, 7, 9
    T_DIR, T_FILE = 1, 2


class OwnedFs(Fs):
    """An Fs whose files and folders have Cowrie's nodes: owner, group and mode (as the persona's
    tree has them), so that sort -o meets the permissions of the file and its folder."""

    def __init__(self, nodes):
        super().__init__(files={path: FILES.get(path, b"old\n") for path, node in nodes.items() if node[1] == 2},
                         dirs={path for path, node in nodes.items() if node[1] == 1})
        self.nodes = {path: list(node) for path, node in nodes.items()}
        self.private = 0

    def _ensure_private_fs(self):  # noqa: N802 - Cowrie's name
        self.private += 1

    def mkfile(self, path, uid, gid, size, mode):
        self.made = (path, uid, gid, mode)
        return super().mkfile(path, uid, gid, size, mode)


_ROOT = None


def patched_root():
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "cowrie"
        shutil.copytree(FIXTURE, _ROOT)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(_ROOT)], check=True, capture_output=True)
    return _ROOT


def keys_of(kind, image_cases):
    """[shell] keys for an image of the reference: its coreutils or its BusyBox (banner of its output)."""
    style, detail, *locale = kind.split()
    if style == "gnu":
        years = {"8.22": "2013", "8.32": "2020", "9.1": "2022", "9.4": "2023", "9.7": "2025"}
        keys = {"text_style": "gnu", "text_version": detail, "text_year": years[detail], "text_collate": locale[0]}
        if locale[1:]:
            keys["text_fold"] = locale[1]
        if detail == "9.7":
            keys["text_packager"] = "Debian (9.7-3)"
        return keys
    banner = image_cases["seq --help"][1].split(" multi-call")[0]
    return {"text_style": "busybox", "text_banner": banner, "text_version": banner.split()[1][1:],
            "text_libc": detail, "text_unicode": "true"}


class TextToolsTest(unittest.TestCase):
    def namespace(self, keys):
        config = FakeConfig({("shell", key): value for key, value in keys.items()})
        return module_all((patched_root() / "src/cowrie/commands/wc.py").read_text(),
                          {"HoneyPotCommand": Base, "CowrieConfig": config, "log": Log, "tpot_shell_fs": ShellFs})

    def run_cmd(self, keys, name, args, stdin=None, fs=None):
        namespace = self.namespace(keys)
        cmd = namespace["commands"][name](args, stdin, fs)
        cmd.start()
        self.assertTrue(cmd.exited, f"{name} {args} did not exit")
        return cmd.out.decode("utf-8", "surrogateescape"), cmd.err, cmd.exit_code

    def test_the_commands_answer_every_spelling(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.4"})
        for name in ("wc", "sort", "seq"):
            for spelling in (name, f"/bin/{name}", f"/usr/bin/{name}"):
                self.assertIs(namespace["commands"][spelling], namespace[f"Command_tpot_{name}"], spelling)

    def test_the_output_of_the_real_programs(self):
        """Every case of the reference run, per image: stdout, stderr and the exit status."""
        for image, ref in REFS["images"].items():
            keys = keys_of(ref["kind"], ref["cases"])
            for case, (out, err, code) in ref["cases"].items():
                if case in LEFT_OUT or case in LEFT_OUT_OF.get(image, ()):
                    continue
                name, args, stdin, head = split_case(case)
                with self.subTest(image=image, case=case):
                    got = self.run_cmd(keys, name, args, stdin)
                    if head:
                        got = ("".join(got[0].splitlines(True)[:head]), got[1], 0)
                    self.assertEqual(got, (out, err, code))

    def test_wc_reads_standard_input_line_by_line(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.4"})
        cmd = namespace["commands"]["wc"](["-l"], None, Fs())
        cmd.start()
        self.assertFalse(cmd.exited)
        for line in ("one two", "three"):
            cmd.lineReceived(line)
        cmd.eofReceived()
        self.assertEqual((cmd.out, cmd.exit_code, cmd.exited), (b"2\n", 0, True))
        cmd = namespace["commands"]["wc"]([], None, Fs())
        cmd.start()
        cmd.lineReceived("a b")
        cmd.eofReceived()
        self.assertEqual(cmd.out, b"      1       2       4\n")

    def test_sort_reads_standard_input_until_its_end(self):
        namespace = self.namespace({"text_style": "busybox", "text_banner": "BusyBox v1.33.2 (2023-04-17 13:15:36 UTC)",
                                    "text_version": "1.33.2", "text_libc": "musl"})
        cmd = namespace["commands"]["sort"](["-n"], None, Fs())
        cmd.start()
        self.assertFalse(cmd.exited)
        for line in ("10", "9", "100"):
            cmd.lineReceived(line)
        cmd.eofReceived()
        self.assertEqual((cmd.out, cmd.exited), (b"9\n10\n100\n", True))

    def test_wc_counts_the_first_file_and_the_files_of_the_tree(self):
        keys = {"text_style": "gnu", "text_version": "9.1"}
        self.assertEqual(self.run_cmd(keys, "wc", ["-l", "/tmp/p.txt"]), ("3 /tmp/p.txt\n", "", 0))
        # /proc files have no size: GNU pads to the width of their sizes (1)
        fs = Fs({"/proc/meminfo": b"MemTotal: 1 kB\nMemFree: 1 kB\n"})
        self.assertEqual(self.run_cmd(keys, "wc", ["/proc/meminfo"], fs=fs), ("2 6 29 /proc/meminfo\n", "", 0))

    def test_busybox_wc_of_a_firmware_without_unicode(self):
        """-m counts bytes and is left out of the usage text (the usage of the OpenWrt BusyBox is
        "[-clwL] [FILE]..."); 1.14 has no -m and the older texts (libbb/appletlib.c and usage.h of 1.14.1)."""
        keys = {"text_style": "busybox", "text_banner": "BusyBox v1.19.4 (2022-07-14 23:31:52 EDT)",
                "text_version": "1.19.4", "text_libc": "uclibc"}
        self.assertEqual(self.run_cmd(keys, "wc", ["-m", "/tmp/u.txt"]), ("13 /tmp/u.txt\n", "", 0))
        out, err, code = self.run_cmd(keys, "wc", ["-x"])
        self.assertEqual((out, code), ("", 1))
        # wc -x of the BusyBox v1.19.4 of the Archer C7 v5 (qemu-mips-static): before 1.21 the usage ends
        #   with a blank line
        self.assertEqual(err, (
            'wc: invalid option -- x\n'
            'BusyBox v1.19.4 (2022-07-14 23:31:52 EDT) multi-call binary.\n'
            '\n'
            'Usage: wc [-clwL] [FILE]...\n'
            '\n'
            'Count lines, words, and bytes for each FILE (or stdin)\n'
            '\n'
            '\t-c\tCount bytes\n'
            '\t-l\tCount newlines\n'
            '\t-w\tCount words\n'
            '\t-L\tPrint longest line length\n'
            '\n'
        ))
        self.assertEqual(self.run_cmd(keys, "wc", ["-l", "/tmp/p.txt", "/tmp/w.txt"])[0],
                         "        3 /tmp/p.txt\n        2 /tmp/w.txt\n        5 total\n")
        keys.update(text_banner="BusyBox v1.14.1 (2016-06-28 10:53:08 CST)", text_version="1.14.1")
        out, err, code = self.run_cmd(keys, "wc", ["-m"])
        self.assertEqual((out, code), ("", 1))
        self.assertEqual(err, "wc: invalid option -- m\nBusyBox v1.14.1 (2016-06-28 10:53:08 CST) multi-call binary\n"
                              "\nUsage: wc [OPTION]... [FILE]...\n\nPrint line, word, and byte counts for each FILE, "
                              "and a total line if\nmore than one FILE is specified. With no FILE, read standard "
                              "input.\n\nOptions:\n\t-c\tPrint the byte counts\n\t-l\tPrint the newline counts\n"
                              "\t-L\tPrint the length of the longest line\n\t-w\tPrint the word counts\n\n")

    def test_sort_collates_like_the_locale(self):
        """sort with LC_ALL=en_US.UTF-8 in centos:7 (glibc 2.17, coreutils 8.22 of Red Hat) and in
        debian:12 with the locale generated (glibc 2.36, coreutils 9.1); C.UTF-8 orders bytes."""
        lines = b"10 b\n9 a\n100 c\n9 a\n-3 d\n2.5 e\n b x\nB y\nb z\na_b\nab\nA\n_x\n"
        cases = {
            ("en-2.17", "bytes", "8.22"): {
                "": "100 c|10 b|2.5 e|-3 d|9 a|9 a|A|ab|a_b| b x|B y|b z|_x|",
                "-f": " b x|-3 d|10 b|100 c|2.5 e|9 a|9 a|A|ab|a_b|B y|b z|_x|",
                "-u": "100 c|10 b|2.5 e|-3 d|9 a|A|ab|a_b| b x|B y|b z|_x|",
                "-d": "100 c|10 b|2.5 e|-3 d|9 a|9 a|A|ab|a_b| b x|B y|b z|_x|",
                "-n": "-3 d|A|ab|a_b| b x|B y|b z|_x|2.5 e|9 a|9 a|10 b|100 c|",
                "-k2": "A|ab|a_b|_x|9 a|9 a|10 b|100 c|-3 d|2.5 e| b x|B y|b z|",
                "-r": "_x|b z|B y| b x|a_b|ab|A|9 a|9 a|-3 d|2.5 e|10 b|100 c|",
                "-fu": " b x|-3 d|10 b|100 c|2.5 e|9 a|A|ab|a_b|B y|b z|_x|"},
            ("en", "", "9.1"): {
                "": "100 c|10 b|2.5 e|-3 d|9 a|9 a|A|a_b|ab| b x|B y|b z|_x|",
                "-f": "100 c|10 b|2.5 e|-3 d|9 a|9 a|A|a_b|ab| b x|B y|b z|_x|",
                "-u": "100 c|10 b|2.5 e|-3 d|9 a|A|a_b|ab| b x|B y|b z|_x|",
                "-d": "100 c|10 b|2.5 e|-3 d|9 a|9 a|A|a_b|ab| b x|B y|b z|_x|",
                "-n": "-3 d|A|a_b|ab| b x|B y|b z|_x|2.5 e|9 a|9 a|10 b|100 c|",
                "-k2": "A|a_b|ab|_x|9 a|9 a|10 b|100 c|-3 d|2.5 e| b x|B y|b z|",
                "-r": "_x|b z|B y| b x|ab|a_b|A|9 a|9 a|-3 d|2.5 e|10 b|100 c|",
                "-fu": "100 c|10 b|2.5 e|-3 d|9 a|A|a_b|ab| b x|B y|b z|_x|"},
            ("c", "", "9.1"): {"": " b x|-3 d|10 b|100 c|2.5 e|9 a|9 a|A|B y|_x|a_b|ab|b z|"},
        }
        for (collate, fold, version), expected in cases.items():
            keys = {"text_style": "gnu", "text_version": version, "text_collate": collate, "text_fold": fold}
            for option, order in expected.items():
                with self.subTest(collate=collate, option=option):
                    self.assertEqual(self.run_cmd(keys, "sort", [option] if option else [], lines)[0].replace("\n", "|"),
                                     order)
        # the help text of an en_* locale names no translation address
        keys = {"text_style": "gnu", "text_version": "9.1", "text_collate": "en"}
        self.assertTrue(self.run_cmd(keys, "wc", ["--help"])[0].endswith(
            "\nGNU coreutils online help: <https://www.gnu.org/software/coreutils/>\n"
            "Full documentation <https://www.gnu.org/software/coreutils/wc>\n"
            "or available locally via: info '(coreutils) wc invocation'\n"))
        self.assertEqual(self.run_cmd(keys, "sort", ["-k0"], b"x\n")[1],
                         "sort: field number is zero: invalid field specification \u2018\u0030\u2019\n")

    def test_limits(self):
        """Large inputs, long sequences and many keys end in bounded time and memory."""
        keys = {"text_style": "gnu", "text_version": "9.4", "text_year": "2023"}
        start = time.monotonic()
        out, err, code = self.run_cmd(keys, "seq", ["1", "100000000"])
        self.assertLess(len(out), 2 << 20)
        self.assertTrue(out.startswith("1\n2\n3\n"))
        out, err, code = self.run_cmd(keys, "seq", ["1", "0.000000001", "2"])
        self.assertLess(len(out), 2 << 20)
        big = b"".join(b"%d line\n" % (n % 977) for n in range(300_000))
        out, err, code = self.run_cmd(keys, "sort", ["-n"], big)
        self.assertEqual((out, err, code), ("", "sort: memory exhausted\n", 2))
        out, err, code = self.run_cmd(keys, "wc", ["-l"], big * 4)
        self.assertEqual(out, "1200000\n")
        self.assertEqual(self.run_cmd(keys, "sort", ["-k1,1"] * 40, b"b\na\n")[2], 2)
        self.assertLess(time.monotonic() - start, 10)
        bb = {"text_style": "busybox", "text_banner": "BusyBox v1.33.2 (2023-04-17 13:15:36 UTC)",
              "text_version": "1.33.2", "text_libc": "musl"}
        out, err, code = self.run_cmd(bb, "seq", ["1", "0", "3"])
        self.assertLess(len(out), 2 << 20)
        self.assertEqual(self.run_cmd(bb, "sort", [], big)[1:], ("sort: out of memory\n", 2))

    def timed(self, keys, name, args, stdin=None, limit=1.0):
        Log.errors.clear()
        start = time.monotonic()
        got = self.run_cmd(keys, name, args, stdin)
        took = time.monotonic() - start
        self.assertLess(took, limit, (name, args[:4], took))
        self.assertEqual(Log.errors, [], (name, args[:4]))
        return got

    def test_every_call_ends_well_within_a_second(self):
        gnu = {"text_style": "gnu", "text_version": "9.4", "text_year": "2023"}
        old = {"text_style": "gnu", "text_version": "8.22", "text_year": "2013", "text_collate": "en-2.17"}
        bb = {"text_style": "busybox", "text_banner": "BusyBox v1.33.2 (2023-04-17 13:15:36 UTC)",
              "text_version": "1.33.2", "text_libc": "musl"}
        lines = b"".join(b"%d word x.%d\n" % (n, n % 97) for n in range(1000))
        for keys in (gnu, bb):
            for args, data in ((["-k", "2147483647"], b"b a\na b\n"), (["-k", "1000000"], lines),
                               (["-k", "1,2147483647"], b"b a\na b\n"), (["-k", "2147483647.5,2147483647.9"], lines)):
                with self.subTest(style=keys["text_style"], args=args):
                    self.timed(keys, "sort", args, data)
        many = b"".join(b"%d v1.%d.%d x\n" % (n, n % 13, n % 7) for n in range(99_000))
        self.assertEqual(self.timed(gnu, "sort", ["-k2V"] * 32, many)[1:], ("sort: memory exhausted\n", 2))
        wide = (b"x" * 399 + b"\n") * 2600
        self.timed(gnu, "sort", ["-k400"] * 32, wide)
        self.timed(bb, "sort", ["-k400"] * 32, wide)
        text = ("caf\u00e9 na\u00efve \u65e5\u672c\n" * 900_000).encode()
        for keys in (gnu, bb):
            for args in ([], ["-L"], ["-m"]):
                with self.subTest(style=keys["text_style"], wc=args):
                    self.timed(keys, "wc", args, text)
        self.timed(gnu, "wc", ["-w"], b"x" * (16 << 20))
        # seq: the output limit holds for an empty separator, wide formats and a zero step
        for keys, args in ((gnu, ["-s", "", "-f", "%99999f", "1", "1000000000000"]),
                           (old, ["-s", "", "-f", "%99999f", "1", "0", "2"]),
                           (gnu, ["-f", "%99999f", "1", "1000000000000"]),
                           (gnu, ["1e-999999999", "1"]), (gnu, ["-w", "1e4931", "1e4931"]),
                           (bb, ["-s", "", "1", "0", "2"])):
            with self.subTest(seq=args):
                out = self.timed(keys, "seq", args)[0]
                self.assertLess(len(out), 2 << 20)

    def test_seq_formats_and_ranges_as_coreutils(self):
        """debian:12 (9.1) and centos:7 (8.22): numbers beyond the long double range are invalid,
        tiny ones are 0 with their precision, a format width pads."""
        gnu = {"text_style": "gnu", "text_version": "9.1", "text_year": "2022"}
        try_help = "Try 'seq --help' for more information.\n"
        for arg in ("1e99999999999999999999", "0x1p99999", "1e999999999", "1e4933"):
            with self.subTest(arg=arg):
                self.assertEqual(self.timed(gnu, "seq", [arg]),
                                 ("", f"seq: invalid floating point argument: \u2018{arg}\u2019\n" + try_help, 1))
        self.assertEqual(self.timed(gnu, "seq", ["-w", "1e999999999"])[2], 1)
        out = self.timed(gnu, "seq", ["1e-999999999", "1"])[0]
        self.assertTrue(out.startswith("0." + "0" * 1000))
        self.assertEqual(len(self.timed(gnu, "seq", ["-s", "", "-f", "%99999f", "1", "3"])[0]), 299998)
        out = self.timed(gnu, "seq", ["-f", "%99999f", "1", "2"])[0]
        self.assertEqual((len(out), out[:3], out[99991:100000]), (200000, "   ", "1.000000\n"))
        self.assertEqual(self.timed(gnu, "seq", ["-f", "%-12f|", "1", "2"])[0], "1.000000    |\n2.000000    |\n")
        self.assertEqual(self.timed(gnu, "seq", ["-f", "%012.3f", "1", "2"])[0], "00000001.000\n00000002.000\n")
        self.assertEqual(len(self.timed(gnu, "seq", ["-f", "%.70000f", "1", "1"])[0]), 70003)

    def test_an_internal_error_ends_the_command(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.4"})
        Log.errors.clear()

        def broken(self, terminal):
            raise RuntimeError("unexpected")

        namespace["Command_tpot_wc"].tpot_main = broken
        cmd = namespace["commands"]["wc"]([], b"a\n", Fs())
        cmd.start()
        self.assertEqual((cmd.exited, cmd.exit_code, len(Log.errors)), (True, 1, 1))

    def test_sort_o_writes_the_file(self):
        fs = Fs()
        keys = {"text_style": "gnu", "text_version": "9.4"}
        namespace = self.namespace(keys)
        cmd = namespace["commands"]["sort"](["-o", "/tmp/sorted", "/tmp/k.txt"], None, fs)
        cmd.start()
        self.assertEqual((cmd.out, cmd.exit_code), (b"", 0))
        self.assertEqual(fs.nodes["/tmp/sorted"][7], b"w:10:b\nx:3:z\ny:1:a\nz:2:m\n")
        self.assertEqual(fs.nodes["/tmp/sorted"][4], 25)
        cmd = namespace["commands"]["sort"](["-o", "/nosuch/dir/f", "/tmp/k.txt"], None, fs)
        cmd.start()
        self.assertEqual((cmd.err, cmd.exit_code), ("sort: open failed: /nosuch/dir/f: No such file or directory\n", 2))

    def test_seq_f_and_s_keep_backslashes(self):
        """seq -f and -s print a backslash as it is: coreutils 8.22 (centos:7), 9.1 (debian:12),
        9.4 (ubuntu:24.04) and 9.7 (debian:13) in containers, `seq -f FORMAT 2 | od -c`."""
        cases = {"a\\tb%g": "a\\tb1\na\\tb2\n", "%g\\n": "1\\n\n2\\n\n", "%g\\\\x": "1\\\\x\n2\\\\x\n",
                 "x\\x41%g": "x\\x411\nx\\x412\n", "\\101%g": "\\1011\n\\1012\n", "%g\\c": "1\\c\n2\\c\n",
                 "q\\%g": "q\\1\nq\\2\n", "%%%g": "%1\n%2\n", "%g\\": "1\\\n2\\\n",
                 "a\\nb%gc": "a\\nb1c\na\\nb2c\n", "%5.2f\\t|": " 1.00\\t|\n 2.00\\t|\n", "\\e%g": "\\e1\n\\e2\n"}
        for version in ("8.22", "9.1", "9.4", "9.7"):
            keys = {"text_style": "gnu", "text_version": version}
            for fmt, out in cases.items():
                with self.subTest(version=version, fmt=fmt):
                    self.assertEqual(self.run_cmd(keys, "seq", ["-f", fmt, "2"]), (out, "", 0))
            self.assertEqual(self.run_cmd(keys, "seq", ["-s", "\\t", "3"]), ("1\\t2\\t3\n", "", 0))
            self.assertEqual(self.run_cmd(keys, "seq", ["-s", "\\n", "2"]), ("1\\n2\n", "", 0))

    def test_sort_o_meets_the_permissions_of_the_persona(self):
        """sort -o as the persona's sort (coreutils 9.1 and 8.22, BusyBox 1.27.2 and 1.36.1 as uid
        65534 in containers): a file or folder the user may not write is "Permission denied" (2);
        a new file is a regular file of mode 0644 of its user; an existing file keeps its owner and mode."""
        import stat
        def nodes():
            return {"/": ["", 1, 0, 0, 4096, 0o40755, 0, [], None, None],
                    "/etc": ["etc", 1, 0, 0, 4096, 0o40755, 0, [], None, None],
                    "/etc/passwd": ["passwd", 2, 0, 0, 4, 0o100644, 0, None, None, "/honeyfs/etc/passwd"],
                    "/tmp": ["tmp", 1, 0, 0, 4096, 0o41777, 0, [], None, None],
                    "/tmp/k.txt": ["k.txt", 2, 0, 0, 4, 0o100644, 0, None, None, None],
                    "/tmp/mine": ["mine", 2, 1000, 1000, 4, 0o100600, 0, None, None, "/honeyfs/tmp/mine"],
                    "/tmp/ro": ["ro", 2, 1000, 1000, 4, 0o100444, 0, None, None, None]}
        sorted_k = b"w:10:b\nx:3:z\ny:1:a\nz:2:m\n"
        for keys, denied in (({"text_style": "gnu", "text_version": "9.1"}, "sort: open failed: {}: Permission denied\n"),
                             ({"text_style": "gnu", "text_version": "8.22"}, "sort: open failed: {}: Permission denied\n"),
                             ({"text_style": "busybox", "text_version": "1.36.1", "text_libc": "musl"},
                              "sort: can't open '{}': Permission denied\n"),
                             ({"text_style": "busybox", "text_version": "1.27.2", "text_libc": "musl"},
                              "sort: can't open '{}': Permission denied\n")):
            namespace = self.namespace(keys)

            def sort(fs, target, uid=1000, sudo=False):
                cmd = namespace["commands"]["sort"](["-o", target, "/tmp/k.txt"], None, fs)
                cmd.user = {"uid": uid, "gid": uid}
                cmd.pp = type("Pipe", (), {"tpot_sudo": sudo})()
                cmd.start()
                return cmd.err, cmd.exit_code

            with self.subTest(keys=keys):
                for target in ("/etc/passwd", "/etc/newfile", "/tmp/ro"):
                    fs = OwnedFs(nodes())
                    self.assertEqual(sort(fs, target), (denied.format(target), 2))
                    self.assertNotIn(target, getattr(fs, "made", ("",)))
                    if target in fs.nodes:
                        self.assertEqual(fs.nodes[target], nodes()[target])
                # a new file of the user: a regular file of mode 0644
                fs = OwnedFs(nodes())
                self.assertEqual(sort(fs, "/tmp/out"), ("", 0))
                self.assertEqual(fs.made, ("/tmp/out", 1000, 1000, stat.S_IFREG | 0o644))
                self.assertEqual(fs.nodes["/tmp/out"][7], sorted_k)
                # the user's file keeps its owner and mode; its new text replaces the honeyfs file
                self.assertEqual(sort(fs, "/tmp/mine"), ("", 0))
                self.assertEqual(fs.nodes["/tmp/mine"][:6], ["mine", 2, 1000, 1000, len(sorted_k), 0o100600])
                self.assertEqual((fs.nodes["/tmp/mine"][7], fs.nodes["/tmp/mine"][9]), (sorted_k, None))
                # written now: its time is the time of the write, as ls -l shows it
                self.assertLess(abs(fs.nodes["/tmp/mine"][6] - time.time()), 5)
                self.assertGreater(fs.private, 0)
                # root and the command sudo runs may write it: the owner stays root
                for how in ({"uid": 0}, {"sudo": True}):
                    fs = OwnedFs(nodes())
                    self.assertEqual(sort(fs, "/etc/passwd", **how), ("", 0))
                    self.assertEqual(fs.nodes["/etc/passwd"][2:6], [0, 0, len(sorted_k), 0o100644])
                    self.assertEqual(fs.nodes["/etc/passwd"][7], sorted_k)
                    self.assertEqual(sort(fs, "/etc/newfile", **how), ("", 0))
                    self.assertEqual(fs.made, ("/etc/newfile", 0, 0, stat.S_IFREG | 0o644))

    def test_sort_o_has_a_budget_per_session(self):
        """The files of sort -o of one session share TPOT_WRITE_BUDGET bytes; past it the text of a
        full disk (coreutils / BusyBox in containers with a 4 KiB tmpfs) and the part that fits."""
        cases = (({"text_style": "gnu", "text_version": "9.4"},
                  "sort: write failed: '/tmp/a b': No space left on device\nsort: write error\n", 2),
                 ({"text_style": "gnu", "text_version": "8.22"},
                  "sort: write failed: /tmp/a b: No space left on device\nsort: write error\n", 2),
                 ({"text_style": "busybox", "text_version": "1.36.1", "text_libc": "musl"},
                  "sort: standard output: No space left on device\n", 2),
                 ({"text_style": "busybox", "text_version": "1.35.0", "text_libc": "glibc"},
                  "sort: standard output: No space left on device\n", 0),
                 ({"text_style": "busybox", "text_version": "1.36.1", "text_libc": "uclibc"}, "", 0))
        for keys, err, code in cases:
            with self.subTest(keys=keys):
                fs = Fs()
                namespace = self.namespace(keys)
                namespace["TPOT_WRITE_BUDGET"] = 40
                first = namespace["commands"]["sort"](["-o", "/tmp/one", "/tmp/k.txt"], None, fs)
                first.start()
                self.assertEqual((first.err, first.exit_code), ("", 0))
                second = namespace["commands"]["sort"](["-o", "/tmp/a b", "/tmp/k.txt"], None, fs)
                second.protocol = first.protocol
                second.start()
                self.assertEqual((second.err, second.exit_code), (err, code))
                self.assertEqual(fs.nodes["/tmp/a b"][7], b"w:10:b\nx:3:z\ny:1:a\nz:2:m\n"[:15])
                self.assertEqual(fs.nodes["/tmp/a b"][4], 15)

    def test_wc_counts_a_large_input_without_a_list_of_its_words(self):
        """The words beyond TPOT_WC_INPUT are counted at C speed without a list of them."""
        import tracemalloc
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.4"})
        data = b"ab " * (10 << 20) + b"cd\n"
        tracemalloc.start()
        try:
            counts = namespace["tpot_wc_counts"](data, True, (9, 4), True, longest=False)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(counts[:4], [1, (10 << 20) + 1, len(data), len(data)])
        self.assertLess(peak, 8 << 20)

    def test_wc_of_an_exec_channel_takes_its_stdin(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.4"})
        cmd = namespace["commands"]["wc"](["-l"], None, Fs())
        cmd.protocol.execcmd = "wc -l"
        cmd.protocol.input_data = b"one\ntwo\nthree\n"
        cmd.start()
        cmd.eofReceived()
        self.assertEqual((cmd.out, cmd.exited), (b"3\n", True))

    def test_wc_c_gives_the_size_of_a_large_file(self):
        big = b"x" * (5 << 20) + b"\n"
        fs = Fs({"/tmp/big": big})
        out = self.run_cmd({"text_style": "gnu", "text_version": "9.4"}, "wc", ["-c", "/tmp/big"], fs=fs)[0]
        self.assertEqual(out, f"{len(big)} /tmp/big\n")

    def test_bot_one_liners_on_every_persona(self):
        for persona in catalog.PERSONAS:
            ident = draw(persona, 3)
            keys = system.text_keys(persona, ident)
            if not keys:
                continue
            with self.subTest(persona=persona.id):
                self.assertEqual(self.run_cmd(keys, "wc", ["-l"], FILES["/tmp/p.txt"])[:2], ("3\n", ""))
                self.assertEqual(self.run_cmd(keys, "sort", ["-rn"], b"1\n3\n2\n")[0], "3\n2\n1\n")
                self.assertEqual(self.run_cmd(keys, "seq", ["3"])[0], "1\n2\n3\n")



READLINK = json.loads((Path(__file__).parent / "fixtures" / "readlink.json").read_text(encoding="utf-8"))["images"]
# the cases of readlink.json the replay leaves to ReadlinkTest: they name the container's processes (its /proc and
#   /etc/mtab, the PID of the readlink that ran) or the emulator of an amd64 image
READLINK_OWN = ("/proc/", "/etc/mtab")
# what the persona's readlink answers otherwise than the recording, with the reason
READLINK_KNOWN = {
    ("centos_7", "readlink --canon l2"): "getopt_long of glibc 2.17 names the ambiguous options in another order",
}


class UniqTest(unittest.TestCase):
    """uniq of the persona (Command_tpot_uniq of the patched wc.py) against the recording of the real tool
    (fixtures/shell_uniq.json): GNU coreutils of the persona releases and BusyBox 1.25.1, 1.33.1 and 1.36.1 (musl);
    every recorded case with its own input, stdout, stderr and exit status."""

    RECORDING = json.loads((Path(__file__).parent / "fixtures" / "shell_uniq.json").read_text(encoding="utf-8"))
    # the locale is requested as en_US.UTF-8 there, but setlocale fails (_source.locale): C quote marks, ASCII
    NO_LOCALE_IMAGES = ("rockylinux:9",)

    def namespace(self, keys):
        config = FakeConfig({("shell", key): value for key, value in keys.items()})
        return module_all((patched_root() / "src/cowrie/commands/wc.py").read_text(),
                          {"HoneyPotCommand": Base, "CowrieConfig": config, "log": Log, "tpot_shell_fs": ShellFs})

    def keys(self, ref):
        cases = ref["cases"]
        if ref["kind"].startswith("busybox"):
            # keys_of reads the banner from the seq --help case; uniq --help prints the same banner line
            banner = next(line for line in cases["uniq --help"][1].splitlines()
                          if line.startswith("BusyBox v"))
            cases = {"seq --help": ("", banner, 0)}
        keys = keys_of(ref["kind"], cases)
        if ref["image"] in self.NO_LOCALE_IMAGES:
            keys["text_quote"] = "ascii"
        return keys

    def split(self, case):
        """`printf FORMAT | uniq ARGS` (the printf output is the input) or `uniq ARGS`."""
        import codecs
        import shlex
        stdin = None
        if case.startswith("printf "):
            head, _, case = case.partition(" | ")
            stdin = codecs.decode(shlex.split(head)[1], "unicode_escape").encode("utf-8")
        words = shlex.split(case)
        return words[0], words[1:], stdin

    def answer(self, namespace, name, args, stdin):
        cmd = namespace["commands"][name](args, stdin, Fs())
        cmd.start()
        self.assertTrue(cmd.exited, f"{name} {args} did not exit")
        return cmd.out.decode("utf-8", "surrogateescape"), cmd.err, cmd.exit_code

    def test_uniq_answers_every_spelling(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        self.assertIn("Command_tpot_uniq", namespace)
        for spelling in ("uniq", "/bin/uniq", "/usr/bin/uniq"):
            self.assertIs(namespace["commands"].get(spelling), namespace["Command_tpot_uniq"], spelling)

    def test_terminal_final_newlines_match_the_recorded_tools(self):
        refs = json.loads((Path(__file__).parent / "fixtures/uniq_terminal.json").read_text())
        for keys in ({"text_style": "gnu", "text_version": "8.32"},
                     {"text_style": "gnu", "text_version": "9.1"},
                     {"text_style": "busybox", "text_version": "1.37.0", "text_banner": "BusyBox v1.37.0"}):
            namespace = self.namespace(keys)
            for case in refs["cases"]:
                with self.subTest(keys=keys, input=case["input"]):
                    cmd = namespace["commands"]["uniq"]([], None, Fs())
                    cmd.start()
                    for line in case["input"].splitlines():
                        cmd.lineReceived(line)
                    self.assertEqual(cmd.out, b"")  # T-Pot buffers until EOF; GNU does not.
                    cmd.eofReceived()
                    self.assertEqual((cmd.out.decode(), cmd.err, cmd.exit_code),
                                     (case["output"], "", case["status"]))

    def test_every_recorded_case(self):
        done = 0
        for image, ref in self.RECORDING["images"].items():
            namespace = self.namespace(self.keys(ref))
            for case, result in ref["cases"].items():
                name, args, stdin = self.split(case)
                with self.subTest(image=image, case=case):
                    self.assertEqual(self.answer(namespace, name, args, stdin), tuple(result))
                    done += 1
        self.assertEqual(done, 360)

    def test_quote_marks_follow_the_text_quote_key(self):
        """Straight marks with [shell] text_quote ascii or the POSIX collation, else curly ones."""
        base = {"text_style": "gnu", "text_version": "9.1"}
        cases = (({"text_collate": "en"}, "\u2018x\u2019"),
                 ({"text_collate": "en", "text_quote": "ascii"}, "'x'"),
                 ({"text_collate": "c"}, "\u2018x\u2019"),
                 ({"text_collate": "posix"}, "'x'"))
        for keys, quoted in cases:
            with self.subTest(keys=keys):
                err = self.answer(self.namespace(dict(base, **keys)), "uniq", ["--all-repeated=x"], None)[1]
                self.assertTrue(err.startswith(f"uniq: invalid argument {quoted} for "), err)

    def test_quote_marks_of_the_rhel_and_raspios_personas(self):
        """rhel (en_US.UTF-8, as the other tools recorded on rockylinux_9) and raspios (en_GB.UTF-8, derived): curly marks."""
        families = set()
        for persona in catalog.PERSONAS:
            if persona.family not in ("rhel", "raspios"):
                continue
            families.add(persona.family)
            keys = system.text_keys(persona, draw(persona, 3))
            err = self.answer(self.namespace(keys), "uniq", ["--all-repeated=x"], None)[1]
            quoted = "\u2018x\u2019"
            with self.subTest(persona=persona.id):
                self.assertNotIn("text_quote", keys)
                self.assertTrue(err.startswith(f"uniq: invalid argument {quoted} for "), err)
        self.assertEqual(families, {"rhel", "raspios"})


class DateTest(unittest.TestCase):
    """date of the persona (Command_tpot_date of the patched wc.py) against the recording of the real tool
    (fixtures/shell_date.json): GNU coreutils 8.22 to 9.7 and BusyBox 1.25.1, 1.33.1 and 1.36.1 (musl), each case as
    root and the `date -s` / `date TIME` cases as uid 1000, and the locale cases of Debian 12 in en_GB.UTF-8. The
    persona's clock is CLOCK; the clock values of the recording are filled from it (expected)."""

    RECORDING = json.loads((Path(__file__).parent / "fixtures" / "shell_date.json").read_text(encoding="utf-8"))
    CLOCK = 1792584000.25   # 2026-10-21 12:00:00.25 UTC: the quarter of %q is 4, as recorded
    # the recording ran a little later than the clock of a case: the words of the case move it
    SHIFT = {"date +%s -d '1 hour ago'": -3600, "date -d '+1 day' +%F": 86400, "date -d yesterday +%Y": -86400,
             "date +%s -d yesterday": -86400, "date +%s -d tomorrow": 86400, "date +%s -d today": 0,
             "date +%s -d now": 0, "date +%s -d '1 day ago'": -86400, "date +%s -d '2 hours'": 7200,
             "date +%s -d '3 days ago'": -259200, "date +%s -d '1 week ago'": -604800}
    # the [shell] text_date of each image: the locale of its date
    LOCALES = {"centos_7": "us-2.17", "rockylinux_9": "us", "debian_12_en_GB": "gb"}

    def namespace(self, keys):
        config = FakeConfig({("shell", key): value for key, value in keys.items()})
        namespace = module_all((patched_root() / "src/cowrie/commands/wc.py").read_text(),
                               {"HoneyPotCommand": Base, "CowrieConfig": config, "log": Log, "tpot_shell_fs": ShellFs})
        namespace["Command_tpot_date"].clock = staticmethod(lambda: self.CLOCK)
        return namespace

    def keys(self, image, ref):
        cases = ref["cases"]
        if ref["kind"].startswith("busybox"):
            # keys_of reads the banner from the seq --help case; BusyBox date --help prints it on stderr, first
            cases = {"seq --help": ("", cases["date --help"][1].splitlines()[0], 0)}
        keys = keys_of(ref["kind"], cases)
        if keys["text_style"] == "gnu":
            keys["text_date"] = self.LOCALES.get(image, "c")
        return keys

    def fs(self):
        """/etc/passwd with a modification time (the clock): date -r reads it."""
        fs = Fs(files={**FILES, "/etc/passwd": b"root:x:0:0:root:/root:/bin/bash\n"})
        fs.nodes = {"/etc/passwd": ["passwd", ShellFs.T_FILE, 0, 0, 30, 0o644, self.CLOCK, b"", None, None]}
        return fs

    def split(self, case):
        """`TZ=UTC date ARGS` (the zone is the persona's own) or `date ARGS`."""
        import shlex
        if case.startswith("TZ=UTC "):
            case = case[len("TZ=UTC "):]
        words = shlex.split(case)
        return words[0], words[1:]

    def expected(self, text, shift):
        """The recorded output with its clock values filled from the persona's clock moved by shift seconds."""
        when = int(self.CLOCK) + shift
        moment = time.gmtime(when)
        nanos = 250000000   # the .25 of CLOCK
        filled = {
            "EPOCH": str(when), "EPOCHNANOS19": f"{when}{nanos:09d}", "EPOCH%N": f"{when}%N",
            "NANOS9": f"{nanos:09d}", "DATE": time.strftime("%Y-%m-%d", moment),
            "TIME": time.strftime("%H:%M:%S", moment), "DATE_TIME": time.strftime("%Y-%m-%d_%H:%M:%S", moment),
            "DATETTIME+00:00": time.strftime("%Y-%m-%dT%H:%M:%S", moment) + "+00:00",
            "DATETTIME+0000": time.strftime("%Y-%m-%dT%H:%M:%S", moment) + "+0000",
            "WEEKDAY MONTH": time.strftime("%A %B", moment), "YEAR": time.strftime("%Y", moment),
            "YYYYMMDDHHMMSS": time.strftime("%Y%m%d%H%M%S", moment),
            "DDD, DD Mon YYYY HH:MM:SS +0000": time.strftime("%a, %d %b %Y %H:%M:%S +0000", moment),
        }
        line = text[:-1] if text.endswith("\n") else text
        return filled[line] + text[len(line):] if line in filled else text

    def run_case(self, namespace, case, uid):
        name, args = self.split(case)
        cmd = namespace["commands"][name](args, None, self.fs())
        cmd.user = {"uid": uid, "gid": uid}
        cmd.start()
        self.assertTrue(cmd.exited, f"{case} did not exit")
        return cmd

    def test_date_answers_every_spelling(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        for spelling in ("date", "/bin/date", "/usr/bin/date"):
            self.assertIs(namespace["commands"].get(spelling), namespace["Command_tpot_date"], spelling)
        patch = (Path(__file__).parent.parent / "dist" / "patch_cowrie_persona_support.py").read_text(encoding="utf-8")
        self.assertIn('"Command_tpot_date"', patch)

    def test_every_recorded_case(self):
        done = total = 0
        for image, ref in self.RECORDING["images"].items():
            namespace = self.namespace(self.keys(image, ref))
            for group, uid in (("cases", 0), ("cases_user", 1000)):
                for case, (out, err, code) in ref[group].items():
                    total += 1
                    with self.subTest(image=image, case=case, uid=uid):
                        cmd = self.run_case(namespace, case, uid)
                        self.assertEqual(cmd.out.decode("utf-8", "surrogateescape"),
                                         self.expected(out, self.SHIFT.get(case, 0)))
                        self.assertEqual(cmd.err, err)
                        self.assertEqual(cmd.exit_code, code)
                        done += 1
        self.assertEqual((done, total), (2273, 2273))

    def test_the_default_format_of_each_locale_is_the_recorded_one(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        locales = namespace["TPOT_DATE_LOCALES"]
        for image, ref in self.RECORDING["images"].items():
            with self.subTest(image=image):
                key = self.LOCALES.get(image, "c") if ref["kind"].startswith("gnu") else "c"
                self.assertEqual(locales[key]["default"], ref["bare_format"])

    def test_root_setting_the_date_prints_it_and_leaves_the_clock(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1", "text_date": "c"})
        protocol = namespace["commands"]["date"](["-u"], None, self.fs()).protocol
        for args, expected in ((["-s", "2024-01-02 03:04:05"], "Tue Jan  2 03:04:05 UTC 2024\n"),
                               (["+%F"], "2026-10-21\n")):
            cmd = namespace["commands"]["date"](args, None, self.fs())
            cmd.protocol = protocol
            cmd.start()
            self.assertEqual((cmd.out.decode(), cmd.err, cmd.exit_code), (expected, "", 0), args)

    def test_the_other_gnu_options_follow_the_release(self):
        old = self.namespace({"text_style": "gnu", "text_version": "8.32", "text_date": "c"})
        new = self.namespace({"text_style": "gnu", "text_version": "9.1", "text_date": "c"})
        for namespace, resolution in ((old, False), (new, True)):
            cmd = namespace["commands"]["date"](["--resolution"], None, self.fs())
            cmd.start()
            self.assertEqual(cmd.out.decode() if resolution else cmd.err.splitlines()[0],
                             "0.000000001\n" if resolution else "date: unrecognized option '--resolution'")
        cmd = new["commands"]["date"](["--rfc-3339=seconds", "-d", "@1772600767"], None, self.fs())
        cmd.start()
        self.assertEqual(cmd.out.decode(), "2026-03-04 05:06:07+00:00\n")

    def timed(self, namespace, args):
        """date ARGS as root, which must end without an entry of the error log (the deadline itself is pinned below)."""
        Log.errors.clear()
        cmd = namespace["commands"]["date"](args, None, self.fs())
        cmd.user = {"uid": 0, "gid": 0}
        start = time.monotonic()
        cmd.start()
        took = time.monotonic() - start
        self.assertTrue(cmd.exited, args[0][:20])
        self.assertLess(took, 10.0, (args[0][:20], took))  # generous: the 0.8 s deadline must not decide the test
        self.assertEqual(Log.errors, [], args[0][:20])
        return cmd

    def test_long_formats_and_dates_end_within_the_deadline(self):
        gnu = {"text_style": "gnu", "text_version": "9.1"}
        old = {"text_style": "gnu", "text_version": "8.22", "text_date": "us-2.17"}
        musl = {"text_style": "busybox", "text_banner": "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)",
                "text_version": "1.36.1", "text_libc": "musl"}
        uclibc = {"text_style": "busybox", "text_banner": "BusyBox v1.25.1 (2016-10-07 18:17:00 UTC)",
                  "text_version": "1.25.1", "text_libc": "uclibc"}
        for name, keys in (("gnu", gnu), ("old", old), ("musl", musl), ("uclibc", uclibc)):
            namespace = self.namespace(keys)
            for args in (["+" + "%c" * 20000], ["+" + "%d" * 500_000]):
                with self.subTest(style=name, format=args[0][:6]):
                    self.assertLess(len(self.timed(namespace, args).out), 3 << 20)
            with self.subTest(style=name, width="digits"):
                for letter in "dY":
                    self.timed(namespace, ["+%" + "9" * 5000 + letter])
                    self.timed(namespace, ["+%" + "0" * 5000 + "5" + letter])
        namespace = self.namespace(gnu)
        for text in ("1 day " * 160_000, "1 day ago " * 100_000, "mon " * 250_000):
            with self.subTest(date=text[:10]):
                self.timed(namespace, ["-d", text])
        for text in ("9" * 5000, "+" + "9" * 5000, "@" + "9" * 5000, "1 day " + "9" * 5000):
            with self.subTest(date=text[:10]):
                cmd = self.timed(namespace, ["-d", text])
                self.assertEqual((cmd.err[:18], cmd.exit_code), ("date: invalid date", 1))
        for keys in (musl, uclibc):
            self.assertEqual(self.timed(self.namespace(keys), ["-d", "@" + "9" * 5000]).exit_code, 1)

    def test_the_formats_and_dates_stop_where_the_time_is_used_up(self):
        gnu = {"text_style": "gnu", "text_version": "9.1"}
        musl = {"text_style": "busybox", "text_banner": "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)",
                "text_version": "1.36.1", "text_libc": "musl"}
        uclibc = {"text_style": "busybox", "text_banner": "BusyBox v1.25.1 (2016-10-07 18:17:00 UTC)",
                  "text_version": "1.25.1", "text_libc": "uclibc"}
        for name, keys in (("gnu", gnu), ("musl", musl), ("uclibc", uclibc)):
            namespace = self.namespace(keys)
            namespace["TPOT_TEXT_SECONDS"] = -1.0
            for fmt in ("%c", "%d", "x"):
                with self.subTest(style=name, format=fmt):
                    cmd = self.run_case(namespace, "date +" + fmt * 20000, 0)
                    self.assertLess(len(cmd.out), 512 * 64)  # the floor: two checks of 256 steps
        namespace = self.namespace(gnu)
        namespace["TPOT_TEXT_SECONDS"] = -1.0
        cmd = self.run_case(namespace, "date -d '" + "1 day " * 1000 + "'", 0)
        self.assertEqual((cmd.err[:18], cmd.exit_code), ("date: invalid date", 1))
        self.assertEqual(self.run_case(namespace, "date -d '3 days ago' +%F", 0).out, b"2026-10-18\n")
        cmd = self.run_case(namespace, "date -d '" + "1 day " * 20 + "' +%F", 0)
        self.assertEqual((cmd.err, cmd.exit_code), ("", 0))

    def test_relative_words_add_and_a_bare_number_is_a_time(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})

        def date(*args):
            cmd = self.run_case(namespace, "date " + " ".join(args), 0)
            return cmd.out.decode()
        self.assertEqual(date("-d", "'2 days tomorrow'", "+%s"), date("-d", "'3 days'", "+%s"))
        # derived from gnulib's digits_to_date_time, not recorded: up to four digits are an hour or hhmm
        self.assertEqual(date("-d", "1230", "'+%F %T'"), "2026-10-21 12:30:00\n")
        self.assertEqual(date("-d", "5", "'+%F %T'"), "2026-10-21 05:00:00\n")

    def test_date_reads_a_file_of_dates(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1", "text_date": "c"})
        fs = Fs(files={**FILES, "/tmp/dates": b"@0\nnonsense\n2024-01-02\n"})
        cmd = namespace["commands"]["date"](["-f", "/tmp/dates", "+%F"], None, fs)
        cmd.start()
        self.assertEqual((cmd.out.decode(), cmd.err, cmd.exit_code),
                         ("1970-01-01\n2024-01-02\n", "date: invalid date \u2018nonsense\u2019\n", 1))


class ReadlinkTest(unittest.TestCase):
    """readlink of the persona (Command_tpot_readlink of the patched wc.py) against the recordings of the real tool
    (fixtures/readlink.json): GNU coreutils of the persona releases, BusyBox 1.25.1, 1.33.1 and 1.36.1, every case
    run on Cowrie's own file system set up as the recording was (/tmp/w with d, file and its links)."""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        from tests import test_fs_tools
        cls.fst = test_fs_tools

    def make_fs(self, busybox=False):
        fst = self.fst
        link = lambda name, target, uid=0: fst.node(name, fst.T_LINK, 0o120777, uid=uid, target=target)  # noqa: E731
        w = fst.node("w", fst.T_DIR, 0o40755, [
            fst.node("d", fst.T_DIR, 0o40755), fst.node("file", fst.T_FILE, 0o100644), link("l1", "file"),
            link("l2", "l1"), link("dangling", "missing"), link("dl", "d"), link("abs", "/tmp/w/l2"),
            link("loop1", "loop2"), link("loop2", "loop1"), link("notdir", "file/x")])
        shell = fst.node("3089", fst.T_DIR, 0o40555, [
            link("exe", "/usr/bin/bash", 1000), link("cwd", "/home/user", 1000), link("root", "/", 1000),
            fst.node("mounts", fst.T_FILE, 0o100444, contents=b"", uid=1000),
            fst.node("fd", fst.T_DIR, 0o40500, [link("0", "/dev/pts/0", 1000)], uid=1000)], uid=1000)
        init = fst.node("1", fst.T_DIR, 0o40555, [link("exe", "/usr/lib/systemd/systemd"), link("cwd", "/"),
                                                    link("root", "/")])
        proc = fst.node("proc", fst.T_DIR, 0o40555, [link("self", "3089"), link("mounts", "self/mounts"),
                                                      shell, init])
        programs = [fst.node("busybox", fst.T_FILE, 0o100755)] if busybox else []
        usr_bin = [link("readlink", "/bin/busybox")] if busybox else [fst.node("readlink", fst.T_FILE, 0o100755),
                                                                       fst.node("bash", fst.T_FILE, 0o100755)]
        root = fst.node("/", fst.T_DIR, 0o40755, [
            fst.node("bin", fst.T_DIR, 0o40755, programs), fst.node("tmp", fst.T_DIR, 0o41777, [w]),
            fst.node("etc", fst.T_DIR, 0o40755, [link("mtab", "../proc/self/mounts")]), proc,
            fst.node("usr", fst.T_DIR, 0o40755, [fst.node("bin", fst.T_DIR, 0o40755, usr_bin)]),
            fst.node("home", fst.T_DIR, 0o40755, [fst.node("user", fst.T_DIR, 0o40755, uid=1000)])])
        fs = object.__new__(fst.fs_module()["HoneyPotFilesystem"])
        fs.fs, fs._private_fs, fs.arch, fs.home = root, True, "linux-x64-lsb", "/root"
        fs.tempfiles, fs.filenames, fs.newcount, fs.generated_files = {}, {}, 0, {}
        return fs

    def run_line(self, name, line, uid=0, fs=None):
        import shlex
        shell = self.fst.SHELLS.get(name) or self.fst.SHELLS.setdefault(
            name, self.fst.Shell(self.fst.keys_of(name, READLINK[name] if name in READLINK else {})))
        fs = fs or self.make_fs(busybox=name.startswith(("busybox", "fw_", "src_")))
        words = shlex.split(line)
        cmd = shell.namespace["commands"][words[0]](words[1:], None, fs)
        cmd.cwd = "/tmp/w"
        cmd.user = {"uid": uid, "gid": uid}
        cmd.protocol.tpot_login_pid = 3089
        cmd.protocol.tpot_new_pid = lambda count=1, now=None: 4711
        Log.errors.clear()
        cmd.start()
        self.assertEqual(Log.errors, [], line)
        self.assertTrue(cmd.exited, line)
        return [cmd.out.decode("utf-8", "surrogateescape"), cmd.err, cmd.exit_code]

    def test_every_recorded_case(self):
        done = 0
        for name, image in READLINK.items():
            for case, result in image["cases"].items():
                if any(part in case for part in READLINK_OWN) or (name, case) in READLINK_KNOWN:
                    continue
                with self.subTest(image=name, case=case):
                    self.assertEqual(self.run_line(name, case), result)
                    done += 1
        self.assertGreater(done, 600)

    def test_proc_self_is_readlink_itself(self):
        """/proc/self of readlink is its own process (a new PID of the session, its program), not the shell's;
        -f /etc/mtab names its mounts as the real readlink does."""
        for name, exe in (("debian_12", "/usr/bin/readlink"), ("busybox_1.36.1-musl", "/bin/busybox")):
            with self.subTest(variant=name):
                self.assertEqual(self.run_line(name, "readlink /proc/self"), ["4711\n", "", 0])
                self.assertEqual(self.run_line(name, "readlink /proc/self/exe"), [exe + "\n", "", 0])
                self.assertEqual(self.run_line(name, "readlink -f /proc/self/exe"), [exe + "\n", "", 0])
                self.assertEqual(self.run_line(name, "readlink /proc/self/cwd"), ["/tmp/w\n", "", 0])
                self.assertEqual(self.run_line(name, "readlink /etc/mtab"), ["../proc/self/mounts\n", "", 0])
                self.assertEqual(self.run_line(name, "readlink -f /etc/mtab"), ["/proc/4711/mounts\n", "", 0])
                # the shell's own: its program, the folder it is in
                self.assertEqual(self.run_line(name, "readlink /proc/3089/exe"), ["/usr/bin/bash\n", "", 0])
                self.assertEqual(self.run_line(name, "readlink /proc/3089/cwd"), ["/tmp/w\n", "", 0])
                self.assertEqual(self.run_line(name, "readlink /proc/1/exe"), ["/usr/lib/systemd/systemd\n", "", 0])
        # the recordings: readlink of /proc/self, its exe and -f of /etc/mtab name the PID and program of readlink
        recorded = READLINK["debian_12"]["cases"]
        self.assertEqual(recorded["readlink /proc/self/exe"][0], "/usr/bin/readlink\n")
        self.assertRegex(recorded["readlink -f /etc/mtab"][0], r"^/proc/\d+/mounts\n$")
        self.assertRegex(recorded["readlink /proc/self"][0], r"^\d+\n$")

    def test_a_long_name_is_read_in_linear_time(self):
        for name in ("debian_12", "busybox_1.36.1-musl"):
            self.run_line(name, "readlink a")  # builds the shell of the variant first
            for line in ("readlink -m " + "a/" * 32000, "readlink -f " + "a/" * 32000,
                         "readlink -m " + "a/" * 16000 + "/" * 30000, "readlink -m " + "../" * 32000 + "tmp",
                         "readlink -f " + "x" * 60000):
                with self.subTest(variant=name, line=line[:20]):
                    started = time.monotonic()
                    self.run_line(name, line)
                    self.assertLess(time.monotonic() - started, 0.3)
        # a part that is missing hides what follows it until a dot dot leaves it
        self.assertEqual(self.run_line("debian_12", "readlink -m nosuch/a/../../file"), ["/tmp/w/file\n", "", 0])
        self.assertEqual(self.run_line("debian_12", "readlink -m nosuch/a/../l2"), ["/tmp/w/nosuch/l2\n", "", 0])

    def test_another_users_process_is_not_readable(self):
        """exe, cwd and root of a process of another user: EACCES (silent, -v names it)."""
        self.assertEqual(self.run_line("debian_12", "readlink /proc/1/exe", uid=1000), ["", "", 1])
        self.assertEqual(self.run_line("debian_12", "readlink -v /proc/1/exe", uid=1000),
                         ["", "readlink: /proc/1/exe: Permission denied\n", 1])
        self.assertEqual(self.run_line("debian_12", "readlink /proc/3089/exe", uid=1000), ["/usr/bin/bash\n", "", 0])

    def test_readlink_of_every_persona(self):
        """Every persona's readlink answers in the style of its variant; a BusyBox without the applet (the DIR-859,
        the IP camera) has none: the shell says it is not found."""
        from tpot_personas import render
        for persona in catalog.PERSONAS:
            ident = draw(persona, 1)
            keys = dict(system.text_keys(persona, ident), **system.fs_keys(persona, ident))
            skipped = set(render.skip_python_commands(persona, ident, persona.builder(persona, ident)))
            with self.subTest(persona=persona.id):
                if "readlink" not in system.busybox_applets(persona, ident) and persona.kind == "device" \
                        and system.applet_links(persona, ident) is not None:
                    self.assertIn("readlink", skipped)
                    continue
                self.assertNotIn("readlink", skipped)
                self.assertEqual(self.run_cmd_readlink(keys, "readlink l2"), ["l1\n", "", 0])
                self.assertEqual(self.run_cmd_readlink(keys, "readlink -f l2"), ["/tmp/w/file\n", "", 0])

    def run_cmd_readlink(self, keys, line):
        shell = self.fst.Shell(keys)
        name = "persona " + json.dumps(keys, sort_keys=True)
        self.fst.SHELLS[name] = shell
        return self.run_line(name, line, fs=self.make_fs(busybox=keys.get("text_style") == "busybox"))

    def test_readlink_is_registered_with_the_file_commands(self):
        source = (patched_root() / "src/cowrie/commands/wc.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('("mkdir", Command_tpot_mkdir), ("readlink", Command_tpot_readlink)):\n'), 1)
        namespace = module_all(source, {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig({}), "log": Log})
        for spelling in ("readlink", "/bin/readlink", "/usr/bin/readlink"):
            self.assertIs(namespace["commands"][spelling], namespace["Command_tpot_readlink"], spelling)
        self.assertIn("readlink", system.COWRIE_COMMAND_NAMES)


class XargsSink:
    def __init__(self):
        self.out, self.err = b"", b""


class XargsPP:
    """The stdio wiring (Cowrie's PipeProtocol) of a command of the test: its output goes to the sink."""

    def __init__(self, sink, input_data=b""):
        self.targets, self.input_data, self.stdin_from_pipe = sink, input_data, True

    def write_stdout(self, data):
        self.targets.out += data

    def write_stderr(self, data):
        self.targets.err += data


class XargsPipe(XargsPP):
    def __init__(self, protocol, cmdclass, args, input_data, targets, cwd=None, user=None):
        super().__init__(targets, input_data)


class XargsUnsupported(Exception):
    """A command of the recorded case that the test has no stand-in for."""


class XargsChild(Base):
    """A command of the test as Cowrie runs one: it writes through its pp and leaves the cmdstack on exit."""

    def __init__(self, args, input_data=None, fs=None, cwd="/tmp"):
        super().__init__(args, input_data, fs, cwd)
        self.environ, self.last_exit_code, self.pp = {"PATH": "/usr/bin:/bin"}, 0, None

    def write(self, text):
        self.writeBytes(text.encode("utf-8", "surrogateescape"))

    def writeBytes(self, data):  # noqa: N802 - Cowrie's name
        self.pp.write_stdout(data)

    def errorWrite(self, text):  # noqa: N802 - Cowrie's name
        self.pp.write_stderr(text.encode("utf-8", "surrogateescape"))

    def exit(self, code=None):
        if self.exited:
            return
        super().exit(code)
        stack = self.protocol.cmdstack
        if self in stack:
            stack.remove(self)
        if stack:
            stack[-1].last_exit_code = self.exit_code
            stack[-1].resume()

    def resume(self):
        pass

    def eofReceived(self):  # noqa: N802 - Cowrie's name
        pass

    def start(self):
        self.exit(self.run())


def xargs_printf(fmt, rest):
    text = codecs.decode(fmt.encode("utf-8"), "unicode_escape")
    if "%s" in text:
        return "".join(text.replace("%s", arg) for arg in (rest or [""]))
    return text


class XargsEcho(XargsChild):
    def run(self):
        args, newline = self.args, "\n"
        if args[:1] == ["-n"]:
            args, newline = args[1:], ""
        self.write(" ".join(args) + newline)
        return 0


class XargsTrue(XargsChild):
    def run(self):
        return 0


class XargsFalse(XargsChild):
    def run(self):
        return 1


class XargsPrintf(XargsChild):
    def run(self):
        self.write(xargs_printf(self.args[0], self.args[1:]) if self.args else "")
        return 0


class XargsCat(XargsChild):
    def run(self):
        code = 0
        for name in self.args:
            path = self.fs.resolve_path(name, self.cwd)
            if name != "-" and path not in self.fs.files:
                self.errorWrite(f"cat: can't open '{name}': No such file or directory\n"
                                if self.protocol.busybox else f"cat: {name}: No such file or directory\n")
                code = 1
            elif name != "-":
                self.writeBytes(self.fs.files[path])
        return code


class XargsSh(XargsChild):
    """sh -c of the few scripts the recorded cases use."""

    def run(self):
        if self.args[:1] != ["-c"]:
            raise XargsUnsupported("sh")
        positional = self.args[2:]
        zero, rest = (positional[0] if positional else "sh"), positional[1:]
        code = 0
        for statement in self.args[1].split(";"):
            code, stop = self.statement(statement.strip(), zero, rest)
            if stop:
                break
        return code

    def words(self, text, zero, rest):
        out = []
        for word in shlex.split(text):
            if word == "$@":
                out += rest
            else:
                out.append(word.replace("$0", zero).replace("$#", str(len(rest)))
                           .replace("$1", rest[0] if rest else ""))
        return out

    def statement(self, text, zero, rest):
        if not text:
            return 0, False
        words = self.words(text, zero, rest)
        if words[0] == "exit":
            return int(words[1]), True
        if words[0] == "echo":
            self.write(" ".join(words[1:]) + "\n")
            return 0, False
        if words[0] == "cat":
            code = 0
            for name in words[1:]:
                path = self.fs.resolve_path(name, self.cwd)
                if path in self.fs.files:
                    self.writeBytes(self.fs.files[path])
                else:
                    code = 1
            return code, False
        found = re.fullmatch(r"test (\S+) -gt (\S+) && (.*)", " ".join(words) if words[0] == "test" else "")
        if found:
            if int(found.group(1)) > int(found.group(2)):
                return self.statement(found.group(3), zero, rest)
            return 1, True
        raise XargsUnsupported(text)


class XargsSlow(XargsChild):
    """A command that ends later (a download): it stays on the cmdstack until the test ends it."""

    def start(self):
        self.protocol.started.append(self)

    def handle_CTRL_C(self):  # noqa: N802 - Cowrie's name
        self.write("^C\n")
        self.exit(130)


class XargsBuiltin(XargsChild):
    """A command class Cowrie registers for a shell builtin (exit, export, cd ...): it must never start under xargs."""

    def start(self):
        self.protocol.started.append(self)
        self.exit(0)


class XargsTerminal:
    def __init__(self):
        self.out = bytearray()

    def write(self, data):
        self.out += data


XARGS_UNSUPPORTED = {"wc", "env", "touch", "rm", "mkdir", "cp", "ls"}


class XargsProtocol:
    """The parts of Cowrie's protocol a command that runs commands uses: getCommand, call_command, cmdstack."""

    class Events:
        def dispatch(self, *args, **kwargs):
            pass

    class Shell:
        def resume(self):
            pass

    def __init__(self, fs, registry, busybox):
        self.fs, self.registry, self.busybox = fs, registry, busybox
        self.cmdstack, self.pp, self.started, self.unsupported = [self.Shell()], None, [], None
        self.pps, self.terminal = [], None
        self.events, self.user = self.Events(), {"uid": 0, "gid": 0}

    def getCommand(self, name, paths, cwd):  # noqa: N802 - Cowrie's name
        if name in self.registry:
            return self.registry[name]
        if name in XARGS_UNSUPPORTED:
            self.unsupported = name
        return None

    def call_command(self, pp, cmd, *args):
        self.pp = pp
        self.pps.append(pp)
        obj = cmd(list(args), pp.input_data, self.fs, "/tmp/w")
        obj.protocol, obj.pp = self, pp
        self.cmdstack.append(obj)
        try:
            obj.start()
        except XargsUnsupported as unsupported:
            self.unsupported = str(unsupported)
            obj.exit(0)
        if self.cmdstack and self.cmdstack[-1] is obj:
            obj.eofReceived()


class XargsTest(unittest.TestCase):
    """xargs of the persona (Command_tpot_xargs of the patched wc.py) against the recording of the real tool
    (fixtures/shell_xargs.json): GNU findutils 4.5.11 to 4.10 and BusyBox 1.25.1, 1.33.1 and 1.36.1 (musl). The commands
    it runs are stand-ins (echo, cat, printf, sh -c, xargs itself) that the test's protocol hands to call_command as
    Cowrie's does; the recorded cases that run other programs, redirect or chain are left out."""

    RECORDING = json.loads((Path(__file__).parent / "fixtures" / "shell_xargs.json").read_text(encoding="utf-8"))
    GNU = {
        "centos_7": {"text_version": "8.22", "text_year": "2013", "text_collate": "en-2.17"},
        "rockylinux_9": {"text_version": "8.32", "text_year": "2020", "text_collate": "en"},
        "ubuntu_22.04": {"text_version": "8.32", "text_year": "2020", "text_collate": "c"},
        "ubuntu_24.04": {"text_version": "9.4", "text_year": "2023", "text_collate": "c"},
        "debian_12": {"text_version": "9.1", "text_year": "2022", "text_collate": "c"},
        "debian_13": {"text_version": "9.7", "text_year": "2025", "text_collate": "c",
                      "text_packager": "Debian (9.7-3)"},
    }
    # the bytes the environment of the recording took up (--show-limits, the limit of -s)
    ENV_BYTES = {"centos_7": 168, "rockylinux_9": 168}
    FILES = {"/tmp/w/list.txt": b"1\n2\n3\n", "/tmp/w/a.txt": b"x\n", "/dev/null": b""}

    def namespace(self, keys):
        config = FakeConfig({("shell", key): value for key, value in keys.items()})
        namespace = module_all((patched_root() / "src/cowrie/commands/wc.py").read_text(),
                               {"HoneyPotCommand": XargsChild, "CowrieConfig": config, "log": Log,
                                "tpot_shell_fs": ShellFs, "PipeProtocol": XargsPipe})
        return namespace

    def keys(self, image, ref):
        if ref["kind"].startswith("gnu"):
            return dict(self.GNU[image], text_style="gnu")
        banner = ref["cases"]["xargs --help"][1].split(" multi-call")[0]
        return {"text_style": "busybox", "text_banner": banner, "text_version": banner.split()[1][1:],
                "text_libc": ref["kind"].split()[1], "text_unicode": "true"}

    def split(self, case):
        """The words and operators of a shell line outside its quotes: ("w", raw word) and ("o", operator)."""
        out, word, quote, last = [], "", "", -1
        at = 0
        while at < len(case):
            char = case[at]
            if quote:
                word += char
                quote = "" if char == quote else quote
            elif char in "'\"":
                quote, word = char, word + char
            elif char == "\\" and at + 1 < len(case):
                word, at = word + char + case[at + 1], at + 1
            elif char in "|;<>&":
                if word:
                    out.append(("w", word))
                    word = ""
                if last == at - 1 and out and out[-1][0] == "o":
                    out[-1] = ("o", out[-1][1] + char)
                else:
                    out.append(("o", char))
                last = at
            elif char in " \t":
                if word:
                    out.append(("w", word))
                    word = ""
            else:
                word += char
            at += 1
        if word:
            out.append(("w", word))
        return out

    def value(self, raw):
        return "/root" if raw == "$HOME" else shlex.split(raw)[0]

    def parse(self, case):
        """(stdin, xargs words, whether `; echo rc=$?` follows) of a case this test can run, else None."""
        status = case.endswith("; echo rc=$?")
        if status:
            case = case[:-len("; echo rc=$?")]
        tokens = self.split(case)
        operators = [text for kind, text in tokens if kind == "o"]
        words = [self.value(text) for kind, text in tokens if kind == "w"]
        if not operators:
            return (None, words[1:], status) if words[:1] == ["xargs"] else None
        if operators == ["<"]:
            if words[:1] == ["xargs"] and words[-1] == "list.txt":
                return self.FILES["/tmp/w/list.txt"], words[1:-1], status
            return None
        if operators != ["|"]:
            return None
        cut = next(i for i, (kind, _) in enumerate(tokens) if kind == "o")
        head = [self.value(text) for _, text in tokens[:cut]]
        tail = [self.value(text) for _, text in tokens[cut + 1:]]
        if tail[:1] != ["xargs"]:
            return None
        if head[0] == "printf":
            stdin = xargs_printf(head[1], head[2:]).encode("utf-8")
        elif head[0] == "cat":
            stdin = self.FILES["/tmp/w/" + head[1]]
        elif head[0] == "echo":
            stdin = (" ".join(head[1:]) + "\n").encode()
        else:
            return None
        return stdin, tail[1:], status

    def default_registry(self, namespace):
        return {"echo": XargsEcho, "true": XargsTrue, "false": XargsFalse, "cat": XargsCat, "printf": XargsPrintf,
                "sh": XargsSh, "xargs": namespace["commands"]["xargs"],
                **{name: XargsBuiltin for name in namespace["TPOT_XARGS_BUILTINS"]}}

    def run_cmd(self, namespace, busybox, args, stdin, env_bytes=139, registry=None, files=None):
        fs = Fs(files={**FILES, **self.FILES, **(files or {})}, dirs=DIRS | {"/tmp/w", "/dev"})
        sink = XargsSink()
        registry = registry or self.default_registry(namespace)
        protocol = XargsProtocol(fs, registry, busybox)
        cmd = namespace["commands"]["xargs"](list(args), stdin, fs, "/tmp/w")
        cmd.protocol, cmd.pp = protocol, XargsPP(sink, stdin or b"")
        cmd.environ = {"PATH": "/usr/bin", "X": "x" * (env_bytes - 14 - 3)}
        protocol.cmdstack.append(cmd)
        cmd.start()
        return cmd, protocol, sink

    def test_xargs_answers_every_spelling(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        for spelling in ("xargs", "/bin/xargs", "/usr/bin/xargs"):
            self.assertIs(namespace["commands"].get(spelling), namespace["Command_tpot_xargs"], spelling)
        patch = (Path(__file__).parent.parent / "dist" / "patch_cowrie_persona_support.py").read_text(encoding="utf-8")
        self.assertIn('"Command_tpot_xargs"', patch)
        self.assertIn("xargs", system.COWRIE_COMMAND_NAMES)

    def test_every_recorded_case(self):
        done = skipped = 0
        for image, ref in self.RECORDING["images"].items():
            namespace = self.namespace(self.keys(image, ref))
            busybox = ref["kind"].startswith("busybox")
            for case, result in ref["cases"].items():
                parsed = self.parse(case)
                if parsed is None or result[1] == "TIMEOUT":  # the recorded program never ended
                    skipped += 1
                    continue
                stdin, args, status = parsed
                with self.subTest(image=image, case=case):
                    Log.errors.clear()
                    cmd, protocol, sink = self.run_cmd(namespace, busybox, args, stdin, self.ENV_BYTES.get(image, 139))
                    if protocol.unsupported:
                        skipped += 1
                        continue
                    self.assertTrue(cmd.exited, "xargs did not exit")
                    out = sink.out.decode("utf-8", "surrogateescape")
                    code = cmd.exit_code
                    if status:
                        out, code = out + f"rc={code}\n", 0
                    self.assertEqual((out, sink.err.decode("utf-8", "surrogateescape"), code), tuple(result))
                    self.assertEqual(Log.errors, [])
                    done += 1
        self.assertGreaterEqual(done, 2370, f"{done} cases ran, {skipped} left out")  # 2384 of 2538 ran when written

    def test_a_command_that_ends_later_lets_xargs_go_on(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        registry = {"slow": XargsSlow, "true": XargsTrue}
        cmd, protocol, sink = self.run_cmd(namespace, False, ["-n1", "slow"], b"a b\n", registry=registry)
        self.assertFalse(cmd.exited)
        self.assertEqual([c.args for c in protocol.started], [["a"]])
        protocol.started[0].exit(0)
        self.assertFalse(cmd.exited)
        self.assertEqual([c.args for c in protocol.started], [["a"], ["b"]])
        protocol.started[1].exit(3)
        self.assertTrue(cmd.exited)
        self.assertEqual(cmd.exit_code, 123)
        self.assertEqual(protocol.cmdstack[0].__class__, XargsProtocol.Shell)

    def test_the_name_of_a_shell_builtin_is_no_program(self):
        # recorded: xargs execs a file, there is none for exit / export / cd ... (fixtures/shell_xargs.json)
        for keys, busybox in (({"text_style": "gnu", "text_version": "9.1"}, False),
                              ({"text_style": "busybox", "text_banner": "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)",
                                "text_version": "1.36.1", "text_libc": "musl"}, True)):
            namespace = self.namespace(keys)
            for name in ("exit", "export", "unset", "cd", "set"):
                with self.subTest(style=keys["text_style"], name=name):
                    cmd, protocol, sink = self.run_cmd(namespace, busybox, [name, "x"], b"a\n")
                    self.assertEqual(protocol.started, [])
                    self.assertEqual((sink.out, sink.err.decode(), cmd.exit_code),
                                     (b"", f"xargs: {name}: No such file or directory\n", 127))

    def test_every_builtin_the_shell_registers_is_no_program(self):
        # the names come from the registrations (upstream commands and the patch), not from TPOT_XARGS_BUILTINS
        registered = set()
        sources = list((Path(__file__).parent / "fixtures" / "v3.1.4" / "src" / "cowrie" / "commands").glob("*.py"))
        for source in sources + [Path(__file__).parent.parent / "dist" / "patch_cowrie_persona_support.py"]:
            registered |= set(re.findall(r'^\s*commands\["([^"/]+)"\]\s*=', source.read_text(encoding="utf-8"), re.M))
        shell_words = set(": [[ alias bg bind break builtin caller cd command compgen complete compopt continue declare "
                          "dirs disown enable eval exec exit export fc fg getopts hash help history jobs let local logout "
                          "mapfile popd pushd read readarray readonly return set shift shopt source suspend times trap "
                          "type typeset ulimit umask unalias unset wait if then else elif fi case esac for select while "
                          "until do done in function time { } ! ]] coproc".split())
        expected = sorted(registered & shell_words)
        self.assertIn("continue", expected)
        self.assertIn("done", expected)
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        registry = {**self.default_registry(namespace), **{name: XargsBuiltin for name in expected}}
        for name in expected:
            with self.subTest(name=name):
                cmd, protocol, sink = self.run_cmd(namespace, False, [name, "x"], b"a\n", registry=registry)
                self.assertEqual(protocol.started, [])
                self.assertEqual((sink.out, sink.err.decode(), cmd.exit_code),
                                 (b"", f"xargs: {name}: No such file or directory\n", 127))

    def test_a_builtin_name_with_a_file_on_the_path_changes_nothing(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        cmd, protocol, sink = self.run_cmd(namespace, False, ["cd", "x"], b"a\n", files={"/usr/bin/cd": b""})
        self.assertEqual((protocol.started, sink.out, sink.err, cmd.exit_code), ([], b"", b"", 0))

    def test_ctrl_c_ends_xargs_with_its_running_command(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        cmd, protocol, sink = self.run_cmd(namespace, False, ["-n1", "slow"], b"a b c\n", registry={"slow": XargsSlow})
        self.assertFalse(cmd.exited)
        protocol.cmdstack[-1].handle_CTRL_C()
        self.assertEqual(([c.args for c in protocol.started], cmd.exited, cmd.exit_code), ([["a"]], True, 130))
        self.assertEqual((sink.out, sink.err), (b"^C\n", b""))
        self.assertEqual(protocol.cmdstack[0].__class__, XargsProtocol.Shell)

    def test_the_runs_of_one_call_are_bounded(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        namespace["TPOT_XARGS_RUNS"] = 3
        registry = {"slow": XargsSlow}
        # the call ends at the cap with the status of its last run (real xargs has no cap: a departure)
        for codes, want in (([0, 0, 0], 0), ([3, 0, 0], 0), ([0, 0, 3], 3)):
            with self.subTest(codes=codes):
                cmd, protocol, _ = self.run_cmd(namespace, False, ["-n1", "slow"], b"a b c d e f\n", registry=registry)
                for number, code in enumerate(codes, 1):
                    self.assertEqual(len(protocol.started), number)
                    protocol.started[number - 1].exit(code)
                self.assertTrue(cmd.exited)
                self.assertEqual((len(protocol.started), cmd.exit_code), (3, want))

    def test_resume_counts_as_a_call_of_the_session(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        depths = []

        class Probe(XargsSlow):
            def start(self):
                super().start()
                depths.append(budget_names()["tpot_budget"](self.protocol).depth)

        cmd, protocol, _ = self.run_cmd(namespace, False, ["-n1", "probe"], b"a b\n", registry={"probe": Probe})
        budget = budget_names()["tpot_budget"](protocol)
        self.assertEqual((depths, budget.depth), ([1], 0))  # the first command starts inside start()
        protocol.started[0].exit(0)
        self.assertEqual((depths, budget.depth), ([1, 1], 0))  # the second one inside resume()
        protocol.started[1].exit(0)
        self.assertTrue(cmd.exited)
        self.assertEqual(budget.depth, 0)

    def real_pipe(self):
        """PipeProtocol of Cowrie's own shell/pipe.py (verbatim v3.1.4 in the fixture tree). What the file imports of
        Twisted and Cowrie is stubbed: a command without redirections never reaches that code."""
        source = (FIXTURE / "src" / "cowrie" / "shell" / "pipe.py").read_text(encoding="utf-8")
        keep = [node for node in ast.parse(source).body
                if not (isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in ("cowrie", "twisted"))]

        class Logger:
            def info(self, *args, **kwargs):
                pass

        namespace = {"Logger": Logger, "CowrieConfig": FakeConfig({}), "fs": types.SimpleNamespace(),
                     "temp_download_path": lambda prefix: "/nonexistent"}
        exec(compile(ast.Module(keep, []), "<shell/pipe.py>", "exec"), namespace)  # noqa: S102
        return namespace["PipeProtocol"]

    def run_real(self, namespace, pipe, args, stdin, registry=None, terminal=False):
        """xargs wired as Cowrie wires it: its own pp and the pp of every command it starts are the real PipeProtocol;
        only the protocol and the terminal around them are the test's."""
        fs = Fs(files={**FILES, **self.FILES}, dirs=DIRS | {"/tmp/w", "/dev"})
        protocol = XargsProtocol(fs, registry or self.default_registry(namespace), False)
        protocol.terminal = XargsTerminal()
        out, err = bytearray(), bytearray()
        namespace["PipeProtocol"] = pipe
        cmd = namespace["commands"]["xargs"](list(args), stdin, fs, "/tmp/w")
        cmd.protocol = protocol
        cmd.pp = pipe(protocol, type(cmd), list(args), stdin or b"",
                      None if terminal else {1: ("capture", out), 2: ("capture", err)}, cwd="/tmp/w", user=protocol.user)
        cmd.pp.stdin_from_pipe = True
        cmd.environ = {"PATH": "/usr/bin"}
        protocol.cmdstack.append(cmd)
        cmd.start()
        return cmd, protocol, bytes(out), bytes(err)

    def test_xargs_runs_its_commands_on_the_real_pipe_protocol_of_cowrie(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        pipe = self.real_pipe()
        cmd, protocol, out, err = self.run_real(namespace, pipe, ["-n1", "echo", "x"], b"a b\n")
        self.assertEqual((out, err, cmd.exit_code), (b"x a\nx b\n", b"", 0))
        self.assertEqual(len(protocol.pps), 2)
        for child in protocol.pps:
            self.assertIs(type(child), pipe)
            self.assertEqual((child.input_data, child.stdin_from_pipe), (b"", True))
            self.assertIs(child.base_targets, cmd.pp.targets)
            self.assertEqual((child.cwd, child.user), ("/tmp/w", protocol.user))
        # the standard error and the status of a command that fails
        cmd, _, out, err = self.run_real(namespace, pipe, ["cat", "nosuch"], b"\n")
        self.assertEqual((out, err, cmd.exit_code), (b"", b"cat: nosuch: No such file or directory\n", 123))
        # without a capture the output reaches the terminal of the protocol
        cmd, protocol, out, err = self.run_real(namespace, pipe, ["echo", "x"], b"a b\n", terminal=True)
        self.assertEqual((out, err, bytes(protocol.terminal.out), cmd.exit_code), (b"", b"", b"x a b\n", 0))
        # a command that ends later: its pp is still the one the real class made
        cmd, protocol, out, err = self.run_real(namespace, pipe, ["-n1", "slow"], b"a b\n", registry={"slow": XargsSlow})
        self.assertFalse(cmd.exited)
        protocol.started[0].exit(0)
        protocol.started[1].exit(3)
        self.assertEqual((cmd.exited, cmd.exit_code), (True, 123))
        self.assertTrue(all(type(child) is pipe for child in protocol.pps))

    def test_xargs_waits_for_the_terminal(self):
        namespace = self.namespace({"text_style": "gnu", "text_version": "9.1"})
        cmd, _, sink = self.run_cmd(namespace, False, ["echo", "x"], None)
        self.assertFalse(cmd.exited)
        cmd.lineReceived("a b")
        cmd.eofReceived()
        self.assertTrue(cmd.exited)
        self.assertEqual((sink.out, sink.err, cmd.exit_code), (b"x a b\n", b"", 0))

    def test_a_large_input_ends_within_the_time(self):
        for keys in ({"text_style": "gnu", "text_version": "9.1"},
                     {"text_style": "busybox", "text_banner": "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)",
                      "text_version": "1.36.1", "text_libc": "musl"}):
            namespace = self.namespace(keys)
            for args, data in ((["-n", "100000", "true"], b"a " * 1_500_000),
                               (["-I", "{}", "true", "{}"], b"a\n" * 1_000_000),
                               (["true"], b"'a' " * 1_000_000), (["-L", "1", "true"], b"a\n" * 1_000_000)):
                if keys["text_style"] == "busybox" and "-L" in args:
                    continue
                with self.subTest(style=keys["text_style"], args=args):
                    Log.errors.clear()
                    start = time.monotonic()
                    cmd, _, sink = self.run_cmd(namespace, False, args, data)
                    self.assertTrue(cmd.exited)
                    self.assertLess(time.monotonic() - start, 10.0)
                    self.assertEqual(Log.errors, [])


if __name__ == "__main__":
    unittest.main()
