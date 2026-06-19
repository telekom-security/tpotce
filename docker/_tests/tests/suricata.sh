#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="suricata"
DEFAULT_IMAGE="dtagdevsec/suricata:24.04.1"
IMAGE=""
LOG_DIR=""
PCAP_DIR=""
PCAP_FILE=""
EVE_LOG_FILE=""
SURICATA_LOG_FILE=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Suricata image.

The test generates a small HTTP PCAP, runs Suricata against it, and verifies
that Suricata writes a matching HTTP event to eve.json plus a runtime log.

Options:
  --image IMAGE      Image to test. Defaults to docker/suricata/docker-compose.yml.
  --timeout SEC      Timeout accepted for runner compatibility. Default: 30.
  --bind-ip IP       Accepted for runner compatibility; Suricata exposes no host port.
  --keep-artifacts   Keep temporary compose files, PCAPs, and logs for debugging.
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

generate_http_pcap() {
  local token="$1"

  python3 - "${PCAP_FILE}" "${token}" <<'PY'
import socket
import struct
import sys
import time
from pathlib import Path

pcap_path = Path(sys.argv[1])
token = sys.argv[2]

client_ip = "198.51.100.10"
server_ip = "203.0.113.20"
client_port = 45678
server_port = 8080
client_seq = 1000
server_seq = 5000
client_mac = bytes.fromhex("020000000001")
server_mac = bytes.fromhex("020000000002")

request = (
    f"GET /tpot-suricata-smoke/{token} HTTP/1.1\r\n"
    "Host: suricata-smoke.local\r\n"
    f"User-Agent: tpot-suricata-smoke/{token}\r\n"
    "Accept: */*\r\n"
    "Connection: close\r\n"
    "\r\n"
).encode("ascii")
response_body = f"suricata smoke {token}\n".encode("ascii")
response = (
    b"HTTP/1.1 200 OK\r\n"
    + f"Content-Length: {len(response_body)}\r\n".encode("ascii")
    + b"Content-Type: text/plain\r\n"
    + b"Connection: close\r\n"
    + b"\r\n"
    + response_body
)


def checksum(data):
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def tcp_segment(src_ip, dst_ip, src_port, dst_port, seq, ack, flags, payload=b""):
    data_offset = 5 << 4
    window = 64240
    header = struct.pack(
        "!HHIIBBHHH",
        src_port,
        dst_port,
        seq,
        ack,
        data_offset,
        flags,
        window,
        0,
        0,
    )
    pseudo_header = (
        socket.inet_aton(src_ip)
        + socket.inet_aton(dst_ip)
        + struct.pack("!BBH", 0, socket.IPPROTO_TCP, len(header) + len(payload))
    )
    tcp_checksum = checksum(pseudo_header + header + payload)
    header = struct.pack(
        "!HHIIBBHHH",
        src_port,
        dst_port,
        seq,
        ack,
        data_offset,
        flags,
        window,
        tcp_checksum,
        0,
    )
    return header + payload


def ipv4_packet(src_ip, dst_ip, payload, ident):
    version_ihl = 0x45
    tos = 0
    total_length = 20 + len(payload)
    flags_fragment = 0x4000
    ttl = 64
    protocol = socket.IPPROTO_TCP
    header = struct.pack(
        "!BBHHHBBH4s4s",
        version_ihl,
        tos,
        total_length,
        ident,
        flags_fragment,
        ttl,
        protocol,
        0,
        socket.inet_aton(src_ip),
        socket.inet_aton(dst_ip),
    )
    ip_checksum = checksum(header)
    header = struct.pack(
        "!BBHHHBBH4s4s",
        version_ihl,
        tos,
        total_length,
        ident,
        flags_fragment,
        ttl,
        protocol,
        ip_checksum,
        socket.inet_aton(src_ip),
        socket.inet_aton(dst_ip),
    )
    return header + payload


def ethernet_frame(src_mac, dst_mac, payload):
    return dst_mac + src_mac + b"\x08\x00" + payload


def packet(src_ip, dst_ip, src_mac, dst_mac, src_port, dst_port, seq, ack, flags, ident, payload=b""):
    segment = tcp_segment(src_ip, dst_ip, src_port, dst_port, seq, ack, flags, payload)
    return ethernet_frame(src_mac, dst_mac, ipv4_packet(src_ip, dst_ip, segment, ident))


packets = [
    packet(client_ip, server_ip, client_mac, server_mac, client_port, server_port, client_seq, 0, 0x02, 1),
    packet(server_ip, client_ip, server_mac, client_mac, server_port, client_port, server_seq, client_seq + 1, 0x12, 2),
    packet(client_ip, server_ip, client_mac, server_mac, client_port, server_port, client_seq + 1, server_seq + 1, 0x10, 3),
    packet(client_ip, server_ip, client_mac, server_mac, client_port, server_port, client_seq + 1, server_seq + 1, 0x18, 4, request),
    packet(server_ip, client_ip, server_mac, client_mac, server_port, client_port, server_seq + 1, client_seq + 1 + len(request), 0x10, 5),
    packet(server_ip, client_ip, server_mac, client_mac, server_port, client_port, server_seq + 1, client_seq + 1 + len(request), 0x18, 6, response),
    packet(client_ip, server_ip, client_mac, server_mac, client_port, server_port, client_seq + 1 + len(request), server_seq + 1 + len(response), 0x11, 7),
]

now = int(time.time())
with pcap_path.open("wb") as handle:
    handle.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
    for index, frame in enumerate(packets):
        handle.write(struct.pack("<IIII", now, index * 1000, len(frame), len(frame)))
        handle.write(frame)

print(f"Generated HTTP PCAP at {pcap_path} with token {token}")
PY
}

prepare_suricata_harness() {
  local token="$1"

  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  PCAP_DIR="${TEST_TMP_ROOT}/pcap"
  PCAP_FILE="${PCAP_DIR}/smoke.pcap"
  EVE_LOG_FILE="${LOG_DIR}/eve.json"
  SURICATA_LOG_FILE="${LOG_DIR}/suricata.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${PCAP_DIR}"
  chmod 0777 "${LOG_DIR}"
  chmod 0755 "${PCAP_DIR}"

  generate_http_pcap "${token}"
  chmod 0644 "${PCAP_FILE}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  suricata:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    entrypoint: ["suricata"]
    command: ["-r", "/pcap/smoke.pcap", "-l", "/var/log/suricata", "-k", "none"]
    volumes:
      - "${LOG_DIR}:/var/log/suricata"
      - "${PCAP_DIR}:/pcap:ro"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_suricata_pcap() {
  local output=""
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local state=""
  local exit_code=""

  if ! output="$(test_compose up -d --no-build 2>&1)"; then
    printf '%s\n' "${output}" >&2
    return 1
  fi

  while (( SECONDS < deadline )); do
    state="$(docker inspect -f '{{.State.Status}}' "${TEST_CONTAINER_NAME}" 2>/dev/null || true)"
    case "${state}" in
      exited|dead)
        exit_code="$(docker inspect -f '{{.State.ExitCode}}' "${TEST_CONTAINER_NAME}" 2>/dev/null || true)"
        [[ "${exit_code}" == "0" ]] && return 0
        test_compose logs --no-color --tail=120 >&2 || true
        printf 'Suricata container exited with status %s\n' "${exit_code:-unknown}" >&2
        return 1
        ;;
      running|created|restarting|"")
        ;;
      *)
        printf 'Unexpected Suricata container state: %s\n' "${state}" >&2
        return 1
        ;;
    esac
    sleep 1
  done

  test_compose logs --no-color --tail=120 >&2 || true
  printf 'Suricata container did not finish within %s seconds\n' "${TEST_TIMEOUT}" >&2
  return 1
}

find_http_log_event() {
  local token="$1"

  python3 - "${EVE_LOG_FILE}" "${token}" <<'PY'
import json
import sys
from pathlib import Path

log_file = Path(sys.argv[1])
token = sys.argv[2]

if not log_file.exists():
    print(f"{log_file} does not exist", file=sys.stderr)
    sys.exit(1)

try:
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
except OSError as exc:
    print(f"Could not read {log_file}: {exc}", file=sys.stderr)
    sys.exit(1)

if not lines:
    print(f"{log_file} is empty", file=sys.stderr)
    sys.exit(1)

invalid = []
for line_number, line in enumerate(lines, 1):
    stripped = line.strip()
    if not stripped:
        continue

    try:
        event = json.loads(stripped)
    except json.JSONDecodeError as exc:
        invalid.append(f"{log_file}:{line_number}: invalid JSON: {exc}")
        continue

    if not isinstance(event, dict):
        invalid.append(f"{log_file}:{line_number}: JSON event is not an object")
        continue

    if event.get("event_type") != "http":
        continue

    http = event.get("http")
    if not isinstance(http, dict):
        continue

    url = str(http.get("url", ""))
    user_agent = str(http.get("http_user_agent", ""))
    method = http.get("http_method")

    if token not in url and token not in user_agent:
        continue
    if method != "GET":
        print(f"{log_file}:{line_number}: expected http_method='GET', got {method!r}", file=sys.stderr)
        sys.exit(1)

    expected = {
        "src_ip": "198.51.100.10",
        "dest_ip": "203.0.113.20",
        "dest_port": 8080,
        "proto": "TCP",
    }
    for field, value in expected.items():
        if event.get(field) != value:
            print(f"{log_file}:{line_number}: expected {field}={value!r}, got {event.get(field)!r}", file=sys.stderr)
            sys.exit(1)

    missing = [field for field in ("timestamp", "flow_id", "src_port") if event.get(field) in (None, "")]
    if missing:
        print(f"{log_file}:{line_number}: missing fields: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    hostname = http.get("hostname", "")
    print(
        f"Suricata HTTP event found in {log_file}:{line_number} "
        f"{event['src_ip']}:{event['src_port']} -> {event['dest_ip']}:{event['dest_port']} "
        f"host={hostname!r} url={url!r}"
    )
    sys.exit(0)

if invalid:
    print("Invalid Suricata EVE JSON entries found:", file=sys.stderr)
    for item in invalid[:5]:
        print(f"  - {item}", file=sys.stderr)
else:
    event_types = set()
    for line in lines:
        if not line.strip().startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event_type") is not None:
            event_types.add(str(event.get("event_type")))
    print(
        f"No matching Suricata HTTP event found in {log_file} for token {token}; "
        f"event types: {', '.join(sorted(event_types)) or 'none'}",
        file=sys.stderr,
    )
sys.exit(1)
PY
}

assert_runtime_log_written() {
  [[ -s "${SURICATA_LOG_FILE}" ]] || test_die "Suricata runtime log was not written at ${SURICATA_LOG_FILE}"

  if ! grep -E "Suricata version|Engine started|pcap file|packets processed" "${SURICATA_LOG_FILE}" >/dev/null 2>&1; then
    test_die "Suricata runtime log does not contain expected startup or packet-processing text"
  fi
}

assert_no_runtime_errors() {
  local pattern="\\b(FATAL|PFATAL)\\b|Segmentation fault|Permission denied|Operation not permitted"

  if grep -R -I -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Suricata runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Suricata runtime error found in Docker logs"
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

  local token="suricata-test-$(date +%s)-$$"
  prepare_suricata_harness "${token}"
  test_enable_cleanup

  test_info "Running Suricata against generated HTTP PCAP with token: ${token}"
  run_suricata_pcap || test_die "Suricata failed to process the generated HTTP PCAP"
  test_ok "Suricata processed the generated HTTP PCAP"

  find_http_log_event "${token}" || test_die "Suricata did not write the generated HTTP probe to eve.json"
  test_ok "Suricata HTTP probe was written to eve.json"

  assert_runtime_log_written
  test_ok "Suricata runtime log was written"

  assert_no_runtime_errors
  test_ok "No Suricata runtime errors found in logs"

  test_ok "Suricata post-build smoke test completed successfully"
}

main "$@"
