#!/bin/bash

########################################################
# T-Pot                                                #
# Container and services restart script                #
#                                                      #
# v0.03 by mo, DTAG, 2015-11-02                        #
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
if [ $myUPTIME -gt 5 ];
  then
    for i in $myIMAGES
      do
        service $i stop
    done
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
    docker rm -v $(docker ps -aq)
    docker rmi $(docker images | grep "^<none>" | awk '{print $3}')
    for i in $myIMAGES
      do
        service $i start
        sleep $(((RANDOM %5)+5))
    done
fi

rm /var/run/check.lock

/etc/rc.local
