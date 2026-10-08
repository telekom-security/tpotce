#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="galah"
DEFAULT_IMAGE="dtagdevsec/galah:24.04.2"
IMAGE=""
LOG_DIR=""
CERT_DIR=""
CACHE_DIR=""
JSON_LOG_FILE=""
MAPPED_HTTP_PORT=""
MAPPED_HTTP8080_PORT=""
MAPPED_TLS_PORT=""
MAPPED_TLS8443_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Galah image. It checks the
T-Pot adjustments on top of upstream Galah: the flat event log in the format
of the former T-Pot fork, the static rule for "/", the self-signed TLS
certificate and its persistence. No LLM backend is needed, requests the rule
does not answer fail on purpose and are logged as failedResponse.

Options:
  --image IMAGE      Image to test. Defaults to docker/galah/docker-compose.yml.
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
}

prepare_galah_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  CERT_DIR="${TEST_TMP_ROOT}/cert"
  CACHE_DIR="${TEST_TMP_ROOT}/cache"
  JSON_LOG_FILE="${LOG_DIR}/galah.json"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${CERT_DIR}" "${CACHE_DIR}"
  chmod 0777 "${LOG_DIR}" "${CERT_DIR}" "${CACHE_DIR}"

  # The LLM backend is unreachable on purpose
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  galah:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    environment:
      LLM_PROVIDER: "ollama"
      LLM_SERVER_URL: "http://127.0.0.1:9"
      LLM_MODEL: "llama3.1"
      LLM_TEMPERATURE: "1"
      LLM_API_KEY: ""
      LLM_CLOUD_LOCATION: ""
      LLM_CLOUD_PROJECT: ""
    ports:
      - "${TEST_BIND_IP}::80"
      - "${TEST_BIND_IP}::8080"
      - "${TEST_BIND_IP}::443"
      - "${TEST_BIND_IP}::8443"
    volumes:
      - "${CACHE_DIR}:/opt/galah/config/cache"
      - "${CERT_DIR}:/opt/galah/config/cert"
      - "${LOG_DIR}:/opt/galah/log"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

resolve_ports() {
  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "80")" || test_die "Could not resolve mapped host port for 80/tcp"
  MAPPED_HTTP8080_PORT="$(test_get_mapped_port "${TEST_NAME}" "8080")" || test_die "Could not resolve mapped host port for 8080/tcp"
  MAPPED_TLS_PORT="$(test_get_mapped_port "${TEST_NAME}" "443")" || test_die "Could not resolve mapped host port for 443/tcp"
  MAPPED_TLS8443_PORT="$(test_get_mapped_port "${TEST_NAME}" "8443")" || test_die "Could not resolve mapped host port for 8443/tcp"
}

# "/" is answered by the static rule, "/tpot-smoke-test" needs the LLM and fails.
run_http_probes() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${MAPPED_HTTP8080_PORT}" "${MAPPED_TLS_PORT}" "${MAPPED_TLS8443_PORT}" "${TEST_TIMEOUT}" <<'PY'
import ssl
import sys
import time
import urllib.error
import urllib.request

host = sys.argv[1]
http_port, http8080_port, tls_port, tls8443_port = (int(p) for p in sys.argv[2:6])
timeout = int(sys.argv[6])
# Galah presents the self-signed certificate under test, on loopback only
context = ssl.create_default_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE


def fetch(url, path):
    request = urllib.request.Request(url + path, headers={"User-Agent": "tpot-smoke-test"})
    try:
        response = urllib.request.urlopen(request, timeout=timeout, context=context)
        return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


deadline = time.monotonic() + timeout
last_error = None
while time.monotonic() < deadline:
    try:
        fetch("http://{}:{}".format(host, http_port), "/")
        break
    except OSError as exc:
        last_error = exc
        time.sleep(1)
else:
    print("Galah did not answer on port 80: {}".format(last_error), file=sys.stderr)
    sys.exit(1)

checks = [
    ("http://{}:{}".format(host, http_port), "/", 200, "401 Unauthorized"),
    ("http://{}:{}".format(host, http8080_port), "/", 200, "401 Unauthorized"),
    ("https://{}:{}".format(host, tls_port), "/", 200, "401 Unauthorized"),
    ("https://{}:{}".format(host, tls8443_port), "/", 200, "401 Unauthorized"),
    ("http://{}:{}".format(host, http_port), "/tpot-smoke-test", 500, None),
]
for base, path, expected_status, expected_body in checks:
    try:
        status, body = fetch(base, path)
    except OSError as exc:
        print("Request to {}{} failed: {}".format(base, path, exc), file=sys.stderr)
        sys.exit(1)
    if status != expected_status or (expected_body and expected_body not in body):
        print("{}{} answered {} {!r}, expected {} {!r}".format(base, path, status, body[:80], expected_status, expected_body), file=sys.stderr)
        sys.exit(1)
    print("{}{} answered {}".format(base, path, status))
PY
}

# The key sets are those the former T-Pot fork wrote, ewsposter and the
# Kibana dashboard rely on them.
wait_for_flat_log_events() {
  python3 - "${JSON_LOG_FILE}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from datetime import datetime
from pathlib import Path

log_file, timeout = Path(sys.argv[1]), int(sys.argv[2])

common = {
    "timestamp", "level", "msg", "src_ip", "src_port", "dest_port", "hostname", "sensorName", "session",
    "request.method", "request.protocol", "request.requestURI", "request.userAgent", "request.body",
    "request.bodySha256", "request.headers.sorted", "request.headers.sortedSha256",
    "request.headers.User-Agent", "response.metadata.provider", "response.metadata.model",
    "response.metadata.temperature",
}
successful = common | {"response.body", "response.headers.Content-Type", "response.headers.Server",
                       "response.metadata.generationSource"}
failed = common | {"error_type", "fields.msg", "invalidResponse"}
nested = {"eventTime", "srcIP", "srcPort", "port", "httpRequest", "httpResponse", "responseMetadata", "error", "type", "time"}

deadline = time.monotonic() + timeout
problems = []
while time.monotonic() < deadline:
    problems, ports = [], set()
    found_failed = False
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        lines = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            print("Invalid JSON in {}:{}: {}".format(log_file, number, exc), file=sys.stderr)
            sys.exit(1)
        upstream = nested & set(event)
        if upstream:
            print("Upstream keys {} in {}:{}".format(sorted(upstream), log_file, number), file=sys.stderr)
            sys.exit(1)
        try:
            datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            print("No ISO8601 timestamp in {}:{}".format(log_file, number), file=sys.stderr)
            sys.exit(1)
        if event.get("msg") == "successfulResponse":
            missing = successful - set(event)
            if missing:
                problems.append("successfulResponse: missing keys {}".format(sorted(missing)))
            if event.get("response.metadata.generationSource") != "static":
                problems.append("successfulResponse: generationSource {!r}".format(event.get("response.metadata.generationSource")))
            ports.add(event.get("dest_port"))
        elif event.get("msg") == "failedResponse: returned 500 internal server error":
            missing = failed - set(event)
            if missing:
                problems.append("failedResponse: missing keys {}".format(sorted(missing)))
            found_failed = True
    if ports != {"80", "8080", "443", "8443"}:
        problems.append("successfulResponse for dest_port {}, expected 80, 8080, 443, 8443".format(sorted(p for p in ports if p)))
    if not found_failed:
        problems.append("missing event: failedResponse")
    if not problems:
        print("Found the successfulResponse and failedResponse events with the T-Pot fields")
        sys.exit(0)
    time.sleep(1)

print("\n".join(problems), file=sys.stderr)
sys.exit(1)
PY
}

# The key is created by the container user, so its identity is taken from
# stat (no read access needed): a regenerated key changes inode or mtime.
cert_file_id() {
  python3 - "${CERT_DIR}/key.pem" "${CERT_DIR}/cert.pem" <<'PY'
import os
import sys

ids = []
for path in sys.argv[1:]:
    try:
        info = os.stat(path)
    except OSError as exc:
        print("Could not stat {}: {}".format(path, exc), file=sys.stderr)
        sys.exit(1)
    if info.st_size == 0:
        print("{} is empty".format(path), file=sys.stderr)
        sys.exit(1)
    ids.append("{}:{}:{}".format(info.st_ino, info.st_size, info.st_mtime_ns))
print(",".join(ids))
PY
}

tls_fingerprint() {
  python3 - "${TEST_BIND_IP}" "$1" "${TEST_TIMEOUT}" <<'PY'
import hashlib
import ssl
import sys
import time

host, port, timeout = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None
while time.monotonic() < deadline:
    try:
        pem = ssl.get_server_certificate((host, port), timeout=5)
        print(hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest())
        sys.exit(0)
    except OSError as exc:
        last_error = exc
        time.sleep(1)
print("TLS handshake on port {} failed: {}".format(port, last_error), file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern='panic:|Permission denied|Read-only file system|error starting server|error loading'

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Galah runtime error found in Docker logs"
  fi
}

main() {
  parse_args "$@"
  validate_args
  test_check_dependencies
  local cert_before=""
  local cert_after=""
  local fp_before=""
  local fp_after=""

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  prepare_galah_harness
  test_enable_cleanup

  test_info "Starting isolated Galah container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Galah container did not stay running"
  test_ok "Container is running"
  resolve_ports
  test_ok "Ports 80, 8080, 443 and 8443 are mapped to ${TEST_BIND_IP}"

  test_info "Probing the HTTP and TLS ports"
  run_http_probes || test_die "HTTP / TLS probes failed"
  test_ok "The static rule answers on all ports, a request for the LLM fails as expected"

  test_info "Waiting for the flat T-Pot events in galah.json"
  wait_for_flat_log_events || test_die "galah.json does not hold the expected T-Pot events"
  test_ok "galah.json holds the events in the T-Pot format"

  test_info "Checking the persistent TLS certificate"
  cert_before="$(cert_file_id)" || test_die "The TLS certificate was not created in the persistent cert volume"
  fp_before="$(tls_fingerprint "${MAPPED_TLS_PORT}")" || test_die "TLS handshake on port 443 failed"
  [[ "${fp_before}" == "$(tls_fingerprint "${MAPPED_TLS8443_PORT}")" ]] || test_die "Ports 443 and 8443 present different certificates"

  test_info "Restarting container to verify certificate persistence"
  test_compose restart "${TEST_NAME}" >/dev/null
  test_wait_for_container || test_die "Galah container did not stay running after restart"
  resolve_ports
  fp_after="$(tls_fingerprint "${MAPPED_TLS_PORT}")" || test_die "TLS handshake on port 443 failed after restart"
  cert_after="$(cert_file_id)" || test_die "The TLS certificate was not readable after restart"
  [[ "${cert_before}" == "${cert_after}" ]] || test_die "The TLS certificate files changed after container restart"
  [[ "${fp_before}" == "${fp_after}" ]] || test_die "The TLS certificate changed after container restart"
  test_ok "TLS certificate is unchanged after container restart (sha256 ${fp_after:0:16}...)"

  assert_no_runtime_errors
  test_ok "No Galah runtime errors found in logs"

  test_ok "Galah post-build smoke test completed successfully"
}

main "$@"
