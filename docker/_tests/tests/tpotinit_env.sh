#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="tpotinit_env"
DEFAULT_IMAGE="dtagdevsec/tpotinit:24.04.2"
IMAGE=""
TPOTINIT_DIR="${DOCKER_ROOT}/tpotinit"

usage() {
  cat <<EOF
Usage: $0 [options]

Check the .env validation of tpotinit against docker/tpotinit/tests/env_cases.yml,
the cases tpot's Python rules (tpotctl/envschema.py) have to pass as well.

The image only provides the tools (bash, yq, ip, tzdata): dist/bin/env_validate.sh
and dist/etc/env.schema.yml of this checkout are mounted over the ones in it, so an
older image tests the current rules. On top of the cases it checks what only works
inside the container: an interface that does not exist, and that a sourced
validation hands the default of TPOT_PERSISTENCE_CYCLES to the entrypoint.

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
CASES=/work/env_cases.yml
VALIDATE=/opt/tpot/bin/env_validate.sh

load_example() {
  local line key value
  while IFS= read -r line; do
    [[ "${line}" =~ ^([A-Za-z_][A-Za-z0-9_]*)(=|:[[:space:]]+)(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[3]}"
    value="${value%\"}"; value="${value#\"}"
    export "${key}=${value}"
  done < /work/env.example
}

fuSET() { export "$1=${2#x}"; }

case_env() {   # $1 = index
  eval "$(yq -r "(.base // {}) * (.cases[$1].set // {}) | to_entries | .[] | \"fuSET \" + .key + \" \" + (\"x\" + (.value | tostring) | @sh)" "${CASES}")"
}

case_compose() {   # $1 = index, $2 = file
  { echo "services:"; yq -r ".cases[$1].services // [] | .[] | \"  \" + . + \":\"" "${CASES}"; } > "$2"
}

keys_of() {   # $1 = output, $2 = Error | Warning
  sed -n "s/^# $2: \([A-Z_]*\):.*/\1/p" <<<"$1" | sort -u | tr '\n' ' '
}

expected() {   # $1 = index, $2 = errors | warnings
  yq -r ".cases[$1].$2 // [] | .[]" "${CASES}" | sort -u | tr '\n' ' '
}

failed=0
count="$(yq -r '.cases | length' "${CASES}")"
for (( i = 0; i < count; i++ )); do
  name="$(yq -r ".cases[$i].name" "${CASES}")"
  out="$(
    load_example
    case_env "$i"
    case_compose "$i" /tmp/compose.yml
    bash "${VALIDATE}" /opt/tpot/etc/env.schema.yml /tmp/compose.yml 2>&1
  )"
  errors="$(keys_of "${out}" Error)"; warnings="$(keys_of "${out}" Warning)"
  want_errors="$(expected "$i" errors)"; want_warnings="$(expected "$i" warnings)"
  if [[ "${errors}" == "${want_errors}" && "${warnings}" == "${want_warnings}" ]]; then
    echo "ok   ${name}"
  else
    echo "FAIL ${name}: errors [${errors}] expected [${want_errors}], warnings [${warnings}] expected [${want_warnings}]"
    echo "${out}" | sed 's/^/     /'
    failed=1
  fi
done

# only checkable in the container: interfaces of this network namespace
out="$(load_example; export WEB_USER="$(yq -r '.base.WEB_USER' "${CASES}")" TPOT_CAPTURE_INTERFACE=nope0
       printf 'services:\n  p0f:\n' > /tmp/compose.yml; bash "${VALIDATE}" /opt/tpot/etc/env.schema.yml /tmp/compose.yml 2>&1)"
if grep -q '^# Error: TPOT_CAPTURE_INTERFACE: interface "nope0" does not exist' <<<"${out}"; then
  echo "ok   missing capture interface"
else
  echo "FAIL missing capture interface"; failed=1
fi
out="$(load_example; export WEB_USER="$(yq -r '.base.WEB_USER' "${CASES}")" TPOT_CAPTURE_INTERFACE=eth0
       printf 'services:\n  p0f:\n' > /tmp/compose.yml; bash "${VALIDATE}" /opt/tpot/etc/env.schema.yml /tmp/compose.yml 2>&1)"
if ! grep -q '^# Error: TPOT_CAPTURE_INTERFACE' <<<"${out}"; then
  echo "ok   existing capture interface"
else
  echo "FAIL existing capture interface"; failed=1
fi

# sourced as by entrypoint.sh: the default replaces an invalid cycle count
cycles="$(load_example; export WEB_USER="$(yq -r '.base.WEB_USER' "${CASES}")" TPOT_PERSISTENCE_CYCLES=abc
          COMPOSE=/tmp/none.yml; source "${VALIDATE}"; fuENV_VALIDATE >/dev/null; fuENV_REPORT >/dev/null
          echo "${TPOT_PERSISTENCE_CYCLES}:$?")"
if [[ "${cycles}" == "30:0" ]]; then
  echo "ok   sourced validation sets the default cycles"
else
  echo "FAIL sourced validation sets the default cycles (${cycles})"; failed=1
fi
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

  test_info "Checking the .env validation of ${IMAGE} with the rules of this checkout"
  local output status=0
  output="$(docker run --rm --entrypoint bash \
    -v "${TPOTINIT_DIR}/dist/bin/env_validate.sh:/opt/tpot/bin/env_validate.sh:ro" \
    -v "${TPOTINIT_DIR}/dist/etc/env.schema.yml:/opt/tpot/etc/env.schema.yml:ro" \
    -v "${TPOTINIT_DIR}/tests/env_cases.yml:/work/env_cases.yml:ro" \
    -v "${REPO_ROOT}/env.example:/work/env.example:ro" \
    -v "${TEST_TMP_ROOT}/run.sh:/work/run.sh:ro" \
    "${IMAGE}" /work/run.sh 2>&1)" || status=$?
  printf '%s\n' "${output}"
  [[ "${status}" -eq 0 ]] || test_die "The .env validation of tpotinit does not match docker/tpotinit/tests/env_cases.yml"
  test_ok "tpotinit .env validation matches all $(grep -c '^ok ' <<<"${output}") checks"
}

main "$@"
