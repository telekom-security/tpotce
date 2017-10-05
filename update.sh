#!/bin/bash

# Some vars
myCONFIGFILE="/opt/tpot/etc/tpot.yml"
myRED="[0;31m"
myGREEN="[0;32m"
myWHITE="[0;0m"
myBLUE="[0;34m"

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    sudo ./$0
    exit
fi

# Check for existing tpot.yml
function fuCONFIGCHECK () {
  echo "### Checking for T-Pot configuration file ..."
  echo -n "###### $myBLUE$myCONFIGFILE$myWHITE "
  if ! [ -f $myCONFIGFILE ]; 
    then
      echo
      echo $myRED"Error - No T-Pot configuration file present."
      echo "Please copy one of the preconfigured configuration files from /opt/tpot/etc/compose/*.yml to /opt/tpot/etc/tpot.yml."$myWHITE
      echo
      exit 1
    else
      echo $myGREEN"OK"$myWHITE
  fi
}

# Let's test the internet connection
function fuCHECKINET () {
mySITES=$1
  echo "### Now checking availability of ..."
  for i in $mySITES;
    do
      echo -n "###### $myBLUE$i$myWHITE "
      curl --connect-timeout 5 -IsS $i 2>&1>/dev/null
        if [ $? -ne 0 ];
          then
            echo
            echo $myRED"Error - Internet connection test failed. This might indicate some problems with your connection."
            echo "Exiting."$myWHITE
            echo
            exit 1
          else
            echo $myGREEN"OK"$myWHITE
        fi
  done;
}

# Only run with command switch
if [ "$1" != "-y" ]; then
  echo "This script will update / upgrade all T-Pot related scripts, tools and packages" 
  echo "Some of your changes might be overwritten, so make sure to save your work"
  echo "This feature is still experimental, run with \"-y\" switch" 
  echo
  exit
fi

echo "### Now running T-Pot update script."
echo

fuCHECKINET "https://index.docker.io https://github.com https://pypi.python.org https://ubuntu.com"
echo

fuCONFIGCHECK
echo

echo "### Now stopping T-Pot"
systemctl stop tpot

echo
echo "### Now upgrading packages"
apt-get autoclean -y
apt-get autoremove -y
apt-get update
apt-get dist-upgrade -y
pip install --upgrade pip
pip install docker-compose==1.16.1
pip install elasticsearch-curator==5.2.0
ln -s /usr/bin/nodejs /usr/bin/node 2>&1
npm install https://github.com/t3chn0m4g3/wetty -g
npm install https://github.com/t3chn0m4g3/elasticsearch-dump -g
wget https://github.com/bcicen/ctop/releases/download/v0.6.1/ctop-0.6.1-linux-amd64 -O /usr/bin/ctop && chmod +x /usr/bin/ctop

echo
echo "### Now pulling T-Pot Repo"
git pull

echo
echo "### Now replacing T-Pot related config files on host"
cp    host/etc/systemd/* /etc/systemd/system/
cp    host/etc/issue /etc/
cp -R host/etc/nginx/ssl /etc/nginx/
cp    host/etc/nginx/tpotweb.conf /etc/nginx/sites-available/
cp    host/etc/nginx/nginx.conf /etc/nginx/nginx.conf
cp    host/usr/share/nginx/html/* /usr/share/nginx/html/

echo
echo "### Now reloading systemd, nginx"
systemctl daemon-reload
nginx -s reload

echo
echo "### Now restarting wetty, nginx, docker"
systemctl restart wetty.service
systemctl restart nginx.service
systemctl restart docker.service

echo
echo "### Now pulling latest docker images"
docker-compose -f /opt/tpot/etc/tpot.yml pull

echo
echo "### Now starting T-Pot service"
systemctl start tpot

echo
echo "### Done. If all services run correctly (dps.sh) you should perform a reboot."
