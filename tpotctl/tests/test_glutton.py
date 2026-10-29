"""Glutton's catch-all must never own an administrative or published service port."""

import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "glutton_rules", ROOT / "docker/tpotinit/dist/bin/glutton_rules.py")
firewall = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firewall)


class PortsTest(unittest.TestCase):
    def ports(self, *bindings):
        return firewall.protected_ports({"services": {"example": {"ports": list(bindings)}}})

    def test_system_ports_always_protected_in_both_protocols(self):
        ports = self.ports()
        for protocol in ("tcp", "udp"):
            self.assertTrue({64294, 64295, 64297, 64298, 64305, 7634} <= ports[protocol])

    def test_short_long_ipv6_ranges_and_protocols(self):
        ports = self.ports("127.0.0.1:8080:80", "[::1]:2200-2202:22-24/tcp",
                           "1053:53/udp", {"target": 443, "published": "8443", "protocol": "tcp"})
        self.assertTrue({8080, 2200, 2201, 2202, 8443} <= ports["tcp"])
        self.assertIn(1053, ports["udp"])
        self.assertNotIn(1053, ports["tcp"])
        self.assertNotIn(8080, ports["udp"])

    def test_no_multiport_limit(self):
        ports = self.ports("1000-1100:1000-1100")
        self.assertTrue(set(range(1000, 1101)) <= ports["tcp"])

    def test_environment_expansion(self):
        with patch.dict("os.environ", {"GLUTTON_TEST_PORT": "12345"}):
            self.assertIn(12345, self.ports("${GLUTTON_TEST_PORT}:80")["tcp"])

    def test_unresolved_random_and_invalid_ports_are_errors(self):
        for value in ("80", "0:80", "65536:80", "200-100:80", "${UNSET_GLUTTON_TEST}:80",
                      "hello:80", {"target": 80}, "123:80/invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ports(value)

    def test_empty_ports_and_sctp_do_not_affect_glutton(self):
        self.assertEqual(self.ports("123:80/sctp"), self.ports())
        self.assertEqual(firewall.protected_ports({"services": {"example": {"ports": None}}}),
                         self.ports())

    def test_invalid_compose_shapes_are_reported(self):
        for compose in (None, [], {}, {"services": []}, {"services": {"example": None}},
                        {"services": {"example": {"ports": "123:80"}}},
                        {"services": {"example": {"ports": {"123:80": None}}}}):
            with self.subTest(compose=compose), self.assertRaises(ValueError):
                firewall.protected_ports(compose)
        for binding in (None, [], True, {"published": 123, "protocol": []}):
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                self.ports(binding)


class RulesTest(unittest.TestCase):
    LEGACY = ("-A PREROUTING -i eth0 -p tcp -m state ! --state RELATED,ESTABLISHED "
              "-m tcp ! --dport 22 -j TPROXY --on-port 5000 --on-ip 127.0.0.1 --tproxy-mark 0x0/0x0")

    def test_only_exact_historical_redirects_are_removed(self):
        self.assertIsNotNone(firewall.legacy_rule(self.LEGACY))
        self.assertIsNotNone(firewall.legacy_rule(
            self.LEGACY.replace("tcp", "udp").replace("5000", "5001").replace("22 ", "64295 ")))
        for foreign in (self.LEGACY + " -m comment --comment foreign",
                        self.LEGACY.replace("5000", "5002"),
                        self.LEGACY.replace("127.0.0.1", "127.0.0.2"),
                        self.LEGACY.replace("0x0/0x0", "0x1/0x1"),
                        self.LEGACY.replace("PREROUTING", "FOREIGN"),
                        "-P PREROUTING ACCEPT"):
            with self.subTest(rule=foreign):
                self.assertIsNone(firewall.legacy_rule(foreign))

    def test_legacy_cleanup_deletes_individual_rules_not_the_mangle_table(self):
        with patch.object(firewall, "run", side_effect=[
                '{"nftables":[{"table":{"family":"ip","name":"mangle"}}]}',
                self.LEGACY + "\n" + self.LEGACY.replace("5000", "5999"), ""]) as run:
            firewall.remove()
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args.args[:6],
                             ("iptables", "-w", "-t", "mangle", "-D", "PREROUTING"))

    def test_only_own_table_and_only_local_traffic_on_capture_interface(self):
        rules = firewall.rules({"services": {}}, "eth0")
        self.assertIn("flush table ip tpot_glutton", rules)
        self.assertNotIn("flush ruleset", rules)
        self.assertNotIn("queue", rules)
        self.assertNotIn("notrack", rules)
        for expression in ('iifname != "eth0" return', "fib daddr type != local return",
                           "socket transparent 0 return", "ct state established,related return"):
            self.assertLess(rules.index(expression), rules.index("tproxy ip"))
        for protocol in ("tcp", "udp"):
            self.assertLess(rules.index(f"{protocol} dport"), rules.index(f"meta l4proto {protocol} tproxy"))
        self.assertIn("127.0.0.1:5000 accept", rules)
        self.assertIn("127.0.0.1:5001 accept", rules)

    def test_interface_quoted_and_validated(self):
        self.assertIn('iifname != "eth\\"0"', firewall.rules({"services": {}}, 'eth"0'))
        for value in ("", "a" * 16, "eth\x000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                firewall.rules({"services": {}}, value)

    def test_remove_is_idempotent_and_leaves_other_tables(self):
        with patch.object(firewall, "run", return_value=json.dumps(
                {"nftables": [{"table": {"family": "ip", "name": "docker-bridges"}}]})) as run:
            firewall.remove()
            self.assertEqual(run.call_count, 1)
        with patch.object(firewall, "run", side_effect=[
                json.dumps({"nftables": [{"table": {"family": "ip", "name": firewall.TABLE}}]}),
                ""]) as run:
            firewall.remove()
            self.assertEqual(run.call_args.args, ("nft", "delete", "table", "ip", firewall.TABLE))

    def test_missing_kernel_support_is_visible_failure(self):
        with patch("sys.argv", ["glutton_rules.py", "set", "/compose.yml"]), \
             patch.object(firewall, "run", side_effect=[
                 '{"nftables": []}', '{"services": {}}', "eth0\n",
                 subprocess.CalledProcessError(1, ["nft"], stderr="Operation not supported")]), \
             patch("sys.stderr") as stderr:
            self.assertEqual(firewall.main(), 1)
            self.assertIn("Operation not supported", "".join(str(call) for call in stderr.write.call_args_list))


class EventIntegrationTest(unittest.TestCase):
    def test_decoded_frames_and_payload_stay_out_of_the_dynamic_index(self):
        template = json.loads((ROOT / "docker/elk/logstash/dist/tpot-template.json").read_text())
        properties = template["template"]["mappings"]["properties"]["glutton"]["properties"]
        self.assertFalse(properties["decoded"]["enabled"])
        self.assertFalse(properties["payload"]["index"])
        self.assertFalse(properties["tls"]["properties"]["clientHello"]["index"])
        self.assertEqual(properties["duration_ms"]["type"], "long")

    def test_dashboard_generation_is_stable_and_all_panels_resolve(self):
        spec = importlib.util.spec_from_file_location(
            "glutton_dashboard", ROOT / "docker/glutton/tools/build_dashboard.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        objects = [json.loads(line) for line in generator.EXPORT.read_text().splitlines()]
        self.assertEqual(generator.build(objects), objects)
        by_key = {(obj.get("type"), obj.get("id")): obj for obj in objects}
        dashboard = by_key[("dashboard", "83b11b50-e850-11e8-97df-bbc3de28ece0")]
        panels = json.loads(dashboard["attributes"]["panelsJSON"])
        for ref in dashboard["references"]:
            self.assertIn((ref["type"], ref["id"]), by_key)
        names = {ref["name"] for ref in dashboard["references"]}
        for panel in panels:
            self.assertIn(panel["panelIndex"] + ":" + panel["panelRefName"], names)
        covered = json.dumps([obj for obj in objects if obj.get("attributes", {}).get("title", "").startswith("Glutton")])
        for field in ("glutton.commands.keyword", "transport.keyword", "glutton.scanner.keyword",
                      "glutton.protocol_stack.keyword", "glutton.tls.ja4.keyword", "glutton.tls.ja3.keyword",
                      "glutton.payload_sha256.keyword", "glutton.duration_ms"):
            self.assertIn(field, covered)
        for first in panels:
            a = first["gridData"]
            for second in panels:
                if first is second:
                    continue
                b = second["gridData"]
                self.assertFalse(a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
                                 and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


if __name__ == "__main__":
    unittest.main()
