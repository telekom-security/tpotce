#!/bin/bash

########################################################
# T-Pot                                                #
# Container and services restart script                #
#                                                      #
# v0.04 by mo, DTAG, 2016-02-12                        #
########################################################
myCOUNT=1

while true
do
  if ! [ -a /var/run/check.lock ];
    then break
  fi
  sleep 0.1
  if [ "$myCOUNT" = "1" ];
    then
      echo -n "Waiting for services "
    else echo -n .
  fi
  if [ "$myCOUNT" = "6000" ];
    then
      echo
      echo "Overriding check.lock"
      rm /var/run/check.lock
    break
  fi
  myCOUNT=$[$myCOUNT +1]
done

myIMAGES=$(cat /data/images.conf)

touch /var/run/check.lock

myUPTIME=$(awk '{print int($1/60)}' /proc/uptime)
if [ $myUPTIME -gt 4 ];
  then
    for i in $myIMAGES
      do
        service $i stop
    done
    echo "Waiting 10 seconds before restarting docker ..."
    sleep 10
    iptables -w -F
    service docker restart
    while true
      do
        docker info > /dev/null
        if [ $? -ne 0 ];
          then
            echo Docker daemon is still starting.
          else
            echo Docker daemon is now available.
            break
        fi
        sleep 0.1
    done
    echo "Docker is now up and running again."
    echo "Removing obsolete container data ..."
    docker rm -v $(docker ps -aq)
    echo "Removing obsolete image data ..."
    docker rmi $(docker images | grep "^<none>" | awk '{print $3}')
    echo "Starting T-Pot services ..."
    for i in $myIMAGES
      do
        service $i start
    done
    sleep 5
  else 
    echo "T-Pot needs to be up and running for at least 5 minutes."
fi

rm /var/run/check.lock

/etc/rc.local

echo "Done. Now running status.sh"
/usr/bin/status.sh

