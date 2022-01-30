#!/bin/bash
# Needs buildx to build. Run tpotce/bin/setup-builder.sh first
docker buildx build --output ../../dist/html/esvue/ .
