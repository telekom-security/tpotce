#!/bin/bash

# Some global vars
myDATE=$(date +%Y%m%d%H%M%S)
myRED=$'\e[0;31m'
myGREEN=$'\e[0;32m'
myWHITE=$'\e[0;0m'
myBLUE=$'\e[0;34m'

myTPOTDIR="${HOME}/tpotce"
myBACKUPDIR="${HOME}/tpot_backups"
myARCHIVE=""
myCONFIRMED=""
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

myRESTORER=$(cat << "EOF"
 _____     ____       _     ____           _
|_   _|   |  _ \ ___ | |_  |  _ \ ___  ___| |_ ___  _ __ ___ _ __
  | |_____| |_) / _ \| __| | |_) / _ \/ __| __/ _ \| '__/ _ \ '__|
  | |_____|  __/ (_) | |_  |  _ <  __/\__ \ || (_) | | |  __/ |
  |_|     |_|   \___/ \__| |_| \_\___||___/\__\___/|_|  \___|_|
EOF
)

function fuPRINT_HELP () {
	cat <<EOF
Usage: $0 [-l] [-f <archive>] [-y]

Restores a backup written by update.sh.

Options:
  -l                List the available backups and what they hold
  -f <archive>      Restore from this archive. Default: the newest one in
                    ${myBACKUPDIR}
  -y                Restore everything without asking, including the rollback
                    of the git checkout
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
	    echo "###### $myBLUE""Could not create a temporary directory.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
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
	echo "### Backups in ${myBACKUPDIR} ..."
	if [ -z "$(fuARCHIVE_LIST)" ];
	  then
	    echo "###### $myBLUE""No backups found.""$myWHITE"
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
	    echo "###### $myBLUE$(basename "${myFILE}")$myWHITE  $(du -h "${myFILE}" | cut -f1), ${myKIND}"
	    tar xOf "${myFILE}" MANIFEST 2>/dev/null | sed -n '2,8p' | sed 's/^/         /'
	    echo "         Holds: $(tar tf "${myFILE}" 2>/dev/null | sed 's|/.*||' | sort -u | tr '\n' ' ')"
	done
	echo
	exit 0
}

# Pick the archive and read its table of contents
function fuPICK_ARCHIVE () {
	echo
	echo "### Looking for a backup ..."
	if [ -z "${myARCHIVE}" ];
	  then
	    myARCHIVE=$(fuARCHIVE_LIST | head -1)
	fi
	if [ -z "${myARCHIVE}" ] || [ ! -f "${myARCHIVE}" ];
	  then
	    echo "###### $myBLUE""No backup found in ${myBACKUPDIR}. Name one with '-f'.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo
	    exit 1
	fi
	fuTMPDIR
	if ! tar tf "${myARCHIVE}" > "${myTMPDIR}/toc" 2>"${myTMPDIR}/toc.err";
	  then
	    echo "###### $myBLUE""Cannot read ${myARCHIVE}.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    sed 's/^/###### /' "${myTMPDIR}/toc.err"
	    echo
	    exit 1
	fi
	echo "###### $myBLUE${myARCHIVE}$myWHITE  $(du -h "${myARCHIVE}" | cut -f1), $(grep -c . "${myTMPDIR}/toc") entries"
	if fuHAS "^MANIFEST$";
	  then
	    tar xOf "${myARCHIVE}" MANIFEST | sed 's/^/###### /'
	  else
	    echo "###### $myBLUE""No MANIFEST - this does not look like a backup from update.sh.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	fi
	echo
}

# Ask, unless -y was given
function fuASK () {   # $1 = Frage
	local myANSWER=""
	[ -n "${myCONFIRMED}" ] && return 0
	read -rp "###### $1 (y/n) " myANSWER
	case "${myANSWER}" in
	  y|Y|yes|YES) return 0 ;;
	  *)           return 1 ;;
	esac
}

# What is in there, and which of it should come back?
function fuCHOOSE () {
	echo "### What should be restored?"
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
	    echo "###### $myBLUE""Nothing selected, leaving everything as it is.""$myWHITE"
	    echo
	    exit 0
	fi
}

function fuSTOP_TPOT () {
	echo
	echo -n "### Now stopping T-Pot ... "
	if sudo systemctl stop tpot.service 2>/dev/null;
	  then
	    echo "[ $myGREEN"OK"$myWHITE ]"
	  else
	    echo "[ $myRED""WARNING""$myWHITE ]"
	    echo "###### $myBLUE""No tpot.service, trying docker compose.""$myWHITE"
	    [ -f "${myTPOTDIR}/docker-compose.yml" ] && ( cd "${myTPOTDIR}" && docker compose down ) >/dev/null 2>&1
	fi
}

function fuSTART_TPOT () {
	echo
	echo -n "### Now starting T-Pot ... "
	if sudo systemctl start tpot.service 2>/dev/null;
	  then
	    echo "[ $myGREEN"OK"$myWHITE ]"
	  else
	    echo "[ $myRED""WARNING""$myWHITE ]"
	    echo "###### $myBLUE""Could not start tpot.service, please start T-Pot yourself.""$myWHITE"
	    return 1
	fi
}

# The rollback comes first: a `git reset --hard` puts .env and docker-compose.yml
# back to the state of the commit, so it would overwrite anything done after it.
function fuDO_GIT () {
	local myCOMMIT=""
	[ -z "${myDO_GIT}" ] && return
	echo
	echo "### Rolling the checkout back ..."
	tar xOf "${myARCHIVE}" rollback.txt > "${myTMPDIR}/rollback.txt"
	myCOMMIT=$(tr -d "[:space:]" < "${myTMPDIR}/rollback.txt")
	if [ -z "${myCOMMIT}" ];
	  then
	    echo "###### $myBLUE""rollback.txt is empty, skipping.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	    return
	fi
	if ! git -C "${myTPOTDIR}" cat-file -e "${myCOMMIT}^{commit}" 2>/dev/null;
	  then
	    echo "###### $myBLUE""Commit ${myCOMMIT} is not in ${myTPOTDIR}, skipping.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	    return
	fi
	echo -n "###### $myBLUE Resetting to ${myCOMMIT}.$myWHITE "
	if git -C "${myTPOTDIR}" reset -q --hard "${myCOMMIT}";
	  then
	    echo "[ $myGREEN"OK"$myWHITE ]"
	  else
	    echo " [ $myRED""NOT OK""$myWHITE ]"
	fi
}

function fuDO_CONFIG () {
	local myFILE=""
	[ -z "${myDO_CONFIG}" ] && return
	echo
	echo "### Restoring the configuration ..."
	if tar xf "${myARCHIVE}" -C "${myTMPDIR}" env 2>/dev/null && cp "${myTMPDIR}/env" "${myTPOTDIR}/.env";
	  then
	    echo "###### $myBLUE""Wrote ${myTPOTDIR}/.env.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	  else
	    echo "###### $myBLUE""Could not restore .env.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	fi
	if fuHAS "^docker-compose.yml$";
	  then
	    if tar xf "${myARCHIVE}" -C "${myTMPDIR}" docker-compose.yml 2>/dev/null \
	       && cp "${myTMPDIR}/docker-compose.yml" "${myTPOTDIR}/docker-compose.yml";
	      then
	        echo "###### $myBLUE""Wrote ${myTPOTDIR}/docker-compose.yml ($(head -1 "${myTPOTDIR}/docker-compose.yml")).""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	      else
	        echo "###### $myBLUE""Could not restore docker-compose.yml.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
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
	echo
	echo "### Re-applying your changes to tracked files ..."
	tar xf "${myARCHIVE}" -C "${myTMPDIR}" tracked.patch 2>/dev/null
	if [ ! -s "${myTMPDIR}/tracked.patch" ];
	  then
	    echo "###### $myBLUE""The patch is empty, there was nothing to re-apply.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	    return
	fi
	if git -C "${myTPOTDIR}" apply --check "${myTMPDIR}/tracked.patch" 2>/dev/null;
	  then
	    git -C "${myTPOTDIR}" apply "${myTMPDIR}/tracked.patch" \
	      && echo "###### $myBLUE""Applied.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	    return
	fi
	if git -C "${myTPOTDIR}" apply --3way "${myTMPDIR}/tracked.patch" 2>/dev/null;
	  then
	    echo "###### $myBLUE""Applied with a three-way merge, please review the result.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	    return
	fi
	cp "${myTMPDIR}/tracked.patch" "${myBACKUPDIR}/${myDATE}_tracked.patch"
	echo "###### $myBLUE""The patch does not apply to this tree, most likely because those changes are already in place.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	echo "###### $myBLUE""Left it in ${myBACKUPDIR}/${myDATE}_tracked.patch for you to look at.""$myWHITE"
}

function fuDO_UNTRACKED () {
	[ -z "${myDO_UNTRACKED}" ] && return
	echo
	echo "### Restoring your untracked files ..."
	rm -rf "${myTMPDIR}/untracked"
	if ! tar xf "${myARCHIVE}" -C "${myTMPDIR}" untracked 2>/dev/null;
	  then
	    echo "###### $myBLUE""Could not read them from the archive.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    return
	fi
	if ( cd "${myTMPDIR}/untracked" && tar cf - . ) | ( cd "${myTPOTDIR}" && tar xf - );
	  then
	    echo "###### $myBLUE""Restored $(find "${myTMPDIR}/untracked" -type f | wc -l) files.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	  else
	    echo "###### $myBLUE""Could not write them.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	fi
}

# The files under data/ belong to tpot:tpot (uid/gid 2000) with 0770. Without the
# right modes the containers will not start, so owner and mode come from the archive
# instead of from the caller's umask.
function fuDO_DATA () {
	[ -z "${myDO_DATA}" ] && return
	echo
	echo "### Restoring the files from data/ ..."
	echo -n "###### $myBLUE $(grep -c '^data/' "${myTMPDIR}/toc") entries.$myWHITE "
	if sudo tar xf "${myARCHIVE}" -C "${myTPOTDIR}" -p --numeric-owner --wildcards "data/*" 2>"${myTMPDIR}/data.err";
	  then
	    echo "[ $myGREEN"OK"$myWHITE ]"
	    echo "###### $myBLUE""Owner and mode came from the archive.""$myWHITE"
	  else
	    echo " [ $myRED""NOT OK""$myWHITE ]"
	    sed 's/^/###### /' "${myTMPDIR}/data.err"
	fi
}

# The Elasticsearch import needs a running instance - unlike everything else, which
# wants T-Pot stopped. So it runs last, after the start.
function fuDO_ELASTIC () {
	local myWAIT=0
	local myOBJ=0
	local myKEEP=""
	[ -z "${myDO_ELASTIC}" ] && return
	echo
	echo "### Importing the Elasticsearch state ..."
	rm -rf "${myTMPDIR}/elastic"
	tar xf "${myARCHIVE}" -C "${myTMPDIR}" elastic 2>/dev/null
	echo -n "###### $myBLUE Waiting for Kibana on ${myKIBANA} $myWHITE"
	while [ "${myWAIT}" -lt "${myKIBANA_TIMEOUT}" ];
	  do
	    curl -s -f -o /dev/null --connect-timeout 3 "${myKIBANA}/api/status" && break
	    sleep 5
	    myWAIT=$((myWAIT+5))
	    echo -n "."
	done
	if ! curl -s -f -o /dev/null "${myKIBANA}/api/status";
	  then
	    echo " [ $myRED""NOT OK""$myWHITE ]"
	    # The files sit in the working directory, which is about to vanish - for the
	    # manual route they have to stay, and the commands have to name the real path.
	    myKEEP="${myBACKUPDIR}/${myDATE}_elastic"
	    mkdir -p "${myKEEP}" && cp -a "${myTMPDIR}/elastic/." "${myKEEP}/" 2>/dev/null
	    echo "###### $myBLUE""Kibana did not come up in ${myWAIT}s. The files are in ${myKEEP}, run these once it does:""$myWHITE"
	    echo "######   $myBLUE""curl -X POST '${myKIBANA}/api/saved_objects/_import?overwrite=true' -H 'kbn-xsrf: true' --form file=@${myKEEP}/kibana_export.ndjson""$myWHITE"
	    echo "######   $myBLUE""curl -X PUT '${myES}/_ilm/policy/tpot' -H 'Content-Type: application/json' -d @${myKEEP}/ilm_policy_tpot.json""$myWHITE"
	    return 1
	fi
	echo " [ $myGREEN"OK"$myWHITE ] after ${myWAIT}s"
	if [ -s "${myTMPDIR}/elastic/kibana_export.ndjson" ];
	  then
	    echo -n "###### $myBLUE Importing the Kibana objects.$myWHITE "
	    if curl -s -f -X POST "${myKIBANA}/api/saved_objects/_import?overwrite=true" \
	         -H "kbn-xsrf: true" --form file=@"${myTMPDIR}/elastic/kibana_export.ndjson" \
	         -o "${myTMPDIR}/import.json";
	      then
	        myOBJ=$(sed -n 's/.*"successCount":\([0-9]*\).*/\1/p' "${myTMPDIR}/import.json")
	        if grep -q '"success":true' "${myTMPDIR}/import.json";
	          then
	            echo "[ $myGREEN"OK"$myWHITE ] ${myOBJ:-0} objects"
	          else
	            echo " [ $myRED""WARNING""$myWHITE ] ${myOBJ:-0} objects, errors reported"
	            sed 's/^/###### /' "${myTMPDIR}/import.json" | head -5
	        fi
	      else
	        echo " [ $myRED""NOT OK""$myWHITE ]"
	    fi
	fi
	if [ -s "${myTMPDIR}/elastic/ilm_policy_tpot.json" ];
	  then
	    echo -n "###### $myBLUE Putting the ILM policy back.$myWHITE "
	    # update.sh stored it ready to be put back, so this is a plain PUT against the
	    # Elasticsearch port every edition that ships it publishes on the loopback.
	    if curl -s -f -X PUT "${myES}/_ilm/policy/tpot" \
	         -H "Content-Type: application/json" \
	         -d @"${myTMPDIR}/elastic/ilm_policy_tpot.json" -o /dev/null;
	      then
	        echo "[ $myGREEN"OK"$myWHITE ]"
	      else
	        echo " [ $myRED""WARNING""$myWHITE ]"
	        echo "###### $myBLUE""Could not put the ILM policy back, the file is in the archive under elastic/.""$myWHITE"
	    fi
	fi
}

################
# Main section #
################

while getopts ":lf:yh" opt; do
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
    h|\?)
      fuPRINT_HELP
      ;;
    :)
      echo "Option -${OPTARG} requires an argument."
      fuPRINT_HELP
      ;;
  esac
done

echo "$myRESTORER"

fuTMPDIR
[ -n "${myLIST}" ] && fuLIST

if [ ! -d "${myTPOTDIR}" ];
  then
    echo "###### $myBLUE""There is no ${myTPOTDIR}.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
    echo
    exit 1
fi

fuPICK_ARCHIVE

if [ -n "${myCONFIRMED}" ];
  then
    echo "### Restoring everything from this archive."
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
  else
    echo
    echo "### Done. You can now start T-Pot using 'systemctl start tpot' or 'docker compose up -d'."
fi
echo
