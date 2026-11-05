#!/usr/bin/env bash
set -Eeuo pipefail
unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="sensor_heartbeat"
LOGSTASH_DIR="${DOCKER_ROOT}/elk/logstash"
IMAGE=""

usage() {
  cat <<EOF
Usage: $0 [--image IMAGE] [--timeout SEC] [--bind-ip IP] [--keep-artifacts]

Test the HIVE's actual HTTP input and Elasticsearch outputs in an isolated
Docker network. Verify authenticated identity, status replacement, sampling,
sensor/receive clocks, malformed frames and unchanged normal-event routing.
Uses local official images at the versions of the Dockerfiles; never builds
or pulls. No ports are published; requests run inside the isolated network.
--image overrides Logstash. The temporary Elasticsearch data is removed.
--bind-ip is accepted for runner compatibility.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
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
test_require_image "${IMAGE}" "docker pull ${IMAGE}"
test_require_image "${ES_IMAGE}" "docker pull ${ES_IMAGE}"
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
networks:
  default:
    internal: true
EOF

test_info "Starting isolated Logstash and Elasticsearch"
test_compose up -d >/dev/null
python3 - "${TEST_HARNESS_COMPOSE}" "${TEST_PROJECT_NAME}" "${TEST_TIMEOUT}" <<'PY'
import copy
import json
import sys
import time
import subprocess
import urllib.error
from datetime import datetime, timedelta, timezone

compose, project, timeout = sys.argv[1:]
http = "http://logstash:64305"
es = "http://127.0.0.1:9200"
timeout = max(180, int(timeout))

def request(url, data=None, method=None, headers=None):
    command = ["docker", "compose", "-f", compose, "-p", project, "exec", "-T", "elasticsearch",
               "curl", "--noproxy", "*", "-sS", "--max-time", "10", "-w", "\n%{http_code}",
               "-X", method or ("POST" if data is not None else "GET")]
    for key, value in {"Content-Type": "application/json", **(headers or {})}.items():
        command += ["-H", f"{key}: {value}"]
    if data is not None:
        command += ["--data-binary", "@-"]
    done = subprocess.run(command + [url], input=None if data is None else json.dumps(data),
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
send(legacy, "sensor-b")
wait_for(lambda: request(es + "/tpot-sensor-status/_doc/sensor-b")["_source"]["heartbeat"]["version"] == "",
         "old tpotinit must retain reachability with an unknown version")

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
PY
docker logs "${TEST_CONTAINER_NAME}" > "${TEST_TMP_ROOT}/logstash.log" 2>&1
REJECTED="$(grep -c -F 'Rejected sensor heartbeat' "${TEST_TMP_ROOT}/logstash.log" || true)"
(( REJECTED >= 20 )) || test_die "Malformed heartbeat rejections were not explicitly logged"
test_ok "HIVE heartbeat HTTP/Elasticsearch integration passed"
