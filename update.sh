#!/bin/bash

# Some global vars
myCONFIGFILE="/opt/tpot/etc/tpot.yml"
myCOMPOSEPATH="/opt/tpot/etc/compose"
myRED="[0;31m"
myGREEN="[0;32m"
myWHITE="[0;0m"
myBLUE="[0;34m"

# Check for existing tpot.yml
function fuCONFIGCHECK () {
  echo "### Checking for T-Pot configuration file ..."
  if ! [ -L $myCONFIGFILE ];
    then
      echo -n "###### $myBLUE$myCONFIGFILE$myWHITE "
      myFILE=$(head -n 1 $myCONFIGFILE | tr -d "()" | tr [:upper:] [:lower:] | awk '{ print $3 }')
      myFILE+=".yml"
      echo "[ $myRED""NOT OK""$myWHITE ] - Broken symlink, trying to reset to '$myFILE'."
      rm -rf $myCONFIGFILE
      ln -s $myCOMPOSEPATH/$myFILE $myCONFIGFILE
  fi
  if [ -L $myCONFIGFILE ];
    then
      echo "###### $myBLUE$myCONFIGFILE$myWHITE [ $myGREEN""OK""$myWHITE ]"
    else
      echo "[ $myRED""NOT OK""$myWHITE ] - Broken symlink and / or restore failed."
      echo "Please create a link to your desired config i.e. 'ln -s /opt/tpot/etc/compose/standard.yml /opt/tpot/etc/tpot.yml'."
      exit
  fi
echo
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
            echo "###### $myBLUE""Error - Internet connection test failed.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
            echo "Exiting.""$myWHITE"
            echo
            exit 1
          else
            echo "[ $myGREEN"OK"$myWHITE ]"
        fi
  done;
echo
}

# Update
function fuSELFUPDATE () {
  echo "### Now checking for newer files in repository ..."
  git fetch --all
  myREMOTESTAT=$(git status | grep -c "up-to-date")
  if [ "$myREMOTESTAT" != "0" ];
    then
      echo "###### $myBLUE""No updates found in repository.""$myWHITE"
      return
  fi
  myRESULT=$(git diff --name-only origin/master | grep update.sh)
  if [ "$myRESULT" == "update.sh" ];
    then
      echo "###### $myBLUE""Found newer version, will be pulling updates and restart myself.""$myWHITE"
      git reset --hard
      git pull --force
      exec "$1" "$2"
      exit 1
    else
      echo "###### $myBLUE""Pulling updates from repository.""$myWHITE"
      git reset --hard
      git pull --force
  fi
echo
}

# Let's check for version
function fuCHECK_VERSION () {
local myMINVERSION="19.03.0"
local myMASTERVERSION="20.06.0"
echo
echo "### Checking for Release ID"
myRELEASE=$(lsb_release -i | grep Debian -c)
if [ "$myRELEASE" == "0" ] 
  then
    echo "###### This version of T-Pot cannot be upgraded automatically. Please run a fresh install.$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
    exit
fi
echo "### Checking for version tag ..."
if [ -f "version" ];
  then
    myVERSION=$(cat version)
    if [[ "$myVERSION" > "$myMINVERSION" || "$myVERSION" == "$myMINVERSION" ]] && [[ "$myVERSION" < "$myMASTERVERSION" || "$myVERSION" == "$myMASTERVERSION" ]]
      then
        echo "###### $myBLUE$myVERSION is eligible for the update procedure.$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
      else
        echo "###### $myBLUE $myVERSION cannot be upgraded automatically. Please run a fresh install.$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	exit
    fi
  else
    echo "###### $myBLUE""Unable to determine version. Please run 'update.sh' from within '/opt/tpot'.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
    exit
  fi
echo
}


# Stop T-Pot to avoid race conditions with running containers with regard to the current T-Pot config
function fuSTOP_TPOT () {
echo "### Need to stop T-Pot ..."
echo -n "###### $myBLUE Now stopping T-Pot.$myWHITE "
systemctl stop tpot
if [ $? -ne 0 ];
  then
    echo " [ $myRED""NOT OK""$myWHITE ]"
    echo "###### $myBLUE""Could not stop T-Pot.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
    echo "Exiting.""$myWHITE"
    echo
    exit 1
  else
    echo "[ $myGREEN"OK"$myWHITE ]"
    echo "###### $myBLUE Now cleaning up containers.$myWHITE "
    if [ "$(docker ps -aq)" != "" ];
      then
        docker stop $(docker ps -aq)
        docker rm $(docker ps -aq)
    fi
fi
echo
}

# Backup
function fuBACKUP () {
local myARCHIVE="/root/$(date +%Y%m%d%H%M)_tpot_backup.tgz"
local myPATH=$PWD
echo "### Create a backup, just in case ... "
echo -n "###### $myBLUE Building archive in $myARCHIVE $myWHITE"
cd /opt/tpot
tar cvfz $myARCHIVE * 2>&1>/dev/null
if [ $? -ne 0 ];
  then
    echo " [ $myRED""NOT OK""$myWHITE ]"
    echo "###### $myBLUE""Something went wrong.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
    echo "Exiting.""$myWHITE"
    echo
    cd $myPATH
    exit 1
  else
    echo "[ $myGREEN"OK"$myWHITE ]"
    cd $myPATH
fi
echo
}

# Remove old images for specific tag
function fuREMOVEOLDIMAGES () {
local myOLDTAG=$1
local myOLDIMAGES=$(docker images | grep -c "$myOLDTAG")
if [ "$myOLDIMAGES" -gt "0" ];
  then
    echo "### Removing old docker images."
    docker rmi $(docker images | grep "$myOLDTAG" | awk '{print $3}')
fi
}

# Let's load docker images in parallel
function fuPULLIMAGES {
local myTPOTCOMPOSE="/opt/tpot/etc/tpot.yml"
for name in $(cat $myTPOTCOMPOSE | grep -v '#' | grep image | cut -d'"' -f2 | uniq)
  do
    docker pull $name &
  done
wait
echo
}

function fuUPDATER () {
export DEBIAN_FRONTEND=noninteractive
echo "### Installing apt-fast"
/bin/bash -c "$(curl -sL https://raw.githubusercontent.com/ilikenwf/apt-fast/master/quick-install.sh)"
local myPACKAGES="aria2 apache2-utils apparmor apt-transport-https aufs-tools bash-completion build-essential ca-certificates cgroupfs-mount cockpit cockpit-docker console-setup console-setup-linux cracklib-runtime curl debconf-utils dialog dnsutils docker.io docker-compose ethtool fail2ban figlet genisoimage git glances grc haveged html2text htop iptables iw jq kbd libcrack2 libltdl7 libpam-google-authenticator man mosh multitail netselect-apt net-tools npm ntp openssh-server openssl pass pigz prips software-properties-common syslinux psmisc pv python3-elasticsearch-curator python3-pip toilet unattended-upgrades unzip vim wget wireless-tools wpasupplicant"
# Remove purge in the future
echo "### Removing repository based install of elasticsearch-curator"
apt-get purge elasticsearch-curator -y
hash -r
echo "### Now upgrading packages ..."
dpkg --configure -a
apt-fast -y autoclean
apt-fast -y autoremove
apt-fast update
apt-fast -y install $myPACKAGES

# Some updates require interactive attention, and the following settings will override that.
echo "docker.io docker.io/restart       boolean true" | debconf-set-selections -v
echo "debconf debconf/frontend select noninteractive" | debconf-set-selections -v
apt-fast -y dist-upgrade -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" --force-yes
dpkg --configure -a
npm cache clean --force
npm install elasticdump -g
pip3 install --upgrade yq
# Remove --force switch in the future ...
pip3 install elasticsearch-curator --force
hash -r
echo "### Removing and holding back problematic packages ..."
apt-fast -y purge exim4-base mailutils pcp cockpit-pcp elasticsearch-curator
apt-mark hold exim4-base mailutils pcp cockpit-pcp elasticsearch-curator
echo

echo "### Now replacing T-Pot related config files on host"
cp host/etc/systemd/* /etc/systemd/system/
systemctl daemon-reload
echo

# Ensure some defaults
echo "### Ensure some T-Pot defaults with regard to some folders, permissions and configs."
sed -i '/^port/Id' /etc/ssh/sshd_config
echo "Port 64295" >> /etc/ssh/sshd_config
echo

### Ensure creation of T-Pot related folders, just in case
mkdir -vp /data/adbhoney/{downloads,log} \
         /data/ciscoasa/log \
         /data/conpot/log \
	 /data/citrixhoneypot/logs \
         /data/cowrie/{downloads,keys,misc,log,log/tty} \
	 /data/dicompot/{images,log} \
         /data/dionaea/{log,bistreams,binaries,rtp,roots,roots/ftp,roots/tftp,roots/www,roots/upnp} \
         /data/elasticpot/log \
         /data/elk/{data,log} \
	 /data/fatt/log \
         /data/honeytrap/{log,attacks,downloads} \
         /data/glutton/log \
         /data/heralding/log \
         /data/honeypy/log \
         /data/honeysap/log \
         /data/mailoney/log \
         /data/medpot/log \
         /data/nginx/{log,heimdall} \
         /data/emobility/log \
         /data/ews/conf \
         /data/rdpy/log \
         /data/spiderfoot \
         /data/suricata/log \
         /data/tanner/{log,files} \
         /data/p0f/log \
	 /home/tsec/.ssh/

### Let's take care of some files and permissions
chmod 770 -R /data
chown tpot:tpot -R /data
chmod 644 -R /data/nginx/conf
chmod 644 -R /data/nginx/cert

echo "### Now pulling latest docker images"
echo "######$myBLUE This might take a while, please be patient!$myWHITE"
fuPULLIMAGES 2>&1>/dev/null

#fuREMOVEOLDIMAGES "1903"
echo "### If you made changes to tpot.yml please ensure to add them again."
echo "### We stored the previous version as backup in /root/."
echo "### Some updates may need an import of the latest Kibana objects as well."
echo "### Download the latest objects here if they recently changed:"
echo "### https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/etc/objects/kibana_export.json.zip"
echo "### Export and import the objects easily through the Kibana WebUI:"
echo "### Go to Kibana > Management > Saved Objects > Export / Import"
echo "### Or use the command:"
echo "### import_kibana-objects.sh /opt/tpot/etc/objects/kibana-objects.tgz"
echo "### All objects will be overwritten upon import, make sure to run an export first if you made changes."
}

function fuRESTORE_EWSCFG () {
if [ -f '/data/ews/conf/ews.cfg' ] && ! grep 'ews.cfg' $myCONFIGFILE > /dev/null; then
    echo
    echo "### Restoring volume mount for ews.cfg in tpot.yml"
    sed -i --follow-symlinks '/\/opt\/ewsposter\/ews.ip/a\\ \ \ \ \ - /data/ews/conf/ews.cfg:/opt/ewsposter/ews.cfg' $myCONFIGFILE
fi
}

function fuRESTORE_HPFEEDS () {
if [ -f '/data/ews/conf/hpfeeds.cfg' ]; then
    echo
    echo "### Restoring HPFEEDS in tpot.yml"
    ./bin/hpfeeds_optin.sh --conf=/data/ews/conf/hpfeeds.cfg
fi
}


################
# Main section #
################

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi

# Only run with command switch
if [ "$1" != "-y" ]; then
  echo "This script will update / upgrade all T-Pot related scripts, tools and packages to the latest versions."
  echo "A backup of /opt/tpot will be written to /root. If you are unsure, you should save your work."
  echo "This is a beta feature and only recommended for experienced users."
  echo "If you understand the involved risks feel free to run this script with the '-y' switch."
  echo
  exit
fi

fuCHECK_VERSION
fuCONFIGCHECK
fuCHECKINET "https://index.docker.io https://github.com https://pypi.python.org https://debian.org"
fuSTOP_TPOT
fuBACKUP
fuSELFUPDATE "$0" "$@"
fuUPDATER
fuRESTORE_EWSCFG
fuRESTORE_HPFEEDS

echo
echo "### Done."
echo
