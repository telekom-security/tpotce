"""awk of the patched Cowrie (patch_awk): the forms scripts run on df, free, uptime, ps and
/proc/cpuinfo, in the awk of the persona (gawk, mawk or BusyBox). The reference texts are the
output of awk in almalinux:9 (gawk 5.1.0), debian:12 (mawk 1.3.4 20200120), ubuntu:24.04
(mawk 1.3.4 20240123) and busybox:1.36 containers for the same programs and input."""
import json
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
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
LEFT_OUT = ['BEGIN { system("uname") }',  # runs a command of the shell
            'BEGIN { "uname" | getline x; print x }',  # getline from a command
            "{ getline; print }",  # getline: reads past the record the rule runs on
            '{ print $1 > "/tmp/out" }', '{ print $1 >> "/tmp/out" }',  # output to files of the session
            '{ print $1 | "sort" }']  # output to a command
# arrays, split and match (tests.test_awk_arrays has the recorded cases): the same in every awk
ARRAYS = [("!seen[$0]++", "a:b\nc:d\na:b\n", "a:b\nc:d\n"),
          ('{ n = split($0, p, ":") } END { print n, p[1], p[n] }', "a:b\nc:d\n", "2 c d\n"),
          ("{ print match($0, /[bd]/), RSTART, RLENGTH }", "a:b\nc:d\nxyz\n", "3 3 1\n3 3 1\n0 0 -1\n"),
          ("{ a[$1]++ } END { for (k in a) n++; print n, a[\"a:b\"] }", "a:b\nc:d\na:b\n", "2 2\n"),
          ('BEGIN { SUBSEP = ":"; a[1, 2] = 3; for (k in a) print k, a[k] }', "", "1:2 3\n")]
EXISTING = {"/etc/passwd": PASSWD.encode(), "/proc/meminfo": MEMINFO.encode()}
# language parts recorded in containers (fixtures/awk_lang.json): image -> (awk_style, awk_version, text_libc)
LANG = json.loads((Path(__file__).parent / "fixtures" / "awk_lang.json").read_text(encoding="utf-8"))["images"]
LANG_STYLES = {
    "centos_7": ("gawk", "4.0.2", None), "debian_9": ("gawk", "4.1.4", None), "rockylinux_9": ("gawk", "5.1.0", None),
    "debian_12": ("gawk", "5.2.1", None), "debian_10": ("mawk", "1.3.3", None),
    "ubuntu_22.04": ("mawk", "1.3.4 20200120", None), "ubuntu_24.04": ("mawk", "1.3.4 20240123", None),
    "debian_13": ("mawk", "1.3.4 20250131", None),
    "busybox_1.36.1-musl": ("busybox", "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)", "musl")}
# the part of the language a recorded case is about, by the start of its label
LANG_PARTS = (("index empty", "strings"), ("function", "functions"), ("param shadows", "functions"),
              ("recursion", "functions"), ("array by reference", "functions"), ("local parameter", "functions"),
              ("undefined function", "functions"), ("range", "ranges"), ("rand", "rand"), ("srand", "rand"),
              ("CONVFMT", "numbers"), ("arithmetic", "numbers"), ("exponent form", "numbers"),
              ("big literal", "numbers"), ("printf", "numbers"), ("index/toupper", "bytes"), ("length utf8", "bytes"),
              ("substr utf8", "bytes"))
CLOCK_SEEDED = ("srand()", "srand() two runs 1 s apart")
OLD_MAWK_CLOCK = ("rand in two runs", "rand in two runs 1 s apart", "rand twice in one run", "srand return value",
                  "srand return value 2") + CLOCK_SEEDED


def lang_volatile(key, label):
    """Whether a recorded case carries the clock or the entropy of its run (its form is compared, not its text)."""
    style, version, _ = LANG_STYLES[key]
    if style != "mawk":
        return label in CLOCK_SEEDED + ("srand return value 2",)
    if version == "1.3.4 20250131":
        return label in ("srand return value", "srand return value 2")
    if version == "1.3.4 20240123" and label.startswith(("srand(1)", "CONVFMT %d")):
        return True
    return label in OLD_MAWK_CLOCK


def lang_runs(case):
    """The awk runs of a recorded line: `for i in 1 2 3; do X; done`, `X; X`, `X; sleep 1; X` or X."""
    found = re.fullmatch(r"for i in 1 2 3; do (.*); done", case)
    if found:
        return [found.group(1)] * 3
    return [run for run in re.split(r"; (?=awk |sleep 1)", case) if run != "sleep 1"]


def lang_form(text):
    return re.sub(r"-?[0-9][0-9.e+-]*", "N", text)


ARM64 = "linux-aarch64-lsb"  # the platform of the recordings of awk_lang.json and awk_big.json
# the cases that depend on the platform, recorded on amd64 and arm64 (fixtures/awk_platform.json):
#   image -> (awk_style, awk_version, text_libc, [shell] arch)
PLATFORM = json.loads((Path(__file__).parent / "fixtures" / "awk_platform.json").read_text(encoding="utf-8"))["images"]
PLATFORM_STYLES = {
    "debian_13_amd64": ("mawk", "1.3.4 20250131", None, "linux-x64-lsb"),
    "busybox_1.36.1-musl_amd64": ("busybox", "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)", "musl", "linux-x64-lsb"),
    "busybox_1.36.1-musl_arm64": ("busybox", "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)", "musl", ARM64),
    "busybox_1.25.1_amd64": ("busybox", "BusyBox v1.25.1 (2016-10-07 18:17:00 UTC)", "uclibc", "linux-x64-lsb"),
    "rockylinux_9_amd64": ("gawk", "5.1.0", None, "linux-x64-lsb"),
    "rockylinux_9_arm64": ("gawk", "5.1.0", None, ARM64),
    "rockylinux_9_arm64_utf8": ("gawk", "5.1.0", None, ARM64)}
PLATFORM_VOLATILE = ("print rand(), rand() }'; ", "srand(); print rand()", "print srand(5)")

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

    def writeBytes(self, data):  # noqa: N802 - Cowrie's name
        self.out += data.decode("utf-8", "surrogateescape")

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
    "OFS join of many fields": (["BEGIN { " + NON_ASCII + "while (i++ < 26) t = tolower(s); "
                                 'o = "x"; while (length(o) < 262144) o = o o; OFS = o; NF = 32767; print "never" }'],
                                None, None),
    "NF with a large OFS": (['BEGIN { o = "x"; while (length(o) < 32768) o = o o; OFS = o; while (1) NF = 32767 }'],
                            None, None),
    "$1 = $1 with a large OFS": (['BEGIN { o = "x"; while (length(o) < 262144) o = o o; OFS = o; '
                                  '$0 = "a b c d e f"; while (1) $1 = $1 }'], None, None),
    "$N with a large OFS": (['BEGIN { o = "x"; while (length(o) < 1048576) o = o o; OFS = o; $3 = "z"; '
                             'print "never" }'], None, None),
    "new grouped regex each time": (['BEGIN { s = "(a|b)"; while (length(s) < 300000) s = s s; '
                                     'while (1) { i++; if ("c" ~ (s i)) n++ } }'], None, None),
}


class FakeLog:
    def __init__(self):
        self.errors = []

    def err(self, *args, **kwargs):
        self.errors.append((args, kwargs))


def awk(style, args, data=None, version=None, log=None, broken=False, libc=None, collate=None, clock=None, arch=None):
    """(stdout, stderr, status) of the patched awk of a style; broken makes the interpreter fail; collate is
    [shell] text_collate (posix: the C locale of the persona); clock the seconds of time.time(); arch [shell] arch
    (linux-x64-lsb when not given)."""
    global _MATCHER
    root = patched_root()
    if _MATCHER is None:
        fs = module_all((root / "src/cowrie/commands/fs.py").read_text(),
                        {"HoneyPotCommand": Base, "fs": object(), "CowrieConfig": FakeConfig({})})
        _MATCHER = fs["TpotGrepMatcher"]
    config = FakeConfig({("shell", "awk_style"): style, ("shell", "awk_version"): version or VERSIONS[style],
                         **({("shell", "text_libc"): libc} if libc else {}),
                         **({("shell", "text_collate"): collate} if collate else {}),
                         **({("shell", "arch"): arch} if arch else {})})
    namespace = module_all((root / "src/cowrie/commands/awk.py").read_text(),
                           {"HoneyPotCommand": Base, "FileNotFound": FileNotFoundError, "TpotGrepMatcher": _MATCHER,
                            "CowrieConfig": config, "log": log or FakeLog()})
    if broken:
        namespace["TpotAwk"].run = lambda *_: 1 // 0
    namespace["TpotAwk"].clock = staticmethod(time.time if clock is None else lambda: clock)
    cmd = namespace["Command_tpot_awk"](args, None if data is None else data.encode("utf-8", "surrogateescape"))
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

    def test_language_parts_as_recorded(self):
        count = 0
        for key, (style, version, libc) in LANG_STYLES.items():
            entry = LANG[key]
            for case, expected in entry["cases"].items():
                label = entry["meta"].get(case)
                part = next((part for start, part in LANG_PARTS if label and label.startswith(start)), None)
                if part is None:
                    continue
                out, err, code = "", "", 0
                for run in lang_runs(case):
                    locale, _, run = run.partition(" ") if run.startswith("LC_ALL=") else ("", "", run)
                    data = None
                    found = re.fullmatch(r"printf '([^']*)' \| (.*)", run)
                    if found:
                        data, run = found.group(1).encode().decode("unicode_escape"), found.group(2)
                    # gawk counts bytes in the C locale (centos:7 has no C.UTF-8: C); mawk and BusyBox always
                    collate = "posix" if style == "gawk" and locale in ("LC_ALL=C",) + (
                        ("LC_ALL=C.UTF-8",) if key == "centos_7" else ()) else None
                    got = awk(style, shlex.split(run)[1:], data, version=version, libc=libc, collate=collate,
                              arch=ARM64)
                    out, err, code = out + got[0], err + got[1], got[2]
                with self.subTest(image=key, case=label):
                    if lang_volatile(key, label):
                        self.assertEqual((lang_form(out), err, code), (lang_form(expected[0]), *expected[1:]))
                    else:
                        self.assertEqual((out, err, code), tuple(expected))
                count += 1
        self.assertEqual(count, 366)  # every recorded case but the version calls

    def test_functions_end_awk_where_they_go_too_deep(self):
        for style in VERSIONS:
            with self.subTest(style=style):
                self.assertEqual(awk(style, ["function f(n) { return f(n + 1) } BEGIN { f(1) }"]), ("", "", 2))
                self.assertEqual(awk(style, ["function f(n) { return n < 2 ? n : f(n-1) + f(n-2) } BEGIN { print f(15) }"]),
                                 ("610\n", "", 0))
                self.assertEqual(awk(style, ['function f(a, k) { for (k in a) n++; delete a; return n } '
                                             'BEGIN { x[1]; x[2]; print f(x), length(x) }']), ("2 0\n", "", 0))
                self.assertEqual(awk(style, ["function f(x) { x[1] = 1 } BEGIN { f(y); f(y); print length(y) }"]),
                                 ("1\n", "", 0))
                self.assertEqual(awk(style, ["BEGIN { return 1 }"])[2], 1 if style != "mawk" else 2)

    def test_range_patterns(self):
        for style in VERSIONS:
            with self.subTest(style=style):
                self.assertEqual(awk(style, ["NR==2, NR==3 { print NR } /c/, /a/"], "a\nb\nc\nd\na\n"),
                                 ("2\n3\nc\nd\na\n", "", 0))
                self.assertEqual(awk(style, ["/b/,/b/ { n++ } END { print n }"], "b\nb\n"), ("2\n", "", 0))

    def test_rand_seeded_by_the_clock_as_recorded(self):
        """srand() and mawk's start seed take the clock: the second of each recorded value (derived: the second
        within 200 of the recording whose seed gives the recorded value, one each)."""
        cases = (("gawk", "4.0.2", 1792616316, "BEGIN { srand(); print rand() }", "0.655639\n"),
                 ("gawk", "4.1.4", 1792616329, "BEGIN { srand(); print rand() }", "0.237515\n"),
                 ("gawk", "5.1.0", 1792616348, "BEGIN { srand(); print rand() }", "0.0458192\n"),
                 ("gawk", "5.2.1", 1792616326, "BEGIN { srand(); print rand() }", "0.389894\n"),
                 ("busybox", None, 1792616340, "BEGIN { srand(); print rand() }", "0.399931\n"),
                 ("mawk", "1.3.3", 1792616327, "BEGIN { print rand(), rand() }", "0.774445 0.0905174\n"),
                 ("mawk", "1.3.3", 1792616329, "BEGIN { srand(); print rand() }", "0.0375202\n"),
                 ("mawk", "1.3.4 20200120", 1792616333, "BEGIN { print rand(), rand() }", "0.841277 0.941606\n"),
                 ("mawk", "1.3.4 20200120", 1792616334, "BEGIN { srand(); print rand() }", "0.201178\n"))
        for style, version, clock, program, out in cases:
            with self.subTest(style=style, version=version, program=program):
                libc = "musl" if style == "busybox" else None
                self.assertEqual(awk(style, [program], version=version, libc=libc, clock=clock + 0.5), (out, "", 0))
        self.assertEqual(awk("mawk", ["BEGIN { print srand(5); print srand() }"], version="1.3.3", clock=1792616331.2),
                         ("1792616331\n5\n", "", 0))
        # mawk 1.3.4 of 2025 seeds from the clock in nanoseconds: arm64 casts it to 2**32 - 1, x86_64 keeps its low
        #   32 bits (fixtures/awk_platform.json)
        program = ["BEGIN { print rand(); srand(); print rand() }"]
        self.assertEqual(awk("mawk", program, version="1.3.4 20250131", arch=ARM64), ("0.118709\n0.118709\n", "", 0))
        clock = 1792666980.1
        low = int(clock * 1e9) & 0xffffffff
        self.assertNotEqual(awk("mawk", program, version="1.3.4 20250131", clock=clock),
                            awk("mawk", program, version="1.3.4 20250131", clock=clock + 1))
        self.assertEqual(awk("mawk", ["BEGIN { print rand() }"], version="1.3.4 20250131", clock=clock),
                         awk("mawk", [f"BEGIN {{ srand({low}); print rand() }}"], version="1.3.4 20250131"))

    def test_busybox_keeps_the_first_text_of_a_number_in_its_variable(self):
        program = 'BEGIN { x = 0.5; CONVFMT = "%.2f"; a = x ""; CONVFMT = "%.3f"; print a, x "", 0.5 "", x + 0 "" }'
        self.assertEqual(awk("busybox", [program]), ("0.50 0.50 0.500 0.500\n", "", 0))
        self.assertEqual(awk("gawk", [program]), ("0.50 0.500 0.500 0.500\n", "", 0))
        program = ['BEGIN { printf "%d %x %u|%d\\n", 2^32 + 5, -1, -1, -2^40 }']
        self.assertEqual(awk("busybox", program, arch=ARM64), ("2147483647 ffffffff 4294967295|-2147483648\n", "", 0))
        self.assertEqual(awk("busybox", program), ("-2147483648 ffffffff 4294967295|-2147483648\n", "", 0))

    def test_busybox_copies_of_a_number_have_their_own_text(self):
        """A copy takes the cached text of a number, if any, and then has its own (BusyBox copyvar; awk_platform.json)."""
        program = 'BEGIN { x = 0.5; s = x ""; y = x; CONVFMT = "%.2f"; print y "" }'
        self.assertEqual(awk("busybox", [program]), ("0.5\n", "", 0))
        cases = (
            ('BEGIN { x = 0.5; y = x; CONVFMT = "%.2f"; a = y ""; CONVFMT = "%.3f"; print a, y "", x "" }',
             "0.50 0.50 0.500\n", "0.50 0.500 0.500\n"),
            ('BEGIN { x = 0.5; CONVFMT = "%.2f"; s = x ""; y = x; CONVFMT = "%.3f"; print y "", x "" }',
             "0.50 0.50\n", "0.500 0.500\n"),
            ('function f(v) { CONVFMT = "%.2f"; return v "" } '
             'BEGIN { x = 0.5; a = f(x); CONVFMT = "%.3f"; print a, x "" }', "0.50 0.500\n", "0.50 0.500\n"),
            ('function f(v) { return v "" } '
             'BEGIN { x = 0.5; CONVFMT = "%.2f"; s = x ""; CONVFMT = "%.3f"; print f(x), x "" }',
             "0.50 0.50\n", "0.500 0.500\n"),
            ('BEGIN { x = 0.5; a[1] = x; CONVFMT = "%.2f"; s = a[1] ""; CONVFMT = "%.3f"; print s, x "", a[1] "" }',
             "0.50 0.500 0.50\n", "0.50 0.500 0.500\n"),
        )
        for program, busybox, gawk in cases:
            with self.subTest(program=program):
                self.assertEqual(awk("busybox", [program]), (busybox, "", 0))
                self.assertEqual(awk("gawk", [program]), (gawk, "", 0))
                self.assertEqual(awk("mawk", [program]), (gawk, "", 0))

    def test_qnap_busybox_1_24_1_awk_follows_its_x86_64_platform(self):
        """QNAP's BusyBox 1.24.1 on x86_64: C conversions of the x86_64 kind, hash and numbers as in every BusyBox
        (the 1.14.1 to 1.36.1 sources; derived, not recorded for 1.24.1)."""
        persona = next(p for p in catalog.PERSONAS if p.id == "qnap-qts")
        keys = system.awk_keys(persona, draw(persona, 4))
        self.assertEqual((persona.arch, keys["awk_style"], keys["awk_version"].split(" (")[0]),
                         ("linux-x64-lsb", "busybox", "BusyBox v1.24.1"))
        program = ['BEGIN { printf "%d %x %u|%d\\n", 2^32 + 5, -1, -1, -2^40; print 2^62, 2^63, 0.5 "" }']
        self.assertEqual(awk("busybox", program, version=keys["awk_version"], arch=persona.arch, libc="glibc"),
                         ("-2147483648 ffffffff 4294967295|-2147483648\n4611686018427387904 9.22337e+18 0.5\n", "", 0))

    def test_platform_cases_as_recorded(self):
        count = 0
        for key, (style, version, libc, arch) in PLATFORM_STYLES.items():
            for case, expected in PLATFORM[key]["cases"].items():
                if not case.startswith("awk '"):
                    continue  # uname -m and the version calls
                out, err, code = "", "", 0
                for run in lang_runs(case):
                    got = awk(style, shlex.split(run)[1:], version=version, libc=libc, arch=arch)
                    out, err, code = out + got[0], err + got[1], got[2]
                with self.subTest(image=key, case=case):
                    if style == "mawk" and any(part in case for part in PLATFORM_VOLATILE):
                        self.assertEqual((lang_form(out), err, code), (lang_form(expected[0]), *expected[1:]))
                    elif "Invalid multibyte" in expected[1]:  # gawk's warning of length() is not emulated
                        self.assertEqual((out, err, code), (expected[0], "", expected[2]))
                    else:
                        self.assertEqual((out, err, code), tuple(expected))
                count += 1
        self.assertEqual(count, 57)

    def test_mawk_deletes_a_large_array_in_time(self):
        started = time.monotonic()
        program = "BEGIN { for (i = 0; i < 50000; i++) a[i]; for (k in a) delete a[k]; print length(a) }"
        self.assertEqual(awk("mawk", [program]), ("0\n", "", 0))
        self.assertLess(time.monotonic() - started, LIMIT_SECONDS)

    def test_bytes_of_input_and_of_the_program(self):
        """mawk and BusyBox count bytes, gawk characters in a UTF-8 locale and bytes in the C locale of a persona
        (DSM: text_collate posix); a byte that is no UTF-8 passes through."""
        program = '{ print length($0), length("é"), substr($0, 2, 1), toupper($0) }'
        data = "aé\n"
        self.assertEqual(awk("gawk", [program], data), ("2 1 é AÉ\n", "", 0))
        expected = "3 2 \udcc3 Aé\n"
        for style, collate in (("gawk", "posix"), ("mawk", None), ("busybox", None)):
            with self.subTest(style=style):
                self.assertEqual(awk(style, [program], data, collate=collate), (expected, "", 0))
        self.assertEqual(awk("gawk", ["{ print }"], "a\udcff\n"), ("a\udcff\n", "", 0))

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

    def test_arrays_split_and_match(self):
        for program, data, out in ARRAYS:
            for style in VERSIONS:
                with self.subTest(program=program, style=style):
                    self.assertEqual(awk(style, [program], data), (out, "", 0))

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

    def test_busybox_1_19_ends_its_usage_with_a_blank_line(self):
        # awk -x of the BusyBox v1.19.4 of the Archer C7 v5 (220715 image, qemu-mips-static)
        expected = (
            'awk: invalid option -- x\n'
            'BusyBox v1.19.4 (2022-07-14 23:31:52 EDT) multi-call binary.\n'
            '\n'
            'Usage: awk [OPTIONS] [AWK_PROGRAM] [FILE]...\n'
            '\n'
            '\t-v VAR=VAL\tSet variable\n'
            '\t-F SEP\t\tUse SEP as field separator\n'
            '\t-f FILE\t\tRead program from FILE\n'
            '\n'
        )
        self.assertEqual(awk("busybox", ["-x"], version="BusyBox v1.19.4 (2022-07-14 23:31:52 EDT)", libc="uclibc"),
                         ("", expected, 1))
        # the getopt of the other C libraries (glibc as busybox:1.36.1, musl of OpenWrt)
        self.assertTrue(awk("busybox", ["-x"])[1].startswith("awk: invalid option -- 'x'\nBusyBox v1.36.1"))
        self.assertTrue(awk("busybox", ["-x"], libc="musl")[1].startswith("awk: unrecognized option: x\n"))
        self.assertTrue(awk("busybox", ["-f"], libc="uclibc")[1].startswith(
            "awk: option requires an argument -- f\n"))

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

    def test_printf_widths_of_many_digits_end_awk(self):
        for program in ('BEGIN { w = "1"; while (length(w) < 5000) w = w w; printf "%" w "d", 1 }',
                        'BEGIN { w = "1"; while (length(w) < 5000) w = w w; printf "%." w "f", 1 }',
                        'BEGIN { printf "%1234567890d|", 1 }'):
            for style in VERSIONS:
                with self.subTest(program=program, style=style):
                    log = FakeLog()
                    self.assertEqual(awk(style, [program], log=log), ("", "", 2))
                    self.assertEqual(log.errors, [])
        self.assertEqual(awk("gawk", ['BEGIN { printf "%000000004d|", 1 }']), ("0001|", "", 0))

    def test_records_are_sized_before_they_are_built(self):
        """A record of fields and OFS past the value limit ends awk before it is joined."""
        programs = ['BEGIN { o = "x"; while (length(o) < 32768) o = o o; OFS = o; NF = 32767; print "never" }',
                    'BEGIN { o = "x"; while (length(o) < 32768) o = o o; OFS = o; $32767 = "z"; print "never" }',
                    'BEGIN { o = "x"; while (length(o) < 32768) o = o o; OFS = o; NF = 100; $1 = $1; print "never" }']
        for program in programs:
            with self.subTest(program=program):
                tracemalloc.start()
                try:
                    self.assertEqual(awk("gawk", [program]), ("", "", 2))
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertLess(peak, 64 * 1024 * 1024)

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
        for program in ('BEGIN { RS = "" } { print }', "BEGIN { RS = \";\" } { print }"):
            for style in VERSIONS:
                with self.subTest(program=program, style=style):
                    self.assertEqual(awk(style, [program], "a\nb\n"), ("", "", 2))
        self.assertEqual(awk("gawk", ["-v", "RS=x", "{ print }"], "a\n"), ("", "", 2))

    def test_substr_and_index_as_the_persona_awk(self):
        """substr with a start below 1 and fractions, index of an empty string: the tables of gawk
        (BusyBox prints the same substr) and of mawk, recorded in containers (fixtures/awk_substr.json)."""
        import json
        refs = json.loads((Path(__file__).parent / "fixtures" / "awk_substr.json").read_text(encoding="utf-8"))
        for style, table in (("gawk", "gawk"), ("busybox", "gawk"), ("mawk", "mawk")):
            with self.subTest(style=style):
                out, err, code = awk(style, [refs["program"]])
                self.assertEqual((err, code), ("", 0))
                self.assertEqual(out.splitlines(), refs[table].splitlines())
        # mawk 1.3.3 (EdgeOS) shortens as POSIX says: a start below 1 takes its distance from 1 off the length
        out, err, code = awk("mawk", [refs["program"]], version="1.3.3")
        self.assertEqual((out.splitlines(), err, code), (refs["mawk-1.3.3"].splitlines(), "", 0))
        # values beyond the int range: each version converts them its own way
        big = refs["big"]
        for style, version in (("gawk", None), ("mawk", "1.3.4 20200120"), ("mawk", "1.3.4 20240123"),
                               ("mawk", "1.3.4 20250131"), ("mawk", "1.3.3")):
            with self.subTest(big=version or style):
                out, err, code = awk(style, [big["program"]], version=version)
                self.assertEqual((out.splitlines(), err, code), (big[version or style].splitlines(), "", 0))
        index = refs["index"]
        for style in ("gawk", "mawk", "busybox"):
            with self.subTest(index=style):
                self.assertEqual(awk(style, [index["program"]], index["input"]), (index[style], "", 0))

    def test_a_record_separator_of_v_is_a_usage_error(self):
        """-v RS=... ends awk with status 2 as every left-out feature does, without an internal error."""
        for style in VERSIONS:
            log = FakeLog()
            with self.subTest(style=style):
                self.assertEqual(awk(style, ["-v", "RS=x", "{ print }"], "a\n", log=log), ("", "", 2))
                self.assertEqual(awk(style, ["-vSUBSEP=:", "{ print }"], "a\n", log=log), ("a\n", "", 0))
                self.assertEqual(log.errors, [])

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
