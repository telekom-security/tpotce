#!/bin/bash

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi

openssl req -nodes -x509 -sha512 -newkey rsa:8192 -keyout "nginx.key" -out "nginx.crt" -days 3650

