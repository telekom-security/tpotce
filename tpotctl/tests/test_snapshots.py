"""Snapshot protocol tests against a real HTTP boundary."""

import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib
import json
import threading
import unittest
from unittest import mock

try:
    snapshots = importlib.import_module("tpotctl.snapshots")
except ModuleNotFoundError:
    snapshots = None


INDEX = "logstash-2024.04.01"
KIBANA_INDEX = ".kibana_8.16.1_001"
SNAPSHOT = {
    "snapshot": "original",
    "uuid": "snapshot-id",
    "version": "8.16.0-8.16.1",
    "indices": [INDEX, KIBANA_INDEX],
    "feature_states": [{"feature_name": "kibana", "indices": [KIBANA_INDEX]}],
    "state": "SUCCESS",
    "shards": {"total": 2, "successful": 2, "failed": 0},
}
STATS = {
    "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
    "timed_out": False,
    "hits": {"total": {"value": 3, "relation": "eq"},
             "hits": [{"_id": "one", "_source": {"type": "Cowrie"}}]},
    "aggregations": {
        "first": {"value": 1711933200000, "value_as_string": "2024-04-01T01:00:00.000Z"},
        "last": {"value": 1711940400000, "value_as_string": "2024-04-01T03:00:00.000Z"},
    },
}
INVENTORY = {
    INDEX: {
        "created": "8518000", "count": 3,
        "first": "2024-04-01T01:00:00.000Z", "last": "2024-04-01T03:00:00.000Z",
        "aliases": {"events": {}},
        "samples": [{"id": "one", "source": {"type": "Cowrie"}}],
    },
}


class Endpoint:
    def __init__(self):
        self.responses = {}
        self.calls = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.loads(raw) if raw else None
                endpoint.calls.append((self.command, self.path, body))
                values = endpoint.responses.get((self.command, self.path))
                status, answer = values.pop(0) if values else (500, {"error": "unexpected request"})
                data = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = handle_request

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def reply(self, method, path, answer, status=200):
        self.responses.setdefault((method, path), []).append((status, answer))

    def close(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def inventory(self, stats=None):
        self.reply("GET", f"/{INDEX}/_settings?flat_settings=true",
                   {INDEX: {"settings": {"index.version.created": "8518000"}}})
        self.reply("POST", f"/{INDEX}/_search", STATS if stats is None else stats)
        self.reply("GET", f"/{INDEX}/_alias", {INDEX: {"aliases": {"events": {}}}})


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(snapshots, "the shared snapshot implementation is missing")
        self.endpoint = Endpoint()
        self.addCleanup(self.endpoint.close)
        self.client = snapshots.SnapshotClient(self.endpoint.url)

    def test_snapshot_checks_terminal_state_and_explicit_scope(self):
        self.endpoint.reply("PUT", "/_snapshot/migration/original?wait_for_completion=false", {"accepted": True})
        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [SNAPSHOT]})
        result = self.client.snapshot("migration", "original", [INDEX], kibana=True)
        self.assertEqual(result["uuid"], "snapshot-id")
        self.assertEqual(self.endpoint.calls[0][2], {
            "indices": INDEX, "include_global_state": False,
            "feature_states": ["kibana"], "partial": False,
        })

    def test_partial_missing_primary_and_malformed_snapshot_never_succeed(self):
        invalid = [
            dict(SNAPSHOT, state="PARTIAL"),
            dict(SNAPSHOT, shards={"total": 2, "successful": 1, "failed": 1}),
            dict(SNAPSHOT, shards={"total": 2, "successful": 1, "failed": 0}),
            dict(SNAPSHOT, indices=[INDEX, KIBANA_INDEX, "foreign"]),
        ]
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                self.endpoint.reply("PUT", "/_snapshot/migration/original?wait_for_completion=false",
                                    {"accepted": True})
                self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [snapshot]})
                with self.assertRaises(snapshots.SnapshotError):
                    self.client.snapshot("migration", "original", [INDEX], kibana=True)
        self.endpoint.reply("PUT", "/_snapshot/migration/original?wait_for_completion=false",
                            {"error": {"type": "snapshot_exception",
                                       "reason": "unassigned primary shards"}}, status=500)
        with self.assertRaisesRegex(snapshots.SnapshotError, "500"):
            self.client.snapshot("migration", "original", [INDEX], kibana=True)

    def test_empty_selection_never_becomes_all_indices(self):
        self.endpoint.reply("PUT", "/_snapshot/migration/original?wait_for_completion=false", {"accepted": True})
        empty = dict(SNAPSHOT, indices=[], feature_states=[], shards={"total": 0, "successful": 0, "failed": 0})
        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [empty]})
        self.client.snapshot("migration", "original", [], kibana=False)
        self.assertEqual(self.endpoint.calls[0][2]["indices"], "-*")
        self.assertEqual(self.endpoint.calls[0][2]["feature_states"], ["none"])
        with self.assertRaises(snapshots.SnapshotError):
            self.client.snapshot("migration", "original", ["*"], kibana=False)

    def test_restore_waits_for_its_snapshot_recovery_and_refuses_existing_data(self):
        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [SNAPSHOT]})
        self.endpoint.reply("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index", [])
        self.endpoint.reply("GET", "/_snapshot/migration/original/_status",
                            {"snapshots": [{"snapshot": "original", "indices": {
                                INDEX: {"shards_stats": {"total": 1, "done": 1}}}}]})
        self.endpoint.reply("POST", "/_snapshot/migration/original/_restore?wait_for_completion=false",
                            {"accepted": True})
        self.endpoint.reply("GET", "/_recovery?active_only=false&expand_wildcards=all",
                            {INDEX: {"shards": [{"id": 0, "primary": True, "type": "SNAPSHOT", "stage": "DONE",
                                                 "source": {"repository": "migration", "snapshot": "original"}}]}})
        result = self.client.restore("migration", "original", [INDEX], kibana=False)
        self.assertEqual(result["snapshot"], "original")
        restore = [body for method, _path, body in self.endpoint.calls if method == "POST"][0]
        self.assertFalse(restore["include_global_state"])
        self.assertEqual(restore["feature_states"], ["none"])

        self.endpoint.calls.clear()
        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [SNAPSHOT]})
        self.endpoint.reply("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index",
                            [{"index": "existing-customer-data"}])
        with self.assertRaisesRegex(snapshots.SnapshotError, "empty"):
            self.client.restore("migration", "original", [INDEX], kibana=False)
        self.assertFalse(any(method == "POST" for method, _path, _body in self.endpoint.calls))

        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [SNAPSHOT]})
        self.endpoint.reply("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index", [])
        self.endpoint.reply("GET", "/_snapshot/migration/original/_status",
                            {"snapshots": [{"snapshot": "original", "indices": {
                                INDEX: {"shards_stats": {"total": 2, "done": 2}}}}]})
        self.endpoint.reply("POST", "/_snapshot/migration/original/_restore?wait_for_completion=false",
                            {"accepted": True})
        self.endpoint.reply("GET", "/_recovery?active_only=false&expand_wildcards=all",
                            {INDEX: {"shards": [{"id": 0, "primary": True, "type": "SNAPSHOT", "stage": "DONE",
                                                 "source": {"repository": "migration", "snapshot": "original"}}]}})
        with mock.patch.object(snapshots.time, "monotonic", side_effect=[0, 0, 999999]), \
                mock.patch.object(snapshots.time, "sleep"), \
                self.assertRaisesRegex(snapshots.SnapshotError, "timed out"):
            self.client.restore("migration", "original", [INDEX], kibana=False)

    def test_inventory_and_verification_detect_loss_without_relying_on_sample_order(self):
        self.endpoint.inventory()
        self.assertEqual(self.client.inventory([INDEX]), INVENTORY)
        for key, value in (("count", 2), ("last", "2024-04-01T02:00:00.000Z"), ("aliases", {})):
            with self.subTest(key=key):
                changed = copy.deepcopy(INVENTORY)
                changed[INDEX][key] = value
                with self.assertRaises(snapshots.SnapshotError):
                    snapshots.verify_inventory(INVENTORY, changed)
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.verify_inventory(INVENTORY, {})
        same = copy.deepcopy(INVENTORY)
        same[INDEX]["samples"] = [{"id": "two", "source": {"type": "Cowrie"}}]
        snapshots.verify_inventory(INVENTORY, same)
        self.endpoint.reply("POST", f"/{INDEX}/_mget",
                            {"docs": [{"_id": "one", "found": True, "_source": {"type": "Changed"}}]})
        with self.assertRaisesRegex(snapshots.SnapshotError, "sample"):
            self.client.verify_samples(INVENTORY)

    def test_restore_includes_hidden_kibana_shards_in_recovery_checks(self):
        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [SNAPSHOT]})
        self.endpoint.reply("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index", [])
        self.endpoint.reply("GET", "/_snapshot/migration/original/_status", {"snapshots": [{
            "snapshot": "original", "indices": {
                INDEX: {"shards_stats": {"total": 1, "done": 1}},
                KIBANA_INDEX: {"shards_stats": {"total": 1, "done": 1}},
            },
        }]})
        self.endpoint.reply("POST", "/_snapshot/migration/original/_restore?wait_for_completion=false",
                            {"accepted": True})
        shard = {"id": 0, "primary": True, "type": "SNAPSHOT", "stage": "DONE",
                 "source": {"repository": "migration", "snapshot": "original"}}
        self.endpoint.reply("GET", "/_recovery?active_only=false", {INDEX: {"shards": [shard]}})
        self.endpoint.reply("GET", "/_recovery?active_only=false&expand_wildcards=all",
                            {INDEX: {"shards": [shard]}, KIBANA_INDEX: {"shards": [shard]}})
        with mock.patch.object(snapshots.time, "monotonic", side_effect=[0, 0, 999999]), \
                mock.patch.object(snapshots.time, "sleep"):
            restored = self.client.restore("migration", "original", [INDEX], kibana=True)
        self.assertEqual(restored["uuid"], "snapshot-id")

    def test_a_long_snapshot_or_recovery_waits_while_it_progresses_and_stops_when_it_stalls(self):
        # wait bounds the time without progress: a history of hundreds of GB takes longer than wait
        client = snapshots.SnapshotClient(self.endpoint.url, wait=10)
        running = dict(SNAPSHOT, state="IN_PROGRESS")
        self.endpoint.reply("PUT", "/_snapshot/migration/original?wait_for_completion=false", {"accepted": True})
        for size in (100, 200):
            self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [running]})
            self.endpoint.reply("GET", "/_snapshot/migration/original/_status", {"snapshots": [{
                "snapshot": "original", "shards_stats": {"done": 0},
                "stats": {"processed": {"file_count": size // 100, "size_in_bytes": size}}}]})
        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [SNAPSHOT]})
        with mock.patch.object(snapshots.time, "monotonic", side_effect=[0, 0, 9, 18]), \
                mock.patch.object(snapshots.time, "sleep"):
            self.assertEqual(client.snapshot("migration", "original", [INDEX], kibana=True)["uuid"], "snapshot-id")

        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [SNAPSHOT]})
        self.endpoint.reply("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index", [])
        self.endpoint.reply("GET", "/_snapshot/migration/original/_status",
                            {"snapshots": [{"snapshot": "original", "indices": {
                                INDEX: {"shards_stats": {"total": 1, "done": 1}}}}]})
        self.endpoint.reply("POST", "/_snapshot/migration/original/_restore?wait_for_completion=false",
                            {"accepted": True})
        for stage, size in (("INDEX", 100), ("INDEX", 200), ("INDEX", 300), ("DONE", 400)):
            self.endpoint.reply("GET", "/_recovery?active_only=false&expand_wildcards=all", {INDEX: {"shards": [{
                "id": 0, "primary": True, "type": "SNAPSHOT", "stage": stage,
                "source": {"repository": "migration", "snapshot": "original"},
                "index": {"size": {"recovered_in_bytes": size}, "files": {"recovered": size // 100}}}]}})
        with mock.patch.object(snapshots.time, "monotonic", side_effect=[0, 0, 9, 18, 26]), \
                mock.patch.object(snapshots.time, "sleep"):
            client.restore("migration", "original", [INDEX], kibana=False)

        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [SNAPSHOT]})
        self.endpoint.reply("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index", [])
        self.endpoint.reply("GET", "/_snapshot/migration/original/_status",
                            {"snapshots": [{"snapshot": "original", "indices": {
                                INDEX: {"shards_stats": {"total": 1, "done": 1}}}}]})
        self.endpoint.reply("POST", "/_snapshot/migration/original/_restore?wait_for_completion=false",
                            {"accepted": True})
        for _poll in range(2):
            self.endpoint.reply("GET", "/_recovery?active_only=false&expand_wildcards=all", {INDEX: {"shards": [{
                "id": 0, "primary": True, "type": "SNAPSHOT", "stage": "INDEX",
                "source": {"repository": "migration", "snapshot": "original"},
                "index": {"size": {"recovered_in_bytes": 100}, "files": {"recovered": 1}}}]}})
        with mock.patch.object(snapshots.time, "monotonic", side_effect=[0, 0, 9, 11]), \
                mock.patch.object(snapshots.time, "sleep"), \
                self.assertRaisesRegex(snapshots.SnapshotError, "timed out"):
            client.restore("migration", "original", [INDEX], kibana=False)

    def test_inexact_counts_failed_search_and_invalid_json_are_errors(self):
        for stats in (
            dict(STATS, timed_out=True),
            dict(STATS, _shards={"total": 2, "successful": 1, "failed": 1}),
            dict(STATS, hits={"total": {"value": 10000, "relation": "gte"}, "hits": []}),
            b"not JSON",
        ):
            with self.subTest(stats=stats):
                self.endpoint.inventory(stats)
                with self.assertRaises(snapshots.SnapshotError):
                    self.client.inventory([INDEX])

    def test_pause_ilm_is_confirmed_and_waiting_is_bounded(self):
        self.endpoint.reply("POST", "/_ilm/stop", {"acknowledged": True})
        self.endpoint.reply("GET", "/_ilm/status", {"operation_mode": "STOPPING"})
        self.endpoint.reply("GET", "/_ilm/status", {"operation_mode": "STOPPED"})
        with mock.patch.object(snapshots.time, "sleep"):
            self.client.pause_ilm()
        self.assertEqual(len([call for call in self.endpoint.calls if call[1] == "/_ilm/status"]), 2)
        self.endpoint.reply("POST", "/_ilm/stop", {"acknowledged": True})
        self.endpoint.reply("GET", "/_ilm/status", {"operation_mode": "STOPPING"})
        with mock.patch.object(snapshots.time, "monotonic", side_effect=[0, 999999]), \
                self.assertRaisesRegex(snapshots.SnapshotError, "timed out"):
            self.client.pause_ilm()

    def test_route_uses_index_format_and_kibana_not_only_cluster_major(self):
        self.assertEqual(snapshots.restore_route("8.16.1", "9.5.4", ["8518000"],
                                                kibana=False, intermediate="8.19.23"), ("9.5.4",))
        self.assertEqual(snapshots.restore_route("8.16.1", "9.5.4", ["8518000"],
                                                kibana=True, intermediate="8.19.23"), ("8.19.23", "9.5.4"))
        self.assertEqual(snapshots.restore_route("8.19.23", "9.5.4", ["8518000"],
                                                kibana=True, intermediate="8.19.23"), ("9.5.4",))
        for source, target, created in (("9.5.4", "8.19.23", ["8518000"]),
                                        ("8.16.1", "9.5.4", ["7170099"]),
                                        ("8.16.1", "9.5.4", ["garbage"]),
                                        ("8.16.0-8.16.1", "9.5.4", ["8518000"])):
            with self.subTest(source=source, target=target, created=created), \
                    self.assertRaises(snapshots.SnapshotError):
                snapshots.restore_route(source, target, created, kibana=True, intermediate="8.19.23")


if __name__ == "__main__":
    unittest.main()
