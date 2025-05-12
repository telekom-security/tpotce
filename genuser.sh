#!/usr/bin/env bash
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
