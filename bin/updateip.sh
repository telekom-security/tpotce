#!/bin/bash
# Let's add the first local ip to the /etc/issue and external ip to ews.ip file
# If the external IP cannot be detected, the internal IP will be inherited.
source /etc/environment
myCHECKIFSENSOR=$(head -n 1 /opt/tpot/etc/tpot.yml | grep "Sensor" | wc -l)
myUUID=$(lsblk -o MOUNTPOINT,UUID | grep -e "^/ " | awk '{ print $2 }')
myLOCALIP=$(hostname -I | awk '{ print $1 }')
myEXTIP=$(/opt/tpot/bin/myip.sh)
if [ "$myEXTIP" = "" ];
  then
    myEXTIP=$myLOCALIP
    myEXTIP_LAT="49.865835022498125"
    myEXTIP_LONG="8.62606472775735"
  else
    myEXTIP_LOC=$(curl -s ipinfo.io/$myEXTIP/loc)
    myEXTIP_LAT=$(echo "$myEXTIP_LOC" | cut -f1 -d",")
    myEXTIP_LONG=$(echo "$myEXTIP_LOC" | cut -f2 -d",")
fi

# Load Blackhole routes if enabled 
myBLACKHOLE_FILE1="/etc/blackhole/mass_scanner.txt"
myBLACKHOLE_FILE2="/etc/blackhole/mass_scanner_cidr.txt"
if [ -f "$myBLACKHOLE_FILE1" ] || [ -f "$myBLACKHOLE_FILE2" ];
  then
    /opt/tpot/bin/blackhole.sh add
fi

myBLACKHOLE_STATUS=$(ip r | grep "blackhole" -c)
if [ "$myBLACKHOLE_STATUS" -gt "500" ];
  then
    myBLACKHOLE_STATUS="| [1;34mBLACKHOLE: [ [0;37mENABLED[1;34m ][0m"
  else
    myBLACKHOLE_STATUS="| [1;34mBLACKHOLE: [ [1;30mDISABLED[1;34m ][0m"
fi

mySSHUSER=$(cat /etc/passwd | grep 1000 | cut -d ':' -f1)

# Export
export myUUID
export myLOCALIP
export myEXTIP
export myEXTIP_LAT
export myEXTIP_LONG
export myBLACKHOLE_STATUS
export mySSHUSER

# Build issue
echo "[H[2J" > /etc/issue
toilet -f ivrit -F metal --filter border:metal "T-Pot   22.04" | sed 's/\\/\\\\/g' >> /etc/issue
echo >> /etc/issue
echo ",---- [ [1;34m\n[0m ] [ [0;34m\d[0m ] [ [1;30m\t[0m ]" >> /etc/issue
echo "|" >> /etc/issue
echo "| [1;34mIP: $myLOCALIP ($myEXTIP)[0m" >> /etc/issue
echo "| [0;34mSSH: ssh -l tsec -p 64295 $myLOCALIP[0m" >> /etc/issue 
if [ "$myCHECKIFSENSOR" == "0" ];
  then
    echo "| [1;30mWEB: https://$myLOCALIP:64297[0m" >> /etc/issue
fi
echo "| [0;37mADMIN: https://$myLOCALIP:64294[0m" >> /etc/issue
echo "$myBLACKHOLE_STATUS" >> /etc/issue
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
MY_EXTIP_LAT=$myEXTIP_LAT
MY_EXTIP_LONG=$myEXTIP_LONG
MY_INTIP=$myLOCALIP
MY_HOSTNAME=$HOSTNAME
EOF

if [ -s "/data/elk/logstash/ls_environment" ];
  then
    source /data/elk/logstash/ls_environment
    tee -a /opt/tpot/etc/compose/elk_environment << EOF
MY_TPOT_TYPE=$MY_TPOT_TYPE
MY_SENSOR_PRIVATEKEYFILE=$MY_SENSOR_PRIVATEKEYFILE
MY_HIVE_USERNAME=$MY_HIVE_USERNAME
MY_HIVE_IP=$MY_HIVE_IP
EOF
fi

chown tpot:tpot /data/ews/conf/ews.ip
chmod 770 /data/ews/conf/ews.ip
