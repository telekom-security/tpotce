#!/usr/bin/env bash

# ANSI color codes for green (OK) and red (FAIL)
BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if the user is in the docker group
if ! groups $(whoami) | grep &>/dev/null '\bdocker\b'; then
    echo -e "${RED}You need to be in the docker group to run this script without root privileges.${NC}"
    echo "Please run the following command to add yourself to the docker group:"
    echo "  sudo usermod -aG docker $(whoami)"
    echo "Then log out and log back in or run the script with sudo."
    exit 1
fi

# Command-line switch check
if [ "$1" != "-y" ]; then
    echo "### Setting up Docker for Multi-Arch Builds."
    echo "### Requires Docker packages from https://get.docker.com/"
    echo "### Use on x64 only!"
    echo "### Run with -y if you fit the requirements!"
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
if docker run --rm --privileged multiarch/qemu-user-static --reset -p yes >/dev/null 2>&1; then
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
    echo -n "  Enabling platforms linux/arm64 and linux/amd64..."
    if docker buildx create --name mybuilder --driver docker-container --use --platform linux/amd64,linux/arm64 >/dev/null 2>&1 && \
       docker buildx inspect mybuilder --bootstrap >/dev/null 2>&1; then
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
echo "    docker run --rm --privileged multiarch/qemu-user-static --reset -p yes"
echo
