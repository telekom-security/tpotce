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
#
# The T-Pot logo: the ANSI logo of the T-Pot Manager (tpotctl/splash_art.py) as a
# still picture with its credits, rendered here in bash in the colours the T-Pot
# Manager would take (fuUI_COLORS). A script that asks a person sets myUI_LOGO=1,
# fuUI_BANNER then shows it once per chain of scripts (TPOT_LOGO_SHOWN) where
# fuUI_LOGO_ON allows it; its pixels, its colour tables (myUI_LOGO_RGB / _256 /
# _16) and the wordmark are generated into the logo data at the end of this block:
# python3 -m tpotctl.ui_logo (--check).

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
# set by the caller: 1 shows the logo in fuUI_BANNER; the version for its credits
# (empty: fuUI_VERSION finds it)
myUI_LOGO="${myUI_LOGO:-}"
myUI_VERSION="${myUI_VERSION:-}"
myUI_COLS=""
myUI_ROWS=""
# the checkout this file lies in, for the copy in install.sh (no file of its own) ~/tpotce
case "${BASH_SOURCE[0]:-}" in
  */installer/lib/ui.sh) myUI_CHECKOUT=$(cd "${BASH_SOURCE[0]%/installer/lib/ui.sh}/" 2>/dev/null && pwd) ;;
  installer/lib/ui.sh) myUI_CHECKOUT="${PWD}" ;;
  *) myUI_CHECKOUT="" ;;
esac
[ -n "${myUI_CHECKOUT}" ] || myUI_CHECKOUT="${HOME}/tpotce"

fuUI_INIT () {
  # gum for a terminal only; output to a file or a pipe stays plain text
  myUI_GUM=""
  [ -t 1 ] || return 0
  [ "${TPOT_GUM:-on}" = "off" ] && return 0
  # gum (lipgloss) and what the script starts (i.e. tpot) know true colour by COLORTERM
  # only: the rule of fuUI_COLORS says it for them where COLORTERM is empty
  if [ -z "${COLORTERM:-}" ] && [ "$(fuUI_COLORS)" = "truecolor" ]; then export COLORTERM=truecolor; fi
  local myDIR="${XDG_DATA_HOME:-${HOME}/.local/share}/tpotce/bin"
  local myBIN="${myDIR}/gum"
  if [ -x "${myBIN}" ] && "${myBIN}" --version 2>/dev/null | grep -q "${myUI_GUM_VERSION}";
    then
      myUI_GUM="${myBIN}"
      return 0
  fi
  # the release is the one of Linux: elsewhere (macOS, Windows) the scripts stop or only show
  # their help (fuUI_LINUX_ONLY), plain text is enough there
  [ "$(uname -s 2>/dev/null)" = "Linux" ] || return 0
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

fuUI_PREF () {
  # fuUI_PREF <icons|colors>: the choice of the T-Pot Manager in its tpot.json
  # (tpotctl/prefs.py), empty without one
  local myFILE="${XDG_CONFIG_HOME:-${HOME}/.config}/tpotce/tpot.json" myTEXT="" myRE
  [ -r "${myFILE}" ] || return 0
  IFS= read -r -d '' myTEXT < "${myFILE}" || true
  myRE="\"$1\"[[:space:]]*:[[:space:]]*\"([a-z0-9]+)\""
  [[ "${myTEXT}" =~ ${myRE} ]] && echo "${BASH_REMATCH[1]}"
  return 0
}

fuUI_ICONS () {
  # the icon set of the T-Pot Manager: TPOT_ICONS, else tpot.json, else unicode
  local myICONS="${TPOT_ICONS:-}"
  case "${myICONS}" in unicode|nerd|ascii) echo "${myICONS}"; return 0 ;; esac
  myICONS=$(fuUI_PREF icons)
  case "${myICONS}" in unicode|nerd|ascii) echo "${myICONS}" ;; *) echo "unicode" ;; esac
}

fuUI_COLORS () {
  # truecolor, 256 or 16, by the one rule of the T-Pot Manager too (prefs.py; the cases
  # are tpotctl/tests/color_cases.json): TPOT_COLORS, else "colors" of tpot.json, unless
  # that says auto; then the terminal: COLORTERM truecolor / 24bit, a TERM of a true
  # colour terminal (*-direct, kitty, ghostty, alacritty, foot, wezterm, contour, rio),
  # outside tmux and screen a terminal that says who it is (TERM_PROGRAM, LC_TERMINAL of
  # iTerm2, which comes over SSH, VTE_VERSION, KONSOLE_VERSION, WT_SESSION); 256 for a
  # TERM *256color or the Terminal of macOS, anything else 16
  local myCOLORS="${TPOT_COLORS:-}" myTERM="${TERM:-}" myMUX=""
  case "${myCOLORS}" in
    truecolor|256|16) echo "${myCOLORS}"; return 0 ;;
    auto) ;;
    *) myCOLORS=$(fuUI_PREF colors)
       case "${myCOLORS}" in truecolor|256|16) echo "${myCOLORS}"; return 0 ;; esac ;;
  esac
  myCOLORS="${COLORTERM:-}"
  case "${myCOLORS,,}" in truecolor|24bit) echo "truecolor"; return 0 ;; esac
  myTERM="${myTERM,,}"
  case "${myTERM}" in
    *-direct|xterm-kitty|xterm-ghostty|alacritty|foot*|wezterm|contour|rio) echo "truecolor"; return 0 ;;
  esac
  # in tmux or screen the terminal outside does not count, the multiplexer draws
  if [ -n "${TMUX:-}" ]; then myMUX=1; fi
  case "${myTERM}" in screen*|tmux*) myMUX=1 ;; esac
  if [ -z "${myMUX}" ]; then
    case "${TERM_PROGRAM:-}" in
      iTerm.app|WezTerm|vscode|ghostty|Hyper|Tabby|rio|WarpTerminal) echo "truecolor"; return 0 ;;
    esac
    if [ "${LC_TERMINAL:-}" = "iTerm2" ] || [ -n "${KONSOLE_VERSION:-}" ] || [ -n "${WT_SESSION:-}" ] \
       || { [[ "${VTE_VERSION:-}" =~ ^[0-9]{1,9}$ ]] && [ "$((10#${VTE_VERSION}))" -ge 3600 ]; };
      then echo "truecolor"; return 0
    fi
    [ "${TERM_PROGRAM:-}" != "Apple_Terminal" ] || { echo "256"; return 0; }
  fi
  case "${myTERM}" in *256color) echo "256" ;; *) echo "16" ;; esac
}

fuUI_TERM_SIZE () {
  # the size of the terminal on stdout into myUI_COLS and myUI_ROWS (stty, then
  # tput, then COLUMNS / LINES); rc 1 without one. Call it directly: in $(...)
  # stdout is a pipe
  local mySIZE=""
  myUI_COLS=""
  myUI_ROWS=""
  { mySIZE=$(stty size <&3 2>/dev/null); } 2>/dev/null 3<&1
  if [[ "${mySIZE}" =~ ^([0-9]+)\ ([0-9]+)$ ]] && [ "${BASH_REMATCH[1]}" -gt 0 ] && [ "${BASH_REMATCH[2]}" -gt 0 ];
    then
      myUI_ROWS="${BASH_REMATCH[1]}"
      myUI_COLS="${BASH_REMATCH[2]}"
      return 0
  fi
  if command -v tput >/dev/null 2>&1;
    then
      myUI_COLS=$(tput cols 2>/dev/null)
      myUI_ROWS=$(tput lines 2>/dev/null)
  fi
  if ! [[ "${myUI_COLS}" =~ ^[1-9][0-9]*$ && "${myUI_ROWS}" =~ ^[1-9][0-9]*$ ]];
    then
      myUI_COLS="${COLUMNS:-}"
      myUI_ROWS="${LINES:-}"
  fi
  [[ "${myUI_COLS}" =~ ^[1-9][0-9]*$ && "${myUI_ROWS}" =~ ^[1-9][0-9]*$ ]] && return 0
  myUI_COLS=""
  myUI_ROWS=""
  return 1
}

fuUI_LOGO_VARIANT () {
  # fuUI_LOGO_VARIANT <cols> <rows>: the largest logo for the terminal, as the splash
  # of the T-Pot Manager picks it: 120 (120 x 49), 80 (80 x 33), 80x24; rc 1 below
  local myC="${1:-}" myR="${2:-}"
  [[ "${myC}" =~ ^[0-9]+$ && "${myR}" =~ ^[0-9]+$ ]] || return 1
  if [ "${myC}" -ge 120 ] && [ "${myR}" -ge 49 ]; then echo "120"
  elif [ "${myC}" -ge 80 ] && [ "${myR}" -ge 33 ]; then echo "80"
  elif [ "${myC}" -ge 80 ] && [ "${myR}" -ge 24 ]; then echo "80x24"
  else return 1
  fi
  return 0
}

fuUI_MARKS_ON () {
  # 0 in the marks mode: install.sh -M (myMARKS) or the task screen of tpot (TPOT_MARKS=1)
  [ -n "${myMARKS:-}" ] || [ "${TPOT_MARKS:-}" = "1" ]
}

fuUI_LOGO_ON () {
  # 0 when the logo may show: stdout is a terminal of at least 80 x 24, gum is not
  # off, no marks mode, a TERM with colours, no NO_COLOR, not shown before in this
  # chain of scripts (TPOT_LOGO_SHOWN), not the ascii icon set; sets myUI_COLS / ROWS
  [ -t 1 ] || return 1
  [ "${TPOT_GUM:-on}" = "off" ] && return 1
  fuUI_MARKS_ON && return 1
  case "${TERM:-}" in dumb|unknown) return 1 ;; esac
  [ -n "${NO_COLOR+set}" ] && return 1
  [ -n "${TPOT_LOGO_SHOWN:-}" ] && return 1
  [ "$(fuUI_ICONS)" = "ascii" ] && return 1
  fuUI_TERM_SIZE || return 1
  [ "${myUI_COLS}" -ge 80 ] && [ "${myUI_ROWS}" -ge 24 ] && return 0
  return 1
}

fuUI_VERSION () {
  # fuUI_VERSION [checkout]: the version of T-Pot from one place: myUI_VERSION when
  # a script set it, else the file version of the checkout (default: the one of this
  # file, ~/tpotce for install.sh), else TPOT_VERSION of its .env, else empty. Only a
  # plain version string counts
  local myDIR="${1:-${myUI_CHECKOUT}}" myV="" myLINE myRE='^[0-9A-Za-z][0-9A-Za-z._+~-]*$' myENV
  myENV='^[[:space:]]*TPOT_VERSION[[:space:]]*[=:][[:space:]]*["'"'"']?([^"'"'"'[:space:]]*)'
  if [ -n "${myUI_VERSION:-}" ]; then echo "${myUI_VERSION}"; return 0; fi
  [ -r "${myDIR}/version" ] && { IFS= read -r myV < "${myDIR}/version" || true; }
  myV="${myV//[[:space:]]/}"
  [[ "${myV}" =~ ${myRE} ]] || myV=""
  if [ -z "${myV}" ] && [ -r "${myDIR}/.env" ]; then
    while IFS= read -r myLINE || [ -n "${myLINE}" ]; do
      [[ "${myLINE}" =~ ${myENV} ]] && myV="${BASH_REMATCH[1]}"
    done < "${myDIR}/.env"
    [[ "${myV}" =~ ${myRE} ]] || myV=""
  fi
  echo "${myV}"
}

fuUI_VERSION_GE () {
  # fuUI_VERSION_GE <version> <minimum>: rc 0 when the version is at least the minimum,
  # part by part as numbers (24.04.10 > 24.04.9, a missing part is 0). Spaces and line
  # ends are left out, a leading v and a suffix after - or + of the version too (2.24.4-rc1
  # counts as 2.24.4). rc 1 for an empty version or a part that is not a number
  local myI myX myY myN myV="${1//[[:space:]]/}" myM="${2//[[:space:]]/}"
  local -a myA=() myB=()
  myV="${myV#v}"
  myV="${myV%%[-+]*}"
  [ -n "${myV}" ] && [ -n "${myM}" ] || return 1
  IFS=. read -r -a myA <<< "${myV}"
  IFS=. read -r -a myB <<< "${myM}"
  myN="${#myA[@]}"
  [ "${#myB[@]}" -le "${myN}" ] || myN="${#myB[@]}"
  for ((myI = 0; myI < myN; myI++)); do
    myX="${myA[myI]-0}" myY="${myB[myI]-0}"
    [[ "${myX}" =~ ^[0-9]{1,18}$ && "${myY}" =~ ^[0-9]{1,18}$ ]] || return 1
    if [ "$((10#${myX}))" -gt "$((10#${myY}))" ]; then return 0; fi
    if [ "$((10#${myX}))" -lt "$((10#${myY}))" ]; then return 1; fi
  done
  return 0
}

fuUI_LOGO_TEXT () {
  # fuUI_LOGO_TEXT <colour> <text>: text (ASCII) over the bottom row of the logo
  # from column myX on, for fuUI_LOGO_RENDER (its myTXT / myTXC / myX)
  local myK
  for ((myK = 0; myK < ${#2}; myK++)); do
    myTXT[myX]="${2:myK:1}"
    myTXC[myX]="$1"
    myX=$((myX + 1))
  done
}

fuUI_LOGO_RENDER () {
  # fuUI_LOGO_RENDER <120|80|80x24> <truecolor|256|16> [version]: the logo and its
  # credits on stdout as the splash of the T-Pot Manager shows them at its end,
  # without any check (fuUI_LOGO makes them). Every row ends with a reset; the
  # credits are below the logo, for 80x24 in its bottom row. A version with more
  # than printable ASCII, or too long for the room, is left out: [ t-pot ]
  local LC_ALL=C IFS=$' \t\n'
  local myVARIANT="$1" myMODE="$2" myVERSION="${3:-}" myESC=$'\033' myWIDTH myI myK myN myP myL myPAIR
  local myROW myRUN myOUT myPRE myNAME myROOM myPAD myX myLAST mySG myCHAR myRESET=$'\033[0m'
  local -a myROWS=() myFG=() myBG=() mySGR=() myCH=() myCOL=() myTXT=() myTXC=()
  case "${myVARIANT}" in
    120) myROWS=("${myUI_LOGO_120[@]}"); myWIDTH=120 ;;
    80) myROWS=("${myUI_LOGO_80[@]}"); myWIDTH=80 ;;
    80x24) myROWS=("${myUI_LOGO_80x24[@]}"); myWIDTH=80 ;;
    *) return 1 ;;
  esac
  for myI in 0 1 2 3 4 5 6 7 8 9; do
    case "${myMODE}" in
      truecolor) myFG[myI]="38;2;${myUI_LOGO_RGB[myI]}"; myBG[myI]="48;2;${myUI_LOGO_RGB[myI]}" ;;
      256) myFG[myI]="38;5;${myUI_LOGO_256[myI]}"; myBG[myI]="48;5;${myUI_LOGO_256[myI]}" ;;
      16) myFG[myI]="${myUI_LOGO_16[myI]}"; myBG[myI]="$((myUI_LOGO_16[myI] + 10))" ;;
      *) return 1 ;;
    esac
  done
  # a pair of pixels (top, bottom) as a character and its colours, as the splash does
  myN=0
  for myPAIR in ${myUI_LOGO_PAIRS}; do
    myP="${myPAIR:0:1}"
    myL="${myPAIR:1:1}"
    if [ "${myP}${myL}" = "00" ]; then mySGR[myN]="0"; myCH[myN]=" "
    elif [ "${myP}" = "${myL}" ]; then mySGR[myN]="0;${myBG[myP]}"; myCH[myN]=" "
    elif [ "${myL}" = "0" ]; then mySGR[myN]="0;${myFG[myP]}"; myCH[myN]="▀"
    elif [ "${myP}" = "0" ]; then mySGR[myN]="0;${myFG[myL]}"; myCH[myN]="▄"
    else mySGR[myN]="0;${myFG[myP]};${myBG[myL]}"; myCH[myN]="▀"
    fi
    myN=$((myN + 1))
  done
  # the credits: t-pot and the version where they fit (splash_anim.Splash.credits)
  [[ "${myVERSION}" =~ ^[[:print:]]*$ ]] || myVERSION=""
  myNAME="t-pot ${myVERSION}"
  myNAME="${myNAME% }"
  myROOM=$((myWIDTH - 34))
  [ "${myVARIANT}" != "80x24" ] || myROOM=24
  [ $((${#myNAME} + 4)) -le "${myROOM}" ] || myNAME="t-pot"
  for ((myI = 0; myI < ${#myROWS[@]}; myI++)); do
    myROW="${myROWS[myI]}"
    if [ "${myVARIANT}" = "80x24" ] && [ "${myI}" -eq $((${#myROWS[@]} - 1)) ]; then
      # the bottom row has room left and right of the honey pool for the credits
      myCOL=()
      for ((myK = 0; myK < ${#myROW}; myK += 2)); do
        myPRE="${myUI_LOGO_ABC%%"${myROW:myK:1}"*}"
        myP="${#myPRE}"
        myPRE="${myUI_LOGO_ABC%%"${myROW:myK+1:1}"*}"
        for ((myL = 0; myL <= ${#myPRE}; myL++)); do myCOL+=("${myP}"); done
      done
      myX=1
      fuUI_LOGO_TEXT 2 "--[ "
      fuUI_LOGO_TEXT 7 "${myNAME}"
      fuUI_LOGO_TEXT 2 " ]"
      myTXT[1]="─"
      myTXT[2]="─"
      myX=$((myWIDTH - 24))
      fuUI_LOGO_TEXT 2 "[ "
      fuUI_LOGO_TEXT 4 "telekom security"
      fuUI_LOGO_TEXT 2 " ]--"
      myTXT[myX - 2]="─"
      myTXT[myX - 1]="─"
      myOUT=""
      myLAST=""
      for ((myX = 0; myX < myWIDTH; myX++)); do
        if [ -n "${myTXT[myX]:-}" ]; then
          myCHAR="${myTXT[myX]}"
          mySG="0;${myFG[myTXC[myX]]}"
          [ "${myCHAR}" != " " ] || mySG="0"
        else
          myP="${myCOL[myX]:-0}"
          myCHAR="${myCH[myP]}"
          mySG="${mySGR[myP]}"
        fi
        if [ "${mySG}" != "${myLAST}" ]; then myOUT+="${myESC}[${mySG}m"; myLAST="${mySG}"; fi
        myOUT+="${myCHAR}"
      done
      printf '%s%s\n' "${myOUT}" "${myRESET}"
      continue
    fi
    myOUT=""
    for ((myK = 0; myK < ${#myROW}; myK += 2)); do
      myPRE="${myUI_LOGO_ABC%%"${myROW:myK:1}"*}"
      myP="${#myPRE}"
      myPRE="${myUI_LOGO_ABC%%"${myROW:myK+1:1}"*}"
      printf -v myRUN '%*s' $((${#myPRE} + 1)) ''
      [ "${myCH[myP]}" = " " ] || myRUN="${myRUN// /${myCH[myP]}}"
      myOUT+="${myESC}[${mySGR[myP]}m${myRUN}"
    done
    printf '%s%s\n' "${myOUT}" "${myRESET}"
  done
  [ "${myVARIANT}" != "80x24" ] || return 0
  # below the logo: an empty line, then the credits in the middle
  printf -v myPAD '%*s' $(((myWIDTH - 30 - ${#myNAME}) / 2)) ''
  printf '%s\n' "${myRESET}"
  printf '%s%s──[ %s%s%s ]══[ %stelekom security%s ]──%s\n' "${myPAD}" "${myESC}[0;${myFG[2]}m" \
    "${myESC}[0;${myFG[7]}m" "${myNAME}" "${myESC}[0;${myFG[2]}m" "${myESC}[0;${myFG[4]}m" \
    "${myESC}[0;${myFG[2]}m" "${myRESET}"
  return 0
}

# shellcheck disable=SC2120 # the version is for the scripts, fuUI_BANNER leaves it out
fuUI_LOGO () {
  # fuUI_LOGO [version]: the T-Pot logo with its credits where fuUI_LOGO_ON allows it,
  # in the largest size for the terminal and its colours; marks it shown for the
  # scripts this one starts (TPOT_LOGO_SHOWN). rc 1 without the logo. The version
  # of the credits: the argument, else fuUI_VERSION
  local myVARIANT myVERSION
  fuUI_LOGO_ON || return 1
  myVARIANT=$(fuUI_LOGO_VARIANT "${myUI_COLS}" "${myUI_ROWS}") || return 1
  if [ "$#" -gt 0 ]; then myVERSION="$1"; else myVERSION=$(fuUI_VERSION); fi
  fuUI_LOGO_RENDER "${myVARIANT}" "$(fuUI_COLORS)" "${myVERSION}" || return 1
  export TPOT_LOGO_SHOWN=1
  return 0
}

fuUI_HANG () {
  # fuUI_HANG <room> <text>: the text as it is when every line of it fits the room,
  # else broken at spaces (fuUI_FOLD) with the lines after the first indented by two
  local myROOM="$1" myLINE myOUT
  while IFS= read -r myLINE; do
    if [ "${#myLINE}" -gt "${myROOM}" ] && [ "${myROOM}" -ge 12 ];
      then
        myOUT=$(fuUI_FOLD $((myROOM - 2)) "$2")
        printf '%s\n' "${myOUT//$'\n'/$'\n'  }"
        return 0
    fi
  done <<< "$2"
  printf '%s\n' "$2"
}

fuUI_BANNER () {
  # fuUI_BANNER <title> <line> ...: the T-Pot logo (myUI_LOGO=1, see fuUI_LOGO) or the
  # T-Pot wordmark of the T-Pot Manager (tpotctl/logo.py), then a title and lines. At a
  # terminal a line wider than it is broken, with a hanging indent
  local myTITLE="$1" myLINE myTEXT myLOGO="" myCOLS=""
  local -a myLINES=()
  shift
  if [ "${myUI_LOGO}" = "1" ] && fuUI_LOGO; then myLOGO=1; fi
  if [ -t 1 ] && fuUI_TERM_SIZE; then myCOLS="${myUI_COLS}"; fi
  if [ -z "${myUI_GUM}" ];
    then
      echo
      echo "### T-Pot ${myTITLE}"
      for myLINE in "$@"; do
        [ -z "${myCOLS}" ] || myTEXT=$(fuUI_HANG $((myCOLS - 4)) "${myLINE}")
        if [ -z "${myCOLS}" ] || [ "${myTEXT}" = "${myLINE}" ];
          then echo "### ${myLINE}"
          else echo "### ${myTEXT//$'\n'/$'\n'### }"
        fi
      done
      echo
      return
  fi
  echo
  if [ -z "${myLOGO}" ];
    then
      "${myUI_GUM}" style --foreground "${myUI_MAGENTA}" --bold --margin "0 2" -- "${myUI_WORDMARK[@]}"
      echo
  fi
  "${myUI_GUM}" style --foreground "${myUI_GLASS}" --bold --margin "0 2" -- "T-Pot ${myTITLE}"
  # gum style does not break lines; its margin takes two columns on each side
  for myLINE in "$@"; do
    if [ -n "${myCOLS}" ];
      then mapfile -t -O "${#myLINES[@]}" myLINES < <(fuUI_HANG $((myCOLS - 4)) "${myLINE}")
      else myLINES+=("${myLINE}")
    fi
  done
  [ "${#myLINES[@]}" -eq 0 ] || "${myUI_GUM}" style --foreground "${myUI_ASH}" --margin "0 2" -- "${myLINES[@]}"
  echo
}

fuUI_INFO () {
  # fuUI_INFO <text>: a line of what happens; at a terminal a line wider than it is broken,
  # with a hanging indent
  local myTEXT="$*" myCOLS="" myLINE myFIRST=1
  if [ -t 1 ] && fuUI_TERM_SIZE; then myCOLS="${myUI_COLS}"; fi
  if [ -z "${myUI_GUM}" ];
    then
      [ -z "${myCOLS}" ] || myTEXT=$(fuUI_HANG $((myCOLS - 4)) "${myTEXT}")
      if [ "${myTEXT}" = "$*" ];
        then echo "### $*"
        else echo "### ${myTEXT//$'\n'/$'\n'### }"
      fi
      return 0
  fi
  [ -z "${myCOLS}" ] || myTEXT=$(fuUI_HANG $((myCOLS - 2)) "${myTEXT}")
  if [ -z "${myCOLS}" ] || [ "${myTEXT}" = "$*" ];
    then
      echo "$(fuUI_PAINT "${myUI_MAGENTA}" "⬢") $(fuUI_PAINT "${myUI_GLASS}" "${myTEXT}")"
      return 0
  fi
  while IFS= read -r myLINE; do
    if [ -n "${myFIRST}" ];
      then echo "$(fuUI_PAINT "${myUI_MAGENTA}" "⬢") $(fuUI_PAINT "${myUI_GLASS}" "${myLINE}")"; myFIRST=""
      else echo "  $(fuUI_PAINT "${myUI_GLASS}" "${myLINE}")"
    fi
  done <<< "${myTEXT}"
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

fuUI_FOLD () {
  # fuUI_FOLD <width> <text>: a paragraph (lines that do not start with a space) with a
  # line wider than width is broken anew at spaces; the others and indented lines stay
  printf '%s\n' "$2" | awk -v w="$1" '
    function flush(  i, n, line, words) {
      if (np == 0) return
      if (wide <= w) { for (i = 1; i <= np; i++) print part[i] }
      else {
        n = split(joined, words, / +/); line = ""
        for (i = 1; i <= n; i++) {
          if (words[i] == "") continue
          if (line == "") line = words[i]
          else if (length(line) + 1 + length(words[i]) <= w) line = line " " words[i]
          else { print line; line = words[i] }
        }
        if (line != "") print line
      }
      np = 0; joined = ""; wide = 0
    }
    /^ / || /^$/ { flush(); print; next }
    { part[++np] = $0; joined = (joined == "" ? $0 : joined " " $0); if (length($0) > wide) wide = length($0) }
    END { flush() }'
}

fuUI_HELP () {
  # fuUI_HELP <title> <usage> [--about <text>]... [--opt <flags> <text>]...
  #   [--example <command> <text>]... [--note <text>]...: the help of a script on
  # stdout, the same layout for all T-Pot scripts, rc 0. A text may have more lines;
  # the option texts line up after the longest flags (up to 24 characters, longer
  # ones get their text on the next line). The texts are broken to the terminal
  # (80 columns without one, 100 at most). Coloured at a terminal with gum
  local myTITLE="$1" myUSAGE="$2" myW=0 myI myLINE myFIRST myREST myPAD myCOLS=80 myTEXT
  local myT="" myH="" myF="" myR="" myMODE myN
  shift 2
  local -a myABOUT=() myFLAGS=() myTEXTS=() myCMDS=() myCTEXTS=() myNOTES=() myC=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --about) myABOUT+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
      --opt) myFLAGS+=("${2:-}"); myTEXTS+=("${3:-}"); shift $(( $# < 3 ? $# : 3 )) ;;
      --example) myCMDS+=("${2:-}"); myCTEXTS+=("${3:-}"); shift $(( $# < 3 ? $# : 3 )) ;;
      --note) myNOTES+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
      *) shift ;;
    esac
  done
  if [ -n "${myUI_GUM}" ] && [ -t 1 ] && [ -z "${NO_COLOR+set}" ]; then
    # the colours of the logo: magenta (4) for the title and the flags, glass (7) for the heads
    myMODE=$(fuUI_COLORS)
    for myN in 4 7; do
      case "${myMODE}" in
        truecolor) myC[myN]="38;2;${myUI_LOGO_RGB[myN]}" ;;
        256) myC[myN]="38;5;${myUI_LOGO_256[myN]}" ;;
        *) myC[myN]="${myUI_LOGO_16[myN]}" ;;
      esac
    done
    myT=$'\033'"[1;${myC[4]}m" myH=$'\033'"[1;${myC[7]}m" myF=$'\033'"[${myC[4]}m" myR=$'\033[0m'
  fi
  for myI in "${!myFLAGS[@]}"; do
    if [ "${#myFLAGS[myI]}" -gt "${myW}" ] && [ "${#myFLAGS[myI]}" -le 24 ]; then myW="${#myFLAGS[myI]}"; fi
  done
  if [ -t 1 ] && fuUI_TERM_SIZE; then myCOLS="${myUI_COLS}"; fi
  [ "${myCOLS}" -le 100 ] || myCOLS=100
  [ "${myCOLS}" -ge $((myW + 26)) ] || myCOLS=$((myW + 26))
  printf '%sT-Pot %s%s\n\n%sUsage:%s %s\n' "${myT}" "${myTITLE}" "${myR}" "${myH}" "${myR}" \
    "${myUSAGE//$'\n'/$'\n'       }"
  for myLINE in "${myABOUT[@]}"; do printf '\n%s\n' "$(fuUI_FOLD "${myCOLS}" "${myLINE}")"; done
  if [ "${#myFLAGS[@]}" -gt 0 ]; then
    printf '\n%sOptions:%s\n' "${myH}" "${myR}"
    printf -v myPAD '%*s' $((myW + 6)) ''
    for myI in "${!myFLAGS[@]}"; do
      myTEXT=$(fuUI_FOLD $((myCOLS - myW - 6)) "${myTEXTS[myI]}")
      myFIRST="${myTEXT%%$'\n'*}"
      myREST=""
      [ "${myFIRST}" = "${myTEXT}" ] || myREST="${myTEXT#*$'\n'}"
      if [ "${#myFLAGS[myI]}" -le "${myW}" ];
        then printf '  %s%*s    %s\n' "${myF}${myFLAGS[myI]}${myR}" $((myW - ${#myFLAGS[myI]})) '' "${myFIRST}"
        else printf '  %s\n%s%s\n' "${myF}${myFLAGS[myI]}${myR}" "${myPAD}" "${myFIRST}"
      fi
      [ -z "${myREST}" ] || printf '%s%s\n' "${myPAD}" "${myREST//$'\n'/$'\n'${myPAD}}"
    done
  fi
  if [ "${#myCMDS[@]}" -gt 0 ]; then
    printf '\n%sExamples:%s\n' "${myH}" "${myR}"
    for myI in "${!myCMDS[@]}"; do
      printf '  %s\n' "${myF}${myCMDS[myI]}${myR}"
      myTEXT=$(fuUI_FOLD $((myCOLS - 6)) "${myCTEXTS[myI]}")
      [ -z "${myCTEXTS[myI]}" ] || printf '      %s\n' "${myTEXT//$'\n'/$'\n'      }"
    done
  fi
  if [ "${#myNOTES[@]}" -gt 0 ]; then
    printf '\n%sNotes:%s\n' "${myH}" "${myR}"
    for myLINE in "${myNOTES[@]}"; do
      myTEXT=$(fuUI_FOLD $((myCOLS - 2)) "${myLINE}")
      printf '  %s\n' "${myTEXT//$'\n'/$'\n'  }"
    done
  fi
  return 0
}

fuUI_USAGE_ERROR () {
  # fuUI_USAGE_ERROR <message> [script]: a wrong option or value on stderr and where
  # the help is, rc 1 (the caller exits with it)
  fuUI_ERROR "$1"
  fuUI_HINT "${2:-${0##*/}} -h shows the options." >&2
  return 1
}

fuUI_LINUX_ONLY () {
  # fuUI_LINUX_ONLY <script> [rc]: ends the script with rc (1) outside Linux (uname -s:
  # macOS, Windows with MINGW / MSYS / Cygwin, any other), an error and where it runs on
  # stderr. WSL2 is Linux. Call it after -h, the help shows everywhere
  local mySYSTEM
  mySYSTEM=$(uname -s 2>/dev/null)
  [ "${mySYSTEM}" != "Linux" ] || return 0
  case "${mySYSTEM}" in
    Darwin) mySYSTEM="macOS" ;;
    MINGW*|MSYS*|CYGWIN*) mySYSTEM="Windows (${mySYSTEM})" ;;
    "") mySYSTEM="an unknown system" ;;
  esac
  fuUI_ERROR "$1 does not run on ${mySYSTEM}."
  fuUI_HINT "$1 runs on Linux: a T-Pot host, a build host or a VM, WSL2 on Windows." >&2
  exit "${2:-1}"
}

fuUI_RESULT () {
  # fuUI_RESULT <ok|fail|warn|next|info> <text>: one line of a result, i.e. of a summary
  if [ -n "${myUI_GUM}" ]; then
    case "$1" in
      ok) echo "$(fuUI_PAINT "${myUI_OK_COLOUR}" "✓") $2" ;;
      fail) echo "$(fuUI_PAINT "${myUI_ERROR_COLOUR}" "✗ $2")" ;;
      warn) echo "$(fuUI_PAINT "${myUI_WARN_COLOUR}" "! $2")" ;;
      next) echo "$(fuUI_PAINT "${myUI_MAGENTA}" "→") $(fuUI_PAINT "${myUI_GLASS}" "$2")" ;;
      *) echo "$(fuUI_PAINT "${myUI_MAGENTA}" "⬢") $2" ;;
    esac
    return 0
  fi
  case "$1" in
    ok) echo "### [OK] - $2" ;;
    fail) echo "### [FAILED] - $2" ;;
    warn) echo "### [WARNING] - $2" ;;
    next) echo "### [NEXT] - $2" ;;
    *) echo "### $2" ;;
  esac
}

fuUI_SUMMARY () {
  # fuUI_SUMMARY <title> [<ok|fail|warn|next|info>:<text>]...: the end of a script, its
  # results and what comes next in a box (gum) or as plain lines; rc 1 when one of
  # them is a fail. An item without a known kind is info
  local myTITLE="$1" myITEM myKIND myTEXT myRC=0 myLONGEST="${#1}"
  shift
  local -a myLINES=() myWIDTH=()
  for myITEM in "$@"; do
    myKIND="${myITEM%%:*}" myTEXT="${myITEM#*:}"
    case "${myKIND}" in ok|fail|warn|next|info) ;; *) myKIND="info" myTEXT="${myITEM}" ;; esac
    [ "${myKIND}" != "fail" ] || myRC=1
    myLINES+=("$(fuUI_RESULT "${myKIND}" "${myTEXT}")")
    # an item is its sign, a space and the text
    [ "$((${#myTEXT} + 2))" -le "${myLONGEST}" ] || myLONGEST=$((${#myTEXT} + 2))
  done
  echo
  if [ -n "${myUI_GUM}" ];
    then
      # gum does not wrap on its own: a box wider than the terminal gets its width (gum
      # wraps the lines in it then), 80 columns when the size is unknown. The box takes
      # 8 columns more than its longest line: padding, border and margin, 2 each side
      fuUI_TERM_SIZE || myUI_COLS=80
      [ "${myUI_COLS}" -ge 24 ] || myUI_COLS=24
      [ "$((myLONGEST + 8))" -le "${myUI_COLS}" ] || myWIDTH=(--width "$((myUI_COLS - 4))")
      "${myUI_GUM}" style --border rounded --border-foreground "${myUI_MAGENTA}" --padding "0 1" \
        --margin "0 2" "${myWIDTH[@]}" -- \
        "$(CLICOLOR_FORCE=1 "${myUI_GUM}" style --foreground "${myUI_GLASS}" --bold -- "${myTITLE}")" \
        "${myLINES[@]}"
    else
      echo "### ${myTITLE}"
      [ "${#myLINES[@]}" -eq 0 ] || printf '%s\n' "${myLINES[@]}"
  fi
  echo
  return "${myRC}"
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

fuUI_CHOOSE_MANY () {
  # fuUI_CHOOSE_MANY [--filter] [--all | --selected <value>]... <header>
  #   <label:value> ...: prints the values of the choices, one per line, in the order
  # of the items (label and value split at the last colon, so a label may have one).
  # --all / --selected mark items to begin with, --selected once per value (a value
  # may have commas). gum choose at a terminal (gum filter with --filter, to find an
  # item by typing), otherwise a numbered list on stderr and a line from stdin: 1,3-5,
  # a for all, n for none, enter for the marked ones. rc 1 when stdin ends, the rc of
  # gum when it is cancelled
  local myFILTER="" myALL="" myI myJ myN myPICK myPART myA myB myOK myOUT myLINE
  local -a mySELECTED=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --filter) myFILTER=1; shift ;;
      --all) myALL=1; shift ;;
      --selected) mySELECTED+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
      *) break ;;
    esac
  done
  local myHEADER="${1:-}"
  [ "$#" -eq 0 ] || shift
  local -a myLABELS=() myVALUES=() myON=() myNEW=() myARGS=()
  for myI in "$@"; do
    myLABELS+=("${myI%:*}")
    myVALUES+=("${myI##*:}")
    myON+=("${myALL}")
    for myJ in "${mySELECTED[@]}"; do
      [ "${myJ}" != "${myVALUES[${#myVALUES[@]}-1]}" ] || myON[${#myON[@]}-1]=1
    done
  done
  myN="${#myLABELS[@]}"
  if [ -n "${myUI_GUM}" ] && [ -t 0 ]; then
    # gum filter has no --label-delimiter: gum gets the labels, they turn into values here.
    # gum splits --selected at commas (\, is one) and takes it again for more labels; * is
    # all of them. A label * or one with \, in it cannot be marked alone, it starts unmarked
    myARGS=(--no-limit --header "${myHEADER}" --header.foreground "${myUI_GLASS}")
    if [ -n "${myALL}" ]; then myARGS+=(--selected "*")
    else
      for myI in "${!myLABELS[@]}"; do
        [ -n "${myON[myI]}" ] || continue
        [ "${myLABELS[myI]}" != "*" ] && [[ "${myLABELS[myI]}" != *'\,'* ]] || continue
        myARGS+=(--selected "${myLABELS[myI]//,/\\,}")
      done
    fi
    if [ -n "${myFILTER}" ];
      then
        myOUT=$("${myUI_GUM}" filter "${myARGS[@]}" --indicator.foreground "${myUI_MAGENTA}" \
          --match.foreground "${myUI_MAGENTA}" --selected-indicator.foreground "${myUI_MAGENTA}" \
          --prompt.foreground "${myUI_MAGENTA}" -- "${myLABELS[@]}") || return $?
      else
        myOUT=$("${myUI_GUM}" choose "${myARGS[@]}" --cursor.foreground "${myUI_MAGENTA}" \
          --item.foreground "${myUI_ASH}" --selected.foreground "${myUI_MAGENTA}" -- "${myLABELS[@]}") || return $?
    fi
    for myI in "${!myLABELS[@]}"; do
      while IFS= read -r myLINE; do
        if [ "${myLINE}" = "${myLABELS[myI]}" ]; then printf '%s\n' "${myVALUES[myI]}"; break; fi
      done <<< "${myOUT}"
    done
    return 0
  fi
  echo "### ${myHEADER}" >&2
  for myI in "${!myLABELS[@]}"; do
    if [ -n "${myON[myI]}" ]; then echo "###   $((myI + 1))) [x] ${myLABELS[myI]}" >&2
    else echo "###   $((myI + 1))) [ ] ${myLABELS[myI]}" >&2; fi
  done
  while true; do
    read -rp "### Choice (i.e. 1,3-5; a = all, n = none, enter = the marked ones): " myPICK || return 1
    myPICK="${myPICK//[[:space:]]/}"
    case "${myPICK}" in
      "") break ;;
      a|A) for myI in "${!myON[@]}"; do myON[myI]=1; done; break ;;
      n|N) for myI in "${!myON[@]}"; do myON[myI]=""; done; break ;;
    esac
    myNEW=()
    myOK=1
    for myI in "${!myON[@]}"; do myNEW[myI]=""; done
    if [[ "${myPICK}" =~ ^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$ ]]; then
      for myPART in ${myPICK//,/ }; do
        myA=$((10#${myPART%-*})) myB=$((10#${myPART#*-}))
        if [ "${myA}" -lt 1 ] || [ "${myB}" -gt "${myN}" ] || [ "${myA}" -gt "${myB}" ]; then myOK=""; break; fi
        for ((myI = myA; myI <= myB; myI++)); do myNEW[myI - 1]=1; done
      done
    else myOK=""
    fi
    if [ -n "${myOK}" ]; then myON=("${myNEW[@]}"); break; fi
    echo "### [WARNING] - Not a choice: ${myPICK}" >&2
  done
  for myI in "${!myVALUES[@]}"; do
    [ -z "${myON[myI]}" ] || printf '%s\n' "${myVALUES[myI]}"
  done
  return 0
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

fuMARK () {
  # machine readable progress for tpot, one line each: @@tpot <what> <value ...>.
  # install.sh -M sets myMARKS, the task screen of tpot TPOT_MARKS=1
  fuUI_MARKS_ON || return 0
  echo "@@tpot $*"
}

fuUI_ALIVE () {
  # fuUI_ALIVE <pid>: 0 while the process is there. kill -0 is not enough: a process of
  # root (sudo, what it runs) refuses the signal of a user, /proc (ps -p without one)
  # still has it
  kill -0 "$1" 2>/dev/null && return 0
  if [ -d /proc/self ];
    then [ -d "/proc/$1" ]
    else ps -p "$1" >/dev/null 2>&1
  fi
}

fuUI_TREE () {
  # fuUI_TREE <pid>: the pid and those of everything it started, one per line, parents first
  local myTABLE myPID myPPID myI=0
  local -a myTREE=("$1")
  myTABLE=$(ps -A -o pid= -o ppid= 2>/dev/null) || myTABLE=""
  while [ "${myI}" -lt "${#myTREE[@]}" ]; do
    while read -r myPID myPPID; do
      [ "${myPPID}" != "${myTREE[myI]}" ] || myTREE+=("${myPID}")
    done <<< "${myTABLE}"
    myI=$((myI + 1))
  done
  printf '%s\n' "${myTREE[@]}"
}

fuUI_STOP () {
  # fuUI_STOP <pid>: ends a step of fuUI_SPIN and everything it started, TERM, KILL after
  # 5 s. What runs as root (sudo) takes the signal through sudo -n, which never asks: the
  # steps refresh sudo before. rc 1 when the step is still there
  local mySIG myP myI
  local -a myPIDS=() myROOT=()
  for mySIG in TERM KILL; do
    mapfile -t myPIDS < <(fuUI_TREE "$1")
    myROOT=()
    for myP in "${myPIDS[@]}"; do
      kill -s "${mySIG}" "${myP}" 2>/dev/null || ! fuUI_ALIVE "${myP}" || myROOT+=("${myP}")
    done
    [ "${#myROOT[@]}" -eq 0 ] || sudo -n kill -s "${mySIG}" "${myROOT[@]}" >/dev/null 2>&1
    for ((myI = 0; myI < 50; myI++)); do
      fuUI_ALIVE "$1" || return 0
      sleep 0.1
    done
  done
  return 1
}

fuUI_SPIN () {
  # fuUI_SPIN <title> <log file> <command> ...: runs the command (a function works too)
  # with its output in the log file and a spinner meanwhile; shows the end of the log
  # on failure. Under gum the command runs in the background, in a subshell: what it
  # sets does not reach the caller, and it must not prompt: refresh sudo before
  # (sudo -v) where it needs a password. Without gum it runs in this shell. In the
  # marks mode (fuUI_MARKS_ON) its output goes through as well, in the foreground, so the
  # T-Pot Manager reads it (i.e. the pulls of the images), and into the log by tee:
  # stdout and stderr together, the rc of the step, also when the log cannot be
  # written; the step runs in a subshell there too
  local myTITLE="$1" myLOG="$2"
  shift 2
  local myPID myRC=0 myGUM_RC=0 myTRAP
  if fuUI_MARKS_ON;
    then
      echo "### ${myTITLE}"
      { "$@" < /dev/null 2>&1 | tee -a "${myLOG}" 2>/dev/null; myRC="${PIPESTATUS[0]}"; } || true
  elif [ -n "${myUI_GUM}" ];
    then
      "$@" >>"${myLOG}" 2>&1 < /dev/null &
      myPID=$!
      # Ctrl+C: gum has the terminal in raw mode, it reads the key and ends with 130, no
      # SIGINT reaches the step (in the background it ignores one anyway). Without a
      # terminal on stdin gum leaves the terminal as it is, the SIGINT then ends gum and
      # (without the trap) this script, the step would go on. Either way the step and all
      # it started end here, the caller stops on 130. The spinner waits as long as
      # fuUI_ALIVE says (a step of sudo is root's, kill -0 of a user fails on it)
      myTRAP=$(trap -p INT)
      trap 'myGUM_RC=130' INT
      "${myUI_GUM}" spin --spinner dot --spinner.foreground "${myUI_MAGENTA}" --title "${myTITLE}" \
        --title.foreground "${myUI_GLASS}" -- sh -c "while kill -0 ${myPID} 2>/dev/null || \
{ if [ -d /proc/self ]; then [ -d /proc/${myPID} ]; else ps -p ${myPID} >/dev/null 2>&1; fi; }; do sleep 0.2; done" \
        || myGUM_RC=$?
      eval "${myTRAP:-trap - INT}"
      if [ "${myGUM_RC}" -ne 0 ] && fuUI_ALIVE "${myPID}";
        then
          fuUI_STOP "${myPID}"
          wait "${myPID}" 2>/dev/null
          fuUI_WARN "Stopped: ${myTITLE%% ...}"
          return 130
      fi
      wait "${myPID}" || myRC=$?
    else
      echo "### ${myTITLE}"
      "$@" >>"${myLOG}" 2>&1 < /dev/null || myRC=$?
  fi
  if [ "${myRC}" -eq 0 ];
    then fuUI_OK "${myTITLE%% ...}"
  elif fuUI_MARKS_ON;
    then fuUI_ERROR "${myTITLE%% ...} failed"
    else
      fuUI_ERROR "${myTITLE%% ...} failed, the end of ${myLOG}:"
      tail -n 15 "${myLOG}" >&2
  fi
  return "${myRC}"
}

# >>> tpot logo data >>>
# generated from tpotctl/logo.py, tpotctl/splash_art.py and tpotctl/theme.py by
# python3 -m tpotctl.ui_logo, do not edit; the format is in tpotctl/ui_logo.py
# the colours of the logo (tpotctl/splash_art.py COLOURS, splash_anim.colours()): true
# colour as R;G;B, the entries of the xterm 256 palette, the SGR codes of the 16 ANSI
# colours (background: +10). Pixel 0 is never painted, it stays the terminal's background.
myUI_LOGO_RGB=("0;0;0" "56;0;29" "103;0;58" "162;0;83" "226;0;116"
               "255;76;167" "255;157;208" "236;239;249" "91;88;90" "162;162;173")
myUI_LOGO_256=(16 53 89 125 162 205 218 231 240 248)
myUI_LOGO_16=(30 35 35 35 95 95 97 97 90 37)
myUI_WORDMARK=(
  '▀▀▀▀██▀▀▀▀        ██▀▀▀▀▄▄            ▄▄'
  '    ██      ▄▄▄▄  ██▄▄▄▄▀▀  ██▀▀██  ▀▀██▀▀'
  '    ██            ██        ██▄▄██    ██▄▄'
)
myUI_LOGO_ABC='!#%&()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ^_abcdefghijklmnopqrstuvwxyz{|}~'
myUI_LOGO_PAIRS='00 01 02 03 12 11 14 31 20 10 32 13 33 23 42 44 21 43 30 41 08 18 04 40 81 69 99 22 34 24 45 49 88 46 63 91 53 52 82 83 80 87 79 98 55 77 54 89 58 56 25 35 67 19 65 97 90 09 78 17 95 64 16 06 71 96 93 74 85 84 29 51 62 38 15 86 28 94'
myUI_LOGO_120=(
''
'!_#!%!&,#!'
'!D(!)!!7#!*!+!,!-+,!.!/!#!'
'!B#!/!0!1!/!#!!4*!2!,!!!#,!#,!3!'
'!D4!)!!65!*!#!-!#!)*#!-!!!%!5!'
'!R%#&!%#&!%!&!6!7!4!-!))-!)!4!6!,!#!&#%#'
'!:8!9!##!0&!:#*!0!+!6%,!-&(!%#&!(#/,&!%!-!6#;!+!0!:!&!%!'
'!4##!%-!<!=!>!<!-!!,&!*!?!6!,!-%!%%!(!?(1!05@!A!*#/!4!/!:!&!'
'!1%!*!1!+#/!&!!#-!<!-#!+:!?!0!@!/!%!#!-!,#6#+!?#1!A!@!A#@93#@!#!0#%#'
'!.#!&!1!;!)!-!)#-!,!7!A!&!!.3!)!-!4!+!2!0!/!*&/!(#?%.%2!35B#C!5!+!4#-#0!'
'!.)!.!!!)*#!!!3!!.3!/!#!D#8!!#,(6+-!6!5!3#B!E!3+B!3!F!+!,#!#8!D!G!!!(!0!'
'!.)!0!!!)+!!3!!-#!&!2!.!/%%#!2-!;!H!6!,!;!H!3&B!I!,!#!%!!&#!J!&!A!.!0!/!#!'
'!-#!(!0!#!!!))!!#!3!#!!*%!A!.!6!-!8!)!-#6#+!4#?!/!&!:!&/!#-!0!3#B!.!!!#!1!K!&!/!?!+!6!,!-#!!-!+!0!/!#!'
'!*#!%!*!.!+#.!*!#!-!)!-!#!%!*!7!,!7!A!%!#!!%/!B!L!!!8!M!N!O!-!!%#&)(-#!.0!3!P!.!-!!.-!D!9!#!-!+!0!/!'
'!(%!*!.!6!)!#!)##!)!;!/!&!/!+!,!!(,!?!(!#!*!5!-!!!M!Q!N!<!-!!%)0#!!,0!3!R!)!!)#(!#-!D!S!9!!!)!0!/!'
'!(3!!#)+!!0!)!!*-!!#(!T!-!!!M!Q!O!L!!&)/-%!,0!3!U!)!!(-!)(#!!#-!D!>!S!!!-!+!V!!1#!)!##'
'!(3!!!),!!0!)!!,(!W!4!!#Q!N!L!!%-!),-(!.0!3!X!9!!)-!)(#!!#-!O!>!Y!!!#!P!!14!0#)!'
'!(3!#!-!)*-!#!0!)!!!#*!!?!J!!#8!Q!D!!&#!))-!!50!B!Z!4!!*))!%<!O!^!8!!!+!/!!0-!4!-#'
'!(,!7!1!&!)!-!)%&!-!6#-!!!,*!!,#!#_!L!!)-!)#-#!86!,!!-)(!%L!O!D!!!)!0!'
'!+-!+!0!/!(!4!-!a!O!G6_!S!8!!.8!Y!b!G3c!8!!,-!)#!&D#!!)!+!#!a!b!G*a!#!'
'!.,#9!O!L!&!d!R4e!f!)!N!)!!,S!G!<!&!P!R1g!!!h!M!8!!,-!!&-#!!#!Y!O!L!a!i!R(A!(!b!Y!'
'!0D#j!e!37)!D!)!!,>!)!W!k!33e!&!D#!4)!>!#!c!k!3*4!)!>!'
'!!,!6#.+4!D#0!355!0#)!D!)!!!#!!*>!)!360!D#!!#!Y!D.S!8!!!8!-!D!3,(!)!O#G!Y!#!+!.)+!6!,!'
'!&,*6!-!<!D!)!-(4!B!3*4!-)#!O!-!!!-!)%##!#>!)!3*-(B!3)0!<#a!O!<!/!@,1!<!O!S!!!0!31!!D#-!,)-!'
'!()!?!1#A!1#(!!!L!>!a&#!)!P!3*)!!!8!a%S!G!-!!-L!)!3*!(P!3)0!!#J!f!W!R!3-@!/!J!<!.!5!30!!D#(!1%?!)!'
'!,#(!#-%L!S!)!P!3*)!!!>!D!)%<!G!h,Y!!!)!3*&!8#a!l!3)5!0!!!0!R#3&5&3)e!&!!!,!7!B!3*7!+!7!+!!!D###'
'!,,#6#,!?!!!#!%#!!>!)!P!3)@!)!!!S!9!O!e.R!!!>!!!)!350!.!!!0!3(0!!&>!3)0!!##!P!3)0!!&a!O!-!,#-!'
'!0)!0!!#-#!!Q!)!3*5!#!!!>!D!0!3/!!>!!!)!325#0!.!,!!!0!3(0!!&>!3)0!!#)!P!3*!!)!>!_!L!'
'!0)!0!!(Q!)!3)@!0!)!!!>!D!.!5-.#!!>!!!)!3*0!.,,!!!8#0!3(0!!&>!3)0!!#)!P!3(5!0!!!-!>!Y!8!!!&!%!'
'!0)!0!!(Q!)!3!0!5#3!5#0!)!#!O!S!8!-!,-!!a!O!!!)!3)5!?!#!!+8!S!_!!!0!3(@!/!a#K!R!3(0!?!!#)!P!3(5!@!A!m#/!G!S!8!6!.!*!#!'
'!%#!1###!(-!.!!(Q!)!3!0*(!)!>!D!L!D!a-O!L!!!8!)!3&5!3!0!?!)!!!S!<)G!!!8#0#305!0%!#)!3!5#3!5#3!5!3&!!D#)!#!-!;!A!'
'!%,!.#,!-!!&#!:!1!:!%!!%N!)!5!0!.)?!)!>!D!!2>!)!3!5!0#5#0!?!)#>!!+D#!!4!.!5-0%.!+!,!9!#!)!5!0,5#!!D#)#-!!!3!'
'!&-#!%%!/!1!6!4!#!)!+!0!/!#!>!!!-!?!4!?!4!?%)!-!>!D!!%#&!!#(!#>!)!0#?))#>!!,L!S!8!!!4!?!4+.!4#-!#!Y!G!Y!#!!!,!4-!!D##!)#!!3!'
'!+?!.!,!#!)(#!-!,!L!O!a!!+8!>!<!#!!%#!)+!#>!9!-+!!9!O!!--!_!S!#!!/a!O!-!!!-!G!M!#!!,8!S!_!-#%!*!.!6!'
'!+?#!!)+!!0!)!L!G,-!(!@!!%-!)+##!!L!D!S!Y&S#D!L!!)##!(-!_.G!_!L!!)_.!!:!1!7!6!!!#!%!#!'
'!+?#!!)+!!0!)!#*(#!#,!5!1!!%#!)-##!0-%!!#!!!#!!!#))!#!!%%!A!+!!##!%!@!%#!!0!!!#!)+#!3!!!#!))%!'
'!+,!.!/!%!-!)%-!!!%!1!7!?!&!)!!!)!-!%!/!.!,!!%+!5!n!!%-!).##!/#!)0-!!#%!@!7!!%-#0!-#!!0!!!)-3!!!-!)%-!#!)!?!'
'!--!+!0!&!#!%!/!+!6!!%,!;!1!:!+!6!-!!)+!H!/!#!!%)0#!!+#!)0-!!%/!3!+!!-@!#!)-3!#!-!)&-!)!?!'
'!0,!7!,!!,,!!--!.!A!%!!%-!)/#!!()2-!!#&!*!.!,!!.,!;!1!/!#!!!)!##&!1!7!,!6!/#!#%!(!,!-!'
'!;#!&%:,#!,!5!A!%!!&-#)(-!)!-%)!!#-!)(-!)&-#!&#!A!5!,!#!:*&!%!!)-!+!1!:!1!+!,!!(,!+#,!'
'!:##)!,!6#_!o!H!5!B!3#5!0!(!)!+!5!A!&!#!!B%!A!5!+!(%/%&###!!6!;#?!/!%!#!!%-!'
'!.#!%)/!*##!6!;%6!-!!!%#8!:!@!R!3#@!1!/!#!-!,!+!.%0#@!0!.!!&#!%%A&@,0!+!;#,!!&)!/!*!A!@!3#R!E!@!1!!%)!?!)!'
'!/-*!!(!:!!(0!3!R!3%0%/#(!)!#!!+4#.!5#3+B%I!o!;!6#,!!+,!6%+!.!0%J!<!4!)!#!%!&!/!0#/-#!'
'!7,!6#7!/%%!#!4!+!o!5%0!.!+!6%,!#06!7!H!5#o!6!-!!%%!/%?!6!;*2!3#+!,!-&,!6('
'!>,!6%&#/!&!/!1#/&0!.!+!6*;!7!5!/#*!%!!#%!f!*#&!%!#&!()%4!+!7!;!-!'
'!K-!)!?!2!0!/#(!!06!p!5)3!@!1!/!%!'
'!Y)!(!?/(!)!-!'
)
myUI_LOGO_80=(
''
'!9%!!1&!?!,!6!,!6%?!%!'
'!8,!.!,!!/0!,!!!#!!!#!!!)!!#4!)!'
'!9-!!+#!%&?!+!)+,!(!%#'
'!28!Y!8!!*#!%!(!?!+!6!,!-!#%(#1,/!1!0!.!?!(!%!#!'
'!,#!&!?#&!#!-!<!-!!(%!(!@!(!##)#(!?!1!A%@13!@!?!5!(!#!'
'!*#!?!,!#!)%+!1!!*0!#!9!q!;!+!.#?!/#1%.!5!3,B!3!2!o!;!9#!!?!'
'!*)!?!!!-!)%!!0!!*/!.!/!K!%!#!!-7!2!6!7!3&6!?!!%(!K!/!0!(!'
'!)#!/!1!%!-!)!-#%!0!&!#!!%&!.!,!8!Y!#!-!,!6#+!4,!#?!3!5!-!!!+!4!6#-!!!#!8!,!0!&!'
'!%#!&!+!4!##4#&!/!6!,!!!-!+!(!#!1!+!8!^!N!L!!#)!#!)&#&!(?!3!D!!&%!#%!!<!S!#!+!/!'
'!%0!!!))?!)!!)(!<!!!Q!>!!##!)*-!)!!*?!3!D!!&)&#!!!<!>!#!,!1!!,%!/!#!'
'!%0!#!-!)(1!)!#!%%#!!!0!!!8!>!)!!##!)&!/4!P!J!!()&!#O!S!!!q!#!!+-!,!'
'!&,!+!/!(#)!9!Y!8*Y!a!9!8#a!#!!!-#!(8-9!8#!)-!)!-!!#O!!!J!)!8!a!8&#!'
'!*,!-!D!J!A!@/r!O!#!!(8!D!J!V!@,*!<!S!#!!)-!!#L!!!8!D!<!s!@%A!G!Y!'
'!!%*#!D!0!300!D!)!!(D#R!3/D!)!!!8!a*8!!!<!9!e!3()!D!8##!%)'
'!#-!6(-!D!4!,%q!3(,&-!D!-#)!-!#!!!D#3&.!,%3(<!9!D!J!A)*!J!S!)!@!3)@#1!D#6&,!'
'!%-!6!;#6!,!!!_!<#9!D!3(!!9!D!<!_!8!a)#!)!3&1!!#8!3()!&!W!3,A!<!+!5!3(5#.!D!<!6%-!'
'!),%(!!!%!#!>!D!3(!!D!)!@+)!D!)!3/0!)!3&?!-#t!3&?!!!D!3&5!!!#!8!D!-!,!'
'!,?!!%>!?!3&5!#!D!)!5!3#5#3!5#)!D!)!3(5(0!.!-!#!3&?!!#D!3&?!!!D!3&5!!!>!9!!!#!'
'!%#!!(?!!%>!?!5#3!5!0!)!D!9#,)-!8!<!)!3&0!)!-!!!-!!#8!D!-!5!3%@!&#u!3%5!?!!!J!3&@!A!m!K!D!t!+!&!#!'
'!#)!0!)!!%#!/!%!!#>!?!0((!D!)!L!_!L#_#L!_!-!8!9!3!5%0!(!D!<!L&!!D!6!0!5%3!5&0!.!4!#!?!5!0&5#.!D#)!#!.!'
'!%-!!!#!&!?!+!)!4!+!&!O!)!4(-!D!)!!%#!!!#%!!D#.#?#.!)!D!)!!(L!D!8!4+,!8!D#8!-!4)-!D!<!)!!!1!'
'!()!(!)(#!(!<!9!a(<!/!)!!#))-!<!a)<!!%##!%L!D!9)D#L!!#L!D!9)<#&!(!6!)!'
'!()!?!)%#!)!#!1!)!#&(#!!,!0!#!!!#!)(##!,-!!%#*!#/!+!!!#!/##!?!!!)!##)##!0!!!)!#!)#%!'
'!),!?!&!)!%!/!4!,#4!&!(!4!,!!%+!K!#!!!-!)+!*##)+-!!!/!.!!%-#!!?!!!))0!!!)!-!)!-!?!'
'!+-!6!,!!(-#!)-!0!&!!!-#4!)*!%)+-!,!-!!!/!.!,!!*-!6!?!(!-!)!&!4!,#(!%#,!-!'
'!3-!6!+!7!5!3%0!(!6!0!&!#!!%-#!+-!!!-#!&&!1!+!%!1!0#+!6!,!)!(!%!#!!!-!6!,!!(-!'
'!*#!%&(!?!-!6#,!%!&!:!*!5#2!?!%!!!,!6#+!.#?!&!%!&!:#A!@(0!.!+!6#,!!!%#/!1!0!5#0!+!!#/!)!'
'!0-!+!(!&#4!6!2!5!@!0!.!+!4!)!!(-!,#6!2!3#2!;!6!,!(!&#()*!/!4!,!6#4%+!,,'
'!5,%4%+!4#(!0!1!4#,%6!+!4#!!#!r!m!*!:!&##!!%-!,!6#,!'
'!?-!,%!&#!%!&%(!?!1!?#6#,!'
)
myUI_LOGO_80x24=(
'!I#*'
'!8%!1!%!!/%!?!,!-),!(!#!'
'!9,!!,#&+!(!)*%!4!)!#!'
'!28!Y!8!!*%#(!4!+!,#4!)#(!?!/#1+/!1!0!?!1!(!%#'
'!+#!%!4%(!&!#!L!-!!(/!4!.!/!&!(#?&.%0!@03#(!.!)!%!'
'!*)!?!!!)&-!0!!*+!/!J!D!!!-!,%6(-!6!5#2!5!3&2!7!6!,!-!!!D!J!/!4!'
'!)#!/!1!%!)&%!0!&!#!!%&!?!6!8!Y!)!,&4!(&)(!#.!3!5!4!!!+!)!,&-!)!6!/!%!'
'!%%!)!+!4!#!)!4#/!?!,!-!!!-!4##!1!,!Y!^!b!-!!!#!)+-!!)?!3!D!!&)!#%!!<!S!8!6!/!#!'
'!%0!!!))?!)!!)1!,!!!N!O!!##!)&-(!*?!B!D!!&)&#!!!-!>!9!!!.!!,4!.!-!'
'!%,#(!/!)#%!)!q!9#t%9!8!t!8!D#8#!#-#!(8,9!q!9!#!!(-!)##!!!L!O!!!J!)!8)'
'!*,#D#A!@/*!O!#!!(8!D!l!V!@,*!D##!!)-!!#L!!!8!D#l!@%A!G!S!'
'!!,!4!?()!D!.!5&3(5#2#.!D!)!!(D#3&5&3(D!)!8!9!D*9!#!<!K!R!3(/!j!J!9!)!?&4!,!'
'!&)!(%#!L!D!8%D!3(!!8%9!D!8#9!8!9!8!-!<!3&?!!##!3()!J!s!A!3*A!K!)!2!3+0!D#(#)!'
'!),%(!!!%!#!>!D!3(!!D!)!m+)!D!)!3(@#3(5!)!3&.!,#q!3&?!!!D!3&5!-!9!8!D!-!,!'
'!,?!!%>!?!3&0!#!D!)!2!5)2!)!D!)!3(2#7%+#9!8!3&?!!#D!3&?!!!D!3&0!#!O!8!#!%#'
'!#%!/!%!!&4!!%>!?!5!0!5!0#)!D!<!9!8*<#)!3#5!3!0!)!8%9#<!L!8!.!5!3%@#3%5!0!?!!!?!5(3#1!D!9!)!,!/!'
'!%,!!#%#4!+#(!%!O!4!+!4&,!D!)!!###!!#%!!D#.()!D!)!!(_!8!4!+,)!9!D!9!6!+)4!D#)!!!1!'
'!()!?!))(!<!D!9(L!/!(!!!-!))-!L!8)L!!%##!%L!<*J!<!L!!#L!<+J!%!4!,!)!#!'
'!(-!(#)&(!?!(#!!)!(#)!!!-!0!%!!!-!)*##!,#()&!!#!1!4!!!-!4#-!?!!!))0!!!)&(!'
'!*-!6!4!,#!%-!,#!(-!6!/!%!!!-#)+!%#!),-!!!%!/!6!-!!),!(#)!-!)!(#+!)!(!)#(!,!'
'!2#!)!4#+!.!0!@#0!(!7!1!&!#!!%-%!!-!!%-%!!-#!%#!&!1!+!%!?&4#)!(!%#!!,!6!,!-!!&,!-!'
'!*-!,&6!+!(!,#-!&!r!m!A!5#2!4##!-!,#6#.!1!&!:!*!A#@!3#5!2!7#6#,#-!!!#!)!?!0!5#I!7!+!%#/!?!%)'
'!1,&)!(!?!.&?!(#/!(!)&(#/!?!.!6!,!(!&!%!4%,&4#2!5!)!!%-&'
'!?,!6#,!!&#!%&?!.&7!6!,!'
)
# <<< tpot logo data <<<
# <<< tpot ui <<<
