#/bin/bash
myDUMP=$1
myES="http://127.0.0.1:64298/"
myCOL1="[0;34m"
myCOL0="[0;0m"

# Check if parameter is given and file exists
if [ "$myDUMP" = "" ];
    then
      echo $myCOL1"### Please proive a backup file name."$myCOL0 
      echo $myCOL1"### restore-elk.sh <es_dump.tgz>"$myCOL0
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
mkdir tmp
tar xvfz $myDUMP -C tmp
cd tmp
# Build indices list
myINDICES=$(ls | cut -c 1-19)
echo $myCOL1"### The following indices will be restored: "$myCOL0
echo $myINDICES
echo

for i in $myINDICES;
  do
    # Delete index if it already exists
    curl -s -XDELETE $myES$i > /dev/null
    echo $myCOL1"### Now uncompressing: "$i".gz" $myCOL0
    gunzip $i.gz
    # Restore index to ES
    echo $myCOL1"### Now restoring: "$i $myCOL0
    elasticdump --input=$i --output=$myES$i --limit 7500
    rm $i
  done;
cd ..
rm -rf tmp
echo $myCOL1"### Done."$myCOL0
