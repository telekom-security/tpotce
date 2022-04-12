#!/bin/bash

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi

if [ "$1" = "2048" ] || [ "$1" = "4096" ] || [ "$1" = "8192" ]
  then 
    openssl dhparam -outform PEM -out dhparam$1.pem $1
  else
    echo "Usage: ./gen-dhparam [2048, 4096, 8192]..."
fi
