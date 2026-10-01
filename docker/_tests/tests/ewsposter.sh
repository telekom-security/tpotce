#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="ewsposter"
DEFAULT_IMAGE="ghcr.io/telekom-security/ewsposter:24.04.2"
IMAGE=""
DATA_DIR=""
CFG_DIR=""
RUN_LOG=""

# Honeypot modules enabled in the test config, all others are switched off.
ENABLED_MODULES="HONEYTRAP ENDLESSH HERALDING IPPHONEY"

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the ewsposter image.

ewsposter runs twice in verbose mode against fixture logs with EWS submission
disabled. Run 1 sees only empty log files, run 2 sees one new line in the
honeytrap and ipphoney logs. Both runs must finish cleanly and run 2 must report
the events from line 1.

The container needs outbound network access, ewsposter looks up the external
IP via api.ipify.org on every start. Nothing is sent to the EWS backend.

Options:
  --image IMAGE      Image to test. Defaults to docker/ewsposter/docker-compose.yml.
  --timeout SEC      Timeout for each ewsposter run. Default: 30.
  --keep-artifacts   Keep temporary compose file, config, fixtures and logs.
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
        # Accepted for run.sh compatibility, the test does not publish ports.
        [[ $# -ge 2 ]] || test_die "--bind-ip requires an argument"
        shift 2
        ;;
      --bind-ip=*)
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

write_test_config() {
  docker run --rm --entrypoint cat "${IMAGE}" /opt/ewsposter/ews.cfg > "${CFG_DIR}/ews.cfg.image" \
    || test_die "Could not read /opt/ewsposter/ews.cfg from ${IMAGE}"

  python3 - "${CFG_DIR}/ews.cfg.image" "${CFG_DIR}/ews.cfg" "${ENABLED_MODULES}" <<'PY'
import configparser
import sys

source, target, enabled = sys.argv[1], sys.argv[2], sys.argv[3].split()

config = configparser.RawConfigParser()
config.optionxform = str
config.read(source)

for section in config.sections():
    toggle = section.lower()
    if config.has_option(section, toggle) and section not in ("EWS", "EWSJSON"):
        config.set(section, toggle, "true" if section in enabled else "false")

missing = [section for section in enabled if not config.has_section(section)]
if missing:
    print(f"Sections missing in image ews.cfg: {' '.join(missing)}", file=sys.stderr)
    sys.exit(1)

config.set("EWS", "ews", "false")
config.set("EWSJSON", "json", "false")

with open(target, "w") as handle:
    config.write(handle)
PY
}

prepare_ewsposter_harness() {
  test_prepare_harness "${TEST_NAME}"

  DATA_DIR="${TEST_TMP_ROOT}/data"
  CFG_DIR="${TEST_TMP_ROOT}/cfg"
  RUN_LOG="${TEST_TMP_ROOT}/log"
  TEST_ARTIFACT_LOG_DIR="${RUN_LOG}"

  mkdir -p "${CFG_DIR}" "${RUN_LOG}" \
    "${DATA_DIR}/honeytrap/log" "${DATA_DIR}/honeytrap/attacks" \
    "${DATA_DIR}/endlessh/log" "${DATA_DIR}/heralding/log" "${DATA_DIR}/ipphoney/log"

  # Empty log files as on a fresh install. Python >= 3.13 linecache returned
  # '\n' for these, which crashed the 'simple' format parsers.
  : > "${DATA_DIR}/honeytrap/log/attacker.log"
  : > "${DATA_DIR}/endlessh/log/endlessh.log"
  : > "${DATA_DIR}/heralding/log/auth.csv"
  : > "${DATA_DIR}/ipphoney/log/ipphoney.json"

  write_test_config

  chmod -R a+rwX "${DATA_DIR}" "${CFG_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  ewsposter:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    user: "2000:2000"
    entrypoint: ["sleep", "3600"]
    environment:
      - EWS_HPFEEDS_ENABLE=false
      - EWS_HPFEEDS_HOST=host
      - EWS_HPFEEDS_PORT=port
      - EWS_HPFEEDS_CHANNELS=channels
      - EWS_HPFEEDS_IDENT=user
      - EWS_HPFEEDS_SECRET=secret
      - EWS_HPFEEDS_TLSCERT=false
      - EWS_HPFEEDS_FORMAT=json
    volumes:
      - "${DATA_DIR}:/data"
      - "${CFG_DIR}:/tpot-test/cfg"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_ewsposter() {
  local run="$1"
  local output="${RUN_LOG}/run${run}.log"
  local rc=0

  docker exec -w /opt/ewsposter "${TEST_CONTAINER_NAME}" \
    timeout "${TEST_TIMEOUT}" python3 -u ews.py -c /tpot-test/cfg/ -v > "${output}" 2>&1 || rc=$?

  if (( rc != 0 )); then
    test_die "ewsposter run ${run} exited with rc=${rc}, see ${output}"
  fi
  if grep -q "Traceback" "${output}"; then
    test_die "ewsposter run ${run} printed a traceback, see ${output}"
  fi
  if ! grep -q "EWSrun finish" "${output}"; then
    test_die "ewsposter run ${run} did not finish, see ${output}"
  fi
}

append_fixture_events() {
  printf '%s\n' '[2026-10-01 12:00:00] tpot tcp 198.51.100.7:40000 -> 192.0.2.10:8080 d41d8cd98f00b204e9800998ecf8427e 12 bytes' \
    >> "${DATA_DIR}/honeytrap/log/attacker.log"
  printf '%s\n' '{"timestamp": "2026-10-01T12:00:01.000000Z", "src_ip": "198.51.100.8", "src_port": 40001, "dst_ip": "192.0.2.10", "dst_port": 631}' \
    >> "${DATA_DIR}/ipphoney/log/ipphoney.json"
}

count_alerts() {
  grep -c -E '^Source IP +: ' "${RUN_LOG}/run$1.log" || true
}

# With ews and hpfeed disabled ewsposter never writes ews.json (finAlert only
# flushes when the XML message holds alerts), so the verbose output is checked.
assert_line1_alerts() {
  local output="${RUN_LOG}/run2.log"
  local module=""
  local ip=""

  for module in HONEYTRAP:198.51.100.7 IPPHONEY:198.51.100.8; do
    ip="${module#*:}"
    module="${module%%:*}"
    python3 - "${output}" "${module}" "${ip}" <<'PY' || return 1
import re
import sys

text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
module, ip = sys.argv[2], sys.argv[3]
block = re.search(rf"-+ {module} -+\n(.*?)(?=\n-+ [A-Z0-9]+ -+\n| => )", text, re.S)
if not block or not re.search(rf"^Source IP +: {re.escape(ip)}$", block.group(1), re.M):
    print(f"No {module} alert for line 1 (source {ip})", file=sys.stderr)
    sys.exit(1)
print(f"{module} alert for line 1 found (source {ip})")
PY
  done
}

main() {
  parse_args "$@"
  test_validate_timeout
  test_check_dependencies

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  prepare_ewsposter_harness
  test_enable_cleanup

  test_info "Starting isolated ewsposter container"
  test_compose up -d --no-build >/dev/null
  test_wait_for_container || test_die "ewsposter container did not stay running"
  test_ok "Container is running"

  test_info "Run 1: empty honeytrap, endlessh, heralding and ipphoney logs"
  run_ewsposter 1
  test_ok "Run 1 finished without errors"

  if [[ "$(count_alerts 1)" != "0" ]]; then
    test_die "Run 1 produced alerts from empty log files"
  fi
  test_ok "No alerts from empty log files"

  test_info "Run 2: one new line in the honeytrap and ipphoney logs"
  append_fixture_events
  run_ewsposter 2
  test_ok "Run 2 finished without errors"

  assert_line1_alerts || test_die "Expected alerts for line 1 were not reported"
  [[ "$(count_alerts 2)" == "2" ]] || test_die "Run 2 reported $(count_alerts 2) alerts, expected 2"
  test_ok "Events from line 1 were not skipped"

  test_ok "ewsposter post-build smoke test completed successfully"
}

main "$@"
