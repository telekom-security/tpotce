#!/bin/bash

VERSION="T-Pot $(cat /opt/tpot/version)"
COMPOSE="/tmp/tpot/docker-compose.yml"

# Check for compatible OSType
echo
echo "# Checking if OSType is compatible."
echo
myOSTYPE=$(uname -a | grep -Eo "linuxkit")
if [ "${myOSTYPE}" == "linuxkit" ] && [ "${TPOT_OSTYPE}" == "linux" ];
  then
    echo "# Docker Desktop for macOS or Windows detected."
    echo "# 1. You need to adjust the OSType in the hidden \".env\" file."
    echo "# 2. You need to use the macos or win docker compose file."
    echo
    echo "# Aborting."
    echo
    exit 1
fi

# Data folder management
if [ -f "/data/uuid" ];
  then
    figlet "Initializing ..."
    figlet "${VERSION}"
    echo
    echo "# Data folder is present, just cleaning up, please be patient ..."
    echo
    /opt/tpot/bin/clean.sh on
    echo
  else
    figlet "Setting up ..."
    figlet "${VERSION}"
    echo
    echo "# Checking for default user."
    if [ "${WEB_USER}" == "changeme" ] || [ "${WEB_PW}" == "changeme" ];
      then
        echo "# Please change WEB_USER and WEB_PW in the hidden \".env\" file."
	echo "# Aborting." 
	echo
        exit 1
    fi
    echo
    echo "# Setting up data folder structure ..."
    echo
    mkdir -vp /data/ews/conf \
              /data/nginx/{cert,conf,log} \
              /data/tpot/etc/compose/ \
	      /data/tpot/etc/logrotate/ \
              /tmp/etc/
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
    echo "# Creating web user from tpot.env, make sure to erase the password from the .env ..."
    echo
    htpasswd -b -c /data/nginx/conf/nginxpasswd "${WEB_USER}" "${WEB_PW}"
    echo
    echo "# Extracting objects, final touches and permissions ..."
    echo
    tar xvfz /opt/tpot/etc/objects/elkbase.tgz -C /
    /opt/tpot/bin/clean.sh off
    uuidgen > /data/uuid
fi

# Check if TPOT_BLACKHOLE is enabled
if [ "${myOSTYPE}" == "linuxkit" ];
  then
    echo
    echo "# Docker Desktop for macOS or Windows detected, Blackhole feature is not supported."
    echo 
  else
    if [ "${TPOT_BLACKHOLE}" == "ENABLED" ] && [ ! -f "/etc/blackhole/mass_scanner.txt" ];
      then
        echo "# Adding Blackhole routes."
        /opt/tpot/bin/blackhole.sh add
        echo
    fi
    if [ "${TPOT_BLACKHOLE}" == "DISABLED" ] && [ -f "/etc/blackhole/mass_scanner.txt" ];
      then
        echo "# Removing Blackhole routes."
        /opt/tpot/bin/blackhole.sh del
        echo
      else
        echo "# Blackhole is not active."
    fi
fi

# Get IP
echo
echo "# Updating IP Info ..."
echo
/opt/tpot/bin/updateip.sh

# Update permissions
echo
echo "# Updating permissions ..."
echo
chown -R tpot:tpot /data
chmod -R 777 /data
#chmod 644 -R /data/nginx/conf
#chmod 644 -R /data/nginx/cert

# Update interface settings (p0f and Suricata) and setup iptables to support NFQ based honeypots (glutton, honeytrap)
### This is currently not supported on Docker for Desktop, only on Docker Engine for Linux
if [ "${myOSTYPE}" != "linuxkit" ] && [ "${TPOT_OSTYPE}" == "linux" ];
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
fi

# Display open ports
if [ "${myOSTYPE}" != "linuxkit" ];
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
    echo "# Docker Desktop for macOS or Windows detected, cannot show open ports on the host."
    echo 
fi 


# Done
echo
figlet "Starting ..."
figlet "${VERSION}"
echo
touch /tmp/success

# We want to see true source for UDP packets in container (https://github.com/moby/libnetwork/issues/1994)
if [ "${myOSTYPE}" != "linuxkit" ];
  then
    sleep 60
    /usr/sbin/conntrack -D -p udp
  else
    echo
    echo "# Docker Desktop for macOS or Windows detected, Conntrack feature is not supported."
    echo 
fi 

# Keep the container running ...
sleep infinity
