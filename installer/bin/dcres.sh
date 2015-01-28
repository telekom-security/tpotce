#!/bin/bash

########################################################
# T-Pot Community Edition                              #
# Container and services restart script                #
#                                                      #
# v0.10 by mo, DTAG, 2015-01-28                        #
########################################################

if [ -f /var/run/check.lock ];
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
    docker rm $(docker ps -aq)
    for i in $myIMAGES
      do
        service $i start
        sleep $(((RANDOM %5)+5))
    done
fi

rm /var/run/check.lock

