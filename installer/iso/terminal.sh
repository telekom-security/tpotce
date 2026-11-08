#!/usr/bin/env bash
# Installed by the ISO as tpot-terminal: a local, authenticated Xorg session with one fullscreen
# Xfce Terminal (VTE), not a desktop. No arguments opens the Manager; -- runs a command. SSH,
# existing graphical terminals and TPOT_TERMINAL=off keep their terminal. The ISO uses this
# before its first-login questions. --ready names the first missing part (rc 1), --packages the
# Debian packages of the graphical terminal.

mySELF="$(CDPATH="" cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"
myFONT="Cascadia Mono"
myFONT_SIZE=12
# INK and GLASS of tpotctl/theme.py: unpainted cells and plain output match the Manager
myBACKGROUND="#000000"
myFOREGROUND="#ECEFF9"
# the size the window opens with, until the window manager makes it fullscreen
myCOLS=80
myROWS=24
myTOOLS=(startx xauth Xorg dbus-run-session xfce4-terminal matchbox-window-manager xprop fc-list)
myPACKAGES=(dbus-daemon fontconfig fonts-cascadia-code fonts-dejavu-core matchbox-window-manager x11-utils
  xauth xfce4-terminal xinit xserver-xorg-core xserver-xorg-input-libinput xserver-xorg-legacy xserver-xorg-video-fbdev)

fuSTOP () {
  echo "T-Pot terminal: $*" >&2
  exit 1
}

fuTEXT () {
  echo "T-Pot terminal: using the text console ($1)." >&2
  shift
  exec "$@"
}

fuREADY () {
  # the first missing part of the graphical terminal on stdout and rc 1; complete: nothing, rc 0
  local myTOOL
  for myTOOL in "${myTOOLS[@]}"; do
    if ! command -v "${myTOOL}" >/dev/null 2>&1; then echo "${myTOOL}"; return 1; fi
  done
  # fc-match always finds some font; fc-list -q only the one asked for
  if ! fc-list -q "${myFONT}" >/dev/null 2>&1; then echo "the font ${myFONT}"; return 1; fi
  return 0
}

fuSAVE_ENV () {
  # the login's own values (set, empty or unset), NUL separated; fuRUN gives them to the command
  printf '%s\0%s\0%s\0' \
    XDG_CONFIG_HOME "${XDG_CONFIG_HOME+1}" "${XDG_CONFIG_HOME-}" \
    XDG_CONFIG_DIRS "${XDG_CONFIG_DIRS+1}" "${XDG_CONFIG_DIRS-}" \
    DBUS_SESSION_BUS_ADDRESS "${DBUS_SESSION_BUS_ADDRESS+1}" "${DBUS_SESSION_BUS_ADDRESS-}" \
    NO_AT_BRIDGE "${NO_AT_BRIDGE+1}" "${NO_AT_BRIDGE-}"
}

fuRESTORE_ENV () {
  local myNAME mySET myVALUE myCOUNT=0
  while IFS= read -r -d '' myNAME && IFS= read -r -d '' mySET && IFS= read -r -d '' myVALUE; do
    case "${myNAME}" in
      XDG_CONFIG_HOME|XDG_CONFIG_DIRS|DBUS_SESSION_BUS_ADDRESS|NO_AT_BRIDGE) ;;
      *) return 1 ;;
    esac
    if [ "${mySET}" = "1" ]; then export "${myNAME}=${myVALUE}"; else unset "${myNAME}"; fi
    myCOUNT=$((myCOUNT + 1))
  done < "$1"
  [ "${myCOUNT}" -eq 4 ]
}

fuPREFERENCES () {
  # the terminal's own preferences, never the user's: F10 and Alt+letter stay with the command, no
  # question on close; help (F1), new tab / window and leaving fullscreen (F11) have no key. The
  # window manager grabs none (its default: F11, Alt+C / Alt+F4 close, Ctrl+Alt+X starts xterm)
  local myDIR="$1" myACTION
  mkdir -p "${myDIR}/xfce4/xfconf/xfce-perchannel-xml" "${myDIR}/xfce4/terminal" "${myDIR}/matchbox" || return 1
  echo "# no keys: every key goes to the command" > "${myDIR}/matchbox/kbdconfig" || return 1
  cat > "${myDIR}/xfce4/xfconf/xfce-perchannel-xml/xfce4-terminal.xml" <<'XML' || return 1
<?xml version="1.0" encoding="UTF-8"?>

<channel name="xfce4-terminal" version="1.0">
  <property name="shortcuts-no-menukey" type="bool" value="true"/>
  <property name="shortcuts-no-mnemonics" type="bool" value="true"/>
  <property name="misc-confirm-close" type="bool" value="false"/>
</channel>
XML
  for myACTION in contents new-tab new-window fullscreen; do
    printf '(gtk_accel_path "<Actions>/terminal-window/%s" "")\n' "${myACTION}"
  done > "${myDIR}/xfce4/terminal/accels.scm"
}

fuFULLSCREEN () {
  # VTE starts the command a moment after the window opened, at times before the window manager
  # made it fullscreen: the command would lay itself out (the logo, the first layout) for that size
  local myEND=$((SECONDS + 3))
  [ -e "$1/wm" ] || return 0
  while [ "$(stty size 2>/dev/null)" = "${myROWS} ${myCOLS}" ] && [ "${SECONDS}" -lt "${myEND}" ]; do
    sleep 0.02
  done
}

fuRUN () {
  local mySTATE="$1" myARG myRC
  local myCOMMAND=()
  while IFS= read -r -d '' myARG; do myCOMMAND+=("${myARG}"); done < "${mySTATE}/argv"
  [ "${#myCOMMAND[@]}" -gt 0 ] || fuSTOP "No command in the session."
  fuRESTORE_ENV "${mySTATE}/env" || fuSTOP "Cannot restore the login environment."
  fuFULLSCREEN "${mySTATE}"
  # VTE says true colour itself; set here too, so the rule of the colours (prefs.detect_colors,
  # fuUI_COLORS) takes the theme's true colours unless the user chose others
  export TPOT_TERMINAL_ACTIVE=1 COLORTERM=truecolor
  "${myCOMMAND[@]}"
  myRC=$?
  printf '%s\n' "${myRC}" > "${mySTATE}/result" || fuSTOP "Cannot save the command's exit status."
  if [ "${myRC}" -ne 0 ];
    then
      echo
      echo "T-Pot terminal: the command ended with status ${myRC}. Press Enter to return to the console."
      read -r myARG
  fi
  return "${myRC}"
}

fuWM_READY () {
  # the window manager manages the screen once it names itself on the root window; a terminal
  # mapped before that starts its command at 80 x 24 and only then goes fullscreen
  local myWM="$1" myEND=$((SECONDS + 5))
  while [ "${SECONDS}" -lt "${myEND}" ]; do
    [[ "$(xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null)" == *"window id #"* ]] && return 0
    kill -0 "${myWM}" 2>/dev/null || { echo "T-Pot terminal: the window manager ended at its start." >&2; return 1; }
    sleep 0.05
  done
  echo "T-Pot terminal: the window manager did not start within 5 seconds." >&2
  return 1
}

fuSESSION () {
  local mySTATE="$1" myWM myRC
  matchbox-window-manager -use_titlebar no -kbdconfig "${mySTATE}/config/matchbox/kbdconfig" &
  myWM=$!
  # the pid now: myWM is local and gone when the trap runs
  # shellcheck disable=SC2064
  trap "kill ${myWM} 2>/dev/null; wait ${myWM} 2>/dev/null" EXIT
  # without it the terminal still opens, only not fullscreen
  if fuWM_READY "${myWM}"; then : > "${mySTATE}/wm" || true; fi
  # preferences and bus of its own: Xfce settings of the user cannot change it. Cascadia Mono names
  # no generic family, so fontconfig would take a glyph it lacks (✗ ↗ ⇢) from a proportional font,
  # wider than its cell; monospace after it takes DejaVu Sans Mono first
  XDG_CONFIG_HOME="${mySTATE}/config" XDG_CONFIG_DIRS="${mySTATE}/config" NO_AT_BRIDGE=1 \
    dbus-run-session -- xfce4-terminal --disable-server --fullscreen --hide-menubar --hide-toolbar \
    --hide-scrollbar --hide-borders --geometry="${myCOLS}x${myROWS}" --font="${myFONT},monospace ${myFONT_SIZE}" \
    --color-bg="${myBACKGROUND}" --color-text="${myFOREGROUND}" --title="T-Pot" --dynamic-title-mode=none \
    --execute "${mySELF}" --run "${mySTATE}"
  myRC=$?
  return "${myRC}"
}

myAUTO=""
if [ "${1:-}" = "--auto" ]; then
  myAUTO=1
  export TPOT_TERMINAL_CHECKED=1
  shift
  [ "${1:-}" = "--" ] || fuSTOP "--auto requires -- command."
fi

case "${1:-}" in
  --run|--session)
    [ "$(id -u)" != "0" ] || fuSTOP "Log in as a regular user; do not use sudo."
    myMODE="$1"; shift
    [ "$#" -eq 1 ] && [ -d "$1" ] || fuSTOP "A private session directory is required."
    if [ "${myMODE}" = "--run" ]; then fuRUN "$@"; else fuSESSION "$@"; fi
    exit $? ;;
  --ready) [ "$#" -eq 1 ] || fuSTOP "--ready takes no arguments."; fuREADY; exit $? ;;
  --packages) [ "$#" -eq 1 ] || fuSTOP "--packages takes no arguments."; echo "${myPACKAGES[*]}"; exit 0 ;;
  -h|--help)
    cat <<'HELP'
Usage: tpot-terminal [manager arguments]
       tpot-terminal -- command [arguments]
       tpot-terminal --ready | --packages

Opens the T-Pot Manager (or a command) in a fullscreen true colour terminal (Xfce Terminal,
Cascadia Mono) on a local Linux console. Log in normally first; never run it with sudo. No
desktop or graphical login starts. SSH and existing graphical terminals are unchanged.
TPOT_TERMINAL=off uses the text terminal. Graphics errors stop without rerunning the command;
see ~/.local/state/tpot/terminal.log (or $XDG_STATE_HOME/tpot/terminal.log). After fixing
them, run the command again. --ready names the first missing part of the graphical terminal,
--packages the Debian packages it needs.
HELP
    exit 0 ;;
  --) shift; [ "$#" -gt 0 ] || fuSTOP "A command is required after --." ;;
  *) set -- tpot "$@" ;;
esac

[ "$(id -u)" != "0" ] || fuSTOP "Log in as a regular user; do not use sudo."
command -v "$1" >/dev/null || fuSTOP "Command not found: $1"
case "${TPOT_TERMINAL:-auto}" in
  auto|off) ;;
  *) fuSTOP "TPOT_TERMINAL must be auto or off." ;;
esac

if [ "${TPOT_TERMINAL:-auto}" = "off" ] || [ -n "${TPOT_TERMINAL_ACTIVE:-}${SSH_CONNECTION:-}${SSH_TTY:-}${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] \
   || [ ! -t 0 ] || [ ! -t 1 ];
  then exec "$@"
fi
myTTY="$(tty)" || fuSTOP "Cannot identify the login terminal."
if [[ ! "${myTTY}" =~ ^/dev/tty([1-9][0-9]*)$ ]]; then exec "$@"; fi
myVT="${BASH_REMATCH[1]}"

if [ -n "${myAUTO}" ] && [ -e "${TPOT_ISO_TEXT:-/run/lock/tpot-iso.text}" ]; then
  fuTEXT "chosen for this boot" "$@"
fi

if ! myMISSING="$(fuREADY)"; then
  [ -z "${myAUTO}" ] || fuTEXT "${myMISSING} is missing for the graphical view" "$@"
  fuSTOP "${myMISSING} is missing. On Debian: sudo apt-get install --no-install-recommends ${myPACKAGES[*]}; or use TPOT_TERMINAL=off for the text console."
fi

if [ -n "${myAUTO}" ]; then
  myGRAPHICS=""
  for myDEV in "${TPOT_ISO_DEV:-/dev}"/dri/card* "${TPOT_ISO_DEV:-/dev}"/fb*; do
    if [ -e "${myDEV}" ]; then myGRAPHICS=1; break; fi
  done
  [ -n "${myGRAPHICS}" ] || fuTEXT "no graphics device" "$@"
fi

umask 077
myLOG_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/tpot"
mkdir -p "${myLOG_DIR}" || fuSTOP "Cannot create ${myLOG_DIR}."
myLOG="${myLOG_DIR}/terminal.log"
mySTATE="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/tpot-terminal.XXXXXXXX")" || fuSTOP "Cannot create a private session directory."
trap 'rm -rf -- "${mySTATE}"' EXIT
printf '%s\0' "$@" > "${mySTATE}/argv" || fuSTOP "Cannot save the command arguments."
fuSAVE_ENV > "${mySTATE}/env" || fuSTOP "Cannot save the login environment."
fuPREFERENCES "${mySTATE}/config" || fuSTOP "Cannot write the terminal's preferences."
export XAUTHORITY="${mySTATE}/Xauthority"
touch "${XAUTHORITY}" || fuSTOP "Cannot create the X authority file."

# startx creates the authentication cookie; never -ac or a listening network X server.
myDISPLAY=0
while [ -e "/tmp/.X11-unix/X${myDISPLAY}" ] || [ -e "/tmp/.X${myDISPLAY}-lock" ]; do
  myDISPLAY=$((myDISPLAY + 1))
  [ "${myDISPLAY}" -lt 64 ] || fuSTOP "No free X display."
done
echo "T-Pot terminal: opening the graphical session (log: ${myLOG})."
myRC=0
startx "${mySELF}" --session "${mySTATE}" -- "$(command -v Xorg)" ":${myDISPLAY}" \
  "vt${myVT}" -keeptty -nolisten tcp -depth 24 > "${myLOG}" 2>&1 || myRC=$?
if [ ! -f "${mySTATE}/result" ];
  then
    fuSTOP "The graphical session ended before the command completed (startx: ${myRC}). See ${myLOG}. To explicitly use the text console, retry with TPOT_TERMINAL=off."
fi
IFS= read -r myRESULT < "${mySTATE}/result"
[[ "${myRESULT}" =~ ^[0-9]+$ ]] && [ "${myRESULT}" -le 255 ] || fuSTOP "Invalid command exit status; see ${myLOG}."
[ "${myRC}" -eq 0 ] || echo "T-Pot terminal: startx ended with status ${myRC}; see ${myLOG}." >&2
[ "${myRESULT}" -eq 0 ] || exit "${myRESULT}"
exit "${myRC}"
