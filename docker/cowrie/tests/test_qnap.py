"""QNAP QTS: admin is the root account (uid 0, the first line of passwd), the prompt of its /etc/profile is
[\\w] \\$, "[~] #" in the home directory."""
import unittest

from tests import test_patch
from tpot_personas import catalog
from tpot_personas.identity import draw


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


if __name__ == "__main__":
    unittest.main()
