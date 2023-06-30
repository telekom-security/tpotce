#!/bin/bash

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
if [ $EUID -eq 0 ]; 
  then
    echo "This script should not be run as root. Please run it as a regular user."
    exit 1
fi

# Check if running on a supported distribution
mySUPPORTED_DISTRIBUTIONS=("Fedora Linux" "Debian GNU/Linux" "openSUSE Tumbleweed" "Ubuntu")
myCURRENT_DISTRIBUTION=$(awk -F= '/^NAME/{print $2}' /etc/os-release | tr -d '"')

if [[ ! " ${mySUPPORTED_DISTRIBUTIONS[@]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    echo "### Only the following distributions are supported: Fedora, Debian, openSUSE Tumbleweed and Ubuntu."
    exit 1
fi

# Begin of Installer
echo "$myINSTALLER"
echo
echo
echo "### This script will now install T-Pot and all of its dependencies."
while [ "$myQST" != "y" ] && [ "$myQST" != "n" ];
  do
    read -p "### Install? (y/n) " myQST
  done
if [ "$myQST" = "n" ];
  then
    echo
    echo "### Aborting!"
    echo
    exit 0
fi

# Install packages based on the distribution
case $myCURRENT_DISTRIBUTION in
  "Fedora")
    sudo dnf update -y
    sudo dnf install -y ${myPACKAGES}
    ;;
  "Debian"|"Ubuntu")
    if ! command -v sudo >/dev/null; 
      then
	echo "### ‘sudo‘ is not installed. To continue you need to provide the ‘root‘ password ... "
	echo "### ... or press CTRL-C to manually install ‘sudo‘ and add your user to the sudoers."
	su -c "apt -y update && apt -y install sudo ${myPACKAGES}"
        su -c "/usr/sbin/usermod -aG sudo $(whoami)"
      else
        sudo apt update
        sudo apt install -y ${myPACKAGES}
    fi
    ;;
  "openSUSE Tumbleweed")
    sudo zypper refresh
    sudo zypper install -y ${myPACKAGES}
    echo "export ANSIBLE_PYTHON_INTERPRETER=/bin/python3" | sudo tee /etc/profile.d/ansible.sh >/dev/null
    source /etc/profile.d/ansible.sh
    ;;
esac
echo

# Check if passwordless sudo access is available
sudo -n true > /dev/null 2>&1
if [ $? -eq 1 ]; 
  then
    myANSIBLE_BECOME_OPTION="--become"
    echo "### ‘sudo‘ is setup passwordless, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo
  else
    myANSIBLE_BECOME_OPTION="--ask-become-pass"
    echo "### ‘sudo‘ is setup with password, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
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
ANSIBLE_LOG_PATH=$PWD/install_tpot.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local ${myANSIBLE_BECOME_OPTION}

# Pull docker images
echo "### Now pulling images ..."
docker compose -f /home/$(whoami)/tpotce/docker-compose.yml pull
echo

# Done and show running services
sudo grc netstat -tulpen
echo "Please review for possible honeypot port conflicts."
echo "While SSH is taken care of, other services such as"
echo "SMTP, HTTP, etc. might prevent T-Pot from starting."

echo "Done. Please reboot and re-connect via SSH on tcp/64295."
echo

