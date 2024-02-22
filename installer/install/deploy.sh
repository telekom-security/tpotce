#!/usr/bin/env bash

myANSIBLE_PORT=64295
myANSIBLE_TPOT_PLAYBOOK="deploy.yml"
myENV_FILE="$HOME/tpotce/.env"


# Check if the script is running in a HIVE installation
if ! grep -q 'TPOT_TYPE=HIVE' "$HOME/tpotce/.env";
  then
    echo "# This script is only supported on HIVE installations."
    exit 1
fi

# Ask if a T-Pot sensor was installed
read -p "# Was a T-Pot sensor installed? (y/n): " mySENSOR_INSTALLED
if [[ ${mySENSOR_INSTALLED} != "y" ]]; 
    then
      echo "# A T-Pot sensor must be installed to continue."
      exit 1
fi

# Check if ssh key has been deployed
read -p "# Has the SSH key been deployed to the sensor? (y/n): " mySSHKEY_DEPLOYED
if [[ ${mySSHKEY_DEPLOYED} != "y" ]]; 
    then
      echo "# Generate a SSH key using 'ssh-keygen' and deploy it to the sensor with 'ssh-copy-id user@sensor-ip'."
      exit 1
fi

# Validate IP/domain name loop
while true; do
  read -p "# Enter the IP/domain name of the sensor: " mySENSOR_IP
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

# Create a random sensor user name that is easily readable
adjective=$(shuf -n1 a.txt)
noun=$(shuf -n1 n.txt)
myLS_WEB_USER="sensor-${adjective}-${noun}"

# Create a random password
myLS_WEB_PW=$(tr -dc 'a-zA-Z0-9' < /dev/urandom | fold -w 32 | head -n 1)

# Create myLS_WEB_USER_ENC
myLS_WEB_USER_ENC=$(htpasswd -b -n "${myLS_WEB_USER}" "${myLS_WEB_PW}")
myLS_WEB_USER_ENC_B64=$(echo -n "${myLS_WEB_USER_ENC}" | base64 -w0)

# Create myTPOT_HIVE_USER, since this is for Logstash on the sensor, it needs to directly base64 encoded
myTPOT_HIVE_USER=$(echo -n "${myLS_WEB_USER}:${myLS_WEB_PW}" | base64 -w0)

# Print credentials
echo "# The following sensor credentials have been created:"
echo "# New sensor username: ${myLS_WEB_USER}"
echo "# New sensor passowrd: ${myLS_WEB_PW}"
echo "# New htpasswd encoded credentials: ${myLS_WEB_USER_ENC}"
echo "# New htpasswd credentials base64 encoded: ${myLS_WEB_USER_ENC_B64}"
echo "# New sensor credentials base64 encoded: ${myTPOT_HIVE_USER}"

# Read LS_WEB_USER from file
myENV_LS_WEB_USER=$(grep "^LS_WEB_USER=" "${myENV_FILE}" | sed 's/^LS_WEB_USER=//g' | tr -d "\"'")

# Add the new sensor and show a complete list of all the sensors
myENV_LS_WEB_USER="${myENV_LS_WEB_USER} ${myLS_WEB_USER_ENC_B64}"

# Update the .env on the host
sed -i "/^LS_WEB_USER=/c\LS_WEB_USER=${myENV_LS_WEB_USER}" "${myENV_FILE}"

echo "# Here is the complete and updated sensor list on HIVE:"
for i in $myENV_LS_WEB_USER;
  do
    echo -n $i | base64 --decode -w0
    echo -n " :" $i
    echo
done

export myTPOT_HIVE_USER
export myTPOT_HIVE_IP

ANSIBLE_LOG_PATH=$HOME/data/deploy_sensor.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -vvv -i ${mySENSOR_IP}, --check -c ssh -e "ansible_port=${myANSIBLE_PORT}"
