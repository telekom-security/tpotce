#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="citrixhoneypot"
DEFAULT_IMAGE="dtagdevsec/citrixhoneypot:24.04"
IMAGE=""
HTTPS_PORT="443"
LOG_DIR=""
MAPPED_HTTPS_PORT=""
REQUEST_PATH=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the CitrixHoneypot image.

Options:
  --image IMAGE       Image to test. Defaults to docker/citrixhoneypot/docker-compose.yml.
  --https-port PORT   Host TCP port for HTTPS. Default: 443.
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
      --https-port)
        [[ $# -ge 2 ]] || test_die "--https-port requires an argument"
        HTTPS_PORT="$2"
        shift 2
        ;;
      --https-port=*)
        HTTPS_PORT="${1#*=}"
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
  test_validate_port "${HTTPS_PORT}"
}

prepare_citrixhoneypot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  citrixhoneypot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${TEST_BIND_IP}:${HTTPS_PORT}:443"
    volumes:
      - "${LOG_DIR}:/opt/citrixhoneypot/logs"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_https_probe() {
  local token="$1"

  REQUEST_PATH="/vpn/../vpns/cfg/smb.conf?${token}"

  python3 - "${TEST_BIND_IP}" "${HTTPS_PORT}" "${REQUEST_PATH}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import ssl
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
path = sys.argv[3]
token = sys.argv[4]
timeout = int(sys.argv[5])
deadline = time.monotonic() + timeout
request = (
    f"GET {path} HTTP/1.1\r\n"
    f"Host: {host}\r\n"
    f"User-Agent: tpot-citrixhoneypot-smoke/{token}\r\n"
    "Connection: close\r\n"
    "\r\n"
).encode("ascii")

context = ssl._create_unverified_context()

try:
    with socket.create_connection((host, port), timeout=timeout) as raw_sock:
        with context.wrap_socket(raw_sock, server_hostname=host) as sock:
            sock.settimeout(1)
            sock.sendall(request)
            chunks = []
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                chunks.append(chunk)

    response = b"".join(chunks)
    if not response.startswith(b"HTTP/"):
        raise RuntimeError(f"Expected HTTP response, got {response[:80]!r}")

    status_line = response.splitlines()[0].decode("iso-8859-1", errors="replace")
    print(f"HTTPS response: {status_line}")
except Exception as exc:
    print(f"HTTPS probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_https_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_https_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

wait_for_log_event() {
  local token="$1"
  local path="$2"

  python3 - "${LOG_DIR}" "${token}" "${path}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

log_dir = Path(sys.argv[1])
token = sys.argv[2]
path = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
last_error = None


def contains_probe(value):
    if isinstance(value, str):
        return token in value or path in value
    if isinstance(value, dict):
        return any(contains_probe(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_probe(item) for item in value)
    return False


while time.monotonic() < deadline:
    files = sorted(item for item in log_dir.rglob("*") if item.is_file())
    for log_file in files:
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            last_error = f"Could not read {log_file}: {exc}"
            continue

        for line_number, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped:
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    event = None
                if event is not None and contains_probe(event):
                    print(f"Structured log event found in {log_file}:{line_number}")
                    sys.exit(0)

            if token in line or path in line:
                print(f"Log text found in {log_file}:{line_number}")
                sys.exit(0)

    if not files:
        last_error = f"No log files found in {log_dir}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
print(f"No CitrixHoneypot log entry found for token {token}", file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  if grep -R -E "Traceback|NameError|Exception" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "CitrixHoneypot runtime error found in logs"
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

  if (( HTTPS_PORT < 1024 )); then
    test_info "Skipping user-space preflight for privileged port ${HTTPS_PORT}; Docker will validate the binding."
  else
    test_ensure_port_free "${TEST_BIND_IP}" "${HTTPS_PORT}" || test_die "${TEST_BIND_IP}:${HTTPS_PORT} is already in use. Try --https-port <free-port>."
  fi

  prepare_citrixhoneypot_harness
  test_enable_cleanup

  test_info "Starting isolated CitrixHoneypot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "CitrixHoneypot container did not stay running"
  test_ok "Container is running"

  MAPPED_HTTPS_PORT="$(test_get_mapped_port "${TEST_NAME}" "443")" || test_die "Could not resolve mapped host port for 443/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTPS_PORT} maps to container port 443/tcp"

  local token="citrixhoneypot-test-$(date +%s)-$$"

  test_info "Running CVE-shaped HTTPS probe with token: ${token}"
  run_https_probe_with_retries "${token}" || test_die "HTTPS probe failed on ${TEST_BIND_IP}:${HTTPS_PORT}"
  test_wait_for_container || test_die "CitrixHoneypot container stopped after HTTPS probe"

  test_info "Waiting for probe entry in CitrixHoneypot logs"
  wait_for_log_event "${token}" "${REQUEST_PATH}" || test_die "Probe token or request path was not found in CitrixHoneypot logs"
  test_ok "Probe was written to CitrixHoneypot logs"

  assert_no_runtime_errors
  test_ok "No CitrixHoneypot runtime errors found in logs"

  test_ok "CitrixHoneypot post-build smoke test completed successfully"
}

main "$@"
