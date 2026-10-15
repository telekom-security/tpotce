"""The normalisation, the hash manifest, the client, the plan and the record of T-Pot's Kibana objects."""
import ast
import contextlib
import errno
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
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


# ---------------------------------------------------------------------------
# the client, the plan, the modes and the record
# ---------------------------------------------------------------------------

NOW = 1_800_000_000.0
DAY = 86400.0


def iso(moment):
    return ko.stamp(moment)


def dash(ident, title=None, body=1):
    return {"type": "dashboard", "id": ident, "references": [],
            "attributes": {"title": title or f"Dashboard {ident}", "panelsJSON": json.dumps([{"v": body}])}}


CONFIG = {"type": "config", "id": "9.5.4", "attributes": {"defaultIndex": "logstash"}, "references": []}


def as_in_kibana(obj, updated=NOW - DAY):
    """`obj` as Kibana exports it: with its times, version and migration fields."""
    out = json.loads(json.dumps(obj))
    out.update({"updated_at": iso(updated), "created_at": iso(updated), "version": "WzEsMV0=",
                "namespaces": ["default"], "managed": False, "coreMigrationVersion": "9.5.4",
                "typeMigrationVersion": "10.3.0"})
    return out


class FakeKibana:
    """urlopen for tests that does what Kibana 9.5.4 did in the VM (fixtures/kibana/import_answers.json):
    an import with one error in it writes nothing, and with overwrite=false an object that is
    there is a conflict. `hidden` objects are there but left out of the export."""

    def __init__(self, objects=(), fail=(), hidden=(), export_status=200, import_status=200, down=False,
                 clock=None, on_import=None, lose=0, lose_written=True):
        self.store = {ko.key(o): json.loads(json.dumps(o)) for o in objects}
        self.fail, self.hidden = set(fail), set(hidden)
        # the next `lose` imports time out: after Kibana wrote them (lose_written), or before
        self.lose, self.lose_written = lose, lose_written
        self.export_status, self.import_status, self.down = export_status, import_status, down
        self.clock = clock or (lambda: NOW)
        self.on_import = on_import
        self.requests = []

    def imports(self):
        return [r for r in self.requests if "/_import" in r.full_url]

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.down:
            raise urllib.error.URLError("connection refused")
        path = request.full_url.split("64296", 1)[1]
        if path.startswith("/api/saved_objects/_export"):
            if self.export_status != 200:
                raise self.http_error(request, self.export_status)
            text = "".join(json.dumps(o) + "\n" for k, o in self.store.items() if k not in self.hidden)
            return io.BytesIO(text.encode())
        if path.startswith("/api/saved_objects/_import"):
            if self.on_import:
                self.on_import(request)
            if self.import_status != 200:
                raise self.http_error(request, self.import_status)
            if self.lose and not self.lose_written:
                self.lose -= 1
                raise TimeoutError("timed out")
            answer = self.answer(request, "overwrite=true" in path)
            if self.lose and answer["success"]:
                self.lose -= 1
                raise TimeoutError("timed out")           # written, but the answer never comes
            return io.BytesIO(json.dumps(answer).encode())
        raise urllib.error.URLError("no route")

    @staticmethod
    def http_error(request, status):
        body = json.dumps({"statusCode": status, "error": "Service Unavailable",
                           "message": "Kibana server is not ready yet " + "x" * 300}).encode()
        return urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(body))

    @staticmethod
    def ndjson(request):
        boundary = request.get_header("Content-type").split("boundary=", 1)[1]
        body = request.data.decode("utf-8")
        part = body.split(f"--{boundary}\r\n", 1)[1].split(f"\r\n--{boundary}--\r\n", 1)[0]
        return part.split("\r\n\r\n", 1)[1]

    def answer(self, request, overwrite):
        objs = ko.parse_ndjson(self.ndjson(request), "the upload")
        errors = []
        for obj in objs:
            name = ko.key(obj)
            if name in self.fail:
                errors.append({"type": obj["type"], "id": obj["id"], "meta": {"title": ko._title(obj)},
                               "error": {"type": "missing_references"}})
            elif not overwrite and name in self.store:
                errors.append({"type": obj["type"], "id": obj["id"], "meta": {"icon": "tag"},
                               "error": {"type": "conflict"}})
        refused = {f"{e['type']}:{e['id']}" for e in errors}
        good = [o for o in objs if ko.key(o) not in refused]
        answer = {"successCount": len(good), "success": not errors, "warnings": []}
        if good:
            answer["successResults"] = [{"type": o["type"], "id": o["id"], "meta": {"title": ko._title(o)},
                                         "managed": False} for o in good]
        if errors:
            answer["errors"] = errors
            return answer                       # nothing is written
        for obj in good:
            self.store[ko.key(obj)] = as_in_kibana(obj, self.clock() + 0.2)
        return answer


class Scripted:
    """urlopen that answers the imports with the given answers, in their order."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        return io.BytesIO(json.dumps(self.answers.pop(0)).encode())


class Site:
    """A data folder, a home and an objects folder with the shipped file (and the advanced
    settings in it) and its manifest, which also knows the `old` versions."""

    def __init__(self, test, objects, old=()):
        self.root = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.root)
        self.data, self.objects = os.path.join(self.root, "data"), os.path.join(self.root, "objects")
        self.home = os.path.join(self.root, "home")
        self.exports = os.path.join(self.home, "tpot_backups")
        for folder in (self.data, self.objects, self.home):
            os.makedirs(folder)
        with open(os.path.join(self.objects, ko.SHIPPED_NAME), "w", encoding="utf-8") as handle:
            for obj in [CONFIG, *objects]:
                handle.write(json.dumps(obj) + "\n")
            handle.write('{"exportedCount":%d,"missingRefCount":0,"missingReferences":[]}\n' % (len(objects) + 1))
        hashes = {}
        for obj in [*objects, *old]:
            hashes.setdefault(ko.key(obj), set()).add(ko.digest(obj))
        with open(os.path.join(self.objects, ko.MANIFEST_NAME), "w", encoding="utf-8") as handle:
            json.dump({"rules": ko.RULES, "objects": {k: sorted(v) for k, v in hashes.items()}}, handle)

    def apply(self, mode, kibana, now=NOW, **kw):
        return ko.run_apply(mode, self.data, self.objects, self.exports, opener=kibana, now=now, **kw)

    def preview(self, kibana):
        return ko.preview(self.data, self.objects, opener=kibana)

    def records(self):
        return ko.Records(self.data).objects

    def record(self, when, *keys):
        records = ko.Records(self.data)
        records.mark(keys, iso(when))
        records.save()

    def files(self, folder=None):
        try:
            return sorted(os.listdir(folder or self.exports))
        except FileNotFoundError:
            return []

    def cli(self, *args, opener):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ko.main(list(args), opener=opener)
        return rc, out.getvalue(), err.getvalue()


def keys(items):
    return [i.key for i in items]


class PlanTest(unittest.TestCase):
    D = dash("d")

    def plan_of(self, installed, records=None, old=(), shipped=None):
        manifest = {}
        for obj in [self.D, *old]:
            manifest.setdefault(ko.key(obj), set()).add(ko.digest(obj))
        return ko.plan(installed, shipped or [self.D], manifest, records or {})

    def action(self, installed, **kw):
        items = self.plan_of(installed, **kw).items
        self.assertEqual(len(items), 1)
        return items[0].action

    def test_plan_missing_is_new(self):
        self.assertEqual(self.action([as_in_kibana(dash("other"))]), "new")

    def test_plan_hash_in_manifest_is_update(self):
        old = dash("d", body=0)
        self.assertEqual(self.action([as_in_kibana(old)], old=[old]), "update")

    def test_plan_same_as_shipped_is_current(self):
        self.assertEqual(self.action([as_in_kibana(self.D)]), "current")

    def test_plan_unknown_hash_without_record_is_yours(self):
        self.assertEqual(self.action([as_in_kibana(dash("d", title="Mine"))]), "yours")
        # a record of another object changes nothing
        self.assertEqual(self.action([as_in_kibana(dash("d", title="Mine"), NOW)],
                                     records={"dashboard:other": iso(NOW)}), "yours")

    def test_plan_unknown_hash_within_60s_of_its_record_is_update(self):
        for after in (0.2, 59.9, 60):
            with self.subTest(after=after):
                self.assertEqual(self.action([as_in_kibana(dash("d", title="Mine"), NOW + after)],
                                             records={"dashboard:d": iso(NOW)}), "update")
        # Kibana's own form of the time, three digits and Z, also with Python 3.9
        mine = as_in_kibana(dash("d", title="Mine"))
        mine["updated_at"] = "2026-10-15T08:08:24.513Z"
        self.assertEqual(self.action([mine], records={"dashboard:d": "2026-10-15T08:08:24.3Z"}), "update")

    def test_plan_unknown_hash_after_its_record_is_yours(self):
        mine = as_in_kibana(dash("d", title="Mine"), NOW + 61)
        self.assertEqual(self.action([mine], records={"dashboard:d": iso(NOW)}), "yours")
        for recorded in ("", "yesterday", "2026-10-15T08:08:24"):   # no time, no zone
            with self.subTest(recorded=recorded):
                self.assertEqual(self.action([as_in_kibana(dash("d", title="Mine"), NOW)],
                                             records={"dashboard:d": recorded}), "yours")
        without = as_in_kibana(dash("d", title="Mine"), NOW)
        del without["updated_at"]
        self.assertEqual(self.action([without], records={"dashboard:d": iso(NOW)}), "yours")

    def test_plan_a_change_before_its_record_is_yours(self):
        # a clock that went back after T-Pot's import: your change lies before the record
        for before in (600, 0.5):
            with self.subTest(before=before):
                self.assertEqual(self.action([as_in_kibana(dash("d", title="Mine"), NOW - before)],
                                             records={"dashboard:d": iso(NOW)}), "yours")
        self.assertEqual(self.action([as_in_kibana(dash("d", title="Mine"), NOW)],
                                     records={"dashboard:d": iso(NOW)}), "update")

    def test_plan_never_lists_config_or_objects_of_your_own(self):
        installed = [as_in_kibana(self.D), as_in_kibana(CONFIG), as_in_kibana(dash("mine"))]
        found = self.plan_of(installed, shipped=[CONFIG, self.D])
        self.assertEqual(keys(found.items), ["dashboard:d"])

    def test_plan_title_comes_from_the_object_in_kibana(self):
        found = self.plan_of([as_in_kibana(dash("d", title="My title"))])
        self.assertEqual(found.items[0].title, "My title")
        self.assertEqual(self.plan_of([]).items[0].title, "Dashboard d")

    def test_each_mode_imports_its_part(self):
        items = [ko.Item(f"dashboard:{a}", "dashboard", a, a, a) for a in ("new", "update", "current", "yours")]
        chosen = ko.Plan(items)
        self.assertEqual([i.action for i in chosen.to_import("keep")], [])
        self.assertEqual([i.action for i in chosen.to_import("add")], ["new"])
        self.assertEqual([i.action for i in chosen.to_import("update")], ["new", "update"])
        self.assertEqual([i.action for i in chosen.to_import("overwrite")], ["new", "update", "yours"])
        with self.assertRaises(ko.KibanaError):
            chosen.to_import("all")

    def test_needs_first_import_when_no_shipped_id_exists(self):
        shipped = [self.D, dash("e")]
        self.assertTrue(ko.needs_first_import([], shipped))
        self.assertTrue(ko.needs_first_import([as_in_kibana(CONFIG), as_in_kibana(dash("mine"))], shipped))
        self.assertFalse(ko.needs_first_import([as_in_kibana(dash("e", title="Mine"))], shipped))
        self.assertFalse(ko.needs_first_import([as_in_kibana(CONFIG)], [CONFIG]))   # nothing of T-Pot to import


class RecordTest(unittest.TestCase):
    def test_a_skipped_object_stays_yours_after_a_later_import(self):
        x, y, y_old = dash("x"), dash("y"), dash("y", body=0)
        site = Site(self, [x, y], old=[y_old])
        t0 = NOW
        site.record(t0, "dashboard:x")
        t1 = t0 + 600
        kibana = FakeKibana([as_in_kibana(dash("x", title="Mine"), t0 + 300), as_in_kibana(y_old, t0)],
                            clock=lambda: t1)
        chosen, result, _ = site.apply("update", kibana, now=t1)
        self.assertEqual(keys(chosen.yours), ["dashboard:x"])
        self.assertEqual(result.imported, [("dashboard", "y")])
        self.assertEqual(kibana.store["dashboard:x"]["attributes"]["title"], "Mine")
        self.assertEqual(site.records(), {"dashboard:x": iso(t0), "dashboard:y": iso(t1)})
        again = site.preview(kibana)
        self.assertEqual(keys(again.yours), ["dashboard:x"])
        self.assertEqual(keys(again.current), ["dashboard:y"])

    def test_restore_drops_the_records_of_what_it_restored(self):
        x, y = dash("x"), dash("y")
        site = Site(self, [x, y])
        site.record(NOW, "dashboard:x", "dashboard:y")
        restored_at = NOW + 10        # within the window of the record of x
        kibana = FakeKibana([as_in_kibana(x, NOW), as_in_kibana(y, NOW)], clock=lambda: restored_at)
        backup = os.path.join(site.root, "mine.ndjson")
        with open(backup, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(as_in_kibana(dash("x", title="Mine"), NOW - DAY)) + "\n")
        export_path, result = ko.run_restore(backup, site.data, site.exports, opener=kibana, now=restored_at)
        self.assertTrue(os.path.isfile(export_path))
        self.assertEqual(result.imported, [("dashboard", "x")])
        self.assertEqual(site.records(), {"dashboard:y": iso(NOW)})
        self.assertEqual(keys(site.preview(kibana).yours), ["dashboard:x"])
        chosen, result, _ = site.apply("update", kibana, now=restored_at + 5)
        self.assertEqual(result.imported, [])
        self.assertEqual(kibana.store["dashboard:x"]["attributes"]["title"], "Mine")

    def test_only_successful_objects_are_recorded(self):
        a, b, c = dash("a"), dash("b"), dash("c")
        site = Site(self, [a, b, c])
        kibana = FakeKibana([as_in_kibana(CONFIG)], fail={"dashboard:c"})
        chosen, result, _ = site.apply("update", kibana)
        self.assertEqual(sorted(site.records()), ["dashboard:a", "dashboard:b"])
        self.assertEqual(result.imported, [("dashboard", "a"), ("dashboard", "b")])
        self.assertEqual(result.errors, [("dashboard", "c", "Dashboard c", "missing_references")])
        self.assertEqual(sorted(kibana.store), ["config:9.5.4", "dashboard:a", "dashboard:b"])
        # the first answer named a and b as successes and wrote nothing; they went in again alone
        self.assertEqual(len(kibana.imports()), 2)
        self.assertNotIn('"id": "c"', FakeKibana.ndjson(kibana.imports()[1]))

    def test_the_answers_kibana_9_5_4_gave(self):
        answers = fixture("import_answers.json")
        a, b = ({"type": "tag", "id": f"tpot-probe-b2-{n}", "attributes": {"name": f"tpot-probe-b2-{n}"}}
                for n in "ab")
        # a conflict: nothing was written, b goes in again alone
        written = dict(answers["conflict_overwrite_false"], success=True, errors=[])
        kibana = Scripted(answers["conflict_overwrite_false"], written)
        result = ko.import_objects([a, b], False, opener=kibana)
        self.assertEqual((result.imported, result.existing, result.errors),
                         ([("tag", "tpot-probe-b2-b")], [("tag", "tpot-probe-b2-a")], []))
        self.assertEqual([o["id"] for o in ko.parse_ndjson(FakeKibana.ndjson(kibana.requests[1]), "x")],
                         ["tpot-probe-b2-b"])
        # only a conflict: one request, nothing imported, no error
        result = ko.import_objects([dash("tpot-probe-b2-only")], False, opener=Scripted(answers["conflict_only"]))
        self.assertEqual((result.imported, result.existing, result.errors),
                         ([], [("dashboard", "tpot-probe-b2-only")], []))
        # a missing reference: the good one is not imported by that answer
        good, bad = dash("tpot-probe-b2-good"), dash("tpot-probe-b2-bad")
        result = ko.import_objects([good, bad], True, opener=Scripted(answers["missing_reference_overwrite_true"],
                                                                      answers["created"]))
        self.assertEqual(result.errors[0], ("dashboard", "tpot-probe-b2-bad", "tpot-probe-b2-bad", "missing_references"))
        # the retry of good got the answer of another object: that one was not sent, good is not named
        self.assertEqual(result.imported, [])
        self.assertEqual(result.errors[1:],
                         [("dashboard", "tpot-probe-b2-good", "Dashboard tpot-probe-b2-good", "not_imported")])
        retry = dict(answers["created"], successResults=[{"type": "dashboard", "id": "tpot-probe-b2-good"}])
        result = ko.import_objects([good, bad], True, opener=Scripted(answers["missing_reference_overwrite_true"],
                                                                      retry))
        self.assertEqual(result.imported, [("dashboard", "tpot-probe-b2-good")])

    def test_only_what_was_sent_and_named_is_imported(self):
        a, b = dash("a"), dash("b")
        answers = {
            "a key that was not sent": {"success": True, "successResults": [
                {"type": "dashboard", "id": "a"}, {"type": "dashboard", "id": "b"}, {"type": "dashboard", "id": "x"}]},
            "no success true": {"successResults": [{"type": "dashboard", "id": "a"}, {"type": "dashboard", "id": "b"}]},
            "success false": {"success": False},
            "an empty answer": {},
            "empty successResults": {"success": True, "successResults": []},
            "one of two named": {"success": True, "successResults": [{"type": "dashboard", "id": "a"}]},
        }
        expected = {"a key that was not sent": ["dashboard:a", "dashboard:b"], "one of two named": ["dashboard:a"]}
        for name, answer in answers.items():
            with self.subTest(name):
                result = ko.import_objects([a, b], True, opener=Scripted(answer))
                self.assertEqual(result.keys(), expected.get(name, []))
                self.assertEqual(sorted(f"{t}:{i}" for t, i, _, why in result.errors if why == "not_imported"),
                                 sorted({"dashboard:a", "dashboard:b"} - set(expected.get(name, []))))
        # and through a mode: only what was sent and named gets a record
        site = Site(self, [a, b, dash("x")])
        site.record(NOW - DAY, "dashboard:x")
        mine = as_in_kibana(dash("x", title="Mine"), NOW - DAY / 2)

        def opener(request, timeout=None):
            if "_export" in request.full_url:
                return io.BytesIO((json.dumps(as_in_kibana(CONFIG)) + "\n" + json.dumps(mine) + "\n").encode())
            return io.BytesIO(json.dumps(answers["a key that was not sent"]).encode())
        chosen, result, _ = site.apply("update", opener)
        self.assertEqual(keys(chosen.yours), ["dashboard:x"])
        self.assertEqual(site.records(), {"dashboard:a": iso(NOW), "dashboard:b": iso(NOW),
                                          "dashboard:x": iso(NOW - DAY)})      # x was named, not sent

    def test_what_still_fails_after_the_last_round_is_an_error(self):
        objs = [dash(f"o{n}") for n in range(ko.ROUNDS + 2)]
        # as Kibana answers: successResults name the rest, of which nothing was written
        refusals = [{"success": False, "errors": [{"type": "dashboard", "id": f"o{n}", "error": {"type": "unknown"}}],
                     "successResults": [{"type": "dashboard", "id": o["id"]} for o in objs[n + 1:]]}
                    for n in range(ko.ROUNDS)]
        kibana = Scripted(*refusals)
        result = ko.import_objects(objs, True, opener=kibana)
        self.assertEqual(len(kibana.requests), ko.ROUNDS)
        self.assertEqual(result.imported, [])
        self.assertEqual([e[3] for e in result.errors], ["unknown"] * ko.ROUNDS + ["not_imported"] * 2)
        # an error of no object in the file: no round more
        kibana = Scripted({"success": False, "errors": [{"type": "x", "id": "y", "error": {"type": "unknown"}}]})
        result = ko.import_objects([dash("a")], True, opener=kibana)
        self.assertEqual(len(kibana.requests), 1)
        self.assertEqual([e[:2] for e in result.errors], [("x", "y"), ("dashboard", "a")])

    def test_records_keep_mode_0660_and_survive_a_missing_file(self):
        site = Site(self, [])
        self.assertEqual(site.records(), {})
        old = os.umask(0)
        try:
            site.record(NOW, "dashboard:a")
        finally:
            os.umask(old)
        records = ko.Records(site.data)
        self.assertEqual(records.objects, {"dashboard:a": iso(NOW)})
        self.assertEqual(stat.S_IMODE(os.stat(records.path).st_mode), 0o660)
        self.assertEqual(stat.S_IMODE(os.stat(records.folder).st_mode), 0o770)
        self.assertEqual(records.path, os.path.join(site.data, "kibana_objects", "record.json"))
        with open(records.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"version": 1, "objects": {"dashboard:a": iso(NOW)}})
        os.chmod(records.path, 0o640)
        records.drop(["dashboard:a"])
        records.save()
        self.assertEqual(stat.S_IMODE(os.stat(records.path).st_mode), 0o660)
        self.assertEqual(site.records(), {})
        self.assertEqual([n for n in os.listdir(records.folder) if n != "record.json"], [])

    def test_a_broken_record_file_is_an_error_not_an_empty_record(self):
        site = Site(self, [dash("a")])
        os.makedirs(ko.state_dir(site.data))
        path = os.path.join(ko.state_dir(site.data), "record.json")
        for text in ("{not json", "", '{"version": 1, "objects": []}', '{"version": 2, "objects": {}}',
                     '{"version": 1, "objects": {"dashboard:a": 1}}'):
            with self.subTest(text=text):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
                with self.assertRaises(ko.KibanaError):
                    ko.Records(site.data)
                kibana = FakeKibana([as_in_kibana(CONFIG)])
                for mode in ("add", "update", "overwrite"):
                    with self.assertRaises(ko.KibanaError):
                        site.apply(mode, kibana)
                self.assertEqual(kibana.requests, [])
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), text)


class ExportTest(unittest.TestCase):
    def test_export_failure_means_no_import_and_no_file(self):
        site = Site(self, [dash("a")])
        for kibana in (FakeKibana([as_in_kibana(dash("a", title="Mine"))], export_status=503),
                       FakeKibana(down=True)):
            for mode in ("add", "update", "overwrite"):
                with self.subTest(mode=mode, down=kibana.down):
                    with self.assertRaises(ko.KibanaError):
                        site.apply(mode, kibana)
                    self.assertEqual(kibana.imports(), [])
                    self.assertEqual(site.files(), [])
        with self.assertRaisesRegex(ko.KibanaError, "503"):
            ko.export_objects(opener=FakeKibana(export_status=503))

    def test_an_export_with_no_objects_counts_as_failed(self):
        site = Site(self, [dash("a")])
        with self.assertRaisesRegex(ko.KibanaError, "no objects"):
            ko.export_objects(opener=FakeKibana([]))
        kibana = FakeKibana([])
        with self.assertRaises(ko.KibanaError):
            site.apply("overwrite", kibana)
        self.assertEqual((kibana.imports(), site.files()), ([], []))

        def broken(request, timeout=None):
            return io.BytesIO(b'{"type":"tag","id":"t","attributes":{}}\n<html>\n')
        with self.assertRaisesRegex(ko.KibanaError, "line 2"):
            ko.export_objects(opener=broken)

    def test_export_files_are_private(self):
        site = Site(self, [])
        text = json.dumps(as_in_kibana(CONFIG)) + "\n"
        old = os.umask(0)
        try:
            path = ko.save_export(text, site.exports, now=NOW)
            second = ko.save_export(text, site.exports, now=NOW)
        finally:
            os.umask(old)
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(NOW))
        self.assertEqual(os.path.basename(path), f"kibana_objects_{stamp}.ndjson")
        self.assertEqual(os.path.basename(second), f"kibana_objects_{stamp}_2.ndjson")
        self.assertEqual(stat.S_IMODE(os.stat(site.exports).st_mode), 0o700)
        for written in (path, second):
            self.assertEqual(stat.S_IMODE(os.stat(written).st_mode), 0o600)
            with open(written, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), text)
        self.assertEqual(site.files(), sorted(os.path.basename(p) for p in (path, second)))

    def test_auto_exports_are_for_the_group_tpot(self):
        site = Site(self, [])
        folder = ko.auto_exports_dir(site.data)
        old = os.umask(0o077)
        try:
            path = ko.save_export("{}\n", folder, ko.AUTO_PREFIX, now=NOW)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o770)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o660)
        self.assertEqual(ko.export_modes(ko.EXPORT_PREFIX), (0o700, 0o600))

    def test_exports_sort_by_time_then_by_stamp_and_number(self):
        site = Site(self, [])
        os.makedirs(site.exports)
        names = ["kibana_objects_20270101000000.ndjson", "kibana_objects_20270101000000_2.ndjson",
                 "kibana_objects_20270101000000_10.ndjson", "kibana_objects_20270102000000.ndjson"]
        for name in names:
            path = os.path.join(site.exports, name)
            open(path, "w").close()
            os.utime(path, (NOW, NOW))                     # one time: the names decide
        self.assertEqual([os.path.basename(p) for p in ko.exports(site.exports, ko.EXPORT_PREFIX)],
                         [names[3], names[2], names[1], names[0]])
        os.utime(os.path.join(site.exports, names[0]), (NOW + 5, NOW + 5))    # the time first
        self.assertEqual(os.path.basename(ko.exports(site.exports, ko.EXPORT_PREFIX)[0]), names[0])
        # the export just written stays, also when its time lies before the others (a clock that went back)
        spare = os.path.join(site.exports, names[1])
        os.utime(spare, (NOW - DAY, NOW - DAY))
        removed = ko.rotate(site.exports, ko.EXPORT_PREFIX, 2, spare=spare)
        self.assertTrue(os.path.exists(spare))
        self.assertEqual(sorted(site.files()), sorted([names[0], names[1]]))
        self.assertEqual(len(removed), 2)

    def test_auto_exports_rotate_at_10(self):
        site = Site(self, [dash("a")])
        folder = ko.auto_exports_dir(site.data)
        os.makedirs(folder)
        manager = os.path.join(folder, "kibana_objects_20200101000000.ndjson")
        open(manager, "w").close()
        kibana = FakeKibana()
        for run in range(12):
            kibana.store["dashboard:a"] = as_in_kibana(dash("a", title=f"Mine {run}"))
            _, result, path = ko.run_apply("overwrite", site.data, site.objects, folder, opener=kibana,
                                           now=NOW + run * 60, prefix=ko.AUTO_PREFIX, keep=10)
            self.assertEqual(result.imported, [("dashboard", "a")])
            self.assertTrue(os.path.isfile(path))
        autos = [n for n in site.files(folder) if n.startswith("auto_")]
        self.assertEqual(len(autos), 10)
        newest = time.strftime("%Y%m%d%H%M%S", time.gmtime(NOW + 11 * 60))
        self.assertEqual(autos[-1], f"auto_{newest}.ndjson")
        oldest_kept = time.strftime("%Y%m%d%H%M%S", time.gmtime(NOW + 2 * 60))
        self.assertEqual(autos[0], f"auto_{oldest_kept}.ndjson")
        self.assertTrue(os.path.exists(manager))      # only its own prefix rotates
        self.assertEqual(ko.rotate(folder, ko.AUTO_PREFIX, 10), [])


class ModeTest(unittest.TestCase):
    def three(self):
        """a missing, b an older T-Pot version, c changed by you, d the shipped one."""
        a, b, c, d = dash("a"), dash("b"), dash("c"), dash("d")
        b_old = dash("b", body=0)
        site = Site(self, [a, b, c, d], old=[b_old])
        kibana = FakeKibana([as_in_kibana(CONFIG), as_in_kibana(b_old), as_in_kibana(dash("c", title="Mine")),
                             as_in_kibana(d), as_in_kibana(dash("own"))])
        return site, kibana

    def test_keep_mode_makes_no_request(self):
        site, kibana = self.three()
        missing = os.path.join(site.root, "no-such-folder")
        chosen, result, path = ko.run_apply("keep", missing, missing, missing, opener=kibana, now=NOW)
        self.assertEqual((chosen.items, result.imported, result.errors, path), ([], [], [], ""))
        self.assertEqual(kibana.requests, [])
        self.assertFalse(os.path.exists(missing))

    def test_add_mode_imports_only_missing_with_overwrite_false_and_conflicts_are_no_errors(self):
        site, kibana = self.three()
        before = json.loads(json.dumps(kibana.store))
        chosen, result, path = site.apply("add", kibana)
        self.assertEqual(path, "")
        self.assertEqual(site.files(), [])                          # add changes nothing that is there
        self.assertEqual([r.full_url.endswith("overwrite=false") for r in kibana.imports()], [True])
        self.assertEqual(result.imported, [("dashboard", "a")])
        for name in ("dashboard:b", "dashboard:c", "dashboard:d", "config:9.5.4"):
            self.assertEqual(kibana.store[name], before[name])
        # e and f were missing in the export, but e is there by the time of the import
        site = Site(self, [dash("e"), dash("f")])
        kibana = FakeKibana([as_in_kibana(CONFIG), as_in_kibana(dash("e", title="Mine"))], hidden={"dashboard:e"})
        chosen, result, _ = site.apply("add", kibana)
        self.assertEqual(keys(chosen.new), ["dashboard:e", "dashboard:f"])
        self.assertEqual((result.imported, result.existing, result.errors),
                         ([("dashboard", "f")], [("dashboard", "e")], []))
        self.assertEqual(kibana.store["dashboard:e"]["attributes"]["title"], "Mine")
        self.assertEqual(sorted(site.records()), ["dashboard:f"])
        self.assertEqual(ko.summary("add", chosen, result)[0], "# Kibana objects: 1 new, 1 already there")

    def test_update_imports_new_and_unchanged_and_keeps_yours(self):
        site, kibana = self.three()
        chosen, result, path = site.apply("update", kibana)
        self.assertEqual(sorted(result.imported), [("dashboard", "a"), ("dashboard", "b")])
        self.assertEqual(kibana.store["dashboard:c"]["attributes"]["title"], "Mine")
        self.assertTrue(all(r.full_url.endswith("overwrite=true") for r in kibana.imports()))
        self.assertNotIn('"type": "config"', FakeKibana.ndjson(kibana.imports()[0]))
        self.assertEqual(os.path.dirname(path), site.exports)
        self.assertEqual(ko.summary("update", chosen, result, path),
                         [f"# Kibana objects: 1 new, 1 updated, 1 the same, 1 of yours kept (export: {path})"])

    def test_overwrite_exports_first_always(self):
        site, kibana = self.three()
        seen = []
        kibana.on_import = lambda request: seen.append(site.files())
        chosen, result, path = site.apply("overwrite", kibana)
        self.assertEqual(seen, [[os.path.basename(path)]])          # the export was there before the import
        with open(path, encoding="utf-8") as handle:
            self.assertIn("Mine", handle.read())
        self.assertEqual(sorted(result.imported), [("dashboard", "a"), ("dashboard", "b"), ("dashboard", "c")])
        self.assertEqual(kibana.store["dashboard:c"]["attributes"]["title"], "Dashboard c")
        self.assertEqual(kibana.store["dashboard:own"], as_in_kibana(dash("own")))
        self.assertNotIn('"type": "config"', FakeKibana.ndjson(kibana.imports()[0]))
        self.assertEqual(ko.summary("overwrite", chosen, result, path)[0],
                         f"# Kibana objects: 1 new, 1 updated, 1 the same, 1 of yours replaced (export: {path})")
        # only an object of yours to replace: still an export first
        kibana.store["dashboard:c"] = as_in_kibana(dash("c", title="Mine again"), NOW + DAY)
        seen.clear()
        chosen, result, second = site.apply("overwrite", kibana, now=NOW + 180)
        self.assertEqual([i.action for i in chosen.to_import("overwrite")], ["yours"])
        self.assertEqual(seen, [sorted(os.path.basename(p) for p in (path, second))])

    def test_nothing_to_do_means_no_export(self):
        d = dash("d")
        site = Site(self, [d])
        kibana = FakeKibana([as_in_kibana(CONFIG), as_in_kibana(d)])
        for mode in ("add", "update", "overwrite"):
            with self.subTest(mode=mode):
                chosen, result, path = site.apply(mode, kibana)
                self.assertEqual((path, result.imported, kibana.imports(), site.files()), ("", [], [], []))
        # yours only: update has nothing to do either
        kibana.store["dashboard:d"] = as_in_kibana(dash("d", title="Mine"))
        chosen, result, path = site.apply("update", kibana)
        self.assertEqual((path, kibana.imports(), site.files()), ("", [], []))
        self.assertEqual(keys(chosen.yours), ["dashboard:d"])
        self.assertFalse(os.path.exists(os.path.join(ko.state_dir(site.data), "record.json")))

    def test_an_import_that_fails_writes_no_record(self):
        site, kibana = self.three()
        kibana.import_status = 500
        with self.assertRaisesRegex(ko.KibanaError, "answered 500"):
            site.apply("update", kibana)
        self.assertEqual(site.records(), {})
        self.assertEqual(len(site.files()), 1)                     # the export is there, it was first


class RestoreTest(unittest.TestCase):
    def test_restore_refuses_a_file_that_is_no_export_before_anything(self):
        site = Site(self, [dash("a")])
        path = os.path.join(site.root, "pick.ndjson")
        kibana = FakeKibana([as_in_kibana(CONFIG)])
        line = json.dumps(dash("a")) + "\n"
        for text, why in (("", "no Kibana objects"), ("\n\n", "no Kibana objects"),
                          (line + line + "not json\n", "line 3"), (line + '{"type": "tag"}\n', "line 2"),
                          ('{"exportedCount": 0, "missingRefCount": 0}\n', "no Kibana objects")):
            with self.subTest(text=text):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
                with self.assertRaisesRegex(ko.KibanaError, why):
                    ko.run_restore(path, site.data, site.exports, opener=kibana, now=NOW)
        with self.assertRaises(ko.KibanaError):
            ko.run_restore(os.path.join(site.root, "missing.ndjson"), site.data, site.exports, opener=kibana)
        self.assertEqual(kibana.requests, [])
        self.assertEqual(site.files(), [])

    def test_restore_imports_config_too(self):
        site = Site(self, [dash("a")])
        kibana = FakeKibana([as_in_kibana(CONFIG), as_in_kibana(dash("a"))])
        path = os.path.join(site.root, "pick.ndjson")
        mine = dict(CONFIG, attributes={"defaultIndex": "mine", "theme:darkMode": True})
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(as_in_kibana(mine)) + "\n" + json.dumps(dash("a", title="Old")) + "\n")
            handle.write('{"exportedCount":2,"missingRefCount":0}\n')
        export_path, result = ko.run_restore(path, site.data, site.exports, opener=kibana, now=NOW)
        self.assertEqual(sorted(result.imported), [("config", "9.5.4"), ("dashboard", "a")])
        self.assertEqual(kibana.store["config:9.5.4"]["attributes"]["defaultIndex"], "mine")
        self.assertEqual(kibana.store["dashboard:a"]["attributes"]["title"], "Old")
        self.assertTrue(kibana.imports()[0].full_url.endswith("overwrite=true"))
        with open(export_path, encoding="utf-8") as handle:     # what Kibana held before
            self.assertIn('"defaultIndex": "logstash"', handle.read())

    def test_restore_into_an_empty_kibana_has_nothing_to_export_first(self):
        site = Site(self, [dash("a")])
        kibana = FakeKibana()
        path = os.path.join(site.root, "pick.ndjson")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(dash("a", title="Old")) + "\n")
        export_path, result = ko.run_restore(path, site.data, site.exports, opener=kibana, now=NOW)
        self.assertEqual((export_path, result.imported, site.files()), ("", [("dashboard", "a")], []))
        kibana = FakeKibana(export_status=503)
        with self.assertRaises(ko.KibanaError):
            ko.run_restore(path, site.data, site.exports, opener=kibana, now=NOW)
        self.assertEqual(kibana.imports(), [])


class RequestTest(unittest.TestCase):
    def test_import_sends_kbn_xsrf_and_multipart(self):
        kibana = FakeKibana()
        obj = dash("a", title="Ä title")
        ko.import_objects([obj], True, opener=kibana)
        request = kibana.imports()[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "http://127.0.0.1:64296/api/saved_objects/_import?overwrite=true")
        self.assertEqual(request.get_header("Kbn-xsrf"), "true")
        content_type = request.get_header("Content-type")
        self.assertRegex(content_type, r"^multipart/form-data; boundary=[0-9a-f]{32}$")
        boundary = content_type.split("boundary=")[1]
        body = request.data.decode("utf-8")
        self.assertTrue(body.startswith(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                                        f"filename=\"tpot.ndjson\"\r\nContent-Type: application/ndjson\r\n\r\n"))
        self.assertTrue(body.endswith(f"\r\n--{boundary}--\r\n"))
        self.assertEqual(ko.parse_ndjson(FakeKibana.ndjson(request), "x"), [obj])
        ko.import_objects([obj], False, opener=kibana)
        self.assertTrue(kibana.imports()[1].full_url.endswith("?overwrite=false"))
        self.assertEqual(ko.import_objects([], True, opener=kibana).imported, [])
        self.assertEqual(len(kibana.requests), 2)                 # nothing to import, no request

        ko.export_objects(opener=FakeKibana([as_in_kibana(CONFIG)]))
        kibana = FakeKibana([as_in_kibana(CONFIG)])
        ko.export_objects(opener=kibana)
        export = kibana.requests[-1]
        self.assertEqual((export.get_method(), export.full_url),
                         ("POST", "http://127.0.0.1:64296/api/saved_objects/_export"))
        self.assertEqual(export.get_header("Kbn-xsrf"), "true")
        self.assertEqual(export.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(export.data), {"type": "*", "excludeExportDetails": True})

    def test_kibana_not_reachable_names_the_url(self):
        with self.assertRaisesRegex(ko.KibanaError, r"Kibana at http://127\.0\.0\.1:64296 is not reachable "
                                                    r"\(connection refused\)"):
            ko.export_objects(opener=FakeKibana(down=True))
        with self.assertRaises(ko.KibanaError) as caught:
            ko.import_objects([dash("a")], True, opener=FakeKibana(import_status=503))
        text = str(caught.exception)
        self.assertTrue(text.startswith("Kibana at http://127.0.0.1:64296 answered 503: {"), text)
        self.assertLessEqual(len(text), len("Kibana at http://127.0.0.1:64296 answered 503: ") + 200)

        def timeout(request, timeout=None):
            raise TimeoutError("timed out")
        with self.assertRaisesRegex(ko.KibanaError, "not reachable"):
            ko.export_objects(url="http://kibana:5601", opener=timeout)
        self.assertIn("http://kibana:5601", ko.status("http://kibana:5601", opener=timeout))
        self.assertIsNone(ko.status(opener=lambda request, timeout=None: io.BytesIO(b"{}")))

    def test_timeouts(self):
        seen = []

        def opener(request, timeout=None):
            seen.append((request.full_url.rsplit("/", 1)[1], timeout))
            return io.BytesIO(b'{"type":"tag","id":"t"}\n' if "_export" in request.full_url
                              else b'{"success": true, "successResults": []}')
        ko.status(opener=opener)
        ko.export_objects(opener=opener)
        ko.import_objects([dash("a")], True, opener=opener)
        self.assertEqual(seen, [("status", 5), ("_export", 30), ("_import?overwrite=true", 30)])


class FirstImportTest(unittest.TestCase):
    def first(self, site, kibana, now=NOW):
        return ko.run_first_import(site.data, site.objects, ko.auto_exports_dir(site.data), opener=kibana, now=now)

    def pending(self, site):
        with open(ko.pending_path(site.data), encoding="utf-8") as handle:
            return handle.read()

    def write_pending(self, site, text):
        os.makedirs(ko.state_dir(site.data), exist_ok=True)
        with open(ko.pending_path(site.data), "w", encoding="utf-8") as handle:
            handle.write(text if isinstance(text, str) else json.dumps(text))

    def test_first_import_takes_everything_and_ends_pending(self):
        site = Site(self, [dash("a"), dash("b")])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana()
        first = self.first(site, kibana)
        self.assertEqual((first.result.errors, first.attempt, first.missing, first.done), ([], 1, [], True))
        self.assertEqual(sorted(kibana.store), ["config:9.5.4", "dashboard:a", "dashboard:b"])
        self.assertTrue(kibana.imports()[0].full_url.endswith("overwrite=true"))
        self.assertFalse(os.path.exists(ko.pending_path(site.data)))
        self.assertEqual(site.records(), {k: iso(NOW) for k in ("config:9.5.4", "dashboard:a", "dashboard:b")})
        self.assertEqual(ko.first_summary(first), ["# Kibana objects: first import, 3 imported"])

    def test_the_first_attempt_takes_the_whole_file_with_the_advanced_settings(self):
        site = Site(self, [dash("a"), dash("b")])
        ko.mark_pending(site.data, "fresh")
        mine = as_in_kibana(dict(CONFIG, attributes={"defaultIndex": "mine"}))
        kibana = FakeKibana([mine, as_in_kibana(dash("own"))])
        first = self.first(site, kibana)
        self.assertEqual(kibana.store["config:9.5.4"]["attributes"], CONFIG["attributes"])
        with open(first.export_path, encoding="utf-8") as handle:       # what was there first
            self.assertIn('"defaultIndex": "mine"', handle.read())
        self.assertEqual(kibana.store["dashboard:own"], as_in_kibana(dash("own")))

    def test_an_existing_host_keeps_its_advanced_settings(self):
        mine = as_in_kibana(dict(CONFIG, attributes={"defaultIndex": "mine"}))
        for pending in ({"version": 1, "reason": "no-objects", "attempt": 0, "refused": []},
                        "", {"attempt": 0}, {"reason": "other", "attempt": 0}):
            with self.subTest(pending=pending):
                site = Site(self, [dash("a"), dash("b")])
                self.write_pending(site, pending)
                kibana = FakeKibana([mine])
                first = self.first(site, kibana)
                self.assertEqual((first.attempt, first.done, first.missing), (1, True, []))
                self.assertEqual(kibana.store["config:9.5.4"], mine)
                self.assertNotIn('"type": "config"', FakeKibana.ndjson(kibana.imports()[0]))
                self.assertEqual(sorted(first.result.keys()), ["dashboard:a", "dashboard:b"])
        site = Site(self, [dash("a")])
        ko.mark_pending(site.data)                                      # the default is the safe side
        self.assertEqual(ko.pending_state(site.data)["reason"], "no-objects")
        with self.assertRaises(ko.KibanaError):
            ko.mark_pending(site.data, "other")

    def test_first_import_partial_keeps_pending(self):
        site = Site(self, [dash("a"), dash("b")])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana(fail={"dashboard:b"})
        first = self.first(site, kibana)
        self.assertEqual(first.result.errors, [("dashboard", "b", "Dashboard b", "missing_references")])
        self.assertEqual(json.loads(self.pending(site)),
                         {"version": 1, "reason": "fresh", "attempt": 1, "refused": ["dashboard:b"]})
        self.assertEqual(sorted(site.records()), ["config:9.5.4", "dashboard:a"])
        self.assertEqual(ko.first_summary(first), [
            "# Kibana objects: first import, 2 imported, 1 failed, 1 not there yet; "
            "tpotinit tries again on the next start",
            '# Kibana objects: not imported: dashboard:b "Dashboard b" (missing_references)'])
        kibana.fail.clear()
        first = self.first(site, kibana, now=NOW + 60)
        self.assertEqual((first.result.imported, first.attempt, first.done), ([("dashboard", "b")], 2, True))
        self.assertFalse(os.path.exists(ko.pending_path(site.data)))
        self.assertEqual(site.records()["dashboard:b"], iso(NOW + 60))

    def test_a_retry_imports_what_update_would_and_never_yours(self):
        a, b, c, d = dash("a"), dash("b"), dash("c"), dash("d")
        b_old = dash("b", body=0)
        site = Site(self, [a, b, c, d], old=[b_old])
        mine_c = as_in_kibana(dash("c", title="Mine"))
        mine_config = as_in_kibana(dict(CONFIG, attributes={"defaultIndex": "mine"}))
        # the advanced settings go along only when an answer refused them
        self.write_pending(site, {"version": 1, "reason": "fresh", "attempt": 1, "refused": ["config:9.5.4"]})
        kibana = FakeKibana([as_in_kibana(b_old), mine_c, as_in_kibana(d), mine_config])
        first = self.first(site, kibana)
        self.assertEqual(sorted(first.result.keys()), ["config:9.5.4", "dashboard:a", "dashboard:b"])
        self.assertEqual(kibana.store["dashboard:c"], mine_c)
        self.assertTrue(first.done)
        # without a refusal they do not, nor does yours: also with no record, and from a broken pending
        for pending in ({"version": 1, "reason": "fresh", "attempt": 1, "refused": []}, "not json"):
            with self.subTest(pending=pending):
                self.write_pending(site, pending)
                os.unlink(os.path.join(ko.state_dir(site.data), "record.json"))
                kibana.store["config:9.5.4"] = mine_config
                kibana.store["dashboard:c"] = mine_c
                kibana.store.pop("dashboard:a", None)
                first = self.first(site, kibana, now=NOW + 600)
                self.assertEqual((first.attempt, first.result.keys()), (2, ["dashboard:a"]))
                self.assertEqual((kibana.store["config:9.5.4"], kibana.store["dashboard:c"]), (mine_config, mine_c))

    def test_all_objects_present_ends_pending(self):
        a, b = dash("a"), dash("b")
        site = Site(self, [a, b])
        site.record(NOW - DAY, "config:9.5.4", "dashboard:a", "dashboard:b")
        self.write_pending(site, {"version": 1, "reason": "fresh", "attempt": 1, "refused": ["dashboard:b"]})
        kibana = FakeKibana([as_in_kibana(dict(CONFIG, attributes={"defaultIndex": "mine"})),
                             as_in_kibana(dash("a", title="Mine"), NOW), as_in_kibana(b)])
        first = self.first(site, kibana)
        self.assertEqual((kibana.imports(), first.done, first.missing, first.attempt), ([], True, [], 2))
        self.assertFalse(os.path.exists(ko.pending_path(site.data)))
        self.assertEqual(ko.first_summary(first)[0].split(" (export")[0],
                         "# Kibana objects: first import, attempt 2, 0 imported")

    def test_three_attempts_end_pending_whatever_is_refused(self):
        site = Site(self, [dash("a"), dash("b")])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana(fail={"dashboard:b"})
        states = []
        for attempt in range(1, 4):
            if attempt == 2:
                kibana.fail = {"dashboard:a", "dashboard:b"}            # another set: the attempts still count
                del kibana.store["dashboard:a"]
            first = self.first(site, kibana, now=NOW + attempt * 600)
            states.append((first.attempt, first.done, first.given_up))
        self.assertEqual(states, [(1, False, False), (2, False, False), (3, True, True)])
        self.assertFalse(os.path.exists(ko.pending_path(site.data)))
        self.assertEqual(ko.first_summary(first)[-1],
                         "# Kibana objects: warning: still not there after 3 attempts: dashboard:a, dashboard:b; "
                         "tpotinit stops trying them, tpot elastic import tries them again")
        with self.assertRaisesRegex(ko.KibanaError, "no first import is pending"):
            self.first(site, kibana)

    def test_a_pending_first_import_that_starts_late_never_touches_yours(self):
        a, b = dash("a"), dash("b")
        site = Site(self, [a, b])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana([as_in_kibana(CONFIG)])
        # start 1 cannot write its export: no attempt
        blocked = ko.auto_exports_dir(site.data)
        os.makedirs(os.path.dirname(blocked), exist_ok=True)
        open(blocked, "w").close()
        with self.assertRaises(ko.KibanaError):
            self.first(site, kibana)
        os.unlink(blocked)
        self.assertEqual((ko.pending_state(site.data)["attempt"], kibana.imports()), (0, []))
        # meanwhile you bring them in yourself and change a and the advanced settings
        site.apply("update", kibana)
        mine_a = as_in_kibana(dash("a", title="Mine"), NOW + DAY)
        mine_config = as_in_kibana(dict(CONFIG, attributes={"defaultIndex": "mine"}), NOW + DAY)
        kibana.store.update({"dashboard:a": mine_a, "config:9.5.4": mine_config})
        sent = len(kibana.imports())
        first = self.first(site, kibana, now=NOW + 2 * DAY)
        self.assertEqual((first.attempt, first.done, first.result.imported), (1, True, []))
        self.assertEqual(len(kibana.imports()), sent)                   # nothing to send at all
        self.assertEqual((kibana.store["dashboard:a"], kibana.store["config:9.5.4"]), (mine_a, mine_config))

    def test_a_refusal_is_used_up_once_it_was_sent_again(self):
        site = Site(self, [dash("a")])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana(fail={"config:9.5.4"}, clock=time.time)
        self.first(site, kibana)
        self.assertEqual(json.loads(self.pending(site))["refused"], ["config:9.5.4"])
        # attempt 2 sends the settings again; Kibana writes them, the answer is lost
        kibana.fail.clear()
        kibana.lose = 1
        with self.assertRaises(ko.KibanaError):
            self.first(site, kibana)
        self.assertEqual(json.loads(self.pending(site)), {"version": 1, "reason": "fresh", "attempt": 2,
                                                          "refused": []})
        mine = as_in_kibana(dict(CONFIG, attributes={"defaultIndex": "mine"}), time.time() + 120)
        kibana.store["config:9.5.4"] = mine
        sent = len(kibana.imports())
        first = self.first(site, kibana)
        self.assertEqual((first.attempt, first.done, len(kibana.imports())), (3, True, sent))
        self.assertEqual(kibana.store["config:9.5.4"], mine)

    def lost(self, site, kibana, lose):
        """Four starts of the CLI, you change a and the advanced settings before each from the
        second on; `lose(start)` breaks that start."""
        codes = []
        for start in range(1, 5):
            if start > 1:
                kibana.store["dashboard:a"] = as_in_kibana(dash("a", title=f"Mine {start}"), time.time() + 120)
                kibana.store["config:9.5.4"] = as_in_kibana(
                    dict(CONFIG, attributes={"defaultIndex": f"mine {start}"}), time.time() + 120)
            before = len(kibana.imports())
            with lose(start):
                rc, out, err = site.cli("apply", "--mode", "keep", "--data", site.data, "--objects", site.objects,
                                        "--exports", ko.auto_exports_dir(site.data), opener=kibana)
            codes.append((rc, len(kibana.imports()) - before, os.path.exists(ko.pending_path(site.data))))
            if start > 1:
                self.assertEqual(kibana.store["dashboard:a"]["attributes"]["title"], f"Mine {start}", start)
                self.assertEqual(kibana.store["config:9.5.4"]["attributes"]["defaultIndex"], f"mine {start}",
                                 start)
        return codes

    def test_a_lost_answer_makes_the_next_start_a_retry(self):
        site = Site(self, [dash("a"), dash("b")])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana(fail={"dashboard:b"}, clock=time.time)

        @contextlib.contextmanager
        def lose(start):
            kibana.lose = 1 if start == 1 else 0          # Kibana writes the second round, its answer is lost
            yield
        codes = self.lost(site, kibana, lose)
        self.assertEqual(codes, [(1, 2, True), (1, 1, True), (1, 1, False), (0, 0, False)])
        self.assertTrue(os.path.exists(ko.given_up_path(site.data)))

    def test_a_lost_answer_on_every_start_gives_up_after_3_attempts(self):
        site = Site(self, [dash("a"), dash("b")])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana(clock=time.time, lose_written=False)

        @contextlib.contextmanager
        def lose(start):
            kibana.lose = 1                               # the request times out, nothing is written
            yield
        codes = self.lost(site, kibana, lose)
        self.assertEqual(codes, [(1, 1, True), (1, 1, True), (1, 1, True), (1, 0, False)])   # never a fourth
        with open(ko.given_up_path(site.data), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["refused"], ["dashboard:b"])

    def test_a_record_that_cannot_be_written_makes_the_next_start_a_retry(self):
        site = Site(self, [dash("a"), dash("b")])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana(fail={"dashboard:b"}, clock=time.time)

        @contextlib.contextmanager
        def lose(start):
            if start != 1:
                yield
                return
            with unittest.mock.patch.object(ko.Records, "save", side_effect=ko.KibanaError("disk full")):
                yield
        codes = self.lost(site, kibana, lose)
        self.assertEqual(codes, [(1, 2, True), (1, 1, True), (1, 1, False), (0, 0, False)])
        self.assertTrue(os.path.exists(ko.given_up_path(site.data)))

    def test_first_import_exports_a_kibana_that_holds_objects(self):
        site = Site(self, [dash("a")])
        folder = ko.auto_exports_dir(site.data)
        ko.mark_pending(site.data, "fresh")
        self.first(site, FakeKibana())
        self.assertEqual(site.files(folder), [])                   # an empty Kibana: nothing to keep
        ko.mark_pending(site.data, "fresh")
        self.first(site, FakeKibana([as_in_kibana(dict(CONFIG, attributes={"defaultIndex": "mine"}))]))
        self.assertEqual(len(site.files(folder)), 1)
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana([as_in_kibana(CONFIG)], export_status=500)
        with self.assertRaises(ko.KibanaError):
            self.first(site, kibana)
        self.assertEqual(kibana.imports(), [])
        self.assertEqual(ko.pending_state(site.data)["attempt"], 0)    # no request, no attempt


class CliTest(unittest.TestCase):
    def args(self, site, mode="update"):
        return ("apply", "--mode", mode, "--data", site.data, "--objects", site.objects,
                "--exports", ko.auto_exports_dir(site.data))

    def test_apply_says_what_it_did_in_one_line(self):
        a, b = dash("a"), dash("b")
        site = Site(self, [a, b])
        kibana = FakeKibana([as_in_kibana(CONFIG), as_in_kibana(dash("b", title="Mine"))])
        rc, out, err = site.cli(*self.args(site), opener=kibana)
        self.assertEqual((rc, err), (0, ""))
        folder = ko.auto_exports_dir(site.data)
        [path] = [os.path.join(folder, n) for n in site.files(folder)]
        self.assertTrue(os.path.basename(path).startswith("auto_"))
        self.assertEqual(out, f"# Kibana objects: 1 new, 0 updated, 0 the same, 1 of yours kept (export: {path})\n")
        self.assertEqual(len([r for r in kibana.requests if "_export" in r.full_url]), 1)   # one export per run
        rc, out, err = site.cli(*self.args(site), opener=kibana)
        self.assertEqual((rc, out), (0, "# Kibana objects: nothing to import (0 new, 0 updated, 1 the same, "
                                        "1 of yours kept)\n"))
        rc, out, err = site.cli(*self.args(site, "keep"), opener=kibana)
        self.assertEqual((rc, out), (0, "# Kibana objects: TPOT_KIBANA_OBJECTS=keep, nothing changed\n"))

    def test_apply_with_a_refused_object_exits_1(self):
        site = Site(self, [dash("a"), dash("b")])
        kibana = FakeKibana([as_in_kibana(CONFIG), as_in_kibana(dash("a", body=5))], fail={"dashboard:b"})
        rc, out, err = site.cli(*self.args(site, "overwrite"), opener=kibana)
        self.assertEqual(rc, 1)
        self.assertIn("0 new, 0 updated, 0 the same, 1 of yours replaced, 1 failed", out)
        self.assertIn('not imported: dashboard:b "Dashboard b" (missing_references)', out)

    def test_apply_without_kibana_exits_1_and_names_it(self):
        site = Site(self, [dash("a")])
        rc, out, err = site.cli(*self.args(site), "--kibana", "http://kibana:5601", opener=FakeKibana(down=True))
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(err, "# Kibana objects: Kibana at http://kibana:5601 is not reachable (connection refused)\n")

    def test_a_wrong_call_exits_2(self):
        site = Site(self, [dash("a")])
        for args in (self.args(site, "all"), ("apply", "--mode", "update", "--data", site.data),
                     ("first",), ("first", "--data", site.data, "--objects", site.objects), ("nothing",)):
            with self.subTest(args=args):
                kibana = FakeKibana()
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(site.cli(*args, opener=kibana)[0], 2)
                self.assertEqual(kibana.requests, [])

    def test_apply_runs_the_first_import_while_it_is_pending(self):
        site = Site(self, [dash("a")])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana([as_in_kibana(dash("own"))])
        rc, out, _ = site.cli(*self.args(site, "keep"), opener=kibana)
        self.assertEqual(rc, 0)
        self.assertTrue(out.splitlines()[-1].startswith("# Kibana objects: first import, 2 imported (export: "))
        self.assertFalse(os.path.exists(ko.pending_path(site.data)))
        self.assertEqual(len([r for r in kibana.requests if "_export" in r.full_url]), 1)
        # a Kibana that holds a T-Pot object already gets a retry, also on attempt 1: yours stays
        ko.mark_pending(site.data, "fresh")
        kibana.store["dashboard:a"] = mine = as_in_kibana(dash("a", title="Mine"), NOW + DAY)
        kibana.store["config:9.5.4"] = setting = as_in_kibana(dict(CONFIG, attributes={"x": 1}), NOW + DAY)
        rc, out, _ = site.cli(*self.args(site, "keep"), opener=kibana)
        self.assertEqual((rc, kibana.store["dashboard:a"], kibana.store["config:9.5.4"]), (0, mine, setting))

    def test_apply_without_any_tpot_object_imports_them_in_every_mode(self):
        for mode in ko.MODES:
            with self.subTest(mode=mode):
                site = Site(self, [dash("a"), dash("b")])
                kibana = FakeKibana([as_in_kibana(CONFIG), as_in_kibana(dash("own"))], fail={"dashboard:b"})
                rc, out, _ = site.cli(*self.args(site, mode), opener=kibana)
                self.assertEqual(rc, 1)
                self.assertEqual(out.splitlines()[0],
                                 "# Kibana objects: Kibana holds none of T-Pot's objects, the first import runs again")
                self.assertTrue(os.path.exists(ko.pending_path(site.data)))   # b failed: next start again
                self.assertIn("dashboard:a", kibana.store)
                self.assertEqual(len(site.files(ko.auto_exports_dir(site.data))), 1)   # what was there first
                kibana.fail.clear()
                rc, out, _ = site.cli(*self.args(site, mode), opener=kibana)
                self.assertEqual(rc, 0)
                self.assertFalse(os.path.exists(ko.pending_path(site.data)))
                # an existing host: its advanced settings are never sent
                self.assertEqual(kibana.store["config:9.5.4"], as_in_kibana(CONFIG))
                self.assertFalse(any('"type": "config"' in FakeKibana.ndjson(r) for r in kibana.imports()))

    def test_a_refused_object_across_12_starts_never_reverts_yours(self):
        site = Site(self, [dash("a"), dash("b"), dash("c")])
        ko.mark_pending(site.data, "fresh")
        kibana = FakeKibana(fail={"dashboard:b"}, clock=time.time)
        codes, pending, warned = [], [], []
        for start in range(1, 13):
            if start > 1:          # you change a and the advanced settings between the starts
                kibana.store["dashboard:a"] = as_in_kibana(dash("a", title=f"Mine {start}"), time.time() + 120)
                kibana.store["config:9.5.4"] = as_in_kibana(
                    dict(CONFIG, attributes={"defaultIndex": f"mine {start}"}), time.time() + 120)
            rc, out, _ = site.cli(*self.args(site, "keep"), opener=kibana)
            codes.append(rc)
            pending.append(os.path.exists(ko.pending_path(site.data)))
            warned.append("warning" in out)
            if start > 1:
                self.assertEqual(kibana.store["dashboard:a"]["attributes"]["title"], f"Mine {start}", start)
                self.assertEqual(kibana.store["config:9.5.4"]["attributes"]["defaultIndex"], f"mine {start}", start)
            if start == 3:
                self.assertIn("still not there after 3 attempts: dashboard:b", out)
                self.assertIn("tpot elastic import tries them again", out)
        self.assertEqual(codes, [1, 1, 1] + [0] * 9)
        self.assertEqual(pending, [True, True] + [False] * 10)
        self.assertEqual(warned, [False, False, True] + [False] * 9)
        self.assertEqual(sorted(site.records()), ["config:9.5.4", "dashboard:a", "dashboard:c"])
        self.assertLessEqual(len(site.files(ko.auto_exports_dir(site.data))), ko.AUTO_KEEP)

    def give_up(self, site, kibana):
        """Three starts of a first import that Kibana refuses all of."""
        ko.mark_pending(site.data)
        for _ in range(3):
            site.cli(*self.args(site, "keep"), opener=kibana)

    def test_a_first_import_that_gave_up_stays_given_up(self):
        site = Site(self, [dash("a"), dash("b")])
        ko.mark_pending(site.data, "fresh")
        everything = {"config:9.5.4", "dashboard:a", "dashboard:b"}
        kibana = FakeKibana(fail=everything)
        imports, config_sent, lines = [], [], []
        for start in range(1, 10):
            before = len(kibana.imports())
            rc, out, _ = site.cli(*self.args(site, "keep"), opener=kibana)
            sent = kibana.imports()[before:]
            imports.append(len(sent))
            config_sent.append(any('"type": "config"' in FakeKibana.ndjson(r) for r in sent))
            lines.append(out.splitlines()[-1])
        self.assertTrue(all(imports[:3]))
        self.assertEqual(imports[3:], [0] * 6)
        self.assertEqual(config_sent[3:], [False] * 6)
        self.assertEqual(lines[3:], ["# Kibana objects: the first import gave up on 3 objects, "
                                     "tpot elastic import retries them"] * 6)
        self.assertFalse(os.path.exists(ko.pending_path(site.data)))
        with open(ko.given_up_path(site.data), encoding="utf-8") as handle:
            marker = json.load(handle)
        self.assertEqual(marker["refused"], sorted(everything))
        self.assertEqual(marker["shipped"], ko.shipped_sha(site.objects))
        self.assertIsNotNone(ko._time(marker["at"]))
        self.assertEqual(stat.S_IMODE(os.stat(ko.given_up_path(site.data)).st_mode), 0o660)

    def test_the_given_up_marker_goes_with_a_new_release(self):
        site = Site(self, [dash("a")])
        kibana = FakeKibana(fail={"config:9.5.4", "dashboard:a"})
        self.give_up(site, kibana)
        self.assertTrue(os.path.exists(ko.given_up_path(site.data)))
        with open(os.path.join(site.objects, ko.SHIPPED_NAME), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(dash("c")) + "\n")      # the shipped file of the next release
        kibana.fail.clear()
        rc, out, _ = site.cli(*self.args(site, "keep"), opener=kibana)
        self.assertEqual(out.splitlines()[0],
                         "# Kibana objects: Kibana holds none of T-Pot's objects, the first import runs again")
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(kibana.store), ["dashboard:a", "dashboard:c"])
        self.assertFalse(os.path.exists(ko.given_up_path(site.data)))

    def test_the_given_up_marker_goes_when_an_import_brings_everything_in(self):
        site = Site(self, [dash("a"), dash("b")])
        kibana = FakeKibana([as_in_kibana(dash("own"))], fail={"config:9.5.4", "dashboard:a", "dashboard:b"})
        self.give_up(site, kibana)
        self.assertTrue(os.path.exists(ko.given_up_path(site.data)))
        kibana.fail = {"dashboard:b"}
        site.apply("update", kibana)                        # tpot elastic import, b refused still
        self.assertTrue(os.path.exists(ko.given_up_path(site.data)))
        kibana.fail.clear()
        chosen, result, _ = site.apply("update", kibana)
        self.assertEqual(result.imported, [("dashboard", "b")])
        self.assertFalse(os.path.exists(ko.given_up_path(site.data)))
        # a marker that cannot be read still holds the first import back
        os.makedirs(ko.state_dir(site.data), exist_ok=True)
        with open(ko.given_up_path(site.data), "w", encoding="utf-8") as handle:
            handle.write("not json")
        self.assertEqual(ko.given_up(site.data, site.objects), [])
        self.assertEqual(ko.given_up(os.path.join(site.root, "nowhere"), site.objects), None)

    def test_first_runs_alone(self):
        site = Site(self, [dash("a")])
        ko.mark_pending(site.data, "fresh")
        rc, out, _ = site.cli("first", "--data", site.data, "--objects", site.objects,
                              "--exports", ko.auto_exports_dir(site.data), opener=FakeKibana())
        self.assertEqual((rc, out), (0, "# Kibana objects: first import, 2 imported\n"))
        self.assertFalse(os.path.exists(ko.pending_path(site.data)))

    def test_first_without_pending_refuses_and_names_apply(self):
        site = Site(self, [dash("a")])
        kibana = FakeKibana([as_in_kibana(CONFIG)])
        rc, out, err = site.cli("first", "--data", site.data, "--objects", site.objects,
                                "--exports", ko.auto_exports_dir(site.data), opener=kibana)
        self.assertEqual((rc, out, kibana.requests), (2, "", []))
        self.assertIn("no first import is pending", err)
        self.assertIn("run apply", err)


@unittest.skipIf(ko.fcntl is None, "no flock on this platform")
class LockTest(unittest.TestCase):
    HOLD = ("import fcntl, os, sys, time\n"
            "handle = os.open(sys.argv[1], os.O_RDONLY | os.O_CREAT, 0o660)\n"
            "fcntl.flock(handle, fcntl.LOCK_EX)\n"
            "print('held', flush=True)\n"
            "time.sleep(float(sys.argv[2]))\n")

    def setUp(self):
        self.site = Site(self, [dash("a")])
        os.makedirs(ko.state_dir(self.site.data))
        self.wait = ko.LOCK_WAIT
        self.addCleanup(setattr, ko, "LOCK_WAIT", self.wait)

    def hold(self, seconds):
        """Another process holds the lock for `seconds`."""
        proc = subprocess.Popen([sys.executable, "-c", self.HOLD, ko.lock_path(self.site.data), str(seconds)],
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.wait)
        self.assertEqual(proc.stdout.readline().strip(), "held")
        proc.stdout.close()
        return proc

    def test_one_import_at_a_time(self):
        site = self.site
        self.hold(3)
        ko.LOCK_WAIT = 0.4
        kibana = FakeKibana([as_in_kibana(CONFIG)])
        backup = os.path.join(site.root, "pick.ndjson")
        with open(backup, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(dash("a")) + "\n")
        ko.mark_pending(site.data, "fresh")
        runs = {
            "update": lambda: site.apply("update", kibana),
            "restore": lambda: ko.run_restore(backup, site.data, site.exports, opener=kibana),
            "first": lambda: ko.run_first_import(site.data, site.objects, ko.auto_exports_dir(site.data),
                                                 opener=kibana),
        }
        for name, run in runs.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ko.KibanaError, r"^another Kibana import is running "
                                                            r"\(tpotinit or tpot elastic\)$"):
                    run()
        rc, out, err = site.cli("apply", "--mode", "update", "--data", site.data, "--objects", site.objects,
                                "--exports", ko.auto_exports_dir(site.data), opener=kibana)
        self.assertEqual((rc, err), (1, f"# Kibana objects: {ko.BUSY}\n"))
        self.assertEqual(kibana.requests, [])
        self.assertEqual(ko.pending_state(site.data)["attempt"], 0)
        # keep makes no request, it needs no lock
        self.assertEqual(site.apply("keep", kibana)[2], "")

    def test_a_run_waits_for_the_other_one(self):
        self.hold(1)
        ko.LOCK_WAIT = 20
        kibana = FakeKibana([as_in_kibana(CONFIG)])
        started = time.monotonic()
        chosen, result, _ = self.site.apply("update", kibana)
        self.assertGreater(time.monotonic() - started, 0.5)
        self.assertEqual(result.imported, [("dashboard", "a")])
        # inside a run of the same process the lock is held already
        with ko.import_lock(self.site.data):
            with ko.import_lock(self.site.data):
                self.site.apply("update", kibana)
        with ko.import_lock(self.site.data):
            pass
        self.assertEqual(ko._held(), {})

    def test_the_lock_file_is_0660_whatever_the_umask(self):
        old = os.umask(0o077)
        try:
            with ko.import_lock(self.site.data):
                pass
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(ko.lock_path(self.site.data)).st_mode), 0o660)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes every file")
    def test_a_lock_file_this_user_cannot_write_is_locked_read_only(self):
        path = ko.lock_path(self.site.data)
        open(path, "w").close()
        os.chmod(path, 0o440)                 # as one of tpotinit's would be for the T-Pot Manager
        self.addCleanup(os.chmod, path, 0o640)
        with ko.import_lock(self.site.data):
            self.assertEqual(ko._held(), {os.path.abspath(path): 1})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o440)   # not ours to change
        # and it still keeps out another run
        self.hold(3)
        ko.LOCK_WAIT = 0.4
        with self.assertRaisesRegex(ko.KibanaError, "another Kibana import is running"):
            with ko.import_lock(self.site.data):
                pass

    def test_two_threads_never_share_the_lock(self):
        held, done = threading.Event(), threading.Event()

        def holder():
            with ko.import_lock(self.site.data):
                held.set()
                done.wait(10)
        thread = threading.Thread(target=holder)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(done.set)
        self.assertTrue(held.wait(10))
        ko.LOCK_WAIT = 0.4
        with self.assertRaisesRegex(ko.KibanaError, "another Kibana import is running"):
            with ko.import_lock(self.site.data):
                pass
        done.set()
        thread.join(10)
        with ko.import_lock(self.site.data):
            self.assertEqual(len(ko._held()), 1)

    def test_a_lock_that_cannot_be_used_is_named(self):
        site = Site(self, [dash("a")])
        open(ko.state_dir(site.data), "w").close()          # a file where the folder belongs
        path = os.path.abspath(ko.lock_path(site.data))
        with self.assertRaisesRegex(ko.KibanaError, f"^the Kibana import lock {re.escape(path)} cannot be used: "):
            site.apply("update", FakeKibana([as_in_kibana(CONFIG)]))
        rc, out, err = site.cli("apply", "--mode", "update", "--data", site.data, "--objects", site.objects,
                                "--exports", os.path.join(site.root, "exports"), opener=FakeKibana())
        self.assertEqual((rc, out, len(err.splitlines())), (1, "", 1))
        self.assertTrue(err.startswith(f"# Kibana objects: the Kibana import lock {path} cannot be used: "), err)
        self.assertNotIn("Traceback", err)
        failing = OSError(errno.ENOLCK, "No locks available")
        with unittest.mock.patch.object(ko.fcntl, "flock", side_effect=failing):
            with self.assertRaisesRegex(ko.KibanaError, "cannot be used: No locks available$"):
                with ko.import_lock(self.site.data):
                    pass


class OpsTest(unittest.TestCase):
    def test_kibana_dirs_come_from_the_env(self):
        from tpotctl import ops
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo)
        with open(os.path.join(repo, ".env"), "w", encoding="utf-8") as handle:
            handle.write("TPOT_DATA_PATH=./data\n")
        data, objects = ops.kibana_dirs(repo)
        self.assertEqual(data, os.path.join(repo, "data"))
        self.assertEqual(objects, os.path.join(repo, "docker", "tpotinit", "dist", "etc", "objects"))
        self.assertEqual(ops.kibana_dirs(repo, {"TPOT_DATA_PATH": "/srv/tpot"})[0], "/srv/tpot")
        self.assertEqual(ops.kibana_dirs(ko.REPO_DIR, {})[1], ko.OBJECTS_DIR)

    def test_kibana_exports_newest_first_from_both_places(self):
        from tpotctl import ops
        site = Site(self, [])
        auto = ko.auto_exports_dir(site.data)
        os.makedirs(site.exports)
        os.makedirs(auto)
        made = []
        for n, (folder, name) in enumerate([(site.exports, "kibana_objects_1.ndjson"), (auto, "auto_1.ndjson"),
                                            (site.exports, "kibana_objects_2.ndjson"), (site.exports, "other.ndjson"),
                                            (site.exports, "x_tpot_backup.tar")]):
            path = os.path.join(folder, name)
            open(path, "w").close()
            os.utime(path, (NOW + n, NOW + n))
            made.append(path)
        self.assertEqual(ops.kibana_exports(site.home), [made[2], made[0]])
        self.assertEqual(ops.kibana_exports(site.home, site.data), [made[2], made[1], made[0]])
        self.assertEqual(ops.kibana_exports(os.path.join(site.root, "nobody")), [])


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
