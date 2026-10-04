# shellcheck shell=bash
# >>> tpot ui >>>
# The look of the T-Pot scripts: gum (https://github.com/charmbracelet/gum) for a
# person at a terminal, plain text otherwise. Keep in sync! install.sh carries an
# identical copy of this block (it runs from curl without the repository), the
# other scripts source installer/lib/ui.sh. tpotctl/tests/test_installer.py checks.
#
# gum is a pinned release, its sha256 taken from the signed checksums.txt of the
# release. A failed download or a wrong hash means plain text, never a stop.
# TPOT_GUM=off keeps the plain text.

myUI_GUM_VERSION="2.0.2"
myUI_GUM_SHA256_x86_64="d842e06d93dbed90af48cb8dd10698db6f22e331fc40346bb37bbc753109edc2"
myUI_GUM_SHA256_arm64="8ebf8b54ec1e8c81f2bb58b59ff9b70998186a4d11375f0cf357b80e0ccfa1d5"
# the colours of tpot (tpotctl/theme.py)
myUI_MAGENTA="#E20074"
myUI_PETROL="#014463"
myUI_GLASS="#ECEFF9"
myUI_ASH="#A2A2AD"
myUI_OK_COLOUR="#3FA34D"
myUI_WARN_COLOUR="#F4B400"
myUI_ERROR_COLOUR="#E8453C"
myUI_GUM=""

fuUI_INIT () {
  # gum for a terminal only; output to a file or a pipe stays plain text
  myUI_GUM=""
  [ -t 1 ] || return 0
  [ "${TPOT_GUM:-on}" = "off" ] && return 0
  local myDIR="${XDG_DATA_HOME:-${HOME}/.local/share}/tpotce/bin"
  local myBIN="${myDIR}/gum"
  if [ -x "${myBIN}" ] && "${myBIN}" --version 2>/dev/null | grep -q "${myUI_GUM_VERSION}";
    then
      myUI_GUM="${myBIN}"
      return 0
  fi
  local myARCH mySHA
  case "$(uname -m)" in
    x86_64|amd64) myARCH="x86_64"; mySHA="${myUI_GUM_SHA256_x86_64}" ;;
    aarch64|arm64) myARCH="arm64"; mySHA="${myUI_GUM_SHA256_arm64}" ;;
    *) return 0 ;;
  esac
  local myNAME="gum_${myUI_GUM_VERSION}_Linux_${myARCH}"
  local myURL="https://github.com/charmbracelet/gum/releases/download/v${myUI_GUM_VERSION}/${myNAME}.tar.gz"
  local myTMP
  myTMP=$(mktemp -d 2>/dev/null) || return 0
  if command -v curl >/dev/null;
    then curl -fsSL --max-time 30 -o "${myTMP}/gum.tgz" "${myURL}" 2>/dev/null
    else wget -q -T 30 -O "${myTMP}/gum.tgz" "${myURL}" 2>/dev/null
  fi
  if [ -s "${myTMP}/gum.tgz" ] && command -v sha256sum >/dev/null \
     && echo "${mySHA}  ${myTMP}/gum.tgz" | sha256sum -c --status 2>/dev/null \
     && tar xzf "${myTMP}/gum.tgz" -C "${myTMP}" "${myNAME}/gum" 2>/dev/null \
     && mkdir -p "${myDIR}" && install -m 0755 "${myTMP}/${myNAME}/gum" "${myBIN}" 2>/dev/null;
    then
      myUI_GUM="${myBIN}"
  fi
  rm -rf "${myTMP}"
  return 0
}

fuUI_STYLE () {
  # fuUI_STYLE <colour> <text>: one coloured line, plain without gum
  if [ -n "${myUI_GUM}" ];
    then "${myUI_GUM}" style --foreground "$1" -- "$2"
    else echo "$2"
  fi
}

fuUI_PAINT () {
  # a coloured piece of a line, for $(...): gum leaves out colours when it does not
  # write to a terminal itself
  CLICOLOR_FORCE=1 "${myUI_GUM}" style --foreground "$1" -- "$2"
}

fuUI_BANNER () {
  # fuUI_BANNER <title> <line> ...: the t-pot wordmark (as in the splash of tpot) and a title
  local myTITLE="$1"
  shift
  if [ -z "${myUI_GUM}" ];
    then
      echo
      echo "### T-Pot ${myTITLE}"
      for myLINE in "$@"; do echo "### ${myLINE}"; done
      echo
      return
  fi
  echo
  "${myUI_GUM}" style --foreground "${myUI_MAGENTA}" --bold --margin "0 2" -- \
    "  ██                             ██" \
    "▀▀██▀▀         ██▀▀█▄  ▄█▀▀█▄  ▀▀██▀▀" \
    "  ██    ▀▀▀▀▀  ██  ██  ██  ██    ██" \
    "   ▀▀▀         ██▀▀▀    ▀▀▀▀      ▀▀▀"
  echo
  "${myUI_GUM}" style --foreground "${myUI_GLASS}" --bold --margin "0 2" -- "T-Pot ${myTITLE}"
  [ "$#" -gt 0 ] && "${myUI_GUM}" style --foreground "${myUI_ASH}" --margin "0 2" -- "$@"
  echo
}

fuUI_INFO () {
  if [ -n "${myUI_GUM}" ];
    then echo "$(fuUI_PAINT "${myUI_MAGENTA}" "⬢") $(fuUI_PAINT "${myUI_GLASS}" "$*")"
    else echo "### $*"
  fi
}

fuUI_OK () {
  if [ -n "${myUI_GUM}" ];
    then echo "$(fuUI_PAINT "${myUI_OK_COLOUR}" "✓") $*"
    else echo "### [OK] - $*"
  fi
}

fuUI_WARN () {
  if [ -n "${myUI_GUM}" ];
    then "${myUI_GUM}" style --foreground "${myUI_WARN_COLOUR}" -- "! $*"
    else echo "### [WARNING] - $*"
  fi
}

fuUI_ERROR () {
  if [ -n "${myUI_GUM}" ];
    then "${myUI_GUM}" style --foreground "${myUI_ERROR_COLOUR}" --bold -- "✗ $*" >&2
    else echo "### [ERROR] - $*" >&2
  fi
}

fuUI_HINT () {
  # commands or details below a message, indented
  local myLINE
  for myLINE in "$@"; do
    if [ -n "${myUI_GUM}" ];
      then "${myUI_GUM}" style --foreground "${myUI_ASH}" -- "    ${myLINE}"
      else echo "###   ${myLINE}"
    fi
  done
}

fuUI_CONFIRM () {
  # fuUI_CONFIRM <question> [yes] [no]: 0 for yes; reads y/n from stdin without gum
  local myANSWER=""
  if [ -n "${myUI_GUM}" ] && [ -t 0 ];
    then
      "${myUI_GUM}" confirm --affirmative "${2:-Yes}" --negative "${3:-No}" \
        --prompt.foreground "${myUI_GLASS}" --selected.background "${myUI_MAGENTA}" \
        --selected.foreground "${myUI_GLASS}" --unselected.background "${myUI_PETROL}" \
        --unselected.foreground "${myUI_GLASS}" -- "$1"
      return $?
  fi
  while [ "${myANSWER}" != "y" ] && [ "${myANSWER}" != "n" ]; do
    read -rp "### $1 (y/n) " myANSWER || return 1
  done
  [ "${myANSWER}" = "y" ]
}

fuUI_CHOOSE () {
  # fuUI_CHOOSE <header> <label:value> ...: prints the value of the choice
  local myHEADER="$1"
  shift
  if [ -n "${myUI_GUM}" ] && [ -t 0 ];
    then
      "${myUI_GUM}" choose --header "${myHEADER}" --label-delimiter ":" \
        --header.foreground "${myUI_GLASS}" --cursor.foreground "${myUI_MAGENTA}" \
        --item.foreground "${myUI_ASH}" --selected.foreground "${myUI_MAGENTA}" -- "$@"
      return $?
  fi
  local myI=1 myITEM myPICK
  echo "### ${myHEADER}" >&2
  for myITEM in "$@"; do
    echo "###   ${myI}) ${myITEM%%:*}" >&2
    myI=$((myI + 1))
  done
  while true; do
    read -rp "### Choice (1-$#): " myPICK || return 1
    if [[ "${myPICK}" =~ ^[0-9]+$ ]] && [ "${myPICK}" -ge 1 ] && [ "${myPICK}" -le "$#" ];
      then
        myITEM="${!myPICK}"
        echo "${myITEM#*:}"
        return 0
    fi
  done
}

fuUI_INPUT () {
  # fuUI_INPUT <prompt> [password]: prints what was typed
  local myVALUE=""
  if [ -n "${myUI_GUM}" ] && [ -t 0 ];
    then
      if [ "$2" = "password" ];
        then "${myUI_GUM}" input --password --header "$1" --header.foreground "${myUI_GLASS}" \
               --cursor.foreground "${myUI_MAGENTA}" --prompt "› " --prompt.foreground "${myUI_MAGENTA}" --placeholder ""
        else "${myUI_GUM}" input --header "$1" --header.foreground "${myUI_GLASS}" \
               --cursor.foreground "${myUI_MAGENTA}" --prompt "› " --prompt.foreground "${myUI_MAGENTA}" --placeholder ""
      fi
      return $?
  fi
  if [ "$2" = "password" ];
    then read -rsp "### $1 " myVALUE; echo >&2
    else read -rp "### $1 " myVALUE
  fi
  echo "${myVALUE}"
}

fuUI_SPIN () {
  # fuUI_SPIN <title> <log file> <command> ...: runs the command (a function works too)
  # in this shell with its output in the log file and a spinner meanwhile; shows the
  # end of the log on failure. It runs in the background, so it must not prompt:
  # refresh sudo before (sudo -v) where it needs a password.
  local myTITLE="$1" myLOG="$2"
  shift 2
  local myPID myRC
  if [ -n "${myUI_GUM}" ];
    then
      "$@" >>"${myLOG}" 2>&1 < /dev/null &
      myPID=$!
      "${myUI_GUM}" spin --spinner dot --spinner.foreground "${myUI_MAGENTA}" --title "${myTITLE}" \
        --title.foreground "${myUI_GLASS}" -- sh -c "while kill -0 ${myPID} 2>/dev/null; do sleep 0.2; done"
      wait "${myPID}"
      myRC=$?
    else
      echo "### ${myTITLE}"
      "$@" >>"${myLOG}" 2>&1 < /dev/null
      myRC=$?
  fi
  if [ "${myRC}" -eq 0 ];
    then fuUI_OK "${myTITLE%% ...}"
    else
      fuUI_ERROR "${myTITLE%% ...} failed, the end of ${myLOG}:"
      tail -n 15 "${myLOG}" >&2
  fi
  return "${myRC}"
}
# <<< tpot ui <<<
