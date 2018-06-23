#!/bin/bash
# Let's add the first local ip to the /etc/issue and external ip to ews.ip file
# If the external IP cannot be detected, the internal IP will be inherited.
source /etc/environment
myLOCALIP=$(hostname -I | awk '{ print $1 }')
myEXTIP=$(/opt/tpot/bin/myip.sh)
if [ "$myEXTIP" = "" ];
  then
    myEXTIP=$myLOCALIP
fi
mySSHUSER=$(cat /etc/passwd | grep 1000 | cut -d ':' -f1)
sed -i "s#IP:.*#IP: $myLOCALIP ($myEXTIP)[0m#" /etc/issue
sed -i "s#SSH:.*#SSH: ssh -l tsec -p 64295 $myLOCALIP[0m#" /etc/issue
sed -i "s#WEB:.*#WEB: https://$myLOCALIP:64297[0m#" /etc/issue
sed -i "s#ADMIN:.*#ADMIN: https://$myLOCALIP:64294[0m#" /etc/issue
tee /data/ews/conf/ews.ip << EOF
[MAIN]
ip = $myEXTIP
EOF
tee /opt/tpot/etc/compose/elk_environment << EOF
MY_EXTIP=$myEXTIP
MY_INTIP=$myLOCALIP
MY_HOSTNAME=$HOSTNAME
EOF
chown tpot:tpot /data/ews/conf/ews.ip
chmod 760 /data/ews/conf/ews.ip
