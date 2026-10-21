#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="ewsposter"
DEFAULT_IMAGE="ghcr.io/telekom-security/ewsposter:24.04.2"
IMAGE=""
DATA_DIR=""
CFG_DIR=""
SINK_DIR=""
RUN_LOG=""
CHECK=""
SINK_URL="http://sink:8080/ews-0.1/alert/postSimpleMessage"
# The longest pause of the loop between two passes (s); --timeout is added for the pass itself
PASS_PAUSE=60

# Honeypot modules enabled in the test config, all others are switched off.
ENABLED_MODULES="HONEYTRAP ENDLESSH HERALDING IPPHONEY COWRIE"

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the ewsposter image.

The container runs its real entrypoint, the loop of ews.py passes, against
fixture logs. EWS submission goes to a local sink container (Python http.server)
that keeps every POST, nothing is sent to the EWS backend.

  1. The first pass sees no log files yet and sends nothing. Then one new
     line in the honeytrap and ipphoney logs and three Cowrie sessions arrive at
     the sink, each Cowrie session labelled SSH or Telnet.
  2. A Cowrie session written right before the hold marker data/ews/hold is
     sent by the normal pass the hold starts with, before ewsposter writes
     data/ews/hold.ack. After the ack its indexes move on over three new Cowrie
     sessions and a honeytrap line, and no POST arrives.
  3. ewsposter is restarted during the hold, two Cowrie sessions are written
     while it is down: it goes on with idle passes, no POST arrives.
  4. Without the marker hold.ack goes away, exactly one new Cowrie session
     arrives, the lines of the hold never do.
  5. A marker older than 30 minutes is ignored.
  6. docker stop ends the container at once.

The container needs outbound network access, ewsposter looks up the external
IP via api.ipify.org on every pass.

Options:
  --image IMAGE      Image to test. Defaults to docker/ewsposter/docker-compose.yml.
  --timeout SEC      Time for one ews.py pass, added to the longest pause of the
                     loop (${PASS_PAUSE} s) when the test waits for a pass. Default: 30.
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

  python3 - "${CFG_DIR}/ews.cfg.image" "${CFG_DIR}/ews.cfg" "${ENABLED_MODULES}" "${SINK_URL}" <<'PY'
import configparser
import sys

source, target, enabled, sink = sys.argv[1], sys.argv[2], sys.argv[3].split(), sys.argv[4]

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

# EWS submission on, to the sink of the test
config.set("EWS", "ews", "true")
config.set("EWS", "rhost_first", sink)
config.set("EWS", "rhost_second", sink)
config.set("EWSJSON", "json", "false")

with open(target, "w") as handle:
    config.write(handle)
PY

  printf '[EWSIP]\nip_int = 192.0.2.10\nip_ext = 192.0.2.10\n' > "${CFG_DIR}/ews.ip"
}

write_sink() {
  # Keeps the body of every POST as posts/NNNN.xml and answers as the EWS backend does
  cat > "${SINK_DIR}/sink.py" <<'PY'
import http.server
import os
import sys
import threading

posts = sys.argv[1]
lock = threading.Lock()
count = [0]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with lock:
            count[0] += 1
            name = os.path.join(posts, f"{count[0]:04d}.xml")
            with open(name + ".part", "wb") as handle:
                handle.write(body)
            os.replace(name + ".part", name)
        reply = b"<Result><StatusCode>OK</StatusCode></Result>"
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, fmt, *args):
        print("sink: " + fmt % args, flush=True)


http.server.ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
PY
  mkdir -p "${SINK_DIR}/posts"
}

write_check() {
  # Reads the alerts of all POSTs the sink kept
  cat > "${CHECK}" <<'PY'
import glob
import os
import sys
import xml.etree.ElementTree as ET


def alerts(posts):
    found = []
    for name in sorted(glob.glob(os.path.join(posts, "*.xml"))):
        root = ET.parse(name).getroot()
        for alert in root.iter("Alert"):
            extra = {item.get("meaning"): item.text for item in alert.iter("AdditionalData")}
            request = {item.get("type"): item.text for item in alert.iter("Request")}
            found.append({
                "analyzer": alert.find("Analyzer").get("id"),
                "source": alert.find("Source").text,
                "description": request.get("description"),
                "sessionid": extra.get("sessionid"),
            })
    return found


command, posts, args = sys.argv[1], sys.argv[2], sys.argv[3:]
found = alerts(posts)
if command == "count":
    print(len(found))
elif command == "posts":
    print(len(glob.glob(os.path.join(posts, "*.xml"))))
elif command == "has":
    sys.exit(0 if any(alert["source"] == args[0] for alert in found) else 1)
elif command == "none-of":
    sent = sorted({alert["source"] for alert in found} & set(args))
    if sent:
        print(f"Sent although held: {' '.join(sent)}", file=sys.stderr)
        sys.exit(1)
elif command == "first":
    expected = {
        ("honeytrap-community-01", "198.51.100.7", None),
        ("ipphoney-community-01", "198.51.100.8", None),
        ("cowrie-community-01", "198.51.100.9", "c0ffee000022"),
        ("cowrie-community-01", "198.51.100.10", "c0ffee000023"),
        ("cowrie-community-01", "198.51.100.11", "c0ffee002323"),
    }
    seen = set()
    for alert in found:
        # The SSH or Telnet label of a session is whatever the ewsposter commit
        # in the image decides; it must be one of the two.
        if alert["sessionid"] and alert["description"] not in ("SSH Honeypot Cowrie", "Telnet Honeypot Cowrie"):
            print(f"Unexpected Cowrie label {alert['description']!r}", file=sys.stderr)
            sys.exit(1)
        label = alert["sessionid"] if alert["sessionid"] else None
        seen.add((alert["analyzer"], alert["source"], label))
    if seen != expected or len(found) != len(expected):
        print(f"Alerts {sorted(seen, key=str)} ({len(found)}), expected {sorted(expected, key=str)}", file=sys.stderr)
        sys.exit(1)
    print("Line 1 of honeytrap and ipphoney, three Cowrie sessions (22 / ssh, 23 / telnet, 2323 without protocol)")
PY
}

prepare_ewsposter_harness() {
  test_prepare_harness "${TEST_NAME}"

  DATA_DIR="${TEST_TMP_ROOT}/data"
  CFG_DIR="${TEST_TMP_ROOT}/cfg"
  SINK_DIR="${TEST_TMP_ROOT}/sink"
  RUN_LOG="${TEST_TMP_ROOT}/log"
  CHECK="${TEST_TMP_ROOT}/check.py"
  TEST_ARTIFACT_LOG_DIR="${RUN_LOG}"

  mkdir -p "${CFG_DIR}" "${RUN_LOG}" "${SINK_DIR}" "${DATA_DIR}/ews" \
    "${DATA_DIR}/honeytrap/log" "${DATA_DIR}/honeytrap/attacks" \
    "${DATA_DIR}/endlessh/log" "${DATA_DIR}/heralding/log" "${DATA_DIR}/ipphoney/log" \
    "${DATA_DIR}/cowrie/log"

  # No log files before their first line. Upstream ewsposter on Python >= 3.13
  # takes an empty log file for one line (linecache returns '\n' for line 1):
  # the line-based parsers stop the pass, a JSON log loses its first line.
  
  write_test_config
  write_sink
  write_check

  chmod -R a+rwX "${DATA_DIR}" "${CFG_DIR}" "${SINK_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  sink:
    image: "${IMAGE}"
    container_name: "${TEST_PROJECT_NAME}-sink"
    restart: "no"
    user: "2000:2000"
    entrypoint: ["python3", "-u", "/sink/sink.py", "/sink/posts"]
    volumes:
      - "${SINK_DIR}:/sink"
  ewsposter:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    user: "2000:2000"
    depends_on:
      - sink
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
      - "${CFG_DIR}/ews.cfg:/opt/ewsposter/ews.cfg:ro"
      - "${CFG_DIR}/ews.ip:/opt/ewsposter/ews.ip:ro"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

# The output of the loop so far
save_logs() {
  docker logs "${TEST_CONTAINER_NAME}" > "${RUN_LOG}/ewsposter.log" 2>&1 || true
  docker logs "${TEST_PROJECT_NAME}-sink" > "${RUN_LOG}/sink.log" 2>&1 || true
}

# wait_until SECONDS COMMAND...: true once COMMAND is
wait_until() {
  local deadline=$((SECONDS + $1))
  shift
  while (( SECONDS < deadline )); do
    if "$@" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  "$@" >/dev/null 2>&1
}

pass_wait() {
  printf '%s\n' "$((PASS_PAUSE + TEST_TIMEOUT))"
}

finished_passes() {
  save_logs
  grep -c "EWSrun finish" "${RUN_LOG}/ewsposter.log" || true
}

passes_at_least() {
  (( $(finished_passes) >= $1 ))
}

alerts_at_least() {
  (( $(python3 "${CHECK}" count "${SINK_DIR}/posts") >= $1 ))
}

alert_from() {
  python3 "${CHECK}" has "${SINK_DIR}/posts" "$1"
}

idx_value() {
  # the index of section $1 in ews.idx of the container
  docker exec "${TEST_CONTAINER_NAME}" cat /opt/ewsposter/ews.idx 2>/dev/null \
    | awk -v section="[$1]" '$0 == section { found = 1; next } /^\[/ { found = 0 } found && $1 == "index" { print $3 }'
}

idx_is() {
  [[ "$(idx_value "$1")" == "$2" ]]
}

sessions_known() {
  local sessions=""
  local session=""
  sessions="$(docker exec "${TEST_CONTAINER_NAME}" cat /opt/ewsposter/cowrie.session 2>/dev/null)" || return 1
  for session in "$@"; do
    grep -qx -- "${session}" <<< "${sessions}" || return 1
  done
}

ack_present() {
  [[ -f "${DATA_DIR}/ews/hold.ack" && ! -L "${DATA_DIR}/ews/hold.ack" ]]
}

ack_absent() {
  [[ ! -e "${DATA_DIR}/ews/hold.ack" && ! -L "${DATA_DIR}/ews/hold.ack" ]]
}

state_is() {
  [[ -f "${DATA_DIR}/ews/hold.state" && "$(cut -d " " -f 1 "${DATA_DIR}/ews/hold.state")" == "$1" ]]
}

ack_mtime() {
  python3 -c 'import os, sys; print(os.stat(sys.argv[1]).st_mtime_ns)' "${DATA_DIR}/ews/hold.ack"
}

ack_newer_than() {
  ack_present && (( $(ack_mtime) > $1 ))
}

cowrie_session() {
  # cowrie_session SESSION SOURCE PORT [PROTOCOL]
  local protocol=""
  [[ -n "${4:-}" ]] && protocol=", \"protocol\": \"$4\""
  printf '{"eventid": "cowrie.session.connect", "session": "%s", "timestamp": "2026-10-01T12:00:02.000000Z", "src_ip": "%s", "src_port": 40002, "dst_ip": "192.0.2.10", "dst_port": %s%s}\n' \
    "$1" "$2" "$3" "${protocol}" >> "${DATA_DIR}/cowrie/log/cowrie.json"
}

honeytrap_line() {
  printf '[2026-10-01 12:00:00] tpot tcp %s:40000 -> 192.0.2.10:8080 d41d8cd98f00b204e9800998ecf8427e 12 bytes\n' "$1" \
    >> "${DATA_DIR}/honeytrap/log/attacker.log"
}

append_first_events() {
  honeytrap_line 198.51.100.7
  printf '%s\n' '{"timestamp": "2026-10-01T12:00:01.000000Z", "src_ip": "198.51.100.8", "src_port": 40001, "dst_ip": "192.0.2.10", "dst_port": 631}' \
    >> "${DATA_DIR}/ipphoney/log/ipphoney.json"
  # Cowrie writes dst_port as an integer and names the protocol in
  # cowrie.session.connect; the last session has no protocol field.
  cowrie_session c0ffee000022 198.51.100.9 22 ssh
  cowrie_session c0ffee000023 198.51.100.10 23 telnet
  cowrie_session c0ffee002323 198.51.100.11 2323
}

step_first_pass() {
  test_info "First pass: no honeytrap, endlessh, heralding, ipphoney and cowrie logs yet"
  wait_until "$(pass_wait)" passes_at_least 1 || test_die "ewsposter finished no pass"
  [[ "$(python3 "${CHECK}" posts "${SINK_DIR}/posts")" == "0" ]] || test_die "The pass without log files sent alerts"
  test_ok "The first pass sent nothing without log files"

  test_info "One new line in the honeytrap and ipphoney logs, three Cowrie sessions"
  append_first_events
  wait_until "$(pass_wait)" alerts_at_least 5 || test_die "The new lines did not arrive at the sink"
  python3 "${CHECK}" first "${SINK_DIR}/posts" || test_die "The sink did not get the expected alerts"
  test_ok "Events from line 1 arrived, Cowrie sessions are labelled SSH or Telnet"
}

step_hold() {
  local posts=""
  local cowrie_idx=""
  local honeytrap_idx=""
  local ack=""

  test_info "Hold: a Cowrie session, right after it the marker; ewsposter must answer with hold.ack"
  cowrie_session c0ffee00a000 198.51.100.20 22 ssh
  : > "${DATA_DIR}/ews/hold"
  wait_until "$(pass_wait)" ack_present || test_die "hold.ack did not appear"
  test_ok "hold.ack appeared"
  # the hold starts with a normal pass, before its ack
  alert_from 198.51.100.20 || test_die "The line from right before the marker was not sent before hold.ack"
  test_ok "The line from right before the marker was sent"
  state_is held || test_die "hold.state does not say held"

  posts="$(python3 "${CHECK}" posts "${SINK_DIR}/posts")"
  cowrie_idx="$(idx_value COWRIE)"
  honeytrap_idx="$(idx_value HONEYTRAP)"
  [[ -n "${cowrie_idx}" && -n "${honeytrap_idx}" ]] || test_die "No COWRIE / HONEYTRAP index in ews.idx"

  test_info "Three Cowrie sessions and a honeytrap line during the hold"
  cowrie_session c0ffee00a001 198.51.100.21 22 ssh
  cowrie_session c0ffee00a002 198.51.100.22 22 ssh
  cowrie_session c0ffee00a003 198.51.100.23 23 telnet
  honeytrap_line 198.51.100.24
  wait_until 30 sessions_known c0ffee00a001 c0ffee00a002 c0ffee00a003 \
    || test_die "The idle passes did not take the Cowrie sessions of the hold"
  wait_until 30 idx_is COWRIE "$((cowrie_idx + 3))" || test_die "The COWRIE index did not move on by 3"
  wait_until 30 idx_is HONEYTRAP "$((honeytrap_idx + 1))" || test_die "The HONEYTRAP index did not move on by 1"
  test_ok "ews.idx and cowrie.session moved on during the hold"

  # two more idle rounds, then still no POST
  ack="$(ack_mtime)"
  wait_until 30 ack_newer_than "${ack}" || test_die "hold.ack was not written again"
  ack="$(ack_mtime)"
  wait_until 30 ack_newer_than "${ack}" || test_die "hold.ack was not written again"
  [[ "$(python3 "${CHECK}" posts "${SINK_DIR}/posts")" == "${posts}" ]] || test_die "A POST arrived during the hold"
  test_ok "No POST during the hold"
}

step_restart_in_hold() {
  local posts=""

  test_info "A restart during the hold: lines written while ewsposter is down"
  posts="$(python3 "${CHECK}" posts "${SINK_DIR}/posts")"
  docker stop -t 30 "${TEST_CONTAINER_NAME}" >/dev/null
  cowrie_session c0ffee00a004 198.51.100.25 22 ssh
  cowrie_session c0ffee00a005 198.51.100.26 23 telnet
  : > "${DATA_DIR}/ews/hold"
  docker start "${TEST_CONTAINER_NAME}" >/dev/null
  wait_until 30 ack_present || test_die "hold.ack did not appear after the restart"
  wait_until 30 sessions_known c0ffee00a004 c0ffee00a005 \
    || test_die "The idle passes after the restart did not take the Cowrie sessions"
  [[ "$(python3 "${CHECK}" posts "${SINK_DIR}/posts")" == "${posts}" ]] || test_die "A POST arrived after the restart in the hold"
  test_ok "After the restart the hold went on idle, no POST"
}

step_hold_over() {
  local alerts=""

  test_info "The hold is over: the marker is removed, hold.ack must go"
  rm -f "${DATA_DIR}/ews/hold"
  wait_until 30 ack_absent || test_die "hold.ack is still there"
  test_ok "hold.ack is gone"
  # the idle round of the end of the hold reads what is there when it runs; a line for the normal
  # pass is written once that round is over
  wait_until "$(pass_wait)" state_is normal || test_die "hold.state does not say normal after the hold"

  alerts="$(python3 "${CHECK}" count "${SINK_DIR}/posts")"
  cowrie_session c0ffee00b001 198.51.100.31 22 ssh
  wait_until "$(pass_wait)" alert_from 198.51.100.31 || test_die "The line after the hold did not arrive"
  python3 "${CHECK}" none-of "${SINK_DIR}/posts" 198.51.100.21 198.51.100.22 198.51.100.23 198.51.100.24 \
    198.51.100.25 198.51.100.26 || test_die "Lines of the hold were sent"
  [[ "$(python3 "${CHECK}" count "${SINK_DIR}/posts")" == "$((alerts + 1))" ]] \
    || test_die "More than the one line after the hold was sent"
  state_is normal || test_die "hold.state does not say normal after the hold"
  test_ok "Only the line after the hold arrived, the lines of the hold did not"
}

step_stale_marker() {
  local old=""

  test_info "A marker older than 30 minutes is ignored"
  : > "${DATA_DIR}/ews/hold"
  old="$(( $(date +%s) - 31 * 60 ))"
  python3 -c 'import os, sys; os.utime(sys.argv[1], (int(sys.argv[2]), int(sys.argv[2])))' "${DATA_DIR}/ews/hold" "${old}"
  cowrie_session c0ffee00c001 198.51.100.41 22 ssh
  wait_until "$(pass_wait)" alert_from 198.51.100.41 || test_die "The line next to an old marker did not arrive"
  ack_absent || test_die "hold.ack appeared for an old marker"
  test_ok "The old marker was ignored"
}

step_stop() {
  local start=""
  local elapsed=""

  test_info "docker stop ends ewsposter at once"
  start=${SECONDS}
  docker stop -t 30 "${TEST_CONTAINER_NAME}" >/dev/null
  elapsed=$(( SECONDS - start ))
  (( elapsed <= 5 )) || test_die "docker stop took ${elapsed} s"
  test_ok "Stopped in ${elapsed} s"
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

  test_info "Starting the sink and ewsposter with its entrypoint"
  test_compose up -d --no-build >/dev/null
  test_wait_for_container || test_die "ewsposter container did not stay running"
  test_ok "Container is running"

  step_first_pass
  step_hold
  step_restart_in_hold
  step_hold_over
  step_stale_marker
  step_stop

  save_logs
  if grep -q "Traceback" "${RUN_LOG}/ewsposter.log"; then
    test_die "ewsposter printed a traceback, see ${RUN_LOG}/ewsposter.log"
  fi
  test_ok "No traceback in the output of the loop"

  test_ok "ewsposter post-build smoke test completed successfully"
}

main "$@"
