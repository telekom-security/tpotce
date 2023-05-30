#!/bin/bash

# Needs to run as non-root
myWHOAMI=$(whoami)
if [ "$myWHOAMI" == "root" ]
  then
    echo "Need to run as user ..."
    exit
fi

# Check if running on Debian
if ! grep -q 'ID=debian' /etc/os-release; then
  echo "This script is designed to run on Debian. Aborting."
  exit 1
fi

if [ -f /var/log/debian-install-lock ]; then
  echo "Error: The installer has already been run on this system. If you wish to run it again, please run the uninstall.sh first."
  exit 1
fi

# Create installer lock file
sudo touch /var/log/debian-install-lock

# Update SSH config
echo "Updating SSH config..."
sudo bash -c 'echo "Port 64295" >> /etc/ssh/sshd_config'

# Install recommended packages
echo "Installing recommended packages..."
sudo apt-get -y update
sudo apt-get -y install bash-completion git grc neovim net-tools

# Remove old Docker
echo "Removing old docker packages..."
sudo apt-get -y remove docker docker-engine docker.io containerd runc

# Add Docker to repositories, install latest docker
echo "Adding Docker to repositories and installing..."
sudo apt-get -y update
sudo apt-get -y install ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get -y update
sudo apt-get -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable docker
sudo systemctl stop docker
sudo systemctl start docker

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

