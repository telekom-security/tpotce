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

export TPOT_FLUENTBIT_START FLB_TLS_VERIFY TPOT_HIVE_IP TPOT_HIVE_PORT TPOT_HIVE_USER FLB_STORAGE_LIMIT MY_VERSION FLB_CA_FILE

# E2: Fluent Bit starts only once the HIVE takes this SENSOR's format (nginx /tpot-ingest answers 204 with
# X-TPot-Ingest 2 to the SENSOR's login while its Logstash runs the sensors pipeline). Until then nothing is
# read or sent: the logs wait on the disk, tpot-waiting keeps the container healthy, the probe runs again
# every interval
FLB_PROBE_INTERVAL="${FLB_PROBE_INTERVAL:-300}"
case "$FLB_PROBE_INTERVAL" in
  ''|*[!0-9]*|0)
    echo "FLB_PROBE_INTERVAL is '$FLB_PROBE_INTERVAL'; it takes the seconds between two probes of the HIVE (default 300)." >&2
    exit 1
    ;;
esac
FLB_PROBE_DIR="${FLB_PROBE_DIR:-/tmp}"
TPOT_HIVE_CRT="${TPOT_HIVE_CRT:-/data/hive.crt}"
FLB_SYSTEM_CA="${FLB_SYSTEM_CA:-/etc/ssl/certs/ca-certificates.crt}"
myWAITING="${FLB_PROBE_DIR}/tpot-waiting"
myHEADERS="${FLB_PROBE_DIR}/tpot-probe.headers"
myERRORS="${FLB_PROBE_DIR}/tpot-probe.err"
myAUTH="${FLB_PROBE_DIR}/tpot-probe.auth"
myVERDICT="${FLB_PROBE_DIR}/tpot-probe.tls"
myTRUST="${FLB_PROBE_DIR}/tpot-trust"

myCHILD=""
fuSTOP () {
  [ -n "$myCHILD" ] && kill "$myCHILD" 2>/dev/null
  rm -f "$myAUTH"
  exit 143
}
# bash as PID 1 ignores a signal without a handler: docker stop must not wait for the probe or the sleep
trap fuSTOP TERM INT

# Waits the seconds given in the background: the TERM trap stops it at once
fuREST () {
  sleep "$1" &
  myCHILD=$!
  wait "$myCHILD"
  myCHILD=""
}

# The trust file of curl, openssl and Fluent Bit (tls.ca_file): hive.crt, pinned, when one of its certificates
# alone verifies itself (the HIVE's own certificate, or the root of its CA appended to it; an expired one too,
# the verdict names that), else the system store (a certificate of a public CA, or no hive.crt)
fuTRUST () {
  local myPEM
  FLB_CA_FILE="$FLB_SYSTEM_CA"
  rm -f "$myTRUST".*.pem
  [ -s "$TPOT_HIVE_CRT" ] || return 0
  awk -v out="$myTRUST" '/-----BEGIN CERTIFICATE-----/{n++; file=out "." n ".pem"}
    file != "" {print > file}
    /-----END CERTIFICATE-----/{if (file != "") close(file); file=""}' "$TPOT_HIVE_CRT" 2>/dev/null
  for myPEM in "$myTRUST".*.pem; do
    [ -f "$myPEM" ] || continue
    # a certificate as its own whole chain: a leaf of a CA fails here as it does in Fluent Bit (no partial chain)
    if openssl verify -no-CApath -no-CAstore -no_check_time -CAfile "$myPEM" "$myPEM" >/dev/null 2>&1; then
      FLB_CA_FILE="$TPOT_HIVE_CRT"
      break
    fi
  done
  rm -f "$myTRUST".*.pem
}

# The TLS verdict of openssl on the trust file (verification on): a certificate it does not verify gets its
# reason and the way out, no curl; without a verdict (no answer, a refused connection) curl names what it finds
fuVERDICT () {
  local myCHECK myREASON
  if [[ "$TPOT_HIVE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    myCHECK=(-verify_ip "$TPOT_HIVE_IP")
  else
    myCHECK=(-servername "$TPOT_HIVE_IP" -verify_hostname "$TPOT_HIVE_IP")
  fi
  rm -f "$myVERDICT"
  timeout 10 openssl s_client -no-CApath -no-CAstore -CAfile "$FLB_CA_FILE" -verify_return_error \
    -connect "${TPOT_HIVE_IP}:${TPOT_HIVE_PORT}" "${myCHECK[@]}" </dev/null >"$myVERDICT" 2>&1 &
  myCHILD=$!
  wait "$myCHILD"
  myCHILD=""
  myREASON=$(tr -d '\r' < "$myVERDICT" 2>/dev/null \
    | awk '/verify error:num=[0-9]+:/{sub(/.*verify error:num=[0-9]+:/, ""); print; exit}')
  rm -f "$myVERDICT"
  [ -n "$myREASON" ] || return 1
  if [ "$FLB_CA_FILE" = "$TPOT_HIVE_CRT" ]; then
    echo "The HIVE's certificate is not trusted (${myREASON}); on the HIVE, tpot sensors cert --distribute copies its certificate here (after --renew if it expired)." >&2
  else
    echo "The HIVE's certificate is not trusted (${myREASON}); with a certificate of your own CA, append the root of that CA to nginx.crt on the HIVE, then run tpot sensors cert --distribute there." >&2
  fi
}

# One request to /tpot-ingest with the curl options given; sets myCODE and myMARK of fuPROBE. Only the
# HIVE's own headers count: an HTTPS proxy's CONNECT reply stays out of the dump (curl >= 7.54)
fuASK () {
  rm -f "$myHEADERS" "$myERRORS"
  curl -sS --max-time 10 --suppress-connect-headers "${myTLS[@]}" "$@" -o /dev/null -D "$myHEADERS" \
    "https://${TPOT_HIVE_IP}:${TPOT_HIVE_PORT}/tpot-ingest" 2>"$myERRORS" &
  myCHILD=$!
  wait "$myCHILD"
  myCHILD=""
  myCODE=$(tr -d '\r' < "$myHEADERS" 2>/dev/null | awk '/^HTTP\//{code=$2} END{print code}')
  myMARK=$(tr -d '\r' < "$myHEADERS" 2>/dev/null \
    | awk 'tolower($0) ~ /^x-tpot-ingest:/{sub(/^[^:]*:[ \t]*/, ""); sub(/[ \t]+$/, ""); mark=$0} END{print mark}')
}

# I1: stage 1 asks without the login; only a HIVE of this release answers it with 401 and X-TPot-Ingest
# login, and only that HIVE gets the login in stage 2 (an old HIVE never sees it). Both stages run every
# round: a HIVE may be installed again with an older release
fuPROBE () {
  local myTLS myCODE myMARK myERROR mySTAGE myWAIT
  touch "$myWAITING"
  # the login only in a file of the SENSOR (printf is a builtin): never in the argv of curl
  if ! { rm -f "$myAUTH" && (umask 077 && set -C && printf 'Authorization: Basic %s\n' "$TPOT_HIVE_USER" > "$myAUTH"); }; then
    echo "Cannot write the login for the probe of the HIVE to ${myAUTH}." >&2
    exit 1
  fi
  while true; do
    myWAIT="$FLB_PROBE_INTERVAL"
    # every round: the certificate of the HIVE may be distributed again meanwhile
    fuTRUST
    if [ "$LS_SSL_VERIFICATION" = "none" ]; then
      myTLS=(-k)
    else
      myTLS=(--cacert "$FLB_CA_FILE")
      if fuVERDICT; then
        fuREST "$myWAIT"
        continue
      fi
    fi
    mySTAGE=1
    fuASK
    if [ "$myCODE" = "401" ] && [ "$myMARK" = "login" ]; then
      mySTAGE=2
      fuASK -H @"$myAUTH"
      if [ "$myCODE" = "204" ] && [ "$myMARK" = "2" ]; then
        rm -f "$myWAITING" "$myHEADERS" "$myERRORS" "$myAUTH"
        echo "The HIVE takes this SENSOR's format; Fluent Bit starts."
        return 0
      fi
    fi
    if [ "$myCODE" = "401" ] && [ "$myMARK" = "login" ] && [ "$mySTAGE" = "2" ]; then
      echo "The HIVE does not accept this SENSOR's login (TPOT_HIVE_USER); tpot sensors list on the HIVE shows its access; logs wait here." >&2
    elif [ "$myCODE" = "401" ] && [ "$mySTAGE" = "1" ]; then
      echo "The HIVE does not take this SENSOR's format yet (update the HIVE first); logs wait here." >&2
    elif [ "$myCODE" = "503" ] && [ -z "$myMARK" ]; then
      echo "The HIVE's Logstash pipeline is not ready for this SENSOR format; logs wait here." >&2
    elif [ "$myCODE" = "429" ]; then
      # C4: the HIVE limits the probes per address; ask again soon, at random so that the SENSORs behind one
      # address spread out
      myWAIT=$((15 + RANDOM % 31))
      [ "$myWAIT" -gt "$FLB_PROBE_INTERVAL" ] && myWAIT="$FLB_PROBE_INTERVAL"
      echo "The HIVE is busy (HTTP 429), next probe in ${myWAIT} s." >&2
    elif [ -n "$myCODE" ] && [ "$myCODE" != "000" ]; then
      echo "The HIVE is not reachable (HTTP ${myCODE})." >&2
    else
      # curl's own line: a TLS error adds lines of advice after it
      myERROR=$(awk '/^curl: /{line=$0} END{print line}' "$myERRORS" 2>/dev/null)
      echo "The HIVE is not reachable (${myERROR:-no answer})." >&2
    fi
    fuREST "$myWAIT"
  done
}

fuPROBE
trap - TERM INT

exec fluent-bit -c /etc/fluent-bit/fluent-bit.yaml
