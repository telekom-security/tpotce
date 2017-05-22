#!/bin/bash
# Let's add the first local ip to the /etc/issue and external ip to ews.ip file
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
