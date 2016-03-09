#!/bin/bash
########################################################
# T-Pot post install script                            #
# Ubuntu server 14.04.4, x64                           #
#                                                      #
# v16.03.14 by mo, DTAG, 2016-03-08                    #
########################################################

# Type of install, SENSOR, INDUSTRIAL or FULL?
myFLAVOR="TPOT"

# Some global vars
myPROXYFILEPATH="/root/tpot/etc/proxy"
myNTPCONFPATH="/root/tpot/etc/ntp"
myPFXPATH="/root/tpot/keys/8021x.pfx"
myPFXPWPATH="/root/tpot/keys/8021x.pw"
myPFXHOSTIDPATH="/root/tpot/keys/8021x.id"

# Let's create a function for colorful output
fuECHO () {
  local myRED=1
  local myWHT=7
  tput setaf $myRED
  echo $1 "$2"
  tput setaf $myWHT
}

# Let's make sure there is a warning if running for a second time
if [ -f install.log ];
  then fuECHO "### Running more than once may complicate things. Erase install.log if you are really sure."
  exit 1;
fi

# Let's log for the beauty of it
set -e
exec 2> >(tee "install.err")
exec > >(tee "install.log")

# Let's setup the proxy for env
if [ -f $myPROXYFILEPATH ];
then fuECHO "### Setting up the proxy."
myPROXY=$(cat $myPROXYFILEPATH)
tee -a /etc/environment <<EOF
export http_proxy=$myPROXY
export https_proxy=$myPROXY
export HTTP_PROXY=$myPROXY
export HTTPS_PROXY=$myPROXY
export no_proxy=localhost,127.0.0.1,.sock
EOF
source /etc/environment

# Let's setup the proxy for apt
tee /etc/apt/apt.conf <<EOF
Acquire::http::Proxy "$myPROXY";
Acquire::https::Proxy "$myPROXY";
EOF
fi

# Let's setup the ntp server
if [ -f $myNTPCONFPATH ];
  then
    fuECHO "### Setting up the ntp server."
    cp $myNTPCONFPATH /etc/ntp.conf
fi

# Let's setup 802.1x networking
if [ -f $myPFXPATH ];
  then
    fuECHO "### Setting up 802.1x networking."
    cp $myPFXPATH /etc/wpa_supplicant/
    if [ -f $myPFXPWPATH ];
      then
        fuECHO "### Setting up 802.1x password."
        myPFXPW=$(cat $myPFXPWPATH)
    fi
    myPFXHOSTID=$(cat $myPFXHOSTIDPATH)
tee -a /etc/network/interfaces <<EOF
        wpa-driver wired
        wpa-conf /etc/wpa_supplicant/wired8021x.conf

### Example wireless config for 802.1x
### This configuration was tested with the IntelNUC series
### If problems occur you can try and change wpa-driver to "iwlwifi"
### Do not forget to enter a ssid in /etc/wpa_supplicant/wireless8021x.conf
#
#auto wlan0
#iface wlan0 inet dhcp
#        wpa-driver wext
#        wpa-conf /etc/wpa_supplicant/wireless8021x.conf
EOF

tee /etc/wpa_supplicant/wired8021x.conf <<EOF
ctrl_interface=/var/run/wpa_supplicant
ctrl_interface_group=root
eapol_version=1
ap_scan=1
network={
  key_mgmt=IEEE8021X
  eap=TLS
  identity="host/$myPFXHOSTID"
  private_key="/etc/wpa_supplicant/8021x.pfx"
  private_key_passwd="$myPFXPW"
}
EOF

tee /etc/wpa_supplicant/wireless8021x.conf <<EOF
ctrl_interface=/var/run/wpa_supplicant
ctrl_interface_group=root
eapol_version=1
ap_scan=1
network={
  ssid="<your_ssid_here_without_brackets>"
  key_mgmt=WPA-EAP
  pairwise=CCMP
  group=CCMP
  eap=TLS
  identity="host/$myPFXHOSTID"
  private_key="/etc/wpa_supplicant/8021x.pfx"
  private_key_passwd="$myPFXPW"
}
EOF
fi

# Let's provide a wireless example config ...
fuECHO "### Providing a wireless example config."
tee -a /etc/network/interfaces <<EOF

### Example wireless config without 802.1x
### This configuration was tested with the IntelNUC series
### If problems occur you can try and change wpa-driver to "iwlwifi"
#
#auto wlan0
#iface wlan0 inet dhcp
#   wpa-driver wext
#   wpa-ssid <your_ssid_here_without_brackets>
#   wpa-ap-scan 1
#   wpa-proto RSN
#   wpa-pairwise CCMP
#   wpa-group CCMP
#   wpa-key-mgmt WPA-PSK
#   wpa-psk "<your_password_here_without_brackets>"
EOF

# Let's modify the sources list
sed -i '/cdrom/d' /etc/apt/sources.list

# Let's make sure SSH roaming is turned off (CVE-2016-0777, CVE-2016-0778)
fuECHO "### Let's make sure SSH roaming is turned off."
tee -a /etc/ssh/ssh_config <<EOF
UseRoaming no
EOF

# Let's add the docker repository
fuECHO "### Adding the docker repository."
apt-key adv --keyserver hkp://p80.pool.sks-keyservers.net:80 --recv-keys 58118E89F3A912897C070ADBF76221572C52609D
tee /etc/apt/sources.list.d/docker.list <<EOF
deb https://apt.dockerproject.org/repo ubuntu-trusty main
EOF

# Let's pull some updates
fuECHO "### Pulling Updates."
apt-get update -y
fuECHO "### Installing Upgrades."
apt-get upgrade -y

# Let's install docker
fuECHO "### Installing docker-engine."
apt-get install docker-engine=1.10.2-0~trusty -y

# Let's add proxy settings to docker defaults
if [ -f $myPROXYFILEPATH ];
then fuECHO "### Setting up the proxy for docker."
myPROXY=$(cat $myPROXYFILEPATH)
tee -a /etc/default/docker <<EOF
export http_proxy=$myPROXY
export https_proxy=$myPROXY
export HTTP_PROXY=$myPROXY
export HTTPS_PROXY=$myPROXY
export no_proxy=localhost,127.0.0.1,.sock
EOF
fi

# Let's add a new user
fuECHO "### Adding new user."
addgroup --gid 2000 tpot
adduser --system --no-create-home --uid 2000 --disabled-password --disabled-login --gid 2000 tpot

# Let's set the hostname
fuECHO "### Setting a new hostname."
myHOST=ce$(date +%s)$RANDOM
hostnamectl set-hostname $myHOST
sed -i 's#127.0.1.1.*#127.0.1.1\t'"$myHOST"'#g' /etc/hosts

# Let's patch sshd_config
fuECHO "### Patching sshd_config to listen on port 64295 and deny password authentication."
sed -i 's#Port 22#Port 64295#' /etc/ssh/sshd_config
sed -i 's#\#PasswordAuthentication yes#PasswordAuthentication no#' /etc/ssh/sshd_config

# Let's disable ssh service
echo "manual" >> /etc/init/ssh.override

# Let's patch docker defaults, so we can run images as service
fuECHO "### Patching docker defaults."
tee -a /etc/default/docker <<EOF
DOCKER_OPTS="-r=false"
EOF

# Let's make sure only myFLAVOR images will be downloaded and started
case $myFLAVOR in
  HP)
    echo "### Preparing HONEYPOT flavor installation."
    cp /root/tpot/data/imgcfg/hp_images.conf /root/tpot/data/images.conf
  ;;
  INDUSTRIAL)
    echo "### Preparing INDUSTRIAL flavor installation."
    cp /root/tpot/data/imgcfg/industrial_images.conf /root/tpot/data/images.conf
  ;;
  TPOT)
    echo "### Preparing TPOT flavor installation."
    cp /root/tpot/data/imgcfg/tpot_images.conf /root/tpot/data/images.conf
  ;;
  ALL)
    echo "### Preparing EVERYTHING flavor installation."
    cp /root/tpot/data/imgcfg/all_images.conf /root/tpot/data/images.conf
  ;;
esac

# Let's load docker images
fuECHO "### Loading docker images. Please be patient, this may take a while."
if [ -d /root/tpot/images ];
  then
    fuECHO "### Found cached images and will load from local."
    for name in $(cat /root/tpot/data/images.conf)
    do
      fuECHO "### Now loading dtagdevsec/$name:latest1603"
      docker load -i /root/tpot/images/$name:latest1603.img
    done
  else
    for name in $(cat /root/tpot/data/images.conf)
    do
      docker pull dtagdevsec/$name:latest1603
    done
fi

# Let's add the daily update check with a weekly clean interval
fuECHO "### Modifying update checks."
tee /etc/apt/apt.conf.d/10periodic <<EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "7";
EOF

# Let's make sure to reboot the system after a kernel panic
fuECHO "### Reboot after kernel panic."
tee -a /etc/sysctl.conf <<EOF

# Reboot after kernel panic, check via /proc/sys/kernel/panic[_on_oops]
kernel.panic = 1
kernel.panic_on_oops = 1
EOF

# Let's add some cronjobs
fuECHO "### Adding cronjobs."
tee -a /etc/crontab <<EOF

# Show running containers every 60s via /dev/tty2
*/2 * * * *   root 	status.sh > /dev/tty2

# Check if containers and services are up
*/5 * * * *   root 	check.sh

# Check if updated images are available and download them
27 1 * * *    root	for i in \$(cat /data/images.conf); do docker pull dtagdevsec/\$i:latest1603; done

# Restart docker service and containers
27 3 * * *    root 	dcres.sh

# Delete elastic indices older than 90 days (kibana index is omitted by default)
27 4 * * *    root  docker exec elk bash -c '/usr/local/bin/curator --host 127.0.0.1 delete indices --older-than 90 --time-unit days --timestring '%Y.%m.%d''

# Update IP and erase check.lock if it exists
27 15 * * *   root  /etc/rc.local

# Check for updated packages every sunday, upgrade and reboot
27 16 * * 0   root  apt-get autoclean -y; apt-get autoremove -y; apt-get update -y; apt-get upgrade -y; sleep 5; reboot
EOF

# Let's create some files and folders
fuECHO "### Creating some files and folders."
mkdir -p /data/conpot/log \
         /data/cowrie/log/tty/ /data/cowrie/downloads/ /data/cowrie/keys/ /data/cowrie/misc/ \
         /data/dionaea/log /data/dionaea/bistreams /data/dionaea/binaries /data/dionaea/rtp /data/dionaea/wwwroot \
         /data/elasticpot/log \
         /data/elk/data /data/elk/log /data/glastopf /data/honeytrap/log/ /data/honeytrap/attacks/ /data/honeytrap/downloads/ \
         /data/emobility/log \
         /data/ews/log /data/ews/conf /data/ews/dionaea /data/ews/emobility \
         /data/suricata/log /home/tsec/.ssh/

# Let's take care of some files and permissions before copying
chmod 500 /root/tpot/bin/*
chmod 600 /root/tpot/data/*
chmod 644 /root/tpot/etc/issue
chmod 755 /root/tpot/etc/rc.local
chmod 700 /root/tpot/home/*
chown tsec:tsec /root/tpot/home/*
chmod 644 /root/tpot/data/upstart/*

# Let's copy some files
tar xvfz /root/tpot/data/elkbase.tgz -C /
cp /root/tpot/data/elkbase.tgz /data/
cp -R /root/tpot/bin/* /usr/bin/
cp -R /root/tpot/data/* /data/
cp -R /root/tpot/etc/issue /etc/
cp -R /root/tpot/home/* /home/tsec/
cp    /root/tpot/keys/authorized_keys /home/tsec/.ssh/authorized_keys
for i in $(cat /data/images.conf);
  do
    cp /data/upstart/$i.conf /etc/init/;
done

# Let's turn persistence off by default
touch /data/persistence.off

# Let's take care of some files and permissions
chmod 760 -R /data
chown tpot:tpot -R /data
chmod 600 /home/tsec/.ssh/authorized_keys
chown tsec:tsec /home/tsec/*.sh /home/tsec/.ssh /home/tsec/.ssh/authorized_keys

# Let's clean up apt
apt-get autoclean -y
apt-get autoremove -y

# Let's replace "quiet splash" options, set a console font for more screen canvas and update grub
sed -i 's#GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"#GRUB_CMDLINE_LINUX_DEFAULT="consoleblank=0"#' /etc/default/grub
sed -i 's#\#GRUB_GFXMODE=640x480#GRUB_GFXMODE=800x600x32#' /etc/default/grub
tee -a /etc/default/grub <<EOF
GRUB_GFXPAYLOAD=800x600x32
GRUB_GFXPAYLOAD_LINUX=800x600x32
EOF
update-grub
cp /usr/share/consolefonts/Uni2-Terminus12x6.psf.gz /etc/console-setup/
gunzip /etc/console-setup/Uni2-Terminus12x6.psf.gz
sed -i 's#FONTFACE=".*#FONTFACE="Terminus"#' /etc/default/console-setup
sed -i 's#FONTSIZE=".*#FONTSIZE="12x6"#' /etc/default/console-setup
update-initramfs -u

# Let's enable a color prompt
sed -i 's#\#force_color_prompt=yes#force_color_prompt=yes#' /home/tsec/.bashrc
sed -i 's#\#force_color_prompt=yes#force_color_prompt=yes#' /root/.bashrc

# Let's create ews.ip before reboot and prevent race condition for first start
myLOCALIP=$(hostname -I | awk '{ print $1 }')
myEXTIP=$(curl myexternalip.com/raw)
sed -i "s#IP:.*#IP: $myLOCALIP, $myEXTIP#" /etc/issue
tee /data/ews/conf/ews.ip << EOF
[MAIN]
ip = $myEXTIP
EOF
chown tpot:tpot /data/ews/conf/ews.ip

# Final steps
fuECHO "### Thanks for your patience. Now rebooting."
mv /root/tpot/etc/rc.local /etc/rc.local && rm -rf /root/tpot/ && chage -d 0 tsec && sleep 2 && reboot
