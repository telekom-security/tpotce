#!/usr/bin/env bash

# an exported CDPATH turns a cd into a search that prints the folder it found ($(cd ... && pwd)
# gives two lines): every cd of this script means the folder it names
unset CDPATH

print_help() {
  fuUI_HELP "Installer" "install.sh [-s] [-t <type>] [-u <webuser>] [-p <password> | -P <file>]"$'\n'"           [-B <file>] [-c <compose file>] [-b <branch>] [-r <url>]"$'\n'"           [-n] [-M]" \
    --about "Installs T-Pot on this host: the packages it needs, Docker Engine and the
services of the edition, by the Ansible playbook installer/install/tpot.yml." \
    --about "Without -s, at a terminal, the installer gets what it needs to start and hands
over to the T-Pot installer assistant (tpot install). -s installs without any
question." \
    --opt "-s" "Suppress installation confirmation prompt, unattended run
(requires passwordless sudo or -B, see below)" \
    --opt "-t <type>" "Type of installation (required if -s is used):
  h - hive      (requires -u and -p / -P)
  s - sensor    (no user/pass required)
  l - llm       (requires -u and -p / -P)
  i - mini      (requires -u and -p / -P)
  m - mobile    (no user/pass required)
  t - tarpit    (requires -u and -p / -P)
With -c only h (a HIVE) or s (a SENSOR)." \
    --opt "-u <webuser>" "Web interface username (required for h/l/i/t)" \
    --opt "-p <password>" "Web interface password (required for h/l/i/t)" \
    --opt "-P <file>" "Read the web interface password from a file, - for stdin.
Unlike -p it does not show up in the process list." \
    --opt "-B <file>" "Read the sudo password from a file, so -s also works without
passwordless sudo. The file is only read." \
    --opt "-c <file>" "Install this compose file (i.e. from tpot customize) instead
of an edition." \
    --opt "-b <branch>" "Branch, tag or commit to install from. Default: the branch
of the local clone this script runs from, otherwise master" \
    --opt "-r <url>" "Repository to install from, https URL, the raw URL for the
playbook is derived from it. Default: the origin of the
local clone this script runs from, otherwise
https://github.com/telekom-security/tpotce" \
    --opt "-n" "No assistant, ask in the terminal (as earlier releases did)" \
    --opt "-M" "Progress marks (@@tpot ...) for the assistant, which runs
install.sh -s -M with its answers" \
    --opt "-h" "Show this help message" \
    --example "./install.sh" "At a terminal: the assistant asks everything, then installs" \
    --example "./install.sh -s -t h -u admin -P ~/webpw.txt" "A HIVE without questions, the web password from a file" \
    --example "./install.sh -s -t s -B ~/sudopw.txt" "A SENSOR without questions, sudo needs a password" \
    --example "./install.sh -b dev -r https://github.com/<you>/tpotce" "Test a branch of a fork (see the README, Testing a Branch)" \
    --note "Run it as a regular user with sudo, not as root." \
    --note "The environment variables TPOT_BRANCH and TPOT_REPO_URL work like -b and -r,
the options win." \
    --note "Logs: ~/install_tpot_prepare.log (packages, clone), ~/install_tpot.log
(playbook), ~/install_tpot_pull.log (images)."
  exit 0
}

usage_error() {
  # a wrong option or value: what is wrong and where the help is, exit 1
  fuUI_USAGE_ERROR "$1" "install.sh"
  exit 1
}

mark_phase() {
  # mark_phase <key>: the phase for the assistant (@@tpot phase, installer.Progress), the one a
  # failure names
  myPHASE="$1"
  fuMARK phase "$1"
}

mark_failed() {
  # mark_failed: for the assistant, the phase that failed (@@tpot fail <phase>, as runlog and
  # installer.Progress read it: the assistant marks the step that failed) and that the run failed
  # (@@tpot phase failed)
  fuMARK fail "${myPHASE}"
  fuMARK phase failed
}

install_failed() {
  # install_failed <what failed> <next step> ...: the summary of a run that stops, exit 1
  local myITEM
  mark_failed
  local -a myITEMS=("fail:$1")
  shift
  for myITEM in "$@"; do myITEMS+=("next:${myITEM}"); done
  fuUI_SUMMARY "T-Pot is not installed" "${myITEMS[@]}"
  exit 1
}

install_stopped() {
  # install_stopped <what was stopped> <next step> ...: the summary of a run stopped with
  # Ctrl+C under a spinner (fuUI_SPIN rc 130), exit 130
  local myITEM
  mark_failed
  local -a myITEMS=("warn:$1")
  shift
  for myITEM in "$@"; do myITEMS+=("next:${myITEM}"); done
  fuUI_SUMMARY "The installation was stopped" "${myITEMS[@]}"
  exit 130
}

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
# the same as entries of the xterm 256 palette (theme.PALETTE_256) and as the 16 ANSI colours
# (theme.PALETTE_16), for gum where the rule says 256 or 16 (fuUI_GUM): lipgloss would take the
# nearest itself, and with 16 colours magenta and the yellow of a warning turn bright red
myUI_GUM_256="#E20074=162 #014463=23 #ECEFF9=231 #A2A2AD=248 #3FA34D=71 #F4B400=214 #E8453C=167"
myUI_GUM_16="#E20074=13 #014463=6 #ECEFF9=15 #A2A2AD=7 #3FA34D=10 #F4B400=11 #E8453C=9"
myUI_GUM=""
# the COLORTERM, TERM and depth of the colours for gum, set by fuUI_INIT (fuUI_GUM)
unset myUI_GUM_COLORTERM myUI_GUM_TERM myUI_GUM_DEPTH
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
  # what the script starts (i.e. tpot) knows true colour by COLORTERM: the rule of
  # fuUI_COLORS says it where COLORTERM is empty. gum (lipgloss) gets the depth of the rule
  # by fuUI_GUM, also where it is less than the terminal says (TPOT_COLORS, GNU screen)
  local myDEPTH myLOW
  myDEPTH=$(fuUI_COLORS)
  if [ -z "${COLORTERM:-}" ] && [ "${myDEPTH}" = "truecolor" ]; then export COLORTERM=truecolor; fi
  myUI_GUM_COLORTERM="" myUI_GUM_TERM="${TERM:-}" myUI_GUM_DEPTH="${myDEPTH}"
  fuUI_LOWER myLOW "${TERM:-}"
  case "${myDEPTH}" in
    truecolor)
      fuUI_LOWER myUI_GUM_COLORTERM "${COLORTERM:-}"
      case "${myUI_GUM_COLORTERM}" in
        truecolor|24bit) myUI_GUM_COLORTERM="${COLORTERM}" ;;
        *) myUI_GUM_COLORTERM="truecolor" ;;
      esac ;;
    256) case "${myLOW}" in ""|dumb|unknown|*256color*) ;; *) myUI_GUM_TERM="xterm-256color" ;; esac ;;
    *) case "${myLOW}" in ""|dumb|unknown|xterm|linux) ;; *) myUI_GUM_TERM="xterm" ;; esac ;;
  esac
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

fuUI_GUM () {
  # fuUI_GUM <argument> ...: gum in the colours of the rule (fuUI_COLORS), as fuUI_INIT worked
  # them out: lipgloss takes true colour from any COLORTERM, 256 from a TERM with 256color,
  # else 16; the colours of tpot as the entries of the palette there (myUI_GUM_256 / _16, only
  # the values of the colour options before --). NO_COLOR that is not empty (no-color.org, as
  # Rich and say) as gum takes it: 1. Without fuUI_INIT (myUI_GUM set by hand) gum as it is
  if [ -z "${myUI_GUM_TERM+set}" ]; then "${myUI_GUM}" "$@"; return; fi
  local myARG myPREV="" myTABLE=""
  local -a myARGS=()
  case "${myUI_GUM_DEPTH:-}" in
    256) myTABLE=" ${myUI_GUM_256} " ;;
    16) myTABLE=" ${myUI_GUM_16} " ;;
  esac
  for myARG in "$@"; do
    case "${myPREV}" in
      --*foreground|--*background)
        case "${myTABLE}" in
          *" ${myARG}="*) myARG="${myTABLE#*" ${myARG}="}" myARG="${myARG%% *}" ;;
        esac ;;
      --) myTABLE="" ;;
    esac
    myARGS+=("${myARG}")
    myPREV="${myARG}"
  done
  if [ -n "${NO_COLOR:-}" ];
    then NO_COLOR=1 COLORTERM="${myUI_GUM_COLORTERM}" TERM="${myUI_GUM_TERM}" "${myUI_GUM}" "${myARGS[@]}"
    else COLORTERM="${myUI_GUM_COLORTERM}" TERM="${myUI_GUM_TERM}" "${myUI_GUM}" "${myARGS[@]}"
  fi
}

fuUI_STYLE () {
  # fuUI_STYLE <colour> <text>: one coloured line, plain without gum
  if [ -n "${myUI_GUM}" ];
    then fuUI_GUM style --foreground "$1" -- "$2"
    else echo "$2"
  fi
}

fuUI_PAINT () {
  # a coloured piece of a line, for $(...): gum leaves out colours when it does not
  # write to a terminal itself
  CLICOLOR_FORCE=1 fuUI_GUM style --foreground "$1" -- "$2"
}

fuUI_PREF () {
  # fuUI_PREF <icons|colors>: the choice of the T-Pot Manager in its tpot.json, empty without
  # one. The grammar of tpotctl/prefs.py read_file: one JSON object of strings without escapes
  # and control characters (what the T-Pot Manager writes), any other file does not count; a
  # key twice: the last. The same in every locale: the bytes count (C locale), the file must be
  # UTF-8 (as Python reads it), control characters are C0 and DEL ([:cntrl:] of C), the C1 ones
  # (U+0080 to U+009F) and U+2028 / U+2029. No \001 in a pattern, bash 3.2 cannot have it
  local LC_ALL=C
  local myFILE="${XDG_CONFIG_HOME:-${HOME}/.config}/tpotce/tpot.json" myTEXT="" myOUT=""
  local myW="[ "$'\t\n\r'"]*" myS="\"([^\"\\[:cntrl:]]*)\"" myPAIR myOBJECT myUTF8
  local myT=$'\200'-$'\277'
  [ -f "${myFILE}" ] && [ -r "${myFILE}" ] || return 0
  # a NUL ends the read with rc 0: no JSON
  if IFS= read -r -d '' myTEXT < "${myFILE}"; then return 0; fi
  case "${myTEXT}" in *$'\342\200\250'*|*$'\342\200\251'*) return 0 ;; esac
  # UTF-8 without the C1 controls (\302\200 to \302\237): ASCII, then 2, 3 and 4 bytes
  myUTF8="^([^${myT}"$'\300'-$'\377'"]|"$'\302'"["$'\240'-$'\277'"]|["$'\303'-$'\337'"][${myT}]"
  myUTF8+="|"$'\340'"["$'\240'-$'\277'"][${myT}]|["$'\341'-$'\354\356\357'"][${myT}][${myT}]"
  myUTF8+="|"$'\355'"["$'\200'-$'\237'"][${myT}]|"$'\360'"["$'\220'-$'\277'"][${myT}][${myT}]"
  myUTF8+="|["$'\361'-$'\363'"][${myT}][${myT}][${myT}]|"$'\364'"["$'\200'-$'\217'"][${myT}][${myT}])*\$"
  [[ "${myTEXT}" =~ ${myUTF8} ]] || return 0
  # one way to read the spaces (linear, as prefs.py): after { or a pair, never two in a row
  myPAIR="${myS}${myW}:${myW}${myS}"
  myOBJECT="^${myW}[{]${myW}(${myPAIR}(${myW},${myW}${myPAIR})*${myW})?[}]${myW}\$"
  [[ "${myTEXT}" =~ ${myOBJECT} ]] || return 0
  while [[ "${myTEXT}" =~ ${myPAIR} ]]; do
    [ "${BASH_REMATCH[1]}" != "$1" ] || myOUT="${BASH_REMATCH[2]}"
    myTEXT="${myTEXT#*"${BASH_REMATCH[0]}"}"
  done
  [ -z "${myOUT}" ] || echo "${myOUT}"
  return 0
}

fuUI_ICONS () {
  # the icon set of the T-Pot Manager: TPOT_ICONS, else tpot.json, else unicode
  local myICONS="${TPOT_ICONS:-}"
  case "${myICONS}" in unicode|nerd|ascii) echo "${myICONS}"; return 0 ;; esac
  myICONS=$(fuUI_PREF icons)
  case "${myICONS}" in unicode|nerd|ascii) echo "${myICONS}" ;; *) echo "unicode" ;; esac
}

fuUI_LOWER () {
  # fuUI_LOWER <variable> <text>: the text with A-Z as a-z (nothing else, as prefs.py) into the
  # variable; no ${x,,} (bash 3.2) and no fork. Its own names are long: the variable is the
  # caller's
  local myUI_LC_IN="$2" myUI_LC_OUT="" myUI_LC_C myUI_LC_P myUI_LC_I
  for ((myUI_LC_I = 0; myUI_LC_I < ${#myUI_LC_IN}; myUI_LC_I++)); do
    myUI_LC_C="${myUI_LC_IN:myUI_LC_I:1}"
    myUI_LC_P="ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    myUI_LC_P="${myUI_LC_P%%"${myUI_LC_C}"*}"
    if [ "${#myUI_LC_P}" -lt 26 ]; then
      myUI_LC_C="abcdefghijklmnopqrstuvwxyz"
      myUI_LC_C="${myUI_LC_C:${#myUI_LC_P}:1}"
    fi
    myUI_LC_OUT+="${myUI_LC_C}"
  done
  printf -v "$1" '%s' "${myUI_LC_OUT}"
}

fuUI_COLORS () {
  # truecolor, 256 or 16, by the one rule of the T-Pot Manager too (prefs.py; the cases
  # are tpotctl/tests/color_cases.json): TPOT_COLORS, else "colors" of tpot.json, unless
  # that says auto; then the terminal: 16 for a dumb TERM (dumb, unknown), whatever else it
  # says; outside GNU screen COLORTERM truecolor / 24bit and a TERM of a true colour terminal
  # (*-direct, kitty, ghostty, alacritty, foot, wezterm, contour, rio); outside tmux and
  # screen a terminal that says who it is (TERM_PROGRAM, LC_TERMINAL of iTerm2, which comes
  # over SSH, VTE_VERSION, KONSOLE_VERSION, WT_SESSION); 256 for a TERM *256color or the
  # Terminal of macOS, anything else 16. GNU screen: STY, or a TERM screen* that is no tmux
  # (TMUX, TERM_PROGRAM tmux): screen 4 has no true colour, what the terminal outside says is
  # only inherited there. Bash 3.2 too (the scripts get as far as fuUI_LINUX_ONLY on macOS)
  local myCOLORS="${TPOT_COLORS:-}" myTERM myMUX="" mySCREEN=""
  case "${myCOLORS}" in
    truecolor|256|16) echo "${myCOLORS}"; return 0 ;;
    auto) ;;
    *) myCOLORS=$(fuUI_PREF colors)
       case "${myCOLORS}" in truecolor|256|16) echo "${myCOLORS}"; return 0 ;; esac ;;
  esac
  fuUI_LOWER myTERM "${TERM:-}"
  case "${myTERM}" in dumb|unknown) echo "16"; return 0 ;; esac
  if [ -n "${STY:-}" ]; then mySCREEN=1; fi
  if [ -z "${TMUX:-}" ] && [ "${TERM_PROGRAM:-}" != "tmux" ]; then
    case "${myTERM}" in screen*) mySCREEN=1 ;; esac
  fi
  if [ -z "${mySCREEN}" ]; then
    fuUI_LOWER myCOLORS "${COLORTERM:-}"
    case "${myCOLORS}" in truecolor|24bit) echo "truecolor"; return 0 ;; esac
    case "${myTERM}" in
      *-direct|xterm-kitty|xterm-ghostty|alacritty|foot*|wezterm|contour|rio) echo "truecolor"; return 0 ;;
    esac
  fi
  # in tmux or screen the terminal outside does not count, the multiplexer draws
  if [ -n "${TMUX:-}" ] || [ -n "${mySCREEN}" ]; then myMUX=1; fi
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
  # off, no marks mode, a TERM with colours, no NO_COLOR (not empty), not shown before in this
  # chain of scripts (TPOT_LOGO_SHOWN), not the ascii icon set; sets myUI_COLS / ROWS
  [ -t 1 ] || return 1
  [ "${TPOT_GUM:-on}" = "off" ] && return 1
  fuUI_MARKS_ON && return 1
  local myTERM
  fuUI_LOWER myTERM "${TERM:-}"
  case "${myTERM}" in dumb|unknown) return 1 ;; esac
  [ -n "${NO_COLOR:-}" ] && return 1
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
  # ends are left out, a leading v and a suffix after - or + too, of both (2.24.4-rc1 counts
  # as 2.24.4). rc 1 for an empty one, a part that is not a number, a dot at either end
  local myI myX myY myN myV="${1//[[:space:]]/}" myM="${2//[[:space:]]/}"
  local -a myA=() myB=()
  myV="${myV#v}"
  myV="${myV%%[-+]*}"
  myM="${myM#v}"
  myM="${myM%%[-+]*}"
  [ -n "${myV}" ] && [ -n "${myM}" ] || return 1
  case "${myV}.${myM}" in .*|*.|*..*) return 1 ;; esac
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
  # fuUI_HANG <room> <text>: the text as it is when every line of it fits the room, else
  # each line that does not is broken at spaces (fuUI_FOLD): it keeps its indent, its lines
  # after the first hang two further; the given line breaks stay
  local myROOM="$1" myLINE myOUT myWIDE="" myIND myW
  [ "${myROOM}" -ge 12 ] || { printf '%s\n' "$2"; return 0; }
  while IFS= read -r myLINE; do
    [ "${#myLINE}" -le "${myROOM}" ] || myWIDE=1
  done <<< "$2"
  if [ -z "${myWIDE}" ]; then printf '%s\n' "$2"; return 0; fi
  while IFS= read -r myLINE; do
    if [ "${#myLINE}" -gt "${myROOM}" ];
      then
        myIND="${myLINE%%[! ]*}"
        myW=$((myROOM - ${#myIND} - 2))
        [ "${myW}" -ge 10 ] || myW=10
        # the text without its indent for fuUI_FOLD: a leading space would keep it as it is
        myOUT=$(fuUI_FOLD "${myW}" "${myLINE#"${myIND}"}")
        printf '%s%s\n' "${myIND}" "${myOUT//$'\n'/$'\n'${myIND}  }"
      else printf '%s\n' "${myLINE}"
    fi
  done <<< "$2"
}

fuUI_BANNER () {
  # fuUI_BANNER <title> <line> ...: the T-Pot logo (myUI_LOGO=1, see fuUI_LOGO) or the
  # T-Pot wordmark of the T-Pot Manager (tpotctl/logo.py), then a title and lines. At a
  # terminal a line wider than it is broken, with a hanging indent. A line of more lines:
  # each of them as a line (plain text: ### before each)
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
        myTEXT="${myLINE}"
        [ -z "${myCOLS}" ] || myTEXT=$(fuUI_HANG $((myCOLS - 4)) "${myLINE}")
        echo "### ${myTEXT//$'\n'/$'\n'### }"
      done
      echo
      return
  fi
  echo
  if [ -z "${myLOGO}" ];
    then
      fuUI_GUM style --foreground "${myUI_MAGENTA}" --bold --margin "0 2" -- "${myUI_WORDMARK[@]}"
      echo
  fi
  fuUI_GUM style --foreground "${myUI_GLASS}" --bold --margin "0 2" -- "T-Pot ${myTITLE}"
  # gum style does not break lines; its margin takes two columns on each side
  for myLINE in "$@"; do
    myTEXT="${myLINE}"
    [ -z "${myCOLS}" ] || myTEXT=$(fuUI_HANG $((myCOLS - 4)) "${myLINE}")
    while IFS= read -r myLINE; do myLINES+=("${myLINE}"); done <<< "${myTEXT}"
  done
  [ "${#myLINES[@]}" -eq 0 ] || fuUI_GUM style --foreground "${myUI_ASH}" --margin "0 2" -- "${myLINES[@]}"
  echo
}

fuUI_INFO () {
  # fuUI_INFO <text>: a line of what happens; at a terminal a line wider than it is broken,
  # with a hanging indent. A text of more lines: plain text ### before each, gum the sign
  # before the first and two spaces before the others
  local myTEXT="$*" myCOLS="" myLINE myFIRST=1
  if [ -t 1 ] && fuUI_TERM_SIZE; then myCOLS="${myUI_COLS}"; fi
  if [ -z "${myUI_GUM}" ];
    then
      [ -z "${myCOLS}" ] || myTEXT=$(fuUI_HANG $((myCOLS - 4)) "${myTEXT}")
      echo "### ${myTEXT//$'\n'/$'\n'### }"
      return 0
  fi
  [ -z "${myCOLS}" ] || myTEXT=$(fuUI_HANG $((myCOLS - 2)) "${myTEXT}")
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
    then fuUI_GUM style --foreground "${myUI_WARN_COLOUR}" -- "! $*"
    else echo "### [WARNING] - $*"
  fi
}

fuUI_ERROR () {
  if [ -n "${myUI_GUM}" ];
    then fuUI_GUM style --foreground "${myUI_ERROR_COLOUR}" --bold -- "✗ $*" >&2
    else echo "### [ERROR] - $*" >&2
  fi
}

fuUI_HINT () {
  # commands or details below a message, indented
  local myLINE
  for myLINE in "$@"; do
    if [ -n "${myUI_GUM}" ];
      then fuUI_GUM style --foreground "${myUI_ASH}" -- "    ${myLINE}"
      else echo "###   ${myLINE}"
    fi
  done
}

fuUI_FOLD () {
  # fuUI_FOLD <width> <text>: a paragraph (lines that do not start with a space) with a
  # line wider than width is broken anew at spaces; the others and indented lines stay. The
  # width is characters of UTF-8: awk runs in the C locale (bytes in every awk) and leaves
  # out the continuation bytes when it counts (mawk and the awk of macOS count bytes anyway)
  printf '%s\n' "$2" | LC_ALL=C awk -v w="$1" '
    function width(s) { gsub(/[\200-\277]/, "", s); return length(s) }
    function flush(  i, n, line, used, words) {
      if (np == 0) return
      if (wide <= w) { for (i = 1; i <= np; i++) print part[i] }
      else {
        n = split(joined, words, / +/); line = ""; used = 0
        for (i = 1; i <= n; i++) {
          if (words[i] == "") continue
          if (line == "") { line = words[i]; used = width(words[i]) }
          else if (used + 1 + width(words[i]) <= w) { line = line " " words[i]; used += 1 + width(words[i]) }
          else { print line; line = words[i]; used = width(words[i]) }
        }
        if (line != "") print line
      }
      np = 0; joined = ""; wide = 0
    }
    /^ / || /^$/ { flush(); print; next }
    { part[++np] = $0; joined = (joined == "" ? $0 : joined " " $0); if (width($0) > wide) wide = width($0) }
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
  # colours where gum paints: a terminal that is not dumb (dumb, unknown), no NO_COLOR (one
  # that is not empty, as fuUI_GUM)
  fuUI_LOWER myN "${TERM:-}"
  case "${myN}" in dumb|unknown) myN="dumb" ;; esac
  if [ -n "${myUI_GUM}" ] && [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${myN}" != "dumb" ]; then
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
      fuUI_GUM style --border rounded --border-foreground "${myUI_MAGENTA}" --padding "0 1" \
        --margin "0 2" "${myWIDTH[@]}" -- \
        "$(CLICOLOR_FORCE=1 fuUI_GUM style --foreground "${myUI_GLASS}" --bold -- "${myTITLE}")" \
        "${myLINES[@]}"
    else
      echo "### ${myTITLE}"
      [ "${#myLINES[@]}" -eq 0 ] || printf '%s\n' "${myLINES[@]}"
  fi
  echo
  return "${myRC}"
}

fuUI_CONFIRM () {
  # fuUI_CONFIRM [--default yes|no] <question> [yes] [no]: 0 for yes. The default is the answer
  # enter gives (gum: the button it starts on), yes without --default. Without gum y / n from
  # stdin: with --default (Y/n) or (y/N) and an empty answer takes it, without the question
  # (y/n) waits for one of them
  local myANSWER="" myDEFAULT=""
  if [ "${1:-}" = "--default" ]; then
    case "${2:-}" in yes|no) myDEFAULT="$2" ;; esac
    shift $(( $# < 2 ? $# : 2 ))
  fi
  if [ -n "${myUI_GUM}" ] && [ -t 0 ];
    then
      local -a myFLAGS=()
      [ "${myDEFAULT}" != "no" ] || myFLAGS=(--default=false)
      fuUI_GUM confirm ${myFLAGS[@]+"${myFLAGS[@]}"} --affirmative "${2:-Yes}" --negative "${3:-No}" \
        --prompt.foreground "${myUI_GLASS}" --selected.background "${myUI_MAGENTA}" \
        --selected.foreground "${myUI_GLASS}" --unselected.background "${myUI_PETROL}" \
        --unselected.foreground "${myUI_GLASS}" -- "$1"
      return $?
  fi
  local myPROMPT="(y/n)"
  case "${myDEFAULT}" in yes) myPROMPT="(Y/n)" ;; no) myPROMPT="(y/N)" ;; esac
  while true; do
    read -rp "### $1 ${myPROMPT} " myANSWER || return 1
    [ -n "${myANSWER}" ] || myANSWER="${myDEFAULT:0:1}"
    case "${myANSWER}" in y) return 0 ;; n) return 1 ;; esac
  done
}

fuUI_CHOOSE () {
  # fuUI_CHOOSE [--selected <value>] <header> <label:value> ...: prints the value of the choice;
  # label and value split at the last colon (a label may have one, i.e. a registry with its
  # port), as fuUI_CHOOSE_MANY. --selected: the item of that value is the default (gum starts
  # on it, without gum an empty answer takes it); a value no item has: none. gum gets the labels
  # (its --label-delimiter splits at the first colon), the first item with the label gum gives
  # back is the choice; the default as its label, a comma as \, (gum splits --selected at
  # commas), a label * (gum: the last one) or with \, (no escape for it) starts on the first. rc 1
  # for none, the rc of gum when it is cancelled
  local myI=1 myITEM myPICK myOUT myDEFAULT="" myVALUE=""
  if [ "${1:-}" = "--selected" ]; then
    myVALUE="${2-}" myDEFAULT=0
    shift $(( $# < 2 ? $# : 2 ))
  fi
  local myHEADER="${1:-}"
  [ "$#" -eq 0 ] || shift
  if [ -n "${myDEFAULT}" ]; then
    myDEFAULT=""
    for myITEM in "$@"; do
      if [ "${myITEM##*:}" = "${myVALUE}" ]; then myDEFAULT="${myI}"; break; fi
      myI=$((myI + 1))
    done
    myI=1
  fi
  if [ -n "${myUI_GUM}" ] && [ -t 0 ];
    then
      local -a myLABELS=() myFLAGS=()
      for myITEM in "$@"; do myLABELS+=("${myITEM%:*}"); done
      if [ -n "${myDEFAULT}" ]; then
        myITEM="${myLABELS[myDEFAULT - 1]}"
        [ "${myITEM}" = "*" ] || [[ "${myITEM}" == *'\,'* ]] || myFLAGS=(--selected "${myITEM//,/\\,}")
      fi
      myOUT=$(fuUI_GUM choose ${myFLAGS[@]+"${myFLAGS[@]}"} --header "${myHEADER}" \
        --header.foreground "${myUI_GLASS}" --cursor.foreground "${myUI_MAGENTA}" \
        --item.foreground "${myUI_ASH}" --selected.foreground "${myUI_MAGENTA}" -- "${myLABELS[@]}") || return $?
      for myITEM in "$@"; do
        if [ "${myITEM%:*}" = "${myOUT}" ]; then echo "${myITEM##*:}"; return 0; fi
      done
      return 1
  fi
  echo "### ${myHEADER}" >&2
  for myITEM in "$@"; do
    echo "###   ${myI}) ${myITEM%:*}" >&2
    myI=$((myI + 1))
  done
  while true; do
    if [ -n "${myDEFAULT}" ];
      then read -rp "### Choice (1-$#, enter = ${myDEFAULT}): " myPICK || return 1
      else read -rp "### Choice (1-$#): " myPICK || return 1
    fi
    myPICK="${myPICK//[[:space:]]/}"
    [ -n "${myPICK}" ] || myPICK="${myDEFAULT}"
    [[ "${myPICK}" =~ ^[0-9]{1,9}$ ]] && myPICK=$((10#${myPICK})) || myPICK=0
    if [ "${myPICK}" -ge 1 ] && [ "${myPICK}" -le "$#" ];
      then
        myITEM="${!myPICK}"
        echo "${myITEM##*:}"
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
        myOUT=$(fuUI_GUM filter "${myARGS[@]}" --indicator.foreground "${myUI_MAGENTA}" \
          --match.foreground "${myUI_MAGENTA}" --selected-indicator.foreground "${myUI_MAGENTA}" \
          --prompt.foreground "${myUI_MAGENTA}" -- "${myLABELS[@]}") || return $?
      else
        myOUT=$(fuUI_GUM choose "${myARGS[@]}" --cursor.foreground "${myUI_MAGENTA}" \
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
      if [ "${2:-}" = "password" ];
        then fuUI_GUM input --password --header "$1" --header.foreground "${myUI_GLASS}" \
               --cursor.foreground "${myUI_MAGENTA}" --prompt "› " --prompt.foreground "${myUI_MAGENTA}" --placeholder ""
        else fuUI_GUM input --header "$1" --header.foreground "${myUI_GLASS}" \
               --cursor.foreground "${myUI_MAGENTA}" --prompt "› " --prompt.foreground "${myUI_MAGENTA}" --placeholder ""
      fi
      return $?
  fi
  if [ "${2:-}" = "password" ];
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
      fuUI_GUM spin --spinner dot --spinner.foreground "${myUI_MAGENTA}" --title "${myTITLE}" \
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

validate_type() {
  [[ "$myTPOT_TYPE" =~ ^[hslimtHSLIMT]$ ]] || usage_error "Invalid installation type: $myTPOT_TYPE"
}

git_source() {
  # Reads $1 ("branch" or "repo") from the clone this script runs from. `$0` is
  # a path only when the script runs as a file - piped through
  # `bash -c "$(curl ...)"` it is not, and a clone in the current directory has
  # nothing to do with the script that is running, so it must not be used.
  command -v git >/dev/null || return
  [ -f "$0" ] || return
  myDIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
  git -C "${myDIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return
  case "$1" in
    branch)
      myREF=$(git -C "${myDIR}" rev-parse --abbrev-ref HEAD 2>/dev/null)
      # a detached HEAD has no branch name, the commit works just as well
      [ "${myREF}" = "HEAD" ] && myREF=$(git -C "${myDIR}" rev-parse HEAD 2>/dev/null)
      echo "${myREF}"
      ;;
    repo)
      git -C "${myDIR}" remote get-url origin 2>/dev/null
      ;;
  esac
}

install_version() {
  # The version for the credits of the T-Pot logo: the file version of the clone this
  # script runs from (fuUI_VERSION, the one source), else the branch when it looks
  # like a tag (X.Y.Z or vX.Y.Z), else none. Not ~/tpotce: an earlier clone there
  # says nothing about what this installer installs.
  local myDIR myV=""
  if [ -f "$0" ] && myDIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd) && [ -r "${myDIR}/version" ];
    then
      myV=$(myUI_VERSION="" fuUI_VERSION "${myDIR}")
  fi
  if [ -z "${myV}" ] && [[ "${myTPOT_BRANCH}" =~ ^v?([0-9]+(\.[0-9]+)+)$ ]];
    then
      myV="${BASH_REMATCH[1]}"
  fi
  echo "${myV}"
}

normalize_repo() {
  # compare repository URLs without a trailing slash or `.git`
  myURL="${1%/}"
  echo "${myURL%.git}"
}

resolve_tpot_source() {
  # -b and -r win, then the environment, then the local clone, then master
  [ -z "${myTPOT_BRANCH}" ] && myTPOT_BRANCH=$(git_source branch)
  [ -z "${myTPOT_BRANCH}" ] && myTPOT_BRANCH="master"
  [ -z "${myTPOT_REPO_URL}" ] && myTPOT_REPO_URL=$(git_source repo)
  [ -z "${myTPOT_REPO_URL}" ] && myTPOT_REPO_URL="https://github.com/telekom-security/tpotce"
  myTPOT_REPO_URL=$(normalize_repo "${myTPOT_REPO_URL}")
}

clone_matches() {
  # Is ~/tpotce at what was requested? A branch by name, a tag or a commit by the
  # commit it points to (those leave a detached HEAD without a name).
  myCLONE_BRANCH=$(git -C "${HOME}/tpotce" rev-parse --abbrev-ref HEAD 2>/dev/null)
  [ "${myCLONE_BRANCH}" = "${myTPOT_BRANCH}" ] && return 0
  myHEAD=$(git -C "${HOME}/tpotce" rev-parse HEAD 2>/dev/null)
  myWANT=$(git -C "${HOME}/tpotce" rev-parse -q --verify "${myTPOT_BRANCH}^{commit}" 2>/dev/null)
  [ -n "${myWANT}" ] && [ "${myWANT}" = "${myHEAD}" ]
}

check_tpot_clone() {
  # `update: no` in the playbook means Ansible keeps an existing ~/tpotce as it
  # is, without looking at the requested repository or branch - a test would
  # silently run against the previous checkout.
  [ -d "${HOME}/tpotce" ] || return
  if ! git -C "${HOME}/tpotce" rev-parse --is-inside-work-tree >/dev/null 2>&1;
    then
      fuUI_WARN "${HOME}/tpotce exists but is not a git repository, its origin cannot be verified."
      echo
      return
  fi
  myCLONE_REPO=$(normalize_repo "$(git -C "${HOME}/tpotce" remote get-url origin 2>/dev/null)")
  if ! clone_matches || [ "${myCLONE_REPO}" != "${myTPOT_REPO_URL}" ];
    then
      myCLONE_BRANCH=$(git -C "${HOME}/tpotce" rev-parse --abbrev-ref HEAD 2>/dev/null)
      [ "${myCLONE_BRANCH}" = "HEAD" ] && myCLONE_BRANCH=$(git -C "${HOME}/tpotce" rev-parse HEAD 2>/dev/null)
      fuUI_ERROR "${HOME}/tpotce already exists and does not match what was requested:"
      fuUI_HINT "found:     ${myCLONE_REPO} at ${myCLONE_BRANCH}" \
                "requested: ${myTPOT_REPO_URL} at ${myTPOT_BRANCH}"
      fuUI_INFO "T-Pot would be installed from the existing checkout. Remove it and run"
      fuUI_INFO "the installer again, or clone what you want to test into ${HOME}/tpotce:"
      fuUI_HINT "sudo rm -rf ${HOME}/tpotce"
      install_failed "${HOME}/tpotce is not what was requested" \
        "Remove it or clone the requested source into it, then run the installer again"
  fi
}

clone_tpot() {
  # The assistant needs the repository before the playbook, which would clone it
  # at its end. It keeps this clone (update: no), check_tpot_clone made sure that
  # an existing one is the requested one.
  [ -d "${HOME}/tpotce/.git" ] && return 0
  git clone -q "${myTPOT_REPO_URL}" "${HOME}/tpotce" \
    && { [ "${myTPOT_BRANCH}" = "master" ] || git -C "${HOME}/tpotce" checkout -q "${myTPOT_BRANCH}"; }
}

sudo_password_required() {
  # `-k` ignores a cached credential: installing the packages refreshes the sudo
  # timestamp, so a plain `sudo -n true` would succeed on a password protected
  # system and Ansible would then fail once the timestamp expires in the middle
  # of the playbook.
  ! command sudo -n -k true > /dev/null 2>&1
}

sudo_rs_become_exe() {
  # Ubuntu 26.04 makes sudo-rs the active `sudo`. It wraps the prompt it is
  # given with `-p` into "[sudo: <prompt>] Password:", while Ansible waits for a
  # line that starts with its own prompt and gives up with "Timed out waiting
  # for become success or become password prompt". The fix landed in
  # ansible-core devel only, so point Ansible at the traditional sudo, which
  # Ubuntu still ships next to sudo-rs. Only needed where a become password is
  # entered, passwordless sudo never shows a prompt.
  if [ -n "${myANSIBLE_BECOME_EXE}" ];
    then
      return
  fi
  command sudo --version 2>&1 | grep -qi "sudo-rs" || return
  for myEXE in /usr/bin/sudo.ws $(update-alternatives --list sudo 2>/dev/null | grep -v -- '-rs$'); do
    if [ -x "${myEXE}" ];
      then
        myANSIBLE_BECOME_EXE="-e ansible_become_exe=${myEXE}"
        fuUI_INFO "‘sudo‘ is sudo-rs, whose password prompt Ansible cannot read."
        fuUI_INFO "Setting the Ansible become executable to ${myEXE}."
        echo
        return
    fi
  done
  fuUI_ERROR "‘sudo‘ is sudo-rs and no traditional sudo was found next to it."
  fuUI_INFO "Ansible cannot read the sudo-rs password prompt, so either install the"
  fuUI_INFO "traditional sudo or configure passwordless sudo for ${myUSER}:"
  fuUI_HINT "sudo apt install sudo" \
            "echo '${myUSER} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/${myUSER}"
  install_failed "Ansible cannot use sudo-rs" \
    "Install the traditional sudo or configure passwordless sudo, then run the installer again"
}

abort_unattended() {
  fuUI_ERROR "‘sudo‘ requires a password, so -s cannot be honoured."
  fuUI_INFO "Either hand the password over with -B <file>, configure passwordless sudo"
  fuUI_INFO "for ${myUSER}, e.g."
  fuUI_HINT "echo '${myUSER} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/${myUSER}"
  fuUI_INFO "or run the installer without -s and enter the password when asked."
  install_failed "-s needs sudo without a password prompt" \
    "Run the installer again with -B <file>, with passwordless sudo or without -s"
}

check_port_conflicts() {
  myPORT_CONFLICT=""
  for myENTRY in ${myCONFLICT_PORTS}; do
    myPROTO="${myENTRY%%/*}"
    myPORT="${myENTRY##*/}"
    # a socket is listed without root, only the process behind it is not
    myLINE=$(ss -H -ln --"${myPROTO}" "sport = :${myPORT}" 2>/dev/null | head -n 1)
    [ -z "${myLINE}" ] && continue
    # naming the process needs root, and on the Debian branch below `sudo` may
    # not be installed yet - the occupied port is reported either way
    myPROC=""
    if command -v sudo >/dev/null;
      then
        myPROC=$(sudo -n ss -H -lnp --"${myPROTO}" "sport = :${myPORT}" 2>/dev/null \
                 | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' \
                 | head -n 1)
    fi
    # The playbook turns the resolved stub listener off, so it is not a blocker.
    # `ss` truncates process names to 15 characters, hence systemd-resolve.
    if [ "${myPROC}" = "systemd-resolve" ];
      then
        continue
    fi
    fuUI_ERROR "${myPROTO}/${myPORT} is occupied by ${myPROC:-an unidentified process}"
    myPORT_CONFLICT="y"
  done
}

rhel_version() {
  # special case for RHEL due to its complicated repo infrastructure
  # primarily used for EPEL repo selection
  # T-Pot follows the current release, which is RHEL 10
  myRHEL_VERSION=$(grep PLATFORM_ID /etc/os-release | cut -d ':' -f2 | grep -Eo '([0-9]{1,2})')
  if [ "$myRHEL_VERSION" -lt 10 ]; then
    echo "Error: RHEL < 10 not supported!" >&2
    exit 1
  fi
  echo "$myRHEL_VERSION"
}

rhel_ansible_repo() {
  # rhel uses a dedicated repo for ansible that we need to enable through subscription-manager
  myRHEL_ANSIBLE_REPO=$(sudo subscription-manager repos --list \
    | grep -E "ansible-automation-platform-[0-9]{1}\.[0-9]{1}-for-rhel-$(rhel_version)-$(arch)-rpms" \
    | awk -F':' '{print $2}' \
    | tr -d ' ' \
    | sort -nr \
    | head -n 1
)
  echo "$myRHEL_ANSIBLE_REPO"
}

install_packages() {
  # the packages the installer needs, per distribution; runs in the background of
  # fuUI_SPIN, so sudo has to be refreshed before (it cannot prompt here)
  case ${myCURRENT_DISTRIBUTION} in
    "Fedora Linux")
      sudo dnf -y --refresh install ${myPACKAGES_FEDORA}
      ;;
    "Debian GNU/Linux"|"Raspbian GNU/Linux"|"Ubuntu")
      sudo apt update && sudo NEEDRESTART_SUSPEND=1 apt install -y ${myPACKAGES_DEBIAN}
      ;;
    "openSUSE Tumbleweed")
      sudo zypper refresh && sudo zypper install -y ${myPACKAGES_OPENSUSE} \
        && echo "export ANSIBLE_PYTHON_INTERPRETER=/bin/python3" | sudo tee /etc/profile.d/ansible.sh >/dev/null
      ;;
    "AlmaLinux"|"Rocky Linux")
      sudo dnf -y --refresh install ${myPACKAGES_ROCKY} && ansible-galaxy collection install ansible.posix
      ;;
    "Red Hat Enterprise Linux")
      echo "RHEL detected - configuring version and Ansible repo strings"
      rhel_version && rhel_ansible_repo || return 1
      sudo yum -y update || return 1
      # extra repo required for EPEL on RHEL
      sudo subscription-manager repos --enable codeready-builder-for-rhel-"$myRHEL_VERSION"-$(arch)-rpms || return 1
      # epel installer is not standard on RHEL
      sudo dnf -y install https://dl.fedoraproject.org/pub/epel/epel-release-latest-"$myRHEL_VERSION".noarch.rpm || return 1
      # ansible comes from rhel subscription manager
      sudo subscription-manager repos --enable "$(rhel_ansible_repo)" || return 1
      sudo dnf -y --refresh install ${myPACKAGES_RHEL} && ansible-galaxy collection install ansible.posix
      ;;
  esac
}

install_sudo_debian() {
  # Debian without sudo: install it with the root password, add the user. Not in
  # the background, su asks for the password in the terminal.
  fuUI_WARN "‘sudo‘ is not installed. To continue you need to provide the ‘root‘ password"
  fuUI_INFO "or press CTRL-C to manually install ‘sudo‘ and add your user to the sudoers."
  echo
  # Ansible cannot be handed a become password by -s, so an unattended
  # run needs a passwordless rule for the user we are about to add.
  if [ "${myUNATTENDED}" = "y" ] && [ -z "${myBECOME_FILE}" ];
    then
      mySUDOERS_RULE="${myUSER} ALL=(ALL) NOPASSWD:ALL"
      fuUI_INFO "‘-s‘ was given, so ${myUSER} will get passwordless sudo."
      fuUI_INFO "Remove /etc/sudoers.d/${myUSER} after the installation to undo it."
      echo
    else
      mySUDOERS_RULE="${myUSER} ALL=(ALL:ALL) ALL"
  fi
  su -c "apt -y update && \
         NEEDRESTART_SUSPEND=1 apt -y install sudo ${myPACKAGES_DEBIAN} && \
         /usr/sbin/usermod -aG sudo ${myUSER} && \
         echo '${mySUDOERS_RULE}' | tee /etc/sudoers.d/${myUSER} >/dev/null && \
         chmod 440 /etc/sudoers.d/${myUSER}" || install_failed "sudo could not be installed" \
           "Install sudo, add ${myUSER} to the sudoers, then run the installer again"
  fuUI_INFO "We need sudo for Ansible, please enter the sudo password ..."
  sudo true && fuUI_OK "sudo works. Note that Ansible needs it without a password prompt, see below."
  echo
}

get_packages() {
  echo
  mark_phase packages
  if [ -n "${TPOT_INSTALL_PACKAGES_DONE}" ];
    then
      # the assistant comes after the bootstrap, which installed them already
      fuUI_OK "The packages the installer needs are there."
      return
  fi
  if [[ "${myCURRENT_DISTRIBUTION}" =~ ^(Debian\ GNU/Linux|Raspbian\ GNU/Linux|Ubuntu)$ ]] && ! command -v sudo >/dev/null;
    then
      install_sudo_debian
      return
  fi
  # a password is asked for now, in the terminal, the spinner cannot ask
  if [ -z "${myBECOME_FILE}" ] && sudo_password_required;
    then
      fuUI_INFO "sudo needs your password to install the packages:"
      sudo -v || install_failed "sudo did not accept the password" "Run the installer again"
  fi
  local myRC=0
  fuUI_SPIN "${myINSTALL_NOTIFICATION}" "${myLOG}" install_packages || myRC=$?
  [ "${myRC}" -ne 130 ] || install_stopped "Stopped while installing the packages the installer needs" \
    "Run the installer again"
  [ "${myRC}" -eq 0 ] || install_failed "The packages the installer needs could not be installed" \
    "Review ${myLOG}, then run the installer again"
  if [ "${myCURRENT_DISTRIBUTION}" = "openSUSE Tumbleweed" ];
    then
      source /etc/profile.d/ansible.sh
  fi
  echo
}

check_ports () {
  # Abort before anything is installed if a service holds a port a honeypot needs.
  # The warning at the end of this script comes too late to act on, and an
  # unattended run cannot act on it at all.
  mark_phase checks
  if ! command -v ss >/dev/null;
    then
      fuUI_ERROR "‘ss‘ was not found, so the check for conflicting services cannot run."
      fuUI_INFO "Install it and run the installer again:"
      fuUI_HINT "Debian, Raspbian, Ubuntu:       sudo apt install iproute2" \
                "AlmaLinux, Fedora, RHEL, Rocky: sudo dnf install iproute" \
                "openSUSE Tumbleweed:            sudo zypper install iproute2"
      install_failed "‘ss‘ is missing" "Install it (see above), then run the installer again"
  fi
  fuUI_INFO "Now checking for services on ports T-Pot needs ..."
  check_port_conflicts
  if [ "${myPORT_CONFLICT}" = "y" ];
    then
      fuUI_INFO "T-Pot publishes these ports for its honeypots, so a clean installation"
      fuUI_INFO "is required. Identify and disable the services, then run the installer"
      fuUI_INFO "again:"
      fuUI_HINT "sudo ss -lntup" \
                "sudo systemctl list-sockets    # for a process that reads ‘systemd‘" \
                "sudo systemctl disable --now <unit>"
      install_failed "Services hold ports T-Pot needs" "Disable them (see above), then run the installer again"
    else
      fuUI_OK "No services found on ports T-Pot needs."
      echo
  fi
}

read_password_file() {
  # -P <file>: the first line, - reads stdin
  if [ "$1" = "-" ];
    then IFS= read -r myWEB_PW
    else IFS= read -r myWEB_PW < "$1" || [ -n "${myWEB_PW}" ] || usage_error "Cannot read the password from $1."
  fi
}

password_weakness() {
  # empty when the password is fine; cracklib if it is there (not before the
  # packages), else at least 12 characters, as tpot users does
  local myCHECK
  for myCHECK in /usr/sbin/cracklib-check /usr/bin/cracklib-check "$(command -v cracklib-check 2>/dev/null)"; do
    if [ -n "${myCHECK}" ] && [ -x "${myCHECK}" ];
      then
        printf "%s" "$1" | "${myCHECK}" | grep -q ": OK$" || printf "%s" "$1" | "${myCHECK}" | sed 's/^.*: //'
        return
    fi
  done
  [ "${#1}" -ge 12 ] || echo "shorter than 12 characters"
}

ask_type() {
  # the classic questions, before anything is installed
  [ -n "${myTPOT_TYPE}" ] && return
  myTPOT_TYPE=$(fuUI_CHOOSE "Choose your T-Pot type:" \
    "Hive - T-Pot Standard / HIVE, everything a distributed setup needs:h" \
    "Sensor - honeypots only, sends to a HIVE (no web UI, no Elastic Stack):s" \
    "LLM - LLM based honeypots Beelzebub and Galah, needs Ollama or ChatGPT:l" \
    "Mini - 30+ honeypots with just a couple of honeypot daemons:i" \
    "Mobile - everything to run T-Pot Mobile (available separately):m" \
    "Tarpit - endless data for attackers, bots and scanners, with ddospot:t") || exit 1
  validate_type
}

ask_web_user() {
  [[ "${myTPOT_TYPE}" =~ ^[hlit]$ ]] || return
  echo
  fuUI_INFO "T-Pot User Configuration ..."
  while [ -z "${myWEB_USER}" ]; do
    myWEB_USER=$(fuUI_INPUT "Enter your web user name:")
    myWEB_USER=$(echo "${myWEB_USER}" | tr -cd "[:alnum:]_.-")
    [ -n "${myWEB_USER}" ] && ! fuUI_CONFIRM "Your username is ${myWEB_USER}, is this correct?" && myWEB_USER=""
  done
  while [ -z "${myWEB_PW}" ]; do
    myWEB_PW=$(fuUI_INPUT "Enter password for your web user:" password)
    [ -z "${myWEB_PW}" ] && continue
    myWEB_PW2=$(fuUI_INPUT "Repeat password for your web user:" password)
    if [ "${myWEB_PW}" != "${myWEB_PW2}" ];
      then
        fuUI_WARN "Passwords do not match."
        myWEB_PW=""
        continue
    fi
    myWEAK=$(password_weakness "${myWEB_PW}")
    if [ -n "${myWEAK}" ] && ! fuUI_CONFIRM "The password is weak (${myWEAK}). Keep it anyway?";
      then
        myWEB_PW=""
    fi
  done
}

# Defaults
myQST=""
myUNATTENDED=""
myTPOT_TYPE=""
myWEB_USER=""
# the web password is no variable of the environment: only htpasswd gets it, on stdin (one from the
# environment this script was started with is not taken, nor handed on to every child)
unset myWEB_PW myWEB_PW2
myWEB_PW=""
myWEB_PW_FILE=""
myBECOME_FILE=""
myCUSTOM_COMPOSE=""
myCLASSIC=""
myMARKS=""
# the phase the assistant is told about (mark_phase), installer.Progress starts with the checks
myPHASE="checks"
# Ansible become executable, empty means the Ansible default. See
# sudo_rs_become_exe.
myANSIBLE_BECOME_EXE=""
# Where to install T-Pot from. Empty means: work it out in resolve_tpot_source.
myTPOT_BRANCH="${TPOT_BRANCH}"
myTPOT_REPO_URL="${TPOT_REPO_URL}"

while getopts ":sb:r:t:u:p:P:B:c:nMh" opt; do
  case "$opt" in
    s)
      myQST="y"
      myUNATTENDED="y"
      ;;
    b)
      myTPOT_BRANCH="${OPTARG}"
      ;;
    r)
      myTPOT_REPO_URL="${OPTARG}"
      ;;
    t)
      # lower case the bash 3.2 way: install.sh runs from curl with any bash up to the distribution check
      myTPOT_TYPE=$(printf '%s' "${OPTARG}" | tr '[:upper:]' '[:lower:]')
      validate_type
      ;;
    u)
      export myWEB_USER="${OPTARG}"
      ;;
    p)
      myWEB_PW="${OPTARG}"
      ;;
    P)
      myWEB_PW_FILE="${OPTARG}"
      ;;
    B)
      myBECOME_FILE="${OPTARG}"
      ;;
    c)
      myCUSTOM_COMPOSE="${OPTARG}"
      ;;
    n)
      myCLASSIC="y"
      ;;
    M)
      # for the assistant: marks on stdout, no terminal behind them
      myMARKS="y"
      ;;
    h)
      print_help
      ;;
    \?)
      usage_error "Unknown option -${OPTARG}."
      ;;
    :)
      usage_error "Option -${OPTARG} requires an argument."
      ;;
  esac
done

[ -n "${myWEB_PW_FILE}" ] && read_password_file "${myWEB_PW_FILE}"

# -s requires -t
if [[ "$myUNATTENDED" == "y" && -z "$myTPOT_TYPE" ]]; then
  usage_error "-t is required when using -s to suppress interaction."
fi

# Determine if user/pass are required based on install type
if [[ "$myUNATTENDED" == "y" && "$myTPOT_TYPE" =~ ^[hlit]$ ]]; then
  [[ -n "$myWEB_USER" && -n "$myWEB_PW" ]] || usage_error "-u and -p (or -P) are required for installation type '$myTPOT_TYPE'."
fi

if [ -n "${myCUSTOM_COMPOSE}" ];
  then
    [[ "${myTPOT_TYPE}" =~ ^[hs]$ ]] || [ -z "${myTPOT_TYPE}" ] || usage_error "With -c the type is h (a HIVE) or s (a SENSOR)."
    [ -f "${myCUSTOM_COMPOSE}" ] || usage_error "${myCUSTOM_COMPOSE} does not exist."
    myCUSTOM_COMPOSE=$(cd "$(dirname "${myCUSTOM_COMPOSE}")" && pwd)/$(basename "${myCUSTOM_COMPOSE}")
fi

if [ -n "${myBECOME_FILE}" ];
  then
    [ -r "${myBECOME_FILE}" ] || usage_error "Cannot read the sudo password from ${myBECOME_FILE}."
    myBECOME_FILE=$(cd "$(dirname "${myBECOME_FILE}")" && pwd)/$(basename "${myBECOME_FILE}")
    # every sudo of this script refreshes the timestamp from the file first, so none
    # of them prompts and pipes into sudo (tee) keep working
    sudo () { command sudo -S -p "" -v < "${myBECOME_FILE}" >/dev/null 2>&1; command sudo "$@"; }
fi

resolve_tpot_source

myINSTALL_NOTIFICATION="Installing the packages the installer needs ..."
myUSER=$(whoami)
myLOG="${HOME}/install_tpot_prepare.log"
myPULL_LOG="${HOME}/install_tpot_pull.log"
myTPOT_CONF_FILE="${HOME}/tpotce/.env"
# git and the Python venv module: the assistant (tpot install) runs before the
# playbook, from a clone the installer makes itself
myPACKAGES_DEBIAN="ansible apache2-utils cracklib-runtime git python3-venv wget"
myPACKAGES_FEDORA="ansible cracklib git httpd-tools python3 wget"
myPACKAGES_ROCKY="ansible-core epel-release cracklib git httpd-tools python3 wget"
myPACKAGES_RHEL="ansible-core ansible-collection-redhat-rhel_mgmt cracklib git httpd-tools python3 wget"
myPACKAGES_OPENSUSE="ansible apache2-utils cracklib git python3 wget"
# Ports a honeypot needs that a distribution service is likely to hold. A
# service on 127.0.0.1 conflicts with a container publishing the same port on
# 0.0.0.0, so a loopback listener counts as well.
myCONFLICT_PORTS="tcp/25 tcp/53 udp/53"

fuUI_INIT

# Check if running with root privileges
if [ ${EUID} -eq 0 ];
  then
    fuUI_ERROR "This script should not be run as root. Please run it as a regular user."
    echo
    mark_failed
    exit 1
fi

# Check if running on a supported distribution
mySUPPORTED_DISTRIBUTIONS=("AlmaLinux" "Debian GNU/Linux" "Fedora Linux" "openSUSE Tumbleweed" "Raspbian GNU/Linux" "Red Hat Enterprise Linux" "Rocky Linux" "Ubuntu")
# no /etc/os-release (macOS): no distribution, the message below says which ones T-Pot runs on
myCURRENT_DISTRIBUTION=$(awk -F= '/^NAME/{print $2}' /etc/os-release 2>/dev/null | tr -d '"')

if [[ ! " ${mySUPPORTED_DISTRIBUTIONS[@]} " =~ " ${myCURRENT_DISTRIBUTION} " ]];
  then
    # the list in words: "a, b and c", from the array, so the message names every one of them
    myLIST=$(printf '%s, ' "${mySUPPORTED_DISTRIBUTIONS[@]:0:${#mySUPPORTED_DISTRIBUTIONS[@]}-1}")
    fuUI_ERROR "Only the following distributions are supported: ${myLIST%, } and ${mySUPPORTED_DISTRIBUTIONS[${#mySUPPORTED_DISTRIBUTIONS[@]}-1]}."
    fuUI_INFO "Please follow the T-Pot documentation on how to run T-Pot on macOS, Windows and other currently unsupported platforms."
    echo
    mark_failed
    exit 1
fi

# Check if running on a supported distribution version. T-Pot follows the
# current release of each distribution, the packages and repositories it uses
# are only available there. openSUSE Tumbleweed rolls and is not pinned.
myVERSION_ID=$(awk -F= '/^VERSION_ID/{print $2}' /etc/os-release 2>/dev/null | tr -d '"')
case ${myCURRENT_DISTRIBUTION} in
  "AlmaLinux"|"Red Hat Enterprise Linux"|"Rocky Linux")
    mySUPPORTED_VERSION="10"
    myCURRENT_VERSION="${myVERSION_ID%%.*}"
    ;;
  "Fedora Linux")
    mySUPPORTED_VERSION="44"
    myCURRENT_VERSION="${myVERSION_ID%%.*}"
    ;;
  "Debian GNU/Linux"|"Raspbian GNU/Linux")
    mySUPPORTED_VERSION="13"
    myCURRENT_VERSION="${myVERSION_ID%%.*}"
    ;;
  "Ubuntu")
    # Ubuntu releases twice a year, its version is major and minor
    mySUPPORTED_VERSION="26.04"
    myCURRENT_VERSION="${myVERSION_ID}"
    ;;
  *)
    mySUPPORTED_VERSION=""
    ;;
esac

if [ -n "${mySUPPORTED_VERSION}" ] && [ "${myCURRENT_VERSION}" != "${mySUPPORTED_VERSION}" ];
  then
    fuUI_ERROR "T-Pot supports ${myCURRENT_DISTRIBUTION} ${mySUPPORTED_VERSION}, this system runs ${myCURRENT_VERSION}."
    fuUI_INFO "Please install T-Pot on the current release of your distribution."
    echo
    mark_failed
    exit 1
fi

# Begin of Installer: the T-Pot logo above the banner, at a terminal only (fuUI_LOGO_ON,
# never with -M). Without a version its credits name none, fuUI_VERSION would read ~/tpotce
myUI_LOGO=1
myUI_VERSION=$(install_version)
[ -n "${myUI_VERSION}" ] || myUI_CHECKOUT="/dev/null"
[ -z "${myMARKS}" ] && fuUI_BANNER "Installer" "This script will now install T-Pot and all of its dependencies." \
  "Source: ${myTPOT_REPO_URL} at ${myTPOT_BRANCH}" "${myCURRENT_DISTRIBUTION} ${myVERSION_ID}"

# A person at a terminal gets the assistant: the installer only gets what it needs
# to start (packages, the repository, the Python packages of tpot) and hands over.
# tpot install asks everything up front and runs this script again with -s.
if [ -z "${myUNATTENDED}" ] && [ -z "${myCLASSIC}" ] && [ -t 0 ] && [ -t 1 ] && [ "${TPOT_ASSISTANT:-on}" != "off" ];
  then
    fuUI_INFO "It first installs git, Ansible and the Python packages it needs."
    if ! fuUI_CONFIRM "Start the T-Pot installer?" "Start" "Abort";
      then
        echo
        fuUI_INFO "Aborting!"
        echo
        exit 0
    fi
    check_tpot_clone
    check_ports
    get_packages
    myRC=0
    fuUI_SPIN "Getting T-Pot from ${myTPOT_REPO_URL} at ${myTPOT_BRANCH} ..." "${myLOG}" clone_tpot || myRC=$?
    [ "${myRC}" -ne 130 ] || install_stopped "Stopped while getting T-Pot" "Run the installer again"
    if [ "${myRC}" -ne 0 ];
      then
        install_failed "T-Pot could not be cloned from ${myTPOT_REPO_URL} at ${myTPOT_BRANCH}" \
          "Check the repository and the branch (${myLOG}), then run the installer again"
    fi
    myRC=0
    fuUI_SPIN "Setting up the T-Pot installer ..." "${myLOG}" "${HOME}/tpotce/tpot" setup || myRC=$?
    [ "${myRC}" -ne 130 ] || install_stopped "Stopped while setting up the T-Pot installer" "Run the installer again"
    if [ "${myRC}" -eq 0 ] && "${HOME}/tpotce/tpot" install --help >/dev/null 2>&1;
      then
        echo
        export TPOT_BRANCH="${myTPOT_BRANCH}" TPOT_REPO_URL="${myTPOT_REPO_URL}" TPOT_INSTALL_PACKAGES_DONE=1
        exec "${HOME}/tpotce/tpot" install
    fi
    fuUI_WARN "The assistant cannot start (see ${myLOG}), the installer asks in the terminal instead."
    export TPOT_INSTALL_PACKAGES_DONE=1
    myQST="y"
fi

if [[ -z "$myQST" ]]; then
  if ! fuUI_CONFIRM "Install?";
    then
      echo
      fuUI_INFO "Aborting!"
      echo
      exit 0
  fi
fi

# The questions come before anything is installed, the rest runs on its own.
ask_type
ask_web_user

# Fail before anything is installed if an existing ~/tpotce would be used
# instead of the repository and branch that were asked for.
check_tpot_clone

# A sudo password from -B has to work, a typo would only show in the playbook
if [ -n "${myBECOME_FILE}" ] && command -v sudo >/dev/null && ! command sudo -S -k -p "" -v < "${myBECOME_FILE}" >/dev/null 2>&1;
  then
    fuUI_ERROR "The sudo password in ${myBECOME_FILE} is not accepted."
    install_failed "sudo does not accept the password of -B" "Check ${myBECOME_FILE}, then run the installer again"
fi

# Fail before anything is installed: -s promises an unattended run, but Ansible
# would ask for the become password. Only possible where sudo already exists -
# the Debian branch below installs it and the check is repeated afterwards.
if [ "${myUNATTENDED}" = "y" ] && [ -z "${myBECOME_FILE}" ] && command -v sudo >/dev/null && sudo_password_required;
  then
    abort_unattended
fi

# Same here: a become password will be needed, so settle the sudo-rs question
# before anything is installed. On the Debian branch below sudo may not exist
# yet, the check is repeated with the become option further down.
if command -v sudo >/dev/null && sudo_password_required;
  then
    sudo_rs_become_exe
fi

check_ports
# Install packages based on the distribution
get_packages

# Define tag for Ansible
myANSIBLE_DISTRIBUTIONS=("Fedora Linux" "Debian GNU/Linux" "Raspbian GNU/Linux" "Rocky Linux" "Red Hat Enterprise Linux")
if [[ "${myANSIBLE_DISTRIBUTIONS[@]}" =~ "${myCURRENT_DISTRIBUTION}" ]];
  then
    # special case AGAIN, /etc/os-release doesn't match Ansible's tagging conventions
    if [[ "${myCURRENT_DISTRIBUTION}" == "Red Hat Enterprise Linux" ]]; then
      myANSIBLE_TAG="RedHat"
    else
      myANSIBLE_TAG=$(echo ${myCURRENT_DISTRIBUTION} | cut -d " " -f 1)
    fi
  else
    myANSIBLE_TAG=${myCURRENT_DISTRIBUTION}
fi

# The playbook comes from the clone of the repository, made now unless it is there
# already (the assistant and a local clone have it). The playbook keeps it.
if [ ! -f "${HOME}/tpotce/installer/install/tpot.yml" ];
  then
    myRC=0
    fuUI_SPIN "Getting T-Pot from ${myTPOT_REPO_URL} at ${myTPOT_BRANCH} ..." "${myLOG}" clone_tpot || myRC=$?
    [ "${myRC}" -ne 130 ] || install_stopped "Stopped while getting T-Pot" "Run the installer again"
    if [ "${myRC}" -ne 0 ];
      then
        # a mistyped branch or repository ends up here, and would fail with a
        # confusing Ansible error further down
        install_failed "T-Pot could not be cloned from ${myTPOT_REPO_URL} at ${myTPOT_BRANCH}" \
          "Check the repository and the branch (${myLOG}), then run the installer again"
    fi
fi
myANSIBLE_TPOT_PLAYBOOK="${HOME}/tpotce/installer/install/tpot.yml"
echo

# Check type of sudo access. Applies to every distribution - making an
# exception for one of them breaks unattended installation there.
if ! sudo_password_required;
  then
    myANSIBLE_BECOME_OPTION="--become"
    fuUI_INFO "Passwordless ‘sudo‘ available, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    echo
elif [ -n "${myBECOME_FILE}" ];
  then
    sudo_rs_become_exe
    myANSIBLE_BECOME_OPTION="--become --become-password-file ${myBECOME_FILE}"
    fuUI_INFO "‘sudo‘ requires a password, Ansible reads it from ${myBECOME_FILE}."
    echo
  else
    # -s promises an unattended run, and --ask-become-pass would prompt. On the
    # Debian branch sudo may have been installed after the check above.
    if [ "${myUNATTENDED}" = "y" ];
      then
        abort_unattended
    fi
    sudo_rs_become_exe
    myANSIBLE_BECOME_OPTION="--become --ask-become-pass"
    fuUI_INFO "‘sudo‘ requires a password, setting ansible become option to ${myANSIBLE_BECOME_OPTION}."
    fuUI_INFO "Ansible will ask for the ‘BECOME password‘ which is typically the password you ’sudo’ with."
    echo
fi

# Run Ansible Playbook
fuUI_INFO "Now running T-Pot Ansible Installation Playbook ..."
echo
rm ${HOME}/install_tpot.log > /dev/null 2>&1
# neither a repository URL nor a git reference contains a space, so the
# unquoted expansion below splits into exactly four arguments
myANSIBLE_EXTRA_VARS="-e tpot_repo=${myTPOT_REPO_URL} -e tpot_branch=${myTPOT_BRANCH}"
if [ -n "${myMARKS}" ];
  then
    myTASKS=$(ANSIBLE_INJECT_FACT_VARS=False ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local \
              --tags "${myANSIBLE_TAG}" --list-tasks 2>/dev/null | grep -c "TAGS: \[")
    fuMARK tasks "${myTASKS}"
fi
mark_phase playbook
# INJECT_FACTS_AS_VARS=False: the playbooks read facts as ansible_facts.<name>,
# the auto injected top-level copies are deprecated and gone in ansible-core
# 2.24. Setting it explicitly makes a leftover fail here and now instead of
# silently working until then, and it keeps the deprecation warnings out of the
# log. PYTHON_INTERPRETER=auto_silent keeps the interpreter discovery hint out,
# the discovery itself is unchanged.
ANSIBLE_INJECT_FACT_VARS=False ANSIBLE_PYTHON_INTERPRETER=auto_silent \
ANSIBLE_LOG_PATH=${HOME}/install_tpot.log ansible-playbook ${myANSIBLE_TPOT_PLAYBOOK} -i 127.0.0.1, -c local --tags "${myANSIBLE_TAG}" ${myANSIBLE_BECOME_OPTION} ${myANSIBLE_BECOME_EXE} ${myANSIBLE_EXTRA_VARS}

# Something went wrong
if [ ! $? -eq 0 ];
  then
    fuUI_ERROR "Something went wrong with the Playbook, please review the output and / or install_tpot.log for clues."
    install_failed "The playbook failed, see the output above" \
      "Review ${HOME}/install_tpot.log, fix the cause, then run the installer again"
  else
    fuUI_OK "Playbook was successful."
    echo
fi

# The T-Pot type, asked before the playbook (or given with -t / -c)
mark_phase compose
# and what the summary says about it: a hint and the next step
myINFO=""
myNEXT=""
case "${myTPOT_TYPE}" in
  h) myTPOT_TYPE="HIVE";   myEDITION="standard" ;;
  s) myTPOT_TYPE="SENSOR"; myEDITION="sensor"
     myINFO="Make sure to deploy SSH keys to this SENSOR and disable SSH password authentication."
     myNEXT="On the HIVE run 'tpot sensors add' to join this SENSOR to the HIVE." ;;
  l) myTPOT_TYPE="HIVE";   myEDITION="llm"
     myNEXT="Adjust the T-Pot config file (.env) for Ollama / ChatGPT settings, i.e. with 'tpot llm'." ;;
  i) myTPOT_TYPE="HIVE";   myEDITION="mini" ;;
  m) myTPOT_TYPE="MOBILE"; myEDITION="mobile" ;;
  t) myTPOT_TYPE="HIVE";   myEDITION="tarpit" ;;
esac
if [ -n "${myCUSTOM_COMPOSE}" ];
  then
    fuUI_INFO "Installing your own compose file ${myCUSTOM_COMPOSE}."
    [ "${myCUSTOM_COMPOSE}" = "${HOME}/tpotce/docker-compose.yml" ] || cp "${myCUSTOM_COMPOSE}" "${HOME}/tpotce/docker-compose.yml"
  else
    fuUI_INFO "Installing T-Pot ${myEDITION}."
    cp "${HOME}/tpotce/compose/${myEDITION}.yml" "${HOME}/tpotce/docker-compose.yml"
fi

if [ "${myTPOT_TYPE}" == "HIVE" ];
  # If T-Pot Type is HIVE write the WebUI username and password
  then
    mark_phase user
    fuUI_INFO "Creating the web user ${myWEB_USER} in ${myTPOT_CONF_FILE}"
    # bcrypt, as `tpot users` creates them; the password goes through stdin, not argv
    myWEB_USER_ENC=$(printf "%s" "${myWEB_PW}" | htpasswd -n -i -B "${myWEB_USER}")
    # nothing after this needs the password
    unset myWEB_PW myWEB_PW2
    myWEB_USER_ENC_B64=$(echo -n "${myWEB_USER_ENC}" | base64 -w0)
    sed -i "s|^WEB_USER=.*|WEB_USER=${myWEB_USER_ENC_B64}|" ${myTPOT_CONF_FILE}
    echo
fi

# Pull docker images
mark_phase pull
if [ -n "${myMARKS}" ];
  then
    fuMARK images "$(sudo docker compose -f "${HOME}/tpotce/docker-compose.yml" config --images 2>/dev/null | wc -l)"
fi
# A spinner meanwhile, the output goes into its log; in the marks mode it passes through,
# so the assistant counts the pulled images. The spinner runs it in the background, where
# sudo cannot ask: a password is asked for now (-B refreshes it from its file)
if ! fuUI_MARKS_ON && [ -z "${myBECOME_FILE}" ] && sudo_password_required;
  then
    sudo -v
fi
rm -f "${myPULL_LOG}"
myPULL_FAILED=""
myRC=0
fuUI_SPIN "Pulling the images ..." "${myPULL_LOG}" sudo docker compose -f "${HOME}/tpotce/docker-compose.yml" pull || myRC=$?
if [ "${myRC}" -eq 130 ];
  then
    # T-Pot is installed by now, only images are missing
    install_stopped "Stopped during the image pull, T-Pot is installed and pulls the missing images when it starts" \
      "Reboot, then re-connect via SSH on tcp/64295"
fi
if [ "${myRC}" -ne 0 ];
  then
    # not a stop: T-Pot pulls what is missing when it starts (TPOT_PULL_POLICY)
    fuMARK warn pull
    myPULL_FAILED="y"
    fuUI_WARN "Not all images could be pulled (see above), T-Pot tries again when it starts."
fi
echo

# Show running services
if [ -z "${myMARKS}" ];
  then
    fuUI_INFO "Please review for possible honeypot port conflicts."
    fuUI_INFO "While SSH is taken care of, other services such as"
    fuUI_INFO "SMTP, HTTP, etc. might prevent T-Pot from starting."
    echo
    sudo grc netstat -tulpen
    echo
fi

# The tpot command: the playbook links it, a file of your own at /usr/local/bin/tpot stays
myTPOT_FOUND=$(command -v tpot)
if [ -n "${myTPOT_FOUND}" ] && [ "$(readlink -f "${myTPOT_FOUND}")" = "$(readlink -f "${HOME}/tpotce/tpot")" ];
  then
    myTPOT_ITEM="ok:The T-Pot Manager is ready, run it with: tpot"
  elif [ -n "${myTPOT_FOUND}" ];
  then
    myTPOT_ITEM="warn:The command tpot is ${myTPOT_FOUND}, not this T-Pot Manager. Run ${HOME}/tpotce/tpot, or link it with: sudo ln -sfn ${HOME}/tpotce/tpot /usr/local/bin/tpot"
  else
    myTPOT_ITEM="warn:The command tpot is not in your PATH, link it with: sudo ln -sfn ${HOME}/tpotce/tpot /usr/local/bin/tpot"
fi

# Done: what is installed and what comes next
mark_phase "done"
if [ -n "${myCUSTOM_COMPOSE}" ];
  then mySUMMARY=("ok:T-Pot is installed with your own compose file (${myTPOT_TYPE})")
  else mySUMMARY=("ok:T-Pot ${myEDITION} is installed (${myTPOT_TYPE})")
fi
[ "${myTPOT_TYPE}" = "HIVE" ] && mySUMMARY+=("ok:The web user ${myWEB_USER} is set up")
[ -n "${myPULL_FAILED}" ] && mySUMMARY+=("warn:Not all images could be pulled, T-Pot tries again when it starts")
mySUMMARY+=("${myTPOT_ITEM}")
[ -n "${myINFO}" ] && mySUMMARY+=("info:${myINFO}")
mySUMMARY+=("next:Reboot, then re-connect via SSH on tcp/64295")
[ "${myTPOT_TYPE}" = "HIVE" ] && mySUMMARY+=("next:Open the web UI on https://<this host>:64297")
[ -n "${myNEXT}" ] && mySUMMARY+=("next:${myNEXT}")
fuUI_SUMMARY "T-Pot is installed" "${mySUMMARY[@]}"
