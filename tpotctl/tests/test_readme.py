"""The port table of the README (Required Ports) follows the compose files: every honeypot row
names the host ports of compose/standard.yml, a honeypot the standard edition does not run those of
the edition it belongs to (or of the catalogue, with the customizer only), and every other edition
that publishes other ports for it has a footnote that names that edition and those ports."""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
README = os.path.join(ROOT, "README.md")
COMPOSE = os.path.join(ROOT, "compose")

# the name of a honeypot in the table -> its services in the compose files
NAMES = {"ADBHoney": ["adbhoney"], "Beelzebub": ["beelzebub"], "CiscoASA": ["ciscoasa"],
         "CitrixHoneypot": ["citrixhoneypot"], "Conpot": "conpot_", "Cowrie": ["cowrie"], "Ddospot": ["ddospot"],
         "Dicompot": ["dicompot"], "Dionaea": ["dionaea"], "Elasticpot": ["elasticpot"], "Endlessh": ["endlessh"],
         "Galah": ["galah"], "Go-pot": ["go-pot"], "H0neytr4p": ["h0neytr4p"], "Hellpot": ["hellpot"],
         "Heralding": ["heralding"], "Honeyaml": ["honeyaml"], "qHoneypots": ["honeypots"], "IPPHoney": ["ipphoney"],
         "Log4Pot": ["log4pot"], "Mailoney": ["mailoney"], "Medpot": ["medpot"], "Miniprint": ["miniprint"],
         "Redishoneypot": ["redishoneypot"], "RDPHoneypot": ["rdphoneypot"], "SentryPeer": ["sentrypeer"],
         "Wordpot": ["wordpot"]}
# the editions in the order a honeypot outside the standard edition is listed with, and their names
EDITIONS = {"standard": "standard", "llm": "LLM", "tarpit": "Tarpit", "mini": "Mini", "mobile": "Mobile",
            "sensor": "Sensor", "mac_win": "Mac / Windows"}
# groups of the catalogue whose services the table lists
GROUPS = ("honeypots", "conpot", "llm")


def published(service):
    """{protocol: {host port}} of a service, without the ports bound to the loopback."""
    found = {}
    for entry in service.get("ports") or []:
        text = str(entry)
        protocol = "udp" if text.endswith("/udp") else "tcp"
        parts = text.split("/")[0].split(":")
        if len(parts) == 3 and parts[0] in ("127.0.0.1", "::1"):
            continue
        found.setdefault(protocol, set()).add(int(parts[-2] if len(parts) >= 2 else parts[0]))
    return found


def services_of(services, name):
    """The services of a table name in one compose file (Conpot has one per template)."""
    wanted = NAMES[name]
    return {n: s for n, s in services.items() if (n.startswith(wanted) if isinstance(wanted, str) else n in wanted)}


def merged(services, name):
    """The published ports of a table name in one compose file, None when it runs none of them."""
    hits = list(services_of(services, name).values())
    if not hits:
        return None
    total = {}
    for service in hits:
        for protocol, ports in published(service).items():
            total.setdefault(protocol, set()).update(ports)
    return total


class PortTableTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest("no PyYAML")
        cls.editions = {}
        for edition in EDITIONS:
            with open(os.path.join(COMPOSE, f"{edition}.yml"), encoding="utf-8") as handle:
                cls.editions[edition] = yaml.safe_load(handle).get("services") or {}
        with open(os.path.join(COMPOSE, "tpot_services.yml"), encoding="utf-8") as handle:
            cls.catalogue = yaml.safe_load(handle)["services"]
        with open(README, encoding="utf-8") as handle:
            text = handle.read()
        section = text[text.index("## Required Ports"):text.index("\n## ", text.index("## Required Ports") + 5)]
        cls.rows = {}
        for line in section.splitlines():
            match = re.match(r"^\|([^|]*)\|([^|]*)\|\s*incoming\s*\|\s*Honeypot: (\S+)([^|]*)\|$", line)
            if match:
                ports = [int(p) for p in match.group(1).split(",")]
                cls.rows.setdefault(match.group(3), []).append((match.group(2).strip(), ports, match.group(4)))
        cls.footnotes = dict(re.findall(r"^([¹²³⁴⁵⁶⁷⁸⁹]) (.*?)(?:<br>)?$", section, re.M))

    def reference(self, name):
        """(edition or None for the catalogue, its ports) a row of the table names."""
        for edition in EDITIONS:
            ports = merged(self.editions[edition], name)
            if ports is not None:
                return edition, ports
        return None, merged(self.catalogue, name)

    def test_every_honeypot_with_a_port_has_its_rows(self):
        listed = set()
        for service, entry in self.catalogue.items():
            if (entry.get("x-tpot") or {}).get("group") not in GROUPS:
                continue
            if any(published(edition.get(service) or {}) for edition in self.editions.values()) \
                    or published(entry):
                name = next((n for n, w in NAMES.items()
                             if (service.startswith(w) if isinstance(w, str) else service in w)), None)
                self.assertIsNotNone(name, f"{service}: no name in NAMES")
                listed.add(name)
        self.assertEqual(sorted(self.rows), sorted(listed))

    def test_the_rows_follow_the_compose_files(self):
        for name, rows in sorted(self.rows.items()):
            edition, ports = self.reference(name)
            with self.subTest(honeypot=name, edition=edition):
                expected = []
                if ports.get("tcp") and ports.get("tcp") == ports.get("udp"):
                    expected.append(("tcp/udp", sorted(ports["tcp"])))
                else:
                    expected += [(protocol, sorted(ports[protocol])) for protocol in ("tcp", "udp") if ports.get(protocol)]
                self.assertEqual(sorted((protocol, numbers) for protocol, numbers, _rest in rows), sorted(expected))
                for _protocol, numbers, rest in rows:
                    self.assertEqual(numbers, sorted(numbers), "ports in ascending order")
                    if edition is None:
                        self.assertIn("with the customizer only", rest)
                    elif edition != "standard":
                        self.assertIn(f"{EDITIONS[edition]} edition", rest)

    def test_other_editions_have_a_footnote(self):
        for name, rows in sorted(self.rows.items()):
            edition, _ports = self.reference(name)
            mine = services_of(self.editions[edition] if edition else self.catalogue, name)
            for other in EDITIONS:
                # an edition that runs fewer of the services (Conpot templates) is no difference, other
                # ports of a service both run are
                both = sorted(set(mine) & set(services_of(self.editions[other], name)))
                ports = {}
                theirs = {}
                for service in both:
                    for protocol, numbers in published(mine[service]).items():
                        ports.setdefault(protocol, set()).update(numbers)
                    for protocol, numbers in published(self.editions[other][service]).items():
                        theirs.setdefault(protocol, set()).update(numbers)
                if other == edition or not both or theirs == ports:
                    continue
                with self.subTest(honeypot=name, edition=other):
                    marks = {m for _p, _n, rest in rows for m in re.findall(r"[¹²³⁴⁵⁶⁷⁸⁹]", rest)}
                    notes = " ".join(self.footnotes.get(m, "") for m in sorted(marks))
                    self.assertIn(f"{EDITIONS[other]} edition", notes)
                    changed = set()
                    for protocol in ("tcp", "udp"):
                        changed |= ports.get(protocol, set()) ^ theirs.get(protocol, set())
                    for port in sorted(changed):
                        self.assertRegex(notes, rf"\b{port}\b")

    def test_every_footnote_is_used(self):
        marks = {m for rows in self.rows.values() for _p, _n, rest in rows for m in re.findall(r"[¹²³⁴⁵⁶⁷⁸⁹]", rest)}
        self.assertEqual(sorted(self.footnotes), sorted(marks))


if __name__ == "__main__":
    unittest.main()
