#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="endlessh"
DEFAULT_IMAGE="dtagdevsec/endlessh:24.04.2"
IMAGE=""
SSH_PORT=""
LOG_DIR=""
IDENTITY_DIR=""
FALLBACK_LOG_DIR=""
LOG_FILE=""
MAPPED_SSH_PORT=""
CORPUS_FILE="${DOCKER_ROOT}/endlessh/dist/banner_corpus.txt"
# fast values for the probe, written over the identity of the first start
PROBE_DELAY=400
PROBE_JITTER=200

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Endlessh image.

Options:
  --image IMAGE      Image to test. Defaults to docker/endlessh/docker-compose.yml.
  --ssh-port PORT    Host TCP port for SSH. Default: dynamic loopback port.
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
      --ssh-port)
        [[ $# -ge 2 ]] || test_die "--ssh-port requires an argument"
        SSH_PORT="$2"
        shift 2
        ;;
      --ssh-port=*)
        SSH_PORT="${1#*=}"
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
}

prepare_endlessh_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  IDENTITY_DIR="${TEST_TMP_ROOT}/identity"
  FALLBACK_LOG_DIR="${TEST_TMP_ROOT}/log-noidentity"
  LOG_FILE="${LOG_DIR}/endlessh.log"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${IDENTITY_DIR}" "${FALLBACK_LOG_DIR}"
  chmod 0777 "${LOG_DIR}" "${IDENTITY_DIR}" "${FALLBACK_LOG_DIR}"

  local port_mapping="${TEST_BIND_IP}::2222"
  if [[ -n "${SSH_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${SSH_PORT}:2222"
  fi

  # the image runs with its own endlessh.conf, as on a T-Pot
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  endlessh:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    ports:
      - "${port_mapping}"
    volumes:
      - "${IDENTITY_DIR}:/var/lib/endlessh"
      - "${LOG_DIR}:/var/log/endlessh"
  # as with a compose file of an earlier T-Pot: no folder for the identity
  endlessh-noidentity:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}-noidentity"
    restart: "no"
    read_only: true
    user: "2000:2000"
    volumes:
      - "${FALLBACK_LOG_DIR}:/var/log/endlessh"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

# the values of the first start: in range, banner lines from the corpus
check_identity() {
  python3 - "${IDENTITY_DIR}" "${CORPUS_FILE}" "${TEST_TIMEOUT}" <<'PY'
import re
import sys
import time
from pathlib import Path

identity_dir = Path(sys.argv[1])
corpus = Path(sys.argv[2]).read_text(encoding="ascii").splitlines()
deadline = time.monotonic() + int(sys.argv[3])
identity = identity_dir / "identity.conf"
banner = identity_dir / "banner.txt"

while time.monotonic() < deadline and not (identity.exists() and banner.exists()):
    time.sleep(0.5)
if not (identity.exists() and banner.exists()):
    sys.exit(f"identity.conf and banner.txt were not created in {identity_dir}")

values = dict(re.findall(r"^(\w+) (\d+)$", identity.read_text(encoding="ascii"), re.M))
delay, jitter, length = (int(values.get(key, -1)) for key in ("Delay", "Jitter", "MaxLineLength"))
problems = []
if not 6000 <= delay <= 14000:
    problems.append(f"Delay {delay} outside 6000-14000")
if not delay * 20 // 100 <= jitter <= delay * 50 // 100:
    problems.append(f"Jitter {jitter} outside 20-50 % of Delay {delay}")
if not 24 <= length <= 64:
    problems.append(f"MaxLineLength {length} outside 24-64")
lines = banner.read_text(encoding="ascii").splitlines()
if not 3 <= len(lines) <= 8:
    problems.append(f"banner has {len(lines)} lines, expected 3-8")
problems += [f"banner line not from the corpus: {line!r}" for line in lines if line not in corpus]
if problems:
    sys.exit("; ".join(problems))
print(f"Identity: Delay {delay}, Jitter {jitter}, MaxLineLength {length}, {len(lines)} banner lines")
PY
}

# without a folder for the identity endlessh still starts, with values for this start only
check_identity_fallback() {
  local container="${TEST_CONTAINER_NAME}-noidentity"
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if grep -E -q ' BannerFile /dev/shm/endlessh/banner.txt lines=[0-9]+$' "${FALLBACK_LOG_DIR}/endlessh.log" 2>/dev/null \
      && docker exec "${container}" test -s /dev/shm/endlessh/banner.txt; then
      docker logs "${container}" 2>&1 | grep -q 'is not writable, the values of this install are not kept' \
        || test_die "No warning about the missing identity folder in the Docker logs"
      [[ "$(docker inspect -f '{{.State.Status}}' "${container}")" == "running" ]] \
        || test_die "Endlessh without an identity folder did not stay running"
      return 0
    fi
    sleep 1
  done
  return 1
}

identity_digest() {
  cat "${IDENTITY_DIR}/identity.conf" "${IDENTITY_DIR}/banner.txt" | cksum
}

# stops the container, which must end on SIGTERM well before Docker's SIGKILL
stop_endlessh() {
  local started=${SECONDS}
  local totals
  totals="$(grep -c ' TOTALS connects=' "${LOG_FILE}" || true)"

  test_compose stop -t 10 >/dev/null
  (( SECONDS - started < 5 )) || test_die "Endlessh took $((SECONDS - started)) s to stop, SIGTERM was not handled"
  (( $(grep -c ' TOTALS connects=' "${LOG_FILE}" || true) > totals )) || test_die "No TOTALS line after the stop in endlessh.log"
}

start_endlessh() {
  test_compose start >/dev/null
  test_wait_for_container || test_die "Endlessh container did not stay running"
  MAPPED_SSH_PORT="$(test_get_mapped_port "${TEST_NAME}" "2222")" || test_die "Could not resolve mapped host port for 2222/tcp"
}

# banner.txt first, then random lines of at most MaxLineLength, spread by the jitter
run_banner_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_SSH_PORT}" "${TEST_TIMEOUT}" "${IDENTITY_DIR}" "${PROBE_DELAY}" "${PROBE_JITTER}" <<'PY'
import re
import socket
import statistics
import sys
import time
from pathlib import Path

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])
identity_dir = Path(sys.argv[4])
delay = int(sys.argv[5]) / 1000
jitter = int(sys.argv[6]) / 1000

banner = [line.encode("ascii") for line in (identity_dir / "banner.txt").read_text(encoding="ascii").splitlines()]
max_length = int(re.search(r"^MaxLineLength (\d+)$", (identity_dir / "identity.conf").read_text(), re.M).group(1))
wanted = len(banner) + 4


class ProbeError(Exception):
    pass


def fail(message):
    print(f"Endlessh banner probe failed: {message}", file=sys.stderr)
    sys.exit(1)


deadline = time.monotonic() + timeout
buffer = bytearray()
lines = []
times = []

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(b"SSH-2.0-tpot-endlessh-smoke\r\n")
        started = time.monotonic()

        while time.monotonic() < deadline and len(lines) < wanted:
            sock.settimeout(max(0.1, min(1.0, deadline - time.monotonic())))
            try:
                chunk = sock.recv(256)
            except socket.timeout:
                continue
            if not chunk:
                raise ProbeError(f"Connection closed after {len(lines)} lines")
            buffer.extend(chunk)
            while b"\r\n" in buffer and len(lines) < wanted:
                line, _, remainder = buffer.partition(b"\r\n")
                buffer = bytearray(remainder)
                lines.append(bytes(line))
                times.append(time.monotonic() - started)

    if len(lines) < wanted:
        raise ProbeError(f"Received {len(lines)} lines, expected {wanted}")
    if lines[:len(banner)] != banner:
        raise ProbeError(f"The first lines are not banner.txt: {lines[:len(banner)]!r}")
    for line in lines[len(banner):]:
        if not line or len(line) > max_length - 2:
            raise ProbeError(f"Random line not within MaxLineLength {max_length}: {line!r}")
        if line.startswith(b"SSH-"):
            raise ProbeError(f"Random line looks like an SSH identification string: {line!r}")
        if any(byte < 0x20 or byte > 0x7E for byte in line):
            raise ProbeError(f"Random line contains non-printable bytes: {line!r}")

    gaps = [b - a for a, b in zip([0.0] + times, times)]
    low, high = delay - jitter - 0.05, delay + jitter + 0.25
    if not all(low <= gap <= high for gap in gaps):
        raise ProbeError(f"Gaps outside {low:.2f}-{high:.2f} s: {[round(gap, 3) for gap in gaps]}")
    if statistics.pstdev(gaps) < jitter / 10:
        raise ProbeError(f"Gaps do not spread with Jitter {jitter * 1000:.0f} ms: {[round(gap, 3) for gap in gaps]}")

    print(f"Endlessh sent {len(banner)} banner lines, then random lines, gaps "
          f"{min(gaps):.2f}-{max(gaps):.2f} s")
except (OSError, ProbeError) as exc:
    fail(exc)
PY
}

# many clients at once: each gets its lines at delay±jitter, a late client holds back no other
run_jitter_probe() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_SSH_PORT}" "${TEST_TIMEOUT}" "${PROBE_DELAY}" "${PROBE_JITTER}" <<'PY'
import socket
import statistics
import sys
import threading
import time

host = sys.argv[1]
port = int(sys.argv[2])
timeout = int(sys.argv[3])
delay = int(sys.argv[4]) / 1000
jitter = int(sys.argv[5]) / 1000
clients = 12
lines_each = 12
gaps = {}
errors = []


def collect(index):
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            buffer, times = b"", []
            while len(times) < lines_each:
                chunk = sock.recv(4096)
                if not chunk:
                    raise OSError(f"client {index}: closed after {len(times)} lines")
                buffer += chunk
                while b"\r\n" in buffer and len(times) < lines_each:
                    _, buffer = buffer.split(b"\r\n", 1)
                    times.append(time.monotonic())
        gaps[index] = [b - a for a, b in zip(times, times[1:])]
    except OSError as exc:
        errors.append(str(exc))


threads = [threading.Thread(target=collect, args=(i,)) for i in range(clients)]
for thread in threads:
    thread.start()
    time.sleep(0.05)
for thread in threads:
    thread.join()

if errors:
    sys.exit("Endlessh jitter probe failed: " + "; ".join(errors))
every = [gap for values in gaps.values() for gap in values]
low, high = delay - jitter - 0.05, delay + jitter + 0.25
mean = statistics.mean(every)
below = sum(gap < delay for gap in every) / len(every)
if not all(low <= gap <= high for gap in every):
    sys.exit(f"Endlessh jitter probe failed: gaps outside {low:.2f}-{high:.2f} s "
             f"(min {min(every):.3f}, max {max(every):.3f})")
if not (delay - 0.06 <= mean <= delay + 0.06 and below >= 0.3):
    sys.exit(f"Endlessh jitter probe failed: mean gap {mean:.3f} s, {below:.0%} below Delay "
             f"{delay:.2f} s: clients wait for one another")
print(f"{clients} clients at once: mean gap {mean:.3f} s, {below:.0%} below Delay, "
      f"gaps {min(every):.2f}-{max(every):.2f} s")
PY
}

run_banner_probe_with_retries() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_banner_probe 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

# logrotate truncates the log (copytruncate), endlessh must go on at its start
check_log_after_truncate() {
  docker exec "${TEST_CONTAINER_NAME}" sh -c ': > /var/log/endlessh/endlessh.log'
  python3 - "${TEST_BIND_IP}" "${MAPPED_SSH_PORT}" <<'PY'
import socket
import sys

with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5):
    pass
PY
  test_wait_for_file_text " CLOSE host=" "${LOG_DIR}" || test_die "No CLOSE line after the log was truncated"
  python3 - "${LOG_FILE}" <<'PY'
import sys

data = open(sys.argv[1], "rb").read()
if b"\0" in data:
    sys.exit(f"{sys.argv[1]} has {data.count(0)} NUL bytes after the truncate, it is not opened for appending")
if not data[:1].isdigit():
    sys.exit(f"{sys.argv[1]} does not start with a timestamp: {data[:40]!r}")
PY
}

wait_for_log_events() {
  python3 - "${LOG_FILE}" "${TEST_TIMEOUT}" <<'PY'
import re
import sys
import time
from pathlib import Path

log_file = Path(sys.argv[1])
timeout = int(sys.argv[2])
deadline = time.monotonic() + timeout
last_error = None
found_accept = None
found_close = None

timestamp = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"
ipv4 = r"\d{1,3}(?:\.\d{1,3}){3}"
accept_re = re.compile(
    rf"^{timestamp}\s+ACCEPT\s+host={ipv4}\s+port=(\d+)\s+fd=\d+\s+n=\d+/\d+\s*$"
)
close_re = re.compile(
    rf"^{timestamp}\s+CLOSE\s+host={ipv4}\s+port=(\d+)\s+fd=\d+\s+"
    r"time=([0-9]+(?:\.[0-9]+)?)\s+bytes=([0-9]+(?:\.[0-9]+)?)\s*$"
)

while time.monotonic() < deadline:
    if not log_file.exists():
        last_error = f"{log_file} does not exist yet"
        time.sleep(1)
        continue

    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        last_error = f"Could not read {log_file}: {exc}"
        time.sleep(1)
        continue

    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        accept_match = accept_re.match(stripped)
        if accept_match:
            found_accept = f"{log_file}:{line_number}"
            continue

        close_match = close_re.match(stripped)
        if close_match:
            transferred = float(close_match.group(3))
            if transferred <= 0:
                print(
                    f"CLOSE event in {log_file}:{line_number} has non-positive bytes={transferred}",
                    file=sys.stderr,
                )
                sys.exit(1)
            found_close = f"{log_file}:{line_number} bytes={transferred:g}"
            continue

        if " ACCEPT " in stripped or " CLOSE " in stripped:
            print(f"Unexpected Endlessh log format in {log_file}:{line_number}: {stripped}", file=sys.stderr)
            sys.exit(1)

    if found_accept and found_close:
        print(f"ACCEPT event found in {found_accept}")
        print(f"CLOSE event found in {found_close}")
        sys.exit(0)

    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
if not found_accept:
    print(f"No Endlessh ACCEPT event found in {log_file}", file=sys.stderr)
if not found_close:
    print(f"No Endlessh CLOSE event with positive bytes found in {log_file}", file=sys.stderr)
sys.exit(1)
PY
}

assert_no_runtime_errors() {
  local pattern="Segmentation fault|Permission denied|Read-only file system|No such file|Address already in use|Cannot listen"

  if grep -R -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Endlessh runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "Endlessh runtime error found in Docker logs"
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

  prepare_endlessh_harness
  test_enable_cleanup

  test_info "Starting isolated Endlessh container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Endlessh container did not stay running"
  test_ok "Container is running"

  check_identity || test_die "The values of this install are missing or out of range"
  test_ok "Values of this install were created on the first start"

  check_identity_fallback || test_die "Endlessh without an identity folder did not start with values in /dev/shm"
  test_ok "Without an identity folder Endlessh runs with values for this start only"

  local digest
  digest="$(identity_digest)"
  stop_endlessh
  test_ok "Endlessh stopped on SIGTERM and wrote its TOTALS"
  start_endlessh
  [[ "$(identity_digest)" == "${digest}" ]] || test_die "identity.conf or banner.txt changed on the second start"
  test_ok "The second start keeps the values of this install"

  sed -i.bak -e "s/^Delay .*/Delay ${PROBE_DELAY}/" -e "s/^Jitter .*/Jitter ${PROBE_JITTER}/" "${IDENTITY_DIR}/identity.conf"
  rm -f "${IDENTITY_DIR}/identity.conf.bak"
  stop_endlessh
  start_endlessh
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_SSH_PORT} maps to container port 2222/tcp"

  test_info "Running Endlessh banner probe (Delay ${PROBE_DELAY} ms, Jitter ${PROBE_JITTER} ms)"
  run_banner_probe_with_retries || test_die "Endlessh banner probe failed on ${TEST_BIND_IP}:${MAPPED_SSH_PORT}"
  test_wait_for_container || test_die "Endlessh container stopped after banner probe"
  test_ok "Endlessh banner probe succeeded"

  test_info "Running Endlessh jitter probe with 12 clients at once"
  run_jitter_probe || test_die "Endlessh clients do not get their lines at delay±jitter"
  test_ok "Every client gets its lines at delay±jitter"

  test_info "Waiting for ACCEPT and CLOSE events in endlessh.log"
  wait_for_log_events || test_die "Expected Endlessh ACCEPT and CLOSE events were not found in endlessh.log"
  test_ok "Endlessh ACCEPT and CLOSE events were written to endlessh.log"

  check_log_after_truncate || test_die "endlessh.log is broken after a truncate"
  test_ok "endlessh.log goes on cleanly after a truncate (logrotate copytruncate)"

  assert_no_runtime_errors
  test_ok "No Endlessh runtime errors found in logs"

  test_ok "Endlessh post-build smoke test completed successfully"
}

main "$@"
