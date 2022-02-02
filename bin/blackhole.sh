#!/bin/bash

# Run as root only.
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi

# Disclaimer
if [ "$1" == "" ];
  then
    echo "### Warning!"
    echo "### This script will download and add blackhole routes for known mass scanners in an attempt to decrease the chance of detection."
    echo "### IPs are neither curated or verified, use at your own risk!"
    echo "###"
    echo "### Routes are not added permanently, if you wish a persistent solution add this script to /etc/rc.local to be started after boot."
    echo
    echo "Usage: blackhole.sh add (add blackhole routes)" 
    echo "       blackhole.sh del (delete blackhole routes)"
    echo
    exit
fi

# QnD paths
mkdir -p /etc/blackhole
cd /etc/blackhole

# Let's load ip reputation lists from listbot service
if ! [ -f "iprep.yaml" ];
  then
    aria2c -s16 -x 16 https://listbot.sicherheitstacho.eu/iprep.yaml.bz2 && \
    bunzip2 -f *.bz2
fi

# Let's extract mass scanner IPs
myBLACKHOLE_IPS=$(grep "mass scanner" iprep.yaml | cut -f 1 -d":" | tr -d '"')

# Let's add blackhole routes for all mass scanner IPs
# Your personal preferences may vary, feel free to adjust accordingly
if [ "$1" == "add" ];
  then
    echo "Now add blackhole routes."
    for i in $myBLACKHOLE_IPS;
      do
        echo "ip route add blackhole $i"
        ip route add blackhole $i
    done
fi

# Let's delete blackhole routes for all mass scanner IPs
if [ "$1" == "del" ];
  then
    echo "Now deleting blackhole routes."
      for i in $myBLACKHOLE_IPS;
        do
          echo "ip route del blackhole $i"
          ip route del blackhole $i
      done
    rm iprep.yaml
fi
