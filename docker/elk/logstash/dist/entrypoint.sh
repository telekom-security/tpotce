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
    set -o allexport
    source "/data/tpot/etc/compose/elk_environment"
    LS_SSL_VERIFICATION="${LS_SSL_VERIFICATION:-full}"
    set +o allexport
fi

# Identify requests to Listbot by user agent and T-Pot UUID (only if the UUID is valid)
myLISTBOTUA="listbot"
myTPOTUUID="${HONEY_UUID:-$(cat /data/uuid 2>/dev/null)}"
if [[ ! "$myTPOTUUID" =~ ^[0-9a-fA-F-]{36}$ ]];
  then
    echo "No valid T-Pot UUID found, sending requests to Listbot without X-TPot-UUID header."
    myTPOTUUID=""
fi
myCURLOPTS=(-A "$myLISTBOTUA")
myWGETOPTS=(--user-agent="$myLISTBOTUA")
if [ -n "$myTPOTUUID" ];
  then
    myCURLOPTS+=(-H "X-TPot-UUID: $myTPOTUUID")
    myWGETOPTS+=(--header="X-TPot-UUID: $myTPOTUUID")
fi

# Check internet availability 
function fuCHECKINET () {
mySITES=$1
error=0
for i in $mySITES;
  do
    curl "${myCURLOPTS[@]}" --connect-timeout 5 -Is $i 2>&1 > /dev/null
      if [ $? -ne 0 ];
        then
          let error+=1
      fi;
  done;
  echo $error
}

# Translation maps are cached in /data/elk/listbot and only downloaded if missing or >= 24h old
myLISTBOTURL="https://listbot.sicherheitstacho.eu"
myLISTBOTCACHE="/data/elk/listbot"
myLISTBOTFILES="cve.yaml.bz2 iprep.yaml.bz2"

# Check if a cached translation map is missing, empty or 24h and older
function fuNEEDSUPDATE () {
  local myFILE=$1
  if [ ! -s "$myFILE" ] || [ -n "$(find "$myFILE" -mmin +1439)" ];
    then
      return 0
  fi
  return 1
}

# Fallback in case tpotinit did not create the cache folder
if [ ! -d "$myLISTBOTCACHE" ];
  then
    mkdir -p "$myLISTBOTCACHE"
    chmod 770 "$myLISTBOTCACHE"
fi

# Check for connectivity only if needed and download latest translation maps
myCHECK=""
for myFILE in $myLISTBOTFILES;
  do
    if ! fuNEEDSUPDATE "$myLISTBOTCACHE/$myFILE";
      then
        echo "Cached $myFILE is current (< 24h), skipping download."
        continue
    fi
    if [ -z "$myCHECK" ];
      then
        myCHECK=$(fuCHECKINET "listbot.sicherheitstacho.eu")
    fi
    if [ "$myCHECK" != "0" ];
      then
        echo "Cannot reach Listbot, skipping download of $myFILE."
        continue
    fi
    echo "Downloading latest $myFILE from Listbot."
    if wget "${myWGETOPTS[@]}" -q --no-use-server-timestamps --timeout=30 --tries=3 -O "$myLISTBOTCACHE/$myFILE.tmp" "$myLISTBOTURL/$myFILE" && \
       bunzip2 -t "$myLISTBOTCACHE/$myFILE.tmp" 2>/dev/null;
      then
        mv -f "$myLISTBOTCACHE/$myFILE.tmp" "$myLISTBOTCACHE/$myFILE"
        chmod 770 "$myLISTBOTCACHE/$myFILE"
      else
        echo "Download of $myFILE failed, keeping cached version if present."
        rm -f "$myLISTBOTCACHE/$myFILE.tmp"
    fi
done

# Provide translation maps where Logstash expects them, fall back to image defaults
for myFILE in $myLISTBOTFILES;
  do
    if [ -s "$myLISTBOTCACHE/$myFILE" ] && \
       bunzip2 -c "$myLISTBOTCACHE/$myFILE" > "/etc/listbot/${myFILE%.bz2}.tmp";
      then
        mv -f "/etc/listbot/${myFILE%.bz2}.tmp" "/etc/listbot/${myFILE%.bz2}"
        echo "Using cached $myFILE."
      else
        rm -f "/etc/listbot/${myFILE%.bz2}.tmp"
        echo "No usable cached $myFILE, starting Logstash with translation map from image."
    fi
done

# Distributed T-Pot installation needs a different pipeline config 
if [ "$TPOT_TYPE" == "SENSOR" ];
  then
    echo
    echo "Distributed T-Pot setup, sending T-Pot logs to $TPOT_HIVE_IP."
    echo
    echo "T-Pot type: $TPOT_TYPE"
    echo "Hive IP: $TPOT_HIVE_IP"
    echo "SSL verification: $LS_SSL_VERIFICATION"
    echo
   # Ensure correct file permissions for private keyfile or SSH will ask for password
    cp /usr/share/logstash/config/pipelines_sensor.yml /usr/share/logstash/config/pipelines.yml
fi

if [ "$TPOT_TYPE" != "SENSOR" ];
  then
    echo
    echo "This is a T-Pot STANDARD / HIVE installation."
    echo
    echo "T-Pot type: $TPOT_TYPE"
    echo

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

ARCH=$(arch)
if [ "$ARCH" = "aarch64" ]; then
  export _JAVA_OPTIONS="-XX:UseSVE=0";
fi
exec /usr/share/logstash/bin/logstash --config.reload.automatic
