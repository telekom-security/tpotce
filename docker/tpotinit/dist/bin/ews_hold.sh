#!/bin/bash
# ews_hold.sh: the hold of ewsposter and the lock of the checks of T-Pot, the same as
# tpotctl/checks/ewshold.py. hptest.sh and attackmap_pipeline_test.sh source it; as a command it
# runs another one under the hold:
#
#   ews_hold.sh [--data DIR] [--compose FILE] [--timeout SEC] [--settle SEC] run -- COMMAND [ARGS ...]
#
# The lock is flock on <data>/ews/check.lock, without waiting: one check at a time. When ewsposter
# is in the compose file and its container runs (or docker cannot say), the marker <data>/ews/hold
# is written and the check waits up to 180 s for a <data>/ews/hold.ack written after it (ewsposter
# then sends nothing, docker/ewsposter/dist/entrypoint.sh); without one the marker goes and nothing
# is probed. When ewsposter is in the compose file but its container does not run, the marker is
# set without waiting (ewsposter starts held when it comes up); without ewsposter in the compose
# file there is no marker.
#
# While the hold lasts, a loop looks every 30 s whether the marker is still there and writes it
# anew every 5 minutes (ewsposter ignores one older than 30 minutes); it ends with the helper, also
# when that is killed. When the marker is gone (a start of T-Pot removes hold and check.lock,
# tpotinit's clear_ews_hold) or two renewals in a row fail, it sends TERM to the script and the
# check stops. At the end (EXIT, also after INT or TERM) the helper waits a few seconds for the
# last log lines of the check, removes the marker and gives the lock back.
#
# Files in <data>/ews are never reached through a link: the folder is writable for the group
# tpot. Every create, open and unlink there goes through python3 and the descriptor of the folder
# (O_NOFOLLOW, O_NONBLOCK); the lock is held by a python3 process that reads a pipe from the
# script, so it ends with the script, however that ends.
#
# Exit codes of the command: those of COMMAND; 2 a wrong option; 3 the lock is taken or ewsposter
# did not confirm the hold (COMMAND did not run).

# the most seconds to wait for hold.ack: one normal pass of ewsposter (10 to 60 s apart) plus the
# idle passes
myEWS_HOLD_TIMEOUT=${myEWS_HOLD_TIMEOUT:-180}
myEWS_HOLD_POLL=${myEWS_HOLD_POLL:-1}
# the marker is written anew after this many seconds, looked at after myEWS_HOLD_WATCH seconds;
# the loop looks at its parent every tick
myEWS_HOLD_REFRESH=${myEWS_HOLD_REFRESH:-300}
myEWS_HOLD_WATCH=${myEWS_HOLD_WATCH:-30}
myEWS_HOLD_TICK=${myEWS_HOLD_TICK:-5}
# the seconds the end of a hold waits for the last log lines of the check (the honeypots write some
# when a connection closes)
myEWS_HOLD_SETTLE=${myEWS_HOLD_SETTLE:-10}
# hold.state is written after every round of ewsposter; an older one says nothing
myEWS_HOLD_STATE_MAX_AGE=120
myEWS_HOLD_DIR=""
myEWS_HOLD_MARKER_NS=""
myEWS_HOLD_HELD=""
# the hold is on for the check: acknowledged, or set for an ewsposter that is down (the end waits
# myEWS_HOLD_SETTLE seconds then)
myEWS_HOLD_ACKED=""
myEWS_HOLD_LOCKED=""
myEWS_HOLD_LOCK_PID=""
myEWS_HOLD_TRAPPED=""
myEWS_HOLD_FINISHED=""
myEWS_HOLD_REFRESH_PID=""
myEWS_HOLD_CLEANUP=""
myEWS_HOLD_CHILD=""
myEWS_HOLD_FORKING=""
myEWS_HOLD_LAST=""

# A message in the look of the script that sourced this file, plain text without one
fuEWS_SAY () {
  local myKIND="$1"
  shift
  if declare -F "fuUI_${myKIND}" >/dev/null;
    then
      "fuUI_${myKIND}" "$*"
      return 0
  fi
  case "${myKIND}" in
    OK) echo "### [OK] - $*" ;;
    WARN) echo "### [WARNING] - $*" ;;
    ERROR) echo "### [ERROR] - $*" >&2 ;;
    *) echo "### $*" ;;
  esac
}

# The data folder of a T-Pot checkout ($1): TPOT_DATA_PATH of its .env, relative to the checkout
fuEWS_DATA_PATH () {
  local myDIR="$1"
  local myDATA=""
  if [ -f "${myDIR}/.env" ];
    then
      myDATA=$(grep -E '^TPOT_DATA_PATH=' "${myDIR}/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"'')
  fi
  [ -n "${myDATA}" ] || myDATA="./data"
  case "${myDATA}" in
    /*) ;;
    "~"/*) myDATA="${HOME}/${myDATA#\~/}" ;;
    *) myDATA="${myDIR}/${myDATA#./}" ;;
  esac
  echo "${myDATA}"
}

# 0 when ewsposter is in the compose file $1
fuEWS_IN_COMPOSE () {
  grep -qE "^[[:space:]]*container_name:[[:space:]]*[\"']?ewsposter[\"']?[[:space:]]*(#.*)?$" "$1" 2>/dev/null
}

# Whether the ewsposter container runs: 0 yes, 1 no (not running, or no such container), 2 docker
# cannot say (the hold then waits for the ack as if it ran)
fuEWS_RUNS () {
  local myOUT=""
  if myOUT=$(docker inspect -f '{{.State.Running}}' ewsposter 2>&1);
    then
      [ "${myOUT}" = "true" ] && return 0
      return 1
  fi
  case "${myOUT}" in
    *"No such object"*|*"No such container"*) return 1 ;;
  esac
  return 2
}

# The lock holder (python3): makes <data>/ews (0770, the group of <data>) and check.lock (0660)
# through the folder descriptors, opens them with O_NOFOLLOW and O_NONBLOCK, refuses a link, a
# special file or a file with another name (a hard link), takes flock without waiting and writes
# what came of it to the file $2: "locked <pid>", "busy", "link <name>", "nodata" or
# "error <text>". With the lock it reads its stdin until EOF: the pipe from the script.
myEWS_LOCK_HOLDER='import errno, fcntl, os, stat, sys
data, result = sys.argv[1], sys.argv[2]
def say(text):
    with open(result, "w") as out:
        out.write(text + "\n")
def group(handle, folder):
    try:
        os.fchown(handle, -1, os.fstat(folder).st_gid)
    except OSError:
        pass
FLAGS = os.O_NOFOLLOW | os.O_NONBLOCK
try:
    try:
        base = os.open(data, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        say("nodata")
        sys.exit(0)
    made = False
    try:
        os.mkdir("ews", 0o770, dir_fd=base)
        made = True
    except FileExistsError:
        pass
    try:
        ews = os.open("ews", os.O_RDONLY | os.O_DIRECTORY | FLAGS, dir_fd=base)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            say("link ews")
            sys.exit(0)
        raise
    if made:
        os.fchmod(ews, 0o770)
        group(ews, base)
    created = False
    try:
        lock = os.open("check.lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | FLAGS, 0o660, dir_fd=ews)
        created = True
    except FileExistsError:
        try:
            lock = os.open("check.lock", os.O_RDWR | FLAGS, dir_fd=ews)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.ENXIO, errno.EISDIR):
                say("link ews/check.lock")
                sys.exit(0)
            raise
    info = os.fstat(lock)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        say("link ews/check.lock")
        sys.exit(0)
    if created:
        os.fchmod(lock, 0o660)
        group(lock, ews)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        say("busy")
        sys.exit(0)
    say("locked %d" % os.getpid())
    while os.read(0, 4096):
        pass
except OSError as error:
    say("error %s" % (error.strerror or error))'

# The lock: the holder runs from a process substitution on fd 9 (started in the background there,
# so it is no child a `wait` of the script waits for); its answer comes through a file of mktemp.
# Closing fd 9, or any end of the script, ends the holder and frees the lock.
fuEWS_LOCK () {
  local myDATA="$1"
  local myRESULT=""
  local myANSWER=""
  local myWAIT=0
  if ! myRESULT=$(mktemp "${TMPDIR:-/tmp}/tpot-ews-lock.XXXXXX");
    then
      fuEWS_SAY ERROR "No temporary file for the lock of the checks."
      return 3
  fi
  exec 9> >(python3 -c "${myEWS_LOCK_HOLDER}" "${myDATA}" "${myRESULT}" <&0 >/dev/null 2>&1 &)
  while [ "${myWAIT}" -lt 200 ];
    do
      myANSWER=$(cat "${myRESULT}" 2>/dev/null)
      [ -n "${myANSWER}" ] && break
      sleep 0.05
      myWAIT=$(( myWAIT + 1 ))
    done
  rm -f "${myRESULT}"
  case "${myANSWER}" in
    "locked "*)
      myEWS_HOLD_LOCK_PID="${myANSWER#locked }"
      myEWS_HOLD_LOCKED="1"
      return 0 ;;
  esac
  exec 9>&-
  case "${myANSWER}" in
    busy) fuEWS_SAY ERROR "Another check is running." ;;
    "link "*) fuEWS_SAY ERROR "${myDATA}/${myANSWER#link } is a link or no regular file; T-Pot leaves it as it is." ;;
    nodata) fuEWS_SAY ERROR "The data folder ${myDATA} does not exist." ;;
    "error "*) fuEWS_SAY ERROR "The lock of the checks in ${myEWS_HOLD_DIR} cannot be taken: ${myANSWER#error }." ;;
    *) fuEWS_SAY ERROR "The lock of the checks in ${myEWS_HOLD_DIR} cannot be taken (no answer)." ;;
  esac
  return 3
}

# Gives the lock back and waits (at most 5 s) until the holder is gone
fuEWS_UNLOCK () {
  local myWAIT=0
  [ -n "${myEWS_HOLD_LOCKED}" ] || return 0
  exec 9>&-
  myEWS_HOLD_LOCKED=""
  while [ -n "${myEWS_HOLD_LOCK_PID}" ] && kill -0 "${myEWS_HOLD_LOCK_PID}" 2>/dev/null && [ "${myWAIT}" -lt 100 ];
    do
      sleep 0.05
      myWAIT=$(( myWAIT + 1 ))
    done
  myEWS_HOLD_LOCK_PID=""
}

# The marker, written new through the descriptor of the folder (no link on the way): refused when
# it is a link or no regular file; a temporary file with O_EXCL and O_NOFOLLOW, 0660 and the group
# of the folder set on its descriptor, renamed into place (a rename never follows a link and never
# moves into a folder). Prints the mtime of the new marker in ns. With "renew" only a marker that
# is there is written anew, so a renewal never brings back one the end of the hold removed.
fuEWS_MARKER_WRITE () {
  python3 -c 'import os, stat, sys, time, uuid
folder = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
try:
    try:
        if not stat.S_ISREG(os.stat("hold", dir_fd=folder, follow_symlinks=False).st_mode):
            sys.exit(1)
    except FileNotFoundError:
        if sys.argv[3] == "renew":
            sys.exit(1)
    temp = "hold.%s.tmp" % uuid.uuid4().hex
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                     dir_fd=folder)
    try:
        try:
            os.fchmod(handle, 0o660)
            try:
                os.fchown(handle, -1, os.fstat(folder).st_gid)
            except OSError:
                pass
            os.write(handle, ("tpot check %s %d\n" % (sys.argv[2], time.time())).encode())
        finally:
            os.close(handle)
        os.replace(temp, "hold", src_dir_fd=folder, dst_dir_fd=folder)
    except BaseException:
        os.unlink(temp, dir_fd=folder)
        raise
    print(os.stat("hold", dir_fd=folder, follow_symlinks=False).st_mtime_ns)
finally:
    os.close(folder)' "${myEWS_HOLD_DIR}" "$$" "${1:-new}" 2>/dev/null
}

# Removes the marker through the descriptor of the folder: 0 removed, 1 not there, 2 a link or no
# regular file (left as it is), 3 it cannot be removed
fuEWS_MARKER_REMOVE () {
  python3 -c 'import os, stat, sys
try:
    folder = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
except OSError:
    sys.exit(3)
try:
    try:
        info = os.stat("hold", dir_fd=folder, follow_symlinks=False)
    except FileNotFoundError:
        sys.exit(1)
    if not stat.S_ISREG(info.st_mode):
        sys.exit(2)
    try:
        os.unlink("hold", dir_fd=folder)
    except FileNotFoundError:
        sys.exit(1)
    except OSError:
        sys.exit(3)
finally:
    os.close(folder)' "${myEWS_HOLD_DIR}" 2>/dev/null
}

# 0 when the marker is a regular file (no link): the hold is still on
fuEWS_MARKER_THERE () {
  [ -f "${myEWS_HOLD_DIR}/hold" ] && [ ! -L "${myEWS_HOLD_DIR}/hold" ]
}

# 0 when hold.ack is a regular file, no link, written at or after the marker (its mtime in ns,
# myEWS_HOLD_MARKER_NS; test -nt of bash compares whole seconds in some releases)
fuEWS_ACKED () {
  python3 -c 'import os, stat, sys
try:
    info = os.lstat(sys.argv[1])
except OSError:
    sys.exit(1)
sys.exit(0 if stat.S_ISREG(info.st_mode) and info.st_mtime_ns >= int(sys.argv[2]) else 1)' \
    "${myEWS_HOLD_DIR}/hold.ack" "${myEWS_HOLD_MARKER_NS}" 2>/dev/null
}

# What hold.state says: normal, held or stuck; unknown when it is missing, no regular file, not of
# its form or older than myEWS_HOLD_STATE_MAX_AGE
fuEWS_STATE () {
  local myFILE="${myEWS_HOLD_DIR}/hold.state"
  local myLINE=""
  local myEPOCH=""
  local myAGE=""
  if [ ! -f "${myFILE}" ] || [ -L "${myFILE}" ];
    then
      echo "unknown"
      return 0
  fi
  myLINE=$(head -c 64 "${myFILE}" 2>/dev/null | head -n 1)
  case "${myLINE}" in
    "normal "*|"held "*|"stuck "*) ;;
    *) echo "unknown"; return 0 ;;
  esac
  myEPOCH="${myLINE#* }"
  case "${myEPOCH}" in
    ''|*[!0-9]*) echo "unknown"; return 0 ;;
  esac
  myAGE=$(( $(date +%s) - myEPOCH ))
  if [ "${myAGE}" -gt "${myEWS_HOLD_STATE_MAX_AGE}" ] || [ "${myAGE}" -lt -60 ];
    then
      echo "unknown"
      return 0
  fi
  echo "${myLINE%% *}"
}

# The error when ewsposter did not confirm the hold, with what hold.state says
fuEWS_TIMEOUT_TEXT () {
  local myTEXT="ewsposter did not confirm the hold within ${myEWS_HOLD_TIMEOUT} s; nothing was probed"
  case "$(fuEWS_STATE)" in
    stuck) myTEXT="${myTEXT}. ${myEWS_HOLD_DIR}/hold.state says stuck: after an earlier hold the idle rounds of ewsposter do not end cleanly or a module is still behind, it sends nothing until one does (docker logs ewsposter)" ;;
    held) myTEXT="${myTEXT}. ewsposter holds, but its idle rounds have not caught up yet (docker logs ewsposter)" ;;
    normal) myTEXT="${myTEXT}. ewsposter has not taken up the hold yet (docker logs ewsposter)" ;;
    *) myTEXT="${myTEXT}. ${myEWS_HOLD_DIR}/hold.state is missing or older than ${myEWS_HOLD_STATE_MAX_AGE} s, ewsposter may not run its loop (docker logs ewsposter)" ;;
  esac
  echo "${myTEXT}."
}

# 0 while the process $1 runs; one that ended counts as gone also before its parent reaped it
# (a zombie, by /proc where there is one)
fuEWS_ALIVE () {
  local myLINE=""
  kill -0 "$1" 2>/dev/null || return 1
  [ -r "/proc/$1/stat" ] || return 0
  read -r myLINE < "/proc/$1/stat" 2>/dev/null || return 1
  myLINE="${myLINE##*) }"
  case "${myLINE%% *}" in
    Z|X|x) return 1 ;;
  esac
  return 0
}

# 0 while a process of the process group $1 runs; zombies (whose parent does not reap them, i.e.
# in a container without an init) count as gone, by /proc where there is one
fuEWS_GROUP_ALIVE () {
  if [ ! -r /proc/self/stat ];
    then
      kill -0 -- "-$1" 2>/dev/null
      return
  fi
  python3 -c 'import os, sys
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    try:
        with open("/proc/%s/stat" % name) as handle:
            line = handle.read()
    except OSError:
        continue
    fields = line[line.rfind(")") + 2:].split()
    if len(fields) > 2 and fields[2] == sys.argv[1] and fields[0] not in ("Z", "X", "x"):
        sys.exit(0)
sys.exit(1)' "$1" 2>/dev/null
}

# The watch of the marker: while the shell that started it ($1) runs (it looks every
# myEWS_HOLD_TICK seconds, so it ends soon after a kill of that shell), every myEWS_HOLD_WATCH
# seconds whether the marker is still there, every myEWS_HOLD_REFRESH seconds it is written anew.
# The end of the hold stops this loop before it removes the marker, so a missing marker means
# someone else removed it (a start of T-Pot): then, and when the renewal fails twice in a row, it
# sends TERM to that shell, the check stops and its trap ends the hold. TERM ends the loop after
# the step it is in.
fuEWS_REFRESH_LOOP () {
  local myPARENT="$1"
  local mySTOP=""
  local myNEXT=$(( $(date +%s) + myEWS_HOLD_REFRESH ))
  local myLOOK=$(( $(date +%s) + myEWS_HOLD_WATCH ))
  local mySLEEP=""
  local myFAILED=0
  trap 'mySTOP="1"' TERM
  trap - EXIT
  while [ -z "${mySTOP}" ];
    do
      sleep "${myEWS_HOLD_TICK}" &
      mySLEEP=$!
      wait "${mySLEEP}"
      kill "${mySLEEP}" 2>/dev/null
      [ -z "${mySTOP}" ] || break
      fuEWS_ALIVE "${myPARENT}" || break
      if [ "$(date +%s)" -ge "${myLOOK}" ];
        then
          myLOOK=$(( $(date +%s) + myEWS_HOLD_WATCH ))
          if ! fuEWS_MARKER_THERE;
            then
              fuEWS_SAY ERROR "The hold marker was removed (T-Pot restarted?), the check stops."
              kill -TERM "${myPARENT}" 2>/dev/null
              break
          fi
      fi
      if [ "$(date +%s)" -ge "${myNEXT}" ];
        then
          myNEXT=$(( $(date +%s) + myEWS_HOLD_REFRESH ))
          if fuEWS_MARKER_WRITE renew > /dev/null;
            then
              myFAILED=0
            else
              myFAILED=$(( myFAILED + 1 ))
              fuEWS_SAY WARN "${myEWS_HOLD_DIR}/hold could not be written anew."
              if [ "${myFAILED}" -ge 2 ];
                then
                  fuEWS_SAY ERROR "The hold marker could not be refreshed, the check stops."
                  kill -TERM "${myPARENT}" 2>/dev/null
                  break
              fi
          fi
      fi
    done
}

# The loop in the background, without the lock (fd 9) and disowned: a plain `wait` of the script
# that sourced this file never waits for it
fuEWS_REFRESH_START () {
  fuEWS_REFRESH_LOOP "$$" 9>&- < /dev/null &
  myEWS_HOLD_REFRESH_PID=$!
  disown "${myEWS_HOLD_REFRESH_PID}" 2>/dev/null
}

# Ends the loop and waits until it is gone (disowned, so by kill -0), at most 10 s
fuEWS_REFRESH_STOP () {
  local myWAIT=0
  [ -n "${myEWS_HOLD_REFRESH_PID}" ] || return 0
  kill -TERM "${myEWS_HOLD_REFRESH_PID}" 2>/dev/null
  while fuEWS_ALIVE "${myEWS_HOLD_REFRESH_PID}" && [ "${myWAIT}" -lt 100 ];
    do
      sleep 0.1
      myWAIT=$(( myWAIT + 1 ))
    done
  myEWS_HOLD_REFRESH_PID=""
}

# The traps of a script under the hold: its own cleanup ($1, optional) and then the end of the
# hold, once, at EXIT and on INT or TERM, which end the script (130, 143). The signal traps do the
# end themselves: a signal that comes while the shell already exits runs its trap there, and an
# exit in it would leave out the EXIT trap. Each first ignores INT and TERM, so a second Ctrl+C
# never cuts the end short.
fuEWS_HOLD_TRAPS () {
  myEWS_HOLD_CLEANUP="${1:-}"
  myEWS_HOLD_TRAPPED="1"
  myEWS_HOLD_FINISHED=""
  trap fuEWS_HOLD_EXIT EXIT
  trap 'fuEWS_HOLD_SIGNAL 130' INT
  trap 'fuEWS_HOLD_SIGNAL 143' TERM
}

fuEWS_HOLD_FINISH () {
  [ -z "${myEWS_HOLD_FINISHED}" ] || return 0
  myEWS_HOLD_FINISHED="1"
  if [ -n "${myEWS_HOLD_CLEANUP}" ];
    then
      "${myEWS_HOLD_CLEANUP}"
  fi
  fuEWS_HOLD_END
  trap '' INT TERM
}

fuEWS_HOLD_SIGNAL () {
  trap '' INT TERM
  fuEWS_HOLD_FINISH
  exit "$1"
}

fuEWS_HOLD_EXIT () {
  local myRC=$?
  trap '' INT TERM
  fuEWS_HOLD_FINISH
  exit "${myRC}"
}

# The start of the hold: $1 the data folder, $2 the compose file in use. 0 when ewsposter holds or
# does not run here, 3 when the lock is taken or ewsposter did not confirm the hold (nothing may be
# probed then; the marker is gone again and the lock given back).
fuEWS_HOLD_BEGIN () {
  local myDATA="$1"
  local myCOMPOSE="$2"
  local myDEADLINE=""
  local myRUNS=0
  myEWS_HOLD_DIR="${myDATA}/ews"
  fuEWS_LOCK "${myDATA}" || return 3
  if ! fuEWS_IN_COMPOSE "${myCOMPOSE}";
    then
      fuEWS_SAY INFO "ewsposter does not run here, nothing to hold."
      return 0
  fi
  fuEWS_RUNS
  myRUNS=$?
  # held before the write: a signal during it still ends with the marker removed
  myEWS_HOLD_HELD="1"
  if ! myEWS_HOLD_MARKER_NS=$(fuEWS_MARKER_WRITE) || [ -z "${myEWS_HOLD_MARKER_NS}" ];
    then
      fuEWS_SAY ERROR "${myEWS_HOLD_DIR}/hold is a link or no regular file, or it cannot be written; nothing was probed."
      fuEWS_HOLD_END
      return 3
  fi
  # a container that is down starts held when it comes up (its entrypoint sees the marker)
  if [ "${myRUNS}" -eq 1 ];
    then
      myEWS_HOLD_ACKED="1"
      fuEWS_SAY INFO "ewsposter is not running; the hold marker is set, so it starts held if it comes up during the check."
      fuEWS_REFRESH_START
      return 0
  fi
  if [ "${myRUNS}" -eq 2 ];
    then
      fuEWS_SAY WARN "docker cannot say whether ewsposter runs; the check waits for its confirmation as if it ran."
  fi
  fuEWS_SAY INFO "Holding ewsposter: it sends nothing until the check is over (up to ${myEWS_HOLD_TIMEOUT} s for its confirmation) ..."
  myDEADLINE=$(( $(date +%s) + myEWS_HOLD_TIMEOUT ))
  while ! fuEWS_ACKED;
    do
      if [ "$(date +%s)" -ge "${myDEADLINE}" ];
        then
          fuEWS_SAY ERROR "$(fuEWS_TIMEOUT_TEXT)"
          fuEWS_HOLD_END
          return 3
      fi
      sleep "${myEWS_HOLD_POLL}"
    done
  myEWS_HOLD_ACKED="1"
  fuEWS_SAY OK "ewsposter holds, nothing leaves this host during the check."
  fuEWS_REFRESH_START
  return 0
}

# The end of the hold, once (also from the EXIT trap): wait for the last log lines, stop the
# loop, remove the marker, give the lock back. INT and TERM wait until it is through.
fuEWS_HOLD_END () {
  local myRC=0
  trap '' INT TERM
  if [ -n "${myEWS_HOLD_ACKED}" ] && [ "${myEWS_HOLD_SETTLE}" != "0" ];
    then
      fuEWS_SAY INFO "Waiting ${myEWS_HOLD_SETTLE} s for the last log lines of the check, then ewsposter sends again ..."
      sleep "${myEWS_HOLD_SETTLE}"
  fi
  myEWS_HOLD_ACKED=""
  fuEWS_REFRESH_STOP
  if [ -n "${myEWS_HOLD_HELD}" ];
    then
      myEWS_HOLD_HELD=""
      fuEWS_MARKER_REMOVE
      myRC=$?
      case "${myRC}" in
        0) fuEWS_SAY INFO "The hold of ewsposter is lifted, it sends again after its next round." ;;
        2) fuEWS_SAY WARN "${myEWS_HOLD_DIR}/hold is a link or no regular file now; T-Pot leaves it as it is." ;;
        3) fuEWS_SAY WARN "${myEWS_HOLD_DIR}/hold could not be removed; ewsposter ignores it after 30 minutes." ;;
      esac
  fi
  fuEWS_UNLOCK
  if [ -n "${myEWS_HOLD_TRAPPED}" ];
    then
      trap 'fuEWS_HOLD_SIGNAL 130' INT
      trap 'fuEWS_HOLD_SIGNAL 143' TERM
    else
      trap - INT TERM
  fi
}

fuEWS_HOLD_USAGE () {
  cat <<EOF
Usage: ews_hold.sh [--data DIR] [--compose FILE] [--timeout SEC] [--settle SEC] run -- COMMAND [ARGS ...]

Runs COMMAND while ewsposter sends nothing (the hold of the checks of T-Pot),
one check at a time. COMMAND runs in a process group of its own and does not
read the terminal; when the check ends early, the whole group is stopped
before the hold ends.

  --data DIR       The data folder, default: TPOT_DATA_PATH of ~/tpotce/.env
  --compose FILE   The compose file in use, default: ~/tpotce/docker-compose.yml
  --timeout SEC    How long to wait for ewsposter to confirm the hold (${myEWS_HOLD_TIMEOUT})
  --settle SEC     How long to wait for the last log lines at the end (${myEWS_HOLD_SETTLE})
  -h               Show this help message

Exit codes: those of COMMAND; 2 a wrong option; 3 another check is running or
ewsposter did not confirm the hold (COMMAND did not run).
EOF
}

# COMMAND in a process group of its own (python3: setpgid, INT back to its default, exec), in the
# background, so a TERM of the loop stops the check at once; it reads no terminal (a background
# group that reads one is stopped), otherwise the stdin of the helper
myEWS_HOLD_GROUP='import os, signal, sys
os.setpgid(0, 0)
signal.signal(signal.SIGINT, signal.SIG_DFL)
os.execvp(sys.argv[1], sys.argv[1:])'

fuEWS_HOLD_MAIN () {
  local myDIR="${TPOTCE_DIR:-${HOME}/tpotce}"
  local myDATA=""
  local myCOMPOSE=""
  local myRC=0
  while [ $# -gt 0 ];
    do
      case "$1" in
        --data) myDATA="${2:-}"; shift 2 || { fuEWS_HOLD_USAGE >&2; exit 2; } ;;
        --compose) myCOMPOSE="${2:-}"; shift 2 || { fuEWS_HOLD_USAGE >&2; exit 2; } ;;
        --timeout) myEWS_HOLD_TIMEOUT="${2:-}"; shift 2 || { fuEWS_HOLD_USAGE >&2; exit 2; } ;;
        --settle) myEWS_HOLD_SETTLE="${2:-}"; shift 2 || { fuEWS_HOLD_USAGE >&2; exit 2; } ;;
        -h|--help) fuEWS_HOLD_USAGE; exit 0 ;;
        run) shift; break ;;
        *) fuEWS_SAY ERROR "Unknown option $1."; fuEWS_HOLD_USAGE >&2; exit 2 ;;
      esac
    done
  [ "${1:-}" = "--" ] && shift
  if [ $# -eq 0 ];
    then
      fuEWS_SAY ERROR "No command to run."
      fuEWS_HOLD_USAGE >&2
      exit 2
  fi
  case "${myEWS_HOLD_TIMEOUT}:${myEWS_HOLD_SETTLE}" in
    :*|*:|*[!0-9:]*)
      fuEWS_SAY ERROR "--timeout and --settle take whole seconds."
      exit 2 ;;
  esac
  [ -n "${myDATA}" ] || myDATA=$(fuEWS_DATA_PATH "${myDIR}")
  [ -n "${myCOMPOSE}" ] || myCOMPOSE="${myDIR}/docker-compose.yml"
  fuEWS_HOLD_TRAPS fuEWS_HOLD_STOP_CHILD
  fuEWS_HOLD_BEGIN "${myDATA}" "${myCOMPOSE}" || exit 3
  # a trap between the fork and the assignment finds the child as $! (FORKING, LAST)
  myEWS_HOLD_LAST="${!:-}"
  myEWS_HOLD_FORKING="1"
  if [ -t 0 ];
    then
      python3 -c "${myEWS_HOLD_GROUP}" "$@" 9>&- < /dev/null &
    else
      python3 -c "${myEWS_HOLD_GROUP}" "$@" 9>&- 0<&0 &
  fi
  myEWS_HOLD_CHILD=$!
  myEWS_HOLD_FORKING=""
  wait "${myEWS_HOLD_CHILD}"
  myRC=$?
  myEWS_HOLD_CHILD=""
  exit "${myRC}"
}

# The command of `run` when the check ends early (Ctrl+C, TERM): TERM to its whole process group,
# then wait for it and for the rest of the group (at most 30 s, then KILL)
fuEWS_HOLD_STOP_CHILD () {
  local myPID="${myEWS_HOLD_CHILD}"
  local myWAIT=0
  if [ -z "${myPID}" ] && [ -n "${myEWS_HOLD_FORKING}" ] && [ "${!:-}" != "${myEWS_HOLD_LAST}" ];
    then
      myPID="${!}"
  fi
  [ -n "${myPID}" ] || return 0
  kill -TERM -- "-${myPID}" 2>/dev/null || kill -TERM "${myPID}" 2>/dev/null
  wait "${myPID}" 2>/dev/null
  while fuEWS_GROUP_ALIVE "${myPID}" && [ "${myWAIT}" -lt 300 ];
    do
      sleep 0.1
      myWAIT=$(( myWAIT + 1 ))
    done
  kill -KILL -- "-${myPID}" 2>/dev/null
  myEWS_HOLD_CHILD=""
}

case "${0##*/}" in
  ews_hold.sh) fuEWS_HOLD_MAIN "$@" ;;
esac
