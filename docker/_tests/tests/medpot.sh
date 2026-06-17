#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="medpot"
DEFAULT_IMAGE="dtagdevsec/medpot:24.04.1"
IMAGE=""
HOST_PORT=""
LOG_DIR=""
EWS_CFG_FILE=""
MEDPOT_LOG_FILE=""
MAPPED_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Medpot image.

Options:
  --image IMAGE      Image to test. Defaults to docker/medpot/docker-compose.yml.
  --host-port PORT   Host TCP port for HL7/FHIR. Default: dynamic loopback port.
  --timeout SEC      Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP       Host IP to bind. Default: 127.0.0.1.
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
      --host-port|--hl7-port|--port)
        [[ $# -ge 2 ]] || test_die "$1 requires an argument"
        HOST_PORT="$2"
        shift 2
        ;;
      --host-port=*|--hl7-port=*|--port=*)
        HOST_PORT="${1#*=}"
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

  if [[ -n "${HOST_PORT}" ]]; then
    test_validate_port "${HOST_PORT}"
  fi
}

prepare_medpot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  EWS_CFG_FILE="${TEST_TMP_ROOT}/ews.cfg"
  MEDPOT_LOG_FILE="${LOG_DIR}/medpot.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${EWS_CFG_FILE}" <<EOF
[EWS]
rhost_first = http://127.0.0.1:1/
username = smoke
token = smoke

[GLASTOPFV3]
nodeid = glastopfv3-smoke
EOF

  local port_mapping="${TEST_BIND_IP}::2575"
  if [[ -n "${HOST_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${HOST_PORT}:2575"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  medpot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/var/log/medpot"
      - "${EWS_CFG_FILE}:/etc/ews.cfg:ro"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_hl7_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
connect_timeout = max(1.0, min(float(timeout), 5.0))

payload = (
    f"MSH|^~\\&|TPOTSMOKE|TEST|MEDPOT|TEST|202606171200||ADT^A01|{token}|P|2.6\r"
    f"EVN|A01|202606171200\r"
    f"PID|||{token}||SMOKE^MEDPOT||19700101|U\r"
).encode("ascii")

deadline = time.monotonic() + timeout
response = bytearray()

try:
    with socket.create_connection((host, port), timeout=connect_timeout) as sock:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)

        while time.monotonic() < deadline:
            remaining = max(0.1, min(1.0, deadline - time.monotonic()))
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue

            if not chunk:
                break

            response.extend(chunk)
            if b"MSA|AA" in response:
                break
except Exception as exc:
    print(f"Medpot HL7 probe failed: {exc}", file=sys.stderr)
    sys.exit(1)

if b"MSH|" not in response or b"MSA|AA" not in response or b"Success" not in response:
    preview = bytes(response[:200])
    print(f"Medpot returned an unexpected HL7 ACK payload: {preview!r}", file=sys.stderr)
    sys.exit(1)

print(f"Medpot HL7 probe succeeded for token {token}")
PY
}

run_hl7_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_hl7_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

wait_for_medpot_log_event() {
  local token="$1"

  python3 - "${MEDPOT_LOG_FILE}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import base64
import json
import sys
import time
from pathlib import Path

log_file = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None
required_keys = {"level", "message", "timestamp", "src_port", "src_ip", "data"}


def decode_payload(encoded):
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError(f"invalid base64 data: {exc}") from exc


while time.monotonic() < deadline:
    if not log_file.exists():
        last_error = f"{log_file} does not exist yet"
        time.sleep(1)
        continue

    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        last_error = f"Could not read {log_file}: {exc}"
        time.sleep(1)
        continue

    if not lines:
        last_error = f"{log_file} is empty"
        time.sleep(1)
        continue

    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON in {log_file}:{line_number}: {exc}", file=sys.stderr)
            sys.exit(1)

        if not isinstance(event, dict):
            print(f"JSON event in {log_file}:{line_number} is not an object", file=sys.stderr)
            sys.exit(1)

        missing = required_keys - set(event)
        if missing:
            print(f"Medpot log event is missing keys {sorted(missing)} in {log_file}:{line_number}", file=sys.stderr)
            sys.exit(1)

        if event.get("level") != "info" or event.get("message") != "Connection found":
            continue

        try:
            payload = decode_payload(event.get("data", ""))
        except ValueError as exc:
            print(f"{exc} in {log_file}:{line_number}", file=sys.stderr)
            sys.exit(1)

        if token.encode("ascii") not in payload:
            continue

        try:
            src_port = int(event.get("src_port", ""))
        except ValueError:
            print(f"Medpot src_port is not numeric in {log_file}:{line_number}: {event!r}", file=sys.stderr)
            sys.exit(1)

        if not 1 <= src_port <= 65535:
            print(f"Medpot src_port is out of range in {log_file}:{line_number}: {event!r}", file=sys.stderr)
            sys.exit(1)

        if not event.get("src_ip"):
            print(f"Medpot src_ip is empty in {log_file}:{line_number}: {event!r}", file=sys.stderr)
            sys.exit(1)

        print(f"Medpot JSON event found in {log_file}:{line_number}")
        sys.exit(0)

    last_error = f"No matching Medpot log event found in {log_file} for token {token}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local docker_log_file="${TEST_TMP_ROOT}/docker-logs.txt"

  test_compose logs --no-color > "${docker_log_file}" 2>/dev/null || true

  python3 - "${LOG_DIR}" "${docker_log_file}" <<'PY'
import re
import sys
from pathlib import Path

patterns = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bpanic(?:ked)?\b",
        r"fatal error",
        r"Error listening:",
        r"Error accepting:",
        r"Error reading:",
        r"permission denied",
        r"address already in use",
    )
]

paths = []
log_dir = Path(sys.argv[1])
docker_log_file = Path(sys.argv[2])

if log_dir.exists():
    paths.extend(path for path in log_dir.rglob("*") if path.is_file())
if docker_log_file.exists():
    paths.append(docker_log_file)

for path in paths:
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in patterns:
        if pattern.search(text):
            print(f"Runtime error pattern {pattern.pattern!r} found in {path}", file=sys.stderr)
            sys.exit(1)
PY
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

  if [[ -n "${HOST_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${HOST_PORT}" || test_die "${TEST_BIND_IP}:${HOST_PORT} is already in use. Try --host-port <free-port>."
  fi

  prepare_medpot_harness
  test_enable_cleanup

  test_info "Starting isolated Medpot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Medpot container did not stay running"
  test_ok "Container is running"

  MAPPED_PORT="$(test_get_mapped_port "${TEST_NAME}" "2575")" || test_die "Could not resolve mapped host port for 2575/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_PORT} maps to container port 2575/tcp"

  local token="medpot-test-$(date +%s)-$$"

  test_info "Running Medpot HL7 probe with token: ${token}"
  run_hl7_probe_with_retries "${token}" || test_die "Medpot HL7 probe failed on ${TEST_BIND_IP}:${MAPPED_PORT}"
  test_wait_for_container || test_die "Medpot container stopped after HL7 probe"

  test_info "Waiting for Medpot JSON log event"
  wait_for_medpot_log_event "${token}" || test_die "Expected Medpot event was not found in medpot.log"
  test_ok "Medpot connection event was written to medpot.log"

  assert_no_runtime_errors
  test_ok "No Medpot runtime errors found in logs"

  test_ok "Medpot post-build smoke test completed successfully"
}

main "$@"
