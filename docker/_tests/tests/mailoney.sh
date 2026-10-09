#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="mailoney"
DEFAULT_IMAGE="dtagdevsec/mailoney:24.04.2"
IMAGE=""
SMTP_PORT=""
SMTPS_PORT=""
SUBMISSION_PORT=""
LOG_DIR=""
MAPPED_SMTP_PORT=""
MAPPED_SMTPS_PORT=""
MAPPED_SUBMISSION_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Mailoney image.

Options:
  --image IMAGE            Image to test. Defaults to docker/mailoney/docker-compose.yml.
  --smtp-port PORT         Host TCP port for SMTP (25). Default: dynamic loopback port.
  --smtps-port PORT        Host TCP port for SMTPS (465). Default: dynamic loopback port.
  --submission-port PORT   Host TCP port for Submission (587). Default: dynamic loopback port.
  --timeout SEC            Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP             Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts         Keep temporary compose file and logs for debugging.
  -h, --help               Show this help message.
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
      --smtps-port)
        [[ $# -ge 2 ]] || test_die "--smtps-port requires an argument"
        SMTPS_PORT="$2"
        shift 2
        ;;
      --smtps-port=*)
        SMTPS_PORT="${1#*=}"
        shift
        ;;
      --submission-port)
        [[ $# -ge 2 ]] || test_die "--submission-port requires an argument"
        SUBMISSION_PORT="$2"
        shift 2
        ;;
      --submission-port=*)
        SUBMISSION_PORT="${1#*=}"
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
  local port

  test_validate_timeout

  for port in "${SMTP_PORT}" "${SMTPS_PORT}" "${SUBMISSION_PORT}"; do
    if [[ -n "${port}" ]]; then
      test_validate_port "${port}"
    fi
  done
}

port_mapping() {
  local host_port="$1"
  local container_port="$2"

  printf '%s:%s:%s' "${TEST_BIND_IP}" "${host_port}" "${container_port}"
}

prepare_mailoney_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  # Mailoney writes its files 0640 and its folders 0750; running as the caller
  # keeps them readable and removable here (T-Pot runs it as 2000).
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  mailoney:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "$(id -u):$(id -g)"
    ports:
      - "$(port_mapping "${SMTP_PORT}" 25)"
      - "$(port_mapping "${SMTPS_PORT}" 465)"
      - "$(port_mapping "${SUBMISSION_PORT}" 587)"
    volumes:
      - "${LOG_DIR}:/opt/mailoney/logs"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_mailoney_start_log() {
  local count="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if (( $(test_compose logs --no-color 2>/dev/null | grep -c -F "Starting SMTP Honeypot on 3 listener(s)") >= count )); then
      return 0
    fi
    sleep 1
  done

  return 1
}

resolve_ports() {
  MAPPED_SMTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "25")" || test_die "Could not resolve mapped host port for 25/tcp"
  MAPPED_SMTPS_PORT="$(test_get_mapped_port "${TEST_NAME}" "465")" || test_die "Could not resolve mapped host port for 465/tcp"
  MAPPED_SUBMISSION_PORT="$(test_get_mapped_port "${TEST_NAME}" "587")" || test_die "Could not resolve mapped host port for 587/tcp"
}

read_identity() {
  [[ -s "${LOG_DIR}/identity" ]] || test_die "Mailoney did not store its server name in identity"
  head -n 1 "${LOG_DIR}/identity"
}

cert_fingerprint() {
  python3 - "${LOG_DIR}/tls/server.crt" <<'PY'
import hashlib
import ssl
import sys

with open(sys.argv[1], encoding="ascii") as handle:
    print(hashlib.sha256(ssl.PEM_cert_to_DER_cert(handle.read())).hexdigest())
PY
}

# Plain SMTP with AUTH and a mail with an attachment on 25, STARTTLS on 587,
# implicit TLS on 465. Prints the sha256 of the attachment.
run_smtp_probe() {
  local token="$1"
  local identity="$2"

  python3 - "${TEST_BIND_IP}" "${MAPPED_SMTP_PORT}" "${MAPPED_SMTPS_PORT}" "${MAPPED_SUBMISSION_PORT}" \
    "${token}" "${identity}" "${TEST_TIMEOUT}" <<'PY'
import base64
import hashlib
import socket
import ssl
import sys
import time
from email.message import EmailMessage

host = sys.argv[1]
smtp_port, smtps_port, submission_port = (int(port) for port in sys.argv[2:5])
token = sys.argv[5]
identity = sys.argv[6]
timeout = int(sys.argv[7])
connect_timeout = max(1.0, min(float(timeout), 5.0))

sender = f"sender-{token}@example.org"
recipient = f"recipient-{token}@example.net"
attachment = f"payload {token}\n".encode("ascii")


class ProbeError(Exception):
    pass


def recv_line(sock, deadline):
    line = bytearray()
    while time.monotonic() < deadline:
        sock.settimeout(max(0.1, min(1.0, deadline - time.monotonic())))
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
    lines = [recv_line(sock, deadline)]
    while lines[-1][3:4] == b"-":
        lines.append(recv_line(sock, deadline))
    text = b"".join(lines).decode("utf-8", errors="replace")
    if not lines[0][:3].isdigit() or lines[0][:3].decode("ascii") != str(expected_code):
        raise ProbeError(f"expected SMTP {expected_code}, got {text!r}")
    return text


def send_command(sock, command, expected_code, deadline):
    sock.sendall(command.encode("ascii"))
    return read_response(sock, expected_code, deadline)


def check_banner(sock, deadline):
    banner = read_response(sock, 220, deadline)
    if f"220 {identity} ESMTP" not in banner:
        raise ProbeError(f"banner does not name the stored server name {identity!r}: {banner!r}")


def tls_context():
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def plain_session(deadline):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"smoke {token}"
    message.set_content(f"body {token}")
    message.add_attachment(attachment, maintype="application", subtype="octet-stream",
                           filename=f"{token}.bin")
    data = message.as_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")

    with socket.create_connection((host, smtp_port), timeout=connect_timeout) as sock:
        check_banner(sock, deadline)
        ehlo = send_command(sock, f"EHLO smtp-{token}.example\r\n", 250, deadline)
        for feature in ("STARTTLS", "AUTH PLAIN LOGIN"):
            if feature not in ehlo:
                raise ProbeError(f"EHLO on 25 does not offer {feature}: {ehlo!r}")
        send_command(sock, "AUTH LOGIN\r\n", 334, deadline)
        send_command(sock, base64.b64encode(f"user-{token}".encode()).decode() + "\r\n", 334, deadline)
        send_command(sock, base64.b64encode(f"pass-{token}".encode()).decode() + "\r\n", 235, deadline)
        send_command(sock, f"MAIL FROM:<{sender}>\r\n", 250, deadline)
        send_command(sock, f"RCPT TO:<{recipient}>\r\n", 250, deadline)
        send_command(sock, "DATA\r\n", 354, deadline)
        sock.sendall(data + b"\r\n.\r\n")
        read_response(sock, 250, deadline)
        send_command(sock, "QUIT\r\n", 221, deadline)


def starttls_session(deadline):
    with socket.create_connection((host, submission_port), timeout=connect_timeout) as raw:
        check_banner(raw, deadline)
        send_command(raw, f"EHLO submission-{token}.example\r\n", 250, deadline)
        send_command(raw, "STARTTLS\r\n", 220, deadline)
        with tls_context().wrap_socket(raw) as sock:
            ehlo = send_command(sock, f"EHLO submission-{token}.example\r\n", 250, deadline)
            if "STARTTLS" in ehlo:
                raise ProbeError(f"EHLO after STARTTLS still offers STARTTLS: {ehlo!r}")
            send_command(sock, "QUIT\r\n", 221, deadline)


def implicit_tls_session(deadline):
    with socket.create_connection((host, smtps_port), timeout=connect_timeout) as raw:
        with tls_context().wrap_socket(raw) as sock:
            check_banner(sock, deadline)
            send_command(sock, f"EHLO smtps-{token}.example\r\n", 250, deadline)
            send_command(sock, "QUIT\r\n", 221, deadline)


deadline = time.monotonic() + timeout
try:
    plain_session(deadline)
    starttls_session(deadline)
    implicit_tls_session(deadline)
except Exception as exc:
    print(f"Mailoney SMTP probe failed: {exc}", file=sys.stderr)
    sys.exit(1)

print(hashlib.sha256(attachment).hexdigest())
PY
}

run_smtp_probe_with_retries() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_smtp_probe "$@" 2>&1)"; then
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
  local attachment_sha256="$2"

  python3 - "${LOG_DIR}" "${token}" "${attachment_sha256}" "${TEST_TIMEOUT}" <<'PY'
import hashlib
import json
import sys
import time
from pathlib import Path

log_dir = Path(sys.argv[1])
token = sys.argv[2]
attachment_sha256 = sys.argv[3]
timeout = int(sys.argv[4])
log_file = log_dir / "log.json"
mail_dir = log_dir / "mails"

sender = f"sender-{token}@example.org"
recipient = f"recipient-{token}@example.net"


def read_events():
    events = []
    for line_number, line in enumerate(log_file.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.exit(f"Invalid JSON in {log_file}:{line_number}: {exc}")
        for field in ("event.type", "timestamp", "session_id", "src_ip", "src_port", "dest_port",
                      "listener.name", "session_outcome"):
            if field not in event:
                sys.exit(f"Missing {field!r} in {log_file}:{line_number}")
        events.append(event)
    return events


def stored(relative, sha256=None):
    path = mail_dir / relative
    if not path.is_file():
        return False
    return sha256 is None or hashlib.sha256(path.read_bytes()).hexdigest() == sha256


def mine(event, listener):
    return event["listener.name"] == listener and token in event.get("smtp_input", "")


def validate(events):
    auth = [e for e in events if e["event.type"] == "auth" and mine(e, "smtp")]
    mail = [e for e in events if e["event.type"] == "mail" and mine(e, "smtp")]
    submission = [e for e in events if e["event.type"] == "session" and mine(e, "submission")]
    smtps = [e for e in events if e["event.type"] == "session" and mine(e, "smtps")]
    checks = {
        "auth event with the credentials": any(
            e.get("auth.method") == "login" and e.get("auth.username") == f"user-{token}"
            and e.get("auth.password") == f"pass-{token}" for e in auth),
        "mail event with envelope and subject": any(
            e.get("mail.envelope_from") == sender and e.get("mail.envelope_to") == [recipient]
            and e.get("mail.subject") == f"smoke {token}" and e.get("dest_port") == 25
            and e.get("session_outcome") == "ok" and e.get("tls.used") is False for e in mail),
        "attachment with its sha256": any(
            e.get("attachment.count") == 1 and e.get("attachment.sha256") == [attachment_sha256]
            and e.get("attachment.filenames") == [f"{token}.bin"] for e in mail),
        "stored mail and attachment": any(
            stored(e.get("mail.eml_file", "")) and stored(e["attachment.files"][0], attachment_sha256)
            for e in mail if e.get("attachment.files")),
        "STARTTLS session on 587": any(
            e.get("tls.used") is True and e.get("listener.tls_mode") == "starttls"
            and e.get("dest_port") == 587 for e in submission),
        "implicit TLS session on 465": any(
            e.get("tls.used") is True and e.get("listener.tls_mode") == "implicit"
            and e.get("dest_port") == 465 for e in smtps),
    }
    return [name for name, ok in checks.items() if not ok]


deadline = time.monotonic() + timeout
last_error = f"{log_file} does not exist yet"
while time.monotonic() < deadline:
    if log_file.exists():
        missing = validate(read_events())
        if not missing:
            print(f"Mailoney log.json contains the probe events for token {token}")
            sys.exit(0)
        last_error = "Missing Mailoney log events: " + ", ".join(missing)
    time.sleep(1)

sys.exit(last_error)
PY
}

assert_no_runtime_errors() {
  local pattern="Traceback|ImportError|ModuleNotFoundError|PermissionError|Permission denied|Address already in use|Unhandled Error|Exception|WARNING: cannot store"

  if grep -I -E "${pattern}" "${LOG_DIR}/log.json" >/dev/null 2>&1; then
    test_die "Mailoney runtime error found in log.json"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Mailoney runtime error found in Docker logs"
  fi
}

main() {
  local port

  parse_args "$@"
  validate_args
  test_check_dependencies

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  for port in "${SMTP_PORT}" "${SMTPS_PORT}" "${SUBMISSION_PORT}"; do
    if [[ -n "${port}" ]]; then
      test_ensure_port_free "${TEST_BIND_IP}" "${port}" || test_die "${TEST_BIND_IP}:${port} is already in use. Pick another one with the port options."
    fi
  done

  prepare_mailoney_harness
  test_enable_cleanup

  test_info "Starting isolated Mailoney container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Mailoney container did not stay running"
  test_ok "Container is running"

  wait_for_mailoney_start_log 1 || test_die "Mailoney did not start its three listeners"
  resolve_ports
  test_ok "Ports ${TEST_BIND_IP}:${MAPPED_SMTP_PORT} / ${MAPPED_SMTPS_PORT} / ${MAPPED_SUBMISSION_PORT} map to 25 / 465 / 587"

  local identity
  identity="$(read_identity)"
  [[ "${identity}" =~ ^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*){1,3}$ ]] || test_die "Stored server name is no host name: ${identity}"
  test_ok "Server name of this install: ${identity}"

  local token="mailoney-test-$(date +%s)-$$"
  local attachment_sha256

  test_info "Running Mailoney SMTP, STARTTLS and SMTPS probes with token: ${token}"
  attachment_sha256="$(run_smtp_probe_with_retries "${token}" "${identity}" | tail -n 1)" \
    || test_die "Mailoney SMTP probe failed on ${TEST_BIND_IP}"
  test_wait_for_container || test_die "Mailoney container stopped after SMTP probe"

  test_info "Waiting for Mailoney JSON log events"
  wait_for_log_events "${token}" "${attachment_sha256}" || test_die "Expected Mailoney events were not found in log.json"
  test_ok "Mailoney logged auth, mail and TLS sessions and stored the mail with its attachment"

  local fingerprint
  fingerprint="$(cert_fingerprint)"
  test_info "Restarting Mailoney"
  test_compose restart >/dev/null
  test_wait_for_container || test_die "Mailoney container did not stay running after the restart"
  wait_for_mailoney_start_log 2 || test_die "Mailoney did not start again after the restart"
  resolve_ports
  [[ "$(read_identity)" == "${identity}" ]] || test_die "Server name changed after the restart"
  [[ "$(cert_fingerprint)" == "${fingerprint}" ]] || test_die "TLS certificate changed after the restart"
  run_smtp_probe_with_retries "${token}-again" "${identity}" >/dev/null || test_die "Mailoney SMTP probe failed after the restart"
  test_ok "Server name and TLS certificate survive a restart"

  assert_no_runtime_errors
  test_ok "No Mailoney runtime errors found in logs"

  test_ok "Mailoney post-build smoke test completed successfully"
}

main "$@"
