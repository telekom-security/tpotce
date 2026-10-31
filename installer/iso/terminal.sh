#!/usr/bin/env bash
# Installed by the ISO as tpot-terminal: a local, authenticated Xorg session, not a desktop.
# No arguments opens the Manager; -- runs a command. SSH, existing graphical terminals and
# TPOT_TERMINAL=off keep their terminal. The ISO uses this before its first-login questions.

mySELF="$(CDPATH="" cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"

fuSTOP () {
  echo "T-Pot terminal: $*" >&2
  exit 1
}

fuRUN () {
  local mySTATE="$1" myARG myRC
  local myCOMMAND=()
  while IFS= read -r -d '' myARG; do myCOMMAND+=("${myARG}"); done < "${mySTATE}/argv"
  [ "${#myCOMMAND[@]}" -gt 0 ] || fuSTOP "No command in the session."
  # xterm clears inherited COLORTERM; advertise RGB only inside the actual terminal.
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

fuSESSION () {
  local mySTATE="$1" myWM myRC
  matchbox-window-manager -use_titlebar no &
  myWM=$!
  trap 'kill "${myWM}" 2>/dev/null; wait "${myWM}" 2>/dev/null' EXIT
  xterm -fullscreen -tn xterm-256color -u8 -fa "DejaVu Sans Mono" -fs 12 \
    -bg "#111318" -fg "#eef1f5" -b 0 -bw 0 +sb \
    -xrm 'XTerm*directColor: true' -xrm 'XTerm*allowWindowOps: false' \
    -title "T-Pot" -e "${mySELF}" --run "${mySTATE}"
  myRC=$?
  return "${myRC}"
}

case "${1:-}" in
  --run|--session)
    [ "$(id -u)" != "0" ] || fuSTOP "Log in as a regular user; do not use sudo."
    myMODE="$1"; shift
    [ "$#" -eq 1 ] && [ -d "$1" ] || fuSTOP "A private session directory is required."
    if [ "${myMODE}" = "--run" ]; then fuRUN "$@"; else fuSESSION "$@"; fi
    exit $? ;;
  -h|--help)
    cat <<'HELP'
Usage: tpot-terminal [manager arguments]
       tpot-terminal -- command [arguments]

Opens the T-Pot Manager (or a command) in a fullscreen Truecolor terminal on a local Linux
console. Log in normally first; never run it with sudo. No desktop or graphical login starts.
SSH and existing graphical terminals are unchanged. TPOT_TERMINAL=off uses the text terminal.
Graphics errors stop without rerunning the command; see ~/.local/state/tpot/terminal.log
(or $XDG_STATE_HOME/tpot/terminal.log). After fixing them, run the command again.
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

if [ "${TPOT_TERMINAL:-auto}" = "off" ] || [ -n "${SSH_CONNECTION:-}${SSH_TTY:-}${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] \
   || [ ! -t 0 ] || [ ! -t 1 ];
  then exec "$@"
fi
myTTY="$(tty)" || fuSTOP "Cannot identify the login terminal."
if [[ ! "${myTTY}" =~ ^/dev/tty([1-9][0-9]*)$ ]]; then exec "$@"; fi
myVT="${BASH_REMATCH[1]}"

for myTOOL in startx xauth Xorg xterm matchbox-window-manager; do
  command -v "${myTOOL}" >/dev/null || fuSTOP "${myTOOL} is missing. Restore the ISO's graphical terminal packages, or use TPOT_TERMINAL=off for the text console."
done

umask 077
myLOG_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/tpot"
mkdir -p "${myLOG_DIR}" || fuSTOP "Cannot create ${myLOG_DIR}."
myLOG="${myLOG_DIR}/terminal.log"
mySTATE="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/tpot-terminal.XXXXXXXX")" || fuSTOP "Cannot create a private session directory."
trap 'rm -rf -- "${mySTATE}"' EXIT
printf '%s\0' "$@" > "${mySTATE}/argv" || fuSTOP "Cannot save the command arguments."
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
