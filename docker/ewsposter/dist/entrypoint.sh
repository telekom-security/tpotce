#!/bin/ash
#
# ewsposter runs one ews.py pass (-l 0) after the other, with a pause of 10 to 60 s between them,
# drawn once per start.
#
# The hold: while T-Pot checks itself (tpot check), the check writes the marker /data/ews/hold.
# One normal pass first sends the lines from before the hold, then the passes are idle: ews.py
# builds, spools and sends no alert, its indexes move on, so the lines of the check are never
# sent, not even later. /data/ews/hold.ack says that ewsposter holds: it is written after each
# idle round that ended cleanly (every pass with rc 0, no module behind) and removed when the hold
# is over. A module whose idle pass keeps failing (also on its own, -m) does not keep the others
# from being held: hold.ack names it in one line "failed: <module> ...", so the check leaves its
# honeypots alone (a later round names a module that fails later, the check reads it again).
# When the marker is gone (removed, or older than 30 minutes) one more idle round runs; every
# module that was not clean and caught up in it is quarantined: it stays idle-only (false in the
# config copy of the normal passes, an idle pass of its own each round) until one idle pass of
# its own is clean and caught up, then it sends again; the others send at once. A module that
# never catches up never sends; /data/ews/quarantine names the quarantined modules (one line
# "<epoch seconds> <module> ...", written every round, gone when none is). When not even that
# round can run (no module started), the hold is stuck: nothing is sent. /data/ews/hold.state
# says normal, held or stuck. A hold that is on when
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
# the state of the hold for the Manager: one line "<normal|held|stuck> <epoch seconds of the round>"
myHOLD_STATE="/data/ews/hold.state"
# how often a hold that cannot end says so as an error (seconds)
mySTUCK_REPORT=600
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
# the modules whose idle passes failed in the last round (fuIDLE), one space before each
myIDLE_FAILED=""
# the modules still behind in the last round (fuIDLE), one space before each
myIDLE_BEHIND=""
# this hold: an ack was written (yes), it began without one (ewsposter started held: yes); either
# way a later round that fails names its modules in hold.ack for the check
myHOLD_ACKED=""
myHOLD_NOACK=""
# the modules that send nothing until an idle pass of their own is clean and caught up
myQUARANTINE=""
myQUARANTINE_FILE="/data/ews/quarantine"
# the config folder of the normal passes while modules are quarantined
myNORMAL_CFG="/tmp/ews-normal"
mySTUCK_SINCE=""
mySTUCK_SAID=0

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
# try of every pass ended with rc 0 and no module is behind; 2 when they ended with rc 0 but the
# last pass still stopped at the limit (clean, but behind); 1 when a pass failed.
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
      if [ ${myPASS} -ge ${myIDLE_PASSES} ];
        then
          echo " => [WARN] After ${myIDLE_PASSES} idle passes ${1:+of $1 }a module is still behind."
          return 2
      fi
      myPASS=$(( myPASS + 1 ))
  done
}

# The modules ews.py runs with the idle config, one per line: as readMYcfg of ews.py, every
# section with a key of its own name that is not empty (a module skips itself on "false"),
# without the four output sections
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
      if (key == section && value != "" && section !~ /^(ews|hpfeed|ewsjson|influxdb)$/) print section
    }
  ' "${myIDLE_CFG}/ews.cfg"
}

# One idle round: all modules in one ews.py; when that still fails, module by module (-m), so a
# module that keeps failing does not keep the others from moving on. When no module was started
# (the start of ews.py failed: config, lock, the lookup of the address), one by one would fail the
# same way and is left out. 0 only when the round ended cleanly and no module is behind, 2 when
# it ended cleanly but a module is behind (the cap of myIDLE_PASSES), 3 when it ended cleanly for
# every module but those in myIDLE_FAILED, 1 when it failed (also when every module failed).
fuIDLE () {
  local myMODULE=""
  local myFAILED=""
  local myBEHIND=""
  local myCLEAN=""
  local myRC=0
  myIDLE_FAILED=""
  myIDLE_BEHIND=""
  if ! fuIDLE_CONFIG;
    then
      echo " => [WARN] The config of the idle passes could not be written to ${myIDLE_CFG}."
      return 1
  fi
  fuIDLE_PASSES
  myRC=$?
  if [ ${myRC} -eq 2 ];
    then
      myIDLE_BEHIND=$(fuLIMIT_MODULES)
  fi
  [ ${myRC} -eq 1 ] || return ${myRC}
  if ! grep -qF -- "${myIDLE_MODULE_TEXT}" "${myIDLE_CFG}/pass.log" 2>/dev/null;
    then
      return 1
  fi
  echo " => [WARN] The idle round failed, now module by module."
  for myMODULE in $(fuIDLE_MODULES);
    do
      fuIDLE_PASSES "${myMODULE}"
      case $? in
        1) myFAILED="${myFAILED} ${myMODULE}" ;;
        2) myBEHIND="${myBEHIND} ${myMODULE}"; myCLEAN="yes" ;;
        *) myCLEAN="yes" ;;
      esac
  done
  if [ -n "${myFAILED}" ];
    then
      echo " => [WARN] Idle passes failed for:${myFAILED}."
      myIDLE_FAILED="${myFAILED}"
      # no module could be held
      [ -n "${myCLEAN}" ] || return 1
  fi
  if [ -n "${myBEHIND}" ];
    then
      myIDLE_BEHIND="${myBEHIND}"
      return 2
  fi
  [ -z "${myIDLE_FAILED}" ] || return 3
  return 0
}

# The modules the last idle pass stopped at the limit (ews.py names them in upper case), one space
# before each
fuLIMIT_MODULES () {
  local myMODULE=""
  for myMODULE in $(sed -n "s/.*${myIDLE_LIMIT_TEXT} \([A-Za-z0-9_-]*\) reached.*/\1/p" "${myIDLE_CFG}/pass.log" 2>/dev/null | tr 'A-Z' 'a-z' | sort -u);
    do
      printf ' %s' "${myMODULE}"
  done
}

# 0 when the word $1 is one of $2
fuHAS () {
  case " $2 " in
    *" $1 "*) return 0 ;;
  esac
  return 1
}

# The words of $1 and $2 without repeats, one space before each
fuUNION () {
  local myWORD=""
  local myOUT=""
  for myWORD in $1 $2;
    do
      fuHAS "${myWORD}" "${myOUT}" || myOUT="${myOUT} ${myWORD}"
  done
  echo "${myOUT}"
}

# /data/ews/quarantine: "<epoch> $1" through the folder, O_NOFOLLOW, a temporary file renamed into
# place (a link of that name is replaced, never followed); removed when $1 is empty
myQUARANTINE_WRITE='import os, stat, sys, time
folder, name, text = os.path.dirname(sys.argv[1]), os.path.basename(sys.argv[1]), sys.argv[2].split()
fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    if not text:
        try:
            if not stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass
        sys.exit(0)
    temp = ".%s.%d" % (name, os.getpid())
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o660, dir_fd=fd)
    try:
        os.write(handle, ("%d %s\n" % (time.time(), " ".join(text))).encode())
        os.fchmod(handle, 0o660)
    finally:
        os.close(handle)
    os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
finally:
    os.close(fd)'
# the modules of /data/ews/quarantine (a regular file, never a link), one space before each
myQUARANTINE_READ='import os, re, stat, sys
folder, name = os.path.dirname(sys.argv[1]), os.path.basename(sys.argv[1])
try:
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            text = os.read(handle, 4096).decode("ascii", "replace") if stat.S_ISREG(os.fstat(handle).st_mode) else ""
        finally:
            os.close(handle)
    finally:
        os.close(fd)
except OSError:
    sys.exit(0)
words = text.split("\n")[0].split()[1:]
print("".join(" " + word for word in words if re.fullmatch(r"[a-z0-9_-]{1,64}", word)))'

fuQUARANTINE_SAVE () {
  python3 -c "${myQUARANTINE_WRITE}" "${myQUARANTINE_FILE}" "${myQUARANTINE}" 2>/dev/null \
    || echo " => [WARN] ${myQUARANTINE_FILE} could not be written."
}

fuQUARANTINE_LOAD () {
  myQUARANTINE=$(python3 -c "${myQUARANTINE_READ}" "${myQUARANTINE_FILE}" 2>/dev/null)
}

# Prints the ews.cfg $1 with the module key of the sections in $2 set to false; every other line
# stays as it is
fuNORMAL_CONFIG_REWRITE () {
  awk -v off="$2" '
    BEGIN { n = split(off, list, " "); for (i = 1; i <= n; i++) skip[list[i]] = 1 }
    /^\[/ {
      section = tolower(substr($0, 2, index($0, "]") - 2))
      print
      next
    }
    (section in skip) && tolower($0) ~ ("^" section "[ \t]*[=:]") {
      print section " = false"
      next
    }
    { print }
  ' "$1"
}

# The config folder of the normal passes with the quarantined modules off, ews.ip and ews.uuid as
# links to those of ewsposter
fuNORMAL_CONFIG () {
  local myFILE=""
  mkdir -p "${myNORMAL_CFG}" || return 1
  fuNORMAL_CONFIG_REWRITE "${myEWS}/ews.cfg" "${myQUARANTINE}" > "${myNORMAL_CFG}/ews.cfg.new" || return 1
  mv -f "${myNORMAL_CFG}/ews.cfg.new" "${myNORMAL_CFG}/ews.cfg" || return 1
  for myFILE in ews.ip ews.uuid;
    do
      if [ -e "${myEWS}/${myFILE}" ];
        then
          ln -sf "${myEWS}/${myFILE}" "${myNORMAL_CFG}/${myFILE}" || return 1
        else
          rm -f "${myNORMAL_CFG}/${myFILE}"
      fi
  done
}

# The quarantined modules, each with an idle pass of its own: one that is clean and caught up
# leaves the quarantine. 1 when the idle config cannot be written (nothing may be sent then).
fuQUARANTINE_ROUND () {
  local myMODULE=""
  local myLEFT=""
  # one pass each round, no tries again: a module that keeps failing waits for the next round
  local myIDLE_RETRIES=0
  [ -n "${myQUARANTINE}" ] || return 0
  if ! fuIDLE_CONFIG;
    then
      echo " => [WARN] The config of the idle passes could not be written to ${myIDLE_CFG}: no normal pass while${myQUARANTINE} catch up."
      return 1
  fi
  for myMODULE in ${myQUARANTINE};
    do
      if fuIDLE_PASSES "${myMODULE}";
        then
          echo " => ${myMODULE} caught up after the hold, it sends again."
        else
          myLEFT="${myLEFT} ${myMODULE}"
      fi
  done
  myQUARANTINE="${myLEFT}"
  fuQUARANTINE_SAVE
}

# hold.ack, a new file renamed into place (a link of that name is replaced, never followed):
# empty, or with the modules $1 that could not be held, one line "failed: <module> ..."
fuHOLD_ACK () {
  local myTMP="${myHOLD_ACK}.$$"
  rm -f "${myTMP}"
  if ! { if [ -n "${1:-}" ]; then echo "failed: $1"; fi > "${myTMP}" && mv -f "${myTMP}" "${myHOLD_ACK}"; } 2>/dev/null;
    then
      rm -f "${myTMP}" 2>/dev/null
      echo " => [WARN] ${myHOLD_ACK} could not be written."
  fi
}

# hold.state: "$1 <epoch seconds>", a new file renamed into place (a link of that name is
# replaced, never followed)
fuHOLD_STATE () {
  local myTMP="${myHOLD_STATE}.$$"
  rm -f "${myTMP}"
  if ! { echo "$1 $(date +%s)" > "${myTMP}" && mv -f "${myTMP}" "${myHOLD_STATE}"; } 2>/dev/null;
    then
      rm -f "${myTMP}" 2>/dev/null
      echo " => [WARN] ${myHOLD_STATE} could not be written."
  fi
}

# The hold is over but cannot end: an error when that begins, then every mySTUCK_REPORT seconds
fuSTUCK () {
  local myNOW=""
  myNOW=$(date +%s)
  if [ -z "${mySTUCK_SINCE}" ];
    then
      mySTUCK_SINCE=${myNOW}
      mySTUCK_SAID=0
  fi
  if [ $(( myNOW - mySTUCK_SAID )) -ge ${mySTUCK_REPORT} ];
    then
      echo " => [ERROR] The hold is over since $(date -d "@${mySTUCK_SINCE}" 2>/dev/null || echo "${mySTUCK_SINCE}"), but its idle rounds do not end cleanly or a module is still behind: ewsposter sends nothing until one does, or until the container starts again."
      mySTUCK_SAID=${myNOW}
  fi
}

# One normal pass; with quarantined modules, through the config copy that has them off (no copy:
# no pass)
fuPASS () {
  local myRC=0
  if [ -n "${myQUARANTINE}" ];
    then
      if ! fuNORMAL_CONFIG;
        then
          echo " => [WARN] The config of the normal passes could not be written to ${myNORMAL_CFG}: no normal pass."
          return 0
      fi
      fuRUN "${myPYTHON}" -u "${myEWS}/ews.py" -c "${myNORMAL_CFG}/" -l 0
    else
      fuRUN "${myPYTHON}" -u "${myEWS}/ews.py" -l 0
  fi
  myRC=$?
  if [ ${myRC} -ne 0 ];
    then
      echo " => [WARN] ews.py ended with rc=${myRC}."
  fi
  return 0
}

# One round of the loop. While the hold is on: idle passes, and hold.ack once they ended
# cleanly with no module behind (the first round of a hold starts with a normal pass); modules
# that fail on their own are named in it. When it is over: hold.ack removed, one more idle round,
# and every module that was not clean and caught up in it is quarantined, the others send at once
# (when that round could not run at all, the rounds stay idle: stuck). Each normal round first
# gives every quarantined module an idle pass of its own. Sets myINTERVAL, the pause after the
# round, and writes hold.state.
fuROUND () {
  local myRC=0
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
      fuIDLE
      myRC=$?
      case ${myRC} in
        0)
          fuHOLD_ACK
          myHOLD_ACKED="yes" ;;
        3)
          # the others are held; the check leaves the honeypots of these modules alone
          echo " => [WARN] hold.ack without the modules whose idle passes fail:${myIDLE_FAILED}; the check leaves their honeypots alone."
          fuHOLD_ACK "${myIDLE_FAILED# }"
          myHOLD_ACKED="yes" ;;
        2) echo " => [WARN] No hold.ack yet: a module is still behind, the idle rounds go on in ${myHOLD_INTERVAL} s." ;;
        *)
          echo " => [WARN] No hold.ack: the idle round did not end cleanly, it runs again in ${myHOLD_INTERVAL} s."
          # a check that holds already learns which modules fail from now on
          if [ -n "${myIDLE_FAILED}" ] && { [ -n "${myHOLD_ACKED}" ] || [ -n "${myHOLD_NOACK}" ]; };
            then
              fuHOLD_ACK "${myIDLE_FAILED# }"
          fi ;;
      esac
      fuHOLD_STATE held
      return 0
  fi
  if [ -n "${myHELD}" ];
    then
      rm -f "${myHOLD_ACK}"
      fuIDLE
      myRC=$?
      case ${myRC} in
        0) ;;
        2|3)
          myQUARANTINE=$(fuUNION "${myIDLE_FAILED}" "${myIDLE_BEHIND}")
          if [ -z "${myQUARANTINE}" ];
            then
              # behind, but the pass named no module: every module catches up first
              myQUARANTINE=$(fuUNION "$(fuIDLE_MODULES)" "")
          fi ;;
        *)
          if [ -n "${myIDLE_FAILED}" ];
            then
              myQUARANTINE="${myIDLE_FAILED}"
            else
              # no module started (config, lock, the lookup of the address): idle again, nothing sent
              echo " => [WARN] The hold is over, but its idle round did not run: idle again in ${myHOLD_INTERVAL} s, nothing is sent."
              fuSTUCK
              fuHOLD_STATE stuck
              return 0
          fi ;;
      esac
      myHELD=""
      mySTUCK_SINCE=""
      myHOLD_ACKED=""
      myHOLD_NOACK=""
      fuQUARANTINE_SAVE
      if [ -n "${myQUARANTINE}" ];
        then
          echo " => [WARN] The hold is over, ewsposter sends again, except${myQUARANTINE}: they send only once an idle pass of their own is clean and caught up."
        else
          echo " => The hold is over, ewsposter sends again."
      fi
    else
      if ! fuQUARANTINE_ROUND;
        then
          fuHOLD_STATE normal
          return 0
      fi
  fi
  fuPASS
  myINTERVAL=${mySLEEP}
  fuHOLD_STATE normal
}

# The state at the start: an ack of an earlier run would tell a new check that ewsposter holds
# before it does; a hold that is on at the start has been probing for a while already, so it goes
# on with idle passes at once, without the normal pass a new hold starts with. Modules that were
# quarantined (the container started again, tpotinit did not run) stay so.
fuSTART () {
  myHELD=""
  mySTUCK_SINCE=""
  myHOLD_ACKED=""
  myHOLD_NOACK=""
  rm -f "${myHOLD_ACK}"
  fuQUARANTINE_LOAD
  if fuHOLD_ON;
    then
      echo " => The hold for the check of T-Pot is on at the start: idle passes only, nothing is sent."
      myHELD="yes"
      myHOLD_NOACK="yes"
      fuHOLD_STATE held
    else
      fuHOLD_STATE normal
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
