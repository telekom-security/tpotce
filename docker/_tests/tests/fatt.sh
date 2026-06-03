#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="fatt"
DEFAULT_IMAGE="ghcr.io/telekom-security/fatt:24.04.1"
IMAGE=""
LOG_DIR=""
LOG_FILE=""
HTTP_TARGET_CONTAINER_NAME=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Fatt image.

Options:
  --image IMAGE      Image to test. Defaults to docker/fatt/docker-compose.yml.
  --timeout SEC      Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP       Accepted for runner compatibility; Fatt exposes no host port.
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

prepare_fatt_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  LOG_FILE="${LOG_DIR}/fatt.log"
  HTTP_TARGET_CONTAINER_NAME="${TEST_PROJECT_NAME}-http-target"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  fatt:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    cap_add:
      - NET_ADMIN
      - NET_RAW
      - SYS_NICE
    volumes:
      - "${LOG_DIR}:/opt/fatt/log"
  http-target:
    image: "${IMAGE}"
    container_name: "${HTTP_TARGET_CONTAINER_NAME}"
    restart: "no"
    user: "0:0"
    command: ["python3", "-m", "http.server", "80", "--bind", "0.0.0.0"]
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_http_target_container() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local state=""

  while (( SECONDS < deadline )); do
    state="$(docker inspect -f '{{.State.Status}}' "${HTTP_TARGET_CONTAINER_NAME}" 2>/dev/null || true)"
    case "${state}" in
      running)
        return 0
        ;;
      exited|dead)
        return 1
        ;;
    esac
    sleep 1
  done

  return 1
}

run_http_probe() {
  local token="$1"

  docker exec -i "${TEST_CONTAINER_NAME}" python3 - "http-target" "${token}" "${TEST_TIMEOUT}" <<'PY'
import http.client
import sys

host = sys.argv[1]
token = sys.argv[2]
timeout = min(max(1, int(sys.argv[3])), 3)
path = f"/tpot-fatt-smoke/{token}"
headers = {
    "Host": host,
    "User-Agent": f"tpot-fatt-smoke/{token}",
    "Accept": "*/*",
}

try:
    conn = http.client.HTTPConnection(host, 80, timeout=timeout)
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    response.read()
finally:
    try:
        conn.close()
    except NameError:
        pass

if response.status >= 500:
    print(f"HTTP target returned status {response.status}", file=sys.stderr)
    sys.exit(1)

print(f"HTTP probe sent: GET {path} status={response.status}")
PY
}

find_http_log_event() {
  local token="$1"

  python3 - "${LOG_FILE}" "${token}" <<'PY'
import json
import sys
from pathlib import Path

log_file = Path(sys.argv[1])
token = sys.argv[2]

if not log_file.exists():
    print(f"{log_file} does not exist yet", file=sys.stderr)
    sys.exit(1)

try:
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
except OSError as exc:
    print(f"Could not read {log_file}: {exc}", file=sys.stderr)
    sys.exit(1)

last_error = None
for line_number, line in enumerate(lines, 1):
    stripped = line.strip()
    if not stripped:
        continue

    try:
        event = json.loads(stripped)
    except json.JSONDecodeError as exc:
        last_error = f"Invalid JSON in {log_file}:{line_number}: {exc}"
        continue

    http = event.get("http")
    if not isinstance(http, dict):
        continue
    if event.get("protocol") != "http":
        continue
    if http.get("requestMethod") != "GET":
        continue

    request_uri = str(http.get("requestURI", ""))
    user_agent = str(http.get("userAgent", ""))
    if token not in request_uri and token not in user_agent:
        continue

    required_fields = ("sourceIp", "destinationIp", "sourcePort", "destinationPort")
    missing = [field for field in required_fields if not event.get(field)]
    if missing:
        print(f"Matching Fatt event in {log_file}:{line_number} is missing fields: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    if str(event.get("destinationPort")) != "80":
        print(
            f"Matching Fatt event in {log_file}:{line_number} has destinationPort={event.get('destinationPort')!r}, expected 80",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        "Fatt HTTP event found in {}:{} {}:{} -> {}:{} requestURI={!r}".format(
            log_file,
            line_number,
            event.get("sourceIp"),
            event.get("sourcePort"),
            event.get("destinationIp"),
            event.get("destinationPort"),
            request_uri,
        )
    )
    sys.exit(0)

if last_error:
    print(last_error, file=sys.stderr)
else:
    print(f"No matching Fatt HTTP event found in {log_file} for token {token}", file=sys.stderr)
sys.exit(1)
PY
}

run_probe_until_logged() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local probe_output=""
  local log_output=""

  while (( SECONDS < deadline )); do
    probe_output="$(run_http_probe "${token}" 2>&1)" || true
    if log_output="$(find_http_log_event "${token}" 2>&1)"; then
      printf '%s\n' "${probe_output}"
      printf '%s\n' "${log_output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${probe_output}" >&2
  printf '%s\n' "${log_output}" >&2
  return 1
}

assert_no_runtime_errors() {
  local pattern="Traceback|Permission denied|No such file|cap_set_proc|No interfaces found|The capture session could not be initiated|Couldn't run .*dumpcap|dumpcap:.*(error|failed)|tshark:.*(error|failed)"

  if grep -R -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Fatt runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Fatt runtime error found in Docker logs"
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

  prepare_fatt_harness
  test_enable_cleanup

  test_info "Starting isolated Fatt container and HTTP target"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Fatt container did not stay running"
  test_ok "Fatt container is running"

  wait_for_http_target_container || test_die "Fatt HTTP target container did not stay running"
  test_ok "HTTP target container is running"

  local token="fatt-test-$(date +%s)-$$"
  test_info "Generating local HTTP traffic with token: ${token}"
  run_probe_until_logged "${token}" || test_die "Fatt did not log the generated HTTP probe"
  test_wait_for_container || test_die "Fatt container stopped after HTTP probe"
  test_ok "Fatt HTTP probe was written to fatt.log"

  assert_no_runtime_errors
  test_ok "No Fatt runtime errors found in logs"

  test_ok "Fatt post-build smoke test completed successfully"
}

main "$@"
