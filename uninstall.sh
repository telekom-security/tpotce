#!/usr/bin/env bash

sudo_password_required() {
  # `-k` ignores a cached credential, so a system that only appears to have
  # passwordless sudo does not slip through and Ansible fail once the timestamp
  # expires in the middle of the playbook.
  ! sudo -n -k true > /dev/null 2>&1
}

sudo_rs_become_exe() {
  # Ubuntu 26.04 makes sudo-rs the active `sudo`. It wraps the prompt it is
  # given with `-p` into "[sudo: <prompt>] Password:", while Ansible waits for a
  # line that starts with its own prompt and gives up with "Timed out waiting
  # for become success or become password prompt". The fix landed in
  # ansible-core devel only, so point Ansible at the traditional sudo, which
  # Ubuntu still ships next to sudo-rs.
  sudo --version 2>&1 | grep -qi "sudo-rs" || return
  for myEXE in /usr/bin/sudo.ws $(update-alternatives --list sudo 2>/dev/null | grep -v -- '-rs$'); do
    if [ -x "${myEXE}" ];
      then
        myANSIBLE_BECOME_EXE="-e ansible_become_exe=${myEXE}"
        echo "### ‘sudo‘ is sudo-rs, whose password prompt Ansible cannot read."
        echo "### Setting the Ansible become executable to ${myEXE}."
        echo
        return
    fi
  done
  echo "### ‘sudo‘ is sudo-rs and no traditional sudo was found next to it."
  echo "### Ansible cannot read the sudo-rs password prompt, so either install the"
  echo "### traditional sudo or configure passwordless sudo for ${myUSER}:"
  echo "###   sudo apt install sudo"
  echo "###   echo '${myUSER} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/${myUSER}"
  echo
  exit 1
}

myUNINSTALL_NOTIFICATION="### Now installing required packages ..."
myUSER=$(whoami)
myTPOT_CONF_FILE="/home/${myUSER}/tpotce/.env"
myANSIBLE_TPOT_PLAYBOOK="installer/remove/tpot.yml"
# Ansible become executable, empty means the Ansible default. See
# sudo_rs_become_exe.
myANSIBLE_BECOME_EXE=""

myUNINSTALLER=$(cat << "EOF"
 _____     ____       _     _   _       _           _        _ _
|_   _|   |  _ \ ___ | |_  | | | |_ __ (_)_ __  ___| |_ __ _| | | ___ _ __
  | |_____| |_) / _ \| __| | | | |  _ \| |  _ \/ __| __/ _  | | |/ _ \  __|
  | |_____|  __/ (_) | |_  | |_| | | | | | | | \__ \ || (_| | | |  __/ |
  |_|     |_|   \___/ \__|  \___/|_| |_|_|_| |_|___/\__\__,_|_|_|\___|_|
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
mySUPPORTED_DISTRIBUTIONS=("AlmaLinux" "Debian GNU/Linux" "Fedora Linux" "openSUSE Tumbleweed" "Raspbian GNU/Linux" "Red Hat Enterprise Linux" "Rocky Linux" "Ubuntu")
myCURRENT_DISTRIBUTION=$(awk -F= '/^NAME/{print $2}' /etc/os-release | tr -d '"')

if [[ ! " ${mySUPPORTED_DISTRIBUTIONS[@]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    echo "### Only the following distributions are supported: AlmaLinux, Fedora, Debian, openSUSE Tumbleweed, RHEL, Rocky Linux and Ubuntu."
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
myANSIBLE_DISTRIBUTIONS=("Fedora Linux" "Debian GNU/Linux" "Raspbian GNU/Linux" "Rocky Linux" "Red Hat Enterprise Linux")
if [[ "${myANSIBLE_DISTRIBUTIONS[@]}" =~ "${myCURRENT_DISTRIBUTION}" ]];
  then
    # special case AGAIN, /etc/os-release doesn't match Ansible's tagging conventions
    if [[ "${myCURRENT_DISTRIBUTION}" == "Red Hat Enterprise Linux" ]]; then
      myANSIBLE_TAG="RedHat"
    else
      myANSIBLE_TAG=$(echo ${myCURRENT_DISTRIBUTION} | cut -d " " -f 1)
    fi
  else
    myANSIBLE_TAG=${myCURRENT_DISTRIBUTION}
  fi

# Check type of sudo access. Applies to every distribution - making an
# exception for one of them asks for a password where none is needed.
if ! sudo_password_required;
  then
    myANSIBLE_BECOME_OPTION="--become"
    echo "### Passwordless ‘sudo‘ available, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo
  else
    sudo_rs_become_exe
    myANSIBLE_BECOME_OPTION="--become --ask-become-pass"
    echo "### ‘sudo‘ requires a password, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo "### Ansible will ask for the ‘BECOME password‘ which is typically the password you ’sudo’ with."
    echo
fi

# Run Ansible Playbook
echo "### Now running T-Pot Ansible Uninstallation Playbook ..."
echo
rm ${HOME}/uninstall_tpot.log > /dev/null 2>&1
# see install.sh for why these two are set explicitly
ANSIBLE_INJECT_FACT_VARS=False ANSIBLE_PYTHON_INTERPRETER=auto_silent \
ANSIBLE_LOG_PATH=${HOME}/uninstall_tpot.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local --tags "${myANSIBLE_TAG}" ${myANSIBLE_BECOME_OPTION} ${myANSIBLE_BECOME_EXE}

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
