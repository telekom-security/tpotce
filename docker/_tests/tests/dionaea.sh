#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="dionaea"
DEFAULT_IMAGE="dtagdevsec/dionaea:24.04.2"
IMAGE=""
SELF_TEST_WARNINGS=""
FTP_PORT=""
DIONAEA_DIR=""
CONFIG_DIR=""
LOG_DIR=""
JSON_LOG_FILE=""
DEFAULTS_DIONAEA_DIR=""
DEFAULTS_CONFIG_DIR=""
DEFAULTS_LOG_DIR=""
DEFAULTS_CONTAINER_NAME=""
# the default log of dionaea.cfg (commented out in T-Pot), turned on in the copy of the test
DIONAEA_LOG_IN_CONTAINER="/opt/dionaea/var/dionaea/dionaea.log"

# A value of the storage limits that is not the T-Pot default (2048 MiB, 200000 files,
# 1024 MiB), passed to the container as docker-compose.yml of T-Pot passes it from .env.
# 48 MiB is also below the 64 MiB the harness sets in dionaea.cfg, so an image that
# honours the variables ends below it and the load of assert_health_under_load stays
# within the tmpfs.
LIMIT_VARS=(DIONAEA_STREAMDUMPER_MAX_SIZE DIONAEA_STREAMDUMPER_MAX_FILES DIONAEA_STORE_MAX_SIZE)
LIMIT_VALUES=(48 12345 777)
# The lines dionaea writes at the start, one per limit (doc/source/processor/streamdumper.rst
# and doc/source/ihandler/store.rst of the fork): with the variables above, and without
# them with the values of the dionaea.cfg and store.yaml of T-Pot.
LIMIT_LINES_ENV=(
  "streamdumper: max_size ${LIMIT_VALUES[0]} MiB (from ${LIMIT_VARS[0]})"
  "streamdumper: max_files ${LIMIT_VALUES[1]} (from ${LIMIT_VARS[1]})"
  "store: max_size ${LIMIT_VALUES[2]} MiB (from ${LIMIT_VARS[2]})"
)
LIMIT_LINES_DEFAULTS=(
  "streamdumper: max_size 2048 MiB (from dionaea.cfg)"
  "streamdumper: max_files 200000 (from dionaea.cfg)"
  "store: max_size 1024 MiB (from store.yaml)"
)

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

The roots of the FTP, TFTP, HTTP and UPnP services are mounted read-only as in
T-Pot. The test uploads a sample over FTP (STOR) and HTTP (multipart POST) and
checks the sample store: no second connection can fetch it (FTP RETR, HTTP
GET, TFTP RRQ), a TFTP WRQ is refused, the roots stay empty and the stored
sample has mode 0440. Active FTP PORT/STOR and SMB2 2.02/2.10 NTLMv1/v2
authentication capture in JSON and SQLite are checked as well.

/tmp is a 16 MiB tmpfs as in T-Pot (the files of the emulation). The
healthcheck of the image (cpu_check.py, the main thread only) has to pass
while random data on 445/tcp keeps the emu workers busy.

The copy of dionaea.cfg of the test turns the default log (dionaea.log) on.
After dionaea stopped it must have one line per storage limit at the start:
with DIONAEA_STREAMDUMPER_MAX_SIZE, DIONAEA_STREAMDUMPER_MAX_FILES and
DIONAEA_STORE_MAX_SIZE set to 48, 12345 and 777 (as docker-compose.yml passes
the .env settings), and in a second start without them the values of
dionaea.cfg and store.yaml (2048 MiB, 200000, 1024 MiB).

Options:
  --image IMAGE       Image to test. Defaults to docker/dionaea/docker-compose.yml.
  --ftp-port PORT     Host TCP port for FTP control. Default: dynamic free port.
  --timeout SEC       Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP        Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts    Keep temporary compose file and logs for debugging.
  --self-test-warnings  Only check the counting of warnings on a made-up log (no Docker).
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
      --self-test-warnings)
        SELF_TEST_WARNINGS="true"
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

# derive_dionaea_config DIR: a copy of the etc/ of the image (docker/dionaea/dist/etc) in
# DIR, without SIP, with the default log of dionaea.cfg turned on. T-Pot writes only
# dionaea-errors.log (warning, error, critical: a record of Python's logging at error is
# a critical one of glib); the lines of the limits are info.
derive_dionaea_config() {
  local dir="$1"

  # the shipped file (and so the copy) must carry critical in the levels of the errors log
  grep -Eq '^errors\.levels=(.*,)?critical(,.*)?$' "${DOCKER_ROOT}/${TEST_NAME}/dist/etc/dionaea.cfg" \
    || test_die "errors.levels of docker/dionaea/dist/etc/dionaea.cfg has no critical"

  mkdir -p "${dir}"
  cp -a "${DOCKER_ROOT}/${TEST_NAME}/dist/etc/." "${dir}/"
  rm -f "${dir}/services/sip.yaml"
  sed -i.orig \
    -e "s|^#default\.filename=.*\$|default.filename=${DIONAEA_LOG_IN_CONTAINER}|" \
    -e 's|^#default\.levels=.*$|default.levels=all,-debug|' \
    -e 's|^#default\.domains=.*$|default.domains=*|' \
    "${dir}/dionaea.cfg"
  rm -f "${dir}/dionaea.cfg.orig"
  grep -qx "default.filename=${DIONAEA_LOG_IN_CONTAINER}" "${dir}/dionaea.cfg" \
    && grep -qx 'default.levels=all,-debug' "${dir}/dionaea.cfg" \
    && grep -qx 'default.domains=\*' "${dir}/dionaea.cfg" \
    || test_die "Could not turn the default log on in the copy of dionaea.cfg"
}

prepare_dionaea_harness() {
  test_prepare_harness "${TEST_NAME}"

  DIONAEA_DIR="${TEST_TMP_ROOT}/dionaea"
  CONFIG_DIR="${TEST_TMP_ROOT}/etc"
  LOG_DIR="${TEST_TMP_ROOT}/log"
  JSON_LOG_FILE="${LOG_DIR}/dionaea.json"
  TEST_ARTIFACT_LOG_DIR=""
  DEFAULTS_DIONAEA_DIR="${TEST_TMP_ROOT}/defaults/dionaea"
  DEFAULTS_CONFIG_DIR="${TEST_TMP_ROOT}/defaults/etc"
  DEFAULTS_LOG_DIR="${TEST_TMP_ROOT}/defaults/log"
  DEFAULTS_CONTAINER_NAME="${TEST_CONTAINER_NAME}-defaults"

  mkdir -p \
    "${CONFIG_DIR}" \
    "${LOG_DIR}" \
    "${DIONAEA_DIR}/binaries" \
    "${DIONAEA_DIR}/bistreams" \
    "${DIONAEA_DIR}/roots/ftp" \
    "${DIONAEA_DIR}/roots/tftp" \
    "${DIONAEA_DIR}/roots/www" \
    "${DIONAEA_DIR}/roots/upnp" \
    "${DIONAEA_DIR}/rtp" \
    "${DEFAULTS_LOG_DIR}" \
    "${DEFAULTS_DIONAEA_DIR}/binaries" \
    "${DEFAULTS_DIONAEA_DIR}/bistreams" \
    "${DEFAULTS_DIONAEA_DIR}/roots/ftp" \
    "${DEFAULTS_DIONAEA_DIR}/roots/tftp" \
    "${DEFAULTS_DIONAEA_DIR}/roots/www" \
    "${DEFAULTS_DIONAEA_DIR}/roots/upnp" \
    "${DEFAULTS_DIONAEA_DIR}/rtp"
  chmod -R 0777 "${LOG_DIR}" "${DIONAEA_DIR}" "${DEFAULTS_LOG_DIR}" "${DEFAULTS_DIONAEA_DIR}"

  derive_dionaea_config "${CONFIG_DIR}"
  # bistreams/ is a tmpfs here, it counts towards the 1 GiB of the container: 64 MiB of
  # dumps at most (T-Pot: 2048 on disk), the load of assert_health_under_load stays within it
  sed -i.orig 's/^config.max_size=2048$/config.max_size=64/' "${CONFIG_DIR}/dionaea.cfg"
  rm -f "${CONFIG_DIR}/dionaea.cfg.orig"
  grep -q '^config.max_size=64$' "${CONFIG_DIR}/dionaea.cfg" \
    || test_die "Could not set the bistream cap of the harness"
  # the second start without the variables keeps the limits of dionaea.cfg as T-Pot ships it
  derive_dionaea_config "${DEFAULTS_CONFIG_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  dionaea:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    environment:
      - "${LIMIT_VARS[0]}=${LIMIT_VALUES[0]}"
      - "${LIMIT_VARS[1]}=${LIMIT_VALUES[1]}"
      - "${LIMIT_VARS[2]}=${LIMIT_VALUES[2]}"
    read_only: true
    mem_limit: 1g
    memswap_limit: 1g
    pids_limit: 256
    cpus: 1
    user: "2000:2000"
    tmpfs:
      - /tmp:uid=2000,gid=2000,size=16m
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
      - "${DIONAEA_DIR}/roots/ftp:/opt/dionaea/var/dionaea/roots/ftp:ro"
      - "${DIONAEA_DIR}/roots/tftp:/opt/dionaea/var/dionaea/roots/tftp:ro"
      - "${DIONAEA_DIR}/roots/www:/opt/dionaea/var/dionaea/roots/www:ro"
      - "${DIONAEA_DIR}/roots/upnp:/opt/dionaea/var/dionaea/roots/upnp:ro"
      - "${LOG_DIR}:/opt/dionaea/var/log"
  # started on its own after the run of the test (assert_storage_limits_logged)
  dionaea-defaults:
    image: "${IMAGE}"
    container_name: "${DEFAULTS_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    mem_limit: 1g
    memswap_limit: 1g
    pids_limit: 256
    cpus: 1
    user: "2000:2000"
    tmpfs:
      - /tmp:uid=2000,gid=2000,size=16m
    volumes:
      - "${DEFAULTS_CONFIG_DIR}:/opt/dionaea/etc/dionaea:ro"
      - "${DEFAULTS_DIONAEA_DIR}:/opt/dionaea/var/dionaea"
      - "${DEFAULTS_LOG_DIR}:/opt/dionaea/var/log"
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
  local container="${1:-${TEST_CONTAINER_NAME}}"
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

    if output="$(docker exec "${container}" python3 - "${args[@]}" <<'PY' 2>&1
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


def ftp_connect(port, deadline):
    """A connection with its first line. Under emulation dionaea listens on the address of
    the container a moment after its sockets are bound: a refused or closed connection is
    tried again until the deadline."""
    while True:
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            return sock, read_line(sock, deadline)
        except (ProbeError, OSError):
            if sock is not None:
                sock.close()
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def probe_ftp(port, deadline):
    username = f"{token}-ftp"
    sock, line = ftp_connect(port, deadline)
    with sock:
        banner = line.decode("utf-8", errors="replace").strip()
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



for spec in specs:
    protocol, container_port_text, host_port_text = spec.split(":", 2)
    container_port = int(container_port_text)
    host_port = int(host_port_text)
    deadline = time.monotonic() + timeout

    try:
        if container_port == 21:
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

run_tftp_probe() {
  # TFTP replies originate at an ephemeral server port. Docker Desktop does
  # not forward that reply through the published UDP socket, so read it inside.
  docker exec -i "${TEST_CONTAINER_NAME}" python3 - "${TEST_TIMEOUT}" <<'PYTFTP'
import socket
import struct
import sys

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.settimeout(int(sys.argv[1]))
    sock.sendto(b"\0\1smoke-missing-file\0octet\0", ("127.0.0.1", 69))
    response, _ = sock.recvfrom(1024)
    if len(response) < 4 or struct.unpack("!H", response[:2])[0] not in (3, 5):
        raise RuntimeError("Expected TFTP DATA or ERROR response")
    print("TFTP RRQ received DATA/ERROR response")
PYTFTP
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

run_sample_store_probes() {
  local token="$1"

  # FTP passive data connections and TFTP answers do not pass the published
  # ports, so the probe runs inside the container against 127.0.0.1.
  docker exec -i "${TEST_CONTAINER_NAME}" python3 - "${token}" "${TEST_TIMEOUT}" <<'PY'
import hashlib
import os
import re
import socket
import stat
import struct
import sys
import time

token = sys.argv[1]
timeout = int(sys.argv[2])
HOST = "127.0.0.1"
BINARIES = "/opt/dionaea/var/dionaea/binaries"


class CheckError(Exception):
    pass


def ok(message):
    print(f"ok: {message}")


def read_line(sock, deadline):
    buf = b""
    while time.monotonic() < deadline:
        sock.settimeout(1.0)
        try:
            chunk = sock.recv(1)
        except socket.timeout:
            continue
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b"\n"):
            return buf.decode("utf-8", "replace").rstrip()
    raise CheckError(f"timed out waiting for an FTP reply, got {buf!r}")


def reply(sock, deadline):
    """One FTP reply, multi-line replies (123-) are read to their end."""
    line = read_line(sock, deadline)
    if len(line) > 3 and line[3] == "-":
        code = line[:3]
        while True:
            line = read_line(sock, deadline)
            if line.startswith(code + " "):
                break
    return line


def command(sock, deadline, text, expect):
    sock.sendall(text.encode() + b"\r\n")
    answer = reply(sock, deadline)
    if not answer.startswith(expect):
        raise CheckError(f"{text.split()[0]}: expected {expect}, got {answer!r}")
    return answer


def ftp_login(deadline):
    sock = socket.create_connection((HOST, 21), timeout=timeout)
    banner = reply(sock, deadline)
    if not banner.startswith("220"):
        raise CheckError(f"FTP banner: {banner!r}")
    answer = command(sock, deadline, "USER anonymous", ("230", "331"))
    if answer.startswith("331"):
        command(sock, deadline, "PASS probe@example.org", "230")
    command(sock, deadline, "TYPE I", "200")
    return sock


def pasv(sock, deadline):
    answer = command(sock, deadline, "PASV", "227")
    m = re.search(r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)", answer)
    if not m:
        raise CheckError(f"PASV: no address in {answer!r}")
    port = int(m.group(5)) * 256 + int(m.group(6))
    return socket.create_connection((HOST, port), timeout=timeout)


def ftp_active_store(name, data):
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((HOST, 0))
        listener.listen(1)
        listener.settimeout(timeout)
        port = listener.getsockname()[1]
        sock = ftp_login(deadline)
        try:
            command(sock, deadline, f"PORT 127,0,0,1,{port // 256},{port % 256}", "200")
            with listener.accept()[0] as data_sock:
                command(sock, deadline, f"STOR {name}", ("150", "125"))
                data_sock.sendall(data)
            if not reply(sock, deadline).startswith("226"):
                raise CheckError("Active FTP STOR did not complete")
            command(sock, deadline, "QUIT", "221")
        finally:
            sock.close()


def ftp_store(name, data):
    deadline = time.monotonic() + timeout
    sock = ftp_login(deadline)
    try:
        data_sock = pasv(sock, deadline)
        command(sock, deadline, f"STOR {name}", ("150", "125"))
        data_sock.sendall(data)
        data_sock.close()
        answer = reply(sock, deadline)
        if not answer.startswith("226"):
            raise CheckError(f"STOR was not completed: {answer!r}")
        command(sock, deadline, "QUIT", "221")
    finally:
        sock.close()


def ftp_fetch_fails(name):
    deadline = time.monotonic() + timeout
    sock = ftp_login(deadline)
    try:
        data_sock = pasv(sock, deadline)
        sock.sendall(f"RETR {name}\r\n".encode())
        answer = reply(sock, deadline)
        data_sock.settimeout(2)
        try:
            received = data_sock.recv(4096)
        except (socket.timeout, OSError):
            received = b""
        data_sock.close()
        if not answer.startswith("550"):
            raise CheckError(f"RETR {name}: expected 550, got {answer!r}")
        if received:
            raise CheckError(f"RETR {name} sent data: {received[:32]!r}")
    finally:
        sock.close()


def http(method, path, headers=None, body=b""):
    request = f"{method} {path} HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n"
    for key, value in (headers or {}).items():
        request += f"{key}: {value}\r\n"
    request += f"Content-Length: {len(body)}\r\n\r\n"
    with socket.create_connection((HOST, 81), timeout=timeout) as sock:
        sock.sendall(request.encode() + body)
        sock.settimeout(timeout)
        raw = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            raw += chunk
    status = raw.split(b"\r\n", 1)[0].decode("latin-1")
    parts = status.split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise CheckError(f"{method} {path}: no HTTP status in {raw[:64]!r}")
    return int(parts[1]), raw


def http_upload(name, data):
    boundary = f"----tpot{token}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    status, _raw = http("POST", "/upload.php", {"Content-Type": f"multipart/form-data; boundary={boundary}"}, body)
    if status >= 500:
        raise CheckError(f"POST answered {status}")


def http_fetch_fails(name, data):
    status, raw = http("GET", f"/{name}")
    if status != 404:
        raise CheckError(f"GET /{name}: expected 404, got {status}")
    if data in raw:
        raise CheckError(f"GET /{name} sent the upload back")


def tftp_exchange(packet):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(packet, (HOST, 69))
        answer, _addr = sock.recvfrom(1024)
    finally:
        sock.close()
    return answer


def tftp_error(answer, what):
    if len(answer) < 4 or struct.unpack("!H", answer[:2])[0] != 5:
        raise CheckError(f"{what}: expected a TFTP ERROR packet, got {answer[:16]!r}")
    return struct.unpack("!H", answer[2:4])[0]


def wait_for_sample(md5, deadline):
    path = os.path.join(BINARIES, md5)
    while time.monotonic() < deadline:
        if os.path.isfile(path):
            return path
        time.sleep(0.5)
    raise CheckError(f"sample {md5} did not appear in {BINARIES}: {sorted(os.listdir(BINARIES))}")


def check_mode(path):
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode != 0o440:
        raise CheckError(f"{path} has mode {mode:04o}, expected 0440")


ftp_name = f"{token}-ftp.bin"
http_name = f"{token}-http.bin"
ftp_data = (f"MZ ftp upload {token} ".encode() * 64)
http_data = (f"MZ http upload {token} ".encode() * 64)

try:
    ftp_store(ftp_name, ftp_data)
    ok(f"FTP STOR {ftp_name} accepted")
    ftp_active_store(ftp_name + "-active", ftp_data)
    ok("Active FTP PORT/STOR accepted")
    http_upload(http_name, http_data)
    ok(f"HTTP multipart POST {http_name} accepted")

    deadline = time.monotonic() + timeout
    for data, origin in ((ftp_data, "FTP"), (http_data, "HTTP")):
        md5 = hashlib.md5(data).hexdigest()
        path = wait_for_sample(md5, deadline)
        check_mode(path)
        ok(f"{origin} sample {md5} is in binaries/ with mode 0440")

    # Dionaea keeps the temp files of the samples in binaries/.incoming (a
    # folder, 0700 or 0770): folders are no samples, so the execute bit
    # check skips them, and the folder itself must be empty by now.
    leftovers = []
    for name in sorted(os.listdir(BINARIES)):
        st = os.stat(os.path.join(BINARIES, name))
        if stat.S_ISDIR(st.st_mode):
            continue
        if stat.S_IMODE(st.st_mode) & 0o111:
            leftovers.append(name)
    if leftovers:
        raise CheckError(f"files with an execute bit in binaries/: {leftovers}")
    ok("no file in binaries/ has an execute bit")

    incoming = os.path.join(BINARIES, ".incoming")
    if not os.path.isdir(incoming):
        raise CheckError(f"{incoming} is not a directory")
    stale = sorted(os.listdir(incoming))
    if stale:
        raise CheckError(f"files left in binaries/.incoming after the uploads: {stale}")
    ok("binaries/.incoming is a folder and holds no files")

    # A second connection, after the upload, must not get the file back
    for name, data in ((ftp_name, ftp_data), (http_name, http_data)):
        ftp_fetch_fails(name)
        ok(f"FTP RETR {name} fails (550)")
        http_fetch_fails(name, data)
        ok(f"HTTP GET /{name} fails (404)")
        code = tftp_error(
            tftp_exchange(b"\x00\x01" + name.encode() + b"\x00octet\x00"),
            f"TFTP RRQ {name}",
        )
        ok(f"TFTP RRQ {name} fails (ERROR {code})")

    code = tftp_error(
        tftp_exchange(b"\x00\x02" + f"{token}-wrq.bin".encode() + b"\x00octet\x00"),
        "TFTP WRQ",
    )
    ok(f"TFTP WRQ is rejected (ERROR {code})")
except (CheckError, OSError) as exc:
    print(f"sample store probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

assert_tmp_tmpfs() {
  # the files of the emulation (CreateFile, _lcreat) are made in /tmp, the root filesystem is read-only
  docker exec "${TEST_CONTAINER_NAME}" sh -c \
    'grep -q "^[^ ]* /tmp tmpfs " /proc/mounts && f=$(mktemp /tmp/emu-XXXXXX) && rm -f "$f"' \
    || test_die "/tmp is no tmpfs that uid 2000 can write"
}

# ticks (utime + stime) of all threads of the dionaea process
dionaea_process_ticks() {
  docker exec "${TEST_CONTAINER_NAME}" sh -c 'cut -d")" -f2- "/proc/$(pgrep -of dionaea)/stat" | awk "{print \$12 + \$13}"'
}

# The healthcheck (cpu_check.py, the main thread, the event loop) passes while
# random data on 445/tcp keeps the emu workers busy; the process as a whole may
# be near one core then (cpus 1).
assert_health_under_load() {
  local health="" hz="" before="" after="" output="" rc=0 load_pid=""

  health="$(docker inspect --format '{{index .Config.Healthcheck.Test 1}}' "${IMAGE}")"
  [[ "${health}" == *cpu_check.py* ]] || test_die "Unexpected healthcheck of the image: ${health}"
  hz="$(docker exec "${TEST_CONTAINER_NAME}" getconf CLK_TCK)"

  # 4 clients as fast as they can for 12 s, 1 MiB a connection (the emu scans the first
  # 4 MiB of each); the dumps stop at the bistream cap of the harness
  python3 - "${TEST_BIND_IP}" "${MAPPED_TCP_PORTS[445]}" 12 <<'PY' &
import os, socket, sys, threading, time
host, port, seconds = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
end = time.monotonic() + seconds

def run():
    while time.monotonic() < end:
        try:
            with socket.create_connection((host, port), timeout=5) as sock:
                for _ in range(32):
                    if time.monotonic() >= end:
                        break
                    body = os.urandom(32768)
                    # NetBIOS frames of a type smbd ignores (0x86): the connection stays
                    sock.sendall(bytes([0x86]) + len(body).to_bytes(3, "big") + body)
        except OSError:
            time.sleep(0.2)

threads = [threading.Thread(target=run) for _ in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
PY
  load_pid=$!

  sleep 4
  before="$(dionaea_process_ticks)"
  output="$(docker exec "${TEST_CONTAINER_NAME}" sh -c "${health}" 2>&1)" || rc=$?
  after="$(dionaea_process_ticks)"
  wait "${load_pid}" || true
  test_info "Under load the dionaea process used $(( (after - before) * 100 / hz / 3 ))% of one core; ${output##*$'\n'}"
  [[ ${rc} -eq 0 ]] || test_die "The healthcheck failed under load: ${output}"
}

assert_roots_untouched() {
  local root=""
  local entries=""

  for root in ftp tftp www upnp; do
    entries="$(find "${DIONAEA_DIR}/roots/${root}" -mindepth 1 2>/dev/null | head -n 5)"
    if [[ -n "${entries}" ]]; then
      test_die "Dionaea wrote into roots/${root}: ${entries}"
    fi
  done
}

# count_warning_keys ERRORS_LOG: the warnings of the log per message, the most frequent first, as
# "COUNT domain: message" (numbers as N, addresses as ADDR). The key of a summary ends at the first
# " in the last minute, the last: ", and only where the text before it ends in ": N <unit>": its
# last text may hold the words of a summary itself (the rule of the fork, tests/functional/tools/
# count_warnings.sh).
count_warning_keys() {
  { grep -E '^\[[0-9]{8} [0-9:]{8}\] [^ ]+( [^ ]+)?-warning: ' "$1" || true; } \
    | sed -E 's/^\[[^]]*\] ([^ ]+)( [^ ]+)?-warning: /\1: /; s/0x[0-9a-fA-F]+/ADDR/g; s/\.incoming\/[^ :]+/.incoming\/FILE/g; s/(^|[^A-Za-z0-9_])[0-9]+/\1N/g' \
    | awk '
        {
          at = index($0, " in the last minute, the last: ")
          if (at > 0 && substr($0, 1, at - 1) ~ /: N [^:,]+$/)
            $0 = substr($0, 1, at - 1) " in the last minute"
          print
        }' \
    | sort | uniq -c | sort -rn
}

# self_test_warning_keys: count_warning_keys on a made-up log, no Docker. A summary whose last text
# holds the words of a summary is one key; a message that is no summary keeps its whole text.
self_test_warning_keys() {
  local tmp counts="" lines=""

  tmp="$(mktemp -d "${TMPDIR:-/tmp}/dionaea-warnkeys.XXXXXX")"
  cat > "${tmp}/errors.log" <<'LOG'
[21102026 10:00:01] processor-warning: scans stopped at a limit: 3 scan(s) in the last minute, the last: a: 1 byte in the last minute, the last: x
[21102026 10:01:01] processor-warning: scans stopped at a limit: 5 scan(s) in the last minute, the last: b
[21102026 10:02:01] processor-warning: scans stopped at a limit: 7 scan(s) in the last minute, the last: c: 9 thing(s) in the last minute, the last: y
[21102026 10:02:02] log_sqlite log_sqlite.py:1-warning: incidents not recorded in the sqlite log: 2 incident(s) in the last minute, the last: d
[21102026 10:02:03] connection-warning: peer 0x7f00 sent a, in the last minute, the last: z
LOG
  counts="$(count_warning_keys "${tmp}/errors.log")"
  rm -rf "${tmp}"
  lines="$(printf '%s\n' "${counts}" | sed -E 's/^ *//' | sort)"
  [[ "$(printf '%s\n' "${lines}" | grep -c .)" == "3" ]] \
    || test_die "warning keys self-test: expected 3 keys, got: ${lines}"
  grep -qxF '3 processor: scans stopped at a limit: N scan(s) in the last minute' <<< "${lines}" \
    || test_die "warning keys self-test: the summaries with a nested summary text are not one key: ${lines}"
  grep -qxF '1 log_sqlite: incidents not recorded in the sqlite log: N incident(s) in the last minute' <<< "${lines}" \
    || test_die "warning keys self-test: the sqlite summary key is wrong: ${lines}"
  grep -qxF '1 connection: peer ADDR sent a, in the last minute, the last: z' <<< "${lines}" \
    || test_die "warning keys self-test: a message that is no summary was cut: ${lines}"
  test_ok "Warning keys: a summary with the words of a summary in its last text is one key"
}

# assert_warnings_summed ERRORS_LOG LABEL: a warning that every connection can write is info, with
# one warning a minute that sums it up ("<what>: N <unit> in the last minute, the last: <text>").
# So no warning message is more than 5 times in the errors log of a run, and a summary is there
# once per started minute of the run plus once more. Messages are counted with numbers as N,
# addresses as ADDR; a summary by the text before its number. A line of the log:
# "[DDMMYYYY HH:MM:SS] domain[ file:line]-level: message". The run is the time between the
# first and the last line of the log (the start and the stop of dionaea).
assert_warnings_summed() {
  local log="$1" label="$2" minutes="" counts="" over=""

  [[ -f "${log}" ]] || return 0
  minutes="$(sed -nE 's/^\[[0-9]{8} ([0-9]{2}):([0-9]{2}):([0-9]{2})\].*$/\1 \2 \3/p' "${log}" \
    | awk 'NR == 1 { first = $1 * 3600 + $2 * 60 + $3 }
           { last = $1 * 3600 + $2 * 60 + $3 }
           END { if (NR == 0) { print 1; exit }
                 if (last < first) last += 86400
                 print int(last / 60) - int(first / 60) + 1 }')"
  counts="$(count_warning_keys "${log}")"
  if [[ -n "${counts}" ]]; then
    test_info "Warnings of dionaea-errors.log (${label}, ${minutes} minute(s) started), per message:"
    printf '%s\n' "${counts}" | head -n 12 | sed 's/^/    /'
  fi
  over="$(printf '%s\n' "${counts}" | awk -v summary_max="$(( minutes + 1 ))" -v max=5 '
      NF { bound = ($0 ~ / in the last minute$/) ? summary_max : max
           if ($1 > bound) print }')"
  if [[ -n "${over}" ]]; then
    printf '%s\n' "${over}" >&2
    test_die "A warning of dionaea-errors.log is more often there than once per minute and 5 times (${label}): every connection must not write one"
  fi
}

# assert_no_runtime_errors [DIONAEA_DIR LOG_DIR LABEL]: the logs of the run of the test, or
# of the container whose folders are given
assert_no_runtime_errors() {
  local dionaea_dir="${1:-${DIONAEA_DIR}}" log_dir="${2:-${LOG_DIR}}" label="${3:-test}"
  local error_log="${dionaea_dir}/dionaea-errors.log"

  if grep -R -I -E "Traceback|LoaderError|Assertion .*failed|Segmentation fault|CRITICAL" "${log_dir}" >/dev/null 2>&1; then
    test_die "Dionaea runtime error found in the log artifacts (${label})"
  fi

  if [[ -f "${error_log}" ]] && grep -I -E "Traceback|LoaderError|Assertion .*failed|Segmentation fault|CRITICAL" "${error_log}" >/dev/null 2>&1; then
    test_die "Dionaea runtime error found in dionaea-errors.log (${label})"
  fi

  assert_warnings_summed "${error_log}" "${label}"

  # dionaea-errors.log has the critical level (errors of the services); a client's data is
  # logged at info or debug, so a probe run leaves no critical line: <domain>-critical:
  if [[ -f "${error_log}" ]] && grep -I -E -- '-critical:' "${error_log}" >/dev/null 2>&1; then
    test_die "Dionaea logged a critical line in dionaea-errors.log (${label})"
  fi
}

assert_resource_limits() {
  local limits=""
  local memory="" swap="" pids="" cpus=""

  limits="$(docker inspect --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.PidsLimit}} {{.HostConfig.NanoCpus}}' "${TEST_CONTAINER_NAME}")"
  read -r memory swap pids cpus <<< "${limits}"
  # 1 GiB memory, 1 GiB memory + swap (no swap), 256 processes, 1 CPU
  if [[ "${swap}" == "-1" ]]; then
    # the daemon reports -1 where the kernel has no swap accounting (i.e. Raspberry Pi OS)
    test_info "Note: MemorySwap is -1, this kernel has no swap accounting; the swap limit is not checked"
    swap="1073741824"
  fi
  if [[ "${memory} ${swap} ${pids} ${cpus}" != "1073741824 1073741824 256 1000000000" ]]; then
    test_die "Dionaea resource limits differ from T-Pot (memory memswap pids nanocpus): ${limits}"
  fi
}

# assert_limit_lines LOG LABEL LINE...: dionaea.log has each LINE as a whole info message
# (after "[DDMMYYYY HH:MM:SS] <domain> <file>:<line>-info: "), and one line per limit
# only. dionaea writes the rest of its log files when it stops, so LOG is read after that;
# a bind mount of Docker Desktop can show the end of the file a moment later, so LOG is
# read again until it has every LINE or TEST_TIMEOUT is over.
assert_limit_lines() {
  local log="$1" label="$2" messages="" line="" key="" count="" missing=""
  local deadline=$((SECONDS + TEST_TIMEOUT))
  shift 2

  while :; do
    # the info messages without their prefix, read once (a grep -q in a pipe would end sed early)
    messages="$(sed -nE 's/^\[[0-9]{8} [0-9]{2}:[0-9]{2}:[0-9]{2}\] [^ ]+( [^ ]+)?-info: //p' "${log}" 2>/dev/null || true)"
    missing=""
    for line in "$@"; do
      grep -qxF -- "${line}" <<< "${messages}" || missing="${line}"
    done
    [[ -z "${missing}" ]] && break
    (( SECONDS < deadline )) || break
    sleep 1
  done

  [[ -s "${log}" ]] || test_die "${label}: dionaea.log is missing or empty (${log})"
  for line in "$@"; do
    # "streamdumper: max_size", "streamdumper: max_files", "store: max_size"
    key="$(cut -d ' ' -f 1-2 <<< "${line}")"
    count="$(grep -c -- "^${key} " <<< "${messages}" || true)"
    if ! grep -qxF -- "${line}" <<< "${messages}"; then
      test_info "${label}: dionaea.log has $(wc -c < "${log}" | tr -d ' ') bytes, its lines about the limits:"
      grep -E 'streamdumper: max_|store: max_size|DIONAEA_(STREAMDUMPER|STORE)_' "${log}" >&2 || true
      test_die "${label}: dionaea.log has no line '${line}'"
    fi
    [[ "${count}" == "1" ]] || test_die "${label}: dionaea.log has ${count} lines '${key} ...' (expected 1)"
    test_ok "${label}: dionaea.log: ${line}"
  done
}

# stop_dionaea CONTAINER: stop it the way Docker stops T-Pot (docker stop, the stop signal
# of the image: SIGINT), with time for the emu workers, so that dionaea closes (writes) its
# log files. dionaea (PID 1) handles SIGINT and SIGTERM before its services listen, so the
# stop comes as soon as the ports listen; it must end with exit code 0 (a signal it
# ignored ends in a kill, 137).
stop_dionaea() {
  local container="$1" state=""

  docker stop --time 60 "${container}" >/dev/null || test_die "Could not stop ${container}"
  state="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}' "${container}" 2>/dev/null || true)"
  [[ "${state}" == "exited 0 false" ]] \
    || test_die "${container} did not stop cleanly (status, exit code, OOM killed): ${state}"
}

# The limits from the environment (the run of the test) and, in a second start without the
# variables, the limits of dionaea.cfg and store.yaml as T-Pot ships them.
assert_storage_limits_logged() {
  stop_dionaea "${TEST_CONTAINER_NAME}"
  assert_limit_lines "${DIONAEA_DIR}/dionaea.log" "environment" "${LIMIT_LINES_ENV[@]}"

  test_info "Starting Dionaea without the limit variables"
  test_compose up -d --no-build dionaea-defaults >/dev/null \
    || test_die "Could not start ${DEFAULTS_CONTAINER_NAME}"
  wait_for_container_ports "${DEFAULTS_CONTAINER_NAME}" \
    || test_die "Not all listener sockets of ${DEFAULTS_CONTAINER_NAME} became available"
  stop_dionaea "${DEFAULTS_CONTAINER_NAME}"
  assert_limit_lines "${DEFAULTS_DIONAEA_DIR}/dionaea.log" "defaults" "${LIMIT_LINES_DEFAULTS[@]}"
}

main() {
  parse_args "$@"
  if [[ -n "${SELF_TEST_WARNINGS}" ]]; then
    self_test_warning_keys
    exit 0
  fi
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
  test_compose up -d --no-build dionaea >/dev/null

  test_wait_for_container || test_die "Dionaea container did not stay running"
  test_ok "Container is running"
  assert_resource_limits
  test_ok "Memory, swap, process and CPU limits are set as in T-Pot"
  assert_tmp_tmpfs
  test_ok "/tmp is a tmpfs uid 2000 can write"

  resolve_mapped_ports

  test_info "Waiting for all Dionaea listener sockets"
  wait_for_container_ports || test_die "Not all Dionaea listener sockets became available"
  test_ok "All Dionaea listener sockets are available"

  local token="dionaea-test-$(date +%s)-$$"
  test_info "Running Dionaea protocol probes with token: ${token}"
  run_port_probes "${token}" || test_die "One or more Dionaea port probes failed"
  run_tftp_probe || test_die "TFTP RRQ response probe failed"
  test_ok "All Dionaea port probes completed"

  test_info "Waiting for FTP USER event in dionaea.json"
  wait_for_json_ftp_event "${token}" || test_die "FTP USER event was not found in dionaea.json"
  test_ok "FTP USER event was written to dionaea.json"

  test_info "Checking the sample store (FTP, HTTP, TFTP)"
  run_sample_store_probes "${token}" || test_die "The sample store check failed"
  test_ok "Uploads are stored as 0440 samples and cannot be fetched back"
  test_info "Checking SMB2 rejected authentication and JSON/SQLite metadata"
  docker exec -i "${TEST_CONTAINER_NAME}" python3 - \
    --token "${token}" --timeout "${TEST_TIMEOUT}" \
    --json /opt/dionaea/var/log/dionaea.json \
    --sqlite /opt/dionaea/var/log/dionaea.sqlite \
    < "${SCRIPT_DIR}/../lib/dionaea_auth.py" || test_die "SMB2 authentication capture failed"
  test_ok "SMB2 2.02/2.10 and NTLMv1/v2 are recorded with empty password fields"
  assert_roots_untouched
  test_ok "The read-only service roots stayed empty"
  assert_health_under_load
  test_ok "The healthcheck passes while the emu workers are busy"

  self_test_warning_keys
  test_wait_for_container || test_die "Dionaea container stopped after port probes"
  assert_storage_limits_logged
  test_ok "The storage limits of the environment and of the config are in dionaea.log"
  assert_no_runtime_errors
  assert_no_runtime_errors "${DEFAULTS_DIONAEA_DIR}" "${DEFAULTS_LOG_DIR}" "defaults"
  test_ok "No Dionaea runtime errors found in test artifacts"

  test_ok "Dionaea post-build smoke test completed successfully"
}

main "$@"
