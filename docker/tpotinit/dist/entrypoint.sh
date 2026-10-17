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
  # autoheal, es_retention.sh and kibana_objects.sh, once they run; a PUT of the ILM policy in flight
  # may finish, the Kibana objects are checked again on the next start
  [ -n "${PID}" ] && kill -TERM "${PID}" 2>/dev/null
  if [ -n "${myILM_PID}" ];
    then
      kill -TERM "${myILM_PID}" 2>/dev/null
      wait "${myILM_PID}" 2>/dev/null
  fi
  if [ -n "${myKIBANA_PID}" ];
    then
      kill -TERM "${myKIBANA_PID}" 2>/dev/null
      wait "${myKIBANA_PID}" 2>/dev/null
  fi
  rm -f /tmp/success
  echo "# Cleanup done."
  echo
  # the waits below are interruptible (sleep & wait), the script ends here instead of going on
  exit 0
}
PID=""
myILM_PID=""
myKIBANA_PID=""
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

# The hold of ewsposter (hold, hold.ack) and the lock of the checks (check.lock) in <data>/ews
# end with every start, so a check that was cut off never keeps ewsposter quiet. The folder is
# reached by its descriptor, and a name in it is removed, never followed; a folder of that name stays.
clear_ews_hold() {
    python3 - "${1:-/data}" <<'PY'
import os
import stat
import sys

data = sys.argv[1]
try:
    data_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY)
except OSError:
    sys.exit(0)
try:
    ews_fd = os.open("ews", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
except FileNotFoundError:
    sys.exit(0)
except OSError as error:
    if os.path.islink(os.path.join(data, "ews")):
        print(f"# Warning: {data}/ews is a link, the hold of ewsposter is not cleared there.")
    else:
        print(f"# Warning: {data}/ews could not be opened ({error.strerror}), the hold of ewsposter is not cleared.")
    sys.exit(0)
for name in ("hold", "hold.ack", "check.lock", "quarantine"):
    try:
        if stat.S_ISDIR(os.stat(name, dir_fd=ews_fd, follow_symlinks=False).st_mode):
            print(f"# Warning: {data}/ews/{name} is a folder, it stays.")
            continue
        os.unlink(name, dir_fd=ews_fd)
        print(f"# Removed {data}/ews/{name}.")
    except FileNotFoundError:
        continue
    except OSError as error:
        print(f"# Warning: {data}/ews/{name} could not be removed ({error.strerror}).")
PY
}

# Folders 770, files 660 (no execute bit), nginx conf and cert 774 / 664; links are not followed
. /opt/tpot/bin/data_modes.sh

update_permissions() {
	echo
	echo "# Updating permissions ..."
	echo
	chown -R tpot:tpot /data
	fuDATA_MODES /data
	fuDATA_WEB_MODES /data/nginx/conf /data/nginx/cert
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
    # The first import of the Kibana objects is pending until it worked (kibana_objects.sh), written
    # before /data/uuid: a first start that ends before the import tries it again on the next one
    if ! python3 /opt/tpot/bin/kibana_objects.py mark-fresh --data /data;
      then
        echo "# Warning: the first import of the Kibana objects could not be marked, the Kibana objects job of this start imports them without the advanced settings."
    fi
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

# A hold of ewsposter or a lock of the checks from before this start ends here
clear_ews_hold /data

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

# The ILM policy tpot follows TPOT_ES_RETENTION, unless it was changed outside of T-Pot
# (rules in its bin/es_retention.sh). In the background: Elasticsearch takes a while
# and nothing else waits for it.
if [ "${TPOT_TYPE}" == "HIVE" ] && yq -e '.services | has("elasticsearch")' "${COMPOSE}" >/dev/null 2>&1;
  then
    /opt/tpot/bin/es_retention.sh &
    myILM_PID=$!
fi

# T-Pot's Kibana objects: the first import until it worked, then TPOT_KIBANA_OBJECTS (rules in
# bin/kibana_objects.py). In the background: Kibana takes a while and nothing else waits for it.
if [ "${TPOT_TYPE}" == "HIVE" ] && yq -e '.services | has("kibana")' "${COMPOSE}" >/dev/null 2>&1;
  then
    /opt/tpot/bin/kibana_objects.sh &
    myKIBANA_PID=$!
fi

# We want to see true source for UDP packets in container (https://github.com/moby/libnetwork/issues/1994)
# Start autoheal if running on a supported os
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    # in the background, so that a SIGTERM ends the wait at once (cleanup)
    sleep 60 & wait $!
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
