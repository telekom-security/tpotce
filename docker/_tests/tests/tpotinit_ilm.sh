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
  policy="$(yq -o=json -I=0 "explode(.) | .cases[$i].policy" "${CASES}")"   # explode: the &legacy anchor
  out="$(fuILM_DECIDE "${value}" "${force}" <<<"${policy}")"
  want_action="$(yq -r ".cases[$i].action" "${CASES}")"
  got="$(jq -r '.action' <<<"${out}")"
  want="${want_action}"
  if [[ "${want_action}" == "create" || "${want_action}" == "update" ]]; then
    got="${got} $(jq -r '(.policy.phases.delete.min_age // "none") + " " + (.policy._meta.tpot_retention|tostring) + " " + (.policy.phases|keys|join(","))' <<<"${out}")"
    want="${want} $(yq -r ".cases[$i].min_age + \" \" + .cases[$i].retention + \" \" + (.cases[$i].phases | join(\",\"))" "${CASES}")"
  fi
  got="${got} current=$(jq -r '.current' <<<"${out}") managed=$(jq -r '.managed' <<<"${out}")"
  want="${want} current=$(yq -r ".cases[$i].current" "${CASES}") managed=$(yq -r ".cases[$i].managed" "${CASES}")"
  if [[ "${got}" == "${want}" ]]; then echo "ok   ${name}"; else echo "FAIL ${name}: [${got}] expected [${want}]"; failed=1; fi
done
# fuILM_MAIN with curl stubbed (no Elasticsearch): only myUP answers _cluster/health, the GET
# of the policy answers myGET_CODE with myGET_BODY; every other call is logged to myCALLS
myCALLS="$(mktemp)"
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
  if [[ -n "${myWRITE}" ]]; then printf '%s' "${myGET_BODY}" > "${myOUT}"; printf '%s' "${myGET_CODE}"; fi
  return 0
}
fuSTUB_CHECK () {   # $1 = name, $2 = text the output must have, $3 = the calls expected; env: the stub's
  local myOUT
  : > "${myCALLS}"
  myOUT="$(TPOT_ES_RETENTION=7 myILM_WAIT=0 fuILM_MAIN 2>&1)"
  if [[ "${myOUT}" == *"$2"* && "$(cat "${myCALLS}")" == "$3" ]]
    then echo "ok   $1"
    else echo "FAIL $1: [${myOUT}] calls=[$(cat "${myCALLS}")]"; failed=1
  fi
}
# 200 without a policy is no missing policy: warn, never PUT
myUP="http://127.0.0.1:64298" myGET_CODE=200 myGET_BODY='{}' \
  fuSTUB_CHECK "200 without a policy writes nothing" \
  "# Warning: Elasticsearch answered without the ILM policy tpot, it is not checked." \
  "GET http://127.0.0.1:64298/_ilm/policy/tpot"
# the published port first, the service name of the compose network second (MAC_WIN: tpotinit is no host container)
myUP="http://elasticsearch:9200" myGET_CODE=404 myGET_BODY='{}' \
  fuSTUB_CHECK "Elasticsearch by its service name when the published port does not answer" \
  "# ILM policy tpot: created, events are kept for 7 days." \
  "GET http://elasticsearch:9200/_ilm/policy/tpot
PUT http://elasticsearch:9200/_ilm/policy/tpot"
myUP="http://es.example:9200" myGET_CODE=404 myGET_BODY='{}' TPOT_ES_URL="http://es.example:9200" \
  fuSTUB_CHECK "TPOT_ES_URL is the only one tried" \
  "# ILM policy tpot: created" \
  "GET http://es.example:9200/_ilm/policy/tpot
PUT http://es.example:9200/_ilm/policy/tpot"
myUP="http://elasticsearch:9200" myGET_CODE=404 myGET_BODY='{}' TPOT_ES_URL="http://es.example:9200" \
  fuSTUB_CHECK "nothing answers: nothing is written" \
  "# Warning: Elasticsearch did not answer" ""
unset -f curl
rm -f "${myCALLS}"
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
