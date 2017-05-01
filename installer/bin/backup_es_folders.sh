#!/bin/bash
# Backup all ES relevant folders
# Make sure ES is available
myES="http://127.0.0.1:64298/"
myESSTATUS=$(curl -s -XGET ''$myES'_cluster/health' | jq '.' | grep -c green)
if ! [ "$myESSTATUS" = "1" ]
  then
    echo "### Elasticsearch is not available, try starting via 'systemctl start elk'."
    exit
  else
    echo "### Elasticsearch is available, now continuing."
    echo
fi

# Set vars
myCOUNT=1
myDATE=$(date +%Y%m%d%H%M)
myELKPATH="/data/elk/data"
myKIBANAINDEXNAME=$(curl -s -XGET ''$myES'_cat/indices/' | grep .kibana | awk '{ print $4 }')
myKIBANAINDEXPATH=$myELKPATH/nodes/0/indices/$myKIBANAINDEXNAME

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  ### Start ELK
  systemctl start elk
  echo "### Now starting up ELK ..."
  ### Allow checks to resume
  rm -rf /var/run/check.lock
}
trap fuCLEANUP EXIT

# Make sure not to interrupt a check
while true
do
  if ! [ -a /var/run/check.lock ];
    then break
  fi
  sleep 0.1
  if [ "$myCOUNT" = "1" ];
    then
      echo -n "### Waiting for services "
    else echo -n .
  fi
  if [ "$myCOUNT" = "6000" ];
    then
    echo
    echo "### Overriding check.lock"
    rm /var/run/check.lock
    break
  fi
  myCOUNT=$[$myCOUNT +1]
done

# We do not want to get interrupted by a check
touch /var/run/check.lock

# Stop ELK to lift db lock
echo "### Now stopping ELK ..."
systemctl stop elk
sleep 10

# Backup DB in 2 flavors
echo "### Now backing up Elasticsearch folders ..."
tar cvfz "elkall_"$myDATE".tgz" $myELKPATH
tar cvfz "elkbase_"$myDATE".tgz" $myKIBANAINDEXPATH
