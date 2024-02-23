#!/usr/bin/env bash

myANSIBLE_PORT=64295
myANSIBLE_TPOT_PLAYBOOK="installer/install/deploy.yml"
myADJECTIVE=$(shuf -n1 installer/install/a.txt)
myNOUN=$(shuf -n1 installer/install/n.txt)
myENV_FILE="$HOME/tpotce/.env"

myDEPLOY=$(cat << "EOF"

 ____   [ T-Pot ]                  ____             _
/ ___|  ___ _ __  ___  ___  _ __  |  _ \  ___ _ __ | | ___  _   _
\___ \ / _ \  _ \/ __|/ _ \|  __| | | | |/ _ \  _ \| |/ _ \| | | |
 ___) |  __/ | | \__ \ (_) | |    | |_| |  __/ |_) | | (_) | |_| |
|____/ \___|_| |_|___/\___/|_|    |____/ \___| .__/|_|\___/ \__, |
                                             |_|            |___/

EOF
)

# Check if the script is running in a HIVE installation
if ! grep -q 'TPOT_TYPE=HIVE' "$HOME/tpotce/.env";
  then
    echo "# This script is only supported on HIVE installations."
    exit 1
fi

echo "${myDEPLOY}"
echo
echo "This script will prepare a T-Pot SENSOR installation to transmit logs into this HIVE."
echo

# Ask if a T-Pot SENSOR was installed
read -p "# Was a T-Pot SENSOR installed? (y/n): " mySENSOR_INSTALLED
if [[ ${mySENSOR_INSTALLED} != "y" ]]; 
    then
      echo "# A T-Pot SENSOR must be installed to continue."
      exit 1
fi

# Check if ssh key has been deployed
read -p "# Has the SSH key been deployed to the SENSOR? (y/n): " mySSHKEY_DEPLOYED
if [[ ${mySSHKEY_DEPLOYED} != "y" ]]; 
    then
      echo "# Generate a SSH key using 'ssh-keygen' and deploy it to the SENSOR with 'ssh-copy-id user@sensor-ip'."
      exit 1
fi

# Validate IP/domain name loop
while true; do
  read -p "# Enter the IP/domain name of the SENSOR: " mySENSOR_IP
  if [[ ${mySENSOR_IP} =~ ^([a-zA-Z0-9]+(\.[a-zA-Z0-9]+)*\.[a-zA-Z]{2,})|(([0-9]{1,3}\.){3}[0-9]{1,3})$ ]];
    then
      break
    else
      echo "# Invalid IP/domain. Please enter a valid IP or domain name."
  fi
done

# Validate IP/domain name of HIVE
while true; do
  read -p "# Enter the IP/domain name of this HIVE: " myTPOT_HIVE_IP
  if [[ ${myTPOT_HIVE_IP} =~ ^([a-zA-Z0-9]+(\.[a-zA-Z0-9]+)*\.[a-zA-Z]{2,})|(([0-9]{1,3}\.){3}[0-9]{1,3})$ ]]; 
    then
      break
    else
      echo "# Invalid IP/domain. Please enter a valid IP or domain name."
  fi
done

# Create a random SENSOR user name that is easily readable
myLS_WEB_USER="sensor-${myADJECTIVE}-${myNOUN}"

# Create a random password
myLS_WEB_PW=$(tr -dc 'a-zA-Z0-9' < /dev/urandom | fold -w 32 | head -n 1)

# Create myLS_WEB_USER_ENC
myLS_WEB_USER_ENC=$(htpasswd -b -n "${myLS_WEB_USER}" "${myLS_WEB_PW}")
myLS_WEB_USER_ENC_B64=$(echo -n "${myLS_WEB_USER_ENC}" | base64 -w0)

# Create myTPOT_HIVE_USER, since this is for Logstash on the SENSOR, it needs to directly base64 encoded
myTPOT_HIVE_USER=$(echo -n "${myLS_WEB_USER}:${myLS_WEB_PW}" | base64 -w0)

# Print credentials
echo "# The following SENSOR credentials have been created:"
echo "# New SENSOR username: ${myLS_WEB_USER}"
echo "# New SENSOR passowrd: ${myLS_WEB_PW}"
echo "# New htpasswd encoded credentials: ${myLS_WEB_USER_ENC}"
echo "# New htpasswd credentials base64 encoded: ${myLS_WEB_USER_ENC_B64}"
echo "# New SENSOR credentials base64 encoded: ${myTPOT_HIVE_USER}"

# Read LS_WEB_USER from file
myENV_LS_WEB_USER=$(grep "^LS_WEB_USER=" "${myENV_FILE}" | sed 's/^LS_WEB_USER=//g' | tr -d "\"'")

# Add the new SENSOR and show a complete list of all the SENSORs
myENV_LS_WEB_USER="${myENV_LS_WEB_USER} ${myLS_WEB_USER_ENC_B64}"

# Update the T-Pot .env config and lswebpasswd (avoid the need to restart T-Pot) on the host
echo "# Updating SENSOR users on this HIVE and in the T-Pot .env config:"
sed -i "/^LS_WEB_USER=/c\LS_WEB_USER=${myENV_LS_WEB_USER}" "${myENV_FILE}"
: > "${HOME}"/tpotce/data/nginx/conf/lswebpasswd
for i in $myENV_LS_WEB_USER;
  do
    if [[ -n $i ]]; 
      then
        # Need to control newlines as they kept coming up for some reason
        echo -n "$i" | base64 -d -w0
        echo
        echo -n "$i" | base64 -d -w0 | tr -d '\n' >> ${HOME}/tpotce/data/nginx/conf/lswebpasswd
        echo >> ${HOME}/tpotce/data/nginx/conf/lswebpasswd
      fi
done

# Need to export for Ansible
export myTPOT_HIVE_USER
export myTPOT_HIVE_IP

ANSIBLE_LOG_PATH=${HOME}/tpotce/data/deploy_sensor.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i ${mySENSOR_IP}, --check -c ssh -e "ansible_port=${myANSIBLE_PORT}"

unset myTPOT_HIVE_USER
unset myTPOT_HIVE_IP
