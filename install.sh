#!/usr/bin/env bash

print_help() {
  cat <<EOF
Usage: $0 [-s] -t <type> [-u <webuser>] [-p <password>] [-b <branch>] [-r <url>]

Options:
  -s                Suppress installation confirmation prompt, unattended run
                    (requires passwordless sudo, see below)
  -b <branch>       Branch, tag or commit to install from. Default: the branch
                    of the local clone this script runs from, otherwise master
  -r <url>          Repository to install from, https URL, the raw URL for the
                    playbook is derived from it. Default: the origin of the
                    local clone this script runs from, otherwise
                    https://github.com/telekom-security/tpotce
  -t <type>         Type of installation (required if -s is used):
                      h - hive      (requires -u and -p)
                      s - sensor    (no user/pass required)
                      l - llm       (requires -u and -p)
                      i - mini      (requires -u and -p)
                      m - mobile    (no user/pass required)
                      t - tarpit    (requires -u and -p)
  -u <webuser>      Web interface username (required for h/l/i/t)
  -p <password>     Web interface password (required for h/l/i/t)
  -h                Show this help message
EOF
  exit 1
}

validate_type() {
  [[ "$myTPOT_TYPE" =~ ^[hslimtHSLIMT]$ ]] || {
    echo "Invalid installation type: $myTPOT_TYPE"
    print_help
  }
}

git_source() {
  # Reads $1 ("branch" or "repo") from the clone this script runs from. `$0` is
  # a path only when the script runs as a file - piped through
  # `bash -c "$(curl ...)"` it is not, and a clone in the current directory has
  # nothing to do with the script that is running, so it must not be used.
  command -v git >/dev/null || return
  [ -f "$0" ] || return
  myDIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
  git -C "${myDIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return
  case "$1" in
    branch)
      myREF=$(git -C "${myDIR}" rev-parse --abbrev-ref HEAD 2>/dev/null)
      # a detached HEAD has no branch name, the commit works just as well
      [ "${myREF}" = "HEAD" ] && myREF=$(git -C "${myDIR}" rev-parse HEAD 2>/dev/null)
      echo "${myREF}"
      ;;
    repo)
      git -C "${myDIR}" remote get-url origin 2>/dev/null
      ;;
  esac
}

normalize_repo() {
  # compare repository URLs without a trailing slash or `.git`
  myURL="${1%/}"
  echo "${myURL%.git}"
}

resolve_tpot_source() {
  # -b and -r win, then the environment, then the local clone, then master
  [ -z "${myTPOT_BRANCH}" ] && myTPOT_BRANCH=$(git_source branch)
  [ -z "${myTPOT_BRANCH}" ] && myTPOT_BRANCH="master"
  [ -z "${myTPOT_REPO_URL}" ] && myTPOT_REPO_URL=$(git_source repo)
  [ -z "${myTPOT_REPO_URL}" ] && myTPOT_REPO_URL="https://github.com/telekom-security/tpotce"
  myTPOT_REPO_URL=$(normalize_repo "${myTPOT_REPO_URL}")
}

check_tpot_clone() {
  # `update: no` in the playbook means Ansible keeps an existing ~/tpotce as it
  # is, without looking at the requested repository or branch - a test would
  # silently run against the previous checkout.
  [ -d "${HOME}/tpotce" ] || return
  if ! git -C "${HOME}/tpotce" rev-parse --is-inside-work-tree >/dev/null 2>&1;
    then
      echo "### ${HOME}/tpotce exists but is not a git repository, its origin cannot be verified."
      echo
      return
  fi
  myCLONE_BRANCH=$(git -C "${HOME}/tpotce" rev-parse --abbrev-ref HEAD 2>/dev/null)
  [ "${myCLONE_BRANCH}" = "HEAD" ] && myCLONE_BRANCH=$(git -C "${HOME}/tpotce" rev-parse HEAD 2>/dev/null)
  myCLONE_REPO=$(normalize_repo "$(git -C "${HOME}/tpotce" remote get-url origin 2>/dev/null)")
  if [ "${myCLONE_BRANCH}" != "${myTPOT_BRANCH}" ] || [ "${myCLONE_REPO}" != "${myTPOT_REPO_URL}" ];
    then
      echo "### ${HOME}/tpotce already exists and does not match what was requested:"
      echo "###   found:     ${myCLONE_REPO} at ${myCLONE_BRANCH}"
      echo "###   requested: ${myTPOT_REPO_URL} at ${myTPOT_BRANCH}"
      echo "### T-Pot would be installed from the existing checkout. Remove it and run"
      echo "### the installer again, or clone what you want to test into ${HOME}/tpotce:"
      echo "###   sudo rm -rf ${HOME}/tpotce"
      echo
      exit 1
  fi
}

sudo_password_required() {
  # `-k` ignores a cached credential: installing the packages refreshes the sudo
  # timestamp, so a plain `sudo -n true` would succeed on a password protected
  # system and Ansible would then fail once the timestamp expires in the middle
  # of the playbook.
  ! sudo -n -k true > /dev/null 2>&1
}

abort_unattended() {
  echo "### ‘sudo‘ requires a password, so -s cannot be honoured."
  echo "### Either configure passwordless sudo for ${myUSER}, e.g."
  echo "###   echo '${myUSER} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/${myUSER}"
  echo "### or run the installer without -s and enter the password when asked."
  echo
  exit 1
}

check_port_conflicts() {
  myPORT_CONFLICT=""
  for myENTRY in ${myCONFLICT_PORTS}; do
    myPROTO="${myENTRY%%/*}"
    myPORT="${myENTRY##*/}"
    # a socket is listed without root, only the process behind it is not
    myLINE=$(ss -H -ln --"${myPROTO}" "sport = :${myPORT}" 2>/dev/null | head -n 1)
    [ -z "${myLINE}" ] && continue
    # naming the process needs root, and on the Debian branch below `sudo` may
    # not be installed yet - the occupied port is reported either way
    myPROC=""
    if command -v sudo >/dev/null;
      then
        myPROC=$(sudo ss -H -lnp --"${myPROTO}" "sport = :${myPORT}" 2>/dev/null \
                 | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' \
                 | head -n 1)
    fi
    # The playbook turns the resolved stub listener off, so it is not a blocker.
    # `ss` truncates process names to 15 characters, hence systemd-resolve.
    if [ "${myPROC}" = "systemd-resolve" ];
      then
        continue
    fi
    echo "###   ${myPROTO}/${myPORT} is occupied by ${myPROC:-an unidentified process}"
    myPORT_CONFLICT="y"
  done
}

rhel_version() {
  # special case for RHEL due to its complicated repo infrastructure
  # primarily used for EPEL repo selection
  # T-Pot follows the current release, which is RHEL 10
  myRHEL_VERSION=$(grep PLATFORM_ID /etc/os-release | cut -d ':' -f2 | grep -Eo '([0-9]{1,2})')
  if [ "$myRHEL_VERSION" -lt 10 ]; then
    echo "Error: RHEL < 10 not supported!" >&2
    exit 1
  fi
  echo "$myRHEL_VERSION"
}

rhel_ansible_repo() {
  # rhel uses a dedicated repo for ansible that we need to enable through subscription-manager
  myRHEL_ANSIBLE_REPO=$(sudo subscription-manager repos --list \
    | grep -E "ansible-automation-platform-[0-9]{1}\.[0-9]{1}-for-rhel-$(rhel_version)-$(arch)-rpms" \
    | awk -F':' '{print $2}' \
    | tr -d ' ' \
    | sort -nr \
    | head -n 1
)
  echo "$myRHEL_ANSIBLE_REPO"
}

# Defaults
myQST=""
myUNATTENDED=""
myTPOT_TYPE=""
myWEB_USER=""
myWEB_PW=""
# Where to install T-Pot from. Empty means: work it out in resolve_tpot_source.
myTPOT_BRANCH="${TPOT_BRANCH}"
myTPOT_REPO_URL="${TPOT_REPO_URL}"

while getopts ":sb:r:t:u:p:h" opt; do
  case "$opt" in
    s)
      myQST="y"
      myUNATTENDED="y"
      ;;
    b)
      myTPOT_BRANCH="${OPTARG}"
      ;;
    r)
      myTPOT_REPO_URL="${OPTARG}"
      ;;
    t)
      myTPOT_TYPE="${OPTARG,,}"
      validate_type
      ;;
    u)
      export myWEB_USER="${OPTARG}"
      ;;
    p)
      export myWEB_PW="${OPTARG}"
      ;;
    h|\?)
      print_help
      ;;
    :)
      echo "Option -${OPTARG} requires an argument."
      print_help
      ;;
  esac
done

# -s requires -t
if [[ "$myUNATTENDED" == "y" && -z "$myTPOT_TYPE" ]]; then
  echo "Error: -t is required when using -s to suppress interaction."
  print_help
fi

# Determine if user/pass are required based on install type
if [[ "$myTPOT_TYPE" =~ ^[hlit]$ ]]; then
  [[ -n "$myWEB_USER" && -n "$myWEB_PW" ]] || {
    echo "Error: -u and -p are required for installation type '$myTPOT_TYPE'."
    print_help
  }
fi

resolve_tpot_source

myINSTALL_NOTIFICATION="### Now installing required packages ..."
myUSER=$(whoami)
myTPOT_CONF_FILE="${HOME}/tpotce/.env"
myPACKAGES_DEBIAN="ansible apache2-utils cracklib-runtime wget"
myPACKAGES_FEDORA="ansible cracklib httpd-tools wget"
myPACKAGES_ROCKY="ansible-core epel-release cracklib httpd-tools wget"
myPACKAGES_RHEL="ansible-core ansible-collection-redhat-rhel_mgmt cracklib httpd-tools wget"    
myPACKAGES_OPENSUSE="ansible apache2-utils cracklib wget"
# Ports a honeypot needs that a distribution service is likely to hold. A
# service on 127.0.0.1 conflicts with a container publishing the same port on
# 0.0.0.0, so a loopback listener counts as well.
myCONFLICT_PORTS="tcp/25 tcp/53 udp/53"


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
mySUPPORTED_DISTRIBUTIONS=("AlmaLinux" "Debian GNU/Linux" "Fedora Linux" "openSUSE Tumbleweed" "Raspbian GNU/Linux" "Red Hat Enterprise Linux" "Rocky Linux" "Ubuntu")
myCURRENT_DISTRIBUTION=$(awk -F= '/^NAME/{print $2}' /etc/os-release | tr -d '"')

if [[ ! " ${mySUPPORTED_DISTRIBUTIONS[@]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    echo "### Only the following distributions are supported: AlmaLinux, Fedora, Debian, openSUSE Tumbleweed, RHEL, Rocky Linux and Ubuntu."
    echo "### Please follow the T-Pot documentation on how to run T-Pot on macOS, Windows and other currently unsupported platforms."
    echo
    exit 1
fi

# Check if running on a supported distribution version. T-Pot follows the
# current release of each distribution, the packages and repositories it uses
# are only available there. openSUSE Tumbleweed rolls and is not pinned.
myVERSION_ID=$(awk -F= '/^VERSION_ID/{print $2}' /etc/os-release | tr -d '"')
case ${myCURRENT_DISTRIBUTION} in
  "AlmaLinux"|"Red Hat Enterprise Linux"|"Rocky Linux")
    mySUPPORTED_VERSION="10"
    myCURRENT_VERSION="${myVERSION_ID%%.*}"
    ;;
  "Fedora Linux")
    mySUPPORTED_VERSION="44"
    myCURRENT_VERSION="${myVERSION_ID%%.*}"
    ;;
  "Debian GNU/Linux"|"Raspbian GNU/Linux")
    mySUPPORTED_VERSION="13"
    myCURRENT_VERSION="${myVERSION_ID%%.*}"
    ;;
  "Ubuntu")
    # Ubuntu releases twice a year, its version is major and minor
    mySUPPORTED_VERSION="26.04"
    myCURRENT_VERSION="${myVERSION_ID}"
    ;;
  *)
    mySUPPORTED_VERSION=""
    ;;
esac

if [ -n "${mySUPPORTED_VERSION}" ] && [ "${myCURRENT_VERSION}" != "${mySUPPORTED_VERSION}" ];
  then
    echo "### T-Pot supports ${myCURRENT_DISTRIBUTION} ${mySUPPORTED_VERSION}, this system runs ${myCURRENT_VERSION}."
    echo "### Please install T-Pot on the current release of your distribution."
    echo
    exit 1
fi

# Begin of Installer
echo "$myINSTALLER"
echo
echo
echo "### This script will now install T-Pot and all of its dependencies."
echo "### Source: ${myTPOT_REPO_URL} at ${myTPOT_BRANCH}"
if [[ -z "$myQST" ]]; then
  while [ "${myQST}" != "y" ] && [ "${myQST}" != "n" ]; do
    echo
    read -p "### Install? (y/n) " myQST
    echo
  done
fi
if [ "${myQST}" = "n" ]; then
    echo
    echo "### Aborting!"
    echo
    exit 0
fi

# Fail before anything is installed if an existing ~/tpotce would be used
# instead of the repository and branch that were asked for.
check_tpot_clone

# Fail before anything is installed: -s promises an unattended run, but Ansible
# would ask for the become password. Only possible where sudo already exists -
# the Debian branch below installs it and the check is repeated afterwards.
if [ "${myUNATTENDED}" = "y" ] && command -v sudo >/dev/null && sudo_password_required;
  then
    abort_unattended
fi

# Abort before anything is installed if a service holds a port a honeypot needs.
# The warning at the end of this script comes too late to act on, and an
# unattended run cannot act on it at all.
if ! command -v ss >/dev/null;
  then
    echo "### ‘ss‘ was not found, so the check for conflicting services cannot run."
    echo "### Install it and run the installer again:"
    echo "###   Debian, Raspbian, Ubuntu:       sudo apt install iproute2"
    echo "###   AlmaLinux, Fedora, RHEL, Rocky: sudo dnf install iproute"
    echo "###   openSUSE Tumbleweed:            sudo zypper install iproute2"
    echo
    exit 1
fi
echo "### Now checking for services on ports T-Pot needs ..."
check_port_conflicts
if [ "${myPORT_CONFLICT}" = "y" ];
  then
    echo "### T-Pot publishes these ports for its honeypots, so a clean installation"
    echo "### is required. Identify and disable the services, then run the installer"
    echo "### again:"
    echo "###   sudo ss -lntup"
    echo "###   sudo systemctl list-sockets    # for a process that reads ‘systemd‘"
    echo "###   sudo systemctl disable --now <unit>"
    echo
    exit 1
  else
    echo "### ... no services found on ports T-Pot needs."
    echo
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
        # Ansible cannot be handed a become password by -s, so an unattended
        # run needs a passwordless rule for the user we are about to add.
        if [ "${myUNATTENDED}" = "y" ];
          then
            mySUDOERS_RULE="${myUSER} ALL=(ALL) NOPASSWD:ALL"
            echo "### ‘-s‘ was given, so ${myUSER} will get passwordless sudo."
            echo "### Remove /etc/sudoers.d/${myUSER} after the installation to undo it."
            echo
          else
            mySUDOERS_RULE="${myUSER} ALL=(ALL:ALL) ALL"
        fi
        su -c "apt -y update && \
               NEEDRESTART_SUSPEND=1 apt -y install sudo ${myPACKAGES_DEBIAN} && \
               /usr/sbin/usermod -aG sudo ${myUSER} && \
               echo '${mySUDOERS_RULE}' | tee /etc/sudoers.d/${myUSER} >/dev/null && \
               chmod 440 /etc/sudoers.d/${myUSER}"
        echo "### We need sudo for Ansible, please enter the sudo password ..."
        sudo echo "### ... sudo works. Note that Ansible needs it without a password prompt, see below."
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
  "Red Hat Enterprise Linux")
    echo
    echo ${myINSTALL_NOTIFICATION}
    echo
    echo "RHEL detected - configuring version and Ansible repo strings"
    rhel_version
    rhel_ansible_repo
    sudo yum update
    # extra repo required for EPEL on RHEL
    sudo subscription-manager repos --enable codeready-builder-for-rhel-"$myRHEL_VERSION"-$(arch)-rpms
    # epel installer is not standard on RHEL
    sudo dnf -y install https://dl.fedoraproject.org/pub/epel/epel-release-latest-"$myRHEL_VERSION".noarch.rpm
    # ansible comes from rhel subscription manager
    sudo subscription-manager repos --enable "$myRHEL_ANSIBLE_REPO"
    sudo dnf -y --refresh install ${myPACKAGES_RHEL}
    ansible-galaxy collection install ansible.posix
esac
echo

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

# Download tpot.yml if not found locally
if [ ! -f installer/install/tpot.yml ] && [ ! -f tpot.yml ];
  then
    echo "### Now downloading T-Pot Ansible Installation Playbook ... "
    myANSIBLE_TPOT_PLAYBOOK_URL="${myTPOT_REPO_URL/github.com/raw.githubusercontent.com}/${myTPOT_BRANCH}/installer/install/tpot.yml"
    if ! wget -qO tpot.yml "${myANSIBLE_TPOT_PLAYBOOK_URL}";
      # a mistyped branch ends up here, and would fail with a confusing Ansible
      # error further down
      then
        echo "### Download failed: ${myANSIBLE_TPOT_PLAYBOOK_URL}"
        echo "### Check the repository and the branch, then run the installer again."
        echo
        exit 1
    fi
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

# Check type of sudo access. Applies to every distribution - making an
# exception for one of them breaks unattended installation there.
if ! sudo_password_required;
  then
    myANSIBLE_BECOME_OPTION="--become"
    echo "### Passwordless ‘sudo‘ available, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo
  else
    # -s promises an unattended run, and --ask-become-pass would prompt. On the
    # Debian branch sudo may have been installed after the check above.
    if [ "${myUNATTENDED}" = "y" ];
      then
        abort_unattended
    fi
    myANSIBLE_BECOME_OPTION="--become --ask-become-pass"
    echo "### ‘sudo‘ requires a password, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo "### Ansible will ask for the ‘BECOME password‘ which is typically the password you ’sudo’ with."
    echo
fi

# Run Ansible Playbook
echo "### Now running T-Pot Ansible Installation Playbook ..."
echo
rm ${HOME}/install_tpot.log > /dev/null 2>&1
# neither a repository URL nor a git reference contains a space, so the
# unquoted expansion below splits into exactly four arguments
myANSIBLE_EXTRA_VARS="-e tpot_repo=${myTPOT_REPO_URL} -e tpot_branch=${myTPOT_BRANCH}"
ANSIBLE_LOG_PATH=${HOME}/install_tpot.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local --tags "${myANSIBLE_TAG}" ${myANSIBLE_BECOME_OPTION} ${myANSIBLE_EXTRA_VARS}

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
echo "### (L)LM    - T-Pot LLM installation."
echo "###            Uses LLM based honeypots Beelzebub & Galah."
echo "###            Requires Ollama (recommended) or ChatGPT subscription."
echo "### M(i)ni   - T-Pot Mini installation."
echo "###            Run 30+ honeypots with just a couple of honeypot daemons."
echo "### (M)obile - T-Pot Mobile installation."
echo "###            Includes everything to run T-Pot Mobile (available separately)."
echo "### (T)arpit - T-Pot Tarpit installation."
echo "###            Feed data endlessly to attackers, bots and scanners."
echo "###            Also runs a Denial of Service Honeypot (ddospot)."
echo
while true; do
  if [[ -z "$myTPOT_TYPE" ]]; then
    read -p "### Install Type? (h/s/l/i/m/t) " myTPOT_TYPE
  fi  
  
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
    l|L)
      echo
      echo "### Installing T-Pot LLM."
      myTPOT_TYPE="HIVE"
      cp ${HOME}/tpotce/compose/llm.yml ${HOME}/tpotce/docker-compose.yml
      myINFO="Make sure to adjust the T-Pot config file (.env) for Ollama / ChatGPT settings."
      break ;;
    i|I)
      echo
      echo "### Installing T-Pot Mini."
      myTPOT_TYPE="HIVE"
      cp ${HOME}/tpotce/compose/mini.yml ${HOME}/tpotce/docker-compose.yml
      myINFO=""
      break ;;
    m|M)
      echo
      echo "### Installing T-Pot Mobile."
      myTPOT_TYPE="MOBILE"
      cp ${HOME}/tpotce/compose/mobile.yml ${HOME}/tpotce/docker-compose.yml
      myINFO=""
      break ;;
    t|T)
      echo
      echo "### Installing T-Pot Tarpit."
      myTPOT_TYPE="HIVE"
      cp ${HOME}/tpotce/compose/tarpit.yml ${HOME}/tpotce/docker-compose.yml
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
  if [[ -z "$myWEB_USER" ]]; then
    myWEB_USER=""
    while [ 1 != 2 ]; do
      myOK=""
      read -rp "### Enter your web user name: " myWEB_USER
      myWEB_USER=$(echo $myWEB_USER | tr -cd "[:alnum:]_.-")
      echo "### Your username is: ${myWEB_USER}"
      while [[ ! "${myOK}" =~ [YyNn] ]]; do    
        read -rp "### Is this correct? (y/n) " myOK
      done
      if [[ "${myOK}" =~ [Yy] ]] && [ "$myWEB_USER" != "" ]; then
        break
      else
        echo
      fi
    done
  fi

  # Asking for web user password
  if [[ -z "$myWEB_PW" ]]; then
    myWEB_PW="pass1"
    myWEB_PW2="pass2"
    mySECURE=0
    myOK=""
    while [ "${myWEB_PW}" != "${myWEB_PW2}" ] && [ "${mySECURE}" == "0" ]; do
      echo
      while [ "${myWEB_PW}" == "pass1" ] || [ "${myWEB_PW}" == "" ]; do
        read -rsp "### Enter password for your web user: " myWEB_PW
        echo
      done
      read -rsp "### Repeat password you your web user: " myWEB_PW2
      echo
      if [ "${myWEB_PW}" != "${myWEB_PW2}" ]; then
        echo "### Passwords do not match."
        myWEB_PW="pass1"
        myWEB_PW2="pass2"
      fi
      mySECURE=$(printf "%s" "$myWEB_PW" | /usr/sbin/cracklib-check | grep -c "OK")
      if [ "$mySECURE" == "0" ] && [ "$myWEB_PW" == "$myWEB_PW2" ]; then
        while [[ ! "${myOK}" =~ [YyNn] ]]; do
          read -rp "### Keep insecure password? (y/n) " myOK
        done
        if [[ "${myOK}" =~ [Nn] ]] || [ "$myWEB_PW" == "" ]; then
          myWEB_PW="pass1"
          myWEB_PW2="pass2"
          mySECURE=0
          myOK=""
        fi
      fi
    done
  fi


  # Write username and password to T-Pot config file
  echo "### Creating base64 encoded htpasswd username and password for T-Pot config file: ${myTPOT_CONF_FILE}"
  myWEB_USER_ENC=$(htpasswd -b -n "${myWEB_USER}" "${myWEB_PW}")
    myWEB_USER_ENC_B64=$(echo -n "${myWEB_USER_ENC}" | base64 -w0)
    
  echo
  sed -i "s|^WEB_USER=.*|WEB_USER=${myWEB_USER_ENC_B64}|" ${myTPOT_CONF_FILE}
fi

# Pull docker images
echo "### Now pulling images ..."
sudo docker compose -f "${HOME}/tpotce/docker-compose.yml" pull
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
