#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="wordpot"
DEFAULT_IMAGE="dtagdevsec/wordpot:24.04.2"
IMAGE=""
HTTP_PORT=""
LOG_DIR=""
WORDPOT_LOG_FILE=""
RUNTIME_LOG_FILE=""
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
  WORDPOT_LOG_FILE="${LOG_DIR}/wordpot.json"
  RUNTIME_LOG_FILE="${LOG_DIR}/wordpot-runtime.log"
  DOCKER_LOG_FILE="${TEST_TMP_ROOT}/docker.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  local port_mapping="${TEST_BIND_IP}::80"
  if [[ -n "${HTTP_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${HTTP_PORT}:80"
  fi

  # As in T-Pot: read only with a tmpfs for gunicorn and werkzeug. One fixed
  # profile keeps the expected markup stable; the image's own healthcheck runs,
  # only more often than in T-Pot.
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  wordpot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      WORDPOT_PROFILE_ID: "modern-business"
    healthcheck:
      interval: 2s
      start_period: 5s
    tmpfs:
      - /tmp:uid=2000,gid=2000
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

run_http_probes() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${TOKEN}" "${TEST_TIMEOUT}" <<'PY'
import http.client
import sys
import time
import uuid

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
connect_timeout = max(1.0, min(float(timeout), 5.0))


class ProbeError(Exception):
    pass


def request(method, path, body=None, headers=None):
    conn = http.client.HTTPConnection(host, port, timeout=connect_timeout)
    try:
        all_headers = {"User-Agent": token}
        all_headers.update(headers or {})
        conn.request(method, path, body=body, headers=all_headers)
        response = conn.getresponse()
        text = response.read().decode("utf-8", errors="replace")
        return response, text
    finally:
        conn.close()


def expect(method, path, status, required_text=(), body=None, headers=None):
    response, text = request(method, path, body, headers)
    if response.status != status:
        raise ProbeError(f"{method} {path} returned HTTP {response.status}, expected {status}: {text[:200]!r}")
    missing = [item for item in required_text if item not in text]
    if missing:
        raise ProbeError(f"{method} {path} response missed {missing!r}: {text[:200]!r}")
    return response


def xmlrpc_call(method, *params):
    values = "".join(f"<param><value><string>{value}</string></value></param>" for value in params)
    return f'<?xml version="1.0"?><methodCall><methodName>{method}</methodName><params>{values}</params></methodCall>'


def multicall(*pairs):
    calls = "".join(
        "<value><struct>"
        "<member><name>methodName</name><value><string>wp.getUsersBlogs</string></value></member>"
        "<member><name>params</name><value><array><data>"
        f"<value><string>{user}</string></value><value><string>{password}</string></value>"
        "</data></array></value></member>"
        "</struct></value>"
        for user, password in pairs
    )
    return ('<?xml version="1.0"?><methodCall><methodName>system.multicall</methodName><params>'
            f"<param><value><array><data>{calls}</data></array></value></param></params></methodCall>")


def upload_body(filename):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
        f"<?php echo '{token}'; ?>\r\n"
        f"--{boundary}--\r\n"
    )
    return body.encode(), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def scenario():
    response = expect("GET", "/", 200, ('<meta name="generator" content="WordPress 7.0" />', "/wp-json/"))
    servers = response.msg.get_all("Server") or []
    if len(servers) != 1 or "gunicorn" in servers[0].lower():
        raise ProbeError(f"GET / sent Server headers {servers!r}, expected one profile header")

    expect("GET", "/wp-login.php", 200, ('id="loginform"', 'id="wp-submit"'))
    expect("POST", "/wp-login.php", 200, body=f"log=admin&pwd={token}-pw&wp-submit=Log+In",
           headers={"Content-Type": "application/x-www-form-urlencoded"})
    expect("POST", "/xmlrpc.php", 200, ("<methodResponse>",), body=multicall(("admin", f"{token}-mc")),
           headers={"Content-Type": "text/xml"})
    expect("POST", "/xmlrpc.php", 200, ("faultCode",), body=xmlrpc_call("wp.getUsersBlogs", "editor", f"{token}-x"),
           headers={"Content-Type": "text/xml"})
    expect("GET", "/wp-json/wp/v2/users", 200, ('"slug": "admin"',))
    expect("GET", "/wp-content/plugins/elementor/readme.txt", 200, ("Elementor",))
    expect("GET", "/.env", 200)
    expect("GET", f"/wp-admin/admin-ajax.php?action=duplicator_download&url=http://{token}/", 200)
    expect("GET", "/wp-admin/admin-ajax.php?action=heartbeat", 200)
    body, headers = upload_body(f"{token}.php")
    expect("POST", "/wp-content/uploads/2026/10/report.php", 404, body=body, headers=headers)
    expect("GET", "/readme.html", 200, headers={"Host": "example.org:8080"})


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
  python3 - "${LOG_DIR}" "${TOKEN}" "${TEST_TIMEOUT}" <<'PY'
import hashlib
import json
import sys
import time
from pathlib import Path

log_dir = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
log_file = log_dir / "wordpot.json"
runtime_log = log_dir / "wordpot-runtime.log"

required_keys = {
    "timestamp", "request_id", "profile_id", "src_ip", "src_port", "dest_port", "user_agent",
    "url", "method", "path", "component_type", "technique", "payload_size", "payload_stored",
    "response_status",
}

# technique, path and the fields each event must carry
expected_events = [
    ("credential_attempt", "/wp-login.php", {"username": "admin", "password": f"{token}-pw"}),
    ("xmlrpc_multicall", "/xmlrpc.php", {}),
    ("xmlrpc_login", "/xmlrpc.php", {"username": "editor", "password": f"{token}-x"}),
    ("rest_user_enumeration", "/wp-json/wp/v2/users", {}),
    ("plugin_probe", "/wp-content/plugins/elementor/readme.txt", {"component_slug": "elementor"}),
    ("config_bait_served", "/.env", {}),
    ("admin_ajax_action", "/wp-admin/admin-ajax.php", {}),
    ("upload_lure_payload", "/wp-content/uploads/2026/10/report.php", {"component_type": "upload"}),
    ("core_version_probe", "/readme.html", {}),
]


class LogError(Exception):
    pass


def load_records():
    if not log_file.is_file():
        raise LogError(f"Missing Wordpot event log: {log_file}")
    records = []
    for line_number, line in enumerate(log_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise LogError(f"Invalid JSON in {log_file}:{line_number}: {exc}") from exc
    return records


def find(records, technique, path, query=None):
    for record in records:
        if record.get("user_agent") == token and record.get("technique") == technique and record.get("path") == path:
            if query is None or record.get("query") == query:
                return record
    raise LogError(f"Missing Wordpot event {technique} {path} {query or ''}".rstrip())


def validate_once():
    if not runtime_log.is_file() or "profile: modern-business" not in runtime_log.read_text(encoding="utf-8"):
        raise LogError("Startup banner with the profile is missing from wordpot-runtime.log")

    records = load_records()
    for technique, path, fields in expected_events:
        record = find(records, technique, path)
        missing = required_keys - set(record)
        if missing:
            raise LogError(f"{technique} misses keys {sorted(missing)}: {record!r}")
        if record["profile_id"] != "modern-business":
            raise LogError(f"Unexpected profile in {technique}: {record['profile_id']!r}")
        # dest_port is the port gunicorn listens on, the Host header must not change it
        if record["dest_port"] != 80:
            raise LogError(f"Unexpected dest_port in {technique}: {record['dest_port']!r}")
        wrong = {key: record.get(key) for key, value in fields.items() if record.get(key) != value}
        if wrong:
            raise LogError(f"{technique} has unexpected values {wrong!r}")

    pairs = find(records, "xmlrpc_multicall", "/xmlrpc.php").get("details", {}).get("credential_pairs")
    if pairs != [{"username": "admin", "password": f"{token}-mc"}]:
        raise LogError(f"xmlrpc_multicall has unexpected credential_pairs: {pairs!r}")

    # lure parameters are a list of name / value pairs, absent without one
    lure = find(records, "admin_ajax_action", "/wp-admin/admin-ajax.php",
                f"action=duplicator_download&url=http://{token}/")
    params = lure.get("details", {}).get("lure_params")
    if params != [{"name": "url", "value": f"http://{token}/"}]:
        raise LogError(f"admin_ajax_action has unexpected details.lure_params: {params!r}")
    plain = find(records, "admin_ajax_action", "/wp-admin/admin-ajax.php", "action=heartbeat")
    if "lure_params" in plain.get("details", {}):
        raise LogError(f"admin_ajax_action without lure parameters has details.lure_params: {plain!r}")

    host = find(records, "core_version_probe", "/readme.html").get("details", {}).get("http_host")
    if host != "example.org:8080":
        raise LogError(f"core_version_probe has unexpected details.http_host: {host!r}")

    upload = find(records, "upload_lure_payload", "/wp-content/uploads/2026/10/report.php")
    if upload.get("payload_stored") is not True or not upload.get("payload_ref"):
        raise LogError(f"Upload payload was not stored: {upload!r}")
    payload = log_dir / "payloads" / upload["payload_ref"]
    if not payload.is_file():
        raise LogError(f"Missing payload file {payload}")
    data = payload.read_bytes()
    if hashlib.sha256(data).hexdigest() != upload["payload_sha256"] or len(data) != upload["payload_size"]:
        raise LogError(f"Payload file {payload} does not match payload_sha256 / payload_size")
    if token.encode() not in data:
        raise LogError(f"Payload file {payload} does not hold the uploaded body")
    if not any(item.get("basename") == f"{token}.php" for item in upload.get("details", {}).get("uploaded_files", [])):
        raise LogError(f"Upload event misses details.uploaded_files: {upload!r}")

    loopback = [r for r in records if r.get("path") == "/healthz" and r.get("src_ip", "").startswith("127.")]
    if loopback:
        raise LogError(f"Healthcheck requests were written to wordpot.json: {loopback[0]!r}")


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

  python3 - "${RUNTIME_LOG_FILE}" "${DOCKER_LOG_FILE}" <<'PY'
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
        r"Read-only file system",
        r"Address already in use",
        r"Unable to (write JSONL event|store payload)",
        r"Can't load conf file",
        r"Worker failed to boot",
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
  wait_for_healthy || test_die "Wordpot container did not become healthy"
  test_ok "Container is running and healthy"

  resolve_mapped_port

  test_info "Running Wordpot HTTP probes with token: ${TOKEN}"
  run_http_probes || test_die "Wordpot HTTP probes failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "Wordpot container stopped after HTTP probes"

  test_info "Waiting for Wordpot events and payloads"
  validate_wordpot_logs || test_die "Expected Wordpot events or payloads were not found"
  test_ok "Wordpot events were written to wordpot.json, the upload to the payload spool"

  assert_no_runtime_errors
  test_ok "No Wordpot runtime errors found in logs"

  test_ok "Wordpot post-build smoke test completed successfully"
}

main "$@"
