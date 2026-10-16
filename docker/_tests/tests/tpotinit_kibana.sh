#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="tpotinit_kibana"
DEFAULT_IMAGE="ghcr.io/telekom-security/tpotinit:24.04.2"
IMAGE=""
TPOTINIT_DIR="${DOCKER_ROOT}/tpotinit"
myNET=""
myVOLUME=""
mySTUB=""
myFAILED=0

usage() {
  cat <<EOF
Usage: $0 [options]

Check the Kibana objects job of tpotinit (dist/bin/kibana_objects.sh with
kibana_objects.py) against a stub Kibana in a second container: the first run
marks the first import as pending, a Kibana that answers late and refuses the
first import keeps it pending, the next start imports, later starts keep the
objects as TPOT_KIBANA_OBJECTS says, a Kibana without T-Pot's objects gets them
again, and a TERM during the wait ends the job at once.

The image provides python3, curl and yq; the job, the module and the Kibana
objects of this checkout are mounted over the ones in it.

Options:
  --image IMAGE      Image to test. Defaults to docker/tpotinit/docker-compose.yml.
  --timeout SEC      Accepted for runner compatibility.
  --bind-ip IP       Accepted for runner compatibility; nothing is published.
  --keep-artifacts   Keep the temporary files for debugging.
  -h, --help         Show this help message.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image) [[ $# -ge 2 ]] || test_die "--image requires an argument"; IMAGE="$2"; shift 2 ;;
      --image=*) IMAGE="${1#*=}"; shift ;;
      --timeout) [[ $# -ge 2 ]] || test_die "--timeout requires an argument"; TEST_TIMEOUT="$2"; shift 2 ;;
      --timeout=*) TEST_TIMEOUT="${1#*=}"; shift ;;
      --bind-ip) [[ $# -ge 2 ]] || test_die "--bind-ip requires an argument"; TEST_BIND_IP="$2"; shift 2 ;;
      --bind-ip=*) TEST_BIND_IP="${1#*=}"; shift ;;
      --keep-artifacts) TEST_KEEP_ARTIFACTS="true"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) test_die "Unknown option: $1" ;;
    esac
  done
}

# the stub Kibana: /api/status answers 503 for the first LATE requests, then 200 without a version (as
# T-Pot's Kibana does without a login); with OWN_CONFIG it holds the config it made of its own at its
# start (config:9.5.4, buildNum 107666); the first FAIL_IMPORTS imports answer 500; it keeps what is
# imported and exports it; one line per request on stdout
write_stub() {
  cat > "${TEST_TMP_ROOT}/stub.py" <<'EOF'
import datetime
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LATE = int(os.environ.get("LATE", "0"))
FAIL_IMPORTS = int(os.environ.get("FAIL_IMPORTS", "0"))
STORE = {}
if os.environ.get("OWN_CONFIG"):
    STORE["config:9.5.4"] = {"type": "config", "id": "9.5.4", "references": [],
                             "attributes": {"buildNum": 107666, "isDefaultIndexMigrated": True},
                             "updated_at": "2026-10-16T14:20:09.290Z"}
COUNT = {"status": 0, "import": 0}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, code, text, kind="application/json"):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        print(f"{self.command} {self.path.split('?')[0]} {code}", flush=True)

    def do_GET(self):
        if self.path != "/api/status":
            return self.answer(404, "{}")
        COUNT["status"] += 1
        if COUNT["status"] <= LATE:
            return self.answer(503, '{"status":{"overall":{"level":"unavailable"}}}')
        return self.answer(200, '{"status":{"overall":{"level":"available"}}}')

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
        if self.headers.get("kbn-xsrf") != "true":
            return self.answer(400, '{"message":"no kbn-xsrf"}')
        if self.path.startswith("/api/saved_objects/_export"):
            return self.answer(200, "".join(json.dumps(o) + "\n" for o in STORE.values()), "application/x-ndjson")
        if not self.path.startswith("/api/saved_objects/_import"):
            return self.answer(404, "{}")
        COUNT["import"] += 1
        if COUNT["import"] <= FAIL_IMPORTS:
            return self.answer(500, '{"statusCode":500,"error":"Internal Server Error","message":"stub"}')
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        results = []
        for line in body.split("\n"):
            line = line.strip()
            if not line.startswith("{"):
                continue
            obj = dict(json.loads(line), updated_at=now)
            STORE[f"{obj['type']}:{obj['id']}"] = obj
            results.append({"type": obj["type"], "id": obj["id"], "meta": {}})
        return self.answer(200, json.dumps({"success": True, "successCount": len(results),
                                            "successResults": results}))


ThreadingHTTPServer(("0.0.0.0", 5601), Stub).serve_forever()
EOF
  printf 'services:\n  tpotinit:\n    networks: [nginx_local]\n' > "${TEST_TMP_ROOT}/net.yml"
}

cleanup() {
  [[ -z "${mySTUB}" ]] || docker rm -f "${mySTUB}" >/dev/null 2>&1 || true
  docker rm -f "${TEST_PROJECT_NAME}-job" >/dev/null 2>&1 || true
  [[ -z "${myNET}" ]] || docker network rm "${myNET}" >/dev/null 2>&1 || true
  [[ -z "${myVOLUME}" ]] || docker volume rm "${myVOLUME}" >/dev/null 2>&1 || true
  if [[ "${TEST_KEEP_ARTIFACTS}" != "true" ]]; then rm -rf "${TEST_TMP_ROOT}"; else test_info "Artifacts in ${TEST_TMP_ROOT}"; fi
}

# a new stub Kibana, reached as kibana:5601 on the test network; $1 = LATE, $2 = FAIL_IMPORTS
start_stub() {
  [[ -z "${mySTUB}" ]] || docker rm -f "${mySTUB}" >/dev/null 2>&1 || true
  mySTUB="${TEST_PROJECT_NAME}-kibana-$((RANDOM))"
  docker run -d --name "${mySTUB}" --network "${myNET}" --network-alias kibana \
    -e LATE="$1" -e FAIL_IMPORTS="$2" -e OWN_CONFIG="${3:-}" \
    -v "${TEST_TMP_ROOT}/stub.py:/work/stub.py:ro" \
    --entrypoint python3 "${IMAGE}" /work/stub.py >/dev/null
}

# the requests the stub has answered so far
stub_log() {
  docker logs "${mySTUB}" 2>/dev/null
}

# docker run with the data volume, the test network and the files of this checkout; removed when it
# ends, unless the first argument is --keep (cleanup removes it)
in_tpotinit() {
  local myRM="--rm"
  if [[ "${1:-}" == "--keep" ]]; then myRM="--init=false"; shift; fi
  docker run "${myRM}" --network "${myNET}" -v "${myVOLUME}:/data" \
    -v "${TPOTINIT_DIR}/dist/bin/kibana_objects.sh:/opt/tpot/bin/kibana_objects.sh:ro" \
    -v "${TPOTINIT_DIR}/dist/bin/kibana_objects.py:/opt/tpot/bin/kibana_objects.py:ro" \
    -v "${TPOTINIT_DIR}/dist/etc/objects:/opt/tpot/etc/objects:ro" \
    -v "${TEST_TMP_ROOT}/net.yml:/work/net.yml:ro" \
    -e COMPOSE=/work/net.yml -e TPOT_KIBANA_WAIT=60 -e TPOT_KIBANA_OBJECTS="${myMODE:-keep}" \
    "$@"
}

# one start of the job: its output into out, its exit code into myRC
run_job() {
  myRC=0
  in_tpotinit --entrypoint bash "${IMAGE}" /opt/tpot/bin/kibana_objects.sh > "${TEST_TMP_ROOT}/job.out" 2>&1 || myRC=$?
  out="$(cat "${TEST_TMP_ROOT}/job.out")"
}

# how many advanced settings (config) the stub Kibana holds
stub_configs() {
  in_tpotinit --entrypoint curl "${IMAGE}" -s -X POST -H "kbn-xsrf: true" -H "Content-Type: application/json" \
    -d '{"type":"*"}' http://kibana:5601/api/saved_objects/_export | grep -c '"type": "config"' || true
}

# the ids of the advanced settings (config) the stub Kibana holds, one per line
stub_config_ids() {
  in_tpotinit --entrypoint curl "${IMAGE}" -s -X POST -H "kbn-xsrf: true" -H "Content-Type: application/json" \
    -d '{"type":"*"}' http://kibana:5601/api/saved_objects/_export | grep '"type": "config"' \
    | sed -n 's/.*"id": "\([^"]*\)".*/\1/p' | sort
}

# the config objects the stub Kibana holds, as it exports them
stub_config() {
  in_tpotinit --entrypoint curl "${IMAGE}" -s -X POST -H "kbn-xsrf: true" -H "Content-Type: application/json" \
    -d '{"type":"*"}' http://kibana:5601/api/saved_objects/_export | grep '"type": "config"'
}

# $1 = name, $2 = the condition (a command line, run with eval), $3 = what to show on a FAIL
check() {
  if eval "$2"; then echo "ok   $1"; else echo "FAIL $1: $3"; myFAILED=1; fi
}

# the state in the data volume: pending (or "none"), whether record.json is there, then every file or
# folder of /data/kibana_objects that does not belong to the owner of /data
state() {
  in_tpotinit --entrypoint sh "${IMAGE}" -c \
    'cat /data/kibana_objects/pending 2>/dev/null || echo none
     [ -f /data/kibana_objects/record.json ] && echo record || echo no-record
     u="$(stat -c %u /data)"; g="$(stat -c %g /data)"
     find /data/kibana_objects \( ! -user "${u}" -o ! -group "${g}" \) -exec stat -c "%n %u:%g" {} +'
}

# $1 = uid:gid for /data, as tpotinit sets it (update_permissions) before the job
own_data() {
  in_tpotinit --entrypoint chown "${IMAGE}" "$1" /data >/dev/null
}

main() {
  parse_args "$@"
  test_validate_timeout
  test_require_command docker
  docker info >/dev/null 2>&1 || test_die "Docker daemon is not accessible"
  IMAGE="${IMAGE:-$(test_read_compose_image tpotinit "${DEFAULT_IMAGE}")}"
  test_require_image "${IMAGE}" "docker compose -f docker/tpotinit/docker-compose.yml build"
  docker run --rm --entrypoint python3 "${IMAGE}" -c 'import sys' >/dev/null 2>&1 \
    || test_die "${IMAGE} has no python3, build it again: docker compose -f docker/tpotinit/docker-compose.yml build"
  test_prepare_harness "${TEST_NAME}"
  trap cleanup EXIT
  write_stub
  myNET="${TEST_PROJECT_NAME}-net"
  myVOLUME="${TEST_PROJECT_NAME}-data"
  docker network create "${myNET}" >/dev/null
  docker volume create "${myVOLUME}" >/dev/null

  test_info "Checking the Kibana objects job of ${IMAGE} with the files of this checkout"
  local out st

  # the first run: pending before /data/uuid, as entrypoint.sh writes it; /data is tpot's
  own_data 2000:2000
  out="$(in_tpotinit --entrypoint python3 "${IMAGE}" /opt/tpot/bin/kibana_objects.py mark-fresh --data /data 2>&1)" || true
  st="$(state)"
  check "mark-fresh writes a fresh pending" \
    '[[ "$(head -1 <<<"${st}")" == "{\"attempt\": 0, \"reason\": \"fresh\", \"refused\": [], \"version\": 1}" ]]' "${out} ${st}"
  check "  ... its folder and file belong to the owner of /data (2000:2000)" \
    '[[ "$(tail -n +2 <<<"${st}")" == no-record ]]' "${st}"

  # start 1: Kibana answers late, then refuses the import: still pending, the attempt counted
  start_stub 2 1
  run_job
  st="$(state)"
  check "a late Kibana is waited for (2 x 503, then 200)" \
    '[[ "$(stub_log | grep -c "^GET /api/status 503")" -eq 2 && "$(stub_log | grep -c "^GET /api/status 200")" -eq 1 ]]' "$(stub_log)"
  check "a refused first import stays pending" \
    '[[ "${myRC}" -eq 1 && "$(head -1 <<<"${st}")" == *"\"attempt\": 1"* && "${st}" == *no-record* ]]' "rc=${myRC} ${out} ${st}"

  # start 2: the same Kibana takes the import, pending goes, the files are tpot's
  run_job
  st="$(state)"
  check "the next start imports and ends the pending first import" \
    '[[ "${myRC}" -eq 0 && "${out}" == *"first import, attempt 2, "*" imported"* && "$(head -1 <<<"${st}")" == none ]]' "rc=${myRC} ${out} ${st}"
  check "  ... the record is written and every file belongs to the owner of /data (2000:2000)" \
    '[[ "$(tail -n +2 <<<"${st}")" == record ]]' "${st}"
  check "  ... the job reached Kibana by its service name (kibana:5601)" \
    '[[ "$(stub_log | grep -c "^POST /api/saved_objects/_import 200")" -eq 1 ]]' "$(stub_log)"

  # start 3: keep with T-Pot's objects in Kibana: one export, no import
  run_job
  check "keep with T-Pot's objects imports nothing" \
    '[[ "${myRC}" -eq 0 && "${out}" == *"TPOT_KIBANA_OBJECTS=keep, nothing changed"* && "$(stub_log | grep -c "^POST /api/saved_objects/_import")" -eq 2 ]]' "rc=${myRC} ${out} $(stub_log)"

  # start 4: a new Kibana without T-Pot's objects (an import that never made it): imported again
  start_stub 0 0
  run_job
  st="$(state)"
  check "keep without any T-Pot object imports them again" \
    '[[ "${myRC}" -eq 0 && "${out}" == *"Kibana holds none of T-Pot'"'"'s objects"* && "${out}" == *"first import, "*" imported"* && "$(head -1 <<<"${st}")" == none ]]' "rc=${myRC} ${out} ${st}"
  check "  ... the advanced settings stay out (no fresh install)" '[[ "$(stub_configs)" -eq 0 ]]' "$(stub_configs) imported"

  # a fresh install whose Kibana answers at once: the whole file, the advanced settings too
  in_tpotinit --entrypoint sh "${IMAGE}" -c 'rm -rf /data/kibana_objects' >/dev/null 2>&1
  own_data 1234:4321
  in_tpotinit --entrypoint python3 "${IMAGE}" /opt/tpot/bin/kibana_objects.py mark-fresh --data /data >/dev/null 2>&1
  # its Kibana made config:9.5.4 of its own at its start, /api/status names no version
  start_stub 0 0 1
  run_job
  check "a fresh first import takes the advanced settings along" \
    '[[ "${myRC}" -eq 0 && "${out}" == *"first import, "*" imported"* && "$(stub_configs)" -ge 1 ]]' "rc=${myRC} ${out}"
  check "  ... under the version of Kibana's own config (config:9.5.4, its buildNum)" \
    '[[ "$(stub_config_ids)" == 9.5.4 && "$(stub_config)" == *"\"defaultRoute\""* && "$(stub_config)" == *"\"buildNum\": 107666"* ]]' \
    "$(stub_config_ids | tr '\n' ' ') $(stub_config)"
  st="$(state)"
  check "  ... its files belong to the owner of /data (1234:4321) right after the job" \
    '[[ "$(tail -n +2 <<<"${st}")" == record ]]' "${st}"

  # TERM while the job waits for Kibana: it ends at once
  start_stub 100000 0
  in_tpotinit --keep -d --name "${TEST_PROJECT_NAME}-job" --entrypoint bash "${IMAGE}" /opt/tpot/bin/kibana_objects.sh >/dev/null
  sleep 3
  local myBEFORE myAFTER myCODE
  myBEFORE="$(date +%s)"
  docker kill --signal TERM "${TEST_PROJECT_NAME}-job" >/dev/null
  myCODE="$(docker wait "${TEST_PROJECT_NAME}-job")"
  myAFTER="$(date +%s)"
  out="$(docker logs "${TEST_PROJECT_NAME}-job" 2>&1 || true)"
  check "TERM during the wait for Kibana ends the job at once" \
    '[[ "${myCODE}" -eq 143 && $(( myAFTER - myBEFORE )) -le 3 && "${out}" == *"tpotinit stops"* ]]' "code=${myCODE} took=$(( myAFTER - myBEFORE )) s ${out}"

  [[ "${myFAILED}" -eq 0 ]] || test_die "The Kibana objects job of tpotinit failed a check"
  test_ok "tpotinit Kibana objects job passed all checks"
}

main "$@"
