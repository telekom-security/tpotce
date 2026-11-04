#!/usr/bin/env bash
# The T-Pot installer of a T-Pot ISO installation (/usr/local/share/tpot-iso/start.sh).
# tpot-iso.service starts it after the boot on the console (tty8) as the user created during the
# installation, the login hook (/etc/profile.d/tpot-iso.sh) at a login, by hand:
#   bash /usr/local/share/tpot-iso/start.sh [-b <branch>] [-r <repository>]
# On a screen of this machine (tty8, a login on a VT) it runs again in the graphical terminal of
# the ISO (terminal.sh: Xorg with one fullscreen xterm, no desktop) and watches it (fuWATCH): X up
# within 30 s, the key S there within 60 s. Where it cannot start (no graphics device, its packages
# missing), does not start or its screen stays black it asks on the text console: the graphical
# terminal again, the text console (kept until the next boot) or later. At a terminal it
# asks which T-Pot: the one of this ISO (its install.sh with the repository and the branch of
# iso.conf, the same installer as on a prepared Debian), the newest of that branch (fetched into
# ~/tpotce, its own install.sh) or another branch or repository for a test (cloned into ~/tpotce,
# its own install.sh; the T-Pot of the ISO that prepare.sh put there is moved aside for it while
# untouched). Without a terminal the one of the ISO. One installer at a time: a lock in /run/lock
# that goes along to install.sh. The wheels of the ISO go to tpot setup (TPOT_WHEELS) of either
# installer.

unset CDPATH
myDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
myCONF="${myDIR}/iso.conf"
myDEST="${HOME}/tpotce"
myBACK="./update.sh -y -b master"
myLOCK="${TPOT_ISO_LOCK:-/run/lock/tpot-iso.lock}"
myINSTALLED="${TPOT_ISO_INSTALLED:-/etc/systemd/system/tpot.service}"
myLOG="${XDG_STATE_HOME:-${HOME}/.local/state}/tpot/start.log"
myLATER="The T-Pot installer starts again after the next boot or at a login"
myBUSY_HINT="Wait until it ends, or end it there first (on the console: sudo systemctl stop tpot-iso.service)."
myARGS=("$@")
# the run in the graphical terminal of a screen (fuWATCH): it got this far (ran), the key S shows
# that its screen is seen (fuWELCOME)
if [ -n "${TPOT_ISO_RAN:-}" ]; then : > "${TPOT_ISO_RAN}" 2>/dev/null; unset TPOT_ISO_RAN; fi
mySEEN="${TPOT_ISO_SEEN:-}"
unset TPOT_ISO_SEEN
# the watch (fuWATCH): X up within myX_WAIT s, the key S within myKEY_WAIT s; the graphics devices
# in myDEV_DIR; the text console chosen in this boot (/run/lock is emptied at the boot)
myX_WAIT="${TPOT_ISO_X_WAIT:-30}"
myKEY_WAIT="${TPOT_ISO_KEY_WAIT:-60}"
[[ "${myX_WAIT}" =~ ^[0-9]{1,4}$ ]] || myX_WAIT=30
[[ "${myKEY_WAIT}" =~ ^[0-9]{1,4}$ ]] || myKEY_WAIT=60
myX_WAIT=$((10#${myX_WAIT})) myKEY_WAIT=$((10#${myKEY_WAIT}))
myDEV_DIR="${TPOT_ISO_DEV:-/dev}"
myTEXT="${TPOT_ISO_TEXT:-/run/lock/tpot-iso.text}"
# the console of tpot-iso.service closes with it: what it says there waits for Enter (fuPAUSE)
myPAUSE=""
if [ -n "${TPOT_ISO_PAUSE:-}" ] || { [ -n "${TPOT_ISO_CONSOLE:-}" ] && [ -z "${TPOT_ISO_TERMINAL_TRIED:-}" ]; }; then myPAUSE=1; fi
unset TPOT_ISO_PAUSE

# the look of the T-Pot scripts: the ui.sh of this ISO, else the one of ~/tpotce
myUI_VERSION=""
[ ! -r "${myDIR}/version" ] || IFS= read -r myUI_VERSION < "${myDIR}/version"
myUI=""
for myFILE in "${myDIR}/ui.sh" "${myDEST}/installer/lib/ui.sh"; do
  if [ -r "${myFILE}" ]; then myUI="${myFILE}"; break; fi
done
if [ -z "${myUI}" ];
  then
    echo "T-Pot ISO: ${myDIR}/ui.sh is missing, the T-Pot installer cannot start." >&2
    echo "  Run it by hand: bash ${myDIR}/install.sh" >&2
    exit 1
fi
# shellcheck source=../lib/ui.sh
. "${myUI}"
fuUI_INIT
myVERSION="${myUI_VERSION:-$(fuUI_VERSION)}"

fuCONF () {
  # the value of $1 in iso.conf: KEY=VALUE lines, read and never sourced
  local myKEY myVALUE
  [ -r "${myCONF}" ] || return 1
  while IFS='=' read -r myKEY myVALUE; do
    if [ "${myKEY}" = "$1" ];
      then
        printf '%s\n' "${myVALUE}"
        return 0
    fi
  done < "${myCONF}"
  return 1
}

fuPAUSE () {
  local myIN
  [ -n "${myPAUSE}" ] && [ -t 0 ] || return 0
  printf '%s' "Enter closes the T-Pot installer. " >&2
  read -r myIN || true
}

fuSTOP () {
  fuUI_ERROR "$1"
  shift
  [ "$#" -eq 0 ] || fuUI_HINT "$@" >&2
  fuPAUSE
  exit 1
}

fuUSAGE () {
  fuUI_ERROR "$1"
  fuUI_HINT "bash ${myDIR}/start.sh -h shows the options." >&2
  exit 2
}

fuLATER () {
  # nothing installed: rc $1
  echo
  fuUI_INFO "Nothing installed. ${myLATER} (by hand: bash ${myDIR}/start.sh)."
  exit "$1"
}

fuDONE () {
  fuUI_OK "T-Pot is installed already (${myINSTALLED}), the T-Pot installer has nothing to do."
  fuUI_HINT "The T-Pot Manager: tpot"
  exit 0
}

fuVALID_REPO () {
  # no option, no space, no character a shell or git would read on its own (build.sh: the same)
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._~:/@%+=-]*$ ]]
}

fuVALID_BRANCH () {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._/+-]*$ ]]
}

fuNORMALIZE_REPO () {
  # without a trailing slash or .git (install.sh: normalize_repo)
  local myURL="${1%/}"
  echo "${myURL%.git}"
}

fuTRIM () {
  # fuTRIM <text>: the text without the spaces around it
  local myV="$1"
  myV="${myV#"${myV%%[![:space:]]*}"}"
  printf '%s' "${myV%"${myV##*[![:space:]]}"}"
}

fuHELP () {
  fuUI_HELP "ISO installer" "bash ${myDIR}/start.sh [-b <branch>] [-r <repository>]" \
    --about "Installs T-Pot on this Debian. tpot-iso.service starts it after the boot on the console (tty8, Alt+F8), a login starts it too, until T-Pot is installed." \
    --about "On a screen of this machine it opens the graphical terminal of the ISO and waits for the key S there; where that does not start or its screen stays black, it asks on the text console." \
    --about "At a terminal it asks which T-Pot: the one of this ISO (${myISO_REPO} at ${myISO_BRANCH}), the newest of that branch, or another branch or repository to test it. Without a terminal it installs the one of this ISO." \
    --opt "-b <branch>" "another branch, e.g. to test it; it is cloned into ~/tpotce and its own install.sh runs. A tag or a commit works too, update.sh cannot update it." \
    --opt "-r <repository>" "another repository (default: the one of this ISO)" \
    --opt "-h" "this help" \
    --note "One T-Pot installer at a time: a second one says where the first runs." \
    --note "No start after the boot: sudo systemctl disable tpot-iso.service"
}

fuISO_SOURCE () {
  [ "${myBRANCH}" = "${myISO_BRANCH}" ] && [ "${myREPO}" = "${myISO_REPO}" ]
}

fuCHECK_SOURCE () {
  # does the repository have the branch? rc 0 a branch, 1 not to be had (said why), 2 a tag or a
  # commit (said so: update.sh cannot update it)
  local myRC=0
  command -v git >/dev/null \
    || fuSTOP "git is missing, it gets T-Pot from ${myREPO}." "sudo apt-get install git, then: bash ${myDIR}/start.sh -b ${myBRANCH} -r ${myREPO}"
  GIT_TERMINAL_PROMPT=0 git ls-remote --exit-code --heads "${myREPO}" "refs/heads/${myBRANCH}" >/dev/null 2>&1 || myRC=$?
  [ "${myRC}" -ne 0 ] || return 0
  if [ "${myRC}" -ne 2 ];
    then
      fuUI_ERROR "${myREPO} cannot be reached (git ls-remote ended with ${myRC})."
      return 1
  fi
  if [[ "${myBRANCH}" =~ ^[0-9a-f]{40}$ ]] \
     || GIT_TERMINAL_PROMPT=0 git ls-remote --exit-code --tags "${myREPO}" "refs/tags/${myBRANCH}" >/dev/null 2>&1;
    then
      fuUI_WARN "${myBRANCH} is a tag or a commit, no branch: T-Pot is installed from it, but update.sh cannot update it (later on a branch again: ${myBACK})."
      return 2
  fi
  fuUI_ERROR "${myREPO} has no branch ${myBRANCH}."
  return 1
}

fuWHERE () {
  # where this installer runs, for another one that finds it running
  local myTTY
  if [ -n "${TPOT_ISO_CONSOLE:-}" ];
    then echo "on the console (tty8, Alt+F8)"
  elif [ -n "${SSH_CONNECTION:-}" ];
    then echo "in an SSH login from ${SSH_CONNECTION%% *}"
  elif myTTY=$(tty 2>/dev/null);
    then echo "on ${myTTY}"
    else echo "without a terminal"
  fi
}

fuBUSY () {
  # 0 while another installer holds the lock, who and where in myHOLDER. A lock only a leftover of
  # a finished one holds (a process that inherited it, i.e. an agent) does not count
  local myPID="" myWHO=""
  myHOLDER=""
  command -v flock >/dev/null 2>&1 && [ -r "${myLOCK}" ] || return 1
  flock -n 8 8<"${myLOCK}" 2>/dev/null && return 1
  { IFS= read -r myPID; IFS= read -r myWHO; } < "${myLOCK}" 2>/dev/null
  [[ "${myPID}" =~ ^[0-9]+$ ]] && fuUI_ALIVE "${myPID}" || return 1
  myHOLDER="${myWHO:-process ${myPID}}"
  return 0
}

fuLOCK () {
  # one installer at a time: fd 9 goes along with exec to install.sh and the T-Pot installer, the
  # lock ends with the last of them. Without flock or the file: no lock
  command -v flock >/dev/null 2>&1 || return 0
  { exec 9>>"${myLOCK}"; } 2>/dev/null || { exec 9<"${myLOCK}"; } 2>/dev/null || return 0
  if ! flock -n 9 2>/dev/null;
    then
      exec 9>&-
      ! fuBUSY || fuSTOP "The T-Pot installer runs already: ${myHOLDER}." "${myBUSY_HINT}"
      fuUI_WARN "A finished T-Pot installer left its lock (${myLOCK}), this one goes on without it."
      return 0
  fi
  if [ -w "${myLOCK}" ];
    then
      printf '%s\n%s\n' "$$" "$(id -un) $(fuWHERE) since $(date '+%H:%M')" > "${myLOCK}" 2>/dev/null
      chmod 0644 "${myLOCK}" 2>/dev/null
  fi
  # one that ended while this one asked
  [ ! -e "${myINSTALLED}" ] || fuDONE
  return 0
}

fuCOLOR_TEST () {
  # an SSH login: the terminal may have true colour without saying so (COLORTERM rarely comes
  # along), one look at a bar tells. Yes goes to tpot.json of the T-Pot Manager too
  local myI myN=48 myR myG myB myROW=""
  [ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ] && [ -t 0 ] && [ -t 1 ] || return 0
  [ -z "${TPOT_COLORS:-}" ] && [ -z "${NO_COLOR:-}" ] && ! fuUI_DUMB || return 0
  case "$(fuUI_PREF colors)" in ""|auto) ;; *) return 0 ;; esac
  [ "$(fuUI_COLORS)" != "truecolor" ] || return 0
  if fuUI_TERM_SIZE && [ "${myUI_COLS}" -lt $((myN + 4)) ]; then myN=$((myUI_COLS - 4)); fi
  [ "${myN}" -ge 8 ] || return 0
  # magenta to petrol, the colours of the logo
  for ((myI = 0; myI < myN; myI++)); do
    myR=$((226 - 225 * myI / (myN - 1))) myG=$((68 * myI / (myN - 1))) myB=$((116 - 17 * myI / (myN - 1)))
    myROW+=$'\033'"[48;2;${myR};${myG};${myB}m "
  done
  myROW+=$'\033[0m'
  echo
  printf '  %s\n  %s\n\n' "${myROW}" "${myROW}"
  if ! fuUI_CONFIRM --default no "Does the bar above run smoothly from magenta to dark blue, without steps?" "Smoothly" "With steps";
    then
      echo
      return 0
  fi
  export TPOT_COLORS=truecolor COLORTERM=truecolor
  fuUI_INIT
  if [ -d "${myDEST}/tpotctl" ] && python3 - "${myDEST}" >/dev/null 2>&1 <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from tpotctl import prefs
chosen = prefs.load(environment=False)
chosen.colors = "truecolor"
sys.exit(0 if prefs.save(chosen) else 1)
PY
    then fuUI_OK "True colour, the T-Pot Manager keeps it (${XDG_CONFIG_HOME:-~/.config}/tpotce/tpot.json)."
    else fuUI_OK "True colour for this run."
  fi
  echo
}

fuNEWEST_LOOKUP () {
  # fuNEWEST_LOOKUP <file>: the newest commit of the branch of the ISO into the file, empty
  # without one (no network); never fails, the spinner shows no error for it
  local myOUT=""
  local -a myLIMIT=()
  ! command -v timeout >/dev/null 2>&1 || myLIMIT=(timeout 15)
  myOUT=$(GIT_TERMINAL_PROMPT=0 ${myLIMIT[@]+"${myLIMIT[@]}"} git ls-remote --heads "${myISO_REPO}" \
          "refs/heads/${myISO_BRANCH}" 2>/dev/null) || myOUT=""
  myOUT="${myOUT%%[[:space:]]*}"
  [[ "${myOUT}" =~ ^[0-9a-f]{40}$ ]] || myOUT=""
  printf '%s\n' "${myOUT}" > "$1"
  return 0
}

fuNEWEST_FETCH () {
  # the T-Pot of the ISO in ~/tpotce (fuISO_CLONE) to the newest commit of its branch
  GIT_TERMINAL_PROMPT=0 git -C "${myDEST}" fetch -q --depth 1 --no-tags origin \
    "+refs/heads/${myISO_BRANCH}:refs/remotes/origin/${myISO_BRANCH}" \
    && git -C "${myDEST}" reset -q --hard "refs/remotes/origin/${myISO_BRANCH}"
}

fuOTHER () {
  # another branch (and repository) for a test: rc 0 installs it, 1 goes back to the choice
  local myIN myRC=0
  echo
  myIN=$(fuUI_INPUT "Branch, tag or commit (empty: back)") || return 1
  myIN=$(fuTRIM "${myIN}")
  [ -n "${myIN}" ] || return 1
  if ! fuVALID_BRANCH "${myIN}";
    then
      fuUI_WARN "Not a branch, tag or commit: ${myIN}"
      return 1
  fi
  myBRANCH="${myIN}"
  myIN=$(fuUI_INPUT "Repository (empty: ${myISO_REPO})") || return 1
  myIN=$(fuTRIM "${myIN}")
  myREPO="${myISO_REPO}"
  [ -z "${myIN}" ] || myREPO=$(fuNORMALIZE_REPO "${myIN}")
  if ! fuVALID_REPO "${myREPO}";
    then
      fuUI_WARN "Not a repository URL: ${myREPO}"
      return 1
  fi
  fuISO_SOURCE && return 0
  fuCHECK_SOURCE || myRC=$?
  [ "${myRC}" -ne 0 ] || return 0
  [ "${myRC}" -eq 2 ] || return 1
  fuUI_CONFIRM --default no "Install it anyway?" "Install" "Back"
}

fuDIALOG () {
  # at a terminal: which T-Pot. Sets myPICK (iso, newest, other), myBRANCH and myREPO
  local myCOMMIT myNEWEST="" myFILE
  local -a myITEMS=()
  myCOMMIT=$(fuCONF TPOT_COMMIT) || myCOMMIT=""
  [[ "${myCOMMIT}" =~ ^[0-9a-f]{40}$ ]] || myCOMMIT=""
  myUI_LOGO=1
  fuUI_BANNER "Installer" "T-Pot ${myVERSION} of this ISO: ${myISO_REPO} at ${myISO_BRANCH}" \
    "Abort keeps a plain Debian. ${myLATER}."
  # the newest of the branch: only for the T-Pot of the ISO as prepare.sh left it
  if [ -n "${myCOMMIT}" ] && [[ ! "${myISO_BRANCH}" =~ ^[0-9a-f]{40}$ ]] && fuISO_CLONE \
     && myFILE=$(mktemp "${TMPDIR:-/tmp}/tpot-iso.XXXXXXXX");
    then
      fuUI_SPIN "Looking for a newer ${myISO_BRANCH} ..." "${myLOG}" fuNEWEST_LOOKUP "${myFILE}" || true
      IFS= read -r myNEWEST < "${myFILE}" || true
      rm -f "${myFILE}"
      if [ -z "${myNEWEST}" ];
        then fuUI_WARN "${myISO_REPO} cannot be reached now, the T-Pot installer needs the internet for the images of T-Pot."
      elif [ "${myNEWEST}" = "${myCOMMIT}" ];
        then fuUI_INFO "This ISO carries the newest ${myISO_BRANCH}."; myNEWEST=""
      fi
      echo
  fi
  while true; do
    myITEMS=("T-Pot ${myVERSION} of this ISO (${myISO_BRANCH}${myCOMMIT:+ at ${myCOMMIT:0:7}}):iso")
    [ -z "${myNEWEST}" ] || myITEMS+=("The newest ${myISO_BRANCH} (${myNEWEST:0:7}):newest")
    myITEMS+=("Another branch or repository, to test it:other" "Quit, install later:quit")
    myPICK=$(fuUI_CHOOSE --selected iso "Which T-Pot should be installed?" "${myITEMS[@]}") || myPICK=""
    myBRANCH="${myISO_BRANCH}" myREPO="${myISO_REPO}"
    case "${myPICK}" in
      iso|newest) return 0 ;;
      other) if fuOTHER; then return 0; fi; echo ;;
      quit) fuLATER 0 ;;
      *) fuLATER 1 ;;
    esac
  done
}

fuVT () {
  # a login on a VT of this machine, not over SSH, not inside X: the graphical terminal of the ISO
  # can start there (terminal.sh: the same rule)
  local myTTY
  [ "${TPOT_TERMINAL:-auto}" != "off" ] || return 1
  [ -z "${SSH_CONNECTION:-}${SSH_TTY:-}${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || return 1
  myTTY=$(tty 2>/dev/null) || return 1
  [[ "${myTTY}" =~ ^/dev/tty[1-9][0-9]*$ ]]
}

fuGRAPHICAL () {
  # can the graphical terminal of the ISO start? rc 1 with the reason in myWHY: tools (its
  # packages, the missing one in myMISSING), device (no /dev/dri/card*, no /dev/fb*; a render
  # node is no screen), text (the text console chosen in this boot)
  local myTOOL myDEV
  for myTOOL in startx xauth Xorg xterm matchbox-window-manager; do
    if ! command -v "${myTOOL}" >/dev/null 2>&1; then myWHY="tools" myMISSING="${myTOOL}"; return 1; fi
  done
  if [ ! -r "${myDIR}/terminal.sh" ]; then myWHY="tools" myMISSING="${myDIR}/terminal.sh"; return 1; fi
  myWHY="device"
  for myDEV in "${myDEV_DIR}"/dri/card* "${myDEV_DIR}"/fb*; do
    if [ -e "${myDEV}" ]; then myWHY=""; break; fi
  done
  [ -z "${myWHY}" ] || return 1
  if [ -e "${myTEXT}" ]; then myWHY="text"; return 1; fi
  return 0
}

fuVT_TEXT () {
  # a stopped Xorg may leave the VT in graphics mode with its keyboard off: text again (KDSETMODE
  # KD_TEXT) and the keys of a UTF-8 console (KDSKBMODE K_UNICODE); not a VT: nothing
  command -v python3 >/dev/null 2>&1 || return 0
  python3 -c 'import array, fcntl
def get(request):
    value = array.array("i", [0])
    fcntl.ioctl(0, request, value, True)
    return value[0]
try:
    if get(0x4B3B) != 0:
        fcntl.ioctl(0, 0x4B3A, 0)
    if get(0x4B44) not in (1, 3):
        fcntl.ioctl(0, 0x4B45, 3)
except OSError:
    pass' 2>/dev/null || true
}

fuX_RUN () {
  # start.sh again in the graphical terminal of the ISO, watched: X up within myX_WAIT s (ran),
  # the key S there within myKEY_WAIT s (seen, fuWELCOME); then this one ends with its rc. Else
  # the terminal is stopped and myWHY says why: nox, nokey
  local myRUN myJOB myRC myEND
  if ! myRUN=$(mktemp -d "${TMPDIR:-/tmp}/tpot-iso.XXXXXXXX"); then myWHY="nox"; return 0; fi
  # <&0: the keys of this terminal (bash gives a job without job control /dev/null instead)
  { TPOT_ISO_TERMINAL_TRIED=1 TPOT_ISO_RAN="${myRUN}/ran" TPOT_ISO_SEEN="${myRUN}/seen" \
      TPOT_ISO_KEY_WAIT="${myKEY_WAIT}" bash "${myDIR}/terminal.sh" -- bash "${myDIR}/start.sh" "${myARGS[@]}"
    echo "$?" > "${myRUN}/rc"; } <&0 &
  myJOB=$!
  myEND=$((SECONDS + myX_WAIT))
  while [ ! -e "${myRUN}/ran" ] && [ ! -e "${myRUN}/rc" ] && [ "${SECONDS}" -lt "${myEND}" ]; do sleep 0.5; done
  myWHY="nox"
  if [ -e "${myRUN}/ran" ];
    then
      # its own wait ends after myKEY_WAIT s, X takes a while to end
      myEND=$((SECONDS + myKEY_WAIT + 15))
      while [ ! -e "${myRUN}/seen" ] && [ ! -e "${myRUN}/rc" ] && [ "${SECONDS}" -lt "${myEND}" ]; do sleep 0.5; done
      myWHY="nokey"
      [ ! -e "${myRUN}/seen" ] || myWHY=""
  fi
  if [ -n "${myWHY}" ] && [ ! -e "${myRUN}/rc" ];
    then
      fuUI_STOP "${myJOB}" || true
      wait "${myJOB}" 2>/dev/null
      fuVT_TEXT
    else
      wait "${myJOB}" 2>/dev/null
  fi
  if [ -z "${myWHY}" ];
    then
      IFS= read -r myRC < "${myRUN}/rc" 2>/dev/null || myRC=1
      [[ "${myRC}" =~ ^[0-9]+$ ]] || myRC=1
      rm -rf "${myRUN}"
      exit "${myRC}"
  fi
  rm -rf "${myRUN}"
  return 0
}

fuWATCH () {
  # a screen of this machine (tpot-iso.service on tty8, a login on a VT): start.sh in the
  # graphical terminal of the ISO where it can start (fuX_RUN); else, and when it did not start or
  # its screen stayed black, a choice on the text console: the graphical terminal (again), the
  # text console (kept for this boot, myTEXT) or later
  local myWHY="" myMISSING="" myPICK myDEFAULT
  if fuGRAPHICAL; then fuX_RUN; fi
  while true; do
    myDEFAULT="text"
    echo
    case "${myWHY}" in
      tools) fuUI_WARN "The graphical terminal of the ISO is missing its packages (${myMISSING})." ;;
      device) fuUI_WARN "No graphics device here (${myDEV_DIR}/dri, ${myDEV_DIR}/fb): the graphical terminal of the ISO cannot start." ;;
      text) fuUI_INFO "The text console was chosen in this boot." ;;
      nokey)
        myDEFAULT="graphical"
        fuUI_WARN "No key S within ${myKEY_WAIT} s in the graphical terminal of the ISO: did its screen stay black?" ;;
      *) fuUI_WARN "The graphical terminal of the ISO did not start (${XDG_STATE_HOME:-${HOME}/.local/state}/tpot/terminal.log)." ;;
    esac
    myPICK=$(fuUI_CHOOSE --selected "${myDEFAULT}" "How should the T-Pot installer start?" \
      "Graphical terminal:graphical" "Text console:text" "Later:later") || myPICK="later"
    case "${myPICK}" in
      graphical) fuX_RUN ;;
      text)
        : > "${myTEXT}" 2>/dev/null || true
        if [ -n "${TPOT_ISO_CONSOLE:-}" ];
          then TPOT_ISO_TERMINAL_TRIED=1 TPOT_ISO_PAUSE=1 exec bash "${myDIR}/start.sh" "${myARGS[@]}"
          else TPOT_ISO_TERMINAL_TRIED=1 exec bash "${myDIR}/start.sh" "${myARGS[@]}"
        fi ;;
      *)
        if [ -n "${TPOT_ISO_CONSOLE:-}" ];
          then
            fuUI_INFO "${myLATER}: log in here (Alt+F1) or over SSH."
            exit 0
        fi
        fuLATER 0 ;;
    esac
  done
}

fuWELCOME () {
  # in the graphical terminal of fuWATCH: the key S shows that its screen is seen (mySEEN), Enter
  # does not count. Nothing within myKEY_WAIT s: rc 0 (terminal.sh then waits for no Enter),
  # fuWATCH asks on the text console
  local myKEY myRC myEND
  [ -n "${mySEEN}" ] || return 0
  myUI_LOGO=1
  fuUI_BANNER "Installer" "T-Pot ${myVERSION} of this ISO: ${myISO_REPO} at ${myISO_BRANCH}"
  myEND=$((SECONDS + myKEY_WAIT))
  while [ "${SECONDS}" -lt "${myEND}" ]; do
    printf '\r  Press S to start the T-Pot installer (%s s) ' "$((myEND - SECONDS))"
    myRC=0
    IFS= read -rsn1 -t 1 myKEY || myRC=$?
    if [ "${myRC}" -eq 0 ];
      then
        case "${myKEY}" in
          s|S)
            : > "${mySEEN}" 2>/dev/null
            echo
            # the dialog on a clean screen, with the logo
            if [ -n "${myUI_GUM}" ] && [ -t 1 ]; then printf '\033[H\033[2J\033[3J'; unset TPOT_LOGO_SHOWN; fi
            return 0 ;;
        esac
    elif [ "${myRC}" -le 128 ];
      then sleep 1
    fi
  done
  echo
  exit 0
}

fuCLONE_AT () {
  # is ~/tpotce at the branch, the tag or the commit (install.sh: clone_matches)?
  local myHEAD myWANT
  [ "$(git -C "${myDEST}" rev-parse --abbrev-ref HEAD 2>/dev/null)" != "${myBRANCH}" ] || return 0
  myHEAD=$(git -C "${myDEST}" rev-parse HEAD 2>/dev/null)
  myWANT=$(git -C "${myDEST}" rev-parse -q --verify "${myBRANCH}^{commit}" 2>/dev/null)
  [ -n "${myWANT}" ] && [ "${myWANT}" = "${myHEAD}" ]
}

fuISO_CLONE () {
  # is ~/tpotce the T-Pot of the ISO as prepare.sh left it (origin, commit, nothing changed or added)?
  local myCOMMIT
  myCOMMIT=$(fuCONF TPOT_COMMIT) || return 1
  [ -d "${myDEST}/.git" ] && [ -n "${myCOMMIT}" ] \
    && [ "$(fuNORMALIZE_REPO "$(git -C "${myDEST}" remote get-url origin 2>/dev/null)")" = "${myISO_REPO}" ] \
    && [ "$(git -C "${myDEST}" rev-parse HEAD 2>/dev/null)" = "${myCOMMIT}" ] \
    && [ -z "$(git -C "${myDEST}" status --porcelain 2>/dev/null)" ]
}

fuGIT_CLONE () {
  GIT_TERMINAL_PROMPT=0 git clone -q "${myREPO}" "${myDEST}" \
    && git -C "${myDEST}" -c advice.detachedHead=false checkout -q "${myBRANCH}"
}

fuCLONE () {
  # another source: cloned into ~/tpotce, or the clone there if it is that source, and its own
  # install.sh, the installer of the branch under test
  local myAGAIN="bash ${myDIR}/start.sh -b ${myBRANCH} -r ${myREPO}" myASIDE=""
  command -v git >/dev/null || fuSTOP "git is missing, it gets T-Pot from ${myREPO}." "sudo apt-get install git, then: ${myAGAIN}"
  if [ -e "${myDEST}" ] && ! { [ "$(fuNORMALIZE_REPO "$(git -C "${myDEST}" remote get-url origin 2>/dev/null)")" = "${myREPO}" ] \
                               && fuCLONE_AT; } && fuISO_CLONE;
    then
      # the T-Pot of the ISO, untouched: aside while the other source is cloned, back if that fails
      myASIDE="${myDEST}.iso-$$"
      mv "${myDEST}" "${myASIDE}" || fuSTOP "${myDEST} cannot be moved aside." "Remove it (rm -rf ${myDEST}), then: ${myAGAIN}"
  fi
  if [ -e "${myDEST}" ];
    then
      if [ "$(fuNORMALIZE_REPO "$(git -C "${myDEST}" remote get-url origin 2>/dev/null)")" != "${myREPO}" ] || ! fuCLONE_AT;
        then
          fuSTOP "${myDEST} exists already and is not ${myREPO} at ${myBRANCH}." \
            "Remove it (rm -rf ${myDEST}), then: ${myAGAIN}"
      fi
      fuUI_INFO "${myDEST} is ${myREPO} at ${myBRANCH} already."
    else
      if ! fuUI_SPIN "Cloning ${myREPO} at ${myBRANCH} into ${myDEST} ..." "${myLOG}" fuGIT_CLONE;
        then
          rm -rf "${myDEST}"
          [ -z "${myASIDE}" ] || mv "${myASIDE}" "${myDEST}"
          fuSTOP "Could not clone ${myREPO} at ${myBRANCH}." "Check the network and the name, then: ${myAGAIN}"
      fi
      [ -z "${myASIDE}" ] || rm -rf "${myASIDE}"
  fi
  [ -f "${myDEST}/install.sh" ] || fuSTOP "${myREPO} at ${myBRANCH} has no install.sh." "Remove ${myDEST} and pick another branch: bash ${myDIR}/start.sh"
  fuSTART "${myDEST}"
}

fuSTART () {
  # fuSTART <folder>: its install.sh, with the lock (fd 9)
  fuUI_INFO "The T-Pot installer starts: T-Pot from ${myREPO} at ${myBRANCH}."
  fuUI_HINT "Abort keeps a plain Debian. ${myLATER} (by hand: bash ${myDIR}/start.sh)."
  echo
  exec bash "$1/install.sh" -b "${myBRANCH}" -r "${myREPO}"
}

myISO_REPO="$(fuCONF TPOT_REPO_URL)"
myISO_BRANCH="$(fuCONF TPOT_BRANCH)"
# the wheels of the T-Pot Manager on the ISO: tpot setup takes them first, PyPI for the rest
[ ! -d "${myDIR}/wheels" ] || export TPOT_WHEELS="${myDIR}/wheels"
myBRANCH=""
myREPO=""
myPICK=""
myHOLDER=""
while getopts ":b:r:h" myOPT; do
  case "${myOPT}" in
    b) myBRANCH="${OPTARG}" ;;
    r) myREPO=$(fuNORMALIZE_REPO "${OPTARG}") ;;
    h) fuHELP; exit 0 ;;
    :) fuUSAGE "Option -${OPTARG} requires an argument." ;;
    *) fuUSAGE "Unknown option -${OPTARG}." ;;
  esac
done
shift $((OPTIND - 1))
[ "$#" -eq 0 ] || fuUSAGE "Unknown argument: $1"

fuVALID_REPO "${myISO_REPO}" \
  || fuSTOP "${myCONF} has no valid TPOT_REPO_URL." "Run the installer by hand: ${myDIR}/install.sh -r <repository> -b <branch>"
fuVALID_BRANCH "${myISO_BRANCH}" \
  || fuSTOP "${myCONF} has no valid TPOT_BRANCH." "Run the installer by hand: ${myDIR}/install.sh -r <repository> -b <branch>"

if [ "$(id -u)" = "0" ];
  then
    fuSTOP "The T-Pot installer runs as the user created during the installation, not as root." \
      "Log in as that user and run: bash ${myDIR}/start.sh"
fi
# T-Pot runs its containers as the system user tpot (uid 2000): a login of that name would be
# turned into it
if [ "$(id -un)" = "tpot" ];
  then
    fuSTOP "The user tpot is reserved for the containers of T-Pot." \
      "Create another user with sudo (sudo adduser <name> && sudo usermod -aG sudo <name>)," \
      "log in as that user and run: bash ${myDIR}/start.sh"
fi
[ ! -e "${myINSTALLED}" ] || fuDONE
! fuBUSY || fuSTOP "The T-Pot installer runs already: ${myHOLDER}." "${myBUSY_HINT}"

if [ -z "${TPOT_ISO_TERMINAL_TRIED:-}" ] && { [ -n "${TPOT_ISO_CONSOLE:-}" ] || { [ -t 0 ] && [ -t 1 ] && fuVT; }; };
  then fuWATCH
fi
fuWELCOME

mkdir -p "${myLOG%/*}" 2>/dev/null && : >> "${myLOG}" 2>/dev/null || myLOG="/dev/null"
fuCOLOR_TEST
if [ -n "${myBRANCH}${myREPO}" ];
  then
    # the options: a tag or a commit named with -b is meant, the warning is enough
    [ -n "${myBRANCH}" ] || myBRANCH="${myISO_BRANCH}"
    [ -n "${myREPO}" ] || myREPO="${myISO_REPO}"
    fuVALID_BRANCH "${myBRANCH}" || fuUSAGE "Not a branch, tag or commit: ${myBRANCH}"
    fuVALID_REPO "${myREPO}" || fuUSAGE "Not a repository URL: ${myREPO}"
    if ! fuISO_SOURCE;
      then
        myRC=0
        fuCHECK_SOURCE || myRC=$?
        [ "${myRC}" -ne 1 ] || fuSTOP "T-Pot cannot be installed from ${myREPO} at ${myBRANCH}." "Name another one: bash ${myDIR}/start.sh -b <branch> [-r <repository>]"
    fi
elif [ -t 0 ];
  then
    fuDIALOG
    # install.sh asks no "Start the T-Pot installer?" after this choice
    export TPOT_INSTALL_CONFIRMED=1
  else
    myBRANCH="${myISO_BRANCH}" myREPO="${myISO_REPO}"
fi

fuLOCK
if [ "${myPICK}" = "newest" ];
  then
    if fuUI_SPIN "Fetching the newest ${myISO_BRANCH} into ${myDEST} ..." "${myLOG}" fuNEWEST_FETCH;
      then fuSTART "${myDEST}"
    fi
    fuUI_CONFIRM --default yes "Install the T-Pot of this ISO instead?" "Install" "Quit" || fuLATER 1
fi
fuISO_SOURCE || fuCLONE
fuSTART "${myDIR}"
