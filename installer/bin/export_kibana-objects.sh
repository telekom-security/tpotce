#!/bin/bash
# Export all Kibana objects
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
myDATE=$(date +%Y%m%d%H%M)
myINDEXCOUNT=$(curl -s -XGET ''$myES'.kibana/index-pattern/logstash-*' | tr '\\' '\n' | grep "scripted" | wc -w)
myDASHBOARDS=$(curl -s -XGET ''$myES'.kibana/dashboard/_search?filter_path=hits.hits._id&pretty&size=10000'  | jq '.hits.hits[] | {_id}' | jq -r '._id')
myVISUALIZATIONS=$(curl -s -XGET ''$myES'.kibana/visualization/_search?filter_path=hits.hits._id&pretty&size=10000' | jq '.hits.hits[] | {_id}' | jq -r '._id')
mySEARCHES=$(curl -s -XGET ''$myES'.kibana/search/_search?filter_path=hits.hits._id&pretty&size=10000' | jq '.hits.hits[] | {_id}' | jq -r '._id')
myCOL1="[0;34m"
myCOL0="[0;0m"

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  rm -rf patterns/ dashboards/ visualizations/ searches/
}
trap fuCLEANUP EXIT

# Export index patterns
mkdir -p patterns
echo $myCOL1"### Now exporting"$myCOL0 $myINDEXCOUNT $myCOL1"index patterns." $myCOL0
curl -s -XGET ''$myES'.kibana/index-pattern/logstash-*?' | jq '._source' > patterns/index-patterns.json
echo

# Export dashboards
mkdir -p dashboards
echo $myCOL1"### Now exporting"$myCOL0 $(echo $myDASHBOARDS | wc -w) $myCOL1"dashboards." $myCOL0
for i in $myDASHBOARDS;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XGET ''$myES'.kibana/dashboard/'$i'' | jq '._source' > dashboards/$i.json
  done;
echo

# Export visualizations
mkdir -p visualizations
echo $myCOL1"### Now exporting"$myCOL0 $(echo $myVISUALIZATIONS | wc -w) $myCOL1"visualizations." $myCOL0
for i in $myVISUALIZATIONS;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XGET ''$myES'.kibana/visualization/'$i'' | jq '._source' > visualizations/$i.json
  done;
echo

# Export searches
mkdir -p searches
echo $myCOL1"### Now exporting"$myCOL0 $(echo $mySEARCHES | wc -w) $myCOL1"searches." $myCOL0
for i in $mySEARCHES;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XGET ''$myES'.kibana/search/'$i'' | jq '._source' > searches/$i.json
  done;
echo

# Building tar archive
echo $myCOL1"### Now building archive"$myCOL0 "kibana-objects_"$myDATE".tgz"
tar cvfz kibana-objects_$myDATE.tgz patterns dashboards visualizations searches > /dev/null

# Stats
echo
echo $myCOL1"### Statistics"
echo $myCOL1"###### Exported"$myCOL0 $myINDEXCOUNT $myCOL1"index patterns." $myCOL0
echo $myCOL1"###### Exported"$myCOL0 $(echo $myDASHBOARDS | wc -w) $myCOL1"dashboards." $myCOL0
echo $myCOL1"###### Exported"$myCOL0 $(echo $myVISUALIZATIONS | wc -w) $myCOL1"visualizations." $myCOL0
echo $myCOL1"###### Exported"$myCOL0 $(echo $mySEARCHES | wc -w) $myCOL1"searches." $myCOL0
echo
