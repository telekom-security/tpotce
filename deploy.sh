#!/usr/bin/env bash
# Deploy a T-Pot SENSOR from this HIVE. This is `tpot sensors add` now (pre-checks,
# certificate, registry, access taken back if it fails); without the Python packages
# of tpot (i.e. no internet to set them up) the steps below run as before.
# the look of the T-Pot scripts (installer/lib/ui.sh), plain text if it is missing
# shellcheck source=installer/lib/ui.sh
if ! source "$HOME/tpotce/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback
    fuUI_INIT () { return 0; }
    fuUI_BANNER () { local myLINE; echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE}"; done; echo; }
    fuUI_INFO () { echo "### $*"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuUI_CONFIRM () {
      local myANSWER=""
      while [ "${myANSWER}" != "y" ] && [ "${myANSWER}" != "n" ]; do
        read -rp "### $1 (y/n) " myANSWER || return 1
      done
      [ "${myANSWER}" = "y" ]
    }
    fuUI_INPUT () {
      local myVALUE=""
      if [ "$2" = "password" ];
        then read -rsp "### $1 " myVALUE; echo >&2
        else read -rp "### $1 " myVALUE
      fi
      echo "${myVALUE}"
    }
    fuUI_RESULT () {
      case "$1" in
        ok) echo "### [OK] - $2" ;;
        fail) echo "### [FAILED] - $2" ;;
        warn) echo "### [WARNING] - $2" ;;
        next) echo "### [NEXT] - $2" ;;
        *) echo "### $2" ;;
      esac
    }
    fuUI_SUMMARY () {
      local myTITLE="$1" myITEM myKIND myTEXT myRC=0
      shift
      echo
      echo "### ${myTITLE}"
      for myITEM in "$@"; do
        myKIND="${myITEM%%:*}" myTEXT="${myITEM#*:}"
        case "${myKIND}" in ok|fail|warn|next|info) ;; *) myKIND="info" myTEXT="${myITEM}" ;; esac
        [ "${myKIND}" != "fail" ] || myRC=1
        fuUI_RESULT "${myKIND}" "${myTEXT}"
      done
      echo
      return "${myRC}"
    }
# <<< plain fallback
fi
fuUI_INIT
# a person at a terminal sees the T-Pot logo and what comes, then tpot asks; not for the help
myBANNER=""
case " $* " in
  *" -h "*|*" --help "*) ;;
  *) if [ -t 0 ] && [ -t 1 ];
       then
         # shellcheck disable=SC2034 # fuUI_BANNER of installer/lib/ui.sh reads it
         myUI_LOGO=1
         fuUI_BANNER "Sensor deploy" "Joins a T-Pot SENSOR to this HIVE, it sends its logs here."
         myBANNER="y"
     fi ;;
esac
myTPOT="$HOME/tpotce/tpot"
if [ -x "${myTPOT}" ] && "${myTPOT}" setup > /dev/null 2>&1;
  then
    exec "${myTPOT}" sensors add "$@"
fi
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

[ -n "${myBANNER}" ] || fuUI_BANNER "Sensor deploy" "This script will prepare a T-Pot SENSOR installation to transmit logs into this HIVE."

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
myRC=$?

if [ "${myRC}" == 0 ];
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

# Done: what happened and what comes next
if [ "${myRC}" == 0 ];
  then
    fuUI_SUMMARY "The SENSOR is deployed" "ok:${mySENSOR_IP} sends its logs to ${myTPOT_HIVE_IP} as ${myLS_WEB_USER}" \
      "info:The SENSOR reboots, its data shows up on this HIVE once it is back" \
      "next:Next time use tpot sensors add, it checks before and takes the access back if a step fails"
  else
    fuUI_SUMMARY "The SENSOR is not deployed" "fail:The deployment playbook failed, this HIVE is unchanged" \
      "next:Review ${HOME}/tpotce/data/deploy_sensor.log, then run deploy.sh again"
    exit 1
fi
