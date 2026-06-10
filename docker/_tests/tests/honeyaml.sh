#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="honeyaml"
DEFAULT_IMAGE="dtagdevsec/honeyaml:24.04.1"
IMAGE=""
HTTP_PORT=""
LOG_DIR=""
API_FILE=""
HONEYAML_LOG_FILE=""
DOCKER_LOG_FILE=""
CONTAINER_USER=""
MAPPED_HTTP_PORT=""
TOKEN=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Honeyaml image.

Options:
  --image IMAGE      Image to test. Defaults to docker/honeyaml/docker-compose.yml.
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

prepare_honeyaml_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  API_FILE="${TEST_TMP_ROOT}/api.yml"
  HONEYAML_LOG_FILE="${LOG_DIR}/honeyaml.log"
  DOCKER_LOG_FILE="${LOG_DIR}/docker.log"
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
  honeyaml:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "${CONTAINER_USER}"
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/opt/honeyaml/log"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

extract_api_config() {
  docker run --rm --entrypoint /bin/sh "${IMAGE}" -c 'cat /opt/honeyaml/api.yml' > "${API_FILE}" \
    || test_die "Could not extract /opt/honeyaml/api.yml from ${IMAGE}"
  [[ -s "${API_FILE}" ]] || test_die "Extracted api.yml is empty"
  cp "${API_FILE}" "${LOG_DIR}/api.yml"
}

validate_api_config() {
  python3 - "${API_FILE}" <<'PY'
import sys
from pathlib import Path

api_file = Path(sys.argv[1])
lines = api_file.read_text().splitlines()
entries = []
current = None

for line in lines:
    stripped = line.strip()
    if stripped.startswith("- path: "):
        if current:
            entries.append(current)
        current = {"path": stripped.removeprefix("- path: ").strip()}
    elif current and ": " in stripped:
        key, value = stripped.split(": ", 1)
        if key in {"path_type", "method", "auth_required", "return_code", "authorization"}:
            current[key] = value.strip()

if current:
    entries.append(current)

expected = [
    ("/auth", "POST", "authenticator", None, None),
    ("/health", "GET", "rest", "false", "200"),
    ("/metrics", "GET", "rest", "false", "200"),
    ("/api/v1/debug/vars", "GET", "rest", "false", "200"),
    ("/api/v1/internal/status", "GET", "rest", "false", "200"),
    ("/api/v1/tokens/exchange", "POST", "rest", "false", "200"),
    ("/api/v1/ci/runners/register", "POST", "rest", "false", "201"),
    ("/users/me", "GET", "rest", "true", "200"),
    ("/api/v1/config/environment", "GET", "rest", "true", "200"),
    ("/api/v1/secrets", "GET", "rest", "true", "200"),
    ("/api/v1/admin/audit-logs", "GET", "rest", "true", "200"),
]

def matches(entry, path, method, path_type, auth_required, return_code):
    if entry.get("path") != path or entry.get("method") != method:
        return False
    if path_type is not None and entry.get("path_type") != path_type:
        return False
    if auth_required is not None and entry.get("auth_required") != auth_required:
        return False
    if return_code is not None and entry.get("return_code") != return_code:
        return False
    return True

missing = [
    f"{method} {path}"
    for path, method, path_type, auth_required, return_code in expected
    if not any(matches(entry, path, method, path_type, auth_required, return_code) for entry in entries)
]
if missing:
    print("api.yml is missing expected routes: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)

api_text = api_file.read_text()
for needle in ("username: user", "password: 123456", "authorization: jwt"):
    if needle not in api_text:
        print(f"api.yml is missing expected auth setting: {needle}", file=sys.stderr)
        sys.exit(1)

print("api.yml route validation succeeded")
PY
}

resolve_mapped_port() {
  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" 8080)" \
    || test_die "Could not resolve mapped host port for 8080/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTP_PORT} maps to Honeyaml container port 8080/tcp"
}

run_http_probes() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${TOKEN}" "${TEST_TIMEOUT}" <<'PY'
import http.client
import json
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
connect_timeout = max(1.0, min(float(timeout), 5.0))


class ProbeError(Exception):
    pass


def request(method, path, body=None, headers=None):
    request_headers = {
        "User-Agent": f"tpot-honeyaml-smoke/{token}",
    }
    if headers:
        request_headers.update(headers)

    conn = http.client.HTTPConnection(host, port, timeout=connect_timeout)
    try:
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        return response.status, raw
    finally:
        conn.close()


def request_json(method, path, expected_status, body=None, headers=None):
    status, raw = request(method, path, body=body, headers=headers)
    if status != expected_status:
        raise ProbeError(f"{method} {path} returned HTTP {status}: {raw[:200]}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"{method} {path} did not return JSON: {exc}: {raw[:200]}") from exc


def expect_text(method, path, expected_status, expected_text=None, headers=None):
    status, raw = request(method, path, headers=headers)
    if status != expected_status:
        raise ProbeError(f"{method} {path} returned HTTP {status}: {raw[:200]}")
    if expected_text is not None and raw.strip() != expected_text:
        raise ProbeError(f"{method} {path} returned unexpected body: {raw[:200]!r}")
    return raw


def scenario():
    expect_text("GET", "/health", 200, "OK")

    metrics = request_json("GET", "/metrics", 200)
    if "requests" not in metrics or "errors" not in metrics:
        raise ProbeError(f"Unexpected /metrics payload: {metrics!r}")

    debug_vars = request_json("GET", f"/api/v1/debug/vars?smoke={token}", 200)
    if debug_vars.get("env", {}).get("APP_ENV") != "production":
        raise ProbeError(f"Unexpected /api/v1/debug/vars payload: {debug_vars!r}")

    internal_status = request_json("GET", f"/api/v1/internal/status?smoke={token}", 200)
    if internal_status.get("database") != "ok":
        raise ProbeError(f"Unexpected /api/v1/internal/status payload: {internal_status!r}")

    exchange = request_json("POST", "/api/v1/tokens/exchange", 200, body="{}",
                            headers={"Content-Type": "application/json"})
    if exchange.get("token_type") != "Bearer":
        raise ProbeError(f"Unexpected /api/v1/tokens/exchange payload: {exchange!r}")

    runner = request_json("POST", "/api/v1/ci/runners/register", 201, body="{}",
                          headers={"Content-Type": "application/json"})
    if "registration_token" not in runner:
        raise ProbeError(f"Unexpected /api/v1/ci/runners/register payload: {runner!r}")

    auth_body = json.dumps({"username": "user", "password": "123456"})
    status, jwt = request(
        "POST",
        "/auth",
        body=auth_body,
        headers={"Content-Type": "application/json"},
    )
    if status != 200:
        raise ProbeError(f"POST /auth returned HTTP {status}: {jwt[:200]}")
    if len(jwt.strip().split(".")) != 3:
        raise ProbeError(f"POST /auth did not return a JWT-like token: {jwt[:120]!r}")

    status, raw = request("GET", "/users/me")
    if status != 401:
        raise ProbeError(f"GET /users/me without token returned HTTP {status}: {raw[:200]}")

    auth_headers = {"Authorization": f"Bearer {jwt.strip()}"}

    me = request_json("GET", "/users/me", 200, headers=auth_headers)
    if me.get("username") != "user":
        raise ProbeError(f"Unexpected /users/me payload: {me!r}")

    environment = request_json("GET", "/api/v1/config/environment", 200, headers=auth_headers)
    if "aws_access_key_id" not in environment:
        raise ProbeError(f"Unexpected /api/v1/config/environment payload: {environment!r}")

    secrets = request_json("GET", "/api/v1/secrets", 200, headers=auth_headers)
    if not any(item.get("name") == "prod/postgres/app" for item in secrets):
        raise ProbeError(f"Unexpected /api/v1/secrets payload: {secrets!r}")

    audit = request_json("GET", "/api/v1/admin/audit-logs", 200, headers=auth_headers)
    if not any(item.get("action") == "secrets.export" for item in audit):
        raise ProbeError(f"Unexpected /api/v1/admin/audit-logs payload: {audit!r}")


deadline = time.monotonic() + timeout
last_error = None
while time.monotonic() < deadline:
    try:
        scenario()
        print("Honeyaml HTTP probes succeeded")
        sys.exit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(1)

print(f"Honeyaml HTTP probes failed: {last_error}", file=sys.stderr)
sys.exit(1)
PY
}

validate_honeyaml_logs() {
  python3 - "${HONEYAML_LOG_FILE}" "${MAPPED_HTTP_PORT}" "${TOKEN}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

log_file = Path(sys.argv[1])
mapped_port = sys.argv[2]
token = sys.argv[3]
timeout = int(sys.argv[4])

required_keys = {
    "timestamp",
    "level",
    "src_ip",
    "path",
    "method",
    "status_code",
    "host",
    "dest_port",
    "target",
}

expected_events = [
    ("GET", "/health", 200),
    ("GET", "/metrics", 200),
    ("GET", "/api/v1/debug/vars", 200),
    ("GET", "/api/v1/internal/status", 200),
    ("POST", "/api/v1/tokens/exchange", 200),
    ("POST", "/api/v1/ci/runners/register", 201),
    ("POST", "/auth", 200),
    ("GET", "/users/me", 401),
    ("GET", "/users/me", 200),
    ("GET", "/api/v1/config/environment", 200),
    ("GET", "/api/v1/secrets", 200),
    ("GET", "/api/v1/admin/audit-logs", 200),
]
query_paths = {"/api/v1/debug/vars", "/api/v1/internal/status"}
authorized_paths = {
    "/users/me",
    "/api/v1/config/environment",
    "/api/v1/secrets",
    "/api/v1/admin/audit-logs",
}


class LogError(Exception):
    pass


def load_records():
    if not log_file.is_file():
        raise LogError(f"Missing Honeyaml log file: {log_file}")

    records = []
    for index, line in enumerate(log_file.read_text(errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LogError(f"Could not parse {log_file}:{index}: {exc}") from exc
        if isinstance(record, dict):
            records.append(record)
    if not records:
        raise LogError(f"No records found in {log_file}")
    return records


def matching_records(records, method, path, status):
    return [
        record
        for record in records
        if record.get("method") == method
        and record.get("path") == path
        and str(record.get("status_code")) == str(status)
    ]


def validate_once():
    records = load_records()

    for record in records:
        missing = required_keys - set(record)
        if missing:
            raise LogError(f"Record is missing keys {sorted(missing)}: {record!r}")
        if record.get("target") != "honeyaml::access-log":
            raise LogError(f"Unexpected target in Honeyaml log: {record!r}")
        if str(record.get("dest_port")) != mapped_port:
            raise LogError(f"Unexpected dest_port in Honeyaml log: {record!r}")

    for method, path, status in expected_events:
        matches = matching_records(records, method, path, status)
        if not matches:
            raise LogError(f"Missing log event for {method} {path} HTTP {status}")

        for record in matches:
            if path in query_paths and record.get("query_string") != f"smoke={token}":
                raise LogError(f"Missing smoke query string for {path}: {record!r}")

        if path == "/auth" and status == 200:
            auth_record = matches[-1]
            if auth_record.get("content_type") != "application/json":
                raise LogError(f"Missing JSON content_type on /auth log: {auth_record!r}")
            if "content_length" not in auth_record:
                raise LogError(f"Missing content_length on /auth log: {auth_record!r}")
            body = auth_record.get("body", "")
            if "username:user" not in body or "password:123456" not in body:
                raise LogError(f"Missing credentials in /auth body log: {auth_record!r}")

        if path in authorized_paths and status == 200:
            if not any(str(record.get("authorization", "")).startswith("Bearer ") for record in matches):
                raise LogError(f"Missing Bearer authorization log for {path}")


deadline = time.monotonic() + timeout
last_error = None
while time.monotonic() < deadline:
    try:
        validate_once()
        print("Honeyaml log validation succeeded")
        sys.exit(0)
    except LogError as exc:
        last_error = exc
        time.sleep(1)

print(f"Honeyaml log validation failed: {last_error}", file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  docker logs "${TEST_CONTAINER_NAME}" > "${DOCKER_LOG_FILE}" 2>&1 || true

  python3 - "${DOCKER_LOG_FILE}" "${HONEYAML_LOG_FILE}" <<'PY'
import re
import sys
from pathlib import Path

patterns = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"can not bind to address",
        r"cannot setup logger",
        r"couldn't create writer for logs",
        r"failed to create appender",
        r"permission denied",
        r"\bpanic(?:ked)?\b",
    )
]

for file_name in sys.argv[1:]:
    path = Path(file_name)
    if not path.exists():
        continue
    text = path.read_text(errors="replace")
    for pattern in patterns:
        if pattern.search(text):
            print(f"Runtime error pattern {pattern.pattern!r} found in {path}", file=sys.stderr)
            sys.exit(1)
PY
}

main() {
  parse_args "$@"
  validate_args

  IMAGE="${IMAGE:-$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")}"
  TOKEN="smoke-$(date +%s)-$$"

  test_check_dependencies
  test_require_image "${IMAGE}" "docker compose -f docker/honeyaml/docker-compose.yml build honeyaml"

  test_enable_cleanup
  prepare_honeyaml_harness
  extract_api_config
  validate_api_config

  test_info "Starting Honeyaml smoke container from ${IMAGE}"
  test_compose up -d >/dev/null
  test_wait_for_container || test_die "Honeyaml container did not stay running"

  resolve_mapped_port
  run_http_probes
  validate_honeyaml_logs
  assert_no_runtime_errors

  test_ok "Honeyaml smoke test passed"
}

main "$@"
