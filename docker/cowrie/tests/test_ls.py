"""ls of the patched Cowrie (commands/ls.py): one name per line when stdout is no terminal (a pipe, a
file, an exec session), as GNU ls and BusyBox ls do; with -l a link named on the command line is the
link itself, with its target (GNU ls: no dereference of command line links in long format)."""
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, method

LS = "src/cowrie/commands/ls.py"
A_NAME, A_TYPE, A_UID, A_GID, A_SIZE, A_MODE, A_CTIME, A_CONTENTS, A_TARGET = range(9)
T_LINK, T_DIR, T_FILE = 0, 1, 2


def node(name, kind, size=0, target=None, mode=0o755, children=None):
    n = [name, kind, 0, 0, size, mode | (0o120000 if kind == T_LINK else 0), 1700000000, children or [], target]
    return n


class Fs:
    """The parts of Cowrie's HoneyPotFilesystem that ls uses: /bin -> usr/bin, /usr/bin/{dash, sh -> dash}."""

    def __init__(self):
        dash = node("dash", T_FILE, 125560)
        sh = node("sh", T_LINK, 4, "dash", mode=0o777)
        self.usr_bin = node("bin", T_DIR, 4096, children=[dash, sh])
        usr = node("usr", T_DIR, 4096, children=[self.usr_bin])
        etc = node("etc", T_DIR, 4096, children=[node(n, T_FILE, 10, mode=0o644) for n in
                                                 ("adduser.conf", "alternatives", "apt", "hostname", "hosts")])
        self.bin = node("bin", T_LINK, 7, "usr/bin", mode=0o777)
        self.root = node("/", T_DIR, 4096, children=[self.bin, etc, usr])

    def resolve_path(self, path, cwd):
        import posixpath
        return posixpath.normpath(posixpath.join(cwd, path))

    def _walk(self, path, follow):
        current = self.root
        parts = [p for p in path.split("/") if p]
        for i, part in enumerate(parts):
            if current[A_TYPE] == T_LINK:
                current = self._walk("/" + current[A_TARGET], True)
            found = [c for c in current[A_CONTENTS] if c[A_NAME] == part]
            if not found:
                return None
            current = found[0]
            last = i == len(parts) - 1
            if current[A_TYPE] == T_LINK and (follow or not last):
                import posixpath
                base = "/" + "/".join(parts[:i])
                current = self._walk(posixpath.normpath(posixpath.join(base, current[A_TARGET])), True)
                if current is None:
                    return None
        return current

    def getfile(self, path, follow_symlinks=True):
        return self._walk(path, follow_symlinks)

    def isdir(self, path):
        n = self._walk(path, True)
        return n is not None and n[A_TYPE] == T_DIR

    def get_path(self, path):
        return self._walk(path, True)[A_CONTENTS]


class LsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True,
                       text=True)
        cls.patched = (root / LS).read_text()
        cls.upstream = (FIXTURE / LS).read_text()

    def ls(self, args, source=None, terminal=True, exec_session=False, style=None, total=None):
        source = source or self.patched
        fsmod = types.SimpleNamespace(A_NAME=A_NAME, A_TYPE=A_TYPE, A_UID=A_UID, A_GID=A_GID, A_SIZE=A_SIZE,
                                      A_MODE=A_MODE, A_CTIME=A_CTIME, A_CONTENTS=A_CONTENTS, A_TARGET=A_TARGET, T_LINK=T_LINK,
                                      T_DIR=T_DIR, T_FILE=T_FILE)
        import getopt
        import posixpath
        import stat
        import time
        config = FakeConfig({**({("shell", "ls_style"): style} if style else {}),
                             **({("shell", "ls_total"): total} if total else {})})
        namespace = {"fs": fsmod, "getopt": getopt, "posixpath": posixpath, "stat": stat, "time": time,
                     "CowrieConfig": config}
        names = ("call", "get_dir_files", "do_ls_normal", "do_ls_l", "uid2name", "gid2name")
        if source is not self.upstream and "def tpot_bb_long" in source:
            names += ("tpot_bb_long",)
        out, err = [], []

        class Command:
            pass

        for name in names:
            setattr(Command, name, method(source, "Command_ls", name, namespace))
        Command.uid2name = lambda self, uid: "root"
        Command.gid2name = lambda self, gid: "root"
        command = Command()
        protocol = types.SimpleNamespace(fs=Fs(), user=types.SimpleNamespace(windowSize=(24, 80)))
        if exec_session:
            protocol.execcmd = b"ls"
        command.protocol, command.args, command.cwd = protocol, list(args), "/"
        command.pp = types.SimpleNamespace(targets={1: ("terminal" if terminal else "pipe",)})
        command.write, command.errorWrite = out.append, err.append
        command.call()
        return "".join(out), "".join(err)

    def test_into_a_pipe_one_name_per_line(self):
        # debian:12 `ls /etc | head -3`: adduser.conf / alternatives / apt; BusyBox the same
        self.assertEqual(self.ls(["/etc"], terminal=False),
                         ("adduser.conf\nalternatives\napt\nhostname\nhosts\n", ""))
        # an exec session (ssh host ls) has no terminal either
        self.assertEqual(self.ls(["/etc"], exec_session=True)[0].count("\n"), 5)

    def test_on_the_terminal_in_columns_as_upstream(self):
        self.assertEqual(self.ls(["/etc"]), self.ls(["/etc"], source=self.upstream))
        self.assertEqual(self.ls(["/etc"])[0].count("\n"), 1)

    def test_long_format_shows_a_link_with_its_target(self):
        # debian:12 `ls -l /bin/sh`: lrwxrwxrwx 1 root root 4 ... /bin/sh -> dash
        out, _ = self.ls(["-l", "/bin/sh"])
        self.assertTrue(out.startswith("lrwxrwxrwx 1 root root 4 "), out)
        self.assertTrue(out.endswith(" /bin/sh -> dash\n"), out)
        # a link to a directory named on the command line is listed itself: /bin -> usr/bin
        out, _ = self.ls(["-l", "/bin"])
        self.assertTrue(out.endswith(" /bin -> usr/bin\n"), out)
        self.assertEqual(out.count("\n"), 1)
        # without -l the directory behind the link is listed, as upstream
        self.assertEqual(self.ls(["/bin"], terminal=False), ("dash\nsh\n", ""))
        # the entries of a directory keep their arrows
        out, _ = self.ls(["-l", "/usr/bin"])
        self.assertIn(" sh -> dash\n", out)

    def test_busybox_long_format(self):
        # BusyBox v1.19.4 of the Archer C7 v5 (qemu-mips-static): "%-10s %4u %-8.8s %-8.8s %9s", the date
        #   "Jul 15  2022" (older than half a year or in the future) or "Oct 15 10:11", no total line
        out, _ = self.ls(["-l", "/bin/sh"], style="busybox")
        self.assertEqual(out, "lrwxrwxrwx    1 root     root             4 Nov 14  2023 /bin/sh -> dash\n")
        out, _ = self.ls(["-l", "/usr/bin"], style="busybox")
        self.assertEqual(out, "-rwxr-xr-x    1 root     root        125560 Nov 14  2023 dash\n"
                              "lrwxrwxrwx    1 root     root             4 Nov 14  2023 sh -> dash\n")
        import time
        long = method(self.patched, "Command_ls", "tpot_bb_long", {"time": time, "fs": types.SimpleNamespace(
            A_TYPE=A_TYPE, A_CTIME=A_CTIME, A_NAME=A_NAME, A_CONTENTS=A_CONTENTS, A_UID=A_UID, A_GID=A_GID,
            T_DIR=T_DIR)})
        command = types.SimpleNamespace(uid2name=lambda uid: "averyverylongname", gid2name=lambda gid: "root")
        now = time.time()
        recent = node("u", T_FILE, 0, mode=0o644)
        recent[A_CTIME] = now - 3600
        line = long(command, "-rw-r--r--", recent, "0", "")
        stamp = time.strftime("%b %e %H:%M", time.localtime(now - 3600))
        self.assertEqual(line, f"-rw-r--r--    1 averyver root             0 {stamp} u")
        folder = node("www", T_DIR, 544, children=[node("a", T_DIR), node("b", T_DIR), node("c", T_FILE)])
        self.assertTrue(long(command, "drwxrwxr-x", folder, "544", "").startswith("drwxrwxr-x    4 averyver root"))

    def test_busybox_total_line_of_a_build_with_desktop(self):
        # the total of a listed folder: BusyBox from 1.16 with CONFIG_DESKTOP (coreutils/ls.c 1.16.0 to 1.36.1;
        #   busybox:1.26.2 / 1.27.2 `ls -l` of a folder, a 2 byte file and a link: "total 8", with -a "total 16"),
        #   (1 + the 512 byte blocks) / 2; none for a file or a link named on the command line
        out, _ = self.ls(["-l", "/usr/bin"], style="busybox", total="yes")
        self.assertTrue(out.startswith("total 124\n-rwxr-xr-x    1 root     root        125560 "), out)
        out, _ = self.ls(["-la", "/usr/bin"], style="busybox", total="yes")
        self.assertTrue(out.startswith("total 132\n"), out)
        for args in (["-l", "/bin/sh"], ["-l", "/bin"], ["-ld", "/usr/bin"]):
            self.assertNotIn("total", self.ls(args, style="busybox", total="yes")[0], args)
        # a build without CONFIG_DESKTOP (the Archer C7 v5, the NAS326, OpenWrt) and GNU as upstream: none
        self.assertNotIn("total", self.ls(["-l", "/usr/bin"], style="busybox")[0])
        self.assertNotIn("total", self.ls(["-l", "/usr/bin"], total="yes")[0])

    def test_the_busybox_personas_take_its_long_format(self):
        import configparser

        from tpot_personas import catalog, render, system
        from tpot_personas.identity import draw
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render.render_cfg(persona, draw(persona, 0), Path("/tmp/r"), Path("/tmp/k")))
                self.assertEqual(cfg.get("shell", "ls_style", fallback="gnu"),
                                 "busybox" if persona.family in system.ASH_FAMILIES else "gnu")
                # QNAP's busybox-1.24.1 is built with CONFIG_DESKTOP (its .config), the other BusyBox builds are not
                #   (1.14.1 of the DIR-859 has no total line)
                self.assertEqual(cfg.get("shell", "ls_total", fallback="no"), "yes" if persona.family == "qnap" else "no")
                self.assertIs(system.busybox_desktop(persona), (persona.family == "qnap") if persona.family in system.ASH_FAMILIES else None)

    def test_upstream_followed_the_link(self):
        out, _ = self.ls(["-l", "/bin/sh"], source=self.upstream)
        self.assertNotIn("->", out)

    def test_fails_loudly_on_unknown_upstream(self):
        cases = ("        line = [x[fs.A_NAME] for x in files]\n        if not line:\n            return\n",
                 "                time.strftime(\"%Y-%m-%d %H:%M\", ctime),\n",
                 "    def do_ls_l(self, path: str, name: str) -> None:\n",
                 "        if not files:\n            return\n        # Create a list to hold formatted sizes for display\n",
                 "                file = self.protocol.fs.getfile(path)[:]\n",
                 "            if self.protocol.fs.isdir(path) and not self.showDirectories:\n",
                 "        for arg in args:\n            resolved = self.protocol.fs.resolve_path(arg, self.cwd)\n")
        for old in cases:
            with self.subTest(needle=old):
                with tempfile.TemporaryDirectory() as handle:
                    root = Path(handle) / "cowrie"
                    shutil.copytree(FIXTURE, root)
                    path = root / LS
                    text = path.read_text()
                    self.assertEqual(text.count(old), 1)
                    path.write_text(text.replace(old, old.replace("    ", "  ", 1)))
                    done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                          capture_output=True, text=True)
                    self.assertNotEqual(done.returncode, 0)
                    self.assertIn("Could not find expected text", done.stderr)


if __name__ == "__main__":
    unittest.main()
