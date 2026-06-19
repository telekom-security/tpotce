#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="wordpot"
DEFAULT_IMAGE="dtagdevsec/wordpot:24.04.1"
IMAGE=""
HTTP_PORT=""
LOG_DIR=""
WORDPOT_LOG_FILE=""
DOCKER_LOG_FILE=""
MAPPED_HTTP_PORT=""
TOKEN=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Wordpot image.

Options:
  --image IMAGE      Image to test. Defaults to docker/wordpot/docker-compose.yml.
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

prepare_wordpot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  WORDPOT_LOG_FILE="${LOG_DIR}/wordpot.log"
  DOCKER_LOG_FILE="${LOG_DIR}/docker.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  local port_mapping="${TEST_BIND_IP}::80"
  if [[ -n "${HTTP_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${HTTP_PORT}:80"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  wordpot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/opt/wordpot/logs"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

resolve_mapped_port() {
  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" 80)" \
    || test_die "Could not resolve mapped host port for 80/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTP_PORT} maps to Wordpot container port 80/tcp"
}

run_http_probes() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${TOKEN}" "${TEST_TIMEOUT}" <<'PY'
import http.client
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
connect_timeout = max(1.0, min(float(timeout), 5.0))


class ProbeError(Exception):
    pass


def request(path):
    conn = http.client.HTTPConnection(host, port, timeout=connect_timeout)
    try:
        conn.request("GET", path, headers={"User-Agent": token})
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        content_type = response.getheader("Content-Type", "")
        return response.status, content_type, body
    finally:
        conn.close()


def expect(path, expected_status, required_text):
    status, content_type, body = request(path)
    if status != expected_status:
        raise ProbeError(f"GET {path} returned HTTP {status}: {body[:200]}")
    if "text/html" not in content_type:
        raise ProbeError(f"GET {path} returned unexpected content type: {content_type!r}")
    missing = [text for text in required_text if text not in body]
    if missing:
        raise ProbeError(f"GET {path} response missed {missing!r}: {body[:200]!r}")


def scenario():
    expect("/", 200, ("<title>Wordpress |", "WordPress 2.8", "Hello world!"))
    expect("/wp-login.php", 200, ('id="loginform"', 'id="wp-submit"', "Log In"))
    expect("/xmlrpc.php", 200, ("XML-RPC server accepts POST requests only.",))


deadline = time.monotonic() + timeout
last_error = None
while time.monotonic() < deadline:
    try:
        scenario()
        print("Wordpot HTTP probes succeeded")
        sys.exit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(1)

print(f"Wordpot HTTP probes failed: {last_error}", file=sys.stderr)
sys.exit(1)
PY
}

validate_wordpot_logs() {
  python3 - "${WORDPOT_LOG_FILE}" "${TOKEN}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

log_file = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])

required_keys = {
    "timestamp",
    "src_ip",
    "src_port",
    "dest_ip",
    "dest_port",
    "user_agent",
    "url",
    "plugin",
}

expected_events = [
    {
        "path": "/wp-login.php",
        "plugin": "badlogin",
        "extra": {"info": "enumeration"},
    },
    {
        "path": "/xmlrpc.php",
        "plugin": "commonfiles",
        "extra": {"filename": "xmlrpc.php"},
    },
]


class LogError(Exception):
    pass


def load_log():
    if not log_file.is_file():
        raise LogError(f"Missing Wordpot log file: {log_file}")

    text = log_file.read_text(encoding="utf-8", errors="replace")
    if "Honeypot started on 0.0.0.0:80" not in text:
        raise LogError("Wordpot startup marker is missing from wordpot.log")

    records = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LogError(f"Invalid JSON in {log_file}:{line_number}: {exc}") from exc
        if isinstance(record, dict):
            records.append(record)

    if not records:
        raise LogError(f"No JSON request records found in {log_file}")
    return records


def validate_record_shape(records):
    for record in records:
        missing = required_keys - set(record)
        if missing:
            raise LogError(f"Record is missing keys {sorted(missing)}: {record!r}")
        if str(record.get("dest_port")) != "80":
            raise LogError(f"Unexpected dest_port in Wordpot record: {record!r}")
        if not record.get("src_ip"):
            raise LogError(f"Missing src_ip in Wordpot record: {record!r}")


def matches_expected(record, event):
    if record.get("user_agent") != token:
        return False
    if record.get("plugin") != event["plugin"]:
        return False
    if urlsplit(record.get("url", "")).path != event["path"]:
        return False
    return all(record.get(key) == value for key, value in event["extra"].items())


def validate_once():
    records = load_log()
    validate_record_shape(records)

    missing = [
        f"{event['plugin']} {event['path']}"
        for event in expected_events
        if not any(matches_expected(record, event) for record in records)
    ]
    if missing:
        raise LogError("Missing Wordpot log events: " + ", ".join(missing))


deadline = time.monotonic() + timeout
last_error = None
while time.monotonic() < deadline:
    try:
        validate_once()
        print("Wordpot log validation succeeded")
        sys.exit(0)
    except LogError as exc:
        last_error = exc
        time.sleep(1)

print(f"Wordpot log validation failed: {last_error}", file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  docker logs "${TEST_CONTAINER_NAME}" > "${DOCKER_LOG_FILE}" 2>&1 || true

  python3 - "${WORDPOT_LOG_FILE}" "${DOCKER_LOG_FILE}" <<'PY'
import re
import sys
from pathlib import Path

patterns = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"Traceback",
        r"ModuleNotFoundError",
        r"ImportError",
        r"PermissionError",
        r"permission denied",
        r"Address already in use",
        r"Error occurred while processing request",
        r"Exception on /",
    )
]

for file_name in sys.argv[1:]:
    path = Path(file_name)
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in patterns:
        if pattern.search(text):
            print(f"Runtime error pattern {pattern.pattern!r} found in {path}", file=sys.stderr)
            sys.exit(1)
PY
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

  TOKEN="tpot-wordpot-smoke-$(date +%s)-$$"

  prepare_wordpot_harness
  test_enable_cleanup

  test_info "Starting isolated Wordpot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Wordpot container did not stay running"
  test_ok "Container is running"

  resolve_mapped_port

  test_info "Running Wordpot HTTP probes with token: ${TOKEN}"
  run_http_probes || test_die "Wordpot HTTP probes failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "Wordpot container stopped after HTTP probes"

  test_info "Waiting for Wordpot request logs"
  validate_wordpot_logs || test_die "Expected Wordpot request events were not found in wordpot.log"
  test_ok "Wordpot startup and request events were written to wordpot.log"

  assert_no_runtime_errors
  test_ok "No Wordpot runtime errors found in logs"

  test_ok "Wordpot post-build smoke test completed successfully"
}

main "$@"
