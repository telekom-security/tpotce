#!/bin/bash

# Needs to run as non-root
myWHOAMI=$(whoami)
if [ "$myWHOAMI" == "root" ]
  then
    echo "Need to run as user ..."
    exit
fi

# Check if running on OpenSuse Tumbleweed
if ! grep -q 'ID="opensuse-tumbleweed"' /etc/os-release; then
  echo "This script is designed to run on OpenSuse Tumbleweed. Aborting."
  exit 1
fi

if [ ! -f /var/log/suse-install-lock ]; then
  echo "Error: The installer has not been run on this system. Aborting uninstallation."
  exit 1
fi

# Remove SSH config changes
echo "Removing SSH config changes..."
sudo sed -i '/Port 64295/d' /etc/ssh/sshd_config.d/port.conf

# Remove Firewall rules
echo "Removing Firewall rules..."
sudo firewall-cmd --permanent --remove-port=64295/tcp
sudo firewall-cmd --permanent --zone=public --set-target=default
#sudo firewall-cmd --reload
sudo firewall-cmd --list-all

# Uninstall Docker
echo "Stopping and removing all containers ..."
docker stop $(docker ps -aq)
docker rm $(docker ps -aq)
echo "Uninstalling Docker..."
sudo systemctl stop docker
sudo systemctl disable docker
sudo zypper -n remove docker docker-compose
sudo zypper -n install cups postfix

# Remove user from Docker group
echo "Removing user from Docker group..."
sudo gpasswd -d $(whoami) docker

# Remove aliases
echo "Removing aliases..."
sed -i '/alias dps=/d' ~/.bashrc
sed -i '/alias dpsw=/d' ~/.bashrc

# Remove installer lock file
sudo rm /var/log/suse-install-lock

echo "Done. Please reboot and re-connect via SSH on tcp/22"

