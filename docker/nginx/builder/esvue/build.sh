#!/bin/bash
# Needs buildx to build. Run tpotce/bin/setup-builder.sh first
echo "do not build!"
exit 0
docker buildx build --no-cache --progress plain --output ../../dist/html/esvue/ .
