#!/usr/bin/env bash
# Add a user of the T-Pot web UI. This is `tpot users add` now (bcrypt, the change
# counts at once); without the Python packages of tpot (i.e. no internet to set them
# up) the tpotinit container asks for the user as before.
myTPOT="$HOME/tpotce/tpot"
if [ -x "${myTPOT}" ] && "${myTPOT}" setup > /dev/null 2>&1;
  then
    exec "${myTPOT}" users add "$@"
fi
# the look of the T-Pot scripts (installer/lib/ui.sh), plain text if it is missing
# shellcheck source=installer/lib/ui.sh
if ! source "$HOME/tpotce/installer/lib/ui.sh" 2>/dev/null;
  then
# >>> plain fallback
    fuUI_INIT () { return 0; }
    fuUI_BANNER () { echo; echo "### T-Pot $1"; shift; for myLINE in "$@"; do echo "### ${myLINE}"; done; echo; }
    fuUI_INFO () { echo "### $*"; }
    fuUI_OK () { echo "### [OK] - $*"; }
    fuUI_WARN () { echo "### [WARNING] - $*"; }
    fuUI_ERROR () { echo "### [ERROR] - $*" >&2; }
    fuUI_HINT () { local myLINE; for myLINE in "$@"; do echo "###   ${myLINE}"; done; }
    fuUI_CONFIRM () { local myANSWER; read -rp "### $1 (y/n) " myANSWER; [[ "${myANSWER}" =~ ^(y|Y|yes|YES)$ ]]; }
    fuUI_INPUT () { local myVALUE; read -rp "### $1 " myVALUE; echo "${myVALUE}"; }
# <<< plain fallback
fi
fuUI_INIT
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
docker run -v $HOME/tpotce:/data --entrypoint "bash" -it -u "${USER}":"${GROUP}" "${TPOT_REPO}"/tpotinit:"${TPOT_VERSION}" "/opt/tpot/bin/genuser.sh"
