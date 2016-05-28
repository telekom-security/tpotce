#!/bin/bash

########################################################
# T-Pot                                                #
# Container Data Cleaner                               #
#                                                      #
# v16.10.0 by mo, DTAG, 2016-05-28                     #
########################################################

# Set persistence
myPERSISTENCE=$2

# Check persistence
if [ "$myPERSISTENCE" = "on" ];
  then
    echo "### Persistence enabled, nothing to do."
    exit
fi

# Let's create a function to clean up dionaea data
fuDIONAEA () {
  rm -rf /data/dionaea/*
  rm /data/ews/dionaea/ews.json
  mkdir -p /data/dionaea/log /data/dionaea/bistreams /data/dionaea/binaries /data/dionaea/rtp /data/dionaea/wwwroot
  chmod 760 /data/dionaea -R
  chown tpot:tpot /data/dionaea -R
}

case $1 in
  dionaea)
    fuDIONAEA $1
  ;;
esac
