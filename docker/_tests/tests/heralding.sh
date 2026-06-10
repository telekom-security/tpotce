#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="heralding"
DEFAULT_IMAGE="dtagdevsec/heralding:24.04.1"
IMAGE=""
LOG_DIR=""
HERALDING_LOG_FILE=""
TOKEN=""

PROBED_PROTOCOLS=(ftp telnet pop3 pop3s imap imaps smtp smtps http https socks5)
declare -A CONTAINER_PORTS=(
  [ftp]=21
  [telnet]=23
  [pop3]=110
  [pop3s]=995
  [imap]=143
  [imaps]=993
  [smtp]=25
  [smtps]=465
  [http]=80
  [https]=443
  [socks5]=1080
)
declare -A MAPPED_PORTS=()

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Heralding image.

Options:
  --image IMAGE       Image to test. Defaults to dtagdevsec/heralding:24.04.1.
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

prepare_heralding_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  HERALDING_LOG_FILE="${LOG_DIR}/heralding.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  heralding:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    tmpfs:
      - /tmp/heralding:uid=2000,gid=2000,mode=0777
    ports:
      - "${TEST_BIND_IP}::21"
      - "${TEST_BIND_IP}::23"
      - "${TEST_BIND_IP}::25"
      - "${TEST_BIND_IP}::80"
      - "${TEST_BIND_IP}::110"
      - "${TEST_BIND_IP}::143"
      - "${TEST_BIND_IP}::443"
      - "${TEST_BIND_IP}::465"
      - "${TEST_BIND_IP}::993"
      - "${TEST_BIND_IP}::995"
      - "${TEST_BIND_IP}::1080"
    volumes:
      - "${LOG_DIR}:/var/log/heralding"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_heralding_start_log() {
  test_wait_for_file_text "Initializing Heralding version" "${LOG_DIR}"
}

resolve_mapped_ports() {
  local protocol=""
  local container_port=""

  for protocol in "${PROBED_PROTOCOLS[@]}"; do
    container_port="${CONTAINER_PORTS[${protocol}]}"
    MAPPED_PORTS["${protocol}"]="$(test_get_mapped_port "${TEST_NAME}" "${container_port}")" \
      || test_die "Could not resolve mapped host port for ${container_port}/tcp"
    test_ok "Port ${TEST_BIND_IP}:${MAPPED_PORTS[${protocol}]} maps to ${protocol} container port ${container_port}/tcp"
  done
}

run_protocol_probes() {
  local args=("${TEST_BIND_IP}" "${TEST_TIMEOUT}" "${TOKEN}")
  local protocol=""

  for protocol in "${PROBED_PROTOCOLS[@]}"; do
    args+=("${protocol}=${MAPPED_PORTS[${protocol}]}")
  done

  python3 - "${args[@]}" <<'PY'
import base64
import ftplib
import http.client
import socket
import ssl
import smtplib
import sys
import time

host = sys.argv[1]
timeout = int(sys.argv[2])
token = sys.argv[3]
ports = {}
for item in sys.argv[4:]:
    protocol, port = item.split("=", 1)
    ports[protocol] = int(port)

connect_timeout = max(1.0, min(float(timeout), 5.0))
context = ssl._create_unverified_context()


class ProbeError(Exception):
    pass


def credentials(protocol):
    return f"{protocol}-user-{token}", f"{protocol}-pass-{token}"


def retry(protocol, probe):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            probe()
            print(f"{protocol} probe succeeded")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise ProbeError(f"{protocol} probe failed: {last_error}")


def connect(port):
    return socket.create_connection((host, port), timeout=connect_timeout)


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ProbeError(f"Connection closed while waiting for {size} bytes")
        data.extend(chunk)
    return bytes(data)


def recv_line(sock, deadline):
    data = bytearray()
    while time.monotonic() < deadline:
        remaining = max(0.1, min(1.0, deadline - time.monotonic()))
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(1)
        except socket.timeout:
            continue
        if not chunk:
            break
        data.extend(chunk)
        if chunk == b"\n":
            break
    if not data:
        raise ProbeError("Timed out waiting for line")
    return bytes(data)


def ftp_probe(protocol):
    username, password = credentials(protocol)
    ftp = ftplib.FTP()
    try:
        ftp.connect(host, ports[protocol], timeout=connect_timeout)
        try:
            ftp.login(username, password)
        except ftplib.error_perm:
            return
        raise ProbeError("FTP accepted smoke-test credentials unexpectedly")
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def telnet_probe():
    protocol = "telnet"
    username, password = credentials(protocol)
    deadline = time.monotonic() + timeout
    iac, dont, do, wont, will, sb, se = 255, 254, 253, 252, 251, 250, 240

    def read_until(sock, needles):
        data = bytearray()
        in_subnegotiation = False
        while time.monotonic() < deadline:
            sock.settimeout(max(0.1, min(1.0, deadline - time.monotonic())))
            try:
                chunk = sock.recv(256)
            except socket.timeout:
                continue
            if not chunk:
                break

            index = 0
            while index < len(chunk):
                byte = chunk[index]
                if byte == iac and index + 1 < len(chunk):
                    command = chunk[index + 1]
                    if command in (do, dont, will, wont) and index + 2 < len(chunk):
                        option = chunk[index + 2]
                        sock.sendall(bytes([iac, wont if command in (do, dont) else dont, option]))
                        index += 3
                        continue
                    if command == sb:
                        in_subnegotiation = True
                        index += 2
                        continue
                    if command == se:
                        in_subnegotiation = False
                        index += 2
                        continue
                    index += 2
                    continue

                if not in_subnegotiation:
                    data.append(byte)
                index += 1

            text = data.decode("utf-8", errors="ignore").lower()
            if any(needle in text for needle in needles):
                return text

        raise ProbeError(f"Timed out waiting for telnet prompt {needles}; received {bytes(data)!r}")

    with connect(ports[protocol]) as sock:
        for _ in range(3):
            read_until(sock, ("username:", "login:"))
            sock.sendall((username + "\r\n").encode("ascii"))
            read_until(sock, ("password:",))
            sock.sendall((password + "\r\n").encode("ascii"))
        time.sleep(0.2)


def pop3_probe(protocol, use_ssl):
    username, password = credentials(protocol)
    raw_sock = connect(ports[protocol])
    with context.wrap_socket(raw_sock, server_hostname=host) if use_ssl else raw_sock as sock:
        deadline = time.monotonic() + timeout
        banner = recv_line(sock, deadline)
        if not banner.startswith(b"+OK"):
            raise ProbeError(f"Unexpected POP3 banner: {banner!r}")
        sock.sendall(f"USER {username}\r\n".encode("ascii"))
        response = recv_line(sock, deadline)
        if not response.startswith(b"+OK"):
            raise ProbeError(f"Unexpected POP3 USER response: {response!r}")
        sock.sendall(f"PASS {password}\r\n".encode("ascii"))
        response = recv_line(sock, deadline)
        if not response.startswith(b"-ERR"):
            raise ProbeError(f"Unexpected POP3 PASS response: {response!r}")
        try:
            sock.sendall(b"QUIT\r\n")
        except OSError:
            pass


def imap_probe(protocol, use_ssl):
    username, password = credentials(protocol)
    raw_sock = connect(ports[protocol])
    with context.wrap_socket(raw_sock, server_hostname=host) if use_ssl else raw_sock as sock:
        deadline = time.monotonic() + timeout
        banner = recv_line(sock, deadline)
        if not banner.startswith(b"* OK"):
            raise ProbeError(f"Unexpected IMAP banner: {banner!r}")
        sock.sendall(f'a001 LOGIN "{username}" "{password}"\r\n'.encode("ascii"))
        while time.monotonic() < deadline:
            response = recv_line(sock, deadline)
            if response.lower().startswith(b"a001 "):
                if b"authentication failed" not in response.lower():
                    raise ProbeError(f"Unexpected IMAP LOGIN response: {response!r}")
                break
        else:
            raise ProbeError("Timed out waiting for IMAP LOGIN response")
        try:
            sock.sendall(b"a002 LOGOUT\r\n")
        except OSError:
            pass


def smtp_probe(protocol, use_ssl):
    username, password = credentials(protocol)
    auth = base64.b64encode(f"\0{username}\0{password}".encode("utf-8")).decode("ascii")
    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    kwargs = {"timeout": connect_timeout}
    if use_ssl:
        kwargs["context"] = context
    client = smtp_cls(host, ports[protocol], local_hostname="localhost", **kwargs)
    try:
        client.ehlo("localhost")
        code, response = client.docmd("AUTH", "PLAIN " + auth)
        if code != 535:
            raise ProbeError(f"Unexpected SMTP AUTH response: {code} {response!r}")
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def http_probe(protocol, use_ssl):
    username, password = credentials(protocol)
    auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    conn_cls = http.client.HTTPSConnection if use_ssl else http.client.HTTPConnection
    kwargs = {"timeout": connect_timeout}
    if use_ssl:
        kwargs["context"] = context
    conn = conn_cls(host, ports[protocol], **kwargs)
    try:
        conn.request(
            "GET",
            f"/tpot-heralding-smoke/{token}",
            headers={
                "Authorization": "Basic " + auth,
                "User-Agent": "tpot-heralding-smoke/" + token,
                "Connection": "close",
            },
        )
        response = conn.getresponse()
        response.read()
        if response.status != 401:
            raise ProbeError(f"Expected HTTP 401, got {response.status}")
    finally:
        conn.close()


def socks5_probe():
    protocol = "socks5"
    username, password = credentials(protocol)
    username_bytes = username.encode("utf-8")
    password_bytes = password.encode("utf-8")
    if len(username_bytes) > 255 or len(password_bytes) > 255:
        raise ProbeError("SOCKS5 credentials are too long")
    with connect(ports[protocol]) as sock:
        sock.settimeout(connect_timeout)
        sock.sendall(b"\x05\x01\x02")
        response = recv_exact(sock, 2)
        if response != b"\x05\x02":
            raise ProbeError(f"Unexpected SOCKS5 method response: {response!r}")
        packet = b"\x01" + bytes([len(username_bytes)]) + username_bytes + bytes([len(password_bytes)]) + password_bytes
        sock.sendall(packet)
        response = recv_exact(sock, 2)
        if response != b"\x02\xff":
            raise ProbeError(f"Unexpected SOCKS5 auth response: {response!r}")


retry("ftp", lambda: ftp_probe("ftp"))
retry("telnet", telnet_probe)
retry("pop3", lambda: pop3_probe("pop3", False))
retry("pop3s", lambda: pop3_probe("pop3s", True))
retry("imap", lambda: imap_probe("imap", False))
retry("imaps", lambda: imap_probe("imaps", True))
retry("smtp", lambda: smtp_probe("smtp", False))
retry("smtps", lambda: smtp_probe("smtps", True))
retry("http", lambda: http_probe("http", False))
retry("https", lambda: http_probe("https", True))
retry("socks5", socks5_probe)
PY
}

wait_for_log_events() {
  python3 - "${LOG_DIR}" "${TOKEN}" "${TEST_TIMEOUT}" "${PROBED_PROTOCOLS[@]}" <<'PY'
import csv
import json
import sys
import time
from pathlib import Path

log_dir = Path(sys.argv[1])
token = sys.argv[2]
timeout = int(sys.argv[3])
protocols = sys.argv[4:]
auth_log = log_dir / "auth.csv"
session_log = log_dir / "log_session.json"
deadline = time.monotonic() + timeout
last_error = None


def credentials(protocol):
    return f"{protocol}-user-{token}", f"{protocol}-pass-{token}"


def read_auth_protocols():
    found = set()
    if not auth_log.exists():
        return found, f"{auth_log} does not exist yet"

    try:
        with auth_log.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                protocol = row.get("protocol", "")
                if protocol not in protocols:
                    continue
                username, password = credentials(protocol)
                if row.get("username") == username and row.get("password") == password:
                    found.add(protocol)
    except OSError as exc:
        return found, f"Could not read {auth_log}: {exc}"

    return found, None


def contains_token(value):
    if isinstance(value, str):
        return token in value
    if isinstance(value, dict):
        return any(contains_token(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_token(item) for item in value)
    return False


def read_session_protocols():
    found = set()
    if not session_log.exists():
        return found, f"{session_log} does not exist yet"

    try:
        lines = session_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return found, f"Could not read {session_log}: {exc}"

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            return found, f"Invalid JSON in {session_log}:{line_number}: {exc}"
        protocol = event.get("protocol")
        if (
            protocol in protocols
            and event.get("session_ended") is True
            and event.get("num_auth_attempts", 0) >= 1
            and contains_token(event.get("auth_attempts", []))
        ):
            found.add(protocol)

    return found, None


while time.monotonic() < deadline:
    auth_found, auth_error = read_auth_protocols()
    session_found, session_error = read_session_protocols()

    missing_auth = sorted(set(protocols) - auth_found)
    missing_sessions = sorted(set(protocols) - session_found)
    if not missing_auth and not missing_sessions:
        print(f"Heralding auth/session logs found for: {', '.join(protocols)}")
        sys.exit(0)

    details = []
    if missing_auth:
        details.append("missing auth.csv entries for " + ", ".join(missing_auth))
    if missing_sessions:
        details.append("missing log_session.json entries for " + ", ".join(missing_sessions))
    if auth_error:
        details.append(auth_error)
    if session_error:
        details.append(session_error)
    last_error = "; ".join(details)
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern="Traceback|RuntimeError|ModuleNotFoundError|OSError:|Exception in callback|Unhandled exception"

  if [[ -f "${HERALDING_LOG_FILE}" ]] && grep -E "${pattern}" "${HERALDING_LOG_FILE}" >/dev/null 2>&1; then
    test_die "Heralding runtime error found in heralding.log"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Heralding runtime error found in Docker logs"
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

  prepare_heralding_harness
  test_enable_cleanup

  test_info "Starting isolated Heralding container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Heralding container did not stay running"
  test_ok "Container is running"

  wait_for_heralding_start_log || test_die "Heralding startup log was not written"
  test_ok "Heralding startup log was written"

  resolve_mapped_ports

  TOKEN="heralding-smoke-$(date +%s)-$$"
  test_info "Running Heralding protocol probes with token: ${TOKEN}"
  run_protocol_probes || test_die "One or more Heralding protocol probes failed"
  test_wait_for_container || test_die "Heralding container stopped after protocol probes"

  test_info "Waiting for Heralding auth.csv and log_session.json events"
  wait_for_log_events || test_die "Expected Heralding credential/session log events were not found"
  test_ok "Heralding credential/session log events were written"

  assert_no_runtime_errors
  test_ok "No Heralding runtime errors found in logs"

  test_ok "Heralding post-build smoke test completed successfully"
}

main "$@"
