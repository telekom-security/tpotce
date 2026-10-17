#!/bin/bash
# ews_hold.sh: the hold of ewsposter and the lock of the checks of T-Pot, the same as
# tpotctl/checks/ewshold.py. hptest.sh and attackmap_pipeline_test.sh source it; as a command it
# runs another one under the hold:
#
#   ews_hold.sh [--data DIR] [--compose FILE] [--timeout SEC] [--settle SEC] run -- COMMAND [ARGS ...]
#
# The lock is flock on <data>/ews/check.lock, without waiting: one check at a time. When ewsposter
# is in the compose file and its container runs, the marker <data>/ews/hold is written and the
# check waits up to 180 s for a <data>/ews/hold.ack written after it (ewsposter then sends nothing,
# docker/ewsposter/dist/entrypoint.sh); without one the marker goes and nothing is probed. When
# ewsposter is in the compose file but its container does not run, the marker is set without
# waiting (ewsposter starts held when it comes up); without ewsposter in the compose file there is
# no marker. While the hold lasts, the marker is written anew every 5 minutes (ewsposter ignores
# one older than 30 minutes), by a loop that ends with the helper, also when it is killed; when
# that fails twice in a row, the loop sends TERM to the script and the check stops. At the end (EXIT, also
# after INT or TERM) the helper waits a few seconds for the last log lines of the check, removes
# the marker and gives the lock back. Files in <data>/ews are never reached through a link: the
# folder is writable for the group tpot. Needs flock (util-linux on the host, busybox in the
# tpotinit image) and python3 (for the file steps a shell cannot do through a folder descriptor).
#
# Exit codes of the command: those of COMMAND; 2 a wrong option; 3 the lock is taken or ewsposter
# did not confirm the hold (COMMAND did not run).

# the most seconds to wait for hold.ack: one normal pass of ewsposter (10 to 60 s apart) plus the
# idle passes
myEWS_HOLD_TIMEOUT=${myEWS_HOLD_TIMEOUT:-180}
myEWS_HOLD_POLL=${myEWS_HOLD_POLL:-1}
# the marker is written anew after this many seconds; the loop looks at its parent every tick
myEWS_HOLD_REFRESH=${myEWS_HOLD_REFRESH:-300}
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
myEWS_HOLD_TRAPPED=""
myEWS_HOLD_FINISHED=""
myEWS_HOLD_REFRESH_PID=""
myEWS_HOLD_CLEANUP=""
myEWS_HOLD_CHILD=""

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

# 0 when the ewsposter container runs
fuEWS_RUNS () {
  [ "$(docker inspect -f '{{.State.Running}}' ewsposter 2>/dev/null)" = "true" ]
}

# 0 when $1 is missing or a regular file, no link
fuEWS_PLAIN () {
  [ ! -L "$1" ] && { [ ! -e "$1" ] || [ -f "$1" ]; }
}

# 0 when fd 9 is the file $1, and that is a regular file, no link, with no other name (no hard link)
fuEWS_IS_FD9 () {
  python3 -c 'import os, stat, sys
opened, named = os.fstat(9), os.lstat(sys.argv[1])
same = (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)
sys.exit(0 if same and stat.S_ISREG(named.st_mode) and named.st_nlink == 1 else 1)' "$1" 2>/dev/null
}

# The lock on fd 9: check.lock made 0660 with O_EXCL (no link followed), opened read-only (no
# O_CREAT, flock needs no more), then checked to be the regular file of that name with no other
# name (no link, no hard link); flock without waiting
fuEWS_LOCK () {
  local myLOCK="${myEWS_HOLD_DIR}/check.lock"
  if ! fuEWS_PLAIN "${myLOCK}";
    then
      fuEWS_SAY ERROR "${myLOCK} is a link or no regular file; T-Pot leaves it as it is."
      return 3
  fi
  if [ ! -e "${myLOCK}" ];
    then
      ( umask 117; set -C; : > "${myLOCK}" ) 2>/dev/null
  fi
  if ! { exec 9<"${myLOCK}"; } 2>/dev/null;
    then
      fuEWS_SAY ERROR "${myLOCK} cannot be opened."
      return 3
  fi
  if ! fuEWS_IS_FD9 "${myLOCK}";
    then
      exec 9>&-
      fuEWS_SAY ERROR "${myLOCK} is a link or no regular file; T-Pot leaves it as it is."
      return 3
  fi
  if ! flock -n 9;
    then
      exec 9>&-
      fuEWS_SAY ERROR "Another check is running."
      return 3
  fi
  myEWS_HOLD_LOCKED="1"
}

# The marker, written new through the descriptor of the folder (no link on the way): refused when
# it is a link or no regular file; a temporary file with O_EXCL and O_NOFOLLOW, 0660 and the group
# of the folder set on its descriptor, renamed into place (a rename never follows a link and never
# moves into a folder). Prints the mtime of the new marker in ns. With "renew" only a marker that
# is there is written anew, so a renewal never brings back one the end of the hold removed.
fuEWS_MARKER_WRITE () {
  python3 -c 'import os, stat, sys, time, uuid
folder = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    try:
        if not stat.S_ISREG(os.stat("hold", dir_fd=folder, follow_symlinks=False).st_mode):
            sys.exit(1)
    except FileNotFoundError:
        if sys.argv[3] == "renew":
            sys.exit(1)
    temp = "hold.%s.tmp" % uuid.uuid4().hex
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
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

# The renewal of the marker: every myEWS_HOLD_REFRESH seconds while the shell that started it ($1)
# runs; it looks every myEWS_HOLD_TICK seconds, so it ends soon after a kill of that shell. TERM
# ends it after the step it is in. When the renewal fails twice in a row (the marker is gone, a
# link or cannot be written) it sends TERM to that shell: the check stops, its trap ends the hold.
fuEWS_REFRESH_LOOP () {
  local myPARENT="$1"
  local mySTOP=""
  local myNEXT=$(( $(date +%s) + myEWS_HOLD_REFRESH ))
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

# The renewal in the background, without the lock (fd 9) and disowned: a plain `wait` of the
# script that sourced this file never waits for it
fuEWS_REFRESH_START () {
  fuEWS_REFRESH_LOOP "$$" 9>&- < /dev/null &
  myEWS_HOLD_REFRESH_PID=$!
  disown "${myEWS_HOLD_REFRESH_PID}" 2>/dev/null
}

# Ends the renewal and waits until it is gone (disowned, so by kill -0), at most 10 s
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
  myEWS_HOLD_DIR="${myDATA}/ews"
  if [ ! -d "${myDATA}" ];
    then
      fuEWS_SAY ERROR "The data folder ${myDATA} does not exist."
      return 3
  fi
  if [ -L "${myEWS_HOLD_DIR}" ] || { [ -e "${myEWS_HOLD_DIR}" ] && [ ! -d "${myEWS_HOLD_DIR}" ]; };
    then
      fuEWS_SAY ERROR "${myEWS_HOLD_DIR} is a link or no folder; T-Pot leaves it as it is."
      return 3
  fi
  if [ ! -d "${myEWS_HOLD_DIR}" ] && ! mkdir -m 770 "${myEWS_HOLD_DIR}" 2>/dev/null;
    then
      fuEWS_SAY ERROR "${myEWS_HOLD_DIR} cannot be made."
      return 3
  fi
  fuEWS_LOCK || return 3
  if ! fuEWS_IN_COMPOSE "${myCOMPOSE}";
    then
      fuEWS_SAY INFO "ewsposter does not run here, nothing to hold."
      return 0
  fi
  # held before the write: a signal during it still ends with the marker removed
  myEWS_HOLD_HELD="1"
  if ! myEWS_HOLD_MARKER_NS=$(fuEWS_MARKER_WRITE) || [ -z "${myEWS_HOLD_MARKER_NS}" ];
    then
      fuEWS_SAY ERROR "${myEWS_HOLD_DIR}/hold is a link or no regular file, or it cannot be written; nothing was probed."
      fuEWS_HOLD_END
      return 3
  fi
  # a container that is down starts held when it comes up (its entrypoint sees the marker)
  if ! fuEWS_RUNS;
    then
      myEWS_HOLD_ACKED="1"
      fuEWS_SAY INFO "ewsposter is not running; the hold marker is set, so it starts held if it comes up during the check."
      fuEWS_REFRESH_START
      return 0
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
# renewal, remove the marker, give the lock back. INT and TERM wait until it is through.
fuEWS_HOLD_END () {
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
      if [ -f "${myEWS_HOLD_DIR}/hold" ] && [ ! -L "${myEWS_HOLD_DIR}/hold" ];
        then
          if rm -f "${myEWS_HOLD_DIR}/hold";
            then
              fuEWS_SAY INFO "The hold of ewsposter is lifted, it sends again after its next round."
            else
              fuEWS_SAY WARN "${myEWS_HOLD_DIR}/hold could not be removed; ewsposter ignores it after 30 minutes."
          fi
      elif [ -e "${myEWS_HOLD_DIR}/hold" ] || [ -L "${myEWS_HOLD_DIR}/hold" ];
        then
          fuEWS_SAY WARN "${myEWS_HOLD_DIR}/hold is a link or no regular file now; T-Pot leaves it as it is."
      fi
  fi
  if [ -n "${myEWS_HOLD_LOCKED}" ];
    then
      exec 9>&-
      myEWS_HOLD_LOCKED=""
  fi
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
one check at a time.

  --data DIR       The data folder, default: TPOT_DATA_PATH of ~/tpotce/.env
  --compose FILE   The compose file in use, default: ~/tpotce/docker-compose.yml
  --timeout SEC    How long to wait for ewsposter to confirm the hold (${myEWS_HOLD_TIMEOUT})
  --settle SEC     How long to wait for the last log lines at the end (${myEWS_HOLD_SETTLE})
  -h               Show this help message

Exit codes: those of COMMAND; 2 a wrong option; 3 another check is running or
ewsposter did not confirm the hold (COMMAND did not run).
EOF
}

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
  # the command runs in the background (wait ends at once on a signal, so a TERM of the renewal
  # stops the check at once), its stdin is that of the helper; the traps end it before the hold
  fuEWS_HOLD_TRAPS fuEWS_HOLD_STOP_CHILD
  fuEWS_HOLD_BEGIN "${myDATA}" "${myCOMPOSE}" || exit 3
  "$@" 9>&- 0<&0 &
  myEWS_HOLD_CHILD=$!
  wait "${myEWS_HOLD_CHILD}"
  myRC=$?
  myEWS_HOLD_CHILD=""
  exit "${myRC}"
}

# The command of `run` when the check ends early (Ctrl+C, TERM)
fuEWS_HOLD_STOP_CHILD () {
  [ -n "${myEWS_HOLD_CHILD:-}" ] || return 0
  kill -TERM "${myEWS_HOLD_CHILD}" 2>/dev/null
  wait "${myEWS_HOLD_CHILD}" 2>/dev/null
}

case "${0##*/}" in
  ews_hold.sh) fuEWS_HOLD_MAIN "$@" ;;
esac
