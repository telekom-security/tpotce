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

exit


# notizen

MY_TPOT_TYPE Standard = SINGLE, Distributed = POT

Wenn POT
autossh -f -M 0 -4 -l tpot01 -i /data/elk/logstash/tpot01 -p 64295 -N -L64305:127.0.0.1:64305 172.20.254.194 -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -o "StrictHostKeyChecking=no" -o "UserKnownHostsFile=/dev/null"
exec /usr/share/logstash/bin/logstash -f /etc/logstash/conf.d/http_output.conf --config.reload.automatic --java-execution


Wenn SINGLE
exec /usr/share/logstash/bin/logstash --config.reload.automatic --java-execution

Umgebungsvariable holen aus /data/elk/logstash
m besten über das ELK Environment file, damit es keine probleme gibt

