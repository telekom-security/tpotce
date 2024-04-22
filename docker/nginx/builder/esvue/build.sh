#!/bin/bash
# Needs buildx to build. Run tpotce/bin/setup-builder.sh first
docker buildx build --no-cache --progress plain --output ../../dist/html/esvue/ .
