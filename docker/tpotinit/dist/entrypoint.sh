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

# Function to check if a variable is set, not empty
check_var() {
    local var_name="$1"
    local var_value=$(eval echo \$$var_name)

    # Check if variable is set and not empty
    if [[ -z "$var_value" ]];
      then
        echo "# Error: $var_name is not set or empty. Please check T-Pot .env config."
        echo
        echo "# Aborting"
        exit 1
    fi
}

# Function to check for potentially unsafe characters in most variables
check_safety() {
    local var_name="$1"
    local var_value=$(eval echo \$$var_name)

    # General safety check for most variables
    if [[ $var_value =~ [^a-zA-Z0-9_/.:-] ]];
      then
        echo "# Error: Unsafe characters detected in $var_name. Please check T-Pot .env config."
        echo
        echo "# Aborting"
        exit 1
    fi
}

validate_base64() {
    local myCHECK=$1
    # base64 pattern match
    for i in ${myCHECK};
      do
        if [[ $i =~ ^([A-Za-z0-9+/]{4})*([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$ ]];
          then
            echo -n "Found valid user: "
            echo $i | base64 -d -w0 | cut -f1 -d":"
          else
	        echo "$i is not a valid base64 string. Please check T-Pot .env config."
	        echo
	        echo "# Aborting"
	        exit 1
	    fi
    done
}

# Function to validate specific variable formats
validate_format() {
    local var_name="$1"
    local var_value=$(eval echo \$$var_name)

    case "$var_name" in
        TPOT_BLACKHOLE|TPOT_PERSISTENCE|TPOT_ATTACKMAP_TEXT)
            if ! [[ $var_value =~ ^(ENABLED|DISABLED|on|off|true|false)$ ]];
              then
                echo "# Error: Invalid value for $var_name. Expected ENABLED/DISABLED, on/off, true/false. Please check T-Pot .env config."
		        echo
		        echo "# Aborting"
                exit 1
            fi
            ;;
    esac
}

validate_ip_or_domain() {
    local myCHECK=$1

    # Regular expression for validating IPv4 addresses
    local ipv4Regex='^(([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])\.){3}([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])$'

    # Regular expression for validating domain names (including subdomains)
    local domainRegex='^(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9])$'

    # Check if TPOT_HIVE_IP matches IPv4 or domain name
    if [[ ${myCHECK} =~ $ipv4Regex ]]; then
        echo "${myCHECK} is a valid IPv4 address."
    elif [[ ${myCHECK} =~ $domainRegex ]]; then
        echo "${myCHECK} is a valid domain name."
    else
        echo "# Error: $myCHECK is not a valid IPv4 address or domain name. Please check T-Pot .env config."
        echo
        echo "# Aborting"
        exit 1
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

# Validate environment variables
for var in TPOT_BLACKHOLE TPOT_PERSISTENCE TPOT_ATTACKMAP_TEXT TPOT_ATTACKMAP_TEXT_TIMEZONE TPOT_REPO TPOT_VERSION TPOT_PULL_POLICY TPOT_OSTYPE;
  do
    check_var "$var"
    check_safety "$var"
    validate_format "$var"
done

if [ "${TPOT_TYPE}" == "HIVE" ];
  then
    # No $ for check_var
    check_var "WEB_USER"
    validate_base64 "${WEB_USER}"
    TPOT_HIVE_USER=""
    TPOT_HIVE_IP=""
    if [ "${LS_WEB_USER}" == "" ];
      then
        echo "# Warning: No LS_WEB_USER detected! T-Pots of type SENSOR will not be able to submit logs to this HIVE."
        echo
      else
        validate_base64 "${LS_WEB_USER}"
    fi
fi
if [ "${TPOT_TYPE}" == "SENSOR" ];
 then
   # No $ for check_var
   check_var "TPOT_HIVE_USER"
   check_var "TPOT_HIVE_IP"
   validate_base64 "$TPOT_HIVE_USER"
   validate_ip_or_domain "$TPOT_HIVE_IP"
   WEB_USER=""
fi
echo

echo
echo "# All settings seem to be valid."
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
    /opt/tpot/bin/clean.sh "${TPOT_PERSISTENCE}"
    echo
  else
    figlet "Setting up ..."
    figlet "T-Pot: ${TPOT_VERSION}"
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
    echo "# Extracting objects, final touches and permissions ..."
    echo
    tar xvfz /opt/tpot/etc/objects/elkbase.tgz -C /
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

# Update interface settings (p0f and Suricata) and setup iptables to support NFQ based honeypots (glutton, honeytrap)
### This is currently not supported on Docker for Desktop, only on Docker Engine for Linux
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    echo
    echo "# Get IF, disable offloading, enable promiscious mode for p0f and suricata ..."
    echo
    ethtool --offload $(/sbin/ip address | grep "^2: " | awk '{ print $2 }' | tr -d [:punct:]) rx off tx off
    ethtool -K $(/sbin/ip address | grep "^2: " | awk '{ print $2 }' | tr -d [:punct:]) gso off gro off
    ip link set $(/sbin/ip address | grep "^2: " | awk '{ print $2 }' | tr -d [:punct:]) promisc on
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
