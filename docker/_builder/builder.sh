#!/usr/bin/env bash
# Builds every image of docker-compose.yml here for linux/amd64 and linux/arm64 (buildx),
# -p pushes them to Docker Hub and GHCR. A tool for building releases, not part of the
# T-Pot Manager; run it from docker/_builder as root.

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

# Default settings
PUSH_IMAGES=false
NO_CACHE=false
PARALLELBUILDS=2
UPLOAD_BANDWIDTH=40mbit # Set this to max 90% of available upload bandwidth

# Help message
usage() {
    fuUI_BANNER "Image Builder" "Builds every image of docker-compose.yml here for linux/amd64 and linux/arm64."
    fuUI_INFO "Usage: $0 [-p] [-n] [-h]"
    fuUI_HINT "-p  Push images after building (Docker Hub and GHCR)" \
              "-n  Build images with --no-cache" \
              "-h  Show this help"
    exit "$1"
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
            usage 0
            ;;
        \? )
            fuUI_ERROR "Invalid option: -$OPTARG"
            usage 1
            ;;
    esac
done

# Got root?
if [ "$(whoami)" != "root" ];
  then
    fuUI_ERROR "The image builder needs root, run it with sudo."
    exit 1
fi

INTERFACE=$(ip route | grep "^default" | awk '{ print $5 }')

# Function to apply upload bandwidth limit using tc
apply_bandwidth_limit() {
    if tc qdisc add dev "$INTERFACE" root tbf rate $UPLOAD_BANDWIDTH burst 32kbit latency 400ms >/dev/null 2>&1; then
        fuUI_OK "Upload bandwidth limited to $UPLOAD_BANDWIDTH on $INTERFACE"
        return
    fi
    fuUI_WARN "Could not limit the upload bandwidth on $INTERFACE, removing an old limit and trying again."
    remove_bandwidth_limit
    if tc qdisc add dev "$INTERFACE" root tbf rate $UPLOAD_BANDWIDTH burst 32kbit latency 400ms >/dev/null 2>&1; then
        fuUI_OK "Upload bandwidth limited to $UPLOAD_BANDWIDTH on $INTERFACE"
    else
        fuUI_ERROR "Failed to apply the bandwidth limit on $INTERFACE. Exiting."
        echo
        exit 1
    fi
}

# Function to check if the bandwidth limit is set
is_bandwidth_limit_set() {
    tc qdisc show dev "$INTERFACE" | grep -q 'tbf'
}

# Function to remove the bandwidth limit using tc if it is set
remove_bandwidth_limit() {
    if is_bandwidth_limit_set; then
        if tc qdisc del dev "$INTERFACE" root; then
            fuUI_OK "Upload bandwidth limit on $INTERFACE removed"
        else
            fuUI_ERROR "Could not remove the upload bandwidth limit on $INTERFACE"
        fi
    fi
}

# Check if 'mybuilder' exists, and ensure it's running with bootstrap
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

myMODE="$PARALLELBUILDS builds at a time"
$PUSH_IMAGES && myMODE="$myMODE, pushed to Docker Hub and GHCR"
$NO_CACHE && myMODE="$myMODE, without cache"
fuUI_BANNER "Image Builder" "linux/amd64 and linux/arm64, $myMODE"

if $PUSH_IMAGES; then
    docker login
    docker login ghcr.io
fi

mkdir -p log
myLOG="log/builder.log"
: > "$myLOG"
fuUI_SPIN "Checking the buildx builder 'mybuilder' ..." "$myLOG" ensure_builder || exit 1
# QEMU before the platforms are checked
fuUI_SPIN "Configuring QEMU for cross-platform builds ..." "$myLOG" \
    docker run --rm --privileged tonistiigi/binfmt --install all
fuUI_SPIN "Making sure 'mybuilder' builds linux/arm64 and linux/amd64 ..." "$myLOG" ensure_platforms || exit 1

# Apply bandwidth limit only if pushing images
if $PUSH_IMAGES; then
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
fuUI_INFO "Now building images, the log of each is log/<image>.log ..."

# List of services to build
services=$(docker compose config --services | sort)

# Loop through each service to build
# Plain progress and both streams in the log: builds run in parallel, and with
# only stdout redirected compose picks tty progress for a file and fails with
# "failed to get console" (docker/compose#14182). The builds report one line each,
# this shell shows them.
myBUILD='
    echo "START $1"
    build_cmd="docker compose --progress plain build $1"
    if '$PUSH_IMAGES'; then
        build_cmd="$build_cmd --push"
    fi
    if '$NO_CACHE'; then
        build_cmd="$build_cmd --no-cache"
    fi
    if eval "$build_cmd > log/$1.log 2>&1 < /dev/null"; then
        echo "OK $1"
    else
        echo "FAIL $1"
    fi
'
myFAILED=0
myREPORTED=0
while read -r myRESULT myIMAGE; do
    case "$myRESULT" in
        START) fuUI_INFO "Building $myIMAGE ..." ;;
        OK) fuUI_OK "Image $myIMAGE"; myREPORTED=$((myREPORTED + 1)) ;;
        *) fuUI_ERROR "Image $myIMAGE, see log/$myIMAGE.log"; myFAILED=1; myREPORTED=$((myREPORTED + 1)) ;;
    esac
done < <(echo $services | tr ' ' '\n' | xargs -n 1 -P $PARALLELBUILDS bash -c "$myBUILD" _)
myCOUNT=$(echo $services | wc -w)
if [ "$myREPORTED" -ne "$myCOUNT" ]; then
    fuUI_ERROR "Only $myREPORTED of $myCOUNT builds reported back, see above."
    myFAILED=1
fi

# Remove bandwidth limit if it was applied
if is_bandwidth_limit_set; then
    echo
    remove_bandwidth_limit
fi

echo
if [ "$myFAILED" -eq 0 ]; then
    fuUI_OK "Done."
else
    fuUI_ERROR "Done, but not every image was built, see above."
fi
if ! "$PUSH_IMAGES"; then
    fuUI_HINT "Remember to push the images with -p."
fi
echo
exit "$myFAILED"
