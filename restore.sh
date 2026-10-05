#!/bin/bash

# Some global vars
myDATE=$(date +%Y%m%d%H%M%S)
myBECOME_FILE=""
myGROUPS=""

# The look of the T-Pot scripts (installer/lib/ui.sh: gum at a terminal, plain text
# otherwise) and the @@tpot marks the task screen of tpot reads (fuMARK).
myHERE=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
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
    fuUI_CONFIRM () { local myANSWER; read -rp "### $1 (y/n) " myANSWER; [[ "${myANSWER}" =~ ^(y|Y|yes|YES)$ ]]; }
    fuMARK () { [ "${TPOT_MARKS}" = "1" ] && echo "@@tpot $*"; return 0; }
# <<< plain fallback
fi
fuUI_INIT

myTPOTDIR="${HOME}/tpotce"
myBACKUPDIR="${HOME}/tpot_backups"
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
	cat <<EOF
Usage: $0 [-l] [-f <archive>] [-y | -c | -g <groups>] [-B <file>]

Restores a backup written by update.sh.

Options:
  -l                List the available backups and what they hold
  -f <archive>      Restore from this archive. Default: the newest one in
                    ${myBACKUPDIR}
  -y                Restore everything without asking, including the rollback
                    of the git checkout
  -c                Only roll the checkout back and restore the configuration
                    (.env, docker-compose.yml, your changes to tracked files),
                    without asking. Leaves data/ alone and does not start T-Pot.
  -g <groups>       Restore these groups without asking, comma separated: git,
                    patch, config, untracked, data, elastic (the tpot menu uses it)
  -B <file>         Read the sudo password from a file, so it runs without
                    asking for it (the tpot menu hands one over)
  -h                Show this help message

Without -y every group is offered separately, so you can bring back just the
configuration without touching anything else.
EOF
	exit 1
}

# Check if running with root privileges
if [ ${EUID} -eq 0 ];
  then
    echo "This script should not be run as root. Please run it as a regular user."
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

# All archives, newest first
function fuARCHIVE_LIST () {
	ls -1t "${myBACKUPDIR}"/*_tpot_backup*.tar 2>/dev/null
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
	if ! tar tf "${myARCHIVE}" > "${myTMPDIR}/toc" 2>"${myTMPDIR}/toc.err";
	  then
	    fuUI_ERROR "Cannot read ${myARCHIVE}."
	    sed 's/^/    /' "${myTMPDIR}/toc.err"
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

# Ask, unless -y was given
function fuASK () {   # $1 = Frage
	[ -n "${myCONFIRMED}" ] && return 0
	fuUI_CONFIRM "$1" "Yes" "No"
}

# What is in there, and which of it should come back?
function fuCHOOSE () {
	fuUI_INFO "What should be restored?"
	if fuHAS "^rollback.txt$"; then
	  fuASK "Roll the checkout back to the commit before the update?" && myDO_GIT="1"
	fi
	if fuHAS "^tracked.patch$"; then
	  fuASK "Re-apply all your changes to tracked files (tracked.patch)?" && myDO_PATCH="1"
	fi
	if fuHAS "^env$"; then
	  fuASK "Restore the configuration (.env and docker-compose.yml)?" && myDO_CONFIG="1"
	fi
	if fuHAS "^untracked/"; then
	  fuASK "Restore your untracked files?" && myDO_UNTRACKED="1"
	fi
	if fuHAS "^data/"; then
	  fuASK "Restore the files from data/ (certificates, uuid, host keys)?" && myDO_DATA="1"
	fi
	if fuHAS "^elastic/"; then
	  fuASK "Import the Kibana objects and the ILM policy? T-Pot has to run for that." && myDO_ELASTIC="1"
	fi
	echo
	if [ -z "${myDO_GIT}${myDO_CONFIG}${myDO_PATCH}${myDO_UNTRACKED}${myDO_DATA}${myDO_ELASTIC}" ];
	  then
	    fuUI_HINT "Nothing selected, leaving everything as it is."
	    echo
	    exit 0
	fi
}

function fuSTOP_TPOT () {
	fuMARK phase stop Stopping T-Pot
	echo
	if sudo systemctl stop tpot.service 2>/dev/null;
	  then
	    fuUI_OK "T-Pot is stopped."
	  else
	    fuUI_WARN "No tpot.service, trying docker compose."
	    [ -f "${myTPOTDIR}/docker-compose.yml" ] && ( cd "${myTPOTDIR}" && docker compose down ) >/dev/null 2>&1
	fi
}

function fuSTART_TPOT () {
	fuMARK phase start Starting T-Pot
	echo
	if sudo systemctl start tpot.service 2>/dev/null;
	  then
	    fuUI_OK "T-Pot is started."
	  else
	    fuUI_WARN "Could not start tpot.service, please start T-Pot yourself."
	    return 1
	fi
}

# The rollback comes first: a `git reset --hard` puts .env and docker-compose.yml
# back to the state of the commit, so it would overwrite anything done after it.
function fuDO_GIT () {
	local myCOMMIT=""
	[ -z "${myDO_GIT}" ] && return
	fuMARK phase git Rolling the checkout back
	echo
	fuUI_INFO "Rolling the checkout back ..."
	tar xOf "${myARCHIVE}" rollback.txt > "${myTMPDIR}/rollback.txt"
	myCOMMIT=$(tr -d "[:space:]" < "${myTMPDIR}/rollback.txt")
	if [ -z "${myCOMMIT}" ];
	  then
	    fuUI_WARN "rollback.txt is empty, skipping."
	    return
	fi
	if ! git -C "${myTPOTDIR}" cat-file -e "${myCOMMIT}^{commit}" 2>/dev/null;
	  then
	    fuUI_WARN "Commit ${myCOMMIT} is not in ${myTPOTDIR}, skipping."
	    return
	fi
	if git -C "${myTPOTDIR}" reset -q --hard "${myCOMMIT}";
	  then
	    fuUI_OK "The checkout is at ${myCOMMIT} again."
	  else
	    fuUI_ERROR "Could not reset the checkout to ${myCOMMIT}."
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
	fi
	if fuHAS "^docker-compose.yml$";
	  then
	    if tar xf "${myARCHIVE}" -C "${myTMPDIR}" docker-compose.yml 2>/dev/null \
	       && cp "${myTMPDIR}/docker-compose.yml" "${myTPOTDIR}/docker-compose.yml";
	      then
	        fuUI_OK "Wrote ${myTPOTDIR}/docker-compose.yml ($(head -1 "${myTPOTDIR}/docker-compose.yml"))."
	      else
	        fuUI_ERROR "Could not restore docker-compose.yml."
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
	tar xf "${myARCHIVE}" -C "${myTMPDIR}" tracked.patch 2>/dev/null
	if [ ! -s "${myTMPDIR}/tracked.patch" ];
	  then
	    fuUI_OK "The patch is empty, there was nothing to re-apply."
	    return
	fi
	if git -C "${myTPOTDIR}" apply --check "${myTMPDIR}/tracked.patch" 2>/dev/null;
	  then
	    git -C "${myTPOTDIR}" apply "${myTMPDIR}/tracked.patch" \
	      && fuUI_OK "Applied."
	    return
	fi
	if git -C "${myTPOTDIR}" apply --3way "${myTMPDIR}/tracked.patch" 2>/dev/null;
	  then
	    fuUI_WARN "Applied with a three-way merge, please review the result."
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
	    return
	fi
	if ( cd "${myTMPDIR}/untracked" && tar cf - . ) | ( cd "${myTPOTDIR}" && tar xf - );
	  then
	    fuUI_OK "Restored $(find "${myTMPDIR}/untracked" -type f | wc -l) files."
	  else
	    fuUI_ERROR "Could not write them."
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
	    fi
	fi
	if sudo tar xf "${myARCHIVE}" -C "${myTPOTDIR}" -p --numeric-owner --wildcards "data/*" 2>"${myTMPDIR}/data.err";
	  then
	    fuUI_OK "$(grep -c '^data/' "${myTMPDIR}/toc") entries restored, owner and mode came from the archive."
	  else
	    fuUI_ERROR "The files from data/ could not be restored:"
	    sed 's/^/    /' "${myTMPDIR}/data.err"
	fi
}

# The Elasticsearch import needs a running instance - unlike everything else, which
# wants T-Pot stopped. So it runs last, after the start.
function fuDO_ELASTIC () {
	local myWAIT=0
	local myOBJ=0
	local myKEEP=""
	[ -z "${myDO_ELASTIC}" ] && return
	fuMARK phase elastic Importing the Kibana objects and the ILM policy
	echo
	fuUI_INFO "Importing the Elasticsearch state ..."
	rm -rf "${myTMPDIR}/elastic"
	tar xf "${myARCHIVE}" -C "${myTMPDIR}" elastic 2>/dev/null
	fuUI_HINT "Waiting for Kibana on ${myKIBANA} ..."
	while [ "${myWAIT}" -lt "${myKIBANA_TIMEOUT}" ];
	  do
	    curl -s -f -o /dev/null --connect-timeout 3 "${myKIBANA}/api/status" && break
	    sleep 5
	    myWAIT=$((myWAIT+5))
	done
	if ! curl -s -f -o /dev/null "${myKIBANA}/api/status";
	  then
	    fuUI_ERROR "Kibana does not answer."
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
	    if curl -s -f -X POST "${myKIBANA}/api/saved_objects/_import?overwrite=true" \
	         -H "kbn-xsrf: true" --form file=@"${myTMPDIR}/elastic/kibana_export.ndjson" \
	         -o "${myTMPDIR}/import.json";
	      then
	        myOBJ=$(sed -n 's/.*"successCount":\([0-9]*\).*/\1/p' "${myTMPDIR}/import.json")
	        if grep -q '"success":true' "${myTMPDIR}/import.json";
	          then
	            fuUI_OK "Imported ${myOBJ:-0} Kibana objects."
	          else
	            fuUI_WARN "Imported ${myOBJ:-0} Kibana objects, errors reported:"
	            sed 's/^/    /' "${myTMPDIR}/import.json" | head -5
	        fi
	      else
	        fuUI_ERROR "The Kibana objects could not be imported."
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
	    fi
	fi
}

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
    h|\?)
      fuPRINT_HELP
      ;;
    :)
      echo "Option -${OPTARG} requires an argument."
      fuPRINT_HELP
      ;;
  esac
done

# -g: only known groups
for myGROUP in ${myGROUPS//,/ };
  do
    case "${myGROUP}" in
      git|patch|config|untracked|data|elastic) ;;
      *) fuUI_ERROR "There is no group ${myGROUP}."; fuPRINT_HELP ;;
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
    fuMARK phase "done" Done
  else
    fuMARK phase "done" Done
    echo
    fuUI_OK "Done. You can now start T-Pot using 'systemctl start tpot' or 'docker compose up -d'."
fi
echo
