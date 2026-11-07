"""Dataset boundaries and reference integrity of the shipped honeypot dashboards."""
import json
from pathlib import Path
import re
import unittest
import zipfile


EXPORT = Path(__file__).resolve().parents[2] / "docker/tpotinit/dist/etc/objects/kibana_export.ndjson"
DDOSPOT = "52156230-3b23-11ec-ad33-ebfbdce6bb77"
DDOSPOT_VICTIMS = "2b81a7de-a02d-5351-b928-7f8f4bfd0903"
P0F = "a4b2c6d8-3e1f-4a5b-9c7d-8e0f2a4b6c19"


def read_objects():
    return {(obj["type"], obj["id"]): obj for line in EXPORT.read_text().splitlines()
            if "id" in (obj := json.loads(line))}


def compact_query(query):
    return re.sub(r"\s+", "", query.replace(".keyword", "").replace('"', ""))


class DdospotDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.objects = read_objects()
        cls.dashboard = cls.objects["dashboard", DDOSPOT]
        cls.panels = json.loads(cls.dashboard["attributes"]["panelsJSON"])

    def referenced(self, panel):
        name = panel["panelIndex"] + ":" + panel["panelRefName"]
        ref = next(ref for ref in self.dashboard["references"] if ref["name"] == name)
        return self.objects[ref["type"], ref["id"]]

    def attributes(self, panel):
        embedded = panel["embeddableConfig"].get("attributes")
        return embedded if embedded is not None else self.referenced(panel)["attributes"]

    def test_one_dashboard_keeps_the_existing_main_identity(self):
        dashboards = [obj["id"] for obj in self.objects.values()
                      if obj["type"] == "dashboard"
                      and obj["attributes"]["title"].startswith("Ddospot")]
        self.assertEqual(dashboards, [DDOSPOT])
        self.assertNotIn(("dashboard", DDOSPOT_VICTIMS), self.objects)

    def test_global_query_allows_attackers_and_victims(self):
        source = json.loads(self.dashboard["attributes"]["kibanaSavedObjectMeta"]["searchSourceJSON"])
        self.assertEqual(source["query"]["language"], "kuery")
        self.assertEqual(compact_query(source["query"]["query"]), "type:(DdospotorDdospotTarget)")
        self.assertEqual(source["filter"], [])

    def test_attacker_lenses_have_their_own_scope(self):
        attackers = [self.attributes(panel) for panel in self.panels if panel["type"] == "lens"
                     and "DdospotTarget" not in self.attributes(panel)["state"]["query"]["query"]]
        self.assertTrue(attackers)
        for attrs in attackers:
            with self.subTest(title=attrs["title"]):
                self.assertEqual(attrs["state"]["query"]["language"], "kuery")
                self.assertEqual(compact_query(attrs["state"]["query"]["query"]), "type:Ddospot")

    def test_map_scope_does_not_ignore_user_query_or_time(self):
        maps = [self.attributes(panel) for panel in self.panels if panel["type"] == "map"]
        self.assertTrue(maps)
        for attrs in maps:
            layers = [layer for layer in json.loads(attrs["layerListJSON"])
                      if layer["sourceDescriptor"]["type"] == "ES_GEO_GRID"]
            self.assertTrue(layers)
            for layer in layers:
                self.assertEqual(layer.get("query"), {"language": "kuery", "query": "type.keyword : Ddospot"})
                self.assertTrue(layer["sourceDescriptor"]["applyGlobalQuery"])
                self.assertTrue(layer["sourceDescriptor"]["applyGlobalTime"])

    def test_victim_panels_keep_their_distinct_event_semantics(self):
        expected = {
            "5d5c5915-065c-5932-b7ca-ed73165f98f1": "attack_start",
            "9b1cb8ed-3806-54a5-b14a-c6079e5df09a": "attack_start",
            "b5769600-a85b-5b6c-919c-3c3689a0de1d": "attack_start",
            "63ef4bd4-41a1-5953-b5fd-55e6a2daa66d": "attack_start",
            "039a16fa-b339-5ea0-9958-ac4af7c900c4": "attack_start",
            "8d98e254-7f69-5b0d-adac-58e52a292866": "attack_start",
            "23c39cce-33cd-50c3-936d-5295ec918ffd": "attack_end",
            "e6a3fc67-0116-585b-b90a-385fd3a3b9c2": "attack_end",
            "d0884e2b-3e52-58e2-ad65-6ffe7def4868": "net_end",
        }
        targets = {self.referenced(panel)["id"]: panel for panel in self.panels
                   if "panelRefName" in panel}
        self.assertTrue(expected.keys() <= targets.keys())
        attacker_bottom = max(panel["gridData"]["y"] + panel["gridData"]["h"]
                              for panel in self.panels if panel["type"] != "markdown"
                              and ("panelRefName" not in panel or self.referenced(panel)["id"] not in expected))
        for ident, event in expected.items():
            with self.subTest(id=ident):
                query = self.attributes(targets[ident])["state"]["query"]
                self.assertEqual(compact_query(query["query"]), "type:DdospotTargetandevent:" + event)
                self.assertGreaterEqual(targets[ident]["gridData"]["y"], attacker_bottom)

    def test_scoped_panel_objects_are_not_shared_with_other_dashboards(self):
        target_ids = {ref["id"] for ref in self.dashboard["references"] if ref["type"] in ("lens", "map")}
        for obj in self.objects.values():
            if obj["type"] == "dashboard" and obj["id"] not in (DDOSPOT, DDOSPOT_VICTIMS):
                shared = target_ids & {ref["id"] for ref in obj["references"]}
                self.assertFalse(shared, (obj["attributes"]["title"], shared))

    def test_references_resolve_and_panel_positions_do_not_overlap(self):
        for ref in self.dashboard["references"]:
            self.assertIn((ref["type"], ref["id"]), self.objects)
        for index, panel in enumerate(self.panels):
            first = panel["gridData"]
            for other in self.panels[index + 1:]:
                second = other["gridData"]
                overlap = (first["x"] < second["x"] + second["w"]
                           and second["x"] < first["x"] + first["w"]
                           and first["y"] < second["y"] + second["h"]
                           and second["y"] < first["y"] + first["h"])
                self.assertFalse(overlap, (panel["panelIndex"], other["panelIndex"]))
        with zipfile.ZipFile(str(EXPORT) + ".zip") as archive:
            self.assertEqual(archive.read(EXPORT.name), EXPORT.read_bytes())


class P0fDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.objects = read_objects()

    def test_every_mismatch_view_requires_present_high_confidence(self):
        def queries(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "query" and isinstance(item, str):
                        yield item
                    else:
                        yield from queries(item)
            elif isinstance(value, list):
                for item in value:
                    yield from queries(item)

        for ident in ("5c0f7a3e-2b1d-4e8a-9f61-0a7d3c2e9b14", "8e2d4b91-6c3a-4f7e-b5d2-1f9a0c8e7d63",
                      "b3a91c5d-7e2f-4d68-8a1b-2c6e9f0d4a75", "d7c4e2a9-1b5f-4c3e-9d8a-6f2b0e1c5a86"):
            attrs = self.objects["lens", ident]["attributes"]
            matching = [compact_query(query) for query in queries(attrs["state"]) if "ua_os_mismatch" in query]
            with self.subTest(title=attrs["title"]):
                self.assertTrue(matching)
                for query in matching:
                    self.assertIn("ua_os_mismatch:true", query)
                    self.assertIn("os_confidence>=0.9", query)
                    self.assertNotIn("notos_confidence<", query)
                    self.assertIn("nothttp_proxy:true", query)

    def test_syn_coverage_includes_low_and_missing_confidence(self):
        dashboard = self.objects["dashboard", P0F]
        self.assertIn("tpot-p0f-syn-coverage", {ref["id"] for ref in dashboard["references"]})
        attrs = self.objects["lens", "tpot-p0f-syn-coverage"]["attributes"]
        self.assertEqual(compact_query(attrs["state"]["query"]["query"]), "type:P0fandmod:syn")
        filters = [item["input"]["query"]
                   for layer in attrs["state"]["datasourceStates"]["formBased"]["layers"].values()
                   for column in layer["columns"].values()
                   for item in column.get("params", {}).get("filters", [])]
        self.assertEqual({compact_query(query) for query in filters}, {
            "os_confidence>=0.9andnotos_family:Scanner",
            "os_confidence<0.9andnotos_family:Scanner",
            "notos_confidence:*andnotos_family:Scanner",
            "os_family:Scanner",
        })


if __name__ == "__main__":
    unittest.main()
