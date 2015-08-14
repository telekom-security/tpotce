#!/bin/bash
########################################################
# T-Pot Community Edition                              #
# Volume bug fix script                                #
#                                                      #
# v0.02 by mo, DTAG, 2015-08-14                        #
########################################################
myFIXPATH="/tpot-volume-fix"
myLOCK="/var/run/check.lock"
myIMAGECONFPATH="/data/images.conf"

# Let's set check.lock to prevent the check scripts from execution
touch $myLOCK

# Since there are different versions out there let's update to the latest version first
apt-get update -y
apt-get upgrade -y
apt-get install lxc-docker -y

# Let's stop all docker and t-pot related services
for i in $(cat $myIMAGECONFPATH); do service $i stop; done
service docker stop

# Let's create a tmp and move some configs to prevent unwanted intervention
mkdir $myFIXPATH
for i in $(cat $myIMAGECONFPATH); do mv /etc/init/$i.conf $myFIXPATH; done
mv /etc/crontab $myFIXPATH

# Let's remove docker and all associated files
apt-get purge lxc-docker -y
apt-get autoremove -y
rm -rf /var/lib/docker/
rm -rf /var/run/docker/

# Let's reinstall docker using the new docker repo (old one is deprecated)
wget -qO- https://get.docker.com/gpg | apt-key add -
wget -qO- https://get.docker.com/ | sh

# Let's pull the images
for i in $(cat $myIMAGECONFPATH); do /usr/bin/docker pull dtagdevsec/$i:latest; done

# Let's clone the tpotce repo and replace the buggy configs
git clone https://github.com/dtag-dev-sec/tpotce.git $myFIXPATH/tpotce/
cp $myFIXPATH/tpotce/installer/bin/check.sh /usr/bin/
cp $myFIXPATH/tpotce/installer/bin/dcres.sh /usr/bin/
for i in $(cat $myIMAGECONFPATH); do cp $myFIXPATH/tpotce/installer/upstart/$i.conf /etc/init/; done
cp $myFIXPATH/crontab /etc/
tee -a /etc/crontab <<EOF

# Check for updated packages every sunday, upgrade and reboot
27 16 * * 0   root  sleep \$((RANDOM %600)); apt-get autoclean -y; apt-get autoremove -y; apt-get update -y; apt-get upgrade -y; apt-get upgrade docker-engine -y; sleep 5; reboot
EOF

# Let's remove the check.lock and allow scripts to execute again
rm $myLOCK

# Let's start the services again
for i in $(cat $myIMAGECONFPATH); do service $i start && sleep 2; done
sleep 10
status.sh
