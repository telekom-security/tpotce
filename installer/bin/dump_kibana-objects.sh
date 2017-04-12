#!/bin/bash
myDATE=$(date +%Y%m%d%H%M)
myES="http://127.0.0.1:64298/"
myINDEXCOUNT=$(curl -s -XGET ''$myES'.kibana/index-pattern/logstash-*' | tr '\\' '\n' | grep "scripted" | wc -w)
myDASHBOARDS=$(curl -s -XGET ''$myES'.kibana/dashboard/_search?filter_path=hits.hits._id&pretty&size=10000'  | jq '.hits.hits[] | {_id}' | jq -r '._id')
myVISUALIZATIONS=$(curl -s -XGET ''$myES'.kibana/visualization/_search?filter_path=hits.hits._id&pretty&size=10000' | jq '.hits.hits[] | {_id}' | jq -r '._id')
mySEARCHES=$(curl -s -XGET ''$myES'.kibana/search/_search?filter_path=hits.hits._id&pretty&size=10000' | jq '.hits.hits[] | {_id}' | jq -r '._id')
myCOL1="[0;34m"
myCOL0="[0;0m"

# Export index patterns
mkdir -p patterns
echo $myCOL1"### Now dumping"$myCOL0 $myINDEXCOUNT $myCOL1"index patterns." $myCOL0
curl -s -XGET ''$myES'.kibana/index-pattern/logstash-*?' | jq '._source' > patterns/index-patterns.json
echo

# Export dashboards
mkdir -p dashboards
echo $myCOL1"### Now dumping"$myCOL0 $(echo $myDASHBOARDS | wc -w) $myCOL1"dashboards." $myCOL0
for i in $myDASHBOARDS;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XGET ''$myES'.kibana/dashboard/'$i'' | jq '._source' > dashboards/$i.json
  done;
echo

# Export visualizations
mkdir -p visualizations
echo $myCOL1"### Now dumping"$myCOL0 $(echo $myVISUALIZATIONS | wc -w) $myCOL1"visualizations." $myCOL0
for i in $myVISUALIZATIONS;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XGET ''$myES'.kibana/visualization/'$i'' | jq '._source' > visualizations/$i.json
  done;
echo

# Export searches
mkdir -p searches
echo $myCOL1"### Now dumping"$myCOL0 $(echo $mySEARCHES | wc -w) $myCOL1"searches." $myCOL0
for i in $mySEARCHES;
  do
    echo $myCOL1"###### "$i $myCOL0
    curl -s -XGET ''$myES'.kibana/search/'$i'' | jq '._source' > searches/$i.json
  done;
echo

# Pack into tar
echo $myCOL1"### Now packing archive"$myCOL0 "kibana-objects_"$myDATE".tgz"
tar cvfz kibana-objects_$myDATE.tgz patterns dashboards visualizations searches > /dev/null

# Cleanup
rm -rf patterns dashboards visualizations searches

# Stats
echo
echo $myCOL1"### Statistics"
echo $myCOL1"###### Dumped"$myCOL0 $myINDEXCOUNT $myCOL1"index patterns." $myCOL0
echo $myCOL1"###### Dumped"$myCOL0 $(echo $myDASHBOARDS | wc -w) $myCOL1"dashboards." $myCOL0
echo $myCOL1"###### Dumped"$myCOL0 $(echo $myVISUALIZATIONS | wc -w) $myCOL1"visualizations." $myCOL0
echo $myCOL1"###### Dumped"$myCOL0 $(echo $mySEARCHES | wc -w) $myCOL1"searches." $myCOL0
echo

