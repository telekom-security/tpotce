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

# Both are published on the loopback interface by every edition that ships them,
# so neither needs a detour through a container.
myKIBANA="http://127.0.0.1:64296"
myES="http://127.0.0.1:64298"

# Working directory, cleaned up when the script ends
myTMPDIR=""
myRED="[0;31m"
myGREEN="[0;32m"
myWHITE="[0;0m"
myBLUE="[0;34m"

# Where to update from. Empty means: keep the branch and the origin of the
# current checkout, which is what a plain `update.sh -y` has always done.
myTPOT_BRANCH="${TPOT_BRANCH}"
myTPOT_REPO_URL="${TPOT_REPO_URL}"
myTPOT_SOURCE_GIVEN=""

# The installed T-Pot edition. It only exists as the marker comment in the first
# line of docker-compose.yml, a tracked file, so the update overwrites it with the
# STANDARD edition. Everything needed to put it back is read before the update and
# handed over to a restarted update.sh through the environment, see fuSELFUPDATE.
myEDITION="${TPOT_UPDATE_EDITION}"
myCOMPOSE_BAK="${TPOT_UPDATE_COMPOSE_BAK}"
myCOMPOSE_CUSTOMIZED="${TPOT_UPDATE_COMPOSE_CUSTOMIZED}"
myEDITIONS="STANDARD SENSOR MINI LLM TARPIT MOBILE MAC_WIN"

myUPDATER=$(cat << "EOF"
 _____     ____       _     _   _           _       _
|_   _|   |  _ \ ___ | |_  | | | |_ __   __| | __ _| |_ ___ _ __
  | |_____| |_) / _ \| __| | | | | '_ \ / _` |/ _` | __/ _ \ '__|
  | |_____|  __/ (_) | |_  | |_| | |_) | (_| | (_| | ||  __/ |
  |_|     |_|   \___/ \__|  \___/| .__/ \__,_|\__,_|\__\___|_|
                                 |_|
EOF
)

function fuPRINT_HELP () {
	cat <<EOF
Usage: $0 -y [-b <branch>] [-r <url>] [--full]

Options:
  -y                Confirm the update, required
  --full            Include the whole data/ folder in the backup. Off by default:
                    the update never touches data/, and on a busy sensor it turns
                    a 1 MB archive into tens of GB. Kept uncompressed either way.
  -b <branch>       Branch to update from, i.e. to test a branch before it is
                    merged. The branch is checked out, so every following
                    update stays on it until another branch is requested.
                    Default: the branch of ~/tpotce, environment: TPOT_BRANCH
  -r <url>          Repository to update from, https URL. Replaces the URL of
                    'origin' in ~/tpotce.
                    Default: the origin of ~/tpotce, environment: TPOT_REPO_URL
  -h                Show this help message
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

# Let's test the internet connection
function fuCHECKINET () {
	mySITES=$1
	  echo
	  echo "### Now checking availability of ..."
	  for i in $mySITES;
	    do
	      echo -n "###### $myBLUE$i$myWHITE "
	      curl --connect-timeout 5 -IsS $i >/dev/null 2>&1
	        if [ $? -ne 0 ];
	          then
		    echo
	            echo "###### $myBLUE""Error - Internet connection test failed.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	            echo "Exiting.""$myWHITE"
	            echo
	            exit 1
	          else
	            echo "[ $myGREEN"OK"$myWHITE ]"
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
	    echo "###### $myBLUE""Could not create a temporary directory.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	fi
	trap 'rm -rf "${myTMPDIR}"' EXIT
}

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
	echo "###### $myBLUE""Removing ${myVictim} ($(du -h "${myVictim}" | cut -f1)) to make room.""$myWHITE"
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
	    echo "###### $myBLUE""Keeping the last ${myKeep} ${myKind} backups, removing ${myVictim} ($(du -h "${myVictim}" | cut -f1)).""$myWHITE"
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
	echo "### Checking the space for the backup ..."
	if ! mkdir -p "${myBACKUPDIR}" || ! chmod 0700 "${myBACKUPDIR}";
	  then
	    echo "###### $myBLUE""Could not prepare ${myBACKUPDIR}.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	fi
	myTOTAL=$(df -B1 --output=size "${myBACKUPDIR}" 2>/dev/null | tail -1 | tr -d " ")
	myRESERVE=$(( myTOTAL / 100 * myBACKUP_RESERVE_PERCENT ))
	while :;
	  do
	    myNEED=$(fuBACKUP_SIZE)
	    myFREE=$(df -B1 --output=avail "${myBACKUPDIR}" 2>/dev/null | tail -1 | tr -d " ")
	    echo "###### $myBLUE""Archive needs about $((myNEED / 1048576)) MB, $((myFREE / 1048576)) MB free, keeping $((myRESERVE / 1048576)) MB in reserve.""$myWHITE"
	    if [ $((myFREE - myNEED)) -ge "${myRESERVE}" ];
	      then
	        echo "###### $myBLUE""Enough room.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	        echo
	        return
	    fi
	    if fuDROP_OLDEST;
	      then
	        continue
	    fi
	    # Nothing left to free. A full backup is dispensable, the regular one is not.
	    if [ -n "${myFULL}" ];
	      then
	        echo "###### $myBLUE""Not enough room for a full backup, falling back to the regular one.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	        myFULL=""
	        continue
	    fi
	    echo "###### $myBLUE""Not enough room for a backup and T-Pot needs the disk.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "###### $myBLUE""Free up space in ${myBACKUPDIR} or on the filesystem, T-Pot was left running.""$myWHITE"
	    echo "Exiting.""$myWHITE"
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
	echo "### Checking the update source ..."
	myCURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
	myCURRENT_REPO=$(fuNORMALIZE_REPO "$(git remote get-url origin 2>/dev/null)")
	[ -z "${myTPOT_BRANCH}" ] && myTPOT_BRANCH="${myCURRENT_BRANCH}"
	[ -z "${myTPOT_REPO_URL}" ] && myTPOT_REPO_URL="${myCURRENT_REPO}"
	myTPOT_REPO_URL=$(fuNORMALIZE_REPO "${myTPOT_REPO_URL}")
	echo "###### $myBLUE${myTPOT_REPO_URL} at ${myTPOT_BRANCH}$myWHITE"
	if [ -z "${myTPOT_SOURCE_GIVEN}" ];
	  then
	    echo
	    return
	fi
	# A typo in a branch name must not take T-Pot down, so the requested source
	# is checked before anything is stopped or overwritten. `ls-remote` asks the
	# repository itself and leaves the local checkout alone.
	if [ -z "${myTPOT_BRANCH}" ] || [ "${myTPOT_BRANCH}" == "HEAD" ];
	  then
	    echo "###### $myBLUE""Unable to determine the current branch, please name one with '-b'.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	fi
	if ! git ls-remote --heads "${myTPOT_REPO_URL}" "refs/heads/${myTPOT_BRANCH}" 2>/dev/null | grep -q .;
	  then
	    echo "###### $myBLUE""Branch ${myTPOT_BRANCH} does not exist in ${myTPOT_REPO_URL}.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	  else
	    echo "###### $myBLUE""Repository and branch are available.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	fi
	echo
}

# The template of an edition, i.e. STANDARD -> compose/standard.yml
function fuEDITION_TEMPLATE () {
	echo "$HOME/tpotce/compose/$(echo "${myEDITION}" | tr '[:upper:]' '[:lower:]').yml"
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
	echo "### Checking the installed T-Pot edition ..."
	# A restarted update.sh inherits the result of the first run, the compose file
	# has already been reset by then and cannot be read again.
	if [ -n "${myEDITION}" ];
	  then
	    echo "###### $myBLUE""Edition ${myEDITION} was detected before the restart.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	    echo
	    return
	fi
	# Only the tracked ./docker-compose.yml is overwritten by the update, a compose
	# file under a different name is untracked and stays as it is.
	myCOMPOSE=$(grep -E "^TPOT_DOCKER_COMPOSE=" "$HOME/tpotce/.env" 2>/dev/null | tail -1 | cut -d "=" -f2-)
	myCOMPOSE="${myCOMPOSE:-./docker-compose.yml}"
	if [ "$(basename "${myCOMPOSE}")" != "docker-compose.yml" ];
	  then
	    echo "###### $myBLUE""TPOT_DOCKER_COMPOSE points to ${myCOMPOSE}, which the update leaves alone.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	    echo
	    return
	fi
	if [ ! -f "$HOME/tpotce/docker-compose.yml" ];
	  then
	    echo "###### $myBLUE""There is no docker-compose.yml, so there is no edition to remember.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	    echo
	    return
	fi
	myEDITION=$(head -1 "$HOME/tpotce/docker-compose.yml" | sed -n 's/^# T-Pot: *//p')
	# Only an edition that ships a template in compose/ can be restored from one, a
	# file built with compose/customizer.py has none.
	for i in ${myEDITIONS};
	  do
	    [ "${myEDITION}" == "$i" ] && myKNOWN="1"
	  done;
	[ -n "${myKNOWN}" ] || myEDITION="UNKNOWN"
	# The only source for the edition once the update has overwritten the file, and
	# for the changes of a user who edited it. Kept outside of the checkout so that
	# `git reset --hard` cannot touch it, next to the backup archives.
	mkdir -p "${myBACKUPDIR}" && chmod 0700 "${myBACKUPDIR}"
	myCOMPOSE_BAK="${myBACKUPDIR}/${myDATE}_docker-compose.yml"
	if ! cp "$HOME/tpotce/docker-compose.yml" "${myCOMPOSE_BAK}";
	  then
	    echo "###### $myBLUE""Could not save docker-compose.yml to ${myCOMPOSE_BAK}.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	fi
	if [ "${myEDITION}" == "UNKNOWN" ];
	  then
	    echo "###### $myBLUE""Unable to determine the edition, docker-compose.yml is restored as it is.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	  else
	    # A compose file that differs from its own template was edited by the user.
	    # The update cannot merge that, so it has to be handed back manually.
	    if ! cmp -s "$HOME/tpotce/docker-compose.yml" "$(fuEDITION_TEMPLATE)";
	      then
	        myCOMPOSE_CUSTOMIZED="1"
	    fi
	    echo "###### $myBLUE""Edition ${myEDITION}${myCOMPOSE_CUSTOMIZED:+ (customized)}.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
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
	    echo "###### $myBLUE""Now switching origin from ${myCURRENT_REPO} to ${myTPOT_REPO_URL}.""$myWHITE"
	    git remote set-url origin "${myTPOT_REPO_URL}"
	    mySWITCH="1"
	fi
	if [ "${myTPOT_BRANCH}" != "${myCURRENT_BRANCH}" ];
	  then
	    echo "###### $myBLUE""Now switching from branch ${myCURRENT_BRANCH} to ${myTPOT_BRANCH}.""$myWHITE"
	    mySWITCH="1"
	fi
	[ -n "${mySWITCH}" ] || return
	git fetch origin --prune
	git reset --hard
	if ! git checkout -B "${myTPOT_BRANCH}" "origin/${myTPOT_BRANCH}";
	  then
	    echo "###### $myBLUE""Could not check out ${myTPOT_BRANCH}.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
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
	echo "### Now checking for newer files in repository ..."
	# The running script may be replaced by the update, either by newer commits
	# or by a switch to a different branch, and then has to restart itself.
	myOLDSUM=$(sha256sum "$0" | awk '{ print $1 }')
	fuSWITCH_SOURCE
	echo "###### $myBLUE""Pulling updates from repository.""$myWHITE"
	git fetch --all
	git reset --hard
	git pull --force
	if [ "${myOLDSUM}" != "$(sha256sum "$0" | awk '{ print $1 }')" ];
	  then
	    echo "###### $myBLUE""Found newer version of update.sh, restarting myself.""$myWHITE"
	    # The edition was read from a file the pull above has just overwritten, so
	    # the restarted script cannot read it again and inherits it instead.
	    export TPOT_UPDATE_EDITION="${myEDITION}"
	    export TPOT_UPDATE_COMPOSE_BAK="${myCOMPOSE_BAK}"
	    export TPOT_UPDATE_COMPOSE_CUSTOMIZED="${myCOMPOSE_CUSTOMIZED}"
	    # an `exec` does not fire the EXIT trap, so clean up here
	    [ -n "${myTMPDIR}" ] && rm -rf "${myTMPDIR}"
	    # `-y` is repeated on purpose: after a switch to an older branch the
	    # restarted script is that branch's update.sh, which only looks at `$1`
	    # for the confirmation and would just print its usage otherwise
	    exec bash "$0" -y "$@"
	    exit 1
	fi
	echo
}

function fuCHECK_VERSION () {
	local myMINVERSION="24.04.0"
	local myMASTERVERSION="24.04.1"
	echo
	echo "### Checking for version tag ..."
	if [ -f "version" ];
	  then
	    myVERSION=$(cat version)
	    if [[ "$myVERSION" > "$myMINVERSION" || "$myVERSION" == "$myMINVERSION" ]] && [[ "$myVERSION" < "$myMASTERVERSION" || "$myVERSION" == "$myMASTERVERSION" ]]
	      then
	        echo "###### $myBLUE$myVERSION is eligible for the update procedure.$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	      elif [ -n "${myTPOT_SOURCE_GIVEN}" ];
	        then
	          # A branch or a fork may have moved the version tag on already,
	          # that must not stop a test of the update procedure itself.
	          echo "###### $myBLUE $myVERSION is outside the supported range, continuing because an update source was requested.$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	      else
	        echo "###### $myBLUE $myVERSION cannot be upgraded automatically. Please run a fresh install.$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
		exit
	    fi
	  else
	    echo "###### $myBLUE""Unable to determine version. Please run 'update.sh' from within 'tpotce/'.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    exit
	  fi
	echo
}

# Stop T-Pot to avoid race conditions with running containers with regard to the current T-Pot config
function fuSTOP_TPOT () {
	echo
	echo "### Need to stop T-Pot ..."
	echo -n "###### $myBLUE Now stopping T-Pot.$myWHITE "
	sudo systemctl stop tpot.service
	if [ $? -ne 0 ];
	  then
	    echo " [ $myRED""NOT OK""$myWHITE ]"
	    echo "###### $myBLUE""Could not stop T-Pot.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	  else
	    echo "[ $myGREEN"OK"$myWHITE ]"
	    echo -n "###### $myBLUE Now cleaning up containers.$myWHITE "
	    if [ "$(docker ps -aq)" != "" ];
	      then
	        docker stop $(docker ps -aq)
	        docker container prune -f && docker image prune -f && docker volume prune -f
	    fi
	    echo "[ $myGREEN"OK"$myWHITE ]"
	fi
	echo
}

# Save the state that lives in Elasticsearch rather than in a file: the user's own
# Kibana objects and the ILM policy the retention hangs on. Runs before fuSTOP_TPOT
# because both need a running instance. The README used to ask the user to export
# this by hand, and update.sh gave that hint at the end of the run - too late to
# act on.
function fuEXPORT_ELASTIC () {
	local myOUT=""
	echo
	echo "### Saving the Elasticsearch state ..."
	fuTMPDIR
	myOUT="${myTMPDIR}/stage/elastic"
	mkdir -p "${myOUT}"
	# Two independent probes rather than one: a SENSOR runs neither service, MOBILE
	# runs Elasticsearch without Kibana.
	if curl -s -f -o /dev/null --connect-timeout 5 "${myKIBANA}/api/status";
	  then
	    echo -n "###### $myBLUE Exporting Kibana objects.$myWHITE "
	    if curl -s -f -X POST "${myKIBANA}/api/saved_objects/_export" \
	         -H "kbn-xsrf: true" -H "Content-Type: application/json" \
	         -d '{"type":"*","excludeExportDetails":true}' \
	         -o "${myOUT}/kibana_export.ndjson";
	      then
	        echo "[ $myGREEN"OK"$myWHITE ] $(grep -c . "${myOUT}/kibana_export.ndjson") objects"
	      else
	        echo " [ $myRED""WARNING""$myWHITE ]"
	        rm -f "${myOUT}/kibana_export.ndjson"
	    fi
	  else
	    echo "###### $myBLUE""Kibana is not available on ${myKIBANA}, skipping its objects.""$myWHITE"
	fi
	# The ILM policy is not a saved object, it comes from Elasticsearch itself. What
	# GET returns is wrapped in the policy name, which PUT rejects, so it is stored
	# ready to be put back - restoring it is then a single curl.
	if curl -s -f -o /dev/null --connect-timeout 5 "${myES}";
	  then
	    echo -n "###### $myBLUE Exporting the ILM policy.$myWHITE "
	    if curl -s -f "${myES}/_ilm/policy/tpot" -o "${myTMPDIR}/ilm_raw.json" \
	       && python3 -c "
import json
myRAW = json.load(open('${myTMPDIR}/ilm_raw.json'))
json.dump({'policy': myRAW['tpot']['policy']}, open('${myOUT}/ilm_policy_tpot.json', 'w'), indent=2)
" 2>/dev/null;
	      then
	        echo "[ $myGREEN"OK"$myWHITE ]"
	      else
	        echo " [ $myRED""WARNING""$myWHITE ]"
	        rm -f "${myOUT}/ilm_policy_tpot.json"
	    fi
	  else
	    echo "###### $myBLUE""Elasticsearch is not available on ${myES}, skipping the ILM policy.""$myWHITE"
	fi
	if [ -z "$(ls -A "${myOUT}" 2>/dev/null)" ];
	  then
	    rmdir "${myOUT}" 2>/dev/null
	  else
	    echo "###### $myBLUE""Saved to the archive under 'elastic/'.""$myWHITE"
	fi
	echo
}

# Backup
#
# Only what cannot be restored otherwise goes in. Everything tracked comes back
# from git and an update never touches `data/`, so what is irreplaceable are the
# user's own changes, the configuration and a handful of files under `data/`. Hence
# a list instead of a glob, and hence no compression: the archive is small enough
# that compressing it would only cost time.
function fuBACKUP () {
	local myStage=""
	local myTARGETS=""
	local myJEWEL=""
	local myFILE=""
	local myJEWELLIST=()
	local myJEWELARGS=()
	echo
	echo "### Create a backup, just in case ... "
	fuTMPDIR
	if ! mkdir -p "${myBACKUPDIR}" || ! chmod 0700 "${myBACKUPDIR}";
	  then
	    echo "###### $myBLUE""Could not prepare ${myBACKUPDIR}.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
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

	echo -n "###### $myBLUE Building ${myFULL:+full }archive in $myARCHIVE $myWHITE"
	# A single tar run: intermediate files created under sudo belong to root and
	# could not be moved afterwards.
	if ! sudo tar cf "${myARCHIVE}" -p --numeric-owner \
	        -C "${myStage}" ${myTARGETS} "${myJEWELARGS[@]}" 2>"${myTMPDIR}/tar.err";
	  then
	    echo " [ $myRED""NOT OK""$myWHITE ]"
	    echo "###### $myBLUE""tar failed:""$myWHITE"
	    sed 's/^/###### /' "${myTMPDIR}/tar.err"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	fi
	if ! sudo chown "$(id -u):$(id -g)" "${myARCHIVE}" || ! chmod 0600 "${myARCHIVE}";
	  then
	    echo " [ $myRED""NOT OK""$myWHITE ]"
	    echo "###### $myBLUE""Could not take ownership of ${myARCHIVE}.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	fi
	echo "[ $myGREEN"OK"$myWHITE ]"
	echo "###### $myBLUE""Archive holds $(tar tf "${myARCHIVE}" | wc -l) entries, $(du -h "${myARCHIVE}" | cut -f1).""$myWHITE"
	fuROTATE
	# Point at the old archives from before this directory existed, once
	if ls "$HOME"/*_tpot_backup.tgz >/dev/null 2>&1;
	  then
	    echo "###### $myBLUE""Note: older backups are still in $HOME, new ones go to ${myBACKUPDIR}.""$myWHITE"
	fi
	echo
}

# Remove old images for specific tag
function fuREMOVEOLDIMAGES () {
	local myOLDTAG=$1
    echo "### Removing old docker images."
    docker rmi $(docker images -q "$myOLDTAG") >/dev/null 2>&1
}

function fuPULLIMAGES {
	docker compose -f ~/tpotce/docker-compose.yml pull
}

function fuUPDATER () {
	echo "### Now pulling latest docker images ..."
	echo "######$myBLUE This might take a while, please be patient!$myWHITE"
	fuPULLIMAGES
	fuREMOVEOLDIMAGES "dtagdevsec/*:dev"
	fuREMOVEOLDIMAGES "ghcr.io/telekom-security/*:dev"
	fuREMOVEOLDIMAGES "dtagdevsec/*:24.04"
	fuREMOVEOLDIMAGES "ghcr.io/telekom-security/*:24.04"
	echo
	if [ -n "${myCOMPOSE_CUSTOMIZED}" ];
	  then
	    echo "### If you made changes to docker-compose.yml please ensure to add them again."
	    echo "### We stored your previous docker-compose.yml in $myCOMPOSE_BAK."
	fi
	echo "### We stored the previous version as backup in $myARCHIVE."
	echo "### Your own Kibana objects and the ILM policy were saved to the archive before"
	echo "### T-Pot was stopped, 'restore.sh' can put them back."
	echo "### Some updates ship newer Kibana objects. Download them here if they changed:"
	echo "### https://raw.githubusercontent.com/telekom-security/tpotce/refs/heads/master/docker/tpotinit/dist/etc/objects/kibana_export.ndjson.zip"
	echo "### Import through the Kibana WebUI: Management > Saved Objects > Import"
	echo
}

function fuRESTORE () {
	if [ -f '~/tpotce/data/ews/conf/ews.cfg' ] && ! grep 'ews.cfg' $myCOMPOSEFILE > /dev/null; then
	    echo
	    echo "### Restoring volume mount for ews.cfg in tpot.yml"
	    sed -i '/- ${TPOT_DATA_PATH}:\/data/a \ \ \ \ \ - ${TPOT_DATA_PATH}/ews/conf/ews.cfg:/opt/ewsposter/ews.cfg' $myCOMPOSEFILE
	fi
	echo "### Restoring T-Pot config file .env"
	fuTMPDIR
	# `-C` only applies to the members named after it, hence it comes first. And the
	# return value is checked: a restore that failed silently left T-Pot starting
	# without a web login while the run still reported "Done".
	if ! tar xf "${myARCHIVE}" -C "${myTMPDIR}" env 2>"${myTMPDIR}/untar.err";
	  then
	    echo "###### $myBLUE""Could not read 'env' from ${myARCHIVE}.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    sed 's/^/###### /' "${myTMPDIR}/untar.err"
	    echo "###### $myBLUE""Refusing to continue with a default configuration.""$myWHITE"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	fi
	if ! cp "${myTMPDIR}/env" "$HOME/tpotce/.env";
	  then
	    echo "###### $myBLUE""Could not write $HOME/tpotce/.env.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	    echo "Exiting.""$myWHITE"
	    echo
	    exit 1
	fi
	echo "###### $myBLUE""Restored from ${myARCHIVE}.""$myWHITE"" [ $myGREEN""OK""$myWHITE ]"
	# Backup file (.env) contains a record of the TPOT_VERSION that is used in docker-compose commmands. 
	# We should upgrade the version in this file after restoring the backup.
	newVERSION=$(cat version)
	sed -i "s/^TPOT_VERSION=.*/TPOT_VERSION=${newVERSION}/" $HOME/tpotce/.env
}

# Put the edition back that fuCHECK_EDITION found, using this release's template so
# that the update brings its new service definitions along. Has to run before the
# images are pulled, as every edition needs a different set of them.
function fuRESTORE_EDITION () {
	local myTEMPLATE=""
	echo
	echo "### Restoring the T-Pot edition ..."
	if [ -z "${myEDITION}" ];
	  then
	    echo "###### $myBLUE""No edition was detected, nothing to restore.""$myWHITE"
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
	        echo "###### $myBLUE""TPOT_TYPE is SENSOR, restoring the SENSOR edition instead of ${myEDITION}.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	        myEDITION="SENSOR"
	    fi
	fi
	[ "${myEDITION}" != "UNKNOWN" ] && myTEMPLATE=$(fuEDITION_TEMPLATE)
	if [ -n "${myTEMPLATE}" ] && [ -f "${myTEMPLATE}" ];
	  then
	    echo -n "###### $myBLUE Now restoring the ${myEDITION} edition.$myWHITE "
	    if ! cp "${myTEMPLATE}" "$HOME/tpotce/docker-compose.yml";
	      then
	        echo " [ $myRED""NOT OK""$myWHITE ]"
	        echo "###### $myBLUE""Please copy ${myTEMPLATE} to ~/tpotce/docker-compose.yml manually.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	        echo
	        return
	    fi
	    echo "[ $myGREEN"OK"$myWHITE ]"
	    if [ -n "${myCOMPOSE_CUSTOMIZED}" ];
	      then
	        echo "###### $myBLUE""Your docker-compose.yml had been modified. The ${myEDITION} edition of this release is in place now, please add your changes again.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	        echo "###### $myBLUE""Compare with: diff ${myCOMPOSE_BAK} $HOME/tpotce/docker-compose.yml""$myWHITE"
	    fi
	  else
	    # Without a template the file the user had is put back unchanged, which
	    # keeps it on the service definitions of the previous release.
	    echo -n "###### $myBLUE Now restoring your own docker-compose.yml.$myWHITE "
	    if ! cp "${myCOMPOSE_BAK}" "$HOME/tpotce/docker-compose.yml";
	      then
	        echo " [ $myRED""NOT OK""$myWHITE ]"
	        echo "###### $myBLUE""Please copy ${myCOMPOSE_BAK} to ~/tpotce/docker-compose.yml manually.""$myWHITE"" [ $myRED""NOT OK""$myWHITE ]"
	        echo
	        return
	    fi
	    echo "[ $myGREEN"OK"$myWHITE ]"
	    echo "###### $myBLUE""Your docker-compose.yml does not match any edition in compose/, so the changes of this release were not applied to it.""$myWHITE"" [ $myRED""WARNING""$myWHITE ]"
	fi
	echo
}

################
# Main section #
################

# getopts has no long options, so `--full` is translated before they are read
myARGV=()
for myARG in "$@";
  do
    case "${myARG}" in
      --full) myARGV+=("-F") ;;
      *)      myARGV+=("${myARG}") ;;
    esac
done
set -- "${myARGV[@]}"

while getopts ":yFb:r:h" opt; do
  case "$opt" in
    y)
      myCONFIRMED="y"
      ;;
    F)
      myFULL="1"
      ;;
    b)
      myTPOT_BRANCH="${OPTARG}"
      ;;
    r)
      myTPOT_REPO_URL="${OPTARG}"
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

# -b, -r, TPOT_BRANCH and TPOT_REPO_URL all name an update source explicitly
[ -n "${myTPOT_BRANCH}${myTPOT_REPO_URL}" ] && myTPOT_SOURCE_GIVEN="1"

# Only run with command switch
sudo echo "$myUPDATER"

if [ "${myCONFIRMED}" != "y" ]; then
  echo
  echo "This script will update T-Pot to the latest version."
  echo "A backup of ~/tpotce will be written to $HOME. If you are unsure, you should save your work."
  echo "This tool might break things and therefore only recommended for experienced users."
  echo "If you understand the involved risks feel free to run this script with the '-y' switch."
  echo
  exit
fi

fuCHECK_VERSION
fuCHECKINET "https://index.docker.io https://github.com"
fuCHECK_SOURCE
fuCHECK_EDITION
fuCHECK_BACKUP_SPACE
# The Elasticsearch export needs a running instance, so it goes before the stop
fuEXPORT_ELASTIC
fuSTOP_TPOT
fuBACKUP
fuSELFUPDATE "$@"
# The config and the edition have to be back in place before the images are
# pulled, `docker compose pull` reads both.
fuRESTORE
fuRESTORE_EDITION
fuUPDATER

echo
if [ -n "${myEDITION}" ] && [ "${myEDITION}" != "UNKNOWN" ];
  then
    echo "### The T-Pot ${myEDITION} edition was restored to ~/tpotce/docker-compose.yml."
fi
echo "### Done. You can now start T-Pot using 'systemctl start tpot' or 'docker compose up -d'."
echo
