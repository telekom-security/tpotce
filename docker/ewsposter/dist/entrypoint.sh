#!/bin/ash
#
# ewsposter runs one ews.py pass (-l 0) after the other, with a pause of 10 to 60 s between them,
# drawn once per start.
#
# The hold: while T-Pot checks itself (tpot check), the check writes the marker /data/ews/hold.
# One normal pass first sends the lines from before the hold, then the passes are idle: ews.py
# builds, spools and sends no alert, its indexes move on, so the lines of the check are never
# sent, not even later. /data/ews/hold.ack says that ewsposter holds: it is written after each
# idle round that ended cleanly (every pass with rc 0) and removed when the hold is over. Normal
# passes come back only after a clean idle round without the marker; a hold that is on when
# ewsposter starts goes on with idle passes at once. The marker only counts as a regular file
# (no link) younger than 30 minutes, so a check that was cut off never keeps ewsposter quiet for
# good.
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
# what ews.py prints when a module stopped at the limit, at c6b44c7 modules/ealert.py:437
# (EAlert.buildAlert): print(f'    -> Sendlimit ({self.ECFG["sendlimit"]}) for Honeypot {self.MODUL} reached. Skip.')
myIDLE_LIMIT_TEXT="Sendlimit (${myIDLE_LIMIT}) for Honeypot"
# what ews.py prints when it starts a module, at c6b44c7 modules/ealert.py:43
# (EAlert.__init__): print(f' => Starting {self.MODUL.title()} Honeypot Modul.')
myIDLE_MODULE_TEXT=" => Starting "
# a failed idle pass is tried again this many times, after this pause (seconds)
myIDLE_RETRIES=3
myIDLE_RETRY_PAUSE=5
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

# One idle pass: the idle config, no spool (-E), at most myIDLE_LIMIT lines per module (-L);
# with a module as $1 only that one (-m)
fuIDLE_PASS () {
  local myRC=0
  if [ -n "${1:-}" ];
    then
      set -- -m "$1"
  fi
  fuRUN "${myPYTHON}" -u "${myEWS}/ews.py" -c "${myIDLE_CFG}/" -l 0 -E -L "${myIDLE_LIMIT}" "$@" > "${myIDLE_CFG}/pass.log" 2>&1
  myRC=$?
  cat "${myIDLE_CFG}/pass.log" 2>/dev/null
  return ${myRC}
}

# Idle passes ([MODULE] as for fuIDLE_PASS) until no module stopped at the limit, at most
# myIDLE_PASSES in a row; a failed pass is tried again up to myIDLE_RETRIES times. A module that
# fails on a line has counted that line already, so a new try goes on behind it. 0 when the last
# try of every pass ended with rc 0.
fuIDLE_PASSES () {
  local myPASS=1
  local myTRY=0
  local myRC=0
  while :;
    do
      fuIDLE_PASS "$@"
      myRC=$?
      if [ ${myRC} -ne 0 ];
        then
          if [ ${myTRY} -ge ${myIDLE_RETRIES} ];
            then
              echo " => [WARN] The idle pass ${1:+of $1 }ended with rc=${myRC}, also after ${myIDLE_RETRIES} more tries."
              return 1
          fi
          myTRY=$(( myTRY + 1 ))
          echo " => [WARN] The idle pass ${1:+of $1 }ended with rc=${myRC}, try ${myTRY} of ${myIDLE_RETRIES} in ${myIDLE_RETRY_PAUSE} s."
          fuRUN sleep "${myIDLE_RETRY_PAUSE}"
          continue
      fi
      myTRY=0
      grep -qF -- "${myIDLE_LIMIT_TEXT}" "${myIDLE_CFG}/pass.log" || return 0
      [ ${myPASS} -lt ${myIDLE_PASSES} ] || return 0
      myPASS=$(( myPASS + 1 ))
  done
}

# The modules the idle config has on (the key named as its section is true), one per line
fuIDLE_MODULES () {
  awk '
    /^\[/ {
      section = tolower(substr($0, 2, index($0, "]") - 2))
      next
    }
    match($0, /^[^=:]+[=:]/) {
      key = tolower(substr($0, 1, RLENGTH - 1))
      value = tolower(substr($0, RLENGTH + 1))
      gsub(/[ \t]+$/, "", key)
      gsub(/^[ \t]+|[ \t]+$/, "", value)
      if (key == section && value == "true") print section
    }
  ' "${myIDLE_CFG}/ews.cfg"
}

# One idle round: all modules in one ews.py; when that still fails, module by module (-m), so a
# module that keeps failing does not keep the others from moving on. When no module was started
# (the start of ews.py failed: config, lock, the lookup of the address), one by one would fail the
# same way and is left out. 0 only when the round ended cleanly.
fuIDLE () {
  local myMODULE=""
  local myFAILED=""
  if ! fuIDLE_CONFIG;
    then
      echo " => [WARN] The config of the idle passes could not be written to ${myIDLE_CFG}."
      return 1
  fi
  fuIDLE_PASSES && return 0
  if ! grep -qF -- "${myIDLE_MODULE_TEXT}" "${myIDLE_CFG}/pass.log" 2>/dev/null;
    then
      return 1
  fi
  echo " => [WARN] The idle round failed, now module by module."
  for myMODULE in $(fuIDLE_MODULES);
    do
      fuIDLE_PASSES "${myMODULE}" || myFAILED="${myFAILED} ${myMODULE}"
  done
  if [ -n "${myFAILED}" ];
    then
      echo " => [WARN] Idle passes failed for:${myFAILED}."
      return 1
  fi
  return 0
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

# One round of the loop. While the hold is on: idle passes, and hold.ack once they ended
# cleanly (the first round of a hold starts with a normal pass). When it is over: hold.ack
# removed, one more idle round, and only when that ended cleanly the normal pass; until then the
# rounds stay idle. Sets myINTERVAL, the pause after the round.
fuROUND () {
  myINTERVAL=${myHOLD_INTERVAL}
  if fuHOLD_ON;
    then
      if [ -z "${myHELD}" ];
        then
          # the lines from before the hold are sent first; the check waits for hold.ack, so
          # none of its own lines are there yet
          echo " => Hold for the check of T-Pot: one normal pass, then idle passes, nothing is sent."
          fuPASS
          myHELD="yes"
      fi
      if fuIDLE;
        then
          fuHOLD_ACK
        else
          echo " => [WARN] No hold.ack: the idle round did not end cleanly, it runs again in ${myHOLD_INTERVAL} s."
      fi
      return 0
  fi
  if [ -n "${myHELD}" ];
    then
      rm -f "${myHOLD_ACK}"
      if ! fuIDLE;
        then
          echo " => [WARN] The hold is over, but its idle round did not end cleanly: idle again in ${myHOLD_INTERVAL} s, nothing is sent."
          return 0
      fi
      myHELD=""
      echo " => The hold is over, ewsposter sends again."
  fi
  fuPASS
  myINTERVAL=${mySLEEP}
}

# The state at the start: an ack of an earlier run would tell a new check that ewsposter holds
# before it does; a hold that is on at the start has been probing for a while already, so it goes
# on with idle passes at once, without the normal pass a new hold starts with
fuSTART () {
  myHELD=""
  rm -f "${myHOLD_ACK}"
  if fuHOLD_ON;
    then
      echo " => The hold for the check of T-Pot is on at the start: idle passes only, nothing is sent."
      myHELD="yes"
  fi
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
  myINTERVAL=${mySLEEP}
  fuSTART
  while :;
    do
      fuROUND
      fuRUN sleep "${myINTERVAL}"
  done
}

case "${0##*/}" in
  entrypoint.sh) fuMAIN ;;
esac
