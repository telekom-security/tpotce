#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="logstash_fixtures"
LOGSTASH_DIR="${DOCKER_ROOT}/elk/logstash"
DEFAULT_IMAGE="ghcr.io/telekom-security/logstash:24.04.2"
IMAGE=""
IMAGE_GIVEN="false"
# compare / write the golden documents
GOLDEN=""
GOLDEN_FILE="${TEST_ROOT}/golden/logstash_fixtures.jsonl"
# the test addresses of the checks (attackmap_pipeline_test.sh), GeoIP knows them
TEST_SRC_IP="8.8.8.8"
TEST_EXT_IP="208.67.222.222"
# Logstash needs a while to start; --timeout of the runner is the least it waits
STARTUP_SECONDS=240

usage() {
  cat <<EOF
Usage: $0 [options]

Feed the sample event of every honeypot (tpotctl/checks/fixtures, rendered by
tpotctl.checks.fixtures) through T-Pot's Logstash pipeline and check each event:
the type of its input, no tag ending in "failure" (grok, json, csv, date, GeoIP),
src_ip set, no columnNN field, the token or marker port of the check in it (the
marker port as src_port where the honeypot logs one), dest_port where the Attack
Map needs it, a timestamp field Elasticsearch takes as a date, the time of the
event as @timestamp and a GeoIP location.

The pipeline also gets two lines it cannot parse (a broken JSON line in
cowrie.json, an Endlessh line its grok does not match): each must come out of
the pipeline failures once, as a document of stage parse with the raw line, and
never among the events. They are no fixtures (not in the golden file).

A known failure (KNOWN in check_events: a defect that waits for a decision) is
named and does not fail the test; once it no longer fails, the test does, so
the entry goes.

Every fixture is rendered with fixed values (time 2026-01-02T03:04:05Z, source
8.8.8.8, a source port and token of its name). The documents of the
pipeline are then compared with the golden file
docker/_tests/golden/logstash_fixtures.jsonl (docker/_tests/lib/golden.py):
each field that differs fails the test. --golden write records them instead,
after the checks above passed. Both use the Listbot stand-ins in any image.

The pipelines are those of docker/elk/logstash/dist/pipelines.yml on the part
files of dist/pipeline/ of this checkout as they are, but the file inputs read
the files from the start and the outputs are files instead of Elasticsearch: the
events in events.json, the documents of the pipeline failures in failures.json.
The sensors pipeline is left out (sensor_heartbeat tests it). The container has
no network.

The T-Pot Logstash image is used when it exists locally, otherwise the official
image of its Dockerfile (LS_VER).

Options:
  --golden MODE      compare (default) or write the golden documents.
  --image IMAGE      Image to use. Default: docker/elk/logstash/docker-compose.yml,
                     else docker.elastic.co/logstash/logstash:<LS_VER>.
  --timeout SEC      Least time to wait for the events (Logstash gets ${STARTUP_SECONDS} s at least).
  --bind-ip IP       Accepted for runner compatibility; nothing is published.
  --keep-artifacts   Keep the temporary files for debugging.
  -h, --help         Show this help message.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image) [[ $# -ge 2 ]] || test_die "--image requires an argument"; IMAGE="$2"; IMAGE_GIVEN="true"; shift 2 ;;
      --image=*) IMAGE="${1#*=}"; IMAGE_GIVEN="true"; shift ;;
      --timeout) [[ $# -ge 2 ]] || test_die "--timeout requires an argument"; TEST_TIMEOUT="$2"; shift 2 ;;
      --timeout=*) TEST_TIMEOUT="${1#*=}"; shift ;;
      --bind-ip) [[ $# -ge 2 ]] || test_die "--bind-ip requires an argument"; TEST_BIND_IP="$2"; shift 2 ;;
      --bind-ip=*) TEST_BIND_IP="${1#*=}"; shift ;;
      --keep-artifacts) TEST_KEEP_ARTIFACTS="true"; shift ;;
      --golden) [[ $# -ge 2 ]] || test_die "--golden requires an argument"; GOLDEN="$2"; shift 2 ;;
      --golden=*) GOLDEN="${1#*=}"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) test_die "Unknown option: $1" ;;
    esac
  done
  case "${GOLDEN:=compare}" in
    compare|write) ;;
    *) test_die "--golden takes compare or write, not: ${GOLDEN}" ;;
  esac
}

# the T-Pot image if it is there, else the official image the T-Pot image builds on
choose_image() {
  local official version
  if [[ "${IMAGE_GIVEN}" == "true" ]]; then
    test_require_image "${IMAGE}" "cd ${LOGSTASH_DIR} && docker compose build"
    return 0
  fi
  IMAGE="$(test_read_compose_image "elk/logstash" "${DEFAULT_IMAGE}")"
  if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    return 0
  fi
  version="$(sed -n 's/^ARG LS_VER=//p' "${LOGSTASH_DIR}/Dockerfile" | head -n 1)"
  [[ -n "${version}" ]] || test_die "No ARG LS_VER in ${LOGSTASH_DIR}/Dockerfile"
  official="docker.elastic.co/logstash/logstash:${version}"
  if docker image inspect "${official}" >/dev/null 2>&1; then
    test_info "${IMAGE} is not built here, using ${official} with the T-Pot pipelines"
    IMAGE="${official}"
    return 0
  fi
  test_die "Image not found: ${IMAGE} (nor ${official}). Build it first, for example: cd ${LOGSTASH_DIR} && docker compose build"
}

# the data folder with one rendered fixture per honeypot, the manifest and the pipelines of the test
prepare_files() {
  mkdir -p "${TEST_TMP_ROOT}/data" "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/config" "${TEST_TMP_ROOT}/out" \
    "${TEST_TMP_ROOT}/listbot"
  chmod 0777 "${TEST_TMP_ROOT}/out"
  PYTHONPATH="${REPO_ROOT}" python3 - "${TEST_TMP_ROOT}" "${TEST_SRC_IP}" <<'PY'
import json
import os
import re
import sys
import zlib
from datetime import datetime, timezone

from tpotctl.checks import fixtures, stages
from tpotctl.tests import logstash_conf

root, src_ip = sys.argv[1], sys.argv[2]
data = os.path.join(root, "data")
# fixed values, so the documents are the same in every run (the golden file); the time lies before
# the run: an event that keeps the time Logstash read it at shows up
ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
manifest = {}
for number, name in enumerate(fixtures.names()):
    marker = 40000 + zlib.crc32(name.encode()) % 20000
    token = "golden" + re.sub("[^a-z0-9]", "", name.lower())
    rel = fixtures.log_file(name)
    path = os.path.join(data, rel)
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    line = fixtures.render(name, ts, src_ip, marker, 8080 + number, token)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    os.chmod(path, 0o644)
    manifest[name] = {"path": "/data/" + rel, "type": fixtures.logstash_type(name), "src_ip": src_ip,
                      "marker": marker, "token": token,
                      "second": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                      # the honeypot logs a source port: the marker port is its src_port
                      "logs_src_port": "{src_port}" in fixtures.template(name),
                      # map_data drops an event without dest_port (the check says n/a for these)
                      "map": (fixtures.logstash_type(name) not in stages.NOT_ON_THE_MAP
                              and name not in stages.NO_DEST_PORT)}
# two lines the pipeline cannot parse, after the fixture of their file; they must end in failures.json
broken = [
    {"name": "cowrie", "reason": "_jsonparsefailure",
     "line": '{"eventid": "cowrie.session.connect", "src_ip": "8.8.8.8", "tpotfailure": "broken json'},
    {"name": "endlessh", "reason": "_grokparsefailure",
     "line": "2026-01-02T03:04:05.000Z TPOTFAILURE no line of Endlessh"},
]
for entry in broken:
    entry.update(path="/data/" + fixtures.log_file(entry["name"]), type=fixtures.logstash_type(entry["name"]))
    with open(os.path.join(data, fixtures.log_file(entry["name"])), "a", encoding="utf-8") as handle:
        handle.write(entry["line"] + "\n")
with open(os.path.join(root, "failures-expect.json"), "w", encoding="utf-8") as handle:
    json.dump(broken, handle, indent=1)
for folder, _, _ in os.walk(data):
    os.chmod(folder, 0o755)
with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=1, sort_keys=True)
# an event belongs to the fixture whose token it carries or whose port it has: no two may share one
for left in manifest:
    for right in manifest:
        if left != right and manifest[left]["token"] in manifest[right]["token"]:
            sys.exit(f"the token of {left} is part of that of {right}")
        if left < right and manifest[left]["marker"] == manifest[right]["marker"]:
            sys.exit(f"{left} and {right} get the same source port {manifest[left]['marker']}")
# the fixtures for golden.py: name, token, source port
with open(os.path.join(root, "out", "expect.tsv"), "w", encoding="utf-8") as handle:
    for name, want in sorted(manifest.items()):
        handle.write(f"{name}\t{want['token']}\t{want['marker']}\n")

# The pipelines files, process and failures of pipelines.yml with the
# part files of this checkout (mounted at IMAGE_DIR) as they are, but some of the test (in TEST_DIR):
# the file inputs (which read every file from the start themselves) keep no sincedb, the output of
# the processing pipeline keeps its routing, but its Elasticsearch becomes a file of JSON
# lines, and so does the output of the failures pipeline.
LOGSTASH = "/usr/share/logstash/"
TEST_DIR = LOGSTASH + "test/"
inputs = (logstash_conf.PIPELINE_DIR / "10-input-files.conf").read_text(encoding="utf-8")
count = inputs.count("\n  file {\n")
inputs = inputs.replace("\n  file {\n", '\n  file {\n    sincedb_path => "/dev/null"\n')
if count < len({entry["path"] for entry in manifest.values()}) - 8:   # conpot: one input, nine files
    sys.exit(f"10-input-files.conf: only {count} file inputs")
# the one output of the processing pipeline with its elasticsearch plugin as a file
outputs = [path for path in logstash_conf.pipeline_files("process")
           if re.fullmatch(r"9\d-output-[a-z]+\.conf", path.name)]
if len(outputs) != 1:
    sys.exit(f"the processing pipeline has the outputs {outputs}, not one")
output, plugins = re.subn(
    r"^( *)elasticsearch \{\n.*?^\1\}\n",
    lambda found: (f'{found[1]}file {{\n{found[1]}  path => "/out/events.json"\n'
                   f'{found[1]}  codec => json_lines\n{found[1]}}}\n'),
    outputs[0].read_text(encoding="utf-8"), flags=re.M | re.S)
if plugins != 1:
    sys.exit(f"{outputs[0].name}: {plugins} elasticsearch outputs, not one")
own = {"10-input-files.conf": inputs, "99-output-file.conf": "# Output section (test)\n" + output}
# every pipeline but sensors (sensor_heartbeat tests it)
pipeline_ids = [pipeline_id for pipeline_id in logstash_conf.pipelines() if pipeline_id != "sensors"]
if "failures" in pipeline_ids:
    own["99-output-failures.conf"] = ('# Output section of the failures (test)\noutput {\n'
                                      '  file {\n    path => "/out/failures.json"\n    codec => json_lines\n  }\n}\n')
for name, text in own.items():
    with open(os.path.join(root, "pipeline", name), "w", encoding="utf-8") as handle:
        handle.write(text)
yml, used = ["# T-Pot's pipelines with the file inputs and the output of the test"], []
for pipeline_id in pipeline_ids:
    paths = []
    for path in logstash_conf.pipeline_files(pipeline_id):
        if path.name == "10-input-files.conf":
            paths.append(TEST_DIR + path.name)
        elif path.name == "92-output-failures.conf":
            paths.append(TEST_DIR + "99-output-failures.conf")
        elif re.fullmatch(r"9\d-output-[a-z]+\.conf", path.name):
            paths.append(TEST_DIR + "99-output-file.conf")
        else:
            paths.append(logstash_conf.IMAGE_DIR + path.name)
    used += [path for path in paths if path.startswith(TEST_DIR)]
    # Logstash reads the files sorted by their path: the output of the test comes last as the real one
    settings = dict(logstash_conf.pipelines()[pipeline_id])
    settings["path.config"] = '"' + LOGSTASH + "{" + ",".join(path[len(LOGSTASH):] for path in paths) + '}"'
    yml += [("- " if number == 0 else "  ") + f"{key}: {value}" for number, (key, value) in enumerate(settings.items())]
if sorted(used) != [TEST_DIR + name for name in sorted(own)]:
    sys.exit(f"the pipelines take {used} of the test, not its input and its outputs once each")
with open(os.path.join(root, "config", "pipelines.yml"), "w", encoding="utf-8") as handle:
    handle.write("\n".join(yml) + "\n")
print(f"{len(manifest)} fixtures rendered, {len(broken)} lines that cannot be parsed, {count} file inputs")
PY
  # stand-ins for the Listbot dictionaries of the T-Pot image (translate needs a readable file)
  printf '"192.0.2.1": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/iprep.yaml"
  printf '"0": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/cve.yaml"
  chmod 0755 "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/config" "${TEST_TMP_ROOT}/listbot"
  chmod 0644 "${TEST_TMP_ROOT}/pipeline/"*.conf "${TEST_TMP_ROOT}/config/pipelines.yml" "${TEST_TMP_ROOT}/listbot/"*.yaml
}

write_compose() {
  # the golden documents must not depend on the Listbot data of the day
  test_info "Golden documents: using the Listbot stand-ins"
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  logstash:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    # the host the file input names where a line has none: the same in every run
    hostname: tpot-fixtures
    network_mode: none
    environment:
      - MY_EXTIP=${TEST_EXT_IP}
      - MY_INTIP=172.20.0.1
      - MY_HOSTNAME=tpot-fixtures
      - MY_VERSION=test-release
      - TZ=UTC
      - XPACK_GEOIP_DOWNLOADER_ENABLED=false
      - LS_JAVA_OPTS=-Xms512m -Xmx512m
    # the entrypoint of the image (env2yaml for the settings above, then logstash with these options
    # and the pipelines of the pipelines.yml of the test)
    command: ["--pipeline.workers=1"]
    volumes:
      - "${TEST_TMP_ROOT}/data:/data:ro"
      - "${LOGSTASH_DIR}/dist/pipeline:/usr/share/logstash/pipeline:ro"
      - "${TEST_TMP_ROOT}/pipeline:/usr/share/logstash/test:ro"
      - "${TEST_TMP_ROOT}/config/pipelines.yml:/usr/share/logstash/config/pipelines.yml:ro"
      - "${TEST_TMP_ROOT}/out:/out"
      - "${TEST_TMP_ROOT}/listbot:/etc/listbot:ro"
EOF
}

# waits until every fixture has come out of the pipeline, or Logstash stopped, or the time is up
wait_for_events() {
  local expected broken seconds deadline got state failures
  expected="$(python3 -c 'import json, sys; print(len(json.load(open(sys.argv[1]))))' "${TEST_TMP_ROOT}/manifest.json")"
  broken="$(python3 -c 'import json, sys; print(len(json.load(open(sys.argv[1]))))' "${TEST_TMP_ROOT}/failures-expect.json")"
  seconds=$(( TEST_TIMEOUT > STARTUP_SECONDS ? TEST_TIMEOUT : STARTUP_SECONDS ))
  deadline=$(( SECONDS + seconds ))
  while (( SECONDS < deadline )); do
    got=0
    [[ -f "${TEST_TMP_ROOT}/out/events.json" ]] && got="$(grep -c . "${TEST_TMP_ROOT}/out/events.json" || true)"
    if (( got >= expected )); then
      failures=0
      [[ -f "${TEST_TMP_ROOT}/out/failures.json" ]] && failures="$(grep -c . "${TEST_TMP_ROOT}/out/failures.json" || true)"
      if (( failures >= broken )); then
        sleep 5   # a fixture Logstash splits into more events shows up as well
        return 0
      fi
    fi
    state="$(docker inspect -f '{{.State.Status}}' "${TEST_CONTAINER_NAME}" 2>/dev/null || true)"
    [[ "${state}" == "exited" || "${state}" == "dead" ]] && { test_info "Logstash stopped"; return 1; }
    sleep 2
  done
  test_info "${got} of ${expected} events after ${seconds} s"
  return 0   # check_events names the missing ones
}

check_events() {
  docker logs "${TEST_CONTAINER_NAME}" > "${TEST_TMP_ROOT}/out/logstash.log" 2>&1
  python3 - "${TEST_TMP_ROOT}/manifest.json" "${TEST_TMP_ROOT}/out/events.json" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
events = []
try:
    with open(sys.argv[2], encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
except FileNotFoundError:
    pass

# An event belongs to the fixture whose token or marker port it carries, or else to the file it
# came from: 30-decode.conf moves the file the input names in [path] to t-pot_log_path, [path] is
# the honeypot's own (Go-pot, Honeyaml and Wordpot log the request path as path).
by_name, stray = {}, []
for event in events:
    text = json.dumps(event, sort_keys=True)
    owners = [name for name, want in manifest.items()
              if want["token"] in text or event.get("src_port") == want["marker"]]
    if not owners:
        owners = [name for name, want in manifest.items() if event.get("t-pot_log_path") == want["path"]]
    if len(owners) == 1:
        by_name.setdefault(owners[0], []).append(event)
    else:
        stray.append(event)

COLUMN = re.compile(r"column\d+")
# a date Elasticsearch takes for a field that is mapped as date (dynamic date detection of
# the template logstash, docker/tpotinit/dist/etc/ilm/logstash.json: strict_date_optional_time): an
# offset Z, +HH:MM, +HHMM or +HH, not two of them
ES_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}(:\d{2}(:\d{2}([.,]\d{1,9})?)?)?(Z|[+-]\d{2}(:?\d{2})?)?)?)?)?$")
# failures known and waiting for a decision: honeypot -> (the start of its problem, why)
KNOWN = {}
failed = known = 0
for name, want in sorted(manifest.items()):
    found = by_name.get(name, [])
    problems = []
    if len(found) != 1:
        problems.append(f"no event from {want['path']} (dropped by a filter?)" if not found
                        else f"{len(found)} events from {want['path']}, expected one")
    for event in found:
        text = json.dumps(event, sort_keys=True)
        if event.get("t-pot_log_path") != want["path"]:
            problems.append(f"t-pot_log_path {event.get('t-pot_log_path')!r}, expected {want['path']!r}")
        if str(event.get("path", "")).startswith("/data/"):
            problems.append(f"path {event['path']!r}: the source file stayed in path")
        if event.get("type") != want["type"]:
            problems.append(f"type {event.get('type')!r}, expected {want['type']!r}")
        tags = [tag for tag in event.get("tags") or [] if str(tag).endswith("failure")]
        if tags:
            problems.append("tags " + ", ".join(tags))
        if event.get("src_ip") != want["src_ip"]:
            problems.append(f"src_ip {event.get('src_ip')!r}, expected {want['src_ip']!r}")
        columns = sorted(key for key in event if COLUMN.fullmatch(key))
        if columns:
            problems.append("fields " + ", ".join(columns))
        if want["token"] not in text and event.get("src_port") != want["marker"]:
            problems.append(f"neither the token {want['token']} nor the marker port {want['marker']} in the event")
        if want["logs_src_port"] and event.get("src_port") != want["marker"]:
            problems.append(f"src_port {event.get('src_port')!r}, expected the marker port {want['marker']}")
        if want["map"] and event.get("dest_port") is None:
            problems.append("no dest_port (the Attack Map drops the event)")
        stamp = event.get("timestamp")
        if isinstance(stamp, str) and not ES_DATE.match(stamp):
            problems.append(f"timestamp {stamp!r} is no date Elasticsearch takes (strict_date_optional_time)")
        if not str(event.get("@timestamp", "")).startswith(want["second"]):
            problems.append(f"@timestamp {event.get('@timestamp')!r}, the event says {want['second']}")
        if "geoip" not in event or "latitude" not in (event.get("geoip") or {}):
            problems.append("no geoip.latitude")
        if name == "glutton":
            details = event.get("glutton", {})
            if event.get("transport") != "tcp" or not details.get("handlers"):
                problems.append("structured Glutton transport or handler stack missing")
            if details.get("protocol_stack") != "tcp > spicy-http":
                problems.append("structured Glutton ordered protocol stack missing")
            if details.get("commands") != ["GET"] or details.get("duration_ms") != 12:
                problems.append("structured Glutton commands or session duration missing")
            if not isinstance(details.get("decoded"), list):
                problems.append("structured Glutton decoded frames missing")
    expected = KNOWN.get(name)
    if expected:
        matching = [problem for problem in problems if problem.startswith(expected[0])]
        if matching and len(matching) == len(problems):
            known += 1
            print(f"[KNOWN] {name}: {expected[1]}: " + "; ".join(problems))
            continue
        if not matching:
            problems.append(f"the known failure ({expected[1]}) no longer shows: remove it from KNOWN")
    if problems:
        failed += 1
        print(f"[FAIL] {name}: " + "; ".join(problems))
        for event in found:
            print("       " + json.dumps(event, sort_keys=True)[:1500])
    else:
        print(f"[OK]   {name} ({want['type']})")
for event in stray:
    print("[FAIL] an event of no fixture (or of more than one): " + json.dumps(event, sort_keys=True)[:1500])
    failed += 1
print(f"{len(manifest) - failed - known} of {len(manifest)} fixtures read cleanly"
      + (f", {known} known failure(s)" if known else ""))
sys.exit(1 if failed else 0)
PY
}

# HIVE: every line the pipeline cannot parse is one document of the pipeline failures (stage parse, the
# raw line, the fields of the input and nothing else) and no event
check_failures() {
  python3 - "${TEST_TMP_ROOT}/failures-expect.json" "${TEST_TMP_ROOT}/out/failures.json" \
    "${TEST_TMP_ROOT}/out/events.json" <<'PY'
import json
import sys


def documents(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        return []


with open(sys.argv[1], encoding="utf-8") as handle:
    expected = json.load(handle)
failures, events = documents(sys.argv[2]), documents(sys.argv[3])
problems = []
for want in expected:
    found = [doc for doc in failures if (doc.get("failure") or {}).get("original") == want["line"]]
    if len(found) != 1:
        problems.append(f"{want['name']}: {len(found)} failure documents with the raw line, expected one")
    for doc in found:
        failure = doc.get("failure") or {}
        if failure.get("stage") != "parse" or failure.get("reason") != want["reason"]:
            problems.append(f"{want['name']}: stage {failure.get('stage')!r}, reason {failure.get('reason')!r},"
                            f" expected parse, {want['reason']}")
        if (doc.get("type"), doc.get("t-pot_log_path"), doc.get("t-pot_hostname")) != \
                (want["type"], want["path"], "tpot-fixtures"):
            problems.append(f"{want['name']}: type, t-pot_log_path, t-pot_hostname of the input missing: {doc}")
        others = sorted(set(doc) - {"@timestamp", "type", "t-pot_sensor", "t-pot_hostname", "t-pot_log_path",
                                    "failure"})
        if others or sorted(failure) != ["original", "reason", "stage"]:
            problems.append(f"{want['name']}: fields beyond those of a failure document: {others} {sorted(failure)}")
    leaked = [event for event in events if want["line"] in json.dumps(event)
              or "tpotfailure" in json.dumps(event).lower()]
    if leaked:
        problems.append(f"{want['name']}: {len(leaked)} events of the line that cannot be parsed")
if len(failures) != len(expected):
    problems.append(f"{len(failures)} failure documents, expected {len(expected)}")
for problem in problems:
    print("[FAIL] " + problem)
for doc in failures:
    print("       " + json.dumps(doc, sort_keys=True)[:1500])
if not problems:
    print(f"{len(expected)} lines that cannot be parsed: one failure document each, no event")
sys.exit(1 if problems else 0)
PY
}

# write or compare the golden documents of the HIVE pipeline (docker/_tests/lib/golden.py)
golden() {
  python3 "${TEST_LIB_DIR}/golden.py" "$1" --golden "${GOLDEN_FILE}" \
    --events "${TEST_TMP_ROOT}/out/events.json" --expect "${TEST_TMP_ROOT}/out/expect.tsv"
}

main() {
  parse_args "$@"
  test_check_dependencies
  test_validate_timeout
  choose_image

  test_prepare_harness "${TEST_NAME}"
  test_enable_cleanup
  TEST_ARTIFACT_LOG_DIR="${TEST_TMP_ROOT}/out"

  test_info "Rendering the fixtures and the pipelines of the test"
  prepare_files
  write_compose

  test_info "Starting Logstash (${IMAGE}) without network"
  test_compose up -d >/dev/null
  test_info "Waiting for the events of every fixture"
  wait_for_events || test_die "Logstash stopped before every fixture was read"

  test_info "Checking the events"
  local checked=0 compared=0 failed=0
  check_events || checked=$?
  test_info "Checking the documents of the pipeline failures"
  check_failures || failed=$?
  case "${GOLDEN}" in
    write)
      (( checked == 0 )) || test_die "Logstash did not read every fixture cleanly, ${GOLDEN_FILE} stays as it is"
      test_info "Writing the golden documents"
      golden write || test_die "The golden documents were not written"
      ;;
    compare)
      test_info "Comparing the documents with the golden file"
      golden compare || compared=$?
      ;;
  esac
  (( checked == 0 )) || test_die "Logstash did not read every fixture cleanly"
  (( failed == 0 )) || test_die "The lines that cannot be parsed did not end as failure documents"
  (( compared == 0 )) || test_die "The documents differ from ${GOLDEN_FILE} (a change on purpose: --golden write)"
  test_ok "Logstash fixture checks passed (any known exceptions are listed above)"
}

main "$@"
