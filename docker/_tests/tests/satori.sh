#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="satori"
DEFAULT_IMAGE="dtagdevsec/satori:24.04.2"
IMAGE=""
MODULES="tcp"
LOG_DIR=""
JSON_LOG_FILE=""
TCP_TARGET_CONTAINER_NAME=""
SATORI_CONTAINER_IP=""
TCP_TARGET_IP=""
TCP_TARGET_PORT="8080"

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Satori image.

The test starts Satori on a temporary Docker network, opens TCP connections
from inside the Satori container to a target container, and verifies that
Satori writes matching SYN and SYN+ACK fingerprints as JSON log events.

Options:
  --image IMAGE      Image to test. Defaults to docker/satori/docker-compose.yml.
  --modules LIST     Satori modules to enable. Default: tcp (as in T-Pot).
  --timeout SEC      Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP       Accepted for runner compatibility; Satori exposes no host port.
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
      --modules)
        [[ $# -ge 2 ]] || test_die "--modules requires an argument"
        MODULES="$2"
        shift 2
        ;;
      --modules=*)
        MODULES="${1#*=}"
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
  [[ "${MODULES}" =~ ^[A-Za-z,]+$ ]] || test_die "Invalid --modules value: ${MODULES}"
}

prepare_satori_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  JSON_LOG_FILE="${LOG_DIR}/satori.json"
  TCP_TARGET_CONTAINER_NAME="${TEST_PROJECT_NAME}-tcp-target"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  : > "${JSON_LOG_FILE}"
  chmod 0777 "${LOG_DIR}"
  chmod 0666 "${JSON_LOG_FILE}"

  # Both containers need the same capabilities: the image's python3 carries
  # file capabilities (+eip), and execve fails if they exceed the bounding set.
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  satori:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    environment:
      - SATORI_INTERFACE=eth0
    cap_add:
      - NET_ADMIN
      - NET_RAW
    command: ["--modules", "${MODULES}", "--limit", "0", "--log", "/var/log/satori/satori.json"]
    volumes:
      - "${LOG_DIR}:/var/log/satori"
  tcp-target:
    image: "${IMAGE}"
    container_name: "${TCP_TARGET_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    cap_add:
      - NET_ADMIN
      - NET_RAW
    entrypoint: ["python3", "-c"]
    command:
      - |
        import socket
        server = socket.create_server(("0.0.0.0", ${TCP_TARGET_PORT}))
        while True:
            conn, _ = server.accept()
            conn.close()
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

get_container_ipv4() {
  local container_name="$1"
  local ip=""

  ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${container_name}")"
  [[ -n "${ip}" ]] || test_die "Could not determine IPv4 address for ${container_name}"
  printf '%s\n' "${ip}"
}

run_tcp_probe() {
  docker exec "${TEST_CONTAINER_NAME}" python3 -c '
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
with socket.create_connection((host, port), timeout=3):
    pass
print(f"Satori TCP probe connected to {host}:{port}")
' "${TCP_TARGET_IP}" "${TCP_TARGET_PORT}"
}

find_satori_log_events() {
  python3 - "${JSON_LOG_FILE}" "${SATORI_CONTAINER_IP}" "${TCP_TARGET_IP}" "${TCP_TARGET_PORT}" <<'PY'
import json
import sys
from pathlib import Path

log_file = Path(sys.argv[1])
satori_ip = sys.argv[2]
target_ip = sys.argv[3]
target_port = int(sys.argv[4])

if not log_file.exists():
    print(f"{log_file} does not exist yet", file=sys.stderr)
    sys.exit(1)

try:
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
except OSError as exc:
    print(f"Could not read {log_file}: {exc}", file=sys.stderr)
    sys.exit(1)

if not lines:
    print(f"{log_file} is empty", file=sys.stderr)
    sys.exit(1)

events = []
invalid = []
for line_number, line in enumerate(lines, 1):
    stripped = line.strip()
    if not stripped:
        continue

    try:
        event = json.loads(stripped)
    except json.JSONDecodeError as exc:
        invalid.append(f"{log_file}:{line_number}: {exc}")
        continue

    if not isinstance(event, dict):
        invalid.append(f"{log_file}:{line_number}: JSON log entry is not an object")
        continue

    events.append((line_number, event))

if invalid:
    print("Invalid Satori JSON log entries found:", file=sys.stderr)
    for item in invalid[:5]:
        print(f"  - {item}", file=sys.stderr)
    sys.exit(1)


def require_fields(line_number, event):
    satori = event.get("satori") or {}
    missing = [field for field in ("timestamp", "raw_sig", "params", "os") if event.get(field) in (None, "")]
    missing += [f"satori.{field}" for field in ("signature", "commit") if satori.get(field) in (None, "")]
    if missing:
        print(
            f"Matching Satori event in {log_file}:{line_number} is missing fields: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)


syn_event = None
synack_event = None

for line_number, event in events:
    if event.get("mod") == "syn" and event.get("src_ip") == satori_ip and event.get("dest_ip") == target_ip and event.get("dest_port") == target_port:
        require_fields(line_number, event)
        syn_event = (line_number, event)

    if event.get("mod") == "syn+ack" and event.get("src_ip") == target_ip and event.get("dest_ip") == satori_ip and event.get("src_port") == target_port:
        require_fields(line_number, event)
        synack_event = (line_number, event)

if not syn_event or not synack_event:
    seen = sorted({str(event.get("mod")) for _, event in events})
    print(
        "Expected Satori syn and syn+ack events were not found "
        f"for {satori_ip} <-> {target_ip}:{target_port}; seen mods: {', '.join(seen) or 'none'}",
        file=sys.stderr,
    )
    sys.exit(1)

for label, (line_number, event) in (("SYN", syn_event), ("SYN+ACK", synack_event)):
    print(
        f"Satori {label} event found in {log_file}:{line_number} "
        f"{event['src_ip']}:{event.get('src_port')} -> {event['dest_ip']}:{event.get('dest_port')} "
        f"raw_sig={event['raw_sig']!r} os={event['os']!r}"
    )
PY
}

run_probe_until_logged() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local probe_output=""
  local log_output=""

  while (( SECONDS < deadline )); do
    probe_output="$(run_tcp_probe 2>&1)" || true
    if log_output="$(find_satori_log_events 2>&1)"; then
      printf '%s\n' "${probe_output}"
      printf '%s\n' "${log_output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${probe_output}" >&2
  printf '%s\n' "${log_output}" >&2
  return 1
}

assert_no_runtime_errors() {
  # Satori's capture loop swallows exceptions, so import and capture errors only
  # show up here (or as missing events).
  local pattern="Traceback|ModuleNotFoundError|ImportError|Permission denied|Operation not permitted|Segmentation fault|pcap_|No such device"

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Satori runtime error found in Docker logs"
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

  prepare_satori_harness
  test_enable_cleanup

  test_info "Starting isolated Satori container and TCP target (modules: ${MODULES})"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Satori container did not stay running"
  wait_for_named_container "${TCP_TARGET_CONTAINER_NAME}" || test_die "Satori TCP target container did not stay running"
  test_ok "Containers are running"

  SATORI_CONTAINER_IP="$(get_container_ipv4 "${TEST_CONTAINER_NAME}")"
  TCP_TARGET_IP="$(get_container_ipv4 "${TCP_TARGET_CONTAINER_NAME}")"
  test_ok "Container addresses: satori=${SATORI_CONTAINER_IP}, tcp-target=${TCP_TARGET_IP}"

  test_info "Generating TCP traffic from the Satori container"
  run_probe_until_logged || test_die "Satori did not log the generated TCP handshake"
  test_wait_for_container || test_die "Satori container stopped after TCP probe"
  test_ok "Satori captured the generated SYN and SYN+ACK"

  assert_no_runtime_errors
  test_ok "No Satori runtime errors found in logs"

  test_ok "Satori post-build smoke test completed successfully"
}

main "$@"
