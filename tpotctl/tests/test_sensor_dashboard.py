"""Generated sensor views: references, clock choices and unrelated-object preservation."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "docker/tpotinit/dist/etc/objects/kibana_export.ndjson"


class SensorDashboardTest(unittest.TestCase):
    def build(self):
        self.assertIsNotNone(importlib.util.find_spec("tpotctl.sensor_dashboard"),
                             "sensor dashboard generator is missing")
        from tpotctl import sensor_dashboard
        original = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines()]
        before = copy.deepcopy(original)
        built = sensor_dashboard.build(original)
        self.assertEqual(original, before, "generation must not mutate its input")
        return sensor_dashboard, original, built

    def test_generation_is_idempotent_and_preserves_other_dashboards(self):
        module, original, built = self.build()
        self.assertEqual(module.build(built), built)
        by_key = {(obj.get("type"), obj.get("id")): obj for obj in built}
        for obj in original:
            if obj.get("id") and not obj["id"].startswith("tpot-sensors-"):
                self.assertEqual(by_key[obj["type"], obj["id"]], obj)
        self.assertEqual(built[-1]["exportedCount"], len(built) - 1)

    def test_shipped_export_is_generated_and_zip_matches(self):
        _, original, built = self.build()
        self.assertTrue(original == built, "run python3 -m tpotctl.sensor_dashboard")
        with zipfile.ZipFile(str(EXPORT) + ".zip") as archive:
            self.assertTrue(archive.read(EXPORT.name) == EXPORT.read_bytes(), "export zip is stale")

    def test_dashboard_has_all_five_views_and_resolvable_panel_references(self):
        _, _, built = self.build()
        by_key = {(obj.get("type"), obj.get("id")): obj for obj in built}
        dashboard = by_key["dashboard", "tpot-sensors-dashboard"]
        self.assertEqual(dashboard["attributes"]["title"], "T-Pot Sensors")
        panels = json.loads(dashboard["attributes"]["panelsJSON"])
        refs = {ref["name"]: ref for ref in dashboard["references"]}
        panel_ids = set()
        for panel in panels:
            reference = refs[panel["panelIndex"] + ":" + panel["panelRefName"]]
            self.assertIn((reference["type"], reference["id"]), by_key)
            panel_ids.add(reference["id"])
        self.assertTrue({"tpot-sensors-status", "tpot-sensors-timeline", "tpot-sensors-versions",
                         "tpot-sensors-starts", "tpot-sensors-attacks"} <= panel_ids)
        for obj in built:
            if str(obj.get("id", "")).startswith("tpot-sensors-"):
                for ref in obj.get("references", []):
                    self.assertIn((ref["type"], ref["id"]), by_key)

    def test_current_status_is_not_filtered_by_the_dashboard_time_window(self):
        _, _, built = self.build()
        by_key = {(obj.get("type"), obj.get("id")): obj for obj in built}
        view = by_key["index-pattern", "tpot-sensors-status-view"]["attributes"]
        self.assertEqual(view["title"], "tpot-sensor-status")
        self.assertFalse(view.get("timeFieldName"))
        self.assertIn("heartbeat.state", json.loads(view["runtimeFieldMap"]))
        search = by_key["search", "tpot-sensors-status"]["attributes"]
        self.assertTrue({"t-pot_sensor", "heartbeat.state", "heartbeat.version", "heartbeat.received_at"} <=
                        set(search["columns"]))
        self.assertEqual(search["tabs"][0]["attributes"]["columns"], search["columns"])

    def test_history_uses_sensor_clock_starts_use_start_clock_and_attacks_stay_separate(self):
        _, _, built = self.build()
        by_key = {(obj.get("type"), obj.get("id")): obj for obj in built}
        for name, clock in (("history", "@timestamp"), ("starts", "heartbeat.started_at")):
            view = by_key["index-pattern", f"tpot-sensors-{name}-view"]["attributes"]
            self.assertEqual(view["title"], "tpot-sensor-heartbeat-*")
            self.assertEqual(view["timeFieldName"], clock)
        for name, view_id, field in (
                ("timeline", "tpot-sensors-history-view", "@timestamp"),
                ("versions", "tpot-sensors-history-view", "heartbeat.sensor_version"),
                ("starts", "tpot-sensors-starts-view", "heartbeat.started_at"),
                ("attacks", "logstash-*", "t-pot_sensor.keyword")):
            lens = by_key["lens", f"tpot-sensors-{name}"]
            layers = lens["attributes"]["state"]["datasourceStates"]["formBased"]["layers"]
            self.assertTrue(any(col.get("sourceField") == field
                                for layer in layers.values() for col in layer["columns"].values()))
            self.assertEqual({ref["id"] for ref in lens["references"] if ref["type"] == "index-pattern"}, {view_id})
        starts = by_key["lens", "tpot-sensors-starts"]["attributes"]["state"]["datasourceStates"]["formBased"]["layers"]
        self.assertTrue(any(col.get("operationType") == "unique_count" and
                            col.get("sourceField") == "heartbeat.started_at"
                            for layer in starts.values() for col in layer["columns"].values()),
                        "repeated samples of a process must not become repeated restarts")
