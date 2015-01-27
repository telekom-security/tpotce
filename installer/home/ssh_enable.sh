#!/bin/bash

########################################################
# T-Pot Community Edition                              #
# SSH enable script                                    #
#                                                      #
# v0.21 by mo, DTAG, 2015-01-27                        #
########################################################

if ! [ -f /etc/init/ssh.override ];
  then echo "### SSH is already enabled. Exiting."
  exit 1;
fi

echo "### This script will enable the ssh service (default port tcp/64295)."
echo "### Password authentication is disabled by default."

while true 
do
  echo -n "### Do you want to continue (y/n)? "; read myANSWER;
  case $myANSWER in
    n)
      echo "### Exiting."
      exit 0;
      ;;
    y)
      break
      ;;
  esac
done
sudo rm /etc/init/ssh.override
sudo service ssh start
