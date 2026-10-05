#!/usr/bin/env bash
# Deploy a T-Pot SENSOR from this HIVE. This is `tpot sensors add` now (pre-checks,
# certificate, registry, access taken back if it fails); without the Python packages
# of tpot (i.e. no internet to set them up) the steps below run as before.
myTPOT="$HOME/tpotce/tpot"
if [ -x "${myTPOT}" ] && "${myTPOT}" setup > /dev/null 2>&1;
  then
    exec "${myTPOT}" sensors add "$@"
fi
# the look of the T-Pot scripts (installer/lib/ui.sh), plain text if it is missing
# shellcheck source=installer/lib/ui.sh
if ! source "$HOME/tpotce/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback
    fuUI_INIT () { return 0; }
    fuUI_BANNER () { echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE}"; done; echo; }
    fuUI_INFO () { echo "### $*"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuUI_CONFIRM () { local myANSWER; read -rp "### $1 (y/n) " myANSWER; [[ "${myANSWER}" =~ ^(y|Y|yes|YES)$ ]]; }
    fuUI_INPUT () { local myVALUE; read -rp "### $1 " myVALUE; echo "${myVALUE}"; }
# <<< plain fallback
fi
fuUI_INIT
fuUI_WARN "tpot is not available, using the previous deployment."
cd "$HOME/tpotce" || exit 1

myANSIBLE_PORT=64295
myANSIBLE_TPOT_PLAYBOOK="installer/install/deploy.yml"
myADJECTIVE=$(shuf -n1 installer/install/a.txt)
myNOUN=$(shuf -n1 installer/install/n.txt)
myENV_FILE="$HOME/tpotce/.env"

# Check if the script is running in a HIVE installation
if ! grep -q 'TPOT_TYPE=HIVE' "$HOME/tpotce/.env";
  then
    fuUI_ERROR "This script is only supported on HIVE installations."
    echo
    exit 1
fi

# Check if running on a supported distribution
mySUPPORTED_DISTRIBUTIONS=("AlmaLinux" "Debian GNU/Linux" "Fedora Linux" "openSUSE Tumbleweed" "Raspbian GNU/Linux" "Rocky Linux" "Ubuntu")
myCURRENT_DISTRIBUTION=$(awk -F= '/^NAME/{print $2}' /etc/os-release | tr -d '"')

if [[ ! " ${mySUPPORTED_DISTRIBUTIONS[@]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    fuUI_ERROR "Only the following distributions are supported: AlmaLinux, Fedora, Debian, openSUSE Tumbleweed, Rocky Linux and Ubuntu."
    echo
    exit 1
fi

fuUI_BANNER "Sensor deploy" "This script will prepare a T-Pot SENSOR installation to transmit logs into this HIVE."

# Ask if a T-Pot SENSOR was installed
if ! fuUI_CONFIRM "Was a T-Pot SENSOR installed?";
    then
      fuUI_ERROR "A T-Pot SENSOR must be installed to continue."
      exit 1
fi

# Ask for the remote user
mySSHUSER=$(fuUI_INPUT "Enter the remote username T-Pot SENSOR was installed with:")
if [[ ${mySSHUSER} == "" ]];
    then
      fuUI_ERROR "You need to enter a user. Aborting."
      exit 1
fi

# Validate IP/domain name loop
while true; do
  mySENSOR_IP=$(fuUI_INPUT "Enter the IP/domain name of the SENSOR:")
  if [[ ${mySENSOR_IP} =~ ^([a-zA-Z0-9]+(\.[a-zA-Z0-9]+)*\.[a-zA-Z]{2,})|(([0-9]{1,3}\.){3}[0-9]{1,3})$ ]];
    then
      break
    else
      fuUI_WARN "Invalid IP/domain. Please enter a valid IP or domain name."
  fi
done

# Check if ssh key has been deployed
if ! fuUI_CONFIRM "Has a SSH key been deployed to the SENSOR?";
    then
      fuUI_ERROR "Generate a SSH key using 'ssh-keygen' and deploy it to the SENSOR."
      fuUI_HINT "ssh-copy-id -p 64295 ${mySSHUSER}@${mySENSOR_IP}"
      exit 1
fi

# Validate IP/domain name of HIVE
while true; do
  myTPOT_HIVE_IP=$(fuUI_INPUT "Enter the IP/domain name of this HIVE:")
  if [[ ${myTPOT_HIVE_IP} =~ ^([a-zA-Z0-9]+(\.[a-zA-Z0-9]+)*\.[a-zA-Z]{2,})|(([0-9]{1,3}\.){3}[0-9]{1,3})$ ]];
    then
      break
    else
      fuUI_WARN "Invalid IP/domain. Please enter a valid IP or domain name."
  fi
done

# Create a random SENSOR user name that is easily readable
myLS_WEB_USER="sensor-${myADJECTIVE}-${myNOUN}"

# Create a random password
myLS_WEB_PW=$(tr -dc 'a-zA-Z0-9' < /dev/urandom | fold -w 32 | head -n 1)

# Create myLS_WEB_USER_ENC
# the password goes through stdin, not argv
myLS_WEB_USER_ENC=$(printf "%s" "${myLS_WEB_PW}" | htpasswd -n -i -B "${myLS_WEB_USER}")
myLS_WEB_USER_ENC_B64=$(echo -n "${myLS_WEB_USER_ENC}" | base64 -w0)

# Create myTPOT_HIVE_USER, since this is for Logstash on the SENSOR, it needs to directly base64 encoded
myTPOT_HIVE_USER=$(echo -n "${myLS_WEB_USER}:${myLS_WEB_PW}" | base64 -w0)

# Print credentials
fuUI_OK "The following SENSOR credentials have been created:"
fuUI_HINT "New SENSOR username: ${myLS_WEB_USER}" \
          "New SENSOR password: ${myLS_WEB_PW}" \
          "New htpasswd encoded credentials: ${myLS_WEB_USER_ENC}" \
          "New htpasswd credentials base64 encoded: ${myLS_WEB_USER_ENC_B64}" \
          "New SENSOR credentials base64 encoded: ${myTPOT_HIVE_USER}"
echo
fuUI_INFO "Ansible will ask for the ‘BECOME password‘ which is typically the password you ’sudo’ with on the SENSOR."
fuUI_INFO "The password will allow Ansible to run a reboot via sudo on the SENSOR."
echo

# Read LS_WEB_USER from file
myENV_LS_WEB_USER=$(grep "^LS_WEB_USER=" "${myENV_FILE}" | sed 's/^LS_WEB_USER=//g' | tr -d "\"'")

# Add the new SENSOR user
if [ "${myENV_LS_WEB_USER}" == "" ];
  then
    myENV_LS_WEB_USER="${myLS_WEB_USER_ENC_B64}"
  else
    myENV_LS_WEB_USER="${myENV_LS_WEB_USER} ${myLS_WEB_USER_ENC_B64}"
fi

# Need to export for Ansible
export myTPOT_HIVE_USER
export myTPOT_HIVE_IP

ANSIBLE_LOG_PATH=${HOME}/tpotce/data/deploy_sensor.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i ${mySENSOR_IP}, -c ssh -u ${mySSHUSER} --ask-become-pass -e "ansible_port=${myANSIBLE_PORT}"

if [ "$?" == 0 ];
  then
	# Update the T-Pot .env config and lswebpasswd (avoid the need to restart T-Pot) on the host
	fuUI_INFO "Updating SENSOR users on this HIVE and in the T-Pot .env config:"
    sed -i "/^LS_WEB_USER=/c\LS_WEB_USER=$myENV_LS_WEB_USER" "${myENV_FILE}"
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
fi

unset myTPOT_HIVE_USER
unset myTPOT_HIVE_IP
