#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="hellpot"
DEFAULT_IMAGE="dtagdevsec/hellpot:24.04.1"
IMAGE=""
HTTP_PORT=""
LOG_DIR=""
CONTAINER_USER=""
MAPPED_HTTP_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the HellPot image.

Options:
  --image IMAGE      Image to test. Defaults to dtagdevsec/hellpot:24.04.1.
  --http-port PORT   Host TCP port for HTTP. Default: dynamic loopback port.
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
      --http-port)
        [[ $# -ge 2 ]] || test_die "--http-port requires an argument"
        HTTP_PORT="$2"
        shift 2
        ;;
      --http-port=*)
        HTTP_PORT="${1#*=}"
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

  if [[ -n "${HTTP_PORT}" ]]; then
    test_validate_port "${HTTP_PORT}"
  fi
}

prepare_hellpot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"
  if chown 2000:2000 "${LOG_DIR}" >/dev/null 2>&1; then
    CONTAINER_USER="2000:2000"
  else
    CONTAINER_USER="$(id -u):$(id -g)"
  fi

  local port_mapping="${TEST_BIND_IP}::8080"
  if [[ -n "${HTTP_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${HTTP_PORT}:8080"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  hellpot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "${CONTAINER_USER}"
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/var/log/hellpot"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_blacklist_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
request = (
    "GET / HTTP/1.1\r\n"
    f"Host: {host}\r\n"
    "User-Agent: curl/8.0.0\r\n"
    "Accept: */*\r\n"
    "Connection: close\r\n"
    "\r\n"
).encode("ascii")

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
        response = bytearray()

        while time.monotonic() < deadline and len(response) < 8192:
            remaining = deadline - time.monotonic()
            sock.settimeout(max(0.1, min(1.0, remaining)))

            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue

            if not chunk:
                break

            response.extend(chunk)

    raw = bytes(response)
    header, _, body = raw.partition(b"\r\n\r\n")
    status_line = header.splitlines()[0].decode("iso-8859-1", errors="replace") if header else ""

    if not status_line.startswith("HTTP/") or " 404 " not in status_line:
        raise RuntimeError(f"Expected HTTP 404 for curl user-agent, got {status_line!r}")

    if body.strip() != b"Not found":
        raise RuntimeError(f"Expected body 'Not found', got {body[:80]!r}")

    print(f"HellPot curl blacklist response: {status_line}")
except Exception as exc:
    print(f"HellPot curl blacklist probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_blacklist_probe_with_retries() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_blacklist_probe 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

run_stream_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import re
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
path = f"/tpot-hellpot-smoke/{token}?probe={token}"
request = (
    f"GET {path} HTTP/1.1\r\n"
    f"Host: {host}\r\n"
    f"User-Agent: Mozilla/5.0 tpot-hellpot-smoke/{token}\r\n"
    "Accept: text/html,*/*\r\n"
    "Connection: close\r\n"
    "\r\n"
).encode("ascii")

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
        response = bytearray()

        while time.monotonic() < deadline and len(response) < 65536:
            remaining = deadline - time.monotonic()
            sock.settimeout(max(0.1, min(1.0, remaining)))

            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue

            if not chunk:
                break

            response.extend(chunk)
            if b"\r\n\r\n" in response and len(response.split(b"\r\n\r\n", 1)[1]) >= 1024:
                break

    raw = bytes(response)
    header, _, body = raw.partition(b"\r\n\r\n")
    status_line = header.splitlines()[0].decode("iso-8859-1", errors="replace") if header else ""

    if not status_line.startswith("HTTP/") or " 200 " not in status_line:
        raise RuntimeError(f"Expected HTTP 200 stream response, got {status_line!r}")

    header_text = header.decode("iso-8859-1", errors="replace")
    if not re.search(r"(?im)^Transfer-Encoding:\s*chunked\s*$", header_text):
        raise RuntimeError("Expected Transfer-Encoding: chunked in HellPot stream response")

    if len(body) < 1024:
        raise RuntimeError(f"Expected at least 1024 bytes of stream body, got {len(body)}")

    if b"Not found" in body[:128]:
        raise RuntimeError("Stream probe unexpectedly received Not found body")

    print(f"HellPot stream response: {status_line}; sampled {len(body)} body bytes")
except Exception as exc:
    print(f"HellPot stream probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_stream_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_stream_probe "${token}" 2>&1)"; then
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

  python3 - "${LOG_DIR}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

log_dir = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None


def has_token(value):
    return isinstance(value, str) and token in value


def is_matching_plaintext(line):
    return (
        "NEW" in line
        and "URL=" in line
        and "USERAGENT=" in line
        and token in line
    )


while time.monotonic() < deadline:
    files = sorted(path for path in log_dir.rglob("*") if path.is_file())

    for log_file in files:
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            last_error = f"Could not read {log_file}: {exc}"
            continue

        for line_number, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("{"):
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    print(f"Invalid JSON in {log_file}:{line_number}: {exc}", file=sys.stderr)
                    sys.exit(1)

                if (
                    isinstance(event, dict)
                    and event.get("message") == "NEW"
                    and has_token(event.get("USERAGENT"))
                    and has_token(event.get("URL"))
                ):
                    print(f"HellPot JSON NEW event found in {log_file}:{line_number}")
                    sys.exit(0)
                continue

            if is_matching_plaintext(stripped):
                print(f"HellPot plaintext NEW event found in {log_file}:{line_number}")
                sys.exit(0)

    if not files:
        last_error = f"No HellPot log files found in {log_dir}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
print(f"No matching HellPot log event found for token {token}", file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern="panic:|fatal error|level\":\"fatal|level\":\"panic|permission denied"

  if grep -R -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "HellPot runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "HellPot runtime error found in Docker logs"
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

  if [[ -n "${HTTP_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${HTTP_PORT}" || test_die "${TEST_BIND_IP}:${HTTP_PORT} is already in use. Try --http-port <free-port>."
  fi

  prepare_hellpot_harness
  test_enable_cleanup

  test_info "Starting isolated HellPot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "HellPot container did not stay running"
  test_ok "Container is running"

  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "8080")" || test_die "Could not resolve mapped host port for 8080/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTP_PORT} maps to container port 8080/tcp"

  local token="hellpot-test-$(date +%s)-$$"

  test_info "Running HellPot curl blacklist probe"
  run_blacklist_probe_with_retries || test_die "HellPot curl blacklist probe failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "HellPot container stopped after curl blacklist probe"

  test_info "Running HellPot stream probe with token: ${token}"
  run_stream_probe_with_retries "${token}" || test_die "HellPot stream probe failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "HellPot container stopped after stream probe"

  test_info "Waiting for HellPot log event"
  wait_for_log_event "${token}" || test_die "Expected HellPot NEW event was not found in logs"
  test_ok "HellPot NEW event was written to logs"

  assert_no_runtime_errors
  test_ok "No HellPot runtime errors found in logs"

  test_ok "HellPot post-build smoke test completed successfully"
}

main "$@"
