#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="elasticpot"
DEFAULT_IMAGE="dtagdevsec/elasticpot:24.04.1"
IMAGE=""
HTTP_PORT=""
LOG_DIR=""
MAPPED_HTTP_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the ElasticPot image.

Options:
  --image IMAGE      Image to test. Defaults to docker/elasticpot/docker-compose.yml.
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

prepare_elasticpot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  local port_mapping="${TEST_BIND_IP}::9200"
  if [[ -n "${HTTP_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${HTTP_PORT}:9200"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  elasticpot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/opt/elasticpot/log"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_elasticsearch_probes() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import http.client
import json
import sys

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
payload = '{"query":{"match_all":{}}}'
base_headers = {
    "Host": host,
    "User-Agent": "tpot-elasticpot-smoke/{}".format(token),
}


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def request_json(method, path, body=None, headers=None):
    request_headers = dict(base_headers)
    if headers:
        request_headers.update(headers)

    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        fail("{} {} failed: {}".format(method, path, exc))
    finally:
        conn.close()

    if response.status != 200:
        fail("{} {} returned HTTP {}: {}".format(method, path, response.status, raw[:200]))

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fail("{} {} did not return valid JSON: {}".format(method, path, exc))


banner = request_json("GET", "/")
if banner.get("cluster_name") != "elasticsearch":
    fail("Unexpected banner cluster_name: {!r}".format(banner.get("cluster_name")))
if banner.get("version", {}).get("number") != "1.4.1":
    fail("Unexpected banner version.number: {!r}".format(banner.get("version", {}).get("number")))
if banner.get("tagline") != "You Know, for Search":
    fail("Unexpected banner tagline: {!r}".format(banner.get("tagline")))

cluster = request_json("GET", "/_cluster/health?{}".format(token))
if cluster.get("status") != "yellow":
    fail("Unexpected cluster status: {!r}".format(cluster.get("status")))
if cluster.get("number_of_nodes") != 1:
    fail("Unexpected number_of_nodes: {!r}".format(cluster.get("number_of_nodes")))
if cluster.get("timed_out") is not False:
    fail("Unexpected cluster timed_out: {!r}".format(cluster.get("timed_out")))

search = request_json(
    "POST",
    "/_search?{}".format(token),
    body=payload,
    headers={"Content-Type": "application/json"},
)
if search.get("hits", {}).get("total") != 1:
    fail("Unexpected search hits.total: {!r}".format(search.get("hits", {}).get("total")))
if search.get("_shards", {}).get("failed") != 0:
    fail("Unexpected search _shards.failed: {!r}".format(search.get("_shards", {}).get("failed")))
if search.get("timed_out") is not False:
    fail("Unexpected search timed_out: {!r}".format(search.get("timed_out")))

print("ElasticPot probes succeeded")
PY
}

run_elasticsearch_probes_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_elasticsearch_probes "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

wait_for_json_log_events() {
  local token="$1"

  python3 - "${LOG_DIR}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

log_dir = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
payload = '{"query":{"match_all":{}}}'
last_error = None
found_recon = None
found_attack = None


def has_token(value):
    return isinstance(value, str) and token in value


while time.monotonic() < deadline:
    files = sorted(log_dir.glob("elasticpot.json*"))

    for log_file in files:
        if not log_file.is_file():
            continue

        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            last_error = "Could not read {}: {}".format(log_file, exc)
            continue

        for line_number, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:
                print(
                    "Invalid JSON in {}:{}: {}".format(log_file, line_number, exc),
                    file=sys.stderr,
                )
                sys.exit(1)

            if (
                event.get("eventid") == "elasticpot.recon"
                and event.get("request") == "GET"
                and has_token(event.get("url"))
                and has_token(event.get("user_agent"))
            ):
                found_recon = "{}:{}".format(log_file, line_number)

            if (
                event.get("eventid") == "elasticpot.attack"
                and event.get("request") == "POST"
                and has_token(event.get("url"))
                and has_token(event.get("user_agent"))
                and event.get("payload") == payload
            ):
                found_attack = "{}:{}".format(log_file, line_number)

    if found_recon and found_attack:
        print("Recon event found in {}".format(found_recon))
        print("Attack event found in {}".format(found_attack))
        sys.exit(0)

    if not files:
        last_error = "No elasticpot.json log files found in {}".format(log_dir)

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
if not found_recon:
    print("No matching ElasticPot recon event found for token {}".format(token), file=sys.stderr)
if not found_attack:
    print("No matching ElasticPot attack event found for token {}".format(token), file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern="Traceback|Unhandled Error|NameError|ImportError|Cannot listen|Missing JSON file|Missing file"

  if grep -R -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "ElasticPot runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "ElasticPot runtime error found in Docker logs"
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

  prepare_elasticpot_harness
  test_enable_cleanup

  test_info "Starting isolated ElasticPot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "ElasticPot container did not stay running"
  test_ok "Container is running"

  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "9200")" || test_die "Could not resolve mapped host port for 9200/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_HTTP_PORT} maps to container port 9200/tcp"

  local token="elasticpot-test-$(date +%s)-$$"

  test_info "Running Elasticsearch-shaped HTTP probes with token: ${token}"
  run_elasticsearch_probes_with_retries "${token}" || test_die "ElasticPot HTTP probes failed on ${TEST_BIND_IP}:${MAPPED_HTTP_PORT}"
  test_wait_for_container || test_die "ElasticPot container stopped after HTTP probes"

  test_info "Waiting for ElasticPot JSON log events"
  wait_for_json_log_events "${token}" || test_die "Expected ElasticPot recon and attack events were not found in elasticpot.json"
  test_ok "ElasticPot recon and attack events were written to elasticpot.json"

  assert_no_runtime_errors
  test_ok "No ElasticPot runtime errors found in logs"

  test_ok "ElasticPot post-build smoke test completed successfully"
}

main "$@"
