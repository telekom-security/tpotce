#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="redishoneypot"
DEFAULT_IMAGE="dtagdevsec/redishoneypot:24.04.1"
IMAGE=""
REDIS_PORT=""
LOG_DIR=""
LOG_FILE=""
MAPPED_REDIS_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the RedisHoneyPot image.

Options:
  --image IMAGE       Image to test. Defaults to docker/redishoneypot/docker-compose.yml.
  --redis-port PORT  Host TCP port for Redis. Default: dynamic loopback port.
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
      --redis-port|--host-port|--port)
        [[ $# -ge 2 ]] || test_die "$1 requires an argument"
        REDIS_PORT="$2"
        shift 2
        ;;
      --redis-port=*|--host-port=*|--port=*)
        REDIS_PORT="${1#*=}"
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

  if [[ -n "${REDIS_PORT}" ]]; then
    test_validate_port "${REDIS_PORT}"
  fi
}

prepare_redishoneypot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  LOG_FILE="${LOG_DIR}/redishoneypot.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  local port_mapping="${TEST_BIND_IP}::6379"
  if [[ -n "${REDIS_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${REDIS_PORT}:6379"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  redishoneypot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/var/log/redishoneypot"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_redis_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_REDIS_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
key = "redishoneypot-smoke-key-{}".format(token)


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def encode_command(*parts):
    encoded = ["*{}\r\n".format(len(parts)).encode("ascii")]
    for part in parts:
        raw = str(part).encode("utf-8")
        encoded.append(b"$" + str(len(raw)).encode("ascii") + b"\r\n")
        encoded.append(raw + b"\r\n")
    return b"".join(encoded)


def read_line(reader):
    line = reader.readline()
    if not line:
        raise RuntimeError("connection closed while reading response")
    return line


def read_response(reader):
    line = read_line(reader)
    prefix = line[:1]
    payload = line[1:-2].decode("utf-8", errors="replace")

    if prefix in (b"+", b"-", b":"):
        return prefix.decode("ascii"), payload

    if prefix == b"$":
        length = int(payload)
        if length < 0:
            return "$", None
        body = reader.read(length)
        trailer = reader.read(2)
        if len(body) != length or trailer != b"\r\n":
            raise RuntimeError("invalid bulk response framing")
        return "$", body.decode("utf-8", errors="replace")

    raise RuntimeError("unexpected Redis response line: {!r}".format(line))


def request(sock, reader, *parts):
    sock.sendall(encode_command(*parts))
    return read_response(reader)


connect_timeout = max(1.0, min(float(timeout), 5.0))

try:
    with socket.create_connection((host, port), timeout=connect_timeout) as sock:
        sock.settimeout(connect_timeout)
        reader = sock.makefile("rb")

        kind, payload = request(sock, reader, "PING")
        if (kind, payload) != ("+", "PONG"):
            fail("Unexpected PING response: kind={!r} payload={!r}".format(kind, payload))

        kind, payload = request(sock, reader, "INFO")
        if kind != "$" or "redis_version:6.0.10" not in payload:
            fail("Unexpected INFO response: kind={!r} payload={!r}".format(kind, payload[:160] if payload else payload))

        kind, payload = request(sock, reader, "SET", key, token)
        if (kind, payload) != ("+", "OK"):
            fail("Unexpected SET response: kind={!r} payload={!r}".format(kind, payload))

        kind, payload = request(sock, reader, "GET", key)
        if (kind, payload) != ("+", token):
            fail("Unexpected GET response: kind={!r} payload={!r}".format(kind, payload))

        reader.close()
except Exception as exc:
    if time.monotonic() < deadline:
        fail("RedisHoneyPot Redis probe failed: {}".format(exc))
    fail("RedisHoneyPot Redis probe timed out: {}".format(exc))

print("RedisHoneyPot Redis probe succeeded for token {}".format(token))
PY
}

run_redis_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_redis_probe "${token}" 2>&1)"; then
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
key = "redishoneypot-smoke-key-{}".format(token)
last_error = None


def load_events():
    if not log_file.exists():
        raise RuntimeError("{} does not exist yet".format(log_file))

    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        raise RuntimeError("{} is empty".format(log_file))

    events = []
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Invalid JSON in {}:{}: {}".format(log_file, line_number, exc)) from exc

        if not isinstance(event, dict):
            raise RuntimeError("JSON event in {}:{} is not an object".format(log_file, line_number))

        if event.get("level") == "error":
            raise RuntimeError('JSON log has level "error" in {}:{}'.format(log_file, line_number))

        if event.get("action") and not event.get("addr"):
            raise RuntimeError("JSON event in {}:{} is missing addr: {!r}".format(log_file, line_number, event))

        events.append(event)

    if not events:
        raise RuntimeError("No JSON events found in {}".format(log_file))

    return events


def has_action(events, expected):
    return any(event.get("action") == expected for event in events)


def has_action_containing(events, *needles):
    for event in events:
        action = event.get("action")
        if isinstance(action, str) and all(needle in action for needle in needles):
            return True
    return False


while time.monotonic() < deadline:
    try:
        events = load_events()

        missing = []
        if not has_action(events, "NewConnect"):
            missing.append("NewConnect")
        if not has_action(events, "PING"):
            missing.append("PING")
        if not has_action(events, "INFO"):
            missing.append("INFO")
        if not has_action_containing(events, "SET", key, token):
            missing.append("SET token")
        if not has_action_containing(events, "GET", key):
            missing.append("GET key")
        if not has_action(events, "Closed"):
            missing.append("Closed")

        if not missing:
            print("RedisHoneyPot log events found in {}".format(log_file))
            sys.exit(0)

        last_error = "Missing RedisHoneyPot log events: {}".format(", ".join(missing))
    except RuntimeError as exc:
        last_error = str(exc)

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
print("No complete RedisHoneyPot log event set found in {} for token {}".format(log_file, token), file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern="panic:|fatal error|Permission denied|Read-only file system|Address already in use|create log directory|open log file|redirect stdout|redirect stderr|exec RedisHoneyPot"

  if grep -R -I -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "RedisHoneyPot runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "RedisHoneyPot runtime error found in Docker logs"
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

  if [[ -n "${REDIS_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${REDIS_PORT}" || test_die "${TEST_BIND_IP}:${REDIS_PORT} is already in use. Try --redis-port <free-port>."
  fi

  prepare_redishoneypot_harness
  test_enable_cleanup

  test_info "Starting isolated RedisHoneyPot container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "RedisHoneyPot container did not stay running"
  test_ok "Container is running"

  MAPPED_REDIS_PORT="$(test_get_mapped_port "${TEST_NAME}" "6379")" || test_die "Could not resolve mapped host port for 6379/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_REDIS_PORT} maps to container port 6379/tcp"

  local token="redishoneypot-test-$(date +%s)-$$"

  test_info "Running RedisHoneyPot Redis probe with token: ${token}"
  run_redis_probe_with_retries "${token}" || test_die "RedisHoneyPot probe failed on ${TEST_BIND_IP}:${MAPPED_REDIS_PORT}"
  test_wait_for_container || test_die "RedisHoneyPot container stopped after Redis probe"

  test_info "Waiting for RedisHoneyPot JSON log events"
  wait_for_log_events "${token}" || test_die "Expected RedisHoneyPot events were not found in redishoneypot.log"
  test_ok "RedisHoneyPot command events were written to redishoneypot.log"

  assert_no_runtime_errors
  test_ok "No RedisHoneyPot runtime errors found in logs"

  test_ok "RedisHoneyPot post-build smoke test completed successfully"
}

main "$@"
