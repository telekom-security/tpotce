import unittest
from pathlib import Path

from tests.helpers import DIST  # noqa: F401
from tpot_personas import PersonaError, catalog
from tpot_personas.ssh_profiles import PROFILES

ROOT = Path(__file__).resolve().parents[3]

PORTED = ["ubuntu-jammy", "debian-bookworm-vuln", "fedora-36-vuln", "rhel-9-vuln", "dlink-dir859",
          "tplink-wr841n", "zyxel-nas326", "openwrt-1806", "qnap-qts", "synology-dsm",
          "ubiquiti-edgerouter-x"]


class CatalogTest(unittest.TestCase):
    def test_ids_unique_and_safe(self):
        ids = catalog.ids()
        self.assertEqual(len(ids), len(set(ids)))
        for pid in ids:
            self.assertRegex(pid, r"^[a-z0-9][a-z0-9-]*$")

    def test_the_eleven_personas_are_ported_in_order(self):
        self.assertEqual(catalog.ids(), PORTED)

    def test_by_id_rejects_unknown(self):
        with self.assertRaises(PersonaError):
            catalog.by_id("../etc")
        self.assertEqual(catalog.by_id("qnap-qts").id, "qnap-qts")

    def test_fields(self):
        for p in catalog.PERSONAS:
            self.assertIn(p.kind, ("server", "device"), p.id)
            self.assertIn(p.ssh_profile, PROFILES, p.id)
            self.assertTrue(p.releases and p.hardware and p.hostnames, p.id)
            for r in p.releases:
                self.assertTrue(r.ssh_banner.startswith("SSH-2.0-"), (p.id, r.label))
                product = "dropbear" if p.ssh_profile == "dropbear" else "OpenSSH"
                self.assertIn(product, r.ssh_banner, (p.id, r.label))
            for hw in p.hardware:
                self.assertGreater(hw.cpus, 0, p.id)
                self.assertGreater(hw.mem_kb, 0, p.id)
                self.assertGreater(hw.disk_kb, 0, p.id)
            for name, kind in p.tokens:
                self.assertIn(kind, ("hf", "openai", "aws"), (p.id, name))

    def test_no_tpot_version_in_persona_data(self):
        version = (ROOT / "version").read_text().strip()
        for path in (DIST / "tpot_personas").glob("*.py"):
            self.assertNotIn(version, path.read_text(), path.name)
