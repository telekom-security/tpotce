#!/bin/bash

########################################################
# T-Pot Community Edition                              #
# Check container and services script                  #
#                                                      #
# v0.10 by mo, DTAG, 2015-01-27                        #
########################################################

if [ -f /var/run/check.lock ];
  then exit
fi

touch /var/run/check.lock

myUPTIME=$(awk '{print int($1/60)}' /proc/uptime)
for i in dionaea elk ews glastopf honeytrap kippo suricata
do 
  myCIDSTATUS=$(docker exec -i $i supervisorctl status)
  if [ $? -ne 0 ]; then
    myCIDSTATUS=1 
  else 
    myCIDSTATUS=$(echo $myCIDSTATUS | egrep -c "(STOPPED|FATAL)")
  fi
  if [ $myCIDSTATUS -gt 0 ]; then
    if [ $myUPTIME -gt 5 ]; then
      service docker stop
      docker rm $(docker ps -aq)
      service docker start
      for j in dionaea glastopf honeytrap kippo suricata ews elk
      do
        sleep 10
        service $j start
      done
      rm /var/run/check.lock
      exit 0
    fi
  fi
done

rm /var/run/check.lock

