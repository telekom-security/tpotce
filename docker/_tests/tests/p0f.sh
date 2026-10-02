#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="p0f"
DEFAULT_IMAGE="dtagdevsec/p0f:24.04.2"
IMAGE=""
LOG_DIR=""
JSON_LOG_FILE=""
HTTP_CLIENT_CONTAINER_NAME=""
P0F_CONTAINER_IP=""
HTTP_CLIENT_IP=""
HTTP_PORT="8080"
SCANNER_CONTAINER_NAME=""
SCANNER_IMAGE="alpine:3.24"
SCANNER_IP=""
NMAP_PORT="8081"
MASSCAN_PORT="8082"
SKIP_SCANNERS="false"
# Label of p0f.fp for the SYN of the Docker host kernel (any Linux 4.19 or newer)
LINUX_OS="Linux 4.19 or newer"

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the p0f image.

The test starts p0f on a temporary Docker network, sends HTTP requests from a
client container to a listener in the p0f container, and verifies that p0f writes
matching JSON log events (SYN with the label "${LINUX_OS}", HTTP request), but no
SYN for connections the p0f host opens itself. It then scans the p0f container
with nmap -sS and masscan from a helper container and verifies that both are
recognised as tools. The scanner part installs nmap and masscan with apk and needs
outbound network access, --skip-scanners leaves it out.

Options:
  --image IMAGE      Image to test. Defaults to docker/p0f/docker-compose.yml.
  --timeout SEC      Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP       Accepted for runner compatibility; p0f exposes no host port.
  --keep-artifacts   Keep temporary compose file and logs for debugging.
  --skip-scanners    Do not run the nmap / masscan part (no network access).
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
      --skip-scanners)
        SKIP_SCANNERS="true"
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

prepare_p0f_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  JSON_LOG_FILE="${LOG_DIR}/p0f.json"
  HTTP_CLIENT_CONTAINER_NAME="${TEST_PROJECT_NAME}-http-client"
  SCANNER_CONTAINER_NAME="${TEST_PROJECT_NAME}-scanner"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}"
  : > "${JSON_LOG_FILE}"
  chmod 0777 "${LOG_DIR}"
  chmod 0666 "${JSON_LOG_FILE}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  p0f:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    volumes:
      - "${LOG_DIR}:/var/log/p0f"
  http-client:
    image: "${IMAGE}"
    container_name: "${HTTP_CLIENT_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    entrypoint: ["/bin/sh", "-c", "while true; do sleep 3600; done"]
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_named_container() {
  local container_name="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local state=""

  while (( SECONDS < deadline )); do
    state="$(docker inspect -f '{{.State.Status}}' "${container_name}" 2>/dev/null || true)"
    case "${state}" in
      running)
        return 0
        ;;
      exited|dead)
        return 1
        ;;
    esac
    sleep 1
  done

  return 1
}

get_container_ipv4() {
  local container_name="$1"
  local ip=""

  ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${container_name}")"
  [[ -n "${ip}" ]] || test_die "Could not determine IPv4 address for ${container_name}"
  printf '%s\n' "${ip}"
}

run_http_probe() {
  local token="$1"

  docker exec -i "${HTTP_CLIENT_CONTAINER_NAME}" /bin/bash -s -- "${P0F_CONTAINER_IP}" "${HTTP_PORT}" "${token}" "${TEST_TIMEOUT}" <<'BASH'
set -Eeuo pipefail

host="$1"
port="$2"
token="$3"
timeout="$4"
deadline=$((SECONDS + timeout))
last_error="probe did not run"

while (( SECONDS < deadline )); do
  if exec 3<>"/dev/tcp/${host}/${port}"; then
    printf 'GET /tpot-p0f-smoke/%s HTTP/1.1\r\nHost: %s\r\nUser-Agent: tpot-p0f-smoke/%s\r\nConnection: close\r\n\r\n' "${token}" "${host}" "${token}" >&3

    if IFS= read -r -t 3 response <&3; then
      exec 3<&-
      exec 3>&-

      if [[ "${response}" == *"${token}"* ]]; then
        printf 'p0f HTTP probe echoed token %s from %s:%s\n' "${token}" "${host}" "${port}"
        exit 0
      fi

      last_error="target response did not contain probe token"
    else
      exec 3<&- || true
      exec 3>&- || true
      last_error="connected but no response line was read"
    fi
  else
    last_error="could not connect"
  fi

  sleep 1
done

printf 'p0f HTTP probe failed for token %s: %s\n' "${token}" "${last_error}" >&2
exit 1
BASH
}

find_p0f_log_events() {
  local token="$1"

  python3 - "${JSON_LOG_FILE}" "${HTTP_CLIENT_IP}" "${P0F_CONTAINER_IP}" "${HTTP_PORT}" "${token}" "${LINUX_OS}" <<'PY'
import json
import sys
from pathlib import Path

log_file = Path(sys.argv[1])
client_ip = sys.argv[2]
p0f_ip = sys.argv[3]
http_port = int(sys.argv[4])
token = sys.argv[5]
linux_os = sys.argv[6]

if not log_file.exists():
    print(f"{log_file} does not exist yet", file=sys.stderr)
    sys.exit(1)

try:
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
except OSError as exc:
    print(f"Could not read {log_file}: {exc}", file=sys.stderr)
    sys.exit(1)

if not lines:
    print(f"{log_file} is empty", file=sys.stderr)
    sys.exit(1)

events = []
invalid = []
for line_number, line in enumerate(lines, 1):
    stripped = line.strip()
    if not stripped:
        continue

    try:
        event = json.loads(stripped)
    except json.JSONDecodeError as exc:
        invalid.append(f"{log_file}:{line_number}: {exc}")
        continue

    if not isinstance(event, dict):
        invalid.append(f"{log_file}:{line_number}: JSON log entry is not an object")
        continue

    events.append((line_number, event))

if invalid:
    print("Invalid p0f JSON log entries found:", file=sys.stderr)
    for item in invalid[:5]:
        print(f"  - {item}", file=sys.stderr)
    sys.exit(1)


def is_probe_flow(event):
    try:
        server_port = int(event.get("server_port"))
        client_port = int(event.get("client_port"))
    except (TypeError, ValueError):
        return False

    return (
        event.get("client_ip") == client_ip
        and event.get("server_ip") == p0f_ip
        and server_port == http_port
        and client_port > 0
        and event.get("subject") == "cli"
    )


def require_fields(line_number, event, fields):
    missing = [field for field in fields if event.get(field) in (None, "")]
    if missing:
        print(
            f"Matching p0f event in {log_file}:{line_number} is missing fields: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)


syn_event = None
http_event = None

for line_number, event in events:
    if not is_probe_flow(event):
        continue

    if event.get("mod") == "syn":
        require_fields(line_number, event, ("timestamp", "os", "dist", "params", "raw_sig"))
        if event.get("os") != linux_os:
            print(f"p0f SYN event in {log_file}:{line_number} has os={event.get('os')!r}, expected {linux_os!r} "
                  f"(raw_sig {event.get('raw_sig')})", file=sys.stderr)
            sys.exit(1)
        # conf field of the label in p0f.fp: family, share and sample count
        conf, samples = event.get("os_confidence"), event.get("os_samples")
        if (event.get("os_family") != "Linux" or not isinstance(conf, float) or not 0.9 <= conf <= 1
                or not isinstance(samples, int) or samples <= 0):
            print(f"p0f SYN event in {log_file}:{line_number} has os_family={event.get('os_family')!r} "
                  f"os_confidence={conf!r} os_samples={samples!r}, expected Linux, >= 0.9, > 0", file=sys.stderr)
            sys.exit(1)
        syn_event = (line_number, event)

    if event.get("mod") == "http request" and token in str(event.get("raw_sig", "")):
        require_fields(line_number, event, ("timestamp", "app", "lang", "params", "raw_sig"))
        http_event = (line_number, event)

if not syn_event or not http_event:
    seen = sorted(
        {
            str(event.get("mod"))
            for _, event in events
            if event.get("client_ip") == client_ip
            and event.get("server_ip") == p0f_ip
            and event.get("server_port") == http_port
        }
    )
    print(
        "Expected p0f syn and http request events were not found "
        f"for {client_ip} -> {p0f_ip}:{http_port}; seen mods: {', '.join(seen) or 'none'}",
        file=sys.stderr,
    )
    sys.exit(1)

syn_line, syn = syn_event
http_line, http = http_event
print(
    f"p0f SYN event found in {log_file}:{syn_line} "
    f"{syn['client_ip']}:{syn['client_port']} -> {syn['server_ip']}:{syn['server_port']} os={syn['os']!r}"
)
print(
    f"p0f HTTP request event found in {log_file}:{http_line} "
    f"raw_sig contains token {token!r}"
)
PY
}

run_scanners() {
  docker run -d --name "${SCANNER_CONTAINER_NAME}" --network "${TEST_PROJECT_NAME}_net" \
    --cap-add NET_RAW --cap-add NET_ADMIN "${SCANNER_IMAGE}" sleep 300 >/dev/null
  SCANNER_IP="$(get_container_ipv4 "${SCANNER_CONTAINER_NAME}")"
  docker exec "${SCANNER_CONTAINER_NAME}" apk add --no-cache -q nmap masscan >/dev/null \
    || test_die "Could not install nmap and masscan in ${SCANNER_IMAGE} (network access needed, or use --skip-scanners)"
  # The scanners probe the p0f container itself: a Docker bridge does not forward
  # unicast between two other containers to it.
  docker exec "${SCANNER_CONTAINER_NAME}" nmap -Pn -sS -p "${NMAP_PORT}" "${P0F_CONTAINER_IP}" >/dev/null 2>&1 || true
  # masscan 1.3.2 often does not exit after the scan, so it gets a fixed run time
  docker exec -d "${SCANNER_CONTAINER_NAME}" masscan "${P0F_CONTAINER_IP}" -p"${MASSCAN_PORT}" --rate 10 --wait 1
  sleep 4
}

find_scanner_events() {
  python3 - "${JSON_LOG_FILE}" "${SCANNER_IP}" "${P0F_CONTAINER_IP}" "${NMAP_PORT}" "${MASSCAN_PORT}" <<'PY'
import json
import sys

log_file, scanner_ip, p0f_ip = sys.argv[1:4]
expected = {int(sys.argv[4]): "NMap SYN scan", int(sys.argv[5]): "masscan SYN scan"}
seen = {}
for line in open(log_file, encoding="utf-8", errors="replace"):
    if not line.strip():
        continue
    event = json.loads(line)
    if event.get("mod") == "syn" and event.get("client_ip") == scanner_ip and event.get("server_ip") == p0f_ip:
        # tools get os_family Scanner, but no confidence (the datasets hold no scanners)
        tag = "" if event.get("os_family") == "Scanner" and "os_confidence" not in event else \
            f" (os_family={event.get('os_family')!r}, os_confidence={event.get('os_confidence')!r})"
        seen.setdefault(int(event.get("server_port", 0)), set()).add((event.get("app") or event.get("os")) + tag)
failed = False
for port, app in expected.items():
    if app in seen.get(port, set()):
        print(f"p0f recognised {app!r} on port {port}")
    else:
        print(f"Expected {app!r} for port {port}, p0f logged {sorted(seen.get(port, {'nothing'}))}", file=sys.stderr)
        failed = True
sys.exit(1 if failed else 0)
PY
}

# p0f has to refuse a p0f.fp with a malformed conf field (and still load the shipped one)
check_conf_parser() {
  local fp_dir="${TEST_TMP_ROOT}/fp"
  local case_name="" line="" expect="" rc=0 out=""

  mkdir -p "${fp_dir}"
  docker run --rm --entrypoint /bin/cat "${IMAGE}" /opt/p0f/p0f.fp > "${fp_dir}/p0f.fp"
  python3 -c 'import struct,sys; open(sys.argv[1],"wb").write(struct.pack("<IHHiIII",0xA1B2C3D4,2,4,0,0,65535,1))' "${fp_dir}/empty.pcap"
  chmod -R a+rX "${fp_dir}"

  run_p0f_on() {
    docker run --rm --entrypoint /opt/p0f/p0f -v "${fp_dir}:/fp:ro" "${IMAGE}" -f "/fp/$1" -r /fp/empty.pcap 2>&1
  }

  out="$(run_p0f_on p0f.fp)" || test_die "p0f does not load its own p0f.fp: ${out}"

  for case_name in share family placement userland duplicate; do
    case "${case_name}" in
      share) line="conf = Linux:high:100"; expect="Malformed 'conf' share" ;;
      family) line="conf = BeOS:0.9:100"; expect="Unknown OS family 'BeOS'" ;;
      placement) line="conf = Linux:0.9:100"; expect="Misplaced 'conf'" ;;
      userland) line="conf = Linux:0.9:100"; expect="Misplaced 'conf'" ;;
      duplicate) line="conf = Linux:0.9:100"; expect="Misplaced 'conf'" ;;
    esac
    python3 - "${fp_dir}/p0f.fp" "${fp_dir}/bad-${case_name}.fp" "${case_name}" "${line}" <<'PY'
import re, sys
src, dst, case, line = sys.argv[1:5]
text = open(src).read()
req = text.index("[tcp:request]")
if case == "userland":
    m = re.compile(r"^sys\s*=.*$", re.M).search(text, req)          # after a userland label
elif case == "placement":
    lab = re.compile(r"^label\s*=\s*\S:unix:Linux:.*$", re.M).search(text, req)
    m = re.compile(r"^sig\s*=.*$", re.M).search(text, lab.end())    # after a sig of an OS label
elif case == "duplicate":
    m = re.compile(r"^conf\s*=.*$", re.M).search(text, req)         # second conf for one label
else:
    m = re.compile(r"^label\s*=\s*\S:unix:Linux:.*$", re.M).search(text, req)
    m = re.compile(r"^(conf\s*=.*\n)?", re.M).match(text, m.end() + 1)
    open(dst, "w").write(text[:m.start()] + line + "\n" + text[m.end():])
    sys.exit(0)
open(dst, "w").write(text[:m.end()] + "\n" + line + text[m.end():])
PY
    rc=0
    out="$(run_p0f_on "bad-${case_name}.fp")" || rc=$?
    if [[ "${rc}" -eq 0 ]] || ! grep -qF "${expect}" <<<"${out}"; then
      test_die "p0f accepted a p0f.fp with a malformed conf field (${case_name}): rc=${rc} ${out}"
    fi
  done
}

# Offline mode: arguments go to p0f, which reads a pcap and writes NDJSON to stdout
check_offline_mode() {
  local pcap_dir="${TEST_TMP_ROOT}/pcap"
  local out="" err=""

  mkdir -p "${pcap_dir}"
  # syn.pcap: one Linux 4.19+ SYN from 198.51.100.7, packet time 2023-11-14 22:13:20 UTC
  # vlan.pcap: the same SYN from .8 untagged, .9 with an 802.1Q tag, .10 with QinQ tags
  python3 - "${pcap_dir}" <<'PY'
import struct, sys
def frame(src, tags=b""):
    opts = struct.pack("!BBH", 2, 4, 1460) + b"\x04\x02" + struct.pack("!BBII", 8, 10, 12345, 0) + b"\x01" + struct.pack("!BBB", 3, 3, 7)
    tcp = struct.pack("!HHIIBBHHH", 40001, 22, 1, 0, (20 + len(opts)) // 4 << 4, 0x02, 64240, 0, 0) + opts
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 1, 0x4000, 56, 6, 0, bytes([198, 51, 100, src]), bytes([192, 0, 2, 1]))
    s = sum(struct.unpack("!10H", ip)); s = (s >> 16) + (s & 0xFFFF); ip = ip[:10] + struct.pack("!H", ~(s + (s >> 16)) & 0xFFFF) + ip[12:]
    return b"\x02\x00\x00\x00\x00\x01\x02\x00\x00\x00\x00\x02" + tags + b"\x08\x00" + ip + tcp
def pcap(name, frames):
    with open(f"{sys.argv[1]}/{name}", "wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for n, f in enumerate(frames):
            fh.write(struct.pack("<IIII", 1700000000 + n, 0, len(f), len(f)) + f)
pcap("syn.pcap", [frame(7)])
pcap("vlan.pcap", [frame(8), frame(9, b"\x81\x00\x00\x65"), frame(10, b"\x88\xa8\x00\x64\x81\x00\x00\x65")])
PY
  chmod -R a+rX "${pcap_dir}"

  out="$(docker run --rm --network none -v "${pcap_dir}:/pcap:ro" "${IMAGE}" -r /pcap/syn.pcap 2>/dev/null)" \
    || test_die "p0f offline mode failed: docker run ${IMAGE} -r <pcap>"
  python3 - "${out}" "${LINUX_OS}" <<'PY' || test_die "p0f offline mode did not write the expected NDJSON to stdout"
import json, sys
out, linux_os = sys.argv[1], sys.argv[2]
events = []
for line in out.splitlines():
    try:
        events.append(json.loads(line))
    except json.JSONDecodeError:
        sys.exit(f"stdout holds a line that is not JSON: {line!r}")
syn = [e for e in events if e.get("mod") == "syn" and e.get("client_ip") == "198.51.100.7"]
if len(syn) != 1:
    sys.exit(f"expected one syn event from 198.51.100.7, got {events}")
e = syn[0]
if e.get("timestamp") != "2023/11/14 22:13:20" or e.get("os") != linux_os or e.get("os_family") != "Linux":
    sys.exit(f"unexpected syn event: {e}")
print(f"offline: {e['timestamp']} {e['client_ip']} os={e['os']!r} os_family={e['os_family']}")
PY

  # VLAN tags (802.1Q, QinQ) are skipped per packet, untagged frames in between still work
  out="$(docker run --rm --network none -v "${pcap_dir}:/pcap:ro" "${IMAGE}" -r /pcap/vlan.pcap 2>/dev/null)" \
    || test_die "p0f offline mode failed on a VLAN pcap"
  python3 - "${out}" "${LINUX_OS}" <<'PY' || test_die "p0f did not fingerprint VLAN tagged SYNs"
import json, sys
seen = {e["client_ip"]: e.get("os") for e in map(json.loads, sys.argv[1].splitlines()) if e.get("mod") == "syn"}
want = {"198.51.100.8": "untagged", "198.51.100.9": "802.1Q", "198.51.100.10": "QinQ"}
missing = [f"{ip} ({kind})" for ip, kind in want.items() if seen.get(ip) != sys.argv[2]]
if missing:
    sys.exit(f"no {sys.argv[2]!r} syn event for {', '.join(missing)}; got {seen}")
print("offline: untagged, 802.1Q and QinQ SYNs fingerprinted")
PY
  out="$(docker run --rm --network none -v "${pcap_dir}:/pcap:ro" "${IMAGE}" -r /pcap/vlan.pcap 'src host 198.51.100.10' 2>/dev/null)" \
    || test_die "p0f offline mode with a BPF filter failed on a VLAN pcap"
  grep -q '"client_ip": "198.51.100.10"' <<<"${out}" && ! grep -q '"client_ip": "198.51.100.9"' <<<"${out}" \
    || test_die "p0f BPF filter does not apply to QinQ tagged SYNs: ${out}"

  # a BPF filter after the options is passed on to p0f
  out="$(docker run --rm --network none -v "${pcap_dir}:/pcap:ro" "${IMAGE}" -r /pcap/syn.pcap 'not src host 198.51.100.7' 2>/dev/null)" \
    || test_die "p0f offline mode with a BPF filter failed"
  [[ -z "${out}" ]] || test_die "p0f offline mode ignored the BPF filter: ${out}"
}

# connections the p0f host opens itself must not show up as client SYNs
check_self_filter() {
  docker exec "${TEST_CONTAINER_NAME}" /bin/sh -c \
    "for i in 1 2 3; do nc -w 1 ${HTTP_CLIENT_IP} 9 </dev/null >/dev/null 2>&1; done" || true
  sleep 2
  python3 - "${JSON_LOG_FILE}" "${P0F_CONTAINER_IP}" <<'PY'
import json, sys
own = [l for l in open(sys.argv[1], encoding="utf-8", errors="replace")
       if l.strip() and json.loads(l).get("mod") == "syn" and json.loads(l).get("client_ip") == sys.argv[2]]
if own:
    sys.exit(f"p0f logged {len(own)} SYN(s) the p0f host sent itself, e.g. {own[0].strip()}")
print("no SYN of the p0f host itself in the log")
PY
}

cleanup_scanner() {
  docker rm -f "${SCANNER_CONTAINER_NAME}" >/dev/null 2>&1 || true
}

run_probe_until_logged() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local probe_output=""
  local log_output=""

  while (( SECONDS < deadline )); do
    probe_output="$(run_http_probe "${token}" 2>&1)" || true
    if log_output="$(find_p0f_log_events "${token}" 2>&1)"; then
      printf '%s\n' "${probe_output}"
      printf '%s\n' "${log_output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${probe_output}" >&2
  printf '%s\n' "${log_output}" >&2
  return 1
}

assert_no_runtime_errors() {
  local pattern="Permission denied|Operation not permitted|pcap_open_live|libpcap is out of ideas|Cannot open|chroot\\(.*failed|setgid\\(.*failed|setuid\\(.*failed|Segmentation fault|FATAL|PFATAL"

  if grep -R -I -E "${pattern}" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "p0f runtime error found in log artifacts"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "${pattern}" >/dev/null 2>&1; then
    test_die "p0f runtime error found in Docker logs"
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

  prepare_p0f_harness
  test_enable_cleanup
  trap 'cleanup_scanner; test_cleanup' EXIT

  test_info "Starting isolated p0f container and HTTP client"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "p0f container did not stay running"
  wait_for_named_container "${HTTP_CLIENT_CONTAINER_NAME}" || test_die "p0f HTTP client container did not stay running"
  test_ok "Containers are running"

  P0F_CONTAINER_IP="$(get_container_ipv4 "${TEST_CONTAINER_NAME}")"
  HTTP_CLIENT_IP="$(get_container_ipv4 "${HTTP_CLIENT_CONTAINER_NAME}")"
  test_ok "Container addresses: p0f=${P0F_CONTAINER_IP}, http-client=${HTTP_CLIENT_IP}"

  # the HTTP listener runs in the p0f container: p0f sees the client's SYN and
  # needs its own SYN+ACK to follow the connection to the HTTP request
  docker exec -d "${TEST_CONTAINER_NAME}" /bin/sh -c "nc -lk -p ${HTTP_PORT} -e /bin/cat"

  local token="p0f-test-$(date +%s)-$$"
  test_info "Generating HTTP traffic from the client container with token: ${token}"
  run_probe_until_logged "${token}" || test_die "p0f did not log the generated HTTP probe"
  test_wait_for_container || test_die "p0f container stopped after HTTP probe"
  test_ok "p0f captured the generated SYN (${LINUX_OS}) and HTTP request"

  check_self_filter || test_die "p0f logged SYNs of its own host"
  test_ok "p0f skips SYNs the p0f host sends itself"

  if [[ "${SKIP_SCANNERS}" != "true" ]]; then
    test_info "Scanning the p0f container with nmap -sS and masscan"
    run_scanners
    find_scanner_events || test_die "p0f did not recognise the scanners"
    test_ok "p0f recognised nmap and masscan"
  fi

  assert_no_runtime_errors
  test_ok "No p0f runtime errors found in logs"

  check_conf_parser
  test_ok "p0f rejects malformed conf fields in p0f.fp"

  check_offline_mode
  test_ok "p0f offline mode writes NDJSON to stdout (pcap time stamps, VLAN tags, BPF filter)"

  test_ok "p0f post-build smoke test completed successfully"
}

main "$@"
