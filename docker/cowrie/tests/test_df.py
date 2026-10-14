"""df of the patched Cowrie (patch_df): the persona's file system table in the format of GNU
coreutils or of its BusyBox. The reference texts are the output of `df` in debian:12 (coreutils
9.1) and busybox:1.36 containers on the same host (their numbers are the table here)."""
import json
import posixpath
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, module_all
from tpot_personas import catalog, system
from tpot_personas.identity import draw


def row(source, target, fstype, size, used, avail, inodes=0, iused=0):
    return {"source": source, "target": target, "fstype": fstype, "options": "rw", "size": size, "used": used,
            "avail": avail, "inodes": inodes, "iused": iused}


# `df` in debian:12 (and the same mounts as busybox:1.36 sees them, less its own extra rows)
GNU_ROWS = [row("overlay", "/", "overlay", 263111748, 41148268, 208525376, 16777216, 1079697),
            row("tmpfs", "/dev", "tmpfs", 65536, 0, 65536),
            row("shm", "/dev/shm", "tmpfs", 65536, 0, 65536),
            row("virtiofs2", "/ref", "virtiofs", 971350180, 791715488, 179634692),
            row("/dev/vda1", "/etc/hosts", "ext4", 263111748, 41148268, 208525376),
            row("tmpfs", "/run/lock", "tmpfs", 5120, 0, 5120),
            row("tmpfs", "/proc/scsi", "tmpfs", 4, 0, 4)]
GNU_DF = """Filesystem     1K-blocks      Used Available Use% Mounted on
overlay        263111748  41148268 208525376  17% /
tmpfs              65536         0     65536   0% /dev
shm                65536         0     65536   0% /dev/shm
virtiofs2      971350180 791715488 179634692  82% /ref
/dev/vda1      263111748  41148268 208525376  17% /etc/hosts
tmpfs               5120         0      5120   0% /run/lock
tmpfs                  4         0         4   0% /proc/scsi
"""
GNU_DF_H = """Filesystem      Size  Used Avail Use% Mounted on
overlay         251G   40G  199G  17% /
tmpfs            64M     0   64M   0% /dev
shm              64M     0   64M   0% /dev/shm
virtiofs2       927G  756G  172G  82% /ref
/dev/vda1       251G   40G  199G  17% /etc/hosts
tmpfs           5.0M     0  5.0M   0% /run/lock
tmpfs           4.0K     0  4.0K   0% /proc/scsi
"""
GNU_DF_HT = """Filesystem     Type      Size  Used Avail Use% Mounted on
overlay        overlay   251G   40G  199G  17% /
tmpfs          tmpfs      64M     0   64M   0% /dev
shm            tmpfs      64M     0   64M   0% /dev/shm
virtiofs2      virtiofs  927G  756G  172G  82% /ref
/dev/vda1      ext4      251G   40G  199G  17% /etc/hosts
tmpfs          tmpfs     5.0M     0  5.0M   0% /run/lock
tmpfs          tmpfs     4.0K     0  4.0K   0% /proc/scsi
"""
GNU_TOTAL = GNU_DF_H + "total           1.4T  834G  570G  60% -\n"
GNU_EXCLUDE = """Filesystem     1K-blocks      Used Available Use% Mounted on
virtiofs2      971350180 791715488 179634692  82% /ref
/dev/vda1      263111748  41148268 208525376  17% /etc/hosts
"""
# the root of a second run (41148264 used)
GNU_ROOT = [row("overlay", "/", "overlay", 263111748, 41148264, 208525380, 16777216, 1079697)]
GNU_ROOT_TEXTS = {
    ("-H", "/"): "Filesystem      Size  Used Avail Use% Mounted on\noverlay         270G   43G  214G  17% /\n",
    ("-T", "/"): "Filesystem     Type    1K-blocks     Used Available Use% Mounted on\n"
                 "overlay        overlay 263111748 41148264 208525380  17% /\n",
    ("-P", "/"): "Filesystem     1024-blocks     Used Available Capacity Mounted on\n"
                 "overlay          263111748 41148264 208525380      17% /\n",
    ("-m", "/"): "Filesystem     1M-blocks  Used Available Use% Mounted on\noverlay           256946 40184    203639  17% /\n",
    ("-BM", "/"): "Filesystem     1M-blocks   Used Available Use% Mounted on\n"
                  "overlay          256946M 40184M   203639M  17% /\n",
    ("-i", "/"): "Filesystem       Inodes   IUsed    IFree IUse% Mounted on\n"
                 "overlay        16777216 1079697 15697519    7% /\n",
    ("--output=source,size,pcent,target", "/"): "Filesystem     1K-blocks Use% Mounted on\n"
                                                "overlay        263111748  17% /\n",
    ("--output", "/"): "Filesystem     Type      Inodes   IUsed    IFree IUse% 1K-blocks     Used     Avail Use% File "
                       "Mounted on\noverlay        overlay 16777216 1079697 15697519    7% 263111748 41148264 208525380  "
                       "17% /    /\n",
    ("-ih", "/"): "Filesystem     Inodes IUsed IFree IUse% Mounted on\noverlay           16M  1.1M   15M    7% /\n",
    ("-hP", "/"): "Filesystem      Size  Used Avail Use% Mounted on\noverlay         251G   40G  199G  17% /\n",
    ("-B1000", "/"): "Filesystem     1kB-blocks     Used Available Use% Mounted on\n"
                     "overlay         269426430 42135823 213529990  17% /\n",
}

# `df` in busybox:1.36
BB_ROWS = [row("overlay", "/", "overlay", 263111748, 41148284, 208525360, 16777216, 1079700),
           row("tmpfs", "/dev", "tmpfs", 65536, 0, 65536),
           row("shm", "/dev/shm", "tmpfs", 65536, 0, 65536),
           row("virtiofs2", "/ref", "virtiofs", 971350180, 791715424, 179634756),
           row("/dev/vda1", "/etc/hostname", "ext4", 263111748, 41148284, 208525360),
           row("tmpfs", "/run/lock", "tmpfs", 5120, 0, 5120),
           row("tmpfs", "/proc/scsi", "tmpfs", 4, 0, 4),
           row("proc", "/proc", "proc", 0, 0, 0)]
BB_DF = """Filesystem           1K-blocks      Used Available Use% Mounted on
overlay              263111748  41148284 208525360  16% /
tmpfs                    65536         0     65536   0% /dev
shm                      65536         0     65536   0% /dev/shm
virtiofs2            971350180 791715424 179634756  82% /ref
/dev/vda1            263111748  41148284 208525360  16% /etc/hostname
tmpfs                     5120         0      5120   0% /run/lock
tmpfs                        4         0         4   0% /proc/scsi
"""
BB_DF_H = """Filesystem                Size      Used Available Use% Mounted on
overlay                 250.9G     39.2G    198.9G  16% /
tmpfs                    64.0M         0     64.0M   0% /dev
shm                      64.0M         0     64.0M   0% /dev/shm
virtiofs2               926.4G    755.0G    171.3G  82% /ref
/dev/vda1               250.9G     39.2G    198.9G  16% /etc/hostname
tmpfs                     5.0M         0      5.0M   0% /run/lock
tmpfs                     4.0K         0      4.0K   0% /proc/scsi
"""
BB_DF_HT = """Filesystem           Type            Size      Used Available Use% Mounted on
overlay              overlay       250.9G     39.2G    198.9G  16% /
tmpfs                tmpfs          64.0M         0     64.0M   0% /dev
shm                  tmpfs          64.0M         0     64.0M   0% /dev/shm
virtiofs2            virtiofs      926.4G    755.0G    171.3G  82% /ref
/dev/vda1            ext4          250.9G     39.2G    198.9G  16% /etc/hostname
tmpfs                tmpfs           5.0M         0      5.0M   0% /run/lock
tmpfs                tmpfs           4.0K         0      4.0K   0% /proc/scsi
"""
BB_ROOT_TEXTS = {
    ("-T", "/"): "Filesystem           Type       1K-blocks      Used Available Use% Mounted on\n"
                 "overlay              overlay    263111748  41148284 208525360  16% /\n",
    ("-m", "/"): "Filesystem           1M-blocks      Used Available Use% Mounted on\n"
                 "overlay                 256945     40184    203638  16% /\n",
    ("-i", "/"): "Filesystem              Inodes      Used Available Use% Mounted on\n"
                 "overlay               16777216   1079700  15697516   6% /\n",
}
BB_USAGE = """BusyBox v1.36.1 (2023-05-18 22:34:17 UTC) multi-call binary.

Usage: df [-PkmhTai] [-B SIZE] [-t TYPE] [FILESYSTEM]...

Print filesystem usage statistics

\t-P\tPOSIX output format
\t-k\t1024-byte blocks (default)
\t-m\t1M-byte blocks
\t-h\tHuman readable (e.g. 1K 243M 2G)
\t-T\tPrint filesystem type
\t-t TYPE\tPrint only mounts of this type
\t-a\tShow all filesystems
\t-i\tInodes
\t-B SIZE\tBlocksize
"""
EXISTING = ("/", "/etc", "/etc/hosts", "/etc/hostname", "/dev", "/dev/shm", "/proc", "/proc/scsi", "/ref", "/tmp",
            "/root", "/run", "/run/lock", "/boot", "/boot/efi", "/var", "/home")


class Fs:
    def __init__(self, existing=EXISTING):
        self.existing = set(existing)

    def resolve_path(self, path, cwd):
        return posixpath.normpath(posixpath.join(cwd, path))

    def exists(self, path):
        return path in self.existing


class Base:
    def __init__(self, args, cwd="/root"):
        self.args, self.cwd, self.fs = list(args), cwd, Fs()
        self.out, self.err, self.exit_code = "", "", 0

    def write(self, text):
        self.out += text

    def errorWrite(self, text):  # noqa: N802 - Cowrie's name
        self.err += text


_ROOT = None


def patched_source():
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "cowrie"
        shutil.copytree(FIXTURE, _ROOT)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(_ROOT)], check=True, capture_output=True)
    return (_ROOT / "src/cowrie/commands/fs.py").read_text()


class DfTest(unittest.TestCase):
    def df(self, args, data, cwd="/root"):
        path = Path(tempfile.mkdtemp()) / "df.json"
        path.write_text(json.dumps(data))
        namespace = module_all(patched_source(), {"HoneyPotCommand": Base, "fs": object(),
                                                  "CowrieConfig": FakeConfig({("shell", "df_table"): str(path)})})
        cmd = namespace["Command_df"](args, cwd)
        cmd.call()
        return cmd.out, cmd.err, cmd.exit_code

    def gnu(self, args, rows=GNU_ROWS, **extra):
        return self.df(args, dict({"style": "gnu", "coreutils": "9.1", "coreutils_year": "2022",
                                   "filesystems": rows}, **extra))

    def busybox(self, args, rows=BB_ROWS, version=(1, 36, 1)):
        return self.df(args, {"style": "busybox", "busybox": list(version), "filesystems": rows,
                              "banner": "BusyBox v%d.%d.%d (2023-05-18 22:34:17 UTC)" % version})

    def test_gnu_tables_are_those_of_coreutils(self):
        self.assertEqual(self.gnu([]), (GNU_DF, "", 0))
        self.assertEqual(self.gnu(["-h"]), (GNU_DF_H, "", 0))
        self.assertEqual(self.gnu(["-hT"]), (GNU_DF_HT, "", 0))
        self.assertEqual(self.gnu(["-h", "--total"]), (GNU_TOTAL, "", 0))
        self.assertEqual(self.gnu(["-x", "tmpfs", "-x", "overlay"]), (GNU_EXCLUDE, "", 0))
        for args, text in GNU_ROOT_TEXTS.items():
            with self.subTest(args=args):
                self.assertEqual(self.gnu(list(args), GNU_ROOT), (text, "", 0))

    def test_gnu_last_unit_option_wins(self):
        self.assertEqual(self.gnu(["-k", "-h", "/"], GNU_ROOT)[0], GNU_ROOT_TEXTS[("-hP", "/")])
        self.assertTrue(self.gnu(["-h", "-k", "/"], GNU_ROOT)[0].startswith("Filesystem     1K-blocks"))

    def test_gnu_errors(self):
        self.assertEqual(self.gnu(["-t", "nosuch"]), ("", "df: no file systems processed\n", 1))
        self.assertEqual(self.gnu(["/nonexistent"]), ("", "df: /nonexistent: No such file or directory\n", 1))
        out, err, code = self.gnu(["/nonexistent", "/"], GNU_ROOT)
        self.assertEqual((err, code), ("df: /nonexistent: No such file or directory\n", 1))
        self.assertIn("overlay", out)
        try_help = "Try 'df --help' for more information.\n"
        self.assertEqual(self.gnu(["-Q"]), ("", "df: invalid option -- 'Q'\n" + try_help, 1))
        self.assertEqual(self.gnu(["--output=bogus"]),
                         ("", "df: option --output: field 'bogus' unknown\n" + try_help, 1))
        self.assertEqual(self.gnu(["-i", "--output=source"]),
                         ("", "df: options -i and --output are mutually exclusive\n" + try_help, 1))
        self.assertEqual(self.gnu(["-T", "--output=source"]),
                         ("", "df: options -T and --output are mutually exclusive\n" + try_help, 1))
        self.assertEqual(self.gnu(["--bogus"]), ("", "df: unrecognized option '--bogus'\n" + try_help, 1))
        self.assertEqual(self.gnu(["-t"]), ("", "df: option requires an argument -- 't'\n" + try_help, 1))
        self.assertEqual(self.gnu(["--direct"])[2], 1)
        self.assertEqual(self.gnu(["--direct", "/"], GNU_ROOT, el=True)[2], 0)

    def test_gnu_paths_show_their_file_system(self):
        out, _, _ = self.gnu(["/etc/hosts", "/dev/shm/x", "/run/lock"], GNU_ROWS)
        self.assertEqual([line.split()[-1] for line in out.splitlines()[1:]], ["/etc/hosts", "/run/lock"])
        # a missing file below an existing mount is still missing
        self.assertIn("/dev/shm/x: No such file or directory", self.gnu(["/dev/shm/x"])[1])
        out, _, _ = self.gnu(["shm"], GNU_ROWS)  # not a device path: a relative file name
        self.assertEqual(out, "")
        out, _, _ = self.gnu(["/dev/vda1"], GNU_ROWS)
        self.assertEqual(out.splitlines()[1].split()[-1], "/etc/hosts")
        out, _, _ = self.gnu(["--output=file,target", "../etc"], GNU_ROWS)
        self.assertEqual(out, "File   Mounted on\n../etc /\n")
        self.assertEqual(self.gnu(["-a", "--output=pcent,ipcent"], [row("proc", "/proc", "proc", 0, 0, 0)])[0],
                         "Use% IUse%\n   -     -\n")

    def test_gnu_help_and_version_follow_the_release(self):
        out = self.gnu(["--version"], coreutils="8.22", coreutils_year="2013")[0]
        self.assertTrue(out.startswith("df (GNU coreutils) 8.22\nCopyright (C) 2013 Free Software Foundation"))
        self.assertIn("<http://gnu.org/licenses/gpl.html>", out)
        self.assertIn("https://gnu.org/licenses", self.gnu(["--version"])[0])
        self.assertIn("--direct", self.gnu(["--help"], el=True, coreutils="8.32")[0])
        self.assertNotIn("--direct", self.gnu(["--help"])[0])
        self.assertIn("      --help        display this help", self.gnu(["--help"])[0])

    def test_gnu_help_of_older_releases(self):
        """Lines of `df --help` in centos:7 (coreutils 8.22) and debian:9 (8.26) that differ from 8.32."""
        old = self.gnu(["--help"], coreutils="8.22", coreutils_year="2013", el=True)[0]
        self.assertIn("      --direct          show statistics for a file instead of mount point\n"
                      "      --total           produce a grand total\n"
                      "  -h, --human-readable  print sizes in human readable format (e.g., 1K 234M 2G)\n"
                      "  -H, --si              likewise, but use powers of 1000 not 1024\n", old)
        self.assertIn("SIZE is an integer and optional unit (example: 10M is 10*1024*1024).  Units\n", old)
        self.assertTrue(old.endswith("Report df translation bugs to <http://translationproject.org/team/>\n"
                                     "For complete documentation, run: info coreutils 'df invocation'\n"))
        stretch = self.gnu(["--help"], coreutils="8.26", coreutils_year="2016")[0]
        self.assertIn("  -h, --human-readable  print sizes in powers of 1024 (e.g., 1023M)\n", stretch)
        self.assertNotIn("Binary prefixes", stretch)
        self.assertTrue(stretch.endswith("Full documentation at: <http://www.gnu.org/software/coreutils/df>\n"
                                         "or available locally via: info '(coreutils) df invocation'\n"))
        self.assertIn("Binary prefixes", self.gnu(["--help"], coreutils="8.32", coreutils_year="2020")[0])

    def test_busybox_tables_are_those_of_busybox(self):
        self.assertEqual(self.busybox([]), (BB_DF, "", 0))
        self.assertEqual(self.busybox(["-h"]), (BB_DF_H, "", 0))
        self.assertEqual(self.busybox(["-hT"]), (BB_DF_HT, "", 0))
        for args, text in BB_ROOT_TEXTS.items():
            with self.subTest(args=args):
                self.assertEqual(self.busybox(list(args)), (text, "", 0))
        self.assertTrue(self.busybox(["-P", "/"])[0].startswith(
            "Filesystem           1024-blocks    Used Available Capacity Mounted on\n"))
        self.assertEqual(self.busybox(["-t", "nosuch"]),
                         ("Filesystem           1K-blocks      Used Available Use% Mounted on\n", "", 0))
        self.assertEqual(self.busybox(["/nonexistent"]),
                         ("Filesystem           1K-blocks      Used Available Use% Mounted on\n",
                          "df: /nonexistent: can't find mount point\n", 1))
        self.assertIn("proc                         0         0         0   0% /proc", self.busybox(["-a"])[0])

    def test_busybox_options_follow_its_version(self):
        self.assertEqual(self.busybox(["-Q"]), ("", "df: invalid option -- 'Q'\n" + BB_USAGE, 1))
        self.assertEqual(self.busybox(["--output"]), ("", "df: unrecognized option '--output'\n" + BB_USAGE, 1))
        self.assertEqual(self.busybox(["-x", "tmpfs"])[1].splitlines()[0], "df: invalid option -- 'x'")
        # -T from 1.23, -t TYPE from 1.34, -H from 1.35
        old = self.busybox(["-T"], version=(1, 19, 4))
        self.assertEqual((old[1].splitlines()[0], old[2]), ("df: invalid option -- 'T'", 1))
        self.assertIn("Usage: df [-Pkmhai] [-B SIZE] [FILESYSTEM]...", old[1])
        self.assertEqual(self.busybox(["-T", "/"], version=(1, 27, 2))[2], 0)
        self.assertEqual(self.busybox(["-t", "tmpfs"], version=(1, 33, 2))[2], 1)
        self.assertEqual(self.busybox(["-H", "/"], version=(1, 34, 1))[2], 1)
        self.assertEqual(self.busybox(["-H", "/"], version=(1, 35, 0))[0], self.busybox(["-h", "/"])[0])
        self.assertIn("Usage: df [-Pkmhai] [-B SIZE] [FILESYSTEM...]\n\nPrint filesystem usage statistics\n\n"
                      "Options:", self.busybox(["-Z"], version=(1, 14, 1))[1])

    def test_busybox_option_errors_follow_the_c_library(self):
        """busybox:1.36.1-musl / -uclibc / -glibc and the libbb of 1.14.1 (no dot after the banner)."""
        def bb(args, libc, version=(1, 36, 1)):
            return self.df(args, {"style": "busybox", "busybox": list(version), "filesystems": BB_ROWS, "libc": libc,
                                  "banner": "BusyBox v%d.%d.%d (2023-05-18 22:34:17 UTC)" % version})
        self.assertEqual(bb(["-Q"], "musl")[1].splitlines()[0], "df: unrecognized option: Q")
        self.assertEqual(bb(["--output"], "musl")[1].splitlines()[0], "df: unrecognized option: output")
        self.assertEqual(bb(["-Q"], "uclibc")[1].splitlines()[0], "df: invalid option -- Q")
        self.assertEqual(bb(["--output"], "uclibc")[1].splitlines()[0], "df: unrecognized option `--output'")
        self.assertEqual(bb(["-t"], "musl")[1].splitlines()[0], "df: option requires an argument: t")
        self.assertEqual(bb(["-Q"], "glibc")[1].splitlines()[0], "df: invalid option -- 'Q'")
        old = bb(["-Q"], "uclibc", (1, 14, 1))[1]
        self.assertTrue(old.startswith("df: invalid option -- Q\nBusyBox v1.14.1 (2023-05-18 22:34:17 UTC) "
                                       "multi-call binary\n\nUsage: df "), old[:120])

    def test_gnu_texts_follow_the_locale_and_the_packager(self):
        """en_US (centos:7 with LC_ALL=en_US.UTF-8) names no translation address; trixie's coreutils
        says who packaged it (debian:13)."""
        en = self.gnu(["--help"], collate="en")[0]
        self.assertNotIn("translation bugs", en)
        self.assertIn("translation bugs", self.gnu(["--help"])[0])
        self.assertNotIn("translation bugs", self.gnu(["--help"], coreutils="8.22", coreutils_year="2013",
                                                         collate="en-2.17")[0])
        out = self.gnu(["--version"], coreutils="9.7", coreutils_year="2025", packager="Debian (9.7-3)")[0]
        self.assertTrue(out.startswith("df (GNU coreutils) 9.7\nPackaged by Debian (9.7-3)\nCopyright (C) 2025 "))

    def test_df_of_edgeos_is_its_busybox(self):
        """EdgeOS 2.0.9: /bin/df is a link to its BusyBox 1.27.2 (the image's squashfs)."""
        persona = catalog.by_id("ubiquiti-edgerouter-x")
        ident = draw(persona, 1)
        data = system.df_table(persona, ident, persona.builder(persona, ident).filesystems)
        self.assertEqual((data["style"], data["busybox"], data["libc"]), ("busybox", [1, 27, 2], "glibc"))
        for pid, keys in (("debian-trixie", {"packager": "Debian (9.7-3)", "collate": "c"}),
                          ("rhel-9", {"collate": "en"}), ("openwrt", {"libc": "musl"})):
            persona = catalog.by_id(pid)
            ident = draw(persona, 1)
            data = system.df_table(persona, ident, [])
            for key, value in keys.items():
                self.assertEqual(data.get(key), value, (pid, key))

    def test_busybox_wraps_long_device_names(self):
        rows = [row("/dev/mapper/cachedev_0", "/volume1", "btrfs", 5615615744, 1626629622, 3988986122)]
        out = self.busybox([], rows)[0].splitlines()
        self.assertEqual(out[1], "/dev/mapper/cachedev_0")
        self.assertEqual(out[2], " " * 20 + " 5615615744 1626629622 3988986122  29% /volume1")
        self.assertEqual(self.busybox(["-P"], rows)[0].splitlines()[1],
                         "/dev/mapper/cachedev_0 5615615744 1626629622 3988986122  29% /volume1")

    def test_bot_one_liners_on_every_persona(self):
        """`df -h`, `df -h /` and the root's line are those of the persona's table and style."""
        for persona in catalog.PERSONAS:
            ident = draw(persona, 4)
            content = persona.builder(persona, ident)
            data = system.df_table(persona, ident, content.filesystems)
            with self.subTest(persona=persona.id):
                out, err, code = self.df(["-h"], data)
                self.assertEqual((err, code), ("", 0))
                lines = out.splitlines()
                header = "Filesystem Size Used Avail Use% Mounted on" if data["style"] == "gnu" else \
                    "Filesystem Size Used Available Use% Mounted on"
                self.assertEqual(" ".join(lines[0].split()), header)
                shown = [r for r in content.filesystems if r["size"] and r["source"] != "rootfs"]
                self.assertEqual([line.split()[-1] for line in lines[1:] if "%" in line],
                                 [r["target"] for r in shown])
                out, err, code = self.df(["-h", "/"], data)
                self.assertEqual(code, 0)
                root = [r for r in content.filesystems if r["target"] == "/"]
                if persona.kind == "server":
                    self.assertEqual(out.splitlines()[1].split()[0], root[0]["source"])

    def test_gnu_human_sizes_round_up_with_one_decimal_below_ten(self):
        ns = module_all(patched_source(), {"HoneyPotCommand": Base, "fs": object(), "CowrieConfig": FakeConfig({})})
        human = ns["tpot_df_human"]
        cases = {(0, 1024): "0", (1023, 1024): "1023", (1024, 1024): "1.0K", (1025, 1024): "1.1K",
                 (10 * 1024 - 1, 1024): "10K", (5120 * 1024, 1024): "5.0M", (4 * 1024 ** 3, 1024): "4.0G",
                 (int(3.91 * 1024 ** 3), 1024): "4.0G", (int(3.89 * 1024 ** 3), 1024): "3.9G",
                 (1024 ** 2 - 1, 1024): "1.0M", (1000, 1000): "1.0k", (999_999, 1000): "1.0M",
                 (42135826432, 1000): "43G"}
        for (amount, base), text in cases.items():
            self.assertEqual(human(amount, base), text, (amount, base))
        bb = ns["tpot_df_bb_human"]
        self.assertEqual(bb(5888, 1024, 0), "5.8M")
        self.assertEqual(bb(512, 1024, 0), "512.0K")
        self.assertEqual(bb(1024, 0, 0), "1K")
        self.assertEqual(bb(1048576, 0, 0), "1M")
        self.assertEqual(bb(1024, 0, 1), "1024")


if __name__ == "__main__":
    unittest.main()
