"""Opt-in snapshot integration; only uniquely labelled disposable Docker resources."""

import json
import os
import subprocess
import tarfile
import tempfile
import time
import unittest
import uuid
from unittest import mock

from tpotctl import kibana_objects, migration_history, migration_runtime, snapshots


SOURCE_VERSION = "8.16.1"
INTERMEDIATE_VERSION = "8.19.23"
TARGET_VERSION = "9.5.4"
INDICES = ["logstash-2024.04.01", "logstash-2024.04.02"]


class DockerStack:
    """Disposable fixture, deliberately independent of the migration runtime."""

    def __init__(self):
        self.run_id = uuid.uuid4().hex
        self.prefix = "tpot-snapshot-test-" + self.run_id
        self.resources = []
        self.label = "tpot.snapshot-test=" + self.run_id
        self.network = self.prefix + "-network"

    def docker(self, *args):
        result = subprocess.run(["docker", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(f"docker {args[0]} failed: {result.stderr.strip()}")
        return result.stdout

    def setup(self):
        for version in (SOURCE_VERSION, INTERMEDIATE_VERSION, TARGET_VERSION):
            for kind in ("elasticsearch", "kibana"):
                self.docker("image", "inspect", f"docker.elastic.co/{kind}/{kind}:{version}")
        self.docker("network", "create", "--label", self.label, self.network)
        self.resources.append(("network", self.network))

    def container(self, suffix, args):
        name = self.prefix + "-" + suffix
        self.docker("create", "--name", name, "--label", self.label, *args)
        self.resources.append(("container", name))
        self.docker("start", name)
        return name

    def volume(self, suffix):
        name = self.prefix + "-" + suffix
        self.docker("volume", "create", "--label", self.label, name)
        info = json.loads(self.docker("volume", "inspect", name))[0]
        if info.get("Labels", {}).get("tpot.snapshot-test") != self.run_id:
            raise RuntimeError("refusing a volume that is not owned by this test")
        self.resources.append(("volume", name))
        helper = self.container(suffix + "-permissions", [
            "--network", "none", "--user", "0:0", "--memory", "128m", "--cpus", "1",
            "--mount", f"type=volume,source={name},target=/work", "--entrypoint", "/bin/sh",
            f"docker.elastic.co/elasticsearch/elasticsearch:{SOURCE_VERSION}",
            "-c", "chown 1000:0 /work && chmod 0700 /work",
        ])
        if self.docker("wait", helper).strip() != "0":
            raise RuntimeError("disposable volume permissions could not be prepared")
        return name

    def start(self, kind, version, suffix, *, repositories=(), es_name=""):
        port = 9200 if kind == "elasticsearch" else 5601
        args = ["--network", self.network, "--cpus", "2", "--memory", "2g",
                "-p", f"127.0.0.1::{port}"]
        if kind == "elasticsearch":
            data = self.volume(suffix + "-data")
            args += ["--mount", f"type=volume,source={data},target=/usr/share/elasticsearch/data"]
            settings = (
                "discovery.type=single-node", "xpack.security.enabled=false", "xpack.ml.enabled=false",
                "ingest.geoip.downloader.enabled=false", "path.repo=/snapshots",
                "ES_JAVA_OPTS=-Xms1g -Xmx1g -XX:UseSVE=0", "CLI_JAVA_OPTS=-XX:UseSVE=0",
            )
            for folder, volume, readonly in repositories:
                args += ["--mount", f"type=volume,source={volume},target=/snapshots/{folder}"
                         + (",readonly" if readonly else "")]
        else:
            settings = (f"ELASTICSEARCH_HOSTS=http://{es_name}:9200",
                        "TELEMETRY_OPTIN=false", "TELEMETRY_ENABLED=false",
                        "ELASTIC_APM_ACTIVE=false", "NODE_OPTIONS=--max-old-space-size=1024",
                        "STATUS_ALLOWANONYMOUS=true")
        for setting in settings:
            args += ["-e", setting]
        args += [f"docker.elastic.co/{kind}/{kind}:{version}"]
        name = self.container(suffix, args)
        info = json.loads(self.docker("inspect", name))[0]
        binding = info["NetworkSettings"]["Ports"][f"{port}/tcp"][0]
        if binding["HostIp"] != "127.0.0.1":
            raise RuntimeError("test service was not bound to loopback")
        url = "http://127.0.0.1:" + binding["HostPort"]
        client = snapshots.SnapshotClient(url, timeout=10, wait=180)
        deadline = time.monotonic() + 600
        last = "no response"
        while time.monotonic() < deadline:
            if self.docker("inspect", "--format", "{{.State.Running}}", name).strip() != "true":
                raise RuntimeError(f"{name} stopped: {self.docker('logs', '--tail', '30', name)}")
            try:
                if kind == "elasticsearch":
                    if client.version() != version:
                        raise RuntimeError("the test image's runtime version does not match")
                    health = client.request("GET", "/_cluster/health?wait_for_status=yellow&timeout=5s")
                    if not health["timed_out"] and health["status"] in ("yellow", "green"):
                        return name, client
                else:
                    status = client.request("GET", "/api/status")
                    if status.get("version", {}).get("number") != version:
                        raise RuntimeError("the test image's runtime version does not match")
                    if status.get("status", {}).get("overall", {}).get("level") == "available":
                        return name, url
                    last = "Kibana is not fully available"
            except snapshots.SnapshotError as err:
                last = str(err)
            time.sleep(2)
        raise RuntimeError(f"{name} did not become ready: {last}")

    def stop(self, name):
        self.docker("stop", "--time", "60", name)

    def close(self):
        errors = []
        for kind, name in reversed(self.resources):
            try:
                info = json.loads(self.docker(kind, "inspect", name))[0]
                labels = info["Config"].get("Labels", {}) if kind == "container" else info.get("Labels", {})
                if labels.get("tpot.snapshot-test") != self.run_id:
                    raise RuntimeError(f"refusing cleanup of foreign resource {name}")
                if kind == "container" and info["State"]["Running"]:
                    self.stop(name)
                self.docker(kind, "rm", name)
            except (RuntimeError, KeyError, ValueError, subprocess.SubprocessError, OSError) as err:
                errors.append(str(err))
        if errors:
            raise RuntimeError("snapshot test cleanup failed: " + "; ".join(errors))


@unittest.skipUnless(os.environ.get("TPOT_TEST_SNAPSHOTS") == "1",
                     "set TPOT_TEST_SNAPSHOTS=1 for disposable real Elasticsearch/Kibana tests")
class RealSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.stack = DockerStack()
        self.addCleanup(self.stack.close)
        self.stack.setup()

    def objects(self, url):
        return {
            kibana_objects.key(obj): kibana_objects.normalise(obj)
            for obj in kibana_objects.parse_ndjson(kibana_objects.export_objects(url), "snapshot test")
            if obj["id"].startswith(self.stack.run_id)
        }

    def test_real_snapshot_restore_kibana_upgrade_and_data_loss_gate(self):
        original = self.stack.volume("original")
        intermediate = self.stack.volume("intermediate")
        source_name, source = self.stack.start("elasticsearch", SOURCE_VERSION, "source",
                                               repositories=[("original", original, False)])
        source.pause_ilm()
        for index, count in zip(INDICES, (3, 2)):
            source.request("PUT", "/" + index, {"settings": {"number_of_shards": 2, "number_of_replicas": 0},
                           "mappings": {"properties": {"@timestamp": {"type": "date"}}}})
            for number in range(count):
                source.request("PUT", f"/{index}/_doc/{number}?refresh=true",
                               {"@timestamp": f"2024-04-01T0{number + 1}:00:00Z", "type": "Cowrie"})
            source.request("PUT", f"/{index}/_settings", {"index.blocks.write": True})
        source.request("PUT", f"/{INDICES[0]}/_alias/events", {})
        source.request("PUT", "/foreign-index", {"settings": {"number_of_replicas": 0}})
        kb_name, kb_url = self.stack.start("kibana", SOURCE_VERSION, "source-kibana", es_name=source_name)
        for kind, attributes in (
            ("index-pattern", {"title": "logstash-*", "timeFieldName": "@timestamp"}),
            ("visualization", {"title": "Migration test", "visState": json.dumps({
                "title": "Migration test", "type": "markdown", "params": {"markdown": "preserve me"}}),
                "uiStateJSON": "{}", "kibanaSavedObjectMeta": {"searchSourceJSON": "{}"}}),
            ("dashboard", {"title": "Migration test dashboard", "panelsJSON": "[]", "optionsJSON": "{}"}),
        ):
            references = ([{"name": "panel", "type": "visualization", "id": self.stack.run_id + "-visualization"}]
                          if kind == "dashboard" else [])
            kibana_objects._request("POST", f"/api/saved_objects/{kind}/{self.stack.run_id}-{kind}",
                                    kb_url, None, json.dumps({"attributes": attributes,
                                                            "references": references}).encode(), "application/json")
        expected_objects = self.objects(kb_url)
        self.assertEqual(len(expected_objects), 3)
        kibana_objects._request("POST", "/api/spaces/space", kb_url, None,
                                json.dumps({"id": "own-space", "name": "Own space",
                                            "description": "Preserve this space"}).encode(), "application/json")
        kibana_objects._request("POST", "/s/own-space/api/saved_objects/dashboard/own", kb_url, None,
                                json.dumps({"attributes": {"title": "Other space dashboard",
                                                           "panelsJSON": "[]"}}).encode(), "application/json")
        kibana_objects._request("POST", "/api/kibana/settings", kb_url, None,
                                json.dumps({"changes": {"dateFormat": "YYYY-MM-DD",
                                                        "defaultIndex": self.stack.run_id + "-index-pattern"}}).encode(),
                                "application/json")
        expected_all = migration_history.kibana_inventory(kb_url, version=SOURCE_VERSION)
        self.assertEqual(set(expected_all), {"default", "own-space"})
        self.assertIn("config:" + SOURCE_VERSION, expected_all["default"]["objects"],
                      "the acceptance inventory must contain the active advanced settings")
        self.assertEqual(expected_all["default"]["objects"]["config:" + SOURCE_VERSION]
                         ["attributes"]["dateFormat"], "YYYY-MM-DD")
        expected = source.inventory(INDICES)
        metadata = {"indices": INDICES, "inventory": expected, "kibana": expected_all,
                    "kibana_version": SOURCE_VERSION}
        self.assertEqual([expected[index]["count"] for index in INDICES], [3, 2])
        self.stack.stop(kb_name)
        source.repository("original", "/snapshots/original", readonly=False)
        source.snapshot("original", "original", INDICES, kibana=True)
        self.stack.stop(source_name)

        mid_name, mid = self.stack.start("elasticsearch", INTERMEDIATE_VERSION, "mid",
                                         repositories=[("original", original, True),
                                                       ("intermediate", intermediate, False)])
        mid.pause_ilm()
        mid.repository("original", "/snapshots/original", readonly=True)
        mid.restore("original", "original", INDICES, kibana=True)
        mid_kb, mid_url = self.stack.start("kibana", INTERMEDIATE_VERSION, "mid-kibana", es_name=mid_name)
        self.assertEqual(self.objects(mid_url), expected_objects)
        migration_history.verify_restored(mid, metadata, target_version=INTERMEDIATE_VERSION, kibana_url=mid_url)
        self.stack.stop(mid_kb)
        mid.repository("intermediate", "/snapshots/intermediate", readonly=False)
        mid.snapshot("intermediate", "upgraded", INDICES, kibana=True)
        self.stack.stop(mid_name)

        final_name, final = self.stack.start("elasticsearch", TARGET_VERSION, "target",
                                             repositories=[("intermediate", intermediate, True)])
        final.pause_ilm()
        final.repository("intermediate", "/snapshots/intermediate", readonly=True)
        final.restore("intermediate", "upgraded", INDICES, kibana=True)
        final_kb, final_url = self.stack.start("kibana", TARGET_VERSION, "target-kibana", es_name=final_name)
        self.assertEqual(self.objects(final_url), expected_objects)
        migration_history.verify_restored(final, metadata, target_version=TARGET_VERSION, kibana_url=final_url)
        kibana_objects._request("PUT", f"/api/saved_objects/config/{TARGET_VERSION}", final_url, None,
                                json.dumps({"attributes": {"dateFormat": "YYYY"}}).encode(), "application/json")
        with self.assertRaises(migration_history.MigrationError):
            migration_history.verify_restored(final, metadata, target_version=TARGET_VERSION, kibana_url=final_url)
        with self.assertRaisesRegex(snapshots.SnapshotError, "HTTP 403"):
            final.request("PUT", f"/{INDICES[0]}/_doc/new", {"type": "Cowrie"})
        self.assertFalse(any(row["index"] == "foreign-index" for row in
                             final.request("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index")))
        final.request("PUT", f"/{INDICES[0]}/_settings", {"index.blocks.write": False})
        final.request("DELETE", f"/{INDICES[0]}/_doc/0?refresh=true")
        with self.assertRaisesRegex(snapshots.SnapshotError, "count"):
            migration_history.verify_restored(final, metadata, target_version=TARGET_VERSION, kibana_url=final_url)
        final.request("PUT", "/logstash-unexpected", {"settings": {"number_of_replicas": 0}})
        with self.assertRaisesRegex(migration_history.MigrationError, "index set"):
            migration_history.verify_restored(final, metadata, target_version=TARGET_VERSION, kibana_url=final_url)
        self.stack.stop(final_kb)
        self.stack.stop(final_name)

    def test_direct_events_empty_selection_missing_primary_and_original_rollback(self):
        repository = self.stack.volume("snapshots")
        source_name, source = self.stack.start("elasticsearch", SOURCE_VERSION, "source",
                                               repositories=[("original", repository, False)])
        source.pause_ilm()
        source.request("PUT", "/" + INDICES[0], {"settings": {"number_of_replicas": 0},
                       "mappings": {"properties": {"@timestamp": {"type": "date"}}}})
        source.request("PUT", f"/{INDICES[0]}/_doc/0?refresh=true",
                       {"@timestamp": "2024-04-01T01:00:00Z", "type": "Cowrie"})
        expected = source.inventory([INDICES[0]])
        source.repository("original", "/snapshots/original", readonly=False)
        empty = source.snapshot("original", "empty", [], kibana=False)
        self.assertEqual(empty["indices"], [])
        source.snapshot("original", "events", [INDICES[0]], kibana=False)
        source.request("PUT", "/logstash-unassigned?wait_for_active_shards=0", {
            "settings": {"number_of_replicas": 0, "index.routing.allocation.require._name": "missing-node"},
        })
        with self.assertRaises(snapshots.SnapshotError):
            source.snapshot("original", "incomplete", ["logstash-unassigned"], kibana=False)
        self.stack.stop(source_name)

        for version, suffix in ((TARGET_VERSION, "direct"), (SOURCE_VERSION, "rollback")):
            name, client = self.stack.start("elasticsearch", version, suffix,
                                             repositories=[("original", repository, True)])
            client.pause_ilm()
            client.repository("original", "/snapshots/original", readonly=True)
            client.restore("original", "events", [INDICES[0]], kibana=False)
            snapshots.verify_inventory(expected, client.inventory([INDICES[0]]))
            client.verify_samples(expected)
            self.stack.stop(name)


@unittest.skipUnless(os.environ.get("TPOT_TEST_SNAPSHOTS") == "1",
                     "set TPOT_TEST_SNAPSHOTS=1 for disposable real runtime tests")
class RealRuntimeTest(unittest.TestCase):
    def test_kibana_objects_use_the_internal_network_without_payload_in_arguments(self):
        image = f"docker.elastic.co/elasticsearch/elasticsearch:{TARGET_VERSION}"
        kibana_image = f"docker.elastic.co/kibana/kibana:{TARGET_VERSION}"
        with tempfile.TemporaryDirectory(prefix="tpot-runtime-kibana-") as directory:
            with migration_runtime.Journal(directory + "/run", {"operation": "test"}) as journal:
                docker = migration_runtime.DockerRuntime(journal)
                data = docker.directory("data")
                try:
                    docker.permissions(data, image, docker.image_user(image))
                    es_name, client = docker.elasticsearch(image, TARGET_VERSION, "source", data, {})
                    client.pause_ilm()
                    name, url, opener = docker.kibana(kibana_image, TARGET_VERSION, "kibana", es_name)
                    self.assertEqual(docker.docker(
                        "exec", name, "/usr/share/kibana/bin/kibana", "--version").strip(), TARGET_VERSION)
                    with mock.patch("subprocess.run", wraps=subprocess.run) as calls:
                        kibana_objects._request("POST", "/api/saved_objects/dashboard/test-own", url, opener.open,
                                                json.dumps({"attributes": {
                                                    "title": "private migration test value",
                                                    "panelsJSON": "[]",
                                                }}).encode(), content_type="application/json")
                        objects = kibana_objects.parse_ndjson(
                            kibana_objects.export_objects(url, opener.open), "runtime test")
                        inventory = migration_history.kibana_inventory(url, opener.open, version=TARGET_VERSION)
                    self.assertTrue(any(obj["id"] == "test-own" for obj in objects))
                    self.assertIn("config:" + TARGET_VERSION, inventory["default"]["objects"])
                    self.assertEqual(inventory["default"]["objects"]["dashboard:test-own"]["attributes"]["title"],
                                     "private migration test value")
                    for call in calls.call_args_list:
                        self.assertNotIn("private migration test value", " ".join(call.args[0]))
                    info = docker.inspect("container", name)
                    self.assertFalse(any(info["NetworkSettings"]["Ports"].values()))
                    self.assertEqual(set(info["NetworkSettings"]["Networks"]), {docker.name("network")})
                    self.assertEqual(list(journal.directory.glob(".http-*")), [])
                finally:
                    docker.cleanup()
                    docker.permissions(data, image, f"{os.getuid()}:{os.getgid()}", recursive=True)

    def test_runtime_starts_an_isolated_snapshot_server_and_cleans_its_resources(self):
        image = f"docker.elastic.co/elasticsearch/elasticsearch:{SOURCE_VERSION}"
        with tempfile.TemporaryDirectory(prefix="tpot-migration-runtime-") as temporary:
            with migration_runtime.Journal(temporary + "/run", {"operation": "import"}) as journal:
                docker = migration_runtime.DockerRuntime(journal)
                data = docker.target_directory(os.path.realpath(temporary + "/outside/elk/data"))
                repository = docker.directory("repository")
                try:
                    user = docker.image_user(image)
                    docker.permissions(data, image, user)
                    docker.permissions(repository, image, user)
                    _name, client = docker.elasticsearch(image, SOURCE_VERSION, "source", data,
                                                         {"original": (repository, False)})
                    self.assertTrue(docker.inspect("network", docker.name("network"))["Internal"])
                    self.assertFalse(any(docker.inspect("container", _name)["NetworkSettings"]["Ports"].values()))
                    client.pause_ilm()
                    client.repository("original", "/snapshots/original", readonly=False)
                    result = client.snapshot("original", "empty", [], kibana=False)
                    self.assertEqual(result["indices"], [])
                    self.assertEqual(result["state"], "SUCCESS")
                    client.request("PUT", "/" + INDICES[0] + "/_doc/retained?refresh=true",
                                   {"@timestamp": "2024-04-01T00:00:00Z", "type": "Cowrie"})
                    docker._owned("container", _name)
                    docker.docker("kill", _name)
                    _restarted, recovered = docker.elasticsearch(
                        image, SOURCE_VERSION, "recovered", data, {"original": (repository, False)})
                    self.assertEqual(recovered.request("GET", "/_ilm/status")["operation_mode"], "STOPPED")
                    self.assertEqual(recovered.inventory([INDICES[0]])[INDICES[0]]["count"], 1)
                finally:
                    docker.cleanup()
                    for directory in (data, repository):
                        docker.permissions(directory, image, f"{os.getuid()}:{os.getgid()}", recursive=True)
                    docker.cleanup()
                self.assertEqual(docker.owned_ids("container"), [])
                self.assertEqual(docker.owned_ids("network"), [])


TPOT_TEST_REPO = os.environ.get("TPOT_TEST_TPOT_REPO", "tpot-migration-test")


class ReleaseRecorder:
    def __init__(self):
        self.released = 0

    def release(self):
        self.released += 1


def wait_until(check, what, timeout=600):
    deadline = time.monotonic() + timeout
    last = "no answer"
    while time.monotonic() < deadline:
        try:
            if check():
                return
            last = "not ready"
        except (snapshots.SnapshotError, OSError) as err:
            last = str(err)
        time.sleep(2)
    raise RuntimeError(f"{what} did not become ready: {last}")


@unittest.skipUnless(os.environ.get("TPOT_TEST_SNAPSHOTS") == "1",
                     "set TPOT_TEST_SNAPSHOTS=1 for disposable real restore controller tests")
class RealRestoreControllerTest(unittest.TestCase):
    """The restore controller on real data, ending in the T-Pot images with their own configuration."""

    def setUp(self):
        self.images = {kind: f"{TPOT_TEST_REPO}/{kind}:{TARGET_VERSION}" for kind in ("elasticsearch", "kibana")}
        for kind, image in self.images.items():
            if subprocess.run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode:
                self.fail(f"missing {image}; build it with: docker build -t {image} docker/elk/{kind}")

    def source_package(self, temporary):
        es_image = f"docker.elastic.co/elasticsearch/elasticsearch:{SOURCE_VERSION}"
        kibana_image = f"docker.elastic.co/kibana/kibana:{SOURCE_VERSION}"
        inner = os.path.join(temporary, "repository.tar")
        with migration_runtime.Journal(temporary + "/source", {"operation": "test"}) as journal:
            docker = migration_runtime.DockerRuntime(journal)
            data, repository = docker.directory("data"), docker.directory("repository")
            try:
                user = docker.image_user(es_image)
                for directory in (data, repository):
                    docker.permissions(directory, es_image, user)
                es_name, source = docker.elasticsearch(es_image, SOURCE_VERSION, "source", data,
                                                       {"original": (repository, False)})
                source.pause_ilm()
                for index, count in zip(INDICES, (3, 2)):
                    source.request("PUT", "/" + index, {"settings": {"number_of_replicas": 0},
                                   "mappings": {"properties": {"@timestamp": {"type": "date"}}}})
                    for number in range(count):
                        source.request("PUT", f"/{index}/_doc/{number}?refresh=true",
                                       {"@timestamp": f"2024-04-01T0{number + 1}:00:00Z", "type": "Cowrie"})
                source.request("PUT", f"/{INDICES[0]}/_alias/events", {})
                name, url, opener = docker.kibana(kibana_image, SOURCE_VERSION, "source-kibana", es_name)
                for path, body in (
                    ("/api/saved_objects/index-pattern/own-pattern",
                     {"attributes": {"title": "logstash-*", "timeFieldName": "@timestamp"}}),
                    ("/api/saved_objects/dashboard/own-dashboard",
                     {"attributes": {"title": "Own dashboard", "panelsJSON": "[]", "optionsJSON": "{}"}}),
                    ("/api/spaces/space", {"id": "own-space", "name": "Own space"}),
                    ("/s/own-space/api/saved_objects/dashboard/other",
                     {"attributes": {"title": "Other space dashboard", "panelsJSON": "[]"}}),
                    ("/api/kibana/settings", {"changes": {"dateFormat": "YYYY-MM-DD",
                                                          "defaultIndex": "own-pattern"}}),
                ):
                    kibana_objects._request("POST", path, url, opener.open, json.dumps(body).encode(),
                                            content_type="application/json")
                metadata = {"snapshot": "original", "indices": INDICES, "inventory": source.inventory(INDICES),
                            "kibana": migration_history.kibana_inventory(url, opener.open, version=SOURCE_VERSION),
                            "kibana_version": SOURCE_VERSION}
                docker.stop(name)
                source.repository("original", "/snapshots/original", readonly=False)
                source.snapshot("original", "original", INDICES, kibana=True)
            finally:
                docker.cleanup()
                for directory in (data, repository):
                    docker.permissions(directory, es_image, f"{os.getuid()}:{os.getgid()}", recursive=True)
            migration_history.pack_repository(repository, inner)
        package = os.path.join(temporary, "package.tar")
        with tarfile.open(package, "w") as archive:
            archive.add(inner, "payload/history/repository.tar")
        return metadata, package

    def production_start(self, docker, target):
        """Start the restored data the way compose does: the image's own entrypoint and configuration."""
        user = docker.image_user(self.images["elasticsearch"])
        logs = docker.directory("production-log")
        docker.permissions(logs, self.images["elasticsearch"], user)
        name = docker.container("production", [
            "--network", docker.network(), "--network-alias", "elasticsearch", "--memory", "4g",
            "--cap-add", "IPC_LOCK", "--ulimit", "memlock=-1:-1", "--ulimit", "nofile=65536:65536",
            "-e", "bootstrap.memory_lock=true", "-e", "ES_JAVA_OPTS=-Xms2048m -Xmx2048m -XX:UseSVE=0",
            "-e", "ES_TMPDIR=/tmp",
            "--mount", docker.mount(target, "/data/elk/data"), "--mount", docker.mount(logs, "/data/elk/log"),
            self.images["elasticsearch"]])
        client = snapshots.SnapshotClient("http://127.0.0.1:9200")
        client.opener = migration_runtime.ContainerOpener(docker, name, 9200)
        # a single node stays yellow once Kibana makes an index with a replica, as T-Pot does
        wait_until(lambda: client.request("GET", "/_cluster/health?wait_for_status=yellow&timeout=5s")
                   ["status"] in ("yellow", "green"), "production Elasticsearch")
        kibana = docker.container("production-kibana", [
            "--network", docker.network(), "--memory", "3g", self.images["kibana"]])
        status = snapshots.SnapshotClient("http://127.0.0.1:5601")
        status.opener = migration_runtime.ContainerOpener(docker, kibana, 5601)
        wait_until(lambda: status.request("GET", "/api/status")["status"]["overall"]["level"] == "available",
                   "production Kibana")
        return client, status, logs

    def test_restore_controller_upgrades_to_the_tpot_images_and_survives_a_retry(self):
        with tempfile.TemporaryDirectory(prefix="tpot-history-restore-") as temporary:
            temporary = os.path.realpath(temporary)
            metadata, package = self.source_package(temporary)
            data = os.path.join(temporary, "target-data")
            os.mkdir(data, 0o700)
            target = os.path.join(data, "elk", "data")
            identity = {"operation": "import", "host": "test", "uid": os.getuid(),
                        "target": os.path.join(temporary, "tpotce"), "package_sha256": "0" * 64}
            hold = ReleaseRecorder()
            route = [INTERMEDIATE_VERSION, TARGET_VERSION]
            with migration_runtime.Journal(temporary + "/state/history", identity) as journal:
                journal.record("held")
                docker = migration_runtime.DockerRuntime(journal)
                logs = None
                try:
                    migration_history._restore(journal, hold, docker, metadata, route, data, package, self.images)
                    self.assertEqual((journal.state["phase"], hold.released), ("released", 1))
                    self.assertTrue(journal.state["facts"]["intermediate"])
                    for name in ("original", "intermediate-data", "intermediate"):
                        self.assertFalse(os.path.lexists(journal.directory / name), name)
                    self.assertEqual(docker.owned_ids("container"), [])

                    client, kibana, logs = self.production_start(docker, target)
                    self.assertEqual(client.request("GET", "/")["cluster_name"], "tpotcluster")
                    self.assertEqual(client.version(), TARGET_VERSION)
                    self.assertEqual(client.request("GET", "/_snapshot"), {})
                    self.assertEqual(client.request("GET", "/_ilm/status")["operation_mode"], "RUNNING")
                    snapshots.verify_inventory(metadata["inventory"], client.inventory(INDICES))
                    # the anonymous status of Kibana 9 names no version; the binary and the restored objects do
                    # kibana --version logs a line of its own before the number
                    self.assertEqual(docker.docker("exec", kibana.opener.name, "/usr/share/kibana/bin/kibana",
                                                   "--version").strip().splitlines()[-1], TARGET_VERSION)
                    self.assertEqual(kibana.request("GET", "/api/saved_objects/dashboard/own-dashboard")
                                     ["attributes"]["title"], "Own dashboard")
                    settings = kibana.request("GET", f"/api/saved_objects/config/{TARGET_VERSION}")["attributes"]
                    self.assertEqual(settings["dateFormat"], "YYYY-MM-DD")
                    self.assertEqual(settings["defaultIndex"], "own-pattern")
                    docker.cleanup()

                    journal.record("verified")
                    migration_history._restore(journal, hold, docker, metadata, route, data, package, self.images)
                    self.assertEqual((journal.state["phase"], hold.released), ("released", 2))
                    self.assertEqual(docker.owned_ids("container"), [])
                finally:
                    docker.cleanup()
                    for directory in filter(None, (target, logs)):
                        if os.path.isdir(directory):
                            docker.permissions(directory, self.images["elasticsearch"],
                                               f"{os.getuid()}:{os.getgid()}", recursive=True)
                    docker.cleanup()
                self.assertEqual(docker.owned_ids("network"), [])


if __name__ == "__main__":
    unittest.main()
