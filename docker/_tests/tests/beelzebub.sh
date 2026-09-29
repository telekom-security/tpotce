#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="beelzebub"
DEFAULT_IMAGE="dtagdevsec/beelzebub:24.04.2"
IMAGE=""
LOG_DIR=""
KEY_DIR=""
JSON_LOG_FILE=""
MAPPED_SSH_PORT=""
MAPPED_SSH2222_PORT=""
MAPPED_HTTP_PORT=""
MAPPED_MYSQL_PORT=""
SSH_LOGIN_TESTED="false"

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Beelzebub image. It checks the
T-Pot adjustments on top of upstream Beelzebub: the flat event log in the
format of the former T-Pot fork, the persistent SSH host key and the startup
validation of the LLM settings. No LLM backend is needed, the probes use the
static services.

Options:
  --image IMAGE      Image to test. Defaults to docker/beelzebub/docker-compose.yml.
  --timeout SEC      Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP       Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts   Keep temporary compose file and logs for debugging.
  -h, --help         Show this help message.

The SSH login check needs an OpenSSH client (8.4 or newer), it is skipped otherwise.
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

prepare_beelzebub_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  KEY_DIR="${TEST_TMP_ROOT}/key"
  JSON_LOG_FILE="${LOG_DIR}/beelzebub.json"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${KEY_DIR}"
  chmod 0777 "${LOG_DIR}" "${KEY_DIR}"

  # The LLM backend is unreachable on purpose, the startup validation only
  # needs provider and model, the probes below use the static services.
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  beelzebub:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    environment:
      LLM_PROVIDER: "ollama"
      LLM_MODEL: "openchat"
      LLM_HOST: "http://127.0.0.1:9/api/chat"
      OPEN_AI_SECRET_KEY: ""
    ports:
      - "${TEST_BIND_IP}::22"
      - "${TEST_BIND_IP}::2222"
      - "${TEST_BIND_IP}::3306"
      - "${TEST_BIND_IP}::8080"
    volumes:
      - "${KEY_DIR}:/opt/beelzebub/configurations/key"
      - "${LOG_DIR}:/opt/beelzebub/configurations/log"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

resolve_ports() {
  MAPPED_SSH_PORT="$(test_get_mapped_port "${TEST_NAME}" "22")" || test_die "Could not resolve mapped host port for 22/tcp"
  MAPPED_SSH2222_PORT="$(test_get_mapped_port "${TEST_NAME}" "2222")" || test_die "Could not resolve mapped host port for 2222/tcp"
  MAPPED_MYSQL_PORT="$(test_get_mapped_port "${TEST_NAME}" "3306")" || test_die "Could not resolve mapped host port for 3306/tcp"
  MAPPED_HTTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "8080")" || test_die "Could not resolve mapped host port for 8080/tcp"
}

run_ssh_banner_probe() {
  python3 - "${TEST_BIND_IP}" "$1" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host, port, timeout = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None

while time.monotonic() < deadline:
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.settimeout(5)
            banner = sock.recv(256).decode("ascii", errors="replace").strip()
        if banner == "SSH-2.0-OpenSSH_7.9p1":
            print("SSH banner on port {}: {}".format(port, banner))
            sys.exit(0)
        last_error = "Unexpected SSH banner: {!r}".format(banner)
    except OSError as exc:
        last_error = "SSH banner probe failed: {}".format(exc)
    time.sleep(1)

print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

run_http_and_tcp_probes() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTP_PORT}" "${MAPPED_MYSQL_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import urllib.error
import urllib.request

host, http_port, mysql_port, timeout = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])

request = urllib.request.Request(
    "http://{}:{}/tpot-smoke-test".format(host, http_port),
    headers={"User-Agent": "tpot-smoke-test"},
)
try:
    status = urllib.request.urlopen(request, timeout=timeout).status
except urllib.error.HTTPError as exc:
    status = exc.code
except OSError as exc:
    print("HTTP probe failed: {}".format(exc), file=sys.stderr)
    sys.exit(1)
if status != 401:
    print("Expected HTTP 401 from the Apache 401 service, got {}".format(status), file=sys.stderr)
    sys.exit(1)
print("HTTP probe answered with {}".format(status))

try:
    with socket.create_connection((host, mysql_port), timeout=timeout) as sock:
        sock.sendall(b"tpot-smoke-test\n")
        sock.settimeout(2)
        try:
            sock.recv(256)
        except socket.timeout:
            pass
except OSError as exc:
    print("TCP probe failed: {}".format(exc), file=sys.stderr)
    sys.exit(1)
print("TCP probe sent to port 3306")
PY
}

# Password login on the static ssh-2222 service, then an inline command and an interactive session.
run_ssh_login_probe() {
  local askpass="${TEST_TMP_ROOT}/askpass.sh"
  local ssh_version=""
  local -a ssh_opts=(
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o PreferredAuthentications=password
    -o PubkeyAuthentication=no
    -o ConnectTimeout="${TEST_TIMEOUT}"
    -o LogLevel=ERROR
    -p "${MAPPED_SSH2222_PORT}"
  )

  if ! command -v ssh >/dev/null 2>&1; then
    test_info "No ssh client found, skipping the SSH login check"
    return 0
  fi
  ssh_version="$(ssh -V 2>&1 | sed -n 's/^OpenSSH_\([0-9]*\.[0-9]*\).*/\1/p')"
  if [[ -z "${ssh_version}" ]] || ! awk -v v="${ssh_version}" 'BEGIN { exit !(v >= 8.4) }'; then
    test_info "OpenSSH ${ssh_version:-unknown} has no SSH_ASKPASS_REQUIRE, skipping the SSH login check"
    return 0
  fi

  printf '#!/bin/sh\necho 123456\n' > "${askpass}"
  chmod 0700 "${askpass}"

  SSH_ASKPASS="${askpass}" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 \
    ssh "${ssh_opts[@]}" root@"${TEST_BIND_IP}" uname </dev/null >/dev/null \
    || test_die "SSH inline command on port 2222 failed"
  { sleep 1; echo "ls"; sleep 1; echo "exit"; } \
    | SSH_ASKPASS="${askpass}" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 \
      ssh -tt "${ssh_opts[@]}" root@"${TEST_BIND_IP}" >/dev/null 2>&1 \
    || test_die "SSH interactive session on port 2222 failed"
  SSH_LOGIN_TESTED="true"
  test_ok "SSH password login, inline command and interactive session on port 2222 succeeded"
}

# The key sets per message are those the former T-Pot fork wrote, the T-Pot
# Logstash pipeline, dashboards and ewsposter rely on them.
wait_for_flat_log_events() {
  python3 - "${JSON_LOG_FILE}" "${TEST_TIMEOUT}" "${SSH_LOGIN_TESTED}" <<'PY'
import json
import sys
import time
from datetime import datetime
from pathlib import Path

log_file, timeout, ssh_tested = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3] == "true"

base = {"level", "message", "msg", "timestamp", "protocol", "status", "session", "src_ip", "src_port", "dest_port"}
expected = {
    "HTTP New request": (base | {"request_uri", "request_method", "hostname", "userAgent", "request_headers", "service"}, "8080"),
    "New TCP attempt": (base | {"command", "service"}, "3306"),
}
if ssh_tested:
    expected.update({
        "New SSH attempt": (base | {"username", "password", "client", "service"}, "2222"),
        "New SSH Inline Session": (base | {"username", "input", "output", "service"}, "2222"),
        "End SSH Inline Session": (base | {"session_duration"}, "2222"),
        "New SSH Session": (base | {"username", "client_version", "service"}, "2222"),
        "New SSH Terminal Session": (base | {"input", "output", "input_duration", "service"}, "2222"),
        "End SSH Session": (base | {"session_duration"}, "2222"),
    })

deadline = time.monotonic() + timeout
problems = []
while time.monotonic() < deadline:
    found, problems = {}, []
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
        if "event" in event or "HeadersMap" in event:
            print("Upstream (nested) event format in {}:{}".format(log_file, number), file=sys.stderr)
            sys.exit(1)
        if "message" not in event:
            print("Line without message in {}:{}: {}".format(log_file, number, line), file=sys.stderr)
            sys.exit(1)
        try:
            datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            print("No ISO8601 timestamp in {}:{}".format(log_file, number), file=sys.stderr)
            sys.exit(1)
        found.setdefault(event["message"], event)

    for message, (keys, port) in expected.items():
        event = found.get(message)
        if event is None:
            problems.append("missing event: {}".format(message))
            continue
        missing = keys - set(event)
        if missing:
            problems.append("{}: missing keys {}".format(message, sorted(missing)))
        if event.get("dest_port") != port:
            problems.append("{}: dest_port {!r}, expected {}".format(message, event.get("dest_port"), port))
    if not problems:
        print("Found {} expected event types with the T-Pot fields".format(len(expected)))
        sys.exit(0)
    time.sleep(1)

print("\n".join(problems), file=sys.stderr)
sys.exit(1)
PY
}

ssh_fingerprint() {
  if command -v ssh-keyscan >/dev/null 2>&1 && command -v ssh-keygen >/dev/null 2>&1; then
    ssh-keyscan -T "${TEST_TIMEOUT}" -t rsa -p "$1" "${TEST_BIND_IP}" 2>/dev/null | ssh-keygen -lf - 2>/dev/null | awk '{print $2}'
  fi
}

# The key is 0600 and owned by the container user, so its identity is taken
# from stat (no read access needed): a regenerated key changes inode or mtime.
key_file_id() {
  python3 - "${KEY_DIR}/ssh_host_key" <<'PY'
import os
import sys

try:
    info = os.stat(sys.argv[1])
except OSError as exc:
    print("Could not stat {}: {}".format(sys.argv[1], exc), file=sys.stderr)
    sys.exit(1)
if info.st_size == 0:
    print("{} is empty".format(sys.argv[1]), file=sys.stderr)
    sys.exit(1)
print("{}:{}:{}".format(info.st_ino, info.st_size, info.st_mtime_ns))
PY
}

assert_no_runtime_errors() {
  local pattern='"level":"(fatal|panic)"|panic:|Permission denied|Read-only file system|validation failed'

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Beelzebub runtime error found in Docker logs"
  fi
}

main() {
  parse_args "$@"
  validate_args
  test_check_dependencies
  local key_before=""
  local key_after=""
  local fp_before=""
  local fp_2222=""
  local fp_after=""

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  prepare_beelzebub_harness
  test_enable_cleanup

  test_info "Starting isolated Beelzebub container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Beelzebub container did not stay running (check the startup validation in the logs)"
  test_ok "Container is running"
  resolve_ports
  test_ok "Ports 22, 2222, 3306 and 8080 are mapped to ${TEST_BIND_IP}"

  test_info "Checking the SSH services"
  run_ssh_banner_probe "${MAPPED_SSH_PORT}" || test_die "SSH banner check on port 22 failed"
  run_ssh_banner_probe "${MAPPED_SSH2222_PORT}" || test_die "SSH banner check on port 2222 failed"

  test_info "Probing the HTTP and TCP services"
  run_http_and_tcp_probes || test_die "HTTP / TCP probes failed"

  run_ssh_login_probe

  test_info "Waiting for the flat T-Pot events in beelzebub.json"
  wait_for_flat_log_events || test_die "beelzebub.json does not hold the expected T-Pot events"
  test_ok "beelzebub.json holds the events in the T-Pot format"

  test_info "Checking the persistent SSH host key"
  key_before="$(key_file_id)" || test_die "ssh_host_key was not created in the persistent key volume"
  fp_before="$(ssh_fingerprint "${MAPPED_SSH_PORT}")"
  fp_2222="$(ssh_fingerprint "${MAPPED_SSH2222_PORT}")"
  [[ "${fp_before}" == "${fp_2222}" ]] || test_die "Ports 22 and 2222 present different host keys"

  test_info "Restarting container to verify host key persistence"
  test_compose restart "${TEST_NAME}" >/dev/null
  test_wait_for_container || test_die "Beelzebub container did not stay running after restart"
  resolve_ports
  run_ssh_banner_probe "${MAPPED_SSH_PORT}" >/dev/null || test_die "SSH banner check on port 22 failed after restart"
  key_after="$(key_file_id)" || test_die "ssh_host_key was not readable after restart"
  fp_after="$(ssh_fingerprint "${MAPPED_SSH_PORT}")"
  [[ "${key_before}" == "${key_after}" ]] || test_die "ssh_host_key changed after container restart"
  [[ "${fp_before}" == "${fp_after}" ]] || test_die "SSH host key fingerprint changed after container restart"
  test_ok "SSH host key is unchanged after container restart${fp_after:+ (${fp_after})}"

  assert_no_runtime_errors
  test_ok "No Beelzebub runtime errors found in logs"

  test_ok "Beelzebub post-build smoke test completed successfully"
}

main "$@"
