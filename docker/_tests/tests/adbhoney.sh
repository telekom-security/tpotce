#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="adbhoney"
DEFAULT_IMAGE="dtagdevsec/adbhoney:24.04.1"
IMAGE=""
HOST_PORT=""
LOG_DIR=""
DL_DIR=""
TEXT_LOG_FILE=""
JSON_LOG_FILE=""
MAPPED_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the ADBHoney image.

Options:
  --image IMAGE       Image to test. Defaults to docker/adbhoney/docker-compose.yml.
  --host-port PORT    Host port to bind. Default: dynamic free port.
  --timeout SEC       Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP        Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts    Keep temporary compose file and logs for debugging.
  -h, --help          Show this help message.
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
      --host-port|--port)
        [[ $# -ge 2 ]] || test_die "$1 requires an argument"
        HOST_PORT="$2"
        shift 2
        ;;
      --host-port=*|--port=*)
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

prepare_adbhoney_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  DL_DIR="${TEST_TMP_ROOT}/downloads"
  TEXT_LOG_FILE="${LOG_DIR}/adbhoney.log"
  JSON_LOG_FILE="${LOG_DIR}/adbhoney.json"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${DL_DIR}"
  chmod 0777 "${LOG_DIR}" "${DL_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  adbhoney:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${TEST_BIND_IP}:${HOST_PORT}:5555"
    volumes:
      - "${LOG_DIR}:/opt/adbhoney/log"
      - "${DL_DIR}:/opt/adbhoney/dl"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_adbhoney_start_log() {
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if [[ -f "${TEXT_LOG_FILE}" ]] && grep -F -- "Listening on 0.0.0.0:5555." "${TEXT_LOG_FILE}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  return 1
}

run_adb_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import struct
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])

command_ids = {
    name: struct.unpack("<I", name.encode("ascii"))[0]
    for name in ("SYNC", "CNXN", "AUTH", "OPEN", "OKAY", "CLSE", "WRTE")
}
command_names = {value: name for name, value in command_ids.items()}


class ProbeError(Exception):
    pass


def packet(command, arg0=0, arg1=0, payload=b""):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    command_id = command_ids[command]
    checksum = sum(payload) & 0xFFFFFFFF
    magic = command_id ^ 0xFFFFFFFF
    header = struct.pack("<6I", command_id, arg0, arg1, len(payload), checksum, magic)
    return header + payload


def recv_exact(sock, size, deadline):
    chunks = []
    remaining = size
    while remaining > 0:
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            raise ProbeError(f"Timed out while waiting for {size} bytes")
        sock.settimeout(min(remaining_time, 1.0))
        try:
            chunk = sock.recv(remaining)
        except socket.timeout:
            continue
        if not chunk:
            raise ProbeError("Connection closed by peer")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_packet(sock, deadline):
    header = recv_exact(sock, 24, deadline)
    command_id, arg0, arg1, length, checksum, magic = struct.unpack("<6I", header)

    if magic != (command_id ^ 0xFFFFFFFF):
        raise ProbeError(f"Invalid ADB magic for command id {command_id:#x}")
    if length > 1024 * 1024:
        raise ProbeError(f"Refusing oversized ADB payload: {length} bytes")

    payload = recv_exact(sock, length, deadline) if length else b""
    actual_checksum = sum(payload) & 0xFFFFFFFF
    if actual_checksum != checksum:
        raise ProbeError(
            f"Invalid ADB checksum for {command_names.get(command_id, hex(command_id))}: "
            f"expected {checksum:#x}, got {actual_checksum:#x}"
        )

    return command_names.get(command_id, hex(command_id)), arg0, arg1, payload


deadline = time.monotonic() + timeout
client_id = 1
banner = None
remote_id = None

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(packet("CNXN", 0x01000000, 4096, b"host::adbhoney-test\0"))

        while time.monotonic() < deadline:
            command, arg0, arg1, payload = recv_packet(sock, deadline)
            if command == "CNXN":
                banner = payload.decode("utf-8", errors="replace").rstrip("\0")
                break
            if command == "AUTH":
                raise ProbeError("ADBHoney requested AUTH; expected unauthenticated CNXN")

        if not banner:
            raise ProbeError("No CNXN response received")

        destination = f"shell:echo {token}\0".encode("utf-8")
        sock.sendall(packet("OPEN", client_id, 0, destination))

        while time.monotonic() < deadline:
            command, arg0, arg1, payload = recv_packet(sock, deadline)
            if command == "OKAY" and arg1 == client_id:
                remote_id = arg0
                break
            if command == "WRTE" and arg1 == client_id:
                remote_id = arg0
                break
            if command == "CLSE" and arg1 == client_id:
                raise ProbeError("ADBHoney closed the shell stream before accepting OPEN")

        if remote_id is None:
            raise ProbeError("No OKAY/WRTE response received for shell OPEN")

    print(f"ADB CNXN banner: {banner}")
    print(f"ADB shell OPEN accepted with remote id: {remote_id}")
except (OSError, ProbeError) as exc:
    print(f"ADB probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_adb_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_adb_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

wait_for_json_command_event() {
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
expected_input = f"echo {token}"
last_error = None

while time.monotonic() < deadline:
    if json_log.exists():
        try:
            lines = json_log.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            last_error = f"Could not read {json_log}: {exc}"
            time.sleep(1)
            continue

        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                last_error = f"Invalid JSON in {json_log}:{line_number}: {exc}"
                continue

            if (
                event.get("eventid") == "adbhoney.command.input"
                and event.get("input") == expected_input
                and token in event.get("input", "")
            ):
                print(f"JSON event found: {event['eventid']} input={event['input']}")
                sys.exit(0)
    else:
        last_error = f"{json_log} does not exist yet"

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
print(f"No adbhoney.command.input event found in {json_log} for token {token}", file=sys.stderr)
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
    test_ensure_port_free "${TEST_BIND_IP}" "${HOST_PORT}" || test_die "${TEST_BIND_IP}:${HOST_PORT} is already in use"
  fi

  prepare_adbhoney_harness
  test_enable_cleanup

  test_info "Starting isolated ADBHoney container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "ADBHoney container did not stay running"
  test_ok "Container is running"

  test_info "Waiting for ADBHoney listener entry in adbhoney.log"
  wait_for_adbhoney_start_log || test_die "ADBHoney listener entry was not found in adbhoney.log"
  test_ok "ADBHoney listener entry found in adbhoney.log"

  MAPPED_PORT="$(test_get_mapped_port "${TEST_NAME}" "5555")" || test_die "Could not resolve mapped host port for 5555/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_PORT} maps to container port 5555/tcp"

  local token="adbhoney-test-$(date +%s)-$$"
  test_info "Running ADB protocol probe with token: ${token}"
  run_adb_probe_with_retries "${token}" || test_die "ADB protocol probe failed on ${TEST_BIND_IP}:${MAPPED_PORT}"
  test_ok "ADB protocol probe succeeded"

  test_info "Waiting for command event in adbhoney.json"
  wait_for_json_command_event "${token}" || test_die "Command event was not found in adbhoney.json"
  test_ok "Command event was written to adbhoney.json"

  test_ok "ADBHoney post-build smoke test completed successfully"
}

main "$@"
