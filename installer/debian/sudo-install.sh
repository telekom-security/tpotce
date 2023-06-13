#!/bin/bash

if ! command -v sudo &> /dev/null
then
    echo "sudo is not installed. Installing now..."
    su -c "apt-get -y update && apt-get -y install sudo"
    su -c "/usr/sbin/usermod -aG sudo $(whoami)"
else
    echo "sudo is already installed."
fi
