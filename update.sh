#!/bin/bash

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

# Both are published on the loopback interface by every edition that ships them,
# so neither needs a detour through a container.
myKIBANA="http://127.0.0.1:64296"
myES="http://127.0.0.1:64298"

# How long to wait for a service this installation runs but that is not answering
# yet. Kibana needs about a minute after a start before it does, and an update run
# right after a reboot would otherwise walk away without the saved objects.
myELASTIC_GRACE="${TPOT_ELASTIC_GRACE:-120}"

# Working directory, cleaned up when the script ends
myTMPDIR=""
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

# The look of the T-Pot scripts (installer/lib/ui.sh: gum at a terminal, plain text
# otherwise) and the @@tpot marks the task screen of tpot reads (fuMARK). BASH_SOURCE:
# the tests source this file for its functions.
myHERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
# shellcheck source=installer/lib/ui.sh
if ! source "${myHERE}/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback: a checkout of an earlier release has no installer/lib/ui.sh
    fuUI_INIT () { return 0; }
    fuUI_BANNER () { echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE}"; done; echo; }
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
    fuUI_SPIN () {
      local myTITLE="$1" myLOG="$2" myRC=0
      shift 2
      echo "### ${myTITLE}"
      if fuUI_MARKS_ON;
        then "$@" < /dev/null || myRC=$?
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
# the T-Pot logo in the banner (at a terminal, not in the marks mode); the restart after
# the self update inherits that it was shown, so it shows the banner only
# shellcheck disable=SC2034 # read by fuUI_BANNER (installer/lib/ui.sh)
myUI_LOGO=1

# Where to update from. Empty means: keep the branch and the origin of the
# current checkout, which is what a plain `update.sh -y` has always done.
myTPOT_BRANCH="${TPOT_BRANCH}"
myTPOT_REPO_URL="${TPOT_REPO_URL}"
myTPOT_SOURCE_GIVEN=""

# The installed T-Pot edition. It only exists as the marker comment in the first
# line of docker-compose.yml, a tracked file, so the update overwrites it with the
# STANDARD edition. Everything needed to put it back is read before the update and
# handed over to a restarted update.sh through the environment, see fuSELFUPDATE.
# A restarted update.sh inherits what the first pass already did, so it neither
# stops T-Pot again nor writes a second backup - and it restores from the FIRST
# archive, the only one taken before the checkout was reset.
myPREPARED="${TPOT_UPDATE_PREPARED}"
myARCHIVE="${TPOT_UPDATE_ARCHIVE}"

myEDITION="${TPOT_UPDATE_EDITION}"
myCOMPOSE_CUSTOMIZED="${TPOT_UPDATE_COMPOSE_CUSTOMIZED}"
myEDITIONS="STANDARD SENSOR MINI LLM TARPIT MOBILE MAC_WIN"

# Services that are no longer part of T-Pot. Their images are not built for this
# release, a docker-compose.yml of your own that still has them fails on the pull.
myDROPPED_SERVICES="spiderfoot fatt"

function fuPRINT_HELP () {
	# at a terminal the T-Pot logo first, as a run shows it
	fuUI_LOGO
	fuUI_HELP "Updater" "update.sh -y [-s] [--full] [--backup-only] [-b <branch>] [-r <url>] [-B <file>]" \
	  --about "Updates T-Pot to the latest version of its branch: a backup to ~/tpot_backups first, then the
checkout, your configuration and edition put back, the images of the release pulled." \
	  --opt "-y" "Confirm the update, required" \
	  --opt "-s, --start" "Start T-Pot again once the update is through. Off by default,
so an unattended run leaves the services stopped unless asked." \
	  --opt "-F, --full" "Include the whole data/ folder in the backup. Off by default,
on a busy hive it turns a 1 MB archive into tens of GB. Use it
when a release brings a newer Elastic Stack: Elasticsearch and
Kibana upgrade their data in data/elk/ on the first start, and
that cannot be undone. Kept uncompressed either way." \
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
	  --opt "-h" "Show this help message" \
	  --example "./update.sh -y" "Update from the branch of ~/tpotce, T-Pot stays stopped afterwards" \
	  --example "./update.sh -y -s --full" "Update with all of data/ in the backup and start T-Pot again" \
	  --example "./update.sh -y -b dev" "Test the branch dev (it stays checked out)" \
	  --example "./update.sh -y --backup-only -s" "Only a backup, T-Pot runs again afterwards" \
	  --note "restore.sh brings a backup back, tpot update hands every option on to update.sh."
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
	myDONE+=("$1:$2")
}

# The log of the steps under a spinner: the one of this run (the restart after the self
# update adds to it), next to the backups. Without a place for it the output goes nowhere.
function fuLOG_START () {
	if mkdir -p "${myBACKUPDIR}" 2>/dev/null && chmod 0700 "${myBACKUPDIR}" 2>/dev/null \
	   && { [ -n "${myPREPARED}" ] || : > "${myLOG}"; } 2>/dev/null;
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
	      else fuDID info "T-Pot was left running."
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

# Remove the oldest archive to make room. The newest one always stays - it is the
# most likely rollback point.
function fuDROP_OLDEST () {
	local myAll="" myVictim="" myCount=0
	myAll=$(ls -1tr "${myBACKUPDIR}"/*_tpot_backup*.tar 2>/dev/null)
	myCount=$(echo "${myAll}" | grep -c .)
	[ "${myCount}" -lt 2 ] && return 1
	myVictim=$(echo "${myAll}" | head -1)
	fuUI_HINT "Removing ${myVictim} ($(du -h "${myVictim}" | cut -f1)) to make room."
	rm -f "${myVictim}"
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
	local myPATHS=() myJEWEL="" mySUM=0
	[ -f "$HOME/tpotce/.env" ] && myPATHS+=("$HOME/tpotce/.env")
	[ -f "$HOME/tpotce/docker-compose.yml" ] && myPATHS+=("$HOME/tpotce/docker-compose.yml")
	if [ -n "${myFULL}" ];
	  then
	    [ -d "$HOME/tpotce/data" ] && myPATHS+=("$HOME/tpotce/data")
	  else
	    for myJEWEL in ${myJEWELS};
	      do
	        [ -e "$HOME/tpotce/${myJEWEL}" ] && myPATHS+=("$HOME/tpotce/${myJEWEL}")
	      done;
	fi
	[ -d "${myTMPDIR}/stage/elastic" ] && myPATHS+=("${myTMPDIR}/stage/elastic")
	if [ ${#myPATHS[@]} -gt 0 ];
	  then
	    mySUM=$(sudo du -scb "${myPATHS[@]}" 2>/dev/null | tail -1 | cut -f1)
	fi
	echo $((mySUM + 4194304))
}

# Does the archive still fit without closing the disk for the honeypots? Runs
# before fuSTOP_TPOT on purpose, so that giving up leaves T-Pot running.
function fuCHECK_BACKUP_SPACE () {
	local myNEED=0 myFREE=0 myTOTAL=0 myRESERVE=0
	echo
	fuUI_INFO "Checking the space for the backup ..."
	if [ -n "${myPREPARED}" ];
	  then
	    fuUI_OK "The backup was already taken before the restart, nothing to check."
	    echo
	    return
	fi
	if ! mkdir -p "${myBACKUPDIR}" || ! chmod 0700 "${myBACKUPDIR}";
	  then
	    fuUI_ERROR "Could not prepare ${myBACKUPDIR}."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	myTOTAL=$(df -B1 --output=size "${myBACKUPDIR}" 2>/dev/null | tail -1 | tr -d " ")
	myRESERVE=$(( myTOTAL / 100 * myBACKUP_RESERVE_PERCENT ))
	while :;
	  do
	    myNEED=$(fuBACKUP_SIZE)
	    myFREE=$(df -B1 --output=avail "${myBACKUPDIR}" 2>/dev/null | tail -1 | tr -d " ")
	    fuUI_HINT "Archive needs about $((myNEED / 1048576)) MB, $((myFREE / 1048576)) MB free, keeping $((myRESERVE / 1048576)) MB in reserve."
	    if [ $((myFREE - myNEED)) -ge "${myRESERVE}" ];
	      then
	        fuUI_OK "Enough room."
	        echo
	        return
	    fi
	    if fuDROP_OLDEST;
	      then
	        continue
	    fi
	    # Nothing left to free. `--full` was asked for, usually because the update
	    # brings a new Elastic Stack whose data upgrade cannot be undone - quietly
	    # taking the regular backup instead would leave no way back.
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
	done
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
	myCURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
	myCURRENT_REPO=$(fuNORMALIZE_REPO "$(git remote get-url origin 2>/dev/null)")
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
	    fuUI_ERROR "The checkout is not on a branch (detached at $(git describe --tags --always 2>/dev/null)), so there is nothing to update from."
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

# Was the compose file built by compose/customizer.py (v2 or later)?
function fuCUSTOM_FILE () {   # $1 = compose file
	sed -n 2p "$1" 2>/dev/null | grep -q "^# customizer: version="
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
	    echo
	    return
	fi
	myEDITION=$(head -1 "$HOME/tpotce/docker-compose.yml" | sed -n 's/^# T-Pot: *//p')
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
	if [ "${myEDITION}" == "UNKNOWN" ];
	  then
	    fuUI_WARN "Unable to determine the edition, docker-compose.yml is restored as it is."
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
	local mySWITCH=""
	if [ "${myTPOT_REPO_URL}" != "${myCURRENT_REPO}" ];
	  then
	    fuUI_HINT "Now switching origin from ${myCURRENT_REPO} to ${myTPOT_REPO_URL}."
	    git remote set-url origin "${myTPOT_REPO_URL}"
	    mySWITCH="1"
	fi
	if [ "${myTPOT_BRANCH}" != "${myCURRENT_BRANCH}" ];
	  then
	    fuUI_HINT "Now switching from branch ${myCURRENT_BRANCH} to ${myTPOT_BRANCH}."
	    mySWITCH="1"
	fi
	[ -n "${mySWITCH}" ] || return
	git fetch origin --prune
	git reset --hard
	if ! git checkout -B "${myTPOT_BRANCH}" "origin/${myTPOT_BRANCH}";
	  then
	    fuUI_ERROR "Could not check out ${myTPOT_BRANCH}."
	    fuUI_HINT "Exiting."
	    echo
	    exit 1
	fi
	# a branch that existed locally before keeps whatever it tracked, the pull
	# below has to follow the branch that was just requested
	git branch --set-upstream-to="origin/${myTPOT_BRANCH}" "${myTPOT_BRANCH}"
}

# Update
function fuSELFUPDATE () {
	echo
	fuUI_INFO "Now checking for newer files in repository ..."
	# The running script may be replaced by the update, either by newer commits
	# or by a switch to a different branch, and then has to restart itself.
	myOLDSUM=$(sha256sum "$0" | awk '{ print $1 }')
	local myOLDHEAD=""
	myOLDHEAD=$(git rev-parse HEAD 2>/dev/null)
	fuSWITCH_SOURCE
	fuUI_HINT "Pulling updates from repository."
	# Checked, because a failed pull used to leave the checkout untouched while the
	# run went on to report "Done" - an update that silently did nothing.
	if ! git fetch --all || ! git reset --hard || ! git pull --force;
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
	if [ "$(git rev-parse HEAD 2>/dev/null)" != "${myOLDHEAD}" ];
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
	fuDID ok "The checkout is on $(git rev-parse --abbrev-ref HEAD 2>/dev/null) at $(git rev-parse --short HEAD 2>/dev/null), version $(cat version 2>/dev/null)."
	echo
}

# The options for the restarted script: all of them, but -B <file> only if that
# script knows it - the update.sh of an earlier release stops with its usage on it
# (the sudo timestamp of this run is enough for the restarted one then).
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
	    printf '%s\n' "${myARG}"
	done
}

function fuCHECK_VERSION () {
	local myMINVERSION="24.04.1"
	# The newest release this update.sh knows: the one it belongs to, the file version of
	# its checkout (after the self update the one just fetched), no number written in here
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
	if [ -f "version" ];
	  then
	    myVERSION=$(cat version)
	    if [[ "$myVERSION" > "$myMINVERSION" || "$myVERSION" == "$myMINVERSION" ]] && [[ "$myVERSION" < "$myMASTERVERSION" || "$myVERSION" == "$myMASTERVERSION" ]]
	      then
	        fuUI_OK "$myVERSION is eligible for the update procedure."
	      elif [ -n "${myTPOT_SOURCE_GIVEN}" ];
	        then
	          # A branch or a fork may have moved the version tag on already,
	          # that must not stop a test of the update procedure itself.
	          fuUI_WARN "$myVERSION is outside the supported range, continuing because an update source was requested."
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
	    return 1
	fi
	if ! fuWAIT_FOR "$3" "$4";
	  then
	    fuUI_WARN "$4 did not answer within ${myELASTIC_GRACE}s, it is NOT in the backup."
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
	    fuUI_OK "T-Pot is started."
	    return 0
	fi
	fuUI_WARN "Could not start tpot.service, trying docker compose."
	if [ -f "$HOME/tpotce/docker-compose.yml" ] \
	   && ( cd "$HOME/tpotce" && docker compose up -d ) >/dev/null 2>&1;
	  then
	    mySTOPPED=""
	    fuUI_OK "Started with docker compose."
	    return 0
	fi
	fuUI_ERROR "Please start T-Pot yourself."
	return 1
}

# Where the data folder really is. TPOT_DATA_PATH is relative to ~/tpotce unless
# it is absolute.
function fuDATA_PATH () {
	local myPATH=""
	myPATH=$(grep -E "^TPOT_DATA_PATH=" "$HOME/tpotce/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'")
	[ -z "${myPATH}" ] && myPATH="./data"
	case "${myPATH}" in
	  /*) ;;
	  *)  myPATH="$HOME/tpotce/${myPATH#./}" ;;
	esac
	echo "${myPATH%/}"
}

# Put the checkout and the configuration back to where they were before this run,
# so that neither a start nor the daily reboot picks up the new release. Only
# possible if the archive of this run points at an older commit.
function fuROLLBACK_CHECKOUT () {
	local myCOMMIT=""
	myCOMMIT=$(tar xOf "${myARCHIVE}" rollback.txt 2>/dev/null | tr -d "[:space:]")
	if [ -z "${myCOMMIT}" ] || [ "${myCOMMIT}" == "$(git -C "$HOME/tpotce" rev-parse HEAD)" ] || [ ! -x "$HOME/tpotce/restore.sh" ];
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
	# the checkout is the one before this update again, tpot needs no restart for it
	fuMARK changed back
}

# Ctrl+C in the pause of fuCHECK_ELASTIC: stop before the pull, say how to finish
function fuELASTIC_STOPPED () {
	echo
	fuUI_WARN "Stopped before the image pull, nothing was pulled and T-Pot is stopped."
	fuUI_HINT "Copy the data now, then finish the update with:"
	fuUI_HINT "  docker compose -f $HOME/tpotce/docker-compose.yml pull && sudo systemctl start tpot"
	fuUI_HINT "  $HOME/tpotce/tpot setup    (the Python packages of the T-Pot Manager for this release)"
	echo
	fuDID warn "Stopped before the image pull, nothing was pulled."
	fuDID next "Copy the data, then finish with the commands above."
	exit 130
}

# Elasticsearch and Kibana upgrade their data in place on the first start of a
# newer version, and there is no way back to the older one. Runs after the pull of
# the repository and before the pull of the images, in the restarted script as
# well: everything before the stop is done by the update.sh the user started,
# which may be an older one. T-Pot is stopped at this point.
function fuCHECK_ELASTIC () {
	local myNEW="" myOLD="" myDATA="" myUSED=0
	fuCOMPOSE_HAS elasticsearch || return
	echo
	fuUI_INFO "Checking the Elastic Stack update ..."
	myNEW=$(sed -n "s/^ARG ES_VER=//p" "$HOME/tpotce/docker/elk/elasticsearch/Dockerfile" 2>/dev/null | head -1)
	if [ -z "${myNEW}" ];
	  then
	    fuUI_WARN "Cannot tell which Elasticsearch version this release ships, skipping the check."
	    echo
	    return
	fi
	# The images of the previous version are still there, fuSTOP_TPOT only prunes
	# the untagged ones. ES_VER is set in every T-Pot Elasticsearch image.
	myOLD=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
	        "$(grep -E "^TPOT_REPO=" "$HOME/tpotce/.env" | tail -1 | cut -d= -f2-)/elasticsearch:${myOLDVERSION}" 2>/dev/null \
	        | sed -n "s/^ES_VER=//p" | head -1)
	if [ "${myOLD}" == "${myNEW}" ];
	  then
	    fuUI_OK "Elasticsearch stays on ${myNEW}."
	    echo
	    return
	fi
	if [ -z "${myOLD}" ];
	  then
	    fuUI_WARN "Cannot tell which Elasticsearch version ran so far, assuming it changes to ${myNEW}."
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
	    fuROLLBACK_CHECKOUT
	    fuUI_HINT "Free up space and run the update again. T-Pot is stopped, 'systemctl start tpot' brings it back."
	    fuUI_HINT "Exiting."
	    echo
	    fuDID fail "${myDATA} is ${myUSED}% full, Elasticsearch needs it below 90%: free up space and update again."
	    exit 1
	fi
	if [ -n "${myUSED}" ] && [ "${myUSED}" -ge 85 ];
	  then
	    fuUI_WARN "${myDATA} is ${myUSED}% full, Elasticsearch starts to complain at 85%."
	fi
	# A `--full` archive of ~/tpotce/data is the only way back to the old version
	if [ "${myDATA}" == "$HOME/tpotce/data" ] && tar tf "${myARCHIVE}" data/elk/data >/dev/null 2>&1;
	  then
	    fuUI_OK "The Elasticsearch data is in ${myARCHIVE}."
	    echo
	    return
	fi
	fuUI_WARN "Elasticsearch and Kibana will upgrade their data in ${myDATA}/elk on the next start."
	fuUI_HINT "This cannot be undone, and the backup of this run does not hold that data."
	fuUI_HINT "T-Pot is stopped right now, so this is the moment to copy it, e.g.:"
	fuUI_HINT "  sudo cp -a ${myDATA}/elk/data ${myBACKUPDIR}/elk_data_${myOLD:-old}"
	fuUI_HINT "The checkout and .env are already on the new release, afterwards finish with:"
	fuUI_HINT "  docker compose -f $HOME/tpotce/docker-compose.yml pull && sudo systemctl start tpot"
	fuUI_HINT "  $HOME/tpotce/tpot setup    (the Python packages of the T-Pot Manager for this release)"
	if [ -t 0 ] && [ -t 1 ];
	  then
	    fuUI_HINT "Press Ctrl+C to stop here and copy it first, continuing in 15 seconds ..."
	    # Ctrl+C reaches bash and sleep at once: the trap or the status of the interrupted sleep
	    # stops the run, whichever bash sees first
	    trap fuELASTIC_STOPPED INT
	    sleep 15 || fuELASTIC_STOPPED
	    trap - INT
	fi
	echo
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
	echo
	fuUI_INFO "Create a backup, just in case ... "
	# The second pass would archive the checkout that the pull has already reset, and
	# fuRESTORE would then put that back - the user's configuration would be gone
	# while the run still reported success.
	if [ -n "${myPREPARED}" ];
	  then
	    fuUI_OK "Keeping the backup from before the restart: ${myARCHIVE}"
	    fuDID ok "The backup is ${myARCHIVE}."
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
	git -C "$HOME/tpotce" diff HEAD > "${myStage}/tracked.patch"
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
	# to tpot:tpot with 0770, which the archive has to record.
	if [ -n "${myFULL}" ];
	  then
	    [ -d "$HOME/tpotce/data" ] && myJEWELLIST+=("data")
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

	# A single tar run: intermediate files created under sudo belong to root and
	# could not be moved afterwards. It runs under the spinner, which cannot ask for
	# a password, so sudo is refreshed first.
	sudo -v
	if ! fuUI_SPIN "Building the ${myFULL:+full }archive ${myARCHIVE} ..." "${myLOG}" \
	        sudo tar cf "${myARCHIVE}" -p --numeric-owner -C "${myStage}" ${myTARGETS} "${myJEWELARGS[@]}";
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
	myTOTAL="${#myALL[@]}"
	if [ "${myTOTAL}" -eq 0 ];
	  then
	    fuUI_OK "Nothing to remove."
	    return
	fi
	fuUI_SPIN "Removing ${myTOTAL} image(s) ..." "${myLOG}" fuRMI "${myALL[@]}"
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
	if fuUI_SPIN "Pulling the images of this release ..." "${myLOG}" fuPULLIMAGES;
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
	fuMARK phase cleanup Removing old images
	fuREMOVEOLDIMAGES
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
	newVERSION=$(grep -E "^TPOT_VERSION=" env.example 2>/dev/null | tail -1 | cut -d= -f2-)
	[ -z "${newVERSION}" ] && newVERSION=$(cat version)
	myOLDVERSION=$(grep -E "^TPOT_VERSION=" "$HOME/tpotce/.env" | tail -1 | cut -d= -f2-)
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
	myNEWREPO=$(grep -E "^TPOT_REPO=" env.example 2>/dev/null | tail -1 | cut -d= -f2-)
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
	local myOLDMODEL myOLLAMAMODEL myHOST myAPIKEY myPROVIDER myMODEL
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
	' "${myENVFILE}" > "${myENVFILE}.beelzebub";
	  then
	    rm -f "${myENVFILE}.beelzebub"
	    fuUI_ERROR "Could not migrate the Beelzebub settings in ${myENVFILE}, please set them up as in env.example."
	    return 0
	fi
	# cat keeps the owner and the mode of .env, it holds credentials
	cat "${myENVFILE}.beelzebub" > "${myENVFILE}" && rm -f "${myENVFILE}.beelzebub"
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
	        continue ;;
	    esac
	    [ "${myOLD}" == "${myNEW}" ] && continue
	    sed -i -E "s/^(${myKEY}[[:space:]]*[:=][[:space:]]*)[\"']?${myOLD}[\"']?[[:space:]]*$/\1${myNEW}/" "${myENVFILE}"
	    if [ "$(fuENV_VALUE "${myENVFILE}" "${myKEY}")" != "${myNEW}" ];
	      then
	        fuUI_ERROR "Could not change ${myKEY}=${myOLD} to ${myNEW}, please set it in .env."
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
	[ -f "${myEXAMPLE}" ] && [ -f "${myENVFILE}" ] || return 0
	myVERSION=$(grep -E "^TPOT_VERSION=" "${myEXAMPLE}" | tail -1 | cut -d= -f2-)
	myMARKER=$(grep -n "^# NEVER MAKE CHANGES" "${myEXAMPLE}" | head -1 | cut -d: -f1)
	: > "${myENVFILE}.user"
	: > "${myENVFILE}.system"
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
	    myTARGET="${myENVFILE}.user"
	    [ -n "${myMARKER}" ] && [ "${myKEYLINE}" -gt "${myMARKER}" ] && myTARGET="${myENVFILE}.system"
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
	    if awk -v myUSERFILE="${myENVFILE}.user" -v mySYSTEMFILE="${myENVFILE}.system" -v myOBSOLETE=" ${myOBSOLETE} " -v myVERSION="${myVERSION}" '
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
	      }' "${myENVFILE}" > "${myENVFILE}.merge";
	      then
	        # cat keeps the owner and the mode of .env, it holds credentials
	        cat "${myENVFILE}.merge" > "${myENVFILE}"
	        [ -n "${myADDED}" ] && fuUI_OK "New settings added to .env with their defaults:${myADDED}"
	        [ -n "${myOBSOLETE}" ] && fuUI_OK "Settings no longer used, commented out in .env:${myOBSOLETE}"
	      else
	        fuUI_ERROR "Could not add the new settings to ${myENVFILE}, please compare it with env.example."
	    fi
	fi
	[ -n "${myKEPT}" ] && fuUI_HINT "Not in env.example, kept as a compose file uses them:${myKEPT}"
	rm -f "${myENVFILE}.user" "${myENVFILE}.system" "${myENVFILE}.merge"
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
	if [ "${myEDITION}" == "CUSTOM" ];
	  then
	    fuRESTORE_CUSTOM
	    echo
	    return
	fi
	# An update.sh from before this function existed reset docker-compose.yml to
	# the STANDARD edition without remembering anything, in which case TPOT_TYPE in
	# the restored .env is the only hint that is left.
	if [ "${myEDITION}" == "STANDARD" ] || [ "${myEDITION}" == "UNKNOWN" ];
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
	        echo
	        return
	    fi
	    fuUI_OK "The ${myEDITION} edition is restored."
	    if [ -n "${myCOMPOSE_CUSTOMIZED}" ];
	      then
	        fuUI_WARN "Your docker-compose.yml had been modified. The ${myEDITION} edition of this release is in place now, please add your changes again."
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
	        echo
	        return
	    fi
	    fuUI_OK "Your own docker-compose.yml is restored from the backup."
	    fuUI_WARN "Your docker-compose.yml does not match any edition in compose/, so the changes of this release were not applied to it."
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
	    sudo ln -sfn "${myTPOT}" "${myLINK}" \
	      || fuUI_WARN "Could not link ${myLINK}, run ${myTPOT} directly."
	  else
	    fuUI_WARN "${myLINK} is a file of its own, update.sh leaves it alone: run ${myTPOT} directly."
	fi
	# the venv of the Manager: pip writes a lot, it goes to the log under a spinner
	if fuUI_SPIN "Installing the Python packages of the T-Pot Manager ..." "${myLOG}" "${myTPOT}" setup;
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
	local myCUSTOMIZER="$HOME/tpotce/compose/customizer.py"
	if ! fuCOMPOSE_FROM_ARCHIVE;
	  then
	    fuUI_ERROR "Could not take docker-compose.yml from ${myARCHIVE}. Put it back by hand with:"
	    fuUI_HINT "  tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
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
	        return
	    fi
	    fuUI_WARN "The rebuild failed, see above."
	fi
	if ! cp "${myTMPDIR}/docker-compose.yml" "$HOME/tpotce/docker-compose.yml";
	  then
	    fuUI_ERROR "Your docker-compose.yml could not be restored, put it back by hand with: tar xf ${myARCHIVE} -C $HOME/tpotce docker-compose.yml"
	    return
	fi
	fuUI_OK "Your docker-compose.yml is restored from the backup."
	fuUI_WARN "The changes of this release were not applied to it. Run the customizer again to build it anew:"
	fuUI_HINT "  cd $HOME/tpotce/compose && python3 customizer.py"
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
	        continue
	    fi
	    fuUI_OK "${mySERVICE} is removed from docker-compose.yml, it is no longer part of T-Pot."
	    fuUI_WARN "Your previous file is in the backup: tar xOf ${myARCHIVE} docker-compose.yml"
	done
	[ -n "${myFOUND}" ] && echo
}

# Sourced (the tests do, for the functions): no run
[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0

################
# Main section #
################

# getopts has no long options, so `--full` is translated before they are read
myARGV=()
for myARG in "$@";
  do
    case "${myARG}" in
      --full)  myARGV+=("-F") ;;
      --start) myARGV+=("-s") ;;
      --backup-only) myARGV+=("-o") ;;
      --help)  myARGV+=("-h") ;;
      --?*)    fuUI_USAGE_ERROR "Unknown option ${myARG}." update.sh; exit 1 ;;
      *)      myARGV+=("${myARG}") ;;
    esac
done
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

# --backup-only: the backup of an update, nothing else (i.e. before tpot uninstall)
if [ -n "${myBACKUP_ONLY}" ];
  then
    fuMARK phase check Checking the edition and the space
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
fuSELFUPDATE "$@"
# The config and the edition have to be back in place before the images are
# pulled, `docker compose pull` reads both.
fuMARK phase restore Putting your configuration back
fuRESTORE
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
    fuUI_HINT "The checkout is at $(git rev-parse --abbrev-ref HEAD 2>/dev/null), whose update.sh predates the one you started. Its .env and its compose file ask for the images of that release, and the cleanup would remove the ones in use."
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
fuMARK phase pull Pulling the images
fuUPDATER

if [ -n "${myEDITION}" ] && [ "${myEDITION}" != "UNKNOWN" ];
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
