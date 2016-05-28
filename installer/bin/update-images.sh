#!/bin/bash

########################################################
# T-Pot                                                #
# Only start the containers found in /etc/init/        #
#                                                      #
# v16.10.0 by mo, DTAG, 2016-05-12                     #
########################################################

echo "### I still need some dev-work!"

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

# Stop T-Pot services and delete all T-Pot upstart scripts
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
echo "### Stopping T-Pot services and cleaning up."
for i in $(cat /data/imgcfg/all_images.conf);
  do
    systemctl stop $i
    sleep 2
    systemctl disable $i;
done
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

# Restarting docker services
echo "### Restarting docker services ..."
systemctl stop docker
sleep 2
systemctl start docker
sleep 2

# Setup only T-Pot upstart scripts from images.conf and pull the images
for i in $(cat /data/images.conf);
  do
    docker pull dtagdevsec/$i:latest1603;
    systemctl enable $i;
done

# Announce reboot
echo "### Rebooting in 60 seconds for the changes to take effect."
sleep 60

# Allow checks to resume
rm /var/run/check.lock

# Reboot
reboot
