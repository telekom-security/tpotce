#!/usr/bin/env bash
# T-Pot .env validation against /opt/tpot/etc/env.schema.yml.
#
# Sourced by entrypoint.sh, which calls fuENV_VALIDATE and fuENV_REPORT. Run on its
# own it validates the environment it is started with (docker run --env-file .env),
# that is how docker/_tests/tests/tpotinit_env.sh tests it:
#   env_validate.sh [schema] [compose file]
# All errors are collected and reported at once, settings of a service are only
# checked if the active compose file runs it.

myENV_SCHEMA="${myENV_SCHEMA:-/opt/tpot/etc/env.schema.yml}"
COMPOSE="${COMPOSE:-/tmp/tpot/docker-compose.yml}"
myENV_ERRORS=()
myENV_WARNINGS=0
declare -A myENV_DEFAULT

fuENV_ERROR() {
    myENV_ERRORS+=("$1: $2")
}

fuENV_WARN() {
    echo "# Warning: $1: $2"
    myENV_WARNINGS=$(( myENV_WARNINGS + 1 ))
}

# Value of a variable by its name, empty if it is not set at all
fuVAL() {
    printf '%s' "${!1-}"
}

# Function to check if a variable is set, not empty
check_var() {
    if [[ -z "$(fuVAL "$1")" ]];
      then
        fuENV_ERROR "$1" "is not set or empty."
        return 1
    fi
}

# Function to check for potentially unsafe characters in most variables
check_safety() {
    if [[ "$(fuVAL "$1")" =~ [^a-zA-Z0-9_/.:-] ]];
      then
        fuENV_ERROR "$1" "contains unsafe characters."
        return 1
    fi
}

# Exact match against the allowed values, $1 = variable, $2... = allowed values
fuCHOICE() {
    local myVAR="$1" myVALUE myALLOWED
    shift
    myVALUE="$(fuVAL "${myVAR}")"
    for myALLOWED in "$@";
      do
        [[ "${myVALUE}" == "${myALLOWED}" ]] && return 0
    done
    fuENV_ERROR "${myVAR}" "invalid value \"${myVALUE}\", allowed: $*."
    return 1
}

# True if the active compose file runs one of the services
fuCOMPOSE_HAS() {
    local myPATTERN
    myPATTERN="$(IFS='|'; echo "$*")"
    grep -qE "^  (${myPATTERN}):" "${COMPOSE}" 2>/dev/null
}

# Every entry has to be base64 of "name:secret", $1 = variable, $2 = htpasswd (the
# secret is a htpasswd hash, as genuser.sh creates it) | basic (a plain password,
# as for the basic auth of a SENSOR). Only the names are shown.
fuCREDENTIALS() {
    local myVAR="$1" myMODE="$2" myENTRY myLINE
    local myBASE64='^([A-Za-z0-9+/]{4})*([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$'
    local myHTPASSWD='^([^:[:space:]]+):(\$apr1\$|\$2[aby]\$|\$5\$|\$6\$|\{SHA\})'
    local myBASIC='^([^:[:space:]]+):.+'
    for myENTRY in $(fuVAL "${myVAR}");
      do
        if ! [[ ${myENTRY} =~ ${myBASE64} ]];
          then
            fuENV_ERROR "${myVAR}" "an entry is not a valid base64 string."
            continue
        fi
        myLINE="$(echo -n "${myENTRY}" | base64 -d 2>/dev/null | tr -d '\n')"
        if [ "${myMODE}" == "htpasswd" ] && [[ ${myLINE} =~ ${myHTPASSWD} ]];
          then
            echo "Found valid user in ${myVAR}: ${BASH_REMATCH[1]}"
          elif [ "${myMODE}" == "basic" ] && [[ ${myLINE} =~ ${myBASIC} ]];
            then
              echo "Found valid user in ${myVAR}: ${BASH_REMATCH[1]}"
          elif [ "${myMODE}" == "htpasswd" ];
            then
              fuENV_ERROR "${myVAR}" "an entry is not a htpasswd user (name:hash), please create it with genuser.sh."
          else
            fuENV_ERROR "${myVAR}" "an entry is not base64 of name:password."
        fi
    done
}

# IPv4, IPv6 or a domain name
fuHOST() {
    local myVAR="$1" myCHECK
    myCHECK="$(fuVAL "$1")"
    local myIPV4='^(([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])\.){3}([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])$'
    local myIPV6='^[0-9A-Fa-f]{0,4}(:[0-9A-Fa-f]{0,4}){2,7}$'
    local myDOMAIN='^(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9])$'
    if [[ ${myCHECK} =~ ${myIPV4} ]] || [[ ${myCHECK} =~ ${myIPV6} ]] || [[ ${myCHECK} =~ ${myDOMAIN} ]];
      then
        echo "${myVAR}: ${myCHECK} is a valid IP address or domain name."
      else
        fuENV_ERROR "${myVAR}" "\"${myCHECK}\" is not a valid IPv4 / IPv6 address or domain name."
    fi
}

# Empty or an http(s) URL
fuURL() {
    local myVALUE
    myVALUE="$(fuVAL "$1")"
    if [ -n "${myVALUE}" ] && [[ ! ${myVALUE} =~ ^https?://[^[:space:]]+$ ]];
      then
        fuENV_ERROR "$1" "\"${myVALUE}\" is not an http(s) URL."
    fi
}

# A message of the schema with {value} / {when} filled in
fuFILL() {   # $1 = message, $2 = value, $3 = when
    local myTEXT="$1"
    myTEXT="${myTEXT//\{value\}/$2}"
    myTEXT="${myTEXT//\{when\}/$3}"
    printf '%s' "${myTEXT}"
}

# One key of the schema, the arguments are the fields fuENV_VALIDATE reads. @sh of
# yq gives nothing for an empty string, so every field comes with an x in front.
fuENV_RULE() {
    set -- "${@#x}"
    local myKEY="$1" myTYPE="$2" myREQUIRED="$3" mySAFE="$4" mySCOPE="$5" mySERVICES="$6"
    local myVALUES="$7" myOPTIONAL="$8" myMIN="$9" myMAX="${10}" myPATTERN="${11}" myMESSAGE="${12}"
    local myDEFAULT="${13}" myINVALID="${14}" myRW_KEY="${15}" myRW_VALUES="${16}" myRW_MESSAGE="${17}"
    local myWARN_EMPTY="${18}" myWW_KEY="${19}" myWW_VALUES="${20}" myWW_MESSAGE="${21}"
    local myVALUE myWHEN myIFACE
    myVALUE="$(fuVAL "${myKEY}")"
    [ -n "${mySCOPE}" ] && [ "${mySCOPE}" != "${TPOT_TYPE}" ] && return 0
    # shellcheck disable=SC2086
    [ -n "${mySERVICES}" ] && ! fuCOMPOSE_HAS ${mySERVICES} && return 0
    if [ "${myREQUIRED}" == "true" ] && [ -z "${myVALUE}" ];
      then
        fuENV_ERROR "${myKEY}" "is not set or empty."
        return 0
    fi
    if [ -n "${myRW_KEY}" ] && [ -z "${myVALUE}" ];
      then
        myWHEN="$(fuVAL "${myRW_KEY}")"
        myWHEN="${myWHEN:-${myENV_DEFAULT[${myRW_KEY}]}}"
        if [[ " ${myRW_VALUES} " == *" ${myWHEN} "* ]];
          then
            fuENV_ERROR "${myKEY}" "$(fuFILL "${myRW_MESSAGE}" "" "${myWHEN}")"
            return 0
        fi
    fi
    if [ -n "${myWW_KEY}" ] && [ -z "${myVALUE}" ];
      then
        myWHEN="$(fuVAL "${myWW_KEY}")"
        myWHEN="${myWHEN:-${myENV_DEFAULT[${myWW_KEY}]}}"
        [[ " ${myWW_VALUES} " == *" ${myWHEN} "* ]] && fuENV_WARN "${myKEY}" "$(fuFILL "${myWW_MESSAGE}" "" "${myWHEN}")"
    fi
    if [ -z "${myVALUE}" ];
      then
        [ -n "${myWARN_EMPTY}" ] && fuENV_WARN "${myKEY}" "${myWARN_EMPTY}"
        if [ "${myTYPE}" == "enum" ] && [ "${myOPTIONAL}" != "true" ];
          then
            # shellcheck disable=SC2086
            fuCHOICE "${myKEY}" ${myVALUES}
        fi
        if [ "${myINVALID}" == "default" ];
          then
            fuENV_WARN "${myKEY}" "${myMESSAGE}"
            printf -v "${myKEY}" '%s' "${myDEFAULT}"
        fi
        return 0
    fi
    if [ "${mySAFE}" == "true" ] && ! check_safety "${myKEY}";
      then
        return 0
    fi
    case "${myTYPE}" in
      enum)
        # shellcheck disable=SC2086
        fuCHOICE "${myKEY}" ${myVALUES}
        ;;
      int)
        if [[ ! "${myVALUE}" =~ ^[0-9]+$ ]] || (( 10#${myVALUE} < myMIN )) || (( 10#${myVALUE} > myMAX ));
          then
            if [ "${myINVALID}" == "default" ];
              then
                fuENV_WARN "${myKEY}" "${myMESSAGE}"
                printf -v "${myKEY}" '%s' "${myDEFAULT}"
              else
                fuENV_ERROR "${myKEY}" "$(fuFILL "${myMESSAGE}" "${myVALUE}")"
            fi
        fi
        ;;
      number)
        if [[ ! "${myVALUE}" =~ ^[0-9]+(\.[0-9]+)?$ ]] || \
           awk -v myV="${myVALUE}" -v myLO="${myMIN}" -v myHI="${myMAX}" 'BEGIN { exit !(myV < myLO || myV > myHI) }';
          then
            fuENV_ERROR "${myKEY}" "$(fuFILL "${myMESSAGE}" "${myVALUE}")"
        fi
        ;;
      regex)
        if [[ ! "${myVALUE}" =~ ${myPATTERN} ]];
          then
            fuENV_ERROR "${myKEY}" "$(fuFILL "${myMESSAGE}" "${myVALUE}")"
        fi
        ;;
      host)
        fuHOST "${myKEY}"
        ;;
      url)
        fuURL "${myKEY}"
        ;;
      timezone)
        if [[ "${myVALUE}" == *..* ]] || [ ! -f "/usr/share/zoneinfo/${myVALUE}" ];
          then
            fuENV_ERROR "${myKEY}" "\"${myVALUE}\" is not a known time zone (i.e. UTC, Europe/Berlin)."
        fi
        ;;
      interface)
        if [[ ! "${myVALUE}" =~ ^[A-Za-z0-9_.:@-]{1,15}$ ]];
          then
            fuENV_ERROR "${myKEY}" "is not a valid interface name."
          elif ! ip link show dev "${myVALUE}" > /dev/null 2>&1;
            then
              myIFACE="$(ip -o link show | awk -F': ' '{ sub(/@.*/, "", $2); print $2 }' | grep -vE '^(lo|docker|br-|veth)' | tr '\n' ' ')"
              fuENV_ERROR "${myKEY}" "interface \"${myVALUE}\" does not exist on this host, available: ${myIFACE}"
        fi
        ;;
      htpasswd_list)
        fuCREDENTIALS "${myKEY}" htpasswd
        ;;
      basic_cred)
        fuCREDENTIALS "${myKEY}" basic
        ;;
    esac
    return 0
}

# Check every key of the schema, in its order
fuENV_VALIDATE() {
    local myRULES myDEFAULTS
    if [ ! -f "${myENV_SCHEMA}" ] || ! command -v yq > /dev/null;
      then
        fuENV_ERROR "env.schema.yml" "${myENV_SCHEMA} or yq is missing, the .env config cannot be checked."
        return 0
    fi
    myDEFAULTS="$(yq -r '.keys | to_entries | .[] | "myENV_DEFAULT[" + (.key | @sh) + "]=" + ((.value.default // "") | tostring | @sh)' "${myENV_SCHEMA}")" || {
        fuENV_ERROR "env.schema.yml" "cannot be read."
        return 0
    }
    eval "${myDEFAULTS}"
    myRULES="$(yq -r '.keys | to_entries | .[] | "fuENV_RULE " + ([
        .key,
        .value.type // "text",
        (.value.required // false | tostring),
        (.value.safe // false | tostring),
        .value.scope // "",
        ((.value.services // []) | join(" ")),
        ((.value.values // []) | join(" ")),
        (.value.optional // false | tostring),
        (.value.min // "" | tostring),
        (.value.max // "" | tostring),
        .value.pattern // "",
        .value.message // "",
        (.value.default // "" | tostring),
        .value.on_invalid // "",
        .value.required_when.key // "",
        ((.value.required_when.values // []) | join(" ")),
        .value.required_message // "",
        .value.warn_empty // "",
        .value.warn_when.key // "",
        ((.value.warn_when.values // []) | join(" ")),
        .value.warn_message // ""
      ] | map("x" + . | @sh) | join(" "))' "${myENV_SCHEMA}")" || {
        fuENV_ERROR "env.schema.yml" "cannot be read."
        return 0
    }
    eval "${myRULES}"
}

# Print the collected errors, 1 if there are any
fuENV_REPORT() {
    local myERROR
    if [ "${#myENV_ERRORS[@]}" -gt 0 ];
      then
        for myERROR in "${myENV_ERRORS[@]}";
          do
            echo "# Error: ${myERROR}"
        done
        echo
        echo "# ${#myENV_ERRORS[@]} error(s) found. Please check T-Pot .env config."
        echo
        return 1
    fi
    echo "# All settings seem to be valid.$([ "${myENV_WARNINGS}" -gt 0 ] && echo " ${myENV_WARNINGS} warning(s), see above.")"
    return 0
}

if [ "${BASH_SOURCE[0]}" == "$0" ];
  then
    [ -n "$1" ] && myENV_SCHEMA="$1"
    [ -n "$2" ] && COMPOSE="$2"
    fuENV_VALIDATE
    fuENV_REPORT
    exit $?
fi
