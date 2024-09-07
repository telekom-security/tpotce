#!/bin/bash

# ANSI color codes for green (OK) and red (FAIL)
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# List of services to build
services="adbhoney nginx map"
#test=$(docker compose config --services)
#echo $test

# Loop through each service
echo $services | tr ' ' '\n' | xargs -I {} -P 3 bash -c '
    echo "Building service: {}" && \
    docker compose build {} --no-cache 2>&1 > {}.log && \
    echo -e "Service {}: [\033[0;32mOK\033[0m]" || \
    echo -e "Service {}: [\033[0;31mFAIL\033[0m]"
'
