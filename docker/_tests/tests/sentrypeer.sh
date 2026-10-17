#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="sentrypeer"
DEFAULT_IMAGE="dtagdevsec/sentrypeer:24.04.2"
IMAGE=""
TCP_PORT=""
UDP_PORT=""
LOG_DIR=""
JSON_LOG_FILE=""
DB_FILE=""
MAPPED_TCP_PORT=""
MAPPED_UDP_PORT=""
PROCESS_BASELINE=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the SentryPeer image.

Options:
  --image IMAGE      Image to test. Defaults to docker/sentrypeer/docker-compose.yml.
  --tcp-port PORT    Host TCP port for SIP. Default: dynamic free port.
  --udp-port PORT    Host UDP port for SIP. Default: dynamic free port.
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
      --tcp-port)
        [[ $# -ge 2 ]] || test_die "--tcp-port requires an argument"
        TCP_PORT="$2"
        shift 2
        ;;
      --tcp-port=*)
        TCP_PORT="${1#*=}"
        shift
        ;;
      --udp-port)
        [[ $# -ge 2 ]] || test_die "--udp-port requires an argument"
        UDP_PORT="$2"
        shift 2
        ;;
      --udp-port=*)
        UDP_PORT="${1#*=}"
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
  if [[ -n "${TCP_PORT}" ]]; then
    test_validate_port "${TCP_PORT}"
  fi
  if [[ -n "${UDP_PORT}" ]]; then
    test_validate_port "${UDP_PORT}"
  fi
}

prepare_sentrypeer_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  JSON_LOG_FILE="${LOG_DIR}/sentrypeer.json"
  DB_FILE="${LOG_DIR}/sentrypeer.db"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  sentrypeer:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      SENTRYPEER_VERBOSE: "1"
      SENTRYPEER_DEBUG: "1"
    ports:
      - "${TEST_BIND_IP}:${TCP_PORT}:5060/tcp"
      - "${TEST_BIND_IP}:${UDP_PORT}:5060/udp"
    volumes:
      - "${LOG_DIR}:/var/log/sentrypeer"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

ensure_requested_ports_are_free() {
  if [[ -n "${TCP_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${TCP_PORT}" || test_die "${TEST_BIND_IP}:${TCP_PORT}/tcp is already in use. Try --tcp-port <free-port>."
  fi
  if [[ -n "${UDP_PORT}" ]]; then
    test_ensure_udp_port_free "${TEST_BIND_IP}" "${UDP_PORT}" || test_die "${TEST_BIND_IP}:${UDP_PORT}/udp is already in use. Try --udp-port <free-port>."
  fi
}

run_sip_probe() {
  local transport="$1"
  local port="$2"
  local token="$3"
  local packet_kind="${4:-options}"

  python3 - "${TEST_BIND_IP}" "${port}" "${transport}" "${token}" "${TEST_TIMEOUT}" "${packet_kind}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
transport = sys.argv[3]
token = sys.argv[4]
timeout = int(sys.argv[5])
packet_kind = sys.argv[6]
called_number = "1000"

start_line = f"OPTIONS sip:{called_number}@127.0.0.1 SIP/2.0\r\n"
if packet_kind == "response":
  start_line = "SIP/2.0 200 OK\r\n"

request = (
  start_line +
    f"Via: SIP/2.0/{transport} 127.0.0.1:50999;branch=z9hG4bK-{token}\r\n"
    "Max-Forwards: 70\r\n"
    f"From: \"Smoke\" <sip:smoke@127.0.0.1>;tag={token}\r\n"
    f"To: <sip:{called_number}@127.0.0.1>\r\n"
    f"Call-ID: {token}@127.0.0.1\r\n"
    "CSeq: 1 OPTIONS\r\n"
    "Contact: <sip:smoke@127.0.0.1:50999>\r\n"
    f"User-Agent: tpot-sentrypeer-smoke/{token}\r\n"
    "Content-Length: 0\r\n"
    "\r\n"
).encode("ascii")

if packet_kind == "null-method":
  request = b"SIP/2.0 100 x\r\n"
elif packet_kind not in ("options", "response"):
  raise RuntimeError(f"Unsupported packet kind: {packet_kind}")

def validate_response(response):
    if not response.startswith(b"SIP/2.0 "):
        raise RuntimeError(f"Expected SIP response, got {response[:120]!r}")
    status_line = response.splitlines()[0].decode("ascii", errors="replace")
    if "200 OK" not in status_line:
        raise RuntimeError(f"Expected SIP 200 OK, got {status_line!r}")
    print(f"{transport} SIP response: {status_line}")

try:
    if transport == "UDP":
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(min(timeout, 3))
            sock.sendto(request, (host, port))
            if packet_kind != "options":
              print(f"{transport} {packet_kind}: sent {len(request)} bytes")
              sys.exit(0)
            response, _ = sock.recvfrom(4096)
            validate_response(response)
    elif transport == "TCP":
        deadline = time.monotonic() + timeout
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(1)
            sock.sendall(request)
            if packet_kind != "options":
              sock.shutdown(socket.SHUT_WR)
              print(f"{transport} {packet_kind}: sent {len(request)} bytes")
              sys.exit(0)
            chunks = []
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\r\n\r\n" in b"".join(chunks):
                    break
            validate_response(b"".join(chunks))
    else:
        raise RuntimeError(f"Unsupported transport: {transport}")
except Exception as exc:
    print(f"{transport} SIP probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_probe_with_retries() {
  local description="$1"
  shift
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$("$@" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  printf '%s probe did not succeed before timeout\n' "${description}" >&2
  return 1
}

wait_for_json_event() {
  local transport="$1"
  local token="$2"
  local method="${3:-OPTIONS}"

  python3 - "${JSON_LOG_FILE}" "${transport}" "${token}" "${TEST_TIMEOUT}" "${method}" <<'PY'
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
transport = sys.argv[2]
token = sys.argv[3]
timeout = int(sys.argv[4])
method = sys.argv[5]
deadline = time.monotonic() + timeout
expected_user_agent = f"tpot-sentrypeer-smoke/{token}"
last_error = None

while time.monotonic() < deadline:
    if not path.exists():
        last_error = f"{path} does not exist yet"
        time.sleep(1)
        continue

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        last_error = f"Could not read {path}: {exc}"
        time.sleep(1)
        continue

    if not lines:
        last_error = f"{path} is empty"
        time.sleep(1)
        continue

    invalid = None
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            invalid = f"{path}:{line_number}: invalid JSON: {exc}"
            continue

        if not isinstance(event, dict):
            invalid = f"{path}:{line_number}: JSON event is not an object"
            continue

        if event.get("transport_type") != transport:
            continue
        if event.get("sip_user_agent") != expected_user_agent:
            continue

        required = {
            "app_name": "sentrypeer",
            "collected_method": "responsive",
            "called_number": "1000",
            "sip_method": method,
        }
        for field, expected in required.items():
            if event.get(field) != expected:
                print(
                    f"{path}:{line_number}: expected {field}={expected!r}, got {event.get(field)!r}",
                    file=sys.stderr,
                )
                sys.exit(1)

        source_ip = event.get("source_ip", "")
        destination_ip = event.get("destination_ip", "")
        sip_message = event.get("sip_message", "")
        event_timestamp = event.get("event_timestamp", "")
        event_uuid = event.get("event_uuid", "")

        if ":" not in source_ip:
            print(f"{path}:{line_number}: source_ip has no port: {source_ip!r}", file=sys.stderr)
            sys.exit(1)
        if not destination_ip.endswith(":5060"):
            print(f"{path}:{line_number}: destination_ip is unexpected: {destination_ip!r}", file=sys.stderr)
            sys.exit(1)
        if token not in sip_message:
            print(f"{path}:{line_number}: SIP message does not contain probe token", file=sys.stderr)
            sys.exit(1)
        if not event_timestamp or not event_uuid:
            print(f"{path}:{line_number}: timestamp or UUID is missing", file=sys.stderr)
            sys.exit(1)

        print(f"{transport} JSON event found in {path}:{line_number}")
        sys.exit(0)

    last_error = invalid or f"No {transport} JSON event for token {token} found in {path}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

container_process_state() {
  docker inspect -f '{{.State.Running}}|{{.State.Pid}}|{{.State.StartedAt}}|{{.RestartCount}}' "${TEST_CONTAINER_NAME}"
}

assert_process_unchanged() {
  local description="$1"
  local current=""
  current="$(container_process_state)" || test_die "Could not inspect SentryPeer ${description}"
  [[ "${current}" == "${PROCESS_BASELINE}" ]] || test_die "SentryPeer process changed ${description}: baseline=${PROCESS_BASELINE}, current=${current}"
}

run_response_regression() {
  local transport="$1"
  local port="$2"
  local packet_kind="$3"
  local token="sentrypeer-${transport}-${packet_kind}-$(date +%s)-$$"

  assert_process_unchanged "before ${transport} ${packet_kind}"
  test_info "Sending ${transport} ${packet_kind} regression input"
  run_sip_probe "${transport}" "${port}" "${token}" "${packet_kind}" || test_die "Could not send ${transport} ${packet_kind}"
  assert_process_unchanged "after sending ${transport} ${packet_kind}"
  if [[ "${packet_kind}" == "response" ]]; then
    wait_for_json_event "${transport}" "${token}" "NOT_FOUND" || test_die "${transport} response event with sip_method=NOT_FOUND was not found"
    assert_process_unchanged "after logging ${transport} ${packet_kind}"
  fi

  run_sip_probe "${transport}" "${port}" "${token}-after" || test_die "Fresh ${transport} OPTIONS failed after ${packet_kind}"
  wait_for_json_event "${transport}" "${token}-after" || test_die "Fresh ${transport} OPTIONS JSON was not found after ${packet_kind}"
  assert_process_unchanged "after fresh ${transport} OPTIONS following ${packet_kind}"
  assert_no_runtime_errors
  test_ok "${transport} ${packet_kind}: same running process and fresh OPTIONS JSON"
}

assert_no_runtime_errors() {
  if test_compose logs --no-color 2>/dev/null | grep -Ei "panic|segmentation fault|Traceback|Exception|Assertion .*failed|assertion failed:|(^|[|[:space:]])Aborted([[:space:](]|$)|SIGABRT" >/dev/null 2>&1; then
    test_die "SentryPeer runtime error found in Docker logs"
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
  ensure_requested_ports_are_free

  prepare_sentrypeer_harness
  test_enable_cleanup

  test_info "Starting isolated SentryPeer container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "SentryPeer container did not stay running"
  PROCESS_BASELINE="$(container_process_state)" || test_die "Could not capture SentryPeer process baseline"
  [[ "${PROCESS_BASELINE}" =~ ^true\|[1-9][0-9]*\|[^\|]+\|0$ ]] || test_die "Unexpected SentryPeer process baseline: ${PROCESS_BASELINE}"
  test_ok "Container is running"

  MAPPED_TCP_PORT="$(test_get_mapped_port "${TEST_NAME}" "5060/tcp")" || test_die "Could not resolve mapped host port for 5060/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_TCP_PORT} maps to container port 5060/tcp"

  MAPPED_UDP_PORT="$(test_get_mapped_port "${TEST_NAME}" "5060/udp")" || test_die "Could not resolve mapped host port for 5060/udp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_UDP_PORT} maps to container port 5060/udp"

  local udp_token="sentrypeer-udp-$(date +%s)-$$"
  local tcp_token="sentrypeer-tcp-$(date +%s)-$$"

  test_info "Running UDP SIP OPTIONS probe with token: ${udp_token}"
  run_probe_with_retries "UDP SIP OPTIONS" run_sip_probe "UDP" "${MAPPED_UDP_PORT}" "${udp_token}" || test_die "UDP SIP probe failed on ${TEST_BIND_IP}:${MAPPED_UDP_PORT}"
  wait_for_json_event "UDP" "${udp_token}" || test_die "UDP SIP event was not found in sentrypeer.json"
  assert_process_unchanged "after UDP probe"

  test_info "Running TCP SIP OPTIONS probe with token: ${tcp_token}"
  run_probe_with_retries "TCP SIP OPTIONS" run_sip_probe "TCP" "${MAPPED_TCP_PORT}" "${tcp_token}" || test_die "TCP SIP probe failed on ${TEST_BIND_IP}:${MAPPED_TCP_PORT}"
  wait_for_json_event "TCP" "${tcp_token}" || test_die "TCP SIP event was not found in sentrypeer.json"
  assert_process_unchanged "after TCP probe"
  test_ok "SIP probes were written to sentrypeer.json"

  run_response_regression "UDP" "${MAPPED_UDP_PORT}" "null-method"
  run_response_regression "UDP" "${MAPPED_UDP_PORT}" "response"
  run_response_regression "TCP" "${MAPPED_TCP_PORT}" "null-method"
  run_response_regression "TCP" "${MAPPED_TCP_PORT}" "response"

  [[ -s "${DB_FILE}" ]] || test_die "SentryPeer database was not created at ${DB_FILE}"
  test_ok "SentryPeer database was created"

  assert_no_runtime_errors
  test_ok "No SentryPeer runtime errors found in Docker logs"

  test_ok "SentryPeer post-build smoke test completed successfully"
}

main "$@"
