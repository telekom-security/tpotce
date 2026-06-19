#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="tanner"
DEFAULT_REDIS_IMAGE="dtagdevsec/redis:24.04.1"
DEFAULT_PHPOX_IMAGE="dtagdevsec/phpox:24.04.1"
DEFAULT_TANNER_IMAGE="dtagdevsec/tanner:24.04.1"
DEFAULT_SNARE_IMAGE="dtagdevsec/snare:24.04.1"

REDIS_IMAGE=""
PHPOX_IMAGE=""
TANNER_IMAGE=""
SNARE_IMAGE=""
SNARE_PORT=""
MAPPED_SNARE_PORT=""
LOG_DIR=""
FILES_DIR=""
SNARE_LOG_FILE=""
SNARE_ERR_FILE=""
TANNER_REPORT_FILE=""
TOKEN=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Tanner stack.

Options:
  --redis-image IMAGE   Redis image to test. Defaults to docker/tanner/docker-compose.yml.
  --phpox-image IMAGE   PHPox image to test. Defaults to docker/tanner/docker-compose.yml.
  --tanner-image IMAGE  Tanner image to test. Defaults to docker/tanner/docker-compose.yml.
  --snare-image IMAGE   Snare image to test. Defaults to docker/tanner/docker-compose.yml.
  --snare-port PORT     Host TCP port for Snare HTTP. Default: dynamic loopback port.
  --timeout SEC         Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP          Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts      Keep temporary compose file and logs for debugging.
  -h, --help            Show this help message.
EOF
}

read_tanner_compose_image() {
  local service="$1"
  local fallback="$2"
  local compose_file="${DOCKER_ROOT}/tanner/docker-compose.yml"

  if [[ ! -f "${compose_file}" ]]; then
    printf '%s\n' "${fallback}"
    return
  fi

  python3 - "${compose_file}" "${service}" "${fallback}" <<'PY'
import re
import sys

compose_file, service, fallback = sys.argv[1:]
service_re = re.compile(rf"^(\s*){re.escape(service)}:\s*(?:#.*)?$")
child_re = re.compile(r"^(\s*)[A-Za-z0-9_-]+:\s*(?:#.*)?$")
image_re = re.compile(r"^\s*image:\s*(.+?)\s*(?:#.*)?$")

in_service = False
service_indent = None

with open(compose_file, encoding="utf-8") as fh:
    for line in fh:
        if not in_service:
            match = service_re.match(line)
            if match:
                in_service = True
                service_indent = len(match.group(1))
            continue

        child = child_re.match(line)
        if child and len(child.group(1)) <= service_indent:
            break

        image = image_re.match(line)
        if image:
            print(image.group(1).strip().strip('"\''))
            sys.exit(0)

print(fallback)
PY
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --redis-image)
        [[ $# -ge 2 ]] || test_die "--redis-image requires an argument"
        REDIS_IMAGE="$2"
        shift 2
        ;;
      --redis-image=*)
        REDIS_IMAGE="${1#*=}"
        shift
        ;;
      --phpox-image)
        [[ $# -ge 2 ]] || test_die "--phpox-image requires an argument"
        PHPOX_IMAGE="$2"
        shift 2
        ;;
      --phpox-image=*)
        PHPOX_IMAGE="${1#*=}"
        shift
        ;;
      --tanner-image)
        [[ $# -ge 2 ]] || test_die "--tanner-image requires an argument"
        TANNER_IMAGE="$2"
        shift 2
        ;;
      --tanner-image=*)
        TANNER_IMAGE="${1#*=}"
        shift
        ;;
      --snare-image)
        [[ $# -ge 2 ]] || test_die "--snare-image requires an argument"
        SNARE_IMAGE="$2"
        shift 2
        ;;
      --snare-image=*)
        SNARE_IMAGE="${1#*=}"
        shift
        ;;
      --snare-port|--http-port|--host-port|--port)
        [[ $# -ge 2 ]] || test_die "$1 requires an argument"
        SNARE_PORT="$2"
        shift 2
        ;;
      --snare-port=*|--http-port=*|--host-port=*|--port=*)
        SNARE_PORT="${1#*=}"
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

resolve_images() {
  [[ -n "${REDIS_IMAGE}" ]] || REDIS_IMAGE="$(read_tanner_compose_image "tanner_redis" "${DEFAULT_REDIS_IMAGE}")"
  [[ -n "${PHPOX_IMAGE}" ]] || PHPOX_IMAGE="$(read_tanner_compose_image "tanner_phpox" "${DEFAULT_PHPOX_IMAGE}")"
  [[ -n "${TANNER_IMAGE}" ]] || TANNER_IMAGE="$(read_tanner_compose_image "tanner" "${DEFAULT_TANNER_IMAGE}")"
  [[ -n "${SNARE_IMAGE}" ]] || SNARE_IMAGE="$(read_tanner_compose_image "snare" "${DEFAULT_SNARE_IMAGE}")"
}

validate_args() {
  test_validate_timeout

  if [[ -n "${SNARE_PORT}" ]]; then
    test_validate_port "${SNARE_PORT}"
    test_ensure_port_free "${TEST_BIND_IP}" "${SNARE_PORT}"
  fi
}

require_images() {
  test_require_image "${REDIS_IMAGE}" "docker build -t ${REDIS_IMAGE} docker/tanner/redis"
  test_require_image "${PHPOX_IMAGE}" "docker build -t ${PHPOX_IMAGE} docker/tanner/phpox"
  test_require_image "${TANNER_IMAGE}" "docker build -t ${TANNER_IMAGE} docker/tanner/tanner"
  test_require_image "${SNARE_IMAGE}" "docker build -t ${SNARE_IMAGE} docker/tanner/snare"
}

prepare_tanner_harness() {
  test_prepare_harness "${TEST_NAME}"
  TEST_CONTAINER_NAME="${TEST_PROJECT_NAME}-snare"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  FILES_DIR="${TEST_TMP_ROOT}/files"
  SNARE_LOG_FILE="${LOG_DIR}/snare.log"
  SNARE_ERR_FILE="${LOG_DIR}/snare.err"
  TANNER_REPORT_FILE="${LOG_DIR}/tanner_report.json"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${FILES_DIR}"
  touch "${SNARE_LOG_FILE}" "${SNARE_ERR_FILE}"
  chmod 0777 "${LOG_DIR}" "${FILES_DIR}"
  chmod 0666 "${SNARE_LOG_FILE}" "${SNARE_ERR_FILE}"

  local port_mapping="${TEST_BIND_IP}::80"
  if [[ -n "${SNARE_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${SNARE_PORT}:80"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  tanner_redis:
    image: "${REDIS_IMAGE}"
    container_name: "${TEST_PROJECT_NAME}-redis"
    restart: "no"
    stop_signal: SIGKILL
    read_only: true
  tanner_phpox:
    image: "${PHPOX_IMAGE}"
    container_name: "${TEST_PROJECT_NAME}-phpox"
    restart: "no"
    stop_signal: SIGKILL
    read_only: true
    tmpfs:
      - /tmp:uid=2000,gid=2000,mode=1777
  tanner_api:
    image: "${TANNER_IMAGE}"
    container_name: "${TEST_PROJECT_NAME}-api"
    restart: "no"
    stop_signal: SIGKILL
    read_only: true
    tmpfs:
      - /tmp/tanner:uid=2000,gid=2000,mode=0777
    volumes:
      - "${LOG_DIR}:/var/log/tanner"
    command: tannerapi
    depends_on:
      - tanner_redis
  tanner:
    image: "${TANNER_IMAGE}"
    container_name: "${TEST_PROJECT_NAME}-tanner"
    restart: "no"
    stop_signal: SIGKILL
    read_only: true
    tmpfs:
      - /tmp/tanner:uid=2000,gid=2000,mode=0777
    volumes:
      - "${LOG_DIR}:/var/log/tanner"
      - "${FILES_DIR}:/opt/tanner/files"
    command: tanner
    depends_on:
      - tanner_api
      - tanner_phpox
  snare:
    image: "${SNARE_IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    stop_signal: SIGKILL
    ports:
      - "${port_mapping}"
    volumes:
      - "${SNARE_LOG_FILE}:/opt/snare/snare.log"
      - "${SNARE_ERR_FILE}:/opt/snare/snare.err"
    command:
      - sh
      - -c
      - exec snare --tanner tanner --debug true --auto-update false --host-ip 0.0.0.0 --port 80 --page-dir 10
    depends_on:
      - tanner
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_service_running() {
  local service="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local container_id=""
  local state=""

  while (( SECONDS < deadline )); do
    container_id="$(test_compose ps -q "${service}" 2>/dev/null || true)"
    if [[ -n "${container_id}" ]]; then
      state="$(docker inspect -f '{{.State.Status}}' "${container_id}" 2>/dev/null || true)"
      case "${state}" in
        running)
          return 0
          ;;
        exited|dead)
          return 1
          ;;
      esac
    fi
    sleep 1
  done

  return 1
}

wait_for_stack_running() {
  local service=""

  for service in tanner_redis tanner_phpox tanner_api tanner snare; do
    wait_for_service_running "${service}" || test_die "${service} did not reach running state"
    test_ok "${service} is running"
  done
}

resolve_snare_port() {
  MAPPED_SNARE_PORT="$(test_get_mapped_port "snare" "80")" || test_die "Could not resolve mapped host port for Snare"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_SNARE_PORT} maps to snare container port 80/tcp"
}

run_snare_tanner_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_SNARE_PORT}" "${TOKEN}" "${TEST_TIMEOUT}" <<'PY'
import http.client
import sys
import time
import urllib.parse

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
path = "/?tpot_smoke={}".format(urllib.parse.quote(token))
deadline = time.monotonic() + timeout
last_error = None

while time.monotonic() < deadline:
    try:
        conn = http.client.HTTPConnection(host, port, timeout=min(5, timeout))
        conn.request(
            "GET",
            path,
            headers={
                "User-Agent": "tpot-tanner-smoke/{}".format(token),
                "Accept": "*/*",
            },
        )
        response = conn.getresponse()
        body = response.read(512)
        conn.close()
        if response.status == 200 and body:
            print("Snare/Tanner HTTP probe succeeded with status {}".format(response.status))
            sys.exit(0)
        last_error = "unexpected status/body: {} {!r}".format(response.status, body[:120])
    except Exception as exc:
        last_error = exc
    time.sleep(1)

print("Snare/Tanner HTTP probe failed: {}".format(last_error), file=sys.stderr)
sys.exit(1)
PY
}

wait_for_report_log() {
  python3 - "${TANNER_REPORT_FILE}" "${TOKEN}" "${TEST_TIMEOUT}" <<'PY'
import json
import pathlib
import sys
import time

report_file = pathlib.Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None

while time.monotonic() < deadline:
    if report_file.exists():
        for line in report_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if token not in event.get("path", ""):
                continue
            response_msg = event.get("response_msg", {})
            detection = response_msg.get("response", {}).get("message", {}).get("detection", {})
            if detection.get("name") != "index":
                print("Unexpected Tanner detection: {!r}".format(detection), file=sys.stderr)
                sys.exit(1)
            if not event.get("uuid"):
                print("Tanner report is missing snare uuid", file=sys.stderr)
                sys.exit(1)
            print("Tanner report contains snare uuid {} and path {}".format(event["uuid"], event["path"]))
            sys.exit(0)
    time.sleep(1)

print("Timed out waiting for Tanner report entry: {}".format(last_error), file=sys.stderr)
sys.exit(1)
PY
}

wait_for_snare_log() {
  test_wait_for_file_text "Request path: /?tpot_smoke=${TOKEN}" "${LOG_DIR}" \
    || test_die "Snare request log was not written"
}

run_redis_probe() {
  test_compose exec -T tanner_redis redis-cli PING | grep -F "PONG" >/dev/null \
    || test_die "Redis PING did not return PONG"

  test_compose exec -T tanner_redis redis-cli SMEMBERS snare_ids | grep -E '.+' >/dev/null \
    || test_die "Redis snare_ids set is empty after Snare/Tanner probe"
}

run_tanner_api_probe() {
  test_compose exec -T tanner_api python3 - "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
import urllib.request

timeout = int(sys.argv[1])
deadline = time.monotonic() + timeout
last_error = None

while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen("http://tanner_api:8092/snares", timeout=min(5, timeout)) as response:
            payload = json.loads(response.read().decode("utf-8"))
        snares = payload["response"]["message"]
        if isinstance(snares, list) and snares:
            print("Tanner API returned {} snare id(s)".format(len(snares)))
            sys.exit(0)
        last_error = "empty snares response: {!r}".format(payload)
    except Exception as exc:
        last_error = exc
    time.sleep(1)

print("Tanner API probe failed: {}".format(last_error), file=sys.stderr)
sys.exit(1)
PY
}

run_phpox_probe() {
  test_compose exec -T tanner python3 - "${TOKEN}" "${TEST_TIMEOUT}" <<'PY'
import http.client
import json
import sys
import time
import uuid

token = sys.argv[1]
timeout = int(sys.argv[2])
deadline = time.monotonic() + timeout
last_error = None
php_code = '<?php echo "{}"; ?>'.format(token).encode("utf-8")

while time.monotonic() < deadline:
    boundary = "tpot-smoke-{}".format(uuid.uuid4().hex)
    body = b"".join(
        [
            b"--" + boundary.encode("ascii") + b"\r\n",
            b'Content-Disposition: form-data; name="file"; filename="smoke.php"\r\n',
            b"Content-Type: application/x-php\r\n\r\n",
            php_code,
            b"\r\n--" + boundary.encode("ascii") + b"--\r\n",
        ]
    )
    headers = {
        "Content-Type": "multipart/form-data; boundary={}".format(boundary),
        "Content-Length": str(len(body)),
    }
    try:
        conn = http.client.HTTPConnection("tanner_phpox", 8088, timeout=min(5, timeout))
        conn.request("POST", "/", body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        if response.status != 200:
            last_error = "unexpected status {} body={!r}".format(response.status, raw[:200])
            time.sleep(1)
            continue
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("stdout") == token and payload.get("file_md5"):
            print("PHPox sandbox returned expected stdout and file_md5")
            sys.exit(0)
        last_error = "unexpected PHPox payload: {!r}".format(payload)
    except Exception as exc:
        last_error = exc
    time.sleep(1)

print("PHPox probe failed: {}".format(last_error), file=sys.stderr)
sys.exit(1)
PY
}

main() {
  parse_args "$@"
  resolve_images
  validate_args
  test_check_dependencies
  require_images

  TOKEN="tpot-tanner-smoke-$(date +%s)-$$"

  prepare_tanner_harness
  test_enable_cleanup

  test_info "Starting Tanner stack with temporary logs in ${LOG_DIR}"
  test_compose up -d
  wait_for_stack_running
  resolve_snare_port

  run_snare_tanner_probe
  test_ok "Snare served a request through Tanner"

  wait_for_snare_log
  test_ok "Snare wrote the request log"

  wait_for_report_log
  test_ok "Tanner wrote local JSON reporting"

  run_redis_probe
  test_ok "Redis is reachable and contains the Snare id set"

  run_tanner_api_probe
  test_ok "Tanner API reads Snare ids from Redis"

  run_phpox_probe
  test_ok "PHPox executes a sandboxed PHP probe over the Compose network"
}

main "$@"
