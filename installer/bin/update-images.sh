#!/bin/bash

########################################################
# T-Pot                                                #
# Only start the containers found in /etc/init/        #
#                                                      #
# v16.03.1 by mo, DTAG, 2016-03-09                     #
########################################################

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

# Delete all T-Pot upstart scripts
for i in $(ls /data/upstart/);
  do
    rm -rf /etc/init/$i || true;
done

# Setup only T-Pot upstart scripts from images.conf and pull the images
for i in $(cat /data/images.conf);
  do
    docker pull dtagdevsec/$i:latest1603;
    cp /data/upstart/"$i".conf /etc/init/;
done

# Allow checks to resume
rm /var/run/check.lock

# Announce reboot
echo "### Rebooting in 60 seconds for the changes to take effect."
sleep 60

# Reboot
reboot
