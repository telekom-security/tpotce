#!/bin/bash

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  exit 0
}
trap fuCLEANUP EXIT

# Check internet availability 
function fuCHECKINET () {
mySITES=$1
error=0
for i in $mySITES;
  do
    curl --connect-timeout 5 -Is $i 2>&1 > /dev/null
      if [ $? -ne 0 ];
        then
          let error+=1
      fi;
  done;
  echo $error
}

# Check for connectivity and download latest translation maps
myCHECK=$(fuCHECKINET "listbot.sicherheitstacho.eu")
if [ "$myCHECK" == "0" ];
  then
    echo "Connection to Listbot looks good, now downloading latest translation maps."
    cd /etc/listbot 
    aria2c -s16 -x 16 https://listbot.sicherheitstacho.eu/cve.yaml.bz2 && \
    aria2c -s16 -x 16 https://listbot.sicherheitstacho.eu/iprep.yaml.bz2 && \
    bunzip2 -f *.bz2
    cd /
  else
    echo "Cannot reach Listbot, starting Logstash without latest translation maps."
fi

# Make sure logstash can put latest logstash template by deleting the old one first
# This is now done via common_configs.rb => overwrite default logstash template
#echo "Removing logstash template."
#curl -XDELETE http://elasticsearch:9200/_template/logstash
#echo
#echo "Checking if empty."
#curl -XGET http://elasticsearch:9200/_template/logstash
#echo
