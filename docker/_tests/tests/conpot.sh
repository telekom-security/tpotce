#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="conpot"
DEFAULT_IMAGE="dtagdevsec/conpot:24.04.1"
IMAGE=""
IEC104_PORT="2404"
GUARDIAN_AST_PORT="10001"
IPMI_PORT="623"
KAMSTRUP_PORT="1025"
KAMSTRUP_MANAGEMENT_PORT="50100"
LOG_DIR=""

CONPOT_CONTAINER_IEC104=""
CONPOT_CONTAINER_GUARDIAN_AST=""
CONPOT_CONTAINER_IPMI=""
CONPOT_CONTAINER_KAMSTRUP=""
CONPOT_CONTAINER_NAMES=()

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Conpot image.

Options:
  --image IMAGE                     Image to test. Defaults to docker/conpot/docker-compose.yml.
  --iec104-port PORT                Host TCP port for IEC104. Default: 2404.
  --guardian-ast-port PORT          Host TCP port for Guardian AST. Default: 10001.
  --ipmi-port PORT                  Host UDP port for IPMI. Default: 623.
  --kamstrup-port PORT              Host TCP port for Kamstrup meter. Default: 1025.
  --kamstrup-management-port PORT   Host TCP port for Kamstrup management. Default: 50100.
  --timeout SEC                     Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP                      Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts                  Keep temporary compose file and logs for debugging.
  -h, --help                        Show this help message.
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
      --iec104-port)
        [[ $# -ge 2 ]] || test_die "--iec104-port requires an argument"
        IEC104_PORT="$2"
        shift 2
        ;;
      --iec104-port=*)
        IEC104_PORT="${1#*=}"
        shift
        ;;
      --guardian-ast-port)
        [[ $# -ge 2 ]] || test_die "--guardian-ast-port requires an argument"
        GUARDIAN_AST_PORT="$2"
        shift 2
        ;;
      --guardian-ast-port=*)
        GUARDIAN_AST_PORT="${1#*=}"
        shift
        ;;
      --ipmi-port)
        [[ $# -ge 2 ]] || test_die "--ipmi-port requires an argument"
        IPMI_PORT="$2"
        shift 2
        ;;
      --ipmi-port=*)
        IPMI_PORT="${1#*=}"
        shift
        ;;
      --kamstrup-port)
        [[ $# -ge 2 ]] || test_die "--kamstrup-port requires an argument"
        KAMSTRUP_PORT="$2"
        shift 2
        ;;
      --kamstrup-port=*)
        KAMSTRUP_PORT="${1#*=}"
        shift
        ;;
      --kamstrup-management-port)
        [[ $# -ge 2 ]] || test_die "--kamstrup-management-port requires an argument"
        KAMSTRUP_MANAGEMENT_PORT="$2"
        shift 2
        ;;
      --kamstrup-management-port=*)
        KAMSTRUP_MANAGEMENT_PORT="${1#*=}"
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
  test_validate_port "${IEC104_PORT}"
  test_validate_port "${GUARDIAN_AST_PORT}"
  test_validate_port "${IPMI_PORT}"
  test_validate_port "${KAMSTRUP_PORT}"
  test_validate_port "${KAMSTRUP_MANAGEMENT_PORT}"
}

ensure_tcp_port_free_for_docker() {
  local port="$1"
  local option="$2"

  if (( port < 1024 )); then
    test_info "Skipping user-space preflight for privileged TCP port ${port}; Docker will validate the binding."
  else
    test_ensure_port_free "${TEST_BIND_IP}" "${port}" || test_die "${TEST_BIND_IP}:${port} is already in use. Try ${option} <free-port>."
  fi
}

ensure_udp_port_free_for_docker() {
  local port="$1"
  local option="$2"

  if (( port < 1024 )); then
    test_info "Skipping user-space preflight for privileged UDP port ${port}; Docker will validate the binding."
  else
    test_ensure_udp_port_free "${TEST_BIND_IP}" "${port}" || test_die "${TEST_BIND_IP}:${port}/udp is already in use. Try ${option} <free-port>."
  fi
}

prepare_conpot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  CONPOT_CONTAINER_IEC104="${TEST_PROJECT_NAME}-iec104"
  CONPOT_CONTAINER_GUARDIAN_AST="${TEST_PROJECT_NAME}-guardian-ast"
  CONPOT_CONTAINER_IPMI="${TEST_PROJECT_NAME}-ipmi"
  CONPOT_CONTAINER_KAMSTRUP="${TEST_PROJECT_NAME}-kamstrup-382"
  CONPOT_CONTAINER_NAMES=(
    "${CONPOT_CONTAINER_IEC104}"
    "${CONPOT_CONTAINER_GUARDIAN_AST}"
    "${CONPOT_CONTAINER_IPMI}"
    "${CONPOT_CONTAINER_KAMSTRUP}"
  )

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
networks:
  conpot_local_IEC104:
  conpot_local_guardian_ast:
  conpot_local_ipmi:
  conpot_local_kamstrup_382:

services:
  conpot_IEC104:
    image: "${IMAGE}"
    container_name: "${CONPOT_CONTAINER_IEC104}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      - CONPOT_CONFIG=/etc/conpot/conpot.cfg
      - CONPOT_JSON_LOG=/var/log/conpot/conpot_IEC104.json
      - CONPOT_LOG=/var/log/conpot/conpot_IEC104.log
      - CONPOT_TEMPLATE=IEC104
      - CONPOT_TMP=/tmp/conpot
    tmpfs:
      - /tmp/conpot:uid=2000,gid=2000
    networks:
      - conpot_local_IEC104
    ports:
      - "${TEST_BIND_IP}:${IEC104_PORT}:2404"
    volumes:
      - "${LOG_DIR}:/var/log/conpot"

  conpot_guardian_ast:
    image: "${IMAGE}"
    container_name: "${CONPOT_CONTAINER_GUARDIAN_AST}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      - CONPOT_CONFIG=/etc/conpot/conpot.cfg
      - CONPOT_JSON_LOG=/var/log/conpot/conpot_guardian_ast.json
      - CONPOT_LOG=/var/log/conpot/conpot_guardian_ast.log
      - CONPOT_TEMPLATE=guardian_ast
      - CONPOT_TMP=/tmp/conpot
    tmpfs:
      - /tmp/conpot:uid=2000,gid=2000
    networks:
      - conpot_local_guardian_ast
    ports:
      - "${TEST_BIND_IP}:${GUARDIAN_AST_PORT}:10001"
    volumes:
      - "${LOG_DIR}:/var/log/conpot"

  conpot_ipmi:
    image: "${IMAGE}"
    container_name: "${CONPOT_CONTAINER_IPMI}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      - CONPOT_CONFIG=/etc/conpot/conpot.cfg
      - CONPOT_JSON_LOG=/var/log/conpot/conpot_ipmi.json
      - CONPOT_LOG=/var/log/conpot/conpot_ipmi.log
      - CONPOT_TEMPLATE=ipmi
      - CONPOT_TMP=/tmp/conpot
    tmpfs:
      - /tmp/conpot:uid=2000,gid=2000
    networks:
      - conpot_local_ipmi
    ports:
      - "${TEST_BIND_IP}:${IPMI_PORT}:623/udp"
    volumes:
      - "${LOG_DIR}:/var/log/conpot"

  conpot_kamstrup_382:
    image: "${IMAGE}"
    container_name: "${CONPOT_CONTAINER_KAMSTRUP}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      - CONPOT_CONFIG=/etc/conpot/conpot.cfg
      - CONPOT_JSON_LOG=/var/log/conpot/conpot_kamstrup_382.json
      - CONPOT_LOG=/var/log/conpot/conpot_kamstrup_382.log
      - CONPOT_TEMPLATE=kamstrup_382
      - CONPOT_TMP=/tmp/conpot
    tmpfs:
      - /tmp/conpot:uid=2000,gid=2000
    networks:
      - conpot_local_kamstrup_382
    ports:
      - "${TEST_BIND_IP}:${KAMSTRUP_PORT}:1025"
      - "${TEST_BIND_IP}:${KAMSTRUP_MANAGEMENT_PORT}:50100"
    volumes:
      - "${LOG_DIR}:/var/log/conpot"
EOF
}

test_show_diagnostics() {
  local container=""
  local file=""

  printf '\n[diagnostics] Container states\n' >&2
  if [[ ${#CONPOT_CONTAINER_NAMES[@]} -gt 0 ]]; then
    for container in "${CONPOT_CONTAINER_NAMES[@]}"; do
      printf '%s: ' "${container}" >&2
      docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} error={{.State.Error}}' "${container}" >&2 || true
    done
  else
    printf 'No Conpot container names available.\n' >&2
  fi

  printf '\n[diagnostics] Docker logs\n' >&2
  if [[ -n "${TEST_HARNESS_COMPOSE}" && -f "${TEST_HARNESS_COMPOSE}" ]]; then
    test_compose logs --no-color --tail=160 >&2 || true
  else
    printf 'No temporary compose file available.\n' >&2
  fi

  printf '\n[diagnostics] Test log artifacts\n' >&2
  if [[ -n "${LOG_DIR}" && -d "${LOG_DIR}" ]]; then
    find "${LOG_DIR}" -maxdepth 1 -type f -print | sort >&2 || true
    while IFS= read -r file; do
      printf '\n--- %s ---\n' "${file}" >&2
      tail -n 120 "${file}" >&2 || true
    done < <(find "${LOG_DIR}" -maxdepth 1 -type f -print | sort)
  else
    printf 'No temporary log directory available.\n' >&2
  fi
}

wait_for_containers() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local container=""
  local state=""
  local all_running=""

  while (( SECONDS < deadline )); do
    all_running="true"
    for container in "${CONPOT_CONTAINER_NAMES[@]}"; do
      state="$(docker inspect -f '{{.State.Status}}' "${container}" 2>/dev/null || true)"
      case "${state}" in
        running)
          ;;
        exited|dead)
          return 1
          ;;
        *)
          all_running="false"
          ;;
      esac
    done
    [[ "${all_running}" == "true" ]] && return 0
    sleep 1
  done

  return 1
}

assert_containers_running() {
  local container=""
  local state=""

  for container in "${CONPOT_CONTAINER_NAMES[@]}"; do
    state="$(docker inspect -f '{{.State.Status}}' "${container}" 2>/dev/null || true)"
    [[ "${state}" == "running" ]] || test_die "${container} is not running; state=${state:-unknown}"
  done
}

wait_for_log_line() {
  local log_file="$1"
  local pattern="$2"
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if [[ -f "${log_file}" ]] && grep -F -- "${pattern}" "${log_file}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  return 1
}

wait_for_json_event() {
  local json_file="$1"
  local mode="$2"

  python3 - "${json_file}" "${mode}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
mode = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None


def matches(event):
    data_type = str(event.get("data_type", ""))
    event_type = str(event.get("event_type", ""))
    request = str(event.get("request", ""))
    response = str(event.get("response", ""))
    dst_port = str(event.get("dst_port", ""))

    if mode == "iec104":
        return data_type == "IEC104"
    if mode == "guardian_ast":
        return data_type == "guardian_ast" and (
            event_type == "AST I20100" or "I20100" in request or "I20100" in response
        )
    if mode == "ipmi":
        return data_type == "ipmi" and event_type == "GET_CHANNEL_AUTH_CAPABILITIES" and response not in ("", "None")
    if mode == "kamstrup_meter":
        return data_type == "kamstrup_protocol" and dst_port == "1025"
    if mode == "kamstrup_management":
        return data_type == "kamstrup_management_protocol" and dst_port == "50100"
    raise RuntimeError(f"Unknown JSON event mode: {mode}")


while time.monotonic() < deadline:
    if not path.exists():
        last_error = f"{path} does not exist yet"
        time.sleep(1)
        continue

    raw = path.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        last_error = f"{path} is empty"
        time.sleep(1)
        continue

    found = False
    invalid = None
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            invalid = f"{path}:{line_number}: invalid JSON: {exc}"
            continue
        if matches(event):
            found = True

    if invalid is None and found:
        print(f"JSON event found in {path}: {mode}")
        sys.exit(0)

    last_error = invalid or f"No matching {mode} JSON event found in {path}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

run_iec104_probe() {
  python3 - "${TEST_BIND_IP}" "${IEC104_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])
payload = bytes.fromhex("68 04 07 00 00 00")

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(2)
        sock.sendall(payload)
        try:
            response = sock.recv(64)
        except socket.timeout:
            response = b""
    print(f"IEC104 probe sent {len(payload)} bytes; received {len(response)} bytes")
except Exception as exc:
    print(f"IEC104 probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_guardian_ast_probe() {
  python3 - "${TEST_BIND_IP}" "${GUARDIAN_AST_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
payload = b"\x01I20100\n"

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(1)
        sock.sendall(payload)
        chunks = []
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            chunks.append(chunk)
            if b"I20100" in b"".join(chunks):
                break
    response = b"".join(chunks)
    if b"I20100" not in response and b"IN-TANK" not in response:
        raise RuntimeError(f"Expected Guardian AST inventory response, got {response[:120]!r}")
    print(f"Guardian AST response: {response[:80]!r}")
except Exception as exc:
    print(f"Guardian AST probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_ipmi_probe() {
  python3 - "${TEST_BIND_IP}" "${IPMI_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])
# RMCP/IPMI v1.5 Get Channel Authentication Capabilities.
payload = bytes.fromhex(
    "06 00 ff 07"
    " 00 00 00 00 00 00 00 00 00 09"
    " 20 18 c8 81 00 38 8e 04 b5"
)

try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(min(timeout, 3))
        sock.sendto(payload, (host, port))
        try:
            response, _ = sock.recvfrom(4096)
        except socket.timeout:
            response = b""
    if not response:
        raise RuntimeError("Expected RMCP/IPMI response, got no UDP response")
    if not response.startswith(bytes.fromhex("06 00 ff 07")):
        raise RuntimeError(f"Expected RMCP response, got {response[:16]!r}")
    if b"\x38" not in response:
        raise RuntimeError(f"Expected IPMI command 0x38 response, got {response[:32]!r}")
    print(f"IPMI probe sent {len(payload)} bytes; received {len(response)} bytes")
except Exception as exc:
    print(f"IPMI probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_kamstrup_meter_probe() {
  python3 - "${TEST_BIND_IP}" "${KAMSTRUP_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])

try:
    with socket.create_connection((host, port), timeout=timeout):
        pass
    print("Kamstrup meter TCP connection accepted")
except Exception as exc:
    print(f"Kamstrup meter probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_kamstrup_management_probe() {
  python3 - "${TEST_BIND_IP}" "${KAMSTRUP_MANAGEMENT_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(3)
        banner = sock.recv(1024)
        if b"Welcome" not in banner:
            raise RuntimeError(f"Expected Kamstrup management banner, got {banner[:120]!r}")
        sock.sendall(b"help\r\n")
        try:
            response = sock.recv(1024)
        except socket.timeout:
            response = b""
    print(f"Kamstrup management banner: {banner[:80]!r}; response bytes={len(response)}")
except Exception as exc:
    print(f"Kamstrup management probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

assert_no_runtime_errors() {
  if grep -R -E "Traceback|NameError|Exception" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Conpot runtime error found in log files"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "Traceback|NameError|Exception" >/dev/null 2>&1; then
    test_die "Conpot runtime error found in Docker logs"
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
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build conpot_default"

  ensure_tcp_port_free_for_docker "${IEC104_PORT}" "--iec104-port"
  ensure_tcp_port_free_for_docker "${GUARDIAN_AST_PORT}" "--guardian-ast-port"
  ensure_udp_port_free_for_docker "${IPMI_PORT}" "--ipmi-port"
  ensure_tcp_port_free_for_docker "${KAMSTRUP_PORT}" "--kamstrup-port"
  ensure_tcp_port_free_for_docker "${KAMSTRUP_MANAGEMENT_PORT}" "--kamstrup-management-port"

  prepare_conpot_harness
  test_enable_cleanup

  test_info "Starting isolated Conpot containers"
  test_compose up -d --no-build >/dev/null

  wait_for_containers || test_die "One or more Conpot containers did not stay running"
  test_ok "All Conpot containers are running"

  test_info "Waiting for Conpot listener entries"
  wait_for_log_line "${LOG_DIR}/conpot_IEC104.log" "IEC 60870-5-104 protocol server started on" || test_die "IEC104 listener entry was not found"
  wait_for_log_line "${LOG_DIR}/conpot_guardian_ast.log" "GuardianAST server started on" || test_die "Guardian AST listener entry was not found"
  wait_for_log_line "${LOG_DIR}/conpot_ipmi.log" "IPMI server started on" || test_die "IPMI listener entry was not found"
  wait_for_log_line "${LOG_DIR}/conpot_kamstrup_382.log" "Kamstrup protocol server started on" || test_die "Kamstrup meter listener entry was not found"
  wait_for_log_line "${LOG_DIR}/conpot_kamstrup_382.log" "Kamstrup management protocol server started on" || test_die "Kamstrup management listener entry was not found"
  test_ok "All Conpot listener entries were found"

  assert_containers_running

  test_info "Running IEC104 probe"
  run_iec104_probe

  test_info "Running Guardian AST probe"
  run_guardian_ast_probe

  test_info "Running IPMI probe"
  run_ipmi_probe

  test_info "Running Kamstrup meter probe"
  run_kamstrup_meter_probe

  test_info "Running Kamstrup management probe"
  run_kamstrup_management_probe

  assert_containers_running
  assert_no_runtime_errors
  test_ok "No Conpot runtime errors found in logs"

  test_info "Validating Conpot JSON events"
  wait_for_json_event "${LOG_DIR}/conpot_IEC104.json" "iec104" || test_die "IEC104 JSON event was not found"
  test_ok "IEC104 JSON event found"

  wait_for_json_event "${LOG_DIR}/conpot_guardian_ast.json" "guardian_ast" || test_die "Guardian AST JSON event was not found"
  test_ok "Guardian AST JSON event found"

  wait_for_json_event "${LOG_DIR}/conpot_kamstrup_382.json" "kamstrup_meter" || test_die "Kamstrup meter JSON event was not found"
  test_ok "Kamstrup meter JSON event found"

  wait_for_json_event "${LOG_DIR}/conpot_kamstrup_382.json" "kamstrup_management" || test_die "Kamstrup management JSON event was not found"
  test_ok "Kamstrup management JSON event found"

  wait_for_log_line "${LOG_DIR}/conpot_ipmi.log" "Connection established with" || test_die "IPMI connection establishment was not found in conpot_ipmi.log"
  wait_for_log_line "${LOG_DIR}/conpot_ipmi.log" "IPMI response sent to" || test_die "IPMI response was not found in conpot_ipmi.log"
  test_ok "IPMI protocol exchange was written to conpot_ipmi.log"

  wait_for_json_event "${LOG_DIR}/conpot_ipmi.json" "ipmi" || test_die "IPMI JSON event was not found"
  test_ok "IPMI JSON event found"

  test_ok "Conpot post-build smoke test completed successfully"
}

main "$@"
