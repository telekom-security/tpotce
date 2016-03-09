#!/bin/bash

########################################################
# T-Pot                                                #
# Check container and services script                  #
#                                                      #
# v16.03.1 by mo, DTAG, 2016-03-09                     #
########################################################
if [ -a /var/run/check.lock ];
  then
    echo "Lock exists. Exiting now."
    exit
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
      if [ $myUPTIME -gt 4 ] && [ $myCIDSTATUS -gt 0 ];
        then
          echo "Restarting "$i"."
          service $i stop
          sleep 5
          service $i start
      fi
done

rm /var/run/check.lock
