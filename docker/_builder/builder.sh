#!/usr/bin/env bash
# The image builder of T-Pot: builds the images of docker-compose.yml here with buildx
# (linux/amd64 and linux/arm64), pushes them to Docker Hub and / or GHCR, sets up the
# buildx builder 'mybuilder' with QEMU and runs the smoke tests of docker/_tests after
# the build. A tool for building releases, not part of the T-Pot Manager.
#
# With no option at a terminal it asks (a menu), with -y, any option or without a
# terminal it never asks. -h shows the options and the exit codes. Root or the docker
# group; root only for the upload limit (tc) while pushing.

myDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
myREPO="$(cd "${myDIR}/../.." && pwd)"
# the look of the T-Pot scripts (installer/lib/ui.sh), plain text if it is missing
# shellcheck source=../../installer/lib/ui.sh
if ! source "${myREPO}/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback
    fuUI_INIT () { return 0; }
    fuUI_BANNER () { echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE}"; done; echo; }
    fuUI_INFO () { echo "### $*"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuUI_CONFIRM () {
      local myANSWER=""
      while [ "${myANSWER}" != "y" ] && [ "${myANSWER}" != "n" ]; do
        read -rp "### $1 (y/n) " myANSWER || return 1
      done
      [ "${myANSWER}" = "y" ]
    }
    fuUI_CHOOSE () {
      local myHEADER="$1" myI=1 myITEM myPICK
      shift
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
      local myVALUE=""
      if [ "$2" = "password" ];
        then read -rsp "### $1 " myVALUE; echo >&2
        else read -rp "### $1 " myVALUE
      fi
      echo "${myVALUE}"
    }
    fuUI_MARKS_ON () { [ -n "${myMARKS:-}" ] || [ "${TPOT_MARKS:-}" = "1" ]; }
    fuMARK () { fuUI_MARKS_ON || return 0; echo "@@tpot $*"; }
    fuUI_LOGO () { return 1; }
    fuUI_VERSION () {
      local myDIR="${1:-${HOME}/tpotce}" myV="" myLINE myRE='^[0-9A-Za-z][0-9A-Za-z._+~-]*$' myENV
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
      local myALL="" mySELECTED="" myI myN myPICK myPART myA myB myOK
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --filter) shift ;;
          --all) myALL=1; shift ;;
          --selected) mySELECTED="${2:-}"; shift $(( $# < 2 ? $# : 2 )) ;;
          *) break ;;
        esac
      done
      local myHEADER="${1:-}"
      [ "$#" -eq 0 ] || shift
      local -a myLABELS=() myVALUES=() myON=() myNEW=()
      for myI in "$@"; do
        myLABELS+=("${myI%:*}")
        myVALUES+=("${myI##*:}")
        if [ -n "${myALL}" ] || [[ ",${mySELECTED}," == *",${myVALUES[${#myVALUES[@]}-1]},"* ]];
          then myON+=(1)
          else myON+=("")
        fi
      done
      myN="${#myLABELS[@]}"
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
    fuUI_SPIN () {
      local myTITLE="$1" myLOG="$2" myRC=0
      shift 2
      echo "### ${myTITLE}"
      if fuUI_MARKS_ON;
        then "$@" < /dev/null || myRC=$?
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
myLOGDIR="${TPOT_BUILDER_LOG_DIR:-${myDIR}/log}"
myLOG="${myLOGDIR}/builder.log"
myTESTDIR="${TPOT_BUILDER_TESTS_DIR:-${myREPO}/docker/_tests/tests}"
myBINFMT="${TPOT_BINFMT_DIR:-/proc/sys/fs/binfmt_misc}"
myLOGIN_TIMEOUT="${TPOT_BUILDER_LOGIN_TIMEOUT:-30}"
# build.platforms: !override and tags: !reset of the generated override file
myCOMPOSE_MIN="2.24.4"
myDEFAULT_JOBS=2
myDEFAULT_LIMIT="40mbit" # at most 90% of the upload bandwidth there is
myGROUP_NAMES="honeypots tanner nsm elk tools"

# the options (fuPARSE), "" is off
myACTION="build"
myYES=""
myIMAGES=""
myGROUPS=""
myARCH="both"
myPUSH_HUB=""
myPUSH_GHCR=""
myNO_CACHE=""
myJOBS="${myDEFAULT_JOBS}"
myLIMIT="${myDEFAULT_LIMIT}"
myTAG=""
myDOCKER_REPO=""
myGHCR_REPO=""
myTEST=""
myMENU=""
# the state of a run
myLIMIT_SET=""
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
terminal it never asks. The logs go to docker/_builder/log." \
    --opt "-y, --yes" "Never ask, build at once (all images without other options)" \
    --opt "-i, --images a,b" "Images by name (-L lists them)" \
    --opt "-g, --group g,h" "Images by group: ${myGROUP_NAMES// /, }, all
(default: all; with -i the union)" \
    --opt "-a, --arch ARCH" "amd64, arm64, host (this host only) or both (default)" \
    --opt "-p, --push" "Push to Docker Hub and GHCR (needs an existing docker login)" \
    --opt "--push-hub" "Push to Docker Hub only" \
    --opt "--push-ghcr" "Push to GHCR only" \
    --opt "-n, --no-cache" "Build without the cache" \
    --opt "-j, --jobs N" "Builds at a time, 1-16 (default: ${myDEFAULT_JOBS})" \
    --opt "-l, --upload-limit RATE" "Upload limit while pushing, tc rate or off (default: ${myDEFAULT_LIMIT});
needs root" \
    --opt "-t, --tag VERSION" "The version tag (default: the file version of the checkout)" \
    --opt "--docker-repo REPO" "The Docker Hub repository (default: TPOT_DOCKER_REPO of .env)" \
    --opt "--ghcr-repo REPO" "The GHCR repository (default: TPOT_GHCR_REPO of .env)" \
    --opt "-T, --test" "Run the smoke tests of the built images after the build" \
    --opt "-L, --list" "List the images by group" \
    --opt "--check" "Check the buildx builder 'mybuilder' (platforms, QEMU)" \
    --opt "--setup" "Set up the builder 'mybuilder' and QEMU for multi-arch builds" \
    --opt "--uninstall" "Remove that setup again (builder, QEMU handlers, images)" \
    --opt "-h, --help" "Show this help" \
    --example "sudo builder.sh -y" "Build every image for both platforms" \
    --example "builder.sh -i cowrie,tpotinit -a host -T" "Two images for this host, then their smoke tests" \
    --example "sudo builder.sh -g honeypots -p -l 80mbit" "Build the honeypots, push them with an upload limit" \
    --note "Exit codes: 0 done, 1 an image failed, 2 a wrong option, 3 the environment (rights, Docker,
builder, QEMU, login, tc), 4 built but a smoke test failed, 130 cancelled." \
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
  if [ "${myACTION}" != "build" ] && [ "${myACTION}" != "$1" ];
    then fuUI_USAGE_ERROR "Only one of -L, --check, --setup and --uninstall."; return 2
  fi
  myACTION="$1"
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
      case "${myVAL}" in amd64|arm64|host|both) myARCH="${myVAL}" ;;
        *) fuUI_USAGE_ERROR "Unknown platform: ${myVAL} (amd64, arm64, host, both)."; return 2 ;;
      esac ;;
    -j|--jobs)
      if [[ "${myVAL}" =~ ^[0-9]+$ ]] && [ "$((10#${myVAL}))" -ge 1 ] && [ "$((10#${myVAL}))" -le 16 ];
        then myJOBS="$((10#${myVAL}))"
        else fuUI_USAGE_ERROR "${myOPT} takes 1 to 16 builds at a time, not ${myVAL}."; return 2
      fi ;;
    -l|--upload-limit)
      if [ "${myVAL}" = "off" ] || fuRATE_OK "${myVAL}";
        then myLIMIT="${myVAL}"
        else fuUI_USAGE_ERROR "Not a rate for tc: ${myVAL} (i.e. 40mbit, 800kbit, off)."; return 2
      fi ;;
    -t|--tag)
      fuTAG_OK "${myVAL}" || { fuUI_USAGE_ERROR "Not a version tag: ${myVAL}."; return 2; }
      myTAG="${myVAL}" ;;
    --docker-repo|--ghcr-repo)
      fuREPO_OK "${myVAL}" || { fuUI_USAGE_ERROR "Not a repository: ${myVAL}."; return 2; }
      if [ "${myOPT}" = "--docker-repo" ]; then myDOCKER_REPO="${myVAL}"; else myGHCR_REPO="${myVAL}"; fi ;;
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
      -i|--images|-g|--group|-a|--arch|-j|--jobs|-l|--upload-limit|-t|--tag|--docker-repo|--ghcr-repo)
        if [ -z "${myHAS}" ]; then
          [ "$#" -ge 2 ] || { fuUI_USAGE_ERROR "${myOPT} needs a value."; return 2; }
          myVAL="$2"
          shift
        fi
        fuSET "${myOPT}" "${myVAL}" || return 2
        shift
        continue ;;
    esac
    [ -z "${myHAS}" ] || { fuUI_USAGE_ERROR "${myOPT} takes no value."; return 2; }
    case "${myOPT}" in
      -h|--help) myACTION="help"; return 0 ;;
      -y|--yes) myYES=1 ;;
      -p|--push) myPUSH_HUB=1 myPUSH_GHCR=1 ;;
      --push-hub) myPUSH_HUB=1 ;;
      --push-ghcr) myPUSH_GHCR=1 ;;
      -n|--no-cache) myNO_CACHE=1 ;;
      -T|--test) myTEST=1 ;;
      -L|--list) fuACTION list || return 2 ;;
      --check) fuACTION check || return 2 ;;
      --setup) fuACTION setup || return 2 ;;
      --uninstall) fuACTION uninstall || return 2 ;;
      *) fuUI_USAGE_ERROR "Unknown option: ${myOPT}"; return 2 ;;
    esac
    shift
  done
  return 0
}

fuENV_VALUE () {
  # fuENV_VALUE <key>: the value of a key in docker/_builder/.env
  [ -r "${myENVFILE}" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "${myENVFILE}" | tail -n 1 | tr -d "\"'[:space:]"
}

fuSETTINGS () {
  # the version and the repositories of this run: an option, else the file version of
  # the checkout (fuUI_VERSION) / the environment, else docker/_builder/.env; exported,
  # so compose takes them over its .env
  myVER="${myTAG:-$(fuUI_VERSION "${myREPO}")}"
  [ -n "${myVER}" ] || myVER="${TPOT_VERSION:-$(fuENV_VALUE TPOT_VERSION)}"
  myHUB="${myDOCKER_REPO:-${TPOT_DOCKER_REPO:-$(fuENV_VALUE TPOT_DOCKER_REPO)}}"
  myGHCR="${myGHCR_REPO:-${TPOT_GHCR_REPO:-$(fuENV_VALUE TPOT_GHCR_REPO)}}"
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

fuRIGHTS () {
  # fuRIGHTS [root]: root, or the docker group where root is not needed
  [ "$(whoami 2>/dev/null)" = "root" ] && return 0
  if [ "${1:-}" = "root" ]; then
    fuUI_ERROR "The upload limit while pushing (tc) needs root."
    fuUI_HINT "Run the builder with sudo, or push without a limit: -l off" >&2
    return 3
  fi
  id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker && return 0
  fuUI_ERROR "The image builder needs root or the docker group."
  fuUI_HINT "Run it with sudo, or add yourself to the docker group and log in again:" \
            "sudo usermod -aG docker $(whoami 2>/dev/null)" >&2
  return 3
}

fuDOCKER_READY () {
  if ! command -v docker >/dev/null 2>&1; then
    fuUI_ERROR "Docker is not installed."
    fuUI_HINT "The Docker packages of https://get.docker.com/ bring buildx and compose." >&2
    return 3
  fi
  if ! docker info >/dev/null 2>&1; then
    fuUI_ERROR "Docker does not answer: is it running, and may this user use it?"
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

fuVERSION_GE () {
  # fuVERSION_GE <version> <minimum>: 0 when the version (x.y.z, a v and a suffix are
  # left out) is at least the minimum
  local myI myX myY myV="${1#v}"
  local -a myA=() myB=()
  IFS=. read -r -a myA <<< "${myV%%[-+]*}"
  IFS=. read -r -a myB <<< "$2"
  for myI in 0 1 2; do
    myX="${myA[myI]:-0}" myY="${myB[myI]:-0}"
    [[ "${myX}" =~ ^[0-9]+$ ]] || return 1
    if [ "$((10#${myX}))" -gt "$((10#${myY}))" ]; then return 0; fi
    if [ "$((10#${myX}))" -lt "$((10#${myY}))" ]; then return 1; fi
  done
  return 0
}

fuCOMPOSE_RECENT () {
  local myV
  myV=$(docker compose version --short 2>/dev/null)
  fuVERSION_GE "${myV}" "${myCOMPOSE_MIN}" && return 0
  fuUI_ERROR "docker compose ${myV:-of an unknown version} is too old, -a and pushing to one registry need ${myCOMPOSE_MIN}."
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
  fuUI_HINT "A run with sudo may have left it to root: sudo chown -R $(whoami 2>/dev/null) ${myLOGDIR}" >&2
  return 3
}

fuHAS_PLATFORMS () {
  # fuHAS_PLATFORMS <platforms of buildx inspect> <platform> ...
  local myLINE="$1" myP
  shift
  for myP in "$@"; do [[ "${myLINE}" == *"${myP}"* ]] || return 1; done
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

fuPREPARE () {
  # fuPREPARE <platform> ...: builder, QEMU for the ones of another architecture, platforms
  local myP
  local -a myARCHS=()
  for myP in "$@"; do [ "${myP#linux/}" = "${myHOST}" ] || myARCHS+=("${myP#linux/}"); done
  fuUI_SPIN "Checking the buildx builder '${myBUILDER}' ..." "${myLOG}" fuENSURE_BUILDER || return 3
  if [ "${#myARCHS[@]}" -gt 0 ]; then
    fuUI_SPIN "Configuring QEMU for ${myARCHS[*]} ..." "${myLOG}" fuQEMU "${myARCHS[@]}" || return 3
  fi
  fuUI_SPIN "Making sure '${myBUILDER}' builds $* ..." "${myLOG}" fuENSURE_PLATFORMS "$@" || return 3
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
  local myI myRC=0 myLOAD="${myLOGDIR}/override-load.yml"
  local -a myARGS=()
  fuSMOKE_PLAN "$@"
  for myI in "${mySMOKE_NOTES[@]}"; do mySUMMARY+=("warn:${myI}"); done
  [ "${#mySMOKE_NAMES[@]}" -gt 0 ] || return 0
  if [ -z "${myHOST}" ]; then
    mySUMMARY+=("fail:No smoke tests, this host is neither amd64 nor arm64")
    return 4
  fi
  if ! fuHOST_ONLY || fuPUSHING; then
    fuWRITE_OVERRIDE "${myLOAD}" load "${mySMOKE_IMAGES[@]}"
    if ! fuUI_SPIN "Building ${mySMOKE_IMAGES[*]} for linux/${myHOST} into docker for the smoke tests ..." \
           "${myLOGDIR}/load.log" docker compose "${myFILES[@]}" -f "${myLOAD}" --progress plain build \
           "${mySMOKE_IMAGES[@]}" --builder "${myBUILDER}"; then
      mySUMMARY+=("fail:The build for the smoke tests, see ${myLOGDIR}/load.log")
      return 4
    fi
  fi
  for myI in "${!mySMOKE_NAMES[@]}"; do
    read -r -a myARGS <<< "${mySMOKE_ARGS[myI]}"
    : > "${myLOGDIR}/test-${mySMOKE_NAMES[myI]}.log"
    if fuUI_SPIN "Smoke test ${mySMOKE_NAMES[myI]} ..." "${myLOGDIR}/test-${mySMOKE_NAMES[myI]}.log" \
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
  # the same run without the menu
  local -a myC=()
  [ "$(whoami 2>/dev/null)" != "root" ] || [ -z "${SUDO_USER:-}" ] || myC+=(sudo)
  myC+=("$0" -y)
  [ -z "${myGROUPS}" ] || myC+=(-g "${myGROUPS}")
  [ -z "${myIMAGES}" ] || myC+=(-i "${myIMAGES}")
  [ "${myARCH}" = "both" ] || myC+=(-a "${myARCH}")
  if [ -n "${myPUSH_HUB}" ] && [ -n "${myPUSH_GHCR}" ]; then myC+=(-p)
  elif [ -n "${myPUSH_HUB}" ]; then myC+=(--push-hub)
  elif [ -n "${myPUSH_GHCR}" ]; then myC+=(--push-ghcr)
  fi
  if fuPUSHING && [ "${myLIMIT}" != "${myDEFAULT_LIMIT}" ]; then myC+=(-l "${myLIMIT}"); fi
  [ -z "${myNO_CACHE}" ] || myC+=(-n)
  [ "${myJOBS}" = "${myDEFAULT_JOBS}" ] || myC+=(-j "${myJOBS}")
  [ -z "${myTEST}" ] || myC+=(-T)
  [ -z "${myTAG}" ] || myC+=(-t "${myTAG}")
  [ -z "${myDOCKER_REPO}" ] || myC+=(--docker-repo "${myDOCKER_REPO}")
  [ -z "${myGHCR_REPO}" ] || myC+=(--ghcr-repo "${myGHCR_REPO}")
  printf '%q ' "${myC[@]}" | sed 's/ $//'
}

fuRUN () {
  # one build run, the same from the menu and without it; the exit code of the table
  # in fuHELP
  local myRC=0 myRESULT myIMAGE myREPORTED=0 myFLAGS myOVERRIDE="${myLOGDIR}/override.yml"
  local -a myOK=() myFAILED=()
  mySUMMARY=()
  fuSETTINGS
  fuPLATFORMS || return 3
  if [ -z "${myMENU}" ];
    then fuUI_BANNER "Image Builder" "$(fuDESCRIBE)" "Version ${myVER}, ${myHUB} and ${myGHCR}"
    else fuUI_INFO "Building: $(fuDESCRIBE)"
  fi
  if fuLIMITED; then fuRIGHTS root || return 3; else fuRIGHTS || return 3; fi
  fuDOCKER_READY || return 3
  if fuLIMITED && ! command -v tc >/dev/null 2>&1; then
    fuUI_ERROR "tc (iproute2) is missing for the upload limit."
    fuUI_HINT "Push without a limit: -l off" >&2
    return 3
  fi
  fuLOGDIR || return 3
  fuSELECTION || return 3
  if fuOVERRIDE_NEEDED; then fuCOMPOSE_RECENT || return 3; fi
  if fuPUSHING && [ -z "${myMENU}" ]; then fuLOGINS || return 3; fi
  fuPREPARE "${myPLATFORMS[@]}" || return 3
  if fuOVERRIDE_NEEDED; then
    fuWRITE_OVERRIDE "${myOVERRIDE}" main "${myLIST[@]}"
    myFILES+=(-f "${myOVERRIDE}")
  fi
  if fuLIMITED; then fuLIMIT_ON || return 3; fi

  echo
  fuUI_INFO "Building ${#myLIST[@]} images, the log of each is ${myLOGDIR}/<image>.log ..."
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
    fuSMOKE_TESTS "${myOK[@]}" || { [ "${myRC}" -ne 0 ] || myRC=4; }
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
  # one line about mybuilder for the menu (no bootstrap, nothing is started)
  local myOUT myPLAT mySTATE
  if ! myOUT=$(docker buildx inspect "${myBUILDER}" 2>/dev/null); then
    echo "Builder '${myBUILDER}': not set up (Builder setup sets it up)"
    return 0
  fi
  myPLAT=$(sed -n 's/.*Platforms: *//p' <<< "${myOUT}" | head -n 1)
  mySTATE=$(sed -n 's/^ *Status: *//p' <<< "${myOUT}" | head -n 1)
  echo "Builder '${myBUILDER}': ${mySTATE:-there}${myPLAT:+, ${myPLAT}}"
}

fuCHECK () {
  # --check: rc 0 when mybuilder builds linux/amd64 and linux/arm64
  local myOUT myPLAT myRC=0 myA myV
  local -a myITEMS=()
  fuUI_BANNER "Builder Setup" "Checking the buildx builder '${myBUILDER}'"
  fuRIGHTS || return 3
  fuDOCKER_READY || return 3
  myHOST=$(fuHOST_ARCH) || myHOST=""
  if myOUT=$(docker buildx inspect "${myBUILDER}" --bootstrap 2>&1); then
    myPLAT=$(sed -n 's/.*Platforms: *//p' <<< "${myOUT}" | head -n 1)
    myITEMS+=("ok:Builder '${myBUILDER}' runs")
    for myA in linux/amd64 linux/arm64; do
      if fuHAS_PLATFORMS "${myPLAT}" "${myA}";
        then myITEMS+=("ok:It builds ${myA}")
        else myITEMS+=("fail:It does not build ${myA} (--setup)"); myRC=3
      fi
    done
  else
    myITEMS+=("fail:No buildx builder '${myBUILDER}' (--setup sets it up)")
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
  if fuVERSION_GE "${myV}" "${myCOMPOSE_MIN}";
    then myITEMS+=("ok:docker compose ${myV}")
    else myITEMS+=("warn:docker compose ${myV:-of an unknown version}: -a and pushing to one registry need ${myCOMPOSE_MIN}")
  fi
  fuUI_SUMMARY "Builder '${myBUILDER}'" "${myITEMS[@]}" || true
  return "${myRC}"
}

fuSETUP () {
  # --setup: mybuilder and QEMU for linux/amd64 and linux/arm64
  fuUI_BANNER "Builder Setup" "Setting up Docker for multi-arch builds." \
              "Requires the Docker packages of https://get.docker.com/"
  fuRIGHTS || return 3
  fuDOCKER_READY || return 3
  myHOST=$(fuHOST_ARCH) || myHOST=""
  fuLOGDIR || return 3
  if ! fuPREPARE linux/amd64 linux/arm64; then
    fuUI_SUMMARY "Builder Setup" "fail:The setup failed, see ${myLOG}" || true
    return 3
  fi
  fuUI_SUMMARY "Builder Setup" "ok:'${myBUILDER}' builds linux/amd64 and linux/arm64" \
    "next:Build: $0 (a menu) or $0 -y" \
    "next:One image by hand: cd ${myDIR} && docker compose build tpotinit" \
    "info:Segmentation faults in arm64 builds: $0 --uninstall, then $0 --setup" || true
  return 0
}

fuUNINSTALL () {
  # --uninstall: the builder, what is left of it, the QEMU emulation and the images used
  # for it; steps with nothing to remove are skipped. rc 3 when a step fails
  local myRC=0
  local -a myITEMS=() myIMAGES_LEFT=()
  fuUI_BANNER "Builder Setup" "Removing the multi-arch build setup"
  fuRIGHTS || return 3
  fuDOCKER_READY || return 3
  # buildx keeps its builders per user (root with sudo, a user of the docker group
  # without), so a builder may exist for the other one as well
  if docker buildx inspect "${myBUILDER}" >/dev/null 2>&1; then
    if docker buildx rm "${myBUILDER}" >/dev/null 2>&1;
      then myITEMS+=("ok:Removed the buildx builder '${myBUILDER}'")
      else myITEMS+=("fail:Could not remove the buildx builder '${myBUILDER}'"); myRC=3
    fi
  else
    myITEMS+=("info:No buildx builder '${myBUILDER}', skipped")
  fi
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
  # fuYES <question> [yes] [no]: fuUI_CONFIRM, 0 for yes, 1 for no; cancelled (gum
  # ctrl+c) it ends the builder
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
  # the options one by one
  local myOUT myRATE myHOSTTEXT
  myHOSTTEXT="This host only"
  [ -z "${myHOST}" ] || myHOSTTEXT="${myHOSTTEXT} (linux/${myHOST})"
  fuASK myARCH fuUI_CHOOSE "Platforms" "linux/amd64 and linux/arm64:both" "${myHOSTTEXT}:host" \
    "linux/amd64 only:amd64" "linux/arm64 only:arm64"
  myPUSH_HUB="" myPUSH_GHCR=""
  if fuYES "Push the images to Docker Hub (${myHUB})?" "Push" "No"; then myPUSH_HUB=1; fi
  if fuYES "Push the images to GHCR (${myGHCR})?" "Push" "No"; then myPUSH_GHCR=1; fi
  myLIMIT="${myDEFAULT_LIMIT}"
  if fuPUSHING; then
    if [ "$(whoami 2>/dev/null)" != "root" ];
      then fuUI_WARN "An upload limit needs root (sudo), this run pushes without one."; myLIMIT="off"
      else
        fuASK myOUT fuUI_CHOOSE "Upload limit while pushing" "${myDEFAULT_LIMIT} (the default):${myDEFAULT_LIMIT}" \
          "No limit:off" "Another rate:other"
        myLIMIT="${myOUT}"
        while [ "${myLIMIT}" = "other" ]; do
          fuASK myRATE fuUI_INPUT "Upload limit (a tc rate, i.e. 80mbit):"
          if fuRATE_OK "${myRATE}"; then myLIMIT="${myRATE}"; else fuUI_WARN "Not a rate for tc: ${myRATE}"; fi
        done
    fi
  fi
  myNO_CACHE=""
  if fuYES "Build without the cache (slower, fresh base images)?" "Without cache" "With cache"; then myNO_CACHE=1; fi
  fuASK myJOBS fuUI_CHOOSE "Builds at a time" "1:1" "2 (the default):2" "4:4" "8:8"
  myTEST=""
  if fuYES "Run the smoke tests of the built images afterwards?" "Run them" "No"; then myTEST=1; fi
  if fuYES "Change the version (${myVER}) or the repositories?" "Change" "Keep them"; then
    fuASK myOUT fuUI_INPUT "Version (enter keeps ${myVER}):"
    if [ -n "${myOUT}" ]; then
      if fuTAG_OK "${myOUT}"; then myTAG="${myOUT}"; else fuUI_WARN "Not a version tag: ${myOUT}, kept ${myVER}"; fi
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
  fuMENU_IMAGES || return 1
  fuMENU_OPTIONS
  if fuPUSHING; then fuMENU_LOGIN || return 1; fi
  if [ -n "${myIMAGES}${myGROUPS}" ];
    then myIMAGETEXT="${myGROUPS:+groups ${myGROUPS//,/, }}${myGROUPS:+${myIMAGES:+; }}${myIMAGES//,/, }"
    else myIMAGETEXT="all"
  fi
  fuUI_SUMMARY "Ready to build" "info:Images: ${myIMAGETEXT}" "info:$(fuDESCRIBE)" \
    "info:Version ${myVER}, ${myHUB} and ${myGHCR}" "next:The same without the menu: $(fuCOMMAND_LINE)" || true
  fuASK myWHAT fuUI_CHOOSE "Build now?" "Build:build" "Back:back"
  [ "${myWHAT}" = "build" ]
}

fuMENU () {
  local myWHAT myRC
  fuSETTINGS
  fuPLATFORMS || true
  fuRIGHTS || return 3
  fuDOCKER_READY || return 3
  myMENU=1
  fuUI_BANNER "Image Builder" "$(fuSTATUS)" "Version ${myVER}, ${myHUB} and ${myGHCR}"
  while true; do
    fuASK myWHAT fuUI_CHOOSE "What do you want to do?" "Build images:build" \
      "Builder setup (check, set up, remove):setup" "Quit:quit"
    case "${myWHAT}" in
      quit) return 0 ;;
      setup)
        fuASK myWHAT fuUI_CHOOSE "Builder setup" "Check the builder:check" "Set up the builder:setup" \
          "Remove the builder setup:uninstall" "Back:back"
        case "${myWHAT}" in
          check) fuCHECK ;;
          setup) fuSETUP ;;
          uninstall) if fuYES "Remove the builder, all QEMU handlers and their images?" "Remove" "Keep"; then fuUNINSTALL; fi ;;
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

fuMAIN () {
  local myRC=0
  fuPARSE "$@" || return 2
  case "${myACTION}" in
    help) fuHELP; return 0 ;;
    list) fuLIST; return 0 ;;
  esac
  trap fuLIMIT_OFF EXIT
  trap 'echo; fuUI_WARN "Cancelled."; exit 130' INT TERM
  case "${myACTION}" in
    check) fuCHECK; return $? ;;
    setup) fuSETUP; return $? ;;
    uninstall) fuUNINSTALL; return $? ;;
  esac
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
