#!/bin/bash

########################################################
# T-Pot                                                #
# Export docker images maker                           #
#                                                      #
# v16.03.1 by mo, DTAG, 2016-03-09                     #
########################################################

# This feature is experimental and requires at least docker 1.7!
# Using any docker version < 1.7 may result in a unusable T-Pot installation

# This script will download the docker images and export them to the folder "images".
# When building the .iso image the preloaded docker images will be exported to the .iso which
# may be useful if you need to install more than one machine.

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Please run as root ..."
    exit
fi

if [ -z $1 ]
  then
    echo "Please view the script for more details!"
    exit
fi
if [ $1 == "now" ]
  then
  for name in $(cat installer/data/imgcfg/all_images.conf)
    do
      docker pull dtagdevsec/$name:latest1610
    done
  mkdir images
  chmod 777 images
  for name in $(cat installer/data/full_images.conf)
    do
      echo "Now exporting dtagdevsec/$name:latest1603"
      docker save -o images/$name:latest1610.img dtagdevsec/$name:latest1610
  done
  chmod 777 images/*.img
fi
