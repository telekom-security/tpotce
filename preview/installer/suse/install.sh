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

if [ -f /var/log/suse-install-lock ]; then
  echo "Error: The installer has already been run on this system. If you wish to run it again, please run the uninstall.sh first."
  exit 1
fi

# Create installer lock file
sudo touch /var/log/suse-install-lock

# Update SSH config
echo "Updating SSH config..."
sudo bash -c 'echo "Port 64295" >> /etc/ssh/sshd_config.d/port.conf'

# Update Firewall rules
echo "Updating Firewall rules..."
sudo firewall-cmd --permanent --add-port=64295/tcp
sudo firewall-cmd --permanent --zone=public --set-target=ACCEPT
#sudo firewall-cmd --reload
sudo firewall-cmd --list-all

# Install docker and recommended packages
echo "Installing recommended packages..."
sudo zypper -n update
sudo zypper -n remove cups net-tools postfix yast2-auth-client yast2-auth-server
sudo zypper -n install bash-completion docker docker-compose git grc busybox-net-tools

# Enable and start docker
echo "Enabling and starting docker..."
systemctl enable docker
systemctl start docker

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

