#!/usr/bin/env bash

# Got root?
myWHOAMI=$(whoami)
if [ "$myWHOAMI" != "root" ]
  then
    echo "Need to run as root ..."
    exit
fi

# Only run with command switch
if [ "$1" != "-y" ]; then
  echo "### Setting up docker for Multi Arch Builds."
  echo "### Requires Docker packages from https://get.docker.com/"
  echo "### Use on x64 only!"
  echo "### Run with -y if you fit the requirements!"
  echo
  exit
fi

# We need to create a new builder as the default one cannot handle multi-arch builds
# https://docs.docker.com/desktop/multi-arch/
docker buildx create --name mybuilder

# Set as default
docker buildx use mybuilder

# We need to install emulators, arm64 should be fine for now
# https://github.com/tonistiigi/binfmt/
docker run --privileged --rm tonistiigi/binfmt --install arm64

# Check if everything is setup correctly
docker buildx inspect --bootstrap
echo
echo "### Done."
echo
echo "Example: docker buildx build --platform linux/amd64,linux/arm64 -t username/demo:latest --push ."
echo "Docs: https://docs.docker.com/desktop/multi-arch/"
