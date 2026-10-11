#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="tpotinit_ilm"
DEFAULT_IMAGE="ghcr.io/telekom-security/tpotinit:24.04.2"
IMAGE=""
TPOTINIT_DIR="${DOCKER_ROOT}/tpotinit"

usage() {
  cat <<EOF
Usage: $0 [options]

Check the ILM rule of tpotinit (the retention of the policy tpot, TPOT_ES_RETENTION)
against docker/tpotinit/tests/ilm_cases.yml, the cases tpot's Python rule
(tpotctl/elastic.py) has to pass as well.

The image only provides the tools (bash, jq, yq): dist/bin/es_retention.sh of this
checkout is mounted over the one in it, so an older image tests the current rule.

Options:
  --image IMAGE      Image to test. Defaults to docker/tpotinit/docker-compose.yml.
  --timeout SEC      Accepted for runner compatibility.
  --bind-ip IP       Accepted for runner compatibility; nothing is published.
  --keep-artifacts   Keep the temporary files for debugging.
  -h, --help         Show this help message.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image) [[ $# -ge 2 ]] || test_die "--image requires an argument"; IMAGE="$2"; shift 2 ;;
      --image=*) IMAGE="${1#*=}"; shift ;;
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

write_runner() {
  cat > "${TEST_TMP_ROOT}/run.sh" <<'EOF'
#!/usr/bin/env bash
# Runs inside the tpotinit image, prints one line per check and FAIL lines on a mismatch.
set -u
CASES=/work/ilm_cases.yml
source /opt/tpot/bin/es_retention.sh
failed=0
count="$(yq -r '.values | length' "${CASES}")"
for (( i = 0; i < count; i++ )); do
  myMIN="$(yq -r ".values[$i].min_age" "${CASES}")"; want="$(yq -r ".values[$i].value" "${CASES}")"
  if [[ "${myMIN}" == "none" ]]; then policy='{"phases":{"hot":{"min_age":"0ms","actions":{}}}}'
  elif [[ "${myMIN}" == "30d-noaction" ]]; then policy='{"phases":{"hot":{"min_age":"0ms","actions":{}},"delete":{"min_age":"30d","actions":{}}}}'
  else policy="{\"phases\":{\"hot\":{\"min_age\":\"0ms\",\"actions\":{}},\"delete\":{\"min_age\":\"${myMIN}\",\"actions\":{\"delete\":{}}}}}"; fi
  got="$(fuILM_VALUE_OF <<<"${policy}")"
  if [[ "${got}" == "${want}" ]]; then echo "ok   value of ${myMIN}"; else echo "FAIL value of ${myMIN}: ${got} expected ${want}"; failed=1; fi
done
count="$(yq -r '.shapes | length' "${CASES}")"
for (( i = 0; i < count; i++ )); do
  name="$(yq -r ".shapes[$i].name" "${CASES}")"; want="$(yq -r ".shapes[$i].value" "${CASES}")"
  policy="$(yq -o=json -I=0 "explode(.) | .shapes[$i].policy" "${CASES}")"
  got="$(fuILM_VALUE_OF <<<"${policy}")"
  if [[ "${got}" == "${want}" ]]; then echo "ok   shape ${name}"; else echo "FAIL shape ${name}: [${got}] expected [${want}]"; failed=1; fi
done
count="$(yq -r '.cases | length' "${CASES}")"
for (( i = 0; i < count; i++ )); do
  name="$(yq -r ".cases[$i].name" "${CASES}")"
  value="$(yq -r ".cases[$i].value" "${CASES}")"
  force="$(yq -r ".cases[$i].force // false" "${CASES}")"
  shorten="$(yq -r ".cases[$i].shorten // false" "${CASES}")"
  days="$(yq -r ".cases[$i].oldest_days // \"\"" "${CASES}")"
  oldest=""; [[ -z "${days}" ]] || oldest=$(( 1800000000 - days * 86400 ))
  policy="$(yq -o=json -I=0 "explode(.) | .cases[$i].policy" "${CASES}")"   # explode: the &legacy anchor
  out="$(fuILM_DECIDE "${value}" "${force}" "${shorten}" "${oldest}" 1800000000 <<<"${policy}")"
  want_action="$(yq -r ".cases[$i].action" "${CASES}")"
  got="$(jq -r '.action' <<<"${out}")"
  want="${want_action}"
  if [[ "${want_action}" == "create" || "${want_action}" == "update" ]]; then
    got="${got} $(jq -r '(.policy.phases.delete.min_age // "none") + " " + (.policy._meta.tpot_retention|tostring) + " " + (.policy.phases|keys|join(","))' <<<"${out}")"
    want="${want} $(yq -r ".cases[$i].min_age + \" \" + .cases[$i].retention + \" \" + (.cases[$i].phases | join(\",\"))" "${CASES}")"
    # a delete phase always deletes
    if ! jq -e '.policy.phases.delete | if . == null then true else (.actions | has("delete")) end' <<<"${out}" >/dev/null;
      then echo "FAIL ${name}: the delete phase has no delete action"; failed=1
    fi
    if [[ "$(yq -r ".cases[$i] | has(\"actions\")" "${CASES}")" == "true" ]]; then
      got="${got} actions=$(jq -r '.policy.phases.delete.actions | keys | join(",")' <<<"${out}")"
      want="${want} actions=$(yq -r ".cases[$i].actions | join(\",\")" "${CASES}")"
    fi
  fi
  got="${got} current=$(jq -r '.current' <<<"${out}") managed=$(jq -r '.managed' <<<"${out}") held=$(jq -r '.held // ""' <<<"${out}")"
  want="${want} current=$(yq -r ".cases[$i].current" "${CASES}") managed=$(yq -r ".cases[$i].managed" "${CASES}") held=$(yq -r ".cases[$i].held // \"\"" "${CASES}")"
  if [[ "${got}" == "${want}" ]]; then echo "ok   ${name}"; else echo "FAIL ${name}: [${got}] expected [${want}]"; failed=1; fi
done
# fuILM_CANDIDATES: one URL, by TPOT_ES_URL or by the network mode of tpotinit in the compose file
myDIR="$(mktemp -d)"
printf 'services:\n  tpotinit:\n    network_mode: host\n' > "${myDIR}/host.yml"
printf 'services:\n  tpotinit:\n    networks: [x]\n' > "${myDIR}/net.yml"
fuCAND_CHECK () {   # $1 = name, $2 = the one URL expected; env: COMPOSE, TPOT_ES_URL
  local myGOT
  myGOT="$(fuILM_CANDIDATES)"
  if [[ "${myGOT}" == "$2" ]]; then echo "ok   $1"; else echo "FAIL $1: [${myGOT}] expected [$2]"; failed=1; fi
}
COMPOSE="${myDIR}/host.yml" fuCAND_CHECK "candidate: host network is the published port" "http://127.0.0.1:64298"
COMPOSE="${myDIR}/net.yml" fuCAND_CHECK "candidate: another network is the service name" "http://elasticsearch:9200"
COMPOSE="${myDIR}/missing.yml" fuCAND_CHECK "candidate: no compose file is the published port" "http://127.0.0.1:64298"
COMPOSE="${myDIR}/net.yml" TPOT_ES_URL="http://x:1" fuCAND_CHECK "candidate: TPOT_ES_URL is the only one" "http://x:1"
# fuILM_MAIN with curl stubbed (no Elasticsearch): only myUP answers _cluster/health, the GET
# of the policy answers myGET_CODE with myGET_BODY, _cat/indices answers myCAT_BODY (exit myCAT_RC);
# every call is logged to myCALLS, the body of a PUT to myPUT
myCALLS="$(mktemp)"; myPUT="$(mktemp)"
curl() {
  local myOUT="" myWRITE="" myMETHOD="GET" myURL=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -o) myOUT="$2"; shift 2 ;;
      -w) myWRITE="$2"; shift 2 ;;
      -X) myMETHOD="$2"; shift 2 ;;
      -H|-d|--max-time) shift 2 ;;
      http*) myURL="$1"; shift ;;
      *) shift ;;
    esac
  done
  if [[ "${myURL}" == */_cluster/health ]]; then [[ "${myURL}" == "${myUP}/_cluster/health" ]]; return; fi
  echo "${myMETHOD} ${myURL}" >> "${myCALLS}"
  if [[ "${myURL}" == */_cat/indices/* ]]; then printf '%s' "${myCAT_BODY:-[]}"; return "${myCAT_RC:-0}"; fi
  if [[ "${myMETHOD}" == "PUT" ]]; then cat > "${myPUT}"; return 0; fi
  if [[ -n "${myWRITE}" ]]; then printf '%s' "${myGET_BODY}" > "${myOUT}"; printf '%s' "${myGET_CODE}"; fi
  return 0
}
fuSTUB_CHECK () {   # $1 = name, $2 = text the output must have, $3 = the calls expected; env: the stub's, myRET
  local myOUT
  : > "${myCALLS}"; : > "${myPUT}"
  myOUT="$(TPOT_ES_RETENTION="${myRET:-7}" myILM_WAIT=0 fuILM_MAIN 2>&1)"
  if [[ "${myOUT}" == *"$2"* && "$(cat "${myCALLS}")" == "$3" ]]
    then echo "ok   $1"
    else echo "FAIL $1: [${myOUT}] calls=[$(cat "${myCALLS}")]"; failed=1
  fi
}
fuPUT_CHECK () {    # $1 = name, $2 = a jq test on the body of the PUT
  if jq -e "$2" "${myPUT}" >/dev/null 2>&1; then echo "ok   $1"; else echo "FAIL $1: [$(cat "${myPUT}")]"; failed=1; fi
}
myCAT="_cat/indices/logstash-*?format=json&h=creation.date"
# 200 without a policy is no missing policy: warn, never PUT
myUP="http://127.0.0.1:64298" myGET_CODE=200 myGET_BODY='{}' \
  fuSTUB_CHECK "200 without a policy writes nothing" \
  "# Warning: Elasticsearch answered without the ILM policy tpot, it is not checked." \
  "GET http://127.0.0.1:64298/_ilm/policy/tpot"
# the published port, or the service name of the compose network (MAC_WIN: tpotinit is no host container)
export COMPOSE="${myDIR}/net.yml"
myUP="http://elasticsearch:9200" myGET_CODE=404 myGET_BODY='{}' \
  fuSTUB_CHECK "Elasticsearch by its service name when tpotinit has no host network" \
  "# ILM policy tpot: created, events are kept for 7 days." \
  "GET http://elasticsearch:9200/_ilm/policy/tpot
GET http://elasticsearch:9200/${myCAT}
PUT http://elasticsearch:9200/_ilm/policy/tpot"
export COMPOSE="${myDIR}/host.yml"
myUP="http://elasticsearch:9200" myGET_CODE=404 myGET_BODY='{}' \
  fuSTUB_CHECK "with the host network the service name is not tried" \
  "# Warning: Elasticsearch did not answer" ""
myUP="http://es.example:9200" myGET_CODE=404 myGET_BODY='{}' TPOT_ES_URL="http://es.example:9200" \
  fuSTUB_CHECK "TPOT_ES_URL is the only one tried" \
  "# ILM policy tpot: created" \
  "GET http://es.example:9200/_ilm/policy/tpot
GET http://es.example:9200/${myCAT}
PUT http://es.example:9200/_ilm/policy/tpot"
myUP="http://elasticsearch:9200" myGET_CODE=404 myGET_BODY='{}' TPOT_ES_URL="http://es.example:9200" \
  fuSTUB_CHECK "nothing answers: nothing is written" \
  "# Warning: Elasticsearch did not answer" ""
# a managed 365 days policy and TPOT_ES_RETENTION=7: nothing is written, the output says why
myUP="http://127.0.0.1:64298" myGET_CODE=200 \
  myGET_BODY='{"tpot":{"policy":{"phases":{"hot":{"min_age":"0ms","actions":{}},"delete":{"min_age":"365d","actions":{"delete":{}}}},"_meta":{"managed":true,"tpot_retention":"365","description":"x"}}}}' \
  fuSTUB_CHECK "a shorter value waits, nothing is written" \
  "does not delete events on its own" \
  "GET http://127.0.0.1:64298/_ilm/policy/tpot"
# no policy, TPOT_ES_RETENTION=30, the oldest index 45 days old: the policy keeps everything, the value waits
myOLD=$(( ($(date +%s) - 45 * 86400) * 1000 ))
myUP="http://127.0.0.1:64298" myGET_CODE=404 myGET_BODY='{}' myCAT_BODY="[{\"creation.date\":\"${myOLD}\"}]" myRET=30 \
  fuSTUB_CHECK "no policy and older indices: created without a delete phase, the value waits" \
  "TPOT_ES_RETENTION=30" \
  "GET http://127.0.0.1:64298/_ilm/policy/tpot
GET http://127.0.0.1:64298/${myCAT}
PUT http://127.0.0.1:64298/_ilm/policy/tpot"
fuPUT_CHECK "  ... the policy has no delete phase" '.policy.phases.delete == null and .policy._meta.tpot_retention == "off"'
# no policy, the list of the indices does not come: unknown counts as older events, nothing is deleted
myUP="http://127.0.0.1:64298" myGET_CODE=404 myGET_BODY='{}' myCAT_BODY='' myCAT_RC=22 myRET=30 \
  fuSTUB_CHECK "no policy and no list of indices: created without a delete phase" \
  "TPOT_ES_RETENTION=30" \
  "GET http://127.0.0.1:64298/_ilm/policy/tpot
GET http://127.0.0.1:64298/${myCAT}
PUT http://127.0.0.1:64298/_ilm/policy/tpot"
fuPUT_CHECK "  ... the policy has no delete phase" '.policy.phases.delete == null'
myUP="http://127.0.0.1:64298" myGET_CODE=404 myGET_BODY='{}' myCAT_BODY='not json' myRET=30 \
  fuSTUB_CHECK "no policy and an unreadable list of indices: created without a delete phase" \
  "TPOT_ES_RETENTION=30" \
  "GET http://127.0.0.1:64298/_ilm/policy/tpot
GET http://127.0.0.1:64298/${myCAT}
PUT http://127.0.0.1:64298/_ilm/policy/tpot"
fuPUT_CHECK "  ... the policy has no delete phase" '.policy.phases.delete == null'
# no indices at all: the value is applied
myUP="http://127.0.0.1:64298" myGET_CODE=404 myGET_BODY='{}' myCAT_BODY='[]' myRET=30 \
  fuSTUB_CHECK "no policy and no indices: created with the value" \
  "# ILM policy tpot: created, events are kept for 30 days." \
  "GET http://127.0.0.1:64298/_ilm/policy/tpot
GET http://127.0.0.1:64298/${myCAT}
PUT http://127.0.0.1:64298/_ilm/policy/tpot"
fuPUT_CHECK "  ... the policy deletes after 30d" '.policy.phases.delete.min_age == "30d" and (.policy.phases.delete.actions | has("delete"))'
unset -f curl
rm -rf "${myCALLS}" "${myPUT}" "${myDIR}"
exit "${failed}"
EOF
  chmod 0755 "${TEST_TMP_ROOT}/run.sh"
}

main() {
  parse_args "$@"
  test_validate_timeout
  test_require_command docker
  docker info >/dev/null 2>&1 || test_die "Docker daemon is not accessible"
  IMAGE="${IMAGE:-$(test_read_compose_image tpotinit "${DEFAULT_IMAGE}")}"
  test_require_image "${IMAGE}" "docker compose -f docker/tpotinit/docker-compose.yml build"
  test_prepare_harness "${TEST_NAME}"
  trap 'if [[ "${TEST_KEEP_ARTIFACTS}" != "true" ]]; then rm -rf "${TEST_TMP_ROOT}"; else test_info "Artifacts in ${TEST_TMP_ROOT}"; fi' EXIT
  write_runner

  test_info "Checking the ILM rule of ${IMAGE} with the rule of this checkout"
  local output status=0
  output="$(docker run --rm --entrypoint bash \
    -v "${TPOTINIT_DIR}/dist/bin/es_retention.sh:/opt/tpot/bin/es_retention.sh:ro" \
    -v "${TPOTINIT_DIR}/tests/ilm_cases.yml:/work/ilm_cases.yml:ro" \
    -v "${TEST_TMP_ROOT}/run.sh:/work/run.sh:ro" \
    "${IMAGE}" /work/run.sh 2>&1)" || status=$?
  printf '%s\n' "${output}"
  [[ "${status}" -eq 0 ]] || test_die "The ILM rule of tpotinit does not match docker/tpotinit/tests/ilm_cases.yml"
  test_ok "tpotinit ILM rule matches all $(grep -c '^ok ' <<<"${output}") checks"
}

main "$@"
