"""PATH and SHELL of a login (shell/session.py for SSH, shell/telnet.py for Telnet): upstream's
Debian PATH and /bin/bash, or [shell] login_path / login_shell of the persona (the Archer C7:
the PATH of its /etc/profile and its /bin/ash)."""
import configparser
import copy
import json
import posixpath
import shutil
import stat
import subprocess
import sys
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, method, tmpdir
from tpot_personas import catalog, render
from tpot_personas.identity import draw

C7 = {("shell", "login_path"): "/bin:/sbin:/usr/bin:/usr/sbin", ("shell", "login_shell"): "/bin/ash"}
DEBIAN_ROOT = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class LoginEnvTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        cls.root = root
        cls.ssh = (root / "src/cowrie/shell/session.py").read_text()
        cls.telnet = (root / "src/cowrie/shell/telnet.py").read_text()

    def ssh_environ(self, values, uid=0):
        init = method(self.ssh, "SSHSessionForCowrieUser", "__init__", {"CowrieConfig": FakeConfig(values),
                                                                         "stat": stat})
        server = types.SimpleNamespace(initFileSystem=lambda home: None)
        avatar = types.SimpleNamespace(home="/root", server=server, uid=uid, gid=uid, username="root",
                                       temporary=False)
        session = types.SimpleNamespace()
        init(session, avatar)
        return session.environ

    def telnet_environ(self, values, uid=0):
        entry = {"pw_uid": uid, "pw_gid": uid, "pw_dir": "/root"}
        pwd = types.SimpleNamespace(Passwd=lambda: types.SimpleNamespace(getpwnam=lambda name: entry))
        init = method(self.telnet, "HoneyPotTelnetSession", "__init__", {"CowrieConfig": FakeConfig(values),
                                                                          "pwd": pwd})
        session = types.SimpleNamespace()
        init(session, b"root", types.SimpleNamespace(initFileSystem=lambda home: None))
        return session.environ

    def test_the_persona_path_and_shell(self):
        for environ in (self.ssh_environ(C7), self.telnet_environ(C7), self.ssh_environ(C7, uid=1000),
                        self.telnet_environ(C7, uid=1000)):
            self.assertEqual((environ["PATH"], environ["SHELL"]), ("/bin:/sbin:/usr/bin:/usr/sbin", "/bin/ash"))

    def test_without_the_keys_upstream(self):
        for environ in (self.ssh_environ({}), self.telnet_environ({})):
            self.assertEqual((environ["PATH"], environ["SHELL"]), (DEBIAN_ROOT, "/bin/bash"))
        self.assertEqual(self.ssh_environ({}, uid=1000)["PATH"], "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games")

    def test_bash_has_histsize_and_histcontrol(self):
        # an interactive bash has HISTSIZE (and HISTCONTROL where the profile sets it) as shell variables:
        #   ubuntu:24.04 root 1000 / ignoreboth; BusyBox ash and an exec session have none
        keys = {("shell", "history_size_root"): "1000", ("shell", "history_size"): "1000",
                ("shell", "history_control_root"): "ignoreboth", ("shell", "history_control"): ""}
        environ = self.telnet_environ(keys)
        self.assertEqual((environ["HISTSIZE"], environ["HISTCONTROL"]), ("1000", "ignoreboth"))
        environ = self.telnet_environ(keys, uid=1000)
        self.assertEqual(environ["HISTSIZE"], "1000")
        self.assertNotIn("HISTCONTROL", environ)
        self.assertNotIn("HISTSIZE", self.telnet_environ({**keys, ("shell", "not_found_style"): "ash"}))
        # BusyBox msh neither: its set lists PS2, PS1, IFS, PATH, HOME and SHELL only (tests/fixtures/msh.json)
        msh = self.telnet_environ({**keys, ("shell", "not_found_style"): "msh"})
        self.assertFalse({"HISTSIZE", "HISTFILESIZE", "HISTCONTROL"} & set(msh))
        self.assertNotIn("HISTSIZE", self.telnet_environ({}))
        # SSH: in openShell (the interactive shell), not in execCommand
        values = FakeConfig(keys)
        init = method(self.ssh, "SSHSessionForCowrieUser", "__init__", {"CowrieConfig": values, "stat": stat})
        open_shell = method(self.ssh, "SSHSessionForCowrieUser", "openShell", {
            "CowrieConfig": values, "insults": types.SimpleNamespace(LoggingServerProtocol=lambda *a: types.SimpleNamespace(
                makeConnection=lambda p: None)), "protocol": types.SimpleNamespace(HoneyPotInteractiveProtocol=None),
            "ProtocolTransport": lambda p: None})
        server = types.SimpleNamespace(initFileSystem=lambda home: None)
        avatar = types.SimpleNamespace(home="/root", server=server, uid=0, gid=0, username="root", temporary=False)
        session = types.SimpleNamespace()
        init(session, avatar)
        self.assertNotIn("HISTSIZE", session.environ)
        open_shell(session, types.SimpleNamespace(makeConnection=lambda t: None))
        self.assertEqual((session.environ["HISTSIZE"], session.environ["HISTCONTROL"]), ("1000", "ignoreboth"))

    def test_bash_has_histfilesize(self):
        keys = {("shell", "history_size_root"): "500", ("shell", "history_file_size_root"): "500",
                ("shell", "history_size"): "1000", ("shell", "history_file_size"): "2000"}
        self.assertEqual(self.telnet_environ(keys)["HISTFILESIZE"], "500")
        self.assertEqual(self.telnet_environ(keys, uid=1000)["HISTFILESIZE"], "2000")
        self.assertNotIn("HISTFILESIZE", self.telnet_environ({**keys, ("shell", "not_found_style"): "ash"}))
        self.assertNotIn("HISTFILESIZE", self.telnet_environ({**keys, ("shell", "not_found_style"): "msh"}))

    def test_edgeos_has_its_vyatta_cfg_history_variables(self):
        # vyatta-cfg of the ER-e50 image: HISTCONTROL= (empty), HISTFILESIZE=10000, HISTSIZE=10000,
        #   HISTTIMEFORMAT='%FT%T%z ', all exported
        keys = {("shell", "history_size"): "10000", ("shell", "history_file_size"): "10000",
                ("shell", "history_control"): "", ("shell", "history_time_format"): '"%FT%T%z "',
                ("shell", "history_export"): "HISTSIZE,HISTFILESIZE,HISTCONTROL,HISTTIMEFORMAT"}
        environ = self.telnet_environ(keys, uid=1000)
        self.assertEqual({k: v for k, v in environ.items() if k.startswith("HIST")},
                         {"HISTSIZE": "10000", "HISTFILESIZE": "10000", "HISTCONTROL": "", "HISTTIMEFORMAT": "%FT%T%z ",
                          "HISTFILE": "/root/.bash_history"})
        _, exported = self.shell_exports({("shell", "history_export"): keys[("shell", "history_export")]}, environ)
        self.assertTrue({"HISTSIZE", "HISTFILESIZE", "HISTCONTROL", "HISTTIMEFORMAT"} <= exported)
        # an empty HISTCONTROL that is not exported stays unset (Debian's root)
        self.assertNotIn("HISTCONTROL", self.telnet_environ({("shell", "history_control"): ""}, uid=1000))

    def shell_exports(self, values, environ):
        """environ and exported of the first shell of a session (the patched HoneyPotShell.__init__)."""
        source = (self.root / "src/cowrie/shell/honeypot.py").read_text()

        class Shell:
            pass

        init = method(source, "HoneyPotShell", "__init__", {"CowrieConfig": FakeConfig(values), "copy": copy,
                                                            "HoneyPotShell": Shell})
        shell = Shell()
        init(shell, types.SimpleNamespace(cmdstack=[], environ=environ))
        return shell.environ, shell.exported

    def test_the_history_variables_are_exported_where_the_profile_exports_them(self):
        # Debian and Ubuntu set them in .bashrc (env and export -p list none); Red Hat's /etc/profile exports
        #   HISTSIZE and HISTCONTROL, HISTFILESIZE stays bash's own
        environ = {"PATH": "/usr/bin", "HISTSIZE": "1000", "HISTFILESIZE": "2000", "HISTCONTROL": "ignoreboth"}
        values, exported = self.shell_exports({}, environ)
        self.assertEqual((values, exported), (environ, {"PATH"}))
        _, exported = self.shell_exports({("shell", "history_export"): "HISTSIZE,HISTCONTROL"}, environ)
        self.assertEqual(exported, {"PATH", "HISTSIZE", "HISTCONTROL"})

    def test_the_keys_of_the_personas(self):
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render.render_cfg(persona, draw(persona, 0), Path("/tmp/r"), Path("/tmp/k")))
                # Red Hat's /etc/profile; vyatta-cfg of EdgeOS (the ER-e50 image)
                exports = {"rhel": "HISTSIZE,HISTCONTROL", "centos": "HISTSIZE,HISTCONTROL",
                           "ubiquiti": "HISTSIZE,HISTFILESIZE,HISTCONTROL,HISTTIMEFORMAT"}
                self.assertEqual(cfg.get("shell", "history_export"), exports.get(persona.family, ""))
                # bash 4.2 of CentOS 7 formats a line without a time (recorded), 4.4.12 and 5.x write "??"
                self.assertEqual(cfg.get("shell", "history_untimed", fallback=""),
                                 "format" if persona.family == "centos" else "")
                if persona.family == "ubuntu":
                    self.assertEqual((cfg.get("shell", "history_file_size_root"), cfg.get("shell", "history_file_size"),
                                      cfg.get("shell", "history_control_root")), ("2000", "2000", "ignoredups:ignorespace"))
                if persona.id == "tplink-archer-c7":
                    self.assertEqual(cfg.get("shell", "login_path"), "/bin:/sbin:/usr/bin:/usr/sbin")
                    self.assertEqual(cfg.get("shell", "login_shell"), "/bin/ash")
                elif persona.id == "zyxel-nas326":
                    # the PATH of /etc/profile of the NAS326's initramfs (BASE_PATH /firmware, its
                    #   /etc/settings/firmware_info_path); the shell of the login stays upstream's key
                    self.assertEqual(cfg.get("shell", "login_path"),
                                     "/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin:/firmware/sbin:"
                                     "/usr/local/zy-pkgs/bin:/firmware/htp")
                    self.assertFalse(cfg.has_option("shell", "login_shell"))
                else:
                    self.assertFalse(cfg.has_option("shell", "login_path"))
                    self.assertFalse(cfg.has_option("shell", "login_shell"))

    def test_fails_loudly_on_unknown_upstream(self):
        root = tmpdir(self) / "cowrie"
        shutil.copytree(FIXTURE, root)
        path = root / "src/cowrie/shell/session.py"
        path.write_text(path.read_text().replace("    def openShell(self, processprotocol):\n",
                                                 "    def openShell(self, pp):\n", 1))
        result = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not find expected text", result.stderr)
        for rel in ("src/cowrie/shell/session.py", "src/cowrie/shell/telnet.py"):
            for old, new in (('            self.environ["PATH"] = (\n                "/usr/local/bin:/usr/bin:/bin:',
                              '            self.environ["PATH"] = (\n                "/usr/bin:/bin:'),
                             ("from cowrie.insults import insults\n", "from cowrie import insults\n")):
                with self.subTest(file=rel, needle=old[:30]):
                    root = tmpdir(self) / "cowrie"
                    shutil.copytree(FIXTURE, root)
                    path = root / rel
                    text = path.read_text()
                    self.assertIn(old, text)
                    path.write_text(text.replace(old, new, 1))
                    result = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Could not find expected text", result.stderr)



MSH_PTY = json.loads((Path(__file__).parent / "fixtures" / "msh.json").read_text(encoding="utf-8"))[
    "images"]["fw_dlink_dir859_105b03_msh_pty"]["cases"]


class MshLoginTest(unittest.TestCase):
    """The DIR-859's msh on a terminal (tests/fixtures/msh.json, the pty cases): two empty lines, the banner of
    its BusyBox with "built-in shell (msh)", the line about help, an empty line, then the prompt "# "."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        cls.protocol = (root / "src/cowrie/shell/protocol.py").read_text()
        cls.honeypot = (root / "src/cowrie/shell/honeypot.py").read_text()
        persona = catalog.by_id("dlink-dir859")
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(render.render_cfg(persona, draw(persona, 0), Path("/tmp/r"), Path("/tmp/k")))
        cls.values = {("shell", key): value for key, value in cfg.items("shell")}
        cls.persona = persona

    def motd(self, values, motd=None):
        written = []

        def contents(path):
            if motd is None:
                raise FileNotFoundError(path)
            return motd

        protocol = types.SimpleNamespace(terminal=types.SimpleNamespace(write=written.append),
                                         fs=types.SimpleNamespace(file_contents=contents))
        method(self.protocol, "HoneyPotInteractiveProtocol", "displayMOTD", {"CowrieConfig": FakeConfig(values)})(
            protocol)
        return b"".join(written)

    def test_the_banner_of_msh(self):
        transcript = MSH_PTY["echo a\nexit\n"][0]
        banner = transcript.split("# echo a", 1)[0].replace("\r\n", "\n")
        self.assertEqual(banner, "\n\nBusyBox v1.14.1 (2016-06-28 10:53:08 CST) built-in shell (msh)\n"
                                 "Enter 'help' for a list of built-in commands.\n\n")
        # the persona's BusyBox of its release (1.05B03 above)
        for seed in range(4):
            ident = draw(self.persona, seed)
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read_string(render.render_cfg(self.persona, ident, Path("/tmp/r"), Path("/tmp/k")))
            values = {("shell", key): value for key, value in cfg.items("shell")}
            with self.subTest(release=ident.release.label):
                expected = banner.replace("BusyBox v1.14.1 (2016-06-28 10:53:08 CST)", ident.release.extra["busybox"])
                self.assertEqual(self.motd(values).decode(), expected)
        # the motd of the file system comes first; other shells print none of it
        self.assertTrue(self.motd(self.values, b"motd\n").startswith(b"motd\n\n\nBusyBox v1.14.1 ("))
        self.assertEqual(self.motd({("shell", "not_found_style"): "ash",
                                    ("shell", "text_banner"): "BusyBox v1.19.4"}, b"motd\n"), b"motd\n")
        self.assertEqual(self.motd({}), b"")

    def prompt(self, uid):
        written = []
        protocol = types.SimpleNamespace(terminal=types.SimpleNamespace(write=written.append), ps=None,
                                         hostname="DIR-859")
        shell = types.SimpleNamespace(interactive=True, protocol=protocol, cwd="/root",
                                      user={"username": "root" if uid == 0 else "user", "uid": uid,
                                            "home": "/root" if uid == 0 else "/home/user"})
        method(self.honeypot, "HoneyPotShell", "showPrompt", {"CowrieConfig": FakeConfig(self.values),
                                                                "posixpath": posixpath})(shell)
        return written[0].decode(), protocol.ps

    def test_the_prompt_of_msh(self):
        # "# " for root after the banner and after a failed command; PS2 "> " (a quote over two lines)
        self.assertEqual(self.values[("shell", "prompt_style")], "msh")
        self.assertIn("\r\n\r\n# echo a\r\na\r\n# exit", MSH_PTY["echo a\nexit\n"][0])
        self.assertIn("# if true\r\n> then\r\n> echo y\r\n> fi\r\ny\r\n# ", MSH_PTY["if true\nthen\necho y\nfi\nexit\n"][0])
        self.assertEqual(self.prompt(0), ("# ", (b"# ", b"> ")))
        # a trailing backslash continues with PS1 again (a known deviation: Cowrie has no continuation prompt)
        self.assertIn("# echo a \\\r\n# b\r\na b\r\n# ", MSH_PTY["echo a \\\nb\nexit\n"][0])
        # msh.c: "$ " for the others (the source default; no recording as another user)
        self.assertEqual(self.prompt(1000), ("$ ", (b"$ ", b"> ")))


if __name__ == "__main__":
    unittest.main()
