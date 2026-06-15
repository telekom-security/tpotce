#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="mailoney"
DEFAULT_IMAGE="dtagdevsec/mailoney:24.04.1"
IMAGE=""
SMTP_PORT=""
LOG_DIR=""
COMMANDS_LOG_FILE=""
MAIL_LOG_FILE=""
MAPPED_SMTP_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Mailoney image.

Options:
  --image IMAGE      Image to test. Defaults to docker/mailoney/docker-compose.yml.
  --smtp-port PORT   Host TCP port for SMTP. Default: dynamic loopback port.
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
      --smtp-port)
        [[ $# -ge 2 ]] || test_die "--smtp-port requires an argument"
        SMTP_PORT="$2"
        shift 2
        ;;
      --smtp-port=*)
        SMTP_PORT="${1#*=}"
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

  if [[ -n "${SMTP_PORT}" ]]; then
    test_validate_port "${SMTP_PORT}"
  fi
}

prepare_mailoney_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  COMMANDS_LOG_FILE="${LOG_DIR}/commands.log"
  MAIL_LOG_FILE="${LOG_DIR}/mail.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  local port_mapping="${TEST_BIND_IP}::25"
  if [[ -n "${SMTP_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${SMTP_PORT}:25"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  mailoney:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    environment:
      PYTHONUNBUFFERED: "1"
    ports:
      - "${port_mapping}"
    volumes:
      - "${LOG_DIR}:/opt/mailoney/logs"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_mailoney_start_log() {
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if test_compose logs --no-color 2>/dev/null | grep -F "Mail Relay listening" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  return 1
}

run_smtp_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_SMTP_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
connect_timeout = max(1.0, min(float(timeout), 5.0))

sender = f"sender-{token}@example.org"
recipient = f"recipient-{token}@example.net"
subject = f"smoke {token}"
body = f"body {token}"


class ProbeError(Exception):
    pass


def recv_line(sock, deadline):
    line = bytearray()
    while time.monotonic() < deadline:
        remaining = max(0.1, min(1.0, deadline - time.monotonic()))
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(1)
        except socket.timeout:
            continue
        if not chunk:
            raise ProbeError(f"connection closed while waiting for SMTP line; received {bytes(line)!r}")
        line.extend(chunk)
        if chunk == b"\n":
            return bytes(line)
    raise ProbeError(f"timed out waiting for SMTP line; received {bytes(line)!r}")


def read_response(sock, expected_code, deadline):
    lines = []

    while True:
        first = recv_line(sock, deadline)
        if first in {b".\n", b".\r\n"}:
            continue
        break

    lines.append(first)
    if len(first) < 4 or not first[:3].isdigit():
        raise ProbeError(f"malformed SMTP response line: {first!r}")

    code = first[:3].decode("ascii")
    if first[3:4] == b"-":
        expected_prefix = first[:3] + b" "
        while True:
            line = recv_line(sock, deadline)
            lines.append(line)
            if line.startswith(expected_prefix):
                break

    if code != str(expected_code):
        decoded = b"".join(lines).decode("utf-8", errors="replace")
        raise ProbeError(f"expected SMTP {expected_code}, got {code}: {decoded!r}")

    return b"".join(lines).decode("utf-8", errors="replace")


def send_command(sock, command, expected_code, deadline):
    sock.sendall(command.encode("ascii"))
    return read_response(sock, expected_code, deadline)


deadline = time.monotonic() + timeout

try:
    with socket.create_connection((host, port), timeout=connect_timeout) as sock:
        banner = read_response(sock, 220, deadline)
        if "ESMTP" not in banner:
            raise ProbeError(f"unexpected Mailoney banner: {banner!r}")

        ehlo_response = send_command(sock, f"EHLO {token}.example\r\n", 250, deadline)
        if "AUTH LOGIN PLAIN" not in ehlo_response:
            raise ProbeError(f"EHLO response did not advertise AUTH LOGIN PLAIN: {ehlo_response!r}")

        send_command(sock, f"MAIL FROM:<{sender}>\r\n", 250, deadline)
        send_command(sock, f"RCPT TO:<{recipient}>\r\n", 250, deadline)
        send_command(sock, "DATA\r\n", 354, deadline)
        message = f"Subject: {subject}\r\n\r\n{body}\r\n.\r\n"
        send_command(sock, message, 250, deadline)
        send_command(sock, "QUIT\r\n", 221, deadline)
except Exception as exc:
    print(f"Mailoney SMTP probe failed: {exc}", file=sys.stderr)
    sys.exit(1)

print(f"Mailoney SMTP probe succeeded for token {token}")
PY
}

run_smtp_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_smtp_probe "${token}" 2>&1)"; then
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

  python3 - "${COMMANDS_LOG_FILE}" "${MAIL_LOG_FILE}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

commands_log = Path(sys.argv[1])
mail_log = Path(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])

sender = f"sender-{token}@example.org"
recipient = f"recipient-{token}@example.net"
subject = f"smoke {token}"
body = f"body {token}"


def read_events(path):
    events = []
    if not path.exists():
        return None

    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue

        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON in {path}:{line_number}: {exc}", file=sys.stderr)
            sys.exit(1)

        if not isinstance(event, dict):
            print(f"JSON event in {path}:{line_number} is not an object", file=sys.stderr)
            sys.exit(1)

        for field in ("timestamp", "src_ip", "src_port", "data"):
            if field not in event:
                print(f"Missing {field!r} in {path}:{line_number}", file=sys.stderr)
                sys.exit(1)

        if not isinstance(event.get("data"), str):
            print(f"Field 'data' in {path}:{line_number} is not a string", file=sys.stderr)
            sys.exit(1)

        events.append(event)

    return events


def data_contains(events, *needles):
    return any(all(needle in event.get("data", "") for needle in needles) for event in events)


def data_startswith(events, prefix):
    return any(event.get("data", "").startswith(prefix) for event in events)


def event_has_email(events, data_needle, email):
    for event in events:
        if data_needle not in event.get("data", ""):
            continue
        emails = event.get("emails", [])
        if isinstance(emails, list) and email in emails:
            return True
    return False


def validate(commands, mail):
    command_checks = {
        "EHLO command": data_contains(commands, "EHLO", token),
        "MAIL FROM command": event_has_email(commands, "MAIL FROM:", sender),
        "RCPT TO command": event_has_email(commands, "RCPT TO:", recipient),
        "DATA command": data_startswith(commands, "DATA"),
        "message content command": data_contains(commands, subject, body),
        "QUIT command": data_startswith(commands, "QUIT"),
    }

    mail_checks = {
        "Mail from event": event_has_email(mail, f"Mail from: {sender}", sender),
        "Mail to event": event_has_email(mail, f"Mail to: {recipient}", recipient),
        "mail body event": data_contains(mail, subject, body),
    }

    missing = [name for name, ok in {**command_checks, **mail_checks}.items() if not ok]
    return missing


deadline = time.monotonic() + timeout
last_error = None

while time.monotonic() < deadline:
    commands = read_events(commands_log)
    mail = read_events(mail_log)

    if commands is None:
        last_error = f"{commands_log} does not exist yet"
        time.sleep(1)
        continue

    if mail is None:
        last_error = f"{mail_log} does not exist yet"
        time.sleep(1)
        continue

    missing = validate(commands, mail)
    if not missing:
        print(f"Mailoney commands.log and mail.log contain SMTP probe token {token}")
        sys.exit(0)

    last_error = "Missing Mailoney log events: " + ", ".join(missing)
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern="Traceback|ImportError|ModuleNotFoundError|PermissionError|Permission denied|Address already in use|Unhandled Error|Exception"

  if grep -R -I -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Mailoney runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Mailoney runtime error found in Docker logs"
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

  if [[ -n "${SMTP_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${SMTP_PORT}" || test_die "${TEST_BIND_IP}:${SMTP_PORT} is already in use. Try --smtp-port <free-port>."
  fi

  prepare_mailoney_harness
  test_enable_cleanup

  test_info "Starting isolated Mailoney container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Mailoney container did not stay running"
  test_ok "Container is running"

  wait_for_mailoney_start_log || test_die "Mailoney start log was not emitted"

  MAPPED_SMTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "25")" || test_die "Could not resolve mapped host port for 25/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_SMTP_PORT} maps to container port 25/tcp"

  local token="mailoney-test-$(date +%s)-$$"

  test_info "Running Mailoney SMTP probe with token: ${token}"
  run_smtp_probe_with_retries "${token}" || test_die "Mailoney SMTP probe failed on ${TEST_BIND_IP}:${MAPPED_SMTP_PORT}"
  test_wait_for_container || test_die "Mailoney container stopped after SMTP probe"

  test_info "Waiting for Mailoney JSON log events"
  wait_for_log_events "${token}" || test_die "Expected Mailoney SMTP events were not found in commands.log and mail.log"
  test_ok "Mailoney SMTP events were written to commands.log and mail.log"

  assert_no_runtime_errors
  test_ok "No Mailoney runtime errors found in logs"

  test_ok "Mailoney post-build smoke test completed successfully"
}

main "$@"
