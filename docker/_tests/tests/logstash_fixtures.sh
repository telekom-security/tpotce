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

A known failure (KNOWN in check_events: a defect that waits for a decision) is
named and does not fail the test; once it no longer fails, the test does, so
the entry goes.

The pipeline is docker/elk/logstash/dist/logstash.conf of this checkout with its
inputs and filters as they are; the inputs read the files from the start and the
output is a file instead of Elasticsearch. The container has no network.

The T-Pot Logstash image is used when it exists locally, otherwise the official
image of its Dockerfile (LS_VER). An image without the Listbot dictionaries
(/etc/listbot, the official one) gets small stand-ins for them.

Options:
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
      -h|--help) usage; exit 0 ;;
      *) test_die "Unknown option: $1" ;;
    esac
  done
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
    test_info "${IMAGE} is not built here, using ${official} with the T-Pot pipeline"
    IMAGE="${official}"
    return 0
  fi
  test_die "Image not found: ${IMAGE} (nor ${official}). Build it first, for example: cd ${LOGSTASH_DIR} && docker compose build"
}

# true when the image brings the Listbot dictionaries of the T-Pot image
image_has_listbot() {
  docker run --rm --network none --entrypoint /bin/sh "${IMAGE}" -c \
    'test -s /etc/listbot/iprep.yaml && test -s /etc/listbot/cve.yaml' >/dev/null 2>&1
}

# the data folder with one rendered fixture per honeypot, the manifest and the test pipeline
prepare_files() {
  mkdir -p "${TEST_TMP_ROOT}/data" "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/out" "${TEST_TMP_ROOT}/listbot"
  chmod 0777 "${TEST_TMP_ROOT}/out"
  PYTHONPATH="${REPO_ROOT}" python3 - "${REPO_ROOT}" "${TEST_TMP_ROOT}" "${TEST_SRC_IP}" <<'PY'
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

from tpotctl.checks import fixtures, stages

repo, root, src_ip = sys.argv[1], sys.argv[2], sys.argv[3]
data = os.path.join(root, "data")
# an hour ago: an event that keeps the time Logstash read it at shows up
ts = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=123456)
run = secrets.token_hex(4)
manifest = {}
for number, name in enumerate(fixtures.names()):
    marker = 51234 + number
    token = f"tpot-check-{run}{number:02d}"   # same length for all: no token is the start of another
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
for folder, _, _ in os.walk(data):
    os.chmod(folder, 0o755)
with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=1, sort_keys=True)

# logstash.conf with its inputs and filters as they are: every file input reads from the start
# and keeps no sincedb, the output section becomes one file of JSON lines
with open(os.path.join(repo, "docker", "elk", "logstash", "dist", "logstash.conf"), encoding="utf-8") as handle:
    conf = handle.read()
head, marker_text, _ = conf.partition("\n# Output section\n")
if not marker_text:
    sys.exit("logstash.conf has no '# Output section'")
inputs, filter_text, filters = head.partition("\n# Filter Section\n")
if not filter_text:
    sys.exit("logstash.conf has no '# Filter Section'")
count = inputs.count("\n  file {\n")
inputs = inputs.replace("\n  file {\n", '\n  file {\n    start_position => "beginning"\n    sincedb_path => "/dev/null"\n')
if count < len({entry["path"] for entry in manifest.values()}) - 8:   # conpot: one input, nine files
    sys.exit(f"logstash.conf: only {count} file inputs")
pipeline = (inputs + filter_text + filters + "\n# Output section (test)\noutput {\n"
            '  file {\n    path => "/out/events.json"\n    codec => json_lines\n  }\n}\n')
with open(os.path.join(root, "pipeline", "logstash.conf"), "w", encoding="utf-8") as handle:
    handle.write(pipeline)
print(f"{len(manifest)} fixtures rendered, {count} file inputs")
PY
  # stand-ins for the Listbot dictionaries of the T-Pot image (translate needs a readable file)
  printf '"192.0.2.1": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/iprep.yaml"
  printf '"0": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/cve.yaml"
  chmod 0755 "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/listbot"
  chmod 0644 "${TEST_TMP_ROOT}/pipeline/logstash.conf" "${TEST_TMP_ROOT}/listbot/"*.yaml
}

write_compose() {
  local listbot_volume=""
  if ! image_has_listbot; then
    test_info "${IMAGE} has no Listbot dictionaries, using stand-ins"
    listbot_volume="      - \"${TEST_TMP_ROOT}/listbot:/etc/listbot:ro\""
  fi
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  logstash:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    network_mode: none
    environment:
      - MY_EXTIP=${TEST_EXT_IP}
      - MY_INTIP=172.20.0.1
      - MY_HOSTNAME=tpot-fixtures
      - XPACK_GEOIP_DOWNLOADER_ENABLED=false
      - LS_JAVA_OPTS=-Xms512m -Xmx512m
    # the entrypoint of the image (env2yaml for the settings above, then logstash with these options)
    command: ["-f","/pipeline/logstash.conf", "--pipeline.ecs_compatibility=disabled", "--pipeline.workers=1"]
    volumes:
      - "${TEST_TMP_ROOT}/data:/data:ro"
      - "${TEST_TMP_ROOT}/pipeline:/pipeline:ro"
      - "${TEST_TMP_ROOT}/out:/out"
${listbot_volume}
EOF
}

# waits until every fixture has come out of the pipeline, or Logstash stopped, or the time is up
wait_for_events() {
  local expected seconds deadline got state
  expected="$(python3 -c 'import json, sys; print(len(json.load(open(sys.argv[1]))))' "${TEST_TMP_ROOT}/manifest.json")"
  seconds=$(( TEST_TIMEOUT > STARTUP_SECONDS ? TEST_TIMEOUT : STARTUP_SECONDS ))
  deadline=$(( SECONDS + seconds ))
  while (( SECONDS < deadline )); do
    got=0
    [[ -f "${TEST_TMP_ROOT}/out/events.json" ]] && got="$(grep -c . "${TEST_TMP_ROOT}/out/events.json" || true)"
    if (( got >= expected )); then
      sleep 5   # a fixture Logstash splits into more events shows up as well
      return 0
    fi
    state="$(docker inspect -f '{{.State.Status}}' "${TEST_CONTAINER_NAME}" 2>/dev/null || true)"
    [[ "${state}" == "exited" || "${state}" == "dead" ]] && { test_info "Logstash stopped"; return 1; }
    sleep 2
  done
  test_info "${got} of ${expected} events after ${seconds} s"
  return 0   # check_events names the missing ones
}

check_events() {
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
# came from. The file input names the file in [path] only where the event has no path of its own
# (Glutton, Go-pot, Honeyaml and Wordpot log the request path as path).
by_name, stray = {}, []
for event in events:
    text = json.dumps(event, sort_keys=True)
    owners = [name for name, want in manifest.items()
              if want["token"] in text or event.get("src_port") == want["marker"]]
    if not owners:
        owners = [name for name, want in manifest.items() if event.get("path") == want["path"]]
    if len(owners) == 1:
        by_name.setdefault(owners[0], []).append(event)
    else:
        stray.append(event)

COLUMN = re.compile(r"column\d+")
# a date Elasticsearch takes for a field that is mapped as date (dynamic date detection of
# tpot-template.json: strict_date_optional_time): an offset Z, +HH:MM, +HHMM or +HH, not two of them
ES_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}(:\d{2}(:\d{2}([.,]\d{1,9})?)?)?(Z|[+-]\d{2}(:?\d{2})?)?)?)?)?$")
# failures known and waiting for a decision: honeypot -> (the start of its problem, why)
KNOWN = {"elasticpot": ("timestamp ", "ElasticPot timestamp, decision pending")}
failed = known = 0
for name, want in sorted(manifest.items()):
    found = by_name.get(name, [])
    problems = []
    if len(found) != 1:
        problems.append(f"no event from {want['path']} (dropped by a filter?)" if not found
                        else f"{len(found)} events from {want['path']}, expected one")
    for event in found:
        text = json.dumps(event, sort_keys=True)
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

main() {
  parse_args "$@"
  test_check_dependencies
  test_validate_timeout
  choose_image

  test_prepare_harness "${TEST_NAME}"
  test_enable_cleanup
  TEST_ARTIFACT_LOG_DIR="${TEST_TMP_ROOT}/out"

  test_info "Rendering the fixtures and the test pipeline"
  prepare_files
  write_compose

  test_info "Starting Logstash (${IMAGE}) without network"
  test_compose up -d >/dev/null
  test_info "Waiting for the events of every fixture"
  wait_for_events || test_die "Logstash stopped before every fixture was read"

  test_info "Checking the events"
  check_events || test_die "Logstash did not read every fixture cleanly"
  test_ok "Logstash read every fixture cleanly"
}

main "$@"
