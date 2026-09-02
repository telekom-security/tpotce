#!/bin/bash
# attackmap_pipeline_test.sh — end-to-end test of the T-Pot Attack Map pipeline
#
#   synthetic honeypot JSON  ->  Logstash (file input, all filters: date, geoip,
#   ip_rep, t-pot_hostname)  ->  Elasticsearch (logstash-*)  ->  DataServer
#   (map_data, polls ES)  ->  Redis pubsub (map_redis)  ->  AttackMapServer
#   (map_web)  ->  WebSocket (what the browser receives)
#
# Runs on the T-Pot host (docker access required; every check is executed
# inside the existing containers, so the host needs only bash + docker).
# Test events are appended to the real honeypot log files and become
# ordinary events in Elasticsearch/Kibana afterwards (no cleanup by design).
#
# Usage: attackmap_pipeline_test.sh [--types cowrie,dionaea,honeytrap,rdphoneypot]
#                                   [--ips A,B,C,D] [--timeout SECONDS] [--dry-run]
#   --types     comma list of honeypot formats to inject (default: all four)
#   --ips       test source IPs, one per type in --types order. Default: IPv4
#               addresses that Elasticsearch ALREADY geolocated in the last 24 h
#               (so they are known to resolve in this host's GeoLite2 City DB),
#               padded from a built-in fallback list. Anycast resolvers such as
#               1.1.1.1 or 9.9.9.9 have NO city entry -> _geoip_lookup_failure
#               -> DataServer skips the event; the ES stage reports that.
#   --timeout   seconds to wait per stage (default: 90)
#   --dry-run   print the events and commands only, touch nothing

myTYPES="cowrie,dionaea,honeytrap,rdphoneypot"
myIPS=""
myFALLBACKIPS="8.8.8.8 208.67.222.222 8.8.4.4 4.2.2.2"
myTIMEOUT=90
myDRYRUN=0
myTPOTCE="${TPOTCE_DIR:-$HOME/tpotce}"
myENV="$myTPOTCE/.env"
myREDISCHANNEL="attack-map-production"
myTMP=""
myRUNID="$(date -u +%Y%m%d%H%M%S)"
mySTART="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

myKNOWNTYPES="cowrie dionaea honeytrap rdphoneypot"
myINJECTEDTYPES=""

# Per-type lookups as functions (no associative arrays: portable to bash 3).
function fuKNOWN { case "$1" in cowrie|dionaea|honeytrap|rdphoneypot) return 0 ;; *) return 1 ;; esac; }
# test source IP per type, assigned by fuPICKIPS into myIP_<type>
function fuTESTIP { eval "printf '%s' \"\${myIP_$1:-}\""; }
# fixed, unusual source port per type: together with the IP it identifies OUR
# event unambiguously in ES, Redis and WebSocket output (real traffic from the
# same IP cannot be mistaken for the test event)
function fuSRCPORT {
  case "$1" in cowrie) echo 51234 ;; dionaea) echo 51235 ;; honeytrap) echo 51236 ;; rdphoneypot) echo 51237 ;; esac
}
function fuLOGFILE { # relative to the data path, exactly the Logstash file inputs
  case "$1" in cowrie) echo cowrie/log/cowrie.json ;; dionaea) echo dionaea/log/dionaea.json ;;
    honeytrap) echo honeytrap/log/attackers.json ;; rdphoneypot) echo rdphoneypot/log/rdphoneypot.json ;; esac
}
function fuPORT {
  case "$1" in cowrie) echo 22 ;; dionaea) echo 445 ;; honeytrap) echo 3306 ;; rdphoneypot) echo 3389 ;; esac
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
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

function fuPARSEARGS {
  while [ $# -gt 0 ]; do
    case "$1" in
      --types) myTYPES="$2"; shift 2 ;;
      --ips) myIPS="$2"; shift 2 ;;
      --timeout) myTIMEOUT="$2"; shift 2 ;;
      --dry-run) myDRYRUN=1; shift ;;
      -h|--help) fuUSAGE ;;
      *) echo "ERROR: unknown argument: $1"; fuUSAGE ;;
    esac
  done
  for t in ${myTYPES//,/ }; do
    fuKNOWN "$t" || { echo "ERROR: unknown type '$t' (known: $myKNOWNTYPES)"; exit 2; }
  done
}

function fuCHECKDEPS {
  [ "$myDRYRUN" = 1 ] && return
  command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found."; exit 1; }
  for c in logstash elasticsearch map_redis map_data map_web; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" != "true" ]; then
      echo "ERROR: container '$c' is not running."; exit 1
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
  echo "[*] data path: $myDATA"
}

function fuPICKIPS {
  local picked=""
  if [ -n "$myIPS" ]; then
    picked="${myIPS//,/ }"
  elif [ "$myDRYRUN" = 0 ]; then
    # IPv4 sources Elasticsearch geolocated within the last 24 h: guaranteed
    # to resolve in this host's GeoLite2 City database
    picked=$(docker exec elasticsearch curl -s -H 'Content-Type: application/json' \
      "localhost:9200/logstash-*/_search" \
      -d '{"size":0,"query":{"bool":{"filter":[{"exists":{"field":"geoip.latitude"}},{"range":{"@timestamp":{"gte":"now-24h"}}}]}},"aggs":{"ips":{"terms":{"field":"src_ip.keyword","size":40}}}}' \
      | grep -o '"key":"[0-9.]*"' | cut -d'"' -f4 | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -8 | tr '\n' ' ')
  fi
  # pad with the fallback list, then assign one IP per type (in --types order)
  local pool="$picked $myFALLBACKIPS" i=0 t ip
  for t in ${myTYPES//,/ }; do
    i=$((i + 1))
    ip=$(printf '%s\n' $pool | awk -v n="$i" 'NF && !seen[$1]++ {c++; if (c==n) {print $1; exit}}')
    [ -n "$ip" ] || { echo "ERROR: not enough test IPs (need one per type, use --ips)."; exit 1; }
    eval "myIP_$t=\"$ip\""
  done
  echo "[*] test source IPs:$(for t in ${myTYPES//,/ }; do printf ' %s=%s' "$t" "$(fuTESTIP "$t")"; done)"
  if [ -n "$myIPS" ]; then
    echo "    (given via --ips)"
  elif [ -n "$picked" ]; then
    echo "    (taken from sources Elasticsearch geolocated in the last 24 h)"
  else
    echo "    (no geolocated events in Elasticsearch yet — fresh installation? — using the built-in fallback list)"
  fi
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
    rdphoneypot)
      printf '{"timestamp":"%s","src_ip":"%s","src_port":%s,"dst_ip":"172.20.0.8","dst_port":%s}\n' \
        "$ts" "$ip" "$sport" "$port" ;;
  esac
}

function fuSTARTLISTENERS {
  myTMP=$(mktemp -d)
  echo "[*] starting listeners (Redis pubsub + WebSocket) for ${myTIMEOUT}s ..."
  timeout "$myTIMEOUT" docker exec map_redis redis-cli SUBSCRIBE "$myREDISCHANNEL" > "$myTMP/redis.log" 2>&1 &
  myPID_REDIS=$!
  timeout "$myTIMEOUT" docker exec map_web python3 -c "$myWSCLIENT" > "$myTMP/ws.log" 2>&1 &
  myPID_WS=$!
  sleep 3
  grep -q "WS-CONNECTED" "$myTMP/ws.log" && echo "    websocket client connected to map_web" \
    || echo "    WARNING: websocket client not connected yet ($(head -c 200 "$myTMP/ws.log"))"
}

function fuINJECT {
  echo "[*] injecting test events (run id $myRUNID) ..."
  for t in ${myTYPES//,/ }; do
    local file ts line ip
    file="$myDATA/$(fuLOGFILE "$t")"
    ip="$(fuTESTIP "$t")"
    ts="$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"
    line=$(fuEVENT "$t" "$ip" "$ts")
    echo "  [$t] -> $file"
    echo "        $line"
    if [ "$myDRYRUN" = 1 ]; then continue; fi
    if [ ! -d "$(dirname "$file")" ]; then
      echo "        SKIP: $(dirname "$file") does not exist (honeypot not part of this compose file)"
      continue
    fi
    if ! printf '%s\n' "$line" | tee -a "$file" >/dev/null 2>&1; then
      printf '%s\n' "$line" | sudo tee -a "$file" >/dev/null || { echo "        ERROR: cannot append to $file"; continue; }
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
  echo "[*] waiting for Elasticsearch (logstash-*) ..."
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
          echo "  [$t] indexed, but GeoIP lookup FAILED for $(fuTESTIP "$t") (no GeoLite2 City entry) -> DataServer will skip it; pick another IP (--ips)"
          continue
        fi
        printf '%s' "$res" | grep -q '"geoip":{[^}]*"latitude"' || missing="$missing geoip.latitude"
        printf '%s' "$res" | grep -q '"geoip":{[^}]*"longitude"' || missing="$missing geoip.longitude"
        printf '%s' "$res" | grep -q '"geoip_ext":{[^}]*"latitude"' || missing="$missing geoip_ext.latitude"
        printf '%s' "$res" | grep -q '"t-pot_hostname"' || missing="$missing t-pot_hostname"
        printf '%s' "$res" | grep -q '"dest_port"' || missing="$missing dest_port"
        if [ -z "$missing" ]; then
          fuSETRES ES "$t" "PASS ($(( $(date +%s) - mySTART_EPOCH ))s)"
          echo "  [$t] indexed with geoip coordinates, geoip_ext, t-pot_hostname, dest_port"
        else
          fuSETRES ES "$t" "FAIL (missing:$missing)"
          echo "  [$t] indexed but missing:$missing"
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
    [ -n "$(fuGETRES ES "$t")" ] || { fuSETRES ES "$t" "FAIL (timeout)"; echo "  [$t] NOT indexed within ${myTIMEOUT}s"; }
  done
}

function fuWAITMAP {
  echo "[*] waiting for DataServer -> Redis -> map_web -> WebSocket (DataServer polls with a 10s indexing lag) ..."
  local deadline=$(( $(date +%s) + myTIMEOUT ))
  while :; do
    local pending=0
    for t in $myINJECTEDTYPES; do
      local ip sport
      ip="$(fuTESTIP "$t")"; sport="$(fuSRCPORT "$t")"
      if [ -z "$(fuGETRES REDIS "$t")" ]; then
        if grep "\"src_ip\": *\"$ip\"" "$myTMP/redis.log" | grep -q "\"src_port\": *$sport\b"; then
          fuSETRES REDIS "$t" "PASS ($(( $(date +%s) - mySTART_EPOCH ))s)"; echo "  [$t] seen on Redis pubsub"
        else pending=1; fi
      fi
      if [ -z "$(fuGETRES WS "$t")" ]; then
        if grep "\"src_ip\": *\"$ip\"" "$myTMP/ws.log" | grep -q "\"src_port\": *$sport\b"; then
          fuSETRES WS "$t" "PASS ($(( $(date +%s) - mySTART_EPOCH ))s)"; echo "  [$t] delivered over the WebSocket"
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
      printf '%-14s %-16s %-10s %-26s\n' "$t" "$(fuTESTIP "$t")" "skipped" "-"
      ;;
    esac
  done
  echo
  [ "$rc" = 0 ] && echo "RESULT: PASS — the full pipeline delivered every injected event to the Attack Map." \
                || echo "RESULT: FAIL — see the stages above (listener logs kept in $myTMP)."
  echo "Note: the test events remain in Elasticsearch/Kibana as ordinary events (src_ip$(for t in ${myTYPES//,/ }; do printf ' %s' "$(fuTESTIP "$t")"; done), src_port 51234-51237)."
  return $rc
}

function fuCLEANUP {
  [ -n "${myPID_REDIS:-}" ] && kill "$myPID_REDIS" 2>/dev/null
  [ -n "${myPID_WS:-}" ] && kill "$myPID_WS" 2>/dev/null
  wait 2>/dev/null
}

# Main
fuPARSEARGS "$@"
fuCHECKDEPS
fuDATAPATH
fuPICKIPS
mySTART_EPOCH=$(date +%s)
if [ "$myDRYRUN" = 1 ]; then
  echo "[*] DRY RUN — nothing is written. Events that would be appended:"
  fuINJECT
  echo
  echo "ES query per test IP (example):"
  fuESQUERY "$(printf '%s' "${myTYPES%%,*}")"; echo
  echo "Listeners: docker exec map_redis redis-cli SUBSCRIBE $myREDISCHANNEL | docker exec map_web python3 -c <ws client>"
  exit 0
fi
trap fuCLEANUP EXIT INT TERM
fuSTARTLISTENERS
fuINJECT
[ -n "$myINJECTEDTYPES" ] || { echo "ERROR: nothing injected."; exit 1; }
fuWAITES
fuWAITMAP
fuREPORT
