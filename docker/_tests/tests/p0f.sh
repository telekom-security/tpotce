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
HTTP_TARGET_CONTAINER_NAME=""
P0F_CONTAINER_IP=""
HTTP_TARGET_IP=""
HTTP_TARGET_PORT="8080"
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

The test starts p0f on a temporary Docker network, generates HTTP traffic from
inside the p0f container, and verifies that p0f writes matching JSON log events
(SYN with the label "${LINUX_OS}", HTTP request). It then scans the p0f container
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
  HTTP_TARGET_CONTAINER_NAME="${TEST_PROJECT_NAME}-http-target"
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
  http-target:
    image: "${IMAGE}"
    container_name: "${HTTP_TARGET_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        while true; do
          nc -l -p ${HTTP_TARGET_PORT} -e /bin/cat
        done
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

  docker exec -i "${TEST_CONTAINER_NAME}" /bin/bash -s -- "http-target" "${HTTP_TARGET_PORT}" "${token}" "${TEST_TIMEOUT}" <<'BASH'
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

  python3 - "${JSON_LOG_FILE}" "${P0F_CONTAINER_IP}" "${HTTP_TARGET_IP}" "${HTTP_TARGET_PORT}" "${token}" "${LINUX_OS}" <<'PY'
import json
import sys
from pathlib import Path

log_file = Path(sys.argv[1])
p0f_ip = sys.argv[2]
target_ip = sys.argv[3]
target_port = int(sys.argv[4])
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
        event.get("client_ip") == p0f_ip
        and event.get("server_ip") == target_ip
        and server_port == target_port
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
        syn_event = (line_number, event)

    if event.get("mod") == "http request" and token in str(event.get("raw_sig", "")):
        require_fields(line_number, event, ("timestamp", "app", "lang", "params", "raw_sig"))
        http_event = (line_number, event)

if not syn_event or not http_event:
    seen = sorted(
        {
            str(event.get("mod"))
            for _, event in events
            if event.get("client_ip") == p0f_ip
            and event.get("server_ip") == target_ip
            and event.get("server_port") == target_port
        }
    )
    print(
        "Expected p0f syn and http request events were not found "
        f"for {p0f_ip} -> {target_ip}:{target_port}; seen mods: {', '.join(seen) or 'none'}",
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
        seen.setdefault(int(event.get("server_port", 0)), set()).add(event.get("app") or event.get("os"))
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

  test_info "Starting isolated p0f container and HTTP target"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "p0f container did not stay running"
  wait_for_named_container "${HTTP_TARGET_CONTAINER_NAME}" || test_die "p0f HTTP target container did not stay running"
  test_ok "Containers are running"

  P0F_CONTAINER_IP="$(get_container_ipv4 "${TEST_CONTAINER_NAME}")"
  HTTP_TARGET_IP="$(get_container_ipv4 "${HTTP_TARGET_CONTAINER_NAME}")"
  test_ok "Container addresses: p0f=${P0F_CONTAINER_IP}, http-target=${HTTP_TARGET_IP}"

  local token="p0f-test-$(date +%s)-$$"
  test_info "Generating HTTP traffic from p0f container with token: ${token}"
  run_probe_until_logged "${token}" || test_die "p0f did not log the generated HTTP probe"
  test_wait_for_container || test_die "p0f container stopped after HTTP probe"
  test_ok "p0f captured the generated SYN (${LINUX_OS}) and HTTP request"

  if [[ "${SKIP_SCANNERS}" != "true" ]]; then
    test_info "Scanning the p0f container with nmap -sS and masscan"
    run_scanners
    find_scanner_events || test_die "p0f did not recognise the scanners"
    test_ok "p0f recognised nmap and masscan"
  fi

  assert_no_runtime_errors
  test_ok "No p0f runtime errors found in logs"

  test_ok "p0f post-build smoke test completed successfully"
}

main "$@"
