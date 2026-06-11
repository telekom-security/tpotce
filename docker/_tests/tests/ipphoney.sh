#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="ipphoney"
DEFAULT_IMAGE="dtagdevsec/ipphoney:24.04.1"
IMAGE=""
LOG_DIR=""
CONFIG_FILE=""
JSON_LOG_FILE=""
PROBE_CONTAINER_NAME=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the IPPHoney image.

Options:
  --image IMAGE      Image to test. Defaults to docker/ipphoney/docker-compose.yml.
  --timeout SEC      Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP       Accepted for runner compatibility; IPPHoney exposes no host port.
  --keep-artifacts   Keep temporary compose file and logs for debugging.
  -h, --help         Show this help message.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image)
        [[ $# -ge 2 ]] || test_die "--image requires an argument"
        IMAGE="$2"
        shift 2
        ;;
      --image=*)
        IMAGE="${1#*=}"
        shift
        ;;
      --timeout)
        [[ $# -ge 2 ]] || test_die "--timeout requires an argument"
        TEST_TIMEOUT="$2"
        shift 2
        ;;
      --timeout=*)
        TEST_TIMEOUT="${1#*=}"
        shift
        ;;
      --bind-ip)
        [[ $# -ge 2 ]] || test_die "--bind-ip requires an argument"
        TEST_BIND_IP="$2"
        shift 2
        ;;
      --bind-ip=*)
        TEST_BIND_IP="${1#*=}"
        shift
        ;;
      --keep-artifacts)
        TEST_KEEP_ARTIFACTS="true"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        test_die "Unknown option: $1"
        ;;
    esac
  done
}

validate_args() {
  test_validate_timeout
}

prepare_ipphoney_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  CONFIG_FILE="${TEST_TMP_ROOT}/honeypot.cfg"
  JSON_LOG_FILE="${LOG_DIR}/ipphoney.json"
  PROBE_CONTAINER_NAME="${TEST_PROJECT_NAME}-probe"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${CONFIG_FILE}" <<EOF
[honeypot]
sensor_name = ipphoney
blacklist = 127.0.0.1

[output_jsonlog]
enabled = true
logfile = log/ipphoney.json
epoch_timestamp = false
EOF

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  ipphoney:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    volumes:
      - "${LOG_DIR}:/opt/ipphoney/log"
      - "${CONFIG_FILE}:/opt/ipphoney/etc/honeypot.cfg:ro"
  probe:
    image: "${IMAGE}"
    container_name: "${PROBE_CONTAINER_NAME}"
    restart: "no"
    entrypoint: ["/bin/sh"]
    command: ["-c", "sleep 3600"]
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_named_container() {
  local container_name="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local state=""

  while (( SECONDS < deadline )); do
    state="$(docker inspect -f '{{.State.Status}}' "${container_name}" 2>/dev/null || true)"
    case "${state}" in
      running)
        return 0
        ;;
      exited|dead)
        return 1
        ;;
    esac
    sleep 1
  done

  return 1
}

run_ipphoney_probe() {
  local token="$1"

  docker exec -i "${PROBE_CONTAINER_NAME}" /bin/sh -s -- "ipphoney" "631" "${token}" "${TEST_TIMEOUT}" <<'SH'
set -eu

host="$1"
port="$2"
token="$3"
timeout="$4"
attempt=0
response=""
last_error="probe did not run"

while [ "${attempt}" -lt "${timeout}" ]; do
  response="$(
    printf 'GET /tpot-ipphoney-smoke/%s HTTP/1.1\r\nHost: %s\r\nUser-Agent: tpot-ipphoney-smoke/%s\r\nConnection: close\r\n\r\n' "${token}" "${host}" "${token}" \
      | nc -w 3 "${host}" "${port}" 2>&1 || true
  )"

  case "${response}" in
    HTTP/1.1*|HTTP/1.0*)
      first_line="$(printf '%s\n' "${response}" | sed -n '1p')"
      printf 'IPPHoney probe response: %s\n' "${first_line}"
      exit 0
      ;;
    "")
      last_error="no response"
      ;;
    *)
      first_line="$(printf '%s\n' "${response}" | sed -n '1p')"
      last_error="unexpected response: ${first_line}"
      ;;
  esac

  attempt=$((attempt + 1))
  sleep 1
done

printf 'IPPHoney probe failed on %s:%s for token %s: %s\n' "${host}" "${port}" "${token}" "${last_error}" >&2
exit 1
SH
}

wait_for_json_log_event() {
  local token="$1"

  python3 - "${JSON_LOG_FILE}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

json_log = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None


def has_token(value):
    return isinstance(value, str) and token in value


while time.monotonic() < deadline:
    if json_log.exists():
        try:
            lines = json_log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            last_error = f"Could not read {json_log}: {exc}"
            time.sleep(1)
            continue

        for line_number, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith("{"):
                continue

            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:
                print(f"Invalid JSON in {json_log}:{line_number}: {exc}", file=sys.stderr)
                sys.exit(1)

            if not isinstance(event, dict):
                print(f"JSON event in {json_log}:{line_number} is not an object", file=sys.stderr)
                sys.exit(1)

            if (
                event.get("eventid") == "ipphoney.connect"
                and event.get("request") == "GET"
                and event.get("dst_port") == 631
                and event.get("sensor") == "ipphoney"
                and has_token(event.get("url"))
                and has_token(event.get("user_agent"))
                and event.get("src_ip")
                and event.get("src_port")
                and event.get("dst_ip")
            ):
                print(f"IPPHoney JSON event found in {json_log}:{line_number}")
                sys.exit(0)
    else:
        last_error = f"{json_log} does not exist yet"

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
print(f"No matching IPPHoney JSON event found in {json_log} for token {token}", file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern="Traceback|Unhandled Error|Cannot listen|Permission denied|ImportError|Address already in use"

  if grep -R -I -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "IPPHoney runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "IPPHoney runtime error found in Docker logs"
  fi
}

main() {
  parse_args "$@"
  validate_args
  test_check_dependencies

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  prepare_ipphoney_harness
  test_enable_cleanup

  test_info "Starting isolated IPPHoney container and probe helper"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "IPPHoney container did not stay running"
  wait_for_named_container "${PROBE_CONTAINER_NAME}" || test_die "IPPHoney probe helper did not stay running"
  test_ok "Containers are running"

  local token="ipphoney-test-$(date +%s)-$$"

  test_info "Running IPPHoney HTTP probe with token: ${token}"
  run_ipphoney_probe "${token}" || test_die "IPPHoney probe failed on ipphoney:631"
  test_wait_for_container || test_die "IPPHoney container stopped after probe"

  test_info "Waiting for IPPHoney JSON log event"
  wait_for_json_log_event "${token}" || test_die "Expected IPPHoney event was not found in ipphoney.json"
  test_ok "IPPHoney connection event was written to ipphoney.json"

  assert_no_runtime_errors
  test_ok "No IPPHoney runtime errors found in logs"

  test_ok "IPPHoney post-build smoke test completed successfully"
}

main "$@"
