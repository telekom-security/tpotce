#!/bin/bash

# Do we have root?
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

function fuDEPLOY_POT () {
sshpass -e ssh -4 -t -T -l "$MY_TPOT_USERNAME" -p 64295 "$MY_HIVE_IP" << EOF
echo "$SSHPASS" | sudo -S bash -c 'useradd -m -s /sbin/nologin -G tpotlogs "$MY_HIVE_USERNAME";
mkdir -p /home/"$MY_HIVE_USERNAME"/.ssh;
echo "$MY_POT_PUBLICKEY" >> /home/"$MY_HIVE_USERNAME"/.ssh/authorized_keys;
chmod 600 /home/"$MY_HIVE_USERNAME"/.ssh/authorized_keys;
chmod 755 /home/"$MY_HIVE_USERNAME"/.ssh;
chown "$MY_HIVE_USERNAME":"$MY_HIVE_USERNAME" -R /home/"$MY_HIVE_USERNAME"/.ssh'
EOF
exit
}

# Check Hive availability 
function fuCHECK_HIVE () {
sshpass -e ssh -4 -t -l "$MY_TPOT_USERNAME" -p 64295 -f -N -L64305:127.0.0.1:64305 "$MY_HIVE_IP"
if [ $? -eq 0 ];
  then
    echo ssh success
    myHIVE_OK=$(curl -s http://127.0.0.1:64305)
    if [ "$myHIVE_OK" == "ok" ];
      then
        echo ssh tunnel success
        kill -9 $(pidof ssh)
      else
	echo tunneled port 64305 on Hive unreachable
	echo aborting
        kill -9 $(pidof ssh)
    fi;
  else
    echo ssh on Hive unreachable	
fi;
}

function fuGET_DEPLOY_DATA () {
echo
echo "### Please provide data from your T-Pot Hive installation."
echo "### This usually is the one running the 'T-Pot Hive' type."
echo "### You will be needing the OS user (typically 'tsec'), the users' password and the IP / FQDN."
echo "### Do not worry, the password will not be persisted!"
echo

read -p "Username: " MY_TPOT_USERNAME
read -s -p "Password: " SSHPASS
echo
export SSHPASS
read -p "IP / FQDN: " MY_HIVE_IP
MY_HIVE_USERNAME="$(hostname)"
MY_TPOT_TYPE="POT"

echo "$MY_TPOT_USERNAME"
echo "$MY_HIVE_USERNAME"
echo "$SSHPASS"
echo "$MY_HIVE_IP"
echo "$MY_TPOT_TYPE"
MY_POT_PUBLICKEYFILE="/data/elk/logstash/$MY_HIVE_USERNAME.pub"
MY_POT_PRIVATEKEYFILE="/data/elk/logstash/$MY_HIVE_USERNAME"
if ! [ -s "$MY_POT_PRIVATEKEYFILE" ] && ! [ -s "$MY_POT_PUBLICKEYFILE" ];
  then
    echo "we need to gen a keyfile"
    mkdir -p /data/elk/logstash
    ssh-keygen -f "$MY_POT_PRIVATEKEYFILE" -N "" -C "$MY_HIVE_USERNAME"
    MY_POT_PUBLICKEY="$(cat "$MY_POT_PUBLICKEYFILE")"
    echo "$MY_POT_PUBLICKEY"
  else
    echo "there is a keyfile already, exiting"
    exit
fi
}

# Deploy Pot to Hive
fuGOT_ROOT
echo
echo "-----------------------------"
echo "Ship T-Pot Logs to T-Pot Hive"
echo "-----------------------------"
echo "Executing this script will ship all logs to a T-Pot Hive installation."
echo
echo
echo "------------------------------------"
echo "Please provide data from your T-Pot "
echo "------------------------------------"
echo "[c] - Continue deplyoment"
#echo "[0] - Rollback"
echo "[q] - Abort and exit"
echo
while [ 1 != 2 ]
  do
    read -s -n 1 -p "Your choice: " mySELECT
      echo $mySELECT
      case "$mySELECT" in
        [c,C])
          fuGET_DEPLOY_DATA
          fuCHECK_HIVE
	  fuDEPLOY_POT
          break
          ;;
#        [0])
#          fuOPTOUT
#          break
#          ;;
        [q,Q])
          echo "Aborted."
          exit
          ;;
      esac
done
