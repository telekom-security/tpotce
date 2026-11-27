"""History migration lifetime, holds and recovery."""

import importlib
import copy
import io
import fcntl
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from tpotctl import migration, migration_cli, migration_import, snapshots
from tpotctl.envfile import EnvFile
from tpotctl.tests.test_snapshots import Endpoint, INDEX, STATS, INVENTORY
from tpotctl.tests.test_migration import SOURCE, ImportFixture

try:
    runtime = importlib.import_module("tpotctl.migration_runtime")
except ModuleNotFoundError:
    runtime = None
try:
    history = importlib.import_module("tpotctl.migration_history")
except ModuleNotFoundError:
    history = None


def history_metadata() -> dict:
    return {
        "format": "tpot-migration-history", "schema": 2, "run": "a" * 32,
        "elasticsearch_version": "8.16.1", "kibana_version": "8.16.1",
        "snapshot": "original", "indices": [INDEX], "inventory": copy.deepcopy(INVENTORY),
        "kibana": {"default": {"space": {"id": "default", "name": "Default"}, "objects": {
            "config:8.16.1": {"id": "8.16.1", "type": "config", "references": [],
                             "attributes": {"dateFormat": "YYYY-MM-DD"}},
            "dashboard:own": {"id": "own", "type": "dashboard", "references": [],
                              "attributes": {"title": "private dashboard title"}}}}},
        "ignored_indices": ["other-application"],
    }


def write_target_versions(target: Path) -> None:
    for service, key in (("elasticsearch", "ES_VER"), ("kibana", "KB_VER")):
        path = target / "docker" / "elk" / service
        path.mkdir(parents=True)
        (path / "Dockerfile").write_text(f"ARG {key}=9.5.4\n")
    (target / "update.sh").write_text('myES_STEP_VERSION="8.19.23"\n')


class HistoryDataTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(history, "the history migration controller is missing")
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-history-data-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.endpoint = Endpoint()
        self.addCleanup(self.endpoint.close)

    def test_selection_never_includes_unrelated_or_closed_indices(self):
        path = "/_cat/indices?format=json&expand_wildcards=all&h=index,status"
        rows = [{"index": name, "status": "open"} for name in
                ("logstash-2024.04.01", "tpot-sensor-heartbeat-2024.04.01", "tpot-sensor-status",
                 ".kibana_8.16.1_001", "other-application")]
        self.endpoint.reply("GET", path, rows)
        chosen, ignored = history.select_indices(snapshots.SnapshotClient(self.endpoint.url))
        self.assertEqual(chosen, ["logstash-2024.04.01", "tpot-sensor-heartbeat-2024.04.01",
                                  "tpot-sensor-status"])
        self.assertEqual(ignored, [".kibana_8.16.1_001", "other-application"])
        rows[0]["status"] = "close"
        self.endpoint.reply("GET", path, rows)
        with self.assertRaises(migration.MigrationError):
            history.select_indices(snapshots.SnapshotClient(self.endpoint.url))

    def test_kibana_acceptance_keeps_spaces_separate_and_checks_each_own_object(self):
        self.endpoint.reply("GET", "/api/spaces/space",
                            [{"id": "default", "name": "Default"}, {"id": "other", "name": "Other"}])
        one = {"type": "dashboard", "id": "same", "attributes": {"title": "First"}}
        two = dict(one, attributes={"title": "Second"})
        for path, obj in (("/api/saved_objects/_export", one),
                          ("/s/other/api/saved_objects/_export", two)):
            self.endpoint.reply("GET", path.replace("/api/saved_objects/_export", "/app/home"), b"<html></html>")
            self.endpoint.reply("POST", path, (json.dumps(obj) + "\n").encode())
            self.endpoint.reply("POST", path.replace("_export", "_bulk_get"), {"saved_objects": [
                {"type": "config", "id": "8.16.1", "attributes": {"dateFormat": "YYYY-MM-DD"}},
                {"type": "config-global", "id": "8.16.1", "error": {"statusCode": 404}},
            ]})
        inventory = history.kibana_inventory(self.endpoint.url, version="8.16.1")
        self.assertEqual(inventory["default"]["objects"]["dashboard:same"]["attributes"]["title"], "First")
        self.assertEqual(inventory["other"]["objects"]["dashboard:same"]["attributes"]["title"], "Second")
        self.assertEqual(inventory["other"]["objects"]["config:8.16.1"]["attributes"]["dateFormat"], "YYYY-MM-DD")
        history.verify_kibana(inventory, json.loads(json.dumps(inventory)),
                              source_version="8.16.1", target_version="8.16.1")
        damaged = json.loads(json.dumps(inventory))
        damaged["other"]["objects"].clear()
        with self.assertRaises(migration.MigrationError):
            history.verify_kibana(inventory, damaged, source_version="8.16.1", target_version="8.16.1")

    def test_an_export_older_than_the_real_time_settings_is_read_again(self):
        # Kibana exports through a search, which sees a settings write only after the next refresh
        self.endpoint.reply("GET", "/api/spaces/space", [{"id": "default"}])
        self.endpoint.reply("GET", "/app/home", b"<html></html>")
        old = {"type": "config", "id": "8.16.1", "attributes": {"dateFormat": "old"}}
        new = dict(old, attributes={"dateFormat": "YYYY-MM-DD"})
        missing_global = {"type": "config-global", "id": "8.16.1", "error": {"statusCode": 404}}
        for exported in (old, new):
            self.endpoint.reply("POST", "/api/saved_objects/_export", (json.dumps(exported) + "\n").encode())
            self.endpoint.reply("POST", "/api/saved_objects/_bulk_get", {"saved_objects": [new, missing_global]})
        with mock.patch.object(history.time, "sleep") as sleep:
            inventory = history.kibana_inventory(self.endpoint.url, version="8.16.1")
        self.assertEqual(inventory["default"]["objects"]["config:8.16.1"]["attributes"]["dateFormat"],
                         "YYYY-MM-DD")
        sleep.assert_called_once()
        for _ in range(history.KIBANA_INVENTORY_READS):
            self.endpoint.reply("POST", "/api/saved_objects/_export", (json.dumps(old) + "\n").encode())
            self.endpoint.reply("POST", "/api/saved_objects/_bulk_get", {"saved_objects": [new, missing_global]})
        self.endpoint.reply("GET", "/api/spaces/space", [{"id": "default"}])
        self.endpoint.reply("GET", "/app/home", b"<html></html>")
        with mock.patch.object(history.time, "sleep"), \
                self.assertRaisesRegex(migration.MigrationError, "conflicting"):
            history.kibana_inventory(self.endpoint.url, version="8.16.1")

    def test_settings_read_failures_cannot_be_mistaken_for_absent_preferences(self):
        self.endpoint.reply("GET", "/api/spaces/space", [{"id": "default"}])
        self.endpoint.reply("GET", "/app/home", b"<html></html>")
        self.endpoint.reply("POST", "/api/saved_objects/_export", b"")
        config = {"type": "config", "id": "8.16.1", "attributes": {}}
        missing_global = {"type": "config-global", "id": "8.16.1", "error": {"statusCode": 404}}
        for objects in (
            [dict(config, error={"statusCode": 404}), missing_global],
            [config, dict(missing_global, error={"statusCode": 403})],
            [config],
            [config, config],
            [config, dict(missing_global, id="8.16.0")],
        ):
            with self.subTest(objects=objects):
                self.endpoint.reply("POST", "/api/saved_objects/_bulk_get", {"saved_objects": objects})
                with self.assertRaises(migration.MigrationError):
                    history.kibana_inventory(self.endpoint.url, version="8.16.1")

    def test_restore_acceptance_checks_the_actual_index_set_counts_and_saved_samples(self):
        self.assertTrue(callable(getattr(history, "verify_restored", None)),
                        "the combined restore acceptance check is missing")
        metadata = {"indices": [INDEX], "inventory": INVENTORY, "kibana": {}, "kibana_version": None}
        client = snapshots.SnapshotClient(self.endpoint.url)
        path = "/_cat/indices?format=json&expand_wildcards=all&h=index,status"
        self.endpoint.reply("GET", "/", {"version": {"number": "9.5.4"}})
        self.endpoint.reply("GET", path, [{"index": name, "status": "open"} for name in
                                         (INDEX, "logstash-unexpected")])
        with self.assertRaisesRegex(migration.MigrationError, "index set"):
            history.verify_restored(client, metadata, target_version="9.5.4")
        for corruption in ("count", "sample", None):
            with self.subTest(corruption=corruption):
                self.endpoint.reply("GET", "/", {"version": {"number": "9.5.4"}})
                self.endpoint.reply("GET", path, [{"index": INDEX, "status": "open"}])
                stats = copy.deepcopy(STATS)
                if corruption == "count":
                    stats["hits"]["total"]["value"] = 2
                self.endpoint.inventory(stats)
                if corruption != "count":
                    self.endpoint.reply("POST", f"/{INDEX}/_mget", {"docs": [{
                        "_id": "one", "_source": {"type": "Changed" if corruption == "sample" else "Cowrie"},
                        "found": True,
                    }]})
                if corruption:
                    with self.assertRaises(snapshots.SnapshotError):
                        history.verify_restored(client, metadata, target_version="9.5.4")
                else:
                    history.verify_restored(client, metadata, target_version="9.5.4")

    def test_repository_archive_streams_nested_files_and_refuses_to_merge(self):
        repo = self.root / "repository"
        repo.mkdir()
        (repo / "indices" / "one").mkdir(parents=True)
        (repo / "indices" / "one" / "blob").write_bytes(b"snapshot bytes")
        (repo / "index-0").write_bytes(b"metadata")
        archive = self.root / "repository.tar"
        history.pack_repository(str(repo), str(archive))
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        target = self.root / "restored"
        history.unpack_repository(str(archive), str(target))
        self.assertEqual((target / "indices" / "one" / "blob").read_bytes(), b"snapshot bytes")
        with self.assertRaises(migration.MigrationError):
            history.unpack_repository(str(archive), str(target))
        self.assertEqual((target / "index-0").read_bytes(), b"metadata")

    def test_kibana_acceptance_checks_active_preferences_not_only_the_retained_old_config(self):
        config = {"id": "8.16.1", "type": "config", "references": [],
                  "attributes": {"buildNum": 100, "dateFormat": "YYYY-MM-DD"}}
        before = {"default": {"space": {"id": "default"}, "objects": {"config:8.16.1": config}}}
        after = copy.deepcopy(before)
        after["default"]["objects"]["config:9.5.4"] = dict(
            config, id="9.5.4", attributes={"buildNum": 200, "dateFormat": "YYYY"})
        with self.assertRaisesRegex(migration.MigrationError, "config"):
            history.verify_kibana(before, after, source_version="8.16.1", target_version="9.5.4")
        after["default"]["objects"]["config:9.5.4"]["attributes"]["dateFormat"] = "YYYY-MM-DD"
        history.verify_kibana(before, after, source_version="8.16.1", target_version="9.5.4")
        del after["default"]["objects"]["config:9.5.4"]
        with self.assertRaisesRegex(migration.MigrationError, "config"):
            history.verify_kibana(before, after, source_version="8.16.1", target_version="9.5.4")

    def test_large_repository_does_not_exceed_the_outer_manifest_file_limit(self):
        repo = self.root / "repository"
        repo.mkdir()
        for number in range(10001):
            (repo / str(number)).write_bytes(b"blob")
        archive = self.root / "repository.tar"
        history.pack_repository(str(repo), str(archive))
        package = self.root / "package.tar"
        manifest = migration.create_package(str(package), SOURCE, ["history"],
                                             {"history/repository.tar": str(archive)})
        self.assertEqual(len(manifest["files"]), 1)
        with tarfile.open(archive, "r|") as handle:
            self.assertEqual(sum(1 for _member in handle), 10001)

    def test_unsafe_inner_members_are_rejected_before_any_extraction(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE),
                           ("indices/link", tarfile.SYMTYPE), ("indices/hard", tarfile.LNKTYPE)):
            with self.subTest(name=name):
                archive = self.root / "bad.tar"
                with tarfile.open(archive, "w") as handle:
                    good = tarfile.TarInfo("index-0")
                    good.size = 4
                    handle.addfile(good, io.BytesIO(b"safe"))
                    bad = tarfile.TarInfo(name)
                    bad.type = kind
                    bad.linkname = "/outside"
                    handle.addfile(bad)
                target = self.root / "target"
                with self.assertRaises(migration.MigrationError):
                    history.unpack_repository(str(archive), str(target))
                self.assertFalse(target.exists())


class HistoryPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-history-prepare-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / "target"
        write_target_versions(self.target)
        (self.target / "env.example").write_text(
            "TPOT_DATA_PATH=./data\nTPOT_ES_RETENTION=30\nTPOT_SENSOR_STATUS_RETENTION=7\n")
        self.state = self.root / "state"
        self.package = self.root / "package.tar"
        self.source = copy.deepcopy(SOURCE)
        self.metadata = history_metadata()

    def package_history(self, *, repository_name="index-0", metadata_bytes=None):
        archive = self.root / "repository.tar"
        with tarfile.open(archive, "w") as stream:
            info = tarfile.TarInfo(repository_name)
            info.size = 8
            stream.addfile(info, io.BytesIO(b"snapshot"))
        inventory = self.root / "inventory.json"
        inventory.write_bytes(metadata_bytes if metadata_bytes is not None else json.dumps(self.metadata).encode())
        config = self.root / "source.env"
        config.write_text("TPOT_TYPE=HIVE\n")
        if self.package.exists():
            self.package.unlink()
        self.manifest = migration.create_package(str(self.package), self.source,
            ("configuration", "identity", "history"), {
                "configuration/.env": str(config), "configuration/compose.yml": str(config),
                "history/inventory.json": str(inventory), "history/repository.tar": str(archive),
            })

    def prepare(self):
        self.assertTrue(callable(getattr(history, "prepare_history", None)),
                        "history package preflight is not implemented")
        return history.prepare_history(str(self.target), str(self.state), self.manifest, str(self.package))

    def test_report_plans_the_full_route_without_writing_target_data_or_disclosing_objects(self):
        self.package_history()
        report = self.prepare()
        self.assertEqual(report["route"], ["8.19.23", "9.5.4"])
        self.assertEqual(report["indices"], 1)
        self.assertEqual(report["documents"], 3)
        self.assertEqual(report["first"], "2024-04-01T01:00:00.000Z")
        self.assertEqual(report["last"], "2024-04-01T03:00:00.000Z")
        self.assertEqual((report["spaces"], report["objects"]), (1, 2))
        self.assertEqual(report["retention"], {"events": "30", "sensor_history": "7"})
        self.assertNotIn("private dashboard title", json.dumps(report))
        self.assertFalse(self.state.exists())
        self.assertFalse((self.target / "data").exists())

    def test_malformed_or_inconsistent_inventory_is_refused_before_target_changes(self):
        original = copy.deepcopy(self.metadata)
        variants = []
        for value in (True, -1, "3"):
            changed = copy.deepcopy(original)
            changed["inventory"][INDEX]["count"] = value
            variants.append(changed)
        changed = copy.deepcopy(original)
        changed["indices"] = ["logstash-*"]
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed["inventory"][INDEX]["samples"] *= 2
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed["elasticsearch_version"] = "8.19.23"
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed["inventory"][INDEX]["last"] = "2023-04-01T00:00:00Z"
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed["include_global_state"] = True
        variants.append(changed)
        for value in variants:
            with self.subTest(value=value):
                self.metadata = value
                self.package_history()
                with self.assertRaises(migration.MigrationError):
                    self.prepare()
                self.assertFalse(self.state.exists())
                self.assertFalse((self.target / "data").exists())

    def test_inner_archive_and_duplicate_json_keys_are_checked_without_extraction(self):
        for name, raw in (("../outside", None),
                          ("index-0", json.dumps(self.metadata).replace('"schema": 2', '"schema": 2, "schema": 2').encode())):
            with self.subTest(name=name, duplicate=raw is not None):
                self.package_history(repository_name=name, metadata_bytes=raw)
                with self.assertRaises(migration.MigrationError):
                    self.prepare()
                self.assertFalse((self.root / "outside").exists())
                self.assertFalse(self.state.exists())

    def test_missing_active_settings_and_old_incomplete_history_schema_are_refused(self):
        self.metadata["kibana"]["default"]["objects"].pop("config:8.16.1")
        for schema in (2, 1):
            with self.subTest(schema=schema):
                self.metadata["schema"] = schema
                self.package_history()
                with self.assertRaises(migration.MigrationError):
                    self.prepare()
                self.assertFalse(self.state.exists())

    def test_unsupported_creation_versions_and_missing_target_kibana_are_refused(self):
        self.metadata["inventory"][INDEX]["created"] = "7170099"
        self.package_history()
        with self.assertRaisesRegex(migration.MigrationError, "reindex"):
            self.prepare()
        self.metadata["inventory"][INDEX]["created"] = "8518000"
        (self.target / "docker/elk/kibana/Dockerfile").unlink()
        self.package_history()
        with self.assertRaises(migration.MigrationError):
            self.prepare()

    def test_exact_disk_budget_includes_staging_repository_intermediate_and_target(self):
        self.package_history()
        required = self.package.stat().st_size + 5 * 8 + 512 * 1024 * 1024
        with mock.patch.object(history.shutil, "disk_usage", return_value=SimpleNamespace(free=required - 1)):
            with self.assertRaisesRegex(migration.MigrationError, "space"):
                self.prepare()
        with mock.patch.object(history.shutil, "disk_usage", return_value=SimpleNamespace(free=required)):
            self.assertEqual(self.prepare()["storage"]["required_bytes"], required)

    def test_empty_events_without_kibana_use_the_direct_route(self):
        self.source["kibana_version"] = None
        self.metadata.update(kibana_version=None, kibana={}, indices=[], inventory={})
        self.package_history()
        report = self.prepare()
        self.assertEqual(report["route"], ["9.5.4"])
        self.assertEqual((report["indices"], report["documents"], report["spaces"]), (0, 0, 0))

    def test_private_staging_cannot_overlap_productive_data(self):
        self.package_history()
        self.state = self.target / "data" / "private-stage"
        with self.assertRaisesRegex(migration.MigrationError, "overlap"):
            self.prepare()
        self.assertFalse((self.target / "data").exists())


class HistoryImportFixture(ImportFixture):
    """An import fixture whose package carries the Elastic history next to configuration and identity."""

    ADOPTED = ("  nginx: {}\n  elasticsearch:\n    container_name: elasticsearch\n"
               "  kibana:\n    container_name: kibana\n")

    def setUp(self):
        super().setUp()
        write_target_versions(self.target)
        self.metadata = history_metadata()
        self.make_history_package()

    def make_history_package(self, *, identity=True, metadata_bytes=None):
        archive = self.source_dir / "repository.tar"
        if archive.exists():
            archive.unlink()
        with tarfile.open(archive, "w") as stream:
            info = tarfile.TarInfo("index-0")
            info.size = 8
            stream.addfile(info, io.BytesIO(b"snapshot"))
        inventory = self.source_dir / "inventory.json"
        inventory.write_bytes(metadata_bytes if metadata_bytes is not None
                              else json.dumps(self.metadata).encode())
        files = {"configuration/.env": str(self.source_env),
                 "configuration/compose.yml": str(self.source_compose),
                 "history/inventory.json": str(inventory), "history/repository.tar": str(archive)}
        if identity:
            files["identity/uuid"] = str(self.identity)
        source = dict(SOURCE, architecture=migration_import._architecture())
        if self.package.exists():
            self.package.unlink()
        self.manifest = migration.create_package(
            str(self.package), source, ("configuration", "identity", "history"), files)


class HistoryImportTest(HistoryImportFixture, unittest.TestCase):
    def test_history_package_is_prepared_with_its_restore_report(self):
        self.stage()
        report = self.prepare()

        self.assertEqual(report["history"]["route"], ["8.19.23", "9.5.4"])
        self.assertEqual((report["history"]["indices"], report["history"]["documents"]), (1, 3))
        self.assertEqual(report["history"]["objects"], 2)
        self.assertNotIn("private dashboard title", json.dumps(report))
        self.assertFalse((self.target / "data").exists())

    def test_history_without_identity_or_with_invalid_metadata_is_not_staged(self):
        for kwargs in ({"identity": False}, {"metadata_bytes": b'{"format": "tpot-migration-history"}'}):
            with self.subTest(**{key: bool(value) for key, value in kwargs.items()}):
                self.make_history_package(**kwargs)
                with self.assertRaises(migration.MigrationError):
                    self.stage()
                self.assertFalse(self.state.exists())

    def test_restored_kibana_objects_are_kept_when_the_source_had_no_mode(self):
        self.stage()
        self.prepare()
        migration_import.apply_import(str(self.target), str(self.state))

        self.assertEqual(EnvFile(str(self.target / ".env")).get("TPOT_KIBANA_OBJECTS"), "keep")

    def test_a_kibana_mode_of_the_source_is_carried_over(self):
        with self.source_env.open("a", encoding="utf-8") as handle:
            handle.write("TPOT_KIBANA_OBJECTS=add\n")
        self.make_history_package()
        self.stage()
        self.prepare()
        migration_import.apply_import(str(self.target), str(self.state))

        self.assertEqual(EnvFile(str(self.target / ".env")).get("TPOT_KIBANA_OBJECTS"), "add")

    def test_prepared_history_report_must_match_the_package(self):
        self.stage()
        self.prepare()
        prepared = self.state / migration_import.PREPARED_NAME / "prepared.json"
        report = json.loads(prepared.read_text(encoding="utf-8"))
        report.pop("history")
        prepared.write_text(json.dumps(report), encoding="utf-8")

        with self.assertRaisesRegex(migration.MigrationError, "history"):
            migration_import.apply_import(str(self.target), str(self.state))
        self.assertFalse((self.target / ".env").exists())


class SourceHistoryGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-history-gate-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"
        (self.data / "elk" / "data").mkdir(parents=True)
        self.endpoint = Endpoint()
        self.addCleanup(self.endpoint.close)
        self.journal = runtime.Journal(str(self.root / "run"), {"operation": "test"})
        self.journal.__enter__()
        self.addCleanup(self.journal.__exit__, None, None, None)
        self.source = dict(SOURCE, data_path=str(self.data), kibana_version=None)
        self.config = {"name": "tpotce", "services": {
            "elasticsearch": {"container_name": "elasticsearch"},
            "tpotinit": {"container_name": "tpotinit"},
        }}
        self.container = {
            "Id": "a" * 64, "Name": "/elasticsearch", "Image": "sha256:" + "b" * 64,
            "Config": {"Labels": {"com.docker.compose.project": "tpotce"}},
            "State": {"Running": True},
            "NetworkSettings": {"Ports": {"9200/tcp": [
                {"HostIp": "127.0.0.1", "HostPort": self.endpoint.url.rsplit(":", 1)[1]}]}},
            "Mounts": [{"Type": "bind", "Source": str(self.data), "Destination": "/data", "RW": True}],
        }
        self.running = []
        self.commands = []
        self.kibana = None
        self.kibana_binary_version = "9.5.4"

        def execute(*args, **_options):
            self.commands.append(args)
            if args[1:3] == ("container", "inspect"):
                return json.dumps([self.kibana if args[-1] == "kibana" else self.container])
            if args[1:] == ("exec", "kibana", "/usr/share/kibana/bin/kibana", "--version"):
                return self.kibana_binary_version + "\n"
            if args[1:3] == ("ps", "--quiet"):
                return "\n".join(self.running)
            if args[1:4] == ("inspect", "--format", "{{json .Mounts}}"):
                return json.dumps(self.container["Mounts"])
            raise AssertionError("unexpected command: " + repr(args))

        self.docker = runtime.DockerRuntime(self.journal, execute=execute)

    def inventory(self):
        return migration.SourceInventory(self.source, {}, compose=self.config)

    def test_source_plan_binds_the_runtime_version_image_and_actual_data_mount(self):
        self.endpoint.reply("GET", "/", {"version": {"number": "8.16.1"}})
        self.endpoint.reply("GET", "/_cluster/health?local=true", {"number_of_nodes": 1})
        self.endpoint.reply("GET", "/_nodes/_local/settings?flat_settings=true", {
            "nodes": {"node": {"settings": {"path.data": ["/data/elk/data"]}}},
        })
        plan = history.source_plan(self.inventory(), self.docker)
        self.assertEqual(plan["es_data"], str(self.data / "elk" / "data"))
        self.assertEqual(plan["es_image"], "sha256:" + "b" * 64)
        self.assertEqual(plan["es_url"], self.endpoint.url)
        self.assertFalse(any("stop" in command for command in self.commands))

    def test_a_multi_node_source_is_not_mistaken_for_a_single_local_node(self):
        self.endpoint.reply("GET", "/", {"version": {"number": "8.16.1"}})
        self.endpoint.reply("GET", "/_cluster/health?local=true", {"number_of_nodes": 2})
        self.endpoint.reply("GET", "/_nodes/_local/settings?flat_settings=true", {
            "nodes": {"node": {"settings": {"path.data": ["/data/elk/data"]}}},
        })
        with self.assertRaises(migration.MigrationError):
            history.source_plan(self.inventory(), self.docker)

    def test_redacted_kibana_status_requires_the_running_containers_binary_version(self):
        self.source.update(elasticsearch_version="9.5.4", kibana_version="9.5.4")
        self.source["image_digests"] = dict(self.source["image_digests"], kibana="sha256:" + "c" * 64)
        self.config["services"]["kibana"] = {"container_name": "kibana"}
        self.kibana = dict(self.container, Name="/kibana", Image="sha256:" + "c" * 64,
                           NetworkSettings={"Ports": {"5601/tcp": [
                               {"HostIp": "127.0.0.1", "HostPort": self.endpoint.url.rsplit(":", 1)[1]}]}})
        for _ in range(2):
            self.endpoint.reply("GET", "/", {"version": {"number": "9.5.4"}})
            self.endpoint.reply("GET", "/_cluster/health?local=true", {"number_of_nodes": 1})
            self.endpoint.reply("GET", "/_nodes/_local/settings?flat_settings=true", {
                "nodes": {"node": {"settings": {"path.data": ["/data/elk/data"]}}},
            })
            self.endpoint.reply("GET", "/api/status", {"status": {"overall": {"level": "available"}}})
        self.assertEqual(history.source_plan(self.inventory(), self.docker)["kibana_image"],
                         "sha256:" + "c" * 64)
        self.kibana_binary_version = "9.5.3"
        with self.assertRaisesRegex(migration.MigrationError, "version"):
            history.source_plan(self.inventory(), self.docker)

    def test_a_same_named_container_without_compose_ownership_is_refused(self):
        self.container["Config"]["Labels"] = None
        with self.assertRaises(migration.MigrationError):
            history.source_plan(self.inventory(), self.docker)
        self.assertEqual(self.endpoint.calls, [])

    def test_external_elasticsearch_configuration_is_not_silently_omitted(self):
        self.container["Mounts"].append({
            "Type": "bind", "Source": str(self.root / "custom-config"),
            "Destination": "/usr/share/elasticsearch/config", "RW": False,
        })
        self.endpoint.reply("GET", "/", {"version": {"number": "8.16.1"}})
        self.endpoint.reply("GET", "/_cluster/health?local=true", {"number_of_nodes": 1})
        self.endpoint.reply("GET", "/_nodes/_local/settings?flat_settings=true", {
            "nodes": {"node": {"settings": {"path.data": ["/data/elk/data"]}}},
        })
        with self.assertRaises(migration.MigrationError):
            history.source_plan(self.inventory(), self.docker)
        self.assertFalse(any("stop" in command for command in self.commands))

    def test_an_image_changed_under_the_tag_is_refused_before_the_service_stops(self):
        self.container["Image"] = "sha256:" + "c" * 64
        with self.assertRaises(migration.MigrationError):
            history.source_plan(self.inventory(), self.docker)
        self.assertEqual(self.endpoint.calls, [])
        self.assertFalse(any("stop" in command for command in self.commands))

    def test_remaining_writer_is_refused_without_stopping_foreign_containers(self):
        self.running = ["another-containers-id"]
        with self.assertRaises(migration.MigrationError):
            history.assert_data_idle(str(self.data / "elk" / "data"), self.docker)
        self.assertFalse(any("stop" in command or "rm" in command for command in self.commands))
        self.running = []
        history.assert_data_idle(str(self.data / "elk" / "data"), self.docker)

    def test_private_tmpfs_is_not_mistaken_for_a_host_data_writer(self):
        self.running = ["another-containers-id"]
        self.container["Mounts"] = [{"Type": "tmpfs", "Destination": "/tmp", "RW": True}]
        history.assert_data_idle(str(self.data / "elk" / "data"), self.docker)


class TargetDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-target-data-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / "target/elk/data"
        self.identity = {"operation": "import", "target": str(self.root / "target")}

    def test_only_the_claimed_directory_can_be_used_by_the_permission_helper_after_restart(self):
        with runtime.Journal(str(self.root / "run"), self.identity) as journal:
            docker = runtime.DockerRuntime(journal)
            self.assertTrue(callable(getattr(docker, "target_directory", None)),
                            "owned target data directories are not implemented")
            docker.target_directory(str(self.target))
            (self.target / "state").write_bytes(b"restored data")
        with runtime.Journal(str(self.root / "run"), self.identity) as journal:
            docker = runtime.DockerRuntime(journal)
            self.assertEqual(docker.target_directory(str(self.target)), str(self.target))
            self.assertEqual(docker._private_path(str(self.target)), self.target)
            saved = self.target.with_name("saved")
            self.target.rename(saved)
            self.target.mkdir()
            with self.assertRaises(runtime.RuntimeError):
                docker.target_directory(str(self.target))
            with self.assertRaises(runtime.RuntimeError):
                docker._private_path(str(self.target))
            self.assertEqual((saved / "state").read_bytes(), b"restored data")

    def test_existing_data_and_unclaimed_permission_paths_are_never_taken_over(self):
        self.target.mkdir(parents=True)
        (self.target / "foreign").write_bytes(b"keep")
        with runtime.Journal(str(self.root / "run"), self.identity) as journal:
            docker = runtime.DockerRuntime(journal)
            self.assertTrue(callable(getattr(docker, "target_directory", None)))
            with self.assertRaises(runtime.RuntimeError):
                docker.target_directory(str(self.target))
            with self.assertRaises(runtime.RuntimeError):
                docker._private_path(str(self.target))
            self.assertNotIn("target_data", journal.state["facts"])
        self.assertEqual((self.target / "foreign").read_bytes(), b"keep")

    def test_uncheckpointed_empty_directory_can_resume_but_populated_or_linked_one_cannot(self):
        with runtime.Journal(str(self.root / "run"), self.identity) as journal:
            journal.record("held", target_data={"path": str(self.target), "state": "creating"})
            self.target.mkdir(mode=0o700, parents=True)
            (self.target / "foreign").write_bytes(b"keep")
            docker = runtime.DockerRuntime(journal)
            self.assertTrue(callable(getattr(docker, "target_directory", None)))
            with self.assertRaises(runtime.RuntimeError):
                docker.target_directory(str(self.target))
            saved = self.target.with_name("linked-data")
            self.target.rename(saved)
            self.target.symlink_to(saved, target_is_directory=True)
            with self.assertRaises(runtime.RuntimeError):
                docker.target_directory(str(self.target))
            self.target.unlink()
            saved.rename(self.target)
            (self.target / "foreign").unlink()
            self.assertEqual(docker.target_directory(str(self.target)), str(self.target))


class SystemdGuardTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runtime, "the migration runtime is missing")
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-history-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.unit = "tpot.service"
        self.run_id = "a" * 32
        self.dropin = self.root / "etc/systemd/system/tpot.service.d/90-tpot-migration.conf"

    def guard(self, action, **kwargs):
        return runtime.systemd_guard(str(self.root), self.unit, kwargs.get("run_id", self.run_id),
                                     os.getuid(), action)

    def test_guard_persists_until_its_owner_explicitly_releases_it(self):
        self.assertFalse(self.guard("status"))
        self.assertTrue(self.guard("hold"))
        before = self.dropin.read_bytes()
        self.assertEqual(stat.S_IMODE(self.dropin.stat().st_mode), 0o644)
        self.assertIn(b"[Unit]\nConditionPathExists=", before)
        self.assertTrue(self.guard("hold"))
        self.assertEqual(self.dropin.read_bytes(), before)
        self.assertTrue(self.guard("status"))
        self.assertFalse(self.guard("release"))
        self.assertFalse(self.dropin.exists())

    def test_another_run_and_foreign_dropin_cannot_be_overwritten_or_removed(self):
        self.guard("hold")
        original = self.dropin.read_bytes()
        for action in ("status", "hold", "release"):
            with self.subTest(action=action), self.assertRaises(runtime.RuntimeError):
                self.guard(action, run_id="b" * 32)
            self.assertEqual(self.dropin.read_bytes(), original)
        self.dropin.write_text("[Service]\nEnvironment=OWN=yes\n")
        for action in ("hold", "release"):
            with self.subTest(action=action), self.assertRaises(runtime.RuntimeError):
                self.guard(action)
            self.assertEqual(self.dropin.read_text(), "[Service]\nEnvironment=OWN=yes\n")

    def test_symlinked_guard_parent_and_preexisting_approval_are_refused(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (self.root / "etc").symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(runtime.RuntimeError):
            self.guard("hold")
        self.assertEqual(list(foreign.iterdir()), [])
        (self.root / "etc").unlink()
        self.guard("hold")
        text = self.dropin.read_text()
        approved = text.split("ConditionPathExists=", 1)[1].strip()
        (self.root / approved.lstrip("/")).touch()
        with self.assertRaises(runtime.RuntimeError):
            self.guard("status")

    def test_guard_rejects_unsafe_unit_or_run_before_creating_files(self):
        for unit, run_id in (("../other.service", self.run_id), (self.unit, "../run"),
                              ("tpot.service\n[Service]", self.run_id)):
            with self.subTest(unit=unit, run_id=run_id), self.assertRaises(runtime.RuntimeError):
                runtime.systemd_guard(str(self.root), unit, run_id, os.getuid(), "hold")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_systemd_hold_is_loaded_before_stop_and_password_never_enters_argv(self):
        password = self.root / "password"
        password.write_text("private sudo password\n")
        password.chmod(0o600)
        commands = []

        def command(args, **kwargs):
            commands.append((args, kwargs.get("input")))
            if "--guard" in args:
                result = {"held": True}
                return subprocess.CompletedProcess(args, 0, json.dumps(result), "")
            if args[-4:] == ["show", "--property=ActiveState", "--value", "tpot.service"]:
                return subprocess.CompletedProcess(args, 0, "inactive\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch("subprocess.run", side_effect=command):
            host = runtime.SystemdHold(self.run_id, become_file=str(password))
            host.hold_and_stop()
        calls = [args for args, _stdin in commands]
        hold_at = next(i for i, args in enumerate(calls) if "--guard" in args and args[-1] == "hold")
        reload_at = next(i for i, args in enumerate(calls) if args[-2:] == ["systemctl", "daemon-reload"])
        stop_at = next(i for i, args in enumerate(calls) if args[-3:] == ["systemctl", "stop", "tpot.service"])
        self.assertLess(hold_at, reload_at)
        self.assertLess(reload_at, stop_at)
        self.assertTrue(all("private sudo password" not in " ".join(args) for args in calls))
        self.assertTrue(all(stdin == "private sudo password\n" for _args, stdin in commands))
        self.assertNotIn("private sudo password", repr(host))

    def test_a_long_migration_needs_sudo_that_does_not_rest_on_its_cached_timestamp(self):
        # a snapshot or packaging of hours calls no sudo while the cached timestamp expires
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1, "", "a password is required")

        with mock.patch("subprocess.run", side_effect=run), \
                self.assertRaisesRegex(runtime.RuntimeError, "-B FILE"):
            runtime.SystemdHold(self.run_id).check_access()
        self.assertEqual(calls, [["sudo", "-n", "-k", "true"]])
        password = self.root / "password"
        password.write_text("private sudo password\n")
        password.chmod(0o600)
        with mock.patch("subprocess.run", side_effect=AssertionError("sudo -S needs no cache")):
            runtime.SystemdHold(self.run_id, become_file=str(password)).check_access()

    def test_stop_failure_never_releases_the_persistent_guard(self):
        commands = []

        def command(args, **kwargs):
            commands.append(args)
            if "--guard" in args:
                return subprocess.CompletedProcess(args, 0, '{"held":true}', "")
            if "stop" in args:
                return subprocess.CompletedProcess(args, 1, "", "stop failed")
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch("subprocess.run", side_effect=command), self.assertRaises(runtime.RuntimeError):
            runtime.SystemdHold(self.run_id).hold_and_stop()
        self.assertFalse(any("--guard" in args and args[-1] == "release" for args in commands))

    def test_target_hold_precedes_unit_creation_without_stopping_a_missing_service(self):
        def command(*args):
            if "--guard" in args:
                return json.dumps({"held": self.guard(args[-1])})
            self.assertEqual(args, ("systemctl", "daemon-reload"))
            self.assertTrue(self.guard("status"))
            return ""

        host = runtime.SystemdHold(self.run_id)
        self.assertTrue(callable(getattr(host, "hold", None)), "the pre-install target hold is missing")
        with mock.patch.object(host, "command", side_effect=command):
            host.hold()
        self.assertTrue(self.guard("status"))
        self.assertFalse((self.dropin.parent.parent / self.unit).exists())

    def test_target_docker_does_not_depend_on_the_installers_old_group_session(self):
        self.assertTrue(callable(getattr(history, "_system_docker", None)),
                        "a system Docker executor independent of the user's selected daemon is missing")
        host = runtime.SystemdHold(self.run_id)

        def command(*args, **_options):
            self.assertEqual(args, ("env", "-u", "DOCKER_HOST", "-u", "DOCKER_CONTEXT",
                                    "docker", "--host", "unix:///var/run/docker.sock",
                                    "info", "--format", "{{.ID}}"))
            return "system-engine\n"

        with runtime.Journal(str(self.root / "run"), {"operation": "import"}) as journal, \
                mock.patch.object(host, "command", side_effect=command), \
                mock.patch.object(migration, "_run_output", side_effect=AssertionError(
                    "the fresh installer's current Docker group session is not authoritative")):
            docker = history._system_docker(journal, host)
            self.assertEqual(docker.docker("info", "--format", "{{.ID}}").strip(), "system-engine")


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-history-journal-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "run"
        self.identity = {"operation": "export", "source": "/home/operator/tpotce"}

    def test_private_checkpoint_survives_retry_but_not_a_different_source(self):
        with runtime.Journal(str(self.path), self.identity) as run:
            run_id = run.run_id
            run.record("held", source_version="8.16.1")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.path / "state.json").stat().st_mode), 0o600)
        with runtime.Journal(str(self.path), self.identity) as run:
            self.assertEqual(run.run_id, run_id)
            self.assertEqual(run.state["phase"], "held")
            self.assertEqual(run.state["facts"]["source_version"], "8.16.1")
        before = (self.path / "state.json").read_bytes()
        with self.assertRaises(runtime.RuntimeError):
            with runtime.Journal(str(self.path), dict(self.identity, source="/another/host")):
                self.fail("foreign source must not acquire this migration")
        self.assertEqual((self.path / "state.json").read_bytes(), before)

    def test_competing_run_and_linked_or_invalid_state_are_refused(self):
        with runtime.Journal(str(self.path), self.identity):
            with self.assertRaises(runtime.RuntimeError):
                with runtime.Journal(str(self.path), self.identity):
                    self.fail("a second migration must not own the same state")
        state = self.path / "state.json"
        data = json.loads(state.read_text())
        data["run"] = "../another-run"
        state.write_text(json.dumps(data))
        with self.assertRaises(runtime.RuntimeError):
            with runtime.Journal(str(self.path), self.identity):
                self.fail("invalid run identity must not be used")
        state.unlink()
        other = self.path.parent / "foreign"
        other.write_text("untouched")
        state.symlink_to(other)
        with self.assertRaises(runtime.RuntimeError):
            with runtime.Journal(str(self.path), self.identity):
                self.fail("state links must not be followed")
        self.assertEqual(other.read_text(), "untouched")

    def test_only_a_released_run_can_start_a_new_final_export(self):
        with runtime.Journal(str(self.path), self.identity) as run:
            first = run.run_id
            with self.assertRaises(runtime.RuntimeError):
                run.next_run()
            run.record("released", destination="/private/previous-package.tar")
            run.next_run()
            self.assertNotEqual(run.run_id, first)
            self.assertEqual(run.state["phase"], "new")
            self.assertEqual(run.state["facts"], {})
            previous = json.loads((self.path / ("completed-" + first + ".json")).read_text())
            self.assertEqual(previous["facts"]["destination"], "/private/previous-package.tar")
        with runtime.Journal(str(self.path), self.identity) as run:
            self.assertNotEqual(run.run_id, first)


class AdministrationLockTest(unittest.TestCase):
    def test_migration_holds_update_and_check_locks_without_unlinking_them(self):
        with tempfile.TemporaryDirectory(prefix="tpot-history-locks-") as directory:
            home = Path(directory) / "home"
            data = Path(directory) / "data"
            (data / "ews").mkdir(parents=True)
            check_lock = data / "ews" / "check.lock"
            check_lock.touch(mode=0o600)

            def external(args, **kwargs):
                text = "LoadState=not-found\nActiveState=inactive\n" if args[0] == "systemctl" else ""
                return subprocess.CompletedProcess(args, 0, text, "")

            with mock.patch("subprocess.run", side_effect=external):
                with runtime.administration_locks(home, data):
                    update_lock = home / ".local/share/tpotce/update.lock"
                    before = update_lock.stat().st_ino
                    for path in (update_lock, check_lock):
                        with path.open("rb") as handle, self.assertRaises(BlockingIOError):
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(update_lock.stat().st_ino, before)
                for path in (update_lock, check_lock):
                    with path.open("rb") as handle:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_space_budget_rejects_one_byte_below_the_same_filesystem_requirement(self):
        with tempfile.TemporaryDirectory(prefix="tpot-history-space-") as directory:
            with runtime.Journal(directory + "/run", {"operation": "test"}) as journal:
                with mock.patch("shutil.disk_usage", return_value=SimpleNamespace(free=539870911)):
                    with self.assertRaises(migration.MigrationError):
                        history._check_space(1000000, journal, directory + "/package.tar")
                with mock.patch("shutil.disk_usage", return_value=SimpleNamespace(free=539870912)):
                    history._check_space(1000000, journal, directory + "/package.tar")


class HistoryExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-history-export-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "home/tpotce"
        self.source.mkdir(parents=True)
        self.data = self.source / "data"
        (self.data / "elk/data").mkdir(parents=True)
        (self.source / ".env").write_text("WEB_USER=private-web-secret\n")
        (self.source / "docker-compose.yml").write_text("# source compose\n")
        self.inventory = migration.SourceInventory(
            dict(SOURCE, data_path=str(self.data), kibana_version=None),
            {"configuration/.env": str(self.source / ".env"),
             "configuration/compose.yml": str(self.source / "docker-compose.yml")})
        self.endpoint = Endpoint()
        self.addCleanup(self.endpoint.close)
        self.client = snapshots.SnapshotClient(self.endpoint.url)
        self.plan = {"es_data": str(self.data / "elk/data"), "es_image": "sha256:" + "b" * 64,
                     "es_url": self.endpoint.url, "project": "tpotce", "kibana_image": None}
        self.original_running = True
        self.maintenance = None
        self.events = []
        self.output = self.root / "migration.tar"
        self.dropin = self.root / "system/etc/systemd/system/tpot.service.d/90-tpot-migration.conf"
        self.dropin.parents[4].mkdir()
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.dict(os.environ, {
            "HOME": str(self.source.parent), "XDG_DATA_HOME": str(self.root / "state")}))
        self.patches.enter_context(mock.patch("platform.system", return_value="Linux"))
        self.patches.enter_context(mock.patch.object(history, "_host_id", return_value="a" * 32, create=True))
        self.patches.enter_context(mock.patch("tpotctl.migration.inspect_source", return_value=self.inventory))
        self.patches.enter_context(mock.patch("tpotctl.migration._run_output", return_value="engine-id\n"))
        self.patches.enter_context(mock.patch("tpotctl.fleet_remote.idle", return_value=None))
        self.patches.enter_context(mock.patch.object(history, "source_plan", return_value=self.plan))
        self.patches.enter_context(mock.patch.object(runtime.SystemdHold, "command",
                                                     autospec=True, side_effect=self.command))
        self.patches.enter_context(mock.patch.object(runtime.DockerRuntime, "elasticsearch",
                                                     autospec=True, side_effect=self.start_maintenance))
        self.patches.enter_context(mock.patch.object(runtime.DockerRuntime, "permissions", return_value=None))
        self.access = self.patches.enter_context(mock.patch.object(runtime.SystemdHold, "check_access",
                                                                   autospec=True))

    def command(self, host, *args, **_options):
        if "--guard" in args:
            self.events.append("guard-" + args[-1])
            held = runtime.systemd_guard(str(self.root / "system"), host.unit, host.run_id,
                                         os.getuid(), args[-1])
            return json.dumps({"held": held})
        if args[0] == "systemctl":
            if "ExecStart" in " ".join(args):
                return "{ argv[]=/usr/bin/docker compose -f " + str(self.source / "docker-compose.yml") + " up ; }"
            if "stop" in args:
                self.assertTrue(self.dropin.exists())
                self.original_running = False
                self.events.append("source-stop")
            if "start" in args:
                self.assertFalse(self.dropin.exists())
                self.original_running = True
                self.events.append("source-start")
            if "ActiveState" in " ".join(args):
                return "active\n" if self.original_running else "inactive\n"
            return ""
        if "docker" in args:
            command = list(args[args.index("docker") + 1:])
            if command[:1] == ["--host"]:
                command = command[2:]
            if command[:1] == ["info"]:
                return "engine-id\n"
            if command[:2] == ["image", "inspect"]:
                return json.dumps([{"Config": {"User": "2000:2000"}}])
            if command[:2] == ["ps", "--quiet"]:
                return "source-writer" if self.original_running else ""
            if command[:2] == ["container", "ls"]:
                return self.maintenance or ""
            if command[:2] == ["network", "ls"]:
                return ""
            if command[:2] == ["container", "inspect"]:
                return json.dumps([{"Name": "/" + command[-1],
                                    "Config": {"Labels": {"tpot.migration": host.run_id}},
                                    "State": {"Running": True}}])
            if command[:1] == ["stop"] or command[:2] == ["container", "rm"]:
                self.events.append("maintenance-stop")
                self.maintenance = None
                return ""
        self.fail("unexpected external command: " + repr(args))

    def start_maintenance(self, docker, image, version, suffix, data, repositories):
        self.assertTrue(self.dropin.exists())
        self.assertFalse(self.original_running)
        self.events.append("maintenance-start")
        self.maintenance = docker.name(suffix)
        repo = Path(repositories["original"][0])
        (repo / "index-0").write_bytes(b"private synthetic repository")
        return self.maintenance, self.client

    def replies(self, *, partial=False):
        self.endpoint.reply("GET", "/_stats/store?expand_wildcards=all&filter_path=_all.primaries.store.size_in_bytes",
                            {"_all": {"primaries": {"store": {"size_in_bytes": 100}}}})
        for _step in range(2):
            self.endpoint.reply("POST", "/_ilm/stop", {"acknowledged": True})
            self.endpoint.reply("GET", "/_ilm/status", {"operation_mode": "STOPPED"})
        self.endpoint.reply("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index,status",
                            [{"index": INDEX, "status": "open"}])
        self.endpoint.inventory()
        self.endpoint.reply("PUT", "/_snapshot/migration", {"acknowledged": True})
        self.endpoint.reply("POST", "/_snapshot/migration/_verify", {"nodes": {"node": {}}})
        self.endpoint.reply("PUT", "/_snapshot/migration/original?wait_for_completion=false", {"accepted": True})
        self.endpoint.reply("GET", "/_snapshot/migration/original", {"snapshots": [{
            "snapshot": "original", "indices": [INDEX], "feature_states": [],
            "state": "PARTIAL" if partial else "SUCCESS",
            "shards": {"total": 1, "successful": 0 if partial else 1, "failed": 1 if partial else 0},
        }]})
        self.endpoint.inventory()
        self.endpoint.reply("POST", "/" + INDEX + "/_mget", {
            "docs": [{"_id": "one", "_source": {"type": "Cowrie"}, "found": True}],
        })
        self.endpoint.reply("DELETE", "/_snapshot/migration", {"acknowledged": True})

    def test_export_unregisters_its_repository_from_the_source_cluster(self):
        self.replies()
        history.export_history(str(self.source), str(self.output))
        calls = [(method, path) for method, path, _body in self.endpoint.calls]
        self.assertIn(("DELETE", "/_snapshot/migration"), calls)
        self.assertLess(calls.index(("GET", "/_snapshot/migration/original")),
                        calls.index(("DELETE", "/_snapshot/migration")))

    def test_export_without_an_acknowledged_unregistration_publishes_nothing(self):
        self.replies()
        self.endpoint.responses[("DELETE", "/_snapshot/migration")] = [(200, {"acknowledged": False})]
        with self.assertRaisesRegex(migration.MigrationError, "repository removal"):
            history.export_history(str(self.source), str(self.output))
        self.assertFalse(self.output.exists())
        self.assertTrue(self.dropin.exists())
        self.assertFalse(self.original_running)

    def test_export_stops_before_snapshot_and_leaves_the_source_guarded(self):
        self.replies()
        manifest = history.export_history(str(self.source), str(self.output))
        self.assertIn("history", manifest["scope"])
        self.assertTrue(self.dropin.exists())
        self.assertFalse(self.original_running)
        self.assertIsNone(self.maintenance)
        self.assertLess(self.events.index("source-stop"), self.events.index("maintenance-start"))
        self.assertNotIn("source-start", self.events)
        self.assertEqual(migration.validate_package(str(self.output)), manifest)
        metadata, _size = history.read_history(manifest, str(self.output))
        self.assertEqual(metadata["inventory"][INDEX]["count"], 3)
        state = next((self.root / "state").rglob("state.json"))
        self.assertNotIn("private-web-secret", state.read_text())
        self.assertEqual(json.loads(state.read_text())["phase"], "packaged")
        before = self.events.count("maintenance-start")
        self.assertEqual(history.export_history(str(self.source), str(self.output)), manifest)
        self.assertEqual(self.events.count("maintenance-start"), before)

    def snapshot_copies(self):
        folder = next((self.root / "state").rglob("state.json")).parent
        return sorted(entry.name for entry in folder.iterdir() if entry.name.startswith("repository-"))

    def test_a_packaged_export_gives_the_space_of_its_snapshot_copies_back(self):
        # the package holds the snapshot; a second and third copy beside it fill the source's disk
        self.replies()
        manifest = history.export_history(str(self.source), str(self.output))
        self.assertEqual(self.snapshot_copies(), [])
        leftover = next((self.root / "state").rglob("state.json")).parent / ("repository-" + "c" * 32)
        leftover.mkdir()
        (leftover / "index-0").write_bytes(b"left by an interrupted cleanup")

        self.assertEqual(history.export_history(str(self.source), str(self.output)), manifest)

        self.assertEqual(self.snapshot_copies(), [])
        self.assertEqual(migration.validate_package(str(self.output)), manifest)

    def test_resume_gives_the_space_of_the_snapshot_copy_back(self):
        self.failed_export()
        self.assertNotEqual(self.snapshot_copies(), [])
        self.resume_replies()

        history.resume_source(str(self.source))

        self.assertEqual(self.snapshot_copies(), [])

    def test_export_refuses_sudo_that_only_its_cache_allows_before_it_holds_the_source(self):
        self.access.side_effect = runtime.RuntimeError("sudo needs a password here; use -B FILE")
        with self.assertRaisesRegex(migration.MigrationError, "-B FILE"):
            history.export_history(str(self.source), str(self.output))
        self.assertEqual(self.events, [])
        self.assertTrue(self.original_running)
        self.assertFalse(self.dropin.exists())

    def test_export_does_not_publish_metadata_the_importer_cannot_read(self):
        self.replies()
        with mock.patch.object(history, "MAX_HISTORY_METADATA", 64):
            with self.assertRaisesRegex(migration.MigrationError, "metadata size"):
                history.export_history(str(self.source), str(self.output))
        self.assertFalse(self.output.exists())
        self.assertTrue(self.dropin.exists())
        self.assertFalse(self.original_running)

    def test_old_draft_checkpoint_is_not_republished_or_reported_as_a_complete_export(self):
        self.replies()
        history.export_history(str(self.source), str(self.output))
        filename = next((self.root / "state").rglob("state.json"))
        state = json.loads(filename.read_text())
        facts = state["facts"]
        inventory_file = filename.parent / facts["metadata"]
        metadata = json.loads(inventory_file.read_text())
        metadata["schema"] = 1
        inventory_file.write_text(json.dumps(metadata))
        files = {key: str(filename.parent / path) for key, path in facts["files"].items()}
        # the export gave the space of its archive back; the package still holds it
        repository = self.root / "repository.tar"
        with tarfile.open(self.output) as archive:
            repository.write_bytes(archive.extractfile("payload/history/repository.tar").read())
        files.update({"history/inventory.json": str(inventory_file), "history/repository.tar": str(repository)})
        self.output.unlink()
        facts["manifest"] = migration.create_package(str(self.output), self.inventory.source,
            [*self.inventory.scope, "history"], files)
        for phase in ("packaged", "snapshot"):
            with self.subTest(phase=phase):
                state["phase"] = phase
                filename.write_text(json.dumps(state))
                if phase == "snapshot":
                    self.output.unlink()
                with self.assertRaisesRegex(migration.MigrationError, "metadata"):
                    history.export_history(str(self.source), str(self.output))
                if phase == "snapshot":
                    self.assertFalse(self.output.exists())
                self.assertTrue(self.dropin.exists())
                self.assertFalse(self.original_running)
        self.assertEqual(self.events.count("maintenance-start"), 1)

    def test_cli_history_requires_confirmation_and_exports_without_printing_payloads(self):
        args = ["export", "--source", str(self.source), "--output", str(self.output), "--history"]
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(migration_cli.main(args), 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.events, [])
        self.replies()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(migration_cli.main([*args, "-y"]), 0)
        self.assertIn("history", migration.validate_package(str(self.output))["scope"])
        self.assertNotIn("private-web-secret", output.getvalue() + errors.getvalue())
        self.assertFalse(self.original_running)

    def test_indices_outside_the_snapshot_are_explicitly_reported(self):
        self.replies()
        route = ("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index,status")
        self.endpoint.responses[route][0][1].append({"index": ".kibana_unconfigured", "status": "open"})
        output = io.StringIO()
        with redirect_stdout(output):
            history.export_history(str(self.source), str(self.output))
        self.assertIn(".kibana_unconfigured", output.getvalue())
        with tarfile.open(self.output) as archive:
            data = json.load(archive.extractfile("payload/history/inventory.json"))
        self.assertEqual(data["ignored_indices"], [".kibana_unconfigured"])

    def resume_replies(self, repositories=None):
        self.endpoint.reply("GET", "/", {"version": {"number": "8.16.1"}})
        self.endpoint.reply("GET", "/_snapshot", {} if repositories is None else repositories)
        self.endpoint.reply("POST", "/_ilm/start", {"acknowledged": True})
        self.endpoint.reply("GET", "/_ilm/status", {"operation_mode": "RUNNING"})

    def failed_export(self):
        self.replies(partial=True)
        with self.assertRaises(migration.MigrationError):
            history.export_history(str(self.source), str(self.output))

    def test_resume_after_a_failed_export_unregisters_its_own_repository(self):
        self.failed_export()
        self.resume_replies({
            "migration": {"type": "fs", "settings": {"location": "/snapshots/original", "readonly": "false"}},
            "backup": {"type": "url", "settings": {"url": "file:/mnt/backup"}}})
        history.resume_source(str(self.source))
        calls = [(method, path) for method, path, _body in self.endpoint.calls]
        self.assertIn(("DELETE", "/_snapshot/migration"), calls)
        self.assertNotIn(("DELETE", "/_snapshot/backup"), calls)
        self.assertTrue(self.original_running)

    def test_resume_leaves_a_foreign_repository_of_the_same_name(self):
        self.failed_export()
        self.resume_replies({"migration": {"type": "fs", "settings": {"location": "/mnt/backup"}}})
        history.resume_source(str(self.source))
        calls = [(method, path) for method, path, _body in self.endpoint.calls]
        self.assertNotIn(("DELETE", "/_snapshot/migration"), calls)
        self.assertTrue(self.original_running)

    def test_resume_keeps_its_state_when_the_leftover_repository_cannot_be_removed(self):
        self.failed_export()
        self.resume_replies({"migration": {"type": "fs", "settings": {"location": "/snapshots/original"}}})
        self.endpoint.responses[("DELETE", "/_snapshot/migration")] = [(200, {"acknowledged": False})]
        with self.assertRaisesRegex(migration.MigrationError, "repository removal"):
            history.resume_source(str(self.source))
        state = next((self.root / "state").rglob("state.json"))
        self.assertEqual(json.loads(state.read_text())["phase"], "resuming")

    def test_cli_resume_requires_confirmation_before_releasing_the_source(self):
        self.replies()
        history.export_history(str(self.source), str(self.output))
        args = ["resume-source", "--source", str(self.source)]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(migration_cli.main(args), 2)
        self.assertTrue(self.dropin.exists())
        self.assertFalse(self.original_running)
        self.resume_replies()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(migration_cli.main([*args, "-y"]), 0)
        self.assertFalse(self.dropin.exists())
        self.assertTrue(self.original_running)

    def test_partial_snapshot_never_publishes_a_package_or_restarts_the_source(self):
        self.replies(partial=True)
        with self.assertRaises(migration.MigrationError):
            history.export_history(str(self.source), str(self.output))
        self.assertFalse(self.output.exists())
        self.assertTrue(self.dropin.exists())
        self.assertFalse(self.original_running)
        self.assertIsNone(self.maintenance)
        self.replies()
        manifest = history.export_history(str(self.source), str(self.output))
        self.assertIn("history", manifest["scope"])
        self.assertEqual(self.events.count("maintenance-start"), 2)
        self.assertFalse(self.original_running)

    def attempts(self):
        folder = next((self.root / "state").rglob("state.json")).parent
        facts = json.loads((folder / "state.json").read_text())["facts"]
        return folder, facts, sorted(path.name for path in folder.iterdir()
                                     if path.name.startswith(("repository-", "configuration-", "history-")))

    def test_a_retry_removes_what_its_failed_attempts_left_even_where_elasticsearch_kept_it(self):
        self.replies(partial=True)
        with self.assertRaises(migration.MigrationError):
            history.export_history(str(self.source), str(self.output))
        folder, _facts, left = self.attempts()
        self.assertEqual(len(left), 2, left)
        repository = next(folder / name for name in left if name.startswith("repository-"))
        # as an interrupted snapshot leaves it: owned by the Elasticsearch user, closed to this one
        repository.chmod(0)
        self.addCleanup(lambda: repository.exists() and repository.chmod(0o700))
        (folder / "repository-mine").mkdir()
        mine = f"{os.getuid()}:{os.getgid()}"

        def permissions(directory, _image, owner, *, recursive=False):
            if recursive and owner == mine:
                os.chmod(directory, 0o700)

        runtime.DockerRuntime.permissions.side_effect = permissions
        self.replies()
        history.export_history(str(self.source), str(self.output))
        _folder, facts, kept = self.attempts()
        self.assertFalse(repository.exists())
        self.assertEqual(kept, sorted([facts["metadata"], Path(next(iter(facts["files"].values()))).parts[0],
                                       "repository-mine"]))

    def test_a_retry_removes_the_archive_an_interrupted_packing_left(self):
        self.replies()
        pack = history.pack_repository

        def interrupted(_directory, destination):
            Path(destination).write_bytes(b"half an archive")
            raise KeyboardInterrupt

        with mock.patch.object(history, "pack_repository", side_effect=interrupted), \
                self.assertRaises(KeyboardInterrupt):
            history.export_history(str(self.source), str(self.output))
        _folder, _facts, left = self.attempts()
        self.assertEqual(len([name for name in left if name.endswith(".tar")]), 1, left)
        with mock.patch.object(history, "pack_repository", side_effect=pack):
            history.export_history(str(self.source), str(self.output))
        _folder, facts, kept = self.attempts()
        self.assertEqual([name for name in kept if name.endswith(".tar")], [])
        packed = {entry["path"]: entry for entry in migration.validate_package(str(self.output))["files"]}
        self.assertEqual(packed["history/repository.tar"]["sha256"], facts["archive"]["sha256"])
        self.assertEqual(self.events.count("maintenance-start"), 1)

    def test_a_failed_cleanup_still_gives_the_repository_back_to_this_user(self):
        self.replies()
        cleanup = runtime.DockerRuntime.cleanup

        def failing(docker):
            if self.maintenance is not None:
                raise runtime.RuntimeError("maintenance cleanup failed")
            return cleanup(docker)

        with mock.patch.object(runtime.DockerRuntime, "cleanup", autospec=True, side_effect=failing), \
                self.assertRaisesRegex(migration.MigrationError, "maintenance cleanup failed"):
            history.export_history(str(self.source), str(self.output))
        self.assertIn(mock.call(mock.ANY, self.plan["es_image"], f"{os.getuid()}:{os.getgid()}", recursive=True),
                      runtime.DockerRuntime.permissions.call_args_list)

    def test_export_state_is_the_journal_of_resume_source(self):
        """The page of the T-Pot Manager reads this journal to offer the resume: the same file, a held
        source resumable until resume_source released it."""
        state = history.export_state(str(self.source))
        self.assertEqual(history.export_state(str(self.source / ".." / "tpotce")), state)
        self.assertFalse(os.path.lexists(state))
        self.replies()
        history.export_history(str(self.source), str(self.output))
        with open(state, encoding="utf-8") as handle:
            held = json.load(handle)
        self.assertIn(held["phase"], history.RESUMABLE)
        self.assertTrue(history.holds_source(held))
        self.assertFalse(history.holds_source(dict(held, phase="new", facts={})))
        self.assertTrue(history.holds_source(dict(held, phase="new", facts={"source": "recorded"})))
        self.resume_replies()
        history.resume_source(str(self.source))
        with open(state, encoding="utf-8") as handle:
            released = json.load(handle)
        self.assertEqual(released["phase"], "released")
        self.assertFalse(history.holds_source(released))

    def test_resume_after_an_export_that_never_stopped_the_source_says_so(self):
        self.access.side_effect = runtime.RuntimeError("sudo needs a password here; use -B FILE")
        with self.assertRaises(migration.MigrationError):
            history.export_history(str(self.source), str(self.output))
        with self.assertRaisesRegex(migration.MigrationError, "never stopped the source"):
            history.resume_source(str(self.source))
        self.assertEqual(self.events, [])
        self.assertTrue(self.original_running)

    def test_publication_checkpoint_failure_can_adopt_only_its_complete_package(self):
        self.replies()
        record = runtime.Journal.record

        def publish(journal, phase, **facts):
            if phase == "packaged":
                raise runtime.RuntimeError("checkpoint write failed")
            return record(journal, phase, **facts)

        with mock.patch.object(runtime.Journal, "record", autospec=True, side_effect=publish):
            with self.assertRaises(migration.MigrationError):
                history.export_history(str(self.source), str(self.output))
        self.assertTrue(self.output.exists())
        self.assertTrue(self.dropin.exists())
        manifest = history.export_history(str(self.source), str(self.output))
        self.assertEqual(manifest, migration.validate_package(str(self.output)))
        self.assertEqual(self.events.count("maintenance-start"), 1)

    def test_publication_recovery_never_overwrites_or_accepts_a_foreign_package(self):
        self.replies()
        record = runtime.Journal.record

        def publish(journal, phase, **facts):
            if phase == "packaged":
                raise runtime.RuntimeError("checkpoint write failed")
            return record(journal, phase, **facts)

        with mock.patch.object(runtime.Journal, "record", autospec=True, side_effect=publish):
            with self.assertRaises(migration.MigrationError):
                history.export_history(str(self.source), str(self.output))
        self.output.unlink()
        migration.create_package(str(self.output), self.inventory.source, ["configuration"],
                                  {"configuration/.env": str(self.source / ".env")})
        before = self.output.read_bytes()
        with self.assertRaises(migration.MigrationError):
            history.export_history(str(self.source), str(self.output))
        self.assertEqual(self.output.read_bytes(), before)
        self.assertTrue(self.dropin.exists())
        self.assertFalse(self.original_running)

    def test_interrupt_cleans_maintenance_processes_without_restarting_the_source(self):
        self.replies()
        with mock.patch.object(snapshots.SnapshotClient, "snapshot", side_effect=KeyboardInterrupt), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = migration_cli.main(["export", "--source", str(self.source),
                                         "--output", str(self.output), "--history", "-y"])
        self.assertEqual(result, 130)
        self.assertFalse(self.output.exists())
        self.assertTrue(self.dropin.exists())
        self.assertFalse(self.original_running)
        self.assertIsNone(self.maintenance)

    def test_explicit_resume_releases_only_after_cleanup_and_restores_ilm(self):
        self.replies()
        history.export_history(str(self.source), str(self.output))
        self.resume_replies()
        history.resume_source(str(self.source))
        self.assertFalse(self.dropin.exists())
        self.assertTrue(self.original_running)
        self.assertLess(self.events.index("maintenance-stop"), self.events.index("guard-release"))
        state = next((self.root / "state").rglob("state.json"))
        self.assertEqual(json.loads(state.read_text())["phase"], "released")


class RestoreClient:
    """A maintenance Elasticsearch that records what the restore asks of it."""

    def __init__(self, test, cluster):
        self.test = test
        self.cluster = cluster

    def pause_ilm(self):
        self.test.events.append((self.cluster, "pause_ilm"))

    def repository(self, name, location, *, readonly):
        self.test.events.append((self.cluster, "repository", name, location, readonly))

    def restore(self, repository, name, indices, *, kibana):
        self.test.events.append((self.cluster, "restore", repository, name, list(indices), kibana))
        return {}

    def snapshot(self, repository, name, indices, *, kibana):
        self.test.events.append((self.cluster, "snapshot", repository, name, list(indices), kibana))
        return {}

    def request(self, method, path, body=None):
        self.test.events.append((self.cluster, method, path))
        if method == "DELETE" and path.startswith("/_snapshot/") or (method, path) == ("POST", "/_ilm/start"):
            return {"acknowledged": True}
        if (method, path) == ("GET", "/_ilm/status"):
            return {"operation_mode": self.test.ilm_mode}
        if (method, path) == ("GET", "/_snapshot"):
            return self.test.repositories_left
        self.test.fail("unexpected maintenance request: " + method + " " + path)


class HistoryRestoreTest(HistoryImportFixture, unittest.TestCase):
    UPSTREAM_ES = "docker.elastic.co/elasticsearch/elasticsearch:8.19.23"
    UPSTREAM_KIBANA = "docker.elastic.co/kibana/kibana:8.19.23"

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(runtime, "the migration runtime is missing")
        self.home = self.root / "home"
        self.home.mkdir()
        self.data = Path(migration_import._target_data_path(str(self.target)))
        self.events = []
        self.containers = set()
        self.images = set()
        self.mismatch = None
        self.release_fails = False
        self.reload_fails = False
        self.stop_fails = False
        self.ilm_mode = "RUNNING"
        self.repositories_left = {}
        self.dropin = self.root / "system/etc/systemd/system/tpot.service.d/90-tpot-migration.conf"
        self.dropin.parents[4].mkdir()
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.dict(os.environ, {"HOME": str(self.home)}))
        self.patches.enter_context(mock.patch("platform.system", return_value="Linux"))
        self.patches.enter_context(mock.patch.object(history, "_host_id", return_value="a" * 32))
        self.patches.enter_context(mock.patch("tpotctl.fleet_remote.idle", return_value=None))
        self.patches.enter_context(mock.patch.object(runtime.SystemdHold, "command",
                                                     autospec=True, side_effect=self.command))
        self.patches.enter_context(mock.patch.object(runtime.DockerRuntime, "elasticsearch",
                                                     autospec=True, side_effect=self.start_es))
        self.patches.enter_context(mock.patch.object(runtime.DockerRuntime, "kibana",
                                                     autospec=True, side_effect=self.start_kibana))
        self.patches.enter_context(mock.patch.object(runtime.DockerRuntime, "permissions",
                                                     autospec=True, side_effect=self.permissions))
        self.patches.enter_context(mock.patch.object(history, "verify_restored", side_effect=self.verify))

    def ready(self):
        self.stage()
        self.prepare()
        migration_import.apply_import(str(self.target), str(self.state))
        env = EnvFile(str(self.target / ".env"))
        self.tpot_es = env.get("TPOT_REPO") + "/elasticsearch:" + env.get("TPOT_VERSION")
        self.tpot_kibana = env.get("TPOT_REPO") + "/kibana:" + env.get("TPOT_VERSION")
        self.images = {self.tpot_es, self.tpot_kibana}

    def phase(self):
        path = self.state / "history" / "state.json"
        return json.loads(path.read_text())["phase"] if path.exists() else None

    def hold(self):
        return history.hold_history(str(self.target), str(self.state))

    def restore(self):
        return history.restore_history(str(self.target), str(self.state))

    def command(self, host, *args, **_options):
        if "--guard" in args:
            action = args[-1]
            self.events.append("guard-" + action + ("" if action != "release" else ":" + self.phase()))
            if action == "release" and self.release_fails:
                raise runtime.RuntimeError("sudo refused the release")
            held = runtime.systemd_guard(str(self.root / "system"), host.unit, host.run_id,
                                         os.getuid(), action)
            return json.dumps({"held": held})
        if args == ("systemctl", "daemon-reload"):
            if self.reload_fails:
                raise runtime.RuntimeError("sudo timed out")
            return ""
        if "docker" in args:
            command = list(args[args.index("docker") + 1:])
            if command[:1] == ["--host"]:
                command = command[2:]
            if command[:2] == ["image", "inspect"]:
                if command[-1] not in self.images:
                    raise runtime.RuntimeError("no such image: " + command[-1])
                user = "1000:0" if command[-1].startswith("docker.elastic.co/") else "elasticsearch"
                return json.dumps([{"Config": {"User": user}, "Architecture": "amd64"}])
            if command[:1] == ["pull"]:
                self.events.append("pull " + command[-1])
                self.images.add(command[-1])
                return ""
            if command[:2] == ["container", "ls"]:
                return "\n".join(sorted(self.containers))
            if command[:2] == ["network", "ls"]:
                return ""
            if command[:2] == ["container", "inspect"]:
                return json.dumps([{"Name": "/" + command[-1],
                                    "Config": {"Labels": {"tpot.migration": host.run_id}},
                                    "State": {"Running": command[-1] in self.containers}}])
            if command[:1] == ["stop"] or command[:2] == ["container", "rm"]:
                if self.stop_fails and self.phase() == "verified":
                    raise runtime.RuntimeError("docker did not stop " + command[-1])
                self.containers.discard(command[-1])
                return ""
        self.fail("unexpected external command: " + repr(args))

    def permissions(self, docker, directory, image, owner, *, recursive=False):
        docker._private_path(directory)
        self.events.append(("permissions", os.path.basename(directory), image, owner, recursive))

    def start_es(self, docker, image, version, suffix, data, repositories):
        self.assertTrue(self.dropin.exists())
        for path, _readonly in repositories.values():
            self.assertTrue(os.path.isdir(path))
        if "original" in repositories:
            self.assertEqual((Path(repositories["original"][0]) / "index-0").read_bytes(), b"snapshot")
        self.events.append(("es", suffix, image, version,
                            {name: readonly for name, (_path, readonly) in repositories.items()},
                            sorted(os.listdir(data))))
        if suffix == "target":
            (Path(data) / "node.lock").write_text("")
        name = docker.name(suffix)
        self.containers.add(name)
        return name, RestoreClient(self, suffix)

    def start_kibana(self, docker, image, version, suffix, es_name):
        self.assertIn(es_name, self.containers)
        self.events.append(("kibana", suffix, image, version))
        name = docker.name(suffix)
        self.containers.add(name)
        return name, "http://127.0.0.1:5601/kibana", SimpleNamespace(open=lambda *_args, **_kwargs: None)

    def verify(self, client, metadata, *, target_version, kibana_url=None, opener=None):
        self.events.append((client.cluster, "verify", target_version, kibana_url is not None))
        if self.mismatch == client.cluster:
            raise migration.MigrationError("restored history differs from the source inventory")

    def starts(self):
        return [event for event in self.events if isinstance(event, tuple) and event[0] in ("es", "kibana")]

    def test_hold_is_idempotent_and_a_package_without_history_needs_none(self):
        self.ready()
        self.assertTrue(self.hold())
        self.assertTrue(self.dropin.exists())
        self.assertTrue(self.hold())
        self.assertEqual(self.events.count("guard-hold"), 1)
        self.assertEqual(self.phase(), "held")

    def test_a_package_without_history_is_not_held(self):
        self.make_package(scope=("configuration", "identity"))
        self.stage()
        self.prepare()
        self.assertFalse(self.hold())
        self.assertFalse((self.state / "history").exists())
        self.assertEqual(self.events, [])

    def test_restore_requires_the_hold(self):
        self.ready()
        with self.assertRaisesRegex(migration.MigrationError, "hold"):
            self.restore()
        self.assertFalse((self.data / "elk").exists())
        self.assertEqual(self.starts(), [])

    def test_foreign_elastic_data_on_the_target_is_kept_and_refused(self):
        self.ready()
        self.hold()
        foreign = self.data / "elk" / "data" / "foreign"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("not ours")
        with self.assertRaisesRegex(migration.MigrationError, "existing target data"):
            self.restore()
        self.assertEqual(foreign.read_text(), "not ours")
        self.assertTrue(self.dropin.exists())
        self.assertEqual(self.starts(), [])

    def test_restore_runs_both_stages_and_releases_only_after_verification(self):
        self.ready()
        self.hold()
        report = self.restore()

        self.assertEqual(report["phase"], "released")
        self.assertEqual(report["route"], ["8.19.23", "9.5.4"])
        self.assertEqual((report["indices"], report["documents"], report["objects"]), (1, 3, 2))
        self.assertFalse(self.dropin.exists())
        self.assertIn("pull " + self.UPSTREAM_ES, self.events)
        self.assertIn("pull " + self.UPSTREAM_KIBANA, self.events)
        self.assertEqual([event[:4] for event in self.starts()], [
            ("es", "intermediate", self.UPSTREAM_ES, "8.19.23"),
            ("kibana", "intermediate-kibana", self.UPSTREAM_KIBANA, "8.19.23"),
            ("es", "target", self.tpot_es, "9.5.4"),
            ("kibana", "target-kibana", self.tpot_kibana, "9.5.4")])
        self.assertEqual(self.starts()[0][4], {"original": True, "intermediate": False})
        self.assertEqual(self.starts()[2][4:], ({"intermediate": True}, []))
        self.assertIn(("intermediate", "restore", "original", "original", [INDEX], True), self.events)
        self.assertIn(("intermediate", "snapshot", "intermediate", "intermediate", [INDEX], True), self.events)
        self.assertIn(("target", "restore", "intermediate", "intermediate", [INDEX], True), self.events)
        self.assertIn(("permissions", "original", self.tpot_es, "1000:0", True), self.events)
        self.assertIn(("permissions", "data", self.tpot_es, "elasticsearch", False), self.events)
        self.assertIn(("permissions", "intermediate", self.tpot_es, "elasticsearch", True), self.events)
        order = self.events.index
        self.assertLess(order(("target", "pause_ilm")), order(("target", "restore", "intermediate",
                                                               "intermediate", [INDEX], True)))
        self.assertLess(order(("target", "verify", "9.5.4", True)),
                        order(("target", "DELETE", "/_snapshot/intermediate")))
        self.assertLess(order(("target", "DELETE", "/_snapshot/intermediate")),
                        order(("target", "POST", "/_ilm/start")))
        self.assertIn("guard-release:releasing", self.events)
        self.assertEqual(self.phase(), "released")
        self.assertEqual(self.containers, set())
        self.assertEqual(sorted(path.name for path in (self.state / "history").iterdir()), ["lock", "state.json"])
        self.assertTrue((self.data / "elk" / "data" / "node.lock").exists())
        self.assertEqual(self.restore()["phase"], "released")

    def test_space_the_pulled_images_took_stops_the_restore_before_it_writes(self):
        # apply checked the space before install.sh and the 8.19 route pulled several GB of images
        self.ready()
        self.hold()
        real = shutil.disk_usage

        def usage(path):
            value = real(path)
            pulled = any(isinstance(event, str) and event.startswith("pull ") for event in self.events)
            return value._replace(free=1024) if pulled else value

        with mock.patch("shutil.disk_usage", side_effect=usage), \
                self.assertRaisesRegex(migration.MigrationError, r"guard remains.*not enough free space .*GiB"):
            self.restore()
        self.assertEqual(self.starts(), [])
        self.assertTrue(self.dropin.exists())
        self.assertEqual(self.phase(), "held")
        self.assertFalse((self.state / "history" / "original").exists())
        self.assertEqual(self.restore()["phase"], "released")

    def test_a_differing_restore_keeps_the_guard_and_its_retry_starts_from_an_empty_target(self):
        self.ready()
        self.hold()
        self.mismatch = "target"
        with self.assertRaisesRegex(migration.MigrationError, "start guard remains"):
            self.restore()
        self.assertTrue(self.dropin.exists())
        self.assertEqual(self.phase(), "restoring")
        self.assertEqual(self.containers, set())
        self.assertNotIn(("target", "DELETE", "/_snapshot/intermediate"), self.events)
        self.assertTrue((self.data / "elk" / "data" / "node.lock").exists())

        self.mismatch = None
        self.events.clear()
        self.restore()
        self.assertEqual([event[:2] for event in self.starts()], [("es", "target"), ("kibana", "target-kibana")])
        self.assertEqual(self.starts()[0][5], [])
        owner = f"{os.getuid()}:{os.getgid()}"
        self.assertLess(self.events.index(("permissions", "data", self.tpot_es, owner, True)),
                        self.events.index(("permissions", "data", self.tpot_es, "elasticsearch", False)))
        self.assertFalse(self.dropin.exists())

    def test_a_retry_after_verification_checks_again_without_restoring(self):
        self.ready()
        self.hold()
        self.stop_fails = True
        with self.assertRaisesRegex(migration.MigrationError, "start guard remains"):
            self.restore()
        self.assertEqual(self.phase(), "verified")
        self.assertTrue(self.dropin.exists())
        self.stop_fails = False
        self.events.clear()
        self.repositories_left = {"intermediate": {"type": "fs"}}
        with self.assertRaisesRegex(migration.MigrationError, "repository"):
            self.restore()
        self.assertTrue(self.dropin.exists())

        self.repositories_left = {}
        self.events.clear()
        self.restore()
        self.assertEqual(self.starts()[0][4:], ({}, ["node.lock"]))
        self.assertIn(("target", "verify", "9.5.4", True), self.events)
        self.assertFalse(any(event[1] in ("restore", "DELETE", "POST") for event in self.events
                             if isinstance(event, tuple) and event[0] == "target"))
        self.assertIn(("target", "GET", "/_ilm/status"), self.events)
        self.assertFalse(self.dropin.exists())

    def test_an_interrupted_release_is_finished_by_the_retry_without_another_restore(self):
        self.ready()
        self.hold()
        self.reload_fails = True
        with self.assertRaisesRegex(migration.MigrationError, "run the installer again to finish the release"):
            self.restore()
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.phase(), "releasing")

        self.reload_fails = False
        self.events.clear()
        self.assertTrue(self.hold())
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.restore()["phase"], "released")
        self.assertEqual(self.starts(), [])
        self.assertIn("guard-release:releasing", self.events)
        migration_import.finish_import(str(self.state))

    def test_the_retry_of_an_interrupted_release_waits_for_no_other_administration(self):
        self.ready()
        self.hold()
        self.reload_fails = True
        with self.assertRaisesRegex(migration.MigrationError, "run the installer again to finish the release"):
            self.restore()
        self.reload_fails = False
        self.events.clear()
        state = self.home / ".local/share/tpotce"
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(os.open(state / "update.lock", os.O_RDWR | os.O_CREAT, 0o600), "rb") as update:
            fcntl.flock(update, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(migration.MigrationError, "administration locks"):
                self.restore()
        self.assertEqual(self.phase(), "releasing")
        self.assertNotIn("guard-release:releasing", self.events)
        self.assertEqual(self.restore()["phase"], "released")

    def test_a_refused_release_keeps_the_guard_until_the_retry_releases_it(self):
        self.ready()
        self.hold()
        self.release_fails = True
        with self.assertRaisesRegex(migration.MigrationError, "finish the release"):
            self.restore()
        self.assertTrue(self.dropin.exists())
        self.assertEqual(self.phase(), "releasing")
        self.release_fails = False
        self.restore()
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.phase(), "released")

    def test_import_can_be_applied_again_after_the_claim_but_not_with_foreign_elastic_files(self):
        self.ready()
        self.hold()
        self.restore()
        migration_import.apply_import(str(self.target), str(self.state))
        (self.data / "elk" / "foreign").write_text("not ours")
        with self.assertRaisesRegex(migration.MigrationError, "not created by this migration"):
            migration_import.apply_import(str(self.target), str(self.state))

    def test_an_interrupted_claim_keeps_the_import_repeatable(self):
        self.ready()
        self.hold()
        with history._restore_journal(str(self.target), str(self.state), json.loads(
                (self.state / migration_import.PENDING_NAME).read_text())) as journal:
            journal.record("held", target_data={"state": "creating",
                                                "path": str(self.data / "elk" / "data")})
        (self.data / "elk").mkdir()
        migration_import.apply_import(str(self.target), str(self.state))
        (self.data / "elk" / "data").symlink_to(self.root)
        with self.assertRaisesRegex(migration.MigrationError, "no real directory"):
            migration_import.apply_import(str(self.target), str(self.state))

    def pending_role(self):
        with mock.patch.object(migration_import.installer, "os_release",
                               return_value={"NAME": "Debian GNU/Linux", "ID": "debian", "VERSION_ID": "13"}), \
                mock.patch.object(migration_import.installer, "is_raspberry_pi_os", return_value=False), \
                mock.patch.object(migration_import.installer, "installed", return_value=True):
            return migration_import.pending_source(str(self.target), str(self.state))["role"]

    def test_the_installer_can_read_the_source_again_after_the_restore_began(self):
        # install.sh --migration asks pending_source first on every retry, before the hold and the restore
        self.ready()
        self.hold()
        self.assertEqual(self.pending_role(), "HIVE")
        self.mismatch = "target"
        with self.assertRaisesRegex(migration.MigrationError, "start guard remains"):
            self.restore()
        self.assertTrue((self.data / "elk" / "data" / "node.lock").exists())
        self.assertEqual(self.pending_role(), "HIVE")

        self.mismatch = None
        self.restore()
        self.assertEqual(self.phase(), "released")
        self.assertEqual(self.pending_role(), "HIVE")
        migration_import.apply_import(str(self.target), str(self.state))
        self.assertTrue(self.hold())
        self.assertEqual(self.restore()["phase"], "released")

        (self.data / "elk" / "foreign").write_text("not ours")
        with self.assertRaisesRegex(migration.MigrationError, "not created by this migration"):
            self.pending_role()
        (self.data / "elk" / "foreign").unlink()
        migration_import.finish_import(str(self.state))
        self.assertFalse((self.state / migration_import.PACKAGE_NAME).exists())

    def test_the_stage_is_only_removed_after_the_history_was_released(self):
        self.ready()
        self.hold()
        with self.assertRaisesRegex(migration.MigrationError, "history"):
            migration_import.finish_import(str(self.state))
        self.assertTrue((self.state / migration_import.PACKAGE_NAME).exists())
        self.restore()
        migration_import.finish_import(str(self.state))
        self.assertFalse((self.state / migration_import.PACKAGE_NAME).exists())
        self.assertEqual(json.loads((self.state / "history" / "state.json").read_text())["phase"], "released")


class DockerResourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-history-resources-")
        self.addCleanup(self.temp.cleanup)
        self.journal = runtime.Journal(str(Path(self.temp.name) / "run"), {"operation": "export"})
        self.journal.__enter__()
        self.addCleanup(self.journal.__exit__, None, None, None)
        self.commands = []

    def test_cleanup_removes_own_resources_but_refuses_foreign_label_collisions(self):
        prefix = "tpot-migration-" + self.journal.run_id
        labels = {"tpot.migration": self.journal.run_id}

        def execute(*args, **_options):
            self.commands.append(args)
            if args[1:3] == ("container", "ls"):
                return "owned\nforeign\n"
            if args[1:3] == ("network", "ls"):
                return ""
            if args[1:3] == ("container", "inspect"):
                name = prefix + "-source" if args[-1] == "owned" else "other-users-container"
                return json.dumps([{"Name": "/" + name, "Config": {"Labels": labels},
                                    "State": {"Running": True}}])
            return ""

        docker = runtime.DockerRuntime(self.journal, execute=execute)
        with self.assertRaises(runtime.RuntimeError):
            docker.cleanup()
        self.assertIn(("docker", "stop", "--time", "60", "owned"), self.commands)
        self.assertIn(("docker", "container", "rm", "owned"), self.commands)
        self.assertFalse(any("foreign" in args and ("stop" in args or "rm" in args) for args in self.commands))

    def test_pulls_and_permission_runs_may_take_hours_while_other_docker_calls_stay_bounded(self):
        # the 8.19 images or a recursive chown over hundreds of GB outlast an ordinary Docker call
        labels = {"tpot.migration": self.journal.run_id}
        timeouts = []

        def run(args, **kwargs):
            command = args[args.index("unix:///var/run/docker.sock") + 1:]
            timeouts.append((command[0], kwargs.get("timeout")))
            if command[:2] == ["container", "inspect"]:
                output = json.dumps([{"Name": "/" + command[-1], "Config": {"Labels": labels}}])
            else:
                output = "0\n" if command[0] == "wait" else ""
            return subprocess.CompletedProcess(args, 0, output, "")

        docker = history._system_docker(self.journal, runtime.SystemdHold(self.journal.run_id))
        work = self.journal.directory / "work"
        work.mkdir()
        with mock.patch("subprocess.run", side_effect=run):
            docker.pull("docker.elastic.co/elasticsearch/elasticsearch:8.19.0")
            docker.permissions(str(work), "sha256:" + "b" * 64, "2000:2000", recursive=True)
        slow = {"pull", "wait"}
        self.assertEqual({name for name, _timeout in timeouts} & slow, slow)
        self.assertTrue(all(timeout >= 4 * 3600 for name, timeout in timeouts if name in slow), timeouts)
        self.assertTrue(all(timeout == 180 for name, timeout in timeouts if name not in slow), timeouts)

    def test_maintenance_elasticsearch_is_bounded_by_the_checked_space_not_by_disk_percentages(self):
        # 90 % of a large disk can still hold the checked bytes; a watermark would stall the restore
        prefix = "tpot-migration-" + self.journal.run_id
        labels = {"tpot.migration": self.journal.run_id}
        created = []

        class Created(Exception):
            pass

        def execute(*args, **_options):
            command = args[1:]
            if command[:2] == ("image", "inspect"):
                return json.dumps([{"Architecture": "amd64"}])
            if command[:2] in (("network", "ls"), ("network", "create")):
                return ""
            if command[:2] == ("network", "inspect"):
                return json.dumps([{"Name": prefix + "-network", "Labels": labels, "Internal": True}])
            if command[:1] == ("create",):
                created.extend(command)
                raise Created
            self.fail("unexpected Docker call: " + repr(args))

        docker = runtime.DockerRuntime(self.journal, execute=execute)
        data = docker.directory("data")
        with self.assertRaises(Created):
            docker.elasticsearch("sha256:" + "b" * 64, "8.19.0", "restore", data, {})
        self.assertIn("-Ecluster.routing.allocation.disk.threshold_enabled=false", created)

    def test_permission_helper_refuses_paths_outside_its_private_run(self):
        def execute(*args, **_options):
            self.commands.append(args)
            return ""

        docker = runtime.DockerRuntime(self.journal, execute=execute)
        outside = Path(self.temp.name) / "foreign"
        outside.mkdir()
        (outside / "keep").write_text("untouched")
        with self.assertRaises(runtime.RuntimeError):
            docker.permissions(str(outside), "sha256:" + "b" * 64, "2000:2000")
        self.assertEqual(self.commands, [])
        self.assertEqual((outside / "keep").read_text(), "untouched")
        with self.assertRaises(runtime.RuntimeError):
            docker.stop("elasticsearch")
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
