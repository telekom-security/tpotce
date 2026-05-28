#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="cowrie"
DEFAULT_IMAGE="ghcr.io/telekom-security/cowrie:24.04.1"
IMAGE=""
SSH_PORT=""
TELNET_PORT=""
LOG_DIR=""
TTY_LOG_DIR=""
DL_DIR=""
KEYS_DIR=""
JSON_LOG_FILE=""
MAPPED_SSH_PORT=""
MAPPED_TELNET_PORT=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Cowrie image.

Options:
  --image IMAGE        Image to test. Defaults to docker/cowrie/docker-compose.yml.
  --ssh-port PORT      Host TCP port for SSH. Default: dynamic free port.
  --telnet-port PORT   Host TCP port for Telnet. Default: dynamic free port.
  --timeout SEC        Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP         Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts     Keep temporary compose file and logs for debugging.
  -h, --help           Show this help message.
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
      --ssh-port)
        [[ $# -ge 2 ]] || test_die "--ssh-port requires an argument"
        SSH_PORT="$2"
        shift 2
        ;;
      --ssh-port=*)
        SSH_PORT="${1#*=}"
        shift
        ;;
      --telnet-port)
        [[ $# -ge 2 ]] || test_die "--telnet-port requires an argument"
        TELNET_PORT="$2"
        shift 2
        ;;
      --telnet-port=*)
        TELNET_PORT="${1#*=}"
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
  if [[ -n "${SSH_PORT}" ]]; then
    test_validate_port "${SSH_PORT}"
  fi
  if [[ -n "${TELNET_PORT}" ]]; then
    test_validate_port "${TELNET_PORT}"
  fi
}

prepare_cowrie_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  TTY_LOG_DIR="${LOG_DIR}/tty"
  DL_DIR="${TEST_TMP_ROOT}/downloads"
  KEYS_DIR="${TEST_TMP_ROOT}/keys"
  JSON_LOG_FILE="${LOG_DIR}/cowrie.json"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${TTY_LOG_DIR}" "${DL_DIR}" "${KEYS_DIR}"
  chmod 0777 "${LOG_DIR}" "${TTY_LOG_DIR}" "${DL_DIR}" "${KEYS_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  cowrie:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    tmpfs:
      - /tmp/cowrie:uid=2000,gid=2000
      - /tmp/cowrie/data:uid=2000,gid=2000
    ports:
      - "${TEST_BIND_IP}:${SSH_PORT}:22"
      - "${TEST_BIND_IP}:${TELNET_PORT}:23"
    volumes:
      - "${DL_DIR}:/home/cowrie/cowrie/dl"
      - "${KEYS_DIR}:/home/cowrie/cowrie/etc"
      - "${LOG_DIR}:/home/cowrie/cowrie/log"
      - "${TTY_LOG_DIR}:/home/cowrie/cowrie/log/tty"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_json_log() {
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if [[ -f "${JSON_LOG_FILE}" ]]; then
      return 0
    fi
    sleep 1
  done

  return 1
}

run_ssh_banner_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_SSH_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
client_banner = f"SSH-2.0-tpot-cowrie-smoke-{token}\r\n".encode("ascii")

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(1)
        chunks = []
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(256)
            except socket.timeout:
                continue
            if not chunk:
                raise RuntimeError("Connection closed before SSH banner")
            chunks.append(chunk)
            if b"\n" in b"".join(chunks):
                break

        banner = b"".join(chunks).splitlines()[0]
        if not banner.startswith(b"SSH-"):
            raise RuntimeError(f"Expected SSH banner, got {banner!r}")

        sock.sendall(client_banner)
        time.sleep(0.2)

    print(f"SSH banner: {banner.decode('ascii', errors='replace')}")
except Exception as exc:
    print(f"SSH probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_ssh_banner_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_ssh_banner_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

run_telnet_login_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_TELNET_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
username = f"cowrie-user-{token}"
password = f"cowrie-pass-{token}"
deadline = time.monotonic() + timeout


def readable(data):
    return data.decode("utf-8", errors="ignore").lower()


def read_until(sock, needles):
    data = bytearray()
    in_subnegotiation = False
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(256)
        except socket.timeout:
            continue
        if not chunk:
            break

        index = 0
        while index < len(chunk):
            byte = chunk[index]
            if byte == IAC and index + 1 < len(chunk):
                command = chunk[index + 1]
                if command in (DO, DONT, WILL, WONT) and index + 2 < len(chunk):
                    option = chunk[index + 2]
                    if command in (DO, DONT):
                        sock.sendall(bytes([IAC, WONT, option]))
                    else:
                        sock.sendall(bytes([IAC, DONT, option]))
                    index += 3
                    continue
                if command == SB:
                    in_subnegotiation = True
                    index += 2
                    continue
                if command == SE:
                    in_subnegotiation = False
                    index += 2
                    continue
                index += 2
                continue

            if not in_subnegotiation:
                data.append(byte)
            index += 1

        text = readable(data)
        if any(needle in text for needle in needles):
            return bytes(data)

    raise RuntimeError(f"Timed out waiting for one of {needles}; received {bytes(data)!r}")


try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(1)
        login_prompt = read_until(sock, ("login:", "username:"))
        sock.sendall((username + "\r\n").encode("ascii"))
        password_prompt = read_until(sock, ("password:",))
        sock.sendall((password + "\r\n").encode("ascii"))
        time.sleep(0.5)

    print(
        "Telnet prompts: "
        f"{login_prompt[-80:].decode('utf-8', errors='replace')!r}; "
        f"{password_prompt[-80:].decode('utf-8', errors='replace')!r}"
    )
except Exception as exc:
    print(f"Telnet probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_telnet_login_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_telnet_login_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

wait_for_json_event_containing() {
  local token="$1"
  local mode="$2"

  python3 - "${JSON_LOG_FILE}" "${token}" "${mode}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
token = sys.argv[2]
mode = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
last_error = None


def contains_token(value):
    if isinstance(value, str):
        return token in value
    if isinstance(value, dict):
        return any(contains_token(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_token(item) for item in value)
    return False


def matches_mode(event):
    eventid = str(event.get("eventid", ""))
    if mode == "ssh":
        return contains_token(event) and (
            eventid.startswith("cowrie.client.")
            or eventid.startswith("cowrie.session.")
            or eventid.startswith("cowrie.login.")
        )
    if mode == "telnet":
        return contains_token(event) and (
            eventid.startswith("cowrie.login.")
            or eventid.startswith("cowrie.session.")
            or eventid.startswith("cowrie.client.")
        )
    raise RuntimeError(f"Unknown mode: {mode}")


while time.monotonic() < deadline:
    if not path.exists():
        last_error = f"{path} does not exist yet"
        time.sleep(1)
        continue

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        last_error = f"Could not read {path}: {exc}"
        time.sleep(1)
        continue

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            last_error = f"Invalid JSON in {path}:{line_number}: {exc}"
            continue

        if matches_mode(event):
            print(f"JSON event found in {path}:{line_number}: {event.get('eventid', '<missing eventid>')}")
            sys.exit(0)

    last_error = f"No {mode} JSON event found in {path} for token {token}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

assert_custom_filesystem() {
  docker exec -i "${TEST_CONTAINER_NAME}" python3 - <<'PY'
from pathlib import Path
import sys

root = Path("/home/cowrie/cowrie")
pickle_path = root / "src" / "cowrie" / "data" / "fs.pickle"
honeyfs = root / "honeyfs"
offenders = []


def read_bytes(path):
    try:
        return path.read_bytes()
    except OSError as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        sys.exit(1)


if not pickle_path.is_file():
    print(f"Missing Cowrie filesystem pickle: {pickle_path}", file=sys.stderr)
    sys.exit(1)
if not honeyfs.is_dir():
    print(f"Missing Cowrie honeyfs directory: {honeyfs}", file=sys.stderr)
    sys.exit(1)

if b"phil" in read_bytes(pickle_path).lower():
    offenders.append(str(pickle_path))

for item in honeyfs.rglob("*"):
    if "phil" in item.name.lower():
        offenders.append(str(item))
        continue
    if item.is_file() and b"phil" in read_bytes(item).lower():
        offenders.append(str(item))

if offenders:
    print("Cowrie filesystem still contains 'phil': " + ", ".join(offenders), file=sys.stderr)
    sys.exit(1)

pickle_bytes = read_bytes(pickle_path)
pickle_size = pickle_path.stat().st_size
passwd = read_bytes(honeyfs / "etc" / "passwd")
hostname = read_bytes(honeyfs / "etc" / "hostname")
os_release = read_bytes(honeyfs / "etc" / "os-release")

if pickle_size < 1000000:
    print(f"fs.pickle is unexpectedly small: {pickle_size} bytes", file=sys.stderr)
    sys.exit(1)

checks = [
    (b"ubuntu", pickle_bytes, "fs.pickle does not contain ubuntu"),
    (b"ubuntu", passwd, "honeyfs /etc/passwd does not contain ubuntu"),
    (b"srv01", hostname, "honeyfs /etc/hostname does not contain srv01"),
    (b"Ubuntu 22.04", os_release, "honeyfs /etc/os-release does not describe Ubuntu 22.04"),
]

for needle, haystack, message in checks:
    if needle not in haystack:
        print(message, file=sys.stderr)
        sys.exit(1)

print("Cowrie filesystem profile validated: ubuntu@srv01 without phil")
PY
}

assert_no_runtime_errors() {
  if grep -R -E "Traceback|NameError|Unhandled Error|Exception" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Cowrie runtime error found in log files"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "Traceback|NameError|Unhandled Error|Exception" >/dev/null 2>&1; then
    test_die "Cowrie runtime error found in Docker logs"
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

  if [[ -n "${SSH_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${SSH_PORT}" || test_die "${TEST_BIND_IP}:${SSH_PORT} is already in use. Try --ssh-port <free-port>."
  fi
  if [[ -n "${TELNET_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${TELNET_PORT}" || test_die "${TEST_BIND_IP}:${TELNET_PORT} is already in use. Try --telnet-port <free-port>."
  fi

  prepare_cowrie_harness
  test_enable_cleanup

  test_info "Starting isolated Cowrie container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Cowrie container did not stay running"
  test_ok "Container is running"

  MAPPED_SSH_PORT="$(test_get_mapped_port "${TEST_NAME}" "22")" || test_die "Could not resolve mapped host port for 22/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_SSH_PORT} maps to container port 22/tcp"

  MAPPED_TELNET_PORT="$(test_get_mapped_port "${TEST_NAME}" "23")" || test_die "Could not resolve mapped host port for 23/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_TELNET_PORT} maps to container port 23/tcp"

  test_info "Validating custom Cowrie filesystem profile"
  assert_custom_filesystem || test_die "Custom Cowrie filesystem validation failed"
  test_ok "Custom Cowrie filesystem profile is present"

  test_info "Waiting for cowrie.json"
  wait_for_json_log || test_die "cowrie.json was not created"
  test_ok "cowrie.json exists"

  local ssh_token="ssh-test-$(date +%s)-$$"
  test_info "Running SSH banner probe with token: ${ssh_token}"
  run_ssh_banner_probe_with_retries "${ssh_token}" || test_die "SSH probe failed on ${TEST_BIND_IP}:${MAPPED_SSH_PORT}"
  test_wait_for_container || test_die "Cowrie container stopped after SSH probe"

  test_info "Waiting for SSH probe event in cowrie.json"
  wait_for_json_event_containing "${ssh_token}" "ssh" || test_die "SSH probe token was not found in cowrie.json"
  test_ok "SSH probe was written to cowrie.json"

  local telnet_token="telnet-test-$(date +%s)-$$"
  test_info "Running Telnet login probe with token: ${telnet_token}"
  run_telnet_login_probe_with_retries "${telnet_token}" || test_die "Telnet probe failed on ${TEST_BIND_IP}:${MAPPED_TELNET_PORT}"
  test_wait_for_container || test_die "Cowrie container stopped after Telnet probe"

  test_info "Waiting for Telnet probe event in cowrie.json"
  wait_for_json_event_containing "${telnet_token}" "telnet" || test_die "Telnet probe token was not found in cowrie.json"
  test_ok "Telnet probe was written to cowrie.json"

  assert_no_runtime_errors
  test_ok "No Cowrie runtime errors found in logs"

  test_ok "Cowrie post-build smoke test completed successfully"
}

main "$@"
