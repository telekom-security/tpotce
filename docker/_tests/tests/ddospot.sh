#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="ddospot"
DEFAULT_IMAGE="dtagdevsec/ddospot:24.04.2"
IMAGE=""
CHARGEN_PORT=""
DNS_PORT=""
DNS_TCP_PORT=""
NTP_PORT=""
SSDP_PORT=""
RECURSION="off"
LOG_ROOT=""
LOG_DIR=""
STDOUT_DIR=""
RUN_NAME=""
RUN_PERSONA=""
MAPPED_CHARGEN_PORT=""
MAPPED_DNS_PORT=""
MAPPED_DNS_TCP_PORT=""
MAPPED_NTP_PORT=""
MAPPED_SSDP_PORT=""
FIRST_PERSONA=""
FIRST_USN=""
SECOND_PERSONA=""
SECOND_USN=""

# A name with a TXT answer larger than 512 bytes, for the check with recursion
LARGE_TXT_NAME="microsoft.com"

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the DDoSPot image.

Options:
  --image IMAGE          Image to test. Defaults to docker/ddospot/docker-compose.yml.
  --chargen-port PORT    Host UDP port for CHARGEN. Default: dynamic free port.
  --dns-port PORT        Host UDP port for DNS. Default: dynamic free port.
  --dns-tcp-port PORT    Host TCP port for DNS. Default: dynamic free port.
  --ntp-port PORT        Host UDP port for NTP. Default: dynamic free port.
  --ssdp-port PORT       Host UDP port for SSDP. Default: dynamic free port.
  --recursion            Also check DNS recursion (needs internet access).
  --timeout SEC          Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP           Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts       Keep temporary compose file and logs for debugging.
  -h, --help             Show this help message.
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
      --chargen-port)
        [[ $# -ge 2 ]] || test_die "--chargen-port requires an argument"
        CHARGEN_PORT="$2"
        shift 2
        ;;
      --chargen-port=*)
        CHARGEN_PORT="${1#*=}"
        shift
        ;;
      --dns-port)
        [[ $# -ge 2 ]] || test_die "--dns-port requires an argument"
        DNS_PORT="$2"
        shift 2
        ;;
      --dns-port=*)
        DNS_PORT="${1#*=}"
        shift
        ;;
      --dns-tcp-port)
        [[ $# -ge 2 ]] || test_die "--dns-tcp-port requires an argument"
        DNS_TCP_PORT="$2"
        shift 2
        ;;
      --dns-tcp-port=*)
        DNS_TCP_PORT="${1#*=}"
        shift
        ;;
      --ntp-port)
        [[ $# -ge 2 ]] || test_die "--ntp-port requires an argument"
        NTP_PORT="$2"
        shift 2
        ;;
      --ntp-port=*)
        NTP_PORT="${1#*=}"
        shift
        ;;
      --ssdp-port)
        [[ $# -ge 2 ]] || test_die "--ssdp-port requires an argument"
        SSDP_PORT="$2"
        shift 2
        ;;
      --ssdp-port=*)
        SSDP_PORT="${1#*=}"
        shift
        ;;
      --recursion)
        RECURSION="on"
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
  local port
  for port in "${CHARGEN_PORT}" "${DNS_PORT}" "${DNS_TCP_PORT}" "${NTP_PORT}" "${SSDP_PORT}"; do
    if [[ -n "${port}" ]]; then
      test_validate_port "${port}"
    fi
  done
}

ensure_udp_port_free_for_docker() {
  local port="$1"
  local option="$2"

  if [[ -z "${port}" ]]; then
    return 0
  fi

  if (( port < 1024 )); then
    test_info "Skipping user-space preflight for privileged UDP port ${port}; Docker will validate the binding."
  else
    test_ensure_udp_port_free "${TEST_BIND_IP}" "${port}" || test_die "${TEST_BIND_IP}:${port}/udp is already in use. Try ${option} <free-port>."
  fi
}

ensure_tcp_port_free_for_docker() {
  local port="$1"
  local option="$2"

  if [[ -z "${port}" ]]; then
    return 0
  fi

  if (( port < 1024 )); then
    test_info "Skipping user-space preflight for privileged TCP port ${port}; Docker will validate the binding."
  else
    test_ensure_port_free "${TEST_BIND_IP}" "${port}" || test_die "${TEST_BIND_IP}:${port}/tcp is already in use. Try ${option} <free-port>."
  fi
}

prepare_ddospot_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_ROOT="${TEST_TMP_ROOT}/log"
  STDOUT_DIR="${TEST_TMP_ROOT}/stdout"
  mkdir -p "${LOG_ROOT}" "${STDOUT_DIR}"
  chmod 0777 "${LOG_ROOT}"
}

# write_compose <log-dir> <recursion>: the compose file of one container run
write_compose() {
  local log_dir="$1"
  local recursion="$2"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  ddospot:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    environment:
      - "DDOSPOT_DNS_RECURSION=${recursion}"
      - "DDOSPOT_CLASSIFY_SECONDS=2"
    ports:
      - "${TEST_BIND_IP}:${CHARGEN_PORT}:19/udp"
      - "${TEST_BIND_IP}:${DNS_PORT}:53/udp"
      - "${TEST_BIND_IP}:${DNS_TCP_PORT}:53/tcp"
      - "${TEST_BIND_IP}:${NTP_PORT}:123/udp"
      - "${TEST_BIND_IP}:${SSDP_PORT}:1900/udp"
    volumes:
      - "${log_dir}:/var/log/ddospot"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

# start_run <name> [recursion]: a new container with its own log folder. The
# budgets are kept in memory, so each group of checks starts with a fresh
# container; all probes come from one source address.
start_run() {
  RUN_NAME="$1"
  local recursion="${2:-off}"

  LOG_DIR="${LOG_ROOT}/${RUN_NAME}"
  mkdir -p "${LOG_DIR}"
  chmod 0777 "${LOG_DIR}"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"
  write_compose "${LOG_DIR}" "${recursion}"

  test_info "Starting DDoSPot container (${RUN_NAME}, recursion ${recursion})"
  local output=""
  if ! output="$(test_compose up -d --no-build 2>&1)"; then
    printf '%s\n' "${output}" >&2
    test_die "DDoSPot container did not start"
  fi
  test_wait_for_container || test_die "DDoSPot container did not stay running"

  MAPPED_CHARGEN_PORT="$(test_get_mapped_port "${TEST_NAME}" "19/udp")" || test_die "Could not resolve mapped host port for 19/udp"
  MAPPED_DNS_PORT="$(test_get_mapped_port "${TEST_NAME}" "53/udp")" || test_die "Could not resolve mapped host port for 53/udp"
  MAPPED_DNS_TCP_PORT="$(test_get_mapped_port "${TEST_NAME}" "53/tcp")" || test_die "Could not resolve mapped host port for 53/tcp"
  MAPPED_NTP_PORT="$(test_get_mapped_port "${TEST_NAME}" "123/udp")" || test_die "Could not resolve mapped host port for 123/udp"
  MAPPED_SSDP_PORT="$(test_get_mapped_port "${TEST_NAME}" "1900/udp")" || test_die "Could not resolve mapped host port for 1900/udp"

  wait_for_persona_line || test_die "DDoSPot did not print its start line"
  wait_for_dns_tcp || test_die "DNS on ${TEST_BIND_IP}:${MAPPED_DNS_TCP_PORT}/tcp did not accept a connection"
  test_wait_for_container || test_die "DDoSPot container stopped after start"
}

# stop_run: keeps the output of the container, then removes it
stop_run() {
  save_run_output
  test_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}

save_run_output() {
  test_compose logs --no-color --no-log-prefix ddospot > "${STDOUT_DIR}/${RUN_NAME}.log" 2>&1 || true
}

# wait_for_persona_line: the first line of the output names version and
# persona; sets RUN_PERSONA
wait_for_persona_line() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local first=""

  while (( SECONDS < deadline )); do
    first="$(test_compose logs --no-color --no-log-prefix ddospot 2>/dev/null | head -n 1 || true)"
    if [[ -n "${first}" ]]; then
      break
    fi
    sleep 1
  done

  if [[ ! "${first}" =~ ^ddospot\ v[0-9]+\.[0-9]+\.[0-9]+\ persona=([A-Za-z0-9._-]+)$ ]]; then
    printf 'Unexpected first output line: %s\n' "${first:-<none>}" >&2
    return 1
  fi
  RUN_PERSONA="${BASH_REMATCH[1]}"
  test_ok "Start line: ${first}"
}

# wait_for_dns_tcp: DDoSPot binds all its sockets before the services
# start, so a connection on 53/tcp that stays open means it is ready. A
# Docker port proxy accepts the connect before the container listens and
# then closes it, so the connection has to stay open for 0.3 s. The connect
# sends no query, so it counts against no budget.
wait_for_dns_tcp() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_DNS_TCP_PORT}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
deadline = time.monotonic() + int(sys.argv[3])

while time.monotonic() < deadline:
    try:
        with socket.create_connection((host, port), timeout=1) as sock:
            sock.settimeout(0.3)
            try:
                sock.recv(1)
            except socket.timeout:
                sys.exit(0)
    except OSError:
        pass
    time.sleep(0.2)
sys.exit(1)
PY
}

# version_bind_of <persona>: the version.bind value of a persona
version_bind_of() {
  case "$1" in
    centos6) printf '%s\n' "9.8.2rc1-RedHat-9.8.2-0.68.rc1.el6_10.8" ;;
    centos7) printf '%s\n' "9.11.4-P2-RedHat-9.11.4-26.P2.el7_9.16" ;;
    debian11) printf '%s\n' "dnsmasq-2.85" ;;
    ubuntu2004) printf '%s\n' "dnsmasq-2.90" ;;
    *) return 1 ;;
  esac
}

# run_chargen_probe <count> <expected>: sends count probes one after the
# other and expects that many replies
run_chargen_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_CHARGEN_PORT}" "$1" "$2" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
count = int(sys.argv[3])
expected = int(sys.argv[4])
payload = b"tpot-ddospot-chargen\n"

replies = 0
try:
    for _ in range(count):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.5)
            sock.sendto(payload, (host, port))
            try:
                response, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
        if not response or len(response) > 1024:
            raise RuntimeError(f"Expected a CHARGEN reply of 1 to 1024 bytes, got {len(response)} bytes")
        replies += 1
    if replies != expected:
        raise RuntimeError(f"Expected {expected} of {count} CHARGEN probes answered, got {replies}")
    print(f"CHARGEN: {replies} of {count} probes answered")
except Exception as exc:
    print(f"CHARGEN probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

# run_dns_probe <udp|tcp> <check> <qtype> <qclass> <expected> <name>...:
# one query per name, expects that many answers, each must pass the check
# (version, refused, any, recursive, truncated, full)
run_dns_probe() {
  local transport="$1"
  local port="${MAPPED_DNS_PORT}"

  if [[ "${transport}" == "tcp" ]]; then
    port="${MAPPED_DNS_TCP_PORT}"
  fi

  python3 - "${TEST_BIND_IP}" "${port}" "${RUN_PERSONA}" "$(version_bind_of "${RUN_PERSONA}" || true)" "$@" <<'PY'
import random
import socket
import struct
import sys

host = sys.argv[1]
port = int(sys.argv[2])
persona = sys.argv[3]
persona_version = sys.argv[4]
transport, check, qtype_name, qclass_name = sys.argv[5:9]
expected = int(sys.argv[9])
names = sys.argv[10:]
QTYPES = {"A": 1, "TXT": 16}
QCLASSES = {"IN": 1, "CH": 3}
RCODES = {0: "NOERROR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}
# an answer through the upstream resolver may take up to 3 s
timeout = 5 if check in ("recursive", "truncated", "full") else 2


def query(name, tid):
    labels = [label.encode("ascii") for label in name.rstrip(".").split(".")]
    question = b"".join(bytes([len(label)]) + label for label in labels) + b"\x00"
    return (
        struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)
        + question
        + struct.pack("!HH", QTYPES[qtype_name], QCLASSES[qclass_name])
    )


def skip_name(msg, pos):
    while True:
        length = msg[pos]
        if length & 0xC0 == 0xC0:
            return pos + 2
        if length == 0:
            return pos + 1
        pos += 1 + length


def answers(msg, qdcount, ancount):
    pos = 12
    for _ in range(qdcount):
        pos = skip_name(msg, pos) + 4
    records = []
    for _ in range(ancount):
        pos = skip_name(msg, pos)
        rtype, _, _, rdlength = struct.unpack("!HHIH", msg[pos:pos + 10])
        pos += 10
        rdata = msg[pos:pos + rdlength]
        pos += rdlength
        records.append((rtype, rdata))
    return records


def txt_value(rdata):
    out, pos = [], 0
    while pos < len(rdata):
        length = rdata[pos]
        out.append(rdata[pos + 1:pos + 1 + length].decode("utf-8", errors="replace"))
        pos += 1 + length
    return "".join(out)


def exchange(msg):
    if transport == "tcp":
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(struct.pack("!H", len(msg)) + msg)
            data = b""
            while True:
                if len(data) >= 2 and len(data) >= 2 + struct.unpack("!H", data[:2])[0]:
                    return data[2:2 + struct.unpack("!H", data[:2])[0]]
                try:
                    chunk = sock.recv(65535)
                except socket.timeout:
                    return b""
                if not chunk:
                    return b""
                data += chunk
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(msg, (host, port))
        try:
            response, _ = sock.recvfrom(65535)
        except socket.timeout:
            return b""
        return response


def verify(name, response, tid):
    if len(response) < 12:
        raise RuntimeError(f"{name}: expected a DNS answer, got {len(response)} bytes")
    rid, flags, qdcount, ancount, _, _ = struct.unpack("!HHHHHH", response[:12])
    if rid != tid:
        raise RuntimeError(f"{name}: expected id {tid:#x}, got {rid:#x}")
    if not flags & 0x8000:
        raise RuntimeError(f"{name}: expected the answer flag, got flags {flags:#x}")
    rcode = flags & 0x000F
    tc = bool(flags & 0x0200)
    ra = bool(flags & 0x0080)
    rcode_name = RCODES.get(rcode, str(rcode))
    records = answers(response, qdcount, ancount)
    detail = f"{name}: {rcode_name}, {ancount} answers, {len(response)} bytes over {transport}"
    if check == "version":
        txt = [txt_value(rdata) for rtype, rdata in records if rtype == 16]
        if rcode != 0 or not txt or not txt[0]:
            raise RuntimeError(f"{detail}: expected a TXT value")
        if not persona_version:
            raise RuntimeError(f"{detail}: no version.bind value known for persona {persona}")
        if txt[0] != persona_version:
            raise RuntimeError(f"{detail}: expected {persona_version!r} for persona {persona}, got {txt[0]!r}")
        detail += f", value {txt[0]!r} of persona {persona}"
    elif check == "refused":
        if rcode != 5:
            raise RuntimeError(f"{detail}: expected REFUSED")
    elif check == "recursive":
        if rcode != 0 or not ra or not records:
            raise RuntimeError(f"{detail}, RA {ra}: expected NOERROR with RA and an answer")
        detail += ", RA set"
    elif check == "truncated":
        if not tc:
            raise RuntimeError(f"{detail}: expected the TC flag")
        if len(response) > 512:
            raise RuntimeError(f"{detail}: expected at most 512 bytes")
        detail += ", TC set"
    elif check == "full":
        if tc or rcode != 0 or not records or len(response) <= 512:
            raise RuntimeError(f"{detail}, TC {tc}: expected the full answer of more than 512 bytes")
    elif check != "any":
        raise RuntimeError(f"unknown check {check}")
    print(detail)


try:
    answered = 0
    for name in names:
        tid = random.randrange(0, 65536)
        response = exchange(query(name, tid))
        if not response:
            print(f"{name}: no answer over {transport}")
            continue
        verify(name, response, tid)
        answered += 1
    if answered != expected:
        raise RuntimeError(f"Expected {expected} of {len(names)} DNS queries answered, got {answered}")
    print(f"DNS: {answered} of {len(names)} queries answered")
except Exception as exc:
    print(f"DNS probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

# run_ntp_probe <expected> <mode>...: one request per mode (3 = client,
# 6 = READVAR), expects that many answered requests
run_ntp_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_NTP_PORT}" "$@" <<'PY'
import socket
import struct
import sys

host = sys.argv[1]
port = int(sys.argv[2])
expected = int(sys.argv[3])
modes = [int(mode) for mode in sys.argv[4:]]


def request(mode, seq):
    if mode == 3:
        return b"\x1b" + (b"\x00" * 47)
    return struct.pack("!BBHHHHH", 0x16, 0x02, seq, 0, 0, 0, 0)


def verify(mode, seq, response):
    if mode == 3:
        if len(response) < 48:
            raise RuntimeError(f"Expected an NTP reply of at least 48 bytes, got {len(response)} bytes")
        if response[0] & 0x07 != 4:
            raise RuntimeError(f"Expected NTP server mode 4, got mode {response[0] & 0x07}")
        if (response[0] >> 3) & 0x07 < 1:
            raise RuntimeError(f"Expected a valid NTP version, got {(response[0] >> 3) & 0x07}")
        return f"mode 3 request: {len(response)} bytes, mode 4"
    if len(response) < 12:
        raise RuntimeError(f"Expected a mode 6 reply of at least 12 bytes, got {len(response)} bytes")
    op = response[1]
    rseq = struct.unpack("!H", response[2:4])[0]
    if response[0] & 0x07 != 6 or not op & 0x80 or op & 0x1F != 2 or rseq != seq:
        raise RuntimeError(f"Expected a READVAR reply with sequence {seq}, got header {response[:12].hex()}")
    return f"mode 6 request: {len(response)} bytes, READVAR reply"


try:
    answered = 0
    for seq, mode in enumerate(modes, start=1):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.5)
            sock.sendto(request(mode, seq), (host, port))
            try:
                response, _ = sock.recvfrom(4096)
            except socket.timeout:
                print(f"mode {mode} request: no reply")
                continue
        print(verify(mode, seq, response))
        answered += 1
    if answered != expected:
        raise RuntimeError(f"Expected {expected} of {len(modes)} NTP requests answered, got {answered}")
    print(f"NTP: {answered} of {len(modes)} requests answered")
except Exception as exc:
    print(f"NTP probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

# run_ssdp_probe <mx> <max-seconds>: one M-SEARCH for upnp:rootdevice,
# expects one reply within max-seconds; prints its USN as usn=<value>
run_ssdp_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_SSDP_PORT}" "$1" "$2" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
mx = sys.argv[3]
max_seconds = float(sys.argv[4])
request = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    "MAN: \"ssdp:discover\"\r\n"
    f"MX: {mx}\r\n"
    "ST: upnp:rootdevice\r\n"
    "USER-AGENT: tpot-ddospot-smoke\r\n"
    "\r\n"
).encode("ascii")

try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(max_seconds)
        start = time.monotonic()
        sock.sendto(request, (host, port))
        try:
            response, _ = sock.recvfrom(4096)
        except socket.timeout:
            raise RuntimeError(f"Expected an SSDP reply within {max_seconds:g} s, got none")
        elapsed = time.monotonic() - start

    if not response.startswith(b"HTTP/1.1 200 OK\r\n"):
        raise RuntimeError(f"Expected an SSDP reply starting with HTTP/1.1 200 OK, got {response[:60]!r}")
    if not response.endswith(b"\r\n\r\n"):
        raise RuntimeError(f"Expected an SSDP reply ending with an empty line, got {response[-60:]!r}")
    usn = ""
    for line in response.decode("iso-8859-1").split("\r\n"):
        name, _, value = line.partition(":")
        if name.strip().upper() == "USN":
            usn = value.strip()
    if not usn:
        raise RuntimeError("Expected a USN header in the SSDP reply")
    print(f"SSDP: MX {mx}, reply of {len(response)} bytes after {elapsed:.2f} s")
    print(f"usn={usn}")
except Exception as exc:
    print(f"SSDP probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

# run_probe_with_retries <description> <command>...: at most three tries,
# so that a probe sent while the container starts stays within the budget
run_probe_with_retries() {
  local description="$1"
  shift
  local output=""
  local attempt

  for attempt in 1 2 3; do
    if output="$("$@" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  printf '%s probe did not succeed after %s tries\n' "${description}" "${attempt}" >&2
  return 1
}

# check_log_events <log-dir> <check> <persona> <timeout>: scanner (one
# scanner event in each pot log, no targets.log lines), target_start (one
# start event in targets.log) or target_end (one start and one end event)
check_log_events() {
  python3 - "$@" <<'PY'
import json
import sys
import time
from pathlib import Path

log_dir = Path(sys.argv[1])
check = sys.argv[2]
persona = sys.argv[3]
deadline = time.monotonic() + int(sys.argv[4])
POTS = ("chargenpot.log", "dnspot.log", "ntpot.log", "ssdpot.log")


def events(name):
    path = log_dir / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def scanner():
    for name in POTS:
        lines = events(name)
        if len(lines) != 1:
            return f"{name}: expected 1 event, got {len(lines)}"
        event = lines[0]
        if event.get("role") != "scanner" or not event.get("src_ip") or event.get("persona") != persona:
            return f"{name}: expected role scanner, src_ip and persona {persona}, got {event}"
    target_lines = events("targets.log")
    if target_lines:
        return f"targets.log: expected no events, got {len(target_lines)}"
    return None


def target(wanted):
    lines = events("targets.log")
    got = [event.get("event") for event in lines]
    if got != wanted:
        return f"targets.log: expected events {wanted}, got {got}"
    for event in lines:
        if not event.get("target_ip") or "src_ip" in event or event.get("persona") != persona:
            return f"targets.log: expected target_ip and persona {persona} without src_ip, got {event}"
    return None


CHECKS = {
    "scanner": scanner,
    "target_start": lambda: target(["attack_start"]),
    "target_end": lambda: target(["attack_start", "attack_end"]),
}

error = None


def run_check():
    try:
        return CHECKS[check]()
    except (OSError, json.JSONDecodeError) as exc:
        return str(exc)


while time.monotonic() < deadline:
    error = run_check()
    if error is None:
        if check == "scanner":
            # no second event may follow the first one
            time.sleep(0.5)
            error = run_check()
        break
    time.sleep(0.5)

if error is not None:
    print(error, file=sys.stderr)
    sys.exit(1)
PY
}

check_answers() {
  local output=""

  test_info "Checking one answer per service"
  run_probe_with_retries "CHARGEN" run_chargen_probe 1 1 || test_die "CHARGEN probe failed on ${TEST_BIND_IP}:${MAPPED_CHARGEN_PORT}"
  run_probe_with_retries "DNS" run_dns_probe udp version TXT CH 1 version.bind || test_die "DNS probe failed on ${TEST_BIND_IP}:${MAPPED_DNS_PORT}"
  run_probe_with_retries "NTP" run_ntp_probe 1 3 || test_die "NTP probe failed on ${TEST_BIND_IP}:${MAPPED_NTP_PORT}"
  output="$(run_probe_with_retries "SSDP" run_ssdp_probe 1 3)" || test_die "SSDP probe failed on ${TEST_BIND_IP}:${MAPPED_SSDP_PORT}"
  printf '%s\n' "${output}"
  FIRST_PERSONA="${RUN_PERSONA}"
  FIRST_USN="$(sed -n 's/^usn=//p' <<<"${output}")"
  test_wait_for_container || test_die "DDoSPot container stopped after the probes"
  test_ok "Each service answers"
}

check_scanner_events() {
  test_info "Waiting for the scanner events"
  sleep 3
  check_log_events "${LOG_DIR}" scanner "${RUN_PERSONA}" "${TEST_TIMEOUT}" || test_die "Scanner events are not as expected"
  test_ok "One scanner event per service, targets.log without events"
}

check_response_budget() {
  test_info "Sending 7 CHARGEN probes"
  run_chargen_probe 7 5 || test_die "CHARGEN replies are not within the budget"
  check_log_events "${LOG_DIR}" target_start "${RUN_PERSONA}" "${TEST_TIMEOUT}" || test_die "targets.log has no start event"
  test_ok "Response budget per source"
}

check_stop() {
  local elapsed=""
  local exit_code=""

  test_info "Stopping the container"
  elapsed="$(python3 -c 'import subprocess, sys, time
start = time.monotonic()
subprocess.run(["docker", "stop", sys.argv[1]], check=True, stdout=subprocess.DEVNULL)
print(f"{time.monotonic() - start:.2f}")' "${TEST_CONTAINER_NAME}")" || test_die "docker stop failed"
  python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) < 3 else 1)' "${elapsed}" || test_die "docker stop took ${elapsed} s, expected less than 3 s"
  exit_code="$(docker inspect -f '{{.State.ExitCode}}' "${TEST_CONTAINER_NAME}")"
  [[ "${exit_code}" == "0" ]] || test_die "DDoSPot exited with code ${exit_code} on stop"
  check_log_events "${LOG_DIR}" target_end "${RUN_PERSONA}" "${TEST_TIMEOUT}" || test_die "targets.log has no end event after the stop"
  test_ok "Stop within ${elapsed} s, exit code 0, end event written"
}

check_budget_per_protocol() {
  test_info "Sending 3 NTP mode 3 and 4 NTP mode 6 requests"
  run_ntp_probe 5 3 3 3 6 6 6 6 || test_die "NTP replies are not within the budget"
  test_ok "Response budget per service across NTP modes"
}

check_dns_budget_by_source() {
  test_info "Sending 7 DNS queries for different names"
  run_dns_probe udp any A IN 5 \
    Name1.Example.Org nAmE2.eXaMpLe.OrG NAME3.EXAMPLE.ORG name4.Example.ORG \
    NaMe5.ExAmPlE.oRg nAME6.EXample.org Name7.EXAMPLE.org || test_die "DNS replies are not within the budget"
  test_ok "DNS response budget per source across names"
}

check_refused() {
  test_info "Sending a class IN query with recursion off"
  run_dns_probe udp refused A IN 1 example.org || test_die "DNS did not answer REFUSED"
  run_dns_probe tcp version TXT CH 1 VERSION.bind || test_die "DNS over TCP did not answer on ${TEST_BIND_IP}:${MAPPED_DNS_TCP_PORT}"
  test_ok "REFUSED for class IN with recursion off, version.bind over TCP"
}

check_ssdp_mx() {
  local output=""

  test_info "Sending an M-SEARCH with MX 999999999"
  output="$(run_ssdp_probe 999999999 6)" || test_die "SSDP did not answer within 6 s"
  printf '%s\n' "${output}"
  SECOND_PERSONA="${RUN_PERSONA}"
  SECOND_USN="$(sed -n 's/^usn=//p' <<<"${output}")"
  test_ok "SSDP answers a large MX within 6 s"
}

check_json_only() {
  local file

  while IFS= read -r file; do
    python3 -c 'import json, sys
for number, line in enumerate(open(sys.argv[1]), start=1):
    if not isinstance(json.loads(line), dict):
        sys.exit(f"{sys.argv[1]}:{number}: not a JSON object")' "${file}" || test_die "${file} has a line that is no JSON object"
  done < <(find "${LOG_ROOT}" -type f -print)
  test_ok "Every log line is a JSON object"
}

check_persona_changes() {
  [[ -n "${FIRST_USN}" && -n "${SECOND_USN}" ]] || test_die "SSDP USN of a start is missing"
  test_info "First start: persona ${FIRST_PERSONA}, USN ${FIRST_USN}"
  test_info "Second start: persona ${SECOND_PERSONA}, USN ${SECOND_USN}"
  [[ "${FIRST_USN}" != "${SECOND_USN}" ]] || test_die "SSDP USN is the same on two starts"
  test_ok "Persona details change between starts"
}

check_recursion() {
  local attempt

  # The upstream counts as reachable after its first probe, which runs at
  # the start and then once a minute; a new container probes again at once
  for attempt in 1 2 3; do
    start_run "recursion-${attempt}" on
    test_info "Checking DNS recursion (needs internet access)"
    sleep 5
    if run_dns_probe udp recursive A IN 1 quad9.net; then
      break
    fi
    (( attempt < 3 )) || test_die "DNS recursion did not answer on three starts; is 9.9.9.9:853 reachable from Docker?"
    stop_run
  done
  # one more try when the upstream answer takes longer than the 3 s of the
  # resolver; the answer is cached then, so the UDP query below is quick
  run_dns_probe tcp full TXT IN 1 "${LARGE_TXT_NAME}" \
    || run_dns_probe tcp full TXT IN 1 "${LARGE_TXT_NAME}" \
    || test_die "TXT answer for ${LARGE_TXT_NAME} over TCP is not complete (is its TXT answer still larger than 512 bytes?)"
  run_dns_probe udp truncated TXT IN 1 "${LARGE_TXT_NAME}" || test_die "TXT answer for ${LARGE_TXT_NAME} over UDP is not truncated (is its TXT answer still larger than 512 bytes?)"
  test_ok "DNS recursion: RA over UDP, full large answer over TCP, TC for it over UDP"
}

assert_no_runtime_errors() {
  if grep -R -E "Traceback|panic:|fatal error" "${LOG_ROOT}" "${STDOUT_DIR}" >/dev/null 2>&1; then
    test_die "DDoSPot runtime error found in the logs or the output"
  fi
  test_ok "No DDoSPot runtime errors found in logs"
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

  ensure_udp_port_free_for_docker "${CHARGEN_PORT}" "--chargen-port"
  ensure_udp_port_free_for_docker "${DNS_PORT}" "--dns-port"
  ensure_tcp_port_free_for_docker "${DNS_TCP_PORT}" "--dns-tcp-port"
  ensure_udp_port_free_for_docker "${NTP_PORT}" "--ntp-port"
  ensure_udp_port_free_for_docker "${SSDP_PORT}" "--ssdp-port"

  prepare_ddospot_harness
  test_enable_cleanup

  start_run answers
  check_answers
  check_scanner_events
  stop_run

  start_run budget
  check_response_budget
  check_stop
  stop_run

  start_run ntp
  check_budget_per_protocol
  stop_run

  start_run dns
  check_dns_budget_by_source
  stop_run

  start_run refused
  check_refused
  stop_run

  start_run ssdp
  check_ssdp_mx
  stop_run

  if [[ "${RECURSION}" == "on" ]]; then
    check_recursion
    stop_run
  fi

  check_json_only
  check_persona_changes

  assert_no_runtime_errors
  test_ok "DDoSPot post-build smoke test completed successfully"
}

main "$@"
