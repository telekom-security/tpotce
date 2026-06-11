#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="honeytrap"
DEFAULT_IMAGE="dtagdevsec/honeytrap:24.04.1"
IMAGE=""
LOG_DIR=""
ATTACKS_DIR=""
DOWNLOADS_DIR=""
JSON_LOG_FILE=""
ATTACKER_LOG_FILE=""
PROBE_CONTAINER_NAME=""

HONEYTRAP_PORTS=(2222 2323 31337)

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Honeytrap image.

The test configures NFQUEUE rules inside the temporary Honeytrap container
namespace only. It does not modify host firewall rules or repository data.

Options:
  --image IMAGE      Image to test. Defaults to dtagdevsec/honeytrap:24.04.1.
  --timeout SEC      Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP       Accepted for runner compatibility; Honeytrap exposes no host port.
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

prepare_honeytrap_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  ATTACKS_DIR="${TEST_TMP_ROOT}/attacks"
  DOWNLOADS_DIR="${TEST_TMP_ROOT}/downloads"
  JSON_LOG_FILE="${LOG_DIR}/attackers.json"
  ATTACKER_LOG_FILE="${LOG_DIR}/attacker.log"
  PROBE_CONTAINER_NAME="${TEST_PROJECT_NAME}-probe"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${ATTACKS_DIR}" "${DOWNLOADS_DIR}"
  chmod 0777 "${LOG_DIR}" "${ATTACKS_DIR}" "${DOWNLOADS_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  honeytrap:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    cap_add:
      - NET_ADMIN
    tmpfs:
      - /tmp/honeytrap:uid=2000,gid=2000,mode=0777
    volumes:
      - "${LOG_DIR}:/opt/honeytrap/var/log"
      - "${ATTACKS_DIR}:/opt/honeytrap/var/attacks"
      - "${DOWNLOADS_DIR}:/opt/honeytrap/var/downloads"
  probe:
    image: "${IMAGE}"
    container_name: "${PROBE_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    entrypoint: ["/usr/bin/sleep"]
    command: ["3600"]
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

apply_honeytrap_nfq_rules() {
  docker exec --user 0:0 "${TEST_CONTAINER_NAME}" iptables -w -A INPUT -s 127.0.0.1 -j ACCEPT
  docker exec --user 0:0 "${TEST_CONTAINER_NAME}" iptables -w -A INPUT -d 127.0.0.1 -j ACCEPT
  docker exec --user 0:0 "${TEST_CONTAINER_NAME}" iptables -w -A INPUT -p tcp --syn -m state --state NEW -j NFQUEUE
}

run_port_probe() {
  local port="$1"
  local token="$2"

  docker exec -i "${PROBE_CONTAINER_NAME}" /usr/bin/bash -s -- "honeytrap" "${port}" "${token}" "${TEST_TIMEOUT}" <<'BASH'
set -Eeuo pipefail

host="$1"
port="$2"
token="$3"
timeout="$4"
deadline=$((SECONDS + timeout))
last_error="probe did not run"

while (( SECONDS < deadline )); do
  if exec 3<>"/dev/tcp/${host}/${port}"; then
    printf 'GET /tpot-honeytrap-smoke/%s HTTP/1.0\r\nHost: %s\r\nUser-Agent: tpot-honeytrap-smoke/%s\r\n\r\n' "${token}" "${host}" "${token}" >&3
    if IFS= read -r -t 3 -n 1 _ <&3; then
      exec 3<&-
      exec 3>&-
      printf 'Honeytrap probe on %s:%s read a response byte for token %s\n' "${host}" "${port}" "${token}"
      exit 0
    fi
    exec 3<&- || true
    exec 3>&- || true
    last_error="connected but no response byte was read"
  else
    last_error="could not connect"
  fi
  sleep 1
done

printf 'Honeytrap probe failed on %s:%s for token %s: %s\n' "${host}" "${port}" "${token}" "${last_error}" >&2
exit 1
BASH
}

wait_for_json_event() {
  local port="$1"
  local token="$2"

  python3 - "${JSON_LOG_FILE}" "${port}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

json_log = Path(sys.argv[1])
expected_port = int(sys.argv[2])
token = sys.argv[3].encode("ascii")
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
last_error = None

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

            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:
                last_error = f"Invalid JSON in {json_log}:{line_number}: {exc}"
                continue

            connection = event.get("attack_connection") or {}
            payload = connection.get("payload") or {}
            try:
                payload_bytes = bytes.fromhex(str(payload.get("data_hex", "")))
            except ValueError as exc:
                last_error = f"Invalid payload data_hex in {json_log}:{line_number}: {exc}"
                continue

            if (
                connection.get("protocol") == "tcp"
                and int(connection.get("local_port", -1)) == expected_port
                and token in payload_bytes
            ):
                print(f"Honeytrap JSON event for port {expected_port} found in {json_log}:{line_number}")
                sys.exit(0)
    else:
        last_error = f"{json_log} does not exist yet"

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
print(f"No Honeytrap JSON event found in {json_log} for port {expected_port}", file=sys.stderr)
sys.exit(1)
PY
}

wait_for_attacker_log_port() {
  local port="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local pattern="->[[:space:]]+[^[:space:]]+:${port}[[:space:]]"

  while (( SECONDS < deadline )); do
    if [[ -f "${ATTACKER_LOG_FILE}" ]] && grep -E -- "${pattern}" "${ATTACKER_LOG_FILE}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  return 1
}

assert_no_runtime_errors() {
  local pattern="Permission denied|Operation not permitted|NFQ.*(fail|error)|nfq.*(fail|error)|fatal|panic|Segmentation fault|Traceback|Assertion .*failed"

  if grep -R -I -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Honeytrap runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Honeytrap runtime error found in Docker logs"
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

  prepare_honeytrap_harness
  test_enable_cleanup

  test_info "Starting isolated Honeytrap container and probe helper"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Honeytrap container did not stay running"
  wait_for_named_container "${PROBE_CONTAINER_NAME}" || test_die "Honeytrap probe helper did not stay running"
  test_ok "Containers are running"

  test_info "Waiting for Honeytrap NFQ mode startup"
  test_wait_for_file_text "Trapping attacks via NFQ" "${LOG_DIR}" || test_die "Honeytrap did not report NFQ mode startup"
  test_ok "Honeytrap reported NFQ mode startup"

  test_info "Applying container-local NFQUEUE rules"
  apply_honeytrap_nfq_rules || test_die "Could not apply container-local NFQUEUE rules"
  test_ok "Container-local NFQUEUE rules are active"

  local port=""
  local token=""
  for port in "${HONEYTRAP_PORTS[@]}"; do
    token="honeytrap-test-${port}-$(date +%s)-$$"
    test_info "Running Honeytrap probe on ${port}/tcp with token: ${token}"
    run_port_probe "${port}" "${token}" || test_die "Honeytrap probe failed on ${port}/tcp"
    test_wait_for_container || test_die "Honeytrap container stopped after ${port}/tcp probe"

    test_info "Waiting for Honeytrap JSON event on ${port}/tcp"
    wait_for_json_event "${port}" "${token}" || test_die "Expected Honeytrap JSON event was not found for ${port}/tcp"
    test_ok "Honeytrap JSON event was written for ${port}/tcp"

    test_info "Waiting for Honeytrap attacker.log entry on ${port}/tcp"
    wait_for_attacker_log_port "${port}" || test_die "Expected Honeytrap attacker.log entry was not found for ${port}/tcp"
    test_ok "Honeytrap attacker.log entry was written for ${port}/tcp"
  done

  assert_no_runtime_errors
  test_ok "No Honeytrap runtime errors found in logs"

  test_ok "Honeytrap post-build smoke test completed successfully"
}

main "$@"
