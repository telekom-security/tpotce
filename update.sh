#!/bin/bash

###################################################
# Do not change any contents of this script!
###################################################

# Some vars
myCONFIGFILE="/opt/tpot/etc/tpot.yml"
myCOMPOSEPATH="/opt/tpot/etc/compose"
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

# Self Update
function fuSELFUPDATE () {
  echo "### Now checking for newer files in repository ..."
  git fetch
  myREMOTESTAT=$(git status | grep -c "up-to-date")
  if [ "$myREMOTESTAT" != "0" ];
    then
      echo "###### $myBLUE"No updates found in repository."$myWHITE"
      return
  fi
  myRESULT=$(git diff --name-only origin/master | grep update.sh)
  myLOCALSTAT=$(git status -uno | grep -c update.sh)
  if [ "$myRESULT" == "update.sh" ];
    then
      if [ "$myLOCALSTAT" == "0" ];
        then
          echo "###### $myBLUE"Found newer version, will update myself and restart."$myWHITE"
          git pull --force
          exec "$1" "$2"
          exit 1
        else
          echo $myRED"Error - Update script was changed locally, cannot update."
          echo "Exiting."$myWHITE
          echo
          exit 1
      fi
    else
      echo "###### Update script is already up-to-date."
      git pull --force
  fi
}

# Let's check for version
function fuCHECK_VERSION () {
local myMINVERSION="18.04.0"
local myMASTERVERSION="18.04.0"
echo
echo -n "##### Checking for version tag: "
if [ -f "version" ];
  then
    myVERSION=$(cat version)
    echo "[ OK ] - You are running $myVERSION"
    if [[ "$myVERSION" > "$myMINVERSION" || "$myVERSION" == "$myMINVERSION" ]] && [[ "$myVERSION" < "$myMASTERVERSION" || "$myVERSION" == "$myMASTERVERSION" ]]
      then
        echo "##### Valid version found. Update procedure will be initiated."
        exit
      else
        echo "##### Your T-Pot installation cannot be upgraded automatically. Please run a fresh install."
        exit
    fi
  else
    echo "[ NOT OK ]"
    echo "##### 'version' is missing. Please run 'update.sh' from within '/opt/tpot'."
    exit
  fi
}

# Only run with command switch
if [ "$1" != "-y" ]; then
  echo "This script will update / upgrade all T-Pot related scripts, tools and packages"
  echo "Some of your changes might be overwritten, so make sure to save your work"
  echo "This is beta feature and only recommended for experienced users, run with \"-y\" switch"
  echo
  exit
fi

echo "### Now running T-Pot update script."
echo

fuCHECKINET "https://index.docker.io https://github.com https://pypi.python.org https://ubuntu.com"
echo

fuSELFUPDATE "$0" "$@"
echo

fuCONFIGCHECK
echo

echo "### Now stopping T-Pot"
systemctl stop tpot

# Better safe than sorry
echo "###### Creating backup and storing it in /home/tsec"
tar cvfz /root/tpot_backup.tgz /opt/tpot

echo "###### Getting the current install flavor"
myFLAVOR=$(head $myCONFIGFILE -n 1 | awk '{ print $3 }' | tr -d :'()':)

echo "###### Updating compose file"
case $myFLAVOR in
  HP)
    echo "###### Restoring HONEYPOT flavor installation."
    cp $myCOMPOSEPATH/hp.yml $myCONFIGFILE
  ;;
  Industrial)
    echo "###### Restoring INDUSTRIAL flavor installation."
    cp $myCOMPOSEPATH/industrial.yml $myCONFIGFILE
  ;;
  Standard)
    echo "###### Restoring TPOT flavor installation."
    cp $myCOMPOSEPATH/tpot.yml $myCONFIGFILE
  ;;
  Everything)
    echo "###### Restoring EVERYTHING flavor installation."
    cp $myCOMPOSEPATH/all.yml $myCONFIGFILE
  ;;
esac

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
echo "### If you made changes to tpot.yml please ensure to add them again."
echo "### We stored the previous version as backup in /home/tsec."
echo "### Done."
