#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="honeypots"
DEFAULT_IMAGE="dtagdevsec/honeypots:24.04.1"
IMAGE=""
CONFIG_FILE="${DOCKER_ROOT}/honeypots/dist/config.json"
LOG_DIR=""

PROBED_PROTOCOLS=(ftp telnet pop3 imap smtp http https socks5 elastic)
declare -A CONFIG_PORTS=()
declare -A CONFIG_USERS=()
declare -A CONFIG_PASSWORDS=()
declare -A CONFIG_LOG_FILES=()
declare -A MAPPED_PORTS=()

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Honeypots image.

Options:
  --image IMAGE       Image to test. Defaults to docker/honeypots/docker-compose.yml.
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
  [[ -f "${CONFIG_FILE}" ]] || test_die "Honeypots config not found: ${CONFIG_FILE}"
}

load_config_summary() {
  local protocol=""
  local port=""
  local username=""
  local password=""
  local log_file=""

  while IFS=$'\t' read -r protocol port username password log_file; do
    CONFIG_PORTS["${protocol}"]="${port}"
    CONFIG_USERS["${protocol}"]="${username}"
    CONFIG_PASSWORDS["${protocol}"]="${password}"
    CONFIG_LOG_FILES["${protocol}"]="${log_file}"
  done < <(
    python3 - "${CONFIG_FILE}" "${PROBED_PROTOCOLS[@]}" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
protocols = sys.argv[2:]

try:
    config = json.loads(config_path.read_text())
except Exception as exc:
    print(f"Could not parse {config_path}: {exc}", file=sys.stderr)
    sys.exit(1)

logs = {item.strip() for item in str(config.get("logs", "")).split(",") if item.strip()}
missing_logs = {"file", "json", "tpot"} - logs
if missing_logs:
    print(f"config logs is missing: {', '.join(sorted(missing_logs))}", file=sys.stderr)
    sys.exit(1)

if config.get("logs_location") != "/var/log/honeypots/":
    print(
        f"Unexpected logs_location: {config.get('logs_location')!r}",
        file=sys.stderr,
    )
    sys.exit(1)

custom_filter = config.get("custom_filter", {}).get("honeypots", {})
if custom_filter.get("change", {}).get("server") != "protocol":
    print("custom_filter must rename server to protocol", file=sys.stderr)
    sys.exit(1)

required_fields = {"protocol", "action", "src_ip", "src_port", "dest_ip", "dest_port"}
contains = set(custom_filter.get("contains", []))
missing_fields = required_fields - contains
if missing_fields:
    print(
        f"custom_filter contains is missing: {', '.join(sorted(missing_fields))}",
        file=sys.stderr,
    )
    sys.exit(1)

honeypots = config.get("honeypots", {})
if not isinstance(honeypots, dict):
    print("config honeypots must be an object", file=sys.stderr)
    sys.exit(1)

for protocol in protocols:
    entry = honeypots.get(protocol)
    if not isinstance(entry, dict):
        print(f"Missing honeypot config for {protocol}", file=sys.stderr)
        sys.exit(1)

    port = entry.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        print(f"Invalid port for {protocol}: {port!r}", file=sys.stderr)
        sys.exit(1)

    log_file = entry.get("log_file_name")
    if not isinstance(log_file, str) or not log_file:
        print(f"Missing log_file_name for {protocol}", file=sys.stderr)
        sys.exit(1)

    if protocol in {"ftp", "imap", "pop3", "smtp", "http", "https"}:
        options = entry.get("options", [])
        if "capture_commands" not in options:
            print(f"{protocol} must have capture_commands enabled", file=sys.stderr)
            sys.exit(1)

    username = entry.get("username", "")
    password = entry.get("password", "")
    if not isinstance(username, str) or not isinstance(password, str):
        print(f"Credentials for {protocol} must be strings", file=sys.stderr)
        sys.exit(1)

    print(f"{protocol}\t{port}\t{username}\t{password}\t{log_file}")
PY
  )
}

prepare_honeypots_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  honeypots:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    tmpfs:
      - /tmp:uid=2000,gid=2000,mode=0777
    ports:
      - "${TEST_BIND_IP}::21"
      - "${TEST_BIND_IP}::23"
      - "${TEST_BIND_IP}::25"
      - "${TEST_BIND_IP}::80"
      - "${TEST_BIND_IP}::110"
      - "${TEST_BIND_IP}::143"
      - "${TEST_BIND_IP}::443"
      - "${TEST_BIND_IP}::1080"
      - "${TEST_BIND_IP}::9200"
    volumes:
      - "${CONFIG_FILE}:/opt/honeypots/config.json:ro"
      - "${LOG_DIR}:/var/log/honeypots"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

resolve_mapped_ports() {
  local protocol=""
  local container_port=""

  for protocol in "${PROBED_PROTOCOLS[@]}"; do
    container_port="${CONFIG_PORTS[${protocol}]}"
    MAPPED_PORTS["${protocol}"]="$(test_get_mapped_port "${TEST_NAME}" "${container_port}")" \
      || test_die "Could not resolve mapped host port for ${container_port}/tcp"
    test_ok "Port ${TEST_BIND_IP}:${MAPPED_PORTS[${protocol}]} maps to ${protocol} container port ${container_port}/tcp"
  done
}

run_protocol_probes() {
  local args=("${TEST_BIND_IP}" "${TEST_TIMEOUT}" "${CONFIG_FILE}")
  local protocol=""

  for protocol in "${PROBED_PROTOCOLS[@]}"; do
    args+=("${protocol}=${MAPPED_PORTS[${protocol}]}")
  done

  python3 - "${args[@]}" <<'PY'
import base64
import ftplib
import http.client
import imaplib
import json
import poplib
import socket
import ssl
import smtplib
import sys
import time
import urllib.parse
from pathlib import Path

host = sys.argv[1]
timeout = int(sys.argv[2])
config_path = Path(sys.argv[3])
mapped_ports = {}
for item in sys.argv[4:]:
    protocol, port = item.split("=", 1)
    mapped_ports[protocol] = int(port)

config = json.loads(config_path.read_text())
honeypots = config["honeypots"]
connect_timeout = max(1.0, min(float(timeout), 5.0))
ssl_context = ssl._create_unverified_context()


class ProbeError(Exception):
    pass


def credentials(protocol):
    entry = honeypots[protocol]
    return entry.get("username", ""), entry.get("password", "")


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


def connect(protocol):
    return socket.create_connection((host, mapped_ports[protocol]), timeout=connect_timeout)


def recv_until(sock, needles, deadline):
    data = bytearray()
    lower_needles = [needle.lower() for needle in needles]
    while time.monotonic() < deadline:
        sock.settimeout(max(0.1, min(1.0, deadline - time.monotonic())))
        try:
            chunk = sock.recv(256)
        except socket.timeout:
            continue
        if not chunk:
            break
        data.extend(chunk)
        lower_data = bytes(data).lower()
        if any(needle in lower_data for needle in lower_needles):
            return bytes(data)
    raise ProbeError(f"Timed out waiting for any of {needles!r}; got {bytes(data)!r}")


def ftp_probe():
    username, password = credentials("ftp")
    client = ftplib.FTP()
    try:
        client.connect(host, mapped_ports["ftp"], timeout=connect_timeout)
        client.login(username, password)
        client.pwd()
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def telnet_probe():
    username, password = credentials("telnet")
    deadline = time.monotonic() + connect_timeout
    with connect("telnet") as sock:
        recv_until(sock, [b"login:"], deadline)
        sock.sendall(username.encode("utf-8") + b"\n")
        recv_until(sock, [b"password:"], time.monotonic() + connect_timeout)
        sock.sendall(password.encode("utf-8") + b"\n")
        time.sleep(0.2)


def pop3_probe():
    username, password = credentials("pop3")
    client = poplib.POP3(host, mapped_ports["pop3"], timeout=connect_timeout)
    try:
        client.user(username)
        try:
            client.pass_(password)
        except poplib.error_proto:
            return
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def imap_probe():
    username, password = credentials("imap")
    client = imaplib.IMAP4(host, mapped_ports["imap"], timeout=connect_timeout)
    try:
        try:
            client.login(username, password)
        except imaplib.IMAP4.error:
            return
    finally:
        try:
            client.logout()
        except Exception:
            client.shutdown()


def smtp_probe():
    username, password = credentials("smtp")
    client = smtplib.SMTP(host, mapped_ports["smtp"], timeout=connect_timeout)
    try:
        client.ehlo()
        client.login(username, password)
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def http_probe(protocol):
    username, password = credentials(protocol)
    body = urllib.parse.urlencode({"username": username, "password": password})
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "tpot-honeypots-smoke",
    }
    if protocol == "https":
        conn = http.client.HTTPSConnection(
            host,
            mapped_ports[protocol],
            timeout=connect_timeout,
            context=ssl_context,
        )
    else:
        conn = http.client.HTTPConnection(host, mapped_ports[protocol], timeout=connect_timeout)
    try:
        conn.request("POST", "/login.html", body=body, headers=headers)
        response = conn.getresponse()
        response.read()
        if response.status >= 500:
            raise ProbeError(f"{protocol} returned HTTP {response.status}")
    finally:
        conn.close()


def socks5_probe():
    username, password = credentials("socks5")
    username_b = username.encode("utf-8")
    password_b = password.encode("utf-8")
    if len(username_b) > 255 or len(password_b) > 255:
        raise ProbeError("SOCKS5 username/password too long")
    with connect("socks5") as sock:
        sock.settimeout(connect_timeout)
        sock.sendall(b"\x05\x01\x02")
        response = sock.recv(2)
        if response != b"\x05\x02":
            raise ProbeError(f"Unexpected SOCKS5 auth selection: {response!r}")
        sock.sendall(
            b"\x01"
            + bytes([len(username_b)])
            + username_b
            + bytes([len(password_b)])
            + password_b
        )
        time.sleep(0.2)


def elastic_probe():
    username, password = credentials("elastic")
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    conn = http.client.HTTPSConnection(
        host,
        mapped_ports["elastic"],
        timeout=connect_timeout,
        context=ssl_context,
    )
    try:
        conn.request(
            "POST",
            "/test/_search",
            body='{"size":1}',
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
                "User-Agent": "tpot-honeypots-smoke",
            },
        )
        response = conn.getresponse()
        response.read()
        if response.status >= 500:
            raise ProbeError(f"elastic returned HTTP {response.status}")
    finally:
        conn.close()


retry("ftp", ftp_probe)
retry("telnet", telnet_probe)
retry("pop3", pop3_probe)
retry("imap", imap_probe)
retry("smtp", smtp_probe)
retry("http", lambda: http_probe("http"))
retry("https", lambda: http_probe("https"))
retry("socks5", socks5_probe)
retry("elastic", elastic_probe)
PY
}

validate_honeypots_logs() {
  python3 - "${CONFIG_FILE}" "${LOG_DIR}" "${TEST_TIMEOUT}" "${PROBED_PROTOCOLS[@]}" <<'PY'
import ast
import json
import sys
import time
from pathlib import Path

config_path = Path(sys.argv[1])
log_dir = Path(sys.argv[2])
timeout = int(sys.argv[3])
protocols = sys.argv[4:]

config = json.loads(config_path.read_text())
honeypots = config["honeypots"]
required_keys = {"protocol", "action", "src_ip", "src_port", "dest_ip", "dest_port"}
login_protocols = set(protocols)
http_protocols = {"http", "https"}


class LogError(Exception):
    pass


def parse_line(line):
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return ast.literal_eval(line)


def load_records(protocol):
    log_file = log_dir / honeypots[protocol]["log_file_name"]
    if not log_file.is_file():
        raise LogError(f"Missing log file for {protocol}: {log_file}")

    records = []
    for index, line in enumerate(log_file.read_text(errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = parse_line(line)
        except Exception as exc:
            raise LogError(f"Could not parse {log_file}:{index}: {exc}") from exc
        if isinstance(record, dict):
            records.append(record)
    if not records:
        raise LogError(f"No records found in {log_file}")
    return records


def has_login(records, protocol):
    entry = honeypots[protocol]
    username = entry.get("username", "")
    password = entry.get("password", "")
    for record in records:
        if (
            record.get("action") == "login"
            and record.get("username") == username
            and record.get("password") == password
            and record.get("status") == "success"
        ):
            return True
    return False


def validate_once():
    for protocol in protocols:
        records = load_records(protocol)
        expected_port = str(honeypots[protocol]["port"])

        for record in records:
            missing = required_keys - set(record)
            if missing:
                raise LogError(
                    f"{protocol} record is missing keys {sorted(missing)}: {record!r}"
                )
            if record.get("protocol") != protocol:
                raise LogError(
                    f"{protocol} log has unexpected protocol {record.get('protocol')!r}"
                )
            if str(record.get("dest_port")) != expected_port:
                raise LogError(
                    f"{protocol} log has unexpected dest_port {record.get('dest_port')!r}"
                )

        if protocol in login_protocols and not has_login(records, protocol):
            raise LogError(f"Missing successful login for {protocol}")

        if protocol in http_protocols and not any(record.get("action") == "POST" for record in records):
            raise LogError(f"Missing POST action for {protocol}")

        if protocol == "elastic" and not any(record.get("action") == "dump" for record in records):
            raise LogError("Missing elastic dump action")


deadline = time.monotonic() + timeout
last_error = None
while time.monotonic() < deadline:
    try:
        validate_once()
        print("Honeypots log validation succeeded")
        sys.exit(0)
    except LogError as exc:
        last_error = exc
        time.sleep(1)

print(f"Honeypots log validation failed: {last_error}", file=sys.stderr)
sys.exit(1)
PY
}

main() {
  parse_args "$@"
  validate_args
  load_config_summary

  IMAGE="${IMAGE:-$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")}"

  test_check_dependencies
  test_require_image "${IMAGE}" "docker compose -f docker/honeypots/docker-compose.yml build honeypots"

  test_enable_cleanup
  prepare_honeypots_harness

  test_info "Starting Honeypots smoke container from ${IMAGE}"
  test_compose up -d >/dev/null
  test_wait_for_container || test_die "Honeypots container did not stay running"

  resolve_mapped_ports
  run_protocol_probes
  validate_honeypots_logs

  test_ok "Honeypots smoke test passed"
}

main "$@"
