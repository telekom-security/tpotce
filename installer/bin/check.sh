#!/bin/bash

########################################################
# T-Pot Community Edition                              #
# Check container and services script                  #
#                                                      #
# v0.13 by mo, DTAG, 2015-06-12                        #
########################################################
if [ -a /var/run/check.lock ];
  then exit
fi

myIMAGES=$(cat /data/images.conf)

touch /var/run/check.lock

myUPTIME=$(awk '{print int($1/60)}' /proc/uptime)
for i in $myIMAGES
  do 
    myCIDSTATUS=$(docker exec $i supervisorctl status)
      if [ $? -ne 0 ]; 
        then
          myCIDSTATUS=1 
        else 
          myCIDSTATUS=$(echo $myCIDSTATUS | egrep -c "(STOPPED|FATAL)")
      fi
      if [ $myCIDSTATUS -gt 0 ]; 
        then
          if [ $myUPTIME -gt 5 ]; 
            then
              for j in $myIMAGES
                do
                  service $j stop
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
              docker rm $(docker ps -aq)
              for j in $myIMAGES
                do
                  service $j start
                  sleep $(((RANDOM %5)+5))
              done
              rm /var/run/check.lock
              exit
          fi
      fi
done

rm /var/run/check.lock
