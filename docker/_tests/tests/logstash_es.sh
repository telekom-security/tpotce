#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="logstash_es"
LOGSTASH_DIR="${DOCKER_ROOT}/elk/logstash"
IMAGE=""
INIT_IMAGE=""
# Logstash and Elasticsearch need a while to start; --timeout of the runner is the least each wait gets
STARTUP_SECONDS=300
VERSION="test-release"
TEMPLATES="logstash tpot-failures tpot-sensor-heartbeat tpot-sensor-status"
# a Cowrie line whose geoip.ip Elasticsearch rejects (an ip field of the template logstash)
REJECTED='{"eventid":"cowrie.session.connect","session":"logstash_es_index","geoip":{"ip":"no-address"}}'

usage() {
  cat <<EOF
Usage: $0 [options]

Check that the index templates come from tpotinit and that Logstash waits for them:
the HIVE pipelines of dist/pipeline/ run through the T-Pot entrypoint of this checkout
(dist/entrypoint.sh) in the official Logstash image, the healthcheck is the one of the
Dockerfile (every 5 s), es_retention.sh of this checkout runs in the tpotinit image.

  (a) Logstash without templates waits, stays healthy and names the missing ones.
  (f) es_retention.sh started while Elasticsearch is down (it starts 20 s later)
      writes the policies and templates once it answers,
  (b) then Logstash starts,
  (c) the template logstash carries _meta.tpot_version,
  (e) Logstash created no template of its own,
  (d) a template logstash changed by hand is overwritten by the next run,
  (g) an event Elasticsearch rejects (a mapping conflict) goes through the dead
      letter queue of process into tpot-failures-* as a document of stage index.

The network is internal: nothing outside it is reached, Listbot least of all (a fresh
cache and stand-in dictionaries, the log must show no download). Nothing is published,
the requests run inside the network. Uses local images only; never builds or pulls.

Options:
  --image IMAGE           Logstash image. Default: docker.elastic.co/logstash/logstash:<LS_VER>.
  --tpotinit-image IMAGE  Image for es_retention.sh (bash, jq, curl). Default: docker/tpotinit/docker-compose.yml.
  --timeout SEC           Least time of each wait (at least ${STARTUP_SECONDS} s).
  --bind-ip IP            Accepted for runner compatibility; nothing is published.
  --keep-artifacts        Keep the temporary files for debugging.
  -h, --help              Show this help message.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image) [[ $# -ge 2 ]] || test_die "--image requires an argument"; IMAGE="$2"; shift 2 ;;
      --image=*) IMAGE="${1#*=}"; shift ;;
      --tpotinit-image) [[ $# -ge 2 ]] || test_die "--tpotinit-image requires an argument"; INIT_IMAGE="$2"; shift 2 ;;
      --tpotinit-image=*) INIT_IMAGE="${1#*=}"; shift ;;
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

# the pipeline folder as the Dockerfile builds it, the data folder with the version of tpotinit and a
# fresh Listbot cache (no download is tried), stand-in dictionaries for /etc/listbot
prepare_files() {
  mkdir -p "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/listbot" "${TEST_TMP_ROOT}/data/tpot/etc/compose" \
    "${TEST_TMP_ROOT}/data/elk/listbot" "${TEST_TMP_ROOT}/data/cowrie/log"
  printf '%s\n' "${REJECTED}" > "${TEST_TMP_ROOT}/data/cowrie/log/cowrie.json"
  cp "${LOGSTASH_DIR}/dist/pipeline/"*.conf "${LOGSTASH_DIR}/dist/sensor_heartbeat.rb" "${TEST_TMP_ROOT}/pipeline/"
  printf '"192.0.2.1": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/iprep.yaml"
  printf '"0": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/cve.yaml"
  printf 'MY_EXTIP=192.0.2.10\nMY_INTIP=172.20.0.1\nMY_HOSTNAME=tpot-logstash-es\nMY_VERSION=%s\n' "${VERSION}" > "${TEST_TMP_ROOT}/data/tpot/etc/compose/elk_environment"
  python3 - "${TEST_TMP_ROOT}" <<'PY'
import bz2
import sys
from pathlib import Path

root = Path(sys.argv[1])
for name in ("iprep", "cve"):
    data = (root / "listbot" / (name + ".yaml")).read_bytes()
    (root / "data/elk/listbot" / (name + ".yaml.bz2")).write_bytes(bz2.compress(data))
PY
  find "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/listbot" "${TEST_TMP_ROOT}/data" -type d -exec chmod 0755 {} +
  find "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/listbot" "${TEST_TMP_ROOT}/data" -type f -exec chmod 0644 {} +
}

write_compose() {
  local health retries
  # the healthcheck of the Dockerfile, so the two cannot drift
  health="$(sed -n 's/^HEALTHCHECK .*CMD //p' "${LOGSTASH_DIR}/Dockerfile")"
  retries="$(sed -n 's/^HEALTHCHECK .*--retries=\([0-9]*\).*/\1/p' "${LOGSTASH_DIR}/Dockerfile")"
  [[ -n "${health}" && "${health}" != *'"'* ]] || test_die "No usable HEALTHCHECK in ${LOGSTASH_DIR}/Dockerfile"
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
    entrypoint: ["/bin/bash", "/usr/local/bin/tpot-logstash-entrypoint.sh"]
    command: []
    environment:
      TPOT_TYPE: HIVE
      LS_JAVA_OPTS: "-Xms512m -Xmx512m"
    healthcheck:
      test: ["CMD-SHELL", "${health}"]
      interval: 5s
      retries: ${retries:-10}
    volumes:
      - "${LOGSTASH_DIR}/dist/entrypoint.sh:/usr/local/bin/tpot-logstash-entrypoint.sh:ro"
      - "${TEST_TMP_ROOT}/pipeline:/usr/share/logstash/pipeline:ro"
      - "${LOGSTASH_DIR}/dist/pipelines.yml:/usr/share/logstash/config/pipelines.yml:ro"
      - "${LOGSTASH_DIR}/dist/logstash.yml:/usr/share/logstash/config/logstash.yml:ro"
      - "${TEST_TMP_ROOT}/data:/data:ro"
      - "${TEST_TMP_ROOT}/listbot:/etc/listbot:ro"
  tpotinit:
    image: "${INIT_IMAGE}"
    entrypoint: ["/bin/bash", "/opt/tpot/bin/es_retention.sh"]
    environment:
      TPOT_ES_URL: "http://elasticsearch:9200"
      TPOT_ES_RETENTION: "7"
      TPOT_SENSOR_STATUS_RETENTION: "30"
      TPOT_ES_RETENTION_WAIT: "5"
      TPOT_ES_TEMPLATE_RETRY: "5"
      TPOT_VERSION: "${VERSION}"
    volumes:
      - "${DOCKER_ROOT}/tpotinit/dist/bin/es_retention.sh:/opt/tpot/bin/es_retention.sh:ro"
      - "${DOCKER_ROOT}/tpotinit/dist/etc/ilm:/opt/tpot/etc/ilm:ro"
networks:
  default:
    internal: true
EOF
}

# the logs of a service of the test
logs_of() {
  test_compose logs --no-color --no-log-prefix "$1" 2>&1
}

# waits until the logs of service $1 hold the text $2
wait_for_log() {
  local deadline=$(( SECONDS + WAIT_SECONDS ))
  while (( SECONDS < deadline )); do
    logs_of "$1" | grep -q -F -- "$2" && return 0
    sleep 2
  done
  test_die "No '$2' in the log of $1 after ${WAIT_SECONDS} s"
}

# a request to Elasticsearch from inside the network: es METHOD PATH [BODY]
es() {
  if [[ $# -ge 3 ]]; then
    test_compose exec -T elasticsearch curl -s -X "$1" "http://127.0.0.1:9200$2" \
      -H 'Content-Type: application/json' --data-binary @- <<<"$3"
  else
    test_compose exec -T elasticsearch curl -s -X "$1" "http://127.0.0.1:9200$2"
  fi
}

health_of() {
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "${TEST_CONTAINER_NAME}" 2>/dev/null || true
}

# (a) waits, stays healthy for at least 3 intervals (of 5 s) and names the missing templates
check_waiting() {
  local deadline=$(( SECONDS + WAIT_SECONDS )) state="" first="" line
  test_info "(a) Logstash without Elasticsearch and templates"
  wait_for_log logstash "Waiting for the index templates of tpotinit (es_retention.sh):"
  line="$(logs_of logstash | grep -F "Waiting for the index templates" | head -n 1)"
  for name in ${TEMPLATES}; do
    [[ "${line}" == *"${name}"* ]] || test_die "(a) the wait does not name ${name}: ${line}"
  done
  while (( SECONDS < deadline )); do
    state="$(health_of)"
    if [[ "${state}" == healthy ]]; then
      [[ -n "${first}" ]] || first=${SECONDS}
      (( SECONDS - first > 15 )) && break
    elif [[ -n "${first}" ]]; then
      test_die "(a) Logstash turned ${state} while it waits"
    fi
    sleep 1
  done
  [[ -n "${first}" ]] && (( SECONDS - first > 15 )) || test_die "(a) Logstash is not healthy while it waits: ${state:-no health}"
  ! logs_of logstash | grep -q -F "Index templates in place" || test_die "(a) Logstash did not wait"
  test_ok "(a) Logstash waits and stays healthy for $(( SECONDS - first )) s: ${line}"
}

# (f) es_retention.sh while Elasticsearch is down, Elasticsearch 20 s later
check_late_elasticsearch() {
  local started deadline state code out
  test_info "(f) es_retention.sh before Elasticsearch"
  started=${SECONDS}
  test_compose up -d tpotinit >/dev/null
  wait_for_log tpotinit "# Warning: Elasticsearch did not answer within 5 s, ILM policies and index templates wait for it."
  (( SECONDS - started >= 20 )) || sleep $(( 20 - (SECONDS - started) ))
  test_compose up -d elasticsearch >/dev/null
  deadline=$(( SECONDS + WAIT_SECONDS ))
  while (( SECONDS < deadline )); do
    state="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "$(test_compose ps -a -q tpotinit)" 2>/dev/null || true)"
    [[ "${state}" == exited* ]] && break
    sleep 2
  done
  out="$(logs_of tpotinit)"
  [[ "${state}" == "exited 0" ]] || test_die "(f) es_retention.sh did not end with 0: ${state}; ${out}"
  for code in "ILM policy tpot: created" "ILM policy tpot-sensor-status: created" \
              "# Index template logstash: written (T-Pot ${VERSION})." \
              "# Index template tpot-sensor-status: created." "# Index template tpot-sensor-heartbeat: created." \
              "# Index template tpot-failures: created."; do
    grep -q -F -- "${code}" <<<"${out}" || test_die "(f) no '${code}' in the log of es_retention.sh: ${out}"
  done
  test_ok "(f) policies and templates written once Elasticsearch answered"
}

# (b) Logstash starts, (c) _meta.tpot_version, (e) no template of Logstash's own
check_started() {
  local event deadline found=""
  test_info "(b) Logstash starts once the templates are there"
  wait_for_log logstash "Index templates in place, starting Logstash."
  wait_for_log logstash "Pipelines running"
  test_ok "(b) Logstash started after the templates"

  python3 -c 'import json, sys; m = json.load(sys.stdin)["index_templates"][0]["index_template"]["_meta"]
assert m["tpot_version"] == sys.argv[1], m' "${VERSION}" <<<"$(es GET /_index_template/logstash)" \
    || test_die "(c) the template logstash has no _meta.tpot_version ${VERSION}"
  test_ok "(c) the template logstash carries _meta.tpot_version ${VERSION}"

  # one event through the sensors pipeline of the HIVE: the outputs run, the index takes T-Pot's template
  event='{"type":"Cowrie","message":"logstash_es","src_ip":"192.0.2.1"}'
  deadline=$(( SECONDS + WAIT_SECONDS ))
  while (( SECONDS < deadline )); do
    test_compose exec -T elasticsearch curl -s -o /dev/null -X POST "http://logstash:64305" \
      -H 'Content-Type: application/json' --data-binary "${event}" || true
    es POST /logstash-*/_refresh >/dev/null || true
    if es GET '/logstash-*/_search?q=message:logstash_es&size=0' | grep -q '"value":[1-9]'; then
      found=1
      break
    fi
    sleep 3
  done
  [[ -n "${found}" ]] || test_die "(e) no event of the test reached logstash-*"
  python3 - "$(es GET /_index_template)" "$(es GET /_template)" "$(es GET '/logstash-*/_settings?flat_settings=true')" \
    "${TEMPLATES}" "${VERSION}" <<'PY' || test_die "(e) Logstash created a template of its own"
import json
import sys

composable, legacy, settings = (json.loads(value) for value in sys.argv[1:4])
ours = sorted(sys.argv[4].split())
templates = {item["name"]: item["index_template"] for item in composable["index_templates"]}
assert set(ours) <= set(templates), f"templates {sorted(templates)}, missing some of {ours}"
others = {name: template for name, template in templates.items() if name not in ours}
# the templates of Elasticsearch itself are marked managed or hidden (a name with a dot)
foreign = sorted(name for name, template in others.items()
                 if not name.startswith(".") and not (template.get("_meta") or {}).get("managed"))
assert not foreign, f"templates of no one known: {foreign}"
touching = sorted(name for name, template in others.items()
                  if any(pattern.startswith(("logstash", "tpot-")) for pattern in template["index_patterns"]))
assert not touching, f"other templates for T-Pot's indices: {touching}"
assert not [name for name in legacy if not name.startswith(".")], f"legacy templates {sorted(legacy)}"
logstash = [item for item in composable["index_templates"] if item["name"] == "logstash"][0]["index_template"]
assert logstash["_meta"]["tpot_version"] == sys.argv[5], logstash["_meta"]
for index, value in settings.items():
    assert value["settings"].get("index.lifecycle.name") == "tpot", (index, value["settings"])
PY
  test_ok "(e) Elasticsearch holds only T-Pot's templates, logstash-* follows the template of tpotinit"
}

# (d) a template changed by hand is overwritten by the next run of es_retention.sh
check_overwrite() {
  local before edited answer out
  test_info "(d) es_retention.sh overwrites a template logstash changed by hand"
  before="$(es GET /_index_template/logstash)"
  # what a PUT takes (a GET also names the times of creation and change)
  edited="$(python3 -c 'import json, sys; t = json.load(sys.stdin)["index_templates"][0]["index_template"]
t = {key: value for key, value in t.items() if key in ("index_patterns", "template", "composed_of", "priority")}
t["_meta"] = {"tpot_version": "hand-edited"}; t["priority"] = 7; print(json.dumps(t))' <<<"${before}")"
  answer="$(es PUT /_index_template/logstash "${edited}")"
  grep -q '"acknowledged":true' <<<"${answer}" || test_die "(d) the edit was not taken: ${answer}"
  [[ "$(es GET /_index_template/logstash)" == *'"hand-edited"'* ]] || test_die "(d) the edit is not in Elasticsearch"
  out="$(test_compose run --rm -T --no-deps tpotinit 2>&1)" || test_die "(d) es_retention.sh failed: ${out}"
  python3 - "${before}" "$(es GET /_index_template/logstash)" <<'PY' || test_die "(d) the template was not overwritten: ${out}"
import json
import sys

before, after = (json.loads(value)["index_templates"][0]["index_template"] for value in sys.argv[1:3])
# the times of creation and change are Elasticsearch's own
for template in (before, after):
    for key in [key for key in template if key.endswith("_date_millis") or key.endswith("_date")]:
        del template[key]
assert after == before, (before, after)
PY
  grep -q -F "# Index template logstash: written (T-Pot ${VERSION})." <<<"${out}" || test_die "(d) ${out}"
  test_ok "(d) the template changed by hand was overwritten"
}

# (g) the Cowrie line of the test (REJECTED) is rejected by Elasticsearch and kept in tpot-failures-*
check_index_failure() {
  local deadline answer="" found=""
  test_info "(g) an event Elasticsearch rejects lands in tpot-failures-*"
  deadline=$(( SECONDS + WAIT_SECONDS ))
  while (( SECONDS < deadline )); do
    es POST '/tpot-failures-*/_refresh' >/dev/null || true
    answer="$(es GET '/tpot-failures-*/_search?q=failure.stage:index&size=10' || true)"
    if grep -q '"value":[1-9]' <<<"${answer}"; then
      found=1
      break
    fi
    sleep 3
  done
  [[ -n "${found}" ]] || test_die "(g) no document of stage index in tpot-failures-* after ${WAIT_SECONDS} s: ${answer}"
  python3 - "${answer}" "$(es GET '/tpot-failures-*/_settings?flat_settings=true')" \
    "$(es GET '/logstash-*/_search?q=session:logstash_es_index&size=0')" "${REJECTED}" <<'PY' \
    || test_die "(g) the document of stage index is not as expected: ${answer}"
import json
import sys

answer, settings, events = (json.loads(value) for value in sys.argv[1:4])
rejected = json.loads(sys.argv[4])
hits = answer["hits"]["hits"]
assert len(hits) == 1, hits
doc = hits[0]["_source"]
print(json.dumps(doc, sort_keys=True))
failure = doc["failure"]
assert failure["stage"] == "index", failure
# the reason of the dead letter queue: the 400 of Elasticsearch on geoip.ip (document_parsing_exception,
# older releases: mapper_parsing_exception)
assert "status: 400" in failure["reason"] and "_parsing_exception" in failure["reason"] \
    and "[geoip.ip]" in failure["reason"], failure["reason"]
original = json.loads(failure["original"])
assert original["session"] == rejected["session"] and original["geoip"] == rejected["geoip"], original
assert "@metadata" not in original, original
assert doc["type"] == "Cowrie" and doc["t-pot_log_path"] == "/data/cowrie/log/cowrie.json", doc
assert doc["t-pot_hostname"] == "tpot-logstash-es", doc
assert sorted(doc) == ["@timestamp", "failure", "t-pot_hostname", "t-pot_log_path", "type"], sorted(doc)
assert sorted(failure) == ["original", "reason", "stage"], sorted(failure)
for index, value in settings.items():
    assert value["settings"].get("index.lifecycle.name") == "tpot", (index, value["settings"])
assert events["hits"]["total"]["value"] == 0, events
PY
  test_ok "(g) the rejected event is a document of stage index in tpot-failures-*"
}

# the dead letter queue input of failures finds the folder of process on a fresh start: no plugin error
check_clean_log() {
  local out
  out="$(logs_of logstash)"
  ! grep -q -E "NoSuchFileException|unrecoverable error" <<<"${out}" \
    || test_die "Logstash logged a plugin error: $(grep -E "NoSuchFileException|unrecoverable error" <<<"${out}" | head -n 3)"
  test_ok "No plugin error in the log of Logstash"
}

# R-8a: Listbot is never asked
check_no_listbot() {
  local out
  out="$(logs_of logstash)"
  ! grep -q -F "Downloading latest" <<<"${out}" || test_die "Logstash downloaded from Listbot"
  grep -q -F "Cached iprep.yaml.bz2 is current" <<<"${out}" || test_die "Logstash did not take the Listbot cache"
  test_ok "No download from Listbot (the cache of the test was current)"
}

main() {
  local ls_version es_version
  parse_args "$@"
  test_check_dependencies
  test_validate_timeout
  WAIT_SECONDS=$(( TEST_TIMEOUT > STARTUP_SECONDS ? TEST_TIMEOUT : STARTUP_SECONDS ))
  ls_version="$(sed -n 's/^ARG LS_VER=//p' "${LOGSTASH_DIR}/Dockerfile" | head -n 1)"
  es_version="$(sed -n 's/^ARG ES_VER=//p' "${DOCKER_ROOT}/elk/elasticsearch/Dockerfile" | head -n 1)"
  IMAGE="${IMAGE:-docker.elastic.co/logstash/logstash:${ls_version}}"
  ES_IMAGE="docker.elastic.co/elasticsearch/elasticsearch:${es_version}"
  INIT_IMAGE="${INIT_IMAGE:-$(test_read_compose_image tpotinit "")}"
  test_require_image "${IMAGE}" "docker pull ${IMAGE}"
  test_require_image "${ES_IMAGE}" "docker pull ${ES_IMAGE}"
  test_require_image "${INIT_IMAGE}" "cd docker/tpotinit && docker compose build"
  test_prepare_harness "${TEST_NAME}"
  test_enable_cleanup
  prepare_files
  write_compose

  test_compose up -d logstash >/dev/null
  check_waiting
  check_late_elasticsearch
  check_started
  check_overwrite
  check_index_failure
  check_clean_log
  check_no_listbot
  test_ok "Templates of tpotinit and the wait of Logstash passed"
}

main "$@"
