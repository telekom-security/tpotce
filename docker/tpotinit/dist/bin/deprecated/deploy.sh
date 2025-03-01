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

function fuDEPLOY_SENSOR () {
echo
echo "###############################"
echo "# Deploying to T-Pot Hive ... #"
echo "###############################"
echo
sshpass -e ssh -4 -t -T -l "$MY_TPOT_USERNAME" -p 64295 "$MY_HIVE_IP" << EOF
echo "$SSHPASS" | sudo -S bash -c 'useradd -m -s /sbin/nologin -G tpotlogs "$MY_HIVE_USERNAME";
mkdir -p /home/"$MY_HIVE_USERNAME"/.ssh;
echo "$MY_SENSOR_PUBLICKEY" >> /home/"$MY_HIVE_USERNAME"/.ssh/authorized_keys;
chmod 600 /home/"$MY_HIVE_USERNAME"/.ssh/authorized_keys;
chmod 755 /home/"$MY_HIVE_USERNAME"/.ssh;
chown "$MY_HIVE_USERNAME":"$MY_HIVE_USERNAME" -R /home/"$MY_HIVE_USERNAME"/.ssh'
EOF

echo
echo "###########################"
echo "# Done. Please reboot ... #"
echo "###########################"
echo

exit 0
}

# Check Hive availability 
function fuCHECK_HIVE () {
echo
echo "############################################"
echo "# Checking for T-Pot Hive availability ... #"
echo "############################################"
echo
sshpass -e ssh -4 -t -l "$MY_TPOT_USERNAME" -p 64295 -f -N -L64305:127.0.0.1:64305 "$MY_HIVE_IP" -o "StrictHostKeyChecking=no"
if [ $? -eq 0 ];
  then
    echo
    echo "#########################"
    echo "# T-Pot Hive available! #"
    echo "#########################"
    echo
    myHIVE_OK=$(curl -s http://127.0.0.1:64305)
    if [ "$myHIVE_OK" == "ok" ];
      then
	echo
        echo "##############################"
        echo "# T-Pot Hive tunnel test OK! #"
        echo "##############################"
        echo
        kill -9 $(pidof ssh)
      else
        echo
	echo "######################################################"
        echo "# T-Pot Hive tunnel test FAILED!                     #"
	echo "# Tunneled port tcp/64305 unreachable on T-Pot Hive. #"
	echo "# Aborting.                                          #"
        echo "######################################################"
        echo
        kill -9 $(pidof ssh)
	rm $MY_SENSOR_PUBLICKEYFILE
	rm $MY_SENSOR_PRIVATEKEYFILE
	rm $MY_LS_ENVCONFIGFILE
	exit 1
    fi;
  else
    echo
    echo "#################################################################"
    echo "# Something went wrong, most likely T-Pot Hive was unreachable! #"
    echo "# Aborting.                                                     #"
    echo "#################################################################"
    echo
    rm $MY_SENSOR_PUBLICKEYFILE
    rm $MY_SENSOR_PRIVATEKEYFILE
    rm $MY_LS_ENVCONFIGFILE
    exit 1
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
MY_TPOT_TYPE="SENSOR"
MY_LS_ENVCONFIGFILE="/data/elk/logstash/ls_environment"

MY_SENSOR_PUBLICKEYFILE="/data/elk/logstash/$MY_HIVE_USERNAME.pub"
MY_SENSOR_PRIVATEKEYFILE="/data/elk/logstash/$MY_HIVE_USERNAME"
if ! [ -s "$MY_SENSOR_PRIVATEKEYFILE" ] && ! [ -s "$MY_SENSOR_PUBLICKEYFILE" ];
  then
    echo
    echo "##############################"
    echo "# Generating ssh keyfile ... #"
    echo "##############################"
    echo
    mkdir -p /data/elk/logstash
    ssh-keygen -f "$MY_SENSOR_PRIVATEKEYFILE" -N "" -C "$MY_HIVE_USERNAME"
    MY_SENSOR_PUBLICKEY="$(cat "$MY_SENSOR_PUBLICKEYFILE")"
  else
    echo
    echo "#############################################"
    echo "# There is already a ssh keyfile. Aborting. #"
    echo "#############################################"
    echo
    exit 1
fi
echo
echo "###########################################################"
echo "# Writing config to /data/elk/logstash/ls_environment.    #"
echo "# If you make changes to this file, you need to reboot or #"
echo "# run /opt/tpot/bin/updateip.sh.                          #"
echo "###########################################################"
echo
tee $MY_LS_ENVCONFIGFILE << EOF
MY_TPOT_TYPE=$MY_TPOT_TYPE
MY_SENSOR_PRIVATEKEYFILE=$MY_SENSOR_PRIVATEKEYFILE
MY_HIVE_USERNAME=$MY_HIVE_USERNAME
MY_HIVE_IP=$MY_HIVE_IP
EOF
}

# Deploy Pot to Hive
fuGOT_ROOT
echo
echo "#################################"
echo "# Ship T-Pot Logs to T-Pot Hive #"
echo "#################################"
echo
echo "If you already have a T-Pot Hive installation running and"
echo "this T-Pot installation is running the type \"Pot\" the"
echo "script will automagically setup this T-Pot to ship and"
echo "prepare the Hive to receive logs from this T-Pot."
echo
echo
echo "###################################"
echo "# Deploy T-Pot Logs to T-Pot Hive #"
echo "###################################"
echo 
echo "[c] - Continue deplyoment"
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
	  fuDEPLOY_SENSOR
          break
          ;;
        [q,Q])
          echo "Aborted."
          exit 0
          ;;
      esac
done
