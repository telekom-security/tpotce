"""The usage of BusyBox for uname, head, grep and which where the persona has their recorded texts
([shell] busybox_usage: the Archer C7 v5, fixtures/busybox_usage_c7.json recorded under
qemu-mips-static); the other personas keep Cowrie's answers."""
import configparser
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, module_parts
from tpot_personas import catalog, firmware, render
from tpot_personas.identity import draw

CASES = json.loads((Path(__file__).parent / "fixtures" / "busybox_usage_c7.json").read_text())["cases"]
NAMES = ["tpot_bb_usage_table", "tpot_bb_usage_getopt", "tpot_bb_usage_stop"]


class Command:
    def __init__(self):
        self.err, self.exit_code = [], 0

    def errorWrite(self, text):
        self.err.append(text)


class BusyboxUsageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        cls.root = root

    def stop(self, applet, args, usage=True):
        values = {("shell", "text_banner"): "BusyBox v1.19.4 (2022-07-14 23:31:52 EDT)", ("shell", "text_libc"): "uclibc"}
        if usage:
            values[("shell", "busybox_usage")] = str(firmware.IMAGES / "archer-c7-v5-usage.json")
        names = {"CowrieConfig": FakeConfig(values)}
        module_parts((self.root / "src/cowrie/commands/uname.py").read_text(), NAMES, names)
        command = Command()
        stopped = names["tpot_bb_usage_stop"](command, applet, list(args))
        return stopped, "".join(command.err), command.exit_code

    def test_the_recorded_texts(self):
        for case, text in CASES.items():
            applet, *args = case.split()
            with self.subTest(case=case):
                self.assertEqual(self.stop(applet, args), (True, text, 1))

    def test_what_the_commands_answer_themselves(self):
        for applet, args in (("uname", ["-a"]), ("uname", ["-o"]), ("uname", ["-i"]), ("head", ["-5", "/etc/passwd"]),
                             ("head", ["-n", "2", "/etc/passwd"]), ("head", ["-n2"]), ("head", []),
                             ("grep", ["root", "/etc/passwd"]), ("which", ["ls"]), ("uname", ["--", "-x"])):
            with self.subTest(applet=applet, args=args):
                self.assertEqual(self.stop(applet, args)[0], False)
        # without the key every command answers as before
        self.assertEqual(self.stop("grep", [], usage=False)[0], False)

    def test_the_commands_are_wrapped(self):
        for rel, applet in (("uname.py", "uname"), ("fs.py", "grep"), ("fs.py", "head"), ("which.py", "which")):
            with self.subTest(applet=applet):
                text = (self.root / "src/cowrie/commands" / rel).read_text()
                self.assertIn(f'    commands["{applet}"] = tpot_bb_usage_wrap(commands["{applet}"], "{applet}")', text)

    def test_only_the_archer_c7_has_the_key(self):
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render.render_cfg(persona, draw(persona, 0), Path("/tmp/r"), Path("/tmp/k")))
                self.assertEqual(cfg.has_option("shell", "busybox_usage"), persona.id == "tplink-archer-c7")

    def test_fails_loudly_on_unknown_upstream(self):
        for rel, old in (("uname.py", 'commands["uname"] = Command_uname\n'),
                         ("which.py", 'commands["which"] = Command_which\n'),
                         ("fs.py", 'commands["grep"] = Command_grep\n')):
            with self.subTest(file=rel), tempfile.TemporaryDirectory() as handle:
                root = Path(handle) / "cowrie"
                shutil.copytree(FIXTURE, root)
                path = root / "src/cowrie/commands" / rel
                path.write_text(path.read_text().replace(old, old.replace("] = ", "] =  "), 1))
                result = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], capture_output=True,
                                        text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Could not find expected text", result.stderr)


if __name__ == "__main__":
    unittest.main()
