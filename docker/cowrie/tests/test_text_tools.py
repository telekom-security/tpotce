"""wc, seq and sort of the patched Cowrie (patch_text_tools): GNU coreutils on servers, BusyBox on
devices. The reference texts (fixtures/text_tools.json) are the outputs of the real programs in
containers of debian:12, debian:13, ubuntu:24.04, ubuntu:22.04, centos:7 and busybox images
(glibc, uClibc and musl builds), run on the files of FILES with LC_ALL=C.UTF-8."""
import json
import posixpath
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, module_all
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
    A_CONTENTS, A_SIZE = 7, 4


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
        keys = {"text_style": "busybox", "text_banner": "BusyBox v1.19.2 (2025-03-28 14:13:40 CST)",
                "text_version": "1.19.2", "text_libc": "uclibc"}
        self.assertEqual(self.run_cmd(keys, "wc", ["-m", "/tmp/u.txt"]), ("13 /tmp/u.txt\n", "", 0))
        out, err, code = self.run_cmd(keys, "wc", ["-x"])
        self.assertEqual((out, code), ("", 1))
        self.assertEqual(err, "wc: invalid option -- x\nBusyBox v1.19.2 (2025-03-28 14:13:40 CST) multi-call binary.\n"
                              "\nUsage: wc [-clwL] [FILE]...\n\nCount lines, words, and bytes for each FILE (or stdin)\n"
                              "\n\t-c\tCount bytes\n\t-l\tCount newlines\n\t-w\tCount words\n"
                              "\t-L\tPrint longest line length\n")
        self.assertEqual(self.run_cmd(keys, "wc", ["-l", "/tmp/p.txt", "/tmp/w.txt"])[0],
                         "        3 /tmp/p.txt\n        2 /tmp/w.txt\n        5 total\n")
        keys.update(text_banner="BusyBox v1.14.1 (2016-06-28 10:53:08 CST)", text_version="1.14.1")
        out, err, code = self.run_cmd(keys, "wc", ["-m"])
        self.assertEqual((out, code), ("", 1))
        self.assertEqual(err, "wc: invalid option -- m\nBusyBox v1.14.1 (2016-06-28 10:53:08 CST) multi-call binary\n"
                              "\nUsage: wc [OPTION]... [FILE]...\n\nPrint line, word, and byte counts for each FILE, "
                              "and a total line if\nmore than one FILE is specified. With no FILE, read standard "
                              "input.\n\nOptions:\n\t-c\tPrint the byte counts\n\t-l\tPrint the newline counts\n"
                              "\t-L\tPrint the length of the longest line\n\t-w\tPrint the word counts\n")

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


if __name__ == "__main__":
    unittest.main()
