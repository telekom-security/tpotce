#!/bin/bash

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
    fuUI_BANNER () { local myLINE; echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE}"; done; echo; }
    fuUI_INFO () { echo "### $*"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuUI_MARKS_ON () { [ -n "${myMARKS:-}" ] || [ "${TPOT_MARKS:-}" = "1" ]; }
    fuMARK () { fuUI_MARKS_ON || return 0; echo "@@tpot $*"; }
    fuUI_LOGO () { return 1; }
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
        myPICK="${myPICK//[[:space:]]/}"
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

# How long to wait for Kibana before printing the commands to run by hand. Weaker
# hardware is allowed to take longer.
myKIBANA_TIMEOUT="${TPOT_KIBANA_TIMEOUT:-300}"
myTMPDIR=""

# What to restore. Empty means: ask.
myDO_GIT=""
myDO_CONFIG=""
myDO_PATCH=""
myDO_UNTRACKED=""
myDO_DATA=""
myDO_ELASTIC=""

function fuPRINT_HELP () {
	# at a terminal the T-Pot logo first, as a run shows it
	fuUI_LOGO
	fuUI_HELP "Restorer" "restore.sh [-l] [-f <archive>] [-y | -c | -g <groups>] [-B <file>]" \
	  --about "Restores a backup written by update.sh. Without -y, -c or -g it lists what the archive
holds and you choose what comes back, so you can bring back just the configuration
without touching anything else." \
	  --opt "-l" "List the available backups and what they hold" \
	  --opt "-f <archive>" "Restore from this archive. Default: the newest one in
${myBACKUPDIR}" \
	  --opt "-y" "Restore everything without asking, including the rollback
of the git checkout" \
	  --opt "-c" "Only roll the checkout back and restore the configuration
(.env, docker-compose.yml, your changes to tracked files),
without asking. Leaves data/ alone and does not start T-Pot." \
	  --opt "-g <groups>" "Restore these groups without asking, comma separated: git,
patch, config, untracked, data, elastic (the T-Pot Manager uses it)" \
	  --opt "-B <file>" "Read the sudo password from a file, so it runs without
asking for it (the T-Pot Manager hands one over)" \
	  --opt "-h" "Show this help message" \
	  --example "./restore.sh -l" "The backups and what each one holds" \
	  --example "./restore.sh" "Choose what of the newest backup comes back" \
	  --example "./restore.sh -f ~/tpot_backups/<archive>.tar -g config" "Only .env and docker-compose.yml of that archive" \
	  --note "T-Pot is stopped for everything but the Kibana objects and the ILM policy, which
need it running: restore.sh starts it for them. tpot restore hands every option on."
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
	local myFILE=""
	[ -z "${myDO_CONFIG}" ] && return
	fuMARK phase config Restoring the configuration
	echo
	fuUI_INFO "Restoring the configuration ..."
	if tar xf "${myARCHIVE}" -C "${myTMPDIR}" env 2>/dev/null && cp "${myTMPDIR}/env" "${myTPOTDIR}/.env";
	  then
	    fuUI_OK "Wrote ${myTPOTDIR}/.env."
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
function fuDO_DATA () {
	[ -z "${myDO_DATA}" ] && return
	fuMARK phase data Restoring the files from data/
	echo
	fuUI_INFO "Restoring the files from data/ ..."
	# Elasticsearch data cannot be merged: extracting over a data folder that a
	# newer version has already upgraded leaves its files behind, and the older
	# Elasticsearch of the archive refuses to start on that mix.
	if fuHAS "^data/elk/data/";
	  then
	    if sudo rm -rf "${myTPOTDIR}/data/elk/data";
	      then
	        fuUI_OK "Removed the current data/elk/data, the archive brings its own."
	      else
	        fuUI_ERROR "Could not remove the current data/elk/data."
	        fuFAILED data
	    fi
	fi
	# under the spinner, a full archive takes a while: sudo is refreshed first
	sudo -v
	local myRC=0
	fuUI_SPIN "Extracting data/ from the archive ..." "${myLOG}" \
	  sudo tar xf "${myARCHIVE}" -C "${myTPOTDIR}" -p --numeric-owner --wildcards "data/*" || myRC=$?
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
# Under the spinner: wait for Kibana, up to myKIBANA_TIMEOUT seconds
function fuWAIT_KIBANA () {
	local myWAIT=0
	while [ "${myWAIT}" -lt "${myKIBANA_TIMEOUT}" ];
	  do
	    curl -s -f -o /dev/null --connect-timeout 3 "${myKIBANA}/api/status" && return 0
	    sleep 5
	    myWAIT=$((myWAIT+5))
	done
	curl -s -f -o /dev/null "${myKIBANA}/api/status"
}

function fuDO_ELASTIC () {
	local myWAIT=0
	local myOBJ=0
	local myKEEP=""
	local mySINCE="${SECONDS}"
	local myRC=0
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
	fuUI_SPIN "Waiting for Kibana on ${myKIBANA} ..." "${myLOG}" fuWAIT_KIBANA || myRC=$?
	[ "${myRC}" -ne 130 ] || fuSTOPPED "waiting for Kibana"
	myWAIT=$((SECONDS - mySINCE))
	if ! curl -s -f -o /dev/null "${myKIBANA}/api/status";
	  then
	    fuUI_ERROR "Kibana does not answer."
	    fuFAILED elastic
	    # The files sit in the working directory, which is about to vanish - for the
	    # manual route they have to stay, and the commands have to name the real path.
	    myKEEP="${myBACKUPDIR}/${myDATE}_elastic"
	    mkdir -p "${myKEEP}" && cp -a "${myTMPDIR}/elastic/." "${myKEEP}/" 2>/dev/null
	    fuUI_HINT "Kibana did not come up in ${myWAIT}s. The files are in ${myKEEP}, run these once it does:"
	    fuUI_HINT "  curl -X POST '${myKIBANA}/api/saved_objects/_import?overwrite=true' -H 'kbn-xsrf: true' --form file=@${myKEEP}/kibana_export.ndjson"
	    fuUI_HINT "  curl -X PUT '${myES}/_ilm/policy/tpot' -H 'Content-Type: application/json' -d @${myKEEP}/ilm_policy_tpot.json"
	    return 1
	fi
	fuUI_OK "Kibana answers after ${myWAIT}s."
	if [ -s "${myTMPDIR}/elastic/kibana_export.ndjson" ];
	  then
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
	            sed 's/^/    /' "${myTMPDIR}/import.json" | head -5
	        fi
	      else
	        fuUI_ERROR "The Kibana objects could not be imported."
	        fuFAILED elastic
	    fi
	fi
	if [ -s "${myTMPDIR}/elastic/ilm_policy_tpot.json" ];
	  then
	    # update.sh stored it ready to be put back, so this is a plain PUT against the
	    # Elasticsearch port every edition that ships it publishes on the loopback.
	    if curl -s -f -X PUT "${myES}/_ilm/policy/tpot" \
	         -H "Content-Type: application/json" \
	         -d @"${myTMPDIR}/elastic/ilm_policy_tpot.json" -o /dev/null;
	      then
	        fuUI_OK "The ILM policy is back."
	      else
	        fuUI_WARN "Could not put the ILM policy back, the file is in the archive under elastic/."
	        fuFAILED elastic
	    fi
	fi
}

# The end of a restore: one line per chosen group in a summary, exit 1 when one of
# them did not come back (the rest is through then)
function fuEND () {
	local myGROUP="" myDO="" myITEMS=() myRC=0
	for myGROUP in git patch config untracked data elastic;
	  do
	    case "${myGROUP}" in
	      git) myDO="${myDO_GIT}" ;;
	      patch) myDO="${myDO_PATCH}" ;;
	      config) myDO="${myDO_CONFIG}" ;;
	      untracked) myDO="${myDO_UNTRACKED}" ;;
	      data) myDO="${myDO_DATA}" ;;
	      elastic) myDO="${myDO_ELASTIC}" ;;
	    esac
	    [ -n "${myDO}" ] || continue
	    case ", ${myFAILED}, " in
	      *", ${myGROUP}, "*) myITEMS+=("fail:$(fuGROUP_TITLE "${myGROUP}"): not restored, see above") ;;
	      *) myITEMS+=("ok:$(fuGROUP_TITLE "${myGROUP}")") ;;
	    esac
	done
	if [ -n "${myFAILED}" ];
	  then
	    myRC=1
	    myITEMS+=("info:Not restored: ${myFAILED}. The rest is back, the archive is ${myARCHIVE}.")
	fi
	if [ -n "${mySTART_FAILED}" ];
	  then
	    myITEMS+=("warn:T-Pot did not start, please start it yourself.")
	elif [ -z "${myDO_ELASTIC}" ] && [ -z "${myCONFIG_ONLY}" ];
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
