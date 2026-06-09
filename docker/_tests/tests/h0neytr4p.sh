#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="h0neytr4p"
DEFAULT_IMAGE="dtagdevsec/h0neytr4p:24.04.1"
IMAGE=""
HTTP_PORT=""
HTTPS_PORT=""
LOG_DIR=""
PAYLOAD_DIR=""
CONTAINER_USER=""
MAPPED_HTTP_PORT=""
MAPPED_HTTPS_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the H0neytr4p image.

Options:
  --image IMAGE       Image to test. Defaults to docker/h0neytr4p/docker-compose.yml.
  --http-port PORT    Host TCP port for HTTP. Default: dynamic loopback port.
  --https-port PORT   Host TCP port for HTTPS. Default: dynamic loopback port.
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
      --http-port)
        [[ $# -ge 2 ]] || test_die "--http-port requires an argument"
        HTTP_PORT="$2"
        shift 2
        ;;
      --http-port=*)
        HTTP_PORT="${1#*=}"
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

  if [[ -n "${HTTP_PORT}" ]]; then
    test_validate_port "${HTTP_PORT}"
  fi

  if [[ -n "${HTTPS_PORT}" ]]; then
    test_validate_port "${HTTPS_PORT}"
  fi

  if [[ -n "${HTTP_PORT}" && -n "${HTTPS_PORT}" && "${HTTP_PORT}" == "${HTTPS_PORT}" ]]; then
    test_die "--http-port and --https-port must be different"
  fi
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

prepare_h0neytr4p_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  PAYLOAD_DIR="${TEST_TMP_ROOT}/payloads"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${PAYLOAD_DIR}"
  chmod 0777 "${LOG_DIR}" "${PAYLOAD_DIR}"
  if chown 2000:2000 "${LOG_DIR}" "${PAYLOAD_DIR}" >/dev/null 2>&1; then
    CONTAINER_USER="2000:2000"
  else
    CONTAINER_USER="$(id -u):$(id -g)"
  fi

  local http_port_mapping="${TEST_BIND_IP}::80"
  local https_port_mapping="${TEST_BIND_IP}::443"
  if [[ -n "${HTTP_PORT}" ]]; then
    http_port_mapping="${TEST_BIND_IP}:${HTTP_PORT}:80"
  fi
  if [[ -n "${HTTPS_PORT}" ]]; then
    https_port_mapping="${TEST_BIND_IP}:${HTTPS_PORT}:443"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  h0neytr4p:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "${CONTAINER_USER}"
    ports:
      - "${http_port_mapping}"
      - "${https_port_mapping}"
    volumes:
      - "${LOG_DIR}:/opt/h0neytr4p/log"
      - "${PAYLOAD_DIR}:/data/h0neytr4p/payloads"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_trap_probe() {
  local protocol="$1"
  local port="$2"
  local token="$3"

  python3 - "${TEST_BIND_IP}" "${port}" "${protocol}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import ssl
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
protocol = sys.argv[3]
token = sys.argv[4]
timeout = int(sys.argv[5])
deadline = time.monotonic() + timeout
path = f"/.env?h0neytr4p_test={token}"
request = (
    f"GET {path} HTTP/1.1\r\n"
    f"Host: {host}:{port}\r\n"
    f"User-Agent: tpot-h0neytr4p-smoke/{token}\r\n"
    "Connection: close\r\n"
    "\r\n"
).encode("ascii")


def read_response(sock):
    chunks = []
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        sock.settimeout(max(0.1, min(1.0, remaining)))

        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue

        if not chunk:
            break

        chunks.append(chunk)
        if b"\r\n\r\n" in b"".join(chunks):
            header, _, body = b"".join(chunks).partition(b"\r\n\r\n")
            if body:
                return header + b"\r\n\r\n" + body

    return b"".join(chunks)


try:
    with socket.create_connection((host, port), timeout=timeout) as raw_sock:
        if protocol == "https":
            context = ssl._create_unverified_context()
            with context.wrap_socket(raw_sock, server_hostname=host) as sock:
                sock.sendall(request)
                response = read_response(sock)
        else:
            raw_sock.sendall(request)
            response = read_response(raw_sock)

    header, _, body = response.partition(b"\r\n\r\n")
    status_line = header.splitlines()[0].decode("iso-8859-1", errors="replace") if header else ""

    if not status_line.startswith("HTTP/") or " 200 " not in status_line:
        raise RuntimeError(f"Expected HTTP 200 for {protocol} env_config_file trap, got {status_line!r}")

    if not body:
        raise RuntimeError(f"Expected non-empty {protocol} trap response body")

    print(f"H0neytr4p {protocol.upper()} trap response: {status_line}; sampled {len(body)} body bytes")
except Exception as exc:
    print(f"H0neytr4p {protocol.upper()} trap probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_trap_probe_with_retries() {
  local protocol="$1"
  local port="$2"
  local token="$3"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_trap_probe "${protocol}" "${port}" "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

find_json_log_event() {
  local token="$1"
  local protocol="$2"

  python3 - "${LOG_DIR}/log.json" "${token}" "${protocol}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
from pathlib import Path

log_file = Path(sys.argv[1])
token = sys.argv[2]
protocol = sys.argv[3]


def has_token(value):
    return isinstance(value, str) and token in value


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

for line_number, line in enumerate(lines, 1):
    stripped = line.strip()
    if not stripped:
        continue

    try:
        event = json.loads(stripped)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {log_file}:{line_number}: {exc}", file=sys.stderr)
        sys.exit(1)

    if (
        isinstance(event, dict)
        and event.get("trapped") == "true"
        and event.get("trapped_for") == "env_config_file"
        and event.get("request_method") == "GET"
        and event.get("protocol") == protocol
        and has_token(event.get("request_uri"))
        and has_token(event.get("user-agent"))
    ):
        print(f"H0neytr4p {protocol} env_config_file event found in {log_file}:{line_number}")
        sys.exit(0)

print(f"No matching H0neytr4p {protocol} log event found in {log_file} for token {token}", file=sys.stderr)
sys.exit(1)
PY
}

wait_for_json_log_event() {
  local token="$1"
  local protocol="$2"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(find_json_log_event "${token}" "${protocol}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

assert_no_runtime_errors() {
  local pattern="panic:|fatal error|Traceback|Exception|permission denied|Error configuring|Error parsing traps|Error writing log entry|Error saving file|\\[RESPONSE-ERROR\\]|unable to read file"

  if grep -R -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "H0neytr4p runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "H0neytr4p runtime error found in Docker logs"
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
    ensure_tcp_port_free_for_docker "${HTTP_PORT}" "--http-port"
  fi
  if [[ -n "${HTTPS_PORT}" ]]; then
    ensure_tcp_port_free_for_docker "${HTTPS_PORT}" "--https-port"
  fi

  prepare_h0neytr4p_harness
  test_enable_cleanup

  test_info "Starting isolated H0neytr4p container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "H0neytr4p container did not stay running"
  test_ok "Container is running"

  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "80")" || test_die "Could not resolve mapped host port for 80/tcp"
  MAPPED_HTTPS_PORT="$(test_get_mapped_port "${TEST_NAME}" "443")" || test_die "Could not resolve mapped host port for 443/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTP_PORT} maps to container port 80/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTPS_PORT} maps to container port 443/tcp"

  local http_token="h0neytr4p-http-test-$(date +%s)-$$"
  local https_token="h0neytr4p-https-test-$(date +%s)-$$"

  test_info "Running H0neytr4p HTTP env_config_file probe with token: ${http_token}"
  run_trap_probe_with_retries "http" "${MAPPED_HTTP_PORT}" "${http_token}" || test_die "H0neytr4p HTTP probe failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "H0neytr4p container stopped after HTTP probe"

  test_info "Running H0neytr4p HTTPS env_config_file probe with token: ${https_token}"
  run_trap_probe_with_retries "https" "${MAPPED_HTTPS_PORT}" "${https_token}" || test_die "H0neytr4p HTTPS probe failed on ${TEST_BIND_IP}:${MAPPED_HTTPS_PORT}"
  test_wait_for_container || test_die "H0neytr4p container stopped after HTTPS probe"

  test_info "Waiting for H0neytr4p HTTP JSON log event"
  wait_for_json_log_event "${http_token}" "http" || test_die "Expected H0neytr4p HTTP env_config_file event was not found in log.json"
  test_ok "H0neytr4p HTTP env_config_file event was written to log.json"

  test_info "Waiting for H0neytr4p HTTPS JSON log event"
  wait_for_json_log_event "${https_token}" "https" || test_die "Expected H0neytr4p HTTPS env_config_file event was not found in log.json"
  test_ok "H0neytr4p HTTPS env_config_file event was written to log.json"

  assert_no_runtime_errors
  test_ok "No H0neytr4p runtime errors found in logs"

  test_ok "H0neytr4p post-build smoke test completed successfully"
}

main "$@"
