#!/bin/bash

########################################################
# T-Pot Community Edition                              #
# Container and services restart script                #
#                                                      #
# v0.13 by mo, DTAG, 2015-02-19                        #
########################################################

if [ -a /var/run/check.lock ];
  then exit
fi

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
