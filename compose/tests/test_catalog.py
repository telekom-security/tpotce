"""Catalog (tpot_services.yml) against the editions in compose/.

Run from the repository root: python3 -m unittest discover compose/tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

import customizer_core as core  # noqa: E402

# keys an edition may set differently from the catalog: the host ports it chose to
# avoid clashes, its own startup order and networks
ALLOWED = {"ports", "networks", "depends_on"}
# Docker Desktop has no host networking for tpotinit and the NSM services and other mounts
ALLOWED_MAC_WIN = ALLOWED | {"network_mode", "cap_add", "volumes"}
# every honeypot that talks to no other container shares one network with ICC off,
# the rest keep a network of their own
SHARED = "honeypot_local"
OWN_NETWORK = {"nginx_local", "ewsposter_local", "tpotinit_local", "suricata_local", "default"}
META_KEYS = {"group", "description", "required", "requires", "conflicts", "hidden", "hive_only", "linux_only",
             "edition"}


class CatalogTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.catalog = core.Catalog()

    def test_editions_found(self):
        for edition in ("STANDARD", "SENSOR", "MINI", "MOBILE", "LLM", "TARPIT", "MAC_WIN"):
            self.assertIn(edition, self.catalog.editions)

    def test_every_service_has_metadata(self):
        groups = {g for g, _title in core.GROUPS}
        for name, meta in self.catalog.meta.items():
            with self.subTest(service=name):
                self.assertIn(meta.get("group"), groups)
                self.assertTrue(meta.get("description"))
                self.assertFalse(set(meta) - META_KEYS, "unknown x-tpot keys")
                if "edition" in meta:
                    self.assertIn(meta["edition"], self.catalog.editions, "edition")
                    self.assertIn(name, self.catalog.editions[meta["edition"]].services, "edition")
                for key in ("requires", "conflicts"):
                    for other in meta.get(key) or []:
                        self.assertIn(other, self.catalog.catalog.services, f"{key} {other}")

    def test_conflicts_are_symmetric(self):
        for name in self.catalog.order:
            for other in self.catalog.listed(name, "conflicts"):
                self.assertIn(name, self.catalog.listed(other, "conflicts"), f"{other} misses {name}")

    def test_edition_services_are_in_catalog(self):
        for edition, compose in self.catalog.editions.items():
            for name in compose.services:
                with self.subTest(edition=edition, service=name):
                    self.assertIn(name, self.catalog.catalog.services)

    def test_editions_only_differ_in_allowed_keys(self):
        for edition, compose in self.catalog.editions.items():
            allowed = ALLOWED_MAC_WIN if edition == "MAC_WIN" else ALLOWED
            for name, definition in compose.services.items():
                reference = dict(self.catalog.catalog.services[name])
                reference.pop("x-tpot", None)
                differ = {k for k in set(definition) | set(reference) if definition.get(k) != reference.get(k)}
                with self.subTest(edition=edition, service=name):
                    self.assertFalse(differ - allowed, f"differs in {sorted(differ - allowed)}")

    def test_network_definitions_match(self):
        seen = {}
        for compose in [self.catalog.catalog] + list(self.catalog.editions.values()):
            for name, definition in compose.networks.items():
                if name in seen:
                    self.assertEqual(definition, seen[name][1],
                                     f"network {name} differs between {seen[name][0]} and {compose.path}")
                else:
                    seen[name] = (compose.path, definition)

    def test_service_networks_are_defined(self):
        for compose in [self.catalog.catalog] + list(self.catalog.editions.values()):
            for name, definition in compose.services.items():
                for network in core.service_networks(definition):
                    if network != "default":
                        self.assertIn(network, compose.networks, f"{compose.path}: {name} uses {network}")

    def all_files(self):
        return [self.catalog.catalog] + list(self.catalog.editions.values())

    def test_honeypots_share_the_isolated_network(self):
        for compose in self.all_files():
            for name, definition in compose.services.items():
                if self.catalog.group(name) not in ("honeypots", "conpot", "llm"):
                    continue
                networks = set(core.service_networks(definition))
                if not networks:    # host mode
                    continue
                with self.subTest(file=compose.path, service=name):
                    self.assertEqual(networks, {SHARED})

    def test_shared_network_has_icc_off(self):
        found = 0
        for compose in self.all_files():
            definition = compose.networks.get(SHARED)
            if SHARED in compose.networks:
                found += 1
                self.assertEqual((definition or {}).get("driver_opts", {}).get(
                    "com.docker.network.bridge.enable_icc"), "false", compose.path)
        self.assertGreater(found, 0)

    def test_no_network_of_its_own_for_a_honeypot(self):
        for compose in self.all_files():
            with self.subTest(file=compose.path):
                self.assertEqual(set(compose.networks) - OWN_NETWORK - {SHARED}, set())

    def test_llm_honeypots_belong_to_the_llm_edition(self):
        for name in ("beelzebub", "galah"):
            with self.subTest(service=name):
                self.assertEqual(self.catalog.group(name), "llm")
                self.assertEqual(self.catalog.meta[name].get("edition"), "LLM")
        self.assertIn(("llm", "LLM honeypots"), core.GROUPS)

    def test_llm_edition_has_no_port_clash(self):
        """Beelzebub publishes every prepared service of its own that Galah does not take there."""
        llm = self.catalog.editions["LLM"]
        published = [(p.ip, p.host, p.proto) for name in llm.services
                     for p in core.service_ports(llm.services[name])]
        self.assertEqual(len(published), len(set(published)), sorted(published))
        folder = os.path.join(os.path.dirname(core.COMPOSE_DIR), "docker", "beelzebub", "dist",
                              "configurations", "services")
        prepared = set()
        for file in os.listdir(folder):
            with open(os.path.join(folder, file), encoding="utf-8") as handle:
                address = str(yaml.safe_load(handle).get("address", ""))
            prepared.add(int(address.rsplit(":", 1)[1]))
        galah = {p.host for p in core.service_ports(llm.services["galah"])}
        beelzebub = {p.host for p in core.service_ports(llm.services["beelzebub"])}
        self.assertEqual(beelzebub, prepared - galah)
        catalog = {p.host for p in core.service_ports(self.catalog.catalog.services["beelzebub"])}
        self.assertEqual(catalog, {22})          # elsewhere the others meet other honeypots

    def test_x_tpot_only_in_catalog(self):
        for compose in self.catalog.editions.values():
            with open(compose.path, encoding="utf-8") as handle:
                self.assertNotIn("x-tpot", handle.read(), compose.path)

    def test_root_compose_is_standard(self):
        with open(os.path.join(core.REPO_DIR, "docker-compose.yml"), encoding="utf-8") as root, \
                open(self.catalog.editions["STANDARD"].path, encoding="utf-8") as standard:
            self.assertEqual(root.read(), standard.read())

    def test_catalog_ports_parse(self):
        for compose in [self.catalog.catalog] + list(self.catalog.editions.values()):
            for name, definition in compose.services.items():
                with self.subTest(file=compose.path, service=name):
                    core.service_ports(definition)

    def test_catalog_is_valid_yaml_with_services_section(self):
        with open(self.catalog.catalog.path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        self.assertEqual(list(data), ["networks", "services"])


if __name__ == "__main__":
    unittest.main()
