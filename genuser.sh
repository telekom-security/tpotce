#!/usr/bin/env bash
# Add a user of the T-Pot web UI. This is `tpot users add` now (bcrypt, the change
# counts at once); without the Python packages of tpot (i.e. no internet to set them
# up) the tpotinit container asks for the user as before.
myTPOT="$HOME/tpotce/tpot"
if [ -x "${myTPOT}" ] && "${myTPOT}" setup > /dev/null 2>&1;
  then
    exec "${myTPOT}" users add "$@"
fi
echo "### tpot is not available, using the tpotinit container."
cd "$HOME/tpotce" || exit 1
TPOT_REPO=$(grep -E "^TPOT_REPO" .env | cut -d "=" -f2-)
TPOT_VERSION=$(grep -E "^TPOT_VERSION" .env | cut -d "=" -f2-)
USER=$(id -u)
USERNAME=$(id -un)
GROUP=$(id -g)
echo "### Repository:        ${TPOT_REPO}"
echo "### Version Tag:       ${TPOT_VERSION}"
echo "### Your User Name:    ${USERNAME}"
echo "### Your User ID:      ${USER}"
echo "### Your Group ID:     ${GROUP}"
echo
docker run -v $HOME/tpotce:/data --entrypoint "bash" -it -u "${USER}":"${GROUP}" "${TPOT_REPO}"/tpotinit:"${TPOT_VERSION}" "/opt/tpot/bin/genuser.sh"
