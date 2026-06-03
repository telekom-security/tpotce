#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="endlessh"
DEFAULT_IMAGE="dtagdevsec/endlessh:24.04.1"
IMAGE=""
SSH_PORT=""
LOG_DIR=""
CONFIG_FILE=""
LOG_FILE=""
MAPPED_SSH_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Endlessh image.

Options:
  --image IMAGE      Image to test. Defaults to docker/endlessh/docker-compose.yml.
  --ssh-port PORT    Host TCP port for SSH. Default: dynamic loopback port.
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
      --ssh-port)
        [[ $# -ge 2 ]] || test_die "--ssh-port requires an argument"
        SSH_PORT="$2"
        shift 2
        ;;
      --ssh-port=*)
        SSH_PORT="${1#*=}"
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

  if [[ -n "${SSH_PORT}" ]]; then
    test_validate_port "${SSH_PORT}"
  fi
}

prepare_endlessh_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  CONFIG_FILE="${TEST_TMP_ROOT}/endlessh.conf"
  LOG_FILE="${LOG_DIR}/endlessh.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${CONFIG_FILE}" <<EOF
Port 2222
Delay 1000
MaxLineLength 32
MaxClients 4096
LogLevel 1
BindFamily 4
EOF
  chmod 0644 "${CONFIG_FILE}"

  local port_mapping="${TEST_BIND_IP}::2222"
  if [[ -n "${SSH_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${SSH_PORT}:2222"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  endlessh:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${port_mapping}"
    volumes:
      - "${CONFIG_FILE}:/opt/endlessh/endlessh.conf:ro"
      - "${LOG_DIR}:/var/log/endlessh"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_banner_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_SSH_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])


class ProbeError(Exception):
    pass


def fail(message):
    print(f"Endlessh banner probe failed: {message}", file=sys.stderr)
    sys.exit(1)


def validate_line(line):
    if not line:
        raise ProbeError("Received an empty banner line")
    if len(line) > 32:
        raise ProbeError(f"Banner line exceeds MaxLineLength 32: {line!r}")
    if line.startswith(b"SSH-"):
        raise ProbeError(f"Banner line looks like an SSH identification string: {line!r}")
    if any(byte < 0x20 or byte > 0x7E for byte in line):
        raise ProbeError(f"Banner line contains non-printable bytes: {line!r}")


deadline = time.monotonic() + timeout
buffer = bytearray()
lines = []

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(b"SSH-2.0-tpot-endlessh-smoke\r\n")

        while time.monotonic() < deadline and len(lines) < 3:
            remaining = deadline - time.monotonic()
            sock.settimeout(max(0.1, min(1.0, remaining)))

            try:
                chunk = sock.recv(256)
            except socket.timeout:
                continue

            if not chunk:
                raise ProbeError("Connection closed before three banner lines were received")

            buffer.extend(chunk)
            while b"\n" in buffer and len(lines) < 3:
                line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                line = line.rstrip(b"\r")
                if not line:
                    continue
                validate_line(line)
                lines.append(bytes(line))

    if len(lines) < 3:
        raise ProbeError(f"Received {len(lines)} non-empty banner lines, expected at least 3")

    payload = b"".join(lines)
    if len(payload) < 8:
        raise ProbeError(f"Banner payload is too short to look random: {lines!r}")
    if len(set(payload)) < 3:
        raise ProbeError(f"Banner payload has too little character variety: {lines!r}")

    printable = [line.decode("ascii", errors="replace") for line in lines]
    print("Endlessh banner lines: " + " | ".join(printable))
except (OSError, ProbeError) as exc:
    fail(exc)
PY
}

run_banner_probe_with_retries() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_banner_probe 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

wait_for_log_events() {
  python3 - "${LOG_FILE}" "${TEST_TIMEOUT}" <<'PY'
import re
import sys
import time
from pathlib import Path

log_file = Path(sys.argv[1])
timeout = int(sys.argv[2])
deadline = time.monotonic() + timeout
last_error = None
found_accept = None
found_close = None

timestamp = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"
ipv4 = r"\d{1,3}(?:\.\d{1,3}){3}"
accept_re = re.compile(
    rf"^{timestamp}\s+ACCEPT\s+host={ipv4}\s+port=(\d+)\s+fd=\d+\s+n=\d+/\d+\s*$"
)
close_re = re.compile(
    rf"^{timestamp}\s+CLOSE\s+host={ipv4}\s+port=(\d+)\s+fd=\d+\s+"
    r"time=([0-9]+(?:\.[0-9]+)?)\s+bytes=([0-9]+(?:\.[0-9]+)?)\s*$"
)

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

    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        accept_match = accept_re.match(stripped)
        if accept_match:
            found_accept = f"{log_file}:{line_number}"
            continue

        close_match = close_re.match(stripped)
        if close_match:
            transferred = float(close_match.group(3))
            if transferred <= 0:
                print(
                    f"CLOSE event in {log_file}:{line_number} has non-positive bytes={transferred}",
                    file=sys.stderr,
                )
                sys.exit(1)
            found_close = f"{log_file}:{line_number} bytes={transferred:g}"
            continue

        if " ACCEPT " in stripped or " CLOSE " in stripped:
            print(f"Unexpected Endlessh log format in {log_file}:{line_number}: {stripped}", file=sys.stderr)
            sys.exit(1)

    if found_accept and found_close:
        print(f"ACCEPT event found in {found_accept}")
        print(f"CLOSE event found in {found_close}")
        sys.exit(0)

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
if not found_accept:
    print(f"No Endlessh ACCEPT event found in {log_file}", file=sys.stderr)
if not found_close:
    print(f"No Endlessh CLOSE event with positive bytes found in {log_file}", file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern="Segmentation fault|Permission denied|Read-only file system|No such file|Address already in use|Cannot listen"

  if grep -R -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Endlessh runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Endlessh runtime error found in Docker logs"
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

  if [[ -n "${SSH_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${SSH_PORT}" || test_die "${TEST_BIND_IP}:${SSH_PORT} is already in use. Try --ssh-port <free-port>."
  fi

  prepare_endlessh_harness
  test_enable_cleanup

  test_info "Starting isolated Endlessh container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Endlessh container did not stay running"
  test_ok "Container is running"

  MAPPED_SSH_PORT="$(test_get_mapped_port "${TEST_NAME}" "2222")" || test_die "Could not resolve mapped host port for 2222/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_SSH_PORT} maps to container port 2222/tcp"

  test_info "Running Endlessh random banner probe"
  run_banner_probe_with_retries || test_die "Endlessh banner probe failed on ${TEST_BIND_IP}:${MAPPED_SSH_PORT}"
  test_wait_for_container || test_die "Endlessh container stopped after banner probe"
  test_ok "Endlessh random banner probe succeeded"

  test_info "Waiting for ACCEPT and CLOSE events in endlessh.log"
  wait_for_log_events || test_die "Expected Endlessh ACCEPT and CLOSE events were not found in endlessh.log"
  test_ok "Endlessh ACCEPT and CLOSE events were written to endlessh.log"

  assert_no_runtime_errors
  test_ok "No Endlessh runtime errors found in logs"

  test_ok "Endlessh post-build smoke test completed successfully"
}

main "$@"
