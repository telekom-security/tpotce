"""awk of the patched Cowrie (patch_awk): the forms scripts run on df, free, uptime, ps and
/proc/cpuinfo, in the awk of the persona (gawk, mawk or BusyBox). The reference texts are the
output of awk in almalinux:9 (gawk 5.1.0), debian:12 (mawk 1.3.4 20200120), ubuntu:24.04
(mawk 1.3.4 20240123) and busybox:1.36 containers for the same programs and input."""
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests import test_df
from tests.test_patch import FIXTURE, PATCH, FakeConfig, module_all
from tpot_personas import catalog, system
from tpot_personas.identity import draw

VERSIONS = {"gawk": "5.1.0", "mawk": "1.3.4 20200120", "busybox": "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)"}
DF_H = """Filesystem      Size  Used Avail Use% Mounted on
udev            1.9G     0  1.9G   0% /dev
tmpfs           392M  1.1M  391M   1% /run
/dev/sda1        39G  4.2G   35G  11% /
"""
DF_K = """Filesystem     1K-blocks    Used Available Use% Mounted on
/dev/sda1       40581564 4404148  36160632  11% /
tmpfs             401248       0    401248   0% /run/user/0
"""
FREE_M = """               total        used        free      shared  buff/cache   available
Mem:            7834        1203        5012          12        1618        6340
Swap:           2047           0        2047
"""
CPUINFO = "model name\t: Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz\n"
UPTIME = " 12:01:02 up 3 days,  4:05,  1 user,  load average: 0.00, 0.01, 0.05\n"
UPTIME_USERS = " 12:01:02 up 42 min,  2 users,  load average: 1.25, 0.81, 0.40\n"
PS = """USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.0  0.1 167744 11484 ?        Ss   10:00   0:02 /sbin/init
root         812  0.3  0.5 1234567 45000 ?       Ssl  10:00   1:02 /usr/bin/containerd
"""
PASSWD = """root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
ubuntu:x:1000:1000:Ubuntu:/home/ubuntu:/bin/bash
"""
MEMINFO = "MemTotal:        8023548 kB\nMemFree:         6000000 kB\n"

# (arguments, input, output) that all four awks print alike
SAME = [
    (["FNR == 2 {print $4}"], DF_H, "1.9G\n"),
    (["NR==2{print $5}"], DF_H, "0%\n"),
    (["{print $4}"], DF_K.splitlines()[-1] + "\n", "401248\n"),
    (['$NF=="/"{printf "%s\\t\\t", $5}'], DF_H, "11%\t\t"),
    (["NR>1 {s+=$2} END {print s+0}"], DF_K, "40982812\n"),
    (["{print $5+0}"], DF_H, "0\n0\n1\n11\n"),
    (["NR==2{print $2}"], FREE_M, "7834\n"),
    (['NR==2{printf "%.2f%%\\t\\t", $3*100/$2 }'], FREE_M, "15.36%\t\t"),
    (['/Mem/ {print $2" MB"}'], FREE_M, "7834 MB\n"),
    (["{print $4,$5,$6,$7,$8,$9;}"], CPUINFO, "Intel(R) Xeon(R) CPU E5-2680 v4 @\n"),
    (["-F:", "{print $2}"], CPUINFO, " Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz\n"),
    (["{print $(NF-2)}"], UPTIME, "0.00,\n"),
    (["{print $(NF-2),$(NF-1),$NF}"], UPTIME_USERS, "1.25, 0.81, 0.40\n"),
    (["-Fload average: ", "{print $2}"], UPTIME, "0.00, 0.01, 0.05\n"),
    (["{for(i=1;i<=NF;i++) if ($i ~ /user/) print $(i-1)}"], UPTIME_USERS, "2\n"),
    (["{print $2}"], PS, "PID\n1\n812\n"),
    (['NR>1 {cpu+=$3; mem+=$4} END {printf "%.1f %.1f\\n", cpu, mem}'], PS, "0.3 0.6\n"),
    (["$11 ~ /containerd/ {print $2}"], PS, "812\n"),
    (["-F:", "$3 >= 1000 {print $1}"], PASSWD, "ubuntu\n"),
    (["-F:", "$3 == 0 || $7 ~ /nologin/ {print $1, NR}"], PASSWD, "root 1\ndaemon 2\n"),
    (["-F:", "{print NF, length($1), toupper(substr($1,1,1))}"], PASSWD, "7 4 R\n7 6 D\n7 6 U\n"),
    (["/MemTotal/ {print $2}"], MEMINFO, "8023548\n"),
    (['/MemTotal/ {printf "%.1f\\n", $2/1024/1024}'], MEMINFO, "7.7\n"),
    (["/MemTotal/ {print int($2/1024)}"], MEMINFO, "7835\n"),
    (['BEGIN{OFS=":"}{$2="X"; print; print $1,$3}'], "a b c\n", "a:X:c\na:c\n"),
    (["{NF=2; print}"], "a b c\n", "a b\n"),
    (['{$5="e"; print; print NF}'], "a  b\n", "a b   e\n5\n"),
    (['$1 > $2 {print "gt", $0}'], "10 9\n2 10\nabc 5\n", "gt 10 9\ngt abc 5\n"),
    (['{print ($1 < $2), ($1 == $2), ($1 "" == $2 "")}'], "2 10\n1.0 1\n", "1 0 0\n0 1 0\n"),
    (['BEGIN { if (x == 0 && x == "") print "both"; print x+0, "[" x "]", length() }'], "", "both\n0 [] 0\n"),
    (["NR==1{next} {print}"], "a b\nc d\n", "c d\n"),
    (["BEGIN { while (i < 3) { i++; if (i == 2) continue; print i } }"], "", "1\n3\n"),
    (['{ print ($1 > 5) ? "big" : "small" }'], "7\n3\n", "big\nsmall\n"),
    (['{gsub(/^[ \\t]+|[ \\t]+$/, ""); print "[" $0 "]"}'], "  model : x  \n", "[model : x]\n"),
    (['BEGIN { s = "a.b"; sub(/\\./, "[&]", s); print s }'], "", "a[.]b\n"),
    (["-F[:;] *", "{print NF, $2, $3}"], "a:b; c\n", "3 b c\n"),
    (["-F\\t", "{print $2}"], "a\tb c\td\n", "b c\n"),
    (['BEGIN { printf "%5s|%-5d|%.3s|%c|%c|%05.1f|%+d|%e\\n", "ab", 3, "abcdef", 65, "hello", 3.14159, 5, '
      '12345.678 }'], "", "   ab|3    |abc|A|h|003.1|+5|1.234568e+04\n"),
    (['BEGIN { printf("%s=%d\\n", "x", "12abc") }'], "", "x=12\n"),
    (['BEGIN { print 1 " " 2 * 3, 2 " " -1, 1 - 1 " " 2 }'], "", "1 6 2-1 0 2\n"),
    (["BEGIN { i = 5; print i++, i, ++i, i-- }"], "", "5 6 7 7\n"),
    (["{ i = 2; print $i, $(i+1), $NF, $(NF-1), $i++, i }"], "a b c d\n", "b c d c 0 2\n"),
    (['$0 ~ "name" {print $4}'], "model name : Intel\n", "Intel\n"),
    (['/^[[:alpha:]]+[[:digit:]]+$/ {print "ok"}'], "ab12\n", "ok\n"),
    (["!/^#/ && NF"], "# c\nx y\n\nz\n", "x y\nz\n"),
    (['BEGIN { print "a" # c\nprint "b" }'], "", "a\nb\n"),
    (['BEGIN { if (0)\nprint "a"\nelse\nprint "b"\n}'], "", "b\n"),
    (['/a/{print "A"} /b/{print "B"}'], "ab\nb\n", "A\nB\nB\n"),
    (["NR==2"], "x\ny\nz\n", "y\n"),
    (['BEGIN { print("a", "b"); print ("x") ("y") }'], "", "a b\nxy\n"),
    (["{ print length }"], "abc\n", "3\n"),
    (["{ print tolower($0) }"], "MiXeD Case\n", "mixed case\n"),
    (['BEGIN { print index("hello", "ll"), substr("hello", 2, 3), substr("hello", 5, 10) "|" substr("hello", 6) '
      '"|" substr("hello",1.7,2) "|" substr("hello",0,2) }'], "", "3 ell o||he|he\n"),
    (["-v", "x=a\\tb", "BEGIN{print x}"], "", "a\tb\n"),
    (["{exit 4} END{print \"end\"}"], "x\n", "end\n"),
]
# (arguments, input, {style: (output, error, status)}) where the awks differ
DIFFERENT = [
    (["BEGIN { x = 2147483647; y = 2147483648; print x, y, -2147483648, 1e15, 0.1 * 3, 1e300*1e300 }"], "", {
        "gawk": ("2147483647 2147483648 -2147483648 1000000000000000 0.3 +inf\n", "", 0),
        "mawk": ("2147483647 2.14748e+09 -2.14748e+09 1e+15 0.3 inf\n", "", 0),
        "busybox": ("2147483647 2147483648 -2147483648 1000000000000000 0.3 inf\n", "", 0)}),
    (["{print $1+0, $2+0}"], "0x1A 1e2\n", {
        "gawk": ("0 100\n", "", 0), "mawk": ("26 100\n", "", 0), "busybox": ("26 100\n", "", 0)}),
    (["{print $1/0}"], "4\n", {
        "gawk": ("", "awk: cmd. line:1: (FILENAME=- FNR=1) fatal: division by zero attempted\n", 2),
        "mawk": ("inf\n", "", 0), "busybox": ("", "awk: cmd. line:1: Division by zero\n", 1)}),
    (["{print NF, $NF, $(NF-1)}"], "a b c\n\n", {
        "gawk": ("3 c b\n", "awk: cmd. line:1: (FILENAME=- FNR=2) fatal: attempt to access field -1\n", 2),
        "mawk": ("3 c b\n", 'awk: run time error: negative field index $-1\n\tFILENAME="-" FNR=2 NR=2\n', 2),
        "busybox": ("3 c b\n", "awk: cmd. line:1: Access to negative field\n", 1)}),
    (["{print $1"], "", {
        "gawk": ("", "awk: cmd. line:1: {print $1\nawk: cmd. line:1:          ^ unexpected newline or end of string\n",
                 1),
        "mawk": ("", "awk: line 2: missing } near end of file\n", 2),
        "busybox": ("", "awk: cmd. line:1: Unexpected token\n", 1)}),
    (["BEGIN { print 1 +* 2 }"], "", {
        "gawk": ("", "awk: cmd. line:1: BEGIN { print 1 +* 2 }\nawk: cmd. line:1:                  ^ syntax error\n", 1),
        "mawk": ("", "awk: line 1: syntax error at or near *\n", 2),
        "busybox": ("", "awk: cmd. line:1: Unexpected token\n", 1)}),
    (["BEGIN { foo(1) }"], "", {
        "gawk": ("", "awk: cmd. line:1: fatal: function `foo' not defined\n", 2),
        "mawk": ("", "awk: line 2: function foo never defined\n", 2),
        "busybox": ("", "awk: cmd. line:1: Call to undefined function\n", 1)}),
    (['BEGIN { x = 3; print !x == 0, !(x == 0), - x ^ 2, 2 ^ 3 ^ 2 }'], "", {
        "gawk": ("1 1 -9 512\n", "", 0), "mawk": ("1 1 -9 512\n", "", 0), "busybox": ("1 1 -9 64\n", "", 0)}),
    (['BEGIN { OFMT="%.2f"; x = 3.14159; print x, x "" ; CONVFMT="%.3f"; print x "" }'], "", {
        "gawk": ("3.14 3.14159\n3.142\n", "", 0), "mawk": ("3.14 3.14159\n3.142\n", "", 0),
        "busybox": ("3.14 3.14159\n3.14159\n", "", 0)}),
    (["-Q"], "", {
        "gawk": ("", None, 1), "mawk": ("", "awk: not an option: -Q\n", 2),
        "busybox": ("", None, 1)}),
    (["-F"], "", {
        "gawk": ("", None, 1), "mawk": ("", "awk: option -F lacks argument\n", 2), "busybox": ("", None, 1)}),
    (["-v", "bad", "1"], "", {
        "gawk": ("", None, 1), "mawk": ("", "awk: improper assignment: -v bad\n", 2), "busybox": ("", None, 1)}),
    (["-f", "/nope"], "", {
        "gawk": ("", "awk: fatal: cannot open source file `/nope' for reading: No such file or directory\n", 2),
        "mawk": ("", "awk: cannot open /nope (No such file or directory)\n", 2),
        "busybox": ("", "awk: can't open '/nope': No such file or directory\n", 1)}),
    (["{print}", "/nonexistent"], "", {
        "gawk": ("", "awk: fatal: cannot open file `/nonexistent' for reading: No such file or directory\n", 2),
        "mawk": ("", "awk: cannot open /nonexistent (No such file or directory)\n", 2),
        "busybox": ("", "awk: /nonexistent: No such file or directory\n", 1)}),
]
# valid awk the interpreter leaves out: no output, status 2
LEFT_OUT = ['BEGIN { system("uname") }', 'BEGIN { "uname" | getline x; print x }', "{ getline; print }",
            "{ a[$1]++ } END { for (k in a) print k }", "!seen[$0]++", "function f(x) { return x } { print f($1) }",
            '{ print $1 > "/tmp/out" }', '{ print $1 >> "/tmp/out" }', '{ print $1 | "sort" }',
            "{ n = split($0, p, \":\") }", "BEGIN { print rand() }", "NR==1, NR==2", "{ print match($0, /a/) }",
            "BEGIN { print strftime() }"]
EXISTING = {"/etc/passwd": PASSWD.encode(), "/proc/meminfo": MEMINFO.encode()}

_ROOT = None


def patched_root():
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "cowrie"
        shutil.copytree(FIXTURE, _ROOT)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(_ROOT)], check=True, capture_output=True)
    return _ROOT


class Fs:
    def resolve_path(self, path, cwd):
        return path if path.startswith("/") else cwd.rstrip("/") + "/" + path

    def isdir(self, path):
        return path in ("/", "/etc", "/proc")

    def file_contents(self, path):
        if path in EXISTING:
            return EXISTING[path]
        raise FileNotFoundError(path)


class Base:
    def __init__(self, args, input_data=None):
        self.args, self.input_data, self.cwd, self.fs = list(args), input_data, "/root", Fs()
        self.out, self.err, self.exit_code = "", "", 0

    def write(self, text):
        self.out += text

    def errorWrite(self, text):  # noqa: N802 - Cowrie's name
        self.err += text

    def exit(self):
        pass


_MATCHER = None
GROW = 's = "x"; while (length(s) < 1048576) s = s s; '
LIMIT_SECONDS = 2.0  # a whole awk run on the reactor
NON_ASCII = 's = "é"; while (length(s) < 1048576) s = s s; '
AB = "".join(random.Random(1).choice("ab") for _ in range(524288)) + "\n"
# programs whose work grows with the size of their values: (arguments, input, the correct
# result if the run ends by itself); each ends with status 2 or that result within LIMIT_SECONDS
GROWING = {
    "sprintf of %%": (['BEGIN { f = "%%"; while (length(f) < 1048576) f = f f; while (1) t = sprintf(f) }'], None, None),
    "sprintf of unknown": (['BEGIN { f = "%z"; while (length(f) < 1048576) f = f f; while (1) t = sprintf(f) }'], None,
                           None),
    "printf of %%": (['BEGIN { f = "%%"; while (length(f) < 1048576) f = f f; while (1) printf f }'], None, None),
    "wide float": (['BEGIN { while (1) t = sprintf("%.4096f", 1e308) }'], None, None),
    "wide e": (['BEGIN { while (1) t = sprintf("%.4096e", 1e308) }'], None, None),
    "CONVFMT of %%": (['BEGIN { OFMT = "%%"; s = "%%"; while (length(s) < 1048576) s = s s; CONVFMT = s; '
                       'while (1) t = (0.5 "") }'], None, None),
    "wide CONVFMT": (['BEGIN { CONVFMT = "%.4096f"; while (1) t = (1e308 "") }'], None, None),
    "tolower non-ASCII": (["BEGIN { " + NON_ASCII + "while (1) t = tolower(s) }"], None, None),
    "toupper non-ASCII": (["BEGIN { " + NON_ASCII + "while (1) t = toupper(s) }"], None, None),
    "sub on non-ASCII": (["BEGIN { " + NON_ASCII + 'while (1) { t = s; sub(/^/, "", t) } }'], None, None),
    "gsub with many &": (['BEGIN { r = "&"; while (length(r) < 524288) r = r r; while (1) { t = "x"; gsub(/x/, r, t) } }'],
                         None, None),
    "gsub with many escaped &": (['BEGIN { r = "\\\\&"; while (length(r) < 524288) r = r r; '
                                  'while (1) { t = "x"; gsub(/x/, r, t) } }'], None, None),
    "gsub of empty matches": (["BEGIN { " + GROW + 'while (1) { t = s; gsub(/y*/, "", t) } }'], None, None),
    "gsub of every character": (['BEGIN { s = "x"; while (length(s) < 65536) s = s s; while (1) { t = s; '
                                 'gsub(/./, "&", t) } }'], None, None),
    "rebuild of many fields": (['BEGIN { $32767 = "x"; while (1) $1 = "y" }'], None, None),
    "NF of many fields": (["BEGIN { NF = 32767; while (1) NF = 32767 }"], None, None),
    "record of many fields": (['BEGIN { s = "a "; while (length(s) < 1048576) s = s s; while (1) $0 = s }'], None, None),
    "record by characters": (['BEGIN { FS = ""; ' + GROW + "while (1) $0 = s }"], None, None),
    "record by commas": (['BEGIN { FS = ","; s = "a,"; while (length(s) < 1048576) s = s s; while (1) $0 = s }'], None,
                         None),
    "input by characters": (["-F", "", "{ n += NF } END { print n }"], "x" * (1 << 20) + "\n", "1048576\n"),
    "input record again": (["{ while (1) $0 = $0 }"], "a " * 500000 + "\n", None),
    "regex FS on 1 MiB": (['BEGIN { FS = "[ ]+x?" } { n = NF } END { print n }'], "a  x" * 250000 + "\n",
                          "250001\n"),
    "number of many digits": (['BEGIN { s = "1"; while (length(s) < 1048576) s = s s; while (1) n = s + 0 }'], None,
                              None),
    "fraction of many digits": (['BEGIN { s = "1"; while (length(s) < 524288) s = s s; s = "0." s; '
                                 "while (1) n = s + 0 }"], None, None),
    "comparing digit fields": (['BEGIN { s = "1"; while (length(s) < 1048576) s = s s; $0 = s " " s; '
                                "while (1) n = ($1 == $2) }"], None, None),
    "automaton of many states": (["{ while (1) n += ($0 ~ /(a|b)*a(a|b)(a|b)(a|b)(a|b)(a|b)(a|b)(a|b)(a|b)(a|b)(a|b)"
                                  "(a|b)(a|b)(a|b)c/) }"], AB, None),
    "index of a long needle": (['BEGIN { s = "a"; while (length(s) < 524288) s = s s; t = substr(s, 1, 262144) "b"; '
                                "while (1) n = index(s, t) }"], None, None),
    "copying a large value": (["BEGIN { " + GROW + 'while (1) t = s "" }'], None, None),
    "regex of 1 MiB": (["BEGIN { " + GROW + 'if ("a" ~ s) print 1 }'], None, ""),
    "regex of many alternatives": (['BEGIN { s = "a|"; while (length(s) < 1048576) s = s s; s = s "b"; '
                                    'if ("c" ~ s) print 1 }'], None, ""),
    "regex of many groups": (['BEGIN { s = "(x)"; while (length(s) < 1000000) s = s s; if ("c" ~ s) print 1 }'], None,
                             ""),
    "regex of a large class": (['BEGIN { s = "a"; while (length(s) < 524288) s = s s; s = "[" s "]"; '
                                'if ("c" ~ s) print 1 }'], None, ""),
    "new large regex each time": (['BEGIN { s = "a|"; while (length(s) < 524288) s = s s; '
                                   'while (1) { i++; if ("c" ~ (s i)) n++ } }'], None, None),
    "new regex each time": (['BEGIN { s = "a|"; while (length(s) < 65536) s = s s; '
                             'while (1) { i++; if ("c" ~ (s i)) n++ } }'], None, None),
    "new grouped regex each time": (['BEGIN { s = "(a|b)"; while (length(s) < 300000) s = s s; '
                                     'while (1) { i++; if ("c" ~ (s i)) n++ } }'], None, None),
}


class FakeLog:
    def __init__(self):
        self.errors = []

    def err(self, *args, **kwargs):
        self.errors.append((args, kwargs))


def awk(style, args, data=None, version=None, log=None, broken=False):
    """(stdout, stderr, status) of the patched awk of a style; broken makes the interpreter fail."""
    global _MATCHER
    root = patched_root()
    if _MATCHER is None:
        fs = module_all((root / "src/cowrie/commands/fs.py").read_text(),
                        {"HoneyPotCommand": Base, "fs": object(), "CowrieConfig": FakeConfig({})})
        _MATCHER = fs["TpotGrepMatcher"]
    config = FakeConfig({("shell", "awk_style"): style, ("shell", "awk_version"): version or VERSIONS[style]})
    namespace = module_all((root / "src/cowrie/commands/awk.py").read_text(),
                           {"HoneyPotCommand": Base, "FileNotFound": FileNotFoundError, "TpotGrepMatcher": _MATCHER,
                            "CowrieConfig": config, "log": log or FakeLog()})
    if broken:
        namespace["TpotAwk"].run = lambda *_: 1 // 0
    cmd = namespace["Command_tpot_awk"](args, None if data is None else data.encode())
    cmd.start()
    return cmd.out, cmd.err, cmd.exit_code


class AwkTest(unittest.TestCase):
    def test_programs_print_what_every_awk_prints(self):
        for args, data, out in SAME:
            for style in VERSIONS:
                with self.subTest(args=args, style=style):
                    self.assertEqual(awk(style, args, data), (out, "", 0 if "exit 4" not in args[0] else 4))
            with self.subTest(args=args, style="mawk 2024"):
                self.assertEqual(awk("mawk", args, data, "1.3.4 20240123")[0], out)

    def test_programs_print_what_the_persona_awk_prints(self):
        for args, data, by_style in DIFFERENT:
            for style, (out, err, code) in by_style.items():
                with self.subTest(args=args, style=style):
                    got = awk(style, args, data)
                    self.assertEqual((got[0], got[2]), (out, code))
                    if err is not None:
                        self.assertEqual(got[1], err)
                    else:
                        self.assertIn("Usage:", got[1])

    def test_mawk_of_2024_prints_large_integers(self):
        self.assertEqual(awk("mawk", ["BEGIN { print 2147483648, 2^53 }"], "", "1.3.4 20240123")[0],
                         "2147483648 9007199254740992\n")
        self.assertEqual(awk("mawk", ['BEGIN { printf "%d\\n", 2147483648 }'], "")[0], "2147483647\n")

    def test_files_of_the_emulated_file_system(self):
        self.assertEqual(awk("mawk", ["-F:", "$3 >= 1000 {print $1}", "/etc/passwd"]), ("ubuntu\n", "", 0))
        self.assertEqual(awk("gawk", ["/MemTotal/ {print $2}", "/proc/meminfo"]), ("8023548\n", "", 0))
        self.assertEqual(awk("gawk", ["END {print NR, FILENAME}", "/etc/passwd", "x=1", "/proc/meminfo"]),
                         ("5 /proc/meminfo\n", "", 0))
        out, err, code = awk("gawk", ["{print}", "/etc"])
        self.assertEqual((out, code), ("", 0))
        self.assertIn("is a directory: skipped", err)
        self.assertEqual(awk("mawk", ["-f", "/etc/passwd"], "")[2], 2)  # a file that is no program

    def test_left_out_awk_ends_without_output(self):
        for program in LEFT_OUT:
            for style in VERSIONS:
                with self.subTest(program=program, style=style):
                    self.assertEqual(awk(style, [program], "a:b\nc:d\n"), ("", "", 2))

    def test_help_and_version_follow_the_persona(self):
        self.assertTrue(awk("gawk", ["--version"])[0].startswith("GNU Awk 5.1.0, API: 3.0"))
        self.assertTrue(awk("gawk", ["-V"], version="4.0.2")[0].startswith("GNU Awk 4.0.2\n"))
        self.assertIn("\t-L[fatal|invalid|no-ext]", awk("gawk", ["--help"])[0])
        self.assertEqual(awk("mawk", ["-W", "version"]), (
            "mawk 1.3.4 20200120\nCopyright 2008-2019,2020, Thomas E. Dickey\n"
            "Copyright 1991-1996,2014, Michael D. Brennan\n\n",
            "random-funcs:       srandom/random\nregex-funcs:        internal\ncompiled limits:\n"
            "sprintf buffer      8192\nmaximum-integer     2147483647\n", 0))
        new = awk("mawk", ["--version"], version="1.3.4 20240123")
        self.assertIn("maximum-integer     9223372036854775808", new[0])
        self.assertEqual(awk("mawk", ["--version"])[2], 2)  # the mawk of 2020 has no long options
        self.assertEqual(awk("mawk", []), ("", awk("mawk", ["-W", "usage"])[0], 0))
        self.assertIn("-W posix         stricter", awk("mawk", ["-Wu"], version="1.3.4 20240123")[0])
        out, err, code = awk("busybox", ["-W", "version"])
        self.assertEqual((out, code), ("", 1))
        self.assertTrue(err.startswith("awk: warning: option -W is ignored\nBusyBox v1.36.1"))
        self.assertIn("-e AWK_PROGRAM", err)

    def test_old_gawk_and_mawk(self):
        """gawk 4.0.2 of centos:7 and mawk 1.3.3 of debian:9."""
        out, err, code = awk("gawk", [], version="4.0.2")
        self.assertEqual((out, code), ("", 1))
        self.assertIn("\t-L [fatal]\t\t--lint[=fatal]\n\t-n\t\t\t--non-decimal-data\n", err)
        self.assertNotIn("--debug", err)
        self.assertTrue(err.endswith("\tgawk -F: '{ print $1 }' /etc/passwd\n"))
        self.assertEqual(awk("gawk", ["{print}", "/nonexist"], "", version="4.0.2")[1],
                         "awk: fatal: cannot open file `/nonexist' for reading (No such file or directory)\n")
        self.assertEqual(awk("gawk", ["-f", "/nope"], version="4.0.2")[1],
                         "awk: fatal: can't open source file `/nope' for reading (No such file or directory)\n")
        self.assertEqual(awk("mawk", [], version="1.3.3"), ("", "", 0))
        self.assertEqual(awk("mawk", ["-W", "version"], version="1.3.3"), (
            "mawk 1.3.3 Nov 1996, Copyright (C) Michael D. Brennan\n\n",
            "compiled limits:\nmax NF             32767\nsprintf buffer      2040\n", 0))

    def test_limits(self):
        cases = {"endless while": (["BEGIN { while (1) ; }"], None),
                 "endless for": (["BEGIN { for (;;) x++ }"], None),
                 "endless output": (['BEGIN { while (1) print "xxxxxxxxxxxxxxxx" }'], None),
                 "growing string": (['BEGIN { s = "x"; while (1) s = s s }'], None),
                 "wide printf": (['BEGIN { printf "%999999999d", 1 }'], None),
                 "many fields": (['{ $100000 = "x" }'], "a\n"),
                 "deep parentheses": (["BEGIN { print " + "(" * 5000 + "1" + ")" * 5000 + " }"], None),
                 "deep blocks": (["BEGIN " + "{" * 5000 + "}" * 5000], None),
                 "long chain": (["BEGIN { print " + "+".join(["1"] * 20000) + " }"], None),
                 "deep unary": (["BEGIN { print " + "!" * 5000 + "1 }"], None),
                 "large program": (["BEGIN { x = 1 }" + " " * (64 * 1024)], None),
                 "large regex": (['BEGIN { s = "(a|b)"; while (i++ < 12) s = s s; if ("ab" ~ s) print 1 }'], None),
                 "tolower of a large value": (["BEGIN { " + GROW + "while (1) t = tolower(s) }"], None),
                 "toupper of a large value": (["BEGIN { " + GROW + "while (1) t = toupper(s) }"], None),
                 "index in a large value": (["BEGIN { " + GROW + 'while (1) n = index(s, "y") }'], None),
                 "substr of a large value": (["BEGIN { " + GROW + "while (1) t = substr(s, 2) }"], None),
                 "length of a large value": (["BEGIN { " + GROW + "while (1) n = length(s) }"], None),
                 "comparing large values": (["BEGIN { " + GROW + "t = s; while (1) n = (s == t) }"], None),
                 "sprintf of many conversions": (['BEGIN { f = "%c"; while (length(f) < 1048576) f = f f; '
                                                  "while (1) t = sprintf(f) }"], None),
                 "number of a large value": (['BEGIN { s = " "; while (length(s) < 1048576) s = s s; '
                                              "while (1) n = s + 0 }"], None),
                 "large numeric field": (['BEGIN { s = "1"; while (length(s) < 1048576) s = s s; $0 = s; '
                                          "while (1) n = ($1 < 2) }"], None),
                 "new regex each time": (['BEGIN { while (1) { i++; if ("ab" ~ ("(a|b){200}" i)) n++ } }'], None),
                 "matching a large value": (["BEGIN { " + GROW + "while (1) n = (s ~ /y$/) }"], None),
                 "gsub on a large value": (["BEGIN { " + GROW + 'while (1) { t = s; gsub(/x/, "y", t) } }'], None),
                 "large values in many variables": (['BEGIN { s = "x"; while (length(s) < 524288) s = s s; '
                                                     + " ".join(f"v{k} = s;" for k in range(3000)) + " }"], None)}
        for name, (args, data) in cases.items():
            for style in VERSIONS:
                with self.subTest(name, style=style):
                    started = time.monotonic()
                    out, _, code = awk(style, args, data)
                    self.assertEqual(code, 2)
                    self.assertLessEqual(len(out), 1024 * 1024)
                    self.assertLess(time.monotonic() - started, LIMIT_SECONDS)

    def test_growing_values_end_in_time(self):
        for name, (args, data, result) in GROWING.items():
            for style in VERSIONS:
                with self.subTest(name, style=style):
                    started = time.monotonic()
                    out, _, code = awk(style, args, data)
                    self.assertLess(time.monotonic() - started, LIMIT_SECONDS)
                    if code != 2:
                        self.assertEqual((out, code), (result, 0))

    def test_more_fields_than_mawk_has_end_awk(self):
        for style in VERSIONS:
            with self.subTest(style=style):
                self.assertEqual(awk(style, ["{ print NF }"], "a " * 32767 + "\n"), ("32767\n", "", 0))
                self.assertEqual(awk(style, ["{ print NF }"], "a " * 32768 + "\n"), ("", "", 2))
                self.assertEqual(awk(style, ['BEGIN { $32767 = "x"; print NF }']), ("32767\n", "", 0))
                self.assertEqual(awk(style, ['BEGIN { $32768 = "x" }']), ("", "", 2))
                self.assertEqual(awk(style, ["BEGIN { NF = 32768 }"]), ("", "", 2))

    def test_long_digit_runs_take_linear_time(self):
        program = 'BEGIN { s="1"; while (length(s) < 32768) s = s s; $0 = s "x"; if ($1 == 1) print "y"; print "z" }'
        for style in VERSIONS:
            with self.subTest(style=style):
                started = time.monotonic()
                self.assertEqual(awk(style, [program]), ("z\n", "", 0))
                self.assertLess(time.monotonic() - started, LIMIT_SECONDS)
        data = "1" * 300000 + "x 1" * 3 + "\n"
        started = time.monotonic()
        self.assertEqual(awk("mawk", ["$1 == 1 { print 1 } $2 + 0 == 1 { print 2 }"], data), ("2\n", "", 0))
        self.assertLess(time.monotonic() - started, LIMIT_SECONDS)

    def test_printf_negative_star_precision_is_omitted(self):
        program = 'BEGIN { printf "%.*d|%.*f|%*d|\\n", -3, 5, -1, 2.5, -4, 7 }'
        for style in ("gawk", "mawk"):
            with self.subTest(style=style):
                self.assertEqual(awk(style, [program]), ("5|2.500000|7   |\n", "", 0))
        self.assertEqual(awk("busybox", [program]), ("", "awk: cmd. line:1: %*x formats are not supported\n", 1))

    def test_record_separator_is_left_out(self):
        self.assertEqual(awk("gawk", ['BEGIN { printf "[%s][%s]", RS, SUBSEP }']), ("[\n][\x1c]", "", 0))
        for program in ('BEGIN { RS = "" } { print }', "BEGIN { RS = \";\" } { print }", 'BEGIN { SUBSEP = ":" }'):
            for style in VERSIONS:
                with self.subTest(program=program, style=style):
                    self.assertEqual(awk(style, [program], "a\nb\n"), ("", "", 2))
        self.assertEqual(awk("gawk", ["-v", "RS=x", "{ print }"], "a\n"), ("", "", 2))

    def test_internal_errors_go_to_the_log(self):
        log = FakeLog()
        self.assertEqual(awk("gawk", ["{ print }"], "a\n", log=log, broken=True), ("", "", 2))
        self.assertEqual(len(log.errors), 1)

    def test_huge_input_is_read_up_to_the_limit(self):
        data = "a b c\n" * (3 * 1024 * 1024 // 6)
        started = time.monotonic()
        out, err, code = awk("mawk", ["END { print NR }"], data)
        records = -(-1024 * 1024 // 6)  # the last one cut at the limit
        self.assertEqual((out, err, code), (f"{records}\n", "", 0))
        out, _, code = awk("gawk", ["/b/ { n++ } END { print n }"], data)
        self.assertEqual((out, code), (f"{records}\n", 0))
        self.assertLess(time.monotonic() - started, 30)

    def test_bot_one_liners_on_every_persona(self):
        """`df -h | awk 'NR==2{print $5}'` and the other df forms in the awk of every persona."""
        df = test_df.DfTest()
        for persona in catalog.PERSONAS:
            ident = draw(persona, 4)
            content = persona.builder(persona, ident)
            data = system.df_table(persona, ident, content.filesystems)
            keys = system.awk_keys(persona, ident)
            style, version = keys["awk_style"], keys["awk_version"]
            with self.subTest(persona=persona.id, style=style):
                human = df.df(["-h"], data)[0]
                second = human.splitlines()[1].split()
                # a long name on a line of its own (BusyBox) has no fourth field
                self.assertEqual(awk(style, ["FNR == 2 {print $4}"], human, version),
                                 ((second[3] if len(second) > 3 else "") + "\n", "", 0))
                self.assertEqual(awk(style, ["NR==2{print $5}"], human, version)[0].strip(),
                                 human.splitlines()[1].split()[4] if len(human.splitlines()[1].split()) > 4 else "")
                last = df.df(["-k"], data)[0].splitlines()[-1] + "\n"
                self.assertEqual(awk(style, ["{print $4}"], last, version), (last.split()[3] + "\n", "", 0))
                root = df.df(["-h", "/"], data)[0]
                self.assertTrue(awk(style, ['$NF=="/"{print $5}'], root, version)[0].endswith("%\n"))
                self.assertEqual(awk(style, ["NR==2{print $2}"], FREE_M, version), ("7834\n", "", 0))
                self.assertEqual(awk(style, ["{print $(NF-2)}"], UPTIME, version), ("0.00,\n", "", 0))


if __name__ == "__main__":
    unittest.main()
