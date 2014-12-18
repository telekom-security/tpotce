#!/bin/bash
########################################################
# T-Pot Community Edition post install script          #
# Ubuntu server 14.04, x64                             #
#                                                      #
# v0.18 by mo, DTAG, 2014-12-18                        #
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
apt-get install ntp lxc-docker git -y

# Create the data partition and limit its size
# If we want to extent the size of that filesystem later, without loss of data:
# resize2fs -p data.img 8192M
#fuECHO "### Creating data partition (Please be patient, this may take a while)."
#mkdir -p /opt/virtual-disk/
#dd if=/dev/zero of=/opt/virtual-disk/data.ext4 bs=1024 count=4096000
#mkfs.ext4 /opt/virtual-disk/data.ext4 -F
#tee -a /etc/fstab <<EOF
#/opt/virtual-disk/data.ext4 /data       ext4    loop,rw,nosuid
#EOF
#mkdir -p /data
#mount /opt/virtual-disk/data.ext4 -o loop,rw,nosuid

# Let's add a new user
fuECHO "### Adding new user."
addgroup --gid 2000 tpot
adduser --system --no-create-home --uid 2000 --disabled-password --disabled-login --gid 2000 tpot

# Let's create some files and folders
fuECHO "### Creating some files and folders."
mkdir -p /data/ews/log /data/ews/conf
#mkdir -p /data/puppet/

# Let's modify the ownership / access rights
chmod 760 -R /data
chown tpot:tpot -R /data

# Let's set the hostname
fuECHO "### Setting a new hostname."
myHOST=ce$(date +%s)$RANDOM
hostnamectl set-hostname $myHOST
sed -i 's/127.0.1.1.*/127.0.1.1\t'"$myHOST"'/g' /etc/hosts
#echo $myHOST > /data/puppet/name.conf

# Let's patch sshd_config
fuECHO "### Patching sshd_config to listen on port 64295 and deny password authentication."
sed -i 's#Port 22#Port 64295#' /etc/ssh/sshd_config
sed -i 's#\#PasswordAuthentication yes#PasswordAuthentication no#' /etc/ssh/sshd_config

# Disable ssh service
mv /etc/init/ssh.conf /etc/init/ssh.conf.disable

# Let's add the ssh keys
#fuECHO "### Adding ssh keys for the admin user."
#mkdir -p /home/admin/.ssh/
#tee /home/admin/.ssh/authorized_keys <<EOF
#ssh-rsa AAAAB3NzaC1yc2EAAAABIwAAAQEA8f8Dq8/XuVZl3M8ARxPQNz74T46Gez8nFTV6xjGKh6VZmyU8BL/+ERXSTJg47HsncNLEpqHgPnZTTh1hZK7HxJvPLQ1JrfPO7Fbl2B5Qy26yzAYJTnHQYUBMGTpI8gmLczE6eZcGuK0huMOoot+m7WeIMHQbzZcuNAknPsxBhJHY4s3rvElrJnY7ckz4mroqRSZXvu6w7igthUX3a1A+xsxVmxUatzFJ1Ky4jYswKFdcNPA77/nRckxtt86ORpqJq/r2PjDpuv2JpRha9zdUDpvpdCIQJFM1SdRyGMSrvbMyEWZBCTB3YF/GmQT04sfEytqHUY7zbK7kzNyDhXeg5Q== av@telekom
#ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCt6Af5L8FYaNiDG0JKHPlJDLAbXklK5wVHj1IYqLINR8dIBcGcFwIF+YoJypZmsf1geta9WPjEW8bpd4G6XiYYg6YNRYxgBZScSb0WGVn0rHBMH+cuQxkhIdHucEMq4JFsRTVFWXjpQspu6p5gQxafGHnsLY/RYrgFy9XktS7Ha0Tfa6WXxpF72jyCoRRBUKF8CSip1XFaHIIY0xA0wTHZpmAI7dea4XA44oVDfr6g/4CTDTPQJiwn0HrRnZjgqJPzCT4gyXv+L6c5lcdrob4JpRj/YIis6aD6AMw4PeDsp3d/P9L2Vm9+p2a5Xx5U5cfGNUanvkvicrzZC1v+v3H9 mo@telekom
#ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDHM2Pht1q6VDfRs+gPYu3/Eg5wgFfQrM45A+jRduskcIJSlwO5m/dMEipc10Y+Ut+tIST8ydQA8ZTYicinOjoCbSUju7sTDRb5jMs60nBRaj4BmzCQOqo4hidt3iX8+IpU9JUl8RR5rQzwDsWTdkhuCEEjiD+2YDdJO5kjMoaa1UW19iFEOLY582psoDTmkNY9MOfhoJla4S7m0A6eOMfq4DO/eKMKgOxJ0W8K6fQjSAyMSmqlamirxSjZ2OGohS7r1JVYhTdU6cmJxYRVNa2Rr8BHn8uf1cR4uaV49CfqJgx3W5YMjSjc3nCLt0csfdQd+sur25Gv0033liq7ZQFR ms@telekom
#EOF
#chmod 700 -R /home/admin/.ssh
#chmod 600 /home/admin/.ssh/authorized_keys
#chown admin:admin -R /home/admin/.ssh

# Let's patch docker defaults, so we can run images as service
fuECHO "### Patching docker defaults."
tee -a /etc/default/docker <<EOF
DOCKER_OPTS="-r=false"
EOF

# Let's create an upstart config for the dionaea docker image
fuECHO "### Adding upstart config for the dionaea docker image."
tee -a /etc/init/dionaea.conf <<EOF
description "Dionaea"
author "mo"
start on started docker and filesystem
stop on runlevel [!2345]
respawn
script
  sleep 1
  /usr/bin/docker run --name dionaea --cap-add=NET_ADMIN --rm -p 21:21 -p 42:42 -p 8080:80 -p 135:135 -p 443:443 -p 445:445 -p 1433:1433 -p 3306:3306 -p 5061:5061 -p 5060:5060 -p 69:69/udp -p 5060:5060/udp -v /data/dionaea dtagdevsec/dionaea
end script
post-stop script
  sleep 1
  /usr/bin/docker rm dionaea
end script
EOF

# Let's create an upstart config for the ews docker image
fuECHO "### Adding upstart config for the ews docker image."
tee -a /etc/init/ews.conf <<EOF
description "EWS"
author "mo"
start on started docker and filesystem and started dionaea and started honeytrap and started kippo and started glastopf
stop on runlevel [!2345]
respawn
script
  sleep 15 
  /usr/bin/docker run --name ews --volumes-from dionaea --volumes-from glastopf --volumes-from honeytrap --volumes-from kippo --rm -v /data/ews/:/data/ews/ --link kippo:kippo dtagdevsec/ews
end script
post-stop script
  sleep 1
  /usr/bin/docker rm ews
end script
EOF

# Let's create an upstart config for the glastopf docker image
fuECHO "### Adding upstart config for the glastopf docker image."
tee -a /etc/init/glastopf.conf <<EOF
description "Glastopf"
author "mo"
start on started docker and filesystem
stop on runlevel [!2345]
respawn
script
  sleep 1
  /usr/bin/docker run --name glastopf --rm -p 80:80 -v /data/glastopf dtagdevsec/glastopf 
end script
post-stop script
  sleep 1
  /usr/bin/docker rm glastopf
end script
EOF

# Let's create an upstart config for the honeytrap docker image
fuECHO "### Adding upstart config for the honeytrap docker image."
tee -a /etc/init/honeytrap.conf <<EOF
description "Honeytrap"
author "mo"
start on started docker and filesystem
stop on runlevel [!2345]
respawn
pre-start script
  sleep 1
  /sbin/iptables -A INPUT -w -p tcp --syn -m state --state NEW -j NFQUEUE
end script
script
  sleep 1
  /usr/bin/docker run --name honeytrap --cap-add=NET_ADMIN --net=host --rm -v /data/honeytrap dtagdevsec/honeytrap
end script
post-stop script
  sleep 1
  /sbin/iptables -D INPUT -w -p tcp --syn -m state --state NEW -j NFQUEUE
  /usr/bin/docker rm honeytrap
end script
EOF

# Let's create an upstart config for the kippo docker image
fuECHO "### Adding upstart config for the kippo docker image."
tee -a /etc/init/kippo.conf <<EOF
description "Kippo"
author "mo"
start on started docker and filesystem
stop on runlevel [!2345]
respawn
script
  sleep 1 
  /usr/bin/docker run --name kippo --rm -p 22:2222 -v /data/kippo dtagdevsec/kippo 
end script
post-stop script
  sleep 1
  /usr/bin/docker rm kippo
end script
EOF

# Let's load docker images from local
#fuECHO "### Loading docker images from local."
#cd /root/images
#for name in dionaea ews glastopf honeytrap kippo
#do
#  docker load -i $(ls $name*)
#  docker tag $(ls $name* | cut -d "_" -f 2 | cut -c-12) t3chn0m4g3/beehive:$name
#done
#cd /root
#rm -rf /root/images

# Let's load docker images from remote
fuECHO "### Downloading docker images from DockerHub. Please be patient, this may take a while."
for name in dionaea ews glastopf honeytrap kippo
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
*/1 * * * * root clear > /dev/tty2; date > /dev/tty2; docker ps > /dev/tty2; echo > /dev/tty2
EOF

# Let's add a nice and useful issue text and update rc.local accordingly
fuECHO "### Adding a nice and useful issue text and updating rc.local accordingly."
tee /etc/issue <<EOF
T-Pot Community Edition
Hostname: \n 
IP:



              xxx              .            
            xxx xxx          ==            
          xxx xxx xxx       ===            
       /""""""""""""""""\___/ ===        
  ~~~ {~~ ~~~~ ~~~ ~~~~ ~~ ~ /  ===- ~~~   
       \______ o          __/            
         \    \        __/             
          \____\______/                



CTRL+ALT+F2 - Display current container status
CTRL+ALT+F1 - Return to this screen

EOF

echo "#!/bin/sh -e" > /etc/rc.local.new
echo "# Let's add the first local ip to the /etc/issue file" >> /etc/rc.local.new
echo 'sed -i "s#IP:.*#IP: ""$(hostname -I | awk '"'"'{ print $1 }'"'"')""#" /etc/issue' >> /etc/rc.local.new
echo "exit 0" >> /etc/rc.local.new
chmod +x /etc/rc.local.new

# Final steps
fuECHO "### Thanks for your patience. Now rebooting."
mv /etc/rc.local.new /etc/rc.local && chage -d 0 tsec && sleep 2 && reboot
