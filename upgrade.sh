#!/bin/bash
myLOCK="/var/run/check.lock"
myIMAGECONFPATH="/data/images.conf"

# Let's set check.lock to prevent the check scripts from execution
touch $myLOCK

# Let's stop all docker and t-pot related services
for i in $(cat $myIMAGECONFPATH); do service $i stop; done
service docker stop

# Since there are different versions out there let's update to the latest version first
apt-get update -y
apt-get upgrade -y
apt-get install lxc-docker -y

# Let's remove deprecated lxc-docker
apt-get purge lxc-docker -y
apt-get autoremove -y
rm /etc/apt/sources.list.d/docker.list

# Let's install docker
echo "### Installing docker."
wget -qO- https://get.docker.com/gpg | apt-key add -
wget -qO- https://get.docker.com/ | sh

tee -a /etc/crontab <<EOF
# Check for updated packages every sunday, upgrade and reboot
27 16 * * 0   root  sleep \$((RANDOM %600)); apt-get autoclean -y; apt-get autoremove -y; apt-get update -y; apt-get upgrade -y; apt-get upgrade docker-engine -y; sleep 5; reboot
EOF

# Let's remove the check.lock and allow scripts to execute again
rm $myLOCK

# Let's restart the containers
/usr/bin/dcres.sh

# Let's reboot if so desired
echo "Done. Will reboot in 60 seconds, press CTRL+C now to abort."
sleep 60
reboot
