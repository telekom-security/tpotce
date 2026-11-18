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
sensor/receive clocks, malformed frames and the routing of sensor events:
the new format (X-TPot-Ingest 2, gzipped JSON arrays as Fluent Bit sends
them) through the processing pipeline into logstash-* as the golden
documents, the old format (no marker) refused per sensor in the status
(refused.last_at, at most once a minute), events without a sensor name gone.
Uses local official images at the versions of the Dockerfiles; never builds
or pulls. No ports are published; requests run inside the isolated network,
which is internal: Listbot is never reached (stand-in dictionaries).
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

# The pipeline folder as the Dockerfile builds it (the part files of dist/pipeline/ and the script)
# and a pipelines.yml with the pipelines a sensor event reaches on the HIVE as pipelines.yml has them:
# sensors, process (address tpot_process) and failures. The index templates come from tpotinit
# (es_retention.sh), which runs to its end before Logstash starts. Stand-ins for the Listbot
# dictionaries of the T-Pot image (translate of 50-enrich.conf needs a readable file).
mkdir -p "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/config" "${TEST_TMP_ROOT}/listbot"
cp "${LOGSTASH_DIR}/dist/pipeline/"*.conf "${LOGSTASH_DIR}/dist/sensor_heartbeat.rb" "${TEST_TMP_ROOT}/pipeline/"
PYTHONPATH="${REPO_ROOT}" python3 - "${TEST_TMP_ROOT}/config/pipelines.yml" <<'PY'
import sys

from tpotctl.tests import logstash_conf

lines = []
for pipeline_id in ("sensors", "process", "failures"):
    settings = dict(logstash_conf.pipelines()[pipeline_id])
    settings["path.config"] = '"' + settings["path.config"] + '"'
    lines += [("- " if number == 0 else "  ") + f"{key}: {value}\n"
              for number, (key, value) in enumerate(settings.items())]
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write("".join(lines))
PY
printf '"192.0.2.1": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/iprep.yaml"
printf '"0": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/cve.yaml"
chmod 0755 "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/config" "${TEST_TMP_ROOT}/listbot"
chmod 0644 "${TEST_TMP_ROOT}/pipeline/"* "${TEST_TMP_ROOT}/config/pipelines.yml" "${TEST_TMP_ROOT}/listbot/"*.yaml

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
      # the HIVE's own address: a sensor event keeps the sensor's (12-files.conf is not in these pipelines)
      MY_EXTIP: "192.0.2.10"
    # the folder of the dead letter queue input of failures, as the T-Pot entrypoint makes it; then the
    # entrypoint of the image: pipeline.workers: 1 of the sensors pipeline in pipelines.yml wins over 2
    entrypoint: ["/bin/bash", "-c", "mkdir -p /usr/share/logstash/data/dead_letter_queue/process && exec /usr/local/bin/docker-entrypoint --pipeline.workers=2"]
    depends_on:
      tpotinit:
        condition: service_completed_successfully
    volumes:
      - "${TEST_TMP_ROOT}/pipeline:/usr/share/logstash/pipeline:ro"
      - "${TEST_TMP_ROOT}/config/pipelines.yml:/usr/share/logstash/config/pipelines.yml:ro"
      - "${TEST_TMP_ROOT}/listbot:/etc/listbot:ro"
  tpotinit:
    image: "${INIT_IMAGE}"
    entrypoint: ["/bin/bash", "/opt/tpot/bin/es_retention.sh"]
    environment:
      TPOT_ES_URL: "http://elasticsearch:9200"
      TPOT_ES_RETENTION: "7"
      TPOT_SENSOR_STATUS_RETENTION: "30"
      TPOT_VERSION: "test-release"
    volumes:
      - "${DOCKER_ROOT}/tpotinit/dist/bin/es_retention.sh:/opt/tpot/bin/es_retention.sh:ro"
      - "${DOCKER_ROOT}/tpotinit/dist/etc/ilm:/opt/tpot/etc/ilm:ro"
networks:
  default:
    internal: true
EOF

test_info "Starting isolated Logstash and Elasticsearch"
STARTED="${SECONDS}"
test_compose up -d >/dev/null
python3 - "${TEST_HARNESS_COMPOSE}" "${TEST_PROJECT_NAME}" "${TEST_TIMEOUT}" "${DOCKER_ROOT}/.." <<'PY'
import copy
import gzip
import io
import json
import os
import re
import sys
import time
import subprocess
import urllib.error
import zlib
from datetime import datetime, timedelta, timezone

compose, project, timeout, root = sys.argv[1:]
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "docker", "_tests", "lib"))
import golden
from tpotctl import sensors
from tpotctl.checks import fixtures
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
    # the new format as Fluent Bit sends it: a gzipped JSON array with the marker X-TPot-Ingest 2
    deliver(gzip.compress(json.dumps(events if isinstance(events, list) else [events]).encode()),
            {"Content-Encoding": "gzip", "X-TPot-Ingest": "2"}, sensor)

def send_old(events, sensor=None):
    # the old format of a 24.04.1 SENSOR (Logstash json_batch): no marker
    deliver(json.dumps(events).encode(), {}, sensor)

def deliver(body, headers, sensor):
    command = ["docker", "compose", "-f", compose, "-p", project, "exec", "-T", "elasticsearch",
               "curl", "--noproxy", "*", "-sS", "--max-time", "10", "-o", "/dev/null", "-w", "%{http_code}",
               "-H", "content-type: application/json"]
    for key, value in dict(headers, **({} if sensor is None else {"X-TPot-Sensor": sensor})).items():
        command += ["-H", f"{key}: {value}"]
    done = subprocess.run(command + ["--data-binary", "@-", http], input=body, capture_output=True, timeout=20)
    assert done.returncode == 0 and done.stdout == b"200", (done.stdout, done.stderr)

def line(message, **fields):
    # a Cowrie line of a SENSOR: the raw JSON line in log, as Fluent Bit reads it
    return dict({"type": "Cowrie", "log": json.dumps({"eventid": "cowrie.test", "message": message})}, **fields)

def processed(message):
    def probe():
        refresh()
        return any(hit["_source"].get("message") == message for hit in search("logstash-*"))
    wait_for(probe, f"the event {message} did not reach logstash-*")

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
send(line("normal-authenticated", **{"t-pot_sensor": "forged"}), "sensor-a")
# without a sensor name the event goes (R-15d)
send(line("normal-anonymous", **{"t-pot_sensor": "forged",
                                 "@metadata": {"tpot_heartbeat": True, "tpot_heartbeat_history": True}}))
send(line("normal-forged-metadata", **{"t-pot_sensor": "forged", "@metadata": {"tpot_heartbeat": True}}),
     "sensor-c")

def initial_results():
    refresh()
    logs = search("logstash-*")
    assert not [hit for hit in logs if hit["_source"].get("type") == "TPotSensorHeartbeat"], (
        "Heartbeat leaked into logstash-*", logs)
    status, history = search("tpot-sensor-status"), search("tpot-sensor-heartbeat-*")
    normal = {hit["_source"].get("message"): hit["_source"] for hit in logs}
    assert "sensor-c" not in {hit["_id"] for hit in status}, ("a body's @metadata made a status", status)
    if len(status) != 1 or len(history) != 2 or not {
            "normal-authenticated", "normal-forged-metadata"} <= set(normal):
        return None
    if status[0]["_source"]["heartbeat"]["sequence"] != 10:
        return None
    return status, history, normal

status, history, normal = wait_for(initial_results, "expected one status, two history and normal events")
assert status[0]["_id"] == "sensor-a", status
assert normal["normal-authenticated"]["t-pot_sensor"] == "sensor-a", normal
assert "x_tpot_sensor" not in normal["normal-authenticated"].get("headers", {}), normal
assert "normal-anonymous" not in normal, normal
assert normal["normal-forged-metadata"]["t-pot_sensor"] == "sensor-c", normal
for hit in search("logstash-*"):
    assert not {"headers", "host"}.intersection(hit["_source"]), hit
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
print("Sensor events without headers and host; a body's @metadata routes nothing")

# E2: the capability probe input of the sensors pipeline (64306, what nginx /tpot-ingest asks) answers
# 204 with X-TPot-Ingest 2 and leaves nothing behind: no document, no status, no history entry - also
# not for a request with a body and the header of a sensor (nginx sends neither)
def probe_input(method, body=None):
    command = ["docker", "compose", "-f", compose, "-p", project, "exec", "-T", "elasticsearch",
               "curl", "--noproxy", "*", "-sS", "--max-time", "10", "-o", "/dev/null", "-D", "-", "-X", method]
    if body is not None:
        command += ["-H", "content-type: application/json", "-H", "X-TPot-Sensor: sensor-probe",
                    "--data-binary", "@-"]
    done = subprocess.run(command + ["http://logstash:64306/tpot-ingest-logstash"], capture_output=True,
                          text=True, timeout=20, input=None if body is None else json.dumps(body))
    assert done.returncode == 0, done.stderr
    lines = done.stdout.replace("\r", "").splitlines()
    marks = [line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("x-tpot-ingest:")]
    return int(lines[0].split()[1]), marks

def counts():
    refresh()
    return {pattern: len(search(pattern)) for pattern in ("logstash-*", "tpot-sensor-status", "tpot-sensor-heartbeat-*")}

counted = counts()
assert probe_input("GET") == (204, ["2"]), probe_input("GET")
assert probe_input("POST", batch[:5] + [{"type": "Cowrie", "message": "through-the-probe"}]) == (204, ["2"])
send(line("after-the-probe"), "sensor-a")
processed("after-the-probe")
expected = dict(counted, **{"logstash-*": counted["logstash-*"] + 1})
assert counts() == expected, (counts(), expected)
assert not [hit for hit in search("*") if hit["_source"].get("message") == "through-the-probe"]
print("Probe input 64306: 204 with X-TPot-Ingest 2, no document, status or history")

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
send(line("after-invalid"), "sensor-a")
processed("after-invalid")
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

# E1, cases 1-3: Fluent Bit records of fixtures (raw line in log, the sensor's own t-pot fields and
# file) through tpot_process: the documents of the HIVE (golden), the sensor's address, its file
names = fixtures.names()
golden_documents = golden.load(os.path.join(root, "docker", "_tests", "golden", "logstash_fixtures.jsonl"))

def record(name, ext="208.67.222.222", token=None):
    number = names.index(name)
    marker = 40000 + zlib.crc32(name.encode()) % 20000
    token = token or "golden" + re.sub("[^a-z0-9]", "", name.lower())
    return {"@timestamp": datetime.now(timezone.utc).isoformat(), "type": fixtures.logstash_type(name),
            "log": fixtures.render(name, datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc), "8.8.8.8",
                                   marker, 8080 + number, token),
            "t-pot_log_path": "/data/" + fixtures.log_file(name), "t-pot_ip_ext": ext,
            "t-pot_ip_int": "172.20.0.1", "t-pot_hostname": "tpot-fixtures"}

send([record("cowrie"), record("cowrie", ext="9.9.9.9", token="extcowrie"), record("wordpot")], "sensor-golden")

def golden_results():
    refresh()
    found = {}
    for hit in search("logstash-*"):
        text = json.dumps(hit["_source"])
        for key, token in (("cowrie", "goldencowrie"), ("ext", "extcowrie"), ("wordpot", "goldenwordpot")):
            if token in text:
                found[key] = hit["_source"]
    return found if len(found) == 3 else None

found = wait_for(golden_results, "the fixtures of the sensor did not reach logstash-*")
for key, name in (("cowrie", "cowrie"), ("wordpot", "wordpot")):
    assert found[key]["t-pot_sensor"] == "sensor-golden", found[key]
    problems = golden.compare({name: golden_documents[name]},
                              {name: golden.normalize(found[key], golden.payload_keys(name))})
    assert not problems, problems
assert found["ext"]["t-pot_ip_ext"] == "9.9.9.9", found["ext"]
assert found["ext"]["geoip_ext"]["ip"] == "9.9.9.9", found["ext"]
assert found["ext"]["geoip_ext"] != golden_documents["cowrie"]["geoip_ext"], found["ext"]
assert found["wordpot"]["t-pot_log_path"] == "/data/" + fixtures.log_file("wordpot"), found["wordpot"]
assert found["wordpot"]["path"] == golden_documents["wordpot"]["path"] == "/goldenwordpot", found["wordpot"]
print("New format: the golden documents, the sensor's own address and geoip_ext, its file and path")

# E1, case 4: the old format is refused: 100 unmarked events of one sensor within 2 s (among them a
# valid heartbeat and one that forges the marker in its body, R-15b/c) give one update of
# refused.last_at in its status and nothing else
heartbeat = copy.deepcopy(base)
heartbeat["heartbeat"]["sequence"] = 1
send(heartbeat, "sensor-old")
wait_for(lambda: request(es + "/tpot-sensor-status/_doc/sensor-old")["found"], "no status of sensor-old")
first = request(es + "/tpot-sensor-status/_doc/sensor-old")
refresh()
history_before = history_documents()
old_heartbeat = copy.deepcopy(base)
old_heartbeat["heartbeat"]["sequence"] = 5
spoof = {"type": "Cowrie", "message": "refusedtoken spoof", "log": "refusedtoken spoof",
         "@metadata": {"tpot_ingest": "2", "tpot_heartbeat": True}, "headers": {"x_tpot_ingest": "2"}}
bodies = [[{"type": "Cowrie", "message": f"refusedtoken {number}", "log": f"refusedtoken {number}",
            "tags": ["throttled"] if number == 3 else []} for number in range(start, start + 10)]
          for start in range(0, 90, 10)]
bodies.append([{"type": "Cowrie", "message": f"refusedtoken {number}", "log": f"refusedtoken {number}"}
               for number in range(90, 98)] + [old_heartbeat])
bodies.append(spoof)
assert sum(len(body) if isinstance(body, list) else 1 for body in bodies) == 100
script = ('start=$(date +%s%N); while IFS= read -r body; do '
          'code=$(printf "%s" "$body" | curl --noproxy "*" -sS --max-time 10 -o /dev/null -w "%{http_code}" '
          '-H "content-type: application/json" -H "X-TPot-Sensor: sensor-old" --data-binary @- '
          + http + ') || exit 1; [ "$code" = 200 ] || exit 1; done; echo $(( ($(date +%s%N) - start) / 1000000 ))')
before_refusal = datetime.now(timezone.utc)
done = subprocess.run(["docker", "compose", "-f", compose, "-p", project, "exec", "-T", "elasticsearch",
                       "bash", "-c", script], input="".join(json.dumps(body) + "\n" for body in bodies),
                      capture_output=True, text=True, timeout=60)
assert done.returncode == 0, done.stdout + done.stderr
assert int(done.stdout.split()[-1]) <= 2000, ("the 100 events took longer than 2 s", done.stdout)
send_old({"type": "Cowrie", "message": "refusedtoken fresh", "log": "refusedtoken fresh"}, "sensor-fresh")
send(line("after-refused"), "sensor-a")
processed("after-refused")
refresh()
after = request(es + "/tpot-sensor-status/_doc/sensor-old")
assert after["_seq_no"] == first["_seq_no"] + 1, (first, after)
refused_at = after["_source"]["refused"]["last_at"]
assert before_refusal - timedelta(seconds=5) <= utc(refused_at) <= datetime.now(timezone.utc), after
assert after["_source"]["heartbeat"] == first["_source"]["heartbeat"], (first, after)
assert set(after["_source"]) == set(first["_source"]) | {"refused"}, after
assert after["_source"]["refused"] == {"last_at": refused_at}, after
assert history_documents() == history_before
fresh = request(es + "/tpot-sensor-status/_doc/sensor-fresh")["_source"]
assert set(fresh) == {"t-pot_sensor", "refused"} and fresh["t-pot_sensor"] == "sensor-fresh", fresh
assert set(fresh["refused"]) == {"last_at"}, fresh
for pattern in ("logstash-*", "tpot-failures-*", "tpot-sensor-heartbeat-*"):
    assert not [hit for hit in search(pattern) if "refusedtoken" in json.dumps(hit)], pattern
assert request(es + "/_count?ignore_unavailable=true", {"query": {"query_string": {"query": "refusedtoken"}}},
               method="POST")["count"] == 0
mapping = request(es + "/tpot-sensor-status/_mapping")["tpot-sensor-status"]["mappings"]["properties"]
assert mapping["refused"] == {"properties": {"last_at": {"type": "date"}}}, mapping["refused"]
print("Old format: 100 events in 2 s, one refused.last_at, heartbeat and spoofed marker refused, no document")

# the Manager reads refusals next to the heartbeats of the other sensors (R-16i)
def manager_status():
    observed = sensors.fetch_status(url=es, opener=manager_open)
    assert not observed.problem, observed.problem
    assert observed.heartbeats["batch-sensor-0000"].hostname == "", observed.heartbeats.get("batch-sensor-0000")
    assert len([name for name in observed.heartbeats if name.startswith("batch-sensor-")]) == 1001
    assert "sensor-fresh" not in observed.heartbeats and "sensor-old" in observed.heartbeats
    assert observed.refused["sensor-fresh"] > 0 and observed.refused["sensor-old"] == utc(refused_at).timestamp()
    return observed

observed = manager_status()
for name in ("sensor-old", "sensor-fresh"):
    row = sensors.observe(sensors.Sensor(name), observed, "hive-release")
    assert row.state == "refused" and row.behind, (name, row)
print("Manager: refused sensors are update needed, the other sensors keep their heartbeats")

# E1, case 5: a heartbeat of the new format keeps refused and is newer
send(dict(heartbeat, heartbeat=dict(heartbeat["heartbeat"], sequence=2)), "sensor-old")
wait_for(lambda: request(es + "/tpot-sensor-status/_doc/sensor-old")["_source"]["heartbeat"]["sequence"] == 2,
         "the heartbeat after the refusal did not arrive")
latest = request(es + "/tpot-sensor-status/_doc/sensor-old")["_source"]
assert latest["refused"] == {"last_at": refused_at}, latest
assert utc(latest["heartbeat"]["received_at"]) > utc(first["_source"]["heartbeat"]["received_at"]), latest
print("A heartbeat after the refusal keeps refused.last_at")
observed = manager_status()
received = observed.heartbeats["sensor-old"].received_at
assert received > observed.refused["sensor-old"], observed.heartbeats["sensor-old"]
assert sensors.observe(sensors.Sensor("sensor-old"), observed, "hive-release", now=received).state == "online"
assert sensors.observe(sensors.Sensor("sensor-fresh"), observed, "hive-release").state == "refused"
print("Manager: a heartbeat after the refusal is online again")

# E1, case 6: without a sensor name nothing stays, in neither format
status_count = request(es + "/tpot-sensor-status/_count")["count"]
send(line("nosensortoken new"))
send_old({"type": "Cowrie", "message": "nosensortoken old", "log": "nosensortoken old"})
send(line("after-nosensor"), "sensor-a")
processed("after-nosensor")
refresh()
assert request(es + "/_count?ignore_unavailable=true", {"query": {"query_string": {"query": "nosensortoken"}}},
               method="POST")["count"] == 0
assert request(es + "/tpot-sensor-status/_count")["count"] == status_count
print("Events without a sensor name leave no document")
PY
docker logs "${TEST_CONTAINER_NAME}" > "${TEST_TMP_ROOT}/logstash.log" 2>&1
REJECTED="$(grep -c -F 'Rejected sensor heartbeat' "${TEST_TMP_ROOT}/logstash.log" || true)"
(( REJECTED >= 20 )) || test_die "Malformed heartbeat rejections were not explicitly logged"
# at most one warning a minute for the events without a sensor name (readiness pings, case 6)
UNNAMED="$(grep -c -F 'Dropped sensor events without an authenticated sensor name' "${TEST_TMP_ROOT}/logstash.log" || true)"
(( UNNAMED >= 1 && UNNAMED <= (SECONDS - STARTED) / 60 + 1 )) \
  || test_die "Expected one warning a minute at most for events without a sensor name, got ${UNNAMED}"
! grep -q -F 'Downloading latest' "${TEST_TMP_ROOT}/logstash.log" || test_die "Logstash tried to reach Listbot"
test_ok "HIVE heartbeat HTTP/Elasticsearch integration passed"
