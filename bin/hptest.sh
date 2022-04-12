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
    echo
  else
    echo "Usage: hptest.sh <[host or ip]>"
    echo
    exit
fi
}

function fuGETPORTS {
myDOCKERCOMPOSEUDPPORTS=$(cat $myDOCKERCOMPOSEYML | grep "udp" | tr -d '"\|#\-' | cut -d ":" -f2 | cut -d "/" -f1 | sort -gu)
myDOCKERCOMPOSEPORTS=$(cat $myDOCKERCOMPOSEYML | yq -r '.services[].ports' | grep ':' | sed -e s/127.0.0.1// | tr -d '", ' | sed -e s/^:// | cut -f1 -d ':' | grep -v "6429\|6430" | sort -gu)
myUDPPORTS=$(for i in $myDOCKERCOMPOSEUDPPORTS; do echo -n "U:$i,"; done)
myPORTS=$(for i in $myDOCKERCOMPOSEPORTS; do echo -n "T:$i,"; done)
}

# Main
fuGETPORTS
fuGOTROOT
fuCHECKDEPS
fuCHECKFORARGS
echo
echo "Starting scan on all UDP / TCP ports defined in /opt/tpot/etc/tpot.yml ..."
nmap -sV -sC -v -p $myPORTS $1 &
nmap -sU -sV -sC -v -p $myUDPPORTS $1 &
echo
wait
echo "Done."
echo

