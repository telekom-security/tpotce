#!/usr/bin/env bash
# Add a user of the T-Pot web UI. This is `tpot users add` now (bcrypt, the change
# counts at once); without the Python packages of tpot (i.e. no internet to set them
# up) the tpotinit container asks for the user as before.
# the look of the T-Pot scripts (installer/lib/ui.sh), plain text if it is missing
# shellcheck source=installer/lib/ui.sh
if ! source "$HOME/tpotce/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback
    fuUI_INIT () { return 0; }
    fuUI_BANNER () { local myLINE; echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE}"; done; echo; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
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
# <<< plain fallback
fi
fuUI_INIT
# a person at a terminal sees the T-Pot logo and what comes, then tpot asks; not for the help
case " $* " in
  *" -h "*|*" --help "*) ;;
  *) if [ -t 0 ] && [ -t 1 ];
       then
         # shellcheck disable=SC2034 # fuUI_BANNER of installer/lib/ui.sh reads it
         myUI_LOGO=1
         fuUI_BANNER "Web user" "Adds a user of the T-Pot web UI."
     fi ;;
esac
myTPOT="$HOME/tpotce/tpot"
if [ -x "${myTPOT}" ] && "${myTPOT}" setup > /dev/null 2>&1;
  then
    exec "${myTPOT}" users add "$@"
fi
# without tpot: the tpotinit container, its help says so
case " $* " in
  *" -h "*|*" --help "*)
    fuUI_HELP "Web user" "genuser.sh [-h]" \
      --about "Adds a user of the T-Pot web UI: the tpotinit container asks for its name
and password and writes it to WEB_USER of the .env." \
      --about "This is tpot users add of the T-Pot Manager. Where tpot cannot be set up
(its Python packages need the internet), genuser.sh runs the tpotinit
container instead." \
      --opt "-h, --help" "Show this help" \
      --note "tpot users add -h shows the options of the T-Pot Manager. Its users count
at once, without a restart of T-Pot."
    exit 0 ;;
esac
fuUI_WARN "tpot is not available, using the tpotinit container."
cd "$HOME/tpotce" || exit 1
TPOT_REPO=$(grep -E "^TPOT_REPO" .env | cut -d "=" -f2-)
TPOT_VERSION=$(grep -E "^TPOT_VERSION" .env | cut -d "=" -f2-)
USER=$(id -u)
USERNAME=$(id -un)
GROUP=$(id -g)
fuUI_HINT "Repository:        ${TPOT_REPO}" \
          "Version Tag:       ${TPOT_VERSION}" \
          "Your User Name:    ${USERNAME}" \
          "Your User ID:      ${USER}" \
          "Your Group ID:     ${GROUP}"
echo
if docker run -v "$HOME"/tpotce:/data --entrypoint "bash" -it -u "${USER}":"${GROUP}" "${TPOT_REPO}"/tpotinit:"${TPOT_VERSION}" "/opt/tpot/bin/genuser.sh";
  then
    # tpotinit writes the web users from WEB_USER of the .env when T-Pot starts
    fuUI_SUMMARY "T-Pot web user" "ok:The web user is in WEB_USER of ${HOME}/tpotce/.env" \
      "next:Restart T-Pot, so the web UI takes it: sudo systemctl restart tpot"
  else
    fuUI_SUMMARY "T-Pot web user" "fail:The tpotinit container did not add a web user" \
      "next:Check that Docker runs and the image ${TPOT_REPO}/tpotinit:${TPOT_VERSION} is there, then run genuser.sh again"
    exit 1
fi
