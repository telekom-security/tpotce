#!/bin/bash

########################################################
# T-Pot                                                #
# Only start the container found in /etc/init/t-pot    #
#                                                      #
# v0.01 by mo, DTAG, 2016-02-08                        #
########################################################

rm -rf /etc/init/t-pot/*.conf || true
for i in $(cat /data/images.conf);
  do 
    cp /data/upstart/"$i".conf /etc/init/t-pot/;
done
echo Please reboot for the changes to take effect.
