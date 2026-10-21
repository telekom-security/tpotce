"""PATH and SHELL of a login (shell/session.py for SSH, shell/telnet.py for Telnet): upstream's
Debian PATH and /bin/bash, or [shell] login_path / login_shell of the persona (the Archer C7:
the PATH of its /etc/profile and its /bin/ash)."""
import configparser
import copy
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

    def test_edgeos_has_its_vyatta_cfg_history_variables(self):
        # vyatta-cfg of the ER-e50 image: HISTCONTROL= (empty), HISTFILESIZE=10000, HISTSIZE=10000,
        #   HISTTIMEFORMAT='%FT%T%z ', all exported
        keys = {("shell", "history_size"): "10000", ("shell", "history_file_size"): "10000",
                ("shell", "history_control"): "", ("shell", "history_time_format"): '"%FT%T%z "',
                ("shell", "history_export"): "HISTSIZE,HISTFILESIZE,HISTCONTROL,HISTTIMEFORMAT"}
        environ = self.telnet_environ(keys, uid=1000)
        self.assertEqual({k: v for k, v in environ.items() if k.startswith("HIST")},
                         {"HISTSIZE": "10000", "HISTFILESIZE": "10000", "HISTCONTROL": "", "HISTTIMEFORMAT": "%FT%T%z "})
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
                if persona.family == "ubuntu":
                    self.assertEqual((cfg.get("shell", "history_file_size_root"), cfg.get("shell", "history_file_size"),
                                      cfg.get("shell", "history_control_root")), ("2000", "2000", "ignoredups:ignorespace"))
                if persona.id == "tplink-archer-c7":
                    self.assertEqual(cfg.get("shell", "login_path"), "/bin:/sbin:/usr/bin:/usr/sbin")
                    self.assertEqual(cfg.get("shell", "login_shell"), "/bin/ash")
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


if __name__ == "__main__":
    unittest.main()
