#!/bin/bash
# Show status of SupervisorD within running containers
myCOUNT=1

if [[ $1 == "" ]]
  then
    myIMAGES=$(cat /etc/tpot/tpot.yml | grep container_name | cut -d: -f2)
  else myIMAGES=$1
fi

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
echo "======| System |======"
echo Date:"     "$(date)
echo Uptime:"  "$(uptime)
echo CPU temp: $(sensors | grep "Physical" | awk '{ print $4 }')
echo
for i in $myIMAGES
do
  if [ "$i" != "ui-for-docker" ] && [ "$i" != "netdata" ] && [ "$i" != "spiderfoot" ];
  then
    echo "======| Container:" $i "|======"
    docker exec $i supervisorctl status | GREP_COLORS='mt=01;32' egrep --color=always "(RUNNING)|$" | GREP_COLORS='mt=01;31' egrep --color=always "(STOPPED|FATAL)|$"
    echo
  fi
done
