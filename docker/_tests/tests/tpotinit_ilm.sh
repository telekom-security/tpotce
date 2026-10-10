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
# 200 without a policy is no missing policy: warn, never PUT (curl stubbed, no Elasticsearch)
myMARK="$(mktemp)"
curl() {
  local myOUT="" myWRITE=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -o) myOUT="$2"; shift 2 ;;
      -w) myWRITE="$2"; shift 2 ;;
      -X) echo "$2" >> "${myMARK}"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [[ -n "${myWRITE}" ]]; then echo '{}' > "${myOUT}"; printf '200'; fi
  return 0
}
out="$(TPOT_ES_RETENTION=7 myILM_WAIT=5 fuILM_MAIN 2>&1)"
unset -f curl
if [[ "${out}" == *"# Warning: Elasticsearch answered without the ILM policy tpot, it is not checked."* && ! -s "${myMARK}" ]]
  then echo "ok   200 without a policy writes nothing"
  else echo "FAIL 200 without a policy writes nothing: [${out}] put=[$(cat "${myMARK}")]"; failed=1
fi
rm -f "${myMARK}"
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
