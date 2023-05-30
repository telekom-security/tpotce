#!/bin/bash

# Needs to run as non-root
myWHOAMI=$(whoami)
if [ "$myWHOAMI" == "root" ]
  then
    echo "Need to run as user ..."
    exit
fi

# Check if running on Ubuntu
if ! grep -q 'ID=ubuntu' /etc/os-release; then
  echo "This script is designed to run on Ubuntu. Aborting."
  exit 1
fi

# Check if installer lock file exists
if [ ! -f /var/log/ubuntu-install-lock ]; then
  echo "Error: The installer has not been run on this system. Aborting."
  exit 1
fi

# Remove SSH config changes
echo "Removing SSH config changes..."
sudo sed -i '/Port 64295/d' /etc/ssh/sshd_config
sudo systemctl disable ssh.service
sudo systemctl enable ssh.socket

# Remove DNS config changes
echo "Updating DNS config..."
sudo bash -c "sed -i 's/^.*DNSStubListener=.*/#DNSStubListener=yes/' /etc/systemd/resolved.conf"
sudo systemctl restart systemd-resolved.service

# Uninstall Docker
echo "Stopping and removing all containers ..."
docker stop $(docker ps -aq)
docker rm $(docker ps -aq)
echo "Uninstalling Docker..."
sudo systemctl stop docker
sudo systemctl disable docker
sudo apt-get -y remove docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo apt-get -y autoremove
sudo rm -rf /etc/apt/sources.list.d/docker.list
sudo rm -rf /etc/apt/keyrings/docker.gpg

# Remove user from Docker group
echo "Removing user from Docker group..."
sudo deluser $(whoami) docker

# Remove aliases
echo "Removing aliases..."
sed -i '/alias dps=/d' ~/.bashrc
sed -i '/alias dpsw=/d' ~/.bashrc

# Remove installer lock file
sudo rm -f /var/log/ubuntu-install-lock

echo "Done. Please reboot and re-connect via SSH on tcp/22"

