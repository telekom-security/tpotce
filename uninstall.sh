#!/usr/bin/env bash

print_help() {
  # shellcheck disable=SC2088 # the examples show ~ as a person types it
  fuUI_HELP "Uninstaller" "uninstall.sh [-y] [-k] [-B <file>]" \
    --about "Removes T-Pot from this host: the containers, images and data, Docker Engine,
tpot.service, the tpot user and group, the tpot command and ~/tpotce. SSH goes
back to port 22. Backups in ~/tpot_backups stay." \
    --about "tpot uninstall asks (the host name) and runs it with -y." \
    --opt "-y" "Uninstall without asking, unattended run (requires passwordless
sudo or -B)" \
    --opt "-k" "Keep a full backup first (update.sh --backup-only --full), it
lands in ~/tpot_backups and restore.sh can bring it back" \
    --opt "-B <file>" "Read the sudo password from a file, so -y also works without
passwordless sudo" \
    --opt "-h" "Show this help message" \
    --example "~/tpotce/uninstall.sh" "Asks before it removes T-Pot" \
    --example "~/tpotce/uninstall.sh -y -k" "A full backup first, then T-Pot goes without a question" \
    --note "Run it as a regular user with sudo, not as root." \
    --note "The log of the playbook: ~/uninstall_tpot.log"
  exit 0
}

usage_error() {
  # a wrong option or value: what is wrong and where the help is, exit 1
  fuUI_USAGE_ERROR "$1" "uninstall.sh"
  exit 1
}

uninstall_failed() {
  # uninstall_failed <what failed> <next step> ...: the summary of a run that stops, exit 1
  local myITEM
  local -a myITEMS=("fail:$1")
  shift
  for myITEM in "$@"; do myITEMS+=("next:${myITEM}"); done
  fuUI_SUMMARY "T-Pot is not uninstalled" "${myITEMS[@]}"
  exit 1
}

myHERE=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
# the look of the T-Pot scripts (gum or plain text), see installer/lib/ui.sh
# shellcheck source=installer/lib/ui.sh
source "${myHERE}/installer/lib/ui.sh" 2>/dev/null || source "${HOME}/tpotce/installer/lib/ui.sh" || {
  echo "### installer/lib/ui.sh is missing, ${myHERE} is not a complete T-Pot checkout."
  exit 1
}

sudo_password_required() {
  # `-k` ignores a cached credential, so a system that only appears to have
  # passwordless sudo does not slip through and Ansible fail once the timestamp
  # expires in the middle of the playbook.
  ! command sudo -n -k true > /dev/null 2>&1
}

sudo_rs_become_exe() {
  # Ubuntu 26.04 makes sudo-rs the active `sudo`. It wraps the prompt it is
  # given with `-p` into "[sudo: <prompt>] Password:", while Ansible waits for a
  # line that starts with its own prompt and gives up with "Timed out waiting
  # for become success or become password prompt". The fix landed in
  # ansible-core devel only, so point Ansible at the traditional sudo, which
  # Ubuntu still ships next to sudo-rs.
  command sudo --version 2>&1 | grep -qi "sudo-rs" || return
  for myEXE in /usr/bin/sudo.ws $(update-alternatives --list sudo 2>/dev/null | grep -v -- '-rs$'); do
    if [ -x "${myEXE}" ];
      then
        myANSIBLE_BECOME_EXE="-e ansible_become_exe=${myEXE}"
        fuUI_INFO "‘sudo‘ is sudo-rs, whose password prompt Ansible cannot read."
        fuUI_INFO "Setting the Ansible become executable to ${myEXE}."
        echo
        return
    fi
  done
  fuUI_ERROR "‘sudo‘ is sudo-rs and no traditional sudo was found next to it."
  fuUI_INFO "Ansible cannot read the sudo-rs password prompt, so either install the"
  fuUI_INFO "traditional sudo or configure passwordless sudo for ${myUSER}:"
  fuUI_HINT "sudo apt install sudo" \
            "echo '${myUSER} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/${myUSER}"
  echo
  exit 1
}

fuCLEANUP () {
  [ -n "${myUI_TMP}" ] && rm -rf "${myUI_TMP}"
  # tpot uninstall hands over a password file of its own, it goes with this run
  if [ -n "${myBECOME_FILE}" ] && [ "${TPOT_REMOVE_BECOME_FILE}" = "1" ];
    then
      rm -f "${myBECOME_FILE}"
  fi
}

myQST=""
myUNATTENDED=""
myBACKUP=""
myBECOME_FILE=""
myUSER=$(whoami)
myANSIBLE_TPOT_PLAYBOOK="${myHERE}/installer/remove/tpot.yml"
# Ansible become executable, empty means the Ansible default. See
# sudo_rs_become_exe.
myANSIBLE_BECOME_EXE=""

while getopts ":ykB:h" opt; do
  case "$opt" in
    y) myQST="y"; myUNATTENDED="y" ;;
    k) myBACKUP="y" ;;
    B) myBECOME_FILE="${OPTARG}" ;;
    h) print_help ;;
    \?) usage_error "Unknown option -${OPTARG}." ;;
    :) usage_error "Option -${OPTARG} requires an argument." ;;
  esac
done

trap fuCLEANUP EXIT

if [ -n "${myBECOME_FILE}" ];
  then
    [ -r "${myBECOME_FILE}" ] || usage_error "Cannot read the sudo password from ${myBECOME_FILE}."
    myBECOME_FILE=$(cd "$(dirname "${myBECOME_FILE}")" && pwd)/$(basename "${myBECOME_FILE}")
    # every sudo of this script refreshes the timestamp from the file first
    sudo () { command sudo -S -p "" -v < "${myBECOME_FILE}" >/dev/null 2>&1; command sudo "$@"; }
fi

fuUI_INIT
# the playbook removes ~/.local/share/tpotce and gum with it, the rest of this run
# uses a copy
if [ -n "${myUI_GUM}" ];
  then
    myUI_TMP=$(mktemp -d) && cp "${myUI_GUM}" "${myUI_TMP}/gum" && myUI_GUM="${myUI_TMP}/gum" || myUI_GUM=""
fi

# Check if running with root privileges
if [ ${EUID} -eq 0 ];
  then
    fuUI_ERROR "This script should not be run as root. Please run it as a regular user."
    echo
    exit 1
fi

# Check if running on a supported distribution
mySUPPORTED_DISTRIBUTIONS=("AlmaLinux" "Debian GNU/Linux" "Fedora Linux" "openSUSE Tumbleweed" "Raspbian GNU/Linux" "Red Hat Enterprise Linux" "Rocky Linux" "Ubuntu")
myCURRENT_DISTRIBUTION=$(awk -F= '/^NAME/{print $2}' /etc/os-release | tr -d '"')

if [[ ! " ${mySUPPORTED_DISTRIBUTIONS[*]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    fuUI_ERROR "Only the following distributions are supported: AlmaLinux, Fedora, Debian, openSUSE Tumbleweed, RHEL, Rocky Linux and Ubuntu."
    fuUI_INFO "Please follow the T-Pot documentation on how to run T-Pot on macOS, Windows and other currently unsupported platforms."
    echo
    exit 1
fi

# Begin of Uninstaller: the T-Pot logo above the banner, at a terminal only (fuUI_LOGO_ON)
# shellcheck disable=SC2034 # fuUI_BANNER of installer/lib/ui.sh reads it
myUI_LOGO=1
fuUI_BANNER "Uninstaller" "This script will now uninstall T-Pot: containers, images, data, Docker Engine," \
  "tpot.service, the tpot user and command, ~/tpotce. SSH goes back to port 22."
if [ -z "${myQST}" ] && ! fuUI_CONFIRM "Uninstall T-Pot?" "Uninstall" "Keep T-Pot";
  then
    echo
    fuUI_INFO "Aborting!"
    echo
    exit 0
fi

# A sudo password from -B has to work, a typo would only show in the playbook
if [ -n "${myBECOME_FILE}" ] && ! command sudo -S -k -p "" -v < "${myBECOME_FILE}" >/dev/null 2>&1;
  then
    fuUI_ERROR "The sudo password in ${myBECOME_FILE} is not accepted."
    uninstall_failed "sudo does not accept the password of -B" "Check ${myBECOME_FILE}, then run the uninstaller again"
fi

# -y promises an unattended run, Ansible would ask for the become password
if [ "${myUNATTENDED}" = "y" ] && [ -z "${myBECOME_FILE}" ] && sudo_password_required;
  then
    fuUI_ERROR "‘sudo‘ requires a password, so -y cannot be honoured."
    fuUI_INFO "Hand the password over with -B <file>, configure passwordless sudo for ${myUSER},"
    fuUI_INFO "or run the uninstaller without -y and enter the password when asked."
    uninstall_failed "-y needs sudo without a password prompt" \
      "Run the uninstaller again with -B <file>, with passwordless sudo or without -y"
fi

# A backup first: the same archive an update writes, with the data
if [ "${myBACKUP}" = "y" ];
  then
    fuUI_INFO "Writing a full backup to ~/tpot_backups first ..."
    if [ -z "${myBECOME_FILE}" ] && sudo_password_required;
      then
        sudo -v || uninstall_failed "sudo did not accept the password" "Run the uninstaller again"
      else
        sudo true
    fi
    if ! "${myHERE}/update.sh" -y --backup-only --full;
      then
        fuUI_ERROR "The backup failed, T-Pot is not uninstalled."
        uninstall_failed "The backup failed, see the output above" \
          "Fix the cause and run the uninstaller again, or run it without -k"
    fi
    fuUI_OK "Backup written, it stays in ~/tpot_backups."
    echo
fi

# Define tag for Ansible
myANSIBLE_DISTRIBUTIONS=("Fedora Linux" "Debian GNU/Linux" "Raspbian GNU/Linux" "Rocky Linux" "Red Hat Enterprise Linux")
if [[ " ${myANSIBLE_DISTRIBUTIONS[*]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    # special case AGAIN, /etc/os-release doesn't match Ansible's tagging conventions
    if [[ "${myCURRENT_DISTRIBUTION}" == "Red Hat Enterprise Linux" ]]; then
      myANSIBLE_TAG="RedHat"
    else
      myANSIBLE_TAG=$(echo "${myCURRENT_DISTRIBUTION}" | cut -d " " -f 1)
    fi
  else
    myANSIBLE_TAG=${myCURRENT_DISTRIBUTION}
fi

# Check type of sudo access. Applies to every distribution - making an
# exception for one of them asks for a password where none is needed.
if ! sudo_password_required;
  then
    myANSIBLE_BECOME_OPTION="--become"
    fuUI_INFO "Passwordless ‘sudo‘ available, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo
elif [ -n "${myBECOME_FILE}" ];
  then
    sudo_rs_become_exe
    myANSIBLE_BECOME_OPTION="--become --become-password-file ${myBECOME_FILE}"
    fuUI_INFO "‘sudo‘ requires a password, Ansible reads it from ${myBECOME_FILE}."
    echo
  else
    sudo_rs_become_exe
    myANSIBLE_BECOME_OPTION="--become --ask-become-pass"
    fuUI_INFO "‘sudo‘ requires a password, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    fuUI_INFO "Ansible will ask for the ‘BECOME password‘ which is typically the password you ’sudo’ with."
    echo
fi

# Run Ansible Playbook
fuUI_INFO "Now running T-Pot Ansible Uninstallation Playbook ..."
echo
rm "${HOME}/uninstall_tpot.log" > /dev/null 2>&1
# see install.sh for why these two are set explicitly
ANSIBLE_INJECT_FACT_VARS=False ANSIBLE_PYTHON_INTERPRETER=auto_silent \
ANSIBLE_LOG_PATH=${HOME}/uninstall_tpot.log ansible-playbook "${myANSIBLE_TPOT_PLAYBOOK}" -i 127.0.0.1, -c local --tags "${myANSIBLE_TAG}" ${myANSIBLE_BECOME_OPTION} ${myANSIBLE_BECOME_EXE}

# Something went wrong
if [ ! $? -eq 0 ];
  then
    fuUI_ERROR "Something went wrong with the Playbook, please review the output and / or uninstall_tpot.log for clues."
    uninstall_failed "The playbook failed, see the output above" \
      "Review ${HOME}/uninstall_tpot.log, fix the cause, then run the uninstaller again"
fi
fuUI_OK "Playbook was successful."
echo

# The checkout goes last, with a spinner meanwhile. It runs in the background, where sudo
# cannot ask: a password is asked for now (-B refreshes it from its file)
cd "${HOME}" || exit 1
if [ -z "${myBECOME_FILE}" ] && sudo_password_required;
  then
    sudo -v
fi
myREMOVED="ok:${HOME}/tpotce is removed"
if ! fuUI_SPIN "Removing ${HOME}/tpotce ..." "${HOME}/uninstall_tpot.log" sudo rm -rf "${HOME}/tpotce";
  then
    myREMOVED="warn:${HOME}/tpotce is still there, remove it with: sudo rm -rf ${HOME}/tpotce"
fi
rm -rf "${HOME}/tpot.yml"

# Done: what is gone and what comes next
mySUMMARY=("ok:T-Pot, its data, Docker Engine and the T-Pot Manager are removed" "${myREMOVED}")
if [ "${myBACKUP}" = "y" ];
  then mySUMMARY+=("ok:A full backup is in ~/tpot_backups, restore.sh brings it back after a new installation")
  else mySUMMARY+=("info:Backups in ~/tpot_backups stay")
fi
mySUMMARY+=("next:Reboot, then re-connect via SSH on tcp/22")
fuUI_SUMMARY "T-Pot is uninstalled" "${mySUMMARY[@]}"
