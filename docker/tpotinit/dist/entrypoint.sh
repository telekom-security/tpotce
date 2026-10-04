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

# The .env validation (rules in /opt/tpot/etc/env.schema.yml) collects all errors
# and aborts once, after all checks
source /opt/tpot/bin/env_validate.sh

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
fuENV_VALIDATE
# The credentials of the other type are not used, and must not be acted on
if [ "${TPOT_TYPE}" == "HIVE" ];
  then
    TPOT_HIVE_USER=""
    TPOT_HIVE_IP=""
fi
if [ "${TPOT_TYPE}" == "SENSOR" ];
  then
    WEB_USER=""
fi
echo
if ! fuENV_REPORT;
  then
    echo "# Aborting"
    exit 1
fi
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
