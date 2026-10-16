#!/bin/bash

# an exported CDPATH turns a cd into a search that prints the folder it found ($(cd ... && pwd)
# gives two lines): every cd of this script means the folder it names
unset CDPATH

# Some global vars
myDATE=$(date +%Y%m%d%H%M%S)
myBECOME_FILE=""
myGROUPS=""

# The look of the T-Pot scripts (installer/lib/ui.sh: gum at a terminal, plain text
# otherwise) and the @@tpot marks the task screen of tpot reads (fuMARK). BASH_SOURCE:
# the tests source this file for its functions.
myHERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
# shellcheck source=installer/lib/ui.sh
if ! source "${myHERE}/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback: a checkout of an earlier release has no installer/lib/ui.sh
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
        case "${2:-}" in yes|no) myDEFAULT="$2"; shift 2 ;; *) shift ;; esac
      fi
      case "${myDEFAULT}" in yes) myPROMPT="(Y/n)" ;; no) myPROMPT="(y/N)" ;; esac
      while true; do
        read -rp "### $1 ${myPROMPT} " myANSWER || return 1
        myANSWER="${myANSWER#"${myANSWER%%[![:space:]]*}"}"
        myANSWER="${myANSWER%"${myANSWER##*[![:space:]]}"}"
        [ -n "${myANSWER}" ] || myANSWER="${myDEFAULT}"
        case "${myANSWER}" in [yY]|[yY][eE][sS]) return 0 ;; [nN]|[nN][oO]) return 1 ;; esac
      done
    }
    fuUI_MARKS_ON () { [ -n "${myMARKS:-}" ] || [ "${TPOT_MARKS:-}" = "1" ]; }
    fuMARK () { fuUI_MARKS_ON || return 0; echo "@@tpot $*"; }
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
    fuUI_LINUX_ONLY () {
      local mySYSTEM
      mySYSTEM=$(uname -s 2>/dev/null)
      [ "${mySYSTEM}" != "Linux" ] || return 0
      case "${mySYSTEM}" in
        Darwin) mySYSTEM="macOS" ;;
        MINGW*|MSYS*|CYGWIN*) mySYSTEM="Windows (${mySYSTEM})" ;;
        "") mySYSTEM="an unknown system" ;;
      esac
      echo "### [ERROR] - $1 does not run on ${mySYSTEM}." >&2
      echo "###   $1 runs on Linux: a T-Pot host, a build host or a VM, WSL2 on Windows." >&2
      exit "${2:-1}"
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
    fuUI_CHOOSE_MANY () {
      local myALL="" myI myJ myN myPICK myPART myA myB myOK
      local -a mySELECTED=()
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --filter) shift ;;
          --all) myALL=1; shift ;;
          --selected) mySELECTED+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
          *) break ;;
        esac
      done
      local myHEADER="${1:-}"
      [ "$#" -eq 0 ] || shift
      local -a myLABELS=() myVALUES=() myON=() myNEW=()
      for myI in "$@"; do
        myLABELS+=("${myI%:*}")
        myVALUES+=("${myI##*:}")
        myON+=("${myALL}")
        for myJ in "${mySELECTED[@]}"; do
          [ "${myJ}" != "${myVALUES[${#myVALUES[@]}-1]}" ] || myON[${#myON[@]}-1]=1
        done
      done
      myN="${#myLABELS[@]}"
      echo "### ${myHEADER}" >&2
      for myI in "${!myLABELS[@]}"; do
        if [ -n "${myON[myI]}" ]; then echo "###   $((myI + 1))) [x] ${myLABELS[myI]}" >&2
        else echo "###   $((myI + 1))) [ ] ${myLABELS[myI]}" >&2; fi
      done
      while true; do
        read -rp "### Choice (i.e. 1,3-5; a = all, n = none, enter = the marked ones): " myPICK || return 1
        # the spaces go, one between two digits makes it no choice (1 3 is not 13)
        [[ "${myPICK}" =~ [0-9][[:space:]]+[0-9] ]] || myPICK="${myPICK//[[:space:]]/}"
        case "${myPICK}" in
          "") break ;;
          a|A) for myI in "${!myON[@]}"; do myON[myI]=1; done; break ;;
          n|N) for myI in "${!myON[@]}"; do myON[myI]=""; done; break ;;
        esac
        myNEW=()
        myOK=1
        for myI in "${!myON[@]}"; do myNEW[myI]=""; done
        if [[ "${myPICK}" =~ ^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$ ]]; then
          for myPART in ${myPICK//,/ }; do
            myA=$((10#${myPART%-*})) myB=$((10#${myPART#*-}))
            if [ "${myA}" -lt 1 ] || [ "${myB}" -gt "${myN}" ] || [ "${myA}" -gt "${myB}" ]; then myOK=""; break; fi
            for ((myI = myA; myI <= myB; myI++)); do myNEW[myI - 1]=1; done
          done
        else myOK=""
        fi
        if [ -n "${myOK}" ]; then myON=("${myNEW[@]}"); break; fi
        echo "### [WARNING] - Not a choice: ${myPICK}" >&2
      done
      for myI in "${!myVALUES[@]}"; do
        [ -z "${myON[myI]}" ] || printf '%s\n' "${myVALUES[myI]}"
      done
      return 0
    }
    fuUI_SPIN () {
      local myTITLE="$1" myLOG="$2" myRC=0
      shift 2
      echo "### ${myTITLE}"
      if fuUI_MARKS_ON;
        then { "$@" < /dev/null 2>&1 | tee -a "${myLOG}" 2>/dev/null; myRC="${PIPESTATUS[0]}"; } || true
        else "$@" >>"${myLOG}" 2>&1 < /dev/null || myRC=$?
      fi
      if [ "${myRC}" -eq 0 ];
        then echo "### [OK] - ${myTITLE%% ...}"
        else
          if fuUI_MARKS_ON;
            then echo "### [ERROR] - ${myTITLE%% ...} failed" >&2
            else echo "### [ERROR] - ${myTITLE%% ...} failed, the end of ${myLOG}:" >&2; tail -n 15 "${myLOG}" >&2
          fi
      fi
      return "${myRC}"
    }
# <<< plain fallback
fi
fuUI_INIT
# the T-Pot logo in the banner (at a terminal, not in the marks mode, not when update.sh
# showed it already)
# shellcheck disable=SC2034 # read by fuUI_BANNER (installer/lib/ui.sh)
myUI_LOGO=1

myTPOTDIR="${HOME}/tpotce"
myBACKUPDIR="${HOME}/tpot_backups"
# What runs under a spinner writes its output here (in the marks mode of the task
# screen it goes through instead, fuUI_SPIN), see fuLOG_START
myLOG="${myBACKUPDIR}/restore.log"
myARCHIVE=""
myCONFIRMED=""
myCONFIG_ONLY=""
myKIBANA="http://127.0.0.1:64296"
myES="http://127.0.0.1:64298"

# How long to wait for Kibana, and for Elasticsearch, before printing the commands to run by
# hand. Weaker hardware is allowed to take longer.
myKIBANA_TIMEOUT="${TPOT_KIBANA_TIMEOUT:-300}"
myTMPDIR=""

# What to restore. Empty means: ask.
myDO_GIT=""
myDO_CONFIG=""
myDO_PATCH=""
myDO_UNTRACKED=""
myDO_DATA=""
myDO_ELASTIC=""

# --accept-data-loss: confirms a loss of data where the run cannot ask (fuLOSS_CONFIRM): data/
# replacing the current files and the Elasticsearch data, an ILM policy of the archive that keeps
# the events less long than the current one. -y does not. --accept-data-loss-file: only the losses
# whose key (data, ilm) one of its '@@tpot loss <key> <text>' lines names (the T-Pot Manager: data).
myACCEPT_LOSS=""
myACCEPT_FILE=""

function fuPRINT_HELP () {
	# the help only, without the T-Pot logo (that is for a run)
	fuUI_HELP "Restorer" "restore.sh [-l] [-f <archive>] [-y | -c | -g <groups>] [-B <file>]"$'\n'"           [--accept-data-loss | --accept-data-loss-file <file>]" \
	  --about "Restores a backup written by update.sh. Without -y, -c or -g it lists what the archive
holds and you choose what comes back, so you can bring back just the configuration
without touching anything else." \
	  --opt "-l" "List the available backups and what they hold" \
	  --opt "-f <archive>" "Restore from this archive. Default: the newest one in
${myBACKUPDIR}" \
	  --opt "-y" "Restore everything without asking, including the rollback
of the git checkout. It does not confirm a loss of data,
see --accept-data-loss" \
	  --opt "-c" "Only roll the checkout back and restore the configuration
(.env, docker-compose.yml, your changes to tracked files),
without asking. Leaves data/ alone and does not start T-Pot." \
	  --opt "-g <groups>" "Restore these groups without asking, comma separated: git,
patch, config, untracked, data, elastic (the T-Pot Manager uses it)" \
	  --opt "-B <file>" "Read the sudo password from a file, so it runs without
asking for it (the T-Pot Manager hands one over)" \
	  --opt "--accept-data-loss" "Confirm every loss of data without a terminal, where the run
cannot ask: data/ replaces the current files and the
Elasticsearch data, a shorter ILM policy of the archive" \
	  --opt "--accept-data-loss-file <file>" "Confirm only the losses this file names, one
'@@tpot loss <key> <text>' line each, key data or ilm (the
T-Pot Manager hands one over with data only)" \
	  --opt "-h" "Show this help message" \
	  --example "./restore.sh -l" "The backups and what each one holds" \
	  --example "./restore.sh" "Choose what of the newest backup comes back" \
	  --example "./restore.sh -f ~/tpot_backups/<archive>.tar -g config" "Only .env and docker-compose.yml of that archive" \
	  --example "./restore.sh -f ~/tpot_backups/<archive>.tar -g data --accept-data-loss" "data/ of that archive without a terminal: the current files and Elasticsearch data are replaced" \
	  --note "T-Pot is stopped for everything but the Kibana objects and the ILM policy, which
need it running: restore.sh starts it for them. tpot restore hands every option on." \
	  --note "A loss of data is asked for at a terminal (default no); without one only
--accept-data-loss (every loss) or --accept-data-loss-file (the losses it names) confirms
it, -y does not. Not confirmed, data/ or the shorter ILM policy
stays as it is and the rest goes on. A shorter TPOT_ES_RETENTION of the archive waits:
T-Pot never shortens the ILM policy on its own."
	exit 0
}

# Check if running with root privileges
if [ ${EUID} -eq 0 ];
  then
    fuUI_ERROR "This script should not be run as root. Please run it as a regular user."
    echo
    exit 1
fi

# One working directory for the whole run, gone when the script ends
function fuTMPDIR () {
	[ -n "${myTMPDIR}" ] && return
	myTMPDIR=$(mktemp -d)
	if [ ! -d "${myTMPDIR}" ];
	  then
	    fuUI_ERROR "Could not create a temporary directory."
	    echo
	    exit 1
	fi
	trap 'rm -rf "${myTMPDIR}"' EXIT
}

# The log of the steps under a spinner, the one of this run, next to the backups.
# Without a place for it the output goes nowhere.
function fuLOG_START () {
	if mkdir -p "${myBACKUPDIR}" 2>/dev/null && chmod 0700 "${myBACKUPDIR}" 2>/dev/null \
	   && { : > "${myLOG}"; } 2>/dev/null;
	  then
	    return 0
	fi
	myLOG="/dev/null"
}

# What a group brings back, for the summary at the end
function fuGROUP_TITLE () {   # $1 = group
	case "$1" in
	  git)       echo "The checkout, rolled back to the commit before the update" ;;
	  patch)     echo "Your changes to tracked files (tracked.patch)" ;;
	  config)    echo "The configuration (.env and docker-compose.yml)" ;;
	  untracked) echo "Your untracked files" ;;
	  data)      echo "The files from data/ (certificates, uuid, host keys)" ;;
	  elastic)   echo "The Kibana objects and the ILM policy" ;;
	esac
}

# All archives, newest first
function fuARCHIVE_LIST () {
	ls -1t "${myBACKUPDIR}"/*_tpot_backup*.tar 2>/dev/null
}

# A chosen group that did not come back, named at the end (exit 1)
myFAILED=""
function fuFAILED () {   # $1 = group
	case ", ${myFAILED}, " in
	  *", $1, "*) ;;
	  *) myFAILED="${myFAILED:+${myFAILED}, }$1" ;;
	esac
	# the groups are named like their phases, tpot shows that one as failed
	fuMARK fail "$1"
}

# A chosen group, or a part of it, that was left out on purpose (a loss of data not confirmed):
# a warning in the summary, no failure
myWARNED=()
function fuWARNED () {   # $1 = group, $2 = what was left out
	myWARNED+=("$1:$2")
}

# Does the file of --accept-data-loss-file confirm the loss of this key? A line '@@tpot loss <key> ...'.
function fuLOSS_IN_FILE () {   # $1 = data | ilm
	local myLINE=""
	[ -n "${myACCEPT_FILE}" ] && [ -r "${myACCEPT_FILE}" ] || return 1
	while IFS= read -r myLINE || [ -n "${myLINE}" ];
	  do
	    myLINE="${myLINE%$'\r'}"
	    case "${myLINE}" in
	      "@@tpot loss $1"|"@@tpot loss $1 "*) return 0 ;;
	    esac
	  done < "${myACCEPT_FILE}"
	return 1
}

# Is this loss of data confirmed? A yes at a terminal (no is the default), --accept-data-loss (every
# loss) or a line of its key in --accept-data-loss-file (fuLOSS_IN_FILE); -y confirms the restore,
# never a loss of data. rc 0 for confirmed.
# update.sh has a function of this name too, with another signature on purpose: there it takes the
# names of what goes (backups, images) after the text, because its file lines name them; here a loss
# is one thing under its key, the question comes as the third argument. The hints for a file that does
# not confirm the loss differ with that: here the file names no loss of its key, there the loss was not
# shown like this before the run (a name is missing). Otherwise what they decide is the same: accept,
# file, terminal, refusal, in the same order, with the same warnings (the question can be another here).
function fuLOSS_CONFIRM () {   # $1 = data | ilm, $2 = text, $3 = the question (default: the one of update.sh)
	if [ -n "${myACCEPT_LOSS}" ];
	  then
	    fuUI_WARN "$2 (confirmed with --accept-data-loss)"
	    return 0
	fi
	if [ -n "${myACCEPT_FILE}" ] && fuLOSS_IN_FILE "$1";
	  then
	    fuUI_WARN "$2 (confirmed with --accept-data-loss-file)"
	    return 0
	fi
	fuUI_WARN "$2"
	# a loss the file does not name was not confirmed before the run: not confirmed at all
	[ -z "${myACCEPT_FILE}" ] || fuUI_HINT "--accept-data-loss-file does not confirm it: it names no '$1' loss."
	if [ -t 0 ];
	  then
	    fuUI_CONFIRM --default no "${3:-Accept this loss of data?}"
	    return
	fi
	fuUI_HINT "Not confirmed: no terminal. --accept-data-loss confirms it, -y does not."
	return 1
}

# Is the restore of data/ refused for sure, before anything is stopped? Where fuLOSS_CONFIRM could not
# say yes: no --accept-data-loss, no 'data' line in --accept-data-loss-file and no terminal to ask at.
# At a terminal the question stays where it was (fuDO_DATA). rc 0: refused.
function fuDATA_REFUSED () {
	[ -z "${myACCEPT_LOSS}" ] || return 1
	[ -z "${myACCEPT_FILE}" ] || ! fuLOSS_IN_FILE data || return 1
	[ -t 0 ] && return 1
	return 0
}

# A value of a .env style file, KEY=value or KEY: "value" (as in update.sh)
function fuENV_VALUE () {   # $1 = file, $2 = key
	grep -E "^$2[[:space:]]*[:=]" "$1" 2>/dev/null | tail -1 \
	  | sed -E "s/^[^:=]*[:=][[:space:]]*//; s/[[:space:]]*$//; s/^[\"']//; s/[\"']$//"
}

# Where the data folder really is: TPOT_DATA_PATH of the .env in effect, relative to ~/tpotce
# unless it is absolute or starts with ~, without a trailing slash (the same code as fuDATA_PATH of
# update.sh, a test compares them)
function fuDATA_PATH () {
	local myPATH=""
	myPATH=$(fuENV_VALUE "${myTPOTDIR}/.env" TPOT_DATA_PATH)
	[ -z "${myPATH}" ] && myPATH="./data"
	case "${myPATH}" in
	  /*) ;;
	  "~"|"~/"*) myPATH="${HOME}${myPATH#\~}" ;;
	  *)  myPATH="${myTPOTDIR}/${myPATH#./}" ;;
	esac
	while [ "${myPATH}" != "/" ] && [ "${myPATH%/}" != "${myPATH}" ];
	  do
	    myPATH="${myPATH%/}"
	done
	echo "${myPATH}"
}

# Does NEW keep the events less long than OLD? Days, off or empty (no delete phase, a policy that
# keeps them no whole number of days: both count as forever). rc 0 for shorter.
function fuRETENTION_SHORTER () {   # $1 = OLD, $2 = NEW
	case "$2" in ""|off|*[!0-9]*) return 1 ;; esac
	case "$1" in ""|off|*[!0-9]*) return 0 ;; esac
	[ "$((10#$2))" -lt "$((10#$1))" ]
}

# How long a retention keeps the events, in words
function fuRETENTION_SHOWN () {   # $1 = days, off or empty
	case "$1" in
	  off) echo "forever" ;;
	  "") echo "for no whole number of days" ;;
	  *) echo "$((10#$1)) days" ;;
	esac
}

# The retention an ILM policy stands for (tpotctl.elastic value-of: days, off or empty), from the
# checkout, else from the working directory. rc 1: it could not be read.
function fuPOLICY_VALUE () {   # $1 = policy file ({"policy": ...})
	local myVALUE="" myRC=0
	myVALUE=$(cd "${myTPOTDIR}" 2>/dev/null && python3 -m tpotctl.elastic value-of "$1" 2>/dev/null) || myRC=$?
	if [ "${myRC}" -ne 0 ];
	  then
	    myRC=0
	    myVALUE=$(python3 -m tpotctl.elastic value-of "$1" 2>/dev/null) || myRC=1
	fi
	echo "${myVALUE}"
	return "${myRC}"
}

# When was the archive written? Created: of its MANIFEST, else the date in its name
function fuARCHIVE_DATE () {
	local myWHEN="" myNAME=""
	myWHEN=$(tar xOf "${myARCHIVE}" MANIFEST 2>/dev/null | sed -n 's/^Created:[[:space:]]*//p' | head -1)
	if [ -z "${myWHEN}" ];
	  then
	    myNAME=$(basename "${myARCHIVE}")
	    case "${myNAME}" in
	      [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*)
	        myWHEN="${myNAME:0:4}-${myNAME:4:2}-${myNAME:6:2} ${myNAME:8:2}:${myNAME:10:2}" ;;
	      *) myWHEN="the backup" ;;
	    esac
	fi
	echo "${myWHEN}"
}

# Ctrl+C under a spinner (fuUI_SPIN rc 130): the run ends here, with what to do next
myTPOT_STOPPED=""
function fuSTOPPED () {   # $1 = what was stopped
	local myITEMS=("warn:Stopped while $1, the restore is not complete.")
	[ -z "${myTPOT_STOPPED}" ] || myITEMS+=("next:T-Pot is stopped, 'sudo systemctl start tpot' brings it back.")
	[ -z "${myARCHIVE}" ] || myITEMS+=("next:Run restore.sh -f ${myARCHIVE} again to finish it.")
	fuUI_SUMMARY "The restore was stopped" "${myITEMS[@]}"
	exit 130
}

# What does the archive hold?
function fuHAS () {   # $1 = Member oder Praefix
	grep -q "$1" "${myTMPDIR}/toc" 2>/dev/null
}

function fuLIST () {
	local myFILE=""
	local myKIND=""
	echo
	fuUI_INFO "Backups in ${myBACKUPDIR} ..."
	if [ -z "$(fuARCHIVE_LIST)" ];
	  then
	    fuUI_HINT "No backups found."
	    echo
	    exit 0
	fi
	for myFILE in $(fuARCHIVE_LIST);
	  do
	    case "${myFILE}" in
	      *_full*.tar) myKIND="full" ;;
	      *)           myKIND="regular" ;;
	    esac
	    echo
	    fuUI_INFO "$(basename "${myFILE}")  $(du -h "${myFILE}" | cut -f1), ${myKIND}"
	    tar xOf "${myFILE}" MANIFEST 2>/dev/null | sed -n '2,8p' | sed 's/^/    /'
	    fuUI_HINT "Holds: $(tar tf "${myFILE}" 2>/dev/null | sed 's|/.*||' | sort -u | tr '\n' ' ')"
	done
	echo
	exit 0
}

# Under the spinner: the table of contents of the archive
function fuTOC () {
	tar tf "${myARCHIVE}" > "${myTMPDIR}/toc"
}

# Pick the archive and read its table of contents
function fuPICK_ARCHIVE () {
	fuMARK phase pick Reading the backup
	echo
	fuUI_INFO "Looking for a backup ..."
	if [ -z "${myARCHIVE}" ];
	  then
	    myARCHIVE=$(fuARCHIVE_LIST | head -1)
	fi
	if [ -z "${myARCHIVE}" ] || [ ! -f "${myARCHIVE}" ];
	  then
	    fuUI_ERROR "No backup found in ${myBACKUPDIR}. Name one with '-f'."
	    echo
	    exit 1
	fi
	fuTMPDIR
	fuLOG_START
	# a full archive holds all of data/, reading it takes a while
	local myRC=0
	fuUI_SPIN "Reading the archive ..." "${myLOG}" fuTOC || myRC=$?
	[ "${myRC}" -ne 130 ] || fuSTOPPED "reading the archive"
	if [ "${myRC}" -ne 0 ];
	  then
	    fuUI_ERROR "Cannot read ${myARCHIVE}."
	    echo
	    exit 1
	fi
	fuUI_HINT "${myARCHIVE}  $(du -h "${myARCHIVE}" | cut -f1), $(grep -c . "${myTMPDIR}/toc") entries"
	if fuHAS "^MANIFEST$";
	  then
	    tar xOf "${myARCHIVE}" MANIFEST | sed 's/^/    /'
	  else
	    fuUI_WARN "No MANIFEST - this does not look like a backup from update.sh."
	fi
	echo
}

# What is in there, and which of it should come back? One list of the groups the
# archive holds, all of them chosen to begin with (the questions are the ones the
# T-Pot Manager shows, ops.GROUP_TEXT)
function fuCHOOSE () {
	local myITEMS=() myPICKED="" myGROUP=""
	fuHAS "^rollback.txt$"  && myITEMS+=("Roll the checkout back to the commit before the update?:git")
	fuHAS "^tracked.patch$" && myITEMS+=("Re-apply all your changes to tracked files (tracked.patch)?:patch")
	fuHAS "^env$"           && myITEMS+=("Restore the configuration (.env and docker-compose.yml)?:config")
	fuHAS "^untracked/"     && myITEMS+=("Restore your untracked files?:untracked")
	fuHAS "^data/"          && myITEMS+=("Restore the files from data/ (certificates, uuid, host keys)?:data")
	fuHAS "^elastic/"       && myITEMS+=("Import the Kibana objects and the ILM policy? T-Pot has to run for that.:elastic")
	if [ "${#myITEMS[@]}" -gt 0 ];
	  then
	    # cancelled (or no answer): nothing chosen
	    myPICKED=$(fuUI_CHOOSE_MANY --all "What should be restored?" "${myITEMS[@]}") || myPICKED=""
	fi
	for myGROUP in ${myPICKED};
	  do
	    case "${myGROUP}" in
	      git)       myDO_GIT="1" ;;
	      patch)     myDO_PATCH="1" ;;
	      config)    myDO_CONFIG="1" ;;
	      untracked) myDO_UNTRACKED="1" ;;
	      data)      myDO_DATA="1" ;;
	      elastic)   myDO_ELASTIC="1" ;;
	    esac
	done
	echo
	if [ -z "${myDO_GIT}${myDO_CONFIG}${myDO_PATCH}${myDO_UNTRACKED}${myDO_DATA}${myDO_ELASTIC}" ];
	  then
	    fuUI_HINT "Nothing selected, leaving everything as it is."
	    echo
	    exit 0
	fi
}

# Under the spinner: without tpot.service the containers of the compose file go down
function fuCOMPOSE_DOWN () {
	[ -f "${myTPOTDIR}/docker-compose.yml" ] || return 0
	( cd "${myTPOTDIR}" && docker compose down )
}

# Stopping and starting run under a spinner, which cannot ask for a password: sudo is
# refreshed first
function fuSTOP_TPOT () {
	fuMARK phase stop Stopping T-Pot
	echo
	sudo -v
	local myRC=0
	myTPOT_STOPPED="1"
	fuUI_SPIN "Stopping T-Pot ..." "${myLOG}" sudo systemctl stop tpot.service || myRC=$?
	[ "${myRC}" -ne 130 ] || fuSTOPPED "stopping T-Pot"
	if [ "${myRC}" -ne 0 ];
	  then
	    fuUI_WARN "No tpot.service, trying docker compose."
	    myRC=0
	    fuUI_SPIN "Stopping T-Pot with docker compose ..." "${myLOG}" fuCOMPOSE_DOWN || myRC=$?
	    [ "${myRC}" -ne 130 ] || fuSTOPPED "stopping T-Pot"
	fi
}

mySTART_FAILED=""
function fuSTART_TPOT () {
	fuMARK phase start Starting T-Pot
	echo
	sudo -v
	local myRC=0
	fuUI_SPIN "Starting T-Pot ..." "${myLOG}" sudo systemctl start tpot.service || myRC=$?
	[ "${myRC}" -ne 130 ] || fuSTOPPED "starting T-Pot"
	if [ "${myRC}" -ne 0 ];
	  then
	    fuUI_WARN "Could not start tpot.service, please start T-Pot yourself."
	    mySTART_FAILED="1"
	    return 1
	fi
	myTPOT_STOPPED=""
}

# The rollback comes first: a `git reset --hard` puts .env and docker-compose.yml
# back to the state of the commit, so it would overwrite anything done after it.
function fuDO_GIT () {
	local myCOMMIT=""
	[ -z "${myDO_GIT}" ] && return
	fuMARK phase git Rolling the checkout back
	echo
	fuUI_INFO "Rolling the checkout back ..."
	if ! tar xOf "${myARCHIVE}" rollback.txt > "${myTMPDIR}/rollback.txt" 2>/dev/null;
	  then
	    fuUI_ERROR "Could not read rollback.txt from the archive."
	    fuFAILED git
	    return
	fi
	myCOMMIT=$(tr -d "[:space:]" < "${myTMPDIR}/rollback.txt")
	if [ -z "${myCOMMIT}" ];
	  then
	    fuUI_WARN "rollback.txt is empty, skipping."
	    fuFAILED git
	    return
	fi
	if ! git -C "${myTPOTDIR}" cat-file -e "${myCOMMIT}^{commit}" 2>/dev/null;
	  then
	    fuUI_WARN "Commit ${myCOMMIT} is not in ${myTPOTDIR}, skipping."
	    fuFAILED git
	    return
	fi
	if git -C "${myTPOTDIR}" reset -q --hard "${myCOMMIT}";
	  then
	    fuUI_OK "The checkout is at ${myCOMMIT} again."
	    fuMARK changed checkout
	  else
	    fuUI_ERROR "Could not reset the checkout to ${myCOMMIT}."
	    fuFAILED git
	fi
}

function fuDO_CONFIG () {
	local myFILE="" myOLD="" myNEW="" myWAS=""
	[ -z "${myDO_CONFIG}" ] && return
	fuMARK phase config Restoring the configuration
	echo
	fuUI_INFO "Restoring the configuration ..."
	# no key (or an empty one): T-Pot left the policy as it was, which may delete at an age of its own;
	# any days of the archive count as shorter, the hint says they wait
	myOLD=$(fuENV_VALUE "${myTPOTDIR}/.env" TPOT_ES_RETENTION)
	if tar xf "${myARCHIVE}" -C "${myTMPDIR}" env 2>/dev/null && cp "${myTMPDIR}/env" "${myTPOTDIR}/.env";
	  then
	    fuUI_OK "Wrote ${myTPOTDIR}/.env."
	    # T-Pot never shortens the ILM policy on its own: a shorter value of the backup waits
	    myNEW=$(fuENV_VALUE "${myTPOTDIR}/.env" TPOT_ES_RETENTION)
	    if fuRETENTION_SHORTER "${myOLD}" "${myNEW}";
	      then
	        myWAS="${myOLD:-not set: T-Pot left the policy as it was (it may delete at its own age)}"
	        fuUI_HINT "TPOT_ES_RETENTION=${myNEW} of the backup is shorter than the value before (${myWAS}): T-Pot does not shorten the ILM policy tpot on its own, the value waits." \
	                  "'tpot elastic retention --force' or the Elastic page of the T-Pot Manager applies it, both ask first."
	    fi
	  else
	    fuUI_ERROR "Could not restore .env."
	    fuFAILED config
	fi
	if fuHAS "^docker-compose.yml$";
	  then
	    if tar xf "${myARCHIVE}" -C "${myTMPDIR}" docker-compose.yml 2>/dev/null \
	       && cp "${myTMPDIR}/docker-compose.yml" "${myTPOTDIR}/docker-compose.yml";
	      then
	        fuUI_OK "Wrote ${myTPOTDIR}/docker-compose.yml ($(head -1 "${myTPOTDIR}/docker-compose.yml"))."
	      else
	        fuUI_ERROR "Could not restore docker-compose.yml."
	        fuFAILED config
	    fi
	fi
}

# tracked.patch covers every change to tracked files, so .env and
# docker-compose.yml as well. It describes them against the commit, which is why it
# runs right after the rollback and still before the single-file copies - on a tree
# where .env already holds the changed content it no longer applies. The copies
# afterwards write the same content once more, which costs nothing and covers the
# case where only the configuration was picked.
function fuDO_PATCH () {
	[ -z "${myDO_PATCH}" ] && return
	fuMARK phase patch Applying your changes to tracked files
	echo
	fuUI_INFO "Re-applying your changes to tracked files ..."
	if ! tar xf "${myARCHIVE}" -C "${myTMPDIR}" tracked.patch 2>/dev/null;
	  then
	    fuUI_ERROR "Could not read tracked.patch from the archive."
	    fuFAILED patch
	    return
	fi
	if [ ! -s "${myTMPDIR}/tracked.patch" ];
	  then
	    fuUI_OK "The patch is empty, there was nothing to re-apply."
	    return
	fi
	if git -C "${myTPOTDIR}" apply --check "${myTMPDIR}/tracked.patch" 2>/dev/null;
	  then
	    if git -C "${myTPOTDIR}" apply "${myTMPDIR}/tracked.patch";
	      then
	        fuUI_OK "Applied."
	        fuMARK changed checkout
	      else
	        cp "${myTMPDIR}/tracked.patch" "${myBACKUPDIR}/${myDATE}_tracked.patch"
	        fuUI_ERROR "The patch passed the check but did not apply."
	        fuUI_HINT "Left it in ${myBACKUPDIR}/${myDATE}_tracked.patch for you to look at."
	        fuFAILED patch
	    fi
	    return
	fi
	if git -C "${myTPOTDIR}" apply --3way "${myTMPDIR}/tracked.patch" 2>/dev/null;
	  then
	    fuUI_WARN "Applied with a three-way merge, please review the result."
	    fuMARK changed checkout
	    return
	fi
	cp "${myTMPDIR}/tracked.patch" "${myBACKUPDIR}/${myDATE}_tracked.patch"
	fuUI_WARN "The patch does not apply to this tree, most likely because those changes are already in place."
	fuUI_HINT "Left it in ${myBACKUPDIR}/${myDATE}_tracked.patch for you to look at."
}

function fuDO_UNTRACKED () {
	[ -z "${myDO_UNTRACKED}" ] && return
	fuMARK phase untracked Restoring your untracked files
	echo
	fuUI_INFO "Restoring your untracked files ..."
	rm -rf "${myTMPDIR}/untracked"
	if ! tar xf "${myARCHIVE}" -C "${myTMPDIR}" untracked 2>/dev/null;
	  then
	    fuUI_ERROR "Could not read them from the archive."
	    fuFAILED untracked
	    return
	fi
	if ( cd "${myTMPDIR}/untracked" && tar cf - . ) | ( cd "${myTPOTDIR}" && tar xf - );
	  then
	    fuUI_OK "Restored $(find "${myTMPDIR}/untracked" -type f | wc -l) files."
	  else
	    fuUI_ERROR "Could not write them."
	    fuFAILED untracked
	fi
}

# The files under data/ belong to tpot:tpot (uid/gid 2000) with 0770. Without the
# right modes the containers will not start, so owner and mode come from the archive
# instead of from the caller's umask.
# The archive holds data/ as data/...; it goes into TPOT_DATA_PATH of the .env in effect (after the
# configuration of the archive, if that was chosen). It replaces what is there: newer files, log
# lines and with a full archive the Elasticsearch data since the backup are lost, which needs a
# confirmation (fuLOSS_CONFIRM). Not confirmed, data/ stays as it is and the rest goes on.
function fuDO_DATA () {
	local myDATA="" myTEXT="" mySIZE="" myRC=0
	local myWHERE=()
	[ -z "${myDO_DATA}" ] && return
	fuMARK phase data Restoring the files from data/
	echo
	fuUI_INFO "Restoring the files from data/ ..."
	myDATA=$(fuDATA_PATH)
	myTEXT="Restoring data/: the files under ${myDATA} are replaced by those of the archive, newer files and log lines since the backup are lost."
	if fuHAS "^data/elk/data/";
	  then
	    mySIZE=$(sudo du -sh "${myDATA}/elk/data" 2>/dev/null | cut -f1)
	    myTEXT="${myTEXT} The current Elasticsearch data${mySIZE:+ (${mySIZE})} is deleted and replaced, events since $(fuARCHIVE_DATE) are lost."
	fi
	if ! fuLOSS_CONFIRM data "${myTEXT}";
	  then
	    fuUI_WARN "data/ was not restored: not confirmed."
	    fuWARNED data "not restored: not confirmed, see above"
	    return
	fi
	# the default data path takes the members as they are, any other one without their data/
	if [ "${myDATA}" = "${myTPOTDIR}/data" ];
	  then
	    myWHERE=(-C "${myTPOTDIR}")
	  else
	    sudo mkdir -p "${myDATA}"
	    myWHERE=(-C "${myDATA}" --strip-components=1)
	fi
	# Elasticsearch data cannot be merged: extracting over a data folder that a
	# newer version has already upgraded leaves its files behind, and the older
	# Elasticsearch of the archive refuses to start on that mix.
	if fuHAS "^data/elk/data/";
	  then
	    if sudo rm -rf "${myDATA}/elk/data";
	      then
	        fuUI_OK "Removed the current ${myDATA}/elk/data, the archive brings its own."
	      else
	        fuUI_ERROR "Could not remove the current ${myDATA}/elk/data."
	        fuFAILED data
	    fi
	fi
	# under the spinner, a full archive takes a while: sudo is refreshed first
	sudo -v
	fuUI_SPIN "Extracting data/ from the archive ..." "${myLOG}" \
	  sudo tar xf "${myARCHIVE}" "${myWHERE[@]}" -p --numeric-owner --wildcards "data/*" || myRC=$?
	[ "${myRC}" -ne 130 ] || fuSTOPPED "extracting data/"
	if [ "${myRC}" -eq 0 ];
	  then
	    fuUI_OK "$(grep -c '^data/' "${myTMPDIR}/toc") entries restored, owner and mode came from the archive."
	  else
	    fuUI_ERROR "The files from data/ could not be restored, see above."
	    fuFAILED data
	fi
}

# The Elasticsearch import needs a running instance - unlike everything else, which
# wants T-Pot stopped. So it runs last, after the start.
# Under the spinner: wait for Kibana or Elasticsearch, up to myKIBANA_TIMEOUT seconds. What it
# waited for goes into the log (restore.log), which a failed spinner shows the end of.
function fuWAIT_UP () {   # $1 = URL, $2 = name
	local myWAIT=0
	while [ "${myWAIT}" -lt "${myKIBANA_TIMEOUT}" ];
	  do
	    curl -s -f -o /dev/null --connect-timeout 3 --max-time 10 "$1" && return 0
	    sleep 5
	    myWAIT=$((myWAIT+5))
	done
	curl -s -f -o /dev/null --connect-timeout 3 --max-time 10 "$1" && return 0
	echo "$2 on $1 did not answer within ${myKIBANA_TIMEOUT}s."
	return 1
}

# The folder that keeps files of elastic/ for the manual route, the working directory is about to
# vanish: ${myBACKUPDIR}/${myDATE}_elastic in myKEEP, 0700 like the backups, on every path. rc 1
# when it or a file could not be made, the hints name the archive then.
function fuKEEP_ELASTIC () {   # $@ = the files of ${myTMPDIR}/elastic to keep
	local myFILE=""
	myKEEP="${myBACKUPDIR}/${myDATE}_elastic"
	# the backups folder too, should it not be there yet (as fuLOG_START and update.sh keep it)
	{ mkdir -p "${myBACKUPDIR}" && chmod 0700 "${myBACKUPDIR}" \
	  && mkdir -p "${myKEEP}" && chmod 0700 "${myKEEP}"; } 2>/dev/null || return 1
	for myFILE in "$@";
	  do
	    cp "${myTMPDIR}/elastic/${myFILE}" "${myKEEP}/${myFILE}" 2>/dev/null || return 1
	done
}

# Does an ILM policy delete events at all: has its delete phase the delete action
# (tpotctl.elastic._deletes, from the checkout, else from the working directory)? Prints its
# min_age. rc 0: it deletes, 1: it does not, 2: it cannot be told (counts as deleting).
function fuPOLICY_DELETES () {   # $1 = policy file ({"policy": ...})
	local myOUT="" myRC=0
	local myCODE='import json, sys
from tpotctl import elastic
data = json.load(open(sys.argv[1]))
policy = data.get("policy", data) if isinstance(data, dict) else None
if not isinstance(policy, dict):
    sys.exit(2)
if not elastic._deletes(policy):
    sys.exit(3)
print(policy["phases"]["delete"].get("min_age", ""))'
	myOUT=$(cd "${myTPOTDIR}" 2>/dev/null && python3 -c "${myCODE}" "$1" 2>/dev/null) || myRC=$?
	if [ "${myRC}" -ne 0 ] && [ "${myRC}" -ne 3 ];
	  then
	    myRC=0
	    myOUT=$(python3 -c "${myCODE}" "$1" 2>/dev/null) || myRC=$?
	fi
	echo "${myOUT}"
	case "${myRC}" in
	  0) return 0 ;;
	  3) return 1 ;;
	  *) return 2 ;;
	esac
}

# What putting the ILM policy of the archive back would delete, as the text of the question; empty
# when it needs none: the policy deletes nothing, or it keeps the events whole days and not less
# long than the current one (none, off or not in whole days: forever). Everything else asks.
function fuILM_LOSS () {   # $1 = the policy of the archive
	local myNEW="" myOLD="" myNOW="" myMINAGE="" myDELETES=0 myCODE=""
	myMINAGE=$(fuPOLICY_DELETES "$1") || myDELETES=$?
	[ "${myDELETES}" -ne 1 ] || return 0
	myNEW=$(fuPOLICY_VALUE "$1") || myNEW=""
	case "${myNEW}" in
	  ""|off|*[!0-9]*)
	    if [ "${myDELETES}" -eq 0 ];
	      then
	        echo "The ILM policy of the archive deletes events, but not after whole days (min_age ${myMINAGE:-not set}): restoring it may delete older events right away."
	      else
	        echo "How long the ILM policy of the archive keeps the events cannot be read here, putting it back may delete older events right away."
	    fi
	    return 0 ;;
	esac
	# GET wraps the policy in its name, value-of takes it as update.sh stores it
	# 404: there is none; any other answer (503 while the cluster starts, no answer): it cannot be read.
	# Both count as forever, so the question comes either way, only its text differs.
	myCODE=$(curl -s --connect-timeout 5 -m 30 -o "${myTMPDIR}/ilm_current_raw.json" -w '%{http_code}' \
	           "${myES}/_ilm/policy/tpot" 2>/dev/null) || myCODE="000"
	case "${myCODE}" in
	  200)
	    if python3 -c 'import json, sys; json.dump({"policy": json.load(open(sys.argv[1]))["tpot"]["policy"]}, open(sys.argv[2], "w"))' \
	         "${myTMPDIR}/ilm_current_raw.json" "${myTMPDIR}/ilm_current.json" 2>/dev/null;
	      then
	        myOLD=$(fuPOLICY_VALUE "${myTMPDIR}/ilm_current.json")
	    fi
	    myNOW="the current one $(fuRETENTION_SHOWN "${myOLD}")" ;;
	  404)
	    myNOW="Elasticsearch has none now" ;;
	  *)
	    myNOW="the current one could not be read (HTTP ${myCODE})" ;;
	esac
	fuRETENTION_SHORTER "${myOLD}" "${myNEW}" || return 0
	echo "The ILM policy of the archive keeps the events $(fuRETENTION_SHOWN "${myNEW}"), ${myNOW}: putting it back deletes older events right away."
}

# May the ILM policy of the archive go back? Without a loss of data (fuILM_LOSS) yes, otherwise only
# once that is confirmed (fuLOSS_CONFIRM). rc 0: put it back.
function fuILM_CONFIRMED () {   # $1 = the policy of the archive
	local myTEXT=""
	myTEXT=$(fuILM_LOSS "$1")
	[ -n "${myTEXT}" ] || return 0
	fuLOSS_CONFIRM ilm "${myTEXT}" "Put the ILM policy of the archive back?"
}

# The Python that runs tpotctl/kibana_objects.py of the checkout (standard library only): the one of the
# venv of tpot, else python3. Prints it; rc 1 when there is none that knows its restore.
function fuKO_PYTHON () {
	local myPY=""
	[ -f "${myTPOTDIR}/tpotctl/kibana_objects.py" ] || return 1
	for myPY in "${TPOT_VENV_PYTHON:-${HOME}/.local/share/tpotce/venv/bin/python}" python3;
	  do
	    command -v "${myPY}" >/dev/null 2>&1 || continue
	    if "${myPY}" -I "${myTPOTDIR}/tpotctl/kibana_objects.py" restore --help >/dev/null 2>&1;
	      then
	        echo "${myPY}"
	        return 0
	    fi
	done
	return 1
}

# A restore of the Kibana objects through the module: it waits for an import of tpotinit's job (up to
# 15 min), exports into ~/tpot_backups, imports and drops the records of what it restored. Its lines
# go into $3 and to stdout (the log).
function fuKO_RESTORE () {   # $1 = python, $2 = the export, $3 = its output
	local myRC=0
	"$1" -I "${myTPOTDIR}/tpotctl/kibana_objects.py" restore --data "$(fuDATA_PATH)" \
	  --objects "${myTPOTDIR}/docker/tpotinit/dist/etc/objects" --exports "${HOME}/tpot_backups" \
	  --kibana "${myKIBANA}" --wait 900 "$2" > "$3" 2>&1 || myRC=$?
	cat "$3"
	return "${myRC}"
}

function fuDO_ELASTIC () {
	local myKO_PYTHON=""
	local myTEXT=""
	local myWAIT=0
	local myOBJ=0
	local myKEEP=""
	local mySINCE="${SECONDS}"
	local myRC=0
	local myKIBANA_FILE="${myTMPDIR}/elastic/kibana_export.ndjson"
	local myILM_FILE="${myTMPDIR}/elastic/ilm_policy_tpot.json"
	local myPUT_HINT="curl -X PUT '${myES}/_ilm/policy/tpot' -H 'Content-Type: application/json' -d @"
	local myBAD=""
	local myKIBANA_UP=""
	local myMINAGE=""
	[ -z "${myDO_ELASTIC}" ] && return
	fuMARK phase elastic Importing the Kibana objects and the ILM policy
	echo
	fuUI_INFO "Importing the Elasticsearch state ..."
	rm -rf "${myTMPDIR}/elastic"
	if ! tar xf "${myARCHIVE}" -C "${myTMPDIR}" elastic 2>/dev/null;
	  then
	    fuUI_ERROR "Could not read elastic/ from the archive."
	    fuFAILED elastic
	    return 1
	fi
	if [ ! -s "${myKIBANA_FILE}" ] && [ ! -s "${myILM_FILE}" ];
	  then
	    fuUI_HINT "elastic/ of the archive holds no Kibana objects and no ILM policy, nothing to import."
	fi
	# The Kibana objects, only when the archive has them: the ILM policy below needs Elasticsearch
	# alone and goes on when Kibana does not answer
	if [ -s "${myKIBANA_FILE}" ];
	  then
	    fuUI_SPIN "Waiting for Kibana on ${myKIBANA} ..." "${myLOG}" fuWAIT_UP "${myKIBANA}/api/status" Kibana || myRC=$?
	    [ "${myRC}" -ne 130 ] || fuSTOPPED "waiting for Kibana"
	    myWAIT=$((SECONDS - mySINCE))
	    if ! curl -s -f -o /dev/null --connect-timeout 3 --max-time 10 "${myKIBANA}/api/status";
	      then
	        fuUI_ERROR "Kibana does not answer, the Kibana objects are not imported."
	        fuFAILED elastic
	        myBAD="1"
	        # the working directory is about to vanish: for the manual route the file stays, and the
	        # command names the real path
	        if fuKEEP_ELASTIC kibana_export.ndjson;
	          then
	            fuUI_HINT "Kibana did not come up in ${myWAIT}s. The Kibana objects are in ${myKEEP}, import them once it does:"
	            fuUI_HINT "  curl -X POST '${myKIBANA}/api/saved_objects/_import?overwrite=true' -H 'kbn-xsrf: true' --form file=@${myKEEP}/kibana_export.ndjson"
	          else
	            fuUI_HINT "Kibana did not come up in ${myWAIT}s. The Kibana objects are in the archive under elastic/kibana_export.ndjson, a POST to ${myKIBANA}/api/saved_objects/_import?overwrite=true imports them."
	        fi
	      else
	        fuUI_OK "Kibana answers after ${myWAIT}s."
	        myKIBANA_UP="1"
	    fi
	fi
	if [ -n "${myKIBANA_UP}" ] && myKO_PYTHON=$(fuKO_PYTHON);
	  then
	    # through tpotctl/kibana_objects.py: under the import lock of tpotinit's job (which may import right
	    # now, after the same start), an export into ~/tpot_backups first, and the restored objects are no
	    # T-Pot import any more, so the next start keeps them
	    myRC=0
	    fuUI_SPIN "Importing the Kibana objects (waits up to 15 minutes for an import of T-Pot that runs) ..." "${myLOG}" \
	      fuKO_RESTORE "${myKO_PYTHON}" "${myTMPDIR}/elastic/kibana_export.ndjson" "${myTMPDIR}/import.txt" || myRC=$?
	    [ "${myRC}" -ne 130 ] || fuSTOPPED "importing the Kibana objects"
	    myOBJ=$(sed -n 's/^# Kibana objects: \([0-9]*\) restored.*/\1/p' "${myTMPDIR}/import.txt" 2>/dev/null | head -1)
	    if [ "${myRC}" -eq 0 ];
	      then
	        fuUI_OK "Imported ${myOBJ:-0} Kibana objects (the ones before are exported into ~/tpot_backups)."
	    elif [ -n "${myOBJ}" ];
	      then
	        fuUI_WARN "Imported ${myOBJ} Kibana objects, errors reported:"
	        fuFAILED elastic
	        myBAD="1"
	        sed -n 's/^# Kibana objects: not imported: /    /p' "${myTMPDIR}/import.txt" | head -5
	      else
	        fuUI_ERROR "The Kibana objects could not be imported."
	        fuFAILED elastic
	        myBAD="1"
	        sed 's/^# Kibana objects: /    /' "${myTMPDIR}/import.txt" 2>/dev/null | head -5
	    fi
	elif [ -n "${myKIBANA_UP}" ];
	  then
	    fuUI_WARN "Python 3 or tpotctl/kibana_objects.py is missing, the Kibana objects go in with curl: the restored objects are not protected from tpotinit's next import."
	    fuWARNED elastic "the Kibana objects went in with curl: tpotinit's next import may replace the restored ones"
	    myRC=0
	    fuUI_SPIN "Importing the Kibana objects ..." "${myLOG}" \
	      curl -s -S -f -X POST "${myKIBANA}/api/saved_objects/_import?overwrite=true" \
	      -H "kbn-xsrf: true" --form file=@"${myTMPDIR}/elastic/kibana_export.ndjson" \
	      -o "${myTMPDIR}/import.json" || myRC=$?
	    [ "${myRC}" -ne 130 ] || fuSTOPPED "importing the Kibana objects"
	    if [ "${myRC}" -eq 0 ];
	      then
	        myOBJ=$(sed -n 's/.*"successCount":\([0-9]*\).*/\1/p' "${myTMPDIR}/import.json")
	        if grep -q '"success":true' "${myTMPDIR}/import.json";
	          then
	            fuUI_OK "Imported ${myOBJ:-0} Kibana objects."
	          else
	            fuUI_WARN "Imported ${myOBJ:-0} Kibana objects, errors reported:"
	            fuFAILED elastic
	            myBAD="1"
	            sed 's/^/    /' "${myTMPDIR}/import.json" | head -5
	        fi
	      else
	        fuUI_ERROR "The Kibana objects could not be imported."
	        fuFAILED elastic
	        myBAD="1"
	    fi
	fi
	# The ILM policy, after the Kibana part whatever came of it: it needs Elasticsearch only
	if [ -s "${myILM_FILE}" ];
	  then
	    mySINCE="${SECONDS}"
	    myRC=0
	    fuUI_SPIN "Waiting for Elasticsearch on ${myES} ..." "${myLOG}" fuWAIT_UP "${myES}" Elasticsearch || myRC=$?
	    [ "${myRC}" -ne 130 ] || fuSTOPPED "waiting for Elasticsearch"
	    myWAIT=$((SECONDS - mySINCE))
	    if ! curl -s -f -o /dev/null --connect-timeout 3 --max-time 10 "${myES}";
	      then
	        fuUI_ERROR "Elasticsearch does not answer, the ILM policy is not put back."
	        fuFAILED elastic
	        myBAD="1"
	        if fuKEEP_ELASTIC ilm_policy_tpot.json;
	          then
	            fuUI_HINT "Elasticsearch did not come up in ${myWAIT}s. The ILM policy is in ${myKEEP}, put it back once it does:"
	            # how long the current policy keeps the events cannot be read now: the PUT may shorten it
	            if myMINAGE=$(fuPOLICY_DELETES "${myILM_FILE}") || [ $? -ne 1 ];
	              then
	                fuUI_WARN "The ILM policy of the archive deletes events (min_age ${myMINAGE:-not set}): if the current one keeps them longer, the PUT below deletes older events right away, run it only if you accept it."
	            fi
	            fuUI_HINT "  ${myPUT_HINT}${myKEEP}/ilm_policy_tpot.json"
	          else
	            fuUI_HINT "Elasticsearch did not come up in ${myWAIT}s. The ILM policy is in the archive under elastic/ilm_policy_tpot.json, a PUT to ${myES}/_ilm/policy/tpot puts it back."
	        fi
	    elif ! fuILM_CONFIRMED "${myILM_FILE}";
	      then
	        # kept for the manual route, the working directory is about to vanish
	        fuUI_WARN "The ILM policy of the archive was not put back: not confirmed."
	        if fuKEEP_ELASTIC ilm_policy_tpot.json;
	          then
	            fuUI_HINT "It deletes the older events right away. To put it back yourself:" \
	                      "  ${myPUT_HINT}${myKEEP}/ilm_policy_tpot.json"
	          else
	            fuUI_HINT "It is in the archive under elastic/ilm_policy_tpot.json, a PUT to ${myES}/_ilm/policy/tpot puts it back."
	        fi
	        fuWARNED elastic "the ILM policy of the archive was not put back: not confirmed, see above"
	    else
	        # update.sh stored it ready to be put back, so this is a plain PUT against the
	        # Elasticsearch port every edition that ships it publishes on the loopback.
	        if curl -s -f -X PUT "${myES}/_ilm/policy/tpot" \
	             -H "Content-Type: application/json" \
	             -d @"${myILM_FILE}" -o /dev/null;
	          then
	            fuUI_OK "The ILM policy is back."
	          else
	            fuUI_WARN "Could not put the ILM policy back, the file is in the archive under elastic/."
	            fuFAILED elastic
	            myBAD="1"
	        fi
	    fi
	fi
	[ -z "${myBAD}" ]
}

# The end of a restore: one line per chosen group in a summary, exit 1 when one of
# them did not come back (the rest is through then)
function fuEND () {
	local myGROUP="" myDO="" myITEMS=() myRC=0 myWARN="" myITEM="" myBACK=""
	for myGROUP in git patch config untracked data elastic;
	  do
	    case "${myGROUP}" in
	      git) myDO="${myDO_GIT}" ;;
	      patch) myDO="${myDO_PATCH}" ;;
	      config) myDO="${myDO_CONFIG}" ;;
	      untracked) myDO="${myDO_UNTRACKED}" ;;
	      data) myDO="${myDO_DATA}${myDATA_REFUSED}" ;;
	      elastic) myDO="${myDO_ELASTIC}" ;;
	    esac
	    [ -n "${myDO}" ] || continue
	    myWARN=""
	    for myITEM in "${myWARNED[@]}";
	      do
	        [ "${myITEM%%:*}" != "${myGROUP}" ] || myWARN="${myITEM#*:}"
	    done
	    case ", ${myFAILED}, " in
	      *", ${myGROUP}, "*) myITEMS+=("fail:$(fuGROUP_TITLE "${myGROUP}"): not restored, see above") ;;
	      *) myBACK="1"
	         if [ -n "${myWARN}" ];
	           then myITEMS+=("warn:$(fuGROUP_TITLE "${myGROUP}"): ${myWARN}")
	           else myITEMS+=("ok:$(fuGROUP_TITLE "${myGROUP}")")
	         fi ;;
	    esac
	done
	if [ -n "${myFAILED}" ];
	  then
	    myRC=1
	    # "the rest" only where a chosen group did come back
	    myITEMS+=("info:Not restored: ${myFAILED}.${myBACK:+ The rest is back.} The archive is ${myARCHIVE}.")
	fi
	if [ -n "${mySTART_FAILED}" ];
	  then
	    myITEMS+=("warn:T-Pot did not start, please start it yourself.")
	elif [ -z "${myDO_ELASTIC}" ] && [ -z "${myCONFIG_ONLY}" ] && [ -n "${myDO_GIT}${myDO_CONFIG}${myDO_PATCH}${myDO_UNTRACKED}${myDO_DATA}" ];
	  then
	    myITEMS+=("next:Start T-Pot with 'sudo systemctl start tpot' or 'docker compose up -d'.")
	fi
	fuMARK phase "done" Done
	fuUI_SUMMARY "Restored from $(basename "${myARCHIVE}")" "${myITEMS[@]}"
	exit "${myRC}"
}

# Sourced (the tests do, for the functions): no run
[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0

################
# Main section #
################

# The long options first: the option loop below knows only the short ones
myARGV=()
myACCEPT_FILE_NEXT=""
myACCEPT_FILE_GIVEN=""
for myARG in "$@";
  do
    if [ -n "${myACCEPT_FILE_NEXT}" ];
      then
        myACCEPT_FILE="${myARG}"
        myACCEPT_FILE_GIVEN=1
        myACCEPT_FILE_NEXT=""
        continue
    fi
    case "${myARG}" in
      --accept-data-loss) myACCEPT_LOSS=1 ;;
      --accept-data-loss-file) myACCEPT_FILE_NEXT=1 ;;
      --help) myARGV+=("-h") ;;
      --?*)   fuUI_USAGE_ERROR "Unknown option ${myARG}." restore.sh; exit 1 ;;
      *)      myARGV+=("${myARG}") ;;
    esac
done
if [ -n "${myACCEPT_FILE_NEXT}" ];
  then
    fuUI_USAGE_ERROR "Option --accept-data-loss-file requires a file." restore.sh
    exit 1
fi
set -- "${myARGV[@]}"

while getopts ":lf:ycg:B:h" opt; do
  case "$opt" in
    l)
      myLIST="1"
      ;;
    f)
      myARCHIVE="${OPTARG}"
      ;;
    y)
      myCONFIRMED="y"
      ;;
    c)
      myCONFIG_ONLY="1"
      ;;
    g)
      myGROUPS="${OPTARG}"
      ;;
    B)
      myBECOME_FILE="${OPTARG}"
      ;;
    h)
      fuPRINT_HELP
      ;;
    :)
      fuUI_USAGE_ERROR "Option -${OPTARG} requires an argument." restore.sh
      exit 1
      ;;
    \?)
      fuUI_USAGE_ERROR "Unknown option -${OPTARG}." restore.sh
      exit 1
      ;;
  esac
done

# A T-Pot host is Linux (WSL2 too): elsewhere it stops before any sudo, docker or git
fuUI_LINUX_ONLY restore.sh

# -g: only known groups
for myGROUP in ${myGROUPS//,/ };
  do
    case "${myGROUP}" in
      git|patch|config|untracked|data|elastic) ;;
      *) fuUI_USAGE_ERROR "There is no group ${myGROUP}, the groups are git, patch, config, untracked, data and elastic." restore.sh
         exit 1 ;;
    esac
done

# -B: every sudo of this run refreshes the timestamp from the file first
if [ -n "${myBECOME_FILE}" ];
  then
    if [ ! -r "${myBECOME_FILE}" ];
      then
        fuUI_ERROR "Cannot read the sudo password from ${myBECOME_FILE}."
        exit 1
    fi
    myBECOME_FILE=$(cd "$(dirname "${myBECOME_FILE}")" && pwd)/$(basename "${myBECOME_FILE}")
    sudo () { command sudo -S -p "" -v < "${myBECOME_FILE}" >/dev/null 2>&1; command sudo "$@"; }
fi

# --accept-data-loss-file: the confirmed losses, read where fuLOSS_CONFIRM asks
if [ -n "${myACCEPT_FILE_GIVEN}" ];
  then
    if [ -z "${myACCEPT_FILE}" ] || [ ! -f "${myACCEPT_FILE}" ] || [ ! -r "${myACCEPT_FILE}" ];
      then
        fuUI_ERROR "Cannot read the confirmed losses of data from ${myACCEPT_FILE}."
        exit 1
    fi
    myACCEPT_FILE=$(cd "$(dirname "${myACCEPT_FILE}")" && pwd)/$(basename "${myACCEPT_FILE}")
fi

fuUI_BANNER "Restorer" "Brings back a backup of update.sh, or the parts of it you choose."

fuTMPDIR
[ -n "${myLIST}" ] && fuLIST

if [ ! -d "${myTPOTDIR}" ];
  then
    fuUI_ERROR "There is no ${myTPOTDIR}."
    echo
    exit 1
fi

fuPICK_ARCHIVE

if [ -n "${myCONFIG_ONLY}" ];
  then
    fuUI_INFO "Restoring the checkout and the configuration from this archive."
    fuHAS "^rollback.txt$"   && myDO_GIT="1"
    fuHAS "^tracked.patch$"  && myDO_PATCH="1"
    fuHAS "^env$"            && myDO_CONFIG="1"
elif [ -n "${myGROUPS}" ];
  then
    fuUI_INFO "Restoring ${myGROUPS//,/, } from this archive."
    for myGROUP in ${myGROUPS//,/ };
      do
        case "${myGROUP}" in
          git)       fuHAS "^rollback.txt$"  && myDO_GIT="1" ;;
          patch)     fuHAS "^tracked.patch$" && myDO_PATCH="1" ;;
          config)    fuHAS "^env$"           && myDO_CONFIG="1" ;;
          untracked) fuHAS "^untracked/"     && myDO_UNTRACKED="1" ;;
          data)      fuHAS "^data/"          && myDO_DATA="1" ;;
          elastic)   fuHAS "^elastic/"       && myDO_ELASTIC="1" ;;
        esac || fuUI_WARN "The archive has no ${myGROUP}, skipping it."
    done
    if [ -z "${myDO_GIT}${myDO_CONFIG}${myDO_PATCH}${myDO_UNTRACKED}${myDO_DATA}${myDO_ELASTIC}" ];
      then
        fuUI_INFO "Nothing to restore, leaving everything as it is."
        echo
        exit 0
    fi
elif [ -n "${myCONFIRMED}" ];
  then
    fuUI_INFO "Restoring everything from this archive."
    fuHAS "^rollback.txt$"   && myDO_GIT="1"
    fuHAS "^env$"            && myDO_CONFIG="1"
    fuHAS "^tracked.patch$"  && myDO_PATCH="1"
    fuHAS "^untracked/"      && myDO_UNTRACKED="1"
    fuHAS "^data/"           && myDO_DATA="1"
    fuHAS "^elastic/"        && myDO_ELASTIC="1"
  else
    fuCHOOSE
fi

# data/ that is refused for sure does not stop T-Pot: it drops out here, before the stop, and the rest
# of the groups goes on (the summary names it)
myDATA_REFUSED=""
if [ -n "${myDO_DATA}" ] && fuDATA_REFUSED;
  then
    fuUI_WARN "data/ was not restored: not confirmed. Restoring it replaces the current files and the Elasticsearch data, newer files and events since the backup are lost."
    [ -z "${myACCEPT_FILE}" ] || fuUI_HINT "--accept-data-loss-file does not confirm it: it names no 'data' loss."
    fuUI_HINT "Not confirmed: no terminal. --accept-data-loss confirms it, -y does not."
    fuWARNED data "not restored: not confirmed, see above"
    myDATA_REFUSED="1"
    myDO_DATA=""
fi

# Phase 1 - everything that needs T-Pot stopped. If only the Elasticsearch import
# was picked there is nothing to do here and T-Pot can keep running.
if [ -n "${myDO_GIT}${myDO_CONFIG}${myDO_PATCH}${myDO_UNTRACKED}${myDO_DATA}" ];
  then
    fuSTOP_TPOT
fi
fuDO_GIT
fuDO_PATCH
fuDO_CONFIG
fuDO_UNTRACKED
fuDO_DATA

# Phase 2 - the Elasticsearch import needs a running instance
if [ -n "${myDO_ELASTIC}" ];
  then
    # If T-Pot kept running there is nothing to start
    if [ -n "${myDO_GIT}${myDO_CONFIG}${myDO_PATCH}${myDO_UNTRACKED}${myDO_DATA}" ];
      then
        fuSTART_TPOT
    fi
    fuDO_ELASTIC
fi
fuEND
