#!/bin/bash
########################################################
# T-Pot post install script                            #
# Ubuntu server 16.04.0, x64                           #
#                                                      #
# v17.06 by mo, DTAG, 2017-03-22                       #
########################################################

# Set TERM, DIALOGRC
export TERM=linux
export DIALOGRC=/etc/dialogrc

# Let's load dialog color theme
cp /root/tpot/etc/dialogrc /etc/

# Some global vars
myPROXYFILEPATH="/root/tpot/etc/proxy"
myNTPCONFPATH="/root/tpot/etc/ntp"
myPFXPATH="/root/tpot/keys/8021x.pfx"
myPFXPWPATH="/root/tpot/keys/8021x.pw"
myPFXHOSTIDPATH="/root/tpot/keys/8021x.id"
myBACKTITLE="T-Pot Installer"
mySITES="https://index.docker.io https://ubuntu.com https://github.com http://nsanamegenerator.com"

# Let's create a function for colorful output
fuECHO () {
  local myRED=1
  local myWHT=7
  tput setaf $myRED -T linux
  echo "$1" "$2"
  tput setaf $myWHT -T linux
}

fuRANDOMWORD () {
  local myWORDFILE=/usr/share/dict/names
  local myLINES=$(cat $myWORDFILE  | wc -l)
  local myRANDOM=$((RANDOM % $myLINES))
  local myNUM=$((myRANDOM * myRANDOM % $myLINES + 1))
  echo -n $(sed -n "$myNUM p" $myWORDFILE | tr -d \' | tr A-Z a-z)
}

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

# Let's test internet connection
fuECHO "### Testing internet connection."
for i in $mySITES;
  do
    curl --connect-timeout 5 -IsS $i > /dev/null;
      if [ $? -ne 0 ];
        then
          dialog --backtitle "$myBACKTITLE" --title "[ Continue? ]" --yesno "\nInternet connection test failed. This might indicate some problems with your connection. You can continue, but the installation might fail." 10 50
          if [ $? = 1 ];
            then
              dialog --backtitle "$myBACKTITLE" --title "[ Abort ]" --msgbox "\nInstallation aborted. Exiting the installer." 7 50
              exit
            else
              break;
          fi;
      fi;
  done;

# Let's remove NGINX default website
fuECHO "### Removing NGINX default website."
rm -rf /etc/nginx/sites-enabled/default
rm -rf /etc/nginx/sites-available/default
rm -rf /usr/share/nginx/html/index.html

# Let's wait a few seconds to avoid interference with service messages
fuECHO "### Waiting a few seconds to avoid interference with service messages."
sleep 5

# Let's ask user for install flavor
# Install types are TPOT, HP, INDUSTRIAL, ALL
myFLAVOR=$(dialog --no-cancel --backtitle "$myBACKTITLE" --title "[ Choose your edition ]" --no-tags --menu \
"\nRequired: 4GB RAM, 64GB disk\nRecommended: 8GB RAM, 128GB SSD" 14 60 4 \
"TPOT" "Standard Honeypots, Suricata & ELK" \
"HP" "Honeypots only, w/o Suricata & ELK" \
"INDUSTRIAL" "Conpot, eMobility, Suricata & ELK" \
"EVERYTHING" "Everything" 3>&1 1>&2 2>&3 3>&-)

# Let's ask user for a web username and password
myOK="1"
myUSER="tsec"
while [ 1 != 2 ]
  do
    myUSER=$(dialog --backtitle "$myBACKTITLE" --title "[ Enter your web user name ]" --inputbox "\nUsername (tsec not allowed)" 9 50 3>&1 1>&2 2>&3 3>&-)
    myUSER=$(echo $myUSER | tr -cd "[:alnum:]_.-")
    dialog --backtitle "$myBACKTITLE" --title "[ Your username is ]" --yesno "\n$myUSER" 7 50
    myOK=$?
    if [ "$myOK" = "0" ] && [ "$myUSER" != "tsec" ] && [ "$myUSER" != "" ];
      then
        break
    fi
  done
myPASS1="pass1"
myPASS2="pass2"
while [ "$myPASS1" != "$myPASS2"  ]
  do
    while [ "$myPASS1" == "pass1"  ] || [ "$myPASS1" == "" ]
      do
        myPASS1=$(dialog --insecure --backtitle "$myBACKTITLE" --title "[ Enter your web user password ]" --passwordbox "\nPassword" 9 50 3>&1 1>&2 2>&3 3>&-)
      done
        myPASS2=$(dialog --insecure --backtitle "$myBACKTITLE" --title "[ Repeat web user password ]" --passwordbox "\nPassword" 9 50 3>&1 1>&2 2>&3 3>&-)
    if [ "$myPASS1" != "$myPASS2" ];
      then
        dialog --backtitle "$myBACKTITLE" --title "[ Passwords do not match. ]" --msgbox "\nPlease re-enter your password." 7 50
        myPASS1="pass1"
        myPASS2="pass2"
    fi
  done
htpasswd -b -c /etc/nginx/nginxpasswd "$myUSER" "$myPASS1"
fuECHO

# Let's log for the beauty of it
#set -e
#exec 2> >(tee "install.err")
#exec > >(tee "install.log")

# Let's generate a SSL self-signed certificate without interaction (browsers will see it invalid anyway)
fuECHO "### Generating a self-signed-certificate for NGINX."
mkdir -p /etc/nginx/ssl
openssl req -nodes -x509 -sha512 -newkey rsa:8192 -keyout "/etc/nginx/ssl/nginx.key" -out "/etc/nginx/ssl/nginx.crt" -days 3650 -subj '/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd'

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
### The Intel NUC uses wlpXsY notation instead of wlanX
#
#auto wlp2s0
#iface wlp2s0 inet dhcp
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

# Let's pull some updates
fuECHO "### Pulling Updates."
apt-get update -y
apt-get upgrade -y

# Let's clean up apt
apt-get autoclean -y
apt-get autoremove -y

# Installing alerta-cli, wetty, ctop, elasticdump
fuECHO "### Installing alerta-cli."
pip install --upgrade pip
pip install alerta
fuECHO "### Installing wetty."
ln -s /usr/bin/nodejs /usr/bin/node
npm install https://github.com/t3chn0m4g3/wetty -g
fuECHO "### Installing elasticdump."
npm install https://github.com/t3chn0m4g3/elasticsearch-dump -g
fuECHO "### Installing ctop."
wget https://github.com/bcicen/ctop/releases/download/v0.4.1/ctop-0.4.1-linux-amd64 -O ctop
mv ctop /usr/bin/
chmod +x /usr/bin/ctop

# Let's add proxy settings to docker defaults
if [ -f $myPROXYFILEPATH ];
then fuECHO "### Setting up the proxy for docker."
myPROXY=$(cat $myPROXYFILEPATH)
tee -a /etc/default/docker <<EOF
http_proxy=$myPROXY
https_proxy=$myPROXY
HTTP_PROXY=$myPROXY
HTTPS_PROXY=$myPROXY
no_proxy=localhost,127.0.0.1,.sock
EOF
fi

# Let's add a new user
fuECHO "### Adding new user."
addgroup --gid 2000 tpot
adduser --system --no-create-home --uid 2000 --disabled-password --disabled-login --gid 2000 tpot

# Let's set the hostname
fuECHO "### Setting a new hostname."
myHOST=$(curl -s -f www.nsanamegenerator.com | html2text | tr A-Z a-z | awk '{print $1}')
if [ "$myHOST" = "" ]; then
  fuECHO "### Failed to fetch name from remote, using local cache."
  myHOST=$(fuRANDOMWORD)
fi
hostnamectl set-hostname $myHOST
sed -i 's#127.0.1.1.*#127.0.1.1\t'"$myHOST"'#g' /etc/hosts

# Let's patch sshd_config
fuECHO "### Patching sshd_config to listen on port 64295 and deny password authentication."
sed -i 's#Port 22#Port 64295#' /etc/ssh/sshd_config
sed -i 's#\#PasswordAuthentication yes#PasswordAuthentication no#' /etc/ssh/sshd_config

# Let's allow ssh password authentication from RFC1918 networks
fuECHO "### Allow SSH password authentication from RFC1918 networks"
tee -a /etc/ssh/sshd_config <<EOF
Match address 127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
    PasswordAuthentication yes
EOF

# Let's restart docker for proxy changes to take effect
systemctl restart docker
sleep 5

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
for name in $(cat /root/tpot/data/images.conf)
  do
    docker pull dtagdevsec/$name:1706
  done

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
# Set required map count for ELK
kernel.panic = 1
kernel.panic_on_oops = 1
vm.max_map_count = 262144
EOF

# Let's add some cronjobs
fuECHO "### Adding cronjobs."
tee -a /etc/crontab <<EOF

# Check if containers and services are up
*/5 * * * *	root	check.sh

# Example for alerta-cli IP update
#*/5 * * * *	root	alerta --endpoint-url http://<ip>:<port>/api delete --filters resource=<host> && alerta --endpoint-url http://<ip>:<port>/api send -e IP -r <host> -E Production -s ok -S T-Pot -t \$(cat /data/elk/logstash/mylocal.ip) --status open

# Check if updated images are available and download them
27 1 * * *	root	for i in \$(cat /etc/tpot/images.conf); do docker pull dtagdevsec/\$i:1706; done

# Restart docker service and containers
27 3 * * *	root	dcres.sh

# Delete elastic indices older than 90 days (kibana index is omitted by default)
27 4 * * *	root	docker exec elk bash -c '/usr/local/bin/curator --host 127.0.0.1 delete indices --older-than 90 --time-unit days --timestring \%Y.\%m.\%d'

# Update IP and erase check.lock if it exists
27 5 * * *	root	/etc/rc.local

# Daily reboot
27 23 * * *	root	reboot

# Check for updated packages every sunday, upgrade and reboot
27 16 * * 0	root	apt-get autoclean -y && apt-get autoremove -y && apt-get update -y && apt-get upgrade -y && sleep 10 && reboot
EOF

# Let's create some files and folders
fuECHO "### Creating some files and folders."
mkdir -p /data/conpot/log \
         /data/cowrie/log/tty/ /data/cowrie/downloads/ /data/cowrie/keys/ /data/cowrie/misc/ \
         /data/dionaea/log /data/dionaea/bistreams /data/dionaea/binaries /data/dionaea/rtp /data/dionaea/roots/ftp /data/dionaea/roots/tftp /data/dionaea/roots/www /data/dionaea/roots/upnp \
         /data/elasticpot/log \
         /data/elk/data /data/elk/log /data/elk/logstash/conf \
         /data/glastopf /data/honeytrap/log/ /data/honeytrap/attacks/ /data/honeytrap/downloads/ \
         /data/emobility/log \
         /data/ews/conf \
         /data/suricata/log /home/tsec/.ssh/ \
         /etc/tpot/elk /etc/tpot/imgcfg /etc/tpot/systemd \
         /usr/share/tpot/bin

# Let's take care of some files and permissions before copying
chmod 500 /root/tpot/bin/*
chmod 600 /root/tpot/data/*
chmod 644 /root/tpot/etc/issue
chmod 755 /root/tpot/etc/rc.local
chmod 644 /root/tpot/data/systemd/*

# Let's copy some files
tar xvfz /root/tpot/data/elkbase.tgz -C /
cp -R /root/tpot/bin/* /usr/share/tpot/bin/
cp -R /root/tpot/data/* /etc/tpot/
cp    /root/tpot/data/systemd/* /etc/systemd/system/
cp    /root/tpot/etc/issue /etc/
cp -R /root/tpot/etc/nginx/ssl /etc/nginx/
cp    /root/tpot/etc/nginx/tpotweb.conf /etc/nginx/sites-available/
cp    /root/tpot/etc/nginx/nginx.conf /etc/nginx/nginx.conf
cp    /root/tpot/keys/authorized_keys /home/tsec/.ssh/authorized_keys
cp    /root/tpot/usr/share/nginx/html/* /usr/share/nginx/html/
for i in $(cat /etc/tpot/images.conf);
  do
    systemctl enable $i;
done
systemctl enable wetty

# Let's enable T-Pot website
fuECHO "### Enabling T-Pot website."
ln -s /etc/nginx/sites-available/tpotweb.conf /etc/nginx/sites-enabled/tpotweb.conf

# Let's take care of some files and permissions
chmod 760 -R /data
chown tpot:tpot -R /data
chmod 600 /home/tsec/.ssh/authorized_keys
chown tsec:tsec /home/tsec/.ssh /home/tsec/.ssh/authorized_keys

# Let's replace "quiet splash" options, set a console font for more screen canvas and update grub
sed -i 's#GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"#GRUB_CMDLINE_LINUX_DEFAULT="consoleblank=0"#' /etc/default/grub
sed -i 's#GRUB_CMDLINE_LINUX=""#GRUB_CMDLINE_LINUX="cgroup_enable=memory swapaccount=1"#' /etc/default/grub
#sed -i 's#\#GRUB_GFXMODE=640x480#GRUB_GFXMODE=800x600x32#' /etc/default/grub
#tee -a /etc/default/grub <<EOF
#GRUB_GFXPAYLOAD=800x600x32
#GRUB_GFXPAYLOAD_LINUX=800x600x32
#EOF
update-grub
cp /usr/share/consolefonts/Uni2-Terminus12x6.psf.gz /etc/console-setup/
gunzip /etc/console-setup/Uni2-Terminus12x6.psf.gz
sed -i 's#FONTFACE=".*#FONTFACE="Terminus"#' /etc/default/console-setup
sed -i 's#FONTSIZE=".*#FONTSIZE="12x6"#' /etc/default/console-setup
update-initramfs -u

# Let's enable a color prompt and add /usr/share/tpot/bin to path
myROOTPROMPT='PS1="\[\033[38;5;8m\][\[$(tput sgr0)\]\[\033[38;5;1m\]\u\[$(tput sgr0)\]\[\033[38;5;6m\]@\[$(tput sgr0)\]\[\033[38;5;4m\]\h\[$(tput sgr0)\]\[\033[38;5;6m\]:\[$(tput sgr0)\]\[\033[38;5;5m\]\w\[$(tput sgr0)\]\[\033[38;5;8m\]]\[$(tput sgr0)\]\[\033[38;5;1m\]\\$\[$(tput sgr0)\]\[\033[38;5;15m\] \[$(tput sgr0)\]"'
myUSERPROMPT='PS1="\[\033[38;5;8m\][\[$(tput sgr0)\]\[\033[38;5;2m\]\u\[$(tput sgr0)\]\[\033[38;5;6m\]@\[$(tput sgr0)\]\[\033[38;5;4m\]\h\[$(tput sgr0)\]\[\033[38;5;6m\]:\[$(tput sgr0)\]\[\033[38;5;5m\]\w\[$(tput sgr0)\]\[\033[38;5;8m\]]\[$(tput sgr0)\]\[\033[38;5;2m\]\\$\[$(tput sgr0)\]\[\033[38;5;15m\] \[$(tput sgr0)\]"'
tee -a /root/.bashrc << EOF
$myROOTPROMPT
PATH="$PATH:/usr/share/tpot/bin"
EOF
tee -a /home/tsec/.bashrc << EOF
$myUSERPROMPT
PATH="$PATH:/usr/share/tpot/bin"
EOF

# Let's create ews.ip before reboot and prevent race condition for first start
source /etc/environment
myLOCALIP=$(hostname -I | awk '{ print $1 }')
myEXTIP=$(/usr/share/tpot/bin/myip.sh)
sed -i "s#IP:.*#IP: $myLOCALIP ($myEXTIP)[0m#" /etc/issue
sed -i "s#SSH:.*#SSH: ssh -l tsec -p 64295 $myLOCALIP[0m#" /etc/issue
sed -i "s#WEB:.*#WEB: https://$myLOCALIP:64297[0m#" /etc/issue
tee /data/ews/conf/ews.ip << EOF
[MAIN]
ip = $myEXTIP
EOF
tee /etc/tpot/elk/environment << EOF
MY_EXTIP=$myEXTIP
MY_HOSTNAME=$HOSTNAME
EOF
echo $myLOCALIP > /data/elk/logstash/mylocal.ip
chown tpot:tpot /data/ews/conf/ews.ip

# Final steps
fuECHO "### Thanks for your patience. Now rebooting."
mv /root/tpot/etc/rc.local /etc/rc.local && rm -rf /root/tpot/ && sleep 2 && reboot
