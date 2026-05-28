#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="ddospot"
DEFAULT_IMAGE="dtagdevsec/ddospot:24.04.1"
IMAGE=""
CHARGEN_PORT=""
DNS_PORT=""
NTP_PORT=""
SSDP_PORT=""
LOG_DIR=""
BL_DIR=""
DB_DIR=""
CHARGEN_LOG_FILE=""
DNS_LOG_FILE=""
NTP_LOG_FILE=""
SSDP_LOG_FILE=""
MAPPED_CHARGEN_PORT=""
MAPPED_DNS_PORT=""
MAPPED_NTP_PORT=""
MAPPED_SSDP_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the DDoSPot image.

Options:
  --image IMAGE          Image to test. Defaults to docker/ddospot/docker-compose.yml.
  --chargen-port PORT    Host UDP port for CHARGEN. Default: dynamic free port.
  --dns-port PORT        Host UDP port for DNS. Default: dynamic free port.
  --ntp-port PORT        Host UDP port for NTP. Default: dynamic free port.
  --ssdp-port PORT       Host UDP port for SSDP. Default: dynamic free port.
  --timeout SEC          Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP           Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts       Keep temporary compose file and logs for debugging.
  -h, --help             Show this help message.
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
      --chargen-port)
        [[ $# -ge 2 ]] || test_die "--chargen-port requires an argument"
        CHARGEN_PORT="$2"
        shift 2
        ;;
      --chargen-port=*)
        CHARGEN_PORT="${1#*=}"
        shift
        ;;
      --dns-port)
        [[ $# -ge 2 ]] || test_die "--dns-port requires an argument"
        DNS_PORT="$2"
        shift 2
        ;;
      --dns-port=*)
        DNS_PORT="${1#*=}"
        shift
        ;;
      --ntp-port)
        [[ $# -ge 2 ]] || test_die "--ntp-port requires an argument"
        NTP_PORT="$2"
        shift 2
        ;;
      --ntp-port=*)
        NTP_PORT="${1#*=}"
        shift
        ;;
      --ssdp-port)
        [[ $# -ge 2 ]] || test_die "--ssdp-port requires an argument"
        SSDP_PORT="$2"
        shift 2
        ;;
      --ssdp-port=*)
        SSDP_PORT="${1#*=}"
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
  if [[ -n "${CHARGEN_PORT}" ]]; then
    test_validate_port "${CHARGEN_PORT}"
  fi
  if [[ -n "${DNS_PORT}" ]]; then
    test_validate_port "${DNS_PORT}"
  fi
  if [[ -n "${NTP_PORT}" ]]; then
    test_validate_port "${NTP_PORT}"
  fi
  if [[ -n "${SSDP_PORT}" ]]; then
    test_validate_port "${SSDP_PORT}"
  fi
}

ensure_udp_port_free_for_docker() {
  local port="$1"
  local option="$2"

  if [[ -z "${port}" ]]; then
    return 0
  fi

  if (( port < 1024 )); then
    test_info "Skipping user-space preflight for privileged UDP port ${port}; Docker will validate the binding."
  else
    test_ensure_udp_port_free "${TEST_BIND_IP}" "${port}" || test_die "${TEST_BIND_IP}:${port}/udp is already in use. Try ${option} <free-port>."
  fi
}

prepare_ddospot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  BL_DIR="${TEST_TMP_ROOT}/bl"
  DB_DIR="${TEST_TMP_ROOT}/db"
  CHARGEN_LOG_FILE="${LOG_DIR}/chargenpot.log"
  DNS_LOG_FILE="${LOG_DIR}/dnspot.log"
  NTP_LOG_FILE="${LOG_DIR}/ntpot.log"
  SSDP_LOG_FILE="${LOG_DIR}/ssdpot.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${BL_DIR}" "${DB_DIR}"
  chmod 0777 "${LOG_DIR}" "${BL_DIR}" "${DB_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  ddospot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${TEST_BIND_IP}:${CHARGEN_PORT}:19/udp"
      - "${TEST_BIND_IP}:${DNS_PORT}:53/udp"
      - "${TEST_BIND_IP}:${NTP_PORT}:123/udp"
      - "${TEST_BIND_IP}:${SSDP_PORT}:1900/udp"
    volumes:
      - "${LOG_DIR}:/opt/ddospot/ddospot/logs"
      - "${BL_DIR}:/opt/ddospot/ddospot/bl"
      - "${DB_DIR}:/opt/ddospot/ddospot/db"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_chargen_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_CHARGEN_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
payload = f"tpot-ddospot-chargen-{token}\n".encode("ascii")

try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(min(timeout, 3))
        sock.sendto(payload, (host, port))
        try:
            response, _ = sock.recvfrom(4096)
        except socket.timeout:
            response = b""

    if not response:
        raise RuntimeError("Expected CHARGEN response, got no UDP response")

    print(f"CHARGEN probe sent {len(payload)} bytes; received {len(response)} bytes")
except Exception as exc:
    print(f"CHARGEN probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_dns_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_DNS_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import random
import socket
import struct
import sys

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
transaction_id = random.randrange(0, 65536)

question = b"".join(
    bytes([len(label)]) + label
    for label in (b"version", b"bind")
) + b"\x00"
query = (
    struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
    + question
    + struct.pack("!HH", 16, 3)
)

try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(min(timeout, 3))
        sock.sendto(query, (host, port))
        try:
            response, _ = sock.recvfrom(4096)
        except socket.timeout:
            response = b""

    if len(response) < 12:
        raise RuntimeError(f"Expected DNS response, got {len(response)} bytes")

    response_id, flags, qdcount, ancount, _, _ = struct.unpack("!HHHHHH", response[:12])
    if response_id != transaction_id:
        raise RuntimeError(f"Expected transaction id {transaction_id:#x}, got {response_id:#x}")
    if not flags & 0x8000:
        raise RuntimeError(f"Expected DNS response flag, got flags {flags:#x}")
    if qdcount < 1 and ancount < 1:
        raise RuntimeError("Expected DNS question or answer records in response")

    print(f"DNS CHAOS/TXT probe {token}: received {len(response)} bytes")
except Exception as exc:
    print(f"DNS probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_ntp_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_NTP_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])
payload = b"\x1b" + (b"\x00" * 47)

try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(min(timeout, 3))
        sock.sendto(payload, (host, port))
        try:
            response, _ = sock.recvfrom(4096)
        except socket.timeout:
            response = b""

    if len(response) < 48:
        raise RuntimeError(f"Expected NTP response of at least 48 bytes, got {len(response)} bytes")

    mode = response[0] & 0x07
    version = (response[0] >> 3) & 0x07
    if mode != 4:
        raise RuntimeError(f"Expected NTP server mode 4, got mode {mode}")
    if version < 1:
        raise RuntimeError(f"Expected sane NTP version, got {version}")

    print(f"NTP client-mode probe received {len(response)} bytes")
except Exception as exc:
    print(f"NTP probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_ssdp_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_SSDP_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
request = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    "MAN: \"ssdp:discover\"\r\n"
    "MX: 1\r\n"
    "ST: ssdp:all\r\n"
    f"USER-AGENT: tpot-ddospot-smoke/{token}\r\n"
    "\r\n"
).encode("ascii")

try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(min(timeout, 3))
        sock.sendto(request, (host, port))
        try:
            response, _ = sock.recvfrom(4096)
        except socket.timeout:
            response = b""

    if not response:
        raise RuntimeError("Expected SSDP response, got no UDP response")

    upper = response.upper()
    if not (upper.startswith(b"HTTP/") or b"SSDP" in upper or b"UPNP" in upper):
        raise RuntimeError(f"Expected SSDP-like HTTP response, got {response[:120]!r}")

    status = response.splitlines()[0].decode("iso-8859-1", errors="replace") if response.splitlines() else "<empty>"
    print(f"SSDP probe response: {status}")
except Exception as exc:
    print(f"SSDP probe failed: {exc}", file=sys.stderr)
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
  local json_file="$1"
  local service="$2"

  python3 - "${json_file}" "${service}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
service = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None

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

    invalid = None
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            invalid = f"{path}:{line_number}: invalid JSON: {exc}"
            continue
        if isinstance(event, dict):
            print(f"{service} JSON event found in {path}:{line_number}")
            sys.exit(0)
        invalid = f"{path}:{line_number}: JSON event is not an object"

    last_error = invalid or f"No JSON event found in {path}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  if grep -R -E "Traceback|NameError|Exception" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "DDoSPot runtime error found in log files"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "Traceback|NameError|Exception" >/dev/null 2>&1; then
    test_die "DDoSPot runtime error found in Docker logs"
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

  ensure_udp_port_free_for_docker "${CHARGEN_PORT}" "--chargen-port"
  ensure_udp_port_free_for_docker "${DNS_PORT}" "--dns-port"
  ensure_udp_port_free_for_docker "${NTP_PORT}" "--ntp-port"
  ensure_udp_port_free_for_docker "${SSDP_PORT}" "--ssdp-port"

  prepare_ddospot_harness
  test_enable_cleanup

  test_info "Starting isolated DDoSPot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "DDoSPot container did not stay running"
  test_ok "Container is running"

  MAPPED_CHARGEN_PORT="$(test_get_mapped_port "${TEST_NAME}" "19/udp")" || test_die "Could not resolve mapped host port for 19/udp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_CHARGEN_PORT} maps to container port 19/udp"

  MAPPED_DNS_PORT="$(test_get_mapped_port "${TEST_NAME}" "53/udp")" || test_die "Could not resolve mapped host port for 53/udp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_DNS_PORT} maps to container port 53/udp"

  MAPPED_NTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "123/udp")" || test_die "Could not resolve mapped host port for 123/udp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_NTP_PORT} maps to container port 123/udp"

  MAPPED_SSDP_PORT="$(test_get_mapped_port "${TEST_NAME}" "1900/udp")" || test_die "Could not resolve mapped host port for 1900/udp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_SSDP_PORT} maps to container port 1900/udp"

  local token="ddospot-test-$(date +%s)-$$"

  test_info "Running CHARGEN probe with token: ${token}"
  run_probe_with_retries "CHARGEN" run_chargen_probe "${token}" || test_die "CHARGEN probe failed on ${TEST_BIND_IP}:${MAPPED_CHARGEN_PORT}"
  test_wait_for_container || test_die "DDoSPot container stopped after CHARGEN probe"

  test_info "Running DNS CHAOS/TXT probe with token: ${token}"
  run_probe_with_retries "DNS" run_dns_probe "${token}" || test_die "DNS probe failed on ${TEST_BIND_IP}:${MAPPED_DNS_PORT}"
  test_wait_for_container || test_die "DDoSPot container stopped after DNS probe"

  test_info "Running NTP client-mode probe"
  run_probe_with_retries "NTP" run_ntp_probe || test_die "NTP probe failed on ${TEST_BIND_IP}:${MAPPED_NTP_PORT}"
  test_wait_for_container || test_die "DDoSPot container stopped after NTP probe"

  test_info "Running SSDP M-SEARCH probe with token: ${token}"
  run_probe_with_retries "SSDP" run_ssdp_probe "${token}" || test_die "SSDP probe failed on ${TEST_BIND_IP}:${MAPPED_SSDP_PORT}"
  test_wait_for_container || test_die "DDoSPot container stopped after SSDP probe"

  test_info "Waiting for DDoSPot JSON log events"
  wait_for_json_event "${CHARGEN_LOG_FILE}" "CHARGEN" || test_die "CHARGEN JSON event was not found in chargenpot.log"
  wait_for_json_event "${DNS_LOG_FILE}" "DNS" || test_die "DNS JSON event was not found in dnspot.log"
  wait_for_json_event "${NTP_LOG_FILE}" "NTP" || test_die "NTP JSON event was not found in ntpot.log"
  wait_for_json_event "${SSDP_LOG_FILE}" "SSDP" || test_die "SSDP JSON event was not found in ssdpot.log"
  test_ok "All DDoSPot JSON events were written"

  assert_no_runtime_errors
  test_ok "No DDoSPot runtime errors found in logs"

  test_ok "DDoSPot post-build smoke test completed successfully"
}

main "$@"
