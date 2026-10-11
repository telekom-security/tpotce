#!/usr/bin/env bash
# The ILM policy tpot from TPOT_ES_RETENTION, applied by entrypoint.sh on every start of a HIVE.
#
# T-Pot marks the policy it writes with _meta.tpot_retention. A policy whose marker does not
# match its delete phase (or that has none and is not the policy of an earlier release) was
# changed in Kibana and is left alone, so that nobody loses events they chose to keep - unless
# it keeps them as long as TPOT_ES_RETENTION says, then T-Pot adopts it (only the marker is new).
# Only the delete phase and _meta are ever changed. tpotinit never writes a shorter retention than
# the policy has (nor one that deletes the events of an existing index when it creates the policy):
# a shorter value waits ("pending") for the T-Pot Manager, which asks first. The rule is the one of tpotctl/elastic.py,
# both pass docker/tpotinit/tests/ilm_cases.yml (docker/_tests/run.sh tpotinit_ilm).
# Sourced, it only defines the functions.

# Where Elasticsearch answers: fuILM_CANDIDATES (TPOT_ES_URL, else the port it publishes on the host
# when tpotinit runs with the host network, else its service name: MAC_WIN, tpotinit joins
# nginx_local), within myILM_WAIT seconds of _cluster/health (fuILM_FIND_ES).
myILM_ES=""
myILM_WAIT="${TPOT_ES_RETENTION_WAIT:-600}"

myILM_JQ='
def min_age: if .phases.delete == null then null else (.phases.delete.min_age // "" | tostring) end;
def deletes: ((.phases.delete | type) == "object") and ((.phases.delete.actions | type) == "object")
             and (.phases.delete.actions | has("delete"));
def days($x): if $x == "" or $x == "off" then 1e18 else ($x | tonumber) end;
def shortens($old; $new): ($new != "") and (days($new) < days($old));
def value_of:
  if (type != "object") or ((.phases | type) != "object") then ""
  elif .phases.delete == null then "off"
  elif (deletes | not) then ""
  else min_age as $m
    | if ($m | test("^[0-9]+d$")) then ($m | rtrimstr("d") | tonumber)
      elif ($m | test("^[0-9]+h$")) and (($m | rtrimstr("h") | tonumber) % 24 == 0) then (($m | rtrimstr("h") | tonumber) / 24)
      else 0 end
    | if . >= 1 and . <= 3650 then (. | floor | tostring) else "" end
  end;
def managed:
  if (._meta // {} | has("tpot_retention")) then
    (._meta.tpot_retention | tostring) as $t
    | if $t == "off" then .phases.delete == null else (min_age == ($t + "d") and deletes) end
  else ((._meta // {}).description == "T-Pot ILM policy with a retention of 30 days")
       and ((.phases // {} | keys) == ["delete", "hot"]) and min_age == "30d" and deletes
  end;
def build($v):
  (if . == null then {"phases": {"hot": {"min_age": "0ms", "actions": {}}}} else . end)
  | (if $v == "off" then del(.phases.delete)
     else .phases.delete = ((if (.phases.delete | type) == "object" then .phases.delete else {} end)
          | .actions = ((if (.actions | type) == "object" then .actions else {} end)
                        | if has("delete") then . else . + {"delete": {"delete_searchable_snapshot": true}} end)
          | .min_age = ($v + "d")) end)
  | ._meta = ((._meta // {}) + {"managed": true, "tpot_retention": $v,
                                 "description": "T-Pot ILM policy, its retention is TPOT_ES_RETENTION in .env"});
def decide($v; $force; $shorten; $oldest; $now):
  if . == null then
    (if $v == "" then "off" else $v end) as $w
    | if ($w != "off") and ($shorten | not) and ($oldest != null) and ($oldest < ($now - ($w | tonumber) * 86400))
      then {"action": "create", "policy": build("off"), "current": "", "managed": true, "held": $w}
      else {"action": "create", "policy": build($w), "current": "", "managed": true, "held": ""} end
  else value_of as $cur | managed as $m
    | if $v == "" then {"action": "keep", "policy": null, "current": $cur, "managed": $m, "held": ""}
      elif ($m or $force or ($cur != "" and $cur == $v)) | not then {"action": "foreign", "policy": null, "current": $cur, "managed": $m, "held": ""}
      elif $m and $cur == $v and ((._meta.tpot_retention // "") | tostring) == $v then {"action": "keep", "policy": null, "current": $cur, "managed": $m, "held": ""}
      elif ($shorten | not) and shortens(if $cur == "" then "off" else $cur end; $v) then {"action": "pending", "policy": null, "current": $cur, "managed": $m, "held": $v}
      else {"action": "update", "policy": build($v), "current": $cur, "managed": $m, "held": ""} end
  end;
'

# stdin: the policy (or null), prints the TPOT_ES_RETENTION it stands for
function fuILM_VALUE_OF () {
  jq -r "${myILM_JQ} value_of"
}

# $1 = TPOT_ES_RETENTION, $2 = true to apply it to a foreign policy (--force),
# $3 = true when the user confirmed a shorter value (tpotinit never sets it),
# $4 = epoch seconds of the oldest index (empty: no index), $5 = now (default: the clock);
# stdin: the policy or null. Prints {action, policy, current, managed, held}
function fuILM_DECIDE () {
  jq -c --arg v "$1" --argjson force "${2:-false}" --argjson shorten "${3:-false}" \
        --argjson oldest "${4:-null}" --argjson now "${5:-$(date +%s)}" \
        "${myILM_JQ} decide(\$v; \$force; \$shorten; \$oldest; \$now)"
}

# prints the one URL Elasticsearch is reached by: TPOT_ES_URL, else the service name when tpotinit
# is no host container (MAC_WIN: it joins nginx_local), else the port Elasticsearch publishes
function fuILM_CANDIDATES () {
  local myFILE="${COMPOSE:-/tmp/tpot/docker-compose.yml}" myMODE
  if [[ -n "${TPOT_ES_URL:-}" ]];
    then
      echo "${TPOT_ES_URL}"
    elif [[ -r "${myFILE}" ]] && myMODE="$(yq -r '.services.tpotinit.network_mode // ""' "${myFILE}" 2>/dev/null)" && [[ "${myMODE}" != "host" ]];
      then
        echo "http://elasticsearch:9200"
      else
        echo "http://127.0.0.1:64298"
  fi
}

# sets myILM_ES to the first candidate that answers, tries every 5 s for myILM_WAIT seconds
function fuILM_FIND_ES () {
  local myURL myEND=$(( SECONDS + myILM_WAIT ))
  local myCANDIDATES
  myCANDIDATES="$(fuILM_CANDIDATES)"
  while true; do
    for myURL in ${myCANDIDATES}; do
      if curl -s -f --max-time 5 -o /dev/null "${myURL}/_cluster/health";
        then
          myILM_ES="${myURL}"
          return 0
      fi
    done
    (( SECONDS + 5 <= myEND )) || return 1
    sleep 5
  done
}

# prints the creation date (epoch seconds) of the oldest logstash-* index: nothing when there is none,
# 0 when the list does not come (unknown counts as older events, so no value is applied that deletes them)
function fuILM_OLDEST () {
  local myLIST myOLDEST
  if ! myLIST="$(curl -s -f --max-time 10 "${myILM_ES}/_cat/indices/logstash-*?format=json&h=creation.date" 2>/dev/null)" \
     || ! myOLDEST="$(jq -r 'if type != "array" then error("no list") else [.[]."creation.date" | tonumber] | min // empty | . / 1000 | floor end' <<<"${myLIST}" 2>/dev/null)";
    then
      echo 0
      return 0
  fi
  echo "${myOLDEST}"
}

function fuILM_MAIN () {
  local myCODE myBODY myDECISION myACTION myCURRENT myHELD myOLDEST
  if ! fuILM_FIND_ES;
    then
      echo "# Warning: Elasticsearch did not answer within ${myILM_WAIT} s, the ILM policy tpot is not checked."
      return 0
  fi
  myBODY="$(mktemp)"
  myCODE="$(curl -s --max-time 10 -o "${myBODY}" -w '%{http_code}' "${myILM_ES}/_ilm/policy/tpot")"
  case "${myCODE}" in
    200) if ! jq -e '.tpot.policy | type == "object"' "${myBODY}" >/dev/null 2>&1;
           then
             echo "# Warning: Elasticsearch answered without the ILM policy tpot, it is not checked."
             rm -f "${myBODY}"; return 0
         fi
         myDECISION="$(jq '.tpot.policy' "${myBODY}" | fuILM_DECIDE "${TPOT_ES_RETENTION:-}")" ;;
    404) myOLDEST=""
         # only a value that deletes needs to know how old the events are
         if [[ -n "${TPOT_ES_RETENTION:-}" && "${TPOT_ES_RETENTION}" != "off" ]];
           then
             myOLDEST="$(fuILM_OLDEST)"
         fi
         myDECISION="$(echo null | fuILM_DECIDE "${TPOT_ES_RETENTION:-}" false false "${myOLDEST}")" ;;
    *)   echo "# Warning: Elasticsearch did not answer (HTTP ${myCODE}), the ILM policy tpot is not checked."
         rm -f "${myBODY}"; return 0 ;;
  esac
  rm -f "${myBODY}"
  myACTION="$(jq -r '.action' <<<"${myDECISION}")"
  myCURRENT="$(jq -r '.current' <<<"${myDECISION}")"
  myHELD="$(jq -r '.held // ""' <<<"${myDECISION}")"
  case "${myACTION}" in
    create|update|keep|foreign|pending) ;;
    *) echo "# Warning: the ILM policy tpot could not be checked (no decision)."
       return 0 ;;
  esac
  case "${myACTION}" in
    create|update)
      if jq -c '{policy: .policy}' <<<"${myDECISION}" \
         | curl -s -f --max-time 10 -o /dev/null -X PUT "${myILM_ES}/_ilm/policy/tpot" -H 'Content-Type: application/json' -d @-;
        then
          if [[ -n "${myHELD}" ]];
            then
              echo "# ILM policy tpot: created, events are kept forever: TPOT_ES_RETENTION=${myHELD} would delete events older than ${myHELD} days right away."
              echo "#          T-Pot does not delete events on its own, apply it with 'tpot elastic retention --force' (it asks first)."
            else
              echo "# ILM policy tpot: ${myACTION}d, events are kept $(jq -r '.policy._meta.tpot_retention' <<<"${myDECISION}" | sed 's/^off$/forever/; s/^\([0-9]*\)$/for \1 days/')."
          fi
        else
          echo "# Warning: the ILM policy tpot could not be written."
      fi ;;
    keep)
      echo "# ILM policy tpot: as set (${myCURRENT:-not derivable})." ;;
    pending)
      case "${myCURRENT}" in
        ""|off) myCURRENT="forever" ;;
        *)      myCURRENT="${myCURRENT} days" ;;
      esac
      echo "# TPOT_ES_RETENTION=${myHELD} is shorter than the ILM policy tpot (${myCURRENT}). T-Pot does not delete events on its own:"
      echo "#          apply it with 'tpot elastic retention --force' (it asks first), or keep the policy with 'tpot elastic retention --adopt'." ;;
    foreign)
      echo "# Warning: the ILM policy tpot was changed outside of T-Pot (retention: ${myCURRENT:-not in days}), T-Pot leaves it as it is."
      echo "#          TPOT_ES_RETENTION=${TPOT_ES_RETENTION} is not applied; 'tpot elastic status' shows both,"
      echo "#          'tpot elastic retention --adopt' keeps the policy, 'tpot elastic retention --force' applies .env to it." ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  fuILM_MAIN
fi
