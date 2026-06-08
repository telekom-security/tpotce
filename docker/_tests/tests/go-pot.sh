#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="go-pot"
DEFAULT_IMAGE="dtagdevsec/go-pot:24.04.1"
IMAGE=""
HTTP_PORT=""
LOG_DIR=""
ACCESS_LOG_FILE=""
MAPPED_HTTP_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Go-Pot image.

Options:
  --image IMAGE      Image to test. Defaults to docker/go-pot/docker-compose.yml.
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

prepare_go_pot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  ACCESS_LOG_FILE="${LOG_DIR}/go-pot.json"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  local port_mapping="${TEST_BIND_IP}::8080"
  if [[ -n "${HTTP_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${HTTP_PORT}:8080"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  go-pot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/opt/go-pot/log"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_http_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
path = f"/tpot-go-pot-smoke/{token}?probe={token}"
request = (
    f"GET {path} HTTP/1.1\r\n"
    f"Host: {host}\r\n"
    f"User-Agent: tpot-go-pot-smoke/{token}\r\n"
    "Connection: close\r\n"
    "\r\n"
).encode("ascii")

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
        response = bytearray()

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            sock.settimeout(max(0.1, min(1.0, remaining)))

            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue

            if not chunk:
                break

            response.extend(chunk)
            if response.startswith(b"HTTP/") and b"\n" in response:
                break

    if not response.startswith(b"HTTP/"):
        raise RuntimeError(f"Expected HTTP response, got {bytes(response[:80])!r}")

    status_line = response.splitlines()[0].decode("iso-8859-1", errors="replace")
    print(f"HTTP response: {status_line}")
except Exception as exc:
    print(f"Go-Pot HTTP probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_http_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_http_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

wait_for_access_log_event() {
  local token="$1"

  python3 - "${ACCESS_LOG_FILE}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

log_file = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None


def has_token(value):
    return isinstance(value, str) and token in value


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

    if not lines:
        last_error = f"{log_file} is empty"
        time.sleep(1)
        continue

    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON in {log_file}:{line_number}: {exc}", file=sys.stderr)
            sys.exit(1)

        if not isinstance(event, dict):
            continue

        if (
            event.get("phase") == "start"
            and event.get("method") == "GET"
            and (
                has_token(event.get("path"))
                or has_token(event.get("qs"))
                or has_token(event.get("user_agent"))
            )
        ):
            print(f"Go-Pot access event found in {log_file}:{line_number}")
            sys.exit(0)

    last_error = f"No matching Go-Pot start access event found in {log_file} for token {token}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

assert_no_json_error_levels() {
  local docker_log_file="${TEST_TMP_ROOT}/docker-logs.txt"

  test_compose logs --no-color > "${docker_log_file}" 2>/dev/null || true

  python3 - "${LOG_DIR}" "${docker_log_file}" <<'PY'
import json
import sys
from pathlib import Path

log_dir = Path(sys.argv[1])
docker_log_file = Path(sys.argv[2])


def normalize_line(raw_line):
    stripped = raw_line.strip()
    if stripped.startswith("{"):
        return stripped

    if "|" in raw_line:
        _, after = raw_line.split("|", 1)
        after = after.strip()
        if after.startswith("{"):
            return after

    return ""


def scan_file(path, label):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"Could not read {label}: {exc}", file=sys.stderr)
        sys.exit(1)

    for line_number, raw_line in enumerate(lines, 1):
        line = normalize_line(raw_line)
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON in {label}:{line_number}: {exc}", file=sys.stderr)
            sys.exit(1)

        if isinstance(event, dict) and event.get("level") == "error":
            print(f'Go-Pot JSON log has level "error" in {label}:{line_number}', file=sys.stderr)
            sys.exit(1)


if log_dir.exists():
    for log_file in sorted(path for path in log_dir.rglob("*") if path.is_file()):
        scan_file(log_file, str(log_file))

if docker_log_file.exists():
    scan_file(docker_log_file, "Docker logs")
PY
}

assert_no_go_crash_signals() {
  local pattern="panic:|fatal error"

  if grep -R -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Go-Pot crash signal found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Go-Pot crash signal found in Docker logs"
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

  prepare_go_pot_harness
  test_enable_cleanup

  test_info "Starting isolated Go-Pot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Go-Pot container did not stay running"
  test_ok "Container is running"

  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "8080")" || test_die "Could not resolve mapped host port for 8080/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTP_PORT} maps to container port 8080/tcp"

  local token="go-pot-test-$(date +%s)-$$"

  test_info "Running Go-Pot HTTP probe with token: ${token}"
  run_http_probe_with_retries "${token}" || test_die "Go-Pot HTTP probe failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "Go-Pot container stopped after HTTP probe"

  test_info "Waiting for Go-Pot access log event"
  wait_for_access_log_event "${token}" || test_die "Expected Go-Pot access event was not found in go-pot.json"
  test_ok "Go-Pot access event was written to go-pot.json"

  assert_no_json_error_levels
  assert_no_go_crash_signals
  test_ok "No Go-Pot JSON error-level entries or crash signals found in logs"

  test_ok "Go-Pot post-build smoke test completed successfully"
}

main "$@"
