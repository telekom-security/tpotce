#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="miniprint"
DEFAULT_IMAGE="dtagdevsec/miniprint:24.04.2"
IMAGE=""
RAW_PORT=""
HTTP_PORT=""
LOG_DIR=""
UPLOAD_DIR=""
DATA_DIR=""
JSON_LOG_FILE=""
MAPPED_RAW_PORT=""
MAPPED_HTTP_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Miniprint image.

Options:
  --image IMAGE      Image to test. Defaults to dtagdevsec/miniprint:24.04.2.
  --raw-port PORT    Host TCP port for PJL / raw printer traffic. Default: dynamic loopback port.
  --http-port PORT   Host TCP port for the web admin interface. Default: dynamic loopback port.
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

  if [[ -n "${RAW_PORT}" ]]; then
    test_validate_port "${RAW_PORT}"
  fi
  if [[ -n "${HTTP_PORT}" ]]; then
    test_validate_port "${HTTP_PORT}"
  fi
}

prepare_miniprint_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  UPLOAD_DIR="${TEST_TMP_ROOT}/uploads"
  DATA_DIR="${TEST_TMP_ROOT}/data"
  JSON_LOG_FILE="${LOG_DIR}/miniprint.json"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${UPLOAD_DIR}" "${DATA_DIR}"
  chmod 0777 "${LOG_DIR}" "${UPLOAD_DIR}" "${DATA_DIR}"

  local raw_mapping="${TEST_BIND_IP}::9100"
  if [[ -n "${RAW_PORT}" ]]; then
    raw_mapping="${TEST_BIND_IP}:${RAW_PORT}:9100"
  fi
  local http_mapping="${TEST_BIND_IP}::8000"
  if [[ -n "${HTTP_PORT}" ]]; then
    http_mapping="${TEST_BIND_IP}:${HTTP_PORT}:8000"
  fi

  # the Brother persona is the only one with the full lure chain; the image's
  # own healthcheck runs, only more often than in T-Pot
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  miniprint:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      MINIPRINT_PERSONA: "brother"
    healthcheck:
      interval: 2s
      start_period: 5s
    ports:
      - "${raw_mapping}"
      - "${http_mapping}"
    volumes:
      - "${DATA_DIR}:/opt/miniprint/data"
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
    "printer id": b"@PJL INFO ID\r\nBrother MFC-L9570CDW\r\n" in response,
    "status code": b"CODE=10001" in response,
    "online status": b"ONLINE=TRUE" in response,
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

run_postscript_job_probe() {
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
uel = b"\x1b%-12345X"
payload = (
    uel
    + b"@PJL JOB NAME=\"tpot\"\r\n"
    + b"@PJL ENTER LANGUAGE=POSTSCRIPT\r\n"
    + f"%!PS-Adobe-3.0\n%% {token}\nshowpage\n".encode("utf-8")
    + uel
    + b"@PJL EOJ\r\n"
    + uel
)

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
    print(f"Miniprint PostScript job probe failed: {exc}", file=sys.stderr)
    sys.exit(1)

print(f"Miniprint PostScript job probe sent token {token}")
PY
}

wait_for_uploaded_postscript_job() {
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
    files = sorted(upload_dir.glob("*.ps"))
    if not files:
        last_error = f"No PostScript job files found in {upload_dir}"
        time.sleep(1)
        continue

    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            last_error = f"Could not read {path}: {exc}"
            continue
        if token in text:
            print(f"Miniprint PostScript job found in {path}")
            sys.exit(0)

    last_error = f"No PostScript job file contains token {token}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

read_serial() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${TEST_TIMEOUT}" <<'PY'
import csv
import http.client
import io
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None

while time.monotonic() < deadline:
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/etc/mnt_info.csv")
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        server = response.getheader("Server", "")
        conn.close()
    except OSError as exc:
        last_error = f"Miniprint web admin not reachable: {exc}"
        time.sleep(1)
        continue
    if response.status != 200 or server != "Debut/1.30":
        print(f"Unexpected serial CSV answer: status={response.status} server={server!r}", file=sys.stderr)
        sys.exit(1)
    rows = list(csv.DictReader(io.StringIO(body)))
    if not rows or not rows[0].get("Serial No."):
        print(f"No serial number in {body!r}", file=sys.stderr)
        sys.exit(1)
    print(rows[0]["Serial No."])
    sys.exit(0)

print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

run_http_lure_chain() {
  local password="$1"
  local token="$2"

  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${password}" "${token}" <<'PY'
import http.client
import json
import sys
from urllib.parse import urlencode

host = sys.argv[1]
port = int(sys.argv[2])
password = sys.argv[3]
token = sys.argv[4]
form = {"Content-Type": "application/x-www-form-urlencoded"}


def post(path, params, headers):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("POST", path, body=urlencode(params), headers=headers)
        response = conn.getresponse()
        return response.status, response.read(), response.getheader("Set-Cookie", "")
    finally:
        conn.close()


status, _, cookie = post("/login", {"username": "admin", "password": password}, form)
if status != 200 or not cookie.startswith("AuthCookie="):
    print(f"Default password login failed: status={status} cookie={cookie!r}", file=sys.stderr)
    sys.exit(1)

headers = dict(form, Cookie=cookie.split(";", 1)[0])
status, body, _ = post("/admin/ldap", {"server": f"{token}.example.test", "password": "tpot-secret"}, headers)
if status != 200 or json.loads(body).get("status") != "saved":
    print(f"LDAP settings were not accepted: status={status} body={body!r}", file=sys.stderr)
    sys.exit(1)

print("Miniprint login with the derived default password and LDAP passback succeeded")
PY
}

# identity.json is 0600 for uid 2000, so it is read inside the container
assert_identity_persisted() {
  local serial="$1"
  local identity=""

  identity="$(docker exec "${TEST_CONTAINER_NAME}" cat data/identity.json)" || return 1
  python3 - "${serial}" "${identity}" <<'PY'
import json
import sys

serial = sys.argv[1]
identity = json.loads(sys.argv[2])
if identity.get("persona") != "brother" or identity.get("serial") != serial:
    print(f"Unexpected identity: {identity!r}", file=sys.stderr)
    sys.exit(1)
PY
}

wait_for_healthy() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local health=""

  while (( SECONDS < deadline )); do
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "${TEST_CONTAINER_NAME}" 2>/dev/null || true)"
    case "${health}" in
      healthy)
        return 0
        ;;
      unhealthy|"")
        printf 'Container health: %s\n' "${health:-none}" >&2
        return 1
        ;;
    esac
    sleep 1
  done

  printf 'Container health: %s\n' "${health}" >&2
  return 1
}

wait_for_json_log_events() {
  local pjl_token="$1"
  local ps_token="$2"
  local http_token="$3"
  local password="$4"

  python3 - "${JSON_LOG_FILE}" "${UPLOAD_DIR}" "${pjl_token}" "${ps_token}" "${http_token}" "${password}" "${TEST_TIMEOUT}" <<'PY'
import hashlib
import json
import sys
import time
from pathlib import Path

log_file = Path(sys.argv[1])
upload_dir = Path(sys.argv[2])
pjl_token = sys.argv[3]
ps_token = sys.argv[4]
http_token = sys.argv[5]
password = sys.argv[6]
timeout = int(sys.argv[7])
deadline = time.monotonic() + timeout
last_error = None
deprecated = {"fields", "dst_port", "level", "dir", "response", "job_text"}


def load_events():
    if not log_file.exists():
        raise RuntimeError(f"{log_file} does not exist yet")

    text = log_file.read_text(encoding="utf-8", errors="replace")
    if password in text or "tpot-secret" in text:
        raise RuntimeError(f"A password was logged in plain text in {log_file}")

    events = []
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON in {log_file}:{line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise RuntimeError(f"JSON event in {log_file}:{line_number} is not an object")
        if "timestamp" not in event or "info" not in event or "event" not in event:
            raise RuntimeError(f"Missing base log fields in {log_file}:{line_number}: {event!r}")
        old = deprecated & event.keys()
        if old:
            raise RuntimeError(f"Deprecated keys {sorted(old)} in {log_file}:{line_number}: {event!r}")
        events.append(event)

    if not events:
        raise RuntimeError(f"No JSON events found in {log_file}")
    return events


def validate_session_fields(events):
    for event in events:
        if event.get("persona") != "brother":
            raise RuntimeError(f"Unexpected persona in Miniprint event: {event!r}")
        if not event.get("session_id") or not event.get("src_ip"):
            raise RuntimeError(f"Missing session_id or src_ip in Miniprint event: {event!r}")
        expected_port = {"pjl": 9100, "http": 8000}.get(event.get("protocol"))
        if expected_port is None or event.get("dest_port") != expected_port:
            raise RuntimeError(f"Unexpected protocol or dest_port in Miniprint event: {event!r}")


def find(events, **fields):
    return [event for event in events if all(event.get(key) == value for key, value in fields.items())]


def postscript_saved(events):
    for event in find(events, event="save_print_job", artifact_type="ps", language="POSTSCRIPT"):
        path = upload_dir / event.get("file_name", "")
        if not path.is_file():
            continue
        data = path.read_bytes()
        if ps_token.encode() in data and hashlib.sha256(data).hexdigest() == event.get("payload_sha256") \
                and event.get("size") == len(data):
            return True
    return False


def sessions_closed(events):
    sessions = {event["session_id"] for event in events}
    closed = {event["session_id"] for event in events if event.get("session_end") and "session_duration" in event}
    return sessions - closed


while time.monotonic() < deadline:
    try:
        events = load_events()
        validate_session_fields(events)

        checks = {
            "pjl_connection": bool(find(events, event="connection", action="open_conn", protocol="pjl")),
            "pjl_closed": bool(find(events, event="connection_closed", action="close_conn", protocol="pjl")),
            "info_command": bool(find(events, event="command_received", command="INFO")),
            "echo": bool(find(events, event="echo")),
            "print_language": bool(find(events, event="print_job", language="POSTSCRIPT")),
            "postscript_saved": postscript_saved(events),
            "serial_leak_probe": bool(find(events, event="serial_leak_probe", cve_hint="CVE-2024-51977")),
            "default_password_success": bool(find(
                events, event="default_password_success", username="admin", secret_supplied=True,
                cve_hint="CVE-2024-51978")),
            "passback_attempt": bool(find(
                events, event="passback_attempt", cve_hint="CVE-2024-51984", secret_supplied=True,
                passback_target=f"{http_token}.example.test",
                form_fields=[{"name": "server", "value": f"{http_token}.example.test"}])),
            "http_closed": bool(find(events, event="http_connection_closed", protocol="http")),
        }
        missing = [name for name, ok in checks.items() if not ok]
        open_sessions = sessions_closed(events)
        if open_sessions:
            missing.append(f"session_end of {len(open_sessions)} session(s)")
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
        r"Read-only file system",
    )
]
error_events = {"session_error", "artifact_error", "http_error", "command_error", "identity_ephemeral"}

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
        start = line.find("{")
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event") in error_events:
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
  if [[ -n "${HTTP_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${HTTP_PORT}" || test_die "${TEST_BIND_IP}:${HTTP_PORT} is already in use. Try --http-port <free-port>."
  fi

  prepare_miniprint_harness
  test_enable_cleanup

  test_info "Starting isolated Miniprint container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Miniprint container did not stay running"
  test_ok "Container is running"

  MAPPED_RAW_PORT="$(test_get_mapped_port "${TEST_NAME}" "9100")" || test_die "Could not resolve mapped host port for 9100/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_RAW_PORT} maps to container port 9100/tcp"
  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "8000")" || test_die "Could not resolve mapped host port for 8000/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTP_PORT} maps to container port 8000/tcp"

  local pjl_token="miniprint-pjl-$(date +%s)-$$"
  local ps_token="miniprint-ps-$(date +%s)-$$"
  local http_token="miniprint-http-$(date +%s)-$$"

  test_info "Running Miniprint PJL probe with token: ${pjl_token}"
  run_pjl_probe_with_retries "${pjl_token}" || test_die "Miniprint PJL probe failed on ${TEST_BIND_IP}:${MAPPED_RAW_PORT}"
  test_wait_for_container || test_die "Miniprint container stopped after PJL probe"

  test_info "Running Miniprint PostScript job probe with token: ${ps_token}"
  run_postscript_job_probe "${ps_token}" || test_die "Miniprint PostScript job probe failed on ${TEST_BIND_IP}:${MAPPED_RAW_PORT}"
  wait_for_uploaded_postscript_job "${ps_token}" || test_die "Expected Miniprint PostScript job was not written to uploads"
  test_ok "Miniprint PostScript job was written to uploads"

  test_info "Reading the serial number from the web admin interface"
  local serial=""
  serial="$(read_serial)" || test_die "Miniprint serial number CSV failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_ok "Serial number leaked: ${serial}"

  # the default password comes from the image's own routine, as an attacker would derive it
  local password=""
  password="$(docker exec "${TEST_CONTAINER_NAME}" python -c 'import sys; from brother import brother_default_password; print(brother_default_password(sys.argv[1]))' "${serial}")" \
    || test_die "Could not derive the default password in the container"

  test_info "Running the Brother login and LDAP passback with token: ${http_token}"
  run_http_lure_chain "${password}" "${http_token}" || test_die "Miniprint web admin lure chain failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "Miniprint container stopped after the web admin probes"

  test_info "Waiting for Miniprint JSON log events"
  wait_for_json_log_events "${pjl_token}" "${ps_token}" "${http_token}" "${password}" || test_die "Expected Miniprint events were not found in miniprint.json"
  test_ok "Miniprint PJL, print job and web admin events were written to miniprint.json"

  [[ -s "${DATA_DIR}/identity.json" ]] || test_die "Miniprint identity was not written to the data volume"
  assert_identity_persisted "${serial}" || test_die "Miniprint identity does not match the leaked serial number"
  test_ok "Miniprint identity is persisted in the data volume"

  test_info "Waiting for the image healthcheck"
  wait_for_healthy || test_die "Miniprint container did not become healthy"
  test_ok "Container is healthy"

  assert_no_runtime_errors
  test_ok "No Miniprint runtime errors found in logs"

  test_ok "Miniprint post-build smoke test completed successfully"
}

main "$@"
