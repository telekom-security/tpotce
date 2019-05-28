#/bin/bash
# Restore folder based ES backup
# Make sure ES is available
myES="http://127.0.0.1:64298/"
myESSTATUS=$(curl -s -XGET ''$myES'_cluster/health' | jq '.' | grep -c "green\|yellow")
if ! [ "$myESSTATUS" = "1" ]
  then
    echo "### Elasticsearch is not available, try starting via 'systemctl start tpot'."
    exit
  else
    echo "### Elasticsearch is available, now continuing."
fi

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  rm -rf tmp
}
trap fuCLEANUP EXIT

# Set vars
myDUMP=$1
myCOL1="[0;34m"
myCOL0="[0;0m"

# Check if parameter is given and file exists
if [ "$myDUMP" = "" ];
    then
      echo $myCOL1"### Please provide a backup file name."$myCOL0 
      echo $myCOL1"### restore-elk.sh <es_dump.tar>"$myCOL0
      echo 
      exit 
fi
if ! [ -a $myDUMP ];
    then
      echo $myCOL1"### File not found."$myCOL0 
      exit
fi

# Unpack tar archive
echo $myCOL1"### Now unpacking tar archive: "$myDUMP $myCOL0
tar xvf $myDUMP

# Build indices list
myINDICES="$(ls tmp/logstash*.gz | cut -c 5- | rev | cut -c 4- | rev)"
myINDICES+=" .kibana"
echo $myCOL1"### The following indices will be restored: "$myCOL0
echo $myINDICES
echo

# Force single seat template for everything
echo -n $myCOL1"### Forcing single seat template: "$myCOL0
curl -s XPUT ''$myES'_template/.*' -H 'Content-Type: application/json' -d'
{ "index_patterns": ".*",
  "order": 1,
  "settings": 
    { 
      "number_of_shards": 1,
      "number_of_replicas": 0 
    }
}'
echo

# Set logstash template
echo -n $myCOL1"### Setting up logstash template: "$myCOL0
curl -s XPUT ''$myES'_template/logstash' -H 'Content-Type: application/json' -d'
{
  "index_patterns": "logstash-*",
    "settings" : {
      "index" : { 
        "number_of_shards": 1,
        "number_of_replicas": 0,
          "mapping" : { 
            "total_fields" : {
              "limit" : "2000"
            } 
          }  
      }
    }
}'
echo

# Restore indices
curl -s -X DELETE ''$myES'.kibana*' > /dev/null
for i in $myINDICES;
  do
    # Delete index if it already exists
    curl -s -X DELETE $myES$i > /dev/null
    echo $myCOL1"### Now uncompressing: tmp/$i.gz" $myCOL0
    gunzip -f tmp/$i.gz
    # Restore index to ES
    echo $myCOL1"### Now restoring: "$i $myCOL0
    elasticdump --input=tmp/$i --output=$myES$i --limit 7500
    rm tmp/$i
  done;
echo $myCOL1"### Done."$myCOL0
