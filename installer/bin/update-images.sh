#!/bin/bash

########################################################
# T-Pot                                                #
# Only start the container found in /etc/init/t-pot    #
#                                                      #
# v0.02 by mo, DTAG, 2016-02-08                        #
########################################################

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

echo Please reboot for the changes to take effect.
