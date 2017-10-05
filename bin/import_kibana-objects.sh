#!/bin/bash
# Import Kibana objects
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
myDUMP=$1
myCOL1="[0;34m"
myCOL0="[0;0m"

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  rm -rf patterns/ dashboards/ visualizations/ searches/
}
trap fuCLEANUP EXIT

# Check if parameter is given and file exists
if [ "$myDUMP" = "" ];
  then
    echo $myCOL1"### Please provide a backup file name."$myCOL0 
    echo $myCOL1"### restore-kibana-objects.sh <kibana-objects.tgz>"$myCOL0
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
myINDEXCOUNT=$(cat patterns/index-patterns.json | tr '\\' '\n' | grep "scripted" | wc -w)
echo $myCOL1"### Now importing"$myCOL0 $myINDEXCOUNT $myCOL1"index patterns." $myCOL0
curl -s -XDELETE ''$myES'.kibana/index-pattern/logstash-*' > /dev/null
curl -s -XPUT ''$myES'.kibana/index-pattern/logstash-*' -T patterns/index-patterns.json > /dev/null
echo

# Restore dashboards
myDASHBOARDS=$(ls dashboards/*.json | cut -c 12- | rev | cut -c 6- | rev) 
echo $myCOL1"### Now importing "$myCOL0$(echo $myDASHBOARDS | wc -w)$myCOL1 "dashboards." $myCOL0
for i in $myDASHBOARDS;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XDELETE ''$myES'.kibana/dashboard/'$i'' > /dev/null
    curl -s -XPUT ''$myES'.kibana/dashboard/'$i'' -T dashboards/$i.json > /dev/null
  done;
echo

# Restore visualizations
myVISUALIZATIONS=$(ls visualizations/*.json | cut -c 16- | rev | cut -c 6- | rev)
echo $myCOL1"### Now importing "$myCOL0$(echo $myVISUALIZATIONS | wc -w)$myCOL1 "visualizations." $myCOL0
for i in $myVISUALIZATIONS;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XDELETE ''$myES'.kibana/visualization/'$i'' > /dev/null
    curl -s -XPUT ''$myES'.kibana/visualization/'$i'' -T visualizations/$i.json > /dev/null
  done;
echo

# Restore searches
mySEARCHES=$(ls searches/*.json | cut -c 10- | rev | cut -c 6- | rev) 
echo $myCOL1"### Now importing "$myCOL0$(echo $mySEARCHES | wc -w)$myCOL1 "searches." $myCOL0
for i in $mySEARCHES;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XDELETE ''$myES'.kibana/search/'$i'' > /dev/null
    curl -s -XPUT ''$myES'.kibana/search/'$i'' -T searches/$i.json > /dev/null
  done;
echo

# Stats
echo
echo $myCOL1"### Statistics"
echo $myCOL1"###### Imported"$myCOL0 $myINDEXCOUNT $myCOL1"index patterns." $myCOL0
echo $myCOL1"###### Imported"$myCOL0 $(echo $myDASHBOARDS | wc -w) $myCOL1"dashboards." $myCOL0
echo $myCOL1"###### Imported"$myCOL0 $(echo $myVISUALIZATIONS | wc -w) $myCOL1"visualizations." $myCOL0
echo $myCOL1"###### Imported"$myCOL0 $(echo $mySEARCHES | wc -w) $myCOL1"searches." $myCOL0
echo

