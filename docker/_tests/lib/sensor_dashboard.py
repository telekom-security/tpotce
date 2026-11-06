"""Exercise shipped sensor dashboard objects against a disposable ES/Kibana pair."""

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error


ROOT = Path(__file__).resolve().parents[3]
ES = "http://elasticsearch:9200"
KIBANA = "http://kibana:5601"


def check(compose, project, timeout):
    command = ["docker", "compose", "--env-file", "/dev/null", "-f", compose, "-p", project,
               "exec", "-T", "elasticsearch", "curl", "--noproxy", "*", "-sS",
               "--max-time", "30", "-w", "\n%{http_code}"]

    def request(url, data=None, method=None, upload=False):
        args = command + ["-X", method or ("POST" if data is not None else "GET"), "-H", "kbn-xsrf: true"]
        if upload:
            args += ["-F", "file=@-;filename=kibana_export.ndjson"]
        else:
            args += ["-H", "Content-Type: application/json"]
            if data is not None:
                args += ["--data-binary", "@-"]
        result = subprocess.run(args + [url], text=True, capture_output=True, timeout=40,
                                input=data if isinstance(data, str) or data is None else json.dumps(data))
        if result.returncode:
            raise ConnectionError(result.stderr)
        body, status = result.stdout.rsplit("\n", 1)
        if int(status) >= 400:
            raise urllib.error.HTTPError(url, int(status), body, None, None)
        return json.loads(body)

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            cluster = request(ES)
            if cluster["cluster_name"] != "tpot-dashboard-test":
                raise AssertionError("Refusing to write outside the disposable dashboard test cluster")
            if request(KIBANA + "/api/status")["status"]["overall"]["level"] == "available":
                break
        except (ConnectionError, urllib.error.URLError) as exc:
            last = exc
        time.sleep(2)
    else:
        raise AssertionError(f"ES/Kibana not ready: {last}")

    for name in ("tpot-sensor-status", "tpot-sensor-heartbeat"):
        template = json.loads((ROOT / f"docker/elk/logstash/dist/{name}.json").read_text())
        request(ES + "/_index_template/" + name, template, "PUT")
    request(ES + "/_ilm/policy/tpot-sensor-status", {"policy": {"phases": {}}}, "PUT")
    now = datetime.now(timezone.utc)
    stamp = lambda seconds: (now + timedelta(seconds=seconds)).isoformat()
    current = {
        "@timestamp": stamp(-7200), "type": "TPotSensorHeartbeat",
        "t-pot_sensor": "sensor-a", "t-pot_hostname": "host-a",
        "heartbeat": {"version": "test-new", "sequence": 10, "started_at": stamp(-3600),
                      "received_at": stamp(0)},
    }
    for name, age in (("sensor-a", 0), ("sensor-b", 181), ("sensor-c", None), ("sensor-d", 172800)):
        event = copy.deepcopy(current)
        event["t-pot_sensor"] = name
        if age is None:
            del event["heartbeat"]["received_at"]
        else:
            event["heartbeat"]["received_at"] = stamp(-age)
        request(ES + "/tpot-sensor-status/_doc/" + name, event, "PUT")
    history = "tpot-sensor-heartbeat-" + now.strftime("%Y.%m.%d")
    for number, (sample, start) in enumerate((
            (-7200, -21600), (-6900, -21600), (-6600, -21600), (-1800, -3600), (-1500, -3600))):
        event = copy.deepcopy(current)
        event["@timestamp"] = stamp(sample)
        event["heartbeat"].update(started_at=stamp(start), version="test-old" if number < 3 else "test-new")
        request(ES + "/" + history + "/_doc/" + str(number), event, "PUT")
    event = copy.deepcopy(current)
    event["t-pot_sensor"] = "sensor-b"
    event["heartbeat"]["version"] = ""
    request(ES + "/" + history + "/_doc/empty-version", event, "PUT")
    request(ES + "/logstash-dashboard-test/_doc/attack", {
        "@timestamp": stamp(-3600), "type": "Cowrie", "t-pot_sensor": "sensor-a"}, "PUT")
    request(ES + "/_refresh", method="POST")

    objects = [json.loads(line) for line in
               (ROOT / "docker/tpotinit/dist/etc/objects/kibana_export.ndjson").read_text().splitlines()]
    selected = [obj for obj in objects if obj.get("id", "").startswith("tpot-sensors-")
                or (obj.get("type"), obj.get("id")) == ("index-pattern", "logstash-*")]
    by_id = {obj["id"]: obj for obj in selected}
    runtime = json.loads(by_id["tpot-sensors-status-view"]["attributes"]["runtimeFieldMap"])
    result = request(ES + "/tpot-sensor-status/_search", {
        "runtime_mappings": runtime, "fields": ["heartbeat.state"], "size": 10})
    states = {hit["_id"]: hit["fields"]["heartbeat.state"][0] for hit in result["hits"]["hits"]}
    assert states == {"sensor-a": "online", "sensor-b": "offline", "sensor-c": "unknown", "sensor-d": "offline"}, states
    print("[OK] Runtime status uses HIVE receipt; stale/missing receipts stay visible", flush=True)

    runtime = json.loads(by_id["tpot-sensors-history-view"]["attributes"]["runtimeFieldMap"])
    result = request(ES + "/" + history + "/_search", {
        "runtime_mappings": runtime, "size": 0,
        "aggs": {"versions": {"terms": {"field": "heartbeat.sensor_version", "size": 20}}}})
    versions = {item["key"]: item["doc_count"] for item in result["aggregations"]["versions"]["buckets"]}
    assert versions == {"sensor-a / test-old": 3, "sensor-a / test-new": 2, "sensor-b / unknown": 1}, versions
    result = request(ES + "/" + history + "/_search", {
        "query": {"term": {"t-pot_sensor": "sensor-a"}}, "size": 0,
        "aggs": {"starts": {"cardinality": {"field": "heartbeat.started_at"}}}})
    assert result["aggregations"]["starts"]["value"] == 2, result
    print("[OK] Version series and distinct process starts, not heartbeat counts", flush=True)

    result = request(KIBANA + "/api/saved_objects/_import?overwrite=true",
                     "".join(json.dumps(obj) + "\n" for obj in selected), upload=True)
    assert result.get("success") and result["successCount"] == 11, result
    for name in ("status", "history", "starts"):
        view = request(KIBANA + "/api/data_views/data_view/tpot-sensors-" + name + "-view")["data_view"]
        fields = view["fields"]
        if name == "status":
            assert not view.get("timeFieldName"), view
            assert "heartbeat.state" in fields, fields.keys()
        elif name == "history":
            assert view["timeFieldName"] == "@timestamp", view
            assert fields["heartbeat.sensor_version"]["aggregatable"], fields
        else:
            assert view["timeFieldName"] == "heartbeat.started_at", view
    print("[OK] Kibana imported all 11 objects and resolved the live data view fields", flush=True)


if __name__ == "__main__":
    check(sys.argv[1], sys.argv[2], int(sys.argv[3]))
