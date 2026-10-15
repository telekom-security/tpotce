"""The normalisation and the hash manifest of T-Pot's Kibana objects."""
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

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

    def _field_attrs(self, change):
        obj = fixture("shipped_index_pattern.json")
        other = json.loads(json.dumps(obj))
        attrs = json.loads(other["attributes"]["fieldAttrs"])
        change(attrs)
        other["attributes"]["fieldAttrs"] = json.dumps(attrs)
        return obj, other

    def test_a_discover_count_is_no_change(self):
        def count(attrs):
            attrs["t-pot_hostname"]["count"] = 7
            attrs["dest_port"]["count"] = 2
            attrs["src_ip"] = {"count": 3}
        obj, other = self._field_attrs(count)
        self.assertEqual(ko.digest(obj), ko.digest(other))

    def test_a_changed_custom_label_is_a_change(self):
        def label(attrs):
            attrs["dest_port"]["customLabel"] = "Port"
        obj, other = self._field_attrs(label)
        self.assertNotEqual(ko.digest(obj), ko.digest(other))

    def test_a_new_custom_label_is_a_change(self):
        def label(attrs):
            attrs["src_ip"] = {"customLabel": "Source", "count": 1}
        obj, other = self._field_attrs(label)
        self.assertNotEqual(ko.digest(obj), ko.digest(other))

    def _tab_change(self, change):
        obj = fixture("exported_search_tabs.json")
        other = json.loads(json.dumps(obj))
        change(other["attributes"]["tabs"])
        self.assertNotEqual(ko.digest(obj), ko.digest(other))

    def test_a_change_in_a_search_tab_is_a_change(self):
        def columns(tabs):
            tabs[0]["attributes"]["columns"] = ["src_ip"]

        def query(tabs):
            meta = tabs[0]["attributes"]["kibanaSavedObjectMeta"]
            source = json.loads(meta["searchSourceJSON"])
            source["query"]["query"] = 'type:"Hellpot"'
            meta["searchSourceJSON"] = json.dumps(source)

        def label(tabs):
            tabs[0]["label"] = "Mine"

        def second_tab(tabs):
            tabs.append(json.loads(json.dumps(tabs[0])))
        for change in (columns, query, label, second_tab):
            with self.subTest(change.__name__):
                self._tab_change(change)

    def test_a_change_in_a_legacy_search_is_a_change(self):
        obj = fixture("shipped_search_legacy.json")
        other = json.loads(json.dumps(obj))
        other["attributes"]["columns"] = ["src_ip"]
        self.assertNotEqual(ko.digest(obj), ko.digest(other))

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

    def test_a_byte_order_mark_is_left_out(self):
        text = '﻿{"type":"tag","id":"t","attributes":{}}\r\n'
        self.assertEqual([o["id"] for o in ko.parse_ndjson(text, "x")], ["t"])


class ManifestTest(unittest.TestCase):
    def test_every_shipped_object_is_in_the_manifest(self):
        self.assertEqual(ko.check_manifest(), [])

    def test_the_shipped_file_leaves_out_the_advanced_settings(self):
        with open(ko.SHIPPED, encoding="utf-8") as handle:
            raw = {o["type"] for o in ko.parse_ndjson(handle.read(), ko.SHIPPED)}
        self.assertTrue(raw & ko.SKIP_TYPES, "the shipped file holds the advanced settings")
        self.assertFalse({o["type"] for o in ko.shipped()} & ko.SKIP_TYPES)

    def test_a_manifest_of_other_rules_is_refused(self):
        for rules in (ko.RULES + 1, True, float(ko.RULES), str(ko.RULES), None):
            with self.subTest(rules=rules):
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                    json.dump({"rules": rules, "objects": {}}, handle)
                self.addCleanup(os.unlink, handle.name)
                with self.assertRaises(ko.KibanaError):
                    ko.load_manifest(handle.name)

    def test_the_committed_manifest_is_the_one_of_the_history(self):
        problem = ko.history_problem(ko.REPO_DIR)
        if problem:
            self.skipTest(f"no git history to build the manifest from: {problem}")
        with open(ko.MANIFEST, encoding="utf-8") as handle:
            committed = handle.read()
        self.assertEqual(committed, ko.manifest_text(ko.build_manifest(ko.REPO_DIR)),
                         "run: python3 -m tpotctl.kibana_objects manifest")

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


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class HistoryTest(unittest.TestCase):
    """build_manifest on a small repository of its own."""

    NDJSON, ZIP = ko.HISTORY_PATHS

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")
        self.git("init", "-q", "-b", "main")
        os.makedirs(os.path.join(self.repo, os.path.dirname(self.NDJSON)))

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo, "-c", "commit.gpgsign=false", *args], env=self.env,
                       check=True, capture_output=True)

    @staticmethod
    def tag(color, name="t1"):
        return {"type": "tag", "id": name, "attributes": {"name": name, "color": color}, "references": []}

    def write_ndjson(self, *objects):
        with open(os.path.join(self.repo, self.NDJSON), "w", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(o) + "\n" for o in objects))
            handle.write('{"exportedCount":1,"missingRefCount":0}\n')
        self.git("add", self.NDJSON)

    def commit(self, message):
        self.git("commit", "-q", "-m", message)

    def test_every_version_counts_merges_included(self):
        config = {"type": "config", "id": "9.5.4", "attributes": {"defaultIndex": "x"}}
        v1, v2, v3, v4 = (self.tag(c) for c in ("#000001", "#000002", "#000003", "#000004"))
        zipped = self.tag("#0000aa", "t2")
        self.write_ndjson(v1, config)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("kibana_export.ndjson", json.dumps(zipped) + "\n")
            # the resource fork macOS puts into a zip: no ndjson at all
            handle.writestr("__MACOSX/._kibana_export.ndjson", b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X")
        with open(os.path.join(self.repo, self.ZIP), "wb") as handle:
            handle.write(archive.getvalue())
        self.git("add", self.ZIP)
        self.commit("v1 and the zip")
        self.git("checkout", "-q", "-b", "side")
        self.write_ndjson(v2)
        self.commit("v2 on a branch")
        self.git("checkout", "-q", "main")
        self.write_ndjson(v3)
        self.commit("v3")
        # a merge that brings a version of its own, which no parent has
        self.git("merge", "-q", "-s", "ours", "--no-commit", "side")
        self.write_ndjson(v4)
        self.commit("merge with v4")
        self.git("rm", "-q", self.NDJSON)
        self.commit("the file is gone")

        manifest = ko.build_manifest(self.repo)

        self.assertEqual(manifest["rules"], ko.RULES)
        self.assertEqual(set(manifest["objects"]), {"tag:t1", "tag:t2"})
        self.assertEqual(manifest["objects"]["tag:t1"], sorted(ko.digest(v) for v in (v1, v2, v3, v4)))
        self.assertEqual(manifest["objects"]["tag:t2"], [ko.digest(zipped)])

    def test_the_working_tree_does_not_count(self):
        self.write_ndjson(self.tag("#000001"))
        self.commit("v1")
        self.write_ndjson(self.tag("#0000ff"))
        self.assertEqual(ko.build_manifest(self.repo)["objects"]["tag:t1"], [ko.digest(self.tag("#000001"))])

    def test_without_a_history_there_is_no_manifest(self):
        self.assertTrue(ko.history_problem(self.repo))  # no commit yet
        with self.assertRaises(ko.KibanaError):
            ko.build_manifest(self.repo)
        self.write_ndjson(self.tag("#000001"))
        self.commit("v1")
        self.write_ndjson(self.tag("#000002"))
        self.commit("v2")
        self.assertIsNone(ko.history_problem(self.repo))
        shallow = os.path.join(self.repo, "shallow")
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.repo}", shallow], env=self.env,
                       check=True, capture_output=True)
        self.assertIn("shallow", ko.history_problem(shallow))
        with self.assertRaisesRegex(ko.KibanaError, "shallow"):
            ko.build_manifest(shallow)


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
