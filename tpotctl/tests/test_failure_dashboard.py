"""Generated failure objects: the data view and saved search of tpot-failures-*, every other line untouched."""

import importlib.util
import json
from pathlib import Path
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "docker/tpotinit/dist/etc/objects/kibana_export.ndjson"
IDS = {("index-pattern", "tpot-failures-view"), ("search", "tpot-failures-search")}


def key(line: bytes):
    obj = json.loads(line)
    return obj.get("type"), obj.get("id")


class FailureDashboardTest(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("tpotctl.failure_dashboard"),
                             "failure dashboard generator is missing")
        from tpotctl import failure_dashboard
        return failure_dashboard

    def objects(self, export: bytes) -> dict:
        return {key(line): json.loads(line) for line in export.splitlines()}

    def test_shipped_export_is_generated_and_check_passes(self):
        module = self.module()
        export = EXPORT.read_bytes()
        self.assertTrue(module.build(export) == export, "run python3 -m tpotctl.failure_dashboard")
        with zipfile.ZipFile(str(EXPORT) + ".zip") as archive:
            self.assertTrue(archive.read(EXPORT.name) == export, "export zip is stale")
        self.assertEqual(module.main(["--check"]), 0)

    def test_the_objects_of_the_failure_index(self):
        built = self.objects(self.module().build(EXPORT.read_bytes()))
        view = built["index-pattern", "tpot-failures-view"]["attributes"]
        self.assertEqual((view["title"], view["timeFieldName"]), ("tpot-failures-*", "@timestamp"))
        search = built["search", "tpot-failures-search"]
        attrs = search["attributes"]
        self.assertEqual(attrs["title"], "T-Pot Failures")
        columns = ["type", "t-pot_sensor", "t-pot_log_path", "failure.stage", "failure.reason", "failure.original"]
        self.assertEqual(attrs["columns"], columns)
        self.assertEqual(attrs["sort"], [["@timestamp", "desc"]])
        self.assertEqual(attrs["tabs"][0]["attributes"]["columns"], columns)
        source = json.loads(attrs["kibanaSavedObjectMeta"]["searchSourceJSON"])
        self.assertEqual(search["references"], [{"id": "tpot-failures-view", "type": "index-pattern",
                                                 "name": source["indexRefName"]}])

    def test_generation_changes_only_the_failure_objects(self):
        module = self.module()
        export = EXPORT.read_bytes()
        others = [line for line in export.splitlines()
                  if key(line) not in IDS and b"exportedCount" not in line]
        stripped = b"".join(line + b"\n" for line in export.splitlines() if key(line) not in IDS)
        rebuilt = module.build(stripped)
        self.assertFalse(rebuilt == stripped, "missing objects must be detected as stale")
        self.assertEqual(module.build(rebuilt), rebuilt)
        lines = rebuilt.splitlines()
        self.assertEqual([line for line in lines if key(line) not in IDS and b"exportedCount" not in line], others)
        self.assertEqual({key(line) for line in lines[-3:-1]}, IDS, "new objects go at the end")
        self.assertEqual(json.loads(lines[-1])["exportedCount"], len(lines) - 1)
        # an outdated object is replaced in place
        changed = export.replace(b'"title":"T-Pot Failures"', b'"title":"Old"')
        self.assertFalse(changed == export)
        rebuilt = module.build(changed)
        self.assertTrue(rebuilt == export)

    def test_sensor_dashboard_keeps_the_failure_objects(self):
        from tpotctl import sensor_dashboard
        objects = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines()]
        built = {(obj.get("type"), obj.get("id")): obj for obj in sensor_dashboard.build(objects)}
        shipped = self.objects(EXPORT.read_bytes())
        for ident in IDS:
            self.assertEqual(built[ident], shipped[ident])
