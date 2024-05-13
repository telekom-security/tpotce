#!/usr/bin/env bash

myINSTALL_NOTIFICATION="### Now installing required packages ..."
myUSER=$(whoami)
myTPOT_CONF_FILE="/home/${myUSER}/tpotce/.env"
myPACKAGES_DEBIAN="ansible apache2-utils cracklib-runtime wget"
myPACKAGES_FEDORA="ansible cracklib httpd-tools wget"
myPACKAGES_ROCKY="ansible-core ansible-collection-redhat-rhel_mgmt epel-release cracklib httpd-tools wget"
myPACKAGES_OPENSUSE="ansible apache2-utils cracklib wget"


myINSTALLER=$(cat << "EOF"
 _____     ____       _      ___           _        _ _
|_   _|   |  _ \ ___ | |_   |_ _|_ __  ___| |_ __ _| | | ___ _ __
  | |_____| |_) / _ \| __|   | || '_ \/ __| __/ _` | | |/ _ \ '__|
  | |_____|  __/ (_) | |_    | || | | \__ \ || (_| | | |  __/ |
  |_|     |_|   \___/ \__|  |___|_| |_|___/\__\__,_|_|_|\___|_|
EOF
)

# Check if running with root privileges
if [ ${EUID} -eq 0 ];
  then
    echo "This script should not be run as root. Please run it as a regular user."
    echo
    exit 1
fi

# Check if running on a supported distribution
mySUPPORTED_DISTRIBUTIONS=("AlmaLinux" "Debian GNU/Linux" "Fedora Linux" "openSUSE Tumbleweed" "Raspbian GNU/Linux" "Rocky Linux" "Ubuntu")
myCURRENT_DISTRIBUTION=$(awk -F= '/^NAME/{print $2}' /etc/os-release | tr -d '"')

if [[ ! " ${mySUPPORTED_DISTRIBUTIONS[@]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    echo "### Only the following distributions are supported: AlmaLinux, Fedora, Debian, openSUSE Tumbleweed, Rocky Linux and Ubuntu."
    echo "### Please follow the T-Pot documentation on how to run T-Pot on macOS, Windows and other currently unsupported platforms."
    echo
    exit 1
fi

# Begin of Installer
echo "$myINSTALLER"
echo
echo
echo "### This script will now install T-Pot and all of its dependencies."
while [ "${myQST}" != "y" ] && [ "${myQST}" != "n" ];
  do
    echo
    read -p "### Install? (y/n) " myQST
    echo
  done
if [ "${myQST}" = "n" ];
  then
    echo
    echo "### Aborting!"
    echo
    exit 0
fi

# Install packages based on the distribution
case ${myCURRENT_DISTRIBUTION} in
  "Fedora Linux")
    echo
    echo ${myINSTALL_NOTIFICATION}
    echo
    sudo dnf -y --refresh install ${myPACKAGES_FEDORA}
    ;;
  "Debian GNU/Linux"|"Raspbian GNU/Linux"|"Ubuntu")
    echo
    echo ${myINSTALL_NOTIFICATION}
    echo
    if ! command -v sudo >/dev/null;
      then
        echo "### ‘sudo‘ is not installed. To continue you need to provide the ‘root‘ password"
        echo "### or press CTRL-C to manually install ‘sudo‘ and add your user to the sudoers."
        echo
        su -c "apt -y update && \
               NEEDRESTART_SUSPEND=1 apt -y install sudo ${myPACKAGES_DEBIAN} && \
               /usr/sbin/usermod -aG sudo ${myUSER} && \
               echo '${myUSER} ALL=(ALL:ALL) ALL' | tee /etc/sudoers.d/${myUSER} >/dev/null && \
               chmod 440 /etc/sudoers.d/${myUSER}"
        echo "### We need sudo for Ansible, please enter the sudo password ..."
        sudo echo "### ... sudo for Ansible acquired."
        echo
      else
        sudo apt update
        sudo NEEDRESTART_SUSPEND=1 apt install -y ${myPACKAGES_DEBIAN}
    fi
    ;;
  "openSUSE Tumbleweed")
    echo
    echo ${myINSTALL_NOTIFICATION}
    echo
    sudo zypper refresh
    sudo zypper install -y ${myPACKAGES_OPENSUSE}
    echo "export ANSIBLE_PYTHON_INTERPRETER=/bin/python3" | sudo tee /etc/profile.d/ansible.sh >/dev/null
    source /etc/profile.d/ansible.sh
    ;;
  "AlmaLinux"|"Rocky Linux")
    echo
    echo ${myINSTALL_NOTIFICATION}
    echo
    sudo dnf -y --refresh install ${myPACKAGES_ROCKY}
    ansible-galaxy collection install ansible.posix
    ;;
esac
echo

# Define tag for Ansible
myANSIBLE_DISTRIBUTIONS=("Fedora Linux" "Debian GNU/Linux" "Raspbian GNU/Linux" "Rocky Linux")
if [[ "${myANSIBLE_DISTRIBUTIONS[@]}" =~ "${myCURRENT_DISTRIBUTION}" ]];
  then
    myANSIBLE_TAG=$(echo ${myCURRENT_DISTRIBUTION} | cut -d " " -f 1)
  else
    myANSIBLE_TAG=${myCURRENT_DISTRIBUTION}
fi

# Download tpot.yml if not found locally
if [ ! -f installer/install/tpot.yml ] && [ ! -f tpot.yml ];
  then
    echo "### Now downloading T-Pot Ansible Installation Playbook ... "
    wget -qO tpot.yml https://github.com/telekom-security/tpotce/raw/master/installer/install/tpot.yml
    myANSIBLE_TPOT_PLAYBOOK="tpot.yml"
    echo
  else
    echo "### Using local T-Pot Ansible Installation Playbook ... "
    if [ -f "installer/install/tpot.yml" ];
      then
        myANSIBLE_TPOT_PLAYBOOK="installer/install/tpot.yml"
      else
        myANSIBLE_TPOT_PLAYBOOK="tpot.yml"
    fi
fi

# Check type of sudo access
sudo -n true > /dev/null 2>&1
if [ $? -eq 1 ];
  then
    myANSIBLE_BECOME_OPTION="--ask-become-pass"
    echo "### ‘sudo‘ not acquired, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo "### Ansible will ask for the ‘BECOME password‘ which is typically the password you ’sudo’ with."
    echo
  else
    myANSIBLE_BECOME_OPTION="--become"
    echo "### ‘sudo‘ acquired, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo
fi

# Run Ansible Playbook
echo "### Now running T-Pot Ansible Installation Playbook ..."
echo
rm ${HOME}/install_tpot.log > /dev/null 2>&1
ANSIBLE_LOG_PATH=${HOME}/install_tpot.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local --tags "${myANSIBLE_TAG}" ${myANSIBLE_BECOME_OPTION}

# Something went wrong
if [ ! $? -eq 0 ];
  then
    echo "### Something went wrong with the Playbook, please review the output and / or install_tpot.log for clues."
    echo "### Aborting."
    echo
    exit 1
  else
    echo "### Playbook was successful."
    echo
fi

# Ask for T-Pot Installation Type
echo
echo "### Choose your T-Pot type:"
echo "### (H)ive   - T-Pot Standard / HIVE installation."
echo "###            Includes also everything you need for a distributed setup with sensors."
echo "### (S)ensor - T-Pot Sensor installation."
echo "###            Optimized for a distributed installation, without WebUI, Elasticsearch and Kibana."
echo "### (M)obile - T-Pot Mobile installation."
echo "###            Includes everything to run T-Pot Mobile (available separately)."
while true; do
  read -p "### Install Type? (h/s/m) " myTPOT_TYPE
  case "${myTPOT_TYPE}" in
    h|H)
      echo
      echo "### Installing T-Pot Standard / HIVE."
      myTPOT_TYPE="HIVE"
      cp ${HOME}/tpotce/compose/standard.yml ${HOME}/tpotce/docker-compose.yml
      myINFO=""
      break ;;
    s|S)
      echo
      echo "### Installing T-Pot Sensor."
      myTPOT_TYPE="SENSOR"
      cp ${HOME}/tpotce/compose/sensor.yml ${HOME}/tpotce/docker-compose.yml
      myINFO="### Make sure to deploy SSH keys to this SENSOR and disable SSH password authentication.
### On HIVE run the tpotce/deploy.sh script to join this SENSOR to the HIVE."
      break ;;
    m|M)
      echo
      echo "### Installing T-Pot Mobile."
      myTPOT_TYPE="MOBILE"
      cp ${HOME}/tpotce/compose/mobile.yml ${HOME}/tpotce/docker-compose.yml
      myINFO=""
      break ;;
  esac
done

if [ "${myTPOT_TYPE}" == "HIVE" ];
  # If T-Pot Type is HIVE ask for WebUI username and password
  then
	# Preparing web user for T-Pot
	echo
	echo "### T-Pot User Configuration ..."
	echo
	# Asking for web user name
	myWEB_USER=""
	while [ 1 != 2 ];
	  do
	    myOK=""
	    read -rp "### Enter your web user name: " myWEB_USER
	    myWEB_USER=$(echo $myWEB_USER | tr -cd "[:alnum:]_.-")
	    echo "### Your username is: ${myWEB_USER}"
	    while [[ ! "${myOK}" =~ [YyNn] ]];
	      do
	        read -rp "### Is this correct? (y/n) " myOK
	      done
	    if [[ "${myOK}" =~ [Yy] ]] && [ "$myWEB_USER" != "" ];
	      then
	        break
	      else
	        echo
	    fi
	  done

	# Asking for web user password
	myWEB_PW="pass1"
	myWEB_PW2="pass2"
	mySECURE=0
	myOK=""
	while [ "${myWEB_PW}" != "${myWEB_PW2}"  ] && [ "${mySECURE}" == "0" ]
	  do
	    echo
	    while [ "${myWEB_PW}" == "pass1"  ] || [ "${myWEB_PW}" == "" ]
	      do
	        read -rsp "### Enter password for your web user: " myWEB_PW
	        echo
	      done
	    read -rsp "### Repeat password you your web user: " myWEB_PW2
	    echo
	    if [ "${myWEB_PW}" != "${myWEB_PW2}" ];
	      then
	        echo "### Passwords do not match."
	        myWEB_PW="pass1"
	        myWEB_PW2="pass2"
	    fi
	    mySECURE=$(printf "%s" "$myWEB_PW" | /usr/sbin/cracklib-check | grep -c "OK")
	    if [ "$mySECURE" == "0" ] && [ "$myWEB_PW" == "$myWEB_PW2" ];
	      then
	        while [[ ! "${myOK}" =~ [YyNn] ]];
	          do
	            read -rp "### Keep insecure password? (y/n) " myOK
	          done
	        if [[ "${myOK}" =~ [Nn] ]] || [ "$myWEB_PW" == "" ];
	          then
	            myWEB_PW="pass1"
	            myWEB_PW2="pass2"
	            mySECURE=0
	            myOK=""
	        fi
	    fi
	done

	# Write username and password to T-Pot config file
	echo "### Creating base64 encoded htpasswd username and password for T-Pot config file: ${myTPOT_CONF_FILE}"
	myWEB_USER_ENC=$(htpasswd -b -n "${myWEB_USER}" "${myWEB_PW}")
    myWEB_USER_ENC_B64=$(echo -n "${myWEB_USER_ENC}" | base64 -w0)
    
	echo
	sed -i "s|^WEB_USER=.*|WEB_USER=${myWEB_USER_ENC_B64}|" ${myTPOT_CONF_FILE}
fi

# Pull docker images
echo "### Now pulling images ..."
sudo docker compose -f /home/${myUSER}/tpotce/docker-compose.yml pull
echo

# Show running services
echo "### Please review for possible honeypot port conflicts."
echo "### While SSH is taken care of, other services such as"
echo "### SMTP, HTTP, etc. might prevent T-Pot from starting."
echo
sudo grc netstat -tulpen
echo

# Done
echo "### Done. Please reboot and re-connect via SSH on tcp/64295."
echo "${myINFO}"
echo
