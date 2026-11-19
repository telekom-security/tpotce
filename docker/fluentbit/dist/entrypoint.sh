#!/bin/bash
# Fluent Bit of a SENSOR: the environment of fluent-bit.yaml, then Fluent Bit.

# MY_EXTIP, MY_INTIP, MY_HOSTNAME, MY_VERSION of tpotinit (updateip.sh); without the file the fields stay out
if [ -f /data/tpot/etc/compose/elk_environment ]; then
  set -o allexport
  # shellcheck disable=SC1091
  source /data/tpot/etc/compose/elk_environment
  set +o allexport
fi

# The start of this process for the heartbeat (a restart of the HIVE's view of the sensor)
TPOT_FLUENTBIT_START=$(date -u +%Y-%m-%dT%H:%M:%SZ) || {
  echo "Cannot determine the Fluent Bit start time." >&2
  exit 1
}

LS_SSL_VERIFICATION="${LS_SSL_VERIFICATION:-full}"
case "$LS_SSL_VERIFICATION" in
  full) FLB_TLS_VERIFY="on" ;;
  none) FLB_TLS_VERIFY="off" ;;
  *)
    echo "LS_SSL_VERIFICATION is '$LS_SSL_VERIFICATION'; it takes full (check the certificate of the HIVE) or none. Set it in ~/tpotce/.env." >&2
    exit 1
    ;;
esac

TPOT_HIVE_PORT="${TPOT_HIVE_PORT:-64294}"
FLB_STORAGE_LIMIT="${FLB_STORAGE_LIMIT:-512M}"
# tpotinit takes TPOT_SENSOR_BUFFER_MB=0512: 0512M becomes 512M, 0M and 000 stay refused below
if [[ "${FLB_STORAGE_LIMIT}" =~ ^0+([0-9][0-9]*[KMG]?)$ ]]; then
  FLB_STORAGE_LIMIT="${BASH_REMATCH[1]}"
fi
if [[ ! "${FLB_STORAGE_LIMIT}" =~ ^[1-9][0-9]*[KMG]?$ ]]; then
  echo "FLB_STORAGE_LIMIT is '${FLB_STORAGE_LIMIT}'; it takes a number with K, M or G (TPOT_SENSOR_BUFFER_MB in ~/tpotce/.env sets it)." >&2
  exit 1
fi

if [ -z "${TPOT_HIVE_IP:-}" ]; then
  echo "TPOT_HIVE_IP is empty; a SENSOR needs the address of its HIVE in ~/tpotce/.env (tpot sensors add on the HIVE sets it)." >&2
  exit 1
fi
if [ -z "${TPOT_HIVE_USER:-}" ]; then
  echo "TPOT_HIVE_USER is empty; a SENSOR needs its access to the HIVE in ~/tpotce/.env (tpot sensors add on the HIVE sets it)." >&2
  exit 1
fi

if [ -z "${MY_VERSION:-}" ]; then
  echo "Warning: sensor heartbeat version is unknown; update tpotinit to provide it in elk_environment." >&2
  MY_VERSION=""
fi

export TPOT_FLUENTBIT_START FLB_TLS_VERIFY TPOT_HIVE_IP TPOT_HIVE_PORT TPOT_HIVE_USER FLB_STORAGE_LIMIT MY_VERSION

# E2: Fluent Bit starts only once the HIVE takes this SENSOR's format (nginx /tpot-ingest answers 204 with
# X-TPot-Ingest 2 while its Logstash runs the sensors pipeline). Until then nothing is read or sent: the logs
# wait on the disk, /tmp/tpot-waiting keeps the container healthy, the probe runs again every interval
FLB_PROBE_INTERVAL="${FLB_PROBE_INTERVAL:-300}"
case "$FLB_PROBE_INTERVAL" in
  ''|*[!0-9]*|0)
    echo "FLB_PROBE_INTERVAL is '$FLB_PROBE_INTERVAL'; it takes the seconds between two probes of the HIVE (default 300)." >&2
    exit 1
    ;;
esac

myCHILD=""
fuSTOP () {
  [ -n "$myCHILD" ] && kill "$myCHILD" 2>/dev/null
  exit 143
}
# bash as PID 1 ignores a signal without a handler: docker stop must not wait for the probe or the sleep
trap fuSTOP TERM INT

fuPROBE () {
  local myTLS myCODE myMARK myERROR
  if [ "$LS_SSL_VERIFICATION" = "none" ]; then
    myTLS=(-k)
  else
    myTLS=(--cacert /data/hive.crt)
  fi
  touch /tmp/tpot-waiting
  while true; do
    rm -f /tmp/tpot-probe.headers /tmp/tpot-probe.err
    curl -sS --max-time 10 "${myTLS[@]}" -o /dev/null -D /tmp/tpot-probe.headers \
      "https://${TPOT_HIVE_IP}:${TPOT_HIVE_PORT}/tpot-ingest" 2>/tmp/tpot-probe.err &
    myCHILD=$!
    wait "$myCHILD"
    myCHILD=""
    myCODE=$(tr -d '\r' < /tmp/tpot-probe.headers 2>/dev/null | awk '/^HTTP\//{code=$2} END{print code}')
    myMARK=$(tr -d '\r' < /tmp/tpot-probe.headers 2>/dev/null \
      | awk 'tolower($0) ~ /^x-tpot-ingest:/{sub(/^[^:]*:[ \t]*/, ""); sub(/[ \t]+$/, ""); mark=$0} END{print mark}')
    if [ "$myCODE" = "204" ] && [ "$myMARK" = "2" ]; then
      rm -f /tmp/tpot-waiting /tmp/tpot-probe.headers /tmp/tpot-probe.err
      echo "The HIVE takes this SENSOR's format; Fluent Bit starts."
      return 0
    fi
    if [ "$myCODE" = "401" ]; then
      echo "The HIVE does not take this SENSOR's format yet (update the HIVE first); logs wait here." >&2
    elif [ "$myCODE" = "503" ] && [ -z "$myMARK" ]; then
      echo "The HIVE's Logstash pipeline is not ready for this SENSOR format; logs wait here." >&2
    elif [ "$myCODE" = "429" ]; then
      # C4: the HIVE limits the probes of all its SENSORs together
      echo "The HIVE is busy (HTTP 429), next probe in ${FLB_PROBE_INTERVAL} s." >&2
    elif [ -n "$myCODE" ] && [ "$myCODE" != "000" ]; then
      echo "The HIVE is not reachable (HTTP $myCODE)." >&2
    else
      # curl's own line: a TLS error adds lines of advice after it
      myERROR=$(awk '/^curl: /{line=$0} END{print line}' /tmp/tpot-probe.err 2>/dev/null)
      echo "The HIVE is not reachable (${myERROR:-no answer})." >&2
    fi
    sleep "${FLB_PROBE_INTERVAL}" &
    myCHILD=$!
    wait "$myCHILD"
    myCHILD=""
  done
}

fuPROBE
trap - TERM INT

exec fluent-bit -c /etc/fluent-bit/fluent-bit.yaml
