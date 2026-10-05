"""customizer_core: resolution, checks, rendering and rebuild.

Run from the repository root: python3 -m unittest discover compose/tests
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

import customizer_core as core  # noqa: E402

CUSTOMIZER = os.path.join(core.COMPOSE_DIR, "customizer.py")


def errors(result, kind=None):
    return [f.text for f in result.errors if kind in (None, f.kind)]


class CoreTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.catalog = core.Catalog()

    def resolve(self, base, add=(), remove=(), ports=None, **kwargs):
        return core.resolve(self.catalog, core.Selection(base, list(add), list(remove), dict(ports or {})), **kwargs)

    # editions ---------------------------------------------------------------

    def test_every_edition_renders_unchanged(self):
        for edition, compose in self.catalog.editions.items():
            with self.subTest(edition=edition):
                result = self.resolve(edition)
                self.assertEqual(errors(result), [])
                data = yaml.safe_load(core.render(self.catalog, result))
                self.assertEqual(sorted(data["services"]), sorted(compose.services))
                for name, definition in compose.services.items():
                    self.assertEqual(data["services"][name], definition, name)
                self.assertEqual(data.get("networks"), compose.data.get("networks"))

    # resolution -------------------------------------------------------------

    def test_logstash_pulls_elasticsearch_on_a_hive(self):
        result = self.resolve("STANDARD", remove=["elasticsearch"])
        self.assertTrue(any("elasticsearch cannot be removed, logstash needs it" in e for e in errors(result)))

    def test_sensor_has_no_hive_services(self):
        result = self.resolve("SENSOR")
        self.assertNotIn("elasticsearch", result.services)
        self.assertIn("logstash", result.services)
        self.assertFalse(self.catalog.offered("SENSOR", "kibana"))
        self.assertTrue(errors(self.resolve("SENSOR", add=["kibana"]), "service"))

    def test_snare_brings_tanner_along(self):
        result = self.resolve("TARPIT", add=["snare"], ports={("snare", 80, "tcp"): 8081})
        for name in ("tanner", "tanner_api", "tanner_phpox", "tanner_redis"):
            self.assertIn(name, result.services)
        self.assertEqual(errors(result), [])

    def test_attack_map_brings_its_helpers(self):
        result = self.resolve("STANDARD", remove=["map_data", "map_redis"])
        self.assertTrue(errors(result, "dependency"))

    def test_required_cannot_be_removed(self):
        self.assertTrue(errors(self.resolve("STANDARD", remove=["tpotinit"]), "dependency"))

    def test_declared_conflict(self):
        self.assertEqual(errors(self.resolve("STANDARD", add=["glutton"]), "conflict"),
                         ["glutton and honeytrap cannot run at the same time"])

    def test_mac_win_offers_no_nfq_honeypots(self):
        self.assertFalse(self.catalog.offered("MAC_WIN", "honeytrap"))
        self.assertTrue(self.catalog.offered("MAC_WIN", "p0f"))

    def test_unknown_service_strict_and_lenient(self):
        self.assertTrue(errors(self.resolve("STANDARD", add=["nope"])))
        lenient = self.resolve("STANDARD", add=["nope"], strict=False)
        self.assertEqual(errors(lenient), [])
        self.assertTrue(lenient.warnings)
        self.assertEqual(lenient.selection.add, [])

    # ports ------------------------------------------------------------------

    def test_port_parsing(self):
        port = core.parse_port("161:161/udp")
        self.assertEqual((port.host, port.container, port.proto), (161, 161, "udp"))
        port = core.parse_port("127.0.0.1:64305:64305")
        self.assertEqual((port.ip, port.host, port.proto), ("127.0.0.1", 64305, "tcp"))
        self.assertIsNone(core.parse_port("8080"))
        for bad in ("1000-1010:1000-1010", "${PORT}:80", "80:80/sctp"):
            with self.assertRaises(core.CustomizerError):
                core.parse_port(bad)

    def test_tcp_and_udp_do_not_clash(self):
        self.assertFalse(core.parse_port("5060:5060/tcp").overlaps(core.parse_port("5060:5060/udp")))
        self.assertTrue(core.parse_port("80:80").overlaps(core.parse_port("127.0.0.1:80:8080")))
        self.assertFalse(core.parse_port("127.0.0.1:80:80").overlaps(core.parse_port("127.0.0.2:80:80")))

    def test_port_conflict_and_suggestions(self):
        selection = core.Selection("MINI", add=["wordpot"])
        self.assertEqual(errors(core.resolve(self.catalog, selection), "port"),
                         ["port 80/tcp is used by honeypots and wordpot"])
        options = core.suggestions(self.catalog, selection, "wordpot")
        self.assertTrue(options)
        selection.ports.update(options[0][1])
        self.assertEqual(errors(core.resolve(self.catalog, selection)), [])

    def test_edition_variant_is_suggested(self):
        # wordpot from the catalog on 80 next to hellpot: STANDARD puts it on 8080
        selection = core.Selection("TARPIT", add=["wordpot"], remove=["go-pot"])
        labels = dict(core.suggestions(self.catalog, selection, "wordpot"))
        self.assertEqual(list(labels), ["host ports as in STANDARD, SENSOR, MOBILE, MAC_WIN"])
        self.assertEqual(list(labels.values()), [{("wordpot", 80, "tcp"): 8080}])

    def test_override_by_original_host_port(self):
        # log4pot maps five host ports to container port 8080
        result = self.resolve("STANDARD", add=["log4pot"], ports={
            ("log4pot", 80, "tcp"): None, ("log4pot", 443, "tcp"): None, ("log4pot", 8080, "tcp"): 8090,
            ("log4pot", 9200, "tcp"): None})
        self.assertEqual(errors(result), [])
        self.assertEqual([p.raw for p in result.ports["log4pot"]], ["8090:8080", "25565:8080"])
        data = yaml.safe_load(core.render(self.catalog, result))
        self.assertEqual(data["services"]["log4pot"]["ports"], ["8090:8080", "25565:8080"])

    def test_dropping_all_ports_removes_the_key(self):
        result = self.resolve("STANDARD", ports={("wordpot", 8080, "tcp"): None})
        self.assertEqual(errors(result), [])
        data = yaml.safe_load(core.render(self.catalog, result))
        self.assertNotIn("ports", data["services"]["wordpot"])

    def test_unknown_override(self):
        self.assertTrue(errors(self.resolve("STANDARD", ports={("wordpot", 1, "tcp"): 2}), "port"))

    def test_override_syntax(self):
        self.assertEqual(core.parse_overrides("wordpot:80=8080,sentrypeer:5060/udp=-"),
                         {("wordpot", 80, "tcp"): 8080, ("sentrypeer", 5060, "udp"): None})
        for bad in ("wordpot=8080", "wordpot:80=99999", "wordpot:80=127.0.0.1:80"):
            with self.assertRaises(core.CustomizerError):
                core.parse_overrides(bad)

    # networks ---------------------------------------------------------------

    def test_network_limit_counts_default_network(self):
        result = self.resolve("SENSOR")
        self.assertIn("default", result.networks)                 # logstash has no networks there
        self.assertTrue(errors(self.resolve("SENSOR", max_networks=len(result.networks) - 1), "network"))
        self.assertEqual(errors(self.resolve("SENSOR", max_networks=len(result.networks))), [])

    def test_network_limit_is_checked_for_the_whole_catalog(self):
        # the honeypots share honeypot_local, the whole catalogue fits into a few networks
        everything = [n for n in self.catalog.order if not self.catalog.flag(n, "hidden")]
        result = self.resolve("STANDARD", add=everything, remove=["glutton"])
        self.assertLessEqual(len(result.networks), 6)
        self.assertEqual(errors(result, "network"), [])
        self.assertTrue(errors(self.resolve("STANDARD", add=everything, remove=["glutton"],
                                            max_networks=len(result.networks) - 1), "network"))

    # rendering --------------------------------------------------------------

    def test_render_keeps_comments_and_drops_banners_and_meta(self):
        text = core.render(self.catalog, self.resolve("STANDARD", add=["ddospot"], remove=["honeytrap"]))
        self.assertIn("# Ddospot service", text)
        self.assertIn('#     - "161:161/udp"', text)                 # commented option inside the block
        self.assertIn("#     - ${TPOT_DATA_PATH}/dicompot/images", text)  # trailing comment of a block
        self.assertNotIn("x-tpot", text)
        self.assertNotIn("#### DEV", text)
        self.assertNotIn("#### /ELK", text)
        self.assertIn("#### Conpot (ICS)", text)
        self.assertNotIn("\n  honeytrap:", text)
        for line in text.split("\n"):
            self.assertFalse(line.startswith("   ") and line.strip().endswith(":") and line.startswith("  honeytrap"))

    def test_split_entries(self):
        lines = ["#### Banner", "# Alpha", "  alpha:", "    image: a", "#    x: 1", "    y: 2", "", "# trailing",
                 "", "## Beta", "  beta:", "    image: b", "#### /End", ""]
        blocks = core._split_entries(lines)
        self.assertEqual(blocks["alpha"], ["# Alpha", "  alpha:", "    image: a", "#    x: 1", "    y: 2", "",
                                           "# trailing"])
        self.assertEqual(blocks["beta"], ["## Beta", "  beta:", "    image: b"])

    def test_strip_nested_x_tpot(self):
        block = ["  a:", "    x-tpot:", "      group: tools", "      description: x", "    image: a"]
        self.assertEqual(core._strip_xtpot(block), ["  a:", "    image: a"])

    def test_header_and_checksum(self):
        result = self.resolve("STANDARD", add=["citrixhoneypot"], remove=["honeytrap"],
                              ports={("citrixhoneypot", 443, "tcp"): 8444})
        text = core.render(self.catalog, result)
        self.assertTrue(text.startswith("# T-Pot: CUSTOM\n"))
        self.assertTrue(core.checksum_ok(text))
        self.assertFalse(core.checksum_ok(text.replace("restart: always", "restart: no", 1)))
        selection = core.parse_header(text)
        self.assertEqual((selection.base, selection.add, selection.remove), ("STANDARD", ["citrixhoneypot"],
                                                                            ["honeytrap"]))
        self.assertEqual(selection.ports, {("citrixhoneypot", 443, "tcp"): 8444})
        self.assertIsNone(core.parse_header("# T-Pot: CUSTOM EDITION\nnetworks:\n"))
        self.assertIsNone(core.parse_header("# T-Pot: STANDARD\n"))

    def test_checksum_matches_bash(self):
        text = core.render(self.catalog, self.resolve("MINI"))
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
            handle.write(text)
        try:
            tool = "sha256sum" if subprocess.call(["sh", "-c", "command -v sha256sum >/dev/null"]) == 0 \
                else "shasum -a 256"
            out = subprocess.check_output(
                ["sh", "-c", f"sed '1,/^# customizer: sha256=/d' '{handle.name}' | {tool}"],
                universal_newlines=True)
            expected = [line for line in text.split("\n") if line.startswith("# customizer: sha256=")][0]
            self.assertEqual(out.split()[0], expected.split("=", 1)[1])
        finally:
            os.unlink(handle.name)

    def test_rebuild_is_idempotent_and_drops_gone_services(self):
        text = core.render(self.catalog, self.resolve("STANDARD", add=["ddospot"]))
        selection = core.parse_header(text)
        again = core.render(self.catalog, core.resolve(self.catalog, selection, strict=False))
        self.assertEqual(text, again)
        selection.add.append("spiderfoot")
        result = core.resolve(self.catalog, selection, strict=False)
        self.assertEqual(errors(result), [])
        self.assertEqual(core.render(self.catalog, result), text)


class CliTest(unittest.TestCase):

    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, CUSTOMIZER] + list(args), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              env=dict(os.environ, PATH="/usr/bin:/bin"))   # no docker: no compose check
        return proc.returncode, proc.stdout + proc.stderr

    def test_headless_rebuild_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "custom.yml")
            code, text = self.run_cli("--base", "standard", "--add", "citrixhoneypot", "-o", out)
            self.assertEqual(code, 1, text)
            self.assertIn("port 443/tcp", text)
            self.assertFalse(os.path.exists(out))
            code, text = self.run_cli("--base", "standard", "--add", "citrixhoneypot",
                                      "--port", "citrixhoneypot:443=8444", "-o", out)
            self.assertEqual(code, 0, text)
            with open(out, encoding="utf-8") as handle:
                first = handle.read()
            code, text = self.run_cli("--rebuild", out)
            self.assertEqual(code, 0, text)
            with open(out, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), first)
            standard = self.run_cli("--rebuild", os.path.join(core.COMPOSE_DIR, "standard.yml"))
            self.assertEqual(standard[0], 2)

    def test_text_mode_quits_on_eof(self):
        code, text = self.run_cli("--text")
        self.assertEqual(code, 0, text)
        self.assertIn("Nothing written", text)


if __name__ == "__main__":
    unittest.main()
