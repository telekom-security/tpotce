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
    fuUI_BANNER () { local myLINE; echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE//$'\n'/$'\n'### }"; done; echo; }
    fuUI_INFO () { local myTEXT="$*"; echo "### ${myTEXT//$'\n'/$'\n'### }"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuUI_CONFIRM () {
      local myANSWER="" myDEFAULT="" myPROMPT="(y/n)"
      if [ "${1:-}" = "--default" ]; then
        case "${2:-}" in yes|no) myDEFAULT="$2" ;; esac
        shift $(( $# < 2 ? $# : 2 ))
      fi
      case "${myDEFAULT}" in yes) myPROMPT="(Y/n)" ;; no) myPROMPT="(y/N)" ;; esac
      while true; do
        read -rp "### $1 ${myPROMPT} " myANSWER || return 1
        [ -n "${myANSWER}" ] || myANSWER="${myDEFAULT:0:1}"
        case "${myANSWER}" in y) return 0 ;; n) return 1 ;; esac
      done
    }
    fuUI_INPUT () {
      local myVALUE=""
      if [ "${2:-}" = "password" ];
        then read -rsp "### $1 " myVALUE; echo >&2
        else read -rp "### $1 " myVALUE
      fi
      echo "${myVALUE}"
    }
    fuUI_HELP () {
      local myTITLE="$1" myUSAGE="$2" myW=0 myI myLINE myFIRST myREST myPAD
      shift 2
      local -a myABOUT=() myFLAGS=() myTEXTS=() myCMDS=() myCTEXTS=() myNOTES=()
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --about) myABOUT+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
          --opt) myFLAGS+=("${2:-}"); myTEXTS+=("${3:-}"); shift $(( $# < 3 ? $# : 3 )) ;;
          --example) myCMDS+=("${2:-}"); myCTEXTS+=("${3:-}"); shift $(( $# < 3 ? $# : 3 )) ;;
          --note) myNOTES+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
          *) shift ;;
        esac
      done
      for myI in "${!myFLAGS[@]}"; do
        if [ "${#myFLAGS[myI]}" -gt "${myW}" ] && [ "${#myFLAGS[myI]}" -le 24 ]; then myW="${#myFLAGS[myI]}"; fi
      done
      printf 'T-Pot %s\n\nUsage: %s\n' "${myTITLE}" "${myUSAGE//$'\n'/$'\n'       }"
      for myLINE in "${myABOUT[@]}"; do printf '\n%s\n' "${myLINE}"; done
      if [ "${#myFLAGS[@]}" -gt 0 ]; then
        printf '\nOptions:\n'
        printf -v myPAD '%*s' $((myW + 6)) ''
        for myI in "${!myFLAGS[@]}"; do
          myFIRST="${myTEXTS[myI]%%$'\n'*}"
          myREST=""
          [ "${myFIRST}" = "${myTEXTS[myI]}" ] || myREST="${myTEXTS[myI]#*$'\n'}"
          if [ "${#myFLAGS[myI]}" -le "${myW}" ];
            then printf '  %s%*s    %s\n' "${myFLAGS[myI]}" $((myW - ${#myFLAGS[myI]})) '' "${myFIRST}"
            else printf '  %s\n%s%s\n' "${myFLAGS[myI]}" "${myPAD}" "${myFIRST}"
          fi
          [ -z "${myREST}" ] || printf '%s%s\n' "${myPAD}" "${myREST//$'\n'/$'\n'${myPAD}}"
        done
      fi
      if [ "${#myCMDS[@]}" -gt 0 ]; then
        printf '\nExamples:\n'
        for myI in "${!myCMDS[@]}"; do
          printf '  %s\n' "${myCMDS[myI]}"
          [ -z "${myCTEXTS[myI]}" ] || printf '      %s\n' "${myCTEXTS[myI]//$'\n'/$'\n'      }"
        done
      fi
      if [ "${#myNOTES[@]}" -gt 0 ]; then
        printf '\nNotes:\n'
        for myLINE in "${myNOTES[@]}"; do printf '  %s\n' "${myLINE//$'\n'/$'\n'  }"; done
      fi
      return 0
    }
    fuUI_USAGE_ERROR () {
      echo "### [ERROR] - $1" >&2
      echo "###   ${2:-${0##*/}} -h shows the options." >&2
      return 1
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
# The options of tpot sensors add (the same as its parser, test_scripts_deploy.py checks it): a wrong
# one is a usage error with exit 1 before anything is shown, as in the other T-Pot scripts
myTPOT_FLAGS="--no-become-pass -y --yes"
myTPOT_VALUES="--host --ssh-user --ssh-port --hive-address"
myTPOT_NUMBERS="--ssh-port"
myTPOT_NAMES=0
fuCHECK_OPTIONS () {
  # fuCHECK_OPTIONS <argument ...>: the options of tpot sensors add, myTPOT_FLAGS without a value, myTPOT_VALUES
  # with one (myTPOT_NUMBERS a number), myTPOT_NAMES arguments at most, -- before arguments that start with -.
  # A wrong one ends the script with a usage error (exit 1), before anything is shown
  local myARG myKEY myVALUE myCOUNT=0 myREST=""
  while [ "$#" -gt 0 ]; do
    myARG="$1"
    shift
    if [ -z "${myREST}" ];
      then
        case " ${myTPOT_FLAGS} -h --help " in
          *" ${myARG} "*) continue ;;
        esac
        myKEY="${myARG%%=*}"
        case " ${myTPOT_VALUES} " in
          *" ${myKEY} "*)
            myVALUE="${myARG#*=}"
            if [ "${myKEY}" = "${myARG}" ];
              then
                if [ "$#" -eq 0 ] || [[ "$1" == -* ]];
                  then
                    fuUI_USAGE_ERROR "Option ${myKEY} requires a value." "deploy.sh"
                    exit 1
                fi
                myVALUE="$1"
                shift
            fi
            case " ${myTPOT_NUMBERS} " in
              *" ${myKEY} "*)
                if ! [[ "${myVALUE}" =~ ^[0-9]+$ ]];
                  then
                    fuUI_USAGE_ERROR "${myKEY} takes a number, not ${myVALUE}." "deploy.sh"
                    exit 1
                fi ;;
            esac
            continue ;;
        esac
        case "${myARG}" in
          --) myREST="y"; continue ;;
          -?*) fuUI_USAGE_ERROR "Unknown option ${myARG}." "deploy.sh"; exit 1 ;;
        esac
    fi
    myCOUNT=$((myCOUNT + 1))
    if [ "${myCOUNT}" -gt "${myTPOT_NAMES}" ];
      then
        fuUI_USAGE_ERROR "Unexpected argument ${myARG}." "deploy.sh"
        exit 1
    fi
  done
}

fuCHECK_OPTIONS "$@"
myHELP=""
case " $* " in
  *" -h "*|*" --help "*) myHELP="y" ;;
esac
myTPOT="$HOME/tpotce/tpot"
fuTPOT_READY () {
  # fuTPOT_READY: 0 when tpot runs here (its Python packages are set up, or can be now)
  [ -x "${myTPOT}" ] && "${myTPOT}" setup > /dev/null 2>&1
}
# options and arguments are for tpot: without it they are a usage error, before the logo
myREADY=""
if [ -z "${myHELP}" ] && [ "$#" -gt 0 ];
  then
    if ! fuTPOT_READY;
      then
        fuUI_USAGE_ERROR "${1} needs tpot sensors add, which cannot be set up here." "deploy.sh"
        exit 1
    fi
    myREADY="y"
fi
# a person at a terminal sees the T-Pot logo and what comes, then tpot asks; not for the help
myBANNER=""
if [ -z "${myHELP}" ] && [ -t 0 ] && [ -t 1 ];
  then
    # shellcheck disable=SC2034 # fuUI_BANNER of installer/lib/ui.sh reads it
    myUI_LOGO=1
    fuUI_BANNER "Sensor deploy" "Joins a T-Pot SENSOR to this HIVE, it sends its logs here."
    myBANNER="y"
fi
if [ -n "${myREADY}" ] || fuTPOT_READY;
  then
    exec "${myTPOT}" sensors add "$@"
fi
# without tpot: the steps below, its help says so
if [ -n "${myHELP}" ];
  then
    fuUI_HELP "Sensor deploy" "deploy.sh [-h]" \
      --about "Joins a T-Pot SENSOR to this HIVE, it sends its logs here: deploy.sh asks
for the SENSOR, creates its access and runs the deployment playbook on it." \
      --about "This is tpot sensors add of the T-Pot Manager. Where tpot cannot be set up
(its Python packages need the internet), deploy.sh asks and runs the steps
itself." \
      --opt "-h, --help" "Show this help" \
      --note "tpot sensors add -h shows the options of the T-Pot Manager. It checks SSH
and the certificate first and takes the access back if a step fails."
    exit 0
fi
fuUI_WARN "tpot is not available, using the previous deployment."
cd "$HOME/tpotce" || exit 1

myANSIBLE_PORT=64295
myANSIBLE_TPOT_PLAYBOOK="installer/install/deploy.yml"
myADJECTIVE=$(shuf -n1 installer/install/a.txt)
myNOUN=$(shuf -n1 installer/install/n.txt)
myENV_FILE="$HOME/tpotce/.env"
myLSWEBPASSWD="$HOME/tpotce/data/nginx/conf/lswebpasswd"

fuTRIM () {
  # fuTRIM <text>: the text without the spaces around it
  local myTEXT="$1"
  myTEXT="${myTEXT#"${myTEXT%%[![:space:]]*}"}"
  printf '%s\n' "${myTEXT%"${myTEXT##*[![:space:]]}"}"
}

fuIS_IPV4 () {
  # fuIS_IPV4 <text>: 0 for an IPv4 address, four numbers of 0-255 without leading zeros
  local LC_ALL=C myPART
  local -a myPARTS=()
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
  IFS=. read -r -a myPARTS <<< "$1"
  for myPART in "${myPARTS[@]}"; do
    if ! [[ "${myPART}" =~ ^(0|[1-9][0-9]*)$ ]] || [ "${myPART}" -gt 255 ]; then return 1; fi
  done
  return 0
}

fuIS_IPV6 () {
  # fuIS_IPV6 <text>: 0 for an IPv6 address: groups of 1-4 hex digits, :: once at most for the
  # groups of zeros, an IPv4 address in the last two groups
  local LC_ALL=C myADDRESS="$1" myHALF myCOLONS myGROUPS=0 myMAX=8
  local myGROUP='[0-9A-Fa-f]{1,4}'
  [[ "${myADDRESS}" == *:* ]] || return 1
  if [[ "${myADDRESS}" =~ ^(.*:)([0-9]+\.[0-9.]+)$ ]];
    then
      myADDRESS="${BASH_REMATCH[1]}0:0"
      fuIS_IPV4 "${BASH_REMATCH[2]}" || return 1
  fi
  local myHEAD="${myADDRESS}" myTAIL=""
  if [[ "${myADDRESS}" == *::* ]];
    then
      myHEAD="${myADDRESS%%::*}" myTAIL="${myADDRESS#*::}" myMAX=7
  fi
  for myHALF in "${myHEAD}" "${myTAIL}"; do
    [ -n "${myHALF}" ] || continue
    [[ "${myHALF}" =~ ^${myGROUP}(:${myGROUP})*$ ]] || return 1
    myCOLONS="${myHALF//[^:]/}"
    myGROUPS=$((myGROUPS + ${#myCOLONS} + 1))
  done
  if [ "${myMAX}" -eq 8 ];
    then [ "${myGROUPS}" -eq 8 ]
    else [ "${myGROUPS}" -le 7 ]
  fi
}

fuIS_HOSTNAME () {
  # fuIS_HOSTNAME <text> [characters]: 0 for a host name, labels of letters, digits, inner dashes
  # (and the characters, _ for an alias of ~/.ssh/config) of 63 at most and 253 characters at most;
  # digits and dots only are a mistyped IPv4 address
  local LC_ALL=C myCHARS="A-Za-z0-9${2:-}"
  local myLABEL="[${myCHARS}]([${myCHARS}-]{0,61}[${myCHARS}])?"
  if [ "${#1}" -lt 1 ] || [ "${#1}" -gt 253 ]; then return 1; fi
  [[ "$1" =~ ^(${myLABEL}\.)*${myLABEL}$ ]] || return 1
  ! [[ "$1" =~ ^[0-9.]+$ ]]
}

fuCHECK_ADDRESS () {
  # fuCHECK_ADDRESS <text>: 0 for the address of the SENSOR (ssh, Ansible): an IP address or a host
  # name, an alias with an _ too, as tpot sensors add takes it (sensors.check_address)
  fuIS_IPV4 "$1" || fuIS_IPV6 "$1" || fuIS_HOSTNAME "$1" "_"
}

fuCHECK_HIVE_ADDRESS () {
  # fuCHECK_HIVE_ADDRESS <text>: 0 for the address of this HIVE, TPOT_HIVE_IP of the SENSOR: an IPv4
  # address or a host name as tpotinit takes it there (sensors.check_hive_address). No IPv6: Logstash
  # sends to https://<address>:64294, without brackets that URL breaks; a host name works for IPv6
  fuIS_IPV4 "$1" || fuIS_HOSTNAME "$1"
}

fuCHECK_USER () {
  # fuCHECK_USER <text>: 0 for a user name of Linux, capitals too (useradd of Fedora, RHEL and
  # openSUSE takes them), as tpot sensors add takes it
  local LC_ALL=C
  [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,31}$ ]]
}

fuASK_VALID () {
  # fuASK_VALID <question> <check> <warning>: asks until the answer passes the check and puts it,
  # without the spaces around it, into myANSWER; 1 for no answer (the end of the input, Esc)
  local myVALUE
  myANSWER=""
  while true; do
    myVALUE=$(fuUI_INPUT "$1") || return 1
    myVALUE=$(fuTRIM "${myVALUE}")
    [ -n "${myVALUE}" ] || return 1
    if "$2" "${myVALUE}";
      then
        myANSWER="${myVALUE}"
        return 0
    fi
    fuUI_WARN "$3"
  done
}

fuENV_FIND () {
  # fuENV_FIND <key>: the last line of the key in myENV_LINES (myENV_AT, -1 for none), its value
  # without quotes or a comment (myENV_VALUE) and its quote (myENV_QUOTE)
  local myI myRAW="" myRE="^$1=([\"']?)(.*)$"
  myENV_AT=-1 myENV_VALUE="" myENV_QUOTE=""
  for myI in "${!myENV_LINES[@]}"; do
    [[ "${myENV_LINES[myI]%$'\r'}" =~ ${myRE} ]] || continue
    myENV_AT="${myI}" myENV_QUOTE="${BASH_REMATCH[1]}" myRAW="${BASH_REMATCH[2]}"
  done
  if [ -n "${myENV_QUOTE}" ];
    then myRAW="${myRAW%%"${myENV_QUOTE}"*}"
    else myRAW="${myRAW%%[[:space:]]#*}"
  fi
  myENV_VALUE=$(fuTRIM "${myRAW}")
}

fuENV_SET () {
  # fuENV_SET <key> <value>: the key in its line with its quote, a missing one at the end; the .env
  # is written in place, so its owner and mode stay
  local myCR=""
  fuENV_FIND "$1"
  if [ "${myENV_AT}" -ge 0 ];
    then
      [[ "${myENV_LINES[myENV_AT]}" != *$'\r' ]] || myCR=$'\r'
      myENV_LINES[myENV_AT]="$1=${myENV_QUOTE}$2${myENV_QUOTE}${myCR}"
    else
      myENV_LINES+=("$1=$2")
  fi
  printf '%s\n' "${myENV_LINES[@]}" > "${myENV_FILE}"
}

# Check if the script is running in a HIVE installation
if ! grep -qE '^TPOT_TYPE=["'\'']?HIVE(["'\''[:space:]#]|$)' "${myENV_FILE}";
  then
    fuUI_ERROR "This script is only supported on HIVE installations."
    echo
    exit 1
fi

# Check if running on a supported distribution (the ones of install.sh)
mySUPPORTED_DISTRIBUTIONS=("AlmaLinux" "Debian GNU/Linux" "Fedora Linux" "openSUSE Tumbleweed" "Raspbian GNU/Linux" "Red Hat Enterprise Linux" "Rocky Linux" "Ubuntu")
myCURRENT_DISTRIBUTION=$(awk -F= '/^NAME/{print $2}' /etc/os-release | tr -d '"')
mySUPPORTED=""
for myNAME in "${mySUPPORTED_DISTRIBUTIONS[@]}"; do
  [ "${myNAME}" != "${myCURRENT_DISTRIBUTION}" ] || mySUPPORTED="y"
done
if [ -z "${mySUPPORTED}" ];
  then
    # the list in words: "a, b and c", the sentence of install.sh and uninstall.sh
    myLIST=$(printf '%s, ' "${mySUPPORTED_DISTRIBUTIONS[@]:0:${#mySUPPORTED_DISTRIBUTIONS[@]}-1}")
    fuUI_ERROR "Only the following distributions are supported: ${myLIST%, } and ${mySUPPORTED_DISTRIBUTIONS[${#mySUPPORTED_DISTRIBUTIONS[@]}-1]}."
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

# Ask for the remote user, a user name of Linux only (it goes to ssh)
if ! fuASK_VALID "Enter the remote username T-Pot SENSOR was installed with:" fuCHECK_USER \
       "Invalid user name. Please enter a Linux user name, e.g. tpot.";
  then
    fuUI_ERROR "You need to enter a user. Aborting."
    exit 1
fi
mySSHUSER="${myANSWER}"

# Ask for the IP/domain name of the SENSOR, an address only (it goes to Ansible)
if ! fuASK_VALID "Enter the IP/domain name of the SENSOR:" fuCHECK_ADDRESS \
       "Invalid IP/domain. Please enter a valid IP or domain name.";
  then
    fuUI_ERROR "You need to enter the IP/domain name of the SENSOR. Aborting."
    exit 1
fi
mySENSOR_IP="${myANSWER}"

# Check if ssh key has been deployed
if ! fuUI_CONFIRM "Has an SSH key been deployed to the SENSOR?";
  then
    fuUI_ERROR "Generate an SSH key using 'ssh-keygen' and deploy it to the SENSOR."
    fuUI_HINT "ssh-copy-id -p ${myANSIBLE_PORT} ${mySSHUSER}@${mySENSOR_IP}"
    exit 1
fi

# Ask for the IPv4 address / domain name of this HIVE, the SENSOR sends its logs there
if ! fuASK_VALID "Enter the IPv4 address or the domain name of this HIVE:" fuCHECK_HIVE_ADDRESS \
       "Invalid IP/domain. Enter an IPv4 address or a domain name, no IPv6.";
  then
    fuUI_ERROR "You need to enter the IP/domain name of this HIVE. Aborting."
    exit 1
fi
myTPOT_HIVE_IP="${myANSWER}"

# Create a random SENSOR user name that is easily readable
myLS_WEB_USER="sensor-${myADJECTIVE}-${myNOUN}"

# Create a random password (bytes, whatever the locale)
myLS_WEB_PW=$(LC_ALL=C tr -dc 'a-zA-Z0-9' < /dev/urandom | fold -w 32 | head -n 1)

# Create myLS_WEB_USER_ENC
# the password goes through stdin, not argv
myLS_WEB_USER_ENC=$(printf "%s" "${myLS_WEB_PW}" | htpasswd -n -i -B "${myLS_WEB_USER}")
myLS_WEB_USER_ENC_B64=$(echo -n "${myLS_WEB_USER_ENC}" | base64 -w0)

# Create myTPOT_HIVE_USER, since this is for Logstash on the SENSOR, it needs to directly base64 encoded
myTPOT_HIVE_USER=$(echo -n "${myLS_WEB_USER}:${myLS_WEB_PW}" | base64 -w0)

# Print the credentials, the password only now; its hash and the encoded forms stay in the files
fuUI_OK "The following SENSOR credentials have been created:"
fuUI_HINT "New SENSOR username: ${myLS_WEB_USER}" \
          "New SENSOR password: ${myLS_WEB_PW}"
echo
fuUI_INFO "Ansible will ask for the 'BECOME password' which is typically the password you 'sudo' with on the SENSOR."
fuUI_INFO "The password will allow Ansible to run a reboot via sudo on the SENSOR."
echo

# Read LS_WEB_USER from the .env, the access of the other SENSORs
mapfile -t myENV_LINES < "${myENV_FILE}"
fuENV_FIND "LS_WEB_USER"
myENV_LS_WEB_USER="${myENV_VALUE//[\"\']/}"

# Add the new SENSOR user
if [ "${myENV_LS_WEB_USER}" == "" ];
  then
    myENV_LS_WEB_USER="${myLS_WEB_USER_ENC_B64}"
  else
    myENV_LS_WEB_USER="${myENV_LS_WEB_USER} ${myLS_WEB_USER_ENC_B64}"
fi

# deploy.yml reads both from the environment, they go to the playbook only; every value one argument
ANSIBLE_LOG_PATH="${HOME}/tpotce/data/deploy_sensor.log" myTPOT_HIVE_USER="${myTPOT_HIVE_USER}" \
  myTPOT_HIVE_IP="${myTPOT_HIVE_IP}" ansible-playbook "${myANSIBLE_TPOT_PLAYBOOK}" -i "${mySENSOR_IP}," -c ssh \
  -u "${mySSHUSER}" --ask-become-pass -e "ansible_port=${myANSIBLE_PORT}"
myRC=$?

if [ "${myRC}" == 0 ];
  then
    # Update the T-Pot .env config and lswebpasswd (avoid the need to restart T-Pot) on the host,
    # both in place: lswebpasswd is bind mounted on its own, a new file would not reach nginx
    fuUI_INFO "Updating the SENSOR users on this HIVE and in the T-Pot .env config ..."
    fuENV_SET "LS_WEB_USER" "${myENV_LS_WEB_USER}"
    read -r -a myENTRIES <<< "${myENV_LS_WEB_USER}"
    for myENTRY in "${myENTRIES[@]}"; do
      # one line each, whatever line ends the encoded entry has
      printf '%s\n' "$(echo -n "${myENTRY}" | base64 -d | tr -d '\n')"
    done > "${myLSWEBPASSWD}"
fi

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
