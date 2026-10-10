#!/usr/bin/env bash
# The ILM policy tpot from TPOT_ES_RETENTION, applied by entrypoint.sh on every start of a HIVE.
#
# T-Pot marks the policy it writes with _meta.tpot_retention. A policy whose marker does not
# match its delete phase (or that has none and is not the policy of an earlier release) was
# changed in Kibana and is left alone, so that nobody loses events they chose to keep. Only
# the delete phase and _meta are ever changed. The rule is the one of tpotctl/elastic.py,
# both pass docker/tpotinit/tests/ilm_cases.yml (docker/_tests/run.sh tpotinit_ilm).
# Sourced, it only defines the functions.

myILM_ES="${TPOT_ES_URL:-http://127.0.0.1:64298}"
myILM_WAIT="${TPOT_ES_RETENTION_WAIT:-600}"

myILM_JQ='
def min_age: if .phases.delete == null then null else (.phases.delete.min_age // "" | tostring) end;
def value_of:
  if (type != "object") or ((.phases | type) != "object") then ""
  elif .phases.delete == null then "off"
  else min_age as $m
    | if ($m | test("^[0-9]+d$")) then ($m | rtrimstr("d") | tonumber)
      elif ($m | test("^[0-9]+h$")) and (($m | rtrimstr("h") | tonumber) % 24 == 0) then (($m | rtrimstr("h") | tonumber) / 24)
      else 0 end
    | if . >= 1 and . <= 3650 then (. | floor | tostring) else "" end
  end;
def managed:
  if (._meta // {} | has("tpot_retention")) then
    (._meta.tpot_retention | tostring) as $t
    | if $t == "off" then .phases.delete == null else min_age == ($t + "d") end
  else ((._meta // {}).description == "T-Pot ILM policy with a retention of 30 days")
       and ((.phases // {} | keys) == ["delete", "hot"]) and min_age == "30d"
  end;
def build($v):
  (if . == null then {"phases": {"hot": {"min_age": "0ms", "actions": {}}}} else . end)
  | (if $v == "off" then del(.phases.delete)
     else .phases.delete = ((.phases.delete // {"actions": {"delete": {"delete_searchable_snapshot": true}}})
                            | .min_age = ($v + "d")) end)
  | ._meta = ((._meta // {}) + {"managed": true, "tpot_retention": $v,
                                 "description": "T-Pot ILM policy, its retention is TPOT_ES_RETENTION in .env"});
def decide($v; $force):
  if . == null then {"action": "create", "policy": build(if $v == "" then "30" else $v end), "current": "", "managed": true}
  else value_of as $cur | managed as $m
    | if $v == "" then {"action": "keep", "policy": null, "current": $cur, "managed": $m}
      elif ($m or $force) | not then {"action": "foreign", "policy": null, "current": $cur, "managed": $m}
      elif $m and $cur == $v and ((._meta.tpot_retention // "") | tostring) == $v then {"action": "keep", "policy": null, "current": $cur, "managed": $m}
      else {"action": "update", "policy": build($v), "current": $cur, "managed": $m} end
  end;
'

# stdin: the policy (or null), prints the TPOT_ES_RETENTION it stands for
function fuILM_VALUE_OF () {
  jq -r "${myILM_JQ} value_of"
}

# $1 = TPOT_ES_RETENTION, $2 = true to apply it to a foreign policy; stdin: the policy or null
function fuILM_DECIDE () {
  jq -c --arg v "$1" --argjson force "${2:-false}" "${myILM_JQ} decide(\$v; \$force)"
}

function fuILM_MAIN () {
  local myI myCODE myBODY myDECISION myACTION myCURRENT
  for (( myI = 0; myI < myILM_WAIT / 5; myI++ )); do
    curl -s -f -o /dev/null "${myILM_ES}/_cluster/health" && break
    sleep 5
  done
  myBODY="$(mktemp)"
  myCODE="$(curl -s -o "${myBODY}" -w '%{http_code}' "${myILM_ES}/_ilm/policy/tpot")"
  case "${myCODE}" in
    200) myDECISION="$(jq '.tpot.policy' "${myBODY}" | fuILM_DECIDE "${TPOT_ES_RETENTION:-}")" ;;
    404) myDECISION="$(echo null | fuILM_DECIDE "${TPOT_ES_RETENTION:-}")" ;;
    *)   echo "# Warning: Elasticsearch did not answer (HTTP ${myCODE}), the ILM policy tpot is not checked."
         rm -f "${myBODY}"; return 0 ;;
  esac
  rm -f "${myBODY}"
  myACTION="$(jq -r '.action' <<<"${myDECISION}")"
  myCURRENT="$(jq -r '.current' <<<"${myDECISION}")"
  case "${myACTION}" in
    create|update)
      if jq -c '{policy: .policy}' <<<"${myDECISION}" \
         | curl -s -f -o /dev/null -X PUT "${myILM_ES}/_ilm/policy/tpot" -H 'Content-Type: application/json' -d @-;
        then
          echo "# ILM policy tpot: ${myACTION}d, events are kept for $(jq -r '.policy._meta.tpot_retention' <<<"${myDECISION}" | sed 's/^off$/ever/; s/^\([0-9]*\)$/\1 days/')."
        else
          echo "# Warning: the ILM policy tpot could not be written."
      fi ;;
    keep)
      echo "# ILM policy tpot: as set (${myCURRENT:-not derivable})." ;;
    foreign)
      echo "# Warning: the ILM policy tpot was changed outside of T-Pot (retention: ${myCURRENT:-not in days}), T-Pot leaves it as it is."
      echo "#          TPOT_ES_RETENTION=${TPOT_ES_RETENTION} is not applied; 'tpot elastic retention' shows both and takes one over." ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  fuILM_MAIN
fi
