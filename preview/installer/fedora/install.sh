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

if [ -f /var/log/fedora-install-lock ]; then
  echo "Error: The installer has already been run on this system. If you wish to run it again, please run the uninstall.sh first."
  exit 1
fi

# Create installer lock file
sudo touch /var/log/fedora-install-lock

# Update SSH config
echo "Updating SSH config..."
sudo bash -c 'echo "Port 64295" >> /etc/ssh/sshd_config'

# Update DNS config
echo "Updating DNS config..."
sudo bash -c "sed -i 's/^.*DNSStubListener=.*/DNSStubListener=no/' /etc/systemd/resolved.conf"
sudo systemctl restart systemd-resolved.service

# Update SELinux config
echo "Updating SELinux config..."
sudo sed -i s/SELINUX=enforcing/SELINUX=permissive/g /etc/selinux/config

# Update Firewall rules
echo "Updating Firewall rules..."
sudo firewall-cmd --permanent --add-port=64295/tcp
sudo firewall-cmd --permanent --zone=public --set-target=ACCEPT
#sudo firewall-cmd --reload
sudo firewall-cmd --list-all

# Load kernel modules
echo "Loading kernel modules..."
sudo modprobe -v iptable_filter
echo "iptable_filter" | sudo tee /etc/modules-load.d/iptables.conf

# Add Docker to repositories, install latest docker
echo "Adding Docker to repositories and installing..."
sudo dnf -y update
sudo dnf -y install dnf-plugins-core
sudo dnf -y config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker

# Install recommended packages
echo "Installing recommended packages..."
sudo dnf -y install bash-completion git grc net-tools

# Add user to Docker group
echo "Adding user to Docker group..."
sudo usermod -aG docker $(whoami)

# Add aliases
echo "Adding aliases..."
echo "alias dps='grc docker ps -a'" >> ~/.bashrc
echo "alias dpsw='watch -c \"grc --colour=on docker ps -a\"'" >> ~/.bashrc

# Show running services
sudo grc netstat -tulpen
echo "Please review for possible honeypot port conflicts."
echo "While SSH is taken care of, other services such as"
echo "SMTP, HTTP, etc. might prevent T-Pot from starting."

echo "Done. Please reboot and re-connect via SSH on tcp/64295."

