#!/bin/bash
# T-Pot Universal Installer

##################################
# Extract command line arguments #
##################################

myLSB=$(lsb_release -r | awk '{ print $2 }')
myLSB_SUPPORTED="18.04"
myINFO="\
############################################
### T-Pot Installer for Ubuntu $myLSB_SUPPORTED LTS ###
############################################

Disclaimer:
This script will install T-Pot on this system, by running the script you know what you are doing:
1. SSH will be reconfigured to tcp/64295
2. Some packages will be installed, some will be upgraded
3. Please ensure other means of access to this system in case something goes wrong.
4. At best this script well be executed on the console instead through a SSH session.

###########################################

Usage:
        $0 --help - Help.

Example:
        $0 --type=user - Best option for most users."

if [ "$myLSB" != "$myLSB_SUPPORTED" ];
  then
    echo "Aborting. Ubuntu $myLSB is not supported."
    exit
fi
if [ "$1" == "" ];
  then
    echo "$myINFO"
    exit
fi
for i in "$@"
  do
    case $i in
      --conf=*)
        myTPOT_CONF_FILE="${i#*=}"
        shift
      ;;
      --type=user)
        myTPOT_DEPLOYMENT_TYPE="${i#*=}"
        shift
      ;;
      --type=auto)
        myTPOT_DEPLOYMENT_TYPE="${i#*=}"
        shift
      ;;
      --type=iso)
        myTPOT_DEPLOYMENT_TYPE="${i#*=}"
        shift
      ;;
      --help)
        echo "Usage: $0 <options>"
        echo
        echo "--conf=<Path to \"tpot.conf\">"
	echo "  Use this if you want to automatically deploy a T-Pot instance (--type=auto implied)."
        echo "  A configuration example is available in \"tpotce/iso/installer/tpot.conf.dist\"."
        echo
        echo "--type=<[user, auto, iso]>"
        echo "  user, use this if you want to manually install a T-Pot on a Ubuntu 18.04 LTS machine."
        echo "  auto, implied if a configuration file is passed as an argument for automatic deployment."
        echo "  iso, use this if you are a T-Pot developer and want to install a T-Pot from a pre-compiled iso."
        echo
	exit
      ;;
      *)
        echo "$myINFO"
	exit
      ;;
    esac
  done


###################################################
# Validate command line arguments and load config #
###################################################

# If a valid config file exists, set deployment type to "auto" and load the configuration
if [ "$myTPOT_DEPLOYMENT_TYPE" == "auto" ] && [ "$myTPOT_CONF_FILE" == "" ];
  then
    echo "Aborting. No configuration file given."
    exit
fi
if [ -s "$myTPOT_CONF_FILE" ] && [ "$myTPOT_CONF_FILE" != "" ];
  then
    myTPOT_DEPLOYMENT_TYPE="auto"
    if [ "$(head -n 1 $myTPOT_CONF_FILE | grep -c "# tpot")" == "1" ];
      then
        source "$myTPOT_CONF_FILE"
      else
	echo "Aborting. Config file \"$myTPOT_CONF_FILE\" not a T-Pot configuration file."
        exit
      fi
  elif ! [ -s "$myTPOT_CONF_FILE" ] && [ "$myTPOT_CONF_FILE" != "" ];
    then
      echo "Aborting. Config file \"$myTPOT_CONF_FILE\" not found."
      exit
fi


#######################
# Prepare environment #
#######################

# Got root?
function fuGOT_ROOT {
echo
echo -n "### Checking for root: "
if [ "$(whoami)" != "root" ];
  then
    echo "[ NOT OK ]"
    echo "### Please run as root."
    echo "### Example: sudo $0"
    exit
  else
    echo "[ OK ]"
fi
}

# Let's check if all dependencies are met
function fuGET_DEPS {
local myPACKAGES="apache2-utils apparmor apt-transport-https aufs-tools bash-completion build-essential ca-certificates cgroupfs-mount cockpit cockpit-docker curl debconf-utils dialog dnsutils docker.io docker-compose dstat ethtool fail2ban genisoimage git glances grc html2text htop ifupdown iptables iw jq libcrack2 libltdl7 lm-sensors man mosh multitail net-tools npm ntp openssh-server openssl pass prips software-properties-common syslinux psmisc pv python-pip unattended-upgrades unzip vim wireless-tools wpasupplicant"
apt-get -y update
apt-get -y install software-properties-common
add-apt-repository "deb http://archive.ubuntu.com/ubuntu $(lsb_release -sc) main universe restricted multiverse"
echo
echo "### Getting update information."
echo
apt-get -y update
echo
echo "### Upgrading packages."
echo
# Downlaod and upgrade packages, but silently keep existing configs
echo "docker.io docker.io/restart       boolean true" | debconf-set-selections -v
echo "debconf debconf/frontend select noninteractive" | debconf-set-selections -v
apt-get -y dist-upgrade -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" --force-yes
echo
echo "### Installing T-Pot dependencies."
echo
apt-get -y install $myPACKAGES
}

# Let's load dialog color theme
function fuDIALOG_SETUP {
echo
echo -n "### Checking for dialogrc: "
if [ -f "dialogrc" ];
  then
    echo "[ OK ]"
    cp dialogrc /etc/
  else
    echo "[ NOT OK ]"
    echo "### 'dialogrc' is missing. Please run 'install.sh' from within the setup folder."
    exit
  fi
}

# Let's check for other services
function fuCHECK_PORTS {
if [ "$myTPOT_DEPLOYMENT_TYPE" == "user" ];
  then
    echo
    echo "### Checking for active services."
    echo
    grc netstat -tulpen
    echo
    echo "### Please review your running services."
    echo "### We will take care of SSH (22), but other services i.e. FTP (21), TELNET (23), SMTP (25), HTTP (80), HTTPS (443), etc."
    echo "### might collide with T-Pot's honeypots and prevent T-Pot from starting successfully."
    echo
    while [ 1 != 2 ]
      do
        read -s -n 1 -p "Continue [y/n]? " mySELECT
	echo
        case "$mySELECT" in
          [y,Y])
            break
            ;;
          [n,N])
            exit
            ;;
        esac
      done
fi
}


# Prepare running the installer
echo "$myINFO" | head -n 3
fuGOT_ROOT
fuGET_DEPS
fuCHECK_PORTS
fuDIALOG_SETUP


#############
# Installer #
#############

# Set TERM, DIALOGRC
export TERM=linux
export DIALOGRC=/etc/dialogrc

#######################
# Global vars section #
#######################

myBACKTITLE="T-Pot-Installer"
myCONF_FILE="/root/installer/iso.conf"
myPROGRESSBOXCONF=" --backtitle "$myBACKTITLE" --progressbox 24 80"
mySITES="https://hub.docker.com https://github.com https://pypi.python.org https://ubuntu.com"
myTPOTCOMPOSE="/opt/tpot/etc/tpot.yml"

#####################
# Functions section #
#####################

fuRANDOMWORD () {
  local myWORDFILE="$1"
  local myLINES=$(cat $myWORDFILE  | wc -l)
  local myRANDOM=$((RANDOM % $myLINES))
  local myNUM=$((myRANDOM * myRANDOM % $myLINES + 1))
  echo -n $(sed -n "$myNUM p" $myWORDFILE | tr -d \' | tr A-Z a-z)
}

# If this is a ISO installation we need to wait a few seconds to avoid interference with service messages
if [ "$myTPOT_DEPLOYMENT_TYPE" == "iso" ];
  then
    sleep 5
    tput civis
    dialog --no-ok --no-cancel --backtitle "$myBACKTITLE" --title "[ Wait to avoid interference with service messages ]" --pause "" 6 80 7
fi

# Let's load the iso config file if there is one
if [ -f $myCONF_FILE ];
  then
    dialog --backtitle "$myBACKTITLE" --title "[ Found personalized iso.config ]" --msgbox "\nYour personalized settings will be applied!" 7 47
    source $myCONF_FILE
  else
    # dialog logic considers 1=false, 0=true
    myCONF_PROXY_USE="1"
    myCONF_PFX_USE="1"
    myCONF_NTP_USE="1"
fi


### <--- Begin proxy setup
# If a proxy is set in iso.conf it needs to be setup.
# However, none of the other installation types will automatically take care of a proxy.
# Please open a feature request if you think this is something worth considering.
myPROXY="http://$myCONF_PROXY_IP:$myCONF_PROXY_PORT"
myPROXY_ENV="export http_proxy=$myPROXY
export https_proxy=$myPROXY
export HTTP_PROXY=$myPROXY
export HTTPS_PROXY=$myPROXY
export no_proxy=localhost,127.0.0.1,.sock
"
myPROXY_APT="Acquire::http::Proxy \"$myPROXY\";
Acquire::https::Proxy \"$myPROXY\";
"
myPROXY_DOCKER="http_proxy=$myPROXY
https_proxy=$myPROXY
HTTP_PROXY=$myPROXY
HTTPS_PROXY=$myPROXY
no_proxy=localhost,127.0.0.1,.sock
"

if [ "$myCONF_PROXY_USE" == "0" ];
  then
    # Let's setup proxy for the environment
    echo "$myPROXY_ENV" 2>&1 | tee -a /etc/environment | dialog --title "[ Setting up the proxy ]" $myPROGRESSBOXCONF
    source /etc/environment

    # Let's setup the proxy for apt
    echo "$myPROXY_APT" 2>&1 | tee /etc/apt/apt.conf | dialog --title "[ Setting up the proxy ]" $myPROGRESSBOXCONF

    # Let's add proxy settings to docker defaults
    echo "$myPROXY_DOCKER" 2>&1 | tee -a /etc/default/docker | dialog --title "[ Setting up the proxy ]" $myPROGRESSBOXCONF

    # Let's restart docker for proxy changes to take effect
    systemctl stop docker 2>&1 | dialog --title "[ Stop docker service ]" $myPROGRESSBOXCONF
    systemctl start docker 2>&1 | dialog --title "[ Start docker service ]" $myPROGRESSBOXCONF
fi
### ---> End proxy setup

# Let's test the internet connection
if [ "$myTPOT_DEPLOYMENT_TYPE" == "iso" ] || [ "$myTPOT_DEPLOYMENT_TYPE" == "user" ];
  then
    mySITESCOUNT=$(echo $mySITES | wc -w)
    j=0
    for i in $mySITES;
      do
        curl --connect-timeout 30 -IsS $i 2>&1>/dev/null | dialog --title "[ Testing the internet connection ]" --backtitle "$myBACKTITLE" \
                                                                  --gauge "\n  Now checking: $i\n" 8 80 $(expr 100 \* $j / $mySITESCOUNT)
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
      echo 2>&1>/dev/null | dialog --title "[ Testing the internet connection ]" --backtitle "$myBACKTITLE" \
                                                                                 --gauge "\n  Now checking: $i\n" 8 80 $(expr 100 \* $j / $mySITESCOUNT)
    done;
fi
# Let's put cursor back in standard form
tput cnorm

####################
# User interaction #
####################

# Let's ask the user for install flavor
if [ "$myTPOT_DEPLOYMENT_TYPE" == "iso" ] || [ "$myTPOT_DEPLOYMENT_TYPE" == "user" ];
  then
    myCONF_TPOT_FLAVOR=$(dialog --no-cancel --backtitle "$myBACKTITLE" --title "[ Choose Your T-Pot NG Edition ]" --menu \
    "\nRequired: 6GB RAM, 128GB SSD\nRecommended: 8GB RAM, 256GB SSD" 15 70 7 \
    "STANDARD" "Honeypots, ELK, NSM & Tools" \
    "SENSOR" "Just Honeypots, EWS Poster & NSM" \
    "INDUSTRIAL" "Conpot, RDPY, Vnclowpot, ELK, NSM & Tools" \
    "COLLECTOR" "Heralding, ELK, NSM & Tools" \
    "NEXTGEN" "NextGen (Glutton instead of Honeytrap)" \
    "LEGACY" "Standard Edition from previous release" 3>&1 1>&2 2>&3 3>&-)
fi

# Let's ask for a secure tsec password if installation type is iso
if [ "$myTPOT_DEPLOYMENT_TYPE" == "iso" ];
  then
    myCONF_TPOT_USER="tsec"
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
    printf "%s" "$myCONF_TPOT_USER:$myPASS1" | chpasswd
fi

# Let's ask for a web user credentials if deployment type is iso or user
# In case of auto, credentials are created from config values
# Skip this step entirely if SENSOR flavor
if [ "$myTPOT_DEPLOYMENT_TYPE" == "iso" ] || [ "$myTPOT_DEPLOYMENT_TYPE" == "user" ];
  then
    myOK="1"
    myCONF_WEB_USER="webuser"
    myCONF_WEB_PW="pass1"
    myCONF_WEB_PW2="pass2"
    mySECURE="0"
    while [ 1 != 2 ]
      do
        myCONF_WEB_USER=$(dialog --backtitle "$myBACKTITLE" --title "[ Enter your web user name ]" --inputbox "\nUsername (tsec not allowed)" 9 50 3>&1 1>&2 2>&3 3>&-)
        myCONF_WEB_USER=$(echo $myCONF_WEB_USER | tr -cd "[:alnum:]_.-")
        dialog --backtitle "$myBACKTITLE" --title "[ Your username is ]" --yesno "\n$myCONF_WEB_USER" 7 50
        myOK=$?
        if [ "$myOK" = "0" ] && [ "$myCONF_WEB_USER" != "tsec" ] && [ "$myCONF_WEB_USER" != "" ];
          then
            break
        fi
      done
    while [ "$myCONF_WEB_PW" != "$myCONF_WEB_PW2"  ] && [ "$mySECURE" == "0" ]
      do
        while [ "$myCONF_WEB_PW" == "pass1"  ] || [ "$myCONF_WEB_PW" == "" ]
          do
            myCONF_WEB_PW=$(dialog --insecure --backtitle "$myBACKTITLE" \
                             --title "[ Enter password for your web user ]" \
                             --passwordbox "\nPassword" 9 60 3>&1 1>&2 2>&3 3>&-)
          done
        myCONF_WEB_PW2=$(dialog --insecure --backtitle "$myBACKTITLE" \
                         --title "[ Repeat password for your web user ]" \
                         --passwordbox "\nPassword" 9 60 3>&1 1>&2 2>&3 3>&-)
        if [ "$myCONF_WEB_PW" != "$myCONF_WEB_PW2" ];
          then
            dialog --backtitle "$myBACKTITLE" --title "[ Passwords do not match. ]" \
                   --msgbox "\nPlease re-enter your password." 7 60
            myCONF_WEB_PW="pass1"
            myCONF_WEB_PW2="pass2"
        fi
        mySECURE=$(printf "%s" "$myCONF_WEB_PW" | cracklib-check | grep -c "OK")
        if [ "$mySECURE" == "0" ] && [ "$myCONF_WEB_PW" == "$myCONF_WEB_PW2" ];
          then
            dialog --backtitle "$myBACKTITLE" --title "[ Password is not secure ]" --defaultno --yesno "\nKeep insecure password?" 7 50
            myOK=$?
            if [ "$myOK" == "1" ];
              then
                myCONF_WEB_PW="pass1"
                myCONF_WEB_PW2="pass2"
            fi
        fi
      done
fi
# If flavor is SENSOR do not write credentials
if ! [ "$myCONF_TPOT_FLAVOR" == "SENSOR" ];
  then
    mkdir -p /data/nginx/conf 2>&1
    htpasswd -b -c /data/nginx/conf/nginxpasswd "$myCONF_WEB_USER" "$myCONF_WEB_PW" 2>&1 | dialog --title "[ Setting up user and password ]" $myPROGRESSBOXCONF;
fi


########################
# Installation section #
########################

# Put cursor in invisible mode
tput civis

# Let's generate a SSL self-signed certificate without interaction (browsers will see it invalid anyway)
if ! [ "$myCONF_TPOT_FLAVOR" == "SENSOR" ];
then
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
fi

# Let's setup the ntp server
if [ "$myCONF_NTP_USE" == "0" ];
  then
    cp $myCONF_NTP_CONF_FILE /etc/ntp.conf 2>&1 | dialog --title "[ Setting up the ntp server ]" $myPROGRESSBOXCONF
fi

# Let's setup 802.1x networking
myNETWORK_INTERFACES="
wpa-driver wired
wpa-conf /etc/wpa_supplicant/wired8021x.conf

### Example wireless config for 802.1x
### This configuration was tested with the IntelNUC series
### If problems occur you can try and change wpa-driver to \"iwlwifi\"
### Do not forget to enter a ssid in /etc/wpa_supplicant/wireless8021x.conf
### The Intel NUC uses wlpXsY notation instead of wlanX
#
#auto wlp2s0
#iface wlp2s0 inet dhcp
#        wpa-driver wext
#        wpa-conf /etc/wpa_supplicant/wireless8021x.conf
"
myNETWORK_WIRED8021x="ctrl_interface=/var/run/wpa_supplicant
ctrl_interface_group=root
eapol_version=1
ap_scan=1
network={
  key_mgmt=IEEE8021X
  eap=TLS
  identity=\"host/$myCONF_PFX_HOST_ID\"
  private_key=\"/etc/wpa_supplicant/8021x.pfx\"
  private_key_passwd=\"$myCONF_PFX_PW\"
}
"
myNETWORK_WLAN8021x="ctrl_interface=/var/run/wpa_supplicant
ctrl_interface_group=root
eapol_version=1
ap_scan=1
network={
  ssid="<your_ssid_here_without_brackets>"
  key_mgmt=WPA-EAP
  pairwise=CCMP
  group=CCMP
  eap=TLS
  identity="host/$myCONF_PFX_HOST_ID"
  private_key="/etc/wpa_supplicant/8021x.pfx"
  private_key_passwd="$myCONF_PFX_PW"
}
"
if [ "myCONF_PFX_USE" == "0" ];
  then
    cp $myCONF_PFX_FILE /etc/wpa_supplicant/ 2>&1 | dialog --title "[ Setting 802.1x networking ]" $myPROGRESSBOXCONF
    echo "$myNETWORK_INTERFACES" 2>&1 | tee -a /etc/network/interfaces | dialog --title "[ Setting 802.1x networking ]" $myPROGRESSBOXCONF

    echo "$myNETWORK_WIRED8021x" 2>&1 | tee /etc/wpa_supplicant/wired8021x.conf | dialog --title "[ Setting 802.1x networking ]" $myPROGRESSBOXCONF

    echo "$myNETWORK_WLAN8021x" 2>&1 | tee /etc/wpa_supplicant/wireless8021x.conf | dialog --title "[ Setting 802.1x networking ]" $myPROGRESSBOXCONF
fi

# Let's provide a wireless example config ...
myNETWORK_WLANEXAMPLE="
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
#   wpa-psk \"<your_password_here_without_brackets>\"
"
echo "$myNETWORK_WLANEXAMPLE" 2>&1 | tee -a /etc/network/interfaces | dialog --title "[ Provide WLAN example config ]" $myPROGRESSBOXCONF

# Let's modify the sources list
sed -i '/cdrom/d' /etc/apt/sources.list

# Let's make sure SSH roaming is turned off (CVE-2016-0777, CVE-2016-0778)
echo "UseRoaming no" 2>&1 | tee -a /etc/ssh/ssh_config | dialog --title "[ Turn SSH roaming off ]" $myPROGRESSBOXCONF

# Installing ctop, elasticdump, tpot, yq
npm install https://github.com/taskrabbit/elasticsearch-dump -g 2>&1 | dialog --title "[ Installing elasticsearch-dump ]" $myPROGRESSBOXCONF
pip install --upgrade pip 2>&1 | dialog --title "[ Installing pip ]" $myPROGRESSBOXCONF
hash -r 2>&1 | dialog --title "[ Installing pip ]" $myPROGRESSBOXCONF
pip install elasticsearch-curator yq 2>&1 | dialog --title "[ Installing elasticsearch-curator, yq ]" $myPROGRESSBOXCONF
wget https://github.com/bcicen/ctop/releases/download/v0.7.1/ctop-0.7.1-linux-amd64 -O /usr/bin/ctop 2>&1 | dialog --title "[ Installing ctop ]" $myPROGRESSBOXCONF
chmod +x /usr/bin/ctop 2>&1 | dialog --title "[ Installing ctop ]" $myPROGRESSBOXCONF
git clone https://github.com/dtag-dev-sec/tpotce /opt/tpot 2>&1 | dialog --title "[ Cloning T-Pot ]" $myPROGRESSBOXCONF

# Let's create the T-Pot user
addgroup --gid 2000 tpot 2>&1 | dialog --title "[ Adding T-Pot user ]" $myPROGRESSBOXCONF
adduser --system --no-create-home --uid 2000 --disabled-password --disabled-login --gid 2000 tpot 2>&1 | dialog --title "[ Adding T-Pot user ]" $myPROGRESSBOXCONF

# Let's set the hostname
a=$(fuRANDOMWORD /opt/tpot/host/usr/share/dict/a.txt)
n=$(fuRANDOMWORD /opt/tpot/host/usr/share/dict/n.txt)
myHOST=$a$n
hostnamectl set-hostname $myHOST 2>&1 | dialog --title "[ Setting new hostname ]" $myPROGRESSBOXCONF
sed -i 's#127.0.1.1.*#127.0.1.1\t'"$myHOST"'#g' /etc/hosts 2>&1 | dialog --title "[ Setting new hostname ]" $myPROGRESSBOXCONF
if [ -f "/etc/cloud/cloud.cfg" ];
  then
    sed -i 's/preserve_hostname: false/preserve_hostname: true/' /etc/cloud/cloud.cfg
fi

# Let's patch cockpit.socket, sshd_config
sed -i 's#ListenStream=9090#ListenStream=64294#' /lib/systemd/system/cockpit.socket 2>&1 | dialog --title "[ Cockpit listen on tcp/64294 ]" $myPROGRESSBOXCONF
sed -i '/^port/Id' /etc/ssh/sshd_config 2>&1 | dialog --title "[ SSH listen on tcp/64295 ]" $myPROGRESSBOXCONF
echo "Port 64295" >> /etc/ssh/sshd_config 2>&1 | dialog --title "[ SSH listen on tcp/64295 ]" $myPROGRESSBOXCONF

# Let's make sure only myCONF_TPOT_FLAVOR images will be downloaded and started
case $myCONF_TPOT_FLAVOR in
  STANDARD)
    echo "### Preparing STANDARD flavor installation."
    ln -s /opt/tpot/etc/compose/standard.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
  SENSOR)
    echo "### Preparing SENSOR flavor installation."
    ln -s /opt/tpot/etc/compose/sensor.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
  INDUSTRIAL)
    echo "### Preparing INDUSTRIAL flavor installation."
    ln -s /opt/tpot/etc/compose/industrial.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
  COLLECTOR)
    echo "### Preparing COLLECTOR flavor installation."
    ln -s /opt/tpot/etc/compose/collector.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
  NEXTGEN)
    echo "### Preparing NEXTGEN flavor installation."
    ln -s /opt/tpot/etc/compose/nextgen.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
  LEGACY)
    echo "### Preparing LEGACY flavor installation."
    ln -s /opt/tpot/etc/compose/legacy.yml $myTPOTCOMPOSE 2>&1>/dev/null
  ;;
esac

# Let's load docker images in parallel
function fuPULLIMAGES {
for name in $(cat $myTPOTCOMPOSE | grep -v '#' | grep image | cut -d'"' -f2 | uniq)
  do
    docker pull $name &
done
wait
}
fuPULLIMAGES 2>&1 | dialog --title "[ Pulling docker images, please be patient ]" $myPROGRESSBOXCONF

# Let's add the daily update check with a weekly clean interval
myUPDATECHECK="APT::Periodic::Update-Package-Lists \"1\";
APT::Periodic::Download-Upgradeable-Packages \"0\";
APT::Periodic::AutocleanInterval \"7\";
"
echo "$myUPDATECHECK" 2>&1 | tee /etc/apt/apt.conf.d/10periodic | dialog --title "[ Modifying update checks ]" $myPROGRESSBOXCONF

# Let's make sure to reboot the system after a kernel panic
mySYSCTLCONF="
# Reboot after kernel panic, check via /proc/sys/kernel/panic[_on_oops]
# Set required map count for ELK
kernel.panic = 1
kernel.panic_on_oops = 1
vm.max_map_count = 262144
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
"
echo "$mySYSCTLCONF" 2>&1 | tee -a /etc/sysctl.conf | dialog --title "[ Tweak Sysctl ]" $myPROGRESSBOXCONF

# Let's setup fail2ban config
myFAIL2BANCONF="[DEFAULT]
ignore-ip = 127.0.0.1/8
bantime = 3600
findtime = 600
maxretry = 5

[nginx-http-auth]
enabled  = true
filter   = nginx-http-auth
port     = 64297
logpath  = /data/nginx/log/error.log

[pam-generic]
enabled = true
port    = 64294
filter  = pam-generic
logpath = /var/log/auth.log

[sshd]
enabled = true
port    = 64295
filter  = sshd
logpath = /var/log/auth.log
"
echo "$myFAIL2BANCONF" 2>&1 | tee /etc/fail2ban/jail.d/tpot.conf | dialog --title "[ Setup fail2ban config ]" $myPROGRESSBOXCONF

# Fix systemd error https://github.com/systemd/systemd/issues/3374
mySYSTEMDFIX="[Link]
NamePolicy=kernel database onboard slot path
MACAddressPolicy=none
"
echo "$mySYSTEMDFIX" 2>&1 | tee /etc/systemd/network/99-default.link | dialog --title "[ systemd fix ]" $myPROGRESSBOXCONF

# Let's add some cronjobs
myCRONJOBS="
# Check if updated images are available and download them
27 1 * * *      root    docker-compose -f /opt/tpot/etc/tpot.yml pull

# Delete elasticsearch logstash indices older than 90 days
27 4 * * *      root    curator --config /opt/tpot/etc/curator/curator.yml /opt/tpot/etc/curator/actions.yml

# Uploaded binaries are not supposed to be downloaded
*/1 * * * *     root    mv --backup=numbered /data/dionaea/roots/ftp/* /data/dionaea/binaries/

# Daily reboot
27 3 * * *      root    systemctl stop tpot && docker stop \$(docker ps -aq) || docker rm \$(docker ps -aq) || reboot

# Check for updated packages every sunday, upgrade and reboot
27 16 * * 0     root    apt-get autoclean -y && apt-get autoremove -y && apt-get update -y && apt-get upgrade -y && sleep 10 && reboot
"
echo "$myCRONJOBS" 2>&1 | tee -a /etc/crontab | dialog --title "[ Adding cronjobs ]" $myPROGRESSBOXCONF

# Let's create some files and folders
mkdir -p /data/adbhoney/downloads /data/adbhoney/log \
         /data/ciscoasa/log \
	 /data/conpot/log \
         /data/cowrie/log/tty/ /data/cowrie/downloads/ /data/cowrie/keys/ /data/cowrie/misc/ \
         /data/dionaea/log /data/dionaea/bistreams /data/dionaea/binaries /data/dionaea/rtp /data/dionaea/roots/ftp /data/dionaea/roots/tftp /data/dionaea/roots/www /data/dionaea/roots/upnp \
         /data/elasticpot/log \
         /data/elk/data /data/elk/log \
         /data/glastopf/log /data/glastopf/db \
         /data/honeytrap/log/ /data/honeytrap/attacks/ /data/honeytrap/downloads/ \
	 /data/glutton/log \
	 /data/heralding/log \
         /data/mailoney/log \
         /data/medpot/log \
	 /data/nginx/log \
         /data/emobility/log \
         /data/ews/conf \
         /data/rdpy/log \
         /data/spiderfoot \
         /data/suricata/log /home/tsec/.ssh/ \
	 /data/tanner/log /data/tanner/files \
         /data/p0f/log 2>&1 | dialog --title "[ Creating some files and folders ]" $myPROGRESSBOXCONF
touch /data/spiderfoot/spiderfoot.db 2>&1 | dialog --title "[ Creating some files and folders ]" $myPROGRESSBOXCONF
touch /data/nginx/log/error.log  2>&1 | dialog --title "[ Creating some files and folders ]" $myPROGRESSBOXCONF

# Let's copy some files
tar xvfz /opt/tpot/etc/objects/elkbase.tgz -C / 2>&1 | dialog --title "[ Extracting elkbase.tgz ]" $myPROGRESSBOXCONF
cp /opt/tpot/host/etc/systemd/* /etc/systemd/system/ 2>&1 | dialog --title "[ Copy configs ]" $myPROGRESSBOXCONF
cp /opt/tpot/host/etc/issue /etc/ 2>&1 | dialog --title "[ Copy configs ]" $myPROGRESSBOXCONF
systemctl enable tpot 2>&1 | dialog --title "[ Enabling service for tpot ]" $myPROGRESSBOXCONF

# Let's take care of some files and permissions
chmod 760 -R /data 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF
chown tpot:tpot -R /data 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF
chmod 644 -R /data/nginx/conf 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF
chmod 644 -R /data/nginx/cert 2>&1 | dialog --title "[ Set permissions and ownerships ]" $myPROGRESSBOXCONF

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
for i in $(ls -d /home/*/)
  do
tee -a $i.bashrc 2>&1>/dev/null <<EOF
$myUSERPROMPT
PATH="$PATH:/opt/tpot/bin"
EOF
done

# Let's create ews.ip before reboot and prevent race condition for first start
/opt/tpot/bin/updateip.sh 2>&1>/dev/null

# Let's clean up apt
apt-get autoclean -y 2>&1 | dialog --title "[ Cleaning up ]" $myPROGRESSBOXCONF
apt-get autoremove -y 2>&1 | dialog --title "[ Cleaning up ]" $myPROGRESSBOXCONF

# Final steps
cp /opt/tpot/host/etc/rc.local /etc/rc.local 2>&1>/dev/null && \
rm -rf /root/installer 2>&1>/dev/null && \
if [ "$myTPOT_DEPLOYMENT_TYPE" == "auto" ];
  then
    echo "Done. Please reboot."
  else
    dialog --no-ok --no-cancel --backtitle "$myBACKTITLE" --title "[ Thanks for your patience. Now rebooting. ]" --pause "" 6 80 2 && \
    reboot
fi
