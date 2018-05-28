#!/bin/bash
# T-Pot post install script

# Set TERM, DIALOGRC
export TERM=linux
export DIALOGRC=/etc/dialogrc

# Let's load dialog color theme
cp /root/installer/dialogrc /etc/

# Some global vars
myPROXYFILEPATH="/root/installer/proxy"
myNTPCONFPATH="/root/installer/ntp"
myPFXPATH="/root/installer/keys/8021x.pfx"
myPFXPWPATH="/root/installer/keys/8021x.pw"
myPFXHOSTIDPATH="/root/installer/keys/8021x.id"
myTPOTCOMPOSE="/opt/tpot/etc/tpot.yml"
myBACKTITLE="T-Pot-Installer"
mySITES="https://index.docker.io https://github.com https://pypi.python.org https://ubuntu.com"
myPROGRESSBOXCONF=" --backtitle "$myBACKTITLE" --progressbox 24 80"

fuRANDOMWORD () {
  local myWORDFILE="$1"
  local myLINES=$(cat $myWORDFILE  | wc -l)
  local myRANDOM=$((RANDOM % $myLINES))
  local myNUM=$((myRANDOM * myRANDOM % $myLINES + 1))
  echo -n $(sed -n "$myNUM p" $myWORDFILE | tr -d \' | tr A-Z a-z)
}

# Let's wait a few seconds to avoid interference with service messages
sleep 3
tput civis
dialog --no-ok --no-cancel --backtitle "$myBACKTITLE" --title "[ Wait to avoid interference with service messages ]" --pause "" 6 80 7

# Let's setup the proxy for env
if [ -f $myPROXYFILEPATH ];
then
dialog --title "[ Setting up the proxy ]" $myPROGRESSBOXCONF <<EOF
EOF
myPROXY=$(cat $myPROXYFILEPATH)
tee -a /etc/environment 2>&1>/dev/null <<EOF
export http_proxy=$myPROXY
export https_proxy=$myPROXY
export HTTP_PROXY=$myPROXY
export HTTPS_PROXY=$myPROXY
export no_proxy=localhost,127.0.0.1,.sock
EOF
source /etc/environment

# Let's setup the proxy for apt
tee /etc/apt/apt.conf 2>&1>/dev/null <<EOF
Acquire::http::Proxy "$myPROXY";
Acquire::https::Proxy "$myPROXY";
EOF

# Let's add proxy settings to docker defaults
myPROXY=$(cat $myPROXYFILEPATH)
tee -a /etc/default/docker 2>&1>/dev/null <<EOF
http_proxy=$myPROXY
https_proxy=$myPROXY
HTTP_PROXY=$myPROXY
HTTPS_PROXY=$myPROXY
no_proxy=localhost,127.0.0.1,.sock
EOF

# Let's restart docker for proxy changes to take effect
systemctl stop docker 2>&1 | dialog --title "[ Stop docker service ]" $myPROGRESSBOXCONF
systemctl start docker 2>&1 | dialog --title "[ Start docker service ]" $myPROGRESSBOXCONF
fi

# Let's test the internet connection
mySITESCOUNT=$(echo $mySITES | wc -w)
j=0
for i in $mySITES;
  do
    dialog --title "[ Testing the internet connection ]" --backtitle "$myBACKTITLE" \
           --gauge "\n  Now checking: $i\n" 8 80 $(expr 100 \* $j / $mySITESCOUNT) <<EOF
EOF
    curl --connect-timeout 5 -IsS $i 2>&1>/dev/null
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
    let j+=1
    dialog --title "[ Testing the internet connection ]" --backtitle "$myBACKTITLE" \
           --gauge "\n  Now checking: $i\n" 8 80 $(expr 100 \* $j / $mySITESCOUNT) <<EOF
EOF
  done;

# Let's ask user for install flavor
# Install types are TPOT, HP, INDUSTRIAL, ALL
tput cnorm
myFLAVOR=$(dialog --no-cancel --backtitle "$myBACKTITLE" --title "[ Choose your edition ]" --no-tags --menu \
"\nRequired: 4GB RAM, 64GB disk\nRecommended: 8GB RAM, 128GB SSD" 14 60 5 \
"TPOT" "Standard Honeypots, Suricata & ELK" \
"HP" "Honeypots only, w/o Suricata & ELK" \
"COLLECTOR" "Heralding, Honeytrap & ELK" \
"INDUSTRIAL" "Conpot, Suricata & ELK" \
"EVERYTHING" "Everything" 3>&1 1>&2 2>&3 3>&-)

# Let's ask for a secure tsec password
myUSER="tsec"
myPASS1="pass1"
myPASS2="pass2"
mySECURE="0"
while [ "$myPASS1" != "$myPASS2"  ] && [ "$mySECURE" == "0" ]
  do
    while [ "$myPASS1" == "pass1"  ] || [ "$myPASS1" == "" ]
      do
        myPASS1=$(dialog --insecure --backtitle "$myBACKTITLE" \
                         --title "[ Enter password for console user (tsec) ]" \
                         --passwordbox "\nPassword" 9 60 3>&1 1>&2 2>&3 3>&-)
      done
        myPASS2=$(dialog --insecure --backtitle "$myBACKTITLE" \
                         --title "[ Repeat password for console user (tsec) ]" \
                         --passwordbox "\nPassword" 9 60 3>&1 1>&2 2>&3 3>&-)
    if [ "$myPASS1" != "$myPASS2" ];
      then
        dialog --backtitle "$myBACKTITLE" --title "[ Passwords do not match. ]" \
               --msgbox "\nPlease re-enter your password." 7 60
        myPASS1="pass1"
        myPASS2="pass2"
    fi
    mySECURE=$(printf "%s" "$myPASS1" | cracklib-check | grep -c "OK")
    if [ "$mySECURE" == "0" ] && [ "$myPASS1" == "$myPASS2" ];
      then
        dialog --backtitle "$myBACKTITLE" --title "[ Password is not secure ]" --defaultno --yesno "\nKeep insecure password?" 7 50
        myOK=$?
        if [ "$myOK" == "1" ];
          then
            myPASS1="pass1"
            myPASS2="pass2"
        fi
    fi
  done
printf "%s" "$myUSER:$myPASS1" | chpasswd

# Let's ask for a web username with secure password
myOK="1"
myUSER="tsec"
myPASS1="pass1"
myPASS2="pass2"
mySECURE="0"
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
while [ "$myPASS1" != "$myPASS2"  ] && [ "$mySECURE" == "0" ]
  do
    while [ "$myPASS1" == "pass1"  ] || [ "$myPASS1" == "" ]
      do
        myPASS1=$(dialog --insecure --backtitle "$myBACKTITLE" \
                         --title "[ Enter password for your web user ]" \
                         --passwordbox "\nPassword" 9 60 3>&1 1>&2 2>&3 3>&-)
      done
        myPASS2=$(dialog --insecure --backtitle "$myBACKTITLE" \
                         --title "[ Repeat password for your web user ]" \
                         --passwordbox "\nPassword" 9 60 3>&1 1>&2 2>&3 3>&-)
    if [ "$myPASS1" != "$myPASS2" ];
      then
        dialog --backtitle "$myBACKTITLE" --title "[ Passwords do not match. ]" \
               --msgbox "\nPlease re-enter your password." 7 60
        myPASS1="pass1"
        myPASS2="pass2"
    fi
    mySECURE=$(printf "%s" "$myPASS1" | cracklib-check | grep -c "OK")
    if [ "$mySECURE" == "0" ] && [ "$myPASS1" == "$myPASS2" ];
      then
        dialog --backtitle "$myBACKTITLE" --title "[ Password is not secure ]" --defaultno --yesno "\nKeep insecure password?" 7 50
        myOK=$?
        if [ "$myOK" == "1" ];
          then
            myPASS1="pass1"
            myPASS2="pass2"
        fi
    fi
  done
mkdir -p /data/nginx/conf 2>&1
htpasswd -b -c /data/nginx/conf/nginxpasswd "$myUSER" "$myPASS1" 2>&1 | dialog --title "[ Setting up user and password ]" $myPROGRESSBOXCONF;

# Let's generate a SSL self-signed certificate without interaction (browsers will see it invalid anyway)
tput civis
mkdir -p /data/nginx/cert 2>&1 | dialog --title "[ Generating a self-signed-certificate for NGINX ]" $myPROGRESSBOXCONF;
openssl req \
        -nodes \
        -x509 \
        -sha512 \
        -newkey rsa:8192 \
        -keyout "/data/nginx/cert/nginx.key" \
        -out "/data/nginx/cert/nginx.crt" \
        -days 3650 \
        -subj '/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd' 2>&1 | dialog --title "[ Generating a self-signed-certificate for NGINX ]" $myPROGRESSBOXCONF;

# Let's setup the ntp server
if [ -f $myNTPCONFPATH ];
  then
dialog --title "[ Setting up the ntp server ]" $myPROGRESSBOXCONF <<EOF
EOF
    cp $myNTPCONFPATH /etc/ntp.conf 2>&1 | dialog --title "[ Setting up the ntp server ]" $myPROGRESSBOXCONF
fi

# Let's setup 802.1x networking
if [ -f $myPFXPATH ];
  then
dialog --title "[ Setting 802.1x networking ]" $myPROGRESSBOXCONF <<EOF
EOF
    cp $myPFXPATH /etc/wpa_supplicant/ 2>&1 | dialog --title "[ Setting 802.1x networking ]" $myPROGRESSBOXCONF
    if [ -f $myPFXPWPATH ];
      then
dialog --title "[ Setting up 802.1x password ]" $myPROGRESSBOXCONF <<EOF
EOF
        myPFXPW=$(cat $myPFXPWPATH)
    fi
    myPFXHOSTID=$(cat $myPFXHOSTIDPATH)
tee -a /etc/network/interfaces 2>&1>/dev/null <<EOF
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

tee /etc/wpa_supplicant/wired8021x.conf 2>&1>/dev/null <<EOF
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

tee /etc/wpa_supplicant/wireless8021x.conf 2>&1>/dev/null <<EOF
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
fuECHO "### Providing static ip, wireless example config."
tee -a /etc/network/interfaces 2>&1>/dev/null <<EOF

### Example static ip config
### Replace <eth0> with the name of your physical interface name
#
#auto eth0
#iface eth0 inet static
# address 192.168.1.1
# netmask 255.255.255.0
# network 192.168.1.0
# broadcast 192.168.1.255
# gateway 192.168.1.1
# dns-nameservers 192.168.1.1

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
tee -a /etc/ssh/ssh_config 2>&1>/dev/null <<EOF
UseRoaming no
EOF

# Let's pull some updates
apt-get update -y 2>&1 | dialog --title "[ Pulling updates ]" $myPROGRESSBOXCONF
apt-get upgrade -y 2>&1 | dialog --title "[ Pulling updates ]" $myPROGRESSBOXCONF

# Let's clean up apt
apt-get autoclean -y 2>&1 | dialog --title "[ Pulling updates ]" $myPROGRESSBOXCONF
apt-get autoremove -y 2>&1 | dialog --title "[ Pulling updates ]" $myPROGRESSBOXCONF

# Installing ctop, elasticdump, tpot
pip install --upgrade pip 2>&1 | dialog --title "[ Installing pip ]" $myPROGRESSBOXCONF
pip install elasticsearch-curator==5.4.1 2>&1 | dialog --title "[ Installing elasticsearch-curator ]" $myPROGRESSBOXCONF
pip install yq==2.4.1 2>&1 | dialog --title "[ Installing yq ]" $myPROGRESSBOXCONF
npm install https://github.com/taskrabbit/elasticsearch-dump#9fcc8cc -g 2>&1 | dialog --title "[ Installing elasticsearch-dump ]" $myPROGRESSBOXCONF
wget https://github.com/bcicen/ctop/releases/download/v0.7/ctop-0.7-linux-amd64 -O ctop 2>&1 | dialog --title "[ Installing ctop ]" $myPROGRESSBOXCONF
mv ctop /usr/bin/ 2>&1 | dialog --title "[ Installing ctop ]" $myPROGRESSBOXCONF
chmod +x /usr/bin/ctop 2>&1 | dialog --title "[ Installing ctop ]" $myPROGRESSBOXCONF
git clone https://github.com/dtag-dev-sec/tpotce -b 18.04 /opt/tpot 2>&1 | dialog --title "[ Cloning T-Pot ]" $myPROGRESSBOXCONF

# Let's add a new user
addgroup --gid 2000 tpot 2>&1 | dialog --title "[ Adding new user ]" $myPROGRESSBOXCONF
adduser --system --no-create-home --uid 2000 --disabled-password --disabled-login --gid 2000 tpot 2>&1 | dialog --title "[ Adding new user ]" $myPROGRESSBOXCONF

# Let's set the hostname
a=$(fuRANDOMWORD /opt/tpot/host/usr/share/dict/a.txt)
n=$(fuRANDOMWORD /opt/tpot/host/usr/share/dict/n.txt)
myHOST=$a$n
hostnamectl set-hostname $myHOST 2>&1 | dialog --title "[ Setting new hostname ]" $myPROGRESSBOXCONF
sed -i 's#127.0.1.1.*#127.0.1.1\t'"$myHOST"'#g' /etc/hosts 2>&1 | dialog --title "[ Setting new hostname ]" $myPROGRESSBOXCONF

# Let's patch sshd_config
sed -i 's#\#Port 22#Port 64295#' /etc/ssh/sshd_config 2>&1 | dialog --title "[ SSH listen on tcp/64295 ]" $myPROGRESSBOXCONF
sed -i 's#\#PasswordAuthentication yes#PasswordAuthentication no#' /etc/ssh/sshd_config 2>&1 | dialog --title "[ SSH password authentication only from RFC1918 networks ]" $myPROGRESSBOXCONF
tee -a /etc/ssh/sshd_config 2>&1>/dev/null <<EOF


Match address 127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
    PasswordAuthentication yes
EOF

# Let's make sure only myFLAVOR images will be downloaded and started
case $myFLAVOR in
  COLLECTOR)
    echo "### Preparing COLLECTOR flavor installation."
    cp /opt/tpot/etc/compose/collect.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
  HP)
    echo "### Preparing HONEYPOT flavor installation."
    cp /opt/tpot/etc/compose/hp.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
  INDUSTRIAL)
    echo "### Preparing INDUSTRIAL flavor installation."
    cp /opt/tpot/etc/compose/industrial.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
  TPOT)
    echo "### Preparing TPOT flavor installation."
    cp /opt/tpot/etc/compose/tpot.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
  EVERYTHING)
    echo "### Preparing EVERYTHING flavor installation."
    cp /opt/tpot/etc/compose/all.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
esac

# Let's load docker images
myIMAGESCOUNT=$(cat $myTPOTCOMPOSE | grep -v '#' | grep image | cut -d: -f2 | wc -l)
j=0
for name in $(cat $myTPOTCOMPOSE | grep -v '#' | grep image | cut -d'"' -f2)
  do
    dialog --title "[ Downloading docker images, please be patient ]" --backtitle "$myBACKTITLE" \
           --gauge "\n  Now downloading: $name\n" 8 80 $(expr 100 \* $j / $myIMAGESCOUNT) <<EOF
EOF
    docker pull $name 2>&1>/dev/null
    let j+=1
    dialog --title "[ Downloading docker images, please be patient ]" --backtitle "$myBACKTITLE" \
           --gauge "\n  Now downloading: $name\n" 8 80 $(expr 100 \* $j / $myIMAGESCOUNT) <<EOF
EOF
  done

# Let's add the daily update check with a weekly clean interval
dialog --title "[ Modifying update checks ]" $myPROGRESSBOXCONF <<EOF
EOF
tee /etc/apt/apt.conf.d/10periodic 2>&1>/dev/null <<EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "7";
EOF

# Let's make sure to reboot the system after a kernel panic
dialog --title "[ Reboot after kernel panic ]" $myPROGRESSBOXCONF <<EOF
EOF
tee -a /etc/sysctl.conf 2>&1>/dev/null <<EOF

# Reboot after kernel panic, check via /proc/sys/kernel/panic[_on_oops]
# Set required map count for ELK
kernel.panic = 1
kernel.panic_on_oops = 1
vm.max_map_count = 262144
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF

# Let's add some cronjobs
dialog --title "[ Adding cronjobs ]" $myPROGRESSBOXCONF <<EOF
EOF
tee -a /etc/crontab 2>&1>/dev/null <<EOF

# Check if updated images are available and download them
27 1 * * *      root    docker-compose -f /opt/tpot/etc/tpot.yml pull

# Delete elasticsearch logstash indices older than 90 days
27 4 * * *      root    curator --config /opt/tpot/etc/curator/curator.yml /opt/tpot/etc/curator/actions.yml

# Uploaded binaries are not supposed to be downloaded
*/1 * * * *     root    mv --backup=numbered /data/dionaea/roots/ftp/* /data/dionaea/binaries/

# Daily reboot
27 3 * * *      root    reboot

# Check for updated packages every sunday, upgrade and reboot
27 16 * * 0     root    apt-get autoclean -y && apt-get autoremove -y && apt-get update -y && apt-get upgrade -y && sleep 10 && reboot
EOF

# Let's create some files and folders
mkdir -p /data/ciscoasa/log \
	 /data/conpot/log \
         /data/cowrie/log/tty/ /data/cowrie/downloads/ /data/cowrie/keys/ /data/cowrie/misc/ \
         /data/dionaea/log /data/dionaea/bistreams /data/dionaea/binaries /data/dionaea/rtp /data/dionaea/roots/ftp /data/dionaea/roots/tftp /data/dionaea/roots/www /data/dionaea/roots/upnp \
         /data/elasticpot/log \
         /data/elk/data /data/elk/log \
         /data/glastopf /data/honeytrap/log/ /data/honeytrap/attacks/ /data/honeytrap/downloads/ \
	 /data/glutton/log \
	 /data/heralding/log \
         /data/mailoney/log \
	 /data/nginx/log \
         /data/emobility/log \
         /data/ews/conf \
         /data/rdpy/log \
         /data/spiderfoot \
         /data/suricata/log /home/tsec/.ssh/ \
	 /data/tanner/log /data/tanner/files \
         /data/p0f/log \
         /data/vnclowpot/log 2>&1 | dialog --title "[ Creating some files and folders ]" $myPROGRESSBOXCONF
touch /data/spiderfoot/spiderfoot.db 2>&1 | dialog --title "[ Creating some files and folders ]" $myPROGRESSBOXCONF

# Let's copy some files
tar xvfz /opt/tpot/etc/objects/elkbase.tgz -C / 2>&1 | dialog --title "[ Extracting elkbase.tgz ]" $myPROGRESSBOXCONF
cp    /opt/tpot/host/etc/systemd/* /etc/systemd/system/ 2>&1 | dialog --title "[ Copy configs ]" $myPROGRESSBOXCONF
cp    /opt/tpot/host/etc/issue /etc/ 2>&1 | dialog --title "[ Copy configs ]" $myPROGRESSBOXCONF
cp    /root/installer/keys/authorized_keys /home/tsec/.ssh/authorized_keys 2>&1 | dialog --title "[ Copy configs ]" $myPROGRESSBOXCONF
systemctl enable tpot 2>&1 | dialog --title "[ Enabling service for tpot ]" $myPROGRESSBOXCONF

# Let's take care of some files and permissions
chmod 760 -R /data 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF
chown tpot:tpot -R /data 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF
chmod 644 -R /data/nginx/conf 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF
chmod 644 -R /data/nginx/cert 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF
chmod 600 /home/tsec/.ssh/authorized_keys 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF
chown tsec:tsec /home/tsec/.ssh /home/tsec/.ssh/authorized_keys 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF

# Let's replace "quiet splash" options, set a console font for more screen canvas and update grub
sed -i 's#GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"#GRUB_CMDLINE_LINUX_DEFAULT="consoleblank=0"#' /etc/default/grub 2>&1>/dev/null
sed -i 's#GRUB_CMDLINE_LINUX=""#GRUB_CMDLINE_LINUX="cgroup_enable=memory swapaccount=1"#' /etc/default/grub 2>&1>/dev/null
update-grub 2>&1 | dialog --title "[ Update grub ]" $myPROGRESSBOXCONF
cp /usr/share/consolefonts/Uni2-Terminus12x6.psf.gz /etc/console-setup/
gunzip /etc/console-setup/Uni2-Terminus12x6.psf.gz
sed -i 's#FONTFACE=".*#FONTFACE="Terminus"#' /etc/default/console-setup
sed -i 's#FONTSIZE=".*#FONTSIZE="12x6"#' /etc/default/console-setup
update-initramfs -u 2>&1 | dialog --title "[ Update initramfs ]" $myPROGRESSBOXCONF

# Let's enable a color prompt and add /opt/tpot/bin to path
myROOTPROMPT='PS1="\[\033[38;5;8m\][\[$(tput sgr0)\]\[\033[38;5;1m\]\u\[$(tput sgr0)\]\[\033[38;5;6m\]@\[$(tput sgr0)\]\[\033[38;5;4m\]\h\[$(tput sgr0)\]\[\033[38;5;6m\]:\[$(tput sgr0)\]\[\033[38;5;5m\]\w\[$(tput sgr0)\]\[\033[38;5;8m\]]\[$(tput sgr0)\]\[\033[38;5;1m\]\\$\[$(tput sgr0)\]\[\033[38;5;15m\] \[$(tput sgr0)\]"'
myUSERPROMPT='PS1="\[\033[38;5;8m\][\[$(tput sgr0)\]\[\033[38;5;2m\]\u\[$(tput sgr0)\]\[\033[38;5;6m\]@\[$(tput sgr0)\]\[\033[38;5;4m\]\h\[$(tput sgr0)\]\[\033[38;5;6m\]:\[$(tput sgr0)\]\[\033[38;5;5m\]\w\[$(tput sgr0)\]\[\033[38;5;8m\]]\[$(tput sgr0)\]\[\033[38;5;2m\]\\$\[$(tput sgr0)\]\[\033[38;5;15m\] \[$(tput sgr0)\]"'
tee -a /root/.bashrc 2>&1>/dev/null <<EOF
$myROOTPROMPT
PATH="$PATH:/opt/tpot/bin"
EOF
tee -a /home/tsec/.bashrc 2>&1>/dev/null <<EOF
$myUSERPROMPT
PATH="$PATH:/opt/tpot/bin"
EOF

# Let's create ews.ip before reboot and prevent race condition for first start
/opt/tpot/bin/updateip.sh 2>&1>/dev/null

# Final steps
cp /opt/tpot/host/etc/rc.local /etc/rc.local 2>&1>/dev/null && \
rm -rf /root/installer 2>&1>/dev/null && \
dialog --no-ok --no-cancel --backtitle "$myBACKTITLE" --title "[ Thanks for your patience. Now rebooting. ]" --pause "" 6 80 2 && \
reboot
