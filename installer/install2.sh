#!/bin/bash
########################################################
# T-Pot Community Edition post install script          #
# Ubuntu server 14.04, x64                             #
#                                                      #
# v0.20 by mo, DTAG, 2015-01-20                        #
########################################################

# Let's make sure there is a warning if running for a second time
if [ -f install.log ];
  then fuECHO "### Running more than once may complicate things. Erase install.log if you are really sure."
  exit 1;
fi

# Let's log for the beauty of it
set -e
exec 2> >(tee "install.err")
exec > >(tee "install.log")

# Let's create a function for colorful output
fuECHO () {
  local myRED=1
  local myWHT=7
  tput setaf $myRED
  echo $1 "$2"
  tput setaf $myWHT
}

# Let's modify the sources list
sed -i '/cdrom/d' /etc/apt/sources.list

# Let's add the docker repository
fuECHO "### Adding docker repository."
apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 36A1D7869245C8950F966E92D8576A8BA88D21E9
tee /etc/apt/sources.list.d/docker.list <<EOF
deb https://get.docker.io/ubuntu docker main
EOF

# Let's pull some updates
fuECHO "### Pulling Updates."
apt-get update -y
fuECHO "### Installing Updates."
apt-get dist-upgrade -y

# Let's install all the packages we need
fuECHO "### Installing packages."
apt-get install ethtool git ntp libpam-google-authenticator lxc-docker-1.4.1 vim -y

# Let's add a new user
fuECHO "### Adding new user."
addgroup --gid 2000 tpot
adduser --system --no-create-home --uid 2000 --disabled-password --disabled-login --gid 2000 tpot

# Let's create some files and folders
fuECHO "### Creating some files and folders."
mkdir -p /data/ews/log /data/ews/conf /data/elk/data /data/elk/log

# Let's modify the ownership / access rights
chmod 760 -R /data
chown tpot:tpot -R /data

# Let's set the hostname
fuECHO "### Setting a new hostname."
myHOST=ce$(date +%s)$RANDOM
hostnamectl set-hostname $myHOST
sed -i 's/127.0.1.1.*/127.0.1.1\t'"$myHOST"'/g' /etc/hosts

# Let's patch sshd_config
fuECHO "### Patching sshd_config to listen on port 64295 and deny password authentication."
sed -i 's#Port 22#Port 64295#' /etc/ssh/sshd_config
sed -i 's#\#PasswordAuthentication yes#PasswordAuthentication no#' /etc/ssh/sshd_config

# Let's disable ssh service
mv /etc/init/ssh.conf /etc/init/ssh.conf.disable

# Let's create the 2FA enable script
fuECHO "### Creating 2FA enable script."
tee /home/tsec/2fa_enable.sh <<EOF
#!/bin/bash
echo "### This script will enable Two-Factor-Authentication based on Google Authenticator for SSH."
while true 
do
  echo -n "### Do you want to continue (y/n)? "; read myANSWER;
  case \$myANSWER in
    n)
      echo "### Exiting."
      exit 0;
      ;;
    y)
      break
      ;;
  esac
done
if [ -f /etc/pam.d/sshd.bak ];
  then echo "### Already enabled. Exiting."
  exit 1;
fi
sudo sed -i.bak '\# PAM#aauth required pam_google_authenticator.so' /etc/pam.d/sshd
sudo sed -i.bak 's#ChallengeResponseAuthentication no#ChallengeResponseAuthentication yes#' /etc/ssh/sshd_config
google-authenticator -t -d -f -r 3 -R 30 -w 21
echo "### Please do not forget to run the ssh_enable script."
EOF
chmod 700 /home/tsec/2fa_enable.sh
chown tsec:tsec /home/tsec/2fa_enable.sh

# Let's create the ssh enable script
fuECHO "### Creating ssh enable script."
tee /home/tsec/ssh_enable.sh <<EOF
#!/bin/bash
echo "### This script will enable the ssh service (default port tcp/64295)."
echo "### Password authentication is disabled by default."
while true 
do
  echo -n "### Do you want to continue (y/n)? "; read myANSWER;
  case \$myANSWER in
    n)
      echo "### Exiting."
      exit 0;
      ;;
    y)
      break
      ;;
  esac
done
if [ -f /etc/init/ssh.conf ];
  then echo "### Already enabled. Exiting."
  exit 1;
fi
sudo mv /etc/init/ssh.conf.disable /etc/init/ssh.conf
sudo service ssh start
EOF
chmod 700 /home/tsec/ssh_enable.sh
chown tsec:tsec /home/tsec/ssh_enable.sh


# Let's patch docker defaults, so we can run images as service
fuECHO "### Patching docker defaults."
tee -a /etc/default/docker <<EOF
DOCKER_OPTS="-r=false"
EOF

# Let's create an upstart config for the dionaea docker image
fuECHO "### Adding upstart config for the dionaea docker image."
tee /etc/init/dionaea.conf <<EOF
description "Dionaea"
author "mo"
start on started docker and filesystem
stop on runlevel [!2345]
respawn
script
  sleep 1
  /usr/bin/docker run --name dionaea --cap-add=NET_BIND_SERVICE --rm=true -p 21:21 -p 42:42 -p 8080:80 -p 135:135 -p 443:443 -p 445:445 -p 1433:1433 -p 3306:3306 -p 5061:5061 -p 5060:5060 -p 69:69/udp -p 5060:5060/udp -v /data/dionaea dtagdevsec/dionaea
end script
post-stop script
  sleep 1
  /usr/bin/docker rm dionaea
end script
EOF

# Let's create an upstart config for the elk docker image
fuECHO "### Adding upstart config for the elk docker image."
tee /etc/init/elk.conf <<EOF
description "ELK"
author "mo"
start on started docker and filesystem and started suricata and started ews
stop on runlevel [!2345]
respawn
script
  sleep 15 
  /usr/bin/docker run --name=elk --volumes-from ews --volumes-from suricata -v /data/elk/:/data/elk/ -p 127.0.0.1:64296:80 --rm=true dtagdevsec/elk
end script
post-stop script
  sleep 1
  /usr/bin/docker rm elk 
end script
EOF

# Let's create an upstart config for the ews docker image
fuECHO "### Adding upstart config for the ews docker image."
tee /etc/init/ews.conf <<EOF
description "EWS"
author "mo"
start on started docker and filesystem and started dionaea and started honeytrap and started kippo and started glastopf
stop on runlevel [!2345]
respawn
script
  sleep 15 
  /usr/bin/docker run --name ews --volumes-from dionaea --volumes-from glastopf --volumes-from honeytrap --volumes-from kippo --rm=true -v /data/ews/:/data/ews/ --link kippo:kippo dtagdevsec/ews
end script
post-stop script
  sleep 1
  /usr/bin/docker rm ews
end script
EOF

# Let's create an upstart config for the glastopf docker image
fuECHO "### Adding upstart config for the glastopf docker image."
tee /etc/init/glastopf.conf <<EOF
description "Glastopf"
author "mo"
start on started docker and filesystem
stop on runlevel [!2345]
respawn
script
  sleep 1
  /usr/bin/docker run --name glastopf --rm=true -p 80:80 -v /data/glastopf dtagdevsec/glastopf 
end script
post-stop script
  sleep 1
  /usr/bin/docker rm glastopf
end script
EOF

# Let's create an upstart config for the honeytrap docker image
fuECHO "### Adding upstart config for the honeytrap docker image."
tee /etc/init/honeytrap.conf <<EOF
description "Honeytrap"
author "mo"
start on started docker and filesystem
stop on runlevel [!2345]
respawn
pre-start script
  sleep 1
  /sbin/iptables -A INPUT -p tcp --syn -m state --state NEW --destination-port ! 21,22,42,80,135,443,445,1433,3306,5060,5061,64295,64296 -j NFQUEUE
end script
script
  sleep 1
  /usr/bin/docker run --name honeytrap --cap-add=NET_ADMIN --net=host --rm -v /data/honeytrap dtagdevsec/honeytrap
end script
post-stop script
  sleep 1
  /sbin/iptables -D INPUT -p tcp --syn -m state --state NEW --destination-port ! 21,22,42,80,135,443,445,1433,3306,5060,5061,64295,64296 -j NFQUEUE
  /usr/bin/docker rm honeytrap
end script
EOF

# Let's create an upstart config for the kippo docker image
fuECHO "### Adding upstart config for the kippo docker image."
tee /etc/init/kippo.conf <<EOF
description "Kippo"
author "mo"
start on started docker and filesystem
stop on runlevel [!2345]
respawn
script
  sleep 1 
  /usr/bin/docker run --name kippo --rm=true -p 22:2222 -v /data/kippo dtagdevsec/kippo 
end script
post-stop script
  sleep 1
  /usr/bin/docker rm kippo
end script
EOF

# Let's create an upstart config for the suricata docker image
fuECHO "### Adding upstart config for the suricata docker image."
tee /etc/init/suricata.conf <<EOF
description "Suricata"
author "mo"
start on started docker and filesystem
stop on runlevel [!2345]
respawn
pre-start script
  sleep 1
  myIF=\$(route | grep default | awk '{ print \$8 }')
  /sbin/ethtool --offload \$myIF rx off tx off
  /sbin/ethtool -K \$myIF gso off gro off
  /sbin/ip link set \$myIF promisc on
end script
script
  sleep 1
  /usr/bin/docker run --name suricata --cap-add=NET_ADMIN --net=host --rm=true -v /data/suricata/ dtagdevsec/suricata 
end script
post-stop script
  sleep 1
  /usr/bin/docker rm suricata
end script
EOF

# Let's load docker images from remote
fuECHO "### Downloading docker images from DockerHub. Please be patient, this may take a while."
for name in dionaea elk ews glastopf honeytrap kippo suricata
do
  docker pull dtagdevsec/$name
done

# Let's add the daily update check with a weekly clean interval
fuECHO "### Modifying update checks."
tee /etc/apt/apt.conf.d/10periodic <<EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "7";
EOF

# Let's add "docker ps" output to /dev/tty2 every 60s
fuECHO "### Adding useful docker output to tty2"
tee -a /etc/crontab <<EOF

# Show running containers every 60s via /dev/tty2
*/1 * * * * root echo > /dev/tty2; date > /dev/tty2; docker ps > /dev/tty2; echo > /dev/tty2
EOF

# Let's add a nice and useful issue text and update rc.local accordingly
fuECHO "### Adding a nice and useful issue text and updating rc.local accordingly."
tee /etc/issue <<EOF
T-Pot Community Edition
Hostname: \n 
IP:


___________     _____________________________
\\\__    ___/     \\\______   \\\_____  \\\__    ___/
  |    |  ______ |     ___//   |   \\\|    |
  |    | /_____/ |    |   /    |    \\\    |
  |____|         |____|   \\\_______  /____|
                                  \\\/


CTRL+ALT+F2 - Display current container status
CTRL+ALT+F1 - Return to this screen

EOF

tee /etc/rc.local.new <<EOF
#!/bin/sh -e
# Let's add the first local ip to the /etc/issue file
sed -i "s#IP:.*#IP: \$(hostname -I | awk '{ print \$1 }')#" /etc/issue
setupcon
exit 0
EOF

chmod +x /etc/rc.local.new

# Final steps
fuECHO "### Thanks for your patience. Now rebooting."
mv /etc/rc.local.new /etc/rc.local && chage -d 0 tsec && sleep 2 && reboot
