#!/usr/bin/env bash

print_help() {
  cat <<EOF
Usage: $0 [-s] -t <type> [-u <webuser>] [-p <password> | -P <file>] [-B <file>]
          [-c <compose file>] [-b <branch>] [-r <url>] [-n]

Without -s, at a terminal, the installer gets what it needs to start and hands
over to the T-Pot installer assistant (tpot install). -s installs without any
question.

Options:
  -s                Suppress installation confirmation prompt, unattended run
                    (requires passwordless sudo or -B, see below)
  -b <branch>       Branch, tag or commit to install from. Default: the branch
                    of the local clone this script runs from, otherwise master
  -r <url>          Repository to install from, https URL, the raw URL for the
                    playbook is derived from it. Default: the origin of the
                    local clone this script runs from, otherwise
                    https://github.com/telekom-security/tpotce
  -t <type>         Type of installation (required if -s is used):
                      h - hive      (requires -u and -p / -P)
                      s - sensor    (no user/pass required)
                      l - llm       (requires -u and -p / -P)
                      i - mini      (requires -u and -p / -P)
                      m - mobile    (no user/pass required)
                      t - tarpit    (requires -u and -p / -P)
                    With -c only h (a HIVE) or s (a SENSOR).
  -u <webuser>      Web interface username (required for h/l/i/t)
  -p <password>     Web interface password (required for h/l/i/t)
  -P <file>         Read the web interface password from a file, - for stdin.
                    Unlike -p it does not show up in the process list.
  -B <file>         Read the sudo password from a file, so -s also works without
                    passwordless sudo. The file is only read.
  -c <file>         Install this compose file (i.e. from tpot customize) instead
                    of an edition.
  -n                No assistant, ask in the terminal (as earlier releases did)
  -h                Show this help message
EOF
  exit 1
}

# >>> tpot ui >>>
# The look of the T-Pot scripts: gum (https://github.com/charmbracelet/gum) for a
# person at a terminal, plain text otherwise. Keep in sync! install.sh carries an
# identical copy of this block (it runs from curl without the repository), the
# other scripts source installer/lib/ui.sh. tpotctl/tests/test_installer.py checks.
#
# gum is a pinned release, its sha256 taken from the signed checksums.txt of the
# release. A failed download or a wrong hash means plain text, never a stop.
# TPOT_GUM=off keeps the plain text.

myUI_GUM_VERSION="2.0.2"
myUI_GUM_SHA256_x86_64="d842e06d93dbed90af48cb8dd10698db6f22e331fc40346bb37bbc753109edc2"
myUI_GUM_SHA256_arm64="8ebf8b54ec1e8c81f2bb58b59ff9b70998186a4d11375f0cf357b80e0ccfa1d5"
# the colours of tpot (tpotctl/theme.py)
myUI_MAGENTA="#E20074"
myUI_PETROL="#014463"
myUI_GLASS="#ECEFF9"
myUI_ASH="#A2A2AD"
myUI_OK_COLOUR="#3FA34D"
myUI_WARN_COLOUR="#F4B400"
myUI_ERROR_COLOUR="#E8453C"
myUI_GUM=""

fuUI_INIT () {
  # gum for a terminal only; output to a file or a pipe stays plain text
  myUI_GUM=""
  [ -t 1 ] || return 0
  [ "${TPOT_GUM:-on}" = "off" ] && return 0
  local myDIR="${XDG_DATA_HOME:-${HOME}/.local/share}/tpotce/bin"
  local myBIN="${myDIR}/gum"
  if [ -x "${myBIN}" ] && "${myBIN}" --version 2>/dev/null | grep -q "${myUI_GUM_VERSION}";
    then
      myUI_GUM="${myBIN}"
      return 0
  fi
  local myARCH mySHA
  case "$(uname -m)" in
    x86_64|amd64) myARCH="x86_64"; mySHA="${myUI_GUM_SHA256_x86_64}" ;;
    aarch64|arm64) myARCH="arm64"; mySHA="${myUI_GUM_SHA256_arm64}" ;;
    *) return 0 ;;
  esac
  local myNAME="gum_${myUI_GUM_VERSION}_Linux_${myARCH}"
  local myURL="https://github.com/charmbracelet/gum/releases/download/v${myUI_GUM_VERSION}/${myNAME}.tar.gz"
  local myTMP
  myTMP=$(mktemp -d 2>/dev/null) || return 0
  if command -v curl >/dev/null;
    then curl -fsSL --max-time 30 -o "${myTMP}/gum.tgz" "${myURL}" 2>/dev/null
    else wget -q -T 30 -O "${myTMP}/gum.tgz" "${myURL}" 2>/dev/null
  fi
  if [ -s "${myTMP}/gum.tgz" ] && command -v sha256sum >/dev/null \
     && echo "${mySHA}  ${myTMP}/gum.tgz" | sha256sum -c --status 2>/dev/null \
     && tar xzf "${myTMP}/gum.tgz" -C "${myTMP}" "${myNAME}/gum" 2>/dev/null \
     && mkdir -p "${myDIR}" && install -m 0755 "${myTMP}/${myNAME}/gum" "${myBIN}" 2>/dev/null;
    then
      myUI_GUM="${myBIN}"
  fi
  rm -rf "${myTMP}"
  return 0
}

fuUI_STYLE () {
  # fuUI_STYLE <colour> <text>: one coloured line, plain without gum
  if [ -n "${myUI_GUM}" ];
    then "${myUI_GUM}" style --foreground "$1" -- "$2"
    else echo "$2"
  fi
}

fuUI_PAINT () {
  # a coloured piece of a line, for $(...): gum leaves out colours when it does not
  # write to a terminal itself
  CLICOLOR_FORCE=1 "${myUI_GUM}" style --foreground "$1" -- "$2"
}

fuUI_BANNER () {
  # fuUI_BANNER <title> <line> ...: the t-pot wordmark (as in the splash of tpot) and a title
  local myTITLE="$1"
  shift
  if [ -z "${myUI_GUM}" ];
    then
      echo
      echo "### T-Pot ${myTITLE}"
      for myLINE in "$@"; do echo "### ${myLINE}"; done
      echo
      return
  fi
  echo
  "${myUI_GUM}" style --foreground "${myUI_MAGENTA}" --bold --margin "0 2" -- \
    "  ██                             ██" \
    "▀▀██▀▀         ██▀▀█▄  ▄█▀▀█▄  ▀▀██▀▀" \
    "  ██    ▀▀▀▀▀  ██  ██  ██  ██    ██" \
    "   ▀▀▀         ██▀▀▀    ▀▀▀▀      ▀▀▀"
  echo
  "${myUI_GUM}" style --foreground "${myUI_GLASS}" --bold --margin "0 2" -- "T-Pot ${myTITLE}"
  [ "$#" -gt 0 ] && "${myUI_GUM}" style --foreground "${myUI_ASH}" --margin "0 2" -- "$@"
  echo
}

fuUI_INFO () {
  if [ -n "${myUI_GUM}" ];
    then echo "$(fuUI_PAINT "${myUI_MAGENTA}" "⬢") $(fuUI_PAINT "${myUI_GLASS}" "$*")"
    else echo "### $*"
  fi
}

fuUI_OK () {
  if [ -n "${myUI_GUM}" ];
    then echo "$(fuUI_PAINT "${myUI_OK_COLOUR}" "✓") $*"
    else echo "### [OK] - $*"
  fi
}

fuUI_WARN () {
  if [ -n "${myUI_GUM}" ];
    then "${myUI_GUM}" style --foreground "${myUI_WARN_COLOUR}" -- "! $*"
    else echo "### [WARNING] - $*"
  fi
}

fuUI_ERROR () {
  if [ -n "${myUI_GUM}" ];
    then "${myUI_GUM}" style --foreground "${myUI_ERROR_COLOUR}" --bold -- "✗ $*" >&2
    else echo "### [ERROR] - $*" >&2
  fi
}

fuUI_HINT () {
  # commands or details below a message, indented
  local myLINE
  for myLINE in "$@"; do
    if [ -n "${myUI_GUM}" ];
      then "${myUI_GUM}" style --foreground "${myUI_ASH}" -- "    ${myLINE}"
      else echo "###   ${myLINE}"
    fi
  done
}

fuUI_CONFIRM () {
  # fuUI_CONFIRM <question> [yes] [no]: 0 for yes; reads y/n from stdin without gum
  local myANSWER=""
  if [ -n "${myUI_GUM}" ] && [ -t 0 ];
    then
      "${myUI_GUM}" confirm --affirmative "${2:-Yes}" --negative "${3:-No}" \
        --prompt.foreground "${myUI_GLASS}" --selected.background "${myUI_MAGENTA}" \
        --selected.foreground "${myUI_GLASS}" --unselected.background "${myUI_PETROL}" \
        --unselected.foreground "${myUI_GLASS}" -- "$1"
      return $?
  fi
  while [ "${myANSWER}" != "y" ] && [ "${myANSWER}" != "n" ]; do
    read -rp "### $1 (y/n) " myANSWER || return 1
  done
  [ "${myANSWER}" = "y" ]
}

fuUI_CHOOSE () {
  # fuUI_CHOOSE <header> <label:value> ...: prints the value of the choice
  local myHEADER="$1"
  shift
  if [ -n "${myUI_GUM}" ] && [ -t 0 ];
    then
      "${myUI_GUM}" choose --header "${myHEADER}" --label-delimiter ":" \
        --header.foreground "${myUI_GLASS}" --cursor.foreground "${myUI_MAGENTA}" \
        --item.foreground "${myUI_ASH}" --selected.foreground "${myUI_MAGENTA}" -- "$@"
      return $?
  fi
  local myI=1 myITEM myPICK
  echo "### ${myHEADER}" >&2
  for myITEM in "$@"; do
    echo "###   ${myI}) ${myITEM%%:*}" >&2
    myI=$((myI + 1))
  done
  while true; do
    read -rp "### Choice (1-$#): " myPICK || return 1
    if [[ "${myPICK}" =~ ^[0-9]+$ ]] && [ "${myPICK}" -ge 1 ] && [ "${myPICK}" -le "$#" ];
      then
        myITEM="${!myPICK}"
        echo "${myITEM#*:}"
        return 0
    fi
  done
}

fuUI_INPUT () {
  # fuUI_INPUT <prompt> [password]: prints what was typed
  local myVALUE=""
  if [ -n "${myUI_GUM}" ] && [ -t 0 ];
    then
      if [ "$2" = "password" ];
        then "${myUI_GUM}" input --password --header "$1" --header.foreground "${myUI_GLASS}" \
               --cursor.foreground "${myUI_MAGENTA}" --prompt "› " --prompt.foreground "${myUI_MAGENTA}" --placeholder ""
        else "${myUI_GUM}" input --header "$1" --header.foreground "${myUI_GLASS}" \
               --cursor.foreground "${myUI_MAGENTA}" --prompt "› " --prompt.foreground "${myUI_MAGENTA}" --placeholder ""
      fi
      return $?
  fi
  if [ "$2" = "password" ];
    then read -rsp "### $1 " myVALUE; echo >&2
    else read -rp "### $1 " myVALUE
  fi
  echo "${myVALUE}"
}

fuMARK () {
  # machine readable progress for tpot, one line each: @@tpot <what> <value ...>.
  # install.sh -M sets myMARKS, the task screen of tpot TPOT_MARKS=1
  [ -n "${myMARKS}" ] || [ "${TPOT_MARKS}" = "1" ] || return 0
  echo "@@tpot $*"
}

fuUI_SPIN () {
  # fuUI_SPIN <title> <log file> <command> ...: runs the command (a function works too)
  # in this shell with its output in the log file and a spinner meanwhile; shows the
  # end of the log on failure. It runs in the background, so it must not prompt:
  # refresh sudo before (sudo -v) where it needs a password.
  local myTITLE="$1" myLOG="$2"
  shift 2
  local myPID myRC
  if [ -n "${myUI_GUM}" ];
    then
      "$@" >>"${myLOG}" 2>&1 < /dev/null &
      myPID=$!
      "${myUI_GUM}" spin --spinner dot --spinner.foreground "${myUI_MAGENTA}" --title "${myTITLE}" \
        --title.foreground "${myUI_GLASS}" -- sh -c "while kill -0 ${myPID} 2>/dev/null; do sleep 0.2; done"
      wait "${myPID}"
      myRC=$?
    else
      echo "### ${myTITLE}"
      "$@" >>"${myLOG}" 2>&1 < /dev/null
      myRC=$?
  fi
  if [ "${myRC}" -eq 0 ];
    then fuUI_OK "${myTITLE%% ...}"
    else
      fuUI_ERROR "${myTITLE%% ...} failed, the end of ${myLOG}:"
      tail -n 15 "${myLOG}" >&2
  fi
  return "${myRC}"
}
# <<< tpot ui <<<

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

clone_matches() {
  # Is ~/tpotce at what was requested? A branch by name, a tag or a commit by the
  # commit it points to (those leave a detached HEAD without a name).
  myCLONE_BRANCH=$(git -C "${HOME}/tpotce" rev-parse --abbrev-ref HEAD 2>/dev/null)
  [ "${myCLONE_BRANCH}" = "${myTPOT_BRANCH}" ] && return 0
  myHEAD=$(git -C "${HOME}/tpotce" rev-parse HEAD 2>/dev/null)
  myWANT=$(git -C "${HOME}/tpotce" rev-parse -q --verify "${myTPOT_BRANCH}^{commit}" 2>/dev/null)
  [ -n "${myWANT}" ] && [ "${myWANT}" = "${myHEAD}" ]
}

check_tpot_clone() {
  # `update: no` in the playbook means Ansible keeps an existing ~/tpotce as it
  # is, without looking at the requested repository or branch - a test would
  # silently run against the previous checkout.
  [ -d "${HOME}/tpotce" ] || return
  if ! git -C "${HOME}/tpotce" rev-parse --is-inside-work-tree >/dev/null 2>&1;
    then
      fuUI_WARN "${HOME}/tpotce exists but is not a git repository, its origin cannot be verified."
      echo
      return
  fi
  myCLONE_REPO=$(normalize_repo "$(git -C "${HOME}/tpotce" remote get-url origin 2>/dev/null)")
  if ! clone_matches || [ "${myCLONE_REPO}" != "${myTPOT_REPO_URL}" ];
    then
      myCLONE_BRANCH=$(git -C "${HOME}/tpotce" rev-parse --abbrev-ref HEAD 2>/dev/null)
      [ "${myCLONE_BRANCH}" = "HEAD" ] && myCLONE_BRANCH=$(git -C "${HOME}/tpotce" rev-parse HEAD 2>/dev/null)
      fuUI_ERROR "${HOME}/tpotce already exists and does not match what was requested:"
      fuUI_HINT "found:     ${myCLONE_REPO} at ${myCLONE_BRANCH}" \
                "requested: ${myTPOT_REPO_URL} at ${myTPOT_BRANCH}"
      fuUI_INFO "T-Pot would be installed from the existing checkout. Remove it and run"
      fuUI_INFO "the installer again, or clone what you want to test into ${HOME}/tpotce:"
      fuUI_HINT "sudo rm -rf ${HOME}/tpotce"
      echo
      exit 1
  fi
}

clone_tpot() {
  # The assistant needs the repository before the playbook, which would clone it
  # at its end. It keeps this clone (update: no), check_tpot_clone made sure that
  # an existing one is the requested one.
  [ -d "${HOME}/tpotce/.git" ] && return 0
  git clone -q "${myTPOT_REPO_URL}" "${HOME}/tpotce" \
    && { [ "${myTPOT_BRANCH}" = "master" ] || git -C "${HOME}/tpotce" checkout -q "${myTPOT_BRANCH}"; }
}

sudo_password_required() {
  # `-k` ignores a cached credential: installing the packages refreshes the sudo
  # timestamp, so a plain `sudo -n true` would succeed on a password protected
  # system and Ansible would then fail once the timestamp expires in the middle
  # of the playbook.
  ! command sudo -n -k true > /dev/null 2>&1
}

sudo_rs_become_exe() {
  # Ubuntu 26.04 makes sudo-rs the active `sudo`. It wraps the prompt it is
  # given with `-p` into "[sudo: <prompt>] Password:", while Ansible waits for a
  # line that starts with its own prompt and gives up with "Timed out waiting
  # for become success or become password prompt". The fix landed in
  # ansible-core devel only, so point Ansible at the traditional sudo, which
  # Ubuntu still ships next to sudo-rs. Only needed where a become password is
  # entered, passwordless sudo never shows a prompt.
  if [ -n "${myANSIBLE_BECOME_EXE}" ];
    then
      return
  fi
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

abort_unattended() {
  fuUI_ERROR "‘sudo‘ requires a password, so -s cannot be honoured."
  fuUI_INFO "Either hand the password over with -B <file>, configure passwordless sudo"
  fuUI_INFO "for ${myUSER}, e.g."
  fuUI_HINT "echo '${myUSER} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/${myUSER}"
  fuUI_INFO "or run the installer without -s and enter the password when asked."
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
        myPROC=$(sudo -n ss -H -lnp --"${myPROTO}" "sport = :${myPORT}" 2>/dev/null \
                 | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' \
                 | head -n 1)
    fi
    # The playbook turns the resolved stub listener off, so it is not a blocker.
    # `ss` truncates process names to 15 characters, hence systemd-resolve.
    if [ "${myPROC}" = "systemd-resolve" ];
      then
        continue
    fi
    fuUI_ERROR "${myPROTO}/${myPORT} is occupied by ${myPROC:-an unidentified process}"
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

install_packages() {
  # the packages the installer needs, per distribution; runs in the background of
  # fuUI_SPIN, so sudo has to be refreshed before (it cannot prompt here)
  case ${myCURRENT_DISTRIBUTION} in
    "Fedora Linux")
      sudo dnf -y --refresh install ${myPACKAGES_FEDORA}
      ;;
    "Debian GNU/Linux"|"Raspbian GNU/Linux"|"Ubuntu")
      sudo apt update && sudo NEEDRESTART_SUSPEND=1 apt install -y ${myPACKAGES_DEBIAN}
      ;;
    "openSUSE Tumbleweed")
      sudo zypper refresh && sudo zypper install -y ${myPACKAGES_OPENSUSE} \
        && echo "export ANSIBLE_PYTHON_INTERPRETER=/bin/python3" | sudo tee /etc/profile.d/ansible.sh >/dev/null
      ;;
    "AlmaLinux"|"Rocky Linux")
      sudo dnf -y --refresh install ${myPACKAGES_ROCKY} && ansible-galaxy collection install ansible.posix
      ;;
    "Red Hat Enterprise Linux")
      echo "RHEL detected - configuring version and Ansible repo strings"
      rhel_version && rhel_ansible_repo || return 1
      sudo yum -y update || return 1
      # extra repo required for EPEL on RHEL
      sudo subscription-manager repos --enable codeready-builder-for-rhel-"$myRHEL_VERSION"-$(arch)-rpms || return 1
      # epel installer is not standard on RHEL
      sudo dnf -y install https://dl.fedoraproject.org/pub/epel/epel-release-latest-"$myRHEL_VERSION".noarch.rpm || return 1
      # ansible comes from rhel subscription manager
      sudo subscription-manager repos --enable "$(rhel_ansible_repo)" || return 1
      sudo dnf -y --refresh install ${myPACKAGES_RHEL} && ansible-galaxy collection install ansible.posix
      ;;
  esac
}

install_sudo_debian() {
  # Debian without sudo: install it with the root password, add the user. Not in
  # the background, su asks for the password in the terminal.
  fuUI_WARN "‘sudo‘ is not installed. To continue you need to provide the ‘root‘ password"
  fuUI_INFO "or press CTRL-C to manually install ‘sudo‘ and add your user to the sudoers."
  echo
  # Ansible cannot be handed a become password by -s, so an unattended
  # run needs a passwordless rule for the user we are about to add.
  if [ "${myUNATTENDED}" = "y" ] && [ -z "${myBECOME_FILE}" ];
    then
      mySUDOERS_RULE="${myUSER} ALL=(ALL) NOPASSWD:ALL"
      fuUI_INFO "‘-s‘ was given, so ${myUSER} will get passwordless sudo."
      fuUI_INFO "Remove /etc/sudoers.d/${myUSER} after the installation to undo it."
      echo
    else
      mySUDOERS_RULE="${myUSER} ALL=(ALL:ALL) ALL"
  fi
  su -c "apt -y update && \
         NEEDRESTART_SUSPEND=1 apt -y install sudo ${myPACKAGES_DEBIAN} && \
         /usr/sbin/usermod -aG sudo ${myUSER} && \
         echo '${mySUDOERS_RULE}' | tee /etc/sudoers.d/${myUSER} >/dev/null && \
         chmod 440 /etc/sudoers.d/${myUSER}" || exit 1
  fuUI_INFO "We need sudo for Ansible, please enter the sudo password ..."
  sudo echo "### ... sudo works. Note that Ansible needs it without a password prompt, see below."
  echo
}

get_packages() {
  echo
  fuMARK phase packages
  if [ -n "${TPOT_INSTALL_PACKAGES_DONE}" ];
    then
      # the assistant comes after the bootstrap, which installed them already
      fuUI_OK "The packages the installer needs are there."
      return
  fi
  if [[ "${myCURRENT_DISTRIBUTION}" =~ ^(Debian\ GNU/Linux|Raspbian\ GNU/Linux|Ubuntu)$ ]] && ! command -v sudo >/dev/null;
    then
      install_sudo_debian
      return
  fi
  # a password is asked for now, in the terminal, the spinner cannot ask
  if [ -z "${myBECOME_FILE}" ] && sudo_password_required;
    then
      fuUI_INFO "sudo needs your password to install the packages:"
      sudo -v || exit 1
  fi
  fuUI_SPIN "${myINSTALL_NOTIFICATION}" "${myLOG}" install_packages || exit 1
  if [ "${myCURRENT_DISTRIBUTION}" = "openSUSE Tumbleweed" ];
    then
      source /etc/profile.d/ansible.sh
  fi
  echo
}

check_ports () {
  # Abort before anything is installed if a service holds a port a honeypot needs.
  # The warning at the end of this script comes too late to act on, and an
  # unattended run cannot act on it at all.
  fuMARK phase checks
  if ! command -v ss >/dev/null;
    then
      fuUI_ERROR "‘ss‘ was not found, so the check for conflicting services cannot run."
      fuUI_INFO "Install it and run the installer again:"
      fuUI_HINT "Debian, Raspbian, Ubuntu:       sudo apt install iproute2" \
                "AlmaLinux, Fedora, RHEL, Rocky: sudo dnf install iproute" \
                "openSUSE Tumbleweed:            sudo zypper install iproute2"
      echo
      exit 1
  fi
  fuUI_INFO "Now checking for services on ports T-Pot needs ..."
  check_port_conflicts
  if [ "${myPORT_CONFLICT}" = "y" ];
    then
      fuUI_INFO "T-Pot publishes these ports for its honeypots, so a clean installation"
      fuUI_INFO "is required. Identify and disable the services, then run the installer"
      fuUI_INFO "again:"
      fuUI_HINT "sudo ss -lntup" \
                "sudo systemctl list-sockets    # for a process that reads ‘systemd‘" \
                "sudo systemctl disable --now <unit>"
      echo
      exit 1
    else
      fuUI_OK "No services found on ports T-Pot needs."
      echo
  fi
}

read_password_file() {
  # -P <file>: the first line, - reads stdin
  if [ "$1" = "-" ];
    then IFS= read -r myWEB_PW
    else IFS= read -r myWEB_PW < "$1" || [ -n "${myWEB_PW}" ] || {
      echo "Error: cannot read the password from $1."
      exit 1
    }
  fi
}

password_weakness() {
  # empty when the password is fine; cracklib if it is there (not before the
  # packages), else at least 12 characters, as tpot users does
  local myCHECK
  for myCHECK in /usr/sbin/cracklib-check /usr/bin/cracklib-check "$(command -v cracklib-check 2>/dev/null)"; do
    if [ -n "${myCHECK}" ] && [ -x "${myCHECK}" ];
      then
        printf "%s" "$1" | "${myCHECK}" | grep -q ": OK$" || printf "%s" "$1" | "${myCHECK}" | sed 's/^.*: //'
        return
    fi
  done
  [ "${#1}" -ge 12 ] || echo "shorter than 12 characters"
}

ask_type() {
  # the classic questions, before anything is installed
  [ -n "${myTPOT_TYPE}" ] && return
  myTPOT_TYPE=$(fuUI_CHOOSE "Choose your T-Pot type:" \
    "Hive - T-Pot Standard / HIVE, everything incl. what a distributed setup needs:h" \
    "Sensor - honeypots only, sends its data to a HIVE (no web UI, no Elastic Stack):s" \
    "LLM - LLM based honeypots Beelzebub and Galah, needs Ollama or ChatGPT:l" \
    "Mini - 30+ honeypots with just a couple of honeypot daemons:i" \
    "Mobile - everything to run T-Pot Mobile (available separately):m" \
    "Tarpit - feeds data endlessly to attackers, bots and scanners, with ddospot:t") || exit 1
  validate_type
}

ask_web_user() {
  [[ "${myTPOT_TYPE}" =~ ^[hlit]$ ]] || return
  echo
  fuUI_INFO "T-Pot User Configuration ..."
  while [ -z "${myWEB_USER}" ]; do
    myWEB_USER=$(fuUI_INPUT "Enter your web user name:")
    myWEB_USER=$(echo "${myWEB_USER}" | tr -cd "[:alnum:]_.-")
    [ -n "${myWEB_USER}" ] && ! fuUI_CONFIRM "Your username is ${myWEB_USER}, is this correct?" && myWEB_USER=""
  done
  while [ -z "${myWEB_PW}" ]; do
    myWEB_PW=$(fuUI_INPUT "Enter password for your web user:" password)
    [ -z "${myWEB_PW}" ] && continue
    myWEB_PW2=$(fuUI_INPUT "Repeat password for your web user:" password)
    if [ "${myWEB_PW}" != "${myWEB_PW2}" ];
      then
        fuUI_WARN "Passwords do not match."
        myWEB_PW=""
        continue
    fi
    myWEAK=$(password_weakness "${myWEB_PW}")
    if [ -n "${myWEAK}" ] && ! fuUI_CONFIRM "The password is weak (${myWEAK}). Keep it anyway?";
      then
        myWEB_PW=""
    fi
  done
}

# Defaults
myQST=""
myUNATTENDED=""
myTPOT_TYPE=""
myWEB_USER=""
myWEB_PW=""
myWEB_PW_FILE=""
myBECOME_FILE=""
myCUSTOM_COMPOSE=""
myCLASSIC=""
myMARKS=""
# Ansible become executable, empty means the Ansible default. See
# sudo_rs_become_exe.
myANSIBLE_BECOME_EXE=""
# Where to install T-Pot from. Empty means: work it out in resolve_tpot_source.
myTPOT_BRANCH="${TPOT_BRANCH}"
myTPOT_REPO_URL="${TPOT_REPO_URL}"

while getopts ":sb:r:t:u:p:P:B:c:nMh" opt; do
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
    P)
      myWEB_PW_FILE="${OPTARG}"
      ;;
    B)
      myBECOME_FILE="${OPTARG}"
      ;;
    c)
      myCUSTOM_COMPOSE="${OPTARG}"
      ;;
    n)
      myCLASSIC="y"
      ;;
    M)
      # for the assistant: marks on stdout, no terminal behind them
      myMARKS="y"
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

[ -n "${myWEB_PW_FILE}" ] && read_password_file "${myWEB_PW_FILE}"

# -s requires -t
if [[ "$myUNATTENDED" == "y" && -z "$myTPOT_TYPE" ]]; then
  echo "Error: -t is required when using -s to suppress interaction."
  print_help
fi

# Determine if user/pass are required based on install type
if [[ "$myUNATTENDED" == "y" && "$myTPOT_TYPE" =~ ^[hlit]$ ]]; then
  [[ -n "$myWEB_USER" && -n "$myWEB_PW" ]] || {
    echo "Error: -u and -p (or -P) are required for installation type '$myTPOT_TYPE'."
    print_help
  }
fi

if [ -n "${myCUSTOM_COMPOSE}" ];
  then
    [[ "${myTPOT_TYPE}" =~ ^[hs]$ ]] || [ -z "${myTPOT_TYPE}" ] || {
      echo "Error: with -c the type is h (a HIVE) or s (a SENSOR)."
      print_help
    }
    [ -f "${myCUSTOM_COMPOSE}" ] || { echo "Error: ${myCUSTOM_COMPOSE} does not exist."; exit 1; }
    myCUSTOM_COMPOSE=$(cd "$(dirname "${myCUSTOM_COMPOSE}")" && pwd)/$(basename "${myCUSTOM_COMPOSE}")
fi

if [ -n "${myBECOME_FILE}" ];
  then
    [ -r "${myBECOME_FILE}" ] || { echo "Error: cannot read the sudo password from ${myBECOME_FILE}."; exit 1; }
    myBECOME_FILE=$(cd "$(dirname "${myBECOME_FILE}")" && pwd)/$(basename "${myBECOME_FILE}")
    # every sudo of this script refreshes the timestamp from the file first, so none
    # of them prompts and pipes into sudo (tee) keep working
    sudo () { command sudo -S -p "" -v < "${myBECOME_FILE}" >/dev/null 2>&1; command sudo "$@"; }
fi

resolve_tpot_source

myINSTALL_NOTIFICATION="Installing the packages the installer needs ..."
myUSER=$(whoami)
myLOG="${HOME}/install_tpot_prepare.log"
myTPOT_CONF_FILE="${HOME}/tpotce/.env"
# git and the Python venv module: the assistant (tpot install) runs before the
# playbook, from a clone the installer makes itself
myPACKAGES_DEBIAN="ansible apache2-utils cracklib-runtime git python3-venv wget"
myPACKAGES_FEDORA="ansible cracklib git httpd-tools python3 wget"
myPACKAGES_ROCKY="ansible-core epel-release cracklib git httpd-tools python3 wget"
myPACKAGES_RHEL="ansible-core ansible-collection-redhat-rhel_mgmt cracklib git httpd-tools python3 wget"
myPACKAGES_OPENSUSE="ansible apache2-utils cracklib git python3 wget"
# Ports a honeypot needs that a distribution service is likely to hold. A
# service on 127.0.0.1 conflicts with a container publishing the same port on
# 0.0.0.0, so a loopback listener counts as well.
myCONFLICT_PORTS="tcp/25 tcp/53 udp/53"

fuUI_INIT

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

if [[ ! " ${mySUPPORTED_DISTRIBUTIONS[@]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    fuUI_ERROR "Only the following distributions are supported: AlmaLinux, Fedora, Debian, openSUSE Tumbleweed, RHEL, Rocky Linux and Ubuntu."
    fuUI_INFO "Please follow the T-Pot documentation on how to run T-Pot on macOS, Windows and other currently unsupported platforms."
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
    fuUI_ERROR "T-Pot supports ${myCURRENT_DISTRIBUTION} ${mySUPPORTED_VERSION}, this system runs ${myCURRENT_VERSION}."
    fuUI_INFO "Please install T-Pot on the current release of your distribution."
    echo
    exit 1
fi

# Begin of Installer
[ -z "${myMARKS}" ] && fuUI_BANNER "Installer" "This script will now install T-Pot and all of its dependencies." \
  "Source: ${myTPOT_REPO_URL} at ${myTPOT_BRANCH}" "${myCURRENT_DISTRIBUTION} ${myVERSION_ID}"

# A person at a terminal gets the assistant: the installer only gets what it needs
# to start (packages, the repository, the Python packages of tpot) and hands over.
# tpot install asks everything up front and runs this script again with -s.
if [ -z "${myUNATTENDED}" ] && [ -z "${myCLASSIC}" ] && [ -t 0 ] && [ -t 1 ] && [ "${TPOT_ASSISTANT:-on}" != "off" ];
  then
    if ! fuUI_CONFIRM "Start the T-Pot installer? It first installs git, Ansible and Python packages it needs." "Start" "Abort";
      then
        echo
        fuUI_INFO "Aborting!"
        echo
        exit 0
    fi
    check_tpot_clone
    check_ports
    get_packages
    if ! fuUI_SPIN "Getting T-Pot from ${myTPOT_REPO_URL} at ${myTPOT_BRANCH} ..." "${myLOG}" clone_tpot;
      then
        exit 1
    fi
    if fuUI_SPIN "Setting up the T-Pot installer ..." "${myLOG}" "${HOME}/tpotce/tpot" setup \
       && "${HOME}/tpotce/tpot" install --help >/dev/null 2>&1;
      then
        echo
        export TPOT_BRANCH="${myTPOT_BRANCH}" TPOT_REPO_URL="${myTPOT_REPO_URL}" TPOT_INSTALL_PACKAGES_DONE=1
        exec "${HOME}/tpotce/tpot" install
    fi
    fuUI_WARN "The assistant cannot start (see ${myLOG}), the installer asks in the terminal instead."
    export TPOT_INSTALL_PACKAGES_DONE=1
    myQST="y"
fi

if [[ -z "$myQST" ]]; then
  if ! fuUI_CONFIRM "Install?";
    then
      echo
      fuUI_INFO "Aborting!"
      echo
      exit 0
  fi
fi

# The questions come before anything is installed, the rest runs on its own.
ask_type
ask_web_user

# Fail before anything is installed if an existing ~/tpotce would be used
# instead of the repository and branch that were asked for.
check_tpot_clone

# A sudo password from -B has to work, a typo would only show in the playbook
if [ -n "${myBECOME_FILE}" ] && command -v sudo >/dev/null && ! command sudo -S -k -p "" -v < "${myBECOME_FILE}" >/dev/null 2>&1;
  then
    fuUI_ERROR "The sudo password in ${myBECOME_FILE} is not accepted."
    echo
    exit 1
fi

# Fail before anything is installed: -s promises an unattended run, but Ansible
# would ask for the become password. Only possible where sudo already exists -
# the Debian branch below installs it and the check is repeated afterwards.
if [ "${myUNATTENDED}" = "y" ] && [ -z "${myBECOME_FILE}" ] && command -v sudo >/dev/null && sudo_password_required;
  then
    abort_unattended
fi

# Same here: a become password will be needed, so settle the sudo-rs question
# before anything is installed. On the Debian branch below sudo may not exist
# yet, the check is repeated with the become option further down.
if command -v sudo >/dev/null && sudo_password_required;
  then
    sudo_rs_become_exe
fi

check_ports
# Install packages based on the distribution
get_packages

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

# The playbook comes from the clone of the repository, made now unless it is there
# already (the assistant and a local clone have it). The playbook keeps it.
if [ ! -f "${HOME}/tpotce/installer/install/tpot.yml" ];
  then
    if ! fuUI_SPIN "Getting T-Pot from ${myTPOT_REPO_URL} at ${myTPOT_BRANCH} ..." "${myLOG}" clone_tpot;
      then
        # a mistyped branch or repository ends up here, and would fail with a
        # confusing Ansible error further down
        fuUI_INFO "Check the repository and the branch, then run the installer again."
        echo
        exit 1
    fi
fi
myANSIBLE_TPOT_PLAYBOOK="${HOME}/tpotce/installer/install/tpot.yml"
echo

# Check type of sudo access. Applies to every distribution - making an
# exception for one of them breaks unattended installation there.
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
    # -s promises an unattended run, and --ask-become-pass would prompt. On the
    # Debian branch sudo may have been installed after the check above.
    if [ "${myUNATTENDED}" = "y" ];
      then
        abort_unattended
    fi
    sudo_rs_become_exe
    myANSIBLE_BECOME_OPTION="--become --ask-become-pass"
    fuUI_INFO "‘sudo‘ requires a password, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    fuUI_INFO "Ansible will ask for the ‘BECOME password‘ which is typically the password you ’sudo’ with."
    echo
fi

# Run Ansible Playbook
fuUI_INFO "Now running T-Pot Ansible Installation Playbook ..."
echo
rm ${HOME}/install_tpot.log > /dev/null 2>&1
# neither a repository URL nor a git reference contains a space, so the
# unquoted expansion below splits into exactly four arguments
myANSIBLE_EXTRA_VARS="-e tpot_repo=${myTPOT_REPO_URL} -e tpot_branch=${myTPOT_BRANCH}"
if [ -n "${myMARKS}" ];
  then
    myTASKS=$(ANSIBLE_INJECT_FACT_VARS=False ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local \
              --tags "${myANSIBLE_TAG}" --list-tasks 2>/dev/null | grep -c "TAGS: \[")
    fuMARK tasks "${myTASKS}"
fi
fuMARK phase playbook
# INJECT_FACTS_AS_VARS=False: the playbooks read facts as ansible_facts.<name>,
# the auto injected top-level copies are deprecated and gone in ansible-core
# 2.24. Setting it explicitly makes a leftover fail here and now instead of
# silently working until then, and it keeps the deprecation warnings out of the
# log. PYTHON_INTERPRETER=auto_silent keeps the interpreter discovery hint out,
# the discovery itself is unchanged.
ANSIBLE_INJECT_FACT_VARS=False ANSIBLE_PYTHON_INTERPRETER=auto_silent \
ANSIBLE_LOG_PATH=${HOME}/install_tpot.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local --tags "${myANSIBLE_TAG}" ${myANSIBLE_BECOME_OPTION} ${myANSIBLE_BECOME_EXE} ${myANSIBLE_EXTRA_VARS}

# Something went wrong
if [ ! $? -eq 0 ];
  then
    fuMARK phase failed
    fuUI_ERROR "Something went wrong with the Playbook, please review the output and / or install_tpot.log for clues."
    fuUI_INFO "Aborting."
    echo
    exit 1
  else
    fuUI_OK "Playbook was successful."
    echo
fi

# The T-Pot type, asked before the playbook (or given with -t / -c)
fuMARK phase compose
case "${myTPOT_TYPE}" in
  h) myTPOT_TYPE="HIVE";   myEDITION="standard"; myINFO="" ;;
  s) myTPOT_TYPE="SENSOR"; myEDITION="sensor"
     myINFO="Make sure to deploy SSH keys to this SENSOR and disable SSH password authentication.
On the HIVE run 'tpot sensors add' to join this SENSOR to the HIVE." ;;
  l) myTPOT_TYPE="HIVE";   myEDITION="llm";    myINFO="Make sure to adjust the T-Pot config file (.env) for Ollama / ChatGPT settings, i.e. with 'tpot'." ;;
  i) myTPOT_TYPE="HIVE";   myEDITION="mini";   myINFO="" ;;
  m) myTPOT_TYPE="MOBILE"; myEDITION="mobile"; myINFO="" ;;
  t) myTPOT_TYPE="HIVE";   myEDITION="tarpit"; myINFO="" ;;
esac
if [ -n "${myCUSTOM_COMPOSE}" ];
  then
    fuUI_INFO "Installing your own compose file ${myCUSTOM_COMPOSE}."
    [ "${myCUSTOM_COMPOSE}" = "${HOME}/tpotce/docker-compose.yml" ] || cp "${myCUSTOM_COMPOSE}" "${HOME}/tpotce/docker-compose.yml"
  else
    fuUI_INFO "Installing T-Pot ${myEDITION}."
    cp "${HOME}/tpotce/compose/${myEDITION}.yml" "${HOME}/tpotce/docker-compose.yml"
fi

if [ "${myTPOT_TYPE}" == "HIVE" ];
  # If T-Pot Type is HIVE write the WebUI username and password
  then
    fuMARK phase user
    fuUI_INFO "Creating the web user ${myWEB_USER} in ${myTPOT_CONF_FILE}"
    # bcrypt, as `tpot users` creates them; the password goes through stdin, not argv
    myWEB_USER_ENC=$(printf "%s" "${myWEB_PW}" | htpasswd -n -i -B "${myWEB_USER}")
    myWEB_USER_ENC_B64=$(echo -n "${myWEB_USER_ENC}" | base64 -w0)
    sed -i "s|^WEB_USER=.*|WEB_USER=${myWEB_USER_ENC_B64}|" ${myTPOT_CONF_FILE}
    echo
fi

# Pull docker images
fuMARK phase pull
if [ -n "${myMARKS}" ];
  then
    fuMARK images "$(sudo docker compose -f "${HOME}/tpotce/docker-compose.yml" config --images 2>/dev/null | wc -l)"
fi
fuUI_INFO "Now pulling images ..."
if ! sudo docker compose -f "${HOME}/tpotce/docker-compose.yml" pull;
  then
    # not a stop: T-Pot pulls what is missing when it starts (TPOT_PULL_POLICY)
    fuMARK warn pull
    fuUI_WARN "Not all images could be pulled (see above), T-Pot tries again when it starts."
fi
echo

# Show running services
if [ -z "${myMARKS}" ];
  then
    fuUI_INFO "Please review for possible honeypot port conflicts."
    fuUI_INFO "While SSH is taken care of, other services such as"
    fuUI_INFO "SMTP, HTTP, etc. might prevent T-Pot from starting."
    echo
    sudo grc netstat -tulpen
    echo
fi

# The tpot command: the playbook links it, a file of your own at /usr/local/bin/tpot stays
myTPOT_FOUND=$(command -v tpot)
if [ -n "${myTPOT_FOUND}" ] && [ "$(readlink -f "${myTPOT_FOUND}")" = "$(readlink -f "${HOME}/tpotce/tpot")" ];
  then
    fuUI_OK "The T-Pot Manager is ready, run it with: tpot"
  elif [ -n "${myTPOT_FOUND}" ];
  then
    fuUI_WARN "The command tpot is ${myTPOT_FOUND}, not this T-Pot Manager. Run ${HOME}/tpotce/tpot, or link it with: sudo ln -sfn ${HOME}/tpotce/tpot /usr/local/bin/tpot"
  else
    fuUI_WARN "The command tpot is not in your PATH, link it with: sudo ln -sfn ${HOME}/tpotce/tpot /usr/local/bin/tpot"
fi

# Done
fuMARK phase "done"
fuUI_OK "Done. Please reboot and re-connect via SSH on tcp/64295."
[ -n "${myINFO}" ] && fuUI_INFO "${myINFO}"
echo
