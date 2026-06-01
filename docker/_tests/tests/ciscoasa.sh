#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="ciscoasa"
DEFAULT_IMAGE="dtagdevsec/ciscoasa:24.04.1"
IMAGE=""
HTTPS_PORT="8443"
IKE_PORT="5000"
LOG_DIR=""
LOG_FILE=""
MAPPED_HTTPS_PORT=""
MAPPED_IKE_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the CiscoASA image.

Options:
  --image IMAGE       Image to test. Defaults to docker/ciscoasa/docker-compose.yml.
  --https-port PORT   Host TCP port for HTTPS. Default: 8443.
  --ike-port PORT     Host UDP port for IKE. Default: 5000.
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
      --ike-port)
        [[ $# -ge 2 ]] || test_die "--ike-port requires an argument"
        IKE_PORT="$2"
        shift 2
        ;;
      --ike-port=*)
        IKE_PORT="${1#*=}"
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
  test_validate_port "${IKE_PORT}"
}

prepare_ciscoasa_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  LOG_FILE="${LOG_DIR}/ciscoasa.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  ciscoasa:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    tmpfs:
      - /tmp/ciscoasa:uid=2000,gid=2000
    ports:
      - "${TEST_BIND_IP}:${IKE_PORT}:5000/udp"
      - "${TEST_BIND_IP}:${HTTPS_PORT}:8443"
    volumes:
      - "${LOG_DIR}:/var/log/ciscoasa"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_log_line() {
  local pattern="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if [[ -f "${LOG_FILE}" ]] && grep -F -- "${pattern}" "${LOG_FILE}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  return 1
}

run_https_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${HTTPS_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import ssl
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
path = f"/+CSCOE+/logon.html?{token}"
request = (
    f"GET {path} HTTP/1.1\r\n"
    f"Host: {host}\r\n"
    f"User-Agent: tpot-ciscoasa-smoke/{token}\r\n"
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

assert_no_runtime_errors() {
  if [[ -f "${LOG_FILE}" ]] && grep -E "Traceback|NameError|Exception in callback" "${LOG_FILE}" >/dev/null 2>&1; then
    test_die "CiscoASA runtime error found in ciscoasa.log"
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

  test_ensure_port_free "${TEST_BIND_IP}" "${HTTPS_PORT}" || test_die "${TEST_BIND_IP}:${HTTPS_PORT} is already in use. Try --https-port <free-port>."
  test_ensure_udp_port_free "${TEST_BIND_IP}" "${IKE_PORT}" || test_die "${TEST_BIND_IP}:${IKE_PORT}/udp is already in use. Try --ike-port <free-port>."

  prepare_ciscoasa_harness
  test_enable_cleanup

  test_info "Starting isolated CiscoASA container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "CiscoASA container did not stay running"
  test_ok "Container is running"

  test_info "Waiting for CiscoASA HTTPS listener in ciscoasa.log"
  wait_for_log_line "Starting server on port 8443/tcp" || test_die "HTTPS listener entry was not found in ciscoasa.log"
  test_ok "HTTPS listener entry found in ciscoasa.log"

  test_info "Waiting for CiscoASA IKE listener in ciscoasa.log"
  wait_for_log_line "Starting server on port 5000/udp" || test_die "IKE listener entry was not found in ciscoasa.log"
  test_ok "IKE listener entry found in ciscoasa.log"

  MAPPED_HTTPS_PORT="$(test_get_mapped_port "${TEST_NAME}" "8443")" || test_die "Could not resolve mapped host port for 8443/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTPS_PORT} maps to container port 8443/tcp"

  MAPPED_IKE_PORT="$(test_get_mapped_port "${TEST_NAME}" "5000/udp")" || test_die "Could not resolve mapped host port for 5000/udp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_IKE_PORT} maps to container port 5000/udp"

  local token="ciscoasa-test-$(date +%s)-$$"

  test_info "Running HTTPS probe with token: ${token}"
  run_https_probe "${token}"
  test_wait_for_file_text "${token}" "${LOG_DIR}" || test_die "HTTPS probe token was not found in ciscoasa.log"
  test_ok "HTTPS probe was written to ciscoasa.log"

  test_wait_for_container || test_die "CiscoASA container stopped after HTTPS probe"
  assert_no_runtime_errors
  test_ok "No CiscoASA runtime errors found in ciscoasa.log"

  test_ok "CiscoASA post-build smoke test completed successfully"
}

main "$@"
