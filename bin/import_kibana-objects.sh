#!/bin/bash
# Import Kibana objects
# Make sure ES is available
myES="http://127.0.0.1:64298/"
myKIBANA="http://127.0.0.1:64296/"
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
myDUMP=$1
myCOL1="[0;34m"
myCOL0="[0;0m"

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  rm -rf patterns/ dashboards/ visualizations/ searches/ configs/
}
trap fuCLEANUP EXIT

# Check if parameter is given and file exists
if [ "$myDUMP" = "" ];
  then
    echo $myCOL1"### Please provide a backup file name."$myCOL0 
    echo $myCOL1"### import_kibana-objects.sh <kibana-objects.tgz>"$myCOL0
    echo 
    exit
fi
if ! [ -a $myDUMP ];
  then
    echo $myCOL1"### File not found."$myCOL0 
    exit
fi

# Unpack tar
tar xvfz $myDUMP > /dev/null

# Restore index patterns
myINDEXID=$(ls patterns/*.json | cut -c 10- | rev | cut -c 6- | rev)
myINDEXCOUNT=$(cat patterns/$myINDEXID.json | tr '\\' '\n' | grep "scripted" | wc -w)
echo $myCOL1"### Now importing"$myCOL0 $myINDEXCOUNT $myCOL1"index pattern fields." $myCOL0
curl -s -XDELETE ''$myKIBANA'api/saved_objects/index-pattern/logstash-*' -H "Content-Type: application/json" -H "kbn-xsrf: true" > /dev/null
curl -s -XDELETE ''$myKIBANA'api/saved_objects/index-pattern/'$myINDEXID'' -H "Content-Type: application/json" -H "kbn-xsrf: true" > /dev/null
curl -s -XPOST ''$myKIBANA'api/saved_objects/index-pattern/'$myINDEXID'' -H "Content-Type: application/json" -H "kbn-xsrf: true" -d @patterns/$myINDEXID.json > /dev/null &
echo

# Restore dashboards
myDASHBOARDS=$(ls dashboards/*.json | cut -c 12- | rev | cut -c 6- | rev) 
echo $myCOL1"### Now importing "$myCOL0$(echo $myDASHBOARDS | wc -w)$myCOL1 "dashboards." $myCOL0
for i in $myDASHBOARDS;
  do
    curl -s -XDELETE ''$myKIBANA'api/saved_objects/dashboard/'$i'' -H "Content-Type: application/json" -H "kbn-xsrf: true" > /dev/null &
  done;
wait
for i in $myDASHBOARDS;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XPOST ''$myKIBANA'api/saved_objects/dashboard/'$i'' -H "Content-Type: application/json" -H "kbn-xsrf: true" -d @dashboards/$i.json > /dev/null &
  done;
wait
echo

# Restore visualizations
myVISUALIZATIONS=$(ls visualizations/*.json | cut -c 16- | rev | cut -c 6- | rev)
echo $myCOL1"### Now importing "$myCOL0$(echo $myVISUALIZATIONS | wc -w)$myCOL1 "visualizations." $myCOL0
for i in $myVISUALIZATIONS;
  do
    curl -s -XDELETE ''$myKIBANA'api/saved_objects/visualization/'$i'' -H "Content-Type: application/json" -H "kbn-xsrf: true" > /dev/null &
  done;
wait
for i in $myVISUALIZATIONS;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XPOST ''$myKIBANA'api/saved_objects/visualization/'$i'' -H "Content-Type: application/json" -H "kbn-xsrf: true" -d @visualizations/$i.json > /dev/null &
  done;
wait
echo

# Restore searches
mySEARCHES=$(ls searches/*.json | cut -c 10- | rev | cut -c 6- | rev) 
echo $myCOL1"### Now importing "$myCOL0$(echo $mySEARCHES | wc -w)$myCOL1 "searches." $myCOL0
for i in $mySEARCHES;
  do
    curl -s -XDELETE ''$myKIBANA'api/saved_objects/search/'$i'' -H "Content-Type: application/json" -H "kbn-xsrf: true" > /dev/null &
  done;
wait
for i in $mySEARCHES;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XPOST ''$myKIBANA'api/saved_objects/search/'$i'' -H "Content-Type: application/json" -H "kbn-xsrf: true" -d @searches/$i.json > /dev/null &
  done;
echo
wait

# Restore configs
myCONFIGS=$(ls configs/*.json | cut -c 9- | rev | cut -c 6- | rev)
echo $myCOL1"### Now importing "$myCOL0$(echo $myCONFIGS | wc -w)$myCOL1 "configs." $myCOL0
for i in $myCONFIGS;
  do
    curl -s -XDELETE ''$myKIBANA'api/saved_objects/configs/'$i'' -H "Content-Type: application/json" -H "kbn-xsrf: true" > /dev/null &
  done;
wait
for i in $myCONFIGS;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XPOST ''$myKIBANA'api/saved_objects/configs/'$i'' -H "Content-Type: application/json" -H "kbn-xsrf: true" -d @configs/$i.json > /dev/null &
  done;
echo
wait

# Stats
echo
echo $myCOL1"### Statistics"
echo $myCOL1"###### Imported"$myCOL0 $myINDEXCOUNT $myCOL1"index patterns." $myCOL0
echo $myCOL1"###### Imported"$myCOL0 $(echo $myDASHBOARDS | wc -w) $myCOL1"dashboards." $myCOL0
echo $myCOL1"###### Imported"$myCOL0 $(echo $myVISUALIZATIONS | wc -w) $myCOL1"visualizations." $myCOL0
echo $myCOL1"###### Imported"$myCOL0 $(echo $mySEARCHES | wc -w) $myCOL1"searches." $myCOL0
echo $myCOL1"###### Imported"$myCOL0 $(echo $myCONFIGS | wc -w) $myCOL1"configs." $myCOL0
echo

