#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="rdphoneypot"
DEFAULT_IMAGE="dtagdevsec/rdphoneypot:24.04.1"
IMAGE=""
RDP_PORT=""
LOG_DIR=""
CERT_DIR=""
MAPPED_RDP_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the RDPHoneypot image.

Options:
  --image IMAGE      Image to test. Defaults to docker/rdphoneypot/docker-compose.yml.
  --rdp-port PORT    Host TCP port for RDP. Default: dynamic loopback port.
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
      --rdp-port)
        [[ $# -ge 2 ]] || test_die "--rdp-port requires an argument"
        RDP_PORT="$2"
        shift 2
        ;;
      --rdp-port=*)
        RDP_PORT="${1#*=}"
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

  if [[ -n "${RDP_PORT}" ]]; then
    test_validate_port "${RDP_PORT}"
  fi
}

prepare_rdphoneypot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  CERT_DIR="${TEST_TMP_ROOT}/cert"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${CERT_DIR}"
  chmod 0777 "${LOG_DIR}" "${CERT_DIR}"

  local port_mapping="${TEST_BIND_IP}::3389"
  if [[ -n "${RDP_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${RDP_PORT}:3389"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  rdphoneypot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${port_mapping}"
    volumes:
      - "${CERT_DIR}:/opt/rdphoneypot/cert"
      - "${LOG_DIR}:/opt/rdphoneypot/log"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_rdp_negotiation_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_RDP_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import struct
import sys

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


# TPKT + X.224 Connection Request + RDP Negotiation Request.
# requestedProtocols = SSL | HYBRID, matching modern RDP clients.
request = (
    b"\x03\x00\x00\x13"
    b"\x0e\xe0\x00\x00\x00\x00\x00"
    b"\x01\x00\x08\x00\x03\x00\x00\x00"
)

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(request)
        header = sock.recv(4)
        if len(header) != 4:
            fail("RDP server returned a short TPKT header: {!r}".format(header))
        if header[:2] != b"\x03\x00":
            fail("Unexpected TPKT header: {!r}".format(header))

        length = struct.unpack(">H", header[2:4])[0]
        body = b""
        while len(body) < length - 4:
            chunk = sock.recv(length - 4 - len(body))
            if not chunk:
                break
            body += chunk
except Exception as exc:
    fail("RDP negotiation failed: {}".format(exc))

packet = header + body
if len(packet) != length:
    fail("RDP server returned {} bytes, expected {}".format(len(packet), length))
if len(packet) < 19:
    fail("RDP Connection Confirm is too short: {!r}".format(packet))
if packet[4] != 0x0E or packet[5] != 0xD0:
    fail("Unexpected X.224 Connection Confirm header: {!r}".format(packet[4:7]))

nego_type = packet[-8]
if nego_type == 0x03:
    failure_code = struct.unpack("<I", packet[-4:])[0]
    fail("RDP negotiation failed with code 0x{:08x}".format(failure_code))
if nego_type != 0x02:
    fail("Unexpected RDP negotiation response type: 0x{:02x}".format(nego_type))

selected_protocol = struct.unpack("<I", packet[-4:])[0]
if selected_protocol != 0x00000002:
    fail("Expected HYBRID/NLA protocol 0x00000002, got 0x{:08x}".format(selected_protocol))

print("RDPHoneypot negotiation probe succeeded")
PY
}

run_rdp_negotiation_probe_with_retries() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_rdp_negotiation_probe 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

wait_for_json_log_event() {
  python3 - "${LOG_DIR}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

log_dir = Path(sys.argv[1])
timeout = int(sys.argv[2])
deadline = time.monotonic() + timeout
last_error = None

while time.monotonic() < deadline:
    files = sorted(log_dir.glob("rdphoneypot.json*"))

    for log_file in files:
        if not log_file.is_file():
            continue

        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            last_error = "Could not read {}: {}".format(log_file, exc)
            continue

        for line_number, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:
                print(
                    "Invalid JSON in {}:{}: {}".format(log_file, line_number, exc),
                    file=sys.stderr,
                )
                sys.exit(1)

            if (
                event.get("eventid") == "rdphoneypot.session.connect"
                and event.get("dst_port") == 3389
                and event.get("sensor") == "t-pot"
                and event.get("src_ip")
            ):
                print("Connection event found in {}:{}".format(log_file, line_number))
                sys.exit(0)

    if not files:
        last_error = "No rdphoneypot.json log files found in {}".format(log_dir)

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
print("No matching RDPHoneypot connection event found", file=sys.stderr)
sys.exit(1)
PY
}

wait_for_certificate_hash() {
  python3 - "${CERT_DIR}/server.pem" "${TEST_TIMEOUT}" <<'PY'
import hashlib
import sys
import time
from pathlib import Path

cert_file = Path(sys.argv[1])
timeout = int(sys.argv[2])
deadline = time.monotonic() + timeout

while time.monotonic() < deadline:
    try:
        data = cert_file.read_bytes()
    except FileNotFoundError:
        time.sleep(1)
        continue
    except OSError as exc:
        print("Could not read {}: {}".format(cert_file, exc), file=sys.stderr)
        sys.exit(1)

    if data:
        print(hashlib.sha256(data).hexdigest())
        sys.exit(0)

    time.sleep(1)

print("Certificate was not created at {}".format(cert_file), file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern="Traceback|Unhandled Error|NameError|ImportError|Cannot listen|Permission denied|Read-only file system"

  if grep -R -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "RDPHoneypot runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "RDPHoneypot runtime error found in Docker logs"
  fi
}

main() {
  parse_args "$@"
  validate_args
  test_check_dependencies
  local cert_hash_before=""
  local cert_hash_after=""

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  if [[ -n "${RDP_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${RDP_PORT}" || test_die "${TEST_BIND_IP}:${RDP_PORT} is already in use. Try --rdp-port <free-port>."
  fi

  prepare_rdphoneypot_harness
  test_enable_cleanup

  test_info "Starting isolated RDPHoneypot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "RDPHoneypot container did not stay running"
  test_ok "Container is running"

  MAPPED_RDP_PORT="$(test_get_mapped_port "${TEST_NAME}" "3389")" || test_die "Could not resolve mapped host port for 3389/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_RDP_PORT} maps to container port 3389/tcp"

  test_info "Running RDP negotiation probe"
  run_rdp_negotiation_probe_with_retries || test_die "RDPHoneypot probe failed on ${TEST_BIND_IP}:${MAPPED_RDP_PORT}"
  test_wait_for_container || test_die "RDPHoneypot container stopped after RDP probe"

  test_info "Waiting for RDPHoneypot JSON log event"
  wait_for_json_log_event || test_die "Expected RDPHoneypot connection event was not found in rdphoneypot.json"
  test_ok "RDPHoneypot connection event was written to rdphoneypot.json"

  test_info "Checking persistent RDPHoneypot certificate"
  cert_hash_before="$(wait_for_certificate_hash)" || test_die "RDPHoneypot certificate was not created in the persistent cert volume"
  test_ok "server.pem was created in the persistent cert volume"

  test_info "Restarting container to verify certificate persistence"
  test_compose restart "${TEST_NAME}" >/dev/null
  test_wait_for_container || test_die "RDPHoneypot container did not stay running after restart"
  cert_hash_after="$(wait_for_certificate_hash)" || test_die "RDPHoneypot certificate was not readable after restart"
  [[ "${cert_hash_before}" == "${cert_hash_after}" ]] || test_die "RDPHoneypot certificate changed after container restart"
  test_ok "server.pem is unchanged after container restart"

  assert_no_runtime_errors
  test_ok "No RDPHoneypot runtime errors found in logs"

  test_ok "RDPHoneypot post-build smoke test completed successfully"
}

main "$@"
