"""dd of the patched Cowrie (commands/dd.py): the records it reads and writes, counted as GNU dd and
BusyBox dd count them, and their status lines per version (coreutils 8.22 / 8.26 / 9.1, BusyBox 1.14
and 1.19 and later; references of round 4: debian:12, debian:9, centos:7, busybox:1.27.2 / 1.31.1 /
1.36.1 and the BusyBox sources)."""
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, module_all

DD = "src/cowrie/commands/dd.py"
GNU_TIME = r"[0-9.e+-]+ s, [0-9.]+ [kMGT]?B/s"
BB_TIME = r"[0-9]+\.[0-9]{6} seconds, [0-9.]+[KMGT]?B/s"


class FileNotFound(Exception):  # noqa: N818 - as Cowrie's
    pass


class Fs:
    def __init__(self, files):
        self.files = files

    def resolve_path(self, path, cwd):
        import posixpath
        return posixpath.normpath(posixpath.join(cwd, path))

    def isdir(self, path):
        return path in ("/", "/tmp", "/root")

    def exists(self, path):
        return path in self.files or self.isdir(path)

    def file_contents(self, path):
        if path not in self.files:
            raise FileNotFound(path)
        return self.files[path]


class DdTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True,
                       text=True)
        cls.source = (root / DD).read_text()

    def dd(self, args, style="gnu", version="9.1", stdin=None, files=None, keys=None):
        class HoneyPotCommand:
            def __init__(self):
                self.out, self.err, self.code = bytearray(), [], None

            def write(self, text):
                self.out += text.encode()

            def writeBytes(self, data):
                self.out += data

            def errorWrite(self, text):
                self.err.append(text)

            def exit(self, code=None):
                self.code = code if code is not None else 0

        config = FakeConfig({("shell", "text_style"): style, ("shell", "text_version"): version,
                             **{("shell", key): value for key, value in (keys or {}).items()}})
        namespace = {"CowrieConfig": config, "HoneyPotCommand": HoneyPotCommand, "FileNotFound": FileNotFound}
        namespace["commands"] = {}
        module_all(self.source, namespace)
        command = namespace["Command_tpot_dd"]()
        command.args, command.cwd, command.input_data = list(args), "/root", stdin
        command.fs = Fs({"/dev/zero": b"", "/dev/null": b"", "/etc/hostname": b"web01\n", **(files or {})})
        command.protocol = types.SimpleNamespace()
        command.start()
        return bytes(command.out), "".join(command.err), command.code

    def test_gnu_9_1(self):
        # debian:12: dd if=/dev/zero of=/dev/null bs=1k count=3 / bs=512 count=5 / echo hi | dd of=/dev/null
        _, err, code = self.dd(["if=/dev/zero", "of=/dev/null", "bs=1k", "count=3"])
        self.assertRegex(err, r"\A3\+0 records in\n3\+0 records out\n3072 bytes \(3\.1 kB, 3\.0 KiB\) copied, "
                         + GNU_TIME + r"\n\Z")
        self.assertEqual(code, 0)
        _, err, _ = self.dd(["if=/dev/zero", "of=/dev/null", "bs=512", "count=5"])
        self.assertRegex(err, r"\A5\+0 records in\n5\+0 records out\n2560 bytes \(2\.6 kB, 2\.5 KiB\) copied, ")
        _, err, _ = self.dd(["of=/dev/null"], stdin=b"hi\n")
        self.assertRegex(err, r"\A0\+1 records in\n0\+1 records out\n3 bytes copied, " + GNU_TIME + r"\n\Z")

    def test_gnu_8_22_and_8_26(self):
        # centos:7: "3072 bytes (3.1 kB) copied", "3 bytes (3 B) copied"; debian:9 (8.26) has both units
        _, err, _ = self.dd(["if=/dev/zero", "of=/dev/null", "bs=1k", "count=3"], version="8.22")
        self.assertIn("\n3072 bytes (3.1 kB) copied, ", err)
        _, err, _ = self.dd(["of=/dev/null"], version="8.22", stdin=b"hi\n")
        self.assertIn("\n3 bytes (3 B) copied, ", err)
        _, err, _ = self.dd(["if=/dev/zero", "of=/dev/null", "bs=1k", "count=3"], version="8.26")
        self.assertIn("\n3072 bytes (3.1 kB, 3.0 KiB) copied, ", err)

    def test_busybox(self):
        # busybox 1.27.2 / 1.31.1 / 1.36.1: "3072 bytes (3.0KB) copied, 0.000011 seconds, 266.3MB/s"
        for version in ("1.19.4", "1.26.2", "1.27.2", "1.35.0"):
            with self.subTest(version=version):
                _, err, _ = self.dd(["if=/dev/zero", "of=/dev/null", "bs=1k", "count=3"], "busybox", version)
                self.assertRegex(err, r"\A3\+0 records in\n3\+0 records out\n3072 bytes \(3\.0KB\) copied, "
                                 + BB_TIME + r"\n\Z")
                _, err, _ = self.dd(["if=/dev/zero", "of=/dev/null", "bs=512", "count=5"], "busybox", version)
                self.assertIn("\n2560 bytes (2.5KB) copied, ", err)
                _, err, _ = self.dd(["of=/dev/null"], "busybox", version, stdin=b"hi\n")
                self.assertRegex(err, r"\A0\+1 records in\n0\+1 records out\n3 bytes \(3B\) copied, ")
        # 1.14 prints the records only
        _, err, _ = self.dd(["if=/dev/zero", "of=/dev/null", "bs=1k", "count=3"], "busybox", "1.14.1")
        self.assertEqual(err, "3+0 records in\n3+0 records out\n")

    def test_the_data(self):
        out, err, _ = self.dd(["if=/dev/zero", "bs=1", "count=4"])
        self.assertEqual(out, b"\0\0\0\0")
        self.assertTrue(err.startswith("4+0 records in\n4+0 records out\n4 bytes copied, "))
        # a file: whole records and the rest
        out, err, _ = self.dd(["if=/etc/hostname", "bs=4"])
        self.assertEqual(out, b"web01\n")
        self.assertTrue(err.startswith("1+1 records in\n1+1 records out\n6 bytes copied, "))
        out, err, _ = self.dd(["if=/etc/hostname", "bs=2", "skip=1", "count=1"])
        self.assertEqual(out, b"b0")
        self.assertTrue(err.startswith("1+0 records in\n1+0 records out\n2 bytes copied, "))
        # a large count from /dev/zero to /dev/null is counted, not made
        _, err, _ = self.dd(["if=/dev/zero", "of=/dev/null", "bs=1M", "count=100"])
        self.assertIn("100+0 records in\n100+0 records out\n104857600 bytes (105 MB, 100 MiB) copied, ", err)
        self.assertIn("\n104857600 bytes (100.0MB) copied, ", self.dd(["if=/dev/zero", "of=/dev/null", "bs=1M",
                                                                        "count=100"], "busybox", "1.36.1")[1])
        # the singular of GNU, "1 bytes" of BusyBox
        self.assertIn("\n1 byte copied, ", self.dd(["if=/dev/zero", "of=/dev/null", "bs=1", "count=1"])[1])
        self.assertIn("\n1 byte (1 B) copied, ", self.dd(["if=/dev/zero", "of=/dev/null", "bs=1", "count=1"],
                                                          version="8.22")[1])
        self.assertIn("\n1000 bytes (1.0 kB) copied, ", self.dd(["if=/dev/zero", "of=/dev/null", "bs=1000",
                                                                  "count=1"])[1])
        self.assertIn("\n1 bytes (1B) copied, ", self.dd(["if=/dev/zero", "of=/dev/null", "bs=1", "count=1"],
                                                          "busybox", "1.27.2")[1])

    def test_count_after_skip(self):
        # debian:12 and busybox:1.36.1: dd if=/dev/zero bs=1 skip=5 count=10 of=/dev/null copies 10 records
        for style, version in (("gnu", "9.1"), ("busybox", "1.36.1")):
            with self.subTest(style=style):
                _, err, _ = self.dd(["if=/dev/zero", "bs=1", "skip=5", "count=10", "of=/dev/null"], style, version)
                self.assertTrue(err.startswith("10+0 records in\n10+0 records out\n10 bytes"), err)

    def test_status_and_errors(self):
        self.assertEqual(self.dd(["if=/dev/zero", "of=/dev/null", "count=1", "status=none"])[1], "")
        _, err, _ = self.dd(["if=/dev/zero", "of=/dev/null", "count=1", "status=noxfer"])
        self.assertEqual(err, "1+0 records in\n1+0 records out\n")
        _, err, code = self.dd(["if=/nosuch"])
        self.assertEqual((err, code), ("dd: failed to open '/nosuch': No such file or directory\n", 1))
        _, err, code = self.dd(["if=/nosuch"], "busybox", "1.27.2")
        self.assertEqual((err, code), ("dd: can't open '/nosuch': No such file or directory\n", 1))
        # coreutils 9.1 in C.UTF-8; 8.22 (centos:7, en_US.UTF-8) without the colon and with curly quotes
        _, err, code = self.dd(["if=/nosuch"], version="8.22")
        self.assertEqual(err, "dd: failed to open \u2018/nosuch\u2019: No such file or directory\n")
        _, err, code = self.dd(["bs=0"])
        self.assertEqual((err, code), ("dd: invalid number: \u20180\u2019\n", 1))
        self.assertEqual(self.dd(["bs=0"], version="8.22")[1], "dd: invalid number \u20180\u2019\n")
        _, err, code = self.dd(["foo=1"])
        self.assertEqual((err, code), ("dd: unrecognized operand \u2018foo=1\u2019\n"
                                       "Try 'dd --help' for more information.\n", 1))
        _, err, code = self.dd(["bs=0"], "busybox", "1.27.2")
        self.assertTrue(err.startswith("dd: number 0 is not in 1..9223372036854775807 range\n"), err)
        self.assertEqual(code, 1)

    # dd x of the BusyBox v1.19.4 of the Archer C7 v5 (220715 image, qemu-mips-static)
    C7_USAGE = (
            'BusyBox v1.19.4 (2022-07-14 23:31:52 EDT) multi-call binary.\n'
            '\n'
            'Usage: dd [if=FILE] [of=FILE] [ibs=N] [obs=N] [bs=N] [count=N] [skip=N]\n'
            '\t[seek=N] [conv=notrunc|noerror|sync|fsync]\n'
            '\n'
            'Copy a file with converting and formatting\n'
            '\n'
            '\tif=FILE\t\tRead from FILE instead of stdin\n'
            '\tof=FILE\t\tWrite to FILE instead of stdout\n'
            '\tbs=N\t\tRead and write N bytes at a time\n'
            '\tibs=N\t\tRead N bytes at a time\n'
            '\tobs=N\t\tWrite N bytes at a time\n'
            '\tcount=N\t\tCopy only N input blocks\n'
            '\tskip=N\t\tSkip N input blocks\n'
            '\tseek=N\t\tSkip N output blocks\n'
            "\tconv=notrunc\tDon't truncate output file\n"
            '\tconv=noerror\tContinue after read errors\n'
            '\tconv=sync\tPad blocks with zeros\n'
            '\tconv=fsync\tPhysically write data out before finishing\n'
            '\n'
            'Numbers may be suffixed by c (x1), w (x2), b (x512), kD (x1000), k (x1024),\n'
            'MD (x1000000), M (x1048576), GD (x1000000000) or G (x1073741824)\n'
            '\n'
    )
    C7 = {"text_banner": "BusyBox v1.19.4 (2022-07-14 23:31:52 EDT)", "arch": "linux-mips-msb",
          "dd_status_line": "no"}

    def test_busybox_1_19_of_the_archer_c7(self):
        # its build has no FEATURE_DD_THIRD_STATUS_LINE: the records only
        _, err, code = self.dd(["if=/dev/zero", "of=/dev/null", "bs=1", "count=3"], "busybox", "1.19.4", keys=self.C7)
        self.assertEqual((err, code), ("3+0 records in\n3+0 records out\n", 0))
        # the usage of 1.19 (no iflag=, status=), its banner and the blank line of bb_show_usage before 1.21
        _, err, code = self.dd(["x"], "busybox", "1.19.4", keys=self.C7)
        self.assertEqual((err, code), (self.C7_USAGE, 1))
        # xatoul_range_sfx dies with its message only; ((size_t)-1L)/2 of a 32-bit system
        _, err, code = self.dd(["bs=0"], "busybox", "1.19.4", keys=self.C7)
        self.assertEqual((err, code), ("dd: number 0 is not in 1..2147483647 range\n", 1))

    def test_busybox_range_error_is_the_message_only(self):
        # busybox:1.27.2 and 1.36.1 (x86_64): "dd: number 0 is not in 1..9223372036854775807 range", rc 1
        for version in ("1.27.2", "1.36.1"):
            with self.subTest(version=version):
                _, err, code = self.dd(["bs=0"], "busybox", version, keys={"arch": "linux-x64-lsb"})
                self.assertEqual((err, code), ("dd: number 0 is not in 1..9223372036854775807 range\n", 1))

    def test_size_suffixes(self):
        namespace = {"CowrieConfig": FakeConfig({}), "HoneyPotCommand": object, "FileNotFound": FileNotFound}
        module_all(self.source, namespace)
        size = namespace["tpot_dd_size"]
        for text, value in (("512", 512), ("1k", 1024), ("1K", 1024), ("2b", 1024), ("1kB", 1000), ("1M", 1 << 20),
                            ("1MB", 10 ** 6), ("3w", 6), ("1G", 1 << 30), ("x", None), ("", None), ("-1", None)):
            self.assertEqual(size(text), value, text)

    def test_fails_loudly_on_unknown_upstream(self):
        old = 'commands["/bin/dd"] = Command_dd\ncommands["dd"] = Command_dd\n'
        with tempfile.TemporaryDirectory() as handle:
            root = Path(handle) / "cowrie"
            shutil.copytree(FIXTURE, root)
            path = root / DD
            text = path.read_text()
            self.assertEqual(text.count(old), 1)
            path.write_text(text.replace(old, old.replace("Command_dd\n", "Command_dd  # dd\n", 1)))
            done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], capture_output=True,
                                  text=True)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("Could not find expected text", done.stderr)


if __name__ == "__main__":
    unittest.main()
