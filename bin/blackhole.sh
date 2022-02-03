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

# Calculate age of downloaded reputation list
if [ -f "iprep.yaml" ];
  then
    myNOW=$(date +%s)
    myOLD=$(date +%s -r iprep.yaml)
    myDAYS=$(( (now-old) / (60*60*24) ))
    echo "### Downloaded reputation list is $myDAYS days old."
    myBLACKHOLE_IPS=$(grep "mass scanner" iprep.yaml | cut -f 1 -d":" | tr -d '"')
fi

# Let's load ip reputation list from listbot service
if [[ ! -f "iprep.yaml" && "$1" == "add" || "$myDAYS" -gt 30 ]];
  then
    echo "### Downloading reputation list."
    aria2c -s16 -x 16 https://listbot.sicherheitstacho.eu/iprep.yaml.bz2 && \
    bunzip2 -f *.bz2
    myBLACKHOLE_IPS=$(grep "mass scanner" iprep.yaml | cut -f 1 -d":" | tr -d '"')
fi

myCOUNT=$(echo $myBLACKHOLE_IPS | wc -w)
# Let's extract mass scanner IPs
if [ "$myCOUNT" -lt "3000" ] && [ "$1" == "add" ];
  then
    echo "### Something went wrong. Please check contents of /etc/blackhole/iprep.yaml."
    echo "### Aborting."
    echo
    exit
elif [ "$(ip r | grep 'blackhole' -c)" -gt "3000" ] && [ "$1" == "add" ];
  then
    echo "### Blackhole already enabled."
    echo "### Aborting."
    echo
    exit
fi

# Let's add blackhole routes for all mass scanner IPs
# Your personal preferences may vary, feel free to adjust accordingly
if [ "$1" == "add" ];
  then
    echo
    echo -n "Now adding $myCOUNT IPs to blackhole."
    for i in $myBLACKHOLE_IPS;
      do
        ip route add blackhole $i
	echo -n "."
    done
    echo
    echo "Added $(ip r | grep "blackhole" -c) IPs to blackhole."
    echo
    echo "### Remember!"
    echo "### Routes are not added permanently, if you wish a persistent solution add this script to /etc/rc.local to be started after boot."
    echo
    exit
fi

# Let's delete blackhole routes for all mass scanner IPs
if [ "$1" == "del" ] && [ "$myCOUNT" -gt 3000 ];
  then
    echo
    echo -n "Now deleting $myCOUNT IPs from blackhole."
      for i in $myBLACKHOLE_IPS;
        do
          ip route del blackhole $i
	  echo -n "."
      done
      echo
      echo "$(ip r | grep 'blackhole' -c) IPs remaining in blackhole."
      rm iprep.yaml
  else
    echo "Blackhole already disabled."
fi
