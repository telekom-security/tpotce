#!/usr/bin/env bash
# Prepares Docker for the multi-arch builds of builder.sh (buildx builder 'mybuilder',
# QEMU emulation), -u removes that again. A tool for building releases, not part of
# the T-Pot Manager.

myREPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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
    fuUI_SPIN () {
      local myTITLE="$1" myLOG="$2"
      shift 2
      echo "### ${myTITLE}"
      if "$@" >>"${myLOG}" 2>&1 < /dev/null;
        then fuUI_OK "${myTITLE%% ...}"
        else fuUI_ERROR "${myTITLE%% ...} failed, the end of ${myLOG}:"; tail -n 15 "${myLOG}" >&2; return 1
      fi
    }
# <<< plain fallback
fi
fuUI_INIT

# Check if the user is in the docker group, root does not need to be
if [ "$EUID" -ne 0 ] && ! groups $(whoami) | grep &>/dev/null '\bdocker\b'; then
    fuUI_ERROR "You need to be in the docker group to run this script without root privileges."
    fuUI_INFO "Please run the following command to add yourself to the docker group:"
    fuUI_HINT "sudo usermod -aG docker $(whoami)"
    fuUI_INFO "Then log out and log back in or run the script with sudo."
    exit 1
fi

# Undo the setup: the builder, what is left of it, the QEMU emulation and the
# images used for all of that. Steps with nothing to remove are skipped.
uninstall_builder() {
    local failed=0

    # buildx keeps its builders per user - builder.sh creates 'mybuilder' as root,
    # this script as the calling user - so a builder may exist in either
    if docker buildx inspect mybuilder >/dev/null 2>&1; then
        if docker buildx rm mybuilder >/dev/null 2>&1; then
            fuUI_OK "Removed buildx builder 'mybuilder'"
        else
            fuUI_ERROR "Could not remove buildx builder 'mybuilder'"
            failed=1
        fi
    else
        fuUI_INFO "Buildx builder 'mybuilder' not found, skipping."
    fi

    # The container and its state volume are shared by both, so whatever the
    # other user's builder left behind goes as well
    docker rm -f buildx_buildkit_mybuilder0 >/dev/null 2>&1
    docker volume rm buildx_buildkit_mybuilder0_state >/dev/null 2>&1
    if docker ps -a --format '{{.Names}}' | grep -qx buildx_buildkit_mybuilder0 || \
       docker volume ls --format '{{.Name}}' | grep -qx buildx_buildkit_mybuilder0_state; then
        fuUI_ERROR "Could not remove the leftover BuildKit container and volume"
        failed=1
    else
        fuUI_OK "No BuildKit container and volume left"
    fi

    # Unregisters every qemu-* handler, not only the ones installed here
    fuUI_WARN "This removes all QEMU binfmt handlers on this host, including those of e.g. qemu-user-static."
    if ls /proc/sys/fs/binfmt_misc/qemu-* >/dev/null 2>&1; then
        if docker run --rm --privileged tonistiigi/binfmt --uninstall 'qemu-*' >/dev/null 2>&1 && \
           ! ls /proc/sys/fs/binfmt_misc/qemu-* >/dev/null 2>&1; then
            fuUI_OK "Removed the QEMU emulation for cross-platform builds"
        else
            fuUI_ERROR "Could not remove the QEMU emulation for cross-platform builds"
            failed=1
        fi
    else
        fuUI_INFO "QEMU emulation not registered, skipping."
    fi

    local images=""
    images=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(moby/buildkit|tonistiigi/binfmt):')
    if [ -z "$images" ]; then
        fuUI_INFO "Images moby/buildkit and tonistiigi/binfmt not found, skipping."
    elif docker rmi $images >/dev/null 2>&1; then
        fuUI_OK "Removed the images moby/buildkit and tonistiigi/binfmt"
    else
        fuUI_ERROR "Could not remove the images moby/buildkit and tonistiigi/binfmt"
        failed=1
    fi

    echo
    if [ "$failed" -eq 0 ]; then
        fuUI_OK "Done, the multi-arch build setup has been removed."
    else
        fuUI_ERROR "Some steps failed, see above."
    fi
    echo
    exit $failed
}

# Command-line switch check
if [ "$1" == "-u" ]; then
    fuUI_BANNER "Builder Setup" "Removing the multi-arch build setup"
    uninstall_builder
fi
if [ "$1" != "-y" ]; then
    fuUI_BANNER "Builder Setup" "Setting up Docker for multi-arch builds." \
                "Requires Docker packages from https://get.docker.com/"
    fuUI_HINT "Run with -y if you meet the requirements!" \
              "Run with -u to remove the setup again (builder, QEMU emulation, images)."
    exit 0
fi

fuUI_BANNER "Builder Setup" "Setting up Docker for multi-arch builds."
myLOG=$(mktemp "${TMPDIR:-/tmp}/tpot-setup-builder.XXXXXX")

# Check if the mybuilder exists and is running
ensure_builder() {
    docker buildx inspect mybuilder --bootstrap && return 0
    echo "Creating and starting buildx builder 'mybuilder'"
    docker buildx create --name mybuilder --driver docker-container --use && \
    docker buildx inspect mybuilder --bootstrap
}

# Ensure arm64 and amd64 platforms are active
ensure_platforms() {
    local active_platforms
    active_platforms=$(docker buildx inspect mybuilder --bootstrap | sed -n 's/.*Platforms: *//p')
    [[ "$active_platforms" == *"linux/arm64"* && "$active_platforms" == *"linux/amd64"* ]] && return 0
    # BuildKit only detects the QEMU emulators present when it starts, so a builder
    # started before they were registered has to be restarted - creating it
    # again fails, it already exists
    echo "Restarting 'mybuilder' to enable linux/arm64 and linux/amd64"
    docker buildx stop mybuilder && \
    docker buildx inspect mybuilder --bootstrap && \
    active_platforms=$(docker buildx inspect mybuilder | sed -n 's/.*Platforms: *//p') && \
    [[ "$active_platforms" == *"linux/arm64"* && "$active_platforms" == *"linux/amd64"* ]]
}

fuUI_SPIN "Checking the buildx builder 'mybuilder' ..." "$myLOG" ensure_builder || exit 1
fuUI_SPIN "Configuring QEMU for cross-platform builds ..." "$myLOG" \
    docker run --rm --privileged tonistiigi/binfmt --install all || exit 1
fuUI_SPIN "Making sure 'mybuilder' builds linux/arm64 and linux/amd64 ..." "$myLOG" ensure_platforms || exit 1
rm -f "$myLOG"

echo
fuUI_OK "Done."
echo
fuUI_INFO "Manual multi-arch build:"
fuUI_HINT "docker buildx build --platform linux/amd64,linux/arm64 -t username/demo:latest --push ."
fuUI_INFO "Documentation: https://docs.docker.com/desktop/multi-arch/"
fuUI_INFO "Build release with Docker Compose:"
fuUI_HINT "docker compose build"
fuUI_INFO "Build and push release with Docker Compose:"
fuUI_HINT "docker compose build --push"
fuUI_INFO "Build a single image with Docker Compose:"
fuUI_HINT "docker compose build tpotinit"
fuUI_INFO "Build and push a single image with Docker Compose:"
fuUI_HINT "docker compose build tpotinit --push"
fuUI_INFO "Resolve buildx issues:"
fuUI_HINT "docker buildx create --use --name mybuilder" \
          "docker buildx inspect mybuilder --bootstrap" \
          "docker login -u <username>" \
          "docker login ghcr.io -u <username>"
fuUI_INFO "Fix segmentation faults when building arm64 images:"
fuUI_HINT "docker buildx rm mybuilder && docker run --rm --privileged tonistiigi/binfmt --install all"
echo
