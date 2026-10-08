"""Heralding's config against its old settings and the free-port policy of every compose file.

Run from the repository root: python3 -m unittest discover compose/tests
"""

import copy
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "docker/heralding/dist/heralding.yml"
# the 16 services of Heralding 1.x as T-Pot configured them
LEGACY = ROOT / "docker/heralding/validation/fixtures/tpot_heralding_legacy.yml"


def mapping_key(mapping):
    """The host port and protocol of a compose port mapping."""
    if isinstance(mapping, dict):
        return str(mapping["published"]), mapping.get("protocol", "tcp")
    value, _, protocol = str(mapping).partition("/")
    return value.split(":")[-2] if ":" in value else value, protocol or "tcp"


class HeraldingTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load(CONFIG.read_text())["capabilities"]
        cls.legacy = yaml.safe_load(LEGACY.read_text())["capabilities"]

    def test_old_settings_unchanged(self):
        for name, legacy in self.legacy.items():
            current = self.config[name]
            if name == "rdp":
                # the one addition: RDP keeps TLS 1.2 as its ceiling for the Windows App
                current = copy.deepcopy(current)
                self.assertEqual(current["protocol_specific_data"].pop("tls_max_version"), "TLSv1_2")
            self.assertEqual(current, legacy, f"settings of {name} changed")

    def test_new_services_only_on_free_ports(self):
        new = set(self.config) - set(self.legacy)
        self.assertTrue(new)
        paths = [ROOT / "docker-compose.yml", *sorted((ROOT / "compose").glob("*.yml")),
                 ROOT / "docker/heralding/docker-compose.yml"]
        for path in paths:
            services = yaml.safe_load(path.read_text()).get("services") or {}
            if "heralding" not in services:
                continue
            occupied = {mapping_key(m) for name, service in services.items() if name != "heralding"
                        for m in service.get("ports", [])}
            published = {mapping_key(m) for m in services["heralding"].get("ports", [])}
            for name in sorted(new):
                port = str(self.config[name]["port"])
                for protocol in ("tcp", "udp") if name == "sip" else ("tcp",):
                    key = port, protocol
                    with self.subTest(file=path.relative_to(ROOT).as_posix(), service=name, protocol=protocol):
                        # published exactly where no other service of that file holds the port
                        self.assertEqual(key in published, key not in occupied)


if __name__ == "__main__":
    unittest.main()
