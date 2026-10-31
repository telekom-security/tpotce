#!/bin/bash

unset CDPATH
if [ "$#" -eq 1 ] && { [ "$1" == "-h" ] || [ "$1" == "--help" ]; }; then
    echo "Usage: blackhole.sh <add|del>"
    echo "Add or delete blackhole routes for known mass scanners."
    echo "The addresses are not curated or verified; use at your own risk."
    exit 0
fi
if [ "$#" -ne 1 ] || { [ "$1" != "add" ] && [ "$1" != "del" ]; }; then
    echo "Invalid arguments." >&2
    echo "Usage: blackhole.sh <add|del>" >&2
    exit 1
fi

# Run as root only.
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "### Need to run as root ..."
    echo
    exit 1
fi

# QnD paths, files
if ! mkdir -p /etc/blackhole || ! cd /etc/blackhole; then
    echo "### Cannot access /etc/blackhole." >&2
    exit 1
fi
myDAYS=0
myFAILED=0
myFILE="mass_scanner.txt"
myURL="https://raw.githubusercontent.com/stamparm/maltrail/master/data/mass_scanner.txt"
myBASELINE="500"
# Alternatively, using less routes, but blocking complete /24 networks
#myFILE="mass_scanner_cidr.txt"
#myURL="https://raw.githubusercontent.com/stamparm/maltrail/master/data/mass_scanner_cidr.txt"

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
if [[ "$1" == "add" && ( ! -f "$myFILE" || "$myDAYS" -gt 30 ) ]];
  then
    echo "### Downloading $myFILE list."
    if ! aria2c --allow-overwrite -s16 -x 16 "$myURL"; then
        echo "### Download failed; no routes changed." >&2
        exit 1
    fi
    myBLACKHOLE_IPS=$(grep -o -P "\b(?:\d{1,3}\.){3}\d{1,3}\b" "$myFILE" | sort -u)
fi

myCOUNT=$(echo $myBLACKHOLE_IPS | wc -w)
# Let's extract mass scanner IPs
if [ "$myCOUNT" -lt "$myBASELINE" ] && [ "$1" == "add" ];
  then
    echo "### Something went wrong. Please check contents of /etc/blackhole/$myFILE."
    echo "### Aborting."
    echo
    exit 1
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
        if ! ip route add blackhole "$i"; then
            echo "### Failed to add blackhole route for $i." >&2
            myFAILED=$((myFAILED + 1))
        fi
	echo -n "."
    done
    echo
    echo "Added $(ip r | grep "blackhole" -c) IPs to blackhole."
    echo
    echo "### Remember!"
    echo "### As long as <blackhole.sh del> is not executed the routes will be re-added on T-Pot start through </opt/tpot/bin/updateip.sh>."
    echo "### Check with <ip r> or <dps.sh> if blackhole is enabled."
    echo
    if [ "$myFAILED" -gt 0 ]; then
        echo "### $myFAILED route(s) could not be added." >&2
        exit 1
    fi
    exit 0
fi

# Let's delete blackhole routes for all mass scanner IPs
if [ "$1" == "del" ] && [ "$myCOUNT" -gt "$myBASELINE" ];
  then
    echo
    echo -n "Now deleting $myCOUNT IPs from blackhole."
      for i in $myBLACKHOLE_IPS;
        do
          if ! ip route del blackhole "$i"; then
              if ! myREMAINING=$(ip -4 route show exact "$i/32" type blackhole) || [ -n "$myREMAINING" ]; then
                  echo "### Failed to delete blackhole route for $i." >&2
                  myFAILED=$((myFAILED + 1))
              fi
          fi
	  echo -n "."
      done
      echo
      echo "$(ip r | grep 'blackhole' -c) IPs remaining in blackhole."
      echo
      if [ "$myFAILED" -gt 0 ]; then
          echo "### $myFAILED route(s) could not be removed; keeping $myFILE." >&2
          exit 1
      fi
      if ! rm "$myFILE"; then
          echo "### Cannot remove $myFILE." >&2
          exit 1
      fi
  else
    echo "### Blackhole already disabled."
    echo
fi
