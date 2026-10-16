#!/usr/bin/env bash
# T-Pot's Kibana objects, started by entrypoint.sh in the background on every start of a HIVE with Kibana.
#
# It waits for Kibana, then runs kibana_objects.py apply (a copy of tpotctl/kibana_objects.py) once:
# the first import while it is pending (<data>/kibana_objects/pending, written by the first run before
# /data/uuid, so a start that ends before the import tries it again) or when Kibana holds none of
# T-Pot's objects, otherwise what TPOT_KIBANA_OBJECTS says (empty: keep). Every line it prints goes into
# the tpotinit log. A Kibana that does not answer in time is tried again on the next start.
# Sourced, it only defines the functions.

myKO_PY="/opt/tpot/bin/kibana_objects.py"
myKO_DATA="/data"
myKO_OBJECTS="/opt/tpot/etc/objects"
myKO_CHILD=""

# prints the seconds to wait for Kibana: TPOT_KIBANA_WAIT when it is whole seconds from 60 to 7200,
# else 1800 (a value that is set but does not fit is named on stderr)
function fuKO_WAIT_LIMIT () {
  local myVALUE="${TPOT_KIBANA_WAIT:-}"
  if [[ "${myVALUE}" =~ ^[1-9][0-9]{1,3}$ ]] && (( myVALUE >= 60 && myVALUE <= 7200 ));
    then
      echo "${myVALUE}"
      return 0
  fi
  if [[ -n "${myVALUE}" ]];
    then
      echo "# Warning: TPOT_KIBANA_WAIT=${myVALUE} is no number of seconds from 60 to 7200, Kibana is waited for 1800 s." >&2
  fi
  echo 1800
}

# prints the one URL Kibana is reached by: TPOT_KIBANA_URL, else the service name when tpotinit is no
# host container (MAC_WIN: it joins nginx_local), else the port Kibana publishes (as fuILM_CANDIDATES)
function fuKO_URL () {
  local myFILE="${COMPOSE:-/tmp/tpot/docker-compose.yml}" myMODE
  if [[ -n "${TPOT_KIBANA_URL:-}" ]];
    then
      echo "${TPOT_KIBANA_URL}"
    elif [[ -r "${myFILE}" ]] && myMODE="$(yq -r '.services.tpotinit.network_mode // ""' "${myFILE}" 2>/dev/null)" && [[ "${myMODE}" != "host" ]];
      then
        echo "http://kibana:5601"
      else
        echo "http://127.0.0.1:64296"
  fi
}

# $1 = the URL, $2 = seconds; 0 once /api/status answers 200, tried every 5 s
function fuKO_WAIT_FOR () {
  local myEND=$(( SECONDS + $2 ))
  while true; do
    if curl -s -f --max-time 5 -o /dev/null "$1/api/status";
      then
        return 0
    fi
    (( SECONDS + 5 <= myEND )) || return 1
    # in the background: a SIGTERM (tpotinit stops) ends the wait at once
    sleep 5 & wait $!
  done
}

function fuKO_MAIN () {
  local myMODE="${TPOT_KIBANA_OBJECTS:-keep}" myURL myWAIT myRC=0
  # the values of env.schema.yml: tpotinit validates .env before it starts this, a value that gets here
  # anyway is not worked with
  case "${myMODE}" in
    keep|add|update|overwrite) ;;
    *) echo "# Warning: TPOT_KIBANA_OBJECTS=${myMODE} is not keep, add, update or overwrite, the Kibana objects are not checked."
       return 0 ;;
  esac
  myURL="$(fuKO_URL)"
  myWAIT="$(fuKO_WAIT_LIMIT)"
  if ! fuKO_WAIT_FOR "${myURL}" "${myWAIT}";
    then
      echo "# Warning: Kibana did not answer at ${myURL} within ${myWAIT} s, the Kibana objects are checked on the next start."
      return 0
  fi
  # in the background as well: a SIGTERM ends the run at once, its files are written whole or not at all.
  # What it makes in <data>/kibana_objects gets the owner of <data> from the module (as root), at once.
  python3 "${myKO_PY}" apply --mode "${myMODE}" --data "${myKO_DATA}" --objects "${myKO_OBJECTS}" \
          --exports "${myKO_DATA}/kibana_objects/exports" --kibana "${myURL}" &
  myKO_CHILD=$!
  wait "${myKO_CHILD}" || myRC=$?
  myKO_CHILD=""
  return "${myRC}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  # tpotinit stops (entrypoint.sh cleanup): the wait or the run ends here, the next start goes on
  trap 'kill $(jobs -p) 2>/dev/null; [[ -z "${myKO_CHILD}" ]] || wait "${myKO_CHILD}" 2>/dev/null
        echo "# tpotinit stops, the Kibana objects are checked on the next start."; exit 143' TERM
  fuKO_MAIN
fi
