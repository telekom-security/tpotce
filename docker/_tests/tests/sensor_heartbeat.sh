#!/usr/bin/env bash
set -Eeuo pipefail
unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="sensor_heartbeat"
LOGSTASH_DIR="${DOCKER_ROOT}/elk/logstash"
IMAGE=""
INIT_IMAGE=""

usage() {
  cat <<EOF
Usage: $0 [--image IMAGE] [--tpotinit-image IMAGE] [--timeout SEC] [--bind-ip IP] [--keep-artifacts]

Test the HIVE's actual HTTP input and Elasticsearch outputs in an isolated
Docker network. Verify authenticated identity, status replacement, sampling,
sensor/receive clocks, malformed frames and unchanged normal-event routing.
Uses local official images at the versions of the Dockerfiles; never builds
or pulls. No ports are published; requests run inside the isolated network.
--image overrides Logstash; --tpotinit-image provides bash/jq for the ILM job.
The temporary Elasticsearch data is removed.
--bind-ip is accepted for runner compatibility.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
    --tpotinit-image) INIT_IMAGE="${2:?--tpotinit-image needs a value}"; shift 2 ;;
    --timeout) TEST_TIMEOUT="${2:?--timeout needs a value}"; shift 2 ;;
    --bind-ip) TEST_BIND_IP="${2:?--bind-ip needs a value}"; shift 2 ;;
    --keep-artifacts) TEST_KEEP_ARTIFACTS=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) test_die "Unknown option: $1" ;;
  esac
done

test_check_dependencies
test_validate_timeout
LS_VERSION="$(sed -n 's/^ARG LS_VER=//p' "${LOGSTASH_DIR}/Dockerfile" | head -n 1)"
ES_VERSION="$(sed -n 's/^ARG ES_VER=//p' "${DOCKER_ROOT}/elk/elasticsearch/Dockerfile" | head -n 1)"
IMAGE="${IMAGE:-docker.elastic.co/logstash/logstash:${LS_VERSION}}"
ES_IMAGE="docker.elastic.co/elasticsearch/elasticsearch:${ES_VERSION}"
INIT_IMAGE="${INIT_IMAGE:-$(test_read_compose_image tpotinit "")}"
test_require_image "${IMAGE}" "docker pull ${IMAGE}"
test_require_image "${ES_IMAGE}" "docker pull ${ES_IMAGE}"
test_require_image "${INIT_IMAGE}" "cd docker/tpotinit && docker compose build"
test_prepare_harness "${TEST_NAME}"
test_enable_cleanup

cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  elasticsearch:
    image: "${ES_IMAGE}"
    environment:
      discovery.type: single-node
      xpack.security.enabled: "false"
      node.store.allow_mmap: "false"
      ES_JAVA_OPTS: "-Xms512m -Xmx512m"
  logstash:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    environment:
      LS_JAVA_OPTS: "-Xms512m -Xmx512m"
      XPACK_GEOIP_DOWNLOADER_ENABLED: "false"
    command: ["-f", "/usr/share/logstash/pipeline/http_input.conf", "--pipeline.ecs_compatibility=disabled", "--pipeline.workers=2"]
    volumes:
      - "${LOGSTASH_DIR}/dist:/usr/share/logstash/pipeline:ro"
      - "${LOGSTASH_DIR}/dist/tpot-template.json:/usr/share/logstash/config/tpot-template.json:ro"
  tpotinit:
    image: "${INIT_IMAGE}"
    profiles: ["setup"]
    entrypoint: ["/bin/bash", "/opt/tpot/bin/es_retention.sh"]
    environment:
      TPOT_ES_URL: "http://elasticsearch:9200"
      TPOT_ES_RETENTION: "7"
      TPOT_SENSOR_STATUS_RETENTION: "30"
    volumes:
      - "${DOCKER_ROOT}/tpotinit/dist/bin/es_retention.sh:/opt/tpot/bin/es_retention.sh:ro"
      - "${DOCKER_ROOT}/tpotinit/dist/etc/ilm:/opt/tpot/etc/ilm:ro"
networks:
  default:
    internal: true
EOF

test_info "Starting isolated Logstash and Elasticsearch"
test_compose up -d >/dev/null
python3 - "${TEST_HARNESS_COMPOSE}" "${TEST_PROJECT_NAME}" "${TEST_TIMEOUT}" "${DOCKER_ROOT}/.." <<'PY'
import copy
import io
import json
import sys
import time
import subprocess
import urllib.error
from datetime import datetime, timedelta, timezone

compose, project, timeout, root = sys.argv[1:]
sys.path.insert(0, root)
from tpotctl import sensors
http = "http://logstash:64305"
es = "http://127.0.0.1:9200"
timeout = max(180, int(timeout))

def request(url, data=None, method=None, headers=None):
    command = ["docker", "compose", "-f", compose, "-p", project, "exec", "-T", "elasticsearch",
               "curl", "--noproxy", "*", "-sS", "--max-time", "10", "-w", "\n%{http_code}",
               "-X", method or ("POST" if data is not None else "GET")]
    for key, value in {"content-type": "application/json",
                       **{key.lower(): value for key, value in (headers or {}).items()}}.items():
        command += ["-H", f"{key}: {value}"]
    if data is not None:
        command += ["--data-binary", "@-"]
    done = subprocess.run(command + [url], input=data if data is None or isinstance(data, str) else json.dumps(data),
                          capture_output=True, text=True, timeout=20)
    if done.returncode:
        raise ConnectionError(done.stderr)
    raw, status = done.stdout.rsplit("\n", 1)
    if int(status) >= 400:
        raise urllib.error.HTTPError(url, int(status), raw, None, None)
    return b"ok" if raw == "ok" else json.loads(raw)

def wait_for(probe, reason):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result:
                return result
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(1)
    raise AssertionError(f"{reason}: {last}")

def search(pattern):
    return request(es + "/" + pattern + "/_search?ignore_unavailable=true&allow_no_indices=true",
                   {"size": 1000})["hits"]["hits"]

def refresh():
    request(es + "/_refresh", method="POST")

def send(events, sensor=None):
    headers = {} if sensor is None else {"X-TPot-Sensor": sensor}
    request(http, events, headers=headers)

def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

wait_for(lambda: request(es + "/_cluster/health")["status"] in ("yellow", "green"), "ES not ready")
wait_for(lambda: request(http, {"type": "test-ready"}) == b"ok", "Logstash not ready")
sensor_time = datetime.now(timezone.utc) - timedelta(hours=2)
started = (sensor_time - timedelta(days=1)).isoformat()
before = datetime.now(timezone.utc)
base = {"type": "TPotSensorHeartbeat", "@timestamp": sensor_time.isoformat(),
        "t-pot_sensor": "forged", "t-pot_hostname": "sensor-host",
        "heartbeat": {"version": "test-release", "started_at": started, "sequence": 1,
                      "received_at": "2099-01-01T00:00:00Z"},
        "@metadata": {"tpot_heartbeat": True, "tpot_heartbeat_history": True}}
batch = []
for number in range(1, 11):
    event = copy.deepcopy(base)
    event["heartbeat"]["sequence"] = number
    batch.append(event)
send(batch, "sensor-a")
send({"type": "Cowrie", "message": "normal-authenticated", "t-pot_sensor": "forged"}, "sensor-a")
send({"type": "Cowrie", "message": "normal-anonymous", "t-pot_sensor": "forged",
      "@metadata": {"tpot_heartbeat": True, "tpot_heartbeat_history": True}})

def initial_results():
    refresh()
    logs = search("logstash-*")
    assert not [hit for hit in logs if hit["_source"].get("type") == "TPotSensorHeartbeat"], (
        "Heartbeat leaked into logstash-*", logs)
    status, history = search("tpot-sensor-status"), search("tpot-sensor-heartbeat-*")
    normal = {hit["_source"].get("message"): hit["_source"] for hit in logs}
    if len(status) != 1 or len(history) != 2 or len(normal) < 3:
        return None
    if status[0]["_source"]["heartbeat"]["sequence"] != 10:
        return None
    return status, history, normal

status, history, normal = wait_for(initial_results, "expected one status, two history and normal events")
assert status[0]["_id"] == "sensor-a", status
assert normal["normal-authenticated"]["t-pot_sensor"] == "sensor-a", normal
assert "x_tpot_sensor" not in normal["normal-authenticated"].get("headers", {}), normal
assert "t-pot_sensor" not in normal["normal-anonymous"], normal
assert sorted(hit["_source"]["heartbeat"]["sequence"] for hit in history) == [5, 10], history
day = sensor_time.strftime("%Y.%m.%d")
assert {hit["_index"] for hit in history} == {"tpot-sensor-heartbeat-" + day}, history
for hit in status + history:
    value = hit["_source"]
    assert value["t-pot_sensor"] == "sensor-a", value
    assert utc(value["@timestamp"]) == sensor_time, value
    assert before <= utc(value["heartbeat"]["received_at"]) <= datetime.now(timezone.utc), value
    assert utc(value["heartbeat"]["started_at"]) == utc(started), value
    assert value["heartbeat"]["version"] == "test-release", value
    assert value["t-pot_hostname"] == "sensor-host", value
print("Identity, sensor/HIVE clocks, one status, 1:5 history and normal events OK")

legacy = copy.deepcopy(base)
legacy["heartbeat"]["version"] = ""
legacy.update({"t-pot_hostname": None, "t-pot_ip_ext": None, "t-pot_ip_int": None})
send(legacy, "sensor-b")
wait_for(lambda: request(es + "/tpot-sensor-status/_doc/sensor-b")["_source"]["heartbeat"]["version"] == "",
         "old tpotinit must retain reachability with an unknown version")
legacy_status = request(es + "/tpot-sensor-status/_doc/sensor-b")["_source"]
assert not {"t-pot_hostname", "t-pot_ip_ext", "t-pot_ip_int"}.intersection(legacy_status), legacy_status
print("Optional null metadata is removed at ingestion")

bad = []
for field, values in (
        ("sequence", [None, 0, -1, "5", True, 2**63]),
        ("version", [None, [], {}, "invalid tag!", "v" * 129]),
        ("started_at", [None, [], "not-a-date", "2026-01-01"])):
    for value in values:
        event = copy.deepcopy(base)
        event["heartbeat"][field] = value
        bad.append(event)
event = copy.deepcopy(base)
event["@timestamp"] = "not-a-date"
bad.append(event)
for value in (None, [], "not-an-object"):
    event = copy.deepcopy(base)
    event["heartbeat"] = value
    bad.append(event)
send(bad, "invalid-sensor")
unauthenticated = copy.deepcopy(base)
unauthenticated["headers"] = {"x_tpot_sensor": "forged"}
send(unauthenticated)
send({"type": "Cowrie", "message": "after-invalid"}, "sensor-a")

def invalid_processed():
    refresh()
    return any(hit["_source"].get("message") == "after-invalid" for hit in search("logstash-*"))

wait_for(invalid_processed, "normal event after invalid frames not processed")
refresh()
assert sorted(hit["_id"] for hit in search("tpot-sensor-status")) == ["sensor-a", "sensor-b"]
assert len(search("tpot-sensor-heartbeat-*")) == 2
assert not any(hit["_source"].get("type") == "TPotSensorHeartbeat" for hit in search("logstash-*"))
print("Malformed and unauthenticated heartbeats rejected without polluting attack indices")

# Seed a newer receipt, then deliver an older one through Logstash's real update script.
saved = copy.deepcopy(status[0]["_source"])
saved["heartbeat"]["received_at"] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
saved["heartbeat"]["version"] = "newer-receipt"
request(es + "/tpot-sensor-status/_doc/sensor-a?refresh=true", saved, method="PUT")
older = copy.deepcopy(base)
older["heartbeat"]["sequence"] = 15
send(older, "sensor-a")

def older_processed():
    refresh()
    return len(search("tpot-sensor-heartbeat-*")) == 3

wait_for(older_processed, "older receipt did not traverse the real ES outputs")
assert request(es + "/tpot-sensor-status/_doc/sensor-a")["_source"]["heartbeat"]["version"] == "newer-receipt"
print("Out-of-order receipt cannot replace newer sensor status")

for index in ("tpot-sensor-status", "tpot-sensor-heartbeat-" + day):
    aggregations = request(es + "/" + index + "/_search", {
        "size": 0, "aggs": {"sensors": {"terms": {"field": "t-pot_sensor"}},
                           "versions": {"terms": {"field": "heartbeat.version"}}}})
    assert aggregations["aggregations"]["sensors"]["buckets"], aggregations
    settings = request(es + "/" + index + "/_settings?flat_settings=true")[index]["settings"]
    if index == "tpot-sensor-status":
        assert "index.lifecycle.name" not in settings, settings
    else:
        assert settings.get("index.lifecycle.name") == "tpot-sensor-status", settings
print("Aggregatable identity/version mappings and history-only lifecycle assignment OK")

dated = copy.deepcopy(base)
dated["heartbeat"].update(version="2026-01-01", sequence=20)
send(dated, "2026-01-01")
wait_for(lambda: request(es + "/tpot-sensor-status/_doc/2026-01-01")["_source"]["heartbeat"]["version"] ==
         "2026-01-01", "date-like sensor names and tags must remain strings")

request(es + "/tpot-sensor-heartbeat-unmanaged", {"settings": {"index.lifecycle.name": ""}}, method="PUT")
template_path = es + "/_index_template/tpot-sensor-status"
foreign = request(template_path)["index_templates"][0]["index_template"]
foreign["_meta"] = {"owner": "test-user"}
request(template_path, {key: foreign[key] for key in ("index_patterns", "template", "priority", "version", "_meta")},
        method="PUT")
foreign = request(template_path)["index_templates"][0]["index_template"]
request(es + "/_index_template/tpot-sensor-heartbeat", method="DELETE")

def run_retention(value="30"):
    done = subprocess.run(
        ["docker", "compose", "-f", compose, "-p", project, "run", "--rm", "-T", "--no-deps",
         "-e", "TPOT_SENSOR_STATUS_RETENTION=" + value, "tpotinit"],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout + done.stderr

output = run_retention()
assert request(es + "/_ilm/policy/tpot-sensor-status")["tpot-sensor-status"]["policy"]["phases"]["delete"]["min_age"] == "30d"
assert request(es + "/_ilm/policy/tpot")["tpot"]["policy"]["phases"]["delete"]["min_age"] == "7d"
assert request(es + "/_index_template/tpot-sensor-heartbeat")["index_templates"]
assert request(template_path)["index_templates"][0]["index_template"] == foreign
assert not request(es + "/tpot-sensor-heartbeat-unmanaged/_ilm/explain")["indices"]["tpot-sensor-heartbeat-unmanaged"]["managed"]
assert "Warning:" not in output, output
output = run_retention("7")
assert "tpot elastic retention --sensor-history --force" in output, output
assert request(es + "/_ilm/policy/tpot-sensor-status")["tpot-sensor-status"]["policy"]["phases"]["delete"]["min_age"] == "30d"
assert request(template_path)["index_templates"][0]["index_template"] == foreign
print("tpotinit creates missing templates/policies; foreign templates and unmanaged history stay intact")
print("A restart cannot shorten sensor history retention without confirmation")

def manager_open(req, timeout):
    try:
        answer = request(req.full_url, json.loads(req.data) if req.data is not None else None,
                         req.get_method(), dict(req.header_items()))
    except urllib.error.HTTPError as err:
        raise urllib.error.HTTPError(req.full_url, err.code, str(err.reason), {},
                                     io.BytesIO(str(err.reason).encode())) from err
    return io.BytesIO(json.dumps(answer).encode())

bulk = []
for number in range(1001):
    name = f"batch-sensor-{number:04d}"
    document = copy.deepcopy(saved)
    document["t-pot_sensor"] = name
    if number == 0:
        document.update({"t-pot_hostname": None, "t-pot_ip_ext": None, "t-pot_ip_int": None})
    bulk.extend([json.dumps({"index": {"_index": "tpot-sensor-status", "_id": name}}), json.dumps(document)])
assert not request(es + "/_bulk?refresh=true", "\n".join(bulk) + "\n")["errors"]
observed = sensors.fetch_status(url=es, opener=manager_open)
assert not observed.problem, observed.problem
assert len(observed.heartbeats) == 1004, len(observed.heartbeats)
assert observed.heartbeats["sensor-a"].version == "newer-receipt"
assert utc(observed.heartbeats["sensor-a"].sensor_at) == sensor_time
assert "sensor-a" in observed.sensors
assert observed.heartbeats["batch-sensor-0000"].hostname == ""
assert observed.heartbeats["batch-sensor-0000"].ip_ext == ""
assert observed.heartbeats["batch-sensor-0000"].ip_int == ""
received = observed.heartbeats["sensor-a"].received_at
for age, state in ((179.999, "online"), (180, "offline")):
    row = sensors.observe(sensors.Sensor("sensor-a"), observed, "hive-release", now=received + age)
    assert row.state == state and row.behind, row
def history_documents():
    return {(hit["_index"], hit["_id"]): hit["_source"] for hit in search("tpot-sensor-heartbeat-*")}

history_before = history_documents()
sensors.delete_status("sensor-a", url=es, opener=manager_open)
sensors.delete_status("sensor-a", url=es, opener=manager_open)
refresh()
assert all(hit["_id"] != "sensor-a" for hit in search("tpot-sensor-status"))
assert history_documents() == history_before
print("Manager reads 1004 real status documents, separates clocks/events and removes status without history")
PY
docker logs "${TEST_CONTAINER_NAME}" > "${TEST_TMP_ROOT}/logstash.log" 2>&1
REJECTED="$(grep -c -F 'Rejected sensor heartbeat' "${TEST_TMP_ROOT}/logstash.log" || true)"
(( REJECTED >= 20 )) || test_die "Malformed heartbeat rejections were not explicitly logged"
test_ok "HIVE heartbeat HTTP/Elasticsearch integration passed"
