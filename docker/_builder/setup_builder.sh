#!/usr/bin/env bash

# ANSI color codes for green (OK) and red (FAIL)
BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if the user is in the docker group, root does not need to be
if [ "$EUID" -ne 0 ] && ! groups $(whoami) | grep &>/dev/null '\bdocker\b'; then
    echo -e "${RED}You need to be in the docker group to run this script without root privileges.${NC}"
    echo "Please run the following command to add yourself to the docker group:"
    echo "  sudo usermod -aG docker $(whoami)"
    echo "Then log out and log back in or run the script with sudo."
    exit 1
fi

# Undo the setup: the builder, what is left of it, the QEMU emulation and the
# images used for all of that. Steps with nothing to remove are skipped.
uninstall_builder() {
    local failed=0

    # buildx keeps its builders per user - builder.sh creates 'mybuilder' as root,
    # this script as the calling user - so a builder may exist in either
    echo -n "Removing buildx builder 'mybuilder'..."
    if docker buildx inspect mybuilder >/dev/null 2>&1; then
        if docker buildx rm mybuilder >/dev/null 2>&1; then
            echo -e " [${GREEN}OK${NC}]"
        else
            echo -e " [${RED}FAIL${NC}]"
            failed=1
        fi
    else
        echo " not found, skipping."
    fi

    # The container and its state volume are shared by both, so whatever the
    # other user's builder left behind goes as well
    echo -n "Removing leftover BuildKit container and volume..."
    docker rm -f buildx_buildkit_mybuilder0 >/dev/null 2>&1
    docker volume rm buildx_buildkit_mybuilder0_state >/dev/null 2>&1
    if docker ps -a --format '{{.Names}}' | grep -qx buildx_buildkit_mybuilder0 || \
       docker volume ls --format '{{.Name}}' | grep -qx buildx_buildkit_mybuilder0_state; then
        echo -e " [${RED}FAIL${NC}]"
        failed=1
    else
        echo -e " [${GREEN}OK${NC}]"
    fi

    # Unregisters every qemu-* handler, not only the ones installed here
    echo "Note: this removes all QEMU binfmt handlers on this host, including those of e.g. qemu-user-static."
    echo -n "Removing QEMU emulation for cross-platform builds..."
    if ls /proc/sys/fs/binfmt_misc/qemu-* >/dev/null 2>&1; then
        if docker run --rm --privileged tonistiigi/binfmt --uninstall 'qemu-*' >/dev/null 2>&1 && \
           ! ls /proc/sys/fs/binfmt_misc/qemu-* >/dev/null 2>&1; then
            echo -e " [${GREEN}OK${NC}]"
        else
            echo -e " [${RED}FAIL${NC}]"
            failed=1
        fi
    else
        echo " not registered, skipping."
    fi

    echo -n "Removing images moby/buildkit and tonistiigi/binfmt..."
    local images=""
    images=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(moby/buildkit|tonistiigi/binfmt):')
    if [ -z "$images" ]; then
        echo " not found, skipping."
    elif docker rmi $images >/dev/null 2>&1; then
        echo -e " [${GREEN}OK${NC}]"
    else
        echo -e " [${RED}FAIL${NC}]"
        failed=1
    fi

    echo
    if [ "$failed" -eq 0 ]; then
        echo -e "${BLUE}### Done, the multi-arch build setup has been removed.${NC}"
    else
        echo -e "${RED}### Some steps failed, see above.${NC}"
    fi
    echo
    exit $failed
}

# Command-line switch check
if [ "$1" == "-u" ]; then
    uninstall_builder
fi
if [ "$1" != "-y" ]; then
    echo "### Setting up Docker for Multi-Arch Builds."
    echo "### Requires Docker packages from https://get.docker.com/"
    echo "### Run with -y if you meet the requirements!"
    echo "### Run with -u to remove the setup again (builder, QEMU emulation, images)."
    exit 0
fi

# Check if the mybuilder exists and is running
echo -n "Checking if buildx builder 'mybuilder' exists and is running..."
if ! docker buildx inspect mybuilder --bootstrap >/dev/null 2>&1; then
    echo
    echo -n "  Creating and starting buildx builder 'mybuilder'..."
    if docker buildx create --name mybuilder --driver docker-container --use >/dev/null 2>&1 && \
       docker buildx inspect mybuilder --bootstrap >/dev/null 2>&1; then
        echo -e " [${GREEN}OK${NC}]"
    else
        echo -e " [${RED}FAIL${NC}]"
        exit 1
    fi
else
    echo -e " [${GREEN}OK${NC}]"
fi

# Ensure QEMU is set up for cross-platform builds
echo -n "Ensuring QEMU is configured for cross-platform builds..."
if docker run --rm --privileged tonistiigi/binfmt --install all >/dev/null 2>&1; then
    echo -e " [${GREEN}OK${NC}]"
else
    echo -e " [${RED}FAIL${NC}]"
    exit 1
fi

# Ensure arm64 and amd64 platforms are active
echo -n "Ensuring 'mybuilder' supports linux/arm64 and linux/amd64..."
active_platforms=$(docker buildx inspect mybuilder --bootstrap | grep -oP '(?<=Platforms: ).*')

if [[ "$active_platforms" == *"linux/arm64"* && "$active_platforms" == *"linux/amd64"* ]]; then
    echo -e " [${GREEN}OK${NC}]"
else
    echo
    # BuildKit only detects the QEMU emulators present when it starts, so a builder
    # started before they were registered has to be restarted - creating it
    # again fails, it already exists
    echo -n "  Restarting 'mybuilder' to enable linux/arm64 and linux/amd64..."
    if docker buildx stop mybuilder >/dev/null 2>&1 && \
       docker buildx inspect mybuilder --bootstrap >/dev/null 2>&1 && \
       active_platforms=$(docker buildx inspect mybuilder | grep -oP '(?<=Platforms: ).*') && \
       [[ "$active_platforms" == *"linux/arm64"* && "$active_platforms" == *"linux/amd64"* ]]; then
        echo -e " [${GREEN}OK${NC}]"
    else
        echo -e " [${RED}FAIL${NC}]"
        exit 1
    fi
fi

echo
echo -e "${BLUE}### Done.${NC}"
echo
echo -e "${BLUE}Examples:${NC}"
echo -e "  ${BLUE}Manual multi-arch build:${NC}"
echo "    docker buildx build --platform linux/amd64,linux/arm64 -t username/demo:latest --push ."
echo
echo -e "  ${BLUE}Documentation:${NC} https://docs.docker.com/desktop/multi-arch/"
echo
echo -e "  ${BLUE}Build release with Docker Compose:${NC}"
echo "    docker compose build"
echo
echo -e "  ${BLUE}Build and push release with Docker Compose:${NC}"
echo "    docker compose build --push"
echo
echo -e "  ${BLUE}Build a single image with Docker Compose:${NC}"
echo "    docker compose build tpotinit"
echo
echo -e "  ${BLUE}Build and push a single image with Docker Compose:${NC}"
echo "    docker compose build tpotinit --push"
echo
echo -e "${BLUE}Resolve buildx issues:${NC}"
echo "    docker buildx create --use --name mybuilder"
echo "    docker buildx inspect mybuilder --bootstrap"
echo "    docker login -u <username>"
echo "    docker login ghcr.io -u <username>"
echo
echo -e "${BLUE}Fix segmentation faults when building arm64 images:${NC}"
echo "    docker buildx rm mybuilder && docker run --rm --privileged tonistiigi/binfmt --install all"
echo
