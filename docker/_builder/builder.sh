#!/usr/bin/env bash
# The image builder of T-Pot: builds the images of docker-compose.yml here with buildx
# (linux/amd64 and linux/arm64), pushes them to Docker Hub and / or GHCR, sets up the
# buildx builder 'mybuilder' with QEMU and runs the smoke tests of docker/_tests after
# the build. A tool for building releases, not part of the T-Pot Manager.
#
# With no option at a terminal it asks (a menu), with -y, any option or without a
# terminal it never asks. -h shows the options and the exit codes. Linux only (a build
# host, a VM, WSL2). Any user Docker answers to (root, the docker group, rootless
# Docker); root only for the upload limit (tc) while pushing. The settings of a checkout
# (--set, --unset, --show-config, Settings in the menu) are in docker/_builder/.env.local
# (not in git) over docker/_builder/.env.

# CDPATH="": with an exported CDPATH cd prints the folder it found there, into the path
myDIR="$(CDPATH="" cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
myREPO="$(CDPATH="" cd -- "${myDIR}/../.." && pwd)"
# the builder by its full path, for the command lines it prints: they run from anywhere
# (cron starts in HOME), not only from the folder it was started in
mySELF="${myDIR}/${BASH_SOURCE[0]##*/}"
# the look of the T-Pot scripts (installer/lib/ui.sh), plain text if it is missing
# shellcheck source=../../installer/lib/ui.sh
if ! source "${myREPO}/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback
    fuUI_INIT () { return 0; }
    fuUI_BANNER () { local myLINE; echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE//$'\n'/$'\n'### }"; done; echo; }
    fuUI_INFO () { local myTEXT="$*"; echo "### ${myTEXT//$'\n'/$'\n'### }"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuUI_CONFIRM () {
      local myANSWER="" myDEFAULT="" myPROMPT="(y/n)"
      if [ "${1:-}" = "--default" ]; then
        case "${2:-}" in yes|no) myDEFAULT="$2"; shift 2 ;; *) shift ;; esac
      fi
      case "${myDEFAULT}" in yes) myPROMPT="(Y/n)" ;; no) myPROMPT="(y/N)" ;; esac
      while true; do
        read -rp "### $1 ${myPROMPT} " myANSWER || return 1
        myANSWER="${myANSWER#"${myANSWER%%[![:space:]]*}"}"
        myANSWER="${myANSWER%"${myANSWER##*[![:space:]]}"}"
        [ -n "${myANSWER}" ] || myANSWER="${myDEFAULT}"
        case "${myANSWER}" in [yY]|[yY][eE][sS]) return 0 ;; [nN]|[nN][oO]) return 1 ;; esac
      done
    }
    fuUI_CHOOSE () {
      local myI=1 myITEM myPICK myDEFAULT="" myVALUE=""
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
        # the spaces around it go, ones in it make it no number (1 2 is not 12)
        myPICK="${myPICK#"${myPICK%%[![:space:]]*}"}"
        myPICK="${myPICK%"${myPICK##*[![:space:]]}"}"
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
    fuUI_INPUT () {
      local myVALUE=""
      if [ "${2:-}" = "password" ];
        then read -rsp "### $1 " myVALUE; echo >&2
        else read -rp "### $1 " myVALUE
      fi
      echo "${myVALUE}"
    }
    fuUI_MARKS_ON () { [ -n "${myMARKS:-}" ] || [ "${TPOT_MARKS:-}" = "1" ]; }
    fuUI_VERSION_GE () {
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
    fuUI_HELP () {
      local myTITLE="$1" myUSAGE="$2" myW=0 myI myLINE myFIRST myREST myPAD
      shift 2
      local -a myABOUT=() myFLAGS=() myTEXTS=() myCMDS=() myCTEXTS=() myNOTES=()
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --about) myABOUT+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
          --opt) myFLAGS+=("${2:-}"); myTEXTS+=("${3:-}"); shift $(( $# < 3 ? $# : 3 )) ;;
          --example) myCMDS+=("${2:-}"); myCTEXTS+=("${3:-}"); shift $(( $# < 3 ? $# : 3 )) ;;
          --note) myNOTES+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
          *) shift ;;
        esac
      done
      for myI in "${!myFLAGS[@]}"; do
        if [ "${#myFLAGS[myI]}" -gt "${myW}" ] && [ "${#myFLAGS[myI]}" -le 24 ]; then myW="${#myFLAGS[myI]}"; fi
      done
      printf 'T-Pot %s\n\nUsage: %s\n' "${myTITLE}" "${myUSAGE//$'\n'/$'\n'       }"
      for myLINE in "${myABOUT[@]}"; do printf '\n%s\n' "${myLINE}"; done
      if [ "${#myFLAGS[@]}" -gt 0 ]; then
        printf '\nOptions:\n'
        printf -v myPAD '%*s' $((myW + 6)) ''
        for myI in "${!myFLAGS[@]}"; do
          myFIRST="${myTEXTS[myI]%%$'\n'*}"
          myREST=""
          [ "${myFIRST}" = "${myTEXTS[myI]}" ] || myREST="${myTEXTS[myI]#*$'\n'}"
          if [ "${#myFLAGS[myI]}" -le "${myW}" ];
            then printf '  %s%*s    %s\n' "${myFLAGS[myI]}" $((myW - ${#myFLAGS[myI]})) '' "${myFIRST}"
            else printf '  %s\n%s%s\n' "${myFLAGS[myI]}" "${myPAD}" "${myFIRST}"
          fi
          [ -z "${myREST}" ] || printf '%s%s\n' "${myPAD}" "${myREST//$'\n'/$'\n'${myPAD}}"
        done
      fi
      if [ "${#myCMDS[@]}" -gt 0 ]; then
        printf '\nExamples:\n'
        for myI in "${!myCMDS[@]}"; do
          printf '  %s\n' "${myCMDS[myI]}"
          [ -z "${myCTEXTS[myI]}" ] || printf '      %s\n' "${myCTEXTS[myI]//$'\n'/$'\n'      }"
        done
      fi
      if [ "${#myNOTES[@]}" -gt 0 ]; then
        printf '\nNotes:\n'
        for myLINE in "${myNOTES[@]}"; do printf '  %s\n' "${myLINE//$'\n'/$'\n'  }"; done
      fi
      return 0
    }
    fuUI_USAGE_ERROR () {
      echo "### [ERROR] - $1" >&2
      echo "###   ${2:-${0##*/}} -h shows the options." >&2
      return 1
    }
    fuUI_LINUX_ONLY () {
      local mySYSTEM
      mySYSTEM=$(uname -s 2>/dev/null)
      [ "${mySYSTEM}" != "Linux" ] || return 0
      case "${mySYSTEM}" in
        Darwin) mySYSTEM="macOS" ;;
        MINGW*|MSYS*|CYGWIN*) mySYSTEM="Windows (${mySYSTEM})" ;;
        "") mySYSTEM="an unknown system" ;;
      esac
      echo "### [ERROR] - $1 does not run on ${mySYSTEM}." >&2
      echo "###   $1 runs on Linux: a T-Pot host, a build host or a VM, WSL2 on Windows." >&2
      exit "${2:-1}"
    }
    fuUI_RESULT () {
      case "$1" in
        ok) echo "### [OK] - $2" ;;
        fail) echo "### [FAILED] - $2" ;;
        warn) echo "### [WARNING] - $2" ;;
        next) echo "### [NEXT] - $2" ;;
        *) echo "### $2" ;;
      esac
    }
    fuUI_SUMMARY () {
      local myTITLE="$1" myITEM myKIND myTEXT myRC=0
      shift
      echo
      echo "### ${myTITLE}"
      for myITEM in "$@"; do
        myKIND="${myITEM%%:*}" myTEXT="${myITEM#*:}"
        case "${myKIND}" in ok|fail|warn|next|info) ;; *) myKIND="info" myTEXT="${myITEM}" ;; esac
        [ "${myKIND}" != "fail" ] || myRC=1
        fuUI_RESULT "${myKIND}" "${myTEXT}"
      done
      echo
      return "${myRC}"
    }
    fuUI_CHOOSE_MANY () {
      local myALL="" myI myJ myN myPICK myPART myA myB myOK
      local -a mySELECTED=()
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --filter) shift ;;
          --all) myALL=1; shift ;;
          --selected) mySELECTED+=("${2:-}"); shift $(( $# < 2 ? $# : 2 )) ;;
          *) break ;;
        esac
      done
      local myHEADER="${1:-}"
      [ "$#" -eq 0 ] || shift
      local -a myLABELS=() myVALUES=() myON=() myNEW=()
      for myI in "$@"; do
        myLABELS+=("${myI%:*}")
        myVALUES+=("${myI##*:}")
        myON+=("${myALL}")
        for myJ in "${mySELECTED[@]}"; do
          [ "${myJ}" != "${myVALUES[${#myVALUES[@]}-1]}" ] || myON[${#myON[@]}-1]=1
        done
      done
      myN="${#myLABELS[@]}"
      echo "### ${myHEADER}" >&2
      for myI in "${!myLABELS[@]}"; do
        if [ -n "${myON[myI]}" ]; then echo "###   $((myI + 1))) [x] ${myLABELS[myI]}" >&2
        else echo "###   $((myI + 1))) [ ] ${myLABELS[myI]}" >&2; fi
      done
      while true; do
        read -rp "### Choice (i.e. 1,3-5; a = all, n = none, enter = the marked ones): " myPICK || return 1
        # the spaces go, one between two digits makes it no choice (1 3 is not 13)
        [[ "${myPICK}" =~ [0-9][[:space:]]+[0-9] ]] || myPICK="${myPICK//[[:space:]]/}"
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
    fuUI_SPIN () {
      local myTITLE="$1" myLOG="$2" myRC=0
      shift 2
      echo "### ${myTITLE}"
      if fuUI_MARKS_ON;
        then { "$@" < /dev/null 2>&1 | tee -a "${myLOG}" 2>/dev/null; myRC="${PIPESTATUS[0]}"; } || true
        else "$@" >>"${myLOG}" 2>&1 < /dev/null || myRC=$?
      fi
      if [ "${myRC}" -eq 0 ];
        then echo "### [OK] - ${myTITLE%% ...}"
        else
          if fuUI_MARKS_ON;
            then echo "### [ERROR] - ${myTITLE%% ...} failed" >&2
            else echo "### [ERROR] - ${myTITLE%% ...} failed, the end of ${myLOG}:" >&2; tail -n 15 "${myLOG}" >&2
          fi
      fi
      return "${myRC}"
    }
# <<< plain fallback
fi
fuUI_INIT
# shellcheck disable=SC2034 # fuUI_BANNER of ui.sh shows the T-Pot logo with it
myUI_LOGO=1

myBUILDER="mybuilder"
myCOMPOSE="${myDIR}/docker-compose.yml"
myENVFILE="${myDIR}/.env"
myLOCALFILE="${TPOT_BUILDER_ENV_LOCAL:-${myDIR}/.env.local}"
myLOGDIR="${TPOT_BUILDER_LOG_DIR:-${myDIR}/log}"
myLOG="${myLOGDIR}/builder.log"
myTESTDIR="${TPOT_BUILDER_TESTS_DIR:-${myREPO}/docker/_tests/tests}"
myBINFMT="${TPOT_BINFMT_DIR:-/proc/sys/fs/binfmt_misc}"
myLOGIN_TIMEOUT="${TPOT_BUILDER_LOGIN_TIMEOUT:-30}"
# build.platforms: !override and tags: !reset of the generated override file
myCOMPOSE_MIN="2.24.4"
myDEFAULT_ARCH="both"
myDEFAULT_JOBS=2
myDEFAULT_LIMIT="40mbit" # at most 90% of the upload bandwidth there is
myDEFAULT_HUB="dtagdevsec"
myDEFAULT_GHCR="ghcr.io/telekom-security"
mySTOP_TIMEOUT="${TPOT_BUILDER_STOP_TIMEOUT:-10}" # seconds for the builds to end on a signal
myGROUP_NAMES="honeypots tanner nsm elk tools"
# the settings (fuCONFIG): an option, else the environment, else .env.local, else .env,
# else the built-in default. The version is none: the file version, -t for one run
mySETTING_KEYS="TPOT_DOCKER_REPO TPOT_GHCR_REPO TPOT_BUILDER_ARCH TPOT_BUILDER_JOBS TPOT_BUILDER_LIMIT"
# the environment as it came, before fuSETTINGS exports the values of a run (fuCONFIG_GET
# reads them as myENV_<key without TPOT_>)
myENV_VERSION="${TPOT_VERSION:-}"
# shellcheck disable=SC2034
{
  myENV_DOCKER_REPO="${TPOT_DOCKER_REPO:-}"
  myENV_GHCR_REPO="${TPOT_GHCR_REPO:-}"
  myENV_BUILDER_ARCH="${TPOT_BUILDER_ARCH:-}"
  myENV_BUILDER_JOBS="${TPOT_BUILDER_JOBS:-}"
  myENV_BUILDER_LIMIT="${TPOT_BUILDER_LIMIT:-}"
}

# the options (fuPARSE), "" is off; myARCH / myJOBS / myLIMIT are those of the run,
# myOPT_* what an option said
myACTION="build"
myYES=""
myIMAGES=""
myGROUPS=""
myARCH="${myDEFAULT_ARCH}"
myPUSH_HUB=""
myPUSH_GHCR=""
myNO_CACHE=""
myJOBS="${myDEFAULT_JOBS}"
myLIMIT="${myDEFAULT_LIMIT}"
myOPT_ARCH=""
myOPT_JOBS=""
myOPT_LIMIT=""
myTAG=""
myDOCKER_REPO=""
myGHCR_REPO=""
myTEST=""
myMENU=""
# the platforms were picked in the menu (fuMENU_OPTIONS), for fuARCH_SOURCE
myMENU_ARCH=""
mySET_ITEMS=()
# the first option of a build and the option of another action (fuBUILD_OPTIONS_OK)
myBUILD_OPT=""
myACTION_OPT=""
# the settings without the options (fuCONFIG), for the command line of the menu
myCONF_ARCH="${myDEFAULT_ARCH}"
myCONF_JOBS="${myDEFAULT_JOBS}"
myCONF_LIMIT="${myDEFAULT_LIMIT}"
myCONF_HUB="${myDEFAULT_HUB}"
myCONF_GHCR="${myDEFAULT_GHCR}"
# where myCONF_* come from, and what the files say without the environment (fuCONFIG)
# shellcheck disable=SC2034 # set and read through their names (printf -v, fuTAG_REFUSED)
{
  myFROM_DOCKER_REPO="built in" myFROM_GHCR_REPO="built in" myFROM_BUILDER_ARCH="built in"
  myFROM_BUILDER_JOBS="built in" myFROM_BUILDER_LIMIT="built in"
  myFILES_DOCKER_REPO="${myDEFAULT_HUB}" myFILES_GHCR_REPO="${myDEFAULT_GHCR}" myFILES_BUILDER_ARCH="${myDEFAULT_ARCH}"
  myFILES_BUILDER_JOBS="${myDEFAULT_JOBS}" myFILES_BUILDER_LIMIT="${myDEFAULT_LIMIT}"
}
# the state of a run
myEXIT_DONE=""
myLIMIT_SET=""
myOTHER_DOCKER=""
myOTHER_REMOTE=""
myIF=""
myVER=""
myHUB=""
myGHCR=""
myHOST=""
myPLATFORMS=()
myFOREIGN=()
myLIST=()
myFILES=()

fuGROUP_IMAGES () {
  # fuGROUP_IMAGES <group>: the images of a group, every image of docker-compose.yml is
  # in one of them (tpotctl/tests/test_scripts_builder.py checks)
  case "$1" in
    honeypots) echo "adbhoney beelzebub ciscoasa citrixhoneypot conpot cowrie ddospot dicompot dionaea" \
                    "elasticpot endlessh galah glutton go-pot h0neytr4p hellpot heralding honeyaml" \
                    "honeypots honeytrap ipphoney log4pot mailoney medpot miniprint rdphoneypot" \
                    "redishoneypot sentrypeer wordpot" ;;
    tanner) echo "redis phpox tanner snare" ;;
    nsm) echo "p0f suricata" ;;
    elk) echo "elasticsearch kibana logstash map" ;;
    tools) echo "tpotinit ewsposter nginx" ;;
    *) return 1 ;;
  esac
}

fuGROUP_TEXT () {
  case "$1" in
    honeypots) echo "Honeypots (without the Tanner stack)" ;;
    tanner) echo "Tanner stack (redis, phpox, tanner, snare)" ;;
    nsm) echo "NSM (p0f, suricata)" ;;
    elk) echo "Elastic Stack and Attack Map" ;;
    tools) echo "Tools (tpotinit, ewsposter, nginx)" ;;
  esac
}

fuCOMPOSE_SERVICES () {
  # the services of docker-compose.yml, read from the file (no docker needed)
  awk '/^services:/ { s = 1; next }
       s && /^[^ #]/ { s = 0 }
       s && /^  [a-z0-9][a-z0-9_.-]*:[[:space:]]*$/ { sub(/^  /, ""); sub(/:.*/, ""); print }' "${myCOMPOSE}"
}

fuHELP () {
  fuUI_HELP "Image Builder" "builder.sh [options]" \
    --about "Builds the images of docker/_builder/docker-compose.yml with buildx for linux/amd64 and
linux/arm64, pushes them on request and runs the smoke tests of docker/_tests after it.
With no option at a terminal it asks (a menu); with -y, any option or without a
terminal it never asks. The logs go to docker/_builder/log. It runs on Linux (a build
host, a VM, WSL2) for any user Docker answers to (root, the docker group, rootless
Docker); root only for the upload limit while pushing." \
    --opt "-y, --yes" "Never ask, build at once (all images without other options)" \
    --opt "-i, --images a,b" "Images by name (-L lists them)" \
    --opt "-g, --group g,h" "Images by group: ${myGROUP_NAMES// /, }, all
(default: all; with -i the union)" \
    --opt "-a, --arch ARCH" "amd64, arm64, host (this host only) or both
(default: both, or TPOT_BUILDER_ARCH)" \
    --opt "-p, --push" "Push to Docker Hub and GHCR (needs an existing docker login)" \
    --opt "--push-hub" "Push to Docker Hub only" \
    --opt "--push-ghcr" "Push to GHCR only" \
    --opt "-n, --no-cache" "Build without the cache" \
    --opt "-j, --jobs N" "Builds at a time, 1-16 (default: ${myDEFAULT_JOBS}, or TPOT_BUILDER_JOBS)" \
    --opt "-l, --upload-limit RATE" "Upload limit while pushing, tc rate or off (default: ${myDEFAULT_LIMIT},
or TPOT_BUILDER_LIMIT); needs root" \
    --opt "-t, --tag VERSION" "The version tag (default: the file version of the checkout); a push for
one platform (-a amd64, arm64, host) needs one of its own, no plain version
(digits and dots, a v first or not, as a release has: not <version> or
v<version>), i.e. <version>-arm64" \
    --opt "--docker-repo REPO" "The Docker Hub repository (default: TPOT_DOCKER_REPO)" \
    --opt "--ghcr-repo REPO" "The GHCR repository (default: TPOT_GHCR_REPO)" \
    --opt "-T, --test" "Run the smoke tests of the built images after the build" \
    --opt "-L, --list" "List the images by group" \
    --opt "--check" "Check the buildx builder 'mybuilder' (platforms, QEMU)" \
    --opt "--setup" "Set up the builder 'mybuilder' and QEMU for multi-arch builds" \
    --opt "--uninstall" "Remove that setup again (builder, QEMU handlers, images)" \
    --opt "--set KEY=VALUE" "Keep a setting in docker/_builder/.env.local (not in git), once per key:
${mySETTING_KEYS// /, }" \
    --opt "--unset KEY" "Remove a setting from docker/_builder/.env.local" \
    --opt "--show-config" "Show every setting and where it comes from" \
    --opt "-h, --help" "Show this help" \
    --example "sudo builder.sh -y" "Build every image for both platforms" \
    --example "builder.sh -i cowrie,tpotinit -a host -T" "Two images for this host, then their smoke tests" \
    --example "sudo builder.sh -g honeypots -p -l 80mbit" "Build the honeypots, push them with an upload limit" \
    --example "builder.sh --set TPOT_DOCKER_REPO=me --set TPOT_BUILDER_JOBS=4" "Build for your own Docker Hub repository, four at a time" \
    --note "A setting comes from an option, else the environment, else docker/_builder/.env.local,
else docker/_builder/.env, else the built-in default. The version is no setting: the file
version of the checkout, -t for one run." \
    --note "Exit codes: 0 done, 1 an image failed, 2 a wrong option, 3 the environment (Linux, Docker,
builder, QEMU, login, tc, root for the limit), 4 built but a smoke test failed, 130
cancelled. A wrong setting is 2 as well." \
    --note "Pushing uses the login there is (docker login, docker login ghcr.io as the user who runs
the builder); without one it stops instead of asking."
}

fuLIST () {
  local myGROUP
  for myGROUP in ${myGROUP_NAMES}; do
    printf '%-10s %s\n' "${myGROUP}" "$(fuGROUP_IMAGES "${myGROUP}")"
  done
}

fuACTION () {
  # fuACTION <action> <option>: the one thing this run does besides a build
  if [ "${myACTION}" != "build" ] && [ "${myACTION}" != "$1" ];
    then fuUI_USAGE_ERROR "Only one of -L, --check, --setup, --uninstall, --set / --unset and --show-config."; return 2
  fi
  myACTION="$1"
  [ -n "${myACTION_OPT}" ] || myACTION_OPT="$2"
}

fuBUILD_OPTIONS_OK () {
  # rc 2 for an option of a build next to an action that builds nothing (-L, --check,
  # --setup, --uninstall, --set / --unset): it would be dropped without a word.
  # --show-config shows what they would take, -h shows the help
  case "${myACTION}" in build|show|help) return 0 ;; esac
  [ -n "${myBUILD_OPT}" ] || return 0
  fuUI_USAGE_ERROR "${myBUILD_OPT} is an option of a build, ${myACTION_OPT} builds nothing: one of them."
  return 2
}

fuSETTING_KEY () {
  # fuSETTING_KEY <key>: rc 0 for a key of the settings, else rc 1 and why into mySETTING_WHY
  mySETTING_WHY=""
  [[ " ${mySETTING_KEYS} " != *" $1 "* ]] || return 0
  if [ "$1" = "TPOT_VERSION" ];
    then mySETTING_WHY="TPOT_VERSION is no setting: the file version of the checkout, -t for one run."
    else mySETTING_WHY="Not a setting of the builder: $1 (${mySETTING_KEYS// /, })."
  fi
  return 1
}

fuSETTING_CHECK () {
  # fuSETTING_CHECK <key> <value>: rc 0 for a value the key takes (the value of the
  # key, i.e. 4 for 04, into mySETTING_VALUE), else rc 1 and why into mySETTING_WHY
  local myKEY="$1" myVAL="$2"
  mySETTING_VALUE="${myVAL}"
  fuSETTING_KEY "${myKEY}" || return 1
  case "${myKEY}" in
    TPOT_DOCKER_REPO|TPOT_GHCR_REPO)
      fuREPO_OK "${myVAL}" || mySETTING_WHY="Not a repository: ${myVAL}." ;;
    TPOT_BUILDER_ARCH)
      case "${myVAL}" in amd64|arm64|host|both) ;;
        *) mySETTING_WHY="Unknown platform: ${myVAL} (amd64, arm64, host, both)." ;;
      esac ;;
    TPOT_BUILDER_JOBS)
      if [[ "${myVAL}" =~ ^[0-9]{1,3}$ ]] && [ "$((10#${myVAL}))" -ge 1 ] && [ "$((10#${myVAL}))" -le 16 ];
        then mySETTING_VALUE="$((10#${myVAL}))"
        else mySETTING_WHY="1 to 16 builds at a time, not ${myVAL}."
      fi ;;
    TPOT_BUILDER_LIMIT)
      [ "${myVAL}" = "off" ] || fuRATE_OK "${myVAL}" || \
        mySETTING_WHY="Not a rate for tc: ${myVAL} (i.e. 40mbit, 800kbit, off)." ;;
  esac
  [ -z "${mySETTING_WHY}" ]
}

fuSET () {
  # fuSET <option> <value>: checks and takes the value of an option
  local myOPT="$1" myVAL="$2" myITEM myKNOWN
  case "${myOPT}" in
    -i|--images)
      myKNOWN=" $(fuCOMPOSE_SERVICES | tr '\n' ' ') "
      [ -n "${myVAL//[, ]/}" ] || { fuUI_USAGE_ERROR "${myOPT} needs the names of images."; return 2; }
      for myITEM in ${myVAL//,/ }; do
        [[ "${myKNOWN}" == *" ${myITEM} "* ]] || { fuUI_USAGE_ERROR "Unknown image: ${myITEM} (-L lists them)."; return 2; }
        myIMAGES="${myIMAGES:+${myIMAGES},}${myITEM}"
      done ;;
    -g|--group)
      [ -n "${myVAL//[, ]/}" ] || { fuUI_USAGE_ERROR "${myOPT} needs a group."; return 2; }
      for myITEM in ${myVAL//,/ }; do
        [[ " ${myGROUP_NAMES} all " == *" ${myITEM} "* ]] || \
          { fuUI_USAGE_ERROR "Unknown group: ${myITEM} (${myGROUP_NAMES// /, }, all)."; return 2; }
        myGROUPS="${myGROUPS:+${myGROUPS},}${myITEM}"
      done ;;
    -a|--arch)
      fuSETTING_CHECK TPOT_BUILDER_ARCH "${myVAL}" || { fuUI_USAGE_ERROR "${mySETTING_WHY}"; return 2; }
      myARCH="${mySETTING_VALUE}" myOPT_ARCH="${mySETTING_VALUE}" ;;
    -j|--jobs)
      fuSETTING_CHECK TPOT_BUILDER_JOBS "${myVAL}" || { fuUI_USAGE_ERROR "${myOPT}: ${mySETTING_WHY}"; return 2; }
      myJOBS="${mySETTING_VALUE}" myOPT_JOBS="${mySETTING_VALUE}" ;;
    -l|--upload-limit)
      fuSETTING_CHECK TPOT_BUILDER_LIMIT "${myVAL}" || { fuUI_USAGE_ERROR "${mySETTING_WHY}"; return 2; }
      myLIMIT="${mySETTING_VALUE}" myOPT_LIMIT="${mySETTING_VALUE}" ;;
    -t|--tag)
      fuTAG_OK "${myVAL}" || { fuUI_USAGE_ERROR "Not a version tag: ${myVAL}."; return 2; }
      myTAG="${myVAL}" ;;
    --docker-repo|--ghcr-repo)
      fuREPO_OK "${myVAL}" || { fuUI_USAGE_ERROR "Not a repository: ${myVAL}."; return 2; }
      if [ "${myOPT}" = "--docker-repo" ]; then myDOCKER_REPO="${myVAL}"; else myGHCR_REPO="${myVAL}"; fi ;;
    --set|--unset)
      fuACTION settings "${myOPT}" || return 2
      if [ "${myOPT}" = "--set" ]; then
        [[ "${myVAL}" == *=* ]] || { fuUI_USAGE_ERROR "--set takes KEY=VALUE, not ${myVAL}."; return 2; }
        fuSETTING_CHECK "${myVAL%%=*}" "${myVAL#*=}" || { fuUI_USAGE_ERROR "${mySETTING_WHY}"; return 2; }
        mySET_ITEMS+=("${myVAL%%=*}=${mySETTING_VALUE}")
      else
        fuSETTING_KEY "${myVAL}" || { fuUI_USAGE_ERROR "${mySETTING_WHY}"; return 2; }
        mySET_ITEMS+=("${myVAL}")
      fi ;;
  esac
  return 0
}

fuRATE_OK () { [[ "$1" =~ ^[1-9][0-9]*(\.[0-9]+)?([kKmMgGtT]?(bit|bps))$ ]]; }
fuTAG_OK () { [[ "$1" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; }
fuREPO_OK () { [[ "$1" =~ ^[a-z0-9]([a-z0-9._-]*[a-z0-9])?(:[0-9]+)?(/[a-z0-9]([a-z0-9._-]*[a-z0-9])?)*$ ]]; }

fuPARSE () {
  # fuPARSE <argument> ...: the options into my* (rc 2 with a message for a wrong one);
  # -abc is -a -b -c, -j4 is -j 4, --opt=value is --opt value
  local myOPT myVAL myHAS
  while [ "$#" -gt 0 ]; do
    if [[ "$1" =~ ^-[A-Za-z].+ ]]; then
      case "${1:1:1}" in
        i|g|a|j|l|t) set -- "-${1:1:1}" "${1:2}" "${@:2}" ;;
        *) set -- "-${1:1:1}" "-${1:2}" "${@:2}" ;;
      esac
    fi
    myOPT="$1" myVAL="" myHAS=""
    case "${myOPT}" in --*=*) myVAL="${myOPT#*=}" myOPT="${myOPT%%=*}" myHAS=1 ;; esac
    case "${myOPT}" in
      -i|--images|-g|--group|-a|--arch|-j|--jobs|-l|--upload-limit|-t|--tag|--docker-repo|--ghcr-repo|--set|--unset)
        if [ -z "${myHAS}" ]; then
          [ "$#" -ge 2 ] || { fuUI_USAGE_ERROR "${myOPT} needs a value."; return 2; }
          myVAL="$2"
          shift
        fi
        case "${myOPT}" in --set|--unset) ;; *) [ -n "${myBUILD_OPT}" ] || myBUILD_OPT="${myOPT}" ;; esac
        fuSET "${myOPT}" "${myVAL}" || return 2
        shift
        continue ;;
    esac
    [ -z "${myHAS}" ] || { fuUI_USAGE_ERROR "${myOPT} takes no value."; return 2; }
    case "${myOPT}" in
      -h|--help) myACTION="help"; return 0 ;;
      # -y only says never ask: no option of a build, harmless next to any action
      -y|--yes) myYES=1 ;;
      -p|--push|--push-hub|--push-ghcr|-n|--no-cache|-T|--test)
        [ -n "${myBUILD_OPT}" ] || myBUILD_OPT="${myOPT}"
        case "${myOPT}" in
          -p|--push) myPUSH_HUB=1 myPUSH_GHCR=1 ;;
          --push-hub) myPUSH_HUB=1 ;;
          --push-ghcr) myPUSH_GHCR=1 ;;
          -n|--no-cache) myNO_CACHE=1 ;;
          -T|--test) myTEST=1 ;;
        esac ;;
      -L|--list) fuACTION list "${myOPT}" || return 2 ;;
      --check) fuACTION check "${myOPT}" || return 2 ;;
      --setup) fuACTION setup "${myOPT}" || return 2 ;;
      --uninstall) fuACTION uninstall "${myOPT}" || return 2 ;;
      --show-config) fuACTION show "${myOPT}" || return 2 ;;
      *) fuUI_USAGE_ERROR "Unknown option: ${myOPT}"; return 2 ;;
    esac
    shift
  done
  fuBUILD_OPTIONS_OK
}

fuENV_VALUE () {
  # fuENV_VALUE <file> <key>: the value of the key in an env file (its last line, as
  # compose takes it), without quotes, an inline comment and a CR; read as text, never
  # run, a UTF-8 BOM before the first line left out. rc 1 when the file has none (or an
  # empty one)
  local myLINE myV="" myFOUND="" myFIRST=1 myDQ='^"([^"]*)"' mySQ="^'([^']*)'"
  local myRE="^[[:space:]]*(export[[:space:]]+)?$2[[:space:]]*=[[:space:]]*(.*)$"
  [ -r "$1" ] || return 1
  while IFS= read -r myLINE || [ -n "${myLINE}" ]; do
    myLINE="${myLINE%$'\r'}"
    if [ -n "${myFIRST}" ]; then myLINE="${myLINE#$'\xef\xbb\xbf'}"; myFIRST=""; fi
    [[ "${myLINE}" =~ ${myRE} ]] || continue
    myV="${BASH_REMATCH[2]}"
    if [[ "${myV}" =~ ${myDQ} ]] || [[ "${myV}" =~ ${mySQ} ]];
      then myV="${BASH_REMATCH[1]}"
      else
        myV="${myV%%[[:space:]]#*}"
        myV="${myV%"${myV##*[![:space:]]}"}"
    fi
    myFOUND=1
  done < "$1"
  [ -n "${myFOUND}" ] && [ -n "${myV}" ] || return 1
  printf '%s\n' "${myV}"
}

fuENV_HAS () {
  # fuENV_HAS <file> <key>: rc 0 when a line of the env file sets the key (an empty
  # value too), read like fuENV_VALUE
  local myLINE myFIRST=1 myRE="^[[:space:]]*(export[[:space:]]+)?$2[[:space:]]*="
  [ -r "$1" ] || return 1
  while IFS= read -r myLINE || [ -n "${myLINE}" ]; do
    if [ -n "${myFIRST}" ]; then myLINE="${myLINE#$'\xef\xbb\xbf'}"; myFIRST=""; fi
    [[ "${myLINE}" =~ ${myRE} ]] && return 0
  done < "$1"
  return 1
}

fuSHORT () {
  # fuSHORT <path>: a file of the checkout relative to it, any other one as it is
  case "$1" in "${myREPO}/"*) echo "${1#"${myREPO}/"}" ;; *) echo "$1" ;; esac
}

fuRELEASE_FROM () {
  # the release version and where it comes from, <version>|<origin>: the file version of
  # the checkout, else TPOT_VERSION of the environment, else that of docker/_builder/.env.
  # Never the .env of the checkout (that of an installed T-Pot) nor myUI_VERSION (only
  # the credits of the banner); -t is the version of one run over it (fuSETTINGS)
  local myV=""
  [ -r "${myREPO}/version" ] && { IFS= read -r myV < "${myREPO}/version" || true; }
  myV="${myV//[[:space:]]/}"
  if fuTAG_OK "${myV}"; then echo "${myV}|the file version (-t for one run, no setting)"; return 0; fi
  if [ -n "${myENV_VERSION}" ]; then echo "${myENV_VERSION}|environment"; return 0; fi
  if myV=$(fuENV_VALUE "${myENVFILE}" TPOT_VERSION); then echo "${myV}|$(fuSHORT "${myENVFILE}")"; return 0; fi
  echo "|none"
}

fuRELEASE () {
  local myV
  myV=$(fuRELEASE_FROM)
  echo "${myV%%|*}"
}

fuCONFIG_GET () {
  # fuCONFIG_GET <key> [files]: <value>|<origin> of a setting without the options: the
  # environment (not with files), else .env.local, else .env, else the built-in default
  local myKEY="$1" myVAR myV
  myVAR="myENV_${myKEY#TPOT_}"
  if [ "${2:-}" != "files" ] && [ -n "${!myVAR:-}" ]; then echo "${!myVAR}|environment"; return 0; fi
  if myV=$(fuENV_VALUE "${myLOCALFILE}" "${myKEY}"); then echo "${myV}|$(fuSHORT "${myLOCALFILE}")"; return 0; fi
  if myV=$(fuENV_VALUE "${myENVFILE}" "${myKEY}"); then echo "${myV}|$(fuSHORT "${myENVFILE}")"; return 0; fi
  case "${myKEY}" in
    TPOT_DOCKER_REPO) myV="${myDEFAULT_HUB}" ;;
    TPOT_GHCR_REPO) myV="${myDEFAULT_GHCR}" ;;
    TPOT_BUILDER_ARCH) myV="${myDEFAULT_ARCH}" ;;
    TPOT_BUILDER_JOBS) myV="${myDEFAULT_JOBS}" ;;
    TPOT_BUILDER_LIMIT) myV="${myDEFAULT_LIMIT}" ;;
  esac
  echo "${myV}|built in"
}

fuCONFIG_OPTION () {
  # fuCONFIG_OPTION <key>: what an option of this run says for it, empty for none
  case "$1" in
    TPOT_DOCKER_REPO) echo "${myDOCKER_REPO}" ;;
    TPOT_GHCR_REPO) echo "${myGHCR_REPO}" ;;
    TPOT_BUILDER_ARCH) echo "${myOPT_ARCH}" ;;
    TPOT_BUILDER_JOBS) echo "${myOPT_JOBS}" ;;
    TPOT_BUILDER_LIMIT) echo "${myOPT_LIMIT}" ;;
  esac
}

fuCONFIG () {
  # the settings of this run into myCONF_* (without the options) with where they come
  # from in myFROM_*, what the files say without the environment in myFILES_* (what the
  # command line of the menu leaves out), and myARCH / myJOBS / myLIMIT (an option over
  # them); rc 2 for a value that is used and wrong, with where it comes from, rc 3 for an
  # .env.local that is no text (its keys would go unseen)
  local myKEY myOUT myVAL myFROM
  fuLOCAL_TEXT || return 3
  for myKEY in ${mySETTING_KEYS}; do
    myOUT=$(fuCONFIG_GET "${myKEY}" files)
    # the value the key takes (4 for 04), as the run has it
    myVAL="${myOUT%%|*}"
    if fuSETTING_CHECK "${myKEY}" "${myVAL}"; then myVAL="${mySETTING_VALUE}"; fi
    printf -v "myFILES_${myKEY#TPOT_}" '%s' "${myVAL}"
    myOUT=$(fuCONFIG_GET "${myKEY}")
    myVAL="${myOUT%%|*}" myFROM="${myOUT#*|}"
    printf -v "myFROM_${myKEY#TPOT_}" '%s' "${myFROM}"
    if [ -z "$(fuCONFIG_OPTION "${myKEY}")" ]; then
      if ! fuSETTING_CHECK "${myKEY}" "${myVAL}"; then
        fuUI_ERROR "${myKEY}=${myVAL} (${myFROM}): ${mySETTING_WHY}"
        # the environment wins over .env.local: --set would not help; --unset removes a key
        # of .env.local only, the tracked .env stays (--set overrides it)
        if [ "${myFROM}" = "environment" ]; then
          fuUI_HINT "Change it in the environment, or remove it there: unset ${myKEY}" >&2
        elif [ "${myFROM}" = "$(fuSHORT "${myENVFILE}")" ]; then
          fuUI_HINT "Fix it in ${myFROM}, or override it in $(fuSHORT "${myLOCALFILE}"): ${0##*/} --set ${myKEY}=<value>" >&2
        else
          fuUI_HINT "Change it: ${0##*/} --set ${myKEY}=<value>, or remove it: ${0##*/} --unset ${myKEY}" >&2
        fi
        return 2
      fi
      myVAL="${mySETTING_VALUE}"
    fi
    case "${myKEY}" in
      TPOT_DOCKER_REPO) myCONF_HUB="${myVAL}" ;;
      TPOT_GHCR_REPO) myCONF_GHCR="${myVAL}" ;;
      TPOT_BUILDER_ARCH) myCONF_ARCH="${myVAL}"; myARCH="${myOPT_ARCH:-${myVAL}}" ;;
      TPOT_BUILDER_JOBS) myCONF_JOBS="${myVAL}"; myJOBS="${myOPT_JOBS:-${myVAL}}" ;;
      TPOT_BUILDER_LIMIT) myCONF_LIMIT="${myVAL}"; myLIMIT="${myOPT_LIMIT:-${myVAL}}" ;;
    esac
  done
  return 0
}

fuSHOW_CONFIG () {
  # --show-config: every setting as KEY=VALUE with where it comes from, the version too;
  # rc 3 for an .env.local that is no text
  local myKEY myOUT myVAL myFROM
  fuLOCAL_TEXT || return 3
  printf '%s\n' "# The settings of the image builder: an option, else the environment, else" \
    "# $(fuSHORT "${myLOCALFILE}"), else $(fuSHORT "${myENVFILE}"), else the built-in default"
  for myKEY in ${mySETTING_KEYS}; do
    myVAL=$(fuCONFIG_OPTION "${myKEY}")
    if [ -n "${myVAL}" ];
      then myFROM="option"
      else myOUT=$(fuCONFIG_GET "${myKEY}"); myVAL="${myOUT%%|*}" myFROM="${myOUT#*|}"
    fi
    fuSETTING_CHECK "${myKEY}" "${myVAL}" || myFROM="${myFROM}, wrong: ${mySETTING_WHY}"
    printf '%-40s # %s\n' "${myKEY}=${myVAL}" "${myFROM}"
  done
  if [ -n "${myTAG}" ];
    then myVAL="${myTAG}" myFROM="option"
    else myOUT=$(fuRELEASE_FROM); myVAL="${myOUT%%|*}" myFROM="${myOUT#*|}"
  fi
  printf '%-40s # %s\n' "TPOT_VERSION=${myVAL}" "${myFROM}"
}

fuWRITE_IN_PLACE () {
  # fuWRITE_IN_PLACE <file> <text>: the text into the file in place (inode, mode and
  # owner stay), by the shell itself in one write, no command of its own to be killed
  printf '%s' "$2" > "$1"
}

fuLOCAL_WRITE () {
  # fuLOCAL_WRITE <key> [value]: the key in .env.local set to the value (on its first
  # line, later ones of it go) or, without a value, removed. The new text is made in
  # full first, then written in place, so mode, owner, the other lines, their CRLF and
  # a BOM stay; a new file gets a header. A write that fails half way puts the old text
  # back, a signal during it waits until the file is whole. rc 1 when the file cannot
  # be written (it is as it was), rc 2 when not even the old text went back, rc 3 for a
  # file that is no text (fuLOCAL_TEXT), which stays as it is
  local myKEY="$1" myUNSET="" myOLD="" myNEW myCREATE="" myRC=0 mySIGNAL="" myTRAPS
  [ "$#" -ge 2 ] || myUNSET=1
  if [ ! -e "${myLOCALFILE}" ]; then
    [ -z "${myUNSET}" ] || return 0
    [ -w "$(dirname -- "${myLOCALFILE}")" ] || return 1
    myCREATE=1
    printf -v myNEW '%s\n' "# The settings of the T-Pot image builder in this checkout (not in git), over" \
      "# docker/_builder/.env: builder.sh --set KEY=VALUE / --unset KEY, or Settings in" \
      "# its menu. An option or the environment wins over them; builder.sh -h names the keys." \
      "${myKEY}=${2-}"
  else
    [ -r "${myLOCALFILE}" ] && [ -w "${myLOCALFILE}" ] || return 1
    # the whole file; a NUL ends the read with rc 0 (shell variables hold none): no text
    if IFS= read -r -d '' myOLD < "${myLOCALFILE}"; then return 3; fi
    myNEW=$(printf '%s' "${myOLD}" | LC_ALL=C awk -v key="${myKEY}" -v val="${2-}" -v unset="${myUNSET}" '
      function out(text) { printf "%s%s\n", bom, text; bom = "" }
      { line = $0; cr = ""; if (sub(/\r$/, "", line)) { cr = "\r"; crlf = 1 } }
      NR == 1 && sub(/^\357\273\277/, "", line) { bom = "\357\273\277" }
      line ~ ("^[[:space:]]*(export[[:space:]]+)?" key "[[:space:]]*=") {
        if (!done && !unset) out(key "=" val cr)
        done = 1
        next
      }
      { out(line cr) }
      END { if (!done && !unset) out(key "=" val (crlf ? "\r" : "")) }' && echo .) || return 1
    myNEW="${myNEW%.}"
  fi
  myTRAPS=$(trap -p INT TERM)
  trap 'mySIGNAL=INT' INT
  trap 'mySIGNAL=TERM' TERM
  if ! fuWRITE_IN_PLACE "${myLOCALFILE}" "${myNEW}" 2>/dev/null; then
    myRC=1
    if [ -n "${myCREATE}" ]; then rm -f -- "${myLOCALFILE}"
    elif ! fuWRITE_IN_PLACE "${myLOCALFILE}" "${myOLD}" 2>/dev/null; then myRC=2
    fi
  fi
  trap - INT TERM
  eval "${myTRAPS}"
  # the signal now, to the trap there was before
  [ -z "${mySIGNAL}" ] || kill -s "${mySIGNAL}" "$$"
  [ "${myRC}" -eq 0 ] || return "${myRC}"
  [ -z "${myCREATE}" ] || fuGIVE_BACK "${myLOCALFILE}"
  return 0
}

fuLOCAL_TEXT () {
  # rc 0 when .env.local is text the builder can read and write as lines (or not there);
  # a NUL byte (a binary file, UTF-16) is none: rc 1 with why, the file stays
  local myTEXT
  [ -r "${myLOCALFILE}" ] || return 0
  IFS= read -r -d '' myTEXT < "${myLOCALFILE}" || return 0
  fuLOCAL_FAILED 3
  return 1
}

fuLOCAL_FAILED () {
  # fuLOCAL_FAILED <rc of fuLOCAL_WRITE>: why .env.local was not written, rc 3
  local myPATH="${myLOCALFILE}" myFOLDER
  myFOLDER=$(dirname -- "${myLOCALFILE}")
  if [ "$1" -eq 3 ]; then
    fuUI_ERROR "$(fuSHORT "${myLOCALFILE}") is no text file (a NUL byte, i.e. UTF-16), it stays as it is."
    fuUI_HINT "Save it as UTF-8 text, or remove it: rm $(printf '%q' "${myLOCALFILE}")" >&2
    return 3
  fi
  if [ "$1" -eq 2 ];
    then fuUI_ERROR "Cannot write $(fuSHORT "${myLOCALFILE}"), and its old text did not go back: check it."
    else fuUI_ERROR "Cannot write $(fuSHORT "${myLOCALFILE}")."
  fi
  # a folder that is not there has nothing to give back: make it
  if [ ! -e "${myPATH}" ] && [ ! -d "${myFOLDER}" ]; then
    fuUI_HINT "Its folder is not there: mkdir -p $(printf '%q' "${myFOLDER}")" >&2
  elif ! fuROOT; then
    [ -e "${myPATH}" ] || myPATH="${myFOLDER}"
    fuUI_HINT "A run with sudo or a root login may have left it to root:" \
              "sudo chown $(printf '%q:%q' "$(id -un 2>/dev/null)" "$(id -gn 2>/dev/null)") $(printf '%q' "${myPATH}")" >&2
  fi
  return 3
}

fuENV_WINS () {
  # fuENV_WINS <key>: a warning when the environment of this run sets the key, it wins
  # over .env.local
  local myVAR="myENV_${1#TPOT_}"
  [ -n "${!myVAR:-}" ] || return 0
  fuUI_WARN "$1=${!myVAR} of the environment wins over it (unset $1 for the file to count)."
}

fuSAVE_SETTINGS () {
  # fuSAVE_SETTINGS <KEY=VALUE | KEY> ...: set or remove them in .env.local; rc 3 when
  # it cannot be written
  local myITEM myRC
  fuLOCAL_TEXT || return 3
  for myITEM in "$@"; do
    myRC=0
    if [[ "${myITEM}" == *=* ]]; then
      fuLOCAL_WRITE "${myITEM%%=*}" "${myITEM#*=}" || myRC=$?
      [ "${myRC}" -eq 0 ] || { fuLOCAL_FAILED "${myRC}"; return 3; }
      fuUI_OK "${myITEM} in $(fuSHORT "${myLOCALFILE}")"
      fuENV_WINS "${myITEM%%=*}"
    elif ! fuENV_HAS "${myLOCALFILE}" "${myITEM}"; then
      fuUI_INFO "${myITEM} is not set in $(fuSHORT "${myLOCALFILE}")"
      fuENV_WINS "${myITEM}"
    else
      fuLOCAL_WRITE "${myITEM}" || myRC=$?
      [ "${myRC}" -eq 0 ] || { fuLOCAL_FAILED "${myRC}"; return 3; }
      fuUI_OK "${myITEM} removed from $(fuSHORT "${myLOCALFILE}")"
      fuENV_WINS "${myITEM}"
    fi
  done
  return 0
}

fuSETTINGS () {
  # the version and the repositories of this run: an option, else the settings
  # (fuCONFIG) and the release version (fuRELEASE); exported, so compose takes them over
  # its .env
  myVER="${myTAG:-$(fuRELEASE)}"
  myHUB="${myDOCKER_REPO:-${myCONF_HUB}}"
  myGHCR="${myGHCR_REPO:-${myCONF_GHCR}}"
  export TPOT_VERSION="${myVER}" TPOT_DOCKER_REPO="${myHUB}" TPOT_GHCR_REPO="${myGHCR}"
  export BUILDX_BUILDER="${myBUILDER}"
  myFILES=(--env-file "${myENVFILE}" -f "${myCOMPOSE}")
}

fuHOST_ARCH () {
  case "$(uname -m 2>/dev/null)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    *) return 1 ;;
  esac
}

fuPLATFORMS () {
  # myPLATFORMS of -a, myFOREIGN the architectures among them that need QEMU here
  local myA
  myHOST=$(fuHOST_ARCH) || myHOST=""
  case "${myARCH}" in
    both) myPLATFORMS=(linux/amd64 linux/arm64) ;;
    host)
      if [ -z "${myHOST}" ]; then
        fuUI_ERROR "This host ($(uname -m 2>/dev/null)) is neither amd64 nor arm64, -a host does not work here."
        return 3
      fi
      myPLATFORMS=("linux/${myHOST}") ;;
    *) myPLATFORMS=("linux/${myARCH}") ;;
  esac
  myFOREIGN=()
  for myA in "${myPLATFORMS[@]}"; do
    [ "${myA#linux/}" = "${myHOST}" ] || myFOREIGN+=("${myA#linux/}")
  done
  return 0
}

fuPUSHING () { [ -n "${myPUSH_HUB}${myPUSH_GHCR}" ]; }
fuLIMITED () { fuPUSHING && [ "${myLIMIT}" != "off" ]; }
fuONE_REGISTRY () { fuPUSHING && [ "${myPUSH_HUB}" != "${myPUSH_GHCR}" ]; }
fuOVERRIDE_NEEDED () { [ "${myARCH}" != "both" ] || fuONE_REGISTRY; }
fuHOST_ONLY () { [ "${#myPLATFORMS[@]}" -eq 1 ] && [ "${myPLATFORMS[0]}" = "linux/${myHOST}" ]; }
# a release tag holds the images of linux/amd64 and linux/arm64 in one manifest, a push
# of one platform over it replaces them with that one: it needs a tag of its own,
# neither none nor -t with a release version. Any plain version is one (fuPLAIN_VERSION):
# an older release, the version of a checkout ahead of the last release
fuPLAIN_VERSION () { [[ "$1" =~ ^v?[0-9]+(\.[0-9]+)*$ ]]; }
fuTAG_NEEDED () {
  fuPUSHING && [ "${myARCH}" != "both" ] || return 1
  [ -z "${myTAG}" ] || [ "${myTAG}" = "$(fuRELEASE)" ] || fuPLAIN_VERSION "${myTAG}"
}

fuARCH_NAME () {
  # the architecture of a one-platform run, for a tag of its own
  case "${myARCH}" in
    host) fuHOST_ARCH || echo "host" ;;
    *) echo "${myARCH}" ;;
  esac
}

fuARCH_SOURCE () {
  # where the platforms of a one-platform run come from: -a, the menu, else the setting
  # TPOT_BUILDER_ARCH and where that is
  if [ -n "${myOPT_ARCH}" ]; then echo "-a ${myARCH}"; return 0; fi
  if [ -n "${myMENU_ARCH}" ]; then echo "${myPLATFORMS[*]} of the menu"; return 0; fi
  case "${myFROM_BUILDER_ARCH}" in
    environment) echo "TPOT_BUILDER_ARCH=${myARCH} of the environment" ;;
    *) echo "TPOT_BUILDER_ARCH=${myARCH} of ${myFROM_BUILDER_ARCH}" ;;
  esac
}

fuTAG_REFUSED () {
  # rc 2 with a hint for a push of one platform over a release tag: without -t, or with
  # -t of the release version or any other plain version (fuTAG_NEEDED)
  local myREL
  fuTAG_NEEDED || return 0
  myREL=$(fuRELEASE)
  if [ -z "${myTAG}" ]; then
    fuUI_ERROR "A push for one platform ($(fuARCH_SOURCE)) over the release tag ${myVER} would replace its multi-arch images (linux/amd64 and linux/arm64)."
  elif [ "${myTAG}" = "${myREL}" ]; then
    fuUI_ERROR "A push for one platform ($(fuARCH_SOURCE)) with -t ${myTAG}, the release tag, would replace its multi-arch images (linux/amd64 and linux/arm64)."
  else
    fuUI_ERROR "A push for one platform ($(fuARCH_SOURCE)) with -t ${myTAG}, a release version, would replace the multi-arch images (linux/amd64 and linux/arm64) of that release."
  fi
  fuUI_HINT "A one-platform push needs a tag of its own, no plain version: i.e. -t ${myTAG:-${myREL}}-$(fuARCH_NAME)" \
            "or build both platforms (-a both), or without a push; ${0##*/} -h shows the options." >&2
  return 2
}

fuIMAGE_REF () {
  # fuIMAGE_REF <image>: the name the image gets (GHCR when only GHCR is pushed to)
  if [ -n "${myPUSH_GHCR}" ] && [ -z "${myPUSH_HUB}" ];
    then echo "${myGHCR}/$1:${myVER}"
    else echo "${myHUB}/$1:${myVER}"
  fi
}

fuREGISTRY () {
  # fuREGISTRY <repository>: its registry for docker login, empty for Docker Hub
  local myFIRST="${1%%/*}"
  if [ "${myFIRST}" != "$1" ] && [[ "${myFIRST}" == *[.:]* || "${myFIRST}" = "localhost" ]];
    then echo "${myFIRST}"
  fi
}

fuTIMEOUT () {
  # fuTIMEOUT <seconds> <command> ...: the command, ended after that time (timeout,
  # else a watchdog in bash); rc 124 when it took too long
  local mySECS="$1" myPID myWATCH myRC=0
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "${mySECS}" "$@"
    return $?
  fi
  "$@" &
  myPID=$!
  ( sleep "${mySECS}"; kill "${myPID}" ) >/dev/null 2>&1 &
  myWATCH=$!
  wait "${myPID}" || myRC=$?
  if kill -0 "${myWATCH}" 2>/dev/null;
    then kill "${myWATCH}" 2>/dev/null; wait "${myWATCH}" 2>/dev/null
    else [ "${myRC}" -eq 0 ] || myRC=124
  fi
  return "${myRC}"
}

fuREGISTRY_READY () {
  # fuREGISTRY_READY <registry>: 0 when the login there works, never asks (stdin is
  # /dev/null) and never waits longer than myLOGIN_TIMEOUT
  if [ -n "$1" ];
    then fuTIMEOUT "${myLOGIN_TIMEOUT}" docker login "$1" < /dev/null > /dev/null 2>&1
    else fuTIMEOUT "${myLOGIN_TIMEOUT}" docker login < /dev/null > /dev/null 2>&1
  fi
}

fuTARGETS () {
  # the registries to push to, one per line: <name>|<registry>
  [ -z "${myPUSH_HUB}" ] || echo "Docker Hub (${myHUB})|$(fuREGISTRY "${myHUB}")"
  [ -z "${myPUSH_GHCR}" ] || echo "GHCR (${myGHCR})|$(fuREGISTRY "${myGHCR}")"
}

fuLOGINS () {
  # the logins for pushing, without asking: rc 3 and a hint without one
  local myNAME myREG myRC=0
  while IFS='|' read -r myNAME myREG; do
    [ -n "${myNAME}" ] || continue
    if fuREGISTRY_READY "${myREG}";
      then fuUI_OK "Logged in to ${myNAME}"
      else
        fuUI_ERROR "Not logged in to ${myNAME}, or docker login did not answer within ${myLOGIN_TIMEOUT} s."
        fuUI_HINT "Log in first as the user who runs the builder (root with sudo):" \
                  "docker login${myREG:+ ${myREG}}" >&2
        myRC=3
    fi
  done < <(fuTARGETS)
  return "${myRC}"
}

fuROOT () { [ "$(id -u 2>/dev/null)" = "0" ]; }

fuDOCKER_ENDPOINT () {
  # the daemon the docker of this user talks to: DOCKER_HOST, else the endpoint of the
  # context in use (DOCKER_CONTEXT, docker context use); empty where docker does not say.
  # docker context reads the files of this user, it asks no daemon
  if [ -n "${DOCKER_HOST:-}" ]; then echo "${DOCKER_HOST}"; return 0; fi
  docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null | head -n 1
}

fuROOTLESS_HOST () {
  # fuROOTLESS_HOST [endpoint]: the endpoint (fuDOCKER_ENDPOINT) is the socket of a
  # rootless Docker, in /run/user/<uid> or in the XDG_RUNTIME_DIR of this user (asks no
  # Docker, also for one that does not answer)
  local myURL
  if [ "$#" -gt 0 ]; then myURL="$1"; else myURL=$(fuDOCKER_ENDPOINT); fi
  case "${myURL}" in unix:///run/user/*) return 0 ;; esac
  [ -n "${XDG_RUNTIME_DIR:-}" ] && [[ "${myURL}" == "unix://${XDG_RUNTIME_DIR%/}/"* ]]
}

fuROOTLESS () {
  # this user talks to a rootless Docker (its endpoint, or docker info says
  # name=rootless); root never does
  fuROOT && return 1
  fuROOTLESS_HOST && return 0
  docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q 'name=rootless'
}

fuOTHER_DOCKER () {
  # fuOTHER_DOCKER [down]: this user talks to another Docker than root under sudo, which
  # drops DOCKER_HOST and DOCKER_CONTEXT, has contexts of its own and so talks to the
  # Docker of the system: rootless Docker, Docker Desktop, any context or DOCKER_HOST but
  # the socket of the system. What it is into myOTHER_DOCKER, myOTHER_REMOTE=1 for one of
  # another host (tcp://, ssh://: it pushes from there); root never does. down: a Docker
  # that does not answer, docker info is not asked
  local myURL myNAME=""
  myOTHER_DOCKER="" myOTHER_REMOTE=""
  fuROOT && return 1
  myURL=$(fuDOCKER_ENDPOINT)
  [ -n "${DOCKER_HOST:-}" ] || myNAME="${DOCKER_CONTEXT:-$(docker context show 2>/dev/null)}"
  if fuROOTLESS_HOST "${myURL}" || \
     { [ -z "${1:-}" ] && docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q 'name=rootless'; }; then
    myOTHER_DOCKER="rootless Docker"
    return 0
  fi
  case "${myURL}" in
    unix:///var/run/docker.sock|unix:///run/docker.sock) return 1 ;;
    # docker says no endpoint: the name of the context decides
    "") [ -n "${myNAME}" ] && [ "${myNAME}" != "default" ] || return 1 ;;
  esac
  if [ -n "${DOCKER_HOST:-}" ];
    then myOTHER_DOCKER="DOCKER_HOST=${DOCKER_HOST}"
    else myOTHER_DOCKER="the Docker context ${myNAME:-in use}"
  fi
  case "${myURL}" in ""|unix://*|npipe://*|fd://*) ;; *) myOTHER_REMOTE=1 ;; esac
  return 0
}

fuRIGHTS () {
  # root for the upload limit while pushing (tc), the one thing that needs it; the rest
  # needs a Docker that answers this user (fuDOCKER_READY)
  fuROOT && return 0
  fuUI_ERROR "The upload limit while pushing (tc) needs root."
  if fuOTHER_DOCKER; then
    local myWHERE="this host"
    # a Docker of another host pushes from there
    [ -z "${myOTHER_REMOTE}" ] || myWHERE="the host of that Docker"
    fuUI_HINT "With ${myOTHER_DOCKER} root talks to another Docker (its builder, cache and login):" \
              "push without a limit, -l off (for every run: ${0##*/} --set TPOT_BUILDER_LIMIT=off)," \
              "or limit the upload of ${myWHERE} yourself." >&2
  else
    fuUI_HINT "Run the builder with sudo, or push without a limit: -l off" \
              "(for every run: ${0##*/} --set TPOT_BUILDER_LIMIT=off)" >&2
  fi
  return 3
}

fuDOCKER_READY () {
  # docker, buildx and compose, and a Docker that answers this user: root, the docker
  # group or rootless Docker (DOCKER_HOST), Docker decides
  if ! command -v docker >/dev/null 2>&1; then
    fuUI_ERROR "Docker is not installed."
    fuUI_HINT "The Docker packages of https://get.docker.com/ bring buildx and compose." >&2
    return 3
  fi
  if ! docker info >/dev/null 2>&1; then
    fuUI_ERROR "Docker does not answer: it does not run, or this user may not use it."
    if fuOTHER_DOCKER down && [ "${myOTHER_DOCKER}" = "rootless Docker" ]; then
      fuUI_HINT "Start rootless Docker: systemctl --user start docker" >&2
    elif [ -n "${myOTHER_DOCKER}" ] && [ -n "${DOCKER_HOST:-}" ]; then
      fuUI_HINT "Start the Docker of ${myOTHER_DOCKER}," \
                "or unset DOCKER_HOST for the Docker of the system." >&2
    elif [ -n "${myOTHER_DOCKER}" ]; then
      fuUI_HINT "Start the Docker of ${myOTHER_DOCKER} (Docker Desktop: the app)," \
                "or take the Docker of the system: docker context use default" >&2
    elif ! fuROOT && [[ " $(id -nG 2>/dev/null) " == *" docker "* ]]; then
      # a member of the docker group may use it: it does not run
      fuUI_HINT "Start it: sudo systemctl start docker" >&2
    elif ! fuROOT; then
      fuUI_HINT "Run the builder with sudo," \
                "or join the docker group and log in again: sudo usermod -aG docker $(printf '%q' "$(id -un 2>/dev/null)")," \
                "or use rootless Docker (https://docs.docker.com/engine/security/rootless/)." >&2
    elif [[ "${SUDO_UID:-}" =~ ^[0-9]+$ ]] && [ "$((10#${SUDO_UID}))" -ne 0 ]; then
      fuUI_HINT "Start it: sudo systemctl start docker" \
                "With rootless Docker or Docker Desktop sudo talks to another Docker (the one of root):" \
                "run the builder as your own user, without the upload limit (-l off), which needs root." >&2
    else
      fuUI_HINT "Start it: systemctl start docker" >&2
    fi
    return 3
  fi
  if ! docker buildx version >/dev/null 2>&1; then
    fuUI_ERROR "docker buildx is missing (docker-buildx-plugin)."
    return 3
  fi
  if ! docker compose version >/dev/null 2>&1; then
    fuUI_ERROR "docker compose is missing (docker-compose-plugin)."
    return 3
  fi
  return 0
}

fuLOAD_BUILD () {
  # the smoke tests need a build for this host loaded into docker: the run builds more
  # than this host's platform, or pushes (a pushed build is not loaded)
  ! fuHOST_ONLY || fuPUSHING
}

fuLOAD_NEEDED () {
  # this run makes that build (with an override, fuWRITE_OVERRIDE load): -T, a host of a
  # known architecture, a smoke test for an image of the selection, and fuLOAD_BUILD
  [ -n "${myTEST}" ] && [ -n "${myHOST}" ] && fuLOAD_BUILD || return 1
  fuSMOKE_PLAN "${myLIST[@]}"
  [ "${#mySMOKE_NAMES[@]}" -gt 0 ]
}

fuCOMPOSE_RECENT () {
  # compose of at least myCOMPOSE_MIN for the overrides of this run; the error names
  # what of this run needs it
  local myV myI myTEXT="" myNEED="needs"
  local -a myWHY=()
  myV=$(docker compose version --short 2>/dev/null)
  fuUI_VERSION_GE "${myV}" "${myCOMPOSE_MIN}" && return 0
  [ "${myARCH}" = "both" ] || myWHY+=("$(fuARCH_SOURCE)")
  if fuONE_REGISTRY; then myWHY+=("the push to one registry"); fi
  if fuLOAD_NEEDED; then myWHY+=("the smoke test build (-T)"); fi
  for myI in "${!myWHY[@]}"; do
    if [ "${myI}" -eq 0 ]; then myTEXT="${myWHY[0]}"
    elif [ "${myI}" -eq $((${#myWHY[@]} - 1)) ]; then myTEXT="${myTEXT} and ${myWHY[myI]}" myNEED="need"
    else myTEXT="${myTEXT}, ${myWHY[myI]}"
    fi
  done
  fuUI_ERROR "docker compose ${myV:-of an unknown version} is too old: ${myTEXT} ${myNEED} ${myCOMPOSE_MIN}."
  fuUI_HINT "Update the Docker packages (docker-compose-plugin), i.e. those of https://get.docker.com/" >&2
  return 3
}

fuSELECTION () {
  # myLIST: the images of -i and -g, every image of the compose file without them (or
  # with -g all), sorted
  local myITEM myGROUP
  local -a myALL=()
  myLIST=()
  if [ -z "${myIMAGES}${myGROUPS}" ] || [[ ",${myGROUPS}," == *",all,"* ]]; then
    if ! mapfile -t myLIST < <(docker compose "${myFILES[@]}" config --services 2>>"${myLOG}" | sort -u) \
       || [ "${#myLIST[@]}" -eq 0 ]; then
      fuUI_ERROR "docker compose lists no images in ${myCOMPOSE}, see ${myLOG}."
      return 3
    fi
    return 0
  fi
  for myGROUP in ${myGROUPS//,/ }; do
    for myITEM in $(fuGROUP_IMAGES "${myGROUP}"); do myALL+=("${myITEM}"); done
  done
  for myITEM in ${myIMAGES//,/ }; do myALL+=("${myITEM}"); done
  mapfile -t myLIST < <(printf '%s\n' "${myALL[@]}" | sort -u)
  return 0
}

fuLOGDIR () {
  # the folder of the logs, builder.log emptied
  { mkdir -p "${myLOGDIR}" && : > "${myLOG}"; } 2>/dev/null && return 0
  fuUI_ERROR "Cannot write the logs to ${myLOGDIR}."
  fuUI_HINT "A run with sudo may have left it to root:" \
            "sudo chown -R $(printf '%q:%q' "$(id -un 2>/dev/null)" "$(id -gn 2>/dev/null)") $(printf '%q' "${myLOGDIR}")" >&2
  return 3
}

fuREAL_PATH () {
  # fuREAL_PATH <path>: the path without a symlink in it (a folder, or a file in a
  # folder; a file that is a link itself has none), rc 1 when it cannot be resolved
  local myNAME myPARENT
  if [ -d "$1" ]; then
    (CDPATH="" cd -P -- "$1" 2>/dev/null && pwd -P)
    return $?
  fi
  [ ! -L "$1" ] || return 1
  myNAME=$(basename -- "$1")
  myPARENT=$(CDPATH="" cd -P -- "$(dirname -- "$1")" 2>/dev/null && pwd -P) || return 1
  echo "${myPARENT%/}/${myNAME}"
}

fuGIVE_BACK () {
  # fuGIVE_BACK <path>: what this run wrote there as root under sudo goes back to the
  # user of sudo (SUDO_UID:SUDO_GID), recursively, symlinks themselves and never what
  # they point to (chown -R -h). Only for root with a numeric SUDO_UID other than 0 and
  # a SUDO_GID, and a path that is there. A symlink anywhere in the path (also the last
  # part, with a slash after it or not) is resolved first: the real folder goes back
  # when its real parent belongs to that user (a checkout or a disk of theirs), never
  # one in /tmp, in a folder of another user or behind a link to one
  local myREAL
  fuROOT || return 0
  [[ "${SUDO_UID:-}" =~ ^[0-9]+$ && "${SUDO_GID:-}" =~ ^[0-9]+$ ]] || return 0
  [ "$((10#${SUDO_UID}))" -ne 0 ] || return 0
  [ -e "$1" ] || return 0
  myREAL=$(fuREAL_PATH "$1") && [ -n "${myREAL}" ] && [ "${myREAL}" != "/" ] || return 0
  [ -n "$(find "$(dirname -- "${myREAL}")" -maxdepth 0 -user "${SUDO_UID}" 2>/dev/null)" ] || return 0
  chown -R -h "${SUDO_UID}:${SUDO_GID}" -- "${myREAL}" 2>/dev/null || true
}

fuEXIT () {
  # the end of every run (EXIT, fuCANCEL), once: the upload limit of this run goes, the
  # logs go back to the user of sudo. A signal from here on is ignored, so that this is
  # never left half done (the run ends anyway)
  [ -z "${myEXIT_DONE}" ] || return 0
  myEXIT_DONE=1
  trap '' INT TERM
  fuLIMIT_OFF
  fuGIVE_BACK "${myLOGDIR}"
}

fuHAS_PLATFORMS () {
  # fuHAS_PLATFORMS <platforms of buildx inspect> <platform> ...: rc 0 when each is an
  # entry of the list as a whole (linux/amd64/v2 is not linux/amd64; BuildKit marks the
  # native ones with *). --check, the run and the status line of the menu read it so
  local myLINE="$1" myP
  shift
  myLINE=",${myLINE//[[:space:]*]/},"
  for myP in "$@"; do [[ "${myLINE}" == *",${myP},"* ]] || return 1; done
  return 0
}

fuENSURE_BUILDER () {
  # the buildx builder mybuilder, created and started where it is missing
  docker buildx inspect "${myBUILDER}" --bootstrap && return 0
  echo "Creating and starting the buildx builder '${myBUILDER}'"
  docker buildx create --name "${myBUILDER}" --driver docker-container --use && \
    docker buildx inspect "${myBUILDER}" --bootstrap
}

fuENSURE_PLATFORMS () {
  # fuENSURE_PLATFORMS <platform> ...: mybuilder builds them; BuildKit finds the QEMU
  # emulators only when it starts, so a builder started before they were registered
  # is restarted (creating it again fails, it exists)
  local myACTIVE
  myACTIVE=$(docker buildx inspect "${myBUILDER}" --bootstrap | sed -n 's/.*Platforms: *//p')
  echo "Platforms: ${myACTIVE}"
  fuHAS_PLATFORMS "${myACTIVE}" "$@" && return 0
  echo "Restarting '${myBUILDER}' for $*"
  docker buildx stop "${myBUILDER}" && docker buildx inspect "${myBUILDER}" --bootstrap || return 1
  myACTIVE=$(docker buildx inspect "${myBUILDER}" | sed -n 's/.*Platforms: *//p')
  echo "Platforms: ${myACTIVE}"
  fuHAS_PLATFORMS "${myACTIVE}" "$@" && return 0
  echo "'${myBUILDER}' does not build $*"
  return 1
}

fuHANDLER () { case "$1" in amd64) echo "qemu-x86_64" ;; arm64) echo "qemu-aarch64" ;; esac; }

fuBINFMT_HAS () {
  # fuBINFMT_HAS <arch> ...: the QEMU handlers of binfmt_misc for them are there and
  # enabled; where binfmt_misc is not mounted (no register file) the platforms of the
  # builder decide
  local myA myFILE myFIRST=""
  [ -e "${myBINFMT}/register" ] || return 0
  for myA in "$@"; do
    myFILE="${myBINFMT}/$(fuHANDLER "${myA}")"
    myFIRST=""
    [ -r "${myFILE}" ] && { IFS= read -r myFIRST < "${myFILE}" || true; }
    if [ "${myFIRST}" != "enabled" ]; then
      echo "No enabled binfmt handler ${myFILE} for linux/${myA}"
      return 1
    fi
  done
  return 0
}

fuQEMU () {
  # fuQEMU <arch> ...: QEMU emulation for the architectures, checked afterwards
  docker run --rm --privileged tonistiigi/binfmt --install all || return 1
  fuBINFMT_HAS "$@"
}

fuSPIN () {
  # fuUI_SPIN, but a Ctrl+C under the spinner (gum takes it as a key, rc 130) cancels the run
  local myRC=0
  fuUI_SPIN "$@" || myRC=$?
  [ "${myRC}" -eq 130 ] && fuCANCEL
  return "${myRC}"
}

fuPREPARE () {
  # fuPREPARE <platform> ...: builder, QEMU for the ones of another architecture, platforms
  local myP
  local -a myARCHS=()
  for myP in "$@"; do [ "${myP#linux/}" = "${myHOST}" ] || myARCHS+=("${myP#linux/}"); done
  fuSPIN "Checking the buildx builder '${myBUILDER}' ..." "${myLOG}" fuENSURE_BUILDER || return 3
  if [ "${#myARCHS[@]}" -gt 0 ] && ! fuSPIN "Configuring QEMU for ${myARCHS[*]} ..." "${myLOG}" fuQEMU "${myARCHS[@]}"; then
    # binfmt_misc belongs to the host: a privileged container registers it only with a
    # Docker that runs as root, never with rootless Docker; root and the docker group
    # have that one already, the log says what went wrong
    if fuROOTLESS;
      then fuUI_HINT "QEMU needs Docker running as root (sudo), rootless Docker cannot register it; see ${myLOG}." >&2
      else fuUI_HINT "QEMU could not be set up, see ${myLOG}." >&2
    fi
    return 3
  fi
  fuSPIN "Making sure '${myBUILDER}' builds $* ..." "${myLOG}" fuENSURE_PLATFORMS "$@" || return 3
  return 0
}

fuWRITE_OVERRIDE () {
  # fuWRITE_OVERRIDE <file> <main|load> <image> ...: the compose override of this run:
  # main the platforms of -a and the registry of --push-hub / --push-ghcr, load this
  # host's platform and one name for the smoke tests (needs compose 2.24.4)
  local myFILE="$1" myMODE="$2" myS
  shift 2
  {
    printf '%s\n' "# generated by builder.sh for one run, do not edit (a compose override)"
    echo "services:"
    for myS in "$@"; do
      echo "  ${myS}:"
      if [ "${myMODE}" = "load" ];
        then echo "    image: $(fuIMAGE_REF "${myS}")"
        elif fuONE_REGISTRY && [ -n "${myPUSH_GHCR}" ]; then echo "    image: ${myGHCR}/${myS}:${myVER}"
      fi
      echo "    build:"
      if [ "${myMODE}" = "load" ]; then
        printf '      platforms: !override\n        - linux/%s\n' "${myHOST}"
      elif [ "${myARCH}" != "both" ]; then
        echo "      platforms: !override"
        printf '        - %s\n' "${myPLATFORMS[@]}"
      fi
      if [ "${myMODE}" = "load" ] || fuONE_REGISTRY; then echo "      tags: !reset []"; fi
    done
  } > "${myFILE}"
}

fuLIMIT_ON () {
  # the upload limit of this run (tc tbf) on the interface of the default route; one
  # that is there already is not this run's: stop, it stays
  myIF=$(ip route 2>/dev/null | awk '/^default/ { for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit } }')
  if [ -z "${myIF}" ]; then
    fuUI_ERROR "No default route, so no interface for the upload limit."
    fuUI_HINT "Push without a limit: -l off" >&2
    return 3
  fi
  if tc qdisc show dev "${myIF}" root 2>/dev/null | grep -q "tbf"; then
    fuUI_ERROR "${myIF} has an upload limit (tbf) already, not one of this run, it stays."
    fuUI_HINT "Remove it: sudo tc qdisc del dev ${myIF} root" "or push without a limit: -l off" >&2
    return 3
  fi
  if ! tc qdisc add dev "${myIF}" root tbf rate "${myLIMIT}" burst 32kbit latency 400ms >>"${myLOG}" 2>&1; then
    fuUI_ERROR "Could not limit the upload on ${myIF} to ${myLIMIT}, see ${myLOG}."
    fuUI_HINT "Push without a limit: -l off" >&2
    return 3
  fi
  myLIMIT_SET=1
  fuUI_OK "Upload limited to ${myLIMIT} on ${myIF}"
  return 0
}

fuLIMIT_OFF () {
  # removes the limit of this run, only that one
  [ -n "${myLIMIT_SET}" ] || return 0
  myLIMIT_SET=""
  if tc qdisc del dev "${myIF}" root >/dev/null 2>&1;
    then fuUI_OK "Upload limit on ${myIF} removed"
    else
      fuUI_ERROR "Could not remove the upload limit on ${myIF}."
      fuUI_HINT "sudo tc qdisc del dev ${myIF} root" >&2
  fi
  return 0
}

fuSMOKE_PLAN () {
  # fuSMOKE_PLAN <image> ...: the smoke tests of docker/_tests for built images into
  # mySMOKE_NAMES / mySMOKE_ARGS (one string each), the images they need into
  # mySMOKE_IMAGES and what has none into mySMOKE_NOTES. A test has the name of its
  # image, tpotinit has tpotinit_env, the Tanner stack one for all four (only when all
  # four are built); the Elastic Stack, the Attack Map, nginx and glutton have none
  local myI myT myTANNER=""
  mySMOKE_NAMES=() mySMOKE_ARGS=() mySMOKE_IMAGES=() mySMOKE_NOTES=()
  for myI in "$@"; do
    case "${myI}" in
      elasticsearch|kibana|logstash|map|nginx|glutton) continue ;;
      redis|phpox|tanner|snare) myTANNER="${myTANNER} ${myI}"; continue ;;
      tpotinit) myT="tpotinit_env" ;;
      *) myT="${myI}" ;;
    esac
    if [ -f "${myTESTDIR}/${myT}.sh" ];
      then
        mySMOKE_NAMES+=("${myT}")
        mySMOKE_ARGS+=("--image $(fuIMAGE_REF "${myI}")")
        mySMOKE_IMAGES+=("${myI}")
      else mySMOKE_NOTES+=("No smoke test for ${myI} (${myTESTDIR}/${myT}.sh)")
    fi
  done
  [ -n "${myTANNER}" ] || return 0
  for myI in redis phpox tanner snare; do
    if [[ " ${myTANNER} " != *" ${myI} "* ]]; then
      mySMOKE_NOTES+=("The Tanner test needs redis, phpox, tanner and snare built together, it is left out")
      return 0
    fi
  done
  if [ -f "${myTESTDIR}/tanner.sh" ];
    then
      mySMOKE_NAMES+=("tanner")
      mySMOKE_ARGS+=("--redis-image $(fuIMAGE_REF redis) --phpox-image $(fuIMAGE_REF phpox) --tanner-image $(fuIMAGE_REF tanner) --snare-image $(fuIMAGE_REF snare)")
      mySMOKE_IMAGES+=(redis phpox tanner snare)
    else mySMOKE_NOTES+=("No smoke test for the Tanner stack (${myTESTDIR}/tanner.sh)")
  fi
  return 0
}

fuSMOKE_TESTS () {
  # fuSMOKE_TESTS <image> ...: a build for this host loaded into docker where the build
  # did not load one (more platforms or pushed), then the tests; rc 4 when one fails
  local myI myRC=0 myLOAD="${myLOGDIR}/override-load.yml" myOWN=""
  local -a myARGS=() myCACHE=()
  fuSMOKE_PLAN "$@"
  for myI in "${mySMOKE_NOTES[@]}"; do mySUMMARY+=("warn:${myI}"); done
  [ "${#mySMOKE_NAMES[@]}" -gt 0 ] || return 0
  if [ -z "${myHOST}" ]; then
    mySUMMARY+=("fail:No smoke tests, this host is neither amd64 nor arm64")
    return 4
  fi
  if fuLOAD_BUILD; then
    # compose loads the build of one platform into docker by itself (there is no
    # --load). Where this run built this host's platform (both, or pushed), the build
    # takes the cache on purpose, also after -n: the images this run just built come
    # out of it again. -a arm64 on amd64 (or the other way round) built none, so this
    # is a build of its own, after -n one without the cache as well
    [[ " ${myPLATFORMS[*]} " == *" linux/${myHOST} "* ]] || myOWN=1
    [ -z "${myOWN}" ] || [ -z "${myNO_CACHE}" ] || myCACHE=(--no-cache)
    fuWRITE_OVERRIDE "${myLOAD}" load "${mySMOKE_IMAGES[@]}"
    : > "${myLOGDIR}/load.log"
    if ! fuSPIN "Building ${mySMOKE_IMAGES[*]} for linux/${myHOST} into docker for the smoke tests ..." \
           "${myLOGDIR}/load.log" docker compose "${myFILES[@]}" -f "${myLOAD}" --progress plain build \
           "${mySMOKE_IMAGES[@]}" --builder "${myBUILDER}" "${myCACHE[@]}"; then
      mySUMMARY+=("fail:The build for the smoke tests, see ${myLOGDIR}/load.log")
      return 4
    fi
    # what the tests run is this host's build, not the images of this run
    if [ -n "${myOWN}" ]; then
      mySUMMARY+=("warn:The smoke tests tested a linux/${myHOST} build, not the ${myPLATFORMS[*]} images of this run")
    fi
  fi
  for myI in "${!mySMOKE_NAMES[@]}"; do
    read -r -a myARGS <<< "${mySMOKE_ARGS[myI]}"
    : > "${myLOGDIR}/test-${mySMOKE_NAMES[myI]}.log"
    if fuSPIN "Smoke test ${mySMOKE_NAMES[myI]} ..." "${myLOGDIR}/test-${mySMOKE_NAMES[myI]}.log" \
         bash "${myTESTDIR}/${mySMOKE_NAMES[myI]}.sh" "${myARGS[@]}";
      then mySUMMARY+=("ok:Smoke test ${mySMOKE_NAMES[myI]}")
      else mySUMMARY+=("fail:Smoke test ${mySMOKE_NAMES[myI]}, see ${myLOGDIR}/test-${mySMOKE_NAMES[myI]}.log"); myRC=4
    fi
  done
  return "${myRC}"
}

fuDESCRIBE () {
  # one line about the run: platforms, builds at a time, push, cache, version
  local myTEXT
  myTEXT="${myPLATFORMS[*]}, ${myJOBS} builds at a time"
  if [ -n "${myPUSH_HUB}" ] && [ -n "${myPUSH_GHCR}" ]; then myTEXT="${myTEXT}, pushed to Docker Hub and GHCR"
  elif [ -n "${myPUSH_HUB}" ]; then myTEXT="${myTEXT}, pushed to Docker Hub"
  elif [ -n "${myPUSH_GHCR}" ]; then myTEXT="${myTEXT}, pushed to GHCR"
  fi
  if fuLIMITED; then myTEXT="${myTEXT} (upload ${myLIMIT})"; fi
  [ -z "${myNO_CACHE}" ] || myTEXT="${myTEXT}, without cache"
  [ -z "${myTEST}" ] || myTEXT="${myTEXT}, smoke tests"
  echo "${myTEXT}"
}

fuCOMMAND_LINE () {
  # the same run without the menu, also elsewhere (cron, sudo without the environment):
  # what .env.local, .env or the built-in default say goes without saying (myFILES_*),
  # a value of the environment or of the menu is an option
  local -a myC=()
  ! fuROOT || [ -z "${SUDO_USER:-}" ] || myC+=(sudo)
  myC+=("${mySELF}" -y)
  [ -z "${myGROUPS}" ] || myC+=(-g "${myGROUPS}")
  [ -z "${myIMAGES}" ] || myC+=(-i "${myIMAGES}")
  [ "${myARCH}" = "${myFILES_BUILDER_ARCH:-${myDEFAULT_ARCH}}" ] || myC+=(-a "${myARCH}")
  if [ -n "${myPUSH_HUB}" ] && [ -n "${myPUSH_GHCR}" ]; then myC+=(-p)
  elif [ -n "${myPUSH_HUB}" ]; then myC+=(--push-hub)
  elif [ -n "${myPUSH_GHCR}" ]; then myC+=(--push-ghcr)
  fi
  if fuPUSHING && [ "${myLIMIT}" != "${myFILES_BUILDER_LIMIT:-${myDEFAULT_LIMIT}}" ]; then myC+=(-l "${myLIMIT}"); fi
  [ -z "${myNO_CACHE}" ] || myC+=(-n)
  [ "${myJOBS}" = "${myFILES_BUILDER_JOBS:-${myDEFAULT_JOBS}}" ] || myC+=(-j "${myJOBS}")
  [ -z "${myTEST}" ] || myC+=(-T)
  [ -z "${myTAG}" ] || myC+=(-t "${myTAG}")
  [ "${myHUB}" = "${myFILES_DOCKER_REPO:-${myDEFAULT_HUB}}" ] || myC+=(--docker-repo "${myHUB}")
  [ "${myGHCR}" = "${myFILES_GHCR_REPO:-${myDEFAULT_GHCR}}" ] || myC+=(--ghcr-repo "${myGHCR}")
  printf '%q ' "${myC[@]}" | sed 's/ $//'
}

fuRUN () {
  # one build run, the same from the menu and without it; the exit code of the table
  # in fuHELP
  local myRC=0 myRESULT myIMAGE myREPORTED=0 myFLAGS myOVERRIDE="${myLOGDIR}/override.yml"
  local -a myOK=() myFAILED=() myBUILT=()
  mySUMMARY=()
  fuSETTINGS
  fuTAG_REFUSED || return 2
  fuPLATFORMS || return 3
  if [ -z "${myMENU}" ];
    then fuUI_BANNER "Image Builder" "$(fuDESCRIBE)" "Version ${myVER}, ${myHUB} and ${myGHCR}"
    else fuUI_INFO "Building: $(fuDESCRIBE)"
  fi
  fuDOCKER_READY || return 3
  if fuLIMITED; then fuRIGHTS || return 3; fi
  if fuLIMITED && ! command -v tc >/dev/null 2>&1; then
    fuUI_ERROR "tc (iproute2) is missing for the upload limit."
    fuUI_HINT "Push without a limit: -l off" >&2
    return 3
  fi
  fuLOGDIR || return 3
  # the overrides of an earlier run go, this one writes those it needs
  rm -f "${myLOGDIR}"/override*.yml
  fuSELECTION || return 3
  if fuOVERRIDE_NEEDED || fuLOAD_NEEDED; then fuCOMPOSE_RECENT || return 3; fi
  if fuPUSHING && [ -z "${myMENU}" ]; then fuLOGINS || return 3; fi
  fuPREPARE "${myPLATFORMS[@]}" || return 3
  if fuOVERRIDE_NEEDED; then
    fuWRITE_OVERRIDE "${myOVERRIDE}" main "${myLIST[@]}"
    myFILES+=(-f "${myOVERRIDE}")
  fi
  if fuLIMITED; then fuLIMIT_ON || return 3; fi

  echo
  local myIMAGES="images"
  [ "${#myLIST[@]}" -eq 1 ] && myIMAGES="image"
  fuUI_INFO "Building ${#myLIST[@]} ${myIMAGES}, the log of each is ${myLOGDIR}/<image>.log ..."
  # Plain progress and both streams in the log: builds run in parallel, and with only
  # stdout redirected compose picks tty progress for a file and fails with "failed to
  # get console" (docker/compose#14182). The builds report one line each, this shell
  # shows them. The image right after build, so that a log shows which one it is.
  myFLAGS="--builder ${myBUILDER}"
  if fuPUSHING; then myFLAGS="${myFLAGS} --push"; fi
  [ -z "${myNO_CACHE}" ] || myFLAGS="${myFLAGS} --no-cache"
  export myB_COMPOSE myB_FLAGS="${myFLAGS}" myB_LOGDIR="${myLOGDIR}"
  myB_COMPOSE=$(printf '%q ' "${myFILES[@]}")
  # shellcheck disable=SC2016 # expanded by the bash of each build
  local myBUILD='
    echo "START $1"
    eval "myARGS=(${myB_COMPOSE})"
    if docker compose "${myARGS[@]}" --progress plain build "$1" ${myB_FLAGS} > "${myB_LOGDIR}/$1.log" 2>&1 < /dev/null;
      then echo "OK $1"
      else echo "FAIL $1"
    fi
  '
  while read -r myRESULT myIMAGE; do
    case "${myRESULT}" in
      START) fuUI_INFO "Building ${myIMAGE} ..." ;;
      OK) fuUI_OK "Image ${myIMAGE}"; myOK+=("${myIMAGE}"); myREPORTED=$((myREPORTED + 1)) ;;
      *) fuUI_ERROR "Image ${myIMAGE}, see ${myLOGDIR}/${myIMAGE}.log"; myFAILED+=("${myIMAGE}")
         myREPORTED=$((myREPORTED + 1)) ;;
    esac
  done < <(printf '%s\n' "${myLIST[@]}" | xargs -n 1 -P "${myJOBS}" bash -c "${myBUILD}" _)
  echo
  fuLIMIT_OFF

  mySUMMARY+=("info:Version ${myVER}, $(fuDESCRIBE)")
  if [ "${#myFAILED[@]}" -eq 0 ] && [ "${myREPORTED}" -eq "${#myLIST[@]}" ];
    then mySUMMARY+=("ok:${#myOK[@]} of ${#myLIST[@]} images built")
    else mySUMMARY+=("fail:${#myOK[@]} of ${#myLIST[@]} images built"); myRC=1
  fi
  for myIMAGE in "${myFAILED[@]}"; do mySUMMARY+=("fail:Image ${myIMAGE}, see ${myLOGDIR}/${myIMAGE}.log"); done
  if [ "${myREPORTED}" -ne "${#myLIST[@]}" ]; then
    fuUI_ERROR "Only ${myREPORTED} of ${#myLIST[@]} builds reported back, see above."
    mySUMMARY+=("fail:Only ${myREPORTED} of ${#myLIST[@]} builds reported back")
    myRC=1
  fi
  if [ -n "${myTEST}" ] && [ "${#myOK[@]}" -gt 0 ]; then
    # the built ones in the order of the selection, not in the one the parallel builds
    # ended in
    myBUILT=()
    for myIMAGE in "${myLIST[@]}"; do
      [[ " ${myOK[*]} " != *" ${myIMAGE} "* ]] || myBUILT+=("${myIMAGE}")
    done
    fuSMOKE_TESTS "${myBUILT[@]}" || { [ "${myRC}" -ne 0 ] || myRC=4; }
  fi
  if fuPUSHING;
    then [ "${myRC}" -eq 1 ] || mySUMMARY+=("ok:Pushed: $(fuTARGETS | cut -d '|' -f 1 | paste -sd ',' - | sed 's/,/, /g')")
    else mySUMMARY+=("next:Remember to push the images with -p.")
  fi
  [ "${myRC}" -eq 0 ] || mySUMMARY+=("next:The logs: ${myLOGDIR}")
  fuUI_SUMMARY "Image Builder" "${mySUMMARY[@]}" || true
  return "${myRC}"
}

fuSTATUS () {
  # one line about mybuilder for the menu (no bootstrap, nothing is started): the two
  # platforms T-Pot builds, how many more it has (QEMU brings a dozen), what it lacks
  local myOUT myPLAT mySTATE myP myTEXT myOTHER=0
  local -a myALL=() myHAS=() myLACKS=()
  if ! myOUT=$(docker buildx inspect "${myBUILDER}" 2>/dev/null); then
    echo "Builder '${myBUILDER}': not set up (Builder setup sets it up)"
    return 0
  fi
  myPLAT=$(sed -n 's/.*Platforms: *//p' <<< "${myOUT}" | head -n 1)
  mySTATE=$(sed -n 's/^ *Status: *//p' <<< "${myOUT}" | head -n 1)
  myTEXT="${mySTATE:-there}"
  if [ -n "${myPLAT}" ]; then
    IFS=, read -r -a myALL <<< "${myPLAT}"
    for myP in "${myALL[@]}"; do
      myP="${myP//[[:space:]*]/}"
      case "${myP}" in
        linux/amd64|linux/arm64|"") ;;
        *) myOTHER=$((myOTHER + 1)) ;;
      esac
    done
    for myP in linux/amd64 linux/arm64; do
      if fuHAS_PLATFORMS "${myPLAT}" "${myP}"; then myHAS+=("${myP}"); else myLACKS+=("${myP}"); fi
    done
    if [ "${#myHAS[@]}" -gt 0 ]; then
      myTEXT="${myTEXT}, builds ${myHAS[0]}${myHAS[1]:+ and ${myHAS[1]}}"
      [ "${myOTHER}" -eq 0 ] || myTEXT="${myTEXT} (+${myOTHER} more)"
    elif [ "${myOTHER}" -eq 1 ]; then myTEXT="${myTEXT}, 1 other platform"
    else myTEXT="${myTEXT}, ${myOTHER} other platforms"
    fi
    [ "${#myLACKS[@]}" -eq 0 ] || myTEXT="${myTEXT}, not ${myLACKS[0]}${myLACKS[1]:+ and ${myLACKS[1]}}"
  fi
  echo "Builder '${myBUILDER}': ${myTEXT}"
}

fuWHO () { id -un 2>/dev/null || echo "this user"; }

fuOTHER_BUILDER () {
  # fuOTHER_BUILDER <action>: the summary line about the builder of the other user.
  # buildx keeps its builders per user (BUILDX_CONFIG, else DOCKER_CONFIG/buildx, else
  # ~/.docker/buildx), so a run with sudo has one of root and a run without sudo one of
  # the user. A user cannot look into the store of root (/root is 0700): the line says
  # that it may be there. Root under sudo reads the store of the user of sudo and names
  # its builder where there is one, not where sudo kept the HOME of the user (one store)
  local myUSER="${SUDO_USER:-}" myHOME myTHEIRS myOWN myC
  myC=$(printf '%q' "${mySELF}")
  if ! fuROOT; then
    echo "info:A run with sudo has a builder '${myBUILDER}' of its own (root's), not handled here: sudo ${myC} $1"
    return 0
  fi
  [ -n "${myUSER}" ] && [ "${myUSER}" != "root" ] || return 0
  myHOME=$(getent passwd "${myUSER}" 2>/dev/null | cut -d: -f6)
  [ -n "${myHOME}" ] && [ -e "${myHOME}/.docker/buildx/instances/${myBUILDER}" ] || return 0
  myTHEIRS=$(fuREAL_PATH "${myHOME}/.docker/buildx") || myTHEIRS="${myHOME}/.docker/buildx"
  if [ -n "${BUILDX_CONFIG:-}" ]; then myOWN="${BUILDX_CONFIG}"; else myOWN="${DOCKER_CONFIG:-${HOME}/.docker}/buildx"; fi
  myOWN=$(fuREAL_PATH "${myOWN}") || true
  [ "${myTHEIRS}" != "${myOWN}" ] || return 0
  echo "info:${myUSER} has a builder '${myBUILDER}' of its own too (a run without sudo), not handled here: ${myC} $1 as ${myUSER}"
}

fuCHECK () {
  # --check: rc 0 when mybuilder builds linux/amd64 and linux/arm64
  local myOUT myPLAT myRC=0 myA myV
  local -a myITEMS=()
  fuUI_BANNER "Builder Setup" "Checking the buildx builder '${myBUILDER}'"
  # TPOT_BUILDER_ARCH below: not from an .env.local that is no text
  fuLOCAL_TEXT || return 3
  fuDOCKER_READY || return 3
  myHOST=$(fuHOST_ARCH) || myHOST=""
  if myOUT=$(docker buildx inspect "${myBUILDER}" --bootstrap 2>&1); then
    myPLAT=$(sed -n 's/.*Platforms: *//p' <<< "${myOUT}" | head -n 1)
    myITEMS+=("ok:Builder '${myBUILDER}' of $(fuWHO) runs")
    for myA in linux/amd64 linux/arm64; do
      if fuHAS_PLATFORMS "${myPLAT}" "${myA}";
        then myITEMS+=("ok:It builds ${myA}")
        else myITEMS+=("fail:It does not build ${myA} (--setup)"); myRC=3
      fi
    done
  else
    myITEMS+=("fail:No buildx builder '${myBUILDER}' of $(fuWHO) (--setup sets it up)")
    myRC=3
  fi
  for myA in amd64 arm64; do
    [ "${myA}" != "${myHOST}" ] || continue
    if [ ! -e "${myBINFMT}/register" ]; then
      myITEMS+=("info:binfmt_misc is not mounted at ${myBINFMT}, the platforms of the builder decide")
      break
    fi
    if fuBINFMT_HAS "${myA}" >/dev/null;
      then myITEMS+=("ok:QEMU for linux/${myA}")
      else myITEMS+=("fail:No QEMU handler for linux/${myA} in ${myBINFMT} (--setup)"); myRC=3
    fi
  done
  myV=$(docker compose version --short 2>/dev/null)
  # TPOT_BUILDER_ARCH of one platform: every run without -a needs the override
  myOUT=$(fuCONFIG_GET TPOT_BUILDER_ARCH)
  if fuUI_VERSION_GE "${myV}" "${myCOMPOSE_MIN}"; then myITEMS+=("ok:docker compose ${myV}")
  elif [ "${myOUT%%|*}" != "both" ] && fuSETTING_CHECK TPOT_BUILDER_ARCH "${myOUT%%|*}"; then
    # the setting for fuARCH_SOURCE, here only: not the platforms a build of the menu picked
    local myFROM_BUILDER_ARCH="${myOUT#*|}" myARCH="${myOUT%%|*}" myOPT_ARCH="" myMENU_ARCH=""
    myITEMS+=("fail:docker compose ${myV:-of an unknown version} is too old: $(fuARCH_SOURCE) needs ${myCOMPOSE_MIN} for every run without -a both")
    myRC=3
  else myITEMS+=("warn:docker compose ${myV:-of an unknown version}: -a amd64, arm64 or host, the push to one registry and the smoke tests (-T) need ${myCOMPOSE_MIN}")
  fi
  myOUT=$(fuOTHER_BUILDER --check)
  [ -z "${myOUT}" ] || myITEMS+=("${myOUT}")
  fuUI_SUMMARY "Builder '${myBUILDER}'" "${myITEMS[@]}" || true
  return "${myRC}"
}

fuSETUP () {
  # --setup: mybuilder and QEMU for linux/amd64 and linux/arm64
  local myC
  fuUI_BANNER "Builder Setup" "Setting up Docker for multi-arch builds." \
              "Requires the Docker packages of https://get.docker.com/"
  fuDOCKER_READY || return 3
  myHOST=$(fuHOST_ARCH) || myHOST=""
  fuLOGDIR || return 3
  if ! fuPREPARE linux/amd64 linux/arm64; then
    fuUI_SUMMARY "Builder Setup" "fail:The setup failed, see ${myLOG}" || true
    return 3
  fi
  myC=$(printf '%q' "${mySELF}")
  fuUI_SUMMARY "Builder Setup" "ok:'${myBUILDER}' builds linux/amd64 and linux/arm64" \
    "next:Build: ${myC} (a menu) or ${myC} -y" \
    "next:One image by hand: cd $(printf '%q' "${myDIR}") && docker compose build tpotinit" \
    "info:Segmentation faults in arm64 builds: ${myC} --uninstall, then ${myC} --setup" || true
  return 0
}

fuUNINSTALL () {
  # --uninstall: the builder, what is left of it, the QEMU emulation and the images used
  # for it; steps with nothing to remove are skipped. rc 3 when a step fails
  local myRC=0 myOTHER
  local -a myITEMS=() myIMAGES_LEFT=()
  fuUI_BANNER "Builder Setup" "Removing the multi-arch build setup"
  fuDOCKER_READY || return 3
  # buildx keeps its builders per user (root with sudo, a user of the docker group
  # without), so a builder may exist for the other one as well
  if docker buildx inspect "${myBUILDER}" >/dev/null 2>&1; then
    if docker buildx rm "${myBUILDER}" >/dev/null 2>&1;
      then myITEMS+=("ok:Removed the buildx builder '${myBUILDER}' of $(fuWHO)")
      else myITEMS+=("fail:Could not remove the buildx builder '${myBUILDER}' of $(fuWHO)"); myRC=3
    fi
  else
    myITEMS+=("info:No buildx builder '${myBUILDER}' of $(fuWHO), skipped")
  fi
  myOTHER=$(fuOTHER_BUILDER --uninstall)
  [ -z "${myOTHER}" ] || myITEMS+=("${myOTHER}")
  # the container and its state volume are shared by both
  docker rm -f buildx_buildkit_mybuilder0 >/dev/null 2>&1
  docker volume rm buildx_buildkit_mybuilder0_state >/dev/null 2>&1
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx buildx_buildkit_mybuilder0 || \
     docker volume ls --format '{{.Name}}' 2>/dev/null | grep -qx buildx_buildkit_mybuilder0_state;
    then myITEMS+=("fail:Could not remove the BuildKit container and volume that are left"); myRC=3
    else myITEMS+=("ok:No BuildKit container and volume left")
  fi
  # unregisters every qemu-* handler, not only the ones installed here
  fuUI_WARN "This removes all QEMU binfmt handlers on this host, those of i.e. qemu-user-static too."
  if compgen -G "${myBINFMT}/qemu-*" >/dev/null; then
    if docker run --rm --privileged tonistiigi/binfmt --uninstall 'qemu-*' >/dev/null 2>&1 && \
       ! compgen -G "${myBINFMT}/qemu-*" >/dev/null;
      then myITEMS+=("ok:Removed the QEMU emulation for cross-platform builds")
      else myITEMS+=("fail:Could not remove the QEMU emulation for cross-platform builds"); myRC=3
    fi
  else
    myITEMS+=("info:No QEMU emulation registered, skipped")
  fi
  mapfile -t myIMAGES_LEFT < <(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | \
                                 grep -E '^(moby/buildkit|tonistiigi/binfmt):')
  if [ "${#myIMAGES_LEFT[@]}" -eq 0 ]; then
    myITEMS+=("info:No images moby/buildkit and tonistiigi/binfmt, skipped")
  elif docker rmi "${myIMAGES_LEFT[@]}" >/dev/null 2>&1; then
    myITEMS+=("ok:Removed the images moby/buildkit and tonistiigi/binfmt")
  else
    myITEMS+=("fail:Could not remove the images moby/buildkit and tonistiigi/binfmt"); myRC=3
  fi
  fuUI_SUMMARY "Builder Setup" "${myITEMS[@]}" || true
  return "${myRC}"
}

fuASK () {
  # fuASK <variable> <fuUI_CHOOSE | fuUI_CHOOSE_MANY | fuUI_INPUT> <argument> ...: the
  # answer into the variable; a cancelled question (gum, end of input) ends the builder
  local myASK_VAR="$1" myASK_OUT
  shift
  myASK_OUT=$("$@") || exit 130
  printf -v "${myASK_VAR}" '%s' "${myASK_OUT}"
}

fuYES () {
  # fuYES [--default yes|no] <question> [yes] [no]: fuUI_CONFIRM, 0 for yes, 1 for no;
  # cancelled (gum ctrl+c) it ends the builder
  local myRC=0
  fuUI_CONFIRM "$@" || myRC=$?
  [ "${myRC}" -le 1 ] || exit 130
  return "${myRC}"
}

fuMENU_IMAGES () {
  # which images; rc 1 for back
  local myWHAT myOUT myI
  local -a myITEMS=()
  myIMAGES="" myGROUPS=""
  fuASK myWHAT fuUI_CHOOSE "Which images?" "All images:all" "Groups of images:group" "Pick images:pick" "Back:back"
  case "${myWHAT}" in
    back) return 1 ;;
    group)
      for myI in ${myGROUP_NAMES}; do myITEMS+=("$(fuGROUP_TEXT "${myI}"):${myI}"); done
      fuASK myOUT fuUI_CHOOSE_MANY "Groups" "${myITEMS[@]}"
      myGROUPS=$(paste -sd ',' - <<< "${myOUT}") ;;
    pick)
      while IFS= read -r myI; do myITEMS+=("${myI}:${myI}"); done < <(fuCOMPOSE_SERVICES)
      fuASK myOUT fuUI_CHOOSE_MANY --filter "Images" "${myITEMS[@]}"
      myIMAGES=$(paste -sd ',' - <<< "${myOUT}") ;;
  esac
  if [ "${myWHAT}" != "all" ] && [ -z "${myIMAGES}${myGROUPS}" ]; then
    fuUI_WARN "Nothing chosen."
    return 1
  fi
  return 0
}

fuMENU_OPTIONS () {
  # the options one by one in their order, the settings (fuCONFIG) preselected; no for each
  # yes / no (what a run without the option does). Each round starts anew: Back in the
  # summary forgets the tag and the repositories of the round before
  local myOUT myRATE myHOSTTEXT myN
  local -a myITEMS=()
  myTAG="" myDOCKER_REPO="" myGHCR_REPO=""
  fuSETTINGS
  myHOSTTEXT="This host only"
  [ -z "${myHOST}" ] || myHOSTTEXT="${myHOSTTEXT} (linux/${myHOST})"
  fuASK myARCH fuUI_CHOOSE --selected "${myCONF_ARCH}" "Platforms" "linux/amd64 and linux/arm64:both" \
    "${myHOSTTEXT}:host" "linux/amd64 only:amd64" "linux/arm64 only:arm64"
  myMENU_ARCH=1
  myPUSH_HUB="" myPUSH_GHCR=""
  if fuYES --default no "Push the images to Docker Hub (${myHUB})?" "Push" "No"; then myPUSH_HUB=1; fi
  if fuYES --default no "Push the images to GHCR (${myGHCR})?" "Push" "No"; then myPUSH_GHCR=1; fi
  if fuPUSHING && [ "${myARCH}" != "both" ]; then fuMENU_TAG; fi
  myLIMIT="${myCONF_LIMIT}"
  if fuPUSHING && [ "${myCONF_LIMIT}" != "off" ]; then
    if fuOTHER_DOCKER;
      then
        # sudo talks to the Docker of root, not to this one: no limit, or no push now. What
        # this one is in a line of its own, the question fits 76 columns whatever its name
        fuUI_INFO "With ${myOTHER_DOCKER} root talks to another Docker: no upload limit for this one."
        fuASK myOUT fuUI_CHOOSE "Upload limit needs root (another Docker)" \
          "Push without a limit:off" "Quit:quit"
        [ "${myOUT}" != "quit" ] || exit 0
        myLIMIT="off"
    elif ! fuROOT;
      then
        # the same as without the menu: no limit only when it is chosen
        fuASK myOUT fuUI_CHOOSE "Upload limit needs root (sudo)" "Push without a limit:off" \
          "Quit, then run it with sudo:quit"
        if [ "${myOUT}" = "quit" ]; then
          fuUI_HINT "Run it with sudo: sudo $(printf '%q' "${mySELF}")"
          exit 0
        fi
        myLIMIT="off"
      else
        fuASK myOUT fuUI_CHOOSE --selected "${myCONF_LIMIT}" "Upload limit while pushing" \
          "${myCONF_LIMIT}:${myCONF_LIMIT}" "No limit:off" "Another rate:other"
        myLIMIT="${myOUT}"
        while [ "${myLIMIT}" = "other" ]; do
          fuASK myRATE fuUI_INPUT "Upload limit (a tc rate, i.e. 80mbit):"
          if fuRATE_OK "${myRATE}"; then myLIMIT="${myRATE}"; else fuUI_WARN "Not a rate for tc: ${myRATE}"; fi
        done
    fi
  fi
  myNO_CACHE=""
  if fuYES --default no "Build without the cache (slower, fresh base images)?" "Without cache" "With cache"; then
    myNO_CACHE=1
  fi
  # 1, 2, 4, 8 and the setting where it is none of them, in their order
  for myN in $(printf '%s\n' 1 2 4 8 "${myCONF_JOBS}" | sort -n -u); do myITEMS+=("${myN}:${myN}"); done
  fuASK myJOBS fuUI_CHOOSE --selected "${myCONF_JOBS}" "Builds at a time" "${myITEMS[@]}"
  myTEST=""
  if fuYES --default no "Run the smoke tests of the built images afterwards?" "Run them" "No"; then myTEST=1; fi
  if fuYES --default no "Change the version (${myVER}) or the repositories?" "Change" "Keep them"; then
    fuASK myOUT fuUI_INPUT "Version (enter keeps ${myVER}):"
    if [ -n "${myOUT}" ]; then
      if ! fuTAG_OK "${myOUT}"; then fuUI_WARN "Not a version tag: ${myOUT}, kept ${myVER}"
      elif fuPUSHING && [ "${myARCH}" != "both" ] && [ "${myOUT}" = "$(fuRELEASE)" ]; then
        fuUI_WARN "${myOUT} is the release tag: a one-platform push needs its own, kept ${myVER}"
      elif fuPUSHING && [ "${myARCH}" != "both" ] && fuPLAIN_VERSION "${myOUT}"; then
        fuUI_WARN "${myOUT} is a release version: a one-platform push needs a tag of its own, kept ${myVER}"
      else myTAG="${myOUT}"
      fi
    fi
    fuASK myOUT fuUI_INPUT "Docker Hub repository (enter keeps ${myHUB}):"
    if [ -n "${myOUT}" ]; then
      if fuREPO_OK "${myOUT}"; then myDOCKER_REPO="${myOUT}"; else fuUI_WARN "Not a repository: ${myOUT}, kept ${myHUB}"; fi
    fi
    fuASK myOUT fuUI_INPUT "GHCR repository (enter keeps ${myGHCR}):"
    if [ -n "${myOUT}" ]; then
      if fuREPO_OK "${myOUT}"; then myGHCR_REPO="${myOUT}"; else fuUI_WARN "Not a repository: ${myOUT}, kept ${myGHCR}"; fi
    fi
  fi
  fuSETTINGS
  fuPLATFORMS
}

fuMENU_TAG () {
  # a push of one platform: a tag of its own, no plain version (fuPLAIN_VERSION), a
  # release tag keeps the images of both platforms; enter means no push
  local myOUT myREL
  myREL=$(fuRELEASE)
  myTAG=""
  fuUI_WARN "A push for one platform over the release tag ${myREL} would replace its multi-arch images (linux/amd64 and linux/arm64)."
  while true; do
    fuASK myOUT fuUI_INPUT "Tag for this push (i.e. ${myREL}-$(fuARCH_NAME), enter = no push):"
    if [ -z "${myOUT}" ]; then
      fuUI_WARN "No tag, this run builds without a push."
      myPUSH_HUB="" myPUSH_GHCR=""
      return 0
    fi
    if [ "${myOUT}" = "${myREL}" ]; then fuUI_WARN "${myREL} is the release tag: a tag of its own, i.e. ${myREL}-$(fuARCH_NAME)"; continue; fi
    if fuPLAIN_VERSION "${myOUT}"; then
      fuUI_WARN "${myOUT} is a release version: a tag of its own, no plain version, i.e. ${myOUT}-$(fuARCH_NAME)"
      continue
    fi
    if fuTAG_OK "${myOUT}"; then myTAG="${myOUT}"; fuSETTINGS; return 0; fi
    fuUI_WARN "Not a version tag: ${myOUT}"
  done
}

fuMENU_LOGIN () {
  # the logins for pushing: docker login in the foreground where one is missing, then
  # checked again; rc 1 when one is still missing
  local myNAME myREG
  while IFS='|' read -r myNAME myREG <&3; do
    [ -n "${myNAME}" ] || continue
    if fuREGISTRY_READY "${myREG}"; then fuUI_OK "Logged in to ${myNAME}"; continue; fi
    fuUI_INFO "Log in to ${myNAME}:"
    if [ -n "${myREG}" ]; then docker login "${myREG}"; else docker login; fi
    if fuREGISTRY_READY "${myREG}";
      then fuUI_OK "Logged in to ${myNAME}"
      else fuUI_ERROR "Not logged in to ${myNAME}."; return 1
    fi
  done 3< <(fuTARGETS)
  return 0
}

fuMENU_BUILD () {
  # images, options, login and the summary; rc 0 to build, 1 for back
  local myWHAT myIMAGETEXT
  local -a myTAGTEXT=()
  fuMENU_IMAGES || return 1
  fuMENU_OPTIONS
  if fuPUSHING; then fuMENU_LOGIN || return 1; fi
  if [ -n "${myIMAGES}${myGROUPS}" ];
    then myIMAGETEXT="${myGROUPS:+groups ${myGROUPS//,/, }}${myGROUPS:+${myIMAGES:+; }}${myIMAGES//,/, }"
    else myIMAGETEXT="all"
  fi
  if fuPUSHING && [ "${myARCH}" != "both" ]; then
    myTAGTEXT=("info:Tag ${myTAG} for ${myPLATFORMS[*]} only (the release tag $(fuRELEASE) keeps its multi-arch images)")
  fi
  fuUI_SUMMARY "Ready to build" "info:Images: ${myIMAGETEXT}" "info:$(fuDESCRIBE)" \
    "info:Version ${myVER}, ${myHUB} and ${myGHCR}" "${myTAGTEXT[@]}" \
    "next:The same without the menu: $(fuCOMMAND_LINE)" || true
  fuASK myWHAT fuUI_CHOOSE "Build now?" "Build:build" "Back:back"
  [ "${myWHAT}" = "build" ]
}

fuMENU_SETTINGS () {
  # Settings: every key one by one, Keep first; then what changes, Save or Back. Saved
  # into .env.local like --set / --unset, the run takes them at once
  local myKEY myOUT myVAL myFROM myNAME myOTHER myWHAT
  local -a myCHANGES=() myITEMS=() myTEXTS=()
  # a file that turned into no text while the menu ran: no value of it to show
  fuLOCAL_TEXT || return 0
  for myKEY in ${mySETTING_KEYS}; do
    myOUT=$(fuCONFIG_GET "${myKEY}")
    myVAL="${myOUT%%|*}" myFROM="${myOUT#*|}"
    myITEMS=("Keep:keep")
    case "${myKEY}" in
      TPOT_DOCKER_REPO) myNAME="Docker Hub repository" myOTHER="Another repository:other" ;;
      TPOT_GHCR_REPO) myNAME="GHCR repository" myOTHER="Another repository:other" ;;
      TPOT_BUILDER_ARCH) myNAME="Platforms without -a"; myOTHER=""
        myITEMS+=("linux/amd64 and linux/arm64:both" "This host only:host" "linux/amd64 only:amd64"
                  "linux/arm64 only:arm64") ;;
      TPOT_BUILDER_JOBS) myNAME="Builds at a time without -j"; myOTHER=""
        myITEMS+=("1:1" "2:2" "4:4" "8:8") ;;
      TPOT_BUILDER_LIMIT) myNAME="Upload limit without -l" myOTHER="Another rate:other"
        myITEMS+=("${myDEFAULT_LIMIT}:${myDEFAULT_LIMIT}" "No limit:off") ;;
    esac
    [ -z "${myOTHER}" ] || myITEMS+=("${myOTHER}")
    if fuENV_HAS "${myLOCALFILE}" "${myKEY}"; then
      myITEMS+=("Remove the setting (back to .env or the default):unset")
    fi
    # the files of the builder by their names, so that the question fits 76 columns
    fuASK myWHAT fuUI_CHOOSE "${myNAME} (${myKEY}): ${myVAL} (${myFROM#docker/_builder/})" "${myITEMS[@]}"
    # what is typed is a value, also one named like a choice (a repository keep)
    myOUT="${myWHAT}"
    while [ "${myWHAT}" = "other" ]; do
      fuASK myOUT fuUI_INPUT "${myNAME} (${myKEY}):"
      if fuSETTING_CHECK "${myKEY}" "${myOUT}"; then myWHAT="value"; else fuUI_WARN "${mySETTING_WHY}"; fi
    done
    case "${myWHAT}" in
      keep) ;;
      unset) myCHANGES+=("${myKEY}"); myTEXTS+=("info:${myKEY} removed (back to .env or the default)") ;;
      *) myCHANGES+=("${myKEY}=${myOUT}"); myTEXTS+=("info:${myKEY}=${myOUT}") ;;
    esac
  done
  if [ "${#myCHANGES[@]}" -eq 0 ]; then fuUI_INFO "Nothing changed."; return 0; fi
  myOUT=$(for myVAL in "${myCHANGES[@]}"; do
            if [[ "${myVAL}" == *=* ]]; then printf ' --set %q' "${myVAL}"; else printf ' --unset %q' "${myVAL}"; fi
          done)
  fuUI_SUMMARY "Builder settings" "${myTEXTS[@]}" "info:Into $(fuSHORT "${myLOCALFILE}")" \
    "next:The same without the menu: $(printf '%q' "${mySELF}")${myOUT}" || true
  fuASK myOUT fuUI_CHOOSE "Save the settings?" "Save:save" "Back:back"
  [ "${myOUT}" = "save" ] || return 0
  fuSAVE_SETTINGS "${myCHANGES[@]}" || return 0
  fuCONFIG || true
  fuSETTINGS
  fuPLATFORMS || true
}

fuMENU () {
  local myWHAT myRC
  fuSETTINGS
  fuPLATFORMS || true
  fuDOCKER_READY || return 3
  myMENU=1
  fuUI_BANNER "Image Builder" "$(fuSTATUS)" "Version ${myVER}, ${myHUB} and ${myGHCR}"
  while true; do
    fuASK myWHAT fuUI_CHOOSE "What do you want to do?" "Build images:build" \
      "Builder setup (check, set up, remove):setup" "Settings (repositories, platforms, builds, limit):settings" \
      "Quit:quit"
    case "${myWHAT}" in
      quit) return 0 ;;
      settings) fuMENU_SETTINGS ;;
      setup)
        fuASK myWHAT fuUI_CHOOSE "Builder setup" "Check the builder:check" "Set up the builder:setup" \
          "Remove the builder setup:uninstall" "Back:back"
        case "${myWHAT}" in
          check) fuCHECK ;;
          setup) fuSETUP ;;
          uninstall)
            if fuYES --default no "Remove the builder, all QEMU handlers and their images?" "Remove" "Keep"; then
              fuUNINSTALL
            fi ;;
        esac ;;
      build)
        if fuMENU_BUILD; then
          fuRUN
          myRC=$?
          return "${myRC}"
        fi ;;
    esac
  done
}

fuDESCENDANTS () {
  # fuDESCENDANTS <pid>: the processes below it, parents first, without this snapshot
  # (the subshell it runs in, ps and awk)
  ps -A -o pid= -o ppid= 2>/dev/null | awk -v root="$1" -v self="${BASHPID}" '
    { kids[$2] = kids[$2] " " $1 }
    END {
      tail = split(kids[root], queue, " ")
      for (head = 1; head <= tail; head++) {
        p = queue[head]
        if (p == self) continue
        print p
        n = split(kids[p], c, " ")
        for (i = 1; i <= n; i++) queue[++tail] = c[i]
      }
    }'
}

fuALIVE () {
  # fuALIVE <pid> ...: 0 while one of them runs (a zombie does not)
  ps -A -o pid= -o stat= 2>/dev/null | awk -v list=" $* " '
    index(list, " " $1 " ") && $2 !~ /^Z/ { found = 1 } END { exit !found }'
}

fuSTOP_CHILDREN () {
  # everything this run started (xargs, the builds, docker compose and what they
  # started): stopped first, parents before their children, so none starts a new one,
  # then ended; what still runs after mySTOP_TIMEOUT seconds is killed
  local myP myI myNEW
  local -a myALL=() myFOUND=()
  for myI in 1 2 3 4 5; do
    mapfile -t myFOUND < <(fuDESCENDANTS "$$")
    myNEW=""
    for myP in "${myFOUND[@]}"; do
      [[ " ${myALL[*]} " != *" ${myP} "* ]] || continue
      kill -STOP "${myP}" 2>/dev/null
      myALL+=("${myP}") myNEW=1
    done
    [ -n "${myNEW}" ] || break
  done
  [ "${#myALL[@]}" -gt 0 ] || return 0
  kill -TERM "${myALL[@]}" 2>/dev/null
  kill -CONT "${myALL[@]}" 2>/dev/null
  for ((myI = 0; myI < mySTOP_TIMEOUT * 10; myI++)); do
    fuALIVE "${myALL[@]}" || return 0
    sleep 0.1
  done
  kill -KILL "${myALL[@]}" 2>/dev/null
  return 0
}

fuCANCEL () {
  # INT / TERM: the builds end first, then the upload limit goes, exit 130; a second
  # signal does not cut that short
  trap '' INT TERM
  echo
  fuUI_WARN "Cancelled."
  fuSTOP_CHILDREN
  fuEXIT
  exit 130
}

fuMAIN () {
  local myRC=0
  fuPARSE "$@" || return 2
  [ "${myACTION}" != "help" ] || { fuHELP; return 0; }
  # a Linux tool (bash 4, tc, binfmt_misc, the GNU timeout of the smoke tests): -h shows
  # everywhere, everything else stops outside Linux, one rule for all of it
  fuUI_LINUX_ONLY "Image Builder" 3
  case "${myACTION}" in
    list) fuLIST; return 0 ;;
    show) fuSHOW_CONFIG; return $? ;;
  esac
  trap fuEXIT EXIT
  trap fuCANCEL INT TERM
  case "${myACTION}" in
    settings) fuSAVE_SETTINGS "${mySET_ITEMS[@]}"; return $? ;;
    check) fuCHECK; return $? ;;
    setup) fuSETUP; return $? ;;
    uninstall) fuUNINSTALL; return $? ;;
  esac
  # rc 2 for a wrong setting, 3 for an .env.local that is no text
  fuCONFIG || return $?
  # the menu only without any option (-y too) at a terminal
  if [ -z "${myYES}" ] && [ "$#" -eq 0 ] && [ -t 0 ] && [ -t 1 ];
    then fuMENU || myRC=$?
    else fuRUN || myRC=$?
  fi
  return "${myRC}"
}

# sourced (the tests): the functions only
[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0
fuMAIN "$@"
exit $?
