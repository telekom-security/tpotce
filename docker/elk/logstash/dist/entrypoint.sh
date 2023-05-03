#!/bin/bash

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  exit 0
}
trap fuCLEANUP EXIT

# Source ENVs from file ...
if [ -f "/data/tpot/etc/compose/elk_environment" ];
  then
    echo "Found .env, now exporting ..."
    set -o allexport && source "/data/tpot/etc/compose/elk_environment" && set +o allexport
fi

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

# Distributed T-Pot installation needs a different pipeline config and autossh tunnel. 
if [ "$MY_TPOT_TYPE" == "SENSOR" ];
  then
    echo
    echo "Distributed T-Pot setup, sending T-Pot logs to $MY_HIVE_IP."
    echo
    echo "T-Pot type: $MY_TPOT_TYPE"
    echo "Keyfile used: $MY_SENSOR_PRIVATEKEYFILE"
    echo "Hive username: $MY_HIVE_USERNAME"
    echo "Hive IP: $MY_HIVE_IP"
    echo
    # Ensure correct file permissions for private keyfile or SSH will ask for password
    chmod 600 $MY_SENSOR_PRIVATEKEYFILE
    cp /usr/share/logstash/config/pipelines_sensor.yml /usr/share/logstash/config/pipelines.yml
    autossh -f -M 0 -4 -l $MY_HIVE_USERNAME -i $MY_SENSOR_PRIVATEKEYFILE -p 64295 -N -L64305:127.0.0.1:64305 $MY_HIVE_IP -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -o "StrictHostKeyChecking=no" -o "UserKnownHostsFile=/dev/null"
fi

if [ "$MY_TPOT_TYPE" != "SENSOR" ];
  then
    # Index Management is happening through ILM, but we need to put T-Pot ILM setting on ES.
    myTPOTILM=$(curl -s -XGET "http://elasticsearch:9200/_ilm/policy/tpot" | grep "Lifecycle policy not found: tpot" -c)
    if [ "$myTPOTILM" == "1" ];
      then
        echo "T-Pot ILM template not found on ES, putting it on ES now."
        curl -XPUT "http://elasticsearch:9200/_ilm/policy/tpot" -H 'Content-Type: application/json' -d'
        {
          "policy": {
            "phases": {
              "hot": {
                "min_age": "0ms",
                "actions": {}
              },
              "delete": {
                "min_age": "30d",
                "actions": {
                  "delete": {
                    "delete_searchable_snapshot": true
                  }
                }
              }
            },
            "_meta": {
              "managed": true,
              "description": "T-Pot ILM policy with a retention of 30 days"
            }
          }
        }'
      else
        echo "T-Pot ILM already configured or ES not available."
    fi
fi
echo

exec /usr/share/logstash/bin/logstash --config.reload.automatic
