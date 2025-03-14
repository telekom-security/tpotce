#!/usr/bin/env bash

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi

# ANSI color codes for green (OK) and red (FAIL)
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default settings
PUSH_IMAGES=false
NO_CACHE=false
PARALLELBUILDS=2
UPLOAD_BANDWIDTH=40mbit # Set this to max 90% of available upload bandwidth
INTERFACE=$(ip route | grep "^default" | awk '{ print $5 }')

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
            docker login
            docker login ghcr.io
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

# Function to apply upload bandwidth limit using tc
apply_bandwidth_limit() {
    echo -n "Applying upload bandwidth limit of $UPLOAD_BANDWIDTH on interface $INTERFACE..."
    if tc qdisc add dev $INTERFACE root tbf rate $UPLOAD_BANDWIDTH burst 32kbit latency 400ms >/dev/null 2>&1; then
        echo -e " [${GREEN}OK${NC}]"
    else
        echo -e " [${RED}FAIL${NC}]"
        remove_bandwidth_limit

        # Try to reapply the limit
        echo -n "Reapplying upload bandwidth limit of $UPLOAD_BANDWIDTH on interface $INTERFACE..."
        if tc qdisc add dev $INTERFACE root tbf rate $UPLOAD_BANDWIDTH burst 32kbit latency 400ms >/dev/null 2>&1; then
            echo -e " [${GREEN}OK${NC}]"
        else
            echo -e " [${RED}FAIL${NC}]"
            echo "Failed to apply bandwidth limit on $INTERFACE. Exiting."
            echo
            exit 1
        fi
    fi
}

# Function to check if the bandwidth limit is set
is_bandwidth_limit_set() {
    tc qdisc show dev $INTERFACE | grep -q 'tbf'
}

# Function to remove the bandwidth limit using tc if it is set
remove_bandwidth_limit() {
    if is_bandwidth_limit_set; then
        echo -n "Removing upload bandwidth limit on interface $INTERFACE..."
        if tc qdisc del dev $INTERFACE root; then
            echo -e " [${GREEN}OK${NC}]"
        else
            echo -e " [${RED}FAIL${NC}]"
        fi
    fi
}

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

# Apply bandwidth limit only if pushing images
if $PUSH_IMAGES; then
    echo
    echo "########################################"
    echo "# Setting Upload Bandwidth limit ..."
    echo "########################################"
    echo
    apply_bandwidth_limit
fi

# Trap to ensure bandwidth limit is removed on script error, exit
trap_cleanup() {
    if is_bandwidth_limit_set; then
        remove_bandwidth_limit
    fi
}
trap trap_cleanup INT ERR EXIT

echo
echo "################################"
echo "# Now building images ..."
echo "################################"
echo

mkdir -p log

# List of services to build
services=$(docker compose config --services | sort)

# Loop through each service to build
echo $services | tr ' ' '\n' | xargs -I {} -P $PARALLELBUILDS bash -c '
    echo "Building image: {}" && \
    build_cmd="docker compose build {}" && \
    if '$PUSH_IMAGES'; then \
        build_cmd="$build_cmd --push"; \
    fi && \
    if '$NO_CACHE'; then \
        build_cmd="$build_cmd --no-cache"; \
    fi && \
    eval "$build_cmd 2>&1 > log/{}.log" && \
    echo -e "Image {}: ['$GREEN'OK'$NC']" || \
    echo -e "Image {}: ['$RED'FAIL'$NC']"
'

# Remove bandwidth limit if it was applied
if is_bandwidth_limit_set; then
    echo
    echo "########################################"
    echo "# Removiong Upload Bandwidth limit ..."
    echo "########################################"
    echo
    remove_bandwidth_limit
fi

echo
echo "#######################################################"
echo "# Done."
if ! "$PUSH_IMAGES"; then
  echo "# Remeber to push the images using push option."
fi
echo "#######################################################"
echo
