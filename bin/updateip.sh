#!/bin/bash
# Let's add the first local ip to the /etc/issue and external ip to ews.ip file
# If the external IP cannot be detected, the internal IP will be inherited.
source /etc/environment
myUUID=$(lsblk -o MOUNTPOINT,UUID | grep "/" | awk '{ print $2 }')
myLOCALIP=$(hostname -I | awk '{ print $1 }')
myEXTIP=$(/opt/tpot/bin/myip.sh)
if [ "$myEXTIP" = "" ];
  then
    myEXTIP=$myLOCALIP
fi
mySSHUSER=$(cat /etc/passwd | grep 1000 | cut -d ':' -f1)
echo "[H[2J" > /etc/issue
toilet -f ivrit -F metal --filter border:metal "T-Pot   20.06" | sed 's/\\/\\\\/g' >> /etc/issue
echo >> /etc/issue
echo ",---- [ [1;34m\n[0m ] [ [0;34m\d[0m ] [ [1;30m\t[0m ]" >> /etc/issue
echo "|" >> /etc/issue
echo "| [1;34mIP: $myLOCALIP ($myEXTIP)[0m" >> /etc/issue
echo "| [0;34mSSH: ssh -l tsec -p 64295 $myLOCALIP[0m" >> /etc/issue 
echo "| [1;30mWEB: https://$myLOCALIP:64297[0m" >> /etc/issue
echo "| [0;37mADMIN: https://$myLOCALIP:64294[0m" >> /etc/issue
echo "|" >> /etc/issue
echo "\`----" >> /etc/issue
echo >> /etc/issue
tee /data/ews/conf/ews.ip << EOF
[MAIN]
ip = $myEXTIP
EOF
tee /opt/tpot/etc/compose/elk_environment << EOF
HONEY_UUID=$myUUID
MY_EXTIP=$myEXTIP
MY_INTIP=$myLOCALIP
MY_HOSTNAME=$HOSTNAME
EOF
chown tpot:tpot /data/ews/conf/ews.ip
chmod 770 /data/ews/conf/ews.ip
