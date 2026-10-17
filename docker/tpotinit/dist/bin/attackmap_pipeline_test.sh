#!/bin/bash
# attackmap_pipeline_test.sh — end-to-end test of the T-Pot Attack Map pipeline
#
#   synthetic honeypot JSON  ->  Logstash (file input, all filters: date, geoip,
#   ip_rep, t-pot_hostname)  ->  Elasticsearch (logstash-*)  ->  DataServer
#   (map_data, polls ES)  ->  Redis pubsub (map_redis)  ->  AttackMapServer
#   (map_web)  ->  WebSocket (what the browser receives)
#
# Where the T-Pot Manager runs, this is `tpot check pipeline` (a test event for
# every honeypot, removed from Elasticsearch afterwards; it asks first, -y
# confirms). Without it the test below runs: on the T-Pot host (docker access
# required; every check is executed inside the existing containers, so the
# host needs only bash + docker), its test events are appended to the real
# honeypot log files and stay in Elasticsearch/Kibana as ordinary events.
# ewsposter sends nothing while the test runs (ews_hold.sh), and one check
# runs at a time.
#
# Usage: attackmap_pipeline_test.sh [--types cowrie,dionaea,honeytrap,rdphoneypot]
#                                   [--timeout SECONDS] [--dry-run] [-y]
#   --types     comma list of honeypot formats to inject (default: all four;
#               tpot check pipeline: every honeypot, --types names services).
#               The test source IPs are the fixed ones of T-Pot, never addresses
#               from Elasticsearch: 8.8.8.8 208.67.222.222 8.8.4.4 4.2.2.2, one
#               per type in --types order (from the start again for a fifth).
#   --timeout   seconds to wait per stage (default: 90)
#   --dry-run   print the events and commands only, touch nothing
#   -y, --yes   start without asking (tpot check pipeline asks)
#   -B, --become-file FILE
#               the sudo password in a file, for a log file only root may append to

# an exported CDPATH turns a cd into a search that prints the folder it found
unset CDPATH
myHERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
# the look of the T-Pot scripts (installer/lib/ui.sh of the checkout this lies in,
# or of ~/tpotce), plain text where there is none (i.e. inside the tpotinit image)
# shellcheck source=installer/lib/ui.sh
if ! source "${myHERE}/../../../../installer/lib/ui.sh" 2>/dev/null && \
   ! source "${HOME}/tpotce/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback
    fuUI_INIT () { return 0; }
    fuUI_BANNER () { local myLINE; echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE//$'\n'/$'\n'### }"; done; echo; }
    fuUI_INFO () { local myTEXT="$*"; echo "### ${myTEXT//$'\n'/$'\n'### }"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuMARK () { [ "${TPOT_MARKS:-}" = "1" ] && echo "@@tpot $*"; return 0; }
# <<< plain fallback
fi
fuUI_INIT
# the hold of ewsposter and the lock of the checks
# shellcheck source=docker/tpotinit/dist/bin/ews_hold.sh
if ! source "${myHERE}/ews_hold.sh";
  then
    fuUI_ERROR "${myHERE}/ews_hold.sh is missing, the test does not run without the hold of ewsposter."
    exit 1
fi

myBECOME_FILE=""
myTYPES="cowrie,dionaea,honeytrap,rdphoneypot"
myFALLBACKIPS="8.8.8.8 208.67.222.222 8.8.4.4 4.2.2.2"
myTIMEOUT=90
myDRYRUN=0
myYES=0
myTYPES_SET=0
myTIMEOUT_SET=0
myTPOTCE="${TPOTCE_DIR:-$HOME/tpotce}"
myENV="$myTPOTCE/.env"
myREDISCHANNEL="attack-map-production"
myTMP=""
myRUNID="$(date -u +%Y%m%d%H%M%S)"
mySTART="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

myKNOWNTYPES="cowrie dionaea honeytrap rdphoneypot ciscoasa"
myINJECTEDTYPES=""
# the types left alone because ewsposter cannot hold their module (they fail the test)
myNOTHELDTYPES=""

# Per-type lookups as functions (no associative arrays: portable to bash 3).
function fuKNOWN { case "$1" in cowrie|dionaea|honeytrap|rdphoneypot|ciscoasa) return 0 ;; *) return 1 ;; esac; }
# test source IP per type (the fixed ones of myFALLBACKIPS), assigned by fuPICKIPS into myIP_<type>
function fuTESTIP { eval "printf '%s' \"\${myIP_$1:-}\""; }
# fixed, unusual source port per type: together with the IP it identifies OUR
# event unambiguously in ES, Redis and WebSocket output (real traffic from the
# same IP cannot be mistaken for the test event)
function fuSRCPORT {
  case "$1" in cowrie) echo 51234 ;; dionaea) echo 51235 ;; honeytrap) echo 51236 ;; rdphoneypot) echo 51237 ;; ciscoasa) echo 51238 ;; esac
}
function fuLOGFILE { # relative to the data path, exactly the Logstash file inputs
  case "$1" in cowrie) echo cowrie/log/cowrie.json ;; dionaea) echo dionaea/log/dionaea.json ;;
    honeytrap) echo honeytrap/log/attackers.json ;; rdphoneypot) echo rdphoneypot/log/rdphoneypot.json ;; ciscoasa) echo ciscoasa/log/ciscoasa.json ;; esac
}
function fuPORT {
  case "$1" in cowrie) echo 22 ;; dionaea) echo 445 ;; honeytrap) echo 3306 ;; rdphoneypot) echo 3389 ;; ciscoasa) echo 8443 ;; esac
}
# per-type stage results live in plain variables myRES_<stage>_<type>
function fuSETRES { eval "myRES_$1_$2=\"\$3\"" ; }
function fuGETRES { eval "printf '%s' \"\${myRES_$1_$2:-}\"" ; }

# WebSocket client executed INSIDE map_web (python3 + aiohttp are part of the
# image). No Origin header is sent, so the same-host origin check does not
# apply — exactly like a non-browser client. Prints every received frame.
read -r -d '' myWSCLIENT <<'PY'
import asyncio, os, aiohttp
async def main():
    url = os.environ.get("WS_URL", "ws://127.0.0.1:64299/websocket")
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            print("WS-CONNECTED", flush=True)
            async for m in ws:
                if m.type == aiohttp.WSMsgType.TEXT:
                    print(m.data, flush=True)
                elif m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
asyncio.run(main())
PY

function fuUSAGE {
  sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

function fuPARSEARGS {
  while [ $# -gt 0 ]; do
    case "$1" in
      --types) [ $# -ge 2 ] || { fuUI_ERROR "--types needs a value"; fuUSAGE 1; }
               myTYPES="$2"; myTYPES_SET=1; shift 2 ;;
      --timeout) [ $# -ge 2 ] || { fuUI_ERROR "--timeout needs a value"; fuUSAGE 1; }
                 myTIMEOUT="$2"; myTIMEOUT_SET=1; shift 2 ;;
      --dry-run) myDRYRUN=1; shift ;;
      -y|--yes) myYES=1; shift ;;
      -B|--become-file) [ $# -ge 2 ] || { fuUI_ERROR "$1 needs a file"; fuUSAGE 1; }
                        myBECOME_FILE="$2"; shift 2 ;;
      -h|--help) fuUSAGE 0 ;;
      *) fuUI_ERROR "unknown argument: $1"; fuUSAGE 1 ;;
    esac
  done
  case "$myTIMEOUT" in
    ''|*[!0-9]*|0) fuUI_ERROR "--timeout takes whole seconds above 0, not '$myTIMEOUT'"; exit 1 ;;
  esac
  if [ -n "$myBECOME_FILE" ]; then
    [ -r "$myBECOME_FILE" ] || { fuUI_ERROR "cannot read the sudo password from $myBECOME_FILE."; exit 1; }
    myBECOME_FILE=$(cd "$(dirname "$myBECOME_FILE")" && pwd)/$(basename "$myBECOME_FILE")
    sudo () { command sudo -S -p "" -v < "$myBECOME_FILE" >/dev/null 2>&1; command sudo "$@"; }
  fi
}

function fuLOWER { # the names in lower case (Cowrie, Ciscoasa as the Logstash types write them)
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

function fuCHECKTYPES { # the types this test knows (tpot check pipeline takes every honeypot)
  myTYPES=$(fuLOWER "$myTYPES")
  for t in ${myTYPES//,/ }; do
    fuKNOWN "$t" || { fuUI_ERROR "unknown type '$t' (known: $myKNOWNTYPES)"; exit 1; }
  done
}

# the test is tpot check pipeline of the T-Pot Manager where it runs (as genuser.sh hands over to
# tpot users add); this script is its fallback, i.e. inside the tpotinit image
myTPOT="$myTPOTCE/tpot"
function fuTPOT_READY {
  [ -x "$myTPOT" ] && "$myTPOT" setup >/dev/null 2>&1
}
function fuHANDOVER {
  local -a args=(check pipeline)
  local myANSWER="" myRC=0
  myTYPES=$(fuLOWER "$myTYPES")
  [ "$myTYPES_SET" = 1 ] && args+=(--only "$myTYPES")
  [ "$myTIMEOUT_SET" = 1 ] && args+=(--timeout "$myTIMEOUT")
  [ "$myDRYRUN" = 1 ] && args+=(--dry-run)
  [ -n "$myBECOME_FILE" ] && args+=(--become-file "$myBECOME_FILE")
  [ "$myYES" = 1 ] && args+=(--yes)
  fuTPOT_READY || return 0
  # a name of --types that is no honeypot of this T-Pot is a wrong option of this script (exit 1):
  # the dry run of tpot check pipeline knows them (it holds nothing and writes nothing)
  if [ "$myTYPES_SET" = 1 ]; then
    myANSWER=$("$myTPOT" check pipeline --only "$myTYPES" --dry-run 2>&1 >/dev/null)
    myRC=$?
    if [ "$myRC" = 2 ] && printf '%s' "$myANSWER" | grep -q "not a honeypot of this T-Pot"; then
      fuUI_ERROR "--types: $(printf '%s' "$myANSWER" | sed -n 's/.*\(not a honeypot of this T-Pot.*\)/\1/p' | head -1)"
      exit 1
    fi
  fi
  exec "$myTPOT" "${args[@]}"
}

function fuCHECKDEPS {
  [ "$myDRYRUN" = 1 ] && return
  command -v docker >/dev/null 2>&1 || { fuUI_ERROR "docker not found."; exit 1; }
  for c in logstash elasticsearch map_redis map_data map_web; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" != "true" ]; then
      fuUI_ERROR "container '$c' is not running."; exit 1
    fi
  done
}

function fuDATAPATH {
  myDATA="./data"
  if [ -f "$myENV" ]; then
    myDATA=$(grep -E '^TPOT_DATA_PATH=' "$myENV" | tail -1 | cut -d= -f2- | tr -d '"'"'"'')
    [ -n "$myDATA" ] || myDATA="./data"
  fi
  case "$myDATA" in
    /*) ;;
    *) myDATA="$myTPOTCE/${myDATA#./}" ;;
  esac
  fuUI_INFO "data path: $myDATA"
}

function fuPICKIPS {
  # only the fixed test IPs, one per type in --types order, from the start again when there are
  # more types than IPs: the source port of a type tells its event apart
  local -a pool
  local i=0 t
  read -r -a pool <<< "$myFALLBACKIPS"
  for t in ${myTYPES//,/ }; do
    eval "myIP_$t=\"\${pool[$(( i % ${#pool[@]} ))]}\""
    i=$((i + 1))
  done
  fuUI_INFO "test source IPs:$(for t in ${myTYPES//,/ }; do printf ' %s=%s' "$t" "$(fuTESTIP "$t")"; done)"
  fuUI_HINT "(the fixed test IPs of T-Pot, no addresses of real attackers)"
}

function fuEVENT { # type ip timestamp -> one JSON line (fields the Logstash filter expects)
  local t="$1" ip="$2" ts="$3" port sport sess="attackmap-test-$myRUNID"
  port="$(fuPORT "$t")"
  sport="$(fuSRCPORT "$t")"
  case "$t" in
    cowrie)
      printf '{"eventid":"cowrie.session.connect","src_ip":"%s","src_port":%s,"dst_ip":"172.20.0.5","dst_port":%s,"session":"%s","protocol":"ssh","message":"New connection: %s:%s (172.20.0.5:%s) [session: %s]","sensor":"tpot","timestamp":"%s"}\n' \
        "$ip" "$sport" "$port" "$sess" "$ip" "$sport" "$port" "$sess" "$ts" ;;
    dionaea)
      printf '{"timestamp":"%s","src_ip":"::ffff:%s","src_port":%s,"dst_ip":"::ffff:172.20.0.6","dst_port":%s,"connection":{"type":"accept","protocol":"smbd","transport":"tcp"}}\n' \
        "$ts" "$ip" "$sport" "$port" ;;
    honeytrap)
      printf '{"timestamp":"%s","attack_connection":{"local_ip":"172.20.0.7","local_port":%s,"remote_ip":"%s","remote_port":%s,"protocol":"tcp","payload":{"length":0}}}\n' \
        "$ts" "$port" "$ip" "$sport" ;;
    ciscoasa)
      printf '{"timestamp":"%s","event_type":"http.request","src_ip":"%s","src_port":%s,"dest_port":%s,"protocol":"tcp","cve":[],"payload":"pipeline-test","asa":{"http":{"method":"GET","uri":"/","user_agent":"pipeline-test"},"profile":{"version":"9.20(4)5","hostname":"asa-test"}}}\n' \
        "$ts" "$ip" "$sport" "$port" ;;
    rdphoneypot)
      printf '{"timestamp":"%s","src_ip":"%s","src_port":%s,"dst_ip":"172.20.0.8","dst_port":%s}\n' \
        "$ts" "$ip" "$sport" "$port" ;;
  esac
}

function fuSTARTLISTENERS {
  myTMP=$(mktemp -d)
  fuUI_INFO "starting listeners (Redis pubsub + WebSocket) for ${myTIMEOUT}s ..."
  timeout "$myTIMEOUT" docker exec map_redis redis-cli SUBSCRIBE "$myREDISCHANNEL" > "$myTMP/redis.log" 2>&1 &
  myPID_REDIS=$!
  timeout "$myTIMEOUT" docker exec map_web python3 -c "$myWSCLIENT" > "$myTMP/ws.log" 2>&1 &
  myPID_WS=$!
  sleep 3
  grep -q "WS-CONNECTED" "$myTMP/ws.log" && fuUI_OK "websocket client connected to map_web" \
    || fuUI_WARN "websocket client not connected yet ($(head -c 200 "$myTMP/ws.log"))"
}

function fuINJECT {
  fuUI_INFO "injecting test events (run id $myRUNID) ..."
  for t in ${myTYPES//,/ }; do
    local file ts line ip
    file="$myDATA/$(fuLOGFILE "$t")"
    ip="$(fuTESTIP "$t")"
    ts="$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"
    line=$(fuEVENT "$t" "$ip" "$ts")
    fuUI_HINT "[$t] -> $file"
    fuUI_HINT "    $line"
    if [ "$myDRYRUN" = 1 ]; then continue; fi
    if ! fuEWS_HOLDS "$t"; then
      fuUI_WARN "[$t] ewsposter cannot hold its module (its idle pass fails; docker logs ewsposter), nothing injected"
      myNOTHELDTYPES="$myNOTHELDTYPES $t"
      continue
    fi
    if [ ! -d "$(dirname "$file")" ]; then
      fuUI_WARN "$(dirname "$file") does not exist (honeypot not part of this compose file)"
      continue
    fi
    if ! printf '%s\n' "$line" | tee -a "$file" >/dev/null 2>&1; then
      printf '%s\n' "$line" | sudo tee -a "$file" >/dev/null || { fuUI_ERROR "cannot append to $file"; continue; }
    fi
    myINJECTEDTYPES="$myINJECTEDTYPES $t"
  done
}

function fuESQUERY { # type -> ES query body: exactly OUR event (ip + src_port + type, since start)
  local t="$1"
  printf '{"size":1,"track_total_hits":true,"_source":["type","src_ip","src_port","dest_port","tags","t-pot_hostname","geoip.latitude","geoip.longitude","geoip_ext.latitude","geoip_ext.longitude"],"query":{"bool":{"filter":[{"term":{"src_ip.keyword":"%s"}},{"term":{"src_port":%s}},{"range":{"@timestamp":{"gte":"%s"}}}]}}}' \
    "$(fuTESTIP "$t")" "$(fuSRCPORT "$t")" "$mySTART"
}

function fuWAITES {
  fuUI_INFO "waiting for Elasticsearch (logstash-*) ..."
  local deadline=$(( $(date +%s) + myTIMEOUT ))
  while :; do
    local pending=0
    for t in $myINJECTEDTYPES; do
      [ -n "$(fuGETRES ES "$t")" ] && continue
      local res
      res=$(docker exec elasticsearch curl -s -H 'Content-Type: application/json' \
            "localhost:9200/logstash-*/_search" -d "$(fuESQUERY "$t")")
      local total
      total=$(printf '%s' "$res" | grep -o '"total":{"value":[0-9]*' | head -1 | grep -o '[0-9]*$')
      if [ "${total:-0}" -gt 0 ]; then
        # exactly the fields DataServer.process_data() needs: geoip coordinates
        # (from src_ip), geoip_ext (from MY_EXTIP), t-pot_hostname, dest_port
        local missing=""
        if printf '%s' "$res" | grep -q '_geoip_lookup_failure'; then
          fuSETRES ES "$t" "FAIL (GeoIP lookup failed)"
          fuUI_HINT "[$t] indexed, but GeoIP lookup FAILED for $(fuTESTIP "$t") (no GeoLite2 City entry) -> DataServer will skip it"
          continue
        fi
        printf '%s' "$res" | grep -q '"geoip":{[^}]*"latitude"' || missing="$missing geoip.latitude"
        printf '%s' "$res" | grep -q '"geoip":{[^}]*"longitude"' || missing="$missing geoip.longitude"
        printf '%s' "$res" | grep -q '"geoip_ext":{[^}]*"latitude"' || missing="$missing geoip_ext.latitude"
        printf '%s' "$res" | grep -q '"t-pot_hostname"' || missing="$missing t-pot_hostname"
        printf '%s' "$res" | grep -q '"dest_port"' || missing="$missing dest_port"
        if [ -z "$missing" ]; then
          fuSETRES ES "$t" "PASS ($(( $(date +%s) - mySTART_EPOCH ))s)"
          fuUI_HINT "[$t] indexed with geoip coordinates, geoip_ext, t-pot_hostname, dest_port"
        else
          fuSETRES ES "$t" "FAIL (missing:$missing)"
          fuUI_HINT "[$t] indexed but missing:$missing"
        fi
      else
        pending=1
      fi
    done
    [ "$pending" = 0 ] && return
    [ "$(date +%s)" -ge "$deadline" ] && break
    sleep 3
  done
  for t in $myINJECTEDTYPES; do
    [ -n "$(fuGETRES ES "$t")" ] || { fuSETRES ES "$t" "FAIL (timeout)"; fuUI_HINT "[$t] NOT indexed within ${myTIMEOUT}s"; }
  done
}

function fuWAITMAP {
  fuUI_INFO "waiting for DataServer -> Redis -> map_web -> WebSocket (DataServer polls with a 10s indexing lag) ..."
  local deadline=$(( $(date +%s) + myTIMEOUT ))
  while :; do
    local pending=0
    for t in $myINJECTEDTYPES; do
      local ip sport
      ip="$(fuTESTIP "$t")"; sport="$(fuSRCPORT "$t")"
      if [ -z "$(fuGETRES REDIS "$t")" ]; then
        if grep "\"src_ip\": *\"$ip\"" "$myTMP/redis.log" | grep -q "\"src_port\": *$sport\b"; then
          fuSETRES REDIS "$t" "PASS ($(( $(date +%s) - mySTART_EPOCH ))s)"; fuUI_HINT "[$t] seen on Redis pubsub"
        else pending=1; fi
      fi
      if [ -z "$(fuGETRES WS "$t")" ]; then
        if grep "\"src_ip\": *\"$ip\"" "$myTMP/ws.log" | grep -q "\"src_port\": *$sport\b"; then
          fuSETRES WS "$t" "PASS ($(( $(date +%s) - mySTART_EPOCH ))s)"; fuUI_HINT "[$t] delivered over the WebSocket"
        else pending=1; fi
      fi
    done
    [ "$pending" = 0 ] && return
    [ "$(date +%s)" -ge "$deadline" ] && break
    sleep 2
  done
  for t in $myINJECTEDTYPES; do
    [ -n "$(fuGETRES REDIS "$t")" ] || fuSETRES REDIS "$t" "FAIL (timeout)"
    [ -n "$(fuGETRES WS "$t")" ] || fuSETRES WS "$t" "FAIL (timeout)"
  done
}

function fuREPORT {
  echo
  printf '%-14s %-16s %-10s %-26s %-16s %-16s\n' "type" "src_ip" "injected" "elasticsearch" "redis pubsub" "websocket"
  local rc=0
  for t in ${myTYPES//,/ }; do
    case " $myINJECTEDTYPES " in
      *" $t "*)
      printf '%-14s %-16s %-10s %-26s %-16s %-16s\n' "$t" "$(fuTESTIP "$t")" "yes" "$(fuGETRES ES "$t")" "$(fuGETRES REDIS "$t")" "$(fuGETRES WS "$t")"
      case "$(fuGETRES ES "$t")$(fuGETRES REDIS "$t")$(fuGETRES WS "$t")" in *FAIL*) rc=1 ;; esac
      ;;
      *)
      case " $myNOTHELDTYPES " in
        *" $t "*) printf '%-14s %-16s %-10s %-26s\n' "$t" "$(fuTESTIP "$t")" "no" "FAIL (ewsposter cannot hold it)"; rc=1 ;;
        *) printf '%-14s %-16s %-10s %-26s\n' "$t" "$(fuTESTIP "$t")" "skipped" "-" ;;
      esac
      ;;
    esac
  done
  echo
  [ "$rc" = 0 ] && fuUI_OK "PASS: the full pipeline delivered every injected event to the Attack Map." \
                || fuUI_ERROR "FAIL: see the stages above (listener logs kept in $myTMP)."
  fuUI_HINT "The test events remain in Elasticsearch/Kibana as ordinary events (src_ip$(for t in ${myTYPES//,/ }; do printf ' %s' "$(fuTESTIP "$t")"; done), src_port 51234-51237)."
  return $rc
}

function fuCLEANUP {
  [ -n "${myPID_REDIS:-}" ] && kill "$myPID_REDIS" 2>/dev/null
  [ -n "${myPID_WS:-}" ] && kill "$myPID_WS" 2>/dev/null
  wait ${myPID_REDIS:-} ${myPID_WS:-} 2>/dev/null
}

# Main
fuPARSEARGS "$@"
fuHANDOVER
fuCHECKTYPES
fuUI_BANNER "Attack Map pipeline test" "Honeypot JSON -> Logstash -> Elasticsearch -> map_data -> Redis -> map_web -> WebSocket"
fuMARK phase prepare Checking the containers and picking test IPs
fuCHECKDEPS
fuDATAPATH
fuPICKIPS
mySTART_EPOCH=$(date +%s)
if [ "$myDRYRUN" = 1 ]; then
  fuUI_INFO "DRY RUN — nothing is written. Events that would be appended:"
  fuINJECT
  echo
  fuUI_INFO "ES query per test IP (example):"
  fuESQUERY "$(printf '%s' "${myTYPES%%,*}")"; echo
  fuUI_INFO "Listeners: docker exec map_redis redis-cli SUBSCRIBE $myREDISCHANNEL | docker exec map_web python3 -c <ws client>"
  fuMARK phase "done" Done
  exit 0
fi
# at the end the listeners, then the hold of ewsposter (after the last injected line)
fuEWS_HOLD_TRAPS fuCLEANUP
fuMARK phase hold Holding ewsposter
fuEWS_HOLD_BEGIN "$myDATA" "$myTPOTCE/docker-compose.yml" || { fuMARK fail hold; exit 3; }
fuMARK phase inject Injecting the test events
fuSTARTLISTENERS
fuINJECT
[ -n "$myINJECTEDTYPES" ] || { fuUI_ERROR "nothing injected."; exit 1; }
fuMARK phase elasticsearch Waiting for Elasticsearch
fuWAITES
fuMARK phase map Waiting for the Attack Map
fuWAITMAP
fuMARK phase release Lifting the hold of ewsposter
fuCLEANUP
fuEWS_HOLD_END
fuMARK phase "done" Done
fuREPORT
