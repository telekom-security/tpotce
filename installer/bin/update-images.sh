#!/bin/bash

##########################################################
# T-Pot                                                  #
# Only start the containers found in /etc/systemd/system #
#                                                        #
# v17.06 by mo, DTAG, 2017-03-13                         #
##########################################################

# Make sure not to interrupt a check
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

# We do not want to get interrupted by a check
touch /var/run/check.lock

# Stop T-Pot services and disable all T-Pot services
echo "### Stopping T-Pot services and cleaning up."
for i in $(cat /data/all_images.conf);
  do
    systemctl stop $i
    sleep 2
    systemctl disable $i;
    rm /etc/systemd/system/$i.service
done

# Restarting docker services and optionally clear local repository
echo "### Stopping docker services ..."
systemctl stop docker
sleep 1
# If option "hard" clear the whole repository
if [ "$1" = "hard" ];
  then
    echo "### Clearing local docker repository."
    rm -rf /var/lib/docker
    sleep 1
fi
echo "### Starting docker services ..."
systemctl start docker
sleep 1

# Enable only T-Pot upstart scripts from images.conf and pull the images
for i in $(cat /data/imgcfg/images.conf);
  do
    echo
    echo "### Now pulling "$i
    docker pull dtagdevsec/$i:1706;
    cp /data/systemd/$i.service /etc/systemd/system/
    systemctl enable $i;
done

# Announce reboot
echo
echo "### Rebooting."

# Allow checks to resume
rm /var/run/check.lock

# Reboot
reboot
