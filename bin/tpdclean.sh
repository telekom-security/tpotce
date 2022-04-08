#!/bin/bash
# T-Pot Compose and Container Cleaner
# Set colors
myRED="[0;31m"
myGREEN="[0;32m"
myWHITE="[0;0m"

# Only run with command switch
if [ "$1" != "-y" ]; then
  echo $myRED"### WARNING"$myWHITE
  echo ""
  echo $myRED"###### This script is only intended for the tpot.service."$myWHITE
  echo $myRED"###### Run <systemctl stop tpot> first and then <tpdclean.sh -y>."$myWHITE
  echo $myRED"###### Be aware, all T-Pot container volumes and images will be removed."$myWHITE
  echo ""
  echo $myRED"### WARNING "$myWHITE
  echo
  exit
fi

# Remove old containers, images and volumes
docker-compose -f /opt/tpot/etc/tpot.yml down -v >> /dev/null 2>&1
docker-compose -f /opt/tpot/etc/tpot.yml rm -v >> /dev/null 2>&1
docker network rm $(docker network ls -q) >> /dev/null 2>&1
docker volume rm $(docker volume ls -q) >> /dev/null 2>&1
docker rm -v $(docker ps -aq) >> /dev/null 2>&1
docker rmi $(docker images | grep "<none>" | awk '{print $3}') >> /dev/null 2>&1
docker rmi $(docker images | grep "2203" | awk '{print $3}') >> /dev/null 2>&1
exit 0
