#!/bin/bash
# Run as root only.
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ];
  then
    echo "Need to run as root ..."
    exit
fi

if [ "$1" == "" ] || [ "$1" != "all" ] && [ "$1" != "base" ];
  then
    echo "Usage: backup_es_folders [all, base]"
    echo "       all  = backup all ES folder"
    echo "       base = backup only Kibana index".
    echo
    exit
fi

# Backup all ES relevant folders
# Make sure ES is available
myES="http://127.0.0.1:64298/"
myESSTATUS=$(curl -s -XGET ''$myES'_cluster/health' | jq '.' | grep -c green)
if ! [ "$myESSTATUS" = "1" ]
  then
    echo "### Elasticsearch is not available, try starting via 'systemctl start tpot'."
    exit
  else
    echo "### Elasticsearch is available, now continuing."
    echo
fi

# Set vars
myCOUNT=1
myDATE=$(date +%Y%m%d%H%M)
myELKPATH="/data/elk/data"
myKIBANAINDEXNAME=$(curl -s -XGET ''$myES'_cat/indices/.kibana' | awk '{ print $4 }')
myKIBANAINDEXPATH=$myELKPATH/indices/$myKIBANAINDEXNAME

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  ### Start ELK
  systemctl start tpot
  echo "### Now starting T-Pot ..."
}
trap fuCLEANUP EXIT

# Stop T-Pot to lift db lock
echo "### Now stopping T-Pot"
systemctl stop tpot
sleep 2

# Backup DB in 2 flavors
echo "### Now backing up Elasticsearch folders ..."
if [ "$1" == "all" ];
  then
    tar cvfz "elkall_"$myDATE".tgz" $myELKPATH
elif [ "$1" == "base" ];
  then
    tar cvfz "elkbase_"$myDATE".tgz" $myKIBANAINDEXPATH
fi

