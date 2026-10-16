#!/bin/ash
#
# ewsposter runs one ews.py pass (-l 0) after the other, with a pause of 10 to 60 s between them,
# drawn once per start.
#
# The hold: while T-Pot checks itself (tpot check), the check writes the marker /data/ews/hold.
# The passes are idle then: ews.py builds, spools and sends no alert, its indexes move on, so the
# lines of the check are never sent, not even later. /data/ews/hold.ack says that ewsposter holds:
# it is written after each idle round and removed when the hold is over. The marker only counts
# as a regular file (no link) younger than 30 minutes, so a check that was cut off never keeps
# ewsposter quiet for good.
#
# Only one ews.py runs at a time (its lock socket would end a second one), each pass is waited for.
# The functions can be sourced for the tests (tpotctl/tests/test_ewsposter_entrypoint.py), the loop
# only runs when the file is started as entrypoint.sh.

myPYTHON="/usr/bin/python3"
myEWS="/opt/ewsposter"
myHOLD="/data/ews/hold"
myHOLD_ACK="/data/ews/hold.ack"
# the marker counts while it is younger than this (seconds); one dated more than a minute ahead counts neither
myHOLD_MAX_AGE=1800
# the pause between the passes while the hold is on (seconds)
myHOLD_INTERVAL=5
# the config folder of the idle passes
myIDLE_CFG="/tmp/ews-hold"
# the most lines (alerts) per module and idle pass, the highest value ews.py takes for -L
myIDLE_LIMIT=5000
# a module that reached the limit has more lines: another idle pass, at most this many in a row
myIDLE_PASSES=20
myCHILD=""

# On INT (docker stop) or TERM the running pass or pause ends at once, and so does ewsposter
fuSTOP () {
  if [ -n "${myCHILD}" ];
    then
      kill -TERM "${myCHILD}" 2>/dev/null
      wait "${myCHILD}" 2>/dev/null
  fi
  exit 0
}

# Runs a command in the background and waits for it, so a signal is handled at once
fuRUN () {
  "$@" &
  myCHILD=$!
  wait "${myCHILD}"
  myRC=$?
  myCHILD=""
  return ${myRC}
}

# The hold is on while the marker is a regular file, no link, younger than myHOLD_MAX_AGE
fuHOLD_ON () {
  local myMTIME=""
  local myAGE=""
  [ -f "${myHOLD}" ] || return 1
  [ ! -L "${myHOLD}" ] || return 1
  myMTIME=$(date -r "${myHOLD}" +%s 2>/dev/null) || return 1
  case "${myMTIME}" in
    ''|*[!0-9]*) return 1 ;;
  esac
  myAGE=$(( $(date +%s) - myMTIME ))
  [ "${myAGE}" -ge -60 ] && [ "${myAGE}" -lt "${myHOLD_MAX_AGE}" ]
}

# Prints the ews.cfg $1 with ews, hpfeed, json and influxdb set to false in their sections
# ([EWS], [HPFEED], [EWSJSON], [INFLUXDB]); every other line stays as it is. A key that is missing
# is added at the end of its section, a missing section at the end of the file.
fuIDLE_CONFIG_REWRITE () {
  awk '
    BEGIN {
      off["EWS"] = "ews"
      off["HPFEED"] = "hpfeed"
      off["EWSJSON"] = "json"
      off["INFLUXDB"] = "influxdb"
    }
    function add_missing() {
      if ((section in off) && !(section in done)) {
        print off[section] " = false"
        done[section] = 1
      }
    }
    /^\[/ {
      add_missing()
      section = substr($0, 2, index($0, "]") - 2)
      print
      next
    }
    (section in off) && tolower($0) ~ ("^" off[section] "[ \t]*[=:]") {
      print off[section] " = false"
      done[section] = 1
      next
    }
    { print }
    END {
      add_missing()
      for (name in off) {
        if (!(name in done)) {
          print ""
          print "[" name "]"
          print off[name] " = false"
        }
      }
    }
  ' "$1"
}

# The config folder of the idle passes: the rewritten ews.cfg, ews.ip and ews.uuid as links to
# those of ewsposter (a missing one is left out, ews.py then works without it)
fuIDLE_CONFIG () {
  local myFILE=""
  mkdir -p "${myIDLE_CFG}" || return 1
  fuIDLE_CONFIG_REWRITE "${myEWS}/ews.cfg" > "${myIDLE_CFG}/ews.cfg.new" || return 1
  mv -f "${myIDLE_CFG}/ews.cfg.new" "${myIDLE_CFG}/ews.cfg" || return 1
  for myFILE in ews.ip ews.uuid;
    do
      if [ -e "${myEWS}/${myFILE}" ];
        then
          ln -sf "${myEWS}/${myFILE}" "${myIDLE_CFG}/${myFILE}" || return 1
        else
          rm -f "${myIDLE_CFG}/${myFILE}"
      fi
  done
}

# One idle pass: the idle config, no spool (-E), at most myIDLE_LIMIT lines per module (-L)
fuIDLE_PASS () {
  local myRC=0
  fuRUN "${myPYTHON}" -u "${myEWS}/ews.py" -c "${myIDLE_CFG}/" -l 0 -E -L "${myIDLE_LIMIT}" > "${myIDLE_CFG}/pass.log" 2>&1
  myRC=$?
  cat "${myIDLE_CFG}/pass.log"
  return ${myRC}
}

# Idle passes until no module stopped at the limit; ews.py says so with
# "Sendlimit (5000) for Honeypot <MODULE> reached". At most myIDLE_PASSES in a row.
fuIDLE () {
  local myPASS=1
  local myRC=0
  if ! fuIDLE_CONFIG;
    then
      echo " => [WARN] The config of the idle passes could not be written to ${myIDLE_CFG}."
      return 1
  fi
  while :;
    do
      fuIDLE_PASS
      myRC=$?
      if [ ${myRC} -ne 0 ];
        then
          echo " => [WARN] The idle pass ended with rc=${myRC}."
          return ${myRC}
      fi
      grep -q "Sendlimit (${myIDLE_LIMIT}) for Honeypot" "${myIDLE_CFG}/pass.log" || return 0
      [ ${myPASS} -lt ${myIDLE_PASSES} ] || return 0
      myPASS=$(( myPASS + 1 ))
  done
}

# hold.ack, written new (a link in its place is removed, never followed)
fuHOLD_ACK () {
  if [ -L "${myHOLD_ACK}" ];
    then
      rm -f "${myHOLD_ACK}"
  fi
  touch "${myHOLD_ACK}" 2>/dev/null || echo " => [WARN] ${myHOLD_ACK} could not be written."
}

# One normal pass
fuPASS () {
  local myRC=0
  fuRUN "${myPYTHON}" -u "${myEWS}/ews.py" -l 0
  myRC=$?
  if [ ${myRC} -ne 0 ];
    then
      echo " => [WARN] ews.py ended with rc=${myRC}."
  fi
  return 0
}

# One round of the loop: idle passes and hold.ack while the hold is on; when it is over one more
# idle pass, hold.ack removed, then the normal pass. Sets myINTERVAL, the pause after the round.
fuROUND () {
  if fuHOLD_ON;
    then
      if [ -z "${myHELD}" ];
        then
          echo " => Hold for the check of T-Pot: idle passes, nothing is sent."
          myHELD="yes"
      fi
      fuIDLE
      fuHOLD_ACK
      myINTERVAL=${myHOLD_INTERVAL}
      return 0
  fi
  if [ -n "${myHELD}" ];
    then
      fuIDLE
      rm -f "${myHOLD_ACK}"
      myHELD=""
      echo " => The hold is over, ewsposter sends again."
  fi
  fuPASS
  myINTERVAL=${mySLEEP}
}

fuMAIN () {
  # Source ENVs from file ...
  if [ -f "/data/tpot/etc/compose/elk_environment" ];
    then
      echo "Found .env, now exporting ..."
      set -o allexport && . "/data/tpot/etc/compose/elk_environment" && set +o allexport
  fi
  trap fuSTOP INT TERM
  mySLEEP=$(shuf -i 10-60 -n 1)
  myHELD=""
  myINTERVAL=${mySLEEP}
  # an ack of an earlier run would tell a new check that ewsposter holds before it does
  rm -f "${myHOLD_ACK}"
  while :;
    do
      fuROUND
      fuRUN sleep "${myINTERVAL}"
  done
}

case "${0##*/}" in
  entrypoint.sh) fuMAIN ;;
esac
