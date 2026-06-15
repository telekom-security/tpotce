#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="log4pot"
DEFAULT_IMAGE="dtagdevsec/log4pot:24.04.1"
IMAGE=""
HTTP_PORT=""
LOG_DIR=""
PAYLOAD_DIR=""
LOG_FILE=""
MAPPED_HTTP_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Log4Pot image.

Options:
  --image IMAGE      Image to test. Defaults to dtagdevsec/log4pot:24.04.1.
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

prepare_log4pot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  PAYLOAD_DIR="${TEST_TMP_ROOT}/payloads"
  LOG_FILE="${LOG_DIR}/log4pot.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${PAYLOAD_DIR}"
  chmod 0777 "${LOG_DIR}" "${PAYLOAD_DIR}"

  local port_mapping="${TEST_BIND_IP}::8080"
  if [[ -n "${HTTP_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${HTTP_PORT}:8080"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  log4pot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    tmpfs:
      - /tmp:uid=2000,gid=2000,mode=0777
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/var/log/log4pot/log"
      - "${PAYLOAD_DIR}:/var/log/log4pot/payloads"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_http_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import http.client
import sys

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
path = f"/tpot-log4pot-smoke/{token}?probe={token}"
headers = {
    "Host": host,
    "User-Agent": f"tpot-log4pot-smoke/{token}",
    "Accept": "text/html,*/*",
    "Connection": "close",
}
conn = None

try:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    body = response.read()
finally:
    if conn is not None:
        conn.close()

if response.status != 200:
    print(f"Expected HTTP 200 from Log4Pot, got {response.status}", file=sys.stderr)
    sys.exit(1)

if b"SAP&#x20;NetWeaver&#x20;Portal" not in body and b"sap_logo" not in body:
    print("Log4Pot response did not look like the SAP NetWeaver page", file=sys.stderr)
    sys.exit(1)

print(f"Log4Pot HTTP response: {response.status} {response.reason}")
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

run_cve_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import http.client
import sys

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
headers = {
    "Host": host,
    "User-Agent": f"tpot-log4pot-cve-smoke/{token}",
    "X-Log4Pot-Smoke": f"${{jndi:ldap://127.0.0.1:9/{token}}}",
    "Accept": "text/html,*/*",
    "Connection": "close",
}
conn = None

try:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.request("GET", f"/tpot-log4pot-cve/{token}", headers=headers)
    response = conn.getresponse()
    body = response.read()
finally:
    if conn is not None:
        conn.close()

if response.status != 200:
    print(f"Expected HTTP 200 from Log4Pot CVE probe, got {response.status}", file=sys.stderr)
    sys.exit(1)

if b"SAP&#x20;NetWeaver&#x20;Portal" not in body and b"sap_logo" not in body:
    print("Log4Pot CVE response did not look like the SAP NetWeaver page", file=sys.stderr)
    sys.exit(1)

print(f"Log4Pot CVE-2021-44228 probe response: {response.status} {response.reason}")
PY
}

run_cve_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_cve_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

wait_for_log_events() {
  local token="$1"

  python3 - "${LOG_FILE}" "${token}" "${TEST_TIMEOUT}" <<'PY'
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
    if isinstance(value, str):
        return token in value
    if isinstance(value, dict):
        return any(has_token(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_token(item) for item in value)
    return False


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

    found_start = None
    found_request = None
    found_exploit = None

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

        legacy_type = event.get("type")
        if legacy_type in {"start", "request", "exploit", "payload", "exception", "end"}:
            print(f"Legacy top-level type field found in {log_file}:{line_number}", file=sys.stderr)
            sys.exit(1)

        if event.get("reason") == "start":
            found_start = f"{log_file}:{line_number}"

        if (
            event.get("reason") == "request"
            and (
                has_token(event.get("request"))
                or has_token(event.get("headers"))
            )
        ):
            found_request = f"{log_file}:{line_number}"

        if (
            event.get("reason") == "exploit"
            and event.get("location") == "header-X-Log4Pot-Smoke"
            and has_token(event.get("payload"))
            and has_token(event.get("deobfuscated_payload"))
            and "jndi:" in event.get("payload", "").lower()
        ):
            found_exploit = f"{log_file}:{line_number}"

    if found_start and found_request and found_exploit:
        print(f"Log4Pot start event found in {found_start}")
        print(f"Log4Pot request event found in {found_request}")
        print(f"Log4Pot CVE-2021-44228 exploit event found in {found_exploit}")
        sys.exit(0)

    if not lines:
        last_error = f"{log_file} is empty"
    else:
        last_error = f"No matching Log4Pot request/exploit events found for token {token}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

assert_optional_python_imports() {
  docker exec -i "${TEST_CONTAINER_NAME}" /usr/bin/python3 - <<'PY'
import azure.storage.blob
import boto3
import log4pot.loganalyzer
import log4pot.payloader
import pandas
import pycurl

print("Log4Pot optional imports succeeded")
print(f"pandas={pandas.__version__}")
print(pycurl.version)
print(f"boto3={boto3.__version__}")
PY
}

assert_no_runtime_errors() {
  local pattern="Traceback|ImportError|ModuleNotFoundError|PermissionError|Address already in use|Payload analysis requested"

  if grep -R -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Log4Pot runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Log4Pot runtime error found in Docker logs"
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

  prepare_log4pot_harness
  test_enable_cleanup

  test_info "Starting isolated Log4Pot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Log4Pot container did not stay running"
  test_ok "Container is running"

  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "8080")" || test_die "Could not resolve mapped host port for 8080/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTP_PORT} maps to container port 8080/tcp"

  test_info "Checking Log4Pot optional Python imports"
  assert_optional_python_imports
  test_ok "Optional Python imports succeeded"

  local token="log4pot-test-$(date +%s)-$$"

  test_info "Running Log4Pot HTTP probe with token: ${token}"
  run_http_probe_with_retries "${token}" || test_die "Log4Pot HTTP probe failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "Log4Pot container stopped after HTTP probe"

  test_info "Running Log4Pot CVE-2021-44228 probe with token: ${token}"
  run_cve_probe_with_retries "${token}" || test_die "Log4Pot CVE-2021-44228 probe failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "Log4Pot container stopped after CVE-2021-44228 probe"

  test_info "Waiting for Log4Pot JSON log events"
  wait_for_log_events "${token}" || test_die "Expected Log4Pot start/request/exploit events were not found in log4pot.log"
  test_ok "Log4Pot reason=start, reason=request and CVE reason=exploit events were written to log4pot.log"

  assert_no_runtime_errors
  test_ok "No Log4Pot runtime errors found in logs"

  test_ok "Log4Pot post-build smoke test completed successfully"
}

main "$@"
