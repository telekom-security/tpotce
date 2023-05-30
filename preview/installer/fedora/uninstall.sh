#!/bin/bash

# Needs to run as non-root
myWHOAMI=$(whoami)
if [ "$myWHOAMI" == "root" ]
  then
    echo "Need to run as user ..."
    exit
fi

# Check if running on Fedora
if ! grep -q 'ID=fedora' /etc/os-release; then
  echo "This script is designed to run on Fedora. Aborting."
  exit 1
fi

if [ ! -f /var/log/fedora-install-lock ]; then
  echo "Error: The installer has not been run on this system. Aborting uninstallation."
  exit 1
fi

# Remove SSH config changes
echo "Removing SSH config changes..."
sudo sed -i '/Port 64295/d' /etc/ssh/sshd_config

# Remove DNS config changes
echo "Updating DNS config..."
sudo bash -c "sed -i 's/^.*DNSStubListener=.*/#DNSStubListener=yes/' /etc/systemd/resolved.conf"
sudo systemctl restart systemd-resolved.service

# Restore SELinux config
echo "Restoring SELinux config..."
sudo sed -i s/SELINUX=permissive/SELINUX=enforcing/g /etc/selinux/config

# Remove Firewall rules
echo "Removing Firewall rules..."
sudo firewall-cmd --permanent --remove-port=64295/tcp
sudo firewall-cmd --permanent --zone=public --set-target=default
#sudo firewall-cmd --reload
sudo firewall-cmd --list-all

# Unload kernel modules
echo "Unloading kernel modules..."
sudo modprobe -rv iptable_filter
sudo rm /etc/modules-load.d/iptables.conf

# Uninstall Docker
echo "Stopping and removing all containers ..."
docker stop $(docker ps -aq)
docker rm $(docker ps -aq)
echo "Uninstalling Docker..."
sudo systemctl stop docker
sudo systemctl disable docker
sudo dnf -y remove docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo dnf config-manager --disable docker-ce-stable
sudo rm /etc/yum.repos.d/docker-ce.repo

# Remove user from Docker group
echo "Removing user from Docker group..."
sudo gpasswd -d $(whoami) docker

# Remove aliases
echo "Removing aliases..."
sed -i '/alias dps=/d' ~/.bashrc
sed -i '/alias dpsw=/d' ~/.bashrc

# Remove installer lock file
sudo rm /var/log/fedora-install-lock

echo "Done. Please reboot and re-connect via SSH on tcp/22"

