#!/bin/bash

########################################################
# T-Pot Community Edition                              #
# Container and services status script                 #
#                                                      #
# v0.11 by mo, DTAG, 2015-06-12                        #
########################################################
myCOUNT=1
myIMAGES=$(cat /data/images.conf)
while true
do
  if ! [ -a /var/run/check.lock ];
    then break
  fi
  sleep 0.1
  if [ $myCOUNT = 1 ];
    then
      echo -n "Waiting for services "
    else echo -n .
  fi
  if [ $myCOUNT = 300 ];
    then
    echo
    echo "Services are busy or not available. Please retry later."
    exit 1
  fi
  myCOUNT=$[$myCOUNT +1]
done
echo
echo
echo "****************** $(date) ******************"
echo
echo
for i in $myIMAGES
do
  echo
  echo "======| Container:" $i "|======"
  docker exec $i supervisorctl status | GREP_COLORS='mt=01;32' egrep --color=always "(RUNNING)|$" | GREP_COLORS='mt=01;31' egrep --color=always "(STOPPED|FATAL)|$"
  echo
done
