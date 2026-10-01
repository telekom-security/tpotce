#!/usr/bin/env bash

COMPOSE="/tmp/tpot/docker-compose.yml"
exec > >(tee /data/tpotinit.log) 2>&1

# Function to handle SIGTERM
cleanup() {
  echo "# SIGTERM received, cleaning up ..."
  echo
  if [ "${TPOT_OSTYPE}" = "linux" ];
    then
      echo "## ... removing firewall rules."
      /opt/tpot/bin/rules.sh ${COMPOSE} unset
      echo
      if [ "${TPOT_BLACKHOLE}" == "ENABLED" ] && [ -f "/etc/blackhole/mass_scanner.txt" ];
        then
          echo "## ... removing Blackhole routes."
          /opt/tpot/bin/blackhole.sh del
          echo
      fi
  fi
  kill -TERM "$PID"
  rm -f /tmp/success
  echo "# Cleanup done."
  echo
}
trap cleanup SIGTERM

# The .env validation collects all errors and aborts once, after all checks
myENV_ERRORS=()
myENV_WARNINGS=0

fuENV_ERROR() {
    myENV_ERRORS+=("$1: $2")
}

fuENV_WARN() {
    echo "# Warning: $1: $2"
    myENV_WARNINGS=$(( myENV_WARNINGS + 1 ))
}

# Value of a variable by its name
fuVAL() {
    printf '%s' "${!1}"
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

# Function to validate if TPOT_PERSISTENCE_CYCLES is set and valid
validate_tpot_persistence_cycles() {
  # Check if the variable is unset, empty, not a number, or out of the valid range (1–999)
  if [[ -z "$TPOT_PERSISTENCE_CYCLES" ]] || 
     [[ ! "$TPOT_PERSISTENCE_CYCLES" =~ ^[0-9]+$ ]] || 
     (( TPOT_PERSISTENCE_CYCLES < 1 )) || 
     (( TPOT_PERSISTENCE_CYCLES > 999 )); then

    # Set to default value
    echo "WARNING! TPOT_PERSISTENCE_CYCLES is not set, invalid or out of bounds. Using default of 30 cycles."
    TPOT_PERSISTENCE_CYCLES=30
  fi
}

create_web_users() {
    echo
    echo "# Creating passwd files based on T-Pot .env config ..."
    # Clear / create the passwd files
    : > /data/nginx/conf/nginxpasswd
    : > /data/nginx/conf/lswebpasswd
    for i in ${WEB_USER};
      do
	    if [[ -n $i ]];
	      then
	        # Need to control newlines as they kept coming up for some reason
	        echo -n "$i" | base64 -d -w0 | tr -d '\n' >> /data/nginx/conf/nginxpasswd
	        echo >> /data/nginx/conf/nginxpasswd
	    fi
    done

    for i in ${LS_WEB_USER};
      do
        if [[ -n $i ]];
          then
            # Need to control newlines as they kept coming up for some reason
            echo -n "$i" | base64 -d -w0 | tr -d '\n' >> /data/nginx/conf/lswebpasswd
            echo >> /data/nginx/conf/lswebpasswd
          fi
    done
}

update_permissions() {
	echo
	echo "# Updating permissions ..."
	echo
	chown -R tpot:tpot /data
	chmod -R 770 /data
	chmod 774 -R /data/nginx/conf
	chmod 774 -R /data/nginx/cert
}

# Update permissions
update_permissions

# Check for compatible OSType
echo
echo "# Checking if OSType is set correctly."
echo
myOSTYPE=$(uname -a | grep -Eo "microsoft|linuxkit")
if [ "${myOSTYPE}" == "microsoft" ] && [ "${TPOT_OSTYPE}" != "win" ];
  then
    echo "# Docker Desktop for Windows detected, but TPOT_OSTYPE is not set to win."
    echo "# 1. You need to adjust the OSType in the T-Pot .env config."
    echo "# 2. You need to copy compose/mac_win.yml to ./docker-compose.yml."
    echo
    echo "# Aborting."
    echo
    sleep 1
    exit 1
fi

if [ "${myOSTYPE}" == "linuxkit" ] && [ "${TPOT_OSTYPE}" != "mac" ];
  then
    echo "# Docker Desktop for macOS detected, but TPOT_OSTYPE is not set to mac."
    echo "# 1. You need to adjust the OSType in the T-Pot .env config."
    echo "# 2. You need to copy compose/mac_win.yml to ./docker-compose.yml."
    echo
    echo "# Aborting."
    echo
    sleep 1
    exit 1
fi

if [ "${myOSTYPE}" == "" ] && [ "${TPOT_OSTYPE}" != "linux" ];
  then
    echo "# Docker Engine detected, but TPOT_OSTYPE is not set to linux."
    echo "# 1. You need to adjust the OSType in the T-Pot .env config."
    echo "# 2. You need to copy compose/standard.yml to ./docker-compose.yml."
    echo
    echo "# Aborting."
    echo
    sleep 1
    exit 1
fi

# Validate environment variables, all errors are collected and reported at once
echo
echo "# Validating the T-Pot .env config ..."
echo
for var in TPOT_ATTACKMAP_TEXT_TIMEZONE TPOT_REPO TPOT_VERSION TPOT_PULL_POLICY TPOT_OSTYPE;
  do
    check_var "$var" && check_safety "$var"
done
# Only the values the scripts and services actually act on, anything else was
# silently ignored before (i.e. TPOT_PERSISTENCE=true deleted the logs on every start)
fuCHOICE TPOT_TYPE HIVE SENSOR
fuCHOICE TPOT_PERSISTENCE on off
fuCHOICE TPOT_BLACKHOLE ENABLED DISABLED
fuCHOICE TPOT_ATTACKMAP_TEXT ENABLED DISABLED
if [ -n "${TPOT_ATTACKMAP_TEXT_TIMEZONE}" ] && \
   { [[ "${TPOT_ATTACKMAP_TEXT_TIMEZONE}" == *..* ]] || [ ! -f "/usr/share/zoneinfo/${TPOT_ATTACKMAP_TEXT_TIMEZONE}" ]; };
  then
    fuENV_ERROR TPOT_ATTACKMAP_TEXT_TIMEZONE "\"${TPOT_ATTACKMAP_TEXT_TIMEZONE}\" is not a known time zone (i.e. UTC, Europe/Berlin)."
fi
if [ -n "${TPOT_PULL_POLICY}" ] && [[ ! "${TPOT_PULL_POLICY}" =~ ^(always|missing|never|build|daily|weekly|every_[0-9a-z]+)$ ]];
  then
    fuENV_ERROR TPOT_PULL_POLICY "invalid value \"${TPOT_PULL_POLICY}\", allowed: always missing never build daily weekly every_<duration>."
fi

# Validate TPOT_PERSISTENCE_CYCLES
validate_tpot_persistence_cycles

if [ "${TPOT_TYPE}" == "HIVE" ];
  then
    check_var "WEB_USER" && fuCREDENTIALS WEB_USER htpasswd
    TPOT_HIVE_USER=""
    TPOT_HIVE_IP=""
    if [ "${LS_WEB_USER}" == "" ];
      then
        fuENV_WARN LS_WEB_USER "not set, T-Pots of type SENSOR will not be able to submit logs to this HIVE."
      else
        fuCREDENTIALS LS_WEB_USER htpasswd
    fi
fi
if [ "${TPOT_TYPE}" == "SENSOR" ];
  then
    check_var "TPOT_HIVE_USER" && fuCREDENTIALS TPOT_HIVE_USER basic
    check_var "TPOT_HIVE_IP" && fuHOST TPOT_HIVE_IP
    # Empty uses the default of Logstash (full)
    [ -n "${LS_SSL_VERIFICATION}" ] && fuCHOICE LS_SSL_VERIFICATION full none
    WEB_USER=""
fi

# Settings of services are only checked if the active compose file runs them
if fuCOMPOSE_HAS suricata p0f satori glutton && [ -n "${TPOT_CAPTURE_INTERFACE}" ];
  then
    if [[ ! "${TPOT_CAPTURE_INTERFACE}" =~ ^[A-Za-z0-9_.:@-]{1,15}$ ]];
      then
        fuENV_ERROR TPOT_CAPTURE_INTERFACE "is not a valid interface name."
      elif ! ip link show dev "${TPOT_CAPTURE_INTERFACE}" > /dev/null 2>&1;
        then
          fuENV_ERROR TPOT_CAPTURE_INTERFACE "interface \"${TPOT_CAPTURE_INTERFACE}\" does not exist on this host, available: $(ip -o link show | awk -F': ' '{ sub(/@.*/, "", $2); print $2 }' | grep -vE '^(lo|docker|br-|veth)' | tr '\n' ' ')"
    fi
fi
if fuCOMPOSE_HAS suricata;
  then
    if [ -n "${OINKCODE}" ] && [ "${OINKCODE}" != "OPEN" ] && [[ ! "${OINKCODE}" =~ ^[A-Za-z0-9]+$ ]];
      then
        fuENV_ERROR OINKCODE "has to be OPEN or your Oinkcode (letters and digits only)."
    fi
    [ -n "${SURICATA_RULES_UPDATE}" ] && fuCHOICE SURICATA_RULES_UPDATE on off
fi
if fuCOMPOSE_HAS beelzebub;
  then
    [ -n "${BEELZEBUB_LLM_PROVIDER}" ] && fuCHOICE BEELZEBUB_LLM_PROVIDER ollama openai
    fuURL BEELZEBUB_LLM_HOST
    if [ "${BEELZEBUB_LLM_PROVIDER}" == "openai" ] && [ -z "${BEELZEBUB_LLM_API_KEY}" ];
      then
        fuENV_WARN BEELZEBUB_LLM_API_KEY "not set, the openai provider needs one."
    fi
fi
if fuCOMPOSE_HAS galah;
  then
    myGALAH="${GALAH_LLM_PROVIDER:-ollama}"
    [ -n "${GALAH_LLM_PROVIDER}" ] && fuCHOICE GALAH_LLM_PROVIDER ollama openai anthropic googleai gcp-vertex cohere
    fuURL GALAH_LLM_SERVER_URL
    if [ "${myGALAH}" == "ollama" ] && [ -z "${GALAH_LLM_SERVER_URL}" ];
      then
        fuENV_ERROR GALAH_LLM_SERVER_URL "is required for the ollama provider."
    fi
    if [ -n "${GALAH_LLM_TEMPERATURE}" ] && \
       { [[ ! "${GALAH_LLM_TEMPERATURE}" =~ ^[0-9]+(\.[0-9]+)?$ ]] || \
         awk -v myT="${GALAH_LLM_TEMPERATURE}" 'BEGIN { exit !(myT > 2) }'; };
      then
        fuENV_ERROR GALAH_LLM_TEMPERATURE "has to be a number from 0 to 2."
    fi
    case "${myGALAH}" in
      openai|anthropic|googleai|cohere)
        [ -z "${GALAH_LLM_API_KEY}" ] && fuENV_ERROR GALAH_LLM_API_KEY "is required for the ${myGALAH} provider."
        ;;
      gcp-vertex)
        [ -z "${GALAH_LLM_CLOUD_LOCATION}" ] && fuENV_ERROR GALAH_LLM_CLOUD_LOCATION "is required for the gcp-vertex provider."
        [ -z "${GALAH_LLM_CLOUD_PROJECT}" ] && fuENV_ERROR GALAH_LLM_CLOUD_PROJECT "is required for the gcp-vertex provider."
        ;;
    esac
fi
echo

if [ "${#myENV_ERRORS[@]}" -gt 0 ];
  then
    for myERROR in "${myENV_ERRORS[@]}";
      do
        echo "# Error: ${myERROR}"
    done
    echo
    echo "# ${#myENV_ERRORS[@]} error(s) found. Please check T-Pot .env config."
    echo
    echo "# Aborting"
    exit 1
fi

echo
echo "# All settings seem to be valid.$([ "${myENV_WARNINGS}" -gt 0 ] && echo " ${myENV_WARNINGS} warning(s), see above.")"
echo

# Data folder management
if [ -f "/data/uuid" ];
  then
    figlet "Initializing ..."
    figlet "T-Pot: ${TPOT_VERSION}"
    create_web_users
    echo
    echo "# Data folder is present, just cleaning up, please be patient ..."
    echo
    /opt/tpot/bin/clean.sh "${TPOT_PERSISTENCE}" "${TPOT_PERSISTENCE_CYCLES}"
    echo
  else
    figlet "Setting up ..."
    figlet "T-Pot: ${TPOT_VERSION}"
    myFIRSTRUN="true"
    echo
    echo "# Setting up data folder structure ..."
    echo
    /opt/tpot/bin/clean.sh off
    echo
    echo "# Generating self signed certificate ..."
    echo
    myINTIP=$(/sbin/ip address show | awk '/inet .*brd/{split($2,a,"/"); print a[1]; exit}')
    openssl req \
          -nodes \
          -x509 \
          -sha512 \
          -newkey rsa:8192 \
          -keyout "/data/nginx/cert/nginx.key" \
          -out "/data/nginx/cert/nginx.crt" \
          -days 3650 \
          -subj '/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd' \
          -addext "subjectAltName = IP:${myINTIP}"
    echo
    create_web_users
    echo
    echo "# Final touches and permissions ..."
    echo
    uuidgen > /data/uuid
fi

# Check if TPOT_BLACKHOLE is enabled
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    if [ "${TPOT_BLACKHOLE}" == "ENABLED" ] && [ ! -f "/etc/blackhole/mass_scanner.txt" ];
      then
        echo
        echo "# Adding Blackhole routes."
        /opt/tpot/bin/blackhole.sh add
        echo
    fi
    if [ "${TPOT_BLACKHOLE}" == "DISABLED" ] && [ -f "/etc/blackhole/mass_scanner.txt" ];
      then
        echo
        echo "# Removing Blackhole routes."
        /opt/tpot/bin/blackhole.sh del
        echo
      else
        echo
        echo "# Blackhole is not active."
    fi
  else
    echo
    echo "# T-Pot is configured for macOS / Windows. Blackhole is not supported."
    echo
fi

# Get IP
echo
echo "# Updating IP Info ..."
echo
/opt/tpot/bin/updateip.sh

# Update permissions
update_permissions

# Update interface settings (NSM services) and setup iptables to support NFQ based honeypots (glutton, honeytrap)
### This is currently not supported on Docker for Desktop, only on Docker Engine for Linux
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    echo
    echo "# Get IF, disable offloading, enable promiscious mode for NSM services ..."
    echo
    # Same detection as in the NSM containers (TPOT_CAPTURE_INTERFACE or the route
    # to the internet), so the settings apply to the interface they capture on.
    # Without an interface only the NSM services fail, T-Pot itself keeps running.
    if myIF="$(sh /opt/tpot/bin/capture-if.sh)";
      then
        echo "Capture interface: ${myIF}"
        ethtool --offload "${myIF}" rx off tx off
        ethtool -K "${myIF}" gso off gro off
        ip link set "${myIF}" promisc on
      else
        echo "# Warning: No capture interface found, skipping offload / promiscuous mode settings. NSM services will not start."
    fi
    echo
    echo "# Adding firewall rules ..."
    echo
    /opt/tpot/bin/rules.sh ${COMPOSE} set
  else
    echo
    echo "# T-Pot is configured for macOS / Windows. Setting up firewall rules on the host is not supported."
    echo
fi

# Display open ports
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    echo
    echo "# This is a list of open ports on the host (netstat -tulpen)."
    echo "# Make sure there are no conflicting ports by checking the docker compose file."
    echo "# Conflicting ports will prevent the startup of T-Pot."
    echo
    netstat -tulpen | grep -Eo ':([0-9]+)' | cut -d ":" -f 2 | uniq
    echo
  else
    echo
    echo "# T-Pot is configured for macOS / Windows. Showing open ports from the host is not supported."
    echo
fi


# Done
echo
figlet "Starting ..."
figlet "T-Pot: ${TPOT_VERSION}"
echo
touch /tmp/success

# We need to push objects to Kibana if this is a Hive and a fresh install
if [ "${myFIRSTRUN}" == "true" ] && [ "${TPOT_TYPE}" == "HIVE" ];
  then
    myKIBANA_URL="http://127.0.0.1:64296"
    myKIBANA_CONFIG="/opt/tpot/etc/objects/export.ndjson"

    # Wait for Kibana to be available
    until curl -s -f -o /dev/null "{$myKIBANA_URL}/api/status"; do
      echo "# Waiting for Kibana to upload config..."
      sleep 2
    done

    # Upload Kibana config
    echo "# Now uploading config to Kibana."
    curl -X POST "http://127.0.0.1:64296/api/saved_objects/_import?overwrite=true" \
      -H "kbn-xsrf: true" \
      --form file=@/opt/tpot/etc/objects/kibana_export.ndjson
    echo "# Kibana config has been uploaded."
fi

# We want to see true source for UDP packets in container (https://github.com/moby/libnetwork/issues/1994)
# Start autoheal if running on a supported os
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    sleep 60
    echo "# Dropping UDP connection tables to improve visibility of true source IPs."
    /usr/sbin/conntrack -D -p udp
fi

# Starting container health monitoring
echo
figlet "Starting ..."
figlet "Autoheal"
echo "# Now monitoring healthcheck enabled containers to automatically restart them when unhealthy."
echo
/opt/tpot/autoheal.sh autoheal &
PID=$!
wait $PID
echo "# T-Pot Init and Autoheal were stopped. Exiting."
