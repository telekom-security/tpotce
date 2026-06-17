#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="miniprint"
DEFAULT_IMAGE="dtagdevsec/miniprint:24.04.1"
IMAGE=""
RAW_PORT=""
LOG_DIR=""
UPLOAD_DIR=""
JSON_LOG_FILE=""
MAPPED_RAW_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Miniprint image.

Options:
  --image IMAGE      Image to test. Defaults to dtagdevsec/miniprint:24.04.1.
  --raw-port PORT    Host TCP port for raw printer traffic. Default: dynamic loopback port.
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
      --raw-port|--host-port|--port)
        [[ $# -ge 2 ]] || test_die "$1 requires an argument"
        RAW_PORT="$2"
        shift 2
        ;;
      --raw-port=*|--host-port=*|--port=*)
        RAW_PORT="${1#*=}"
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

  if [[ -n "${RAW_PORT}" ]]; then
    test_validate_port "${RAW_PORT}"
  fi
}

prepare_miniprint_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  UPLOAD_DIR="${TEST_TMP_ROOT}/uploads"
  JSON_LOG_FILE="${LOG_DIR}/miniprint.json"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${UPLOAD_DIR}"
  chmod 0777 "${LOG_DIR}" "${UPLOAD_DIR}"

  local port_mapping="${TEST_BIND_IP}::9100"
  if [[ -n "${RAW_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${RAW_PORT}:9100"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  miniprint:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/opt/miniprint/log"
      - "${UPLOAD_DIR}:/opt/miniprint/uploads"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_pjl_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_RAW_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
connect_timeout = max(1.0, min(float(timeout), 5.0))
deadline = time.monotonic() + timeout

payload = (
    "@PJL INFO ID\r\n"
    "@PJL INFO STATUS\r\n"
    f"@PJL ECHO {token}\r\n"
).encode("utf-8")

response = bytearray()

try:
    with socket.create_connection((host, port), timeout=connect_timeout) as sock:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)

        while time.monotonic() < deadline:
            remaining = max(0.1, min(1.0, deadline - time.monotonic()))
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            response.extend(chunk)
            if token.encode("utf-8") in response:
                break
except Exception as exc:
    print(f"Miniprint PJL probe failed: {exc}", file=sys.stderr)
    sys.exit(1)

checks = {
    "printer id": b"@PJL INFO ID\r\nhp LaserJet 4200\r\n" in response,
    "status code": b"CODE=10001" in response,
    "online status": b"ONLINE=True" in response,
    "echo token": f"@PJL ECHO {token}".encode("utf-8") in response,
}
missing = [name for name, ok in checks.items() if not ok]
if missing:
    preview = bytes(response[:300])
    print(f"Miniprint response missed {', '.join(missing)}: {preview!r}", file=sys.stderr)
    sys.exit(1)

print(f"Miniprint PJL probe succeeded for token {token}")
PY
}

run_pjl_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_pjl_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

run_raw_print_job_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_RAW_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
connect_timeout = max(1.0, min(float(timeout), 5.0))
deadline = time.monotonic() + timeout
payload = f"tpot-miniprint raw print job {token}\n".encode("utf-8")

try:
    with socket.create_connection((host, port), timeout=connect_timeout) as sock:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)

        while time.monotonic() < deadline:
            remaining = max(0.1, min(1.0, deadline - time.monotonic()))
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
except Exception as exc:
    print(f"Miniprint raw print job probe failed: {exc}", file=sys.stderr)
    sys.exit(1)

print(f"Miniprint raw print job probe sent token {token}")
PY
}

wait_for_uploaded_raw_print_job() {
  local token="$1"

  python3 - "${UPLOAD_DIR}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import sys
import time
from pathlib import Path

upload_dir = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None

while time.monotonic() < deadline:
    files = sorted(upload_dir.glob("*.txt"))
    if not files:
        last_error = f"No raw print job files found in {upload_dir}"
        time.sleep(1)
        continue

    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            last_error = f"Could not read {path}: {exc}"
            continue
        if token in text:
            print(f"Miniprint raw print job found in {path}")
            sys.exit(0)

    last_error = f"No raw print job file contains token {token}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

wait_for_json_log_events() {
  local pjl_token="$1"
  local raw_token="$2"

  python3 - "${JSON_LOG_FILE}" "${pjl_token}" "${raw_token}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

log_file = Path(sys.argv[1])
pjl_token = sys.argv[2]
raw_token = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
last_error = None


def load_events():
    if not log_file.exists():
        raise RuntimeError(f"{log_file} does not exist yet")

    events = []
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        raise RuntimeError(f"{log_file} is empty")

    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON in {log_file}:{line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise RuntimeError(f"JSON event in {log_file}:{line_number} is not an object")
        if "timestamp" not in event or "info" not in event:
            raise RuntimeError(f"Missing base log fields in {log_file}:{line_number}: {event!r}")
        events.append(event)

    if not events:
        raise RuntimeError(f"No JSON events found in {log_file}")
    return events


def has_event(events, **fields):
    return any(all(event.get(key) == value for key, value in fields.items()) for event in events)


def has_response(events, event_name, *needles):
    for event in events:
        if event.get("event") != event_name or event.get("action") != "response":
            continue
        response = event.get("response", "")
        if all(needle in response for needle in needles):
            return True
    return False


def has_raw_job_append(events):
    for event in events:
        if event.get("event") != "append_raw_print_job" or event.get("action") != "append":
            continue
        if raw_token in event.get("job_text", ""):
            return True
    return False


def validate_connection_fields(events):
    for event in events:
        if event.get("event") not in {
            "connection",
            "connection_closed",
            "command_received",
            "info_id",
            "info_status",
            "echo",
            "append_raw_print_job",
            "save_raw_print_job",
            "response_sent",
        }:
            continue
        if not event.get("src_ip"):
            raise RuntimeError(f"Missing src_ip in Miniprint connection event: {event!r}")
        if str(event.get("dest_port")) != "9100":
            raise RuntimeError(f"Unexpected dest_port in Miniprint connection event: {event!r}")


while time.monotonic() < deadline:
    try:
        events = load_events()
        validate_connection_fields(events)

        checks = {
            "server_start": has_event(events, event="server_start", action="start"),
            "connection_open": has_event(events, event="connection", action="open_conn"),
            "connection_closed": has_event(events, event="connection_closed", action="close_conn"),
            "info_id_response": has_response(events, "info_id", "hp LaserJet 4200"),
            "info_status_response": has_response(events, "info_status", "CODE=10001", "ONLINE=True"),
            "echo_response": has_response(events, "echo", pjl_token),
            "raw_job_append": has_raw_job_append(events),
            "raw_job_saved": has_event(events, event="save_raw_print_job", action="saving"),
        }
        missing = [name for name, ok in checks.items() if not ok]
        if not missing:
            print(f"Miniprint JSON events found in {log_file}")
            sys.exit(0)
        last_error = "Missing Miniprint log events: " + ", ".join(missing)
    except RuntimeError as exc:
        last_error = str(exc)

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local docker_log_file="${TEST_TMP_ROOT}/docker-logs.txt"

  test_compose logs --no-color > "${docker_log_file}" 2>/dev/null || true

  python3 - "${LOG_DIR}" "${docker_log_file}" <<'PY'
import json
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
    )
]

log_dir = Path(sys.argv[1])
docker_log_file = Path(sys.argv[2])
paths = []

if log_dir.exists():
    paths.extend(path for path in log_dir.rglob("*") if path.is_file())
if docker_log_file.exists():
    paths.append(docker_log_file)

for path in paths:
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in patterns:
        if pattern.search(text):
            print(f"Runtime error pattern {pattern.pattern!r} found in {path}", file=sys.stderr)
            sys.exit(1)

    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event") == "error":
            print(f"Miniprint error event found in {path}:{line_number}: {event!r}", file=sys.stderr)
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

  if [[ -n "${RAW_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${RAW_PORT}" || test_die "${TEST_BIND_IP}:${RAW_PORT} is already in use. Try --raw-port <free-port>."
  fi

  prepare_miniprint_harness
  test_enable_cleanup

  test_info "Starting isolated Miniprint container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Miniprint container did not stay running"
  test_ok "Container is running"

  MAPPED_RAW_PORT="$(test_get_mapped_port "${TEST_NAME}" "9100")" || test_die "Could not resolve mapped host port for 9100/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_RAW_PORT} maps to container port 9100/tcp"

  local pjl_token="miniprint-pjl-$(date +%s)-$$"
  local raw_token="miniprint-raw-$(date +%s)-$$"

  test_info "Running Miniprint PJL probe with token: ${pjl_token}"
  run_pjl_probe_with_retries "${pjl_token}" || test_die "Miniprint PJL probe failed on ${TEST_BIND_IP}:${MAPPED_RAW_PORT}"
  test_wait_for_container || test_die "Miniprint container stopped after PJL probe"

  test_info "Running Miniprint raw print job probe with token: ${raw_token}"
  run_raw_print_job_probe "${raw_token}" || test_die "Miniprint raw print job probe failed on ${TEST_BIND_IP}:${MAPPED_RAW_PORT}"
  test_wait_for_container || test_die "Miniprint container stopped after raw print job probe"

  test_info "Waiting for Miniprint raw print job upload"
  wait_for_uploaded_raw_print_job "${raw_token}" || test_die "Expected Miniprint raw print job was not written to uploads"
  test_ok "Miniprint raw print job was written to uploads"

  test_info "Waiting for Miniprint JSON log events"
  wait_for_json_log_events "${pjl_token}" "${raw_token}" || test_die "Expected Miniprint events were not found in miniprint.json"
  test_ok "Miniprint protocol and print job events were written to miniprint.json"

  assert_no_runtime_errors
  test_ok "No Miniprint runtime errors found in logs"

  test_ok "Miniprint post-build smoke test completed successfully"
}

main "$@"
