"""mkdir of the patched Cowrie (MKDIR_COMMAND of patch_text_tools): GNU coreutils on servers, BusyBox on
devices, with -p, -m MODE and -v. Upstream's mkdir takes every option for a folder name, so from v3.1.3 on
(which gives its errors status 1) `mkdir -p DIR && ...` stopped at an existing folder. The texts are those
of mkdir in containers of debian:12 (coreutils 9.1, C.UTF-8), rockylinux:9 (8.32), centos:7 (8.22,
en_US.UTF-8) and busybox:1.36.1 (musl, uClibc), the usage of older BusyBox from its sources (1.14.1,
1.19.4, 1.24.1, 1.27.2, 1.33.2)."""
import posixpath
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import DIST
from tests.test_patch import FIXTURE, PATCH, FakeConfig, module_all
from tests.test_text_tools import Base, Log, ShellFs, patched_root

BANNER = "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)"
BB_USAGE = (f"{BANNER} multi-call binary.\n\nUsage: mkdir [-m MODE] [-p] DIRECTORY...\n\nCreate DIRECTORY\n\n"
            "\t-m MODE\tMode\n\t-p\tNo error if exists; make parent directories as needed\n")
# the texts are those of the variants of fs_texts.json (tools/build_fs_texts.py from the recordings)
FS_TEXTS = str(DIST / "tpot_personas" / "fs_texts.json")
GNU_9_1 = {"text_style": "gnu", "text_version": "9.1", "text_year": "2022", "text_collate": "c",
           "fs_variant": "debian_12"}
GNU_8_32 = {"text_style": "gnu", "text_version": "8.32", "text_year": "2020", "text_collate": "c",
            "fs_variant": "ubuntu_22.04"}
GNU_8_22 = {"text_style": "gnu", "text_version": "8.22", "text_year": "2013", "text_collate": "en-2.17",
            "fs_variant": "centos_7"}
BB_MUSL = {"text_style": "busybox", "text_banner": BANNER, "text_version": "1.36.1", "text_libc": "musl",
           "fs_variant": "busybox_1.36.1-musl"}
BB_UCLIBC = dict(BB_MUSL, text_libc="uclibc", fs_variant="busybox_1.36.1-uclibc")
TRY = "Try 'mkdir --help' for more information.\n"
HELP_HEAD = ("Usage: mkdir [OPTION]... DIRECTORY...\nCreate the DIRECTORY(ies), if they do not already exist.\n\n"
             "Mandatory arguments to long options are mandatory for short options too.\n"
             "  -m, --mode=MODE   set file mode (as in chmod), not a=rwx - umask\n")
HELP_REST = ("  -v, --verbose     print a message for each created directory\n"
             "  -Z                   set SELinux security context of each created directory\n"
             "                         to the default type\n"
             "      --context[=CTX]  like -Z, or if CTX is specified then set the SELinux\n"
             "                         or SMACK security context to CTX\n")


class Fs:
    """Cowrie's file system as far as mkdir uses it: nodes [name, type, uid, gid, size, mode, ...]."""

    def __init__(self):
        self.nodes = {"/": ["", 1, 0, 0, 4096, 0o40755], "/tmp": ["tmp", 1, 0, 0, 4096, 0o41777],
                      "/root": ["root", 1, 0, 0, 4096, 0o40700], "/tmp/file": ["file", 2, 0, 0, 0, 0o100644]}

    def resolve_path(self, path, cwd):
        return posixpath.normpath(posixpath.join(cwd, path)).replace("//", "/") or "/"

    def getfile(self, path, follow_symlinks=True):
        return self.nodes.get(path, False)

    def exists(self, path):
        return path in self.nodes

    def isdir(self, path):
        return path in self.nodes and self.nodes[path][1] == 1

    def mkdir(self, path, uid, gid, size, mode, ctime=None):
        if posixpath.dirname(path) not in self.nodes:
            raise OSError(2, "No such file or directory", path)
        self.nodes[path] = [posixpath.basename(path), 1, uid, gid, size, mode]


class MkdirTest(unittest.TestCase):
    def run_mkdir(self, keys, *args, uid=0, fs=None):
        config = FakeConfig({("shell", key): value for key, value in dict(keys, fs_texts=FS_TEXTS).items()})
        namespace = module_all((patched_root() / "src/cowrie/commands/wc.py").read_text(),
                               {"HoneyPotCommand": Base, "CowrieConfig": config, "log": Log, "tpot_shell_fs": ShellFs})
        self.fs = fs or Fs()
        cmd = namespace["commands"]["mkdir"](list(args), None, self.fs)
        cmd.user = {"uid": uid, "gid": uid}
        cmd.start()
        self.assertTrue(cmd.exited, f"mkdir {args} did not exit")
        return cmd.out.decode(), cmd.err, cmd.exit_code

    def mode(self, path):
        return self.fs.nodes[path][5]

    def test_the_commands_of_every_spelling_are_the_persona_s(self):
        namespace = module_all((patched_root() / "src/cowrie/commands/wc.py").read_text(),
                               {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig({}), "log": Log,
                                "tpot_shell_fs": ShellFs})
        for spelling in ("mkdir", "/bin/mkdir", "/usr/bin/mkdir"):
            self.assertEqual(namespace["commands"][spelling].__name__, "Command_tpot_mkdir")

    def test_p_is_no_error_for_an_existing_folder(self):
        # the step bots chain before a download or an authorized_keys line
        for keys in (GNU_9_1, BB_MUSL):
            with self.subTest(keys["text_style"]):
                self.assertEqual(self.run_mkdir(keys, "-p", "/tmp"), ("", "", 0))
                self.assertEqual(self.run_mkdir(keys, "-p", "/tmp/a/b/c"), ("", "", 0))
                self.assertTrue(self.fs.isdir("/tmp/a/b/c"))
                self.assertNotIn("/tmp/-p", self.fs.nodes)

    def test_gnu_errors(self):
        self.assertEqual(self.run_mkdir(GNU_9_1, "/tmp"), ("", "mkdir: cannot create directory ‘/tmp’: File exists\n", 1))
        self.assertEqual(self.run_mkdir(GNU_9_1, "x/y"),
                         ("", "mkdir: cannot create directory ‘x/y’: No such file or directory\n", 1))
        self.assertEqual(self.run_mkdir(GNU_9_1, "/tmp/file"),
                         ("", "mkdir: cannot create directory ‘/tmp/file’: File exists\n", 1))
        self.assertEqual(self.run_mkdir(GNU_9_1, "-p", "/tmp/file"),
                         ("", "mkdir: cannot create directory ‘/tmp/file’: File exists\n", 1))
        self.assertEqual(self.run_mkdir(GNU_9_1, "-p", "/tmp/file/sub"),
                         ("", "mkdir: cannot create directory ‘/tmp/file’: Not a directory\n", 1))
        self.assertEqual(self.run_mkdir(GNU_9_1, "/tmp/file/sub"),
                         ("", "mkdir: cannot create directory ‘/tmp/file/sub’: Not a directory\n", 1))
        self.assertEqual(self.run_mkdir(GNU_9_1, "-q", "g"), ("", "mkdir: invalid option -- 'q'\n" + TRY, 1))
        self.assertEqual(self.run_mkdir(GNU_9_1), ("", "mkdir: missing operand\n" + TRY, 1))
        self.assertEqual(self.run_mkdir(GNU_9_1, "-m"), ("", "mkdir: option requires an argument -- 'm'\n" + TRY, 1))
        self.assertEqual(self.run_mkdir(GNU_9_1, "-m", "zz", "a"), ("", "mkdir: invalid mode ‘zz’\n", 1))
        self.assertNotIn("/tmp/a", self.fs.nodes)

    def test_gnu_carries_on_with_the_other_folders(self):
        self.assertEqual(self.run_mkdir(GNU_9_1, "/tmp", "/tmp/n"),
                         ("", "mkdir: cannot create directory ‘/tmp’: File exists\n", 1))
        self.assertTrue(self.fs.isdir("/tmp/n"))

    def test_a_user_without_write_access(self):
        self.assertEqual(self.run_mkdir(GNU_9_1, "/root/x", uid=1000),
                         ("", "mkdir: cannot create directory ‘/root/x’: Permission denied\n", 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "-p", "/root/x/y", uid=1000),
                         ("", "mkdir: can't create directory '/root/x/': Permission denied\n", 1))
        self.assertNotIn("/root/x", self.fs.nodes)

    def test_gnu_verbose_quotes_by_version_and_locale(self):
        out = "mkdir: created directory 'd'\nmkdir: created directory 'd/e'\n"
        self.assertEqual(self.run_mkdir(GNU_9_1, "-pv", "/tmp/d/e")[0],
                         out.replace("'d", "'/tmp/d"))
        self.assertEqual(self.run_mkdir(GNU_8_32, "-v", "/tmp/f"), ("mkdir: created directory '/tmp/f'\n", "", 0))
        self.assertEqual(self.run_mkdir(GNU_8_22, "-pv", "/tmp/d/e"),
                         ("mkdir: created directory ‘/tmp/d’\nmkdir: created directory ‘/tmp/d/e’\n", "", 0))
        self.assertEqual(self.run_mkdir(GNU_8_22, "/tmp")[1], "mkdir: cannot create directory ‘/tmp’: File exists\n")

    def test_modes(self):
        for args, path, mode in ((("-m", "700", "/tmp/h"), "/tmp/h", 0o40700),
                                 (("-m", "u+x,go-rwx", "/tmp/b"), "/tmp/b", 0o40700),
                                 (("-m", "1777", "/tmp/c"), "/tmp/c", 0o41777),
                                 (("--mode=700", "/tmp/i"), "/tmp/i", 0o40700),
                                 (("/tmp/plain",), "/tmp/plain", 0o40755),
                                 (("-m", "a=rx", "/tmp/r"), "/tmp/r", 0o40555),
                                 (("-m", "+t", "/tmp/t"), "/tmp/t", 0o41777)):
            for keys in (GNU_9_1, BB_MUSL):
                with self.subTest(args=args, style=keys["text_style"]):
                    self.assertEqual(self.run_mkdir(keys, *args)[2], 0)
                    self.assertEqual(oct(self.mode(path)), oct(mode))
        self.run_mkdir(GNU_9_1, "-pm", "700", "/tmp/g/h")
        self.assertEqual((self.mode("/tmp/g"), self.mode("/tmp/g/h")), (0o40755, 0o40700))

    def test_gnu_long_options_and_z(self):
        self.assertEqual(self.run_mkdir(GNU_9_1, "--pa", "/tmp/j/k"), ("", "", 0))
        self.assertEqual(self.run_mkdir(GNU_9_1, "-Z", "/tmp/k"), ("", "", 0))
        self.assertEqual(self.run_mkdir(GNU_9_1, "--context", "/tmp/l"), ("", "", 0))
        self.assertTrue(self.fs.isdir("/tmp/l"))

    def test_gnu_help_and_version(self):
        footer_9 = ("\nGNU coreutils online help: <https://www.gnu.org/software/coreutils/>\n"
                    "Report any translation bugs to <https://translationproject.org/team/>\n"
                    "Full documentation <https://www.gnu.org/software/coreutils/mkdir>\n"
                    "or available locally via: info '(coreutils) mkdir invocation'\n")
        self.assertEqual(self.run_mkdir(GNU_9_1, "--help"), (
            HELP_HEAD + "  -p, --parents     no error if existing, make parent directories as needed,\n"
            "                    with their file modes unaffected by any -m option.\n" + HELP_REST
            + "      --help        display this help and exit\n"
              "      --version     output version information and exit\n" + footer_9, "", 0))
        self.assertEqual(self.run_mkdir(GNU_8_32, "--help"), (
            HELP_HEAD + "  -p, --parents     no error if existing, make parent directories as needed\n" + HELP_REST
            + "      --help     display this help and exit\n      --version  output version information and exit\n"
            + footer_9, "", 0))
        # en_US.UTF-8 of CentOS 7: no translation line
        self.assertTrue(self.run_mkdir(GNU_8_22, "--help")[0].endswith(
            "      --version  output version information and exit\n\n"
            "GNU coreutils online help: <http://www.gnu.org/software/coreutils/>\n"
            "For complete documentation, run: info coreutils 'mkdir invocation'\n"))
        out = self.run_mkdir(GNU_8_32, "--version")[0]
        self.assertTrue(out.startswith("mkdir (GNU coreutils) 8.32\nCopyright (C) 2020 Free Software Foundation, Inc.\n"
                                       "License GPLv3+: GNU GPL version 3 or later <https://gnu.org/licenses/gpl.html>.\n"))
        self.assertTrue(out.endswith("Written by David MacKenzie.\n"))

    def test_busybox(self):
        self.assertEqual(self.run_mkdir(BB_MUSL, "/tmp"), ("", "mkdir: can't create directory '/tmp': File exists\n", 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "-pv", "/tmp/d/e"),
                         ("created directory: '/tmp/d/'\ncreated directory: '/tmp/d/e'\n", "", 0))
        self.assertEqual(self.run_mkdir(BB_MUSL, "-v", "/tmp/f"), ("created directory: '/tmp/f'\n", "", 0))
        self.assertEqual(self.run_mkdir(BB_MUSL, "x/y"),
                         ("", "mkdir: can't create directory 'x/y': No such file or directory\n", 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "-p", "/tmp/file"),
                         ("", "mkdir: can't create directory '/tmp/file': File exists\n", 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "-p", "/tmp/file/sub"),
                         ("", "mkdir: can't create directory '/tmp/file/': Not a directory\n", 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "/tmp/file/sub"),
                         ("", "mkdir: can't create directory '/tmp/file/sub': Not a directory\n", 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "-q", "g"), ("", "mkdir: unrecognized option: q\n" + BB_USAGE, 1))
        self.assertEqual(self.run_mkdir(BB_UCLIBC, "-q", "g"), ("", "mkdir: invalid option -- q\n" + BB_USAGE, 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "-Z", "k"), ("", "mkdir: unrecognized option: Z\n" + BB_USAGE, 1))
        self.assertEqual(self.run_mkdir(BB_MUSL), ("", BB_USAGE, 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "-m"), ("", "mkdir: option requires an argument: m\n" + BB_USAGE, 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "-m", "zz", "a"), ("", "mkdir: invalid mode 'zz'\n", 1))
        self.assertEqual(self.run_mkdir(BB_MUSL, "--pa", "/tmp/j/k"), ("", "", 0))
        self.assertEqual(self.run_mkdir(BB_MUSL, "--help"), ("", BB_USAGE, 0))

    def test_older_busybox(self):
        old = {"text_style": "busybox", "text_banner": "BusyBox v1.19.4 (2022-07-14 23:31:52 EDT)",
               "text_version": "1.19.4", "text_libc": "uclibc", "fs_variant": "fw_tplink_c7v5"}
        usage = ("BusyBox v1.19.4 (2022-07-14 23:31:52 EDT) multi-call binary.\n\nUsage: mkdir [OPTIONS] DIRECTORY...\n\n"
                 "Create DIRECTORY\n\n\t-m MODE\tMode\n\t-p\tNo error if exists; make parent directories as needed\n\n")
        # -v from 1.24 on (getopt32 "m:p" before)
        self.assertEqual(self.run_mkdir(old, "-v", "/tmp/v"), ("", "mkdir: invalid option -- v\n" + usage, 1))
        self.assertEqual(self.run_mkdir(old, "/tmp")[1], "mkdir: can't create directory '/tmp': File exists\n")
        oldest = {"text_style": "busybox", "text_banner": "BusyBox v1.14.1 (2016-06-28 10:53:08 CST)",
                  "text_version": "1.14.1", "text_libc": "uclibc", "fs_variant": "fw_dlink_dir859_105b03"}
        self.assertEqual(self.run_mkdir(oldest, "/tmp")[1], "mkdir: cannot create directory '/tmp': File exists\n")
        self.assertEqual(self.run_mkdir(oldest)[1], (
            "BusyBox v1.14.1 (2016-06-28 10:53:08 CST) multi-call binary\n\nUsage: mkdir [OPTION] DIRECTORY...\n\n"
            "Create DIRECTORY\n\nOptions:\n\t-m\tSet permission mode (as in chmod), not rwxrwxrwx - umask\n"
            "\t-p\tNo error if existing, make parent directories as needed\n\n"))
        newer = dict(old, text_banner="BusyBox v1.27.2 (2021-05-11 12:39:02 UTC)", text_version="1.27.2",
                     fs_variant="busybox_1.27.2")
        self.assertEqual(self.run_mkdir(newer, "-v", "/tmp/v"), ("created directory: '/tmp/v'\n", "", 0))
        self.assertIn("Usage: mkdir [OPTIONS] DIRECTORY...\n", self.run_mkdir(newer)[1])

    def test_dot_and_root_on_busybox(self):
        # make_directory() of BusyBox returns at once for "/" and "."
        self.assertEqual(self.run_mkdir(BB_MUSL, "/"), ("", "", 0))
        self.assertEqual(self.run_mkdir(BB_MUSL, "."), ("", "", 0))
        self.assertEqual(self.run_mkdir(GNU_9_1, "/"), ("", "mkdir: cannot create directory ‘/’: File exists\n", 1))


class OrderTest(unittest.TestCase):
    def test_fails_loudly_when_wc_no_longer_loads_after_fs(self):
        root = Path(tempfile.mkdtemp()) / "cowrie"
        self.addCleanup(shutil.rmtree, root.parent, True)
        shutil.copytree(FIXTURE, root)
        init = root / "src/cowrie/commands/__init__.py"
        init.write_text(init.read_text().replace('    "wc",\n', ""))
        done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("no longer loads fs before wc", done.stderr)


if __name__ == "__main__":
    unittest.main()
