#!/usr/bin/env bash

myUNINSTALL_NOTIFICATION="### Now installing required packages ..."
myUSER=$(whoami)
myTPOT_CONF_FILE="/home/${myUSER}/tpotce/.env"
myANSIBLE_TPOT_PLAYBOOK="installer/remove/tpot.yml"

myUNINSTALLER=$(cat << "EOF"

 _____  ________      __       _____  _____    _____   ____     ____  _______    _____  _    _  ______ _    _    _____  __ 
|_   _||___  /\ \    / //\    |  __ \|_   _|  / ____| / __ \   / __ \|__   __|  / ____|| |  | ||___  /| |  | |  / ____|/_ |
  | |     / /  \ \  / //  \   | |  | | | |   | |  __ | |  | | | |  | |  | |    | |  __ | |  | |   / / | |  | | | (___   | |
  | |    / /    \ \/ // /\ \  | |  | | | |   | | |_ || |  | | | |  | |  | |    | | |_ || |  | |  / /  | |  | |  \___ \  | |
 _| |_  / /__    \  // ____ \ | |__| |_| |_  | |__| || |__| | | |__| |  | |    | |__| || |__| | / /__ | |__| |  ____) | | |
|_____|/_____|    \//_/    \_\|_____/|_____|  \_____| \____/   \____/   |_|     \_____| \____/ /_____| \____/  |_____/  |_|
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

# Begin of Uninstaller
echo "$myUNINSTALLER"
echo
echo
echo "### This script will now uninstall T-Pot."
while [ "${myQST}" != "y" ] && [ "${myQST}" != "n" ];
  do
    echo
    read -p "### Uninstall? (y/n) " myQST
    echo
  done
if [ "${myQST}" = "n" ];
  then
    echo
    echo "### Aborting!"
    echo
    exit 0
fi

# Define tag for Ansible
myANSIBLE_DISTRIBUTIONS=("Fedora Linux" "Debian GNU/Linux" "Raspbian GNU/Linux" "Rocky Linux")
if [[ "${myANSIBLE_DISTRIBUTIONS[@]}" =~ "${myCURRENT_DISTRIBUTION}" ]];
  then
    myANSIBLE_TAG=$(echo ${myCURRENT_DISTRIBUTION} | cut -d " " -f 1)
  else
    myANSIBLE_TAG=${myCURRENT_DISTRIBUTION}
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
echo "### Now running T-Pot Ansible Uninstallation Playbook ..."
echo
rm ${HOME}/uninstall_tpot.log > /dev/null 2>&1
ANSIBLE_LOG_PATH=${HOME}/uninstall_tpot.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local --tags "${myANSIBLE_TAG}" ${myANSIBLE_BECOME_OPTION}

# Something went wrong
if [ ! $? -eq 0 ];
  then
    echo "### Something went wrong with the Playbook, please review the output and / or uninstall_tpot.log for clues."
    echo "### Aborting."
    echo
    exit 1
  else
    echo "### Playbook was successful."
    echo "### Now removing ${HOME}/tpotce."
    sudo rm -rf ${HOME}/tpotce
    rm -rf ${HOME}/tpot.yml
    echo
fi

# Done
echo "### Done. Please reboot and re-connect via SSH on tcp/22."
echo
