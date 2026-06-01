#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="dionaea"
DEFAULT_IMAGE="dtagdevsec/dionaea:24.04.1"
IMAGE=""
FTP_PORT=""
DIONAEA_DIR=""
CONFIG_DIR=""
LOG_DIR=""
JSON_LOG_FILE=""

DIONAEA_TCP_PORTS=(20 21 42 81 135 443 445 1433 1723 1883 3306 27017)
DIONAEA_UDP_PORTS=(69)
declare -A MAPPED_TCP_PORTS=()
declare -A MAPPED_UDP_PORTS=()

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Dionaea image.

The test exposes and probes Dionaea ports from docker/dionaea/docker-compose.yml,
except the SIP ports 5060/tcp, 5060/udp, and 5061/tcp:
20/tcp, 21/tcp, 42/tcp, 69/udp, 81/tcp, 135/tcp, 443/tcp, 445/tcp,
1433/tcp, 1723/tcp, 1883/tcp, 3306/tcp, and 27017/tcp.

Options:
  --image IMAGE       Image to test. Defaults to docker/dionaea/docker-compose.yml.
  --ftp-port PORT     Host TCP port for FTP control. Default: dynamic free port.
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
      --ftp-port|--port)
        [[ $# -ge 2 ]] || test_die "$1 requires an argument"
        FTP_PORT="$2"
        shift 2
        ;;
      --ftp-port=*|--port=*)
        FTP_PORT="${1#*=}"
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
  if [[ -n "${FTP_PORT}" ]]; then
    test_validate_port "${FTP_PORT}"
  fi
}

ensure_ftp_port_free_for_docker() {
  if [[ -z "${FTP_PORT}" ]]; then
    return 0
  fi

  if (( FTP_PORT < 1024 )); then
    test_info "Skipping user-space preflight for privileged TCP port ${FTP_PORT}; Docker will validate the binding."
  else
    test_ensure_port_free "${TEST_BIND_IP}" "${FTP_PORT}" || test_die "${TEST_BIND_IP}:${FTP_PORT} is already in use. Try --ftp-port <free-port>."
  fi
}

prepare_dionaea_harness() {
  test_prepare_harness "${TEST_NAME}"

  DIONAEA_DIR="${TEST_TMP_ROOT}/dionaea"
  CONFIG_DIR="${TEST_TMP_ROOT}/etc"
  LOG_DIR="${TEST_TMP_ROOT}/log"
  JSON_LOG_FILE="${LOG_DIR}/dionaea.json"
  TEST_ARTIFACT_LOG_DIR=""

  mkdir -p \
    "${CONFIG_DIR}" \
    "${LOG_DIR}" \
    "${DIONAEA_DIR}/binaries" \
    "${DIONAEA_DIR}/bistreams" \
    "${DIONAEA_DIR}/roots/ftp" \
    "${DIONAEA_DIR}/roots/tftp" \
    "${DIONAEA_DIR}/roots/www" \
    "${DIONAEA_DIR}/roots/upnp" \
    "${DIONAEA_DIR}/rtp"
  chmod -R 0777 "${LOG_DIR}" "${DIONAEA_DIR}"

  cp -a "${DOCKER_ROOT}/${TEST_NAME}/dist/etc/." "${CONFIG_DIR}/"
  rm -f "${CONFIG_DIR}/services/sip.yaml"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  dionaea:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    tmpfs:
      - /opt/dionaea/var/dionaea/bistreams:uid=2000,gid=2000,mode=0777
    ports:
      - "${TEST_BIND_IP}::20"
      - "${TEST_BIND_IP}:${FTP_PORT}:21"
      - "${TEST_BIND_IP}::42"
      - "${TEST_BIND_IP}::69/udp"
      - "${TEST_BIND_IP}::81"
      - "${TEST_BIND_IP}::135"
      - "${TEST_BIND_IP}::443"
      - "${TEST_BIND_IP}::445"
      - "${TEST_BIND_IP}::1433"
      - "${TEST_BIND_IP}::1723"
      - "${TEST_BIND_IP}::1883"
      - "${TEST_BIND_IP}::3306"
      - "${TEST_BIND_IP}::27017"
    volumes:
      - "${CONFIG_DIR}:/opt/dionaea/etc/dionaea:ro"
      - "${DIONAEA_DIR}:/opt/dionaea/var/dionaea"
      - "${LOG_DIR}:/opt/dionaea/var/log"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

resolve_mapped_ports() {
  local port=""

  for port in "${DIONAEA_TCP_PORTS[@]}"; do
    MAPPED_TCP_PORTS["${port}"]="$(test_get_mapped_port "${TEST_NAME}" "${port}")" || test_die "Could not resolve mapped host port for ${port}/tcp"
    test_ok "Port ${TEST_BIND_IP}:${MAPPED_TCP_PORTS[${port}]} maps to container port ${port}/tcp"
  done

  for port in "${DIONAEA_UDP_PORTS[@]}"; do
    MAPPED_UDP_PORTS["${port}"]="$(test_get_mapped_port "${TEST_NAME}" "${port}/udp")" || test_die "Could not resolve mapped host port for ${port}/udp"
    test_ok "Port ${TEST_BIND_IP}:${MAPPED_UDP_PORTS[${port}]} maps to container port ${port}/udp"
  done
}

wait_for_container_ports() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""
  local args=()
  local port=""

  while (( SECONDS < deadline )); do
    args=()
    for port in "${DIONAEA_TCP_PORTS[@]}"; do
      args+=("tcp:${port}")
    done
    for port in "${DIONAEA_UDP_PORTS[@]}"; do
      args+=("udp:${port}")
    done

    if output="$(docker exec "${TEST_CONTAINER_NAME}" python3 - "${args[@]}" <<'PY' 2>&1
import sys
from pathlib import Path

specs = sys.argv[1:]
missing = []


def read_proc(path):
    try:
        lines = Path(path).read_text(encoding="ascii", errors="replace").splitlines()[1:]
    except OSError:
        return set()

    ports = set()
    for line in lines:
        fields = line.split()
        if len(fields) < 2:
            continue
        local_addr = fields[1]
        try:
            port = int(local_addr.rsplit(":", 1)[1], 16)
        except (IndexError, ValueError):
            continue
        ports.add(port)
    return ports


tcp_ports = read_proc("/proc/net/tcp") | read_proc("/proc/net/tcp6")
udp_ports = read_proc("/proc/net/udp") | read_proc("/proc/net/udp6")

for spec in specs:
    protocol, port_text = spec.split(":", 1)
    port = int(port_text)
    if protocol == "tcp" and port not in tcp_ports:
        missing.append(spec)
    elif protocol == "udp" and port not in udp_ports:
        missing.append(spec)

if missing:
    print("Missing listener sockets: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)

print("All Dionaea listener sockets are bound")
PY
)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    test_wait_for_container || return 1
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

run_port_probes() {
  local token="$1"
  local args=("${TEST_BIND_IP}" "${TEST_TIMEOUT}" "${token}")
  local port=""

  for port in "${DIONAEA_TCP_PORTS[@]}"; do
    args+=("tcp:${port}:${MAPPED_TCP_PORTS[${port}]}")
  done
  for port in "${DIONAEA_UDP_PORTS[@]}"; do
    args+=("udp:${port}:${MAPPED_UDP_PORTS[${port}]}")
  done

  python3 - "${args[@]}" <<'PY'
import socket
import ssl
import sys
import time

host = sys.argv[1]
timeout = int(sys.argv[2])
token = sys.argv[3]
specs = sys.argv[4:]


class ProbeError(Exception):
    pass


def recv_some(sock, deadline, size=512):
    chunks = []
    while time.monotonic() < deadline:
        sock.settimeout(min(max(deadline - time.monotonic(), 0.1), 1.0))
        try:
            chunk = sock.recv(size)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
        if sum(len(item) for item in chunks) >= size:
            break
    return b"".join(chunks)


def read_line(sock, deadline):
    chunks = []
    while time.monotonic() < deadline:
        sock.settimeout(min(max(deadline - time.monotonic(), 0.1), 1.0))
        try:
            chunk = sock.recv(1)
        except socket.timeout:
            continue
        if not chunk:
            break
        chunks.append(chunk)
        if chunk == b"\n":
            return b"".join(chunks)
    raise ProbeError("Timed out while waiting for FTP response")


def probe_ftp(port, deadline):
    username = f"{token}-ftp"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        banner = read_line(sock, deadline).decode("utf-8", errors="replace").strip()
        if not banner.startswith("220 "):
            raise ProbeError(f"Expected FTP 220 banner, got: {banner!r}")
        sock.sendall(f"USER {username}\r\n".encode("ascii"))
        response = read_line(sock, deadline).decode("utf-8", errors="replace").strip()
        if not response.startswith(("230 ", "331 ")):
            raise ProbeError(f"Expected FTP USER response, got: {response!r}")
    return f"banner={banner!r} user_response={response!r}"


def probe_https(port, deadline):
    request = (
        f"GET /{token}.php HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: tpot-dionaea-smoke/{token}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    context = ssl._create_unverified_context()
    with socket.create_connection((host, port), timeout=timeout) as raw_sock:
        with context.wrap_socket(raw_sock, server_hostname=host) as sock:
            sock.settimeout(1)
            sock.sendall(request)
            recv_some(sock, deadline)
    return "tls-handshake-ok"


def probe_mqtt(port, deadline):
    connect_packet = b"\x10\x0c\x00\x04MQTT\x04\x02\x00\x3c\x00\x00"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(connect_packet)
        response = recv_some(sock, deadline, size=4)
    if not response.startswith(b"\x20\x02"):
        raise ProbeError(f"Expected MQTT CONNACK, got: {response!r}")
    return f"connack={response.hex()}"


def probe_mysql(port, deadline):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        response = recv_some(sock, deadline)
    if b"5.7.16" not in response and not response.startswith(b"\x34\x00\x00\x00\x0a"):
        raise ProbeError(f"Expected MySQL handshake, got: {response[:32]!r}")
    return "handshake-ok"


def probe_http(port):
    request = (
        f"GET /{token}.php HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: tpot-dionaea-smoke/{token}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
    return "request-sent"


def probe_tcp_connect(port):
    with socket.create_connection((host, port), timeout=timeout):
        pass
    return "connect-ok"


def probe_udp(port, container_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(1)
        if container_port == 69:
            payload = b"\x00\x01" + f"{token}.txt".encode("ascii") + b"\x00octet\x00"
        else:
            payload = token.encode("ascii")
        sock.sendto(payload, (host, port))
    finally:
        sock.close()
    return "datagram-sent"


for spec in specs:
    protocol, container_port_text, host_port_text = spec.split(":", 2)
    container_port = int(container_port_text)
    host_port = int(host_port_text)
    deadline = time.monotonic() + timeout

    try:
        if protocol == "udp":
            result = probe_udp(host_port, container_port)
        elif container_port == 21:
            result = probe_ftp(host_port, deadline)
        elif container_port == 443:
            result = probe_https(host_port, deadline)
        elif container_port == 1883:
            result = probe_mqtt(host_port, deadline)
        elif container_port == 3306:
            result = probe_mysql(host_port, deadline)
        elif container_port == 81:
            result = probe_http(host_port)
        else:
            result = probe_tcp_connect(host_port)
    except Exception as exc:
        print(f"{container_port}/{protocol} probe failed on {host}:{host_port}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"{container_port}/{protocol}: {result}")
PY
}

wait_for_json_ftp_event() {
  local token="$1"
  local username="${token}-ftp"

  python3 - "${JSON_LOG_FILE}" "${username}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

json_log = Path(sys.argv[1])
username = sys.argv[2]
timeout = int(sys.argv[3])
deadline = time.monotonic() + timeout
last_error = None


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


while time.monotonic() < deadline:
    if json_log.exists():
        try:
            lines = json_log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            last_error = f"Could not read {json_log}: {exc}"
            time.sleep(1)
            continue

        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                last_error = f"Invalid JSON in {json_log}:{line_number}: {exc}"
                continue

            connection = event.get("connection") or {}
            ftp = event.get("ftp") or {}
            commands = ftp.get("commands") or {}
            command_values = [str(item).upper() for item in as_list(commands.get("command"))]
            argument_values = [str(item) for item in as_list(commands.get("arguments"))]

            if (
                connection.get("protocol") == "ftpd"
                and "USER" in command_values
                and username in argument_values
            ):
                print(f"FTP JSON event found in {json_log}:{line_number}")
                sys.exit(0)
    else:
        last_error = f"{json_log} does not exist yet"

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
print(f"No Dionaea FTP USER event found in {json_log} for username {username}", file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local error_log="${DIONAEA_DIR}/dionaea-errors.log"

  if grep -R -I -E "Traceback|Assertion .*failed|Segmentation fault|CRITICAL" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Dionaea runtime error found in test log artifacts"
  fi

  if [[ -f "${error_log}" ]] && grep -I -E "Traceback|Assertion .*failed|Segmentation fault|CRITICAL" "${error_log}" >/dev/null 2>&1; then
    test_die "Dionaea runtime error found in dionaea-errors.log"
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
  ensure_ftp_port_free_for_docker

  prepare_dionaea_harness
  test_enable_cleanup

  test_info "Starting isolated Dionaea container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Dionaea container did not stay running"
  test_ok "Container is running"

  resolve_mapped_ports

  test_info "Waiting for all Dionaea listener sockets"
  wait_for_container_ports || test_die "Not all Dionaea listener sockets became available"
  test_ok "All Dionaea listener sockets are available"

  local token="dionaea-test-$(date +%s)-$$"
  test_info "Running Dionaea protocol probes with token: ${token}"
  run_port_probes "${token}" || test_die "One or more Dionaea port probes failed"
  test_ok "All Dionaea port probes completed"

  test_info "Waiting for FTP USER event in dionaea.json"
  wait_for_json_ftp_event "${token}" || test_die "FTP USER event was not found in dionaea.json"
  test_ok "FTP USER event was written to dionaea.json"

  test_wait_for_container || test_die "Dionaea container stopped after port probes"
  assert_no_runtime_errors
  test_ok "No Dionaea runtime errors found in test artifacts"

  test_ok "Dionaea post-build smoke test completed successfully"
}

main "$@"
