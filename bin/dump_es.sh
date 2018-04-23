#/bin/bash
# Dump all ES data
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

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  rm -rf tmp 
}
trap fuCLEANUP EXIT

# Set vars
myDATE=$(date +%Y%m%d%H%M)
myINDICES=$(curl -s -XGET ''$myES'_cat/indices/' | awk '{ print $3 }' | sort | grep -v 1970)
myES="http://127.0.0.1:64298/"
myCOL1="[0;34m"
myCOL0="[0;0m"

# Dumping all ES data
echo $myCOL1"### The following indices will be dumped: "$myCOL0
echo $myINDICES
echo

mkdir tmp 
for i in $myINDICES;
  do
    echo $myCOL1"### Now dumping: "$i $myCOL0
    elasticdump --input=$myES$i --output="tmp/"$i --limit 7500
    echo $myCOL1"### Now compressing: tmp/$i" $myCOL0
    gzip -f "tmp/"$i
  done;

# Build tar archive
echo $myCOL1"### Now building tar archive: es_dump_"$myDATE".tgz" $myCOL0
tar cvf es_dump_$myDATE.tar tmp/.
echo $myCOL1"### Done."$myCOL0
