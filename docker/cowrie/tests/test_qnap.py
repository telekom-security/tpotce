"""QNAP QTS: admin is the root account (uid 0, the first line of passwd), the prompt of its /etc/profile is
[\\w] \\$, "[~] #" in the home directory."""
import configparser
import json
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

from tests import test_patch
from tests.test_patch import FIXTURE, PATCH, FakeConfig, method, tmpdir
from tpot_personas import catalog, render
from tpot_personas.identity import draw

# bash --version of the bash QNAP's GPL tree of QTS 5.2.3 (20250218) builds (src/bash-3.2.48 at patch level 57,
#   run under qemu-x86_64; two lines, status 0)
QNAP_BASH = ("GNU bash, version 3.2.57(1)-release (x86_64-QNAP-linux-gnu)\n"
             "Copyright (C) 2007 Free Software Foundation, Inc.\n")


class QnapTest(unittest.TestCase):
    def test_the_prompt(self):
        case = test_patch.PatchTest("test_redhat_prompt")
        # the borrowed case is never run: its temporary folders go with this test
        self.addCleanup(case.doCleanups)
        self.assertEqual(case.prompt("/home/admin", "admin", 0, "/home/admin", style="qnap"), "[~] # ")
        self.assertEqual(case.prompt("/share/Public", "admin", 0, "/home/admin", style="qnap"), "[/share/Public] # ")
        self.assertEqual(case.prompt("/home/admin/x", "admin", 0, "/home/admin", style="qnap"), "[~/x] # ")
        self.assertEqual(case.prompt("/tmp", "guest", 1000, "/home/guest", style="qnap"), "[/tmp] $ ")

    def test_admin_is_uid_0(self):
        persona = catalog.by_id("qnap-qts")
        self.assertEqual((persona.uid, persona.gid, persona.prompt_style), (0, 0, "qnap"))
        content = persona.builder(persona, draw(persona, 0))
        passwd = content.files["etc/passwd"].splitlines()
        self.assertEqual(passwd[0], "admin:x:0:0:administrator:/home/admin:/bin/sh")
        # the root line of a login as root (the honeypot takes it) comes after those of QTS
        self.assertEqual(passwd[-1], "root:x:0:0:root:/root:/bin/sh")
        self.assertTrue(content.files["etc/group"].startswith("administrators:x:0:admin\n"))
        self.assertTrue(all(p["USER"] != "root" for p in content.processes))


    def test_the_openssh_of_the_gpl_build(self):
        # version.h of src/openssh-7.0p1 (the folder name is older than its contents) of the same tree:
        #   OpenSSH_9.8 p1, the banner sends SSH_VERSION; the OpenSSL of ssh -V is inferred from src/openssl-3.0.9
        release = next(r for r in catalog.by_id("qnap-qts").releases if r.label.startswith("5.2.3."))
        self.assertEqual(release.ssh_banner, "SSH-2.0-OpenSSH_9.8")
        self.assertEqual(release.ssh_version, "OpenSSH_9.8p1, OpenSSL 3.0.9 30 May 2023")

    def test_bash_version(self):
        persona = catalog.by_id("qnap-qts")
        for seed in range(6):
            ident = draw(persona, seed)
            with self.subTest(release=ident.release.label):
                self.assertEqual(persona.builder(persona, ident).bash_version, QNAP_BASH)
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render.render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
                self.assertEqual(json.loads(cfg.get("shell", "bash_version")), QNAP_BASH)
        # the other personas keep Cowrie's bash
        for other in catalog.PERSONAS:
            if other.family != "qnap":
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render.render_cfg(other, draw(other, 0), Path("/tmp/r"), Path("/tmp/k")))
                self.assertFalse(cfg.has_option("shell", "bash_version"), other.id)

    def test_bash_answers_its_version(self):
        root = tmpdir(self) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        source = (root / "src/cowrie/commands/bash.py").read_text()

        def run(argv0, args, values):
            written, done = [], []
            command = types.SimpleNamespace(args=args, argv0=argv0, write=written.append,
                                            exit=lambda: done.append("exit"), input_data=None,
                                            interactive_shell=lambda: done.append("shell"),
                                            execute_script_file=lambda name: done.append("script"))
            method(source, "Command_sh", "start", {"CowrieConfig": FakeConfig(values), "posixpath": __import__("posixpath")})(command)
            return "".join(written), done

        values = {("shell", "bash_version"): json.dumps(QNAP_BASH)}
        for argv0 in ("bash", "/bin/bash"):
            self.assertEqual(run(argv0, ["--version"], values), (QNAP_BASH, ["exit"]))
        # sh is BusyBox on QTS; without the key Cowrie's bash stays
        self.assertEqual(run("sh", ["--version"], values), ("", ["shell"]))
        self.assertEqual(run("bash", ["--version"], {}), ("", ["shell"]))
        self.assertEqual(run("bash", [], values), ("", ["shell"]))


if __name__ == "__main__":
    unittest.main()
