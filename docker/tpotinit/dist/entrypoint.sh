#!/usr/bin/env bash

COMPOSE="/tmp/tpot/docker-compose.yml"
exec > >(tee /data/tpotinit.log) 2>&1

# Function to handle SIGTERM
cleanup() {
  echo "# SIGTERM received, cleaning up ..."
  echo
  if [ "${TPOT_OSTYPE}" = "linux" ];
    then
      echo "## ... removing firewall rules."
      /opt/tpot/bin/rules.sh ${COMPOSE} unset
      echo
      if [ "${TPOT_BLACKHOLE}" == "ENABLED" ] && [ -f "/etc/blackhole/mass_scanner.txt" ];
        then
          echo "## ... removing Blackhole routes."
          /opt/tpot/bin/blackhole.sh del
          echo
      fi
  fi
  # autoheal, es_retention.sh and kibana_objects.sh, once they run; a PUT of the ILM policy in flight
  # may finish, the Kibana objects are checked again on the next start
  [ -n "${PID}" ] && kill -TERM "${PID}" 2>/dev/null
  if [ -n "${myILM_PID}" ];
    then
      kill -TERM "${myILM_PID}" 2>/dev/null
      wait "${myILM_PID}" 2>/dev/null
  fi
  if [ -n "${myKIBANA_PID}" ];
    then
      kill -TERM "${myKIBANA_PID}" 2>/dev/null
      wait "${myKIBANA_PID}" 2>/dev/null
  fi
  rm -f /tmp/success
  echo "# Cleanup done."
  echo
  # the waits below are interruptible (sleep & wait), the script ends here instead of going on
  exit 0
}
PID=""
myILM_PID=""
myKIBANA_PID=""
trap cleanup SIGTERM

# The .env validation (rules in /opt/tpot/etc/env.schema.yml) collects all errors
# and aborts once, after all checks
source /opt/tpot/bin/env_validate.sh

# A passwd file of nginx from its .env key, in place: the files are bind mounted on their own into
# nginx, a new file (rename) would not reach it. The .env names who has access; a line of the old
# file whose user it no longer names is kept in <file>.lost first (a line that is there already is not
# added again), the T-Pot Manager takes it back from there (tpot users import / tpot sensors import).
# If a line cannot be kept (.lost is a link or no regular file, a write fails), the file stays as it
# is and the function ends with 1: the start stops, nothing is dropped and nothing gets access.
# $1 = file, $2 = the entries (base64 of name:hash), $3 = web / sensor, $4 = the key, $5 = the command
# that takes them back, $6 = keep (yes / no)
write_passwd_file() {
    local myFILE="$1" myLOST="$1.lost" myENTRY myLINE myNAMES=$'\n' myNEW="" myREASON="" myOUT
    # kept / left: users import can take back (their hash is one myENV_HTPASSWD of env_validate.sh
    # takes), only: lines in another format, which stay in .lost and cannot come back
    local myKEPT=0 myKEPT_ONLY=0 myLEFT=0 myLEFT_ONLY=0 myLEFT_NAMES=$'\n'
    local -a myDROP=()
    for myENTRY in $2;
      do
        if [[ -n "${myENTRY}" ]];
          then
            # Need to control newlines as they kept coming up for some reason
            myLINE="$(echo -n "${myENTRY}" | base64 -d -w0 | tr -d '\n')"
            myNEW+="${myLINE}"$'\n'
            myNAMES+="${myLINE%%:*}"$'\n'
        fi
    done
    if [[ "$6" == "yes" && -s "${myFILE}" ]];
      then
        # the lines the new content drops
        while IFS= read -r myLINE || [[ -n "${myLINE}" ]];
          do
            myLINE="${myLINE%$'\r'}"
            [[ -n "${myLINE//[[:space:]]/}" ]] || continue
            [[ "${myNAMES}" == *$'\n'"${myLINE%%:*}"$'\n'* ]] && continue
            myDROP+=("${myLINE}")
        done < "${myFILE}"
    fi
    if [[ "${#myDROP[@]}" -gt 0 ]];
      then
        if [[ -L "${myLOST}" ]];
          then
            myREASON="it is a link"
        elif [[ -e "${myLOST}" && ! -f "${myLOST}" ]];
          then
            myREASON="it is not a regular file"
        elif [[ ! -e "${myLOST}" ]];
          then
            if myOUT="$( { : > "${myLOST}"; } 2>&1 )";
              then
                chmod 660 "${myLOST}"
              else
                myREASON="${myOUT##*: }"
                myREASON="${myREASON:-the write failed}"
            fi
        fi
        for myLINE in "${myDROP[@]}";
          do
            [[ -z "${myREASON}" ]] || break
            # a line that is there already (or came twice) is not added again
            grep -Fxq -- "${myLINE}" "${myLOST}" && continue
            if myOUT="$( { printf '%s\n' "${myLINE}" >> "${myLOST}"; } 2>&1 )";
              then
                if [[ "${myLINE}" =~ ${myENV_HTPASSWD} ]];
                  then
                    myKEPT=$((myKEPT + 1))
                  else
                    myKEPT_ONLY=$((myKEPT_ONLY + 1))
                fi
              else
                myREASON="${myOUT##*: }"
                myREASON="${myREASON:-the write failed}"
            fi
        done
        if [[ -n "${myREASON}" ]];
          then
            echo
            echo "# Error: the lines of ${myFILE} that $4 does not name cannot be kept in ${myLOST} (${myREASON}), ${myFILE##*/} stays as it is."
            echo
            return 1
        fi
    fi
    # truncated and written, the inode stays
    printf '%s' "${myNEW}" > "${myFILE}"
    if [[ $((myKEPT + myKEPT_ONLY)) -gt 0 ]];
      then
        echo
        [[ "${myKEPT}" -gt 0 ]] \
          && echo "# Warning: ${myKEPT} $3 user(s) of ${myFILE##*/} are not in $4 and lose access, they are kept in ${myLOST##*/}. Take them back with: $5"
        [[ "${myKEPT_ONLY}" -gt 0 ]] \
          && echo "# Warning: ${myKEPT_ONLY} line(s) of ${myFILE##*/} that $4 does not name lose access, their hash is in a format T-Pot does not take, so they are only kept in ${myLOST##*/} and cannot be taken back (remove them there to drop this note)."
        echo
    elif [[ "$6" == "yes" && -f "${myLOST}" && -s "${myLOST}" && ! -L "${myLOST}" ]];
      then
        while IFS= read -r myLINE || [[ -n "${myLINE}" ]];
          do
            myLINE="${myLINE%$'\r'}"
            [[ -n "${myLINE//[[:space:]]/}" ]] || continue
            [[ "${myNAMES}" == *$'\n'"${myLINE%%:*}"$'\n'* ]] && continue
            if [[ "${myLINE}" =~ ${myENV_HTPASSWD} ]];
              then
                # a name counts once, import takes its last line
                [[ "${myLEFT_NAMES}" == *$'\n'"${myLINE%%:*}"$'\n'* ]] && continue
                myLEFT_NAMES+="${myLINE%%:*}"$'\n'
                myLEFT=$((myLEFT + 1))
              else
                myLEFT_ONLY=$((myLEFT_ONLY + 1))
            fi
        done < "${myLOST}"
        if [[ "${myLEFT}" -gt 0 ]];
          then
            echo "# ${myLOST##*/} holds ${myLEFT} $3 user(s) $4 does not name, take them back with: $5 (to drop them, remove their lines or the whole file ${myLOST##*/})"
        fi
        if [[ "${myLEFT_ONLY}" -gt 0 ]];
          then
            echo "# ${myLOST##*/} keeps ${myLEFT_ONLY} line(s) with a hash in a format T-Pot does not take, they cannot be taken back (remove them there to drop this note)"
        fi
    fi
    return 0
}

# $1 = the data folder (/data)
create_web_users() {
    local myDATA="${1:-/data}" myKEEP="yes"
    echo
    echo "# Creating passwd files based on T-Pot .env config ..."
    # A SENSOR has no nginx (WEB_USER is emptied for it above, LS_WEB_USER is a key of the HIVE): its
    # files are written as before, nothing is kept and nothing is said
    if [ "${TPOT_TYPE}" == "SENSOR" ];
      then
        myKEEP="no"
    fi
    write_passwd_file "${myDATA}/nginx/conf/nginxpasswd" "${WEB_USER}" web WEB_USER "tpot users import" "${myKEEP}" \
      && write_passwd_file "${myDATA}/nginx/conf/lswebpasswd" "${LS_WEB_USER}" sensor LS_WEB_USER "tpot sensors import" "${myKEEP}"
}

# Before the abort of a HIVE without WEB_USER: the lines nginxpasswd still holds (the file stays as it
# is). $1 = the data folder (/data)
web_users_hint() {
    local myFILE="${1:-/data}/nginx/conf/nginxpasswd" myCOUNT
    [ -f "${myFILE}" ] || return 0
    myCOUNT="$(grep -c '[^[:space:]]' "${myFILE}")"
    if [ "${myCOUNT:-0}" -gt 0 ];
      then
        echo "# ${myFILE} still holds ${myCOUNT} line(s), the file stays as it is; the users among them T-Pot accepts are taken over with: tpot users import"
    fi
}

# The hold of ewsposter (hold, hold.ack) and the lock of the checks (check.lock) in <data>/ews
# end with every start, so a check that was cut off never keeps ewsposter quiet. The folder is
# reached by its descriptor, and a name in it is removed, never followed; a folder of that name stays.
clear_ews_hold() {
    python3 - "${1:-/data}" <<'PY'
import fcntl
import os
import stat
import sys

data = sys.argv[1]
try:
    data_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY)
except OSError:
    sys.exit(0)
try:
    ews_fd = os.open("ews", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data_fd)
except FileNotFoundError:
    sys.exit(0)
except OSError as error:
    if os.path.islink(os.path.join(data, "ews")):
        print(f"# Warning: {data}/ews is a link, the hold of ewsposter is not cleared there.")
    else:
        print(f"# Warning: {data}/ews could not be opened ({error.strerror}), the hold of ewsposter is not cleared.")
    sys.exit(0)
for name in ("hold", "hold.ack", "check.lock", "quarantine", "catchup"):
    try:
        info = os.stat(name, dir_fd=ews_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            print(f"# Warning: {data}/ews/{name} is a folder, it stays.")
            continue
        if name == "check.lock" and stat.S_ISREG(info.st_mode):
            # a check that runs holds its flock: its lock stays (it stops by itself once the
            # marker is gone)
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=ews_fd)
            try:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    print(f"# {data}/ews/{name} is held by a running check, it stays.")
                    continue
                os.unlink(name, dir_fd=ews_fd)
            finally:
                os.close(handle)
            print(f"# Removed {data}/ews/{name}.")
            continue
        os.unlink(name, dir_fd=ews_fd)
        print(f"# Removed {data}/ews/{name}.")
    except FileNotFoundError:
        continue
    except OSError as error:
        print(f"# Warning: {data}/ews/{name} could not be removed ({error.strerror}).")
PY
}

# Folders 770, files 660 (no execute bit), nginx conf and cert 774 / 664; links are not followed
. /opt/tpot/bin/data_modes.sh

update_permissions() {
	echo
	echo "# Updating permissions ..."
	echo
	chown -R tpot:tpot /data
	fuDATA_MODES /data
	fuDATA_WEB_MODES /data/nginx/conf /data/nginx/cert
}

# Update permissions
update_permissions

# Check for compatible OSType
echo
echo "# Checking if OSType is set correctly."
echo
myOSTYPE=$(uname -a | grep -Eo "microsoft|linuxkit")
if [ "${myOSTYPE}" == "microsoft" ] && [ "${TPOT_OSTYPE}" != "win" ];
  then
    echo "# Docker Desktop for Windows detected, but TPOT_OSTYPE is not set to win."
    echo "# 1. You need to adjust the OSType in the T-Pot .env config."
    echo "# 2. You need to copy compose/mac_win.yml to ./docker-compose.yml."
    echo
    echo "# Aborting."
    echo
    sleep 1
    exit 1
fi

if [ "${myOSTYPE}" == "linuxkit" ] && [ "${TPOT_OSTYPE}" != "mac" ];
  then
    echo "# Docker Desktop for macOS detected, but TPOT_OSTYPE is not set to mac."
    echo "# 1. You need to adjust the OSType in the T-Pot .env config."
    echo "# 2. You need to copy compose/mac_win.yml to ./docker-compose.yml."
    echo
    echo "# Aborting."
    echo
    sleep 1
    exit 1
fi

if [ "${myOSTYPE}" == "" ] && [ "${TPOT_OSTYPE}" != "linux" ];
  then
    echo "# Docker Engine detected, but TPOT_OSTYPE is not set to linux."
    echo "# 1. You need to adjust the OSType in the T-Pot .env config."
    echo "# 2. You need to copy compose/standard.yml to ./docker-compose.yml."
    echo
    echo "# Aborting."
    echo
    sleep 1
    exit 1
fi

# Validate environment variables, all errors are collected and reported at once
echo
echo "# Validating the T-Pot .env config ..."
echo
fuENV_VALIDATE
# The credentials of the other type are not used, and must not be acted on
if [ "${TPOT_TYPE}" == "HIVE" ];
  then
    TPOT_HIVE_USER=""
    TPOT_HIVE_IP=""
fi
if [ "${TPOT_TYPE}" == "SENSOR" ];
  then
    WEB_USER=""
fi
echo
if ! fuENV_REPORT;
  then
    if [ "${TPOT_TYPE}" != "SENSOR" ] && [ -z "${WEB_USER//[[:space:]]/}" ];
      then
        web_users_hint /data
    fi
    echo "# Aborting"
    exit 1
fi
echo

# Data folder management
if [ -f "/data/uuid" ];
  then
    figlet "Initializing ..."
    figlet "T-Pot: ${TPOT_VERSION}"
    if ! create_web_users;
      then
        echo "# Aborting"
        exit 1
    fi
    echo
    echo "# Data folder is present, just cleaning up, please be patient ..."
    echo
    /opt/tpot/bin/clean.sh "${TPOT_PERSISTENCE}" "${TPOT_PERSISTENCE_CYCLES}"
    echo
  else
    figlet "Setting up ..."
    figlet "T-Pot: ${TPOT_VERSION}"
    myFIRSTRUN="true"
    echo
    echo "# Setting up data folder structure ..."
    echo
    /opt/tpot/bin/clean.sh off
    echo
    # The first import of the Kibana objects is pending until it worked (kibana_objects.sh), written
    # before /data/uuid: a first start that ends before the import tries it again on the next one
    if ! python3 /opt/tpot/bin/kibana_objects.py mark-fresh --data /data;
      then
        echo "# Warning: the first import of the Kibana objects could not be marked, the Kibana objects job of this start imports them without the advanced settings."
    fi
    echo
    echo "# Generating self signed certificate ..."
    echo
    myINTIP=$(/sbin/ip address show | awk '/inet .*brd/{split($2,a,"/"); print a[1]; exit}')
    openssl req \
          -nodes \
          -x509 \
          -sha512 \
          -newkey rsa:8192 \
          -keyout "/data/nginx/cert/nginx.key" \
          -out "/data/nginx/cert/nginx.crt" \
          -days 3650 \
          -subj '/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd' \
          -addext "subjectAltName = IP:${myINTIP}"
    echo
    if ! create_web_users;
      then
        echo "# Aborting"
        exit 1
    fi
    echo
    echo "# Final touches and permissions ..."
    echo
    uuidgen > /data/uuid
fi

# A hold of ewsposter or a lock of the checks from before this start ends here
clear_ews_hold /data

# Check if TPOT_BLACKHOLE is enabled
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    if [ "${TPOT_BLACKHOLE}" == "ENABLED" ] && [ ! -f "/etc/blackhole/mass_scanner.txt" ];
      then
        echo
        echo "# Adding Blackhole routes."
        /opt/tpot/bin/blackhole.sh add
        echo
    fi
    if [ "${TPOT_BLACKHOLE}" == "DISABLED" ] && [ -f "/etc/blackhole/mass_scanner.txt" ];
      then
        echo
        echo "# Removing Blackhole routes."
        /opt/tpot/bin/blackhole.sh del
        echo
      else
        echo
        echo "# Blackhole is not active."
    fi
  else
    echo
    echo "# T-Pot is configured for macOS / Windows. Blackhole is not supported."
    echo
fi

# Get IP
echo
echo "# Updating IP Info ..."
echo
/opt/tpot/bin/updateip.sh

# Update permissions
update_permissions

# Update interface settings (NSM services) and setup iptables to support NFQ based honeypots (glutton, honeytrap)
### This is currently not supported on Docker for Desktop, only on Docker Engine for Linux
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    echo
    echo "# Get IF, disable offloading, enable promiscious mode for NSM services ..."
    echo
    # Same detection as in the NSM containers (TPOT_CAPTURE_INTERFACE or the route
    # to the internet), so the settings apply to the interface they capture on.
    # Without an interface only the NSM services fail, T-Pot itself keeps running.
    if myIF="$(sh /opt/tpot/bin/capture-if.sh)";
      then
        echo "Capture interface: ${myIF}"
        ethtool --offload "${myIF}" rx off tx off
        ethtool -K "${myIF}" gso off gro off
        ip link set "${myIF}" promisc on
      else
        echo "# Warning: No capture interface found, skipping offload / promiscuous mode settings. NSM services will not start."
    fi
    echo
    echo "# Adding firewall rules ..."
    echo
    /opt/tpot/bin/rules.sh ${COMPOSE} set
  else
    echo
    echo "# T-Pot is configured for macOS / Windows. Setting up firewall rules on the host is not supported."
    echo
fi

# Display open ports
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    echo
    echo "# This is a list of open ports on the host (netstat -tulpen)."
    echo "# Make sure there are no conflicting ports by checking the docker compose file."
    echo "# Conflicting ports will prevent the startup of T-Pot."
    echo
    netstat -tulpen | grep -Eo ':([0-9]+)' | cut -d ":" -f 2 | uniq
    echo
  else
    echo
    echo "# T-Pot is configured for macOS / Windows. Showing open ports from the host is not supported."
    echo
fi


# Done
echo
figlet "Starting ..."
figlet "T-Pot: ${TPOT_VERSION}"
echo
touch /tmp/success

# The ILM policy tpot follows TPOT_ES_RETENTION, unless it was changed outside of T-Pot
# (rules in its bin/es_retention.sh). In the background: Elasticsearch takes a while
# and nothing else waits for it.
if [ "${TPOT_TYPE}" == "HIVE" ] && yq -e '.services | has("elasticsearch")' "${COMPOSE}" >/dev/null 2>&1;
  then
    /opt/tpot/bin/es_retention.sh &
    myILM_PID=$!
fi

# T-Pot's Kibana objects: the first import until it worked, then TPOT_KIBANA_OBJECTS (rules in
# bin/kibana_objects.py). In the background: Kibana takes a while and nothing else waits for it.
if [ "${TPOT_TYPE}" == "HIVE" ] && yq -e '.services | has("kibana")' "${COMPOSE}" >/dev/null 2>&1;
  then
    /opt/tpot/bin/kibana_objects.sh &
    myKIBANA_PID=$!
fi

# We want to see true source for UDP packets in container (https://github.com/moby/libnetwork/issues/1994)
# Start autoheal if running on a supported os
if [ "${TPOT_OSTYPE}" == "linux" ];
  then
    # in the background, so that a SIGTERM ends the wait at once (cleanup)
    sleep 60 & wait $!
    echo "# Dropping UDP connection tables to improve visibility of true source IPs."
    /usr/sbin/conntrack -D -p udp
fi

# Starting container health monitoring
echo
figlet "Starting ..."
figlet "Autoheal"
echo "# Now monitoring healthcheck enabled containers to automatically restart them when unhealthy."
echo
/opt/tpot/autoheal.sh autoheal &
PID=$!
wait $PID
echo "# T-Pot Init and Autoheal were stopped. Exiting."
