"""The normalisation and the hash manifest of T-Pot's Kibana objects."""
import ast
import json
import os
import sys
import tempfile
import unittest

from tpotctl import kibana_objects as ko

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "kibana")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as handle:
        return json.load(handle)


class NormaliseTest(unittest.TestCase):
    def test_a_legacy_search_matches_its_tabs_form_of_kibana_9_5(self):
        self.assertEqual(ko.digest(fixture("shipped_search_legacy.json")),
                         ko.digest(fixture("exported_search_tabs.json")))

    def test_a_shipped_search_with_tabs_and_old_fields_matches_its_export(self):
        self.assertEqual(ko.digest(fixture("shipped_search_dev.json")),
                         ko.digest(fixture("exported_search_live.json")))

    def test_sort_is_ignored(self):
        self.assertEqual(ko.digest(fixture("shipped_tag.json")), ko.digest(fixture("exported_tag.json")))

    def test_a_changed_field_format_is_a_change(self):
        self.assertNotEqual(ko.digest(fixture("shipped_index_pattern.json")),
                            ko.digest(fixture("exported_index_pattern_fieldformat.json")))

    def test_empty_field_attrs_entries_mean_nothing(self):
        obj = fixture("shipped_index_pattern.json")
        attrs = json.loads(obj["attributes"].get("fieldAttrs") or "{}")
        attrs["some_field"] = {}
        changed = json.loads(json.dumps(obj))
        changed["attributes"]["fieldAttrs"] = json.dumps(attrs)
        self.assertEqual(ko.digest(obj), ko.digest(changed))

    def test_meta_fields_and_reference_order_are_ignored(self):
        obj = {"type": "dashboard", "id": "x", "attributes": {"title": "A", "panelsJSON": "[{\"a\":1,\"b\":2}]"},
               "references": [{"type": "b", "id": "2", "name": "n2"}, {"type": "a", "id": "1", "name": "n1"}]}
        other = json.loads(json.dumps(obj))
        other.update({"updated_at": "2026-01-01T00:00:00Z", "version": "WzEsMV0=", "managed": False,
                      "typeMigrationVersion": "10.3.0", "coreMigrationVersion": "9.5.4", "namespaces": ["default"]})
        other["references"].reverse()
        other["attributes"]["panelsJSON"] = "[{\"b\":2,\"a\":1}]"
        self.assertEqual(ko.digest(obj), ko.digest(other))

    def test_a_changed_title_is_a_change(self):
        obj = {"type": "dashboard", "id": "x", "attributes": {"title": "A"}, "references": []}
        other = json.loads(json.dumps(obj))
        other["attributes"]["title"] = "B"
        self.assertNotEqual(ko.digest(obj), ko.digest(other))

    def test_a_string_that_only_looks_like_json_stays_a_string(self):
        obj = {"type": "dashboard", "id": "x", "attributes": {"description": "[not json"}, "references": []}
        self.assertEqual(ko.normalise(obj)["attributes"]["description"], "[not json")

    def test_normalise_leaves_its_argument_alone(self):
        obj = fixture("shipped_search_legacy.json")
        before = json.dumps(obj, sort_keys=True)
        ko.normalise(obj)
        self.assertEqual(json.dumps(obj, sort_keys=True), before)


class NdjsonTest(unittest.TestCase):
    def test_the_export_details_line_and_empty_lines_are_skipped(self):
        text = '{"type":"tag","id":"t","attributes":{}}\n\n{"exportedCount":1,"missingRefCount":0}\n'
        self.assertEqual([o["id"] for o in ko.parse_ndjson(text, "x")], ["t"])

    def test_a_line_that_is_no_object_is_named(self):
        with self.assertRaisesRegex(ko.KibanaError, "x, line 2"):
            ko.parse_ndjson('{"type":"tag","id":"t"}\nnot json\n', "x")

    def test_a_line_without_type_or_id_is_named(self):
        with self.assertRaisesRegex(ko.KibanaError, "line 1"):
            ko.parse_ndjson('{"type":"tag"}\n', "x")


class ManifestTest(unittest.TestCase):
    def test_every_shipped_object_is_in_the_manifest(self):
        self.assertEqual(ko.check_manifest(), [])

    def test_the_shipped_file_leaves_out_the_advanced_settings(self):
        self.assertFalse({o["type"] for o in ko.shipped()} & ko.SKIP_TYPES)

    def test_a_manifest_of_other_rules_is_refused(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"rules": ko.RULES + 1, "objects": {}}, handle)
        self.addCleanup(os.unlink, handle.name)
        with self.assertRaises(ko.KibanaError):
            ko.load_manifest(handle.name)

    def test_the_manifest_is_sorted_and_ends_with_a_newline(self):
        with open(ko.MANIFEST, encoding="utf-8") as handle:
            text = handle.read()
        data = json.loads(text)
        self.assertEqual(data["rules"], ko.RULES)
        self.assertEqual(list(data["objects"]), sorted(data["objects"]))
        for hashes in data["objects"].values():
            self.assertEqual(hashes, sorted(set(hashes)))
        self.assertEqual(text, json.dumps(data, indent=1, sort_keys=True) + "\n")
        self.assertFalse({k.split(":", 1)[0] for k in data["objects"]} & ko.SKIP_TYPES)


class StandaloneTest(unittest.TestCase):
    """tpotinit runs a copy of the module with a plain python3, outside the checkout."""

    def test_module_has_no_tpotctl_import(self):
        with open(ko.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "relative import")
                names.append(node.module or "")
        self.assertFalse([n for n in names if n.split(".")[0] == "tpotctl"], names)
        if hasattr(sys, "stdlib_module_names"):
            self.assertFalse([n for n in names if n.split(".")[0] not in sys.stdlib_module_names], names)


if __name__ == "__main__":
    unittest.main()
