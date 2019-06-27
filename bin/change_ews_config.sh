#!/bin/bash

echo """

##############################
# T-POT DTAG Data Submission #
# Contact:                   #
# cert@telekom.de            # 
##############################
"""

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    sudo ./$0
    exit
fi

printf "[*] Enter your API UserID: "
read apiUser
printf "[*] Enter your API Token: "
read apiToken
printf "[*] If you have multiple T-Pots running, give them each a unique NUMBER, e.g. '2' for your second T-Pot installation. Enter unique number for THIS T-Pot: "
read indexNumber
if ! [[ "$indexNumber" =~ ^[0-9]+$ ]]
    then
        echo "Sorry integers only. You have to start over..."
        exit 1
fi
apiURL="https://community.sicherheitstacho.eu/ews-0.1/alert/postSimpleMessage"
printf "[*] Currently, your honeypot is configured to transmit data the default backend at 'https://community.sicherheitstacho.eu/ews-0.1/alert/postSimpleMessage'. Do you want to change this API endpoint? Only do this if you run your own PEBA backend instance? (N/y): "
read replyAPI
if [[ $replyAPI =~ ^[Yy]$ ]]
then    
    printf "[*] Enter your API endpoint URL and make sure it contains the full path, e.g. 'https://myDomain.local:9922/ews-0.1/alert/postSimpleMessage': "
    read apiURL
fi



echo ""
echo "[*] Recap! You defined: "
echo "############################"
echo "API User: " $apiUser
echo "API Token: " $apiToken
echo "API URL: " $apiURL
echo "Unique numeric ID for your T-Pot Installation: "  $indexNumber
echo "Specific honeypot-IDs will look like : <honeypotType>-"$apiUser"-"$indexNumber
echo "############################"
echo ""
printf  "[*] Is the above correct (y/N)? "
read reply
if [[ ! $reply =~ ^[Yy]$ ]]
then	
	echo "OK, then run this again..."
    exit 1
fi
echo ""
echo "[+] Creating config file with API UserID '$apiUser' and API Token '$apiToken'."
echo "[+] Fetching config file from github. Outgoing https requests must be enabled!"
wget -q https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/docker/ews/dist/ews.cfg -O ews.cfg.dist 
if [[ -f "ews.cfg.dist" ]]; then
	echo "[+] Successfully downloaded ews.cfg from github."
else 
	echo "[+] Could not download ews.cfg from github."
	exit 1
fi 
echo "[+] Patching ews.cfg API Credentials."
sed 's/community-01-user/'$apiUser'/' ews.cfg.dist > ews.cfg
sed -i 's/foth{a5maiCee8fineu7/'$apiToken'/' ews.cfg
echo "[+] Patching ews.cfg API Url."
apiURL=${apiURL////\\/};
sed -i 's/https:\/\/community.sicherheitstacho.eu\/ews-0.1\/alert\/postSimpleMessage/'$apiURL'/' ews.cfg
echo "[+] Patching ews.cfg honeypot IDs."
sed -i 's/community-01/'$apiUser'-'$indexNumber'/' ews.cfg

rm ews.cfg.dist

echo "[+] Changing tpot.yml to include new ews.cfg."

cp ews.cfg /data/ews/conf/ews.cfg
cp /opt/tpot/etc/tpot.yml /opt/tpot/etc/tpot.yml.bak
sed -i '/- \/data\/ews\/conf\/ews.ip:\/opt\/ewsposter\/ews.ip/a\ \ \   - \/data\/ews\/conf\/ews.cfg:\/opt\/ewsposter\/ews.cfg' /opt/tpot/etc/tpot.yml

echo "[+] Restarting T-Pot."
systemctl restart tpot
echo "[+] Done."
