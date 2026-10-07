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
