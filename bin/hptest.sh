#!/bin/bash

myHOST="$1"
myPACKAGES="nmap"
myDOCKERCOMPOSEYML="/opt/tpot/etc/tpot.yml"

function fuGOTROOT {
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi
}

function fuCHECKDEPS {
myINST=""
for myDEPS in $myPACKAGES;
do
  myOK=$(dpkg -s $myDEPS | grep ok | awk '{ print $3 }');
  if [ "$myOK" != "ok" ]
    then
      myINST=$(echo $myINST $myDEPS)
  fi
done
if [ "$myINST" != "" ]
  then
    apt-get update -y
    for myDEPS in $myINST;
    do
      apt-get install $myDEPS -y
    done
fi
}

function fuCHECKFORARGS {
if [ "$myHOST" != "" ];
  then
    echo "All arguments met. Continuing."
  else
    echo "Usage: hptest.sh <[host or ip]>"
    exit
fi
}

function fuGETPORTS {
myDOCKERCOMPOSEPORTS=$(cat $myDOCKERCOMPOSEYML | yq -r '.services[].ports' | grep ':' | sed -e s/127.0.0.1// | tr -d '", ' | sed -e s/^:// | cut -f1 -d ':' | grep -v "6429\|6430" | sort -gu)
myPORTS=$(for i in $myDOCKERCOMPOSEPORTS; do echo -n "$i,"; done)
echo "$myPORTS"
}

# Main
fuGOTROOT
fuCHECKDEPS
fuCHECKFORARGS
echo "Starting scan ..."
nmap -sV -sC -v -p $(fuGETPORTS) $1
echo "Done."