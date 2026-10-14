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

    def test_attack_map_brings_its_helpers(self):
        result = self.resolve("STANDARD", remove=["map_data", "map_redis"])
        self.assertTrue(errors(result, "dependency"))

    def test_required_cannot_be_removed(self):
        self.assertTrue(errors(self.resolve("STANDARD", remove=["tpotinit"]), "dependency"))

    def test_declared_conflict(self):
        self.assertEqual(errors(self.resolve("STANDARD", add=["glutton"]), "conflict"),
                         ["glutton and honeytrap cannot run at the same time"])

    def test_llm_honeypot_outside_its_edition_warns(self):
        result = self.resolve("STANDARD", add=["beelzebub"], ports={("beelzebub", 22, "tcp"): 2223})
        self.assertEqual(errors(result), [])
        texts = [f.text for f in result.warnings if f.kind == "edition"]
        self.assertEqual(len(texts), 1, texts)
        self.assertTrue("beelzebub belongs to the LLM edition" in texts[0], texts[0])
        self.assertTrue("BEELZEBUB_LLM_" in texts[0], texts[0])
        self.assertEqual([f for f in self.resolve("LLM").warnings if f.kind == "edition"], [])

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

    def test_ciscoasa_old_ike_override_is_warned_and_dropped(self):
        for override in (15500, None):
            result = self.resolve("STANDARD", ports={("ciscoasa", 5000, "udp"): override}, strict=False)
            self.assertEqual(errors(result), [])
            self.assertTrue(any("5000/udp, override left out" in f.text for f in result.warnings))
            self.assertNotIn(("ciscoasa", 5000, "udp"), result.selection.ports)
            self.assertEqual({(p.host, p.container, p.proto) for p in result.ports["ciscoasa"]},
                             {(500, 500, "udp"), (4500, 4500, "udp"), (8443, 8443, "tcp")})

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
        self.assertIn("# SentryPeer service", text)
        self.assertIn('#     - "4222:4222/udp"', text)               # commented option inside the block
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

    def test_a_comment_of_your_own_above_the_header_keeps_the_edition(self):
        """The first "# T-Pot:" line of the leading comment block names the edition (as update.sh)."""
        text = core.render(self.catalog, self.resolve("MINI", add=["citrixhoneypot"]))
        mine = "# e2e-marker\n\n" + text
        selection = core.parse_header(mine)
        self.assertEqual((selection.base, selection.add), ("MINI", ["citrixhoneypot"]))
        self.assertTrue(core.checksum_ok(mine))                      # the checksum covers the body only
        self.assertEqual(core.edition_of(mine.split("\n")), "CUSTOM")
        self.assertEqual(core.edition_of("# mine\n# T-Pot: STANDARD\nservices: {}\n".split("\n")), "STANDARD")
        # after the first line that is neither empty nor a comment it names nothing
        late = "services: {}\n# T-Pot: STANDARD\n"
        self.assertEqual(core.edition_of(late.split("\n")), "")
        self.assertIsNone(core.parse_header("networks: {}\n" + text))
        # the first "# T-Pot:" line counts, also when it names no edition
        self.assertEqual(core.edition_of("# T-Pot: the platform\n# T-Pot: STANDARD\n".split("\n")), "")
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "docker-compose.yml"), "w", encoding="utf-8") as out:
                out.write(mine)
            edition, selection = core.current_edition(repo)
            self.assertEqual((edition, selection.base), ("CUSTOM", "MINI"))
            self.assertEqual(core.load_compose(os.path.join(repo, "docker-compose.yml")).edition, "CUSTOM")

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


# The definitions of master's tpot_services.yml (24.04.1), as its customizer copied them.
MASTER_SERVICES = {
    "tpotinit": {
        "container_name": "tpotinit", "env_file": [".env"], "restart": "always", "stop_grace_period": "60s",
        "tmpfs": ["/tmp/etc:uid=2000,gid=2000", "/tmp/:uid=2000,gid=2000"], "network_mode": "host",
        "cap_add": ["NET_ADMIN"], "image": "${TPOT_REPO}/tpotinit:${TPOT_VERSION}",
        "pull_policy": "${TPOT_PULL_POLICY}",
        "volumes": ["${TPOT_DOCKER_COMPOSE}:/tmp/tpot/docker-compose.yml:ro",
                    "${TPOT_DATA_PATH}/blackhole:/etc/blackhole", "${TPOT_DATA_PATH}:/data",
                    "/var/run/docker.sock:/var/run/docker.sock:ro"]},
    "logstash": {
        "container_name": "logstash", "restart": "always",
        "depends_on": {"elasticsearch": {"condition": "service_healthy"}}, "networks": ["nginx_local"],
        "environment": ["LS_JAVA_OPTS=-Xms1024m -Xmx1024m", "TPOT_TYPE=${TPOT_TYPE:-HIVE}",
                        "TPOT_HIVE_USER=${TPOT_HIVE_USER}", "TPOT_HIVE_IP=${TPOT_HIVE_IP}",
                        "LS_SSL_VERIFICATION=${LS_SSL_VERIFICATION:-full}"],
        "ports": ["127.0.0.1:64305:64305"], "mem_limit": "2g", "image": "${TPOT_REPO}/logstash:${TPOT_VERSION}",
        "pull_policy": "${TPOT_PULL_POLICY}", "volumes": ["${TPOT_DATA_PATH}:/data"]},
    "cowrie": {
        "container_name": "cowrie", "restart": "always",
        "depends_on": {"tpotinit": {"condition": "service_healthy"}},
        "tmpfs": ["/tmp/cowrie:uid=2000,gid=2000", "/tmp/cowrie/data:uid=2000,gid=2000"],
        "networks": ["cowrie_local"], "ports": ["22:22", "23:23"], "image": "${TPOT_REPO}/cowrie:${TPOT_VERSION}",
        "pull_policy": "${TPOT_PULL_POLICY}", "read_only": True,
        "volumes": ["${TPOT_DATA_PATH}/cowrie/downloads:/home/cowrie/cowrie/dl",
                    "${TPOT_DATA_PATH}/cowrie/keys:/home/cowrie/cowrie/etc",
                    "${TPOT_DATA_PATH}/cowrie/log:/home/cowrie/cowrie/log",
                    "${TPOT_DATA_PATH}/cowrie/log/tty:/home/cowrie/cowrie/log/tty"]},
    "elasticsearch": {
        "container_name": "elasticsearch", "restart": "always",
        "depends_on": {"tpotinit": {"condition": "service_healthy"}}, "networks": ["nginx_local"],
        "environment": ["bootstrap.memory_lock=true", "ES_JAVA_OPTS=-Xms2048m -Xmx2048m", "ES_TMPDIR=/tmp"],
        "cap_add": ["IPC_LOCK"],
        "ulimits": {"memlock": {"soft": -1, "hard": -1}, "nofile": {"soft": 65536, "hard": 65536}},
        "mem_limit": "4g", "ports": ["127.0.0.1:64298:9200"],
        "image": "${TPOT_REPO}/elasticsearch:${TPOT_VERSION}", "pull_policy": "${TPOT_PULL_POLICY}",
        "volumes": ["${TPOT_DATA_PATH}:/data"]},
    "kibana": {
        "container_name": "kibana", "restart": "always",
        "depends_on": {"elasticsearch": {"condition": "service_healthy"}}, "networks": ["nginx_local"],
        "mem_limit": "1g", "ports": ["127.0.0.1:64296:5601"], "image": "${TPOT_REPO}/kibana:${TPOT_VERSION}",
        "pull_policy": "${TPOT_PULL_POLICY}"},
    "spiderfoot": {
        "container_name": "spiderfoot", "restart": "always",
        "depends_on": {"tpotinit": {"condition": "service_healthy"}}, "networks": ["nginx_local"],
        "ports": ["127.0.0.1:64303:8080"], "image": "${TPOT_REPO}/spiderfoot:${TPOT_VERSION}",
        "pull_policy": "${TPOT_PULL_POLICY}",
        "volumes": ["${TPOT_DATA_PATH}/spiderfoot:/home/spiderfoot/.spiderfoot"]},
}


def v1_file(names, change=None):
    """A docker-compose-custom.yml as master's customizer wrote it: its header, then yaml.dump."""
    services = {n: dict(MASTER_SERVICES[n]) for n in names}
    for name, values in (change or {}).items():
        services[name].update(values)
    networks = {}
    for definition in services.values():
        for network in definition.get("networks", []):
            networks[network] = None
    return "# T-Pot: CUSTOM EDITION\n# Generated on: 2025-03-01 12:00:00\n" + yaml.dump(
        {"networks": networks, "services": services}, default_flow_style=False, sort_keys=False, indent=2)


def own_file(catalog, names, extra=None, edition="STANDARD"):
    """A compose file of your own: the release's definitions plus services of your own."""
    services = {}
    for name in names:
        services[name] = {k: v for k, v in catalog.catalog.services[name].items() if k != "x-tpot"}
    services.update(extra or {})
    return f"# T-Pot: {edition}\n" + yaml.safe_dump(
        {"networks": {n: None for n in catalog.catalog.networks}, "services": services}, sort_keys=False)


class AdoptTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.catalog = core.Catalog()

    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, CUSTOMIZER] + list(args), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              env=dict(os.environ, PATH="/usr/bin:/bin"))   # no docker: no compose check
        return proc.returncode, proc.stdout, proc.stderr

    def write(self, tmp, text, name="old.yml"):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        return path

    def test_v1_file_with_a_removed_service_becomes_v2(self):
        names = ["tpotinit", "logstash", "elasticsearch", "kibana", "cowrie", "spiderfoot"]
        adoption = core.adopt(self.catalog, v1_file(names), "HIVE")
        self.assertTrue(adoption.v1)
        self.assertEqual(adoption.dropped, ["spiderfoot (removed in this release)"])
        self.assertEqual(adoption.selection.base, "STANDARD")
        self.assertEqual(adoption.selection.add, [])
        self.assertEqual(adoption.selection.ports, {})
        with tempfile.TemporaryDirectory() as tmp:
            old, new = self.write(tmp, v1_file(names)), os.path.join(tmp, "new.yml")
            code, out, err = self.run_cli("--adopt", old, "-o", new, "--report", "--tpot-type", "HIVE")
            self.assertEqual(code, 0, out + err)
            self.assertIn("dropped: spiderfoot (removed in this release)", out.splitlines())
            for line in out.splitlines():
                self.assertTrue(line.startswith(("dropped: ", "port: ", "kept-out: ")), line)
            with open(new, encoding="utf-8") as handle:
                text = handle.read()
            self.assertTrue(text.startswith("# T-Pot: CUSTOM\n# customizer: version=2 base=STANDARD\n"))
            self.assertTrue(core.checksum_ok(text))
            services = yaml.safe_load(text)["services"]
            for name in names[:-1]:
                self.assertIn(name, services)
            self.assertNotIn("spiderfoot", services)
            self.assertIn("nginx", services)                          # kibana requires it
            self.assertNotIn("dionaea", services)                     # not in the file: removed from the base
            code, out, err = self.run_cli("--rebuild", new)
            self.assertEqual(code, 0, out + err)
            with open(new, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), text)

    def test_changed_host_port_becomes_an_override(self):
        names = ["tpotinit", "logstash", "elasticsearch", "cowrie"]
        text = v1_file(names, {"cowrie": {"ports": ["2222:22", "23:23"]}})
        adoption = core.adopt(self.catalog, text, "HIVE")
        self.assertEqual(adoption.selection.ports, {("cowrie", 22, "tcp"): 2222})
        self.assertEqual([line for line in adoption.not_carried if "port" in line], [])
        with tempfile.TemporaryDirectory() as tmp:
            old, new = self.write(tmp, v1_file(names, {"cowrie": {"ports": ["2222:22", "23:23"]}})), \
                os.path.join(tmp, "new.yml")
            code, out, err = self.run_cli("--adopt", old, "-o", new, "--report", "--tpot-type", "HIVE")
            self.assertEqual(code, 0, out + err)
            self.assertIn("port: cowrie:22/tcp=2222", out.splitlines())
            with open(new, encoding="utf-8") as handle:
                rendered = handle.read()
            self.assertEqual(yaml.safe_load(rendered)["services"]["cowrie"]["ports"], ["2222:22", "23:23"])
            self.assertIn("# customizer: port=cowrie:22/tcp=2222\n", rendered)

    def test_a_release_port_missing_from_the_file_stays_published(self):
        # master's ciscoasa had no IKE ports 500 / 4500, a v1 cowrie without 23 is no choice either
        ciscoasa = {k: v for k, v in self.catalog.catalog.services["ciscoasa"].items() if k != "x-tpot"}
        ciscoasa["ports"] = ["8443:8443"]
        text = own_file(self.catalog, ["tpotinit", "logstash", "elasticsearch"], {"ciscoasa": ciscoasa})
        text = text.replace("# T-Pot: STANDARD", core.V1_EDITION_LINE, 1)
        adoption = core.adopt(self.catalog, text, "HIVE")
        self.assertEqual(adoption.selection.ports, {})
        self.assertEqual(adoption.not_carried, [])
        result = core.resolve(self.catalog, adoption.selection)
        rendered = yaml.safe_load(core.render(self.catalog, result))["services"]["ciscoasa"]["ports"]
        self.assertEqual(sorted(rendered), sorted(self.catalog.catalog.services["ciscoasa"]["ports"]))
        cowrie = core.adopt(self.catalog, v1_file(["tpotinit", "logstash", "elasticsearch", "cowrie"],
                                                  {"cowrie": {"ports": ["22:22"]}}), "HIVE")
        self.assertEqual(cowrie.selection.ports, {})

    def test_an_old_only_port_is_kept_out(self):
        ciscoasa = {k: v for k, v in self.catalog.catalog.services["ciscoasa"].items() if k != "x-tpot"}
        ciscoasa["ports"] = ["5000:5000/udp", "8443:8443"]                 # as master had them
        text = own_file(self.catalog, ["tpotinit", "logstash", "elasticsearch"], {"ciscoasa": ciscoasa})
        adoption = core.adopt(self.catalog, text, "HIVE")
        self.assertEqual(adoption.selection.ports, {})
        self.assertEqual(adoption.not_carried, ["ciscoasa: port 5000/udp (the release no longer has it)"])
        # a bind IP of the file cannot be an override, it is named
        cowrie = core.adopt(self.catalog, v1_file(["tpotinit", "logstash", "elasticsearch", "cowrie"],
                                                  {"cowrie": {"ports": ["127.0.0.1:2222:22", "23:23"]}}), "HIVE")
        self.assertEqual(cowrie.selection.ports, {("cowrie", 22, "tcp"): 2222})
        self.assertEqual(cowrie.not_carried,
                         ["cowrie: port 127.0.0.1:2222:22 (only the host port is carried, the release has 22:22)"])

    def test_adopt_never_drops_a_release_port(self):
        names = ["tpotinit", "logstash", "elasticsearch", "kibana", "cowrie"]
        for text in (v1_file(names, {"cowrie": {}}), v1_file(names, {"cowrie": {"ports": []},
                                                                    "kibana": {"ports": []}})):
            self.assertNotIn(None, core.adopt(self.catalog, text, "HIVE").selection.ports.values())

    def test_own_service_and_own_volume_are_listed_not_carried(self):
        cowrie = {k: v for k, v in self.catalog.catalog.services["cowrie"].items() if k != "x-tpot"}
        cowrie["volumes"] = cowrie["volumes"] + ["/srv/cowrie/etc:/home/cowrie/cowrie/etc/extra:ro"]
        cowrie["environment"] = cowrie["environment"] + ["COWRIE_X=1"]
        cowrie["networks"] = ["honeypot_local", "mynet"]
        mine = {"image": "nginx:alpine", "networks": ["mynet"], "ports": ["8081:80"]}
        text = own_file(self.catalog, ["tpotinit", "logstash", "elasticsearch"],
                        {"cowrie": cowrie, "mytool": mine})
        text = text.replace("networks:\n", "networks:\n  mynet: {}\n", 1)
        adoption = core.adopt(self.catalog, text, "HIVE")
        self.assertFalse(adoption.v1)
        self.assertEqual(adoption.dropped, ["mytool (not a T-Pot service)"])
        self.assertEqual(adoption.not_carried, [
            "cowrie: environment COWRIE_X=1",
            "cowrie: volume /srv/cowrie/etc:/home/cowrie/cowrie/etc/extra:ro",
            "cowrie: network mynet",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            old, new = self.write(tmp, text), os.path.join(tmp, "new.yml")
            code, out, err = self.run_cli("--adopt", old, "-o", new, "--dry-run", "--tpot-type", "HIVE")
            self.assertEqual(code, 0, out + err)
            self.assertEqual(out.splitlines(), ["dropped: mytool (not a T-Pot service)",
                                                "kept-out: cowrie: environment COWRIE_X=1",
                                                "kept-out: cowrie: volume /srv/cowrie/etc:/home/cowrie/cowrie/etc/extra:ro",
                                                "kept-out: cowrie: network mynet"])
            self.assertFalse(os.path.exists(new))                     # --dry-run writes nothing

    def test_old_kibana_mem_limit_is_named(self):
        adoption = core.adopt(self.catalog, v1_file(["tpotinit", "logstash", "elasticsearch", "kibana"]), "HIVE")
        self.assertEqual(adoption.not_carried, ["kibana: mem_limit 1g (the release has 2g)"])

    def test_old_network_of_a_service_and_a_new_default_are_no_loss(self):
        # cowrie_local became honeypot_local, ${X} became ${X:-default}: nothing to name
        cowrie = {k: v for k, v in self.catalog.catalog.services["cowrie"].items() if k != "x-tpot"}
        cowrie["networks"] = ["cowrie_local"]
        cowrie["environment"] = ["COWRIE_PERSONA=${COWRIE_PERSONA}"]
        adoption = core.adopt(self.catalog, own_file(self.catalog, ["tpotinit", "logstash", "elasticsearch"],
                                                     {"cowrie": cowrie}), "HIVE")
        self.assertEqual(adoption.not_carried, [])

    def test_other_keys_image_and_top_level_sections_are_named(self):
        cowrie = {k: v for k, v in self.catalog.catalog.services["cowrie"].items() if k != "x-tpot"}
        cowrie["image"] = "cowrie/cowrie:latest"
        cowrie["command"] = "cowrie start -n"
        cowrie["restart"] = "unless-stopped"
        cowrie["labels"] = {"me": "yes"}
        logstash = {k: v for k, v in self.catalog.catalog.services["logstash"].items() if k != "x-tpot"}
        logstash["image"] = "dtagdevsec/logstash:24.04.1"            # a T-Pot image with its tag: no loss
        text = own_file(self.catalog, ["tpotinit", "elasticsearch"], {"cowrie": cowrie, "logstash": logstash})
        text += "volumes:\n  mydata: {}\n"
        lines = core.adopt(self.catalog, text, "HIVE").not_carried
        self.assertEqual(lines, [
            "cowrie: image cowrie/cowrie:latest (the release has ${TPOT_REPO}/cowrie:${TPOT_VERSION})",
            "cowrie: command cowrie start -n (the release has none)",
            "cowrie: restart unless-stopped (the release has always)",
            "cowrie: labels {me: 'yes'} (the release has none)",
            "volumes: {mydata: {}}",
        ])

    def test_sensor_type_takes_the_sensor_base(self):
        text = v1_file(["tpotinit", "logstash", "cowrie", "kibana", "elasticsearch"])
        adoption = core.adopt(self.catalog, text, "SENSOR")
        self.assertEqual(adoption.selection.base, "SENSOR")
        self.assertIn("kibana (a SENSOR has no kibana)", adoption.dropped)
        self.assertIn("elasticsearch (a SENSOR has no elasticsearch)", adoption.dropped)
        result = core.resolve(self.catalog, adoption.selection)
        self.assertEqual(errors(result), [])
        self.assertIn("cowrie", result.services)
        self.assertNotIn("dionaea", result.services)
        self.assertEqual(core.adopt(self.catalog, text, "HIVE").selection.base, "STANDARD")

    def test_unreadable_file_exits_2_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = os.path.join(tmp, "new.yml")
            for text in ("services: [unclosed\n", "# T-Pot: CUSTOM EDITION\nnetworks: {}\n", "services: {}\n"):
                with self.subTest(text=text):
                    code, out, err = self.run_cli("--adopt", self.write(tmp, text), "-o", new, "--report")
                    self.assertEqual(code, 2, out + err)
                    self.assertEqual(out, "")
                    self.assertFalse(os.path.exists(new))
            code, out, err = self.run_cli("--adopt", os.path.join(tmp, "missing.yml"), "-o", new, "--report")
            self.assertEqual(code, 2, out + err)

    def test_conflict_exits_3_and_writes_nothing(self):
        text = own_file(self.catalog, ["tpotinit", "logstash", "elasticsearch", "glutton", "honeytrap"])
        with tempfile.TemporaryDirectory() as tmp:
            new = os.path.join(tmp, "new.yml")
            for extra in ((), ("--dry-run",)):
                code, out, err = self.run_cli("--adopt", self.write(tmp, text), "-o", new, "--report",
                                              "--tpot-type", "HIVE", *extra)
                self.assertEqual(code, 3, out + err)
                self.assertIn("glutton and honeytrap cannot run at the same time", err)
                self.assertFalse(os.path.exists(new))

    def test_dropped_services_match_update_sh(self):
        with open(os.path.join(core.REPO_DIR, "update.sh"), encoding="utf-8") as handle:
            line = next(line for line in handle if line.startswith("myDROPPED_SERVICES="))
        self.assertEqual(core.DROPPED_SERVICES, line.split("=", 1)[1].strip().strip('"').split())

    def test_earlier_networks_hold_those_of_this_release(self):
        for compose in [self.catalog.catalog] + list(self.catalog.editions.values()):
            for network in compose.networks:
                self.assertIn(network, core.EARLIER_NETWORKS, compose.path)


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
