#!/bin/bash

# Run as root only.
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "### Need to run as root ..."
    echo
    exit
fi

# Disclaimer
if [ "$1" == "" ];
  then
    echo "### Warning!"
    echo "### This script will download and add blackhole routes for known mass scanners in an attempt to decrease the chance of detection."
    echo "### IPs are neither curated or verified, use at your own risk!"
    echo "###"
    echo "### As long as <blackhole.sh del> is not executed the routes will be re-added on T-Pot start through </opt/tpot/bin/updateip.sh>."
    echo "### Check with <ip r> or <dps.sh> if blackhole is enabled."
    echo
    echo "Usage: blackhole.sh add (add blackhole routes)" 
    echo "       blackhole.sh del (delete blackhole routes)"
    echo
    exit
fi

# QnD paths, files
mkdir -p /etc/blackhole
cd /etc/blackhole
myFILE="mass_scanner.txt"
myURL="https://raw.githubusercontent.com/stamparm/maltrail/master/trails/static/mass_scanner.txt"
myBASELINE="500"
# Alternatively, using less routes, but blocking complete /24 networks
#myFILE="mass_scanner_cidr.txt"
#myURL="https://raw.githubusercontent.com/stamparm/maltrail/master/trails/static/mass_scanner_cidr.txt"

# Calculate age of downloaded list, read IPs
if [ -f "$myFILE" ];
  then
    myNOW=$(date +%s)
    myOLD=$(date +%s -r "$myFILE")
    myDAYS=$(( ($myNOW-$myOLD) / (60*60*24) ))
    echo "### Downloaded $myFILE list is $myDAYS days old."
    myBLACKHOLE_IPS=$(grep -o -P "\b(?:\d{1,3}\.){3}\d{1,3}\b" "$myFILE" | sort -u)
fi

# Let's load ip list
if [[ ! -f "$myFILE" && "$1" == "add" || "$myDAYS" -gt 30 ]];
  then
    echo "### Downloading $myFILE list."
    aria2c --allow-overwrite -s16 -x 16 "$myURL" && \
    myBLACKHOLE_IPS=$(grep -o -P "\b(?:\d{1,3}\.){3}\d{1,3}\b" "$myFILE" | sort -u) 
fi

myCOUNT=$(echo $myBLACKHOLE_IPS | wc -w)
# Let's extract mass scanner IPs
if [ "$myCOUNT" -lt "$myBASELINE" ] && [ "$1" == "add" ];
  then
    echo "### Something went wrong. Please check contents of /etc/blackhole/$myFILE."
    echo "### Aborting."
    echo
    exit
elif [ "$(ip r | grep 'blackhole' -c)" -gt "$myBASELINE" ] && [ "$1" == "add" ];
  then
    echo "### Blackhole already enabled."
    echo "### Aborting."
    echo
    exit
fi

# Let's add blackhole routes for all mass scanner IPs
if [ "$1" == "add" ];
  then
    echo
    echo -n "Now adding $myCOUNT IPs to blackhole."
    for i in $myBLACKHOLE_IPS;
      do
        ip route add blackhole "$i"
	echo -n "."
    done
    echo
    echo "Added $(ip r | grep "blackhole" -c) IPs to blackhole."
    echo
    echo "### Remember!"
    echo "### As long as <blackhole.sh del> is not executed the routes will be re-added on T-Pot start through </opt/tpot/bin/updateip.sh>."
    echo "### Check with <ip r> or <dps.sh> if blackhole is enabled."
    echo
    exit
fi

# Let's delete blackhole routes for all mass scanner IPs
if [ "$1" == "del" ] && [ "$myCOUNT" -gt "$myBASELINE" ];
  then
    echo
    echo -n "Now deleting $myCOUNT IPs from blackhole."
      for i in $myBLACKHOLE_IPS;
        do
          ip route del blackhole "$i"
	  echo -n "."
      done
      echo
      echo "$(ip r | grep 'blackhole' -c) IPs remaining in blackhole."
      echo
      rm "$myFILE"
  else
    echo "### Blackhole already disabled."
    echo
fi
