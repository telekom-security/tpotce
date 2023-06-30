#!/bin/bash

myINSTALL_NOTIFICATION="### Now installing required packages ..."
myUSER=$(whoami)
myTPOT_CONF_FILE="/home/${myUSER}/tpotce/.env"
myPACKAGES="ansible wget"
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
mySUPPORTED_DISTRIBUTIONS=("Fedora Linux" "Debian GNU/Linux" "openSUSE Tumbleweed" "Ubuntu")
myCURRENT_DISTRIBUTION=$(awk -F= '/^NAME/{print $2}' /etc/os-release | tr -d '"')

if [[ ! " ${mySUPPORTED_DISTRIBUTIONS[@]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    echo "### Only the following distributions are supported: Fedora, Debian, openSUSE Tumbleweed and Ubuntu."
    echo
    exit 1
fi

# Begin of Installer
echo "$myINSTALLER"
echo
echo
echo "### This script will now install T-Pot and all of its dependencies."
while [ "${myQST}" != "y" ] && [ "{$myQST}" != "n" ];
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
    sudo dnf update -y
    sudo dnf install -y cracklib ${myPACKAGES}
    ;;
  "Debian GNU/Linux"|"Ubuntu")
    echo
    echo ${myINSTALL_NOTIFICATION}
    echo
    if ! command -v sudo >/dev/null;
      then
        echo "### ‘sudo‘ is not installed. To continue you need to provide the ‘root‘ password"
        echo "### or press CTRL-C to manually install ‘sudo‘ and add your user to the sudoers."
        echo
        su -c "apt -y update && \
               apt -y install sudo cracklib-runtime ${myPACKAGES} && \
               /usr/sbin/usermod -aG sudo ${myUSER} && \
               echo '${myUSER} ALL=(ALL:ALL) ALL' | tee /etc/sudoers.d/${myUSER} >/dev/null && \
               chmod 440 /etc/sudoers.d/${myUSER}"
      else
        sudo apt update
        sudo apt install -y cracklib-runtime ${myPACKAGES}
    fi
    ;;
  "openSUSE Tumbleweed")
    echo
    echo ${myINSTALL_NOTIFICATION}
    echo
    sudo zypper refresh
    sudo zypper install -y cracklib ${myPACKAGES}
    echo "export ANSIBLE_PYTHON_INTERPRETER=/bin/python3" | sudo tee /etc/profile.d/ansible.sh >/dev/null
    source /etc/profile.d/ansible.sh
    ;;
esac
echo

# Check type of sudo access
sudo -n true > /dev/null 2>&1
if [ $? -eq 1 ]; 
  then
    myANSIBLE_BECOME_OPTION="--ask-become-pass"
    echo "### ‘sudo‘ is setup with password, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo
  else
    myANSIBLE_BECOME_OPTION="--become"
    echo "### ‘sudo‘ is usable without password, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo
fi

# Download tpot.yml if not found locally
if [ ! -f installer/install/tpot.yml ];
  then
    echo "### Now downloading T-Pot Ansible Installation Playbook ... "
    wget -qO tpot.yml https://github.com/telekom-security/tpotce/raw/dev/installer/install/tpot.yml
    myANSIBLE_TPOT_PLAYBOOK="tpot.yml"
    echo
  else
    echo "### Using local T-Pot Ansible Installation Playbook ... "
    myANSIBLE_TPOT_PLAYBOOK="installer/install/tpot.yml"
fi

# Run Ansible Playbook
echo "### Now running T-Pot Ansible Installation Playbook ..."
echo "### Ansible will ask for the ‘BECOME password‘ which is typically the password you ’sudo’ with."
echo
ANSIBLE_LOG_PATH=${PWD}/install_tpot.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local ${myANSIBLE_BECOME_OPTION}

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
echo "### Writing username and password to T-Pot config file: ${myTPOT_CONF_FILE}"
echo "### You can empty the password <WEB_PW=''> after the first start of T-Pot."
echo
sed -i "/^WEB_USER=/s/.*/WEB_USER='${myWEB_USER}'/" ${myTPOT_CONF_FILE}
sed -i "/^WEB_PW=/s/.*/WEB_PW='${myWEB_PW}'/" ${myTPOT_CONF_FILE}

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
echo "Done. Please reboot and re-connect via SSH on tcp/64295."
echo
