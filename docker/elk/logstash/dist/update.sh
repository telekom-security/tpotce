#!/bin/bash

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  exit 0
}
trap fuCLEANUP EXIT

# Download updated translation maps
cd /etc/listbot 
git pull --all --depth=1
cd /
