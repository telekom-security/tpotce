#!/bin/bash

# an exported CDPATH turns a cd into a search that prints the folder it found ($(cd ... && pwd)
# gives two lines): every cd of this script means the folder it names
unset CDPATH

# Some global vars
myCOMPOSEFILE="~/tpotce/docker-compose.yml"
myDATE=$(date +%Y%m%d%H%M%S)

# Where the backups live. Its own directory with 0700, because the archives carry
# the credentials from .env.
myBACKUPDIR="${HOME}/tpot_backups"

# What of data/ belongs in the archive: everything that exists only there and
# cannot be reproduced. Missing paths are skipped, `hive.crt` only exists on a
# SENSOR. The password files are deliberately left out - they are generated from
# .env on every start.
myJEWELS="data/uuid
data/hive.crt
data/nginx/cert
data/ews/conf
data/cowrie/keys
data/beelzebub/key
data/galah/cert
data/rdphoneypot/cert"

# How many archives are kept. Regular and full archives rotate separately, so a
# full backup never pushes out the small rollback points and the other way round.
myBACKUP_RETAIN=10
myBACKUP_RETAIN_FULL=2

# Share of the partition that has to stay free after writing. The archive usually
# sits on the same filesystem as data/, so an oversized one takes the disk away
# from the honeypots.
myBACKUP_RESERVE_PERCENT=10

# `--full` takes all of data/ along as well
myFULL=""

# T-Pot images live in these two registries. Anything there that does not carry the
# tag this installation uses is left over from an earlier version - `:dev` images
# from a test run included - and only takes up disk.
myREPOS="dtagdevsec ghcr.io/telekom-security"

# How long the registry may take to confirm that the old images can be pulled again, all of
# them together (fuSORT_IMAGES): one that is not confirmed by then is kept unless confirmed.
myPULLABLE_DEADLINE=90

# Set when the checkout turns out to ship an older release than the running script.
# Nothing after the pull is an update then: the compose file, .env and the image tags
# all belong to the older release. The run puts the configuration back and stops
# rather than pulling that release's images and removing the ones in use.
myOLDER_CHECKOUT=""

# Whether the image pull went through. A failed pull is not fatal on its own, but
# T-Pot cannot start without the images, so it changes what the run reports.
myPULLOK=""

# `-s` brings T-Pot back up at the end. Off by default, so a plain run leaves the
# services stopped the way it always has.
mySTART=""
myBACKUP_ONLY=""

# --check-data-loss: only list what the run would lose for good (fuLOSS_ADD), change nothing.
# --accept-data-loss: confirm every such loss where the run cannot ask (fuLOSS_CONFIRM); -y does not.
# --accept-data-loss-file: confirm only the losses its lines name, as --check-data-loss printed them.
# A loss that is not confirmed ends the run with exit 5, where the run cannot go on without it.
myCHECK_LOSS=""
myACCEPT_LOSS=""
myACCEPT_FILE=""
myLOSSES=0

# --adapt-compose: a docker-compose.yml of your own (no edition, not built by compose/customizer.py) is
# backed up and adapted to the release without a question (fuADOPT_COMPOSE); at a terminal the run asks,
# the choice "adapt" sets it as well, so a restarted update.sh gets it (fuRESTART_ARGS). -y never does.
myADAPT_COMPOSE=""
# the report lines of the last compose/customizer.py --adopt (fuADOPT_RUN), set once the question was
# answered in this run (fuADOPT_COMPOSE), and a Ctrl+C at that question (a no)
myADOPT_LINES=()
myADOPT_DECIDED=""
myADOPT_INT=""
# what the adaptation after the pull loses beyond the list shown before the choice (fuADOPT_GREW)
myADOPT_NEW=()

# Both are published on the loopback interface by every edition that ships them,
# so neither needs a detour through a container.
myKIBANA="http://127.0.0.1:64296"
myES="http://127.0.0.1:64298"

# How long to wait for a service this installation runs but that is not answering
# yet. Kibana needs about a minute after a start before it does, and an update run
# right after a reboot would otherwise walk away without the saved objects.
myELASTIC_GRACE="${TPOT_ELASTIC_GRACE:-120}"

# Elasticsearch 9 reads only the data of 8.19, the last minor of 8: the data of an older 8 goes
# through the official images of this version once (fuES_STEP_819), Kibana migrates its saved
# objects there. Their containers and network, the ports on the loopback interface (free while
# T-Pot is stopped), the room the images need in the Docker root, how long the step may take
# (seconds, TPOT_ES_STEP_WAIT: 60 to 86400, checked with the options), how long its cluster may
# answer red in a row (seconds) and the pause between two looks.
myES_STEP_VERSION="8.19.23"
myES_STEP_NAME="tpot_es_step"
myKB_STEP_NAME="tpot_kb_step"
myES_STEP_ES="http://127.0.0.1:64398"
myES_STEP_KB="http://127.0.0.1:64396"
myES_STEP_SPACE=4294967296
myES_STEP_WAIT="${TPOT_ES_STEP_WAIT:-1200}"
myES_STEP_RED=120
myES_STEP_PAUSE=5

# Working directory, cleaned up when the script ends
myTMPDIR=""
# The archive of an earlier updater (the tag 24.04.1, master of 2026-08-24) as read in this run (fuOLD_UPDATER_READ) and the folder in
# myTMPDIR with its list of members, its .env and its docker-compose.yml
myOLD_READ=""
myOLD_DIR=""
# The archive of that updater when fuOLD_UPDATER_CONFIG found your configuration back from it in this run:
# that updater has stopped T-Pot and pulled the new release already (fuADOPT_CANCEL)
myOLD_UPDATER_RAN=""
# The sudo password from -B, empty: sudo asks itself (or needs none)
myBECOME_FILE=""

# What runs under a spinner writes its output here, the log of the last run (in the
# marks mode of the task screen it goes through instead, fuUI_SPIN)
myLOG="${myBACKUPDIR}/update.log"

# The summary at the end (fuEND): what the run did, as <kind>:<text> items of
# fuUI_SUMMARY. myRUNNING is set once the update is confirmed, from then on every way
# out ends with it; mySTOPPED while T-Pot is stopped.
myDONE=()
myEND_TITLE=""
myRUNNING=""
mySTOPPED=""
# set once fuSTART_TPOT brought T-Pot up again in this run
mySTARTED=""
# systemctl is-active tpot when this run started (main, not after a handover): a refusal starts T-Pot
# again only when it ran then (fuRESTART_AFTER_REFUSAL)
myWAS_ACTIVE=""

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
    fuUI_CHOOSE () {
      local myI=1 myITEM myPICK myDEFAULT="" myVALUE=""
      if [ "${1:-}" = "--selected" ]; then
        myVALUE="${2-}" myDEFAULT=0
        shift $(( $# < 2 ? $# : 2 ))
      fi
      local myHEADER="${1:-}"
      [ "$#" -eq 0 ] || shift
      if [ -n "${myDEFAULT}" ]; then
        myDEFAULT=""
        for myITEM in "$@"; do
          if [ "${myITEM##*:}" = "${myVALUE}" ]; then myDEFAULT="${myI}"; break; fi
          myI=$((myI + 1))
        done
        myI=1
      fi
      echo "### ${myHEADER}" >&2
      for myITEM in "$@"; do
        echo "###   ${myI}) ${myITEM%:*}" >&2
        myI=$((myI + 1))
      done
      while true; do
        if [ -n "${myDEFAULT}" ];
          then read -rp "### Choice (1-$#, enter = ${myDEFAULT}): " myPICK || return 1
          else read -rp "### Choice (1-$#): " myPICK || return 1
        fi
        # the spaces around it go, ones in it make it no number (1 2 is not 12)
        myPICK="${myPICK#"${myPICK%%[![:space:]]*}"}"
        myPICK="${myPICK%"${myPICK##*[![:space:]]}"}"
        [ -n "${myPICK}" ] || myPICK="${myDEFAULT}"
        [[ "${myPICK}" =~ ^[0-9]{1,9}$ ]] && myPICK=$((10#${myPICK})) || myPICK=0
        if [ "${myPICK}" -ge 1 ] && [ "${myPICK}" -le "$#" ];
          then
            myITEM="${!myPICK}"
            echo "${myITEM##*:}"
            return 0
        fi
      done
    }
    fuUI_MARKS_ON () { [ -n "${myMARKS:-}" ] || [ "${TPOT_MARKS:-}" = "1" ]; }
    fuMARK () { fuUI_MARKS_ON || return 0; echo "@@tpot $*"; }
    fuUI_VERSION_GE () {
      local myI myX myY myN myV="${1//[[:space:]]/}" myM="${2//[[:space:]]/}"
      local -a myA=() myB=()
      myV="${myV#v}"
      myV="${myV%%[-+]*}"
      myM="${myM#v}"
      myM="${myM%%[-+]*}"
      [ -n "${myV}" ] && [ -n "${myM}" ] || return 1
      case "${myV}.${myM}" in .*|*.|*..*) return 1 ;; esac
      IFS=. read -r -a myA <<< "${myV}"
      IFS=. read -r -a myB <<< "${myM}"
      myN="${#myA[@]}"
      [ "${#myB[@]}" -le "${myN}" ] || myN="${#myB[@]}"
      for ((myI = 0; myI < myN; myI++)); do
        myX="${myA[myI]-0}" myY="${myB[myI]-0}"
        [[ "${myX}" =~ ^[0-9]{1,18}$ && "${myY}" =~ ^[0-9]{1,18}$ ]] || return 1
        if [ "$((10#${myX}))" -gt "$((10#${myY}))" ]; then return 0; fi
        if [ "$((10#${myX}))" -lt "$((10#${myY}))" ]; then return 1; fi
      done
      return 0
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
# the T-Pot logo in the banner (at a terminal, not in the marks mode), in the first pass
# only: the restart after the self update is mid-run and shows the banner only. It knows
# itself by the handover (TPOT_UPDATE_PREPARED, see fuSELFUPDATE); TPOT_LOGO_SHOWN is not
# enough, the update.sh of an earlier release (without the logo) never sets it
# shellcheck disable=SC2034 # read by fuUI_BANNER (installer/lib/ui.sh)
[ -n "${TPOT_UPDATE_PREPARED}" ] || myUI_LOGO=1

# Where to update from. Empty means: keep the branch and the origin of the
# current checkout, which is what a plain `update.sh -y` has always done.
myTPOT_BRANCH="${TPOT_BRANCH}"
myTPOT_REPO_URL="${TPOT_REPO_URL}"
myTPOT_SOURCE_GIVEN=""

# The installed T-Pot edition. It only exists as the marker comment at the top of
# docker-compose.yml (fuEDITION_LINE), a tracked file, so the update overwrites it with the
# STANDARD edition. Everything needed to put it back is read before the update and
# handed over to a restarted update.sh through the environment, see fuSELFUPDATE.
# A restarted update.sh inherits what the first pass already did, so it neither
# stops T-Pot again nor writes a second backup - and it restores from the FIRST
# archive, the only one taken before the checkout was reset.
myPREPARED="${TPOT_UPDATE_PREPARED}"
myARCHIVE="${TPOT_UPDATE_ARCHIVE}"

myEDITION="${TPOT_UPDATE_EDITION}"
myCOMPOSE_CUSTOMIZED="${TPOT_UPDATE_COMPOSE_CUSTOMIZED}"
# set when the edition could not be put in place (fuRESTORE_EDITION), the summary does not claim it then
myEDITION_MISSING=""
myEDITIONS="STANDARD SENSOR MINI LLM TARPIT MOBILE MAC_WIN"

# Services that are no longer part of T-Pot. Their images are not built for this
# release, a docker-compose.yml of your own that still has them fails on the pull.
myDROPPED_SERVICES="spiderfoot fatt tanner_redis tanner_phpox tanner_api tanner snare"

# Is TPOT_ES_STEP_WAIT (myES_STEP_WAIT) whole seconds from 60 to 86400? The step through
# Elasticsearch 8.19 computes with it; anything else is a usage error, rc 1. Not after a handover
# (myPREPARED): the update.sh that started this run has stopped T-Pot and pulled already, a usage
# error would leave it so. The run goes on with 1200 s, the value given is kept in
# myES_STEP_WAIT_GIVEN for the warning once the run has started (fuSTEP_WAIT_WARN)
myES_STEP_WAIT_GIVEN=""
function fuCHECK_STEP_WAIT () {
	if [[ "${myES_STEP_WAIT}" =~ ^[1-9][0-9]{1,4}$ ]] && [ "${myES_STEP_WAIT}" -ge 60 ] && [ "${myES_STEP_WAIT}" -le 86400 ];
	  then
	    return 0
	fi
	if [ -n "${myPREPARED}" ];
	  then
	    myES_STEP_WAIT_GIVEN="${myES_STEP_WAIT}"
	    myES_STEP_WAIT=1200
	    return 0
	fi
	fuUI_USAGE_ERROR "TPOT_ES_STEP_WAIT=${myES_STEP_WAIT} is no whole number of seconds from 60 to 86400." update.sh
}
function fuSTEP_WAIT_WARN () {
	local myTEXT=""
	[ -n "${myES_STEP_WAIT_GIVEN}" ] || return 0
	myTEXT="TPOT_ES_STEP_WAIT=${myES_STEP_WAIT_GIVEN} is no whole number of seconds from 60 to 86400, the step through Elasticsearch ${myES_STEP_VERSION%.*} waits $(( myES_STEP_WAIT / 60 )) min (${myES_STEP_WAIT} s) instead."
	fuUI_WARN "${myTEXT}"
	fuDID warn "${myTEXT}"
}

function fuPRINT_HELP () {
	# the help only, without the T-Pot logo (that is for a run)
	fuUI_HELP "Updater" "update.sh -y [-s] [--full] [--backup-only] [-b <branch>] [-r <url>]"$'\n'"          [-B <file>] [--accept-data-loss]"$'\n'"          [--accept-data-loss-file <file>] [--adapt-compose]"$'\n'"update.sh --check-data-loss [--full] [--backup-only] [-b <branch>]"$'\n'"          [-r <url>] [-B <file>]" \
	  --about "Updates T-Pot to the latest version of its branch: a backup to ~/tpot_backups first, then the
checkout, your configuration and edition put back, the images of the release pulled." \
	  --opt "-y" "Confirm the update, required. It does not confirm a loss of data, see --accept-data-loss" \
	  --opt "-s, --start" "Start T-Pot again once the update is through. Off by default,
so an unattended run leaves the services stopped unless asked." \
	  --opt "-F, --full" "Include the whole data/ folder in the backup. Off by default,
on a busy hive it turns a 1 MB archive into tens of GB. Not
needed for a newer Elastic Stack: before its upgrade, which
cannot be undone, the run adds data/elk/data to the backup by
itself where there is room. Kept uncompressed either way." \
	  --opt "-o, --backup-only" "Only write the backup (T-Pot is stopped for it) and end, no
update. With --full the data is in it, with -s T-Pot starts
again afterwards. tpot uninstall uses it." \
	  --opt "-b <branch>" "Branch to update from, i.e. to test a branch before it is
merged. The branch is checked out, so every following
update stays on it until another branch is requested.
Default: the branch of ~/tpotce, environment: TPOT_BRANCH" \
	  --opt "-r <url>" "Repository to update from, https URL. Replaces the URL of
'origin' in ~/tpotce.
Default: the origin of ~/tpotce, environment: TPOT_REPO_URL" \
	  --opt "-B <file>" "Read the sudo password from a file, so an unattended run also
works without passwordless sudo (the T-Pot Manager hands one over)" \
	  --opt "--check-data-loss" "Only list what the run would lose for good, change nothing (a git
fetch at most). Exit 0: nothing found, 3: found, 1: it cannot tell." \
	  --opt "--accept-data-loss" "Confirm every loss of data without a terminal, where the run cannot
ask (for the command line and cron, where you typed it)" \
	  --opt "--accept-data-loss-file <file>" "Confirm only the losses of this file: the '@@tpot loss' lines
of --check-data-loss that were confirmed (the T-Pot Manager
hands one over), keys backups, elastic, elastic-step, images.
A loss it does not name, or older backups it does not all
name, is not confirmed." \
	  --opt "--adapt-compose" "For a docker-compose.yml of your own (no edition, not built by
compose/customizer.py): back up your compose file and adapt it to
this release without a question; what the run lists as kept-out
is not carried over. At a terminal the run asks (default: cancel),
-y does not choose." \
	  --opt "-h" "Show this help message" \
	  --example "./update.sh -y" "Update from the branch of ~/tpotce, T-Pot stays stopped afterwards" \
	  --example "./update.sh -y -s --full" "Update with all of data/ in the backup and start T-Pot again" \
	  --example "./update.sh -y -b dev" "Test the branch dev (it stays checked out)" \
	  --example "./update.sh -y --backup-only -s" "Only a backup, T-Pot runs again afterwards" \
	  --example "./update.sh --check-data-loss" "List what an update would lose for good: '@@tpot loss <key> <text>'" \
	  --note "restore.sh brings a backup back, tpot update hands every option on to update.sh." \
	  --note "A loss of data is what the backup of the run cannot bring back: older backups deleted to
make room, an Elasticsearch data upgrade whose data the backup has no room for (without --full
the run adds that data to its backup by itself where there is room), the step of the data of an
Elasticsearch before 8.19 through 8.19 on the way to 9, images built here or not
confirmed as pullable again. Each one asks at a terminal (default no); without one only
--accept-data-loss (every loss) or --accept-data-loss-file (the losses it names) confirms it.
Not confirmed, the run keeps the images and goes on, or stops with exit 5 (T-Pot keeps running,
or the checkout goes back where it can and T-Pot starts again where tpot.service is enabled)." \
	  --note "A docker-compose.yml of your own does not get the changes of a release. The run adapts it
(compose/customizer.py --adopt, the backup keeps your file) only after a yes at a terminal or with
--adapt-compose; otherwise it stops with exit 1 before it changes anything (after a handover the
checkout goes back and T-Pot starts again where tpot.service is enabled). A file of the customizer
before v2 (# T-Pot: CUSTOM EDITION) is rebuilt without a question." \
	  --note "Exit 5 is every refusal of the Elasticsearch checks, also one that loses nothing (no room
for the step's images, a failed pull, a step that did not go through). The step waits up to
20 min, TPOT_ES_STEP_WAIT=<seconds> (60 to 86400) sets another bound."
	exit 0
}

# Check if running with root privileges
if [ ${EUID} -eq 0 ];
  then
    fuUI_ERROR "This script should not be run as root. Please run it as a regular user."
    echo
    exit 1
fi

# Let's test the internet connection
function fuCHECKINET () {
	mySITES=$1
	  echo
	  fuUI_INFO "Now checking availability of ..."
	  for i in $mySITES;
	    do
	      curl --connect-timeout 5 -IsS $i >/dev/null 2>&1
	        if [ $? -ne 0 ];
	          then
	            fuUI_ERROR "${i} cannot be reached, the internet connection test failed. Exiting."
	            echo
	            fuDID fail "${i} cannot be reached, nothing was changed."
	            exit 1
	          else
	            fuUI_OK "${i}"
	        fi
	  done;
	echo
}

# One working directory for the whole run, gone when the script ends
function fuTMPDIR () {
	[ -n "${myTMPDIR}" ] && return
	myTMPDIR=$(mktemp -d)
	if [ ! -d "${myTMPDIR}" ];
	  then
	    fuUI_ERROR "Could not create a temporary directory."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
}

# One item of the summary at the end, i.e. fuDID ok "The images are pulled."
function fuDID () {   # $1 = ok | fail | warn | next | info, $2 = text
	# one line each: a newline in the text becomes a space, so an item never splits in two
	myDONE+=("$1:${2//$'\n'/ }")
}

# The items for a restarted update.sh (fuSELFUPDATE), one per line in TPOT_UPDATE_DONE, so that a
# warning of the backup still reaches the summary; fuDONE_IMPORT takes them back.
function fuDONE_EXPORT () {
	TPOT_UPDATE_DONE="$(printf '%s\n' ${myDONE[@]+"${myDONE[@]}"})"
	export TPOT_UPDATE_DONE
}
function fuDONE_IMPORT () {
	local myITEM=""
	[ -n "${TPOT_UPDATE_PREPARED}" ] && [ -n "${TPOT_UPDATE_DONE}" ] || return 0
	while IFS= read -r myITEM;
	  do
	    [ -z "${myITEM}" ] || myDONE+=("${myITEM}")
	done <<< "${TPOT_UPDATE_DONE}"
}
fuDONE_IMPORT

# A loss of data is what the backup of this run cannot bring back: older backups deleted to
# make room, an Elasticsearch data upgrade whose data the backup has no room for (fuAPPEND_ES_DATA),
# the step of the data of an Elasticsearch older than 8.19 through 8.19 (elastic-step, fuES_STEP_819:
# it changes the data for good, only the backup of the run brings it back), images that cannot be
# pulled again; and a compose file of your own (compose): adapted to the release it may lose changes of
# yours (the backup keeps the file), only --adapt-compose or the choice at a terminal adapts it, never
# --accept-data-loss[-file] (fuADOPT_CHOICE).
# --check-data-loss only lists them, one line each for the T-Pot Manager, which asks before it
# starts the update with --accept-data-loss-file and the lines that were confirmed.
function fuLOSS_ADD () {   # $1 = backups | compose | elastic | elastic-step | images, $2 = text
	myLOSSES=$((myLOSSES + 1))
	echo "@@tpot loss $1 $2"
}

# Does the file of --accept-data-loss-file confirm this loss? It needs a line of the key, and every
# name (the backups or images the step deletes) as a whole entry of the list in one of those lines:
# the text after the first ": ", split at ", ", each entry without its " (<n> MB)" and the full stop
# after the last one. A name that only begins like an entry (cowrie:24.04 for cowrie:24.04.1) or
# lacks its end (20261010_tpot_backup for 20261010_tpot_backup.tar) is not confirmed.
# An entry with ", " in it cannot be told from two entries, it is never confirmed. No loss names
# one: the backups are *_tpot_backup*.tar of update.sh and an image reference has no comma.
function fuLOSS_IN_FILE () {   # $1 = backups | elastic | elastic-step | images, $2... = the names it deletes
	local myKEY="$1" myLINE="" myREST="" myENTRY="" myENTRIES="" myFOUND="" myNAME="" myHIT=""
	shift
	[ -n "${myACCEPT_FILE}" ] && [ -r "${myACCEPT_FILE}" ] || return 1
	while IFS= read -r myLINE || [ -n "${myLINE}" ];
	  do
	    myLINE="${myLINE%$'\r'}"
	    case "${myLINE}" in
	      "@@tpot loss ${myKEY}"|"@@tpot loss ${myKEY} "*) ;;
	      *) continue ;;
	    esac
	    myFOUND="1"
	    case "${myLINE}" in
	      *": "*) myREST="${myLINE#*: }" ;;
	      *) continue ;;
	    esac
	    while :;
	      do
	        case "${myREST}" in
	          *", "*) myENTRY="${myREST%%, *}"; myREST="${myREST#*, }" ;;
	          *) myENTRY="${myREST}"; myREST="" ;;
	        esac
	        case "${myENTRY}" in
	          *" ("[0-9]*" MB)."|*" ("[0-9]*" MB)")
	            myENTRY="${myENTRY%.}"
	            case "${myENTRY}" in *" ("[0-9]*" MB)") myENTRY="${myENTRY% (*}" ;; esac ;;
	        esac
	        myENTRIES="${myENTRIES}${myENTRY}"$'\n'
	        [ -n "${myREST}" ] || break
	      done
	  done < "${myACCEPT_FILE}"
	[ -n "${myFOUND}" ] || return 1
	for myNAME in "$@";
	  do
	    myHIT=""
	    while IFS= read -r myENTRY;
	      do
	        [ "${myENTRY}" == "${myNAME}" ] && { myHIT="1"; break; }
	      done <<< "${myENTRIES}"
	    [ -n "${myHIT}" ] || return 1
	  done
	return 0
}

# Is this loss of data confirmed? A yes at a terminal (no is the default), --accept-data-loss (every
# loss) or the lines of --accept-data-loss-file (only the losses they name, fuLOSS_IN_FILE); -y
# confirms the update, never a loss of data. rc 0 for confirmed.
# restore.sh has a function of this name too, with another signature on purpose: there a loss is one
# thing under its key and the question comes as the third argument; here the names of what goes
# (backups, images) follow the text, because the lines of the file name them. The hints for a file that
# does not confirm the loss differ with that: here it was not shown like this before the run (a name is
# missing), there the file names no loss of its key. Otherwise what they decide is the same: accept,
# file, terminal, refusal, in the same order, with the same warnings and the same question.
function fuLOSS_CONFIRM () {   # $1 = backups | elastic | elastic-step | images, $2 = text, $3... = the names it deletes
	local myKEY="$1" myTEXT="$2"
	shift 2
	if [ -n "${myACCEPT_LOSS}" ];
	  then
	    fuUI_WARN "${myTEXT} (confirmed with --accept-data-loss)"
	    return 0
	fi
	if [ -n "${myACCEPT_FILE}" ] && fuLOSS_IN_FILE "${myKEY}" "$@";
	  then
	    fuUI_WARN "${myTEXT} (confirmed with --accept-data-loss-file)"
	    return 0
	fi
	fuUI_WARN "${myTEXT}"
	# a loss the file does not name was not shown before the run: not confirmed at all
	[ -z "${myACCEPT_FILE}" ] || fuUI_HINT "--accept-data-loss-file does not confirm it: it was not shown like this before the run."
	if [ -t 0 ];
	  then
	    fuUI_CONFIRM --default no "Accept this loss of data?"
	    return
	fi
	fuUI_HINT "Not confirmed: no terminal. --accept-data-loss confirms it, -y does not."
	return 1
}

# The log of the steps under a spinner: the one of this run (the restart after the self
# update adds to it), next to the backups, the one of the run before as update.log.1.
# Without a place for it the output goes nowhere.
function fuLOG_START () {
	if mkdir -p "${myBACKUPDIR}" 2>/dev/null && chmod 0700 "${myBACKUPDIR}" 2>/dev/null \
	   && { [ -n "${myPREPARED}" ] || { { [ ! -s "${myLOG}" ] || mv -f "${myLOG}" "${myLOG}.1"; } && : > "${myLOG}"; }; } 2>/dev/null;
	  then
	    return 0
	fi
	myLOG="/dev/null"
}

# Every way out: the working directory goes, and a run that was confirmed ends with its
# summary (an `exec` for the restart does not come here, the restarted script ends it).
# The exit code stays the one of the run.
function fuEND () {
	local myRC=$? myITEM myFAIL="" myTITLE=""
	[ -n "${myTMPDIR}" ] && rm -rf "${myTMPDIR}"
	[ -n "${myRUNNING}" ] || exit "${myRC}"
	myRUNNING=""
	if [ "${myRC}" -eq 0 ];
	  then
	    myTITLE="T-Pot is updated"
	    [ -z "${myBACKUP_ONLY}" ] || myTITLE="The backup is written"
	  else
	    myTITLE="The update did not finish"
	    [ -z "${myBACKUP_ONLY}" ] || myTITLE="The backup did not finish"
	    [ "${myRC}" -ne 130 ] || myTITLE="The update was stopped"
	    for myITEM in "${myDONE[@]}"; do [ "${myITEM%%:*}" != "fail" ] || myFAIL="1"; done
	    if [ -z "${myFAIL}" ] && [ "${myRC}" -ne 130 ];
	      then
	        fuDID fail "It stopped with an error, see the messages above."
	    fi
	    if [ -n "${mySTOPPED}" ];
	      then fuDID next "T-Pot is stopped, 'sudo systemctl start tpot' brings it back."
	    elif [ -z "${mySTARTED}" ];
	      then fuDID info "T-Pot was left running."
	    fi
	fi
	fuUI_SUMMARY "${myEND_TITLE:-${myTITLE}}" "${myDONE[@]}"
	exit "${myRC}"
}
trap fuEND EXIT

# Find a free archive name. The timestamp has second resolution, and a name that
# already exists gets a counter - two runs must not overwrite each other.
function fuARCHIVE_NAME () {
	local myBASE="${myBACKUPDIR}/${myDATE}_tpot_backup${myFULL:+_full}"
	local myTRY="${myBASE}.tar"
	local myNUM=1
	while [ -e "${myTRY}" ];
	  do
	    myTRY="${myBASE}_${myNUM}.tar"
	    myNUM=$((myNUM+1))
	done
	echo "${myTRY}"
}

# All archives of one kind, oldest first
function fuARCHIVE_LIST () {   # $1 = full | small
	local myAll=""
	myAll=$(ls -1tr "${myBACKUPDIR}"/*_tpot_backup*.tar 2>/dev/null)
	[ -z "${myAll}" ] && return
	if [ "$1" == "full" ];
	  then echo "${myAll}" | grep "_tpot_backup_full"
	  else echo "${myAll}" | grep -v "_tpot_backup_full"
	fi
}

# Which archives would make room for $1 more bytes, oldest first. The newest one always stays -
# it is the most likely rollback point. Deletes nothing: prints "<bytes> <archive>" per archive,
# rc 1 when all of them together do not free enough.
function fuDROP_OLDEST () {   # $1 = bytes short
	local myAll="" myVictim="" myKIB=0 myGAIN=0
	myAll=$(ls -1tr "${myBACKUPDIR}"/*_tpot_backup*.tar 2>/dev/null | sed '$d')
	while IFS= read -r myVictim;
	  do
	    [ -n "${myVictim}" ] || continue
	    [ "${myGAIN}" -lt "$1" ] || break
	    myKIB=$(du -k "${myVictim}" 2>/dev/null | cut -f1)
	    [[ "${myKIB}" =~ ^[0-9]+$ ]] || myKIB=0
	    myGAIN=$((myGAIN + myKIB * 1024))
	    echo "$((myKIB * 1024)) ${myVictim}"
	  done <<< "${myAll}"
	[ "${myGAIN}" -ge "$1" ]
}

# Clean up after writing, deliberately not before: an archive is never sacrificed
# for a backup that then fails.
function fuROTATE () {
	local myKind="small" myKeep="${myBACKUP_RETAIN}" myList="" myCount=0 myVictim=""
	if [ -n "${myFULL}" ];
	  then
	    myKind="full"
	    myKeep="${myBACKUP_RETAIN_FULL}"
	fi
	while :;
	  do
	    myList=$(fuARCHIVE_LIST "${myKind}")
	    myCount=$(echo "${myList}" | grep -c .)
	    [ "${myCount}" -le "${myKeep}" ] && break
	    myVictim=$(echo "${myList}" | head -1)
	    fuUI_HINT "Keeping the last ${myKeep} ${myKind} backups, removing ${myVictim} ($(du -h "${myVictim}" | cut -f1))."
	    rm -f "${myVictim}" || break
	done
}

# Roughly what the archive needs. The allowance covers MANIFEST, the patch and the
# Elasticsearch export that is added later.
function fuBACKUP_SIZE () {
	local myPATHS=() myJEWEL="" mySUM=0 myDATA=""
	myDATA=$(fuDATA_PATH)
	[ -f "$HOME/tpotce/.env" ] && myPATHS+=("$HOME/tpotce/.env")
	[ -f "$HOME/tpotce/docker-compose.yml" ] && myPATHS+=("$HOME/tpotce/docker-compose.yml")
	if [ -n "${myFULL}" ];
	  then
	    [ -d "${myDATA}" ] && myPATHS+=("${myDATA}")
	  else
	    for myJEWEL in ${myJEWELS};
	      do
	        [ -e "${myDATA}/${myJEWEL#data/}" ] && myPATHS+=("${myDATA}/${myJEWEL#data/}")
	      done;
	fi
	[ -d "${myTMPDIR}/stage/elastic" ] && myPATHS+=("${myTMPDIR}/stage/elastic")
	if [ ${#myPATHS[@]} -gt 0 ];
	  then
	    mySUM=$(sudo du -scb "${myPATHS[@]}" 2>/dev/null | tail -1 | cut -f1)
	fi
	echo $((mySUM + 4194304))
}

# No room for the backup, even with the older ones gone: T-Pot was left running.
function fuNO_ROOM () {
	# `--full` was asked for, usually because the update brings a new Elastic Stack
	# whose data upgrade cannot be undone - quietly taking the regular backup instead
	# would leave no way back.
	if [ -n "${myFULL}" ];
	  then
	    fuUI_ERROR "Not enough room for a full backup."
	    fuUI_HINT "Free up space in ${myBACKUPDIR} or on the filesystem, or run without '--full'. T-Pot was left running."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	fuUI_ERROR "Not enough room for a backup and T-Pot needs the disk."
	fuUI_HINT "Free up space in ${myBACKUPDIR} or on the filesystem, T-Pot was left running."
	fuUI_HINT "Exiting."
	echo
	exit 1
}

# Does the archive still fit without closing the disk for the honeypots? Runs
# before fuSTOP_TPOT on purpose, so that giving up leaves T-Pot running. Older
# backups only make room once that is confirmed (fuLOSS_CONFIRM), and only when
# deleting them is enough: what would go is worked out before anything is deleted.
function fuCHECK_BACKUP_SPACE () {
	local myNEED=0 myFREE=0 myTOTAL=0 myRESERVE=0 myDIR="${myBACKUPDIR}" myPLAN="" mySIZE=0 myVICTIM=""
	local myLIST="" myCOUNT=0 myTEXT=""
	local myNAMES=()
	echo
	fuUI_INFO "Checking the space for the backup ..."
	if [ -n "${myPREPARED}" ] && [ -z "${myCHECK_LOSS}" ];
	  then
	    fuUI_OK "The backup was already taken before the restart, nothing to check."
	    echo
	    return
	fi
	if [ -n "${myCHECK_LOSS}" ];
	  then
	    # the check changes nothing: without the folder there is no backup to delete either,
	    # df asks for the filesystem it would be on
	    [ -d "${myDIR}" ] || myDIR="$HOME"
	  elif ! mkdir -p "${myBACKUPDIR}" || ! chmod 0700 "${myBACKUPDIR}";
	    then
	      fuUI_ERROR "Could not prepare ${myBACKUPDIR}."
	      fuUI_HINT "Exiting."
	      echo
	      exit 1
	fi
	myTOTAL=$(df -B1 --output=size "${myDIR}" 2>/dev/null | tail -1 | tr -d " ")
	myRESERVE=$(( myTOTAL / 100 * myBACKUP_RESERVE_PERCENT ))
	myNEED=$(fuBACKUP_SIZE)
	myFREE=$(df -B1 --output=avail "${myDIR}" 2>/dev/null | tail -1 | tr -d " ")
	fuUI_HINT "Archive needs about $((myNEED / 1048576)) MB, $((myFREE / 1048576)) MB free, keeping $((myRESERVE / 1048576)) MB in reserve."
	if [ $((myFREE - myNEED)) -ge "${myRESERVE}" ];
	  then
	    fuUI_OK "Enough room."
	    echo
	    return
	fi
	if ! myPLAN=$(fuDROP_OLDEST $((myRESERVE - myFREE + myNEED)));
	  then
	    # Deleting the older backups would not be enough either, so none is deleted. In the
	    # check that is no loss of data: the update stops there, before it changes anything.
	    if [ -n "${myCHECK_LOSS}" ];
	      then
	        fuUI_WARN "Not enough room for a backup even without the older ones, the update would stop there and change nothing."
	        echo
	        return 0
	    fi
	    fuNO_ROOM
	fi
	while read -r mySIZE myVICTIM;
	  do
	    myCOUNT=$((myCOUNT + 1))
	    myNAMES+=("${myVICTIM##*/}")
	    myLIST="${myLIST}${myLIST:+, }${myVICTIM##*/} ($(( (mySIZE + 1048575) / 1048576 )) MB)"
	  done <<< "${myPLAN}"
	myTEXT="Making room for the backup deletes ${myCOUNT} older backup(s) in ${myBACKUPDIR}: ${myLIST}."
	if [ -n "${myCHECK_LOSS}" ];
	  then
	    fuLOSS_ADD backups "${myTEXT}"
	    echo
	    return 0
	fi
	if ! fuLOSS_CONFIRM backups "${myTEXT}" "${myNAMES[@]}";
	  then
	    fuUI_HINT "Nothing was deleted and T-Pot was left running. Free up space in ${myBACKUPDIR} or on the filesystem."
	    fuUI_HINT "Exiting."
	    echo
	    if [ -n "${myBACKUP_ONLY}" ];
	      then fuDID fail "No backup written: making room would delete older backups, which was not confirmed."
	      else fuDID fail "Not updated: making room would delete older backups, which was not confirmed."
	    fi
	    exit 5
	fi
	while read -r mySIZE myVICTIM;
	  do
	    fuUI_HINT "Removing ${myVICTIM} ($(( (mySIZE + 1048575) / 1048576 )) MB) to make room."
	    if ! rm -f "${myVICTIM}";
	      then
	        fuUI_ERROR "Could not remove ${myVICTIM}, so there is not enough room for the backup."
	        fuUI_HINT "Free up space in ${myBACKUPDIR} or on the filesystem, T-Pot was left running."
	        fuUI_HINT "Exiting."
	        echo
	        exit 1
	    fi
	  done <<< "${myPLAN}"
	fuDID warn "Deleted ${myCOUNT} older backup(s) to make room: ${myLIST}."
	# measured again: du and the filesystem may disagree, and nothing beyond what was
	# confirmed goes, so a backup that still does not fit is not written
	myFREE=$(df -B1 --output=avail "${myDIR}" 2>/dev/null | tail -1 | tr -d " ")
	fuUI_HINT "Now $((myFREE / 1048576)) MB free."
	if [ $((myFREE - myNEED)) -lt "${myRESERVE}" ];
	  then
	    fuNO_ROOM
	fi
	fuUI_OK "Enough room."
	echo
}

# Compare repository URLs without a trailing slash or `.git`
function fuNORMALIZE_REPO () {
	myURL="${1%/}"
	echo "${myURL%.git}"
}

# Work out where to update from. The options and the environment variables win,
# otherwise the current checkout is kept as it is.
function fuCHECK_SOURCE () {
	echo
	fuUI_INFO "Checking the update source ..."
	myCURRENT_BRANCH=$(git -C "$HOME/tpotce" rev-parse --abbrev-ref HEAD 2>/dev/null)
	myCURRENT_REPO=$(fuNORMALIZE_REPO "$(git -C "$HOME/tpotce" remote get-url origin 2>/dev/null)")
	[ -z "${myTPOT_BRANCH}" ] && myTPOT_BRANCH="${myCURRENT_BRANCH}"
	[ -z "${myTPOT_REPO_URL}" ] && myTPOT_REPO_URL="${myCURRENT_REPO}"
	myTPOT_REPO_URL=$(fuNORMALIZE_REPO "${myTPOT_REPO_URL}")
	fuUI_HINT "${myTPOT_REPO_URL} at ${myTPOT_BRANCH}"
	# A detached HEAD - a tag or a bare commit checked out - has no upstream to pull
	# into, `git pull` refuses outright. Without a branch the update cannot do
	# anything, so it stops here while T-Pot is still running and untouched. `-b`
	# still works: fuSWITCH_SOURCE checks the branch out and ends the detached state.
	if [ -z "${myTPOT_BRANCH}" ] || [ "${myTPOT_BRANCH}" == "HEAD" ];
	  then
	    fuUI_ERROR "The checkout is not on a branch (detached at $(git -C "$HOME/tpotce" describe --tags --always 2>/dev/null)), so there is nothing to update from."
	    fuUI_HINT "Name a branch with '-b master' and it is checked out for you, or switch by hand first: 'git -C $HOME/tpotce switch master'."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	if [ -z "${myTPOT_SOURCE_GIVEN}" ];
	  then
	    echo
	    return
	fi
	# A typo in a branch name must not take T-Pot down, so the requested source
	# is checked before anything is stopped or overwritten. `ls-remote` asks the
	# repository itself and leaves the local checkout alone.
	if ! git ls-remote --heads "${myTPOT_REPO_URL}" "refs/heads/${myTPOT_BRANCH}" 2>/dev/null | grep -q .;
	  then
	    fuUI_ERROR "Branch ${myTPOT_BRANCH} does not exist in ${myTPOT_REPO_URL}."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	  else
	    fuUI_OK "Repository and branch are available."
	fi
	echo
}

# The template of an edition, i.e. STANDARD -> compose/standard.yml
function fuEDITION_TEMPLATE () {
	echo "$HOME/tpotce/compose/$(echo "${myEDITION}" | tr '[:upper:]' '[:lower:]').yml"
}

# The edition a compose file names: the first "# T-Pot: <EDITION>" line of its leading comment
# block (the lines above the first one that is neither empty nor a comment), so a comment of your
# own above it keeps the edition. Prints "<line number> <EDITION>", nothing without one. The T-Pot
# Manager reads it by the same rule (edition_line of tpotctl/ops.py and compose/customizer_core.py).
function fuEDITION_LINE () {   # $1 = compose file
	awk '/^[[:space:]]*$/ { next } !/^[[:space:]]*#/ { exit } /^# T-Pot:/ { sub(/^# T-Pot:[[:space:]]*/, ""); sub(/[[:space:]]+$/, ""); print NR " " $0; exit }' "$1" 2>/dev/null
}

# Was the compose file built by compose/customizer.py (v2 or later)? Its header follows the edition line.
function fuCUSTOM_FILE () {   # $1 = compose file
	local myLINE=""
	myLINE=$(fuEDITION_LINE "$1" | cut -d " " -f1)
	[ -n "${myLINE}" ] && sed -n "$((myLINE + 1))p" "$1" 2>/dev/null | grep -q "^# customizer: version="
}

# The customizer hashes everything below its header, a mismatch means the file was
# edited by hand and must not be rebuilt over.
function fuCUSTOM_CHECKSUM () {   # $1 = compose file
	local mySUM=""
	mySUM=$(sed -n 's/^# customizer: sha256=\([0-9a-f]\{64\}\)$/\1/p' "$1" | head -1)
	[ -n "${mySUM}" ] && [ "$(sed '1,/^# customizer: sha256=/d' "$1" | sha256sum | awk '{ print $1 }')" == "${mySUM}" ]
}

# An edition is installed by copying one of the compose/*.yml over
# docker-compose.yml, which is tracked by git, so the `git reset --hard` in
# fuSELFUPDATE puts the STANDARD edition back. The edition is read here, before
# anything is stopped, backed up or overwritten.
function fuCHECK_EDITION () {
	local myCOMPOSE=""
	local myKNOWN=""
	local i=""
	echo
	fuUI_INFO "Checking the installed T-Pot edition ..."
	# A restarted update.sh inherits the result of the first run, the compose file
	# has already been reset by then and cannot be read again.
	if [ -n "${myEDITION}" ];
	  then
	    fuUI_OK "Edition ${myEDITION} was detected before the restart."
	    echo
	    return
	fi
	# An empty one after a handover (another TPOT_DOCKER_COMPOSE, or no docker-compose.yml before
	# the reset) is no edition: the files here are the reset ones of the release, read again they
	# would say STANDARD
	if [ -n "${myPREPARED}" ];
	  then
	    myEDITION="UNKNOWN"
	    fuUI_HINT "The update.sh that started this run handed over no edition, and the checkout is reset already: the edition is unknown, it is not read from the files of the release."
	    echo
	    return
	fi
	# Only the tracked ./docker-compose.yml is overwritten by the update, a compose
	# file under a different name is untracked and stays as it is.
	myCOMPOSE=$(grep -E "^TPOT_DOCKER_COMPOSE=" "$HOME/tpotce/.env" 2>/dev/null | tail -1 | cut -d "=" -f2-)
	myCOMPOSE="${myCOMPOSE:-./docker-compose.yml}"
	if [ "$(basename "${myCOMPOSE}")" != "docker-compose.yml" ];
	  then
	    fuUI_OK "TPOT_DOCKER_COMPOSE points to ${myCOMPOSE}, which the update leaves alone."
	    echo
	    return
	fi
	if [ ! -f "$HOME/tpotce/docker-compose.yml" ];
	  then
	    fuUI_WARN "There is no docker-compose.yml, so there is no edition to remember."
	    fuDID warn "There was no docker-compose.yml, so no edition was remembered."
	    echo
	    return
	fi
	myEDITION=$(fuEDITION_LINE "$HOME/tpotce/docker-compose.yml" | cut -d " " -f2-)
	# A file built with compose/customizer.py has no template, it is built again from
	# its header instead, unless it was edited by hand since.
	if [ "${myEDITION}" == "CUSTOM" ] && fuCUSTOM_FILE "$HOME/tpotce/docker-compose.yml";
	  then
	    fuCUSTOM_CHECKSUM "$HOME/tpotce/docker-compose.yml" || myCOMPOSE_CUSTOMIZED="1"
	    fuUI_OK "Edition CUSTOM, built by compose/customizer.py${myCOMPOSE_CUSTOMIZED:+ and edited by hand since}."
	    echo
	    return
	fi
	# Only an edition that ships a template in compose/ can be restored from one, a
	# compose file of your own has none.
	for i in ${myEDITIONS};
	  do
	    [ "${myEDITION}" == "$i" ] && myKNOWN="1"
	  done;
	[ -n "${myKNOWN}" ] || myEDITION="UNKNOWN"
	# No separate copy is kept here: fuBACKUP puts docker-compose.yml into the archive
	# before the checkout is reset, and that is the copy everything below works from.
	if [ "${myEDITION}" == "UNKNOWN" ] && [ -n "${myBACKUP_ONLY}" ];
	  then
	    fuUI_WARN "Unable to determine the edition, the backup keeps docker-compose.yml as it is."
	    fuDID warn "The edition of docker-compose.yml could not be determined."
	elif [ "${myEDITION}" == "UNKNOWN" ];
	  then
	    # a file of the customizer before v2 is rebuilt after the pull, a compose file of your own only
	    # after a yes: the question comes here, before anything is stopped, backed up or pulled
	    fuADOPT_COMPOSE "$HOME/tpotce/docker-compose.yml" check
	  else
	    # A compose file that differs from its own template was edited by the user.
	    # The update cannot merge that, so it has to be handed back manually.
	    if ! cmp -s "$HOME/tpotce/docker-compose.yml" "$(fuEDITION_TEMPLATE)";
	      then
	        myCOMPOSE_CUSTOMIZED="1"
	    fi
	    fuUI_OK "Edition ${myEDITION}${myCOMPOSE_CUSTOMIZED:+ (customized)}."
	fi
	echo
}

# Move the checkout over to the requested repository and branch. The branch is
# checked out for good, so a later `update.sh -y` without options keeps updating
# from it.
function fuSWITCH_SOURCE () {
	[ -n "${myTPOT_SOURCE_GIVEN}" ] || return
	local mySWITCH="" myLOCAL="" myREF=""
	if [ "${myTPOT_REPO_URL}" != "${myCURRENT_REPO}" ];
	  then
	    fuUI_HINT "Now switching origin from ${myCURRENT_REPO} to ${myTPOT_REPO_URL}."
	    git -C "$HOME/tpotce" remote set-url origin "${myTPOT_REPO_URL}"
	    mySWITCH="1"
	fi
	if [ "${myTPOT_BRANCH}" != "${myCURRENT_BRANCH}" ];
	  then
	    fuUI_HINT "Now switching from branch ${myCURRENT_BRANCH} to ${myTPOT_BRANCH}."
	    mySWITCH="1"
	fi
	[ -n "${mySWITCH}" ] || return
	git -C "$HOME/tpotce" fetch origin --prune
	# `checkout -B` puts the local branch on origin's: commits that only the local one has would
	# be left without a name, so they get a ref first, and without one the switch does not start
	myLOCAL=$(git -C "$HOME/tpotce" rev-parse --verify --quiet "refs/heads/${myTPOT_BRANCH}^{commit}" 2>/dev/null)
	if [ -n "${myLOCAL}" ] \
	   && [ -n "$(git -C "$HOME/tpotce" rev-list --max-count=1 "${myLOCAL}" --not --remotes=origin 2>/dev/null)" ];
	  then
	    # the process number keeps two switches within one second apart
	    myREF="refs/tpot-backup/$(date +%Y%m%d%H%M%S)-$$/${myTPOT_BRANCH}"
	    if ! git -C "$HOME/tpotce" update-ref "${myREF}" "${myLOCAL}";
	      then
	        fuUI_ERROR "Could not keep the commits of the local branch ${myTPOT_BRANCH} that origin does not have."
	        fuUI_HINT "Nothing was switched. T-Pot is stopped, start it again with 'systemctl start tpot'. The backup is in ${myARCHIVE}."
	        fuUI_HINT "Exiting."
	        echo
	        exit 1
	    fi
	    fuUI_WARN "The local branch ${myTPOT_BRANCH} has commits that origin does not have. They stay as ${myREF}."
	    fuUI_HINT "'git -C $HOME/tpotce checkout -B ${myTPOT_BRANCH} ${myREF}' brings the branch back as it was."
	    # -C: the branch exists again after the switch below, -c would refuse it
	    fuDID warn "Local commits of ${myTPOT_BRANCH} kept as ${myREF}, git switch -C ${myTPOT_BRANCH} ${myREF} brings them back."
	fi
	git -C "$HOME/tpotce" reset --hard
	if ! git -C "$HOME/tpotce" checkout -B "${myTPOT_BRANCH}" "origin/${myTPOT_BRANCH}";
	  then
	    fuUI_ERROR "Could not check out ${myTPOT_BRANCH}."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	# a branch that existed locally before keeps whatever it tracked, the pull
	# below has to follow the branch that was just requested
	git -C "$HOME/tpotce" branch --set-upstream-to="origin/${myTPOT_BRANCH}" "${myTPOT_BRANCH}"
}

# Update
function fuSELFUPDATE () {
	echo
	fuUI_INFO "Now checking for newer files in repository ..."
	# The running script may be replaced by the update, either by newer commits
	# or by a switch to a different branch, and then has to restart itself.
	myOLDSUM=$(sha256sum "$0" | awk '{ print $1 }')
	local myOLDHEAD=""
	myOLDHEAD=$(git -C "$HOME/tpotce" rev-parse HEAD 2>/dev/null)
	fuSWITCH_SOURCE
	fuUI_HINT "Pulling updates from repository."
	# Checked, because a failed pull used to leave the checkout untouched while the
	# run went on to report "Done" - an update that silently did nothing.
	if ! git -C "$HOME/tpotce" fetch --all || ! git -C "$HOME/tpotce" reset --hard || ! git -C "$HOME/tpotce" pull --force;
	  then
	    fuUI_ERROR "Could not pull the updates."
	    fuUI_HINT "The checkout may be left mid-merge, check it with 'git -C $HOME/tpotce status'."
	    fuUI_HINT "T-Pot is stopped, start it again with 'systemctl start tpot'. The backup is in ${myARCHIVE}."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	# tpot runs from this checkout: a moved HEAD means it should start anew, even if
	# the update fails later (the restarted script writes into the same output)
	if [ "$(git -C "$HOME/tpotce" rev-parse HEAD 2>/dev/null)" != "${myOLDHEAD}" ];
	  then
	    fuMARK changed checkout
	fi
	if [ "${myOLDSUM}" != "$(sha256sum "$0" | awk '{ print $1 }')" ];
	  then
	    # A changed update.sh is not necessarily a newer one. The `git reset --hard`
	    # above throws away a hand-placed update.sh, and `-b` to an older branch
	    # brings that branch's script - both leave an OLDER file here. Restarting
	    # into one is never useful: it knows nothing of the handover, so it stops
	    # T-Pot and writes a backup a second time, and the released 24.04.0 script
	    # keeps restarting itself forever unless the checkout is on master. The
	    # handover variable is the marker, it only exists from 24.04.1 onwards.
	    if ! grep -q "TPOT_UPDATE_PREPARED" "$0";
	      then
	        fuUI_WARN "The update.sh of this checkout predates the one running, so this checkout is an older release."
	        fuUI_HINT "Not restarting into it. Putting the configuration back, then stopping."
	        myOLDER_CHECKOUT="1"
	        echo
	        return
	    fi
	    fuUI_HINT "Found newer version of update.sh, restarting myself."
	    # The edition was read from a file the pull above has just overwritten, so
	    # the restarted script cannot read it again and inherits it instead.
	    export TPOT_UPDATE_PREPARED="1"
	    export TPOT_UPDATE_ARCHIVE="${myARCHIVE}"
	    export TPOT_UPDATE_EDITION="${myEDITION}"
	    export TPOT_UPDATE_COMPOSE_CUSTOMIZED="${myCOMPOSE_CUSTOMIZED}"
	    fuDONE_EXPORT
	    # the list of a compose file of your own shown before the choice: what the yes covered (fuADOPT_GREW)
	    if [ -n "${myTMPDIR}" ] && [ -f "${myTMPDIR}/adopt_shown" ];
	      then
	        TPOT_UPDATE_ADOPT_SHOWN="$(cat "${myTMPDIR}/adopt_shown")"
	        export TPOT_UPDATE_ADOPT_SHOWN
	    fi
	    # an `exec` does not fire the EXIT trap, so clean up here
	    [ -n "${myTMPDIR}" ] && rm -rf "${myTMPDIR}"
	    # `-y` is repeated on purpose: after a switch to an older branch the
	    # restarted script is that branch's update.sh, which only looks at `$1`
	    # for the confirmation and would just print its usage otherwise
	    local myRESTART=()
	    mapfile -t myRESTART < <(fuRESTART_ARGS "$0" "$@")
	    exec bash "$0" -y "${myRESTART[@]}"
	    exit 1
	fi
	fuDID ok "The checkout is on $(git -C "$HOME/tpotce" rev-parse --abbrev-ref HEAD 2>/dev/null) at $(git -C "$HOME/tpotce" rev-parse --short HEAD 2>/dev/null), version $(cat "$HOME/tpotce/version" 2>/dev/null)."
	echo
}

# The options for the restarted script: all of them, but -B <file>, --accept-data-loss and
# --accept-data-loss-file <file> only if that script knows them - the update.sh of an earlier
# release stops with its usage on them (the sudo timestamp of this run is enough for the
# restarted one then, and it has no confirmation of a loss of data to give; the bare
# --accept-data-loss never stands in for the file).
function fuRESTART_ARGS () {   # $1 = the script that restarts, then the options
	local myTARGET="$1" myDROP=""
	shift
	for myARG in "$@";
	  do
	    if [ -n "${myDROP}" ]; then myDROP=""; continue; fi
	    if [ "${myARG}" = "-B" ] && ! grep -q 'getopts ":[^"]*B:' "${myTARGET}";
	      then
	        myDROP="1"
	        continue
	    fi
	    if [ "${myARG}" = "--accept-data-loss-file" ] && ! grep -q -- '--accept-data-loss-file' "${myTARGET}";
	      then
	        myDROP="1"
	        continue
	    fi
	    if [ "${myARG}" = "--accept-data-loss" ] && ! grep -q -- '--accept-data-loss' "${myTARGET}";
	      then
	        continue
	    fi
	    if [ "${myARG}" = "--adapt-compose" ] && ! grep -q -- '--adapt-compose' "${myTARGET}";
	      then
	        continue
	    fi
	    printf '%s\n' "${myARG}"
	done
}

function fuCHECK_VERSION () {
	local myMINVERSION="24.04.1"
	# The newest release this update.sh knows: the one it belongs to, the file version of
	# its checkout (after the self update the one just fetched), no number written in here.
	# The checkout in the current directory is updated when its version lies between the
	# two, both included
	local myMASTERVERSION=""
	[ -r "${myHERE}/version" ] && myMASTERVERSION=$(tr -d "[:space:]" < "${myHERE}/version")
	echo
	fuUI_INFO "Checking for version tag ..."
	if [ -z "${myMASTERVERSION}" ];
	  then
	    fuUI_ERROR "Unable to tell the release of this update.sh, there is no ${myHERE}/version."
	    fuDID fail "This update.sh has no version file next to it, run the one in ~/tpotce."
	    exit 1
	fi
	if [ -f "$HOME/tpotce/version" ];
	  then
	    # the version of the checkout, without spaces and CR (a file written on Windows);
	    # both compared number by number (24.04.10 is newer than 24.04.9), fuUI_VERSION_GE
	    myVERSION=$(tr -d "[:space:]" < "$HOME/tpotce/version")
	    if fuUI_VERSION_GE "${myVERSION}" "${myMINVERSION}" && fuUI_VERSION_GE "${myMASTERVERSION}" "${myVERSION}";
	      then
	        fuUI_OK "$myVERSION is eligible for the update procedure."
	      elif [ -n "${myTPOT_SOURCE_GIVEN}" ];
	        then
	          # A branch or a fork may have moved the version tag on already,
	          # that must not stop a test of the update procedure itself.
	          fuUI_WARN "$myVERSION is outside the supported range, continuing because an update source was requested."
	          fuDID warn "$myVERSION is outside the supported range (${myMINVERSION} to ${myMASTERVERSION}), the update went on because an update source was requested."
	      else
	        fuUI_ERROR "$myVERSION cannot be upgraded automatically. Please run a fresh install."
	        fuDID fail "$myVERSION cannot be upgraded automatically (this update.sh handles ${myMINVERSION} to ${myMASTERVERSION}), please run a fresh install."
	        exit 1
	    fi
	  else
	    fuUI_ERROR "Unable to determine version. Please run 'update.sh' from within 'tpotce/'."
	    fuDID fail "No version file here, run update.sh from within ~/tpotce."
	    exit 1
	  fi
	echo
}

# Stop T-Pot to avoid race conditions with running containers with regard to the current T-Pot config
function fuSTOP_TPOT () {
	echo
	fuUI_INFO "Need to stop T-Pot ..."
	if [ -n "${myPREPARED}" ];
	  then
	    mySTOPPED="1"
	    fuUI_OK "Already stopped before the restart."
	    echo
	    return
	fi
	sudo systemctl stop tpot.service
	if [ $? -ne 0 ];
	  then
	    fuUI_ERROR "Could not stop T-Pot. Exiting."
	    echo
	    exit 1
	  else
	    mySTOPPED="1"
	    fuUI_OK "T-Pot is stopped."
	    if [ "$(docker ps -aq)" != "" ];
	      then
	        docker stop $(docker ps -aq)
	        docker container prune -f && docker image prune -f && docker volume prune -f
	    fi
	    # the networks of an earlier compose file (one per honeypot until 24.04.2) would
	    # keep their address pools
	    docker network prune -f > /dev/null 2>&1
	    fuUI_OK "Containers cleaned up."
	fi
	echo
}

# Does this installation run the service at all? A SENSOR runs neither Kibana nor
# Elasticsearch, MOBILE runs Elasticsearch without Kibana - waiting for something
# that was never deployed would only delay every single update.
function fuCOMPOSE_HAS () {   # $1 = service name
	grep -qE "^  $1:" "$HOME/tpotce/docker-compose.yml" 2>/dev/null
}

# Is the container there? Distinguishes "still starting" from "T-Pot is stopped" -
# waiting only makes sense for the former.
function fuCONTAINER_UP () {   # $1 = container name
	docker ps --format "{{.Names}}" 2>/dev/null | grep -qx "$1"
}

# Wait for an endpoint to answer, but no longer than the grace period.
function fuWAIT_FOR () {   # $1 = URL, $2 = label
	local myWAIT=0
	curl -s -f -o /dev/null --connect-timeout 5 "$1" && return 0
	fuUI_HINT "Waiting up to ${myELASTIC_GRACE}s for $2 ..."
	while [ "${myWAIT}" -lt "${myELASTIC_GRACE}" ];
	  do
	    sleep 5
	    myWAIT=$((myWAIT+5))
	    if curl -s -f -o /dev/null --connect-timeout 5 "$1";
	      then
	        fuUI_OK "$2 answers after ${myWAIT}s."
	        return 0
	    fi
	done
	fuUI_WARN "$2 does not answer after ${myELASTIC_GRACE}s."
	return 1
}

# Is the service reachable, waiting for it if it is still coming up? Returns 1 and
# says why if there is nothing to wait for.
function fuELASTIC_READY () {   # $1 = service, $2 = container, $3 = URL, $4 = label
	if ! fuCOMPOSE_HAS "$1";
	  then
	    fuUI_HINT "This edition does not run $4, nothing to save."
	    return 1
	fi
	if ! fuCONTAINER_UP "$2";
	  then
	    fuUI_WARN "$4 is not running, so it cannot be saved. Start T-Pot first if you want it in the backup."
	    fuDID warn "$4 was not running, it is NOT in the backup."
	    return 1
	fi
	if ! fuWAIT_FOR "$3" "$4";
	  then
	    fuUI_WARN "$4 did not answer within ${myELASTIC_GRACE}s, it is NOT in the backup."
	    fuDID warn "$4 did not answer within ${myELASTIC_GRACE}s, it is NOT in the backup."
	    fuUI_HINT "Raise the wait with TPOT_ELASTIC_GRACE=<seconds> if this machine needs longer."
	    return 1
	fi
	return 0
}

# Save the state that lives in Elasticsearch rather than in a file: the user's own
# Kibana objects and the ILM policy the retention hangs on. Runs before fuSTOP_TPOT
# because both need a running instance. The README used to ask the user to export
# this by hand, and update.sh gave that hint at the end of the run - too late to
# act on.
function fuEXPORT_ELASTIC () {
	local myOUT=""
	echo
	fuUI_INFO "Saving the Elasticsearch state ..."
	if [ -n "${myPREPARED}" ];
	  then
	    fuUI_OK "Already saved before the restart, T-Pot is stopped by now."
	    echo
	    return
	fi
	fuTMPDIR
	myOUT="${myTMPDIR}/stage/elastic"
	mkdir -p "${myOUT}"
	# Two independent checks rather than one: a SENSOR runs neither service, MOBILE
	# runs Elasticsearch without Kibana.
	if fuELASTIC_READY kibana kibana "${myKIBANA}/api/status" "Kibana";
	  then
	    if curl -s -f -X POST "${myKIBANA}/api/saved_objects/_export" \
	         -H "kbn-xsrf: true" -H "Content-Type: application/json" \
	         -d '{"type":"*","excludeExportDetails":true}' \
	         -o "${myOUT}/kibana_export.ndjson";
	      then
	        fuUI_OK "Kibana objects exported, $(grep -c . "${myOUT}/kibana_export.ndjson") objects."
	      else
	        fuUI_WARN "The Kibana objects could not be exported."
	        fuDID warn "The Kibana objects could not be exported, they are NOT in the backup."
	        rm -f "${myOUT}/kibana_export.ndjson"
	    fi
	fi
	# The ILM policy is not a saved object, it comes from Elasticsearch itself. What
	# GET returns is wrapped in the policy name, which PUT rejects, so it is stored
	# ready to be put back - restoring it is then a single curl.
	if fuELASTIC_READY elasticsearch elasticsearch "${myES}" "Elasticsearch";
	  then
	    if curl -s -f "${myES}/_ilm/policy/tpot" -o "${myTMPDIR}/ilm_raw.json" \
	       && python3 -c "
import json
myRAW = json.load(open('${myTMPDIR}/ilm_raw.json'))
json.dump({'policy': myRAW['tpot']['policy']}, open('${myOUT}/ilm_policy_tpot.json', 'w'), indent=2)
" 2>/dev/null;
	      then
	        fuUI_OK "ILM policy exported."
	      else
	        fuUI_WARN "The ILM policy could not be exported."
	        fuDID warn "The ILM policy could not be exported, it is NOT in the backup."
	        rm -f "${myOUT}/ilm_policy_tpot.json"
	    fi
	fi
	if [ -z "$(ls -A "${myOUT}" 2>/dev/null)" ];
	  then
	    rmdir "${myOUT}" 2>/dev/null
	  else
	    fuUI_HINT "Saved to the archive under 'elastic/'."
	fi
	echo
}

# Bring T-Pot back up. Only on request, so nothing changes for anyone who relies on
# the services staying down after a run.
function fuSTART_TPOT () {
	fuUI_INFO "Now starting T-Pot ..."
	if sudo systemctl start tpot.service 2>/dev/null;
	  then
	    mySTOPPED=""
	    mySTARTED="1"
	    fuUI_OK "T-Pot is started."
	    return 0
	fi
	fuUI_WARN "Could not start tpot.service, trying docker compose."
	if [ -f "$HOME/tpotce/docker-compose.yml" ] \
	   && ( cd "$HOME/tpotce" && docker compose up -d ) >/dev/null 2>&1;
	  then
	    mySTOPPED=""
	    mySTARTED="1"
	    fuUI_OK "Started with docker compose."
	    fuDID warn "tpot.service did not start, T-Pot was started with docker compose instead."
	    return 0
	fi
	fuUI_ERROR "Please start T-Pot yourself."
	return 1
}

# Where the data folder really is: TPOT_DATA_PATH of ~/tpotce/.env (KEY=value or KEY: "value"),
# relative to ~/tpotce unless it is absolute or starts with ~, without a trailing slash. From
# `local myPATH` on the same code as fuDATA_PATH of restore.sh (a test compares them).
function fuDATA_PATH () {
	local myTPOTDIR="$HOME/tpotce"
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

# Can fuROLLBACK_CHECKOUT go back at all? Only if the archive of this run points at an older
# commit than the checkout has now and restore.sh is there. It is not after an updater restarted into
# this one without a handover (the tag 24.04.1, the branch master of 2026-08-24): its pull came before
# the backup of this run. The update.sh of the release before on the branch master (what the installed hosts run) hands
# its archive over (myPREPARED): written before its pull, its rollback.txt names the commit that ran.
function fuCAN_ROLLBACK () {
	local myCOMMIT=""
	myCOMMIT=$(tar xOf "${myARCHIVE}" rollback.txt 2>/dev/null | tr -d "[:space:]")
	[ -n "${myCOMMIT}" ] && [ "${myCOMMIT}" != "$(git -C "$HOME/tpotce" rev-parse HEAD 2>/dev/null)" ] \
	  && [ -x "$HOME/tpotce/restore.sh" ]
}

# Put the checkout and the configuration back to where they were before this run,
# so that neither a start nor the daily reboot picks up the new release. Only
# possible if the archive of this run points at an older commit (fuCAN_ROLLBACK).
function fuROLLBACK_CHECKOUT () {
	if ! fuCAN_ROLLBACK;
	  then
	    fuUI_WARN "The archive holds no earlier commit to go back to, the checkout stays on this release."
	    fuUI_HINT "Starting T-Pot now runs the new Elastic Stack on the existing data, free up space first."
	    return 1
	fi
	fuUI_HINT "Putting the checkout and the configuration back to the state before this update."
	local myARGS=(-f "${myARCHIVE}" -c)
	# its own process: the sudo of this run does not reach it, the password file does
	if [ -n "${myBECOME_FILE}" ] && grep -q 'getopts ":[^"]*B:' "$HOME/tpotce/restore.sh";
	  then
	    myARGS+=(-B "${myBECOME_FILE}")
	fi
	# its own phases do not belong to the ones of this update
	if ! TPOT_MARKS="" "$HOME/tpotce/restore.sh" "${myARGS[@]}";
	  then
	    fuUI_ERROR "The checkout and the configuration could not be put back completely, see above. The backup is ${myARCHIVE}."
	    return 1
	fi
	# the summary names the release the host is on now, not the one of the pull
	fuDONE_ROLLED_BACK
	# the checkout is the one before this update again, tpot needs no restart for it
	fuMARK changed back
}

# After fuROLLBACK_CHECKOUT: the items of the summary that named the checkout and the .env of the new
# release go, one item names what the host is on now, read anew from the checkout and .env.
function fuDONE_ROLLED_BACK () {
	local myITEM="" myKEEP=()
	for myITEM in ${myDONE[@]+"${myDONE[@]}"};
	  do
	    case "${myITEM}" in
	      "ok:The checkout is on "*|"ok:Your configuration (.env) is back"*) ;;
	      *) myKEEP+=("${myITEM}") ;;
	    esac
	  done
	myDONE=(${myKEEP[@]+"${myKEEP[@]}"})
	local myVERSION="" myTAG=""
	myVERSION=$(cat "$HOME/tpotce/version" 2>/dev/null | tr -d "[:space:]")
	myTAG=$(fuENV_VALUE "$HOME/tpotce/.env" TPOT_VERSION)
	fuDID info "Back on $(git -C "$HOME/tpotce" rev-parse --abbrev-ref HEAD 2>/dev/null) at $(git -C "$HOME/tpotce" rev-parse --short HEAD 2>/dev/null)${myVERSION:+, version ${myVERSION}}${myTAG:+, TPOT_VERSION=${myTAG}}."
	return 0
}

# After a refusal the checkout and the configuration are the ones from before this update again:
# T-Pot runs again as it ran before. A T-Pot that runs already is left as it is. In the first pass it
# starts where it ran when this run started (myWAS_ACTIVE) or -s asks for it; after a handover, and
# after an updater without one (myOLD_UPDATER_RAN), that is not known: where tpot.service is enabled (a
# reboot would start it as well) or -s asks for it. Only after a rollback that worked: on the new
# checkout a start would upgrade the data. After an updater without a handover the checkout is the new
# release, your .env and compose file from its backup name the images that ran. rc 0 when T-Pot runs.
function fuRESTART_AFTER_REFUSAL () {
	local myON="on the release from before this update"
	[ -z "${myOLD_UPDATER_RAN}" ] || myON="with your .env and docker-compose.yml from the backup of the update.sh before, on their images"
	if [ "$(systemctl is-active tpot 2>/dev/null)" == "active" ];
	  then
	    mySTOPPED=""
	    fuDID info "T-Pot runs, ${myON}."
	    return 0
	fi
	if [ -n "${mySTART}" ];
	  then
	    fuUI_HINT "Starting T-Pot again (-s), ${myON}."
	  elif [ -z "${myPREPARED}" ] && [ -z "${myOLD_UPDATER_RAN}" ];
	    then
	      if [ "${myWAS_ACTIVE}" != "active" ];
	        then
	          fuUI_HINT "T-Pot did not run when this update started, it stays stopped."
	          return 1
	      fi
	      fuUI_HINT "T-Pot ran when this update started, starting it again ${myON}."
	  elif [ "$(systemctl is-enabled tpot 2>/dev/null)" == "enabled" ];
	    then
	      fuUI_HINT "tpot.service is enabled, starting T-Pot again ${myON}."
	  else
	    return 1
	fi
	fuMARK phase start Starting T-Pot
	if fuSTART_TPOT;
	  then
	    fuDID ok "T-Pot runs again, ${myON}."
	    return 0
	fi
	fuDID warn "T-Pot did not start again, please start it yourself."
	return 1
}

# Where a copy of the Elasticsearch data by hand goes: next to the backups, unless the refusal was for want
# of room there (fuAPPEND_ES_DATA, fuES_DATA_FITS): then a place with room, with the size it needs.
function fuES_COPY_TO () {
	if [ "${myES_APPEND_KIND}" == "room" ] || [ "${myES_FITS_KIND}" == "room" ];
	  then
	    echo "<a place with room, e.g. another disk>/elk_data_${myOLD:-old}${myES_WITH:+ (it needs about $(( myES_WITH / 1048576 )) MB)}"
	  else
	    echo "${myBACKUPDIR}/elk_data_${myOLD:-old}"
	fi
}

# The ways on after the Elasticsearch data did not go into the backup, for the hints of fuCHECK_ELASTIC
# and the summary of a refusal. After a handover (myPREPARED) the next run starts with the update.sh
# that started this run: --accept-data-loss goes after `--` then (an older update.sh stops with its
# usage on the option alone, after `--` every one hands it on). Never --full: the update.sh of the
# release before makes room for a full backup by deleting older ones without a question.
function fuES_ROOM_TEXT () {
	if [ -n "${myES_WITH}" ];
	  then echo "Make room in ${myBACKUPDIR} (it needs about $(( myES_WITH / 1048576 )) MB), then run the update again: the Elasticsearch data goes into the backup by itself."
	  else echo "Make room in ${myBACKUPDIR}, then run the update again: the Elasticsearch data goes into the backup by itself."
	fi
}
function fuES_ACCEPT_TEXT () {
	if [ -n "${myPREPARED}" ];
	  then echo "To upgrade without a copy: ./update.sh -y -- --accept-data-loss (an older update.sh rejects --accept-data-loss alone, after -- every one takes it)."
	  else echo "To upgrade without a copy: ./update.sh -y --accept-data-loss."
	fi
}

# The ways on after the step through 8.19 (fuES_STEP_819) was refused before it touched the data:
# the reason the data did not go into the backup, the room it needs, the way by hand (the lines of
# fuES_STEP_MANUAL above) and, where the step was not confirmed, the flag that confirms it
function fuES_STEP_REFUSED_ITEMS () {
	[ -z "${myES_APPEND_WHY}" ] || fuDID info "The Elasticsearch data did not go into the backup: ${myES_APPEND_WHY}"
	[ "${myES_APPEND_KIND}" != "room" ] || fuDID next "$(fuES_ROOM_TEXT)"
	[ -z "${myES_STEP_NEXT}" ] || fuDID next "${myES_STEP_NEXT}"
	fuDID next "Or take the step by hand with T-Pot stopped, as the lines above show (a copy of ${myDATA}/elk/data first), then run the update again."
	[ -n "${myES_STEP_NOT_CONFIRMED}" ] || return 0
	if [ -n "${myPREPARED}" ];
	  then fuDID next "To take the step without a terminal: ./update.sh -y -- --accept-data-loss (an older update.sh rejects --accept-data-loss alone, after -- every one takes it)."
	  else fuDID next "To take the step without a terminal: ./update.sh -y --accept-data-loss."
	fi
}

# The way to put the Elasticsearch data from before the step back from the backup of this run, with
# the restore.sh that is in ~/tpotce now: -g data where it knows it, otherwise (the one of the release
# before after a rollback) by hand, the changed data aside until the archive's is back
function fuES_STEP_RESTORE_TEXT () {
	if grep -q 'getopts ":[^"]*g:' "$HOME/tpotce/restore.sh" 2>/dev/null;
	  then
	    echo "./restore.sh -f ${myARCHIVE} -g data"
	  else
	    echo "sudo mv ${myDATA}/elk/data ${myDATA}/elk/data.step && sudo tar xpf ${myARCHIVE} --numeric-owner -C ${myDATA} --strip-components=1 data/elk/data && sudo rm -rf ${myDATA}/elk/data.step (should the tar fail: sudo rm -rf ${myDATA}/elk/data; sudo mv ${myDATA}/elk/data.step ${myDATA}/elk/data)"
	fi
}

# The ways on after the step had started Elasticsearch 8.19 on the data (myES_STEP_TOUCHED), on the
# output and in the summary alike: the data from before the step comes back from the backup of this
# run (fuES_STEP_RESTORE_TEXT); what then starts depends on the checkout. $1: back (the release
# before, it reads the data again), failed (the rollback did not go through) or none (no rollback
# possible): the new release cannot read the data of myOLD, so not before the checkout is back too,
# or the update runs again and takes the step again.
function fuES_STEP_RESTORE_ITEMS () {   # $1 = back | failed | none
	local myHOW=""
	myHOW=$(fuES_STEP_RESTORE_TEXT)
	fuUI_WARN "The data in ${myDATA}/elk/data may be changed by the step. To put the data from before the step back: ${myHOW}"
	fuDID next "Put the Elasticsearch data from before the step back: ${myHOW}"
	case "$1" in
	  back)
	    fuDID next "Then start T-Pot (sudo systemctl start tpot): the release from before this update reads that data again."
	    if [ "$(systemctl is-enabled tpot 2>/dev/null)" == "enabled" ];
	      then
	        fuDID info "tpot.service is enabled: a reboot before the data is back starts T-Pot on the changed data, and Elasticsearch does not come up until it is back."
	    fi ;;
	  failed)
	    fuDID next "Do not start T-Pot before the checkout is back as well: the new release cannot read the data of Elasticsearch ${myOLD:-?}. ./restore.sh -f ${myARCHIVE} -c puts the checkout back (see above why it did not), then start T-Pot (sudo systemctl start tpot)."
	    fuDID next "Or leave the data as it is and run the update again: it takes the step again." ;;
	  none)
	    fuDID next "Do not start T-Pot on this checkout with that data: the new release cannot read the data of Elasticsearch ${myOLD:-?}. Run the update again instead: it takes the step again."
	    ;;
	esac
	fuDID next "See ${myLOG} for why the step did not go through. Copy ${myARCHIVE} out of ${myBACKUPDIR} before the update runs again: the rotation of the full backups may remove it."
}

# The Elasticsearch upgrade of fuCHECK_ELASTIC was not confirmed (a no, no terminal, Ctrl+C at
# the question or while its data went into the backup), or its step through 8.19 did not go
# through (myES_STEP, myES_REFUSED_WHY says why, fuES_STEP_819): the checkout and the
# configuration go back to the release T-Pot ran so far, and T-Pot starts again on it where it ran
# before (fuRESTART_AFTER_REFUSAL). Where no rollback is possible (myROLLBACK empty, see
# fuCHECK_ELASTIC) T-Pot stays stopped: its next start upgrades the data. myDATA and myOLD are
# the ones of fuCHECK_ELASTIC. The summary says what kept the data out of the backup
# (myES_APPEND_WHY of fuAPPEND_ES_DATA) and the ways on that fit it. Once the step had started
# Elasticsearch 8.19 on the data (myES_STEP_TOUCHED) the release before may not read it any more:
# T-Pot stays stopped, and the summary says how the backup of this run puts the data back.
function fuELASTIC_REFUSED () {
	trap - INT
	local myWHY="${myES_REFUSED_WHY:-the Elasticsearch upgrade was not confirmed}"
	# what the next start of the new release does with data from before 8.19: it cannot read it
	local myNEXT="the next start of T-Pot upgrades the data"
	[ -z "${myES_STEP}" ] || myNEXT="the next start of T-Pot cannot read the data of Elasticsearch ${myOLD:-?}"
	echo
	if [ -n "${myES_STEP_RAN}" ];
	  then fuUI_HINT "The images of the release were not pulled, no image of T-Pot was removed."
	  else fuUI_HINT "Nothing was pulled and no image was removed."
	fi
	if [ -z "${myROLLBACK}" ];
	  then
	    fuUI_HINT "T-Pot stays stopped. Exiting."
	    echo
	    if [ -n "${myES_STEP_TOUCHED}" ];
	      then
	        fuDID fail "Not updated: ${myWHY}. The checkout is already the new release: T-Pot stays stopped."
	        fuES_STEP_RESTORE_ITEMS none
	        exit 5
	    fi
	    fuDID fail "Not updated: ${myWHY}. The checkout is already the new release, ${myNEXT}: T-Pot stays stopped."
	    if [ -n "${myES_STEP}" ];
	      then
	        fuES_STEP_REFUSED_ITEMS
	        exit 5
	    fi
	    [ -z "${myES_APPEND_WHY}" ] || fuDID info "The Elasticsearch data did not go into the backup: ${myES_APPEND_WHY}"
	    fuDID next "Copy ${myDATA}/elk/data first (sudo cp -a ${myDATA}/elk/data $(fuES_COPY_TO)), then run the update again."
	    exit 5
	fi
	# a Ctrl+C now would leave the checkout half way back, and the next start would upgrade
	# the data: the rollback finishes (restore.sh inherits the ignored INT)
	local myRC=0 myRUNS=""
	trap '' INT
	fuROLLBACK_CHECKOUT || myRC=$?
	trap - INT
	if [ "${myRC}" -eq 0 ];
	  then
	    fuDID fail "Not updated: ${myWHY}, the checkout and the configuration are back."
	    # T-Pot runs again, on the release and the data it had before this run; not on data the
	    # step may have changed
	    if [ -n "${myES_STEP_TOUCHED}" ];
	      then
	        fuUI_HINT "T-Pot stays stopped: the release before may not start on data the step changed."
	        fuDID warn "T-Pot stays stopped: the release before may not start on the data the step through Elasticsearch ${myES_STEP_VERSION%.*} changed."
	      else
	        fuRESTART_AFTER_REFUSAL && myRUNS="1"
	    fi
	    fuUI_HINT "Exiting."
	    echo
	  else
	    fuUI_HINT "Exiting."
	    echo
	    if [ -n "${myES_STEP_TOUCHED}" ];
	      then fuDID fail "Not updated: ${myWHY}, and the checkout could not be put back: T-Pot stays stopped."
	      else fuDID fail "Not updated: ${myWHY}, but the checkout could not be put back: ${myNEXT}."
	    fi
	fi
	if [ -n "${myES_STEP_TOUCHED}" ];
	  then
	    if [ "${myRC}" -eq 0 ];
	      then fuES_STEP_RESTORE_ITEMS back
	      else fuES_STEP_RESTORE_ITEMS failed
	    fi
	elif [ -n "${myES_STEP}" ];
	  then
	    fuES_STEP_REFUSED_ITEMS
	else
	    [ -z "${myES_APPEND_WHY}" ] || fuDID info "The Elasticsearch data did not go into the backup: ${myES_APPEND_WHY}"
	    # while T-Pot is stopped its data can be copied by hand; a data/elk/data of another folder in the
	    # archive leaves no other way to a copy
	    if [ -z "${myRUNS}" ] || [ "${myES_APPEND_KIND}" == "stale" ];
	      then
	        fuDID next "Copy ${myDATA}/elk/data first (sudo cp -a ${myDATA}/elk/data $(fuES_COPY_TO), with T-Pot stopped), then run the update again and confirm the upgrade."
	    fi
	    case "${myES_APPEND_KIND}" in
	      room) fuDID next "$(fuES_ROOM_TEXT)" ;;
	      tar)  fuDID next "Or see why tar could not add the data (${myLOG}), then run the update again." ;;
	    esac
	    fuDID next "$(fuES_ACCEPT_TEXT)"
	fi
	if [ -n "${myPREPARED}" ];
	  then
	    fuUI_HINT "Each run of the update.sh that started this run writes a backup to ${myBACKUPDIR} before it gets here, a cron job as well: fix the cause or answer at a terminal."
	    fuDID info "Each run of the update.sh that started this run writes a backup to ${myBACKUPDIR} before it gets here, a cron job as well: fix the cause or answer at a terminal."
	fi
	exit 5
}

# The Elasticsearch version of a T-Pot image: ES_VER is set in every one of them
function fuES_IMAGE_VERSION () {   # $1 = registry, $2 = tag
	docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1/elasticsearch:$2" 2>/dev/null \
	  | sed -n "s/^ES_VER=//p" | head -1
}

# The Elasticsearch version that ran so far: the image of the tag under the registry of .env, else
# under any registry of myREPOS (an installation that just moved from dtagdevsec finds its old one)
function fuOLD_ES_VERSION () {   # $1 = the registry of .env, $2 = tag
	local myREPO="" myVER=""
	for myREPO in "$1" ${myREPOS};
	  do
	    [ -n "${myREPO}" ] || continue
	    myVER=$(fuES_IMAGE_VERSION "${myREPO}" "$2")
	    if [ -n "${myVER}" ];
	      then
	        echo "${myVER}"
	        return 0
	    fi
	  done
	return 1
}

# The archive the update.sh of the tag 24.04.1 wrote before it restarted into this one, if it holds
# the Elasticsearch data: the newest $HOME/*_tpot_backup.tgz, younger than 24 hours. (The update.sh
# of the release before on the branch master hands its archive in ~/tpot_backups over as myARCHIVE
# instead: without --full it holds no data/elk/data, fuAPPEND_ES_DATA adds it.) That script ran
# `tar cvf` (uncompressed despite the name, `tar tf` reads either) of everything in ~/tpotce, so
# it holds a data folder inside ~/tpotce only, under its path there (data/elk/data/...), and the
# .env of that moment, whose TPOT_VERSION must be $2, the version that ran before this update: the
# archive of an update before that one holds the data of a release older than the one that ran.
# The list of its members and its .env are the ones fuOLD_UPDATER_READ took (in a subshell: those
# fuOLD_UPDATER_CONFIG read of the same archive). Prints the archive, rc 1 when there is none.
function fuOLD_RELEASE_BACKUP () {   # $1 = the data folder, $2 = the version that ran before
	local myNEWEST="" myMEMBER="" myVER=""
	case "$1/" in
	  "$HOME/tpotce/"?*) myMEMBER="${1#"$HOME/tpotce/"}/elk/data" ;;
	  *) return 1 ;;
	esac
	myNEWEST=$(fuOLD_UPDATER_ARCHIVE) || return 1
	fuOLD_UPDATER_FRESH "${myNEWEST}" || return 1
	fuOLD_UPDATER_READ "${myNEWEST}" || return 1
	# the folder itself or anything in it, with or without ./ in front
	awk -v myM="${myMEMBER}" '{ sub(/^\.\//, "") } $0 == myM || index($0, myM "/") == 1 { myHIT = 1; exit }
	  END { exit !myHIT }' "${myOLD_DIR}/toc" || return 1
	myVER=$(fuARCHIVE_ENV "${myNEWEST}" TPOT_VERSION)
	[ -n "${myVER}" ] && [ "${myVER}" == "$2" ] || return 1
	echo "${myNEWEST}"
}

# The newest archive the update.sh of the tag 24.04.1 wrote, $HOME/*_tpot_backup.tgz. Prints it, rc 1 for none.
function fuOLD_UPDATER_ARCHIVE () {
	local myNEWEST=""
	myNEWEST=$(ls -1t "$HOME"/*_tpot_backup.tgz 2>/dev/null | head -1)
	[ -n "${myNEWEST}" ] && [ -f "${myNEWEST}" ] || return 1
	echo "${myNEWEST}"
}

# The archive an updater wrote before it reset the checkout and restarted into this script without
# a handover, for fuOLD_UPDATER_CONFIG: the newer of that $HOME/*_tpot_backup.tgz and the newest
# ~/tpot_backups/*_tpot_backup*.tar, the archive of the update.sh of the branch master of 2026-08-24
# (one tar of MANIFEST rollback.txt env tracked.patch docker-compose.yml and some files of data/,
# .env is its member env). That folder also holds the archives of this and of later releases:
# fuOLD_UPDATER_CONFIG takes nothing from one whose .env names the version of the checkout or a
# newer one. A tar there counts only from the last 24 hours (fuOLD_UPDATER_FRESH, by its time, the
# archive is not opened): an older one is never read or named. It runs before the backup of this
# run, which is never the one found. Prints it, rc 1 for none.
function fuOLD_UPDATER_NEWEST () {
	local myTGZ="" myTAR=""
	myTGZ=$(fuOLD_UPDATER_ARCHIVE)
	myTAR=$(ls -1t "${myBACKUPDIR}"/*_tpot_backup*.tar 2>/dev/null | head -1)
	if [ -n "${myTAR}" ] && [ -f "${myTAR}" ] && fuOLD_UPDATER_FRESH "${myTAR}" \
	   && { [ -z "${myTGZ}" ] || [ "${myTAR}" -nt "${myTGZ}" ]; };
	  then
	    echo "${myTAR}"
	    return 0
	fi
	[ -n "${myTGZ}" ] || return 1
	echo "${myTGZ}"
}

# Is that archive from the last 24 hours, i.e. from the run that restarted into this script?
function fuOLD_UPDATER_FRESH () {   # $1 = archive
	[ -n "$(find "$1" -maxdepth 0 -mmin -1440 2>/dev/null)" ]
}

# That archive is read once per run, it holds the data folder and may be large: the list of its
# members (tar tf) and its .env and docker-compose.yml (one tar xf of both) go to a folder in
# myTMPDIR, every lookup of a member and every value comes from there. myOLD_READ names the archive,
# myOLD_DIR the folder. A tar xf that fails (a full disk, an I/O error) may leave half a file: the
# folder is emptied and marked broken, fuOLD_UPDATER_FILE then gives no file of it at all.
# rc 1 when the archive cannot be listed or there is no myTMPDIR.
function fuOLD_UPDATER_READ () {   # $1 = archive
	local myDIR="" myFILE="" myMEMBER="" myMEMBERS=()
	[ -n "${myOLD_READ}" ] && [ "${myOLD_READ}" == "$1" ] && return 0
	[ -n "${myTMPDIR}" ] && [ -d "${myTMPDIR}" ] || return 1
	myDIR=$(mktemp -d "${myTMPDIR}/old_updater.XXXXXX") || return 1
	mkdir "${myDIR}/files" && tar tf "$1" > "${myDIR}/toc" 2>/dev/null || return 1
	for myFILE in .env docker-compose.yml;
	  do
	    myMEMBER=$(fuARCHIVE_MEMBER_NAME "$1" "${myFILE}")
	    myMEMBER=$(grep -m1 -xF -e "${myMEMBER}" -e "./${myMEMBER}" "${myDIR}/toc") && myMEMBERS+=("${myMEMBER}")
	  done
	# the member env of a .tar is the .env of ~/tpotce (a link stays one, fuOLD_UPDATER_FILE refuses it)
	if [ "${#myMEMBERS[@]}" -gt 0 ] \
	   && { ! tar xf "$1" -C "${myDIR}/files" "${myMEMBERS[@]}" 2>/dev/null \
	        || { { [ -e "${myDIR}/files/env" ] || [ -L "${myDIR}/files/env" ]; } \
	             && ! mv -f "${myDIR}/files/env" "${myDIR}/files/.env" 2>/dev/null; }; };
	  then
	    rm -rf "${myDIR}/files"
	    mkdir "${myDIR}/files"
	    : > "${myDIR}/broken"
	fi
	myOLD_READ="$1"
	myOLD_DIR="${myDIR}"
}

# The file of ~/tpotce as fuOLD_UPDATER_READ took it out of that archive: prints its path, rc 1 when
# it is no regular file there (not in the archive, a link, or it could not be taken out) or the
# extract failed.
function fuOLD_UPDATER_FILE () {   # $1 = the file name in ~/tpotce
	[ -n "${myOLD_DIR}" ] && [ ! -e "${myOLD_DIR}/broken" ] || return 1
	[ -f "${myOLD_DIR}/files/$1" ] && [ ! -L "${myOLD_DIR}/files/$1" ] || return 1
	echo "${myOLD_DIR}/files/$1"
}

# A file of that archive that cannot be read: nothing was changed, the run stops.
function fuOLD_UPDATER_UNREADABLE () {   # $1 = archive, $2 = the file name in ~/tpotce, $3 = its member
	fuUI_ERROR "Could not read $2 from $1, the earlier updater had reset it."
	fuUI_HINT "Nothing was changed. Put it back with: tar xOf $1 $3 > ~/tpotce/$2"
	fuUI_HINT "Then run the update again. Exiting."
	echo
	fuDID fail "$2 could not be read from $1: tar xOf $1 $3 > ~/tpotce/$2, then update again."
	exit 1
}

# The name of a file of ~/tpotce in that archive: `tar cvf <archive> * .env` names it .env, a tar of
# `.` names it ./.env, the .tar of the update.sh of 2026-08-24 env (fuARCHIVE_MEMBER_NAME). Prints
# the member, rc 1 when the archive does not hold it.
function fuARCHIVE_MEMBER () {   # $1 = archive, $2 = the file name in ~/tpotce
	local myNAME=""
	fuOLD_UPDATER_READ "$1" || return 1
	myNAME=$(fuARCHIVE_MEMBER_NAME "$1" "$2")
	grep -m1 -xF -e "${myNAME}" -e "./${myNAME}" "${myOLD_DIR}/toc"
}
function fuARCHIVE_MEMBER_NAME () {   # $1 = archive, $2 = the file name in ~/tpotce
	case "$1:$2" in
	  *.tar:.env) echo "env" ;;
	  *) echo "$2" ;;
	esac
}

# A setting of the .env in that archive, read as fuENV_VALUE reads it (KEY=value or KEY: "value",
# quotes and a CR left out). Empty when the archive holds no .env or the .env not the key.
function fuARCHIVE_ENV () {   # $1 = archive, $2 = key
	local myFILE=""
	fuOLD_UPDATER_READ "$1" || return 1
	myFILE=$(fuOLD_UPDATER_FILE .env) || return 1
	fuENV_VALUE "${myFILE}" "$2"
}

# The update.sh of the tag 24.04.1 wrote its backup to $HOME/*_tpot_backup.tgz, the update.sh of the
# branch master of 2026-08-24 to ~/tpot_backups/*_tpot_backup*.tar (fuOLD_UPDATER_NEWEST takes the
# newer of both), then ran `git reset --hard` and restarted into this script without a handover
# (myPREPARED is empty): .env and docker-compose.yml are the files of the release now, your
# configuration is only in that archive.
# Both come back from it before this run checks or writes anything, so its backup and fuRESTORE
# work with your configuration and myOLDVERSION names the version that ran. Only when the archive
# is from the last 24 hours, its .env names an older version than the checkout and the .env here
# is the one of the checkout (unchanged against HEAD: the reset happened) or already the one of the
# archive (a run before this one stopped after it); each file only while it is unchanged against
# HEAD. Both are read from the archive before either is written, in place (mode and owner stay); a
# write that fails puts back the files of the checkout (git show HEAD:<file>) and stops the run.
# Otherwise nothing changes; a .env of the checkout without a web user gets the way to take it
# from the archive yourself. The update.sh of the branch master from 2026-08-25 on (what the
# installed hosts run) needs none of this: it hands over (myPREPARED) and fuRESTORE takes your
# configuration from the archive it hands over.
function fuOLD_UPDATER_CONFIG () {
	local myOLD="" myVER="" myCHECKOUT="" myFILE="" myMEMBER="" myENV="" myUNDO="" myBACK="" myKEPT=""
	local myTAKE=() myWRITTEN=() myAGAIN=() myLEFT=()
	local myGIT=(git -C "$HOME/tpotce")
	[ -z "${myPREPARED}" ] || return 0
	myOLD=$(fuOLD_UPDATER_NEWEST) || return 0
	fuTMPDIR
	fuOLD_UPDATER_READ "${myOLD}" || return 0
	myMEMBER=$(fuARCHIVE_MEMBER "${myOLD}" .env) || return 0
	myENV=$(fuOLD_UPDATER_FILE .env)
	myVER=$(fuARCHIVE_ENV "${myOLD}" TPOT_VERSION)
	myCHECKOUT=$(tr -d "[:space:]" < "$HOME/tpotce/version" 2>/dev/null)
	# the extract failed: its version is unknown, an archive that may count stops the run unchanged
	if [ -e "${myOLD_DIR}/broken" ] && fuOLD_UPDATER_FRESH "${myOLD}" && "${myGIT[@]}" diff --quiet HEAD -- .env 2>/dev/null;
	  then
	    fuOLD_UPDATER_UNREADABLE "${myOLD}" .env "${myMEMBER}"
	fi
	if ! fuOLD_UPDATER_FRESH "${myOLD}" \
	   || ! fuUI_VERSION_GE "${myCHECKOUT}" "${myVER}" || fuUI_VERSION_GE "${myVER}" "${myCHECKOUT}" \
	   || ! { "${myGIT[@]}" diff --quiet HEAD -- .env 2>/dev/null || cmp -s "${myENV}" "$HOME/tpotce/.env"; };
	  then
	    # a .env of the checkout has no web user, a SENSOR has its own .env and none either
	    if "${myGIT[@]}" diff --quiet HEAD -- .env 2>/dev/null \
	       && [ -z "$(fuENV_VALUE "$HOME/tpotce/.env" WEB_USER)" ] \
	       && ! { [ -n "${myENV}" ] && cmp -s "${myENV}" "$HOME/tpotce/.env"; };
	      then
	        fuUI_WARN "$HOME/tpotce/.env is the one of the checkout, without a web user. If your configuration is in ${myOLD}, put it back with: tar xOf ${myOLD} ${myMEMBER} > ~/tpotce/.env"
	        fuDID warn "$HOME/tpotce/.env is the one of the checkout, without a web user (see above)."
	    fi
	    return 0
	fi
	# both files are read before either is written
	for myFILE in .env docker-compose.yml;
	  do
	    myMEMBER=$(fuARCHIVE_MEMBER "${myOLD}" "${myFILE}") || continue
	    if ! "${myGIT[@]}" diff --quiet HEAD -- "${myFILE}" 2>/dev/null;
	      then
	        # a compose file of its own since the reset stays, and is named
	        if [ "${myFILE}" == "docker-compose.yml" ] && fuOLD_UPDATER_FILE "${myFILE}" >/dev/null \
	           && ! cmp -s "$(fuOLD_UPDATER_FILE "${myFILE}")" "$HOME/tpotce/${myFILE}";
	          then
	            myKEPT="docker-compose.yml was changed after the reset, it stays as it is (the archive has the one of ${myVER})."
	        fi
	        continue
	    fi
	    fuOLD_UPDATER_FILE "${myFILE}" >/dev/null || fuOLD_UPDATER_UNREADABLE "${myOLD}" "${myFILE}" "${myMEMBER}"
	    myTAKE+=("${myFILE}")
	  done
	# that updater has stopped T-Pot and pulled already: a cancel later in this run says so (fuADOPT_CANCEL)
	myOLD_UPDATER_RAN="${myOLD}"
	if [ "${#myTAKE[@]}" -eq 0 ];
	  then
	    if [ -n "${myKEPT}" ];
	      then
	        fuUI_OK "Your .env of ${myVER} from ${myOLD} is in place already."
	        fuUI_HINT "${myKEPT}"
	      else
	        fuUI_OK "Your configuration of ${myVER} from ${myOLD} is in place already."
	    fi
	    echo
	    return 0
	fi
	for myFILE in "${myTAKE[@]}";
	  do
	    # `cat >` keeps mode and owner; a failed write puts the files of the checkout back, in place
	    if ! cat "$(fuOLD_UPDATER_FILE "${myFILE}")" > "$HOME/tpotce/${myFILE}";
	      then
	        # every file that changed goes back, each rollback checked: only what is back is said to be.
	        # The file of the checkout is read in full first, a git show that fails leaves the file as it is
	        for myUNDO in ${myWRITTEN[@]+"${myWRITTEN[@]}"} "${myFILE}";
	          do
	            if "${myGIT[@]}" diff --quiet HEAD -- "${myUNDO}" 2>/dev/null \
	               || { "${myGIT[@]}" show "HEAD:${myUNDO}" > "${myOLD_DIR}/head" 2>/dev/null \
	                    && cat "${myOLD_DIR}/head" > "$HOME/tpotce/${myUNDO}" 2>/dev/null; };
	              then
	                [ "${myUNDO}" == "${myFILE}" ] || myAGAIN+=("${myUNDO}")
	              else
	                myLEFT+=("${myUNDO}")
	            fi
	          done
	        myMEMBER=$(fuARCHIVE_MEMBER "${myOLD}" "${myFILE}")
	        fuUI_ERROR "Could not put ${myFILE} back from ${myOLD}, the earlier updater had reset it."
	        [ "${#myAGAIN[@]}" -eq 0 ] || fuUI_HINT "${myAGAIN[*]} is the one of the checkout again."
	        for myUNDO in ${myLEFT[@]+"${myLEFT[@]}"};
	          do
	            fuUI_HINT "${myUNDO} could not be put back to the one of the checkout: git -C ~/tpotce checkout HEAD -- ${myUNDO}"
	            fuDID fail "${myUNDO} could not be put back to the one of the checkout: git -C ~/tpotce checkout HEAD -- ${myUNDO}"
	          done
	        fuUI_HINT "Put it back with: tar xOf ${myOLD} ${myMEMBER} > ~/tpotce/${myFILE}"
	        fuUI_HINT "Then run the update again. Exiting."
	        echo
	        fuDID fail "${myFILE} could not be put back from ${myOLD}: tar xOf ${myOLD} ${myMEMBER} > ~/tpotce/${myFILE}, then update again."
	        exit 1
	    fi
	    myWRITTEN+=("${myFILE}")
	    myBACK="${myBACK:+${myBACK} and }${myFILE}"
	  done
	fuUI_OK "Your configuration of ${myVER} is back from ${myOLD} (the earlier updater had reset it)."
	fuUI_HINT "Put back: ${myBACK}. Other files you changed in ~/tpotce are in that archive as well."
	[ -z "${myKEPT}" ] || fuUI_HINT "${myKEPT}"
	fuDID ok "Your configuration of ${myVER} is back from ${myOLD} (the earlier updater had reset it)."
	echo
}

# Do both paths name the same folder? Each one resolved (realpath, else cd -P), so a symbolic link
# to the data folder is that folder; a path that cannot be resolved counts as written.
function fuSAME_DIR () {   # $1, $2 = the paths
	local myA="" myB=""
	myA=$(realpath -- "$1" 2>/dev/null) || myA=$(cd -P -- "$1" 2>/dev/null && pwd -P) || myA="$1"
	myB=$(realpath -- "$2" 2>/dev/null) || myB=$(cd -P -- "$2" 2>/dev/null && pwd -P) || myB="$2"
	[ "${myA}" == "${myB}" ]
}

# Are both paths on one filesystem? By their device (stat of GNU, else of BSD). A path that cannot
# be told counts as on the same one: the check it guards is then the stricter one.
function fuSAME_FS () {   # $1, $2 = the paths
	local myA="" myB=""
	myA=$(stat -c %d -- "$1" 2>/dev/null || stat -f %d -- "$1" 2>/dev/null)
	myB=$(stat -c %d -- "$2" 2>/dev/null || stat -f %d -- "$2" 2>/dev/null)
	[ -z "${myA}" ] || [ -z "${myB}" ] || [ "${myA}" == "${myB}" ]
}

# Is there room for the Elasticsearch data of the data folder $1 in the backup? Its size (du -sb
# through sudo, as fuBACKUP_SIZE measures) and a margin of 10 % have to fit into the free space of
# myBACKUPDIR (of HOME while that does not exist yet, as fuCHECK_BACKUP_SPACE looks), less $2 bytes
# that are still to be written before (the regular backup, for --check-data-loss), with the reserve
# of every backup (myBACKUP_RESERVE_PERCENT of the partition) still free afterwards. Where the
# backup folder is on the filesystem of the data folder, that filesystem also stays below 85 %:
# Elasticsearch starts to complain there, and stops allocating at 90 %. The size check only, it
# changes nothing. myES_NEED, myES_WITH (with the margin) and myES_FREE hold the bytes,
# myES_FITS_WHY says why not, myES_FITS_KIND is room or size (the size could not be read). rc 0
# when it fits.
function fuES_DATA_FITS () {   # $1 = the data folder, $2 = bytes written before it (default 0)
	local myDIR="${myBACKUPDIR}" myOUT="" myTOTAL="" myRESERVE=0 myBEFORE="${2:-0}" myAFTER=0
	myES_NEED="" myES_WITH="" myES_FREE="" myES_FITS_WHY="" myES_FITS_KIND=""
	[[ "${myBEFORE}" =~ ^[0-9]+$ ]] || myBEFORE=0
	[ -d "${myDIR}" ] || myDIR="$HOME"
	# a du that could not read everything is no size
	myOUT=$(sudo du -sb "$1/elk/data" 2>/dev/null) || myOUT=""
	myES_NEED="${myOUT%%[[:space:]]*}"
	if ! [[ "${myES_NEED}" =~ ^[0-9]+$ ]];
	  then
	    myES_NEED=""
	    myES_FITS_KIND="size"
	    myES_FITS_WHY="the size of $1/elk/data could not be read."
	    return 1
	fi
	myES_WITH=$(( myES_NEED + myES_NEED / 10 ))
	myES_FREE=$(df -B1 --output=avail "${myDIR}" 2>/dev/null | tail -1 | tr -d " ")
	myTOTAL=$(df -B1 --output=size "${myDIR}" 2>/dev/null | tail -1 | tr -d " ")
	if ! [[ "${myES_FREE}" =~ ^[0-9]+$ ]] || ! [[ "${myTOTAL}" =~ ^[0-9]+$ ]] || [ "${myTOTAL}" -eq 0 ];
	  then
	    myES_FITS_KIND="size"
	    myES_FITS_WHY="the free space of ${myDIR} could not be read."
	    return 1
	fi
	myRESERVE=$(( myTOTAL / 100 * myBACKUP_RESERVE_PERCENT ))
	if [ $(( myES_FREE - myBEFORE - myES_WITH )) -lt "${myRESERVE}" ];
	  then
	    myES_FITS_KIND="room"
	    myES_FITS_WHY="$1/elk/data needs about $(( myES_WITH / 1048576 )) MB with a margin of 10 %, ${myDIR} has $(( (myES_FREE - myBEFORE) / 1048576 )) MB free for it and keeps $(( myRESERVE / 1048576 )) MB in reserve."
	    return 1
	fi
	myAFTER=$(( (myTOTAL - myES_FREE + myBEFORE + myES_WITH) * 100 / myTOTAL ))
	if [ "${myAFTER}" -ge 85 ] && fuSAME_FS "${myDIR}" "$1";
	  then
	    myES_FITS_KIND="room"
	    myES_FITS_WHY="with about $(( myES_WITH / 1048576 )) MB of it ${myDIR} would be ${myAFTER}% full, Elasticsearch needs the filesystem of $1 below 85%."
	    return 1
	fi
	return 0
}

# Does the archive of this run hold the Elasticsearch data that runs? Its members are data/...,
# whatever the data path. The update.sh of the branch master always archives ~/tpotce/data: with
# TPOT_DATA_PATH elsewhere a data/elk/data in its archive is that folder, not the one that runs.
# Elsewhere it counts only when the MANIFEST names the data folder that runs (fuBACKUP writes it) or
# this run wrote the archive with --full itself.
function fuARCHIVE_HAS_ES_DATA () {   # $1 = the data folder
	local myFROM=""
	tar tf "${myARCHIVE}" data/elk/data >/dev/null 2>&1 || return 1
	fuSAME_DIR "$1" "$HOME/tpotce/data" && return 0
	myFROM=$(tar xOf "${myARCHIVE}" MANIFEST 2>/dev/null | sed -n 's/^Data folder:[[:space:]]*//p' | head -1)
	if [ -n "${myFROM}" ] && fuSAME_DIR "${myFROM}" "$1";
	  then
	    return 0
	fi
	if [ -z "${myPREPARED}" ] && [ -n "${myFULL}" ];
	  then
	    case "${myARCHIVE##*/}" in *_tpot_backup_full*.tar) return 0 ;; esac
	fi
	return 1
}

# The archive of this run has a new name: everything that names it from here on names the new one,
# the items of the summary ("The backup is ..."), and what a script started from here inherits.
function fuARCHIVE_RENAMED () {   # $1 = the name before, $2 = the new name
	local myI=""
	myARCHIVE="$2"
	# hints of this run printed before name the archive by its old name
	fuUI_HINT "The backup of this run is $2 now (it was $1): the commands above that name $1 take $2."
	for myI in "${!myDONE[@]}";
	  do
	    myDONE[myI]="${myDONE[myI]//"$1"/"$2"}"
	  done
	[ -z "${TPOT_UPDATE_ARCHIVE}" ] || export TPOT_UPDATE_ARCHIVE="$2"
	[ -z "${TPOT_UPDATE_DONE}" ] || TPOT_UPDATE_DONE="${TPOT_UPDATE_DONE//"$1"/"$2"}"
}

# The archive of this run holds no Elasticsearch data (no --full, or the handover of an update.sh that
# never takes it without --full): the data folder $1 adds its elk/data to it, as data/elk/data like
# fuBACKUP, where fuES_DATA_FITS finds room. The archive then has the name of a full one
# (fuARCHIVE_NAME with myFULL), restore.sh and the rotation take it as one, and the full backups
# rotate (fuROTATE). Not into an archive that holds a data/elk/data of another folder already (the
# --full archive of the update.sh of the branch master with TPOT_DATA_PATH elsewhere): on a restore
# the two would mix, and Elasticsearch does not start on that. A tar rf writes from the end of the
# archive on, so its last MiB is kept aside first (more than the end marker and the record around
# it): an append that fails or is stopped puts the archive back as it was, byte for byte, nothing is
# deleted. rc 0 when the data is in the archive now; rc 1 otherwise, myES_APPEND_WHY says why and
# myES_APPEND_KIND what kind of why (stale, room, size, archive, tar, stopped), myES_APPEND_STOPPED
# is set for a Ctrl+C.
function fuAPPEND_ES_DATA () {   # $1 = the data folder
	local myBEFORE="${myARCHIVE}" myNEW="" mySIZE="" myKEEP=0 myTAIL="" myRC=0 myTRAP="" myBACK=""
	myES_APPEND_WHY="" myES_APPEND_KIND="" myES_APPEND_STOPPED=""
	if tar tf "${myARCHIVE}" data/elk/data >/dev/null 2>&1;
	  then
	    fuARCHIVE_HAS_ES_DATA "$1" && return 0
	    myES_APPEND_KIND="stale"
	    myES_APPEND_WHY="${myARCHIVE} holds a data/elk/data already that is not the one of $1 (from $HOME/tpotce/data); a copy added to it would mix with that one on a restore. Copy $1/elk/data by hand."
	    fuUI_WARN "The Elasticsearch data cannot go into the backup: ${myES_APPEND_WHY}"
	    return 1
	fi
	if ! fuES_DATA_FITS "$1";
	  then
	    myES_APPEND_KIND="${myES_FITS_KIND}"
	    myES_APPEND_WHY="${myES_FITS_WHY}"
	    [ "${myES_FITS_KIND}" != "room" ] || myES_APPEND_WHY="no room for it in the backup: ${myES_FITS_WHY}"
	    fuUI_WARN "The Elasticsearch data cannot go into the backup, ${myES_APPEND_WHY}"
	    return 1
	fi
	fuTMPDIR
	mySIZE=$(wc -c < "${myARCHIVE}" 2>/dev/null | tr -d "[:space:]")
	if ! [[ "${mySIZE}" =~ ^[0-9]+$ ]] || [ "${mySIZE}" -lt 1024 ] || [ $(( mySIZE % 512 )) -ne 0 ];
	  then
	    myES_APPEND_KIND="archive"
	    myES_APPEND_WHY="${myARCHIVE} cannot be read as a tar archive."
	    fuUI_WARN "The Elasticsearch data cannot go into the backup, ${myES_APPEND_WHY}"
	    return 1
	fi
	myKEEP=1048576
	[ "${mySIZE}" -ge "${myKEEP}" ] || myKEEP="${mySIZE}"
	myTAIL="${myTMPDIR}/archive.end"
	if ! tail -c "${myKEEP}" "${myARCHIVE}" > "${myTAIL}" 2>/dev/null \
	   || [ "$(wc -c < "${myTAIL}" | tr -d "[:space:]")" != "${myKEEP}" ];
	  then
	    myES_APPEND_KIND="archive"
	    myES_APPEND_WHY="the end of ${myARCHIVE} could not be kept aside."
	    fuUI_WARN "The Elasticsearch data cannot go into the backup, ${myES_APPEND_WHY}"
	    return 1
	fi
	fuUI_HINT "The backup holds no Elasticsearch data yet, adding $1/elk/data (about $(( myES_NEED / 1048576 )) MB, $(( myES_FREE / 1048576 )) MB free)."
	# under the spinner sudo cannot ask; Ctrl+C ends tar, the archive is put back below
	sudo -v
	myTRAP=$(trap -p INT)
	trap 'myES_APPEND_STOPPED="1"' INT
	fuUI_SPIN "Adding the Elasticsearch data to ${myARCHIVE} ..." "${myLOG}" \
	  sudo tar rf "${myARCHIVE}" -p --numeric-owner --transform 'flags=rh;s,^\./,data/,' -C "$1" ./elk/data || myRC=$?
	[ "${myRC}" -ne 130 ] || myES_APPEND_STOPPED="1"
	# a tar that said yes but left no data/elk/data in a readable archive is no backup either
	if [ "${myRC}" -eq 0 ] && ! tar tf "${myARCHIVE}" data/elk/data >/dev/null 2>&1;
	  then
	    myRC=1
	fi
	if [ "${myRC}" -ne 0 ];
	  then
	    # the archive goes back to its size and its end, unstoppable
	    trap '' INT
	    if sudo dd if="${myTAIL}" of="${myARCHIVE}" bs=512 seek=$(( (mySIZE - myKEEP) / 512 )) 2>/dev/null \
	       && [ "$(wc -c < "${myARCHIVE}" | tr -d "[:space:]")" == "${mySIZE}" ] \
	       && tar tf "${myARCHIVE}" >/dev/null 2>&1;
	      then
	        myBACK="1"
	    fi
	    eval "${myTRAP:-trap - INT}"
	    myES_APPEND_KIND="tar"
	    myES_APPEND_WHY="tar could not add it to ${myARCHIVE}, see ${myLOG}."
	    if [ -n "${myES_APPEND_STOPPED}" ];
	      then
	        myES_APPEND_KIND="stopped"
	        myES_APPEND_WHY="it was stopped while it went into ${myARCHIVE}."
	    fi
	    if [ -n "${myBACK}" ];
	      then
	        fuUI_WARN "The Elasticsearch data did not go into the backup, ${myES_APPEND_WHY} The archive is as it was before."
	      else
	        fuUI_ERROR "The Elasticsearch data did not go into the backup, and ${myARCHIVE} could not be put back as it was. Check it with: tar tf ${myARCHIVE}"
	        fuDID warn "${myARCHIVE} could not be put back as it was after the Elasticsearch data did not go into it. Check it with: tar tf ${myARCHIVE}"
	    fi
	    return 1
	fi
	eval "${myTRAP:-trap - INT}"
	myES_APPEND_STOPPED=""
	# a full archive now: its name says so, restore.sh and the rotation go by it
	myNEW=$(myFULL="1" fuARCHIVE_NAME)
	if mv "${myBEFORE}" "${myNEW}" 2>/dev/null;
	  then
	    fuARCHIVE_RENAMED "${myBEFORE}" "${myNEW}"
	  else
	    fuUI_WARN "The archive keeps its name, it could not be renamed to ${myNEW}."
	fi
	fuUI_OK "The Elasticsearch data is now in ${myARCHIVE}."
	fuDID ok "The Elasticsearch data is now in ${myARCHIVE}."
	# the full backups rotate as after a --full backup (the newest, this one, stays)
	myFULL="1" fuROTATE
	return 0
}

# Does the data of Elasticsearch $1 need the step through 8.19 before $2 reads it? An 8 before 8.19
# on the way to 9 or later: Elasticsearch 9 reads only the data of 8.19. rc 0 when it does.
function fuES_NEEDS_STEP () {   # $1 = the version that ran so far, $2 = the version of the release
	local myMAJOR="${2%%.*}" myMINOR=""
	[[ "$1" =~ ^8\.([0-9]+)\.[0-9]+ ]] || return 1
	myMINOR="${BASH_REMATCH[1]}"
	[[ "${myMAJOR}" =~ ^[0-9]+$ ]] && [ "${myMAJOR}" -ge 9 ] && [ "$(( 10#${myMINOR} ))" -lt 19 ]
}

# What runs in the step: Elasticsearch, and Kibana only where the edition has it (its saved objects
# are migrated there; MOBILE runs Elasticsearch without Kibana, its data gets no Kibana indices)
function fuES_STEP_WHAT () {
	if fuCOMPOSE_HAS kibana;
	  then echo "Elasticsearch and Kibana"
	  else echo "Elasticsearch"
	fi
}

# The loss of the step through 8.19, for fuLOSS_CONFIRM and fuLOSS_ADD: the same text in the run
# and in --check-data-loss
function fuES_STEP_TEXT () {   # $1 = the version that ran so far, $2 = the version of the release, $3 = the data folder
	echo "The data of Elasticsearch $1 cannot be read by $2: the update first runs $(fuES_STEP_WHAT) ${myES_STEP_VERSION} once on it (in $3/elk), then the new release. Both steps change the data for good, only the backup of this run brings it back. A backup with the data is attempted first."
}

# The docker run of the step, as arrays, for the step and for the lines by hand (fuES_STEP_MANUAL):
# the official images, the settings of T-Pot's elasticsearch.yml that matter for its data (cluster
# and node name, no machine learning) and no disk watermarks: the step only reads the data in. The
# JDK of the official images can stop on aarch64 hosts without -XX:UseSVE=0 (it did with 8.16 on
# real data), it is set there only. Kibana (myES_STEP_KIBANA) only where the edition has it.
function fuES_STEP_COMMANDS () {   # $1 = the data folder
	local mySVE=""
	case "$(uname -m 2>/dev/null)" in
	  aarch64|arm64) mySVE=" -XX:UseSVE=0" ;;
	esac
	myES_STEP_KIBANA=""
	fuCOMPOSE_HAS kibana && myES_STEP_KIBANA="1"
	myES_STEP_ES_IMAGE="docker.elastic.co/elasticsearch/elasticsearch:${myES_STEP_VERSION}"
	myES_STEP_KB_IMAGE="docker.elastic.co/kibana/kibana:${myES_STEP_VERSION}"
	myES_STEP_IMAGES=("${myES_STEP_ES_IMAGE}")
	[ -z "${myES_STEP_KIBANA}" ] || myES_STEP_IMAGES+=("${myES_STEP_KB_IMAGE}")
	myES_STEP_ES_RUN=(docker run -d --name "${myES_STEP_NAME}" --network "${myES_STEP_NAME}"
	  -p "127.0.0.1:${myES_STEP_ES##*:}:9200" -v "$1/elk/data:/usr/share/elasticsearch/data"
	  -e discovery.type=single-node -e xpack.security.enabled=false
	  -e cluster.routing.allocation.disk.threshold_enabled=false
	  -e cluster.name=tpotcluster -e node.name=tpotcluster-node-01 -e xpack.ml.enabled=false
	  -e "ES_JAVA_OPTS=-Xms1g -Xmx1g${mySVE}")
	[ -z "${mySVE}" ] || myES_STEP_ES_RUN+=(-e CLI_JAVA_OPTS=-XX:UseSVE=0)
	myES_STEP_ES_RUN+=("${myES_STEP_ES_IMAGE}")
	myES_STEP_KB_RUN=(docker run -d --name "${myKB_STEP_NAME}" --network "${myES_STEP_NAME}"
	  -p "127.0.0.1:${myES_STEP_KB##*:}:5601" -e "ELASTICSEARCH_HOSTS=http://${myES_STEP_NAME}:9200"
	  -e TELEMETRY_OPTIN=false "${myES_STEP_KB_IMAGE}")
}

# A command as one line to type: words with a space in quotes
function fuES_STEP_LINE () {   # $@ = the words
	local myWORD="" myLINE=""
	for myWORD in "$@";
	  do
	    case "${myWORD}" in
	      *" "*) myLINE="${myLINE} \"${myWORD}\"" ;;
	      *)     myLINE="${myLINE} ${myWORD}" ;;
	    esac
	  done
	echo "${myLINE# }"
}

# The step by hand, for a refusal before it touched the data: the same commands as fuES_STEP_819
function fuES_STEP_MANUAL () {   # $1 = the data folder
	fuES_STEP_COMMANDS "$1"
	fuUI_HINT "To take the step by hand instead, with T-Pot stopped (sudo systemctl stop tpot):"
	fuUI_HINT "  sudo cp -a $1/elk/data $(fuES_COPY_TO)"
	fuUI_HINT "  docker network create ${myES_STEP_NAME}"
	fuUI_HINT "  sudo chown -R 1000:0 $1/elk/data"
	fuUI_HINT "  $(fuES_STEP_LINE "${myES_STEP_ES_RUN[@]}")"
	if [ -n "${myES_STEP_KIBANA}" ];
	  then
	    fuUI_HINT "  $(fuES_STEP_LINE "${myES_STEP_KB_RUN[@]}")"
	    fuUI_HINT "  wait until curl -s -o /dev/null -w '%{http_code}' ${myES_STEP_KB}/api/status says 200 (Kibana migrated its objects)"
	    fuUI_HINT "  docker stop -t 120 ${myKB_STEP_NAME} ${myES_STEP_NAME}; docker rm ${myKB_STEP_NAME} ${myES_STEP_NAME}; docker network rm ${myES_STEP_NAME}"
	  else
	    fuUI_HINT "  wait until curl -s '${myES_STEP_ES}/_cluster/health?wait_for_status=yellow&timeout=30s' says yellow or green"
	    fuUI_HINT "  docker stop -t 120 ${myES_STEP_NAME}; docker rm ${myES_STEP_NAME}; docker network rm ${myES_STEP_NAME}"
	fi
	fuUI_HINT "  sudo chown -R 2000:2000 $1/elk/data"
	fuUI_HINT "then run the update again (it takes the step once more, on data of 8.19 that goes quickly)."
}

# The step was refused before it touched the data (a no, no room for the data in the backup or for
# the images in the Docker root, Ctrl+C before the step began): the way by hand, then the refusal of
# fuELASTIC_REFUSED
function fuES_STEP_REFUSED () {
	trap - INT
	echo
	fuES_STEP_MANUAL "${myDATA}"
	fuELASTIC_REFUSED
}

# Room for the images of the step in the Docker root (docker info): myES_STEP_SPACE bytes, the
# images unpacked and the layers while they come in. rc 0 when there is room; otherwise
# myES_REFUSED_WHY and myES_STEP_NEXT say why and what to do.
function fuES_STEP_ROOM () {
	local myROOT="" myFREE=""
	myROOT=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)
	[ -n "${myROOT}" ] || myROOT="/var/lib/docker"
	myFREE=$(df -B1 --output=avail "${myROOT}" 2>/dev/null | tail -1 | tr -d " ")
	[[ "${myFREE}" =~ ^[0-9]+$ ]] || myFREE=$(sudo df -B1 --output=avail "${myROOT}" 2>/dev/null | tail -1 | tr -d " ")
	if ! [[ "${myFREE}" =~ ^[0-9]+$ ]];
	  then
	    myES_REFUSED_WHY="the free space of the Docker root ${myROOT} could not be read for the images of the step through Elasticsearch ${myES_STEP_VERSION%.*}"
	    myES_STEP_NEXT="Check that df can read ${myROOT} (docker info names it), then run the update again."
	    fuUI_ERROR "The free space of the Docker root ${myROOT} could not be read."
	    return 1
	fi
	if [ "${myFREE}" -lt "${myES_STEP_SPACE}" ];
	  then
	    myES_REFUSED_WHY="the step through Elasticsearch ${myES_STEP_VERSION%.*} needs about $(( myES_STEP_SPACE / 1048576 )) MB free in the Docker root ${myROOT} for its images, it has $(( myFREE / 1048576 )) MB"
	    myES_STEP_NEXT="Make room in ${myROOT} (about $(( myES_STEP_SPACE / 1048576 )) MB free for the images of the step), then run the update again."
	    fuUI_ERROR "The images of the step need about $(( myES_STEP_SPACE / 1048576 )) MB free in ${myROOT}, it has $(( myFREE / 1048576 )) MB."
	    return 1
	fi
	fuUI_OK "Room for the images of the step in ${myROOT}, $(( myFREE / 1048576 )) MB free."
}

# Under the spinner: Elasticsearch of the step yellow or green (its shards are in), then, where the
# step runs Kibana, Kibana answering /api/status with 200 (its saved objects are migrated); no longer
# than myES_STEP_WAIT seconds for all of it. A cluster that answers red for more than
# myES_STEP_RED seconds in a row stops the wait at once (an answer that is not red starts the count
# anew). rc 0 when it is there; 2 Elasticsearch did not answer in time, 3 it stayed red, 4 it ended,
# 5 Kibana did not answer in time, 6 it ended, 130 stopped (fuES_STEP_819).
function fuES_STEP_WAIT () {
	local myEND=$(( SECONDS + myES_STEP_WAIT )) myHEALTH="" myCODE="" myRED=""
	while :;
	  do
	    [ -z "${myES_STEP_STOPPED}" ] || return 130
	    if [ "$(docker inspect -f '{{.State.Running}}' "${myES_STEP_NAME}" 2>/dev/null)" != "true" ];
	      then
	        echo "Elasticsearch ${myES_STEP_VERSION} is not running any more, the end of its log:"
	        docker logs --tail 40 "${myES_STEP_NAME}" 2>&1
	        return 4
	    fi
	    myHEALTH=$(curl -s -m 40 "${myES_STEP_ES}/_cluster/health?wait_for_status=yellow&timeout=30s" 2>/dev/null \
	      | grep -o '"status" *: *"[a-z]*"' | head -1 | sed 's/.*"\([a-z]*\)"$/\1/')
	    echo "Elasticsearch ${myES_STEP_VERSION}: ${myHEALTH:-no answer yet}"
	    case "${myHEALTH}" in
	      yellow|green) break ;;
	      red)
	        [ -n "${myRED}" ] || myRED="${SECONDS}"
	        [ $(( SECONDS - myRED )) -le "${myES_STEP_RED}" ] || return 3 ;;
	      *) myRED="" ;;
	    esac
	    if [ "${SECONDS}" -ge "${myEND}" ];
	      then
	        [ "${myHEALTH}" != "red" ] || return 3
	        return 2
	    fi
	    sleep "${myES_STEP_PAUSE}"
	  done
	[ -n "${myES_STEP_KIBANA}" ] || return 0
	while :;
	  do
	    [ -z "${myES_STEP_STOPPED}" ] || return 130
	    if [ "$(docker inspect -f '{{.State.Running}}' "${myKB_STEP_NAME}" 2>/dev/null)" != "true" ];
	      then
	        echo "Kibana ${myES_STEP_VERSION} is not running any more, the end of its log:"
	        docker logs --tail 40 "${myKB_STEP_NAME}" 2>&1
	        return 6
	    fi
	    myCODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "${myES_STEP_KB}/api/status" 2>/dev/null)
	    echo "Kibana ${myES_STEP_VERSION}: ${myCODE:-no answer yet}"
	    [ "${myCODE}" != "200" ] || return 0
	    [ "${SECONDS}" -lt "${myEND}" ] || return 5
	    sleep "${myES_STEP_PAUSE}"
	  done
}

# After the step, whatever happened: its containers (Elasticsearch stopped with time to write its
# state), its network and the images this run pulled for it go (myES_STEP_PULLED), the data belongs
# to the uid of T-Pot again where the step had handed it over (sudo refreshed first: the pulls and
# the wait may have outlasted its timestamp). Not to be stopped: runs with INT, TERM and HUP ignored
# (fuES_STEP_819), and without a spinner, which would end on a Ctrl+C.
function fuES_STEP_CLEANUP () {   # $1 = the data folder
	fuUI_INFO "Removing the containers, the network and the images of the step ..."
	{
	  if [ -n "${myES_STEP_KIBANA}" ];
	    then
	      docker stop -t 60 "${myKB_STEP_NAME}"
	      docker rm -f "${myKB_STEP_NAME}"
	  fi
	  docker stop -t 120 "${myES_STEP_NAME}"
	  docker rm -f "${myES_STEP_NAME}"
	  docker network rm "${myES_STEP_NAME}"
	  [ "${#myES_STEP_PULLED[@]}" -eq 0 ] || docker rmi "${myES_STEP_PULLED[@]}"
	} >> "${myLOG}" 2>&1
	myES_STEP_PULLED=()
	[ -n "${myES_STEP_OWNER}" ] || return 0
	fuUI_INFO "Handing $1/elk/data back to T-Pot (2000:2000) ..."
	if sudo -v && sudo chown -R 2000:2000 "$1/elk/data" >> "${myLOG}" 2>&1;
	  then
	    myES_STEP_OWNER=""
	  else
	    fuUI_ERROR "The owner of $1/elk/data could not be set back: sudo chown -R 2000:2000 $1/elk/data"
	    fuDID warn "The owner of $1/elk/data could not be set back after the step: sudo chown -R 2000:2000 $1/elk/data"
	fi
}

# The step of the data of an Elasticsearch before 8.19 through 8.19, after its yes and its backup
# (fuCHECK_ELASTIC), with T-Pot stopped: the official images of Elasticsearch and (where the edition
# has it) Kibana myES_STEP_VERSION, one network, the data handed to the uid of those images
# (1000:0), both started on it until Elasticsearch is yellow or green and Kibana has migrated its
# saved objects (fuES_STEP_WAIT), then everything of the step removed again and the data handed
# back (fuES_STEP_CLEANUP), on every way out. INT, TERM and HUP only end the step: the cleanup runs
# then as well. A reboot meanwhile ends it without the cleanup: the next run takes the step again,
# on the data as the backup holds it once that is back (the hint before it names the way). rc 0
# when it went through; 1 otherwise, with myES_REFUSED_WHY, and myES_STEP_TOUCHED set once
# Elasticsearch 8.19 ran on the data.
function fuES_STEP_819 () {   # $1 = the data folder
	local myWHY="" myRC=0 myTRAP_INT="" myTRAP_TERM="" myTRAP_HUP="" myIMAGE=""
	myES_STEP_STOPPED="" myES_STEP_TOUCHED="" myES_STEP_OWNER=""
	myES_STEP_PULLED=() myES_STEP_RAN="1"
	fuES_STEP_COMMANDS "$1"
	echo
	fuUI_INFO "Taking the step through $(fuES_STEP_WHAT) ${myES_STEP_VERSION} ..."
	fuUI_HINT "Should a reboot cut the step off: put the data back (./restore.sh -f ${myARCHIVE} -g data), then run the update again."
	# under the spinner sudo cannot ask
	sudo -v
	myTRAP_INT=$(trap -p INT)
	myTRAP_TERM=$(trap -p TERM)
	myTRAP_HUP=$(trap -p HUP)
	trap 'myES_STEP_STOPPED="1"' INT TERM HUP
	# what a step that was ended the hard way left behind
	{
	  docker rm -f "${myKB_STEP_NAME}" "${myES_STEP_NAME}"
	  docker network rm "${myES_STEP_NAME}"
	} >> "${myLOG}" 2>&1
	# only what this run pulls goes again afterwards
	for myIMAGE in "${myES_STEP_IMAGES[@]}";
	  do
	    [ -z "${myWHY}${myES_STEP_STOPPED}" ] || break
	    docker image inspect "${myIMAGE}" >/dev/null 2>&1 && continue
	    myES_STEP_PULLED+=("${myIMAGE}")
	    myRC=0
	    fuUI_SPIN "Pulling ${myIMAGE##*/} for the step ..." "${myLOG}" \
	      timeout --foreground 1800 docker pull "${myIMAGE}" || myRC=$?
	    [ "${myRC}" -ne 130 ] || myES_STEP_STOPPED="1"
	    [ "${myRC}" -eq 0 ] || myWHY="the image ${myIMAGE} could not be pulled (see ${myLOG})"
	  done
	if [ -z "${myWHY}${myES_STEP_STOPPED}" ] && ! docker network create "${myES_STEP_NAME}" >> "${myLOG}" 2>&1;
	  then
	    myWHY="its network ${myES_STEP_NAME} could not be created (see ${myLOG})"
	fi
	# the pulls may have outlasted the timestamp of sudo: refreshed before its spinner, which
	# cannot ask
	if [ -z "${myWHY}${myES_STEP_STOPPED}" ] && ! sudo -v;
	  then
	    myWHY="sudo did not work for the owner of $1/elk/data"
	fi
	if [ -z "${myWHY}${myES_STEP_STOPPED}" ];
	  then
	    myES_STEP_OWNER="1"
	    myRC=0
	    fuUI_SPIN "Handing $1/elk/data to the user of the official image (1000:0) ..." "${myLOG}" \
	      sudo chown -R 1000:0 "$1/elk/data" || myRC=$?
	    [ "${myRC}" -ne 130 ] || myES_STEP_STOPPED="1"
	    [ "${myRC}" -eq 0 ] || myWHY="the owner of $1/elk/data could not be changed (see ${myLOG})"
	fi
	if [ -z "${myWHY}${myES_STEP_STOPPED}" ];
	  then
	    if "${myES_STEP_ES_RUN[@]}" >> "${myLOG}" 2>&1;
	      then
	        myES_STEP_TOUCHED="1"
	      else
	        myWHY="Elasticsearch ${myES_STEP_VERSION} did not start (see ${myLOG})"
	    fi
	fi
	if [ -z "${myWHY}${myES_STEP_STOPPED}" ] && [ -n "${myES_STEP_KIBANA}" ] \
	   && ! "${myES_STEP_KB_RUN[@]}" >> "${myLOG}" 2>&1;
	  then
	    myWHY="Kibana ${myES_STEP_VERSION} did not start (see ${myLOG})"
	fi
	if [ -z "${myWHY}${myES_STEP_STOPPED}" ];
	  then
	    myRC=0
	    fuUI_SPIN "Waiting for $(fuES_STEP_WHAT) ${myES_STEP_VERSION} (up to $(( myES_STEP_WAIT / 60 )) min) ..." "${myLOG}" \
	      fuES_STEP_WAIT || myRC=$?
	    [ "${myRC}" -ne 130 ] || myES_STEP_STOPPED="1"
	    case "${myRC}" in
	      0|130) ;;
	      2)   myWHY="Elasticsearch ${myES_STEP_VERSION} did not answer within $(( myES_STEP_WAIT / 60 )) min (TPOT_ES_STEP_WAIT=<seconds> waits longer)" ;;
	      3)   myWHY="the cluster of Elasticsearch ${myES_STEP_VERSION} stayed red for more than ${myES_STEP_RED} s (see ${myLOG})" ;;
	      4)   myWHY="Elasticsearch ${myES_STEP_VERSION} ended on the data (see ${myLOG})" ;;
	      5)   myWHY="Kibana ${myES_STEP_VERSION} did not answer within $(( myES_STEP_WAIT / 60 )) min (TPOT_ES_STEP_WAIT=<seconds> waits longer)" ;;
	      6)   myWHY="Kibana ${myES_STEP_VERSION} ended (see ${myLOG})" ;;
	      *)   myWHY="the wait for $(fuES_STEP_WHAT) ${myES_STEP_VERSION} ended with ${myRC} (see ${myLOG})" ;;
	    esac
	fi
	# from here on nothing stops it
	trap '' INT TERM HUP
	fuES_STEP_CLEANUP "$1"
	eval "${myTRAP_INT:-trap - INT}"
	eval "${myTRAP_TERM:-trap - TERM}"
	eval "${myTRAP_HUP:-trap - HUP}"
	[ -z "${myES_STEP_STOPPED}" ] || myWHY="it was stopped"
	if [ -n "${myWHY}" ];
	  then
	    myES_REFUSED_WHY="the step through Elasticsearch ${myES_STEP_VERSION%.*} did not go through: ${myWHY}"
	    return 1
	fi
	fuUI_OK "The Elasticsearch data is upgraded to ${myES_STEP_VERSION}."
	return 0
}

# Elasticsearch and Kibana upgrade their data in place on the first start of a
# newer version, and there is no way back to the older one. Runs after the pull of
# the repository and before the pull of the images, in the restarted script as
# well: everything before the stop is done by the update.sh the user started,
# which may be an older one. T-Pot is stopped at this point.
function fuCHECK_ELASTIC () {
	local myNEW="" myOLD="" myDATA="" myUSED=0 myOLDARCHIVE="" myTEXT="" myROLLBACK=""
	# the step through 8.19 and what its refusal says (fuES_STEP_819, fuELASTIC_REFUSED)
	myES_STEP="" myES_STEP_NOT_CONFIRMED="" myES_REFUSED_WHY="" myES_STEP_NEXT="" myES_STEP_TOUCHED=""
	myES_STEP_RAN="" myES_STEP_PULLED=()
	fuCOMPOSE_HAS elasticsearch || return
	echo
	fuUI_INFO "Checking the Elastic Stack update ..."
	myNEW=$(sed -n "s/^ARG ES_VER=//p" "$HOME/tpotce/docker/elk/elasticsearch/Dockerfile" 2>/dev/null | head -1)
	if [ -z "${myNEW}" ];
	  then
	    fuUI_WARN "Cannot tell which Elasticsearch version this release ships, skipping the check."
	    fuDID warn "The Elasticsearch version of this release could not be told, its upgrade was not checked."
	    echo
	    return
	fi
	# The images of the previous version are still there, fuSTOP_TPOT only prunes
	# the untagged ones.
	myOLD=$(fuOLD_ES_VERSION "$(grep -E "^TPOT_REPO=" "$HOME/tpotce/.env" | tail -1 | cut -d= -f2-)" "${myOLDVERSION}")
	if [ "${myOLD}" == "${myNEW}" ];
	  then
	    fuUI_OK "Elasticsearch stays on ${myNEW}."
	    echo
	    return
	fi
	if [ -z "${myOLD}" ];
	  then
	    fuUI_WARN "Cannot tell which Elasticsearch version ran so far, assuming it changes to ${myNEW}."
	    fuDID warn "The Elasticsearch version that ran so far could not be told, the update took it as a change to ${myNEW}."
	  else
	    fuUI_HINT "Elasticsearch ${myOLD} -> ${myNEW}."
	fi
	# Elasticsearch stops allocating new shards at 90%, the next daily index would
	# stay red and nothing gets indexed any more
	myDATA=$(fuDATA_PATH)
	myUSED=$(df --output=pcent "${myDATA}" 2>/dev/null | tail -1 | tr -dc "0-9")
	if [ -n "${myUSED}" ] && [ "${myUSED}" -ge 90 ];
	  then
	    fuUI_ERROR "${myDATA} is ${myUSED}% full, Elasticsearch needs it below 90%."
	    fuUI_HINT "Nothing was pulled and no image was removed."
	    fuDID fail "${myDATA} is ${myUSED}% full, Elasticsearch needs it below 90%: free up space and update again."
	    # as after every refusal: the release from before, and T-Pot running where it ran
	    if fuROLLBACK_CHECKOUT && fuRESTART_AFTER_REFUSAL;
	      then
	        fuUI_HINT "Free up space and run the update again."
	      else
	        fuUI_HINT "Free up space and run the update again. T-Pot is stopped, 'systemctl start tpot' brings it back."
	    fi
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	if [ -n "${myUSED}" ] && [ "${myUSED}" -ge 85 ];
	  then
	    fuUI_WARN "${myDATA} is ${myUSED}% full, Elasticsearch starts to complain at 85%."
	    fuDID warn "${myDATA} is ${myUSED}% full, Elasticsearch starts to complain at 85%."
	fi
	# Elasticsearch 9 reads only the data of 8.19: the data of an older 8 takes the step through
	# 8.19 first (fuES_STEP_819), after a yes (elastic-step) and with the data in the backup of this
	# run, also where an older backup holds it already: the way back names this archive. A no, no
	# room for the data or the images, or a step that does not go through is a refusal
	# (fuES_STEP_REFUSED before the step touched the data, fuELASTIC_REFUSED after).
	if fuES_NEEDS_STEP "${myOLD}" "${myNEW}";
	  then
	    myES_STEP="1"
	    fuUI_HINT "Elasticsearch ${myNEW} reads only the data of ${myES_STEP_VERSION%.*}: the data of ${myOLD} takes a step through ${myES_STEP_VERSION} first."
	    myROLLBACK=""
	    fuCAN_ROLLBACK && myROLLBACK="1"
	    myTEXT=$(fuES_STEP_TEXT "${myOLD}" "${myNEW}" "${myDATA}")
	    # Ctrl+C at the question is a no as well
	    myES_STEP_NOT_CONFIRMED="1"
	    myES_REFUSED_WHY="the step through Elasticsearch ${myES_STEP_VERSION%.*} was not confirmed"
	    trap fuES_STEP_REFUSED INT
	    fuLOSS_CONFIRM elastic-step "${myTEXT}" || fuES_STEP_REFUSED
	    myES_STEP_NOT_CONFIRMED=""
	    # from the backup up to the step's own trap a Ctrl+C is a refusal as well: the rollback, and
	    # T-Pot starts again (the append and the step put this trap back when they end)
	    myES_REFUSED_WHY="the step through Elasticsearch ${myES_STEP_VERSION%.*} was stopped before it began"
	    fuTMPDIR
	    if ! fuAPPEND_ES_DATA "${myDATA}";
	      then
	        myES_REFUSED_WHY="the Elasticsearch data did not go into the backup of this run, the step through ${myES_STEP_VERSION%.*} needs it there"
	        fuES_STEP_REFUSED
	    fi
	    fuES_STEP_ROOM || fuES_STEP_REFUSED
	    if ! fuES_STEP_819 "${myDATA}";
	      then
	        if [ -z "${myES_STEP_TOUCHED}" ];
	          then
	            fuUI_ERROR "The ${myES_REFUSED_WHY#the }. The data was not touched."
	            fuES_STEP_REFUSED
	        fi
	        fuUI_ERROR "The ${myES_REFUSED_WHY#the }. The data in ${myDATA}/elk may be changed already: the backup ${myARCHIVE} puts the data from before the step back, the summary says how."
	        fuELASTIC_REFUSED
	    fi
	    trap - INT
	    fuDID ok "Elasticsearch data upgraded to ${myES_STEP_VERSION%.*} for the step to ${myNEW}."
	    echo
	    return
	fi
	# A backup with the data is the only way back to the old version: the archive of this run when
	# its data/elk/data is the folder that runs (the `--full` one of this run, fuARCHIVE_HAS_ES_DATA)
	if fuARCHIVE_HAS_ES_DATA "${myDATA}";
	  then
	    fuUI_OK "The Elasticsearch data is in ${myARCHIVE}."
	    echo
	    return
	fi
	# or the one the update.sh of the tag 24.04.1 wrote before it restarted into this one without a
	# handover: no copy then. After a handover such an archive is from an earlier run, the copy into
	# the archive of this run is the backup of the data as it is now. fuOLD_RELEASE_BACKUP keeps what
	# it reads of that archive in myTMPDIR
	fuTMPDIR
	if [ -z "${myPREPARED}" ] && myOLDARCHIVE=$(fuOLD_RELEASE_BACKUP "${myDATA}" "${myOLDVERSION}");
	  then
	    fuUI_OK "The Elasticsearch data is in ${myOLDARCHIVE}, the backup of the update.sh of the release before."
	    echo
	    return
	fi
	# a no puts the checkout back (fuELASTIC_REFUSED), unless it is the new release already
	myROLLBACK=""
	fuCAN_ROLLBACK && myROLLBACK="1"
	# Not in a backup yet (no --full, or a handover): the data goes into the archive of this run
	# now, where there is room, without a question. A Ctrl+C meanwhile is a no
	if fuAPPEND_ES_DATA "${myDATA}";
	  then
	    echo
	    return
	fi
	[ -z "${myES_APPEND_STOPPED}" ] || fuELASTIC_REFUSED
	myTEXT="Elasticsearch ${myOLD:-?} -> ${myNEW} upgrades the data in ${myDATA}/elk on the next start. This cannot be undone and the backup of this run does not hold it."
	if [ -n "${myROLLBACK}" ];
	  then
	    fuUI_HINT "T-Pot is stopped right now. To keep a copy of the data, answer no, copy it, e.g.:"
	    fuUI_HINT "  sudo cp -a ${myDATA}/elk/data $(fuES_COPY_TO)"
	    fuUI_HINT "and run the update again."
	    [ "${myES_APPEND_KIND}" != "room" ] || fuUI_HINT "$(fuES_ROOM_TEXT)"
	    fuUI_HINT "$(fuES_ACCEPT_TEXT)"
	  else
	    fuUI_HINT "T-Pot is stopped right now."
	    myTEXT="${myTEXT} A no cannot stop the next start of T-Pot from upgrading the data: the checkout is already the new release. Copy ${myDATA}/elk/data first (sudo cp -a ${myDATA}/elk/data $(fuES_COPY_TO)), then run the update again."
	fi
	# Ctrl+C at the question is a no as well: the checkout goes back either way
	trap fuELASTIC_REFUSED INT
	if fuLOSS_CONFIRM elastic "${myTEXT}";
	  then
	    trap - INT
	    echo
	    return
	fi
	fuELASTIC_REFUSED
}

# Backup
#
# Only what cannot be restored otherwise goes in. Everything tracked comes back
# from git and an update leaves `data/` alone - apart from Elasticsearch and Kibana
# upgrading their data on a new Elastic Stack, see fuCHECK_ELASTIC and `--full` -
# so what is irreplaceable are the user's own changes, the configuration and a
# handful of files under `data/`. Hence a list instead of a glob, and hence no
# compression: the archive is small enough that compressing it would only cost time.
function fuBACKUP () {
	local myStage=""
	local myTARGETS=""
	local myJEWEL=""
	local myFILE=""
	local myJEWELLIST=()
	local myJEWELARGS=()
	local myDATA=""
	echo
	fuUI_INFO "Create a backup, just in case ... "
	# The second pass would archive the checkout that the pull has already reset, and
	# fuRESTORE would then put that back - the user's configuration would be gone
	# while the run still reported success.
	if [ -n "${myPREPARED}" ];
	  then
	    fuUI_OK "Keeping the backup from before the restart: ${myARCHIVE}"
	    # the first pass named it already, unless it was an update.sh that hands over no items
	    [ -n "${TPOT_UPDATE_DONE}" ] || fuDID ok "The backup is ${myARCHIVE}."
	    echo
	    return
	fi
	fuTMPDIR
	if ! mkdir -p "${myBACKUPDIR}" || ! chmod 0700 "${myBACKUPDIR}";
	  then
	    fuUI_ERROR "Could not prepare ${myBACKUPDIR}."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	myARCHIVE=$(fuARCHIVE_NAME)
	# fuEXPORT_ELASTIC may already have put elastic/ in here
	myStage="${myTMPDIR}/stage"
	mkdir -p "${myStage}"

	# What git cannot bring back
	# --binary: without it a changed binary file is only a "differ" line and cannot come back
	git -C "$HOME/tpotce" diff --binary HEAD > "${myStage}/tracked.patch"
	git -C "$HOME/tpotce" rev-parse HEAD > "${myStage}/rollback.txt"
	cp "$HOME/tpotce/.env" "${myStage}/env" 2>/dev/null
	[ -f "$HOME/tpotce/docker-compose.yml" ] && cp "$HOME/tpotce/docker-compose.yml" "${myStage}/"

	# Untracked files that are not ignored, with their paths
	while IFS= read -r myFILE;
	  do
	    [ -z "${myFILE}" ] && continue
	    mkdir -p "${myStage}/untracked/$(dirname "${myFILE}")"
	    cp -a "$HOME/tpotce/${myFILE}" "${myStage}/untracked/${myFILE}" 2>/dev/null
	  done < <(git -C "$HOME/tpotce" ls-files --others --exclude-standard)

	# A note saying what this is and how to get back
	{
	  echo "T-Pot backup"
	  echo "Created:      $(date '+%Y-%m-%d %H:%M:%S %z')"
	  echo "Hostname:     $(hostname)"
	  echo "Edition:      ${myEDITION:-unknown}"
	  echo "TPOT_TYPE:    $(grep -E '^TPOT_TYPE=' "$HOME/tpotce/.env" 2>/dev/null | tail -1 | cut -d= -f2-)"
	  echo "TPOT_VERSION: $(grep -E '^TPOT_VERSION=' "$HOME/tpotce/.env" 2>/dev/null | tail -1 | cut -d= -f2-)"
	  echo "Commit:       $(git -C "$HOME/tpotce" rev-parse HEAD) ($(git -C "$HOME/tpotce" rev-parse --abbrev-ref HEAD))"
	  echo "Repository:   $(git -C "$HOME/tpotce" remote get-url origin 2>/dev/null)"
	  # where data/ of the archive comes from: fuARCHIVE_HAS_ES_DATA trusts its data/elk/data by it
	  echo "Data folder:  $(fuDATA_PATH)"
	  echo
	  echo "Restore with 'restore.sh -f <this archive>'."
	  echo "To go back to the commit before the update:"
	  echo "  cd ~/tpotce && git reset --hard \$(cat rollback.txt)"
	} > "${myStage}/MANIFEST"

	# env and tracked.patch carry the credentials from .env, so the modes have to be
	# tight inside the archive already - extracting must not widen them
	chmod -R go-rwx "${myStage}"

	myTARGETS="MANIFEST rollback.txt tracked.patch"
	# Only name what is actually there, or tar stops at a missing member
	[ -f "${myStage}/env" ]                && myTARGETS="${myTARGETS} env"
	[ -f "${myStage}/docker-compose.yml" ] && myTARGETS="${myTARGETS} docker-compose.yml"
	[ -d "${myStage}/untracked" ]          && myTARGETS="${myTARGETS} untracked"
	[ -d "${myStage}/elastic" ]            && myTARGETS="${myTARGETS} elastic"

	# With `--full` all of data/, otherwise only the irreplaceable files. Both belong
	# to tpot:tpot with 0770, which the archive has to record. The data folder is the
	# one TPOT_DATA_PATH names; elsewhere than ~/tpotce/data its files go into the
	# archive as data/..., the way restore.sh looks for them.
	myDATA=$(fuDATA_PATH)
	if [ "${myDATA}" == "$HOME/tpotce/data" ];
	  then
	    if [ -n "${myFULL}" ];
	      then
	        [ -d "${myDATA}" ] && myJEWELLIST+=("data")
	      else
	        for myJEWEL in ${myJEWELS};
	          do
	            [ -e "$HOME/tpotce/${myJEWEL}" ] && myJEWELLIST+=("${myJEWEL}")
	          done;
	    fi
	    if [ ${#myJEWELLIST[@]} -gt 0 ];
	      then
	        myJEWELARGS=(-C "$HOME/tpotce" "${myJEWELLIST[@]}")
	    fi
	  else
	    if [ -n "${myFULL}" ];
	      then
	        [ -d "${myDATA}" ] && myJEWELLIST+=(".")
	      else
	        for myJEWEL in ${myJEWELS};
	          do
	            [ -e "${myDATA}/${myJEWEL#data/}" ] && myJEWELLIST+=("./${myJEWEL#data/}")
	          done;
	    fi
	    if [ ${#myJEWELLIST[@]} -gt 0 ];
	      then
	        # flags=rh: h rewrites the targets of hard links too (they are archive member names, like the
	        # entries), the targets of symbolic links stay as they are
	        myJEWELARGS=(--transform 'flags=rh;s,^\./,data/,' -C "${myDATA}" "${myJEWELLIST[@]}")
	    fi
	fi

	# A single tar run: intermediate files created under sudo belong to root and
	# could not be moved afterwards. It runs under the spinner, which cannot ask for
	# a password, so sudo is refreshed first.
	local myRC=0
	sudo -v
	fuUI_SPIN "Building the ${myFULL:+full }archive ${myARCHIVE} ..." "${myLOG}" \
	  sudo tar cf "${myARCHIVE}" -p --numeric-owner -C "${myStage}" ${myTARGETS} "${myJEWELARGS[@]}" || myRC=$?
	if [ "${myRC}" -eq 130 ];
	  then
	    # Ctrl+C: half an archive is no backup, restore.sh would offer it as the newest
	    sudo rm -f "${myARCHIVE}"
	    fuDID warn "Stopped while writing the backup, the checkout was not touched."
	    exit 130
	fi
	if [ "${myRC}" -ne 0 ];
	  then
	    fuUI_HINT "Exiting."
	    echo
	    fuDID fail "The backup could not be written, the checkout was not touched."
	    exit 1
	fi
	if ! sudo chown "$(id -u):$(id -g)" "${myARCHIVE}" || ! chmod 0600 "${myARCHIVE}";
	  then
	    fuUI_ERROR "Could not take ownership of ${myARCHIVE}. Exiting."
	    echo
	    exit 1
	fi
	fuUI_OK "The archive holds $(tar tf "${myARCHIVE}" | wc -l) entries, $(du -h "${myARCHIVE}" | cut -f1)."
	fuDID ok "The backup is ${myARCHIVE}."
	fuROTATE
	# Point at the old archives from before this directory existed, once
	if ls "$HOME"/*_tpot_backup.tgz >/dev/null 2>&1;
	  then
	    fuUI_HINT "Note: older backups are still in $HOME, new ones go to ${myBACKUPDIR}."
	fi
	echo
}

# Remove the images of earlier versions. "Earlier" is every tag other than the one
# .env names now - no hardcoded list that has to be bumped with each release, and
# nothing that is in use can be caught, because the tag in use is the one kept.
function fuREMOVEOLDIMAGES () {
	local myKEEP="" myREPO="" myTAG="" myLIST="" myTOTAL=0 myLEFT=0 myIMAGE=""
	local myALL=()
	myKEEP=$(grep -E "^TPOT_VERSION=" "$HOME/tpotce/.env" 2>/dev/null | tail -1 | cut -d= -f2-)
	echo
	if [ -z "${myKEEP}" ];
	  then
	    fuUI_INFO "Not touching any images, cannot tell from .env which tag is in use."
	    return
	fi
	fuUI_INFO "Removing docker images of earlier versions, keeping :${myKEEP} ..."
	for myREPO in ${myREPOS};
	  do
	    myLIST=$(fuIMAGES "${myREPO}" "${myKEEP}")
	    [ -z "${myLIST}" ] && continue
	    for myTAG in $(echo "${myLIST}" | sed "s/.*://" | sort -u);
	      do
	        fuUI_HINT "${myREPO}: $(echo "${myLIST}" | grep -c ":${myTAG}$") image(s) tagged :${myTAG}"
	      done;
	    mapfile -t -O "${#myALL[@]}" myALL <<< "${myLIST}"
	done;
	# An image built here, or one the registry did not confirm in time, may never come back: it
	# only goes once that is confirmed (fuLOSS_CONFIRM). Otherwise it stays and the update goes on.
	local myPULLABLE=() myBUILT=() myUNSURE=()
	fuSORT_IMAGES ${myALL[@]+"${myALL[@]}"}
	myALL=(${myPULLABLE[@]+"${myPULLABLE[@]}"})
	if [ "${#myBUILT[@]}" -gt 0 ];
	  then
	    if fuLOSS_CONFIRM images "$(fuLOCAL_IMAGES_TEXT "${myBUILT[@]}")" "${myBUILT[@]}";
	      then
	        myALL+=("${myBUILT[@]}")
	      else
	        fuUI_HINT "Keeping them, the update goes on. 'docker rmi <image>' removes one later."
	        fuDID info "Kept ${#myBUILT[@]} image(s) built here, nothing can pull them again."
	    fi
	fi
	if [ "${#myUNSURE[@]}" -gt 0 ];
	  then
	    if fuLOSS_CONFIRM images "$(fuUNSURE_IMAGES_TEXT "${myUNSURE[@]}")" "${myUNSURE[@]}";
	      then
	        myALL+=("${myUNSURE[@]}")
	      else
	        fuUI_HINT "Keeping them, the update goes on. 'docker rmi <image>' removes one later."
	        fuDID info "Kept ${#myUNSURE[@]} image(s) that could not be confirmed as pullable again."
	    fi
	fi
	myTOTAL="${#myALL[@]}"
	if [ "${myTOTAL}" -eq 0 ];
	  then
	    fuUI_OK "Nothing to remove."
	    return
	fi
	local myRC=0
	fuUI_SPIN "Removing ${myTOTAL} image(s) ..." "${myLOG}" fuRMI "${myALL[@]}" || myRC=$?
	if [ "${myRC}" -eq 130 ];
	  then
	    fuDID warn "Stopped while removing the images of earlier versions, the rest of them stay."
	    exit 130
	fi
	# what is still there is in use by a container of its own
	for myREPO in ${myREPOS};
	  do
	    for myIMAGE in $(fuIMAGES "${myREPO}" "${myKEEP}");
	      do
	        printf '%s\n' "${myALL[@]}" | grep -qxF "${myIMAGE}" && myLEFT=$((myLEFT + 1))
	      done;
	done;
	if [ "${myLEFT}" -eq 0 ];
	  then
	    fuUI_OK "Removed ${myTOTAL} image(s)."
	    fuDID ok "Removed ${myTOTAL} image(s) of earlier versions."
	  else
	    fuUI_WARN "Removed $((myTOTAL - myLEFT)) of ${myTOTAL} image(s), ${myLEFT} still in use, see ${myLOG}."
	    fuDID warn "${myLEFT} image(s) of earlier versions are still in use and stay."
	fi
}

# Can the image be pulled again? Only if the registry still serves its digest: RepoDigests
# alone do not tell, the containerd image store gives a local build one as well. rc 0: the
# registry has the digest; 1: built here (no digest, or another one than the registry's);
# 2: cannot tell (no timeout or buildx, a timeout, no network, any other error). 1 and 2 are
# kept unless confirmed (or the run asks).
function fuIMAGE_PULLABLE () {   # $1 = image, $2 = seconds the registry may take (default 20)
	local myLOCAL="" myREMOTE=""
	myLOCAL=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1" 2>/dev/null \
	          | sed -n 's/^[^@]*@//p')
	[ -n "${myLOCAL}" ] || return 1
	command -v timeout > /dev/null 2>&1 || return 2
	myREMOTE=$(timeout "${2:-20}" docker buildx imagetools inspect --format '{{.Manifest.Digest}}' "$1" 2>/dev/null) || return 2
	myREMOTE=$(printf '%s' "${myREMOTE}" | tr -d "[:space:]")
	[ -n "${myREMOTE}" ] || return 2
	printf '%s\n' "${myLOCAL}" | grep -qxF -- "${myREMOTE}" || return 1
}

# The images sorted by fuIMAGE_PULLABLE into myPULLABLE, myBUILT (built here) and myUNSURE (cannot
# tell), the arrays of the caller. One deadline for all of them (myPULLABLE_DEADLINE): once it is
# over, every image left cannot be told, without another request to the registry.
function fuSORT_IMAGES () {   # $@ = images
	local myIMAGE="" myRC=0 myLEFT=0 myEND=$((SECONDS + ${myPULLABLE_DEADLINE:-90}))
	myPULLABLE=() myBUILT=() myUNSURE=()
	for myIMAGE in "$@";
	  do
	    myLEFT=$((myEND - SECONDS))
	    if [ "${myLEFT}" -le 0 ];
	      then
	        myUNSURE+=("${myIMAGE}")
	        continue
	    fi
	    [ "${myLEFT}" -le 20 ] || myLEFT=20
	    myRC=0
	    fuIMAGE_PULLABLE "${myIMAGE}" "${myLEFT}" || myRC=$?
	    case "${myRC}" in
	      0) myPULLABLE+=("${myIMAGE}") ;;
	      1) myBUILT+=("${myIMAGE}") ;;
	      *) myUNSURE+=("${myIMAGE}") ;;
	    esac
	  done
}

# The loss of the images built here, for fuLOSS_CONFIRM and fuLOSS_ADD
function fuLOCAL_IMAGES_TEXT () {   # $@ = images
	local myIMAGE="" myLIST=""
	for myIMAGE in "$@"; do myLIST="${myLIST}${myLIST:+, }${myIMAGE}"; done
	echo "$# image(s) built here cannot be pulled again: ${myLIST}"
}

# The loss of the images the registry did not confirm, for fuLOSS_CONFIRM and fuLOSS_ADD
function fuUNSURE_IMAGES_TEXT () {   # $@ = images
	local myIMAGE="" myLIST=""
	for myIMAGE in "$@"; do myLIST="${myLIST}${myLIST:+, }${myIMAGE}"; done
	echo "$# image(s) could not be confirmed as pullable again (registry not reachable or slow): ${myLIST}"
}

# The T-Pot images of one registry without the tag in use, repository:tag each
function fuIMAGES () {   # $1 = registry, $2 = the tag to keep
	docker images --format "{{.Repository}}:{{.Tag}}" 2>/dev/null | grep "^$1/" | grep -v ":$2$"
}

# Under the spinner: remove the images. One a container still uses stays, docker says
# so in the log and fuREMOVEOLDIMAGES counts it.
function fuRMI () {   # $@ = images
	docker rmi "$@" || true
}

function fuPULLIMAGES () {
	docker compose -f ~/tpotce/docker-compose.yml pull
}

function fuUPDATER () {
	fuUI_HINT "This might take a while, please be patient!"
	local myRC=0
	fuUI_SPIN "Pulling the images of this release ..." "${myLOG}" fuPULLIMAGES || myRC=$?
	if [ "${myRC}" -eq 130 ];
	  then
	    fuDID warn "Stopped during the image pull, T-Pot pulls what is missing when it starts."
	    exit 130
	fi
	if [ "${myRC}" -eq 0 ];
	  then
	    myPULLOK="1"
	    fuDID ok "The images of this release are pulled."
	  else
	    echo
	    fuMARK warn pull Not all images could be pulled, T-Pot pulls them again when it starts.
	    fuUI_WARN "Could not pull all images."
	    fuUI_HINT "Every service pulls on start, so T-Pot will not come up until they are there."
	    fuUI_HINT "Your .env asks for $(grep -E "^TPOT_REPO=" "$HOME/tpotce/.env" | tail -1 | cut -d= -f2-)/*:$(grep -E "^TPOT_VERSION=" "$HOME/tpotce/.env" | tail -1 | cut -d= -f2-), this release ships ${newVERSION}."
	    fuUI_HINT "If the tag is pinned on purpose, make sure those images exist. Otherwise:"
	    fuUI_HINT "  sed -i 's|^TPOT_VERSION=.*|TPOT_VERSION=${newVERSION}|' $HOME/tpotce/.env"
	    fuUI_HINT "  docker compose -f $HOME/tpotce/docker-compose.yml pull"
	    fuDID warn "Not all images could be pulled, T-Pot pulls them again when it starts (see above)."
	fi
	# The images of the earlier tags are the ones T-Pot would still start from: they go only when
	# the pull brought the images of this release
	if [ -n "${myPULLOK}" ];
	  then
	    fuMARK phase cleanup Removing old images
	    fuREMOVEOLDIMAGES
	  else
	    fuUI_HINT "No image was removed, as the images of this release are not all there."
	fi
	echo
	if [ -n "${myCOMPOSE_CUSTOMIZED}" ];
	  then
	    fuUI_INFO "If you made changes to docker-compose.yml please ensure to add them again."
	    fuUI_INFO "Your previous one is in the backup as 'docker-compose.yml'."
	fi
	fuUI_INFO "We stored the previous version as backup in $myARCHIVE."
	fuUI_INFO "Your own Kibana objects and the ILM policy were saved to the archive before"
	fuUI_INFO "T-Pot was stopped, 'restore.sh' can put them back."
	fuUI_INFO "Some updates ship newer Kibana objects. Download them here if they changed:"
	fuUI_INFO "https://raw.githubusercontent.com/telekom-security/tpotce/refs/heads/master/docker/tpotinit/dist/etc/objects/kibana_export.ndjson.zip"
	fuUI_INFO "Import through the Kibana WebUI: Management > Saved Objects > Import"
	echo
}

function fuRESTORE () {
	if [ -f '~/tpotce/data/ews/conf/ews.cfg' ] && ! grep 'ews.cfg' $myCOMPOSEFILE > /dev/null; then
	    echo
	    fuUI_INFO "Restoring volume mount for ews.cfg in tpot.yml"
	    sed -i '/- ${TPOT_DATA_PATH}:\/data/a \ \ \ \ \ - ${TPOT_DATA_PATH}/ews/conf/ews.cfg:/opt/ewsposter/ews.cfg' $myCOMPOSEFILE
	fi
	fuUI_INFO "Restoring T-Pot config file .env"
	fuTMPDIR
	# `-C` only applies to the members named after it, hence it comes first. And the
	# return value is checked: a restore that failed silently left T-Pot starting
	# without a web login while the run still reported "Done".
	if ! tar xf "${myARCHIVE}" -C "${myTMPDIR}" env 2>"${myTMPDIR}/untar.err";
	  then
	    fuUI_ERROR "Could not read 'env' from ${myARCHIVE}."
	    sed 's/^/    /' "${myTMPDIR}/untar.err"
	    fuUI_HINT "Refusing to continue with a default configuration."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	if ! cp "${myTMPDIR}/env" "$HOME/tpotce/.env";
	  then
	    fuUI_ERROR "Could not write $HOME/tpotce/.env."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	fuUI_OK "Restored from ${myARCHIVE}."
	# .env records the TPOT_VERSION that docker compose resolves the image tags from,
	# so it has to follow the release - otherwise the new compose file asks for images
	# of the old version, and honeypots added since then have no image at all.
	#
	# The tag comes from env.example, not from `version`: at 24.04.0 those differed
	# (release 24.04.0, images tagged 24.04), and env.example is the file that ships
	# what .env should hold.
	newVERSION=$(grep -E "^TPOT_VERSION=" "$HOME/tpotce/env.example" 2>/dev/null | tail -1 | cut -d= -f2-)
	[ -z "${newVERSION}" ] && newVERSION=$(cat "$HOME/tpotce/version")
	myOLDVERSION=$(grep -E "^TPOT_VERSION=" "$HOME/tpotce/.env" | tail -1 | cut -d= -f2-)
	# TPOT_VERSION="24.04.1" or the CR of a file written on Windows: the version all the same
	myOLDVERSION="${myOLDVERSION%$'\r'}"
	case "${myOLDVERSION}" in \"*\"|\'*\') myOLDVERSION="${myOLDVERSION:1:${#myOLDVERSION}-2}" ;; esac
	# Digits and dots are a release tag, `24.04` as much as `24.04.1`. Only a value
	# with something else in it - `dev`, a branch name - is a deliberate pin.
	if echo "${myOLDVERSION}" | grep -qE "^[0-9]+(\.[0-9]+)+$";
	  then
	    if [ "${myOLDVERSION}" != "${newVERSION}" ];
	      then
	        sed -i "s|^TPOT_VERSION=.*|TPOT_VERSION=${newVERSION}|" "$HOME/tpotce/.env"
	        fuUI_HINT "TPOT_VERSION ${myOLDVERSION} -> ${newVERSION}."
	    fi
	  else
	    fuUI_HINT "Keeping TPOT_VERSION=${myOLDVERSION}, it is not a release version."
	fi
	# dtagdevsec was the default before the images moved to ghcr, and Docker Hub rate
	# limits are a known issue. An installation still on that old default is raised;
	# a registry someone picked themselves is left alone.
	myOLDREPO=$(grep -E "^TPOT_REPO=" "$HOME/tpotce/.env" | tail -1 | cut -d= -f2-)
	myNEWREPO=$(grep -E "^TPOT_REPO=" "$HOME/tpotce/env.example" 2>/dev/null | tail -1 | cut -d= -f2-)
	if [ "${myOLDREPO}" == "dtagdevsec" ] && [ -n "${myNEWREPO}" ] && [ "${myNEWREPO}" != "dtagdevsec" ];
	  then
	    sed -i "s|^TPOT_REPO=.*|TPOT_REPO=${myNEWREPO}|" "$HOME/tpotce/.env"
	    fuUI_HINT "TPOT_REPO dtagdevsec -> ${myNEWREPO}, which avoids the Docker Hub rate limits."
	fi
	fuMIGRATE_BEELZEBUB_ENV "$HOME/tpotce/.env"
	fuMIGRATE_TOGGLES "$HOME/tpotce/.env"
	# After the migrations, which look for the settings of the previous release.
	# A compose file of its own (the one of the archive) may use settings env.example
	# does not know, those are kept.
	fuCOMPOSE_FROM_ARCHIVE || true
	fuMERGE_ENV_KEYS "$HOME/tpotce/.env" "$HOME/tpotce/env.example" "$HOME/tpotce/docker-compose.yml" "${myTMPDIR}/docker-compose.yml"
	fuMIGRATE_RETENTION "$HOME/tpotce/.env" "${myTMPDIR}/env" \
	  "$(fuRETENTION_EXPORT "${myTMPDIR}/stage/elastic/ilm_policy_tpot.json" "${myARCHIVE}")"
	fuDID ok "Your configuration (.env) is back, TPOT_VERSION=$(grep -E "^TPOT_VERSION=" "$HOME/tpotce/.env" | tail -1 | cut -d= -f2-)."
}

# Value of a .env setting, written as KEY=value or KEY: "value".
function fuENV_VALUE () {
	grep -E "^$2[[:space:]]*[:=]" "$1" 2>/dev/null | tail -1 \
	  | sed -E "s/^[^:=]*[:=][[:space:]]*//; s/[[:space:]]*$//; s/^[\"']//; s/[\"']$//"
}

# 24.04.2 builds Beelzebub from upstream, which splits the LLM setting into a
# provider and a model name. The old section (BEELZEBUB_LLM_MODEL "ollama" or
# "gpt4-o" plus BEELZEBUB_OLLAMA_MODEL) is rewritten once, BEELZEBUB_LLM_HOST is
# kept. Runs again without effect once BEELZEBUB_LLM_PROVIDER is there.
function fuMIGRATE_BEELZEBUB_ENV () {
	local myENVFILE="$1"
	local myOLDMODEL myOLLAMAMODEL myHOST myAPIKEY myPROVIDER myMODEL myNEWFILE myNOTMP
	if grep -qE "^BEELZEBUB_LLM_PROVIDER[[:space:]]*[:=]" "${myENVFILE}" \
	   || ! grep -qE "^BEELZEBUB_LLM_MODEL[[:space:]]*[:=]" "${myENVFILE}";
	  then
	    return 0
	fi
	myOLDMODEL=$(fuENV_VALUE "${myENVFILE}" BEELZEBUB_LLM_MODEL)
	myOLLAMAMODEL=$(fuENV_VALUE "${myENVFILE}" BEELZEBUB_OLLAMA_MODEL)
	myHOST=$(fuENV_VALUE "${myENVFILE}" BEELZEBUB_LLM_HOST)
	myAPIKEY=$(fuENV_VALUE "${myENVFILE}" BEELZEBUB_OPENAISECRETKEY)
	case "${myOLDMODEL}" in
	  ollama)
	    myPROVIDER="ollama"
	    myMODEL="${myOLLAMAMODEL:-llama3.1:8b}"
	    ;;
	  gpt4-o)
	    myPROVIDER="openai"
	    myMODEL="gpt-4o"
	    # The Ollama default never worked with OpenAI, empty means the OpenAI endpoint
	    [ "${myHOST}" == "http://ollama.local:11434/api/chat" ] && myHOST=""
	    ;;
	  *)
	    fuUI_HINT "BEELZEBUB_LLM_MODEL=${myOLDMODEL} is neither \"ollama\" nor \"gpt4-o\", please set up the Beelzebub section of .env as in env.example."
	    return 0
	    ;;
	esac
	# The new text goes to a temporary file, not to a name next to .env: files of their own under
	# such names stay untouched; without one the warning goes into the summary as well
	if ! myNEWFILE=$(mktemp "${TMPDIR:-/tmp}/tpot-env-beelzebub.XXXXXX");
	  then
	    myNOTMP="No temporary file could be made, the Beelzebub settings of .env are not migrated, set them up as in env.example."
	    fuUI_WARN "${myNOTMP}"
	    fuDID warn "${myNOTMP}"
	    return 0
	fi
	# The old comments describe the old settings, the new block replaces the old assignment
	if ! awk -v provider="${myPROVIDER}" -v model="${myMODEL}" -v host="${myHOST}" -v apikey="${myAPIKEY}" '
	  /^# BEELZEBUB_(LLM_MODEL|LLM_HOST|OLLAMA_MODEL|OPENAISECRETKEY):/ { next }
	  /^BEELZEBUB_(LLM_HOST|OLLAMA_MODEL|OPENAISECRETKEY)[[:space:]]*[:=]/ { next }
	  /^BEELZEBUB_LLM_MODEL[[:space:]]*[:=]/ {
	    print "# BEELZEBUB_LLM_PROVIDER: Set to \"ollama\" or \"openai\"."
	    print "# BEELZEBUB_LLM_MODEL: Set to the model served by the provider, i.e. \"llama3.1:8b\" (ollama) or \"gpt-4o-mini\" (openai)."
	    print "# BEELZEBUB_LLM_HOST: Full URL of the chat endpoint, leave empty to use the provider default."
	    print "# BEELZEBUB_LLM_API_KEY: Only required for \"openai\"."
	    print "BEELZEBUB_LLM_PROVIDER: \"" provider "\""
	    print "BEELZEBUB_LLM_MODEL: \"" model "\""
	    print "BEELZEBUB_LLM_HOST: \"" host "\""
	    print "BEELZEBUB_LLM_API_KEY: \"" apikey "\""
	    next
	  }
	  { print }
	' "${myENVFILE}" > "${myNEWFILE}";
	  then
	    rm -f "${myNEWFILE}"
	    fuUI_ERROR "Could not migrate the Beelzebub settings in ${myENVFILE}, please set them up as in env.example."
	    fuDID warn "The Beelzebub settings of .env could not be migrated, set them up as in env.example."
	    return 0
	fi
	# cat keeps the owner and the mode of .env, it holds credentials
	cat "${myNEWFILE}" > "${myENVFILE}"
	rm -f "${myNEWFILE}"
	fuUI_OK "Beelzebub settings moved to BEELZEBUB_LLM_PROVIDER=${myPROVIDER}, BEELZEBUB_LLM_MODEL=${myMODEL}."
}

# tpotinit accepts only the values the scripts act on. Synonyms that passed its
# validation before are rewritten once, i.e. TPOT_PERSISTENCE=true did not keep the
# logs, only "on" does. Unknown values are reported, tpotinit refuses them.
function fuMIGRATE_TOGGLES () {
	local myENVFILE="$1" myKEY myOLD myNEW
	for myKEY in TPOT_PERSISTENCE TPOT_BLACKHOLE TPOT_ATTACKMAP_TEXT;
	  do
	    myOLD=$(fuENV_VALUE "${myENVFILE}" "${myKEY}")
	    [ -z "${myOLD}" ] && continue
	    case "${myKEY}:$(echo "${myOLD}" | tr '[:upper:]' '[:lower:]')" in
	      TPOT_PERSISTENCE:on|TPOT_PERSISTENCE:true|TPOT_PERSISTENCE:enabled|TPOT_PERSISTENCE:yes)
	        myNEW="on" ;;
	      TPOT_PERSISTENCE:off|TPOT_PERSISTENCE:false|TPOT_PERSISTENCE:disabled|TPOT_PERSISTENCE:no)
	        myNEW="off" ;;
	      *:on|*:true|*:enabled|*:yes)
	        myNEW="ENABLED" ;;
	      *:off|*:false|*:disabled|*:no)
	        myNEW="DISABLED" ;;
	      *)
	        fuUI_ERROR "${myKEY}=${myOLD} is not a valid value, T-Pot will not start until it is set as described in env.example."
	        fuDID warn "${myKEY}=${myOLD} in .env is not a valid value, T-Pot will not start until it is set as described in env.example."
	        continue ;;
	    esac
	    [ "${myOLD}" == "${myNEW}" ] && continue
	    sed -i -E "s/^(${myKEY}[[:space:]]*[:=][[:space:]]*)[\"']?${myOLD}[\"']?[[:space:]]*$/\1${myNEW}/" "${myENVFILE}"
	    if [ "$(fuENV_VALUE "${myENVFILE}" "${myKEY}")" != "${myNEW}" ];
	      then
	        fuUI_ERROR "Could not change ${myKEY}=${myOLD} to ${myNEW}, please set it in .env."
	        fuDID warn "${myKEY}=${myOLD} could not be changed to ${myNEW}, please set it in .env."
	        continue
	    fi
	    if [ "${myKEY}" == "TPOT_PERSISTENCE" ] && [ "${myNEW}" == "on" ];
	      then
	        fuUI_OK "TPOT_PERSISTENCE ${myOLD} -> on, with ${myOLD} the logs were deleted on every start, only \"on\" keeps them."
	      else
	        fuUI_OK "${myKEY} ${myOLD} -> ${myNEW}."
	    fi
	done
}

# The export of fuEXPORT_ELASTIC: in the same run it is still in the stage folder ($1,
# ${myTMPDIR}/stage/elastic). A restarted run (myPREPARED, fuSELFUPDATE) has a new myTMPDIR -
# the old one is removed before the exec and the export is skipped as "already saved" - so
# the file comes from the archive ($2) then, where it is `elastic/`. Prints the path of a
# usable export, nothing if there is none.
function fuRETENTION_EXPORT () {
	local mySTAGE="$1" myARCHIVE="$2"
	if [ -s "${mySTAGE}" ];
	  then
	    echo "${mySTAGE}"
	    return 0
	fi
	[ -n "${myARCHIVE}" ] && [ -f "${myARCHIVE}" ] && [ -n "${myTMPDIR}" ] || return 0
	tar xf "${myARCHIVE}" -C "${myTMPDIR}" elastic/ilm_policy_tpot.json 2>/dev/null
	[ -s "${myTMPDIR}/elastic/ilm_policy_tpot.json" ] && echo "${myTMPDIR}/elastic/ilm_policy_tpot.json"
	return 0
}

# TPOT_ES_RETENTION is new in 24.04.2, before it the retention lived only in the ILM policy
# tpot (Kibana). The first update with it takes the delete phase of the exported policy over,
# so that .env says what the policy does and T-Pot never shortens it: 365 days stay 365 days. After a
# handover the export is the one the update.sh before wrote into its archive (fuRETENTION_EXPORT).
# Without an export, with one that cannot be read or with a value that is no whole number of
# days it is left empty: T-Pot then leaves the policy as it is (and creates a missing one with
# 30 days), nothing shortens an unknown policy by one click.
# $1 = .env, $2 = .env of the archive, $3 = exported policy (ilm_policy_tpot.json)
function fuMIGRATE_RETENTION () {
	local myENVFILE="$1" myOLDENV="$2" myEXPORT="$3" myVALUE="" myTMP=""
	# without the .env of the release before it is unknown whether the key was set: nothing is written
	[ -r "${myOLDENV}" ] && [ -r "${myENVFILE}" ] || return 0
	grep -qE "^TPOT_ES_RETENTION[[:space:]]*[:=]" "${myOLDENV}" 2>/dev/null && return 0
	grep -qE "^TPOT_ES_RETENTION[[:space:]]*[:=]" "${myENVFILE}" || return 0
	if [ -s "${myEXPORT}" ];
	  then
	    myVALUE=$(cd "$HOME/tpotce" 2>/dev/null && python3 -m tpotctl.elastic value-of "${myEXPORT}" 2>/dev/null)
	    [ -z "${myVALUE}" ] && myVALUE=$(python3 -m tpotctl.elastic value-of "${myEXPORT}" 2>/dev/null)
	fi
	# rewritten in place (cat >), so that owner, mode and inode of .env stay; no sed -i (BSD sed differs)
	myTMP=$(mktemp) || myTMP=""
	if [ -n "${myTMP}" ] \
	   && sed -E "s/^(TPOT_ES_RETENTION[[:space:]]*[:=][[:space:]]*).*$/\1${myVALUE}/" "${myENVFILE}" > "${myTMP}" \
	   && { cat "${myTMP}" > "${myENVFILE}"; } 2>/dev/null \
	   && grep -qE "^TPOT_ES_RETENTION[[:space:]]*[:=][[:space:]]*${myVALUE}[[:space:]]*$" "${myENVFILE}";
	  then
	    if [ -n "${myVALUE}" ];
	      then
	        fuUI_OK "TPOT_ES_RETENTION=${myVALUE}, the retention of the ILM policy tpot, T-Pot keeps it."
	      else
	        fuUI_WARN "TPOT_ES_RETENTION is left empty, T-Pot leaves the ILM policy tpot as it is; tpot elastic retention shows it, --adopt lets T-Pot manage it."
	        fuUI_HINT "The exported policy could not be read or keeps the events for no whole number of days."
	        fuDID warn "TPOT_ES_RETENTION is left empty, T-Pot leaves the ILM policy tpot as it is (tpot elastic retention shows it)."
	    fi
	  else
	    fuUI_WARN "TPOT_ES_RETENTION could not be written into ${myENVFILE}, it stays $(fuENV_VALUE "${myENVFILE}" TPOT_ES_RETENTION); tpot elastic retention shows the ILM policy tpot, --adopt lets T-Pot manage it."
	    fuDID warn "TPOT_ES_RETENTION could not be written into .env (tpot elastic retention shows the ILM policy tpot)."
	fi
	[ -z "${myTMP}" ] || rm -f "${myTMP}"
}

# Keys of a .env style file in file order, written as KEY=value or KEY: "value".
function fuENV_KEYS () {
	grep -oE '^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*[:=]' "$1" 2>/dev/null | sed -E 's/[[:space:]]*[:=]$//' | awk '!mySEEN[$0]++'
}

# A restored .env only knows the settings of its release. Settings that are new in
# env.example are added with their comments and defaults, settings env.example no
# longer knows are commented out, unless a compose file still uses them. Existing
# values are never changed, a second run changes nothing.
# $1 = .env, $2 = env.example, $3... = compose files to check for references
function fuMERGE_ENV_KEYS () {
	local myENVFILE="$1" myEXAMPLE="$2"
	shift 2
	local myVERSION myMARKER myKEY myKEYLINE myTARGET myCOMPOSE myADDED="" myOBSOLETE="" myKEPT=""
	local myUSERFILE mySYSTEMFILE myMERGEFILE
	local myNOTMP="No temporary file could be made, the new settings of env.example are not merged into .env, compare them with env.example."
	[ -f "${myEXAMPLE}" ] && [ -f "${myENVFILE}" ] || return 0
	myVERSION=$(grep -E "^TPOT_VERSION=" "${myEXAMPLE}" | tail -1 | cut -d= -f2-)
	myMARKER=$(grep -n "^# NEVER MAKE CHANGES" "${myEXAMPLE}" | head -1 | cut -d: -f1)
	# Temporary files, not names next to .env (.env.user, .env.merge ...): files of their own under
	# these names stay untouched; without them the warning goes into the summary as well
	myUSERFILE=$(mktemp "${TMPDIR:-/tmp}/tpot-env-user.XXXXXX") || { fuUI_WARN "${myNOTMP}"; fuDID warn "${myNOTMP}"; return 0; }
	mySYSTEMFILE=$(mktemp "${TMPDIR:-/tmp}/tpot-env-system.XXXXXX") || { rm -f "${myUSERFILE}"; fuUI_WARN "${myNOTMP}"; fuDID warn "${myNOTMP}"; return 0; }
	myMERGEFILE=$(mktemp "${TMPDIR:-/tmp}/tpot-env-merge.XXXXXX") || { rm -f "${myUSERFILE}" "${mySYSTEMFILE}"; fuUI_WARN "${myNOTMP}"; fuDID warn "${myNOTMP}"; return 0; }
	for myKEY in $(fuENV_KEYS "${myEXAMPLE}");
	  do
	    fuENV_KEYS "${myENVFILE}" | grep -qx "${myKEY}" && continue
	    case "${myKEY}" in
	      WEB_USER|LS_WEB_USER|TPOT_HIVE_USER)
	        # Credentials are never set to the example values
	        fuUI_HINT "${myKEY} is missing in .env, please set it as described in env.example."
	        continue
	        ;;
	    esac
	    myKEYLINE=$(grep -nE "^${myKEY}[[:space:]]*[:=]" "${myEXAMPLE}" | head -1 | cut -d: -f1)
	    myTARGET="${myUSERFILE}"
	    [ -n "${myMARKER}" ] && [ "${myKEYLINE}" -gt "${myMARKER}" ] && myTARGET="${mySYSTEMFILE}"
	    # The key line and the comments right above it, without the section rulers
	    awk -v myAT="${myKEYLINE}" '
	      { myLINE[NR] = $0 }
	      END {
	        myFIRST = myAT
	        while (myFIRST > 1 && myLINE[myFIRST - 1] ~ /^#/ && myLINE[myFIRST - 1] !~ /^####/) myFIRST--
	        print ""
	        for (i = myFIRST; i <= myAT; i++) print myLINE[i]
	      }' "${myEXAMPLE}" >> "${myTARGET}"
	    myADDED="${myADDED} ${myKEY}"
	done
	for myKEY in $(fuENV_KEYS "${myENVFILE}");
	  do
	    fuENV_KEYS "${myEXAMPLE}" | grep -qx "${myKEY}" && continue
	    for myCOMPOSE in "$@";
	      do
	        if [ -f "${myCOMPOSE}" ] && grep -qE "\\\$\\{${myKEY}([:}-]|\$)" "${myCOMPOSE}";
	          then
	            myKEPT="${myKEPT} ${myKEY}"
	            continue 2
	        fi
	    done
	    myOBSOLETE="${myOBSOLETE} ${myKEY}"
	done
	if [ -n "${myADDED}" ] || [ -n "${myOBSOLETE}" ];
	  then
	    # New settings go in front of the ruler above "# NEVER MAKE CHANGES", system settings to the end
	    if awk -v myUSERFILE="${myUSERFILE}" -v mySYSTEMFILE="${mySYSTEMFILE}" -v myOBSOLETE=" ${myOBSOLETE} " -v myVERSION="${myVERSION}" '
	      function fuBLOCK(myFILE,  myL, myN) {
	        myN = 0
	        while ((getline myL < myFILE) > 0) { if (!myN++) printf "# Added by update.sh for T-Pot %s", myVERSION; print (myN == 1 && myL == "" ? "" : myL) }
	        close(myFILE)
	        if (myN) print ""
	      }
	      function fuOBSOLETE(myL,  myKEY) {
	        if (myL !~ /^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*[:=]/) return 0
	        myKEY = myL
	        sub(/[[:space:]]*[:=].*/, "", myKEY)
	        return index(myOBSOLETE, " " myKEY " ")
	      }
	      { myLINE[NR] = $0 }
	      /^# NEVER MAKE CHANGES/ && !myAT { myAT = (NR > 1 && myLINE[NR - 1] ~ /^####/) ? NR - 1 : NR }
	      END {
	        if (!myAT) myAT = NR + 1
	        for (i = 1; i <= NR; i++) {
	          if (i == myAT) fuBLOCK(myUSERFILE)
	          if (fuOBSOLETE(myLINE[i])) print "# " myLINE[i] "  # obsolete since T-Pot " myVERSION ", commented out by update.sh"
	          else print myLINE[i]
	        }
	        if (myAT > NR) fuBLOCK(myUSERFILE)
	        fuBLOCK(mySYSTEMFILE)
	      }' "${myENVFILE}" > "${myMERGEFILE}";
	      then
	        # cat keeps the owner and the mode of .env, it holds credentials
	        cat "${myMERGEFILE}" > "${myENVFILE}"
	        [ -n "${myADDED}" ] && fuUI_OK "New settings added to .env with their defaults:${myADDED}"
	        [ -n "${myOBSOLETE}" ] && fuUI_OK "Settings no longer used, commented out in .env:${myOBSOLETE}"
	      else
	        fuUI_ERROR "Could not add the new settings to ${myENVFILE}, please compare it with env.example."
	        fuDID warn "The new settings could not be added to .env, please compare it with env.example."
	    fi
	fi
	[ -n "${myKEPT}" ] && fuUI_HINT "Not in env.example, kept as a compose file uses them:${myKEPT}"
	rm -f "${myUSERFILE}" "${mySYSTEMFILE}" "${myMERGEFILE}"
	return 0
}

# The docker-compose.yml as it was before the update, taken from the archive. It is
# the fifth member, ahead of data/, so this stays cheap even for a --full archive.
function fuCOMPOSE_FROM_ARCHIVE () {
	if [ -z "${myARCHIVE}" ] || [ ! -f "${myARCHIVE}" ];
	  then
	    return 1
	fi
	fuTMPDIR
	tar xf "${myARCHIVE}" -C "${myTMPDIR}" docker-compose.yml 2>/dev/null \
	  && [ -s "${myTMPDIR}/docker-compose.yml" ]
}

# What kind of compose file is it? An edition of myEDITIONS (its name), CUSTOM (built by
# compose/customizer.py from v2 on, fuCUSTOM_FILE), V1 (written by the customizer before v2: its edition
# line is '# T-Pot: CUSTOM EDITION') or OWN: none of these, a compose file of your own.
function fuCOMPOSE_KIND () {   # $1 = compose file
	local myNAME="" i=""
	myNAME=$(fuEDITION_LINE "$1" | cut -d " " -f2-)
	if [ "${myNAME}" == "CUSTOM" ] && fuCUSTOM_FILE "$1";
	  then
	    echo "CUSTOM"
	    return
	fi
	if [ "${myNAME}" == "CUSTOM EDITION" ];
	  then
	    echo "V1"
	    return
	fi
	for i in ${myEDITIONS};
	  do
	    if [ "${myNAME}" == "$i" ];
	      then
	        echo "$i"
	        return
	    fi
	  done
	echo "OWN"
}

# The Python for compose/customizer.py: the one of the venv of the T-Pot Manager (PyYAML is in it),
# else python3 with the PyYAML of the distribution (fuCUSTOMIZER_DEPS).
function fuCUSTOMIZER_PYTHON () {
	local myVENV="${XDG_DATA_HOME:-$HOME/.local/share}/tpotce/venv/bin/python3"
	if [ -x "${myVENV}" ];
	  then
	    echo "${myVENV}"
	  else
	    echo "python3"
	fi
}

# The customizer that adopts: after the pull (restore) the one of the checkout, with the catalogue of
# the release; before it (check) that one too, unless it cannot adopt yet, then the one next to this
# update.sh.
function fuADOPT_CUSTOMIZER () {   # $1 = check | restore
	local myCUSTOMIZER="$HOME/tpotce/compose/customizer.py"
	if [ "$1" == "check" ] && ! grep -q -- "--adopt" "${myCUSTOMIZER}" 2>/dev/null;
	  then
	    myCUSTOMIZER="${myHERE}/compose/customizer.py"
	fi
	echo "${myCUSTOMIZER}"
}

# compose/customizer.py --adopt: the compose file as a selection of the release, with the report of what
# it does not carry over (stdout of --report, one item per line) in myADOPT_LINES. The type is the one of
# the .env in effect (after a handover the env of the archive, the .env here may be the reset one), the
# host ports are judged against the catalogue of the commit that ran: rollback.txt of the archive, before
# the backup HEAD; without one none. rc of the customizer: 0 adopted (with --dry-run only rendered), 2 not
# readable or not written, 3 conflicts, any other one as well: not adopted; 2 for a customizer that
# cannot adopt.
function fuADOPT_RUN () {   # $1 = compose file, $2 = output, $3 = customizer, $4 = --dry-run (optional)
	local myTYPE="" myCOMMIT="" myCATALOG="" myREPORT="" myLINE="" myRC=0
	local myARGS=()
	myADOPT_LINES=()
	grep -q -- "--adopt" "$3" 2>/dev/null || return 2
	# without PyYAML the customizer would build the venv of the T-Pot Manager first (pypi.org)
	"$(fuCUSTOMIZER_PYTHON)" -c "import yaml" > /dev/null 2>&1 || return 2
	fuTMPDIR
	if [ -n "${myPREPARED}" ] && [ -n "${myARCHIVE}" ];
	  then
	    myTYPE=$(fuARCHIVE_ENV "${myARCHIVE}" TPOT_TYPE)
	  else
	    myTYPE=$(fuENV_VALUE "$HOME/tpotce/.env" TPOT_TYPE)
	fi
	case "${myTYPE}" in
	  HIVE|SENSOR) myARGS+=(--tpot-type "${myTYPE}") ;;
	esac
	if [ -n "${myARCHIVE}" ];
	  then
	    myCOMMIT=$(tar xOf "${myARCHIVE}" rollback.txt 2>/dev/null | tr -d "[:space:]")
	  else
	    myCOMMIT=$(git -C "$HOME/tpotce" rev-parse HEAD 2>/dev/null)
	fi
	myCATALOG="${myTMPDIR}/old_tpot_services.yml"
	if [ -n "${myCOMMIT}" ] \
	   && git -C "$HOME/tpotce" show "${myCOMMIT}:compose/tpot_services.yml" > "${myCATALOG}" 2>/dev/null \
	   && [ -s "${myCATALOG}" ];
	  then
	    myARGS+=(--old-catalog "${myCATALOG}")
	fi
	myREPORT="${myTMPDIR}/adopt_report"
	"$(fuCUSTOMIZER_PYTHON)" "$3" --adopt "$1" -o "$2" --report ${4:+"$4"} ${myARGS[@]+"${myARGS[@]}"} \
	  < /dev/null > "${myREPORT}" || myRC=$?
	while IFS= read -r myLINE || [ -n "${myLINE}" ];
	  do
	    [ -z "${myLINE}" ] || myADOPT_LINES+=("${myLINE}")
	  done < "${myREPORT}"
	return "${myRC}"
}

# What the adaptation loses (myADOPT_LINES), one item per line: every kept-out item, and every service it
# drops for another reason than its removal in the release (a service of your own that is no T-Pot
# service, one a SENSOR has not). A service the release removed is no loss of yours, only named.
function fuADOPT_LOSSES () {
	local myLINE=""
	for myLINE in ${myADOPT_LINES[@]+"${myADOPT_LINES[@]}"};
	  do
	    case "${myLINE}" in
	      "kept-out: "*) echo "${myLINE#kept-out: }" ;;
	      *" (removed in this release)") ;;
	      "dropped: "*) echo "the service ${myLINE#dropped: }" ;;
	    esac
	  done
}

# The command that adapts without a question, for the hints: after a handover the next run starts with
# the update.sh that started this one, which takes the option only after `--` (it stops with its usage on
# the option alone, after `--` every one hands it on). After a restart of this script into a newer one
# myPREPARED is set as well: `--` works there too, the option reaches this script either way.
function fuADAPT_COMMAND () {
	if [ -n "${myPREPARED}" ];
	  then
	    echo "./update.sh -y -- --adapt-compose"
	  else
	    echo "./update.sh -y --adapt-compose"
	fi
}

# Why compose/customizer.py did not adopt: PyYAML missing (no venv of the T-Pot Manager, none of the
# distribution), else its exit code.
function fuADOPT_WHY () {   # $1 = exit code
	if ! "$(fuCUSTOMIZER_PYTHON)" -c "import yaml" > /dev/null 2>&1;
	  then
	    echo "compose/customizer.py needs PyYAML, which is missing here (tpot setup builds the venv of the T-Pot Manager with it, or install the PyYAML package of the distribution)"
	  else
	    echo "compose/customizer.py --adopt ended with $1"
	fi
}

# The report lines of the adaptation, one hint each: what the release removed (dropped), what it keeps of
# yours (port), what the release changes (moved), what is new in it (new), and last what it does not carry
# over (fuADOPT_LOSSES). $1 = before (the question: says so where nothing is lost) | after (the adaptation)
function fuADOPT_REPORT () {
	local myLINE="" myFOUND=""
	for myLINE in ${myADOPT_LINES[@]+"${myADOPT_LINES[@]}"};
	  do
	    case "${myLINE}" in
	      "dropped: "*" (removed in this release)") fuUI_HINT "  dropped: ${myLINE#dropped: }" ;;
	      "port: "*) fuUI_HINT "  your host port, kept: ${myLINE#port: }" ;;
	      "moved: "*) fuUI_HINT "  changed by the release: ${myLINE#moved: }" ;;
	      "new: "*) fuUI_HINT "  new in this release: ${myLINE#new: }" ;;
	    esac
	  done
	while IFS= read -r myLINE;
	  do
	    [ -n "${myLINE}" ] || continue
	    fuUI_HINT "  not carried over: ${myLINE}"
	    myFOUND="1"
	  done < <(fuADOPT_LOSSES)
	if [ -z "${myFOUND}" ] && [ "$1" == "before" ];
	  then
	    fuUI_HINT "  nothing, every setting of your file is carried over."
	fi
}

# The ways to adapt a compose file of your own yourself, for the cancel: an edition, the customizer, or
# your file edited along the diff and the points the adaptation leaves out.
function fuADOPT_WAYS () {
	local myLINE="" myBASE="standard"
	if [ -n "${myPREPARED}" ] && [ -n "${myARCHIVE}" ];
	  then
	    [ "$(fuARCHIVE_ENV "${myARCHIVE}" TPOT_TYPE)" != "SENSOR" ] || myBASE="sensor"
	  else
	    [ "$(fuENV_VALUE "$HOME/tpotce/.env" TPOT_TYPE)" != "SENSOR" ] || myBASE="sensor"
	fi
	fuUI_HINT "Adapt it yourself, then run the update again:"
	fuUI_HINT "  an edition of the release: tpot edition set <edition>, or cp compose/<edition>.yml docker-compose.yml"
	fuUI_HINT "  your selection of services: compose/customizer.py"
	fuUI_HINT "  your own file, edited: diff -u docker-compose.yml compose/${myBASE}.yml"
	while IFS= read -r myLINE;
	  do
	    [ -z "${myLINE}" ] || fuUI_HINT "    and keep: ${myLINE}"
	  done < <(fuADOPT_LOSSES)
	fuUI_HINT "Or answer at a terminal, or run $(fuADAPT_COMMAND) (also without one)."
	fuDID next "Adapt your docker-compose.yml yourself (tpot edition set <edition>, compose/customizer.py, or by hand along diff -u docker-compose.yml compose/${myBASE}.yml), then update again; or run $(fuADAPT_COMMAND)."
}

# Not adapted: the update stops. Before the stop (check) nothing was changed; after the pull (restore)
# the checkout and the configuration go back (fuROLLBACK_CHECKOUT) and T-Pot starts again where
# tpot.service is enabled; where the archive names no earlier commit your file is put back as it was
# and T-Pot stays stopped. Exit 1.
function fuADOPT_CANCEL () {   # $1 = check | restore
	local myRC=0
	trap - INT
	echo
	if [ "$1" == "check" ];
	  then
	    # an updater without a handover has stopped T-Pot and pulled already (fuOLD_UPDATER_CONFIG put your
	    # configuration back from its backup), or T-Pot does not run for another reason: it starts again
	    if [ -n "${myOLD_UPDATER_RAN}" ];
	      then
	        mySTOPPED="1"
	        fuUI_HINT "The update.sh that started this run has pulled the new release already, your configuration and your docker-compose.yml are back from its backup ${myOLD_UPDATER_RAN}."
	        fuDID fail "Not updated: your docker-compose.yml is a compose file of your own and was not adapted. The update.sh that started this run has pulled the new release already, your configuration and your docker-compose.yml are back from its backup ${myOLD_UPDATER_RAN}; T-Pot runs again with your file where tpot.service is enabled. The update finishes once you adapt the file: $(fuADAPT_COMMAND), or adapt it yourself."
	        fuADOPT_WAYS
	        fuRESTART_AFTER_REFUSAL
	        echo
	        exit 1
	    fi
	    if [ "$(systemctl is-active tpot 2>/dev/null)" != "active" ];
	      then
	        mySTOPPED="1"
	        fuUI_HINT "Not updated. T-Pot does not run at the moment (an earlier update.sh may have stopped it), nothing of this run was changed."
	        fuDID fail "Not updated: your docker-compose.yml is a compose file of your own and was not adapted. T-Pot does not run, nothing of this run was changed."
	        fuADOPT_WAYS
	        fuRESTART_AFTER_REFUSAL
	        echo
	        exit 1
	    fi
	    fuUI_HINT "Not updated, nothing was changed."
	    fuADOPT_WAYS
	    echo
	    fuDID fail "Not updated: your docker-compose.yml is a compose file of your own and was not adapted, nothing was changed."
	    exit 1
	fi
	fuUI_HINT "Nothing was pulled and no image was removed."
	if fuCAN_ROLLBACK;
	  then
	    # a Ctrl+C now would leave the checkout half way back: the rollback finishes
	    trap '' INT
	    fuROLLBACK_CHECKOUT || myRC=$?
	    trap - INT
	    if [ "${myRC}" -eq 0 ];
	      then
	        fuDID fail "Not updated: your docker-compose.yml is a compose file of your own and was not adapted, the checkout and the configuration are back."
	        fuRESTART_AFTER_REFUSAL
	      else
	        fuDID fail "Not updated: your docker-compose.yml was not adapted, and the checkout could not be put back: T-Pot stays stopped. restore.sh -f ${myARCHIVE} -c puts it back."
	    fi
	  else
	    fuUI_WARN "The archive holds no earlier commit to go back to, the checkout stays on this release."
	    if fuCOMPOSE_FROM_ARCHIVE && cp "${myTMPDIR}/docker-compose.yml" "$HOME/tpotce/docker-compose.yml";
	      then
	        fuDID fail "Not updated: your docker-compose.yml was not adapted. It is back as it was, on the checkout of this release: T-Pot stays stopped."
	      else
	        fuDID fail "Not updated: your docker-compose.yml was not adapted and could not be put back (tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml): T-Pot stays stopped."
	    fi
	fi
	fuADOPT_WAYS
	if [ -n "${myPREPARED}" ];
	  then
	    fuUI_HINT "Each run of the update.sh that started this run writes a backup to ${myBACKUPDIR} before it gets here, a cron job as well: adapt the file, answer at a terminal or run $(fuADAPT_COMMAND)."
	    fuDID info "Each run of the update.sh that started this run writes a backup to ${myBACKUPDIR} before it gets here, a cron job as well: adapt the file, answer at a terminal or run $(fuADAPT_COMMAND)."
	fi
	fuUI_HINT "Exiting."
	echo
	exit 1
}

# One question with the default cancel, a Ctrl+C there is a no (the trap it found comes back). rc 0 for
# the first answer.
function fuADOPT_ASK () {   # $1 = question, $2 = the answer that goes on, $3 = the one that cancels
	local myPICK="" myTRAP=""
	myTRAP=$(trap -p INT)
	myADOPT_INT=""
	trap 'myADOPT_INT="1"' INT
	myPICK=$(fuUI_CHOOSE --selected cancel "$1" "$2:adapt" "$3:cancel") || myPICK=""
	eval "${myTRAP:-trap - INT}"
	[ "${myPICK}" == "adapt" ] && [ -z "${myADOPT_INT}" ]
}

# Adapt the compose file of your own? --adapt-compose says yes, at a terminal the choice (default:
# cancel), a Ctrl+C there is a no; without a terminal no. -y never chooses. rc 0 for adapt.
function fuADOPT_CHOICE () {
	if [ -n "${myADAPT_COMPOSE}" ];
	  then
	    fuUI_HINT "--adapt-compose: back up your compose file and adapt it to this release; the kept-out list above is not carried over."
	    return 0
	fi
	if [ ! -t 0 ];
	  then
	    fuUI_HINT "Not adapted: no terminal. $(fuADAPT_COMMAND) adapts it, -y does not."
	    return 1
	fi
	fuADOPT_ASK "What should the update do with your compose file?" "Back up and adapt automatically" \
	  "Cancel and adapt it myself" || return 1
	myADAPT_COMPOSE="1"
	fuUI_HINT "Adapting: the backup of the update keeps your file."
}

# The list shown before the choice (fuADOPT_LOSSES), in myTMPDIR: what the yes covered. Kept as it is once
# it is there; a restarted update.sh takes the one of the first pass (TPOT_UPDATE_ADOPT_SHOWN, fuSELFUPDATE).
function fuADOPT_SHOWN_SAVE () {
	[ ! -f "${myTMPDIR}/adopt_shown" ] || return 0
	if [ -n "${myPREPARED}" ] && [ -n "${TPOT_UPDATE_ADOPT_SHOWN+set}" ];
	  then
	    printf '%s\n' "${TPOT_UPDATE_ADOPT_SHOWN}" > "${myTMPDIR}/adopt_shown"
	  else
	    fuADOPT_LOSSES > "${myTMPDIR}/adopt_shown"
	fi
}

# Does the adaptation of the release lose more than the list shown before the choice (the dry run before
# the pull knows only the catalogue that ran)? The new items in myADOPT_NEW. At a terminal they are shown
# and asked about again (default cancel); without one --adapt-compose goes on, the summary names them
# (the backup keeps your file). rc 1 for cancel.
function fuADOPT_GREW () {
	local myLINE=""
	myADOPT_NEW=()
	[ -f "${myTMPDIR}/adopt_shown" ] || return 0
	while IFS= read -r myLINE;
	  do
	    [ -n "${myLINE}" ] || continue
	    grep -qxF -e "${myLINE}" "${myTMPDIR}/adopt_shown" || myADOPT_NEW+=("${myLINE}")
	  done < <(fuADOPT_LOSSES)
	[ "${#myADOPT_NEW[@]}" -gt 0 ] || return 0
	fuUI_WARN "The adaptation to this release leaves out more than the list shown before the choice:"
	for myLINE in "${myADOPT_NEW[@]}";
	  do
	    fuUI_HINT "  not carried over: ${myLINE}"
	  done
	if [ ! -t 0 ];
	  then
	    fuUI_HINT "No terminal: --adapt-compose goes on, the backup keeps your file."
	    return 0
	fi
	fuADOPT_ASK "Adapt your compose file all the same?" "Adapt it, the backup keeps my file" \
	  "Cancel and put the release before back"
}

# The adopted file in place of ~/tpotce/docker-compose.yml, with what it did: the report, the diff against
# your file in the archive, each item it did not carry over in the summary (the ones not in the list shown
# before the choice say so). rc 1 when it could not be copied.
function fuADOPT_WRITE () {   # $1 = v1 | own
	local myLINE="" myITEM="" myNOTE="" myWHAT="Your docker-compose.yml is adapted to this release"
	[ "$1" != "v1" ] || myWHAT="Your docker-compose.yml of the customizer before v2 is rebuilt for this release"
	if ! cp "${myTMPDIR}/adopted.yml" "$HOME/tpotce/docker-compose.yml";
	  then
	    fuUI_ERROR "The adapted docker-compose.yml could not be put in place."
	    return 1
	fi
	fuUI_WARN "${myWHAT} (compose/customizer.py --adopt), the backup keeps your file. What changed:"
	fuADOPT_REPORT after
	fuUI_HINT "Compare it with: tar xOf ${myARCHIVE} docker-compose.yml | diff - $HOME/tpotce/docker-compose.yml"
	fuDID ok "${myWHAT} (compose/customizer.py --adopt), the backup ${myARCHIVE} keeps your file."
	# in the summary as well: it names the archive by its final name (fuARCHIVE_RENAMED)
	fuDID info "Compare it with: tar xOf ${myARCHIVE} docker-compose.yml | diff - $HOME/tpotce/docker-compose.yml"
	while IFS= read -r myLINE;
	  do
	    [ -n "${myLINE}" ] || continue
	    myNOTE=""
	    for myITEM in ${myADOPT_NEW[@]+"${myADOPT_NEW[@]}"};
	      do
	        [ "${myITEM}" != "${myLINE}" ] || myNOTE=" (not in the list shown before)"
	      done
	    fuDID warn "Not carried over from your docker-compose.yml${myNOTE}: ${myLINE}"
	  done < <(fuADOPT_LOSSES)
}

# A compose file that is no edition and not built by compose/customizer.py from v2 on (fuCOMPOSE_KIND):
# - V1, a file of the customizer before v2, is rebuilt for the release (compose/customizer.py --adopt)
#   without a question; one it cannot adopt (exit 2, 3 or another) counts as one of your own;
# - OWN, a compose file of your own, gets none of the changes of a release: it is adapted only after a
#   yes (fuADOPT_CHOICE), with the list of what the adaptation does not carry over before the choice;
#   a longer list after the pull asks again (fuADOPT_GREW). A no or a failed adaptation stops the update
#   (fuADOPT_CANCEL).
# check: fuCHECK_EDITION before the stop, the backup and the pull, on ~/tpotce/docker-compose.yml: it only
# decides. restore: fuRESTORE_EDITION after the pull, on the file of the archive: it writes
# ~/tpotce/docker-compose.yml; the question comes here when it was not asked before the pull (a handover).
function fuADOPT_COMPOSE () {   # $1 = the compose file before the update, $2 = check | restore
	local myKIND="" myCUSTOMIZER="" myRC=0
	fuTMPDIR
	myADOPT_NEW=()
	myKIND=$(fuCOMPOSE_KIND "$1")
	myCUSTOMIZER=$(fuADOPT_CUSTOMIZER "$2")
	if [ "${myKIND}" == "V1" ];
	  then
	    if [ "$2" == "check" ];
	      then
	        fuADOPT_RUN "$1" "${myTMPDIR}/adopted.yml" "${myCUSTOMIZER}" --dry-run || myRC=$?
	        if [ "${myRC}" -eq 0 ];
	          then
	            fuUI_OK "docker-compose.yml was written by the customizer before v2 (# T-Pot: CUSTOM EDITION), the update rebuilds it for this release."
	            return 0
	        fi
	      else
	        fuUI_HINT "Now rebuilding your docker-compose.yml of the customizer before v2 for this release."
	        fuADOPT_RUN "$1" "${myTMPDIR}/adopted.yml" "${myCUSTOMIZER}" || myRC=$?
	        if [ "${myRC}" -eq 0 ] && fuADOPT_WRITE v1;
	          then
	            return 0
	        fi
	    fi
	    fuUI_WARN "Your docker-compose.yml of the customizer before v2 is not rebuilt ($(fuADOPT_WHY "${myRC}")), it counts as a compose file of your own."
	fi
	# a compose file of your own: the question, unless it was answered before the pull
	if [ "$2" == "check" ] || [ -z "${myADOPT_DECIDED}" ];
	  then
	    myRC=0
	    fuADOPT_RUN "$1" "${myTMPDIR}/adopted.yml" "${myCUSTOMIZER}" --dry-run || myRC=$?
	    fuUI_WARN "Your docker-compose.yml is a compose file of your own: the changes of this release do not reach it (mem_limit, volumes, new and removed services), T-Pot may not run properly with it."
	    if [ "${myRC}" -ne 0 ];
	      then
	        fuUI_HINT "It cannot be adapted automatically: $(fuADOPT_WHY "${myRC}"), see above."
	        fuADOPT_CANCEL "$2"
	    fi
	    fuUI_HINT "Back up and adapt automatically: your file goes into the backup of the update, compose/customizer.py builds it anew from your services for this release. This may lose changes of yours, the list below is not carried over."
	    fuADOPT_REPORT before
	    fuADOPT_SHOWN_SAVE
	    fuADOPT_CHOICE || fuADOPT_CANCEL "$2"
	    myADOPT_DECIDED="1"
	    [ "$2" != "check" ] || return 0
	fi
	myRC=0
	fuUI_HINT "Now adapting your docker-compose.yml to this release."
	fuADOPT_RUN "$1" "${myTMPDIR}/adopted.yml" "${myCUSTOMIZER}" || myRC=$?
	if [ "${myRC}" -ne 0 ];
	  then
	    fuUI_ERROR "Your docker-compose.yml could not be adapted ($(fuADOPT_WHY "${myRC}")), see above."
	    fuADOPT_CANCEL restore
	fi
	fuADOPT_GREW || fuADOPT_CANCEL restore
	fuADOPT_WRITE own || fuADOPT_CANCEL restore
	return 0
}

# myEDITION is UNKNOWN in fuRESTORE_EDITION: the compose file of the archive decides. After a handover
# without an edition (TPOT_UPDATE_EDITION empty) the env of the archive tells first: a TPOT_DOCKER_COMPOSE
# of another file leaves the one here as it is, an archive without a docker-compose.yml gets the edition
# of its TPOT_TYPE. rc 0 when it is done (adapted, rebuilt, left, or not readable), rc 1 when it found
# the edition (myEDITION) and fuRESTORE_EDITION goes on with it.
function fuRESTORE_UNKNOWN () {
	local myCOMPOSE="" myTYPE="" myKIND="" myCOMMIT="" myTEMPLATE=""
	fuTMPDIR
	if [ -n "${myPREPARED}" ];
	  then
	    myCOMPOSE=$(fuARCHIVE_ENV "${myARCHIVE}" TPOT_DOCKER_COMPOSE)
	    if [ -n "${myCOMPOSE}" ] && [ "$(basename "${myCOMPOSE}")" != "docker-compose.yml" ];
	      then
	        fuUI_OK "Your compose file is ${myCOMPOSE} (TPOT_DOCKER_COMPOSE), it is not touched."
	        fuDID info "Your compose file is ${myCOMPOSE} (TPOT_DOCKER_COMPOSE), it is not touched."
	        myEDITION=""
	        return 0
	    fi
	    if fuOLD_UPDATER_READ "${myARCHIVE}" && ! fuARCHIVE_MEMBER "${myARCHIVE}" docker-compose.yml > /dev/null;
	      then
	        myTYPE=$(fuARCHIVE_ENV "${myARCHIVE}" TPOT_TYPE)
	        myEDITION="STANDARD"
	        [ "${myTYPE}" != "SENSOR" ] || myEDITION="SENSOR"
	        fuUI_WARN "There was no docker-compose.yml before the update, the ${myEDITION} edition of this release is put in place (TPOT_TYPE ${myTYPE:-HIVE})."
	        fuDID warn "There was no docker-compose.yml before the update, the ${myEDITION} edition of this release is put in place (TPOT_TYPE ${myTYPE:-HIVE})."
	        return 1
	    fi
	fi
	if ! fuCOMPOSE_FROM_ARCHIVE;
	  then
	    fuUI_ERROR "Your own docker-compose.yml could not be taken from ${myARCHIVE}. Put it back by hand with:"
	    fuUI_HINT "  tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
	    fuDID warn "Your own docker-compose.yml could not be put back: tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
	    myEDITION_MISSING="1"
	    return 0
	fi
	myKIND=$(fuCOMPOSE_KIND "${myTMPDIR}/docker-compose.yml")
	case "${myKIND}" in
	  V1|OWN)
	    fuADOPT_COMPOSE "${myTMPDIR}/docker-compose.yml" restore
	    return 0 ;;
	  CUSTOM)
	    myEDITION="CUSTOM"
	    fuCUSTOM_CHECKSUM "${myTMPDIR}/docker-compose.yml" || myCOMPOSE_CUSTOMIZED="1"
	    ;;
	  *)
	    # an edition the handover did not name (the update.sh before reads the first line only): yours
	    # unless it is the template of the commit that ran
	    myEDITION="${myKIND}"
	    myCOMMIT=$(tar xOf "${myARCHIVE}" rollback.txt 2>/dev/null | tr -d "[:space:]")
	    myTEMPLATE="${myTMPDIR}/old_template.yml"
	    if [ -z "${myCOMMIT}" ] \
	       || ! git -C "$HOME/tpotce" show "${myCOMMIT}:compose/$(echo "${myEDITION}" | tr '[:upper:]' '[:lower:]').yml" > "${myTEMPLATE}" 2>/dev/null \
	       || ! cmp -s "${myTEMPLATE}" "${myTMPDIR}/docker-compose.yml";
	      then
	        myCOMPOSE_CUSTOMIZED="1"
	    fi
	    ;;
	esac
	fuUI_OK "Edition ${myEDITION}${myCOMPOSE_CUSTOMIZED:+ (customized)}, from the docker-compose.yml of the backup."
	return 1
}

# A compose file of your own, for --check-data-loss: the update adapts it only after a yes
# (--adapt-compose without a terminal), and the adaptation may lose what fuADOPT_LOSSES names (a dry
# --adopt, as the run will show it before the choice). A file of the customizer before v2 is rebuilt
# without a question, it is no finding.
function fuCOMPOSE_LOSS () {
	local myCOMPOSE="" myKIND="" myLINE="" myITEMS="" myRC=0
	local myFILE="$HOME/tpotce/docker-compose.yml"
	myCOMPOSE=$(grep -E "^TPOT_DOCKER_COMPOSE=" "$HOME/tpotce/.env" 2>/dev/null | tail -1 | cut -d "=" -f2-)
	[ "$(basename "${myCOMPOSE:-./docker-compose.yml}")" == "docker-compose.yml" ] && [ -f "${myFILE}" ] || return 0
	myKIND=$(fuCOMPOSE_KIND "${myFILE}")
	fuTMPDIR
	if [ "${myKIND}" == "V1" ];
	  then
	    if fuADOPT_RUN "${myFILE}" "${myTMPDIR}/adopted.yml" "$(fuADOPT_CUSTOMIZER check)" --dry-run;
	      then
	        fuUI_OK "docker-compose.yml was written by the customizer before v2, the update rebuilds it for this release."
	        return 0
	    fi
	    myKIND="OWN"
	fi
	[ "${myKIND}" == "OWN" ] || return 0
	fuADOPT_RUN "${myFILE}" "${myTMPDIR}/adopted.yml" "$(fuADOPT_CUSTOMIZER check)" --dry-run || myRC=$?
	if [ "${myRC}" -ne 0 ];
	  then
	    fuLOSS_ADD compose "Your docker-compose.yml is a compose file of your own and cannot be adapted to the release automatically ($(fuADOPT_WHY "${myRC}")): the update stops before it changes anything."
	    return 0
	fi
	while IFS= read -r myLINE;
	  do
	    [ -z "${myLINE}" ] || myITEMS="${myITEMS:+${myITEMS}; }${myLINE}"
	  done < <(fuADOPT_LOSSES)
	if [ -n "${myITEMS}" ];
	  then
	    fuLOSS_ADD compose "Your docker-compose.yml is a compose file of your own: the update adapts it to the release only after a yes (--adapt-compose), the backup keeps your file. Not carried over: ${myITEMS}."
	  else
	    fuLOSS_ADD compose "Your docker-compose.yml is a compose file of your own: the update adapts it to the release only after a yes (--adapt-compose), the backup keeps your file. Every setting of it is carried over."
	fi
}

# Put the edition back that fuCHECK_EDITION found, using this release's template so
# that the update brings its new service definitions along. Has to run before the
# images are pulled, as every edition needs a different set of them.
function fuRESTORE_EDITION () {
	local myTEMPLATE=""
	echo
	fuUI_INFO "Restoring the T-Pot edition ..."
	if [ -z "${myEDITION}" ];
	  then
	    fuUI_HINT "No edition was detected, nothing to restore."
	    echo
	    return
	fi
	# No edition: the compose file before the update decides, the one of the archive (the one here is
	# the reset one of the release by now). Not on an older checkout, which puts your file back as it is.
	if [ "${myEDITION}" == "UNKNOWN" ] && [ -z "${myOLDER_CHECKOUT}" ];
	  then
	    if fuRESTORE_UNKNOWN;
	      then
	        echo
	        return
	    fi
	fi
	if [ "${myEDITION}" == "CUSTOM" ];
	  then
	    fuRESTORE_CUSTOM
	    echo
	    return
	fi
	# An update.sh from before this function existed reset docker-compose.yml to
	# the STANDARD edition without remembering anything, in which case TPOT_TYPE in
	# the restored .env is the only hint that is left.
	if [ "${myEDITION}" == "STANDARD" ];
	  then
	    if grep -qE "^TPOT_TYPE=SENSOR" "$HOME/tpotce/.env" 2>/dev/null;
	      then
	        fuUI_WARN "TPOT_TYPE is SENSOR, restoring the SENSOR edition instead of ${myEDITION}."
	        myEDITION="SENSOR"
	    fi
	fi
	[ "${myEDITION}" != "UNKNOWN" ] && myTEMPLATE=$(fuEDITION_TEMPLATE)
	if [ -n "${myTEMPLATE}" ] && [ -f "${myTEMPLATE}" ];
	  then
	    if ! cp "${myTEMPLATE}" "$HOME/tpotce/docker-compose.yml";
	      then
	        fuUI_ERROR "The ${myEDITION} edition could not be restored, please copy ${myTEMPLATE} to ~/tpotce/docker-compose.yml manually."
	        fuDID warn "The ${myEDITION} edition could not be put in place, copy ${myTEMPLATE} to ~/tpotce/docker-compose.yml."
	        myEDITION_MISSING="1"
	        echo
	        return
	    fi
	    fuUI_OK "The ${myEDITION} edition is restored."
	    if [ -n "${myCOMPOSE_CUSTOMIZED}" ];
	      then
	        fuUI_WARN "Your docker-compose.yml had been modified. The ${myEDITION} edition of this release is in place now, please add your changes again."
	        fuDID warn "Your docker-compose.yml had been modified, the ${myEDITION} edition of this release replaces it: add your changes again (the backup has your file)."
	        fuUI_HINT "Your previous file is in the backup, compare it with:"
	        fuUI_HINT "  tar xOf ${myARCHIVE} docker-compose.yml | diff - $HOME/tpotce/docker-compose.yml"
	    fi
	  else
	    # Without a template the file the user had is put back unchanged, which
	    # keeps it on the service definitions of the previous release.
	    if ! fuCOMPOSE_FROM_ARCHIVE \
	       || ! cp "${myTMPDIR}/docker-compose.yml" "$HOME/tpotce/docker-compose.yml";
	      then
	        fuUI_ERROR "Your own docker-compose.yml could not be taken from ${myARCHIVE}. Put it back by hand with:"
	        fuUI_HINT "  tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
	        fuDID warn "Your own docker-compose.yml could not be put back: tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
	        echo
	        return
	    fi
	    fuUI_OK "Your own docker-compose.yml is restored from the backup."
	    fuUI_WARN "Your docker-compose.yml does not match any edition in compose/, so the changes of this release were not applied to it."
	    fuDID warn "Your docker-compose.yml matches no edition, the changes of this release are not in it."
	fi
	echo
}

# The tpot command and compose/customizer.py need PyYAML and the venv module (for
# the venv of tpot). The installer brings both along, older installations get them
# here: Debian and Ubuntu split the venv module off into python3-venv, the other
# distributions ship it with python3.
function fuCUSTOMIZER_DEPS () {
	local myPKGS=() myINSTALL=() myVENV=""
	python3 -c "import ensurepip" 2>/dev/null || myVENV="1"
	if command -v apt-get >/dev/null 2>&1;
	  then
	    python3 -c "import yaml" 2>/dev/null || myPKGS+=(python3-yaml)
	    [ -n "${myVENV}" ] && myPKGS+=(python3-venv)
	    myINSTALL=(env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq)
	elif command -v dnf >/dev/null 2>&1;
	  then
	    python3 -c "import yaml" 2>/dev/null || myPKGS+=(python3-pyyaml)
	    myINSTALL=(dnf -y -q install)
	elif command -v zypper >/dev/null 2>&1;
	  then
	    python3 -c "import yaml" 2>/dev/null || myPKGS+=(python3-PyYAML)
	    myINSTALL=(zypper -n -q install)
	fi
	[ ${#myPKGS[@]} -eq 0 ] && return
	fuUI_INFO "Installing Python packages for tpot and compose/customizer.py ..."
	[ "${myINSTALL[0]}" == "env" ] && sudo apt-get update -qq >/dev/null 2>&1
	if sudo "${myINSTALL[@]}" "${myPKGS[@]}" >/dev/null 2>&1;
	  then
	    fuUI_OK "${myPKGS[*]} installed."
	  else
	    fuUI_WARN "Could not install ${myPKGS[*]}, tpot and the customizer may not start."
	    fuDID warn "${myPKGS[*]} could not be installed, tpot and the customizer may not start."
	fi
	echo
}

# The tpot command: refresh its venv (the pinned packages may have changed with the
# release) and link it for installations that predate it. Only warns, T-Pot itself
# does not need it, and pypi.org is not part of the internet check above.
function fuTPOT_SETUP () {
	local myTPOT="$HOME/tpotce/tpot"
	local myLINK="${myTPOT_LINK:-/usr/local/bin/tpot}"
	[ -x "${myTPOT}" ] || return
	fuUI_INFO "Setting up the T-Pot Manager (the tpot command) ..."
	if [ ! -e "${myLINK}" ] || [ -L "${myLINK}" ];
	  then
	    if ! sudo ln -sfn "${myTPOT}" "${myLINK}";
	      then
	        fuUI_WARN "Could not link ${myLINK}, run ${myTPOT} directly."
	        fuDID warn "${myLINK} could not be linked, run ${myTPOT} directly."
	    fi
	  else
	    fuUI_WARN "${myLINK} is a file of its own, update.sh leaves it alone: run ${myTPOT} directly."
	    fuDID warn "${myLINK} is a file of its own, update.sh leaves it alone: run ${myTPOT} directly."
	fi
	# the venv of the Manager: pip writes a lot, it goes to the log under a spinner
	local myRC=0
	fuUI_SPIN "Installing the Python packages of the T-Pot Manager ..." "${myLOG}" "${myTPOT}" setup || myRC=$?
	if [ "${myRC}" -eq 130 ];
	  then
	    fuDID warn "Stopped while setting up the T-Pot Manager, before the image pull: run update.sh -y again to finish."
	    exit 130
	fi
	if [ "${myRC}" -eq 0 ];
	  then
	    fuUI_OK "The T-Pot Manager is ready."
	    fuDID ok "The T-Pot Manager is ready (tpot)."
	  else
	    fuUI_WARN "The T-Pot Manager could not set up its Python packages, it tries again on its next start."
	    fuDID warn "The T-Pot Manager could not set up its Python packages, it tries again on its next start."
	fi
	echo
}

# A docker-compose.yml from compose/customizer.py names its edition and the changes
# made to it in its header, so it is built again from this release's catalog and
# editions. Edited by hand, without a customizer that can rebuild (an older
# checkout) or without PyYAML, the file is put back unchanged.
function fuRESTORE_CUSTOM () {
	local myCUSTOMIZER="$HOME/tpotce/compose/customizer.py" myLINE=""
	if ! fuCOMPOSE_FROM_ARCHIVE;
	  then
	    fuUI_ERROR "Could not take docker-compose.yml from ${myARCHIVE}. Put it back by hand with:"
	    fuUI_HINT "  tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
	    fuDID warn "Your docker-compose.yml could not be put back: tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
	    myEDITION_MISSING="1"
	    return
	fi
	if [ -n "${myCOMPOSE_CUSTOMIZED}" ];
	  then
	    fuUI_WARN "Your docker-compose.yml was edited after the customizer built it, so it is not rebuilt."
	elif ! grep -q -- "--rebuild" "${myCUSTOMIZER}" 2>/dev/null;
	  then
	    fuUI_WARN "The customizer of this checkout cannot rebuild your docker-compose.yml."
	else
	    fuUI_HINT "Now rebuilding your custom docker-compose.yml from this release."
	    if python3 "${myCUSTOMIZER}" --rebuild "${myTMPDIR}/docker-compose.yml" -o "$HOME/tpotce/docker-compose.yml" </dev/null;
	      then
	        # the rebuild writes the file anew from its header: lines of your own above it are not in it
	        # (blank lines there are none of your own)
	        myLINE=$(fuEDITION_LINE "${myTMPDIR}/docker-compose.yml" | cut -d " " -f1)
	        if [ -n "${myLINE}" ] && [ "${myLINE}" -gt 1 ] \
	           && head -n "$((myLINE - 1))" "${myTMPDIR}/docker-compose.yml" | grep -q "[^[:space:]]";
	          then
	            fuUI_WARN "The lines above '# T-Pot: CUSTOM' in your docker-compose.yml are not in the rebuilt one, compare it with:"
	            fuUI_HINT "  tar xOf ${myARCHIVE} docker-compose.yml | diff - $HOME/tpotce/docker-compose.yml"
	            fuDID warn "The lines above '# T-Pot: CUSTOM' in your docker-compose.yml are not in the rebuilt one, the backup has them."
	        fi
	        return
	    fi
	    fuUI_WARN "The rebuild failed, see above."
	fi
	if ! cp "${myTMPDIR}/docker-compose.yml" "$HOME/tpotce/docker-compose.yml";
	  then
	    fuUI_ERROR "Your docker-compose.yml could not be restored, put it back by hand with: tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
	    fuDID warn "Your docker-compose.yml could not be put back: tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
	    myEDITION_MISSING="1"
	    return
	fi
	fuUI_OK "Your docker-compose.yml is restored from the backup."
	fuUI_WARN "The changes of this release were not applied to it. Run the customizer again to build it anew:"
	fuUI_HINT "  cd $HOME/tpotce/compose && python3 customizer.py"
	fuDID warn "Your custom docker-compose.yml is put back as it was, the changes of this release are not in it: run compose/customizer.py again."
}

# A docker-compose.yml restored from the backup can still hold services of the
# previous release that are gone now. Their block goes, along with the comment
# lines right above it; the templates never have them, so this is a no-op there.
function fuREMOVE_DROPPED_SERVICES () {
	local mySERVICE="" myFOUND=""
	for mySERVICE in ${myDROPPED_SERVICES};
	  do
	    fuCOMPOSE_HAS "${mySERVICE}" || continue
	    if [ -z "${myFOUND}" ];
	      then
	        myFOUND="1"
	        fuUI_INFO "Removing services that are no longer part of T-Pot ..."
	    fi
	    fuTMPDIR
	    if ! awk -v svc="${mySERVICE}" '
	         /^[^ #]/ { insvc = ($0 ~ /^services:/) }
	         skip && (/^[^ #]/ || /^  [^ #]/) { skip = 0; printf "%s", pend; pend = "" }
	         skip && (/^#/ || /^[[:space:]]*$/) { pend = pend $0 "\n"; next }
	         skip { pend = ""; next }
	         insvc && $0 ~ ("^  " svc ":[[:space:]]*$") {
	           b = 0; for (i = 1; i <= n; i++) if (hl[i] ~ /^[[:space:]]*$/) b = i
	           for (i = 1; i < b; i++) print hl[i]
	           n = 0; skip = 1; next
	         }
	         /^#/ || /^[[:space:]]*$/ { hl[++n] = $0; next }
	         { for (i = 1; i <= n; i++) print hl[i]; n = 0; print }
	         END { if (!skip) for (i = 1; i <= n; i++) print hl[i] }
	       ' "$HOME/tpotce/docker-compose.yml" > "${myTMPDIR}/docker-compose.yml" \
	       || ! cp "${myTMPDIR}/docker-compose.yml" "$HOME/tpotce/docker-compose.yml";
	      then
	        fuUI_ERROR "Please remove the ${mySERVICE} service from ~/tpotce/docker-compose.yml manually, T-Pot will not start with it."
	        fuDID warn "${mySERVICE} could not be removed from docker-compose.yml, T-Pot will not start with it: remove it by hand."
	        continue
	    fi
	    fuUI_OK "${mySERVICE} is removed from docker-compose.yml, it is no longer part of T-Pot."
	    fuUI_WARN "Your previous file is in the backup: tar xOf ${myARCHIVE} docker-compose.yml"
	done
	[ -n "${myFOUND}" ] && echo
}

# The sshd drop-in of an earlier T-Pot takes COLORTERM only, the playbook of this release (which
# update.sh does not run) adds the LC_TERMINAL of iTerm2. Only the very file an earlier T-Pot wrote
# is changed: sshd -t checks it (with its privilege separation directory, as the playbook), a failed
# check puts the old line back. Any other file there is yours and stays as it is.
# AlmaLinux, Fedora, RHEL and Rocky keep the folder at 0700 root: there only sudo sees the file.
function fuSSHD_DROPIN () {
	local myDROPIN="${myTPOT_SSHD_DROPIN:-/etc/ssh/sshd_config.d/tpot.conf}"
	local myRUN="${myTPOT_SSHD_RUN:-/run/sshd}"
	local myOLD="AcceptEnv COLORTERM"
	local myNEW="AcceptEnv COLORTERM LC_TERMINAL LC_TERMINAL_VERSION"
	local mySSHD="" myOUT="" myUNIT="" myTEXT="" myDIR="${myDROPIN%/*}"
	local myAS=""
	[ -d "${myDIR}" ] || return 0
	if [ ! -x "${myDIR}" ];
	  then
	    myAS="sudo"
	    if ! sudo test -d "${myDIR}" 2>/dev/null;
	      then
	        fuUI_WARN "Could not look into ${myDIR}, not even with sudo."
	        fuDID warn "SSH may still not take LC_TERMINAL along: ${myDIR} could not be read, not even with sudo (see README, True colours over SSH)."
	        echo
	        return 0
	    fi
	fi
	{ ${myAS} test -f "${myDROPIN}" && ! ${myAS} test -L "${myDROPIN}"; } || return 0
	# the whole file with its line end: exactly what the playbook of an earlier T-Pot wrote
	myTEXT="$(${myAS} cat "${myDROPIN}" 2>/dev/null; echo x)"
	[ "${myTEXT}" == "${myOLD}"$'\n'"x" ] || return 0
	mySSHD=$(PATH="${PATH}:/usr/sbin:/sbin" command -v sshd) || return 0
	fuUI_INFO "Letting SSH bring LC_TERMINAL along (iTerm2) in ${myDROPIN} ..."
	if ! printf '%s\n' "${myNEW}" | sudo tee "${myDROPIN}" > /dev/null;
	  then
	    fuUI_WARN "Could not write ${myDROPIN}, it stays as it was."
	    fuDID warn "SSH does not take LC_TERMINAL along, ${myDROPIN} could not be written."
	    echo
	    return 0
	fi
	[ -d "${myRUN}" ] || sudo install -d -m 0755 "${myRUN}"
	if ! myOUT=$(sudo "${mySSHD}" -t 2>&1);
	  then
	    printf '%s\n' "${myOLD}" | sudo tee "${myDROPIN}" > /dev/null
	    fuUI_WARN "sshd -t did not accept it, ${myDROPIN} is back as it was:"
	    fuUI_HINT "${myOUT}"
	    fuDID warn "SSH does not take LC_TERMINAL along, sshd -t did not accept it (see above)."
	    echo
	    return 0
	fi
	# the unit the playbook restarts: ssh on Debian, Raspbian and Ubuntu, sshd elsewhere
	for myUNIT in ssh sshd;
	  do
	    if systemctl is-active --quiet "${myUNIT}";
	      then
	        sudo systemctl reload "${myUNIT}" || fuUI_WARN "Could not reload ${myUNIT}, its next start takes it."
	        break
	    fi
	done
	fuUI_OK "SSH takes LC_TERMINAL along from the next login on."
	fuDID ok "SSH takes LC_TERMINAL along (iTerm2 shows true colours from the next login on)."
	echo
}

# The commit the update would go to, fetched without touching the checkout: the branch of -r /
# TPOT_REPO_URL from that repository, the one of -b / TPOT_BRANCH from origin, otherwise the
# upstream of the branch in use, the one `git pull` follows. Prints the ref, rc 1 if there is none.
function fuLOSS_TARGET () {
	local myBRANCH="${myTPOT_BRANCH}" myUP=""
	local myGIT=(git -C "$HOME/tpotce")
	[ -n "${myBRANCH}" ] || myBRANCH=$("${myGIT[@]}" rev-parse --abbrev-ref HEAD 2>/dev/null)
	if [ -z "${myBRANCH}" ] || [ "${myBRANCH}" == "HEAD" ];
	  then
	    fuUI_ERROR "The checkout is not on a branch, so there is nothing to update from. '-b master' names one."
	    return 1
	fi
	# no password prompt without a terminal: a repository that wants one is an error here
	if [ -n "${myTPOT_REPO_URL}" ];
	  then
	    if ! GIT_TERMINAL_PROMPT=0 "${myGIT[@]}" fetch --quiet "${myTPOT_REPO_URL}" "${myBRANCH}" < /dev/null > /dev/null 2>&1;
	      then
	        fuUI_ERROR "Could not fetch ${myBRANCH} from ${myTPOT_REPO_URL}."
	        return 1
	    fi
	    echo "FETCH_HEAD"
	    return 0
	fi
	if [ -n "${myTPOT_BRANCH}" ];
	  then
	    if ! GIT_TERMINAL_PROMPT=0 "${myGIT[@]}" fetch --quiet origin < /dev/null > /dev/null 2>&1 \
	       || ! "${myGIT[@]}" rev-parse --verify --quiet "refs/remotes/origin/${myBRANCH}^{commit}" > /dev/null;
	      then
	        fuUI_ERROR "Could not fetch the branch ${myBRANCH} from origin."
	        return 1
	    fi
	    echo "origin/${myBRANCH}"
	    return 0
	fi
	if ! myUP=$("${myGIT[@]}" rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>/dev/null) \
	   || ! GIT_TERMINAL_PROMPT=0 "${myGIT[@]}" fetch --quiet < /dev/null > /dev/null 2>&1;
	  then
	    fuUI_ERROR "Could not fetch the branch ${myBRANCH} follows."
	    return 1
	fi
	echo "${myUP}"
}

# --check-data-loss: what the update (or the backup of --backup-only) would lose for good, one
# fuLOSS_ADD line each, read the way the run itself will see it. Changes nothing, a git fetch at
# most. rc 0 for none, 3 for some, 1 when it cannot tell.
function fuCHECK_DATA_LOSS () {
	local myREF="" myNEW="" myOLD="" myREPO="" myNEWREPO="" myCURRENT="" myKEEP="" myIMAGE="" myDATA=""
	local myLOCAL=() myPULLABLE=() myBUILT=() myUNSURE=()
	local myGIT=(git -C "$HOME/tpotce")
	myLOSSES=0
	fuCHECK_BACKUP_SPACE
	if [ -z "${myBACKUP_ONLY}" ];
	  then
	    fuUI_INFO "Checking what the update brings ..."
	    myREF=$(fuLOSS_TARGET) || return 1
	    fuUI_HINT "The update goes to $("${myGIT[@]}" rev-parse --short "${myREF}" 2>/dev/null) (${myREF})."
	    # a compose file of your own, as fuCHECK_EDITION will see it before the stop
	    fuCOMPOSE_LOSS
	    myCURRENT=$(grep -E "^TPOT_VERSION=" "$HOME/tpotce/.env" 2>/dev/null | tail -1 | cut -d= -f2-)
	    myREPO=$(grep -E "^TPOT_REPO=" "$HOME/tpotce/.env" 2>/dev/null | tail -1 | cut -d= -f2-)
	    # as fuRESTORE: the old default dtagdevsec moves to the registry of env.example
	    myNEWREPO=$("${myGIT[@]}" show "${myREF}:env.example" 2>/dev/null | sed -n "s/^TPOT_REPO=//p" | tail -1)
	    if [ "${myREPO}" == "dtagdevsec" ] && [ -n "${myNEWREPO}" ] && [ "${myNEWREPO}" != "dtagdevsec" ];
	      then
	        myREPO="${myNEWREPO}"
	    fi
	    # Elasticsearch, as fuCHECK_ELASTIC will see it. Never with the archive of the update.sh of the
	    # tag 24.04.1 (fuOLD_RELEASE_BACKUP): this check runs for a T-Pot that is past that release, the
	    # data in such an archive belongs to an update before this one
	    if fuCOMPOSE_HAS elasticsearch;
	      then
	        myNEW=$("${myGIT[@]}" show "${myREF}:docker/elk/elasticsearch/Dockerfile" 2>/dev/null | sed -n "s/^ARG ES_VER=//p" | head -1)
	        myOLD=$(fuOLD_ES_VERSION "${myREPO}" "${myCURRENT}")
	        myDATA=$(fuDATA_PATH)
	        if [ -z "${myNEW}" ];
	          then
	            fuUI_WARN "Cannot tell which Elasticsearch version the update ships, the update skips its check as well."
	          elif [ "${myOLD}" == "${myNEW}" ];
	            then
	              fuUI_OK "Elasticsearch stays on ${myNEW}."
	          # the step through 8.19 changes the data for good with any backup: it always asks; without
	          # room for the data in the backup the run refuses it then
	          elif fuES_NEEDS_STEP "${myOLD}" "${myNEW}";
	            then
	              if [ -z "${myFULL}" ] && ! fuES_DATA_FITS "${myDATA}" "$(fuBACKUP_SIZE)";
	                then
	                  fuUI_HINT "The backup of the run cannot take the Elasticsearch data, the run refuses the step then: ${myES_FITS_WHY}"
	              fi
	              fuLOSS_ADD elastic-step "$(fuES_STEP_TEXT "${myOLD}" "${myNEW}" "${myDATA}")"
	          elif [ -n "${myFULL}" ] && [ -e "${myDATA}/elk/data" ];
	            then
	              fuUI_OK "Elasticsearch ${myOLD:-?} -> ${myNEW}, --full takes its data into the backup."
	          # the run adds the data to its backup by itself where there is room (fuAPPEND_ES_DATA),
	          # after the regular backup it writes first
	          elif fuES_DATA_FITS "${myDATA}" "$(fuBACKUP_SIZE)";
	            then
	              fuUI_OK "Elasticsearch ${myOLD:-?} -> ${myNEW}, the run adds its data to the backup (about $(( myES_NEED / 1048576 )) MB, $(( myES_FREE / 1048576 )) MB free)."
	          else
	            fuUI_HINT "The backup of the run cannot take the Elasticsearch data: ${myES_FITS_WHY}"
	            fuLOSS_ADD elastic "Elasticsearch ${myOLD:-?} -> ${myNEW} upgrades the data in ${myDATA}/elk on the next start. This cannot be undone and the backup of this run does not hold it."
	        fi
	    fi
	    # The images of every other tag go after the pull, as fuREMOVEOLDIMAGES will see them: the
	    # tag of env.example, unless .env pins one that is no release (as fuRESTORE)
	    myKEEP=$("${myGIT[@]}" show "${myREF}:env.example" 2>/dev/null | sed -n "s/^TPOT_VERSION=//p" | tail -1)
	    [ -n "${myKEEP}" ] || myKEEP=$("${myGIT[@]}" show "${myREF}:version" 2>/dev/null | tr -d "[:space:]")
	    echo "${myCURRENT}" | grep -qE "^[0-9]+(\.[0-9]+)+$" || myKEEP="${myCURRENT}"
	    if [ -z "${myKEEP}" ];
	      then
	        fuUI_WARN "Cannot tell which image tag the update keeps, the update removes no image then."
	      else
	        for myNEWREPO in ${myREPOS};
	          do
	            for myIMAGE in $(fuIMAGES "${myNEWREPO}" "${myKEEP}");
	              do
	                myLOCAL+=("${myIMAGE}")
	              done;
	          done;
	        # the same deadline as the run
	        fuSORT_IMAGES ${myLOCAL[@]+"${myLOCAL[@]}"}
	        [ "${#myBUILT[@]}" -eq 0 ] || fuLOSS_ADD images "$(fuLOCAL_IMAGES_TEXT "${myBUILT[@]}")"
	        [ "${#myUNSURE[@]}" -eq 0 ] || fuLOSS_ADD images "$(fuUNSURE_IMAGES_TEXT "${myUNSURE[@]}")"
	    fi
	    echo
	fi
	if [ "${myLOSSES}" -gt 0 ];
	  then
	    fuUI_WARN "${myLOSSES} loss(es) of data found. The run asks before each one at a terminal, without one only --accept-data-loss confirms them, -y does not."
	    return 3
	fi
	fuUI_OK "No loss of data found."
	return 0
}

# Sourced (the tests do, for the functions): no run
[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0

################
# Main section #
################

# getopts has no long options, so `--full` is translated before they are read
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
      --full)  myARGV+=("-F") ;;
      --start) myARGV+=("-s") ;;
      --backup-only) myARGV+=("-o") ;;
      --check-data-loss) myCHECK_LOSS=1 ;;
      --accept-data-loss) myACCEPT_LOSS=1 ;;
      --accept-data-loss-file) myACCEPT_FILE_NEXT=1 ;;
      --adapt-compose) myADAPT_COMPOSE=1 ;;
      --help)  myARGV+=("-h") ;;
      --?*)    fuUI_USAGE_ERROR "Unknown option ${myARG}." update.sh; exit 1 ;;
      *)      myARGV+=("${myARG}") ;;
    esac
done
if [ -n "${myACCEPT_FILE_NEXT}" ];
  then
    fuUI_USAGE_ERROR "Option --accept-data-loss-file requires a file." update.sh
    exit 1
fi
set -- "${myARGV[@]}"

while getopts ":yFsob:r:B:h" opt; do
  case "$opt" in
    y)
      myCONFIRMED="y"
      ;;
    o)
      myBACKUP_ONLY="1"
      ;;
    F)
      myFULL="1"
      ;;
    s)
      mySTART="1"
      ;;
    b)
      myTPOT_BRANCH="${OPTARG}"
      ;;
    r)
      myTPOT_REPO_URL="${OPTARG}"
      ;;
    B)
      myBECOME_FILE="${OPTARG}"
      ;;
    h)
      fuPRINT_HELP
      ;;
    :)
      fuUI_USAGE_ERROR "Option -${OPTARG} requires an argument." update.sh
      exit 1
      ;;
    \?)
      fuUI_USAGE_ERROR "Unknown option -${OPTARG}." update.sh
      exit 1
      ;;
  esac
done

# TPOT_ES_STEP_WAIT: whole seconds, 60 to 86400 (the step through Elasticsearch 8.19 computes with
# it); anything else stops here, before anything changes, as a wrong option does. After a handover
# T-Pot is stopped already: 1200 s then, with a warning
fuCHECK_STEP_WAIT || exit 1

# A T-Pot host is Linux (WSL2 too): elsewhere nothing of this works, it stops before any
# sudo, docker or git, with the way of the mac_win edition (as the T-Pot Manager says)
( fuUI_LINUX_ONLY update.sh ) || {
  fuUI_HINT "On macOS and Windows (the mac_win edition): git pull in ~/tpotce, then tpot customize." >&2
  exit 1
}

# -b, -r, TPOT_BRANCH and TPOT_REPO_URL all name an update source explicitly
[ -n "${myTPOT_BRANCH}${myTPOT_REPO_URL}" ] && myTPOT_SOURCE_GIVEN="1"

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

# --accept-data-loss-file: the confirmed losses, read where fuLOSS_CONFIRM asks; the restarted
# script gets the same file (fuRESTART_ARGS)
if [ -n "${myACCEPT_FILE_GIVEN}" ];
  then
    if [ -z "${myACCEPT_FILE}" ] || [ ! -f "${myACCEPT_FILE}" ] || [ ! -r "${myACCEPT_FILE}" ];
      then
        fuUI_ERROR "Cannot read the confirmed losses of data from ${myACCEPT_FILE}."
        exit 1
    fi
    myACCEPT_FILE=$(cd "$(dirname "${myACCEPT_FILE}")" && pwd)/$(basename "${myACCEPT_FILE}")
fi
myLOSS_ARGS=()
[ -z "${myACCEPT_LOSS}" ] || myLOSS_ARGS+=(--accept-data-loss)
[ -z "${myACCEPT_FILE}" ] || myLOSS_ARGS+=(--accept-data-loss-file "${myACCEPT_FILE}")

# --check-data-loss: no -y needed, it changes nothing and asks nothing. It runs for the T-Pot
# Manager without a terminal, so sudo never asks either: without -B a sudo that needs no
# password, otherwise the sizes as this user sees them
if [ -n "${myCHECK_LOSS}" ];
  then
    if [ -z "${myBECOME_FILE}" ];
      then
        if command sudo -n true 2>/dev/null;
          then sudo () { command sudo -n "$@"; }
          else sudo () { "$@"; }
        fi
    fi
    fuCHECK_DATA_LOSS
    exit $?
fi

fuUI_BANNER "Updater" "Updates T-Pot to the latest version of its branch, with a backup first."
if [ "${myCONFIRMED}" != "y" ]; then
  fuUI_INFO "This script will update T-Pot to the latest version."
  fuUI_INFO "A backup of ~/tpotce will be written to ${HOME}/tpot_backups. If you are unsure, you should save your work."
  fuUI_INFO "This tool might break things and therefore only recommended for experienced users."
  fuUI_INFO "If you understand the involved risks feel free to run this script with the '-y' switch."
  echo
  exit
fi
# From here on every way out ends with the summary (fuEND)
myRUNNING="1"
[ -z "${myPREPARED}" ] || mySTOPPED="1"
# sudo asks for the password right away, not in the middle of the run
if ! sudo true;
  then
    fuDID fail "sudo did not work, nothing was changed."
    exit 1
fi
fuLOG_START
# did T-Pot run when this run started? After a handover the update.sh before has stopped it already
[ -n "${myPREPARED}" ] || myWAS_ACTIVE=$(systemctl is-active tpot 2>/dev/null)
# a TPOT_ES_STEP_WAIT that fuCHECK_STEP_WAIT replaced after a handover
fuSTEP_WAIT_WARN

# --backup-only: the backup of an update, nothing else (i.e. before tpot uninstall)
if [ -n "${myBACKUP_ONLY}" ];
  then
    fuMARK phase check Checking the edition and the space
    fuOLD_UPDATER_CONFIG
    fuCHECK_EDITION
    fuCHECK_BACKUP_SPACE
    fuMARK phase backup Writing the backup
    fuEXPORT_ELASTIC
    fuSTOP_TPOT
    fuBACKUP
    if [ -n "${mySTART}" ];
      then
        fuMARK phase start Starting T-Pot
        if fuSTART_TPOT;
          then fuDID ok "T-Pot is started again."
          else fuDID warn "T-Pot did not start again, please start it yourself."
        fi
      else
        fuDID next "T-Pot is stopped, 'sudo systemctl start tpot' brings it back."
    fi
    fuMARK phase "done" Done
    fuDID next "restore.sh brings the backup back."
    exit 0
fi

fuMARK phase check Checking the version and the source
# the update.sh of the tag 24.04.1 and the one of the branch master of 2026-08-24 reset .env and
# docker-compose.yml before they restarted into this one without a handover
fuOLD_UPDATER_CONFIG
fuCHECK_VERSION
fuCHECKINET "https://index.docker.io https://github.com"
fuCHECK_SOURCE
fuCHECK_EDITION
fuCHECK_BACKUP_SPACE
fuMARK phase backup Writing the backup
# The Elasticsearch export needs a running instance, so it goes before the stop
fuEXPORT_ELASTIC
fuSTOP_TPOT
fuBACKUP
fuMARK phase selfupdate Updating the checkout
# --adapt-compose also when the answer at the terminal was "adapt" (fuADOPT_CHOICE): the restarted script does not ask again
fuSELFUPDATE "$@" ${myLOSS_ARGS[@]+"${myLOSS_ARGS[@]}"} ${myADAPT_COMPOSE:+--adapt-compose}
# The config and the edition have to be back in place before the images are
# pulled, `docker compose pull` reads both.
fuMARK phase restore Putting your configuration back
fuRESTORE
# PyYAML and the venv module before the compose file is adapted (fuRESTORE_EDITION): installed also when
# the run is refused later on (a cancel of the compose file, the Elasticsearch checks), the release needs
# them either way
fuCUSTOMIZER_DEPS
fuRESTORE_EDITION
fuREMOVE_DROPPED_SERVICES

# Everything below belongs to the release of the checkout - the image tag .env now
# carries, and the cleanup that removes every other tag. On an older checkout that
# combination pulls the previous release and deletes the images this machine runs
# on, so the run ends here, after the configuration is safely back.
if [ -n "${myOLDER_CHECKOUT}" ];
  then
    echo
    fuUI_INFO "This is a downgrade, not an update, so nothing was pulled or removed."
    fuUI_HINT "The checkout is at $(git -C "$HOME/tpotce" rev-parse --abbrev-ref HEAD 2>/dev/null), whose update.sh predates the one you started. Its .env and its compose file ask for the images of that release, and the cleanup would remove the ones in use."
    fuUI_HINT "Your configuration and your edition are back in place, no image was touched, T-Pot is stopped."
    fuUI_HINT "To update to the current release instead:"
    fuUI_HINT "  ./update.sh -y -b master"
    fuUI_HINT "To leave things as they are, start T-Pot again with 'systemctl start tpot'."
    fuUI_HINT "The backup of this run is in ${myARCHIVE}."
    echo
    fuDID fail "Not updated: the checkout is an older release, nothing was pulled or removed."
    fuDID next "To update to the current release instead: ./update.sh -y -b master"
    exit 1
fi

# Still before the image pull: the images of the previous version tell which
# Elasticsearch version ran so far. And before tpot gets the packages of the new
# release: a rollback here leaves tpot as it was.
fuCHECK_ELASTIC
fuTPOT_SETUP
fuSSHD_DROPIN
fuMARK phase pull Pulling the images
fuUPDATER

if [ -n "${myEDITION}" ] && [ "${myEDITION}" != "UNKNOWN" ] && [ -z "${myEDITION_MISSING}" ];
  then
    fuDID ok "The T-Pot ${myEDITION} edition is in ~/tpotce/docker-compose.yml."
fi
if [ -n "${mySTART}" ];
  then
    # A failed pull alone is only a warning - the update itself is through, and every
    # start pulls again. But if a start was asked for and it does not come up, the
    # machine is not doing its job and an unattended run has to say so.
    fuMARK phase start Starting T-Pot
    if fuSTART_TPOT;
      then
        fuMARK phase "done" Done
        fuDID ok "T-Pot is started."
      else
        fuUI_ERROR "The update is through, but T-Pot is not running."
        [ -z "${myPULLOK}" ] && fuUI_HINT "The image pull failed earlier, which is the likely reason."
        echo
        myWHY=""
        [ -n "${myPULLOK}" ] || myWHY=", the image pull failed earlier, which is the likely reason"
        myEND_TITLE="T-Pot is updated, but not running"
        fuDID fail "The update is through, but T-Pot is not running${myWHY}."
        exit 1
    fi
  else
    fuMARK phase "done" Done
    fuDID next "Start T-Pot with 'sudo systemctl start tpot' or 'docker compose up -d' (update.sh -s does it for you)."
fi
