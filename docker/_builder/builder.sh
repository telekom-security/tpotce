#!/bin/bash

# ANSI color codes for green (OK) and red (FAIL)
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default flags
PUSH_IMAGES=false
NO_CACHE=false

# Help message
usage() {
    echo "Usage: $0 [-p] [-n] [-h]"
    echo "  -p  Push images after building"
    echo "  -n  Build images with --no-cache"
    echo "  -h  Show help message"
    exit 1
}

# Parse command-line options
while getopts ":pnh" opt; do
    case ${opt} in
        p )
            PUSH_IMAGES=true
            ;;
        n )
            NO_CACHE=true
            ;;
        h )
            usage
            ;;
        \? )
            echo "Invalid option: $OPTARG" 1>&2
            usage
            ;;
    esac
done

echo "###########################"
echo "# T-Pot Image Builder"
echo "###########################"
echo

# Check if 'mybuilder' exists, and ensure it's running with bootstrap
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

# Ensure arm64 and amd64 platforms are active
echo -n "Ensuring 'mybuilder' supports linux/arm64 and linux/amd64..."

# Get active platforms from buildx
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

# Ensure QEMU is set up for cross-platform builds
echo -n "Ensuring QEMU is configured for cross-platform builds..."
if docker run --rm --privileged multiarch/qemu-user-static --reset -p yes > /dev/null 2>&1; then
    echo -e " [${GREEN}OK${NC}]"
else
    echo -e " [${RED}FAIL${NC}]"
fi

echo
echo "################################"
echo "# Now building images ..."
echo "################################"
echo

mkdir -p log

# List of services to build
#services=$(docker compose config --services)
services="tpotinit beelzebub nginx p0f"

# Loop through each service
echo $services | tr ' ' '\n' | xargs -I {} -P 3 bash -c '
    echo "Building image: {}" && \
    build_cmd="docker compose build {}" && \
    if '$PUSH_IMAGES'; then \
        build_cmd="$build_cmd --push"; \
    fi && \
    if '$NO_CACHE'; then \
        build_cmd="$build_cmd --no-cache"; \
    fi && \
    eval "$build_cmd 2>&1 > log/{}.log" && \
    echo -e "Service {}: ['$GREEN'OK'$NC']" || \
    echo -e "Service {}: ['$RED'FAIL'$NC']"
'

echo
echo "#######################################################"
echo "# Done."
if ! "$PUSH_IMAGES"; then
  echo "# Remeber to push the images using push option."
fi
echo "#######################################################"
echo
