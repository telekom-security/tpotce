#!/bin/bash
# Needs buildx to build: docker/_builder/builder.sh --setup sets it up
set -euo pipefail
# an exported CDPATH turns a cd into a search that prints (or goes to) the folder it found
unset CDPATH

cd "$(dirname "$0")"
OUT_DIR="../../dist/html/esvue"

docker buildx build --no-cache --progress plain --output "${OUT_DIR}/" .

cd "${OUT_DIR}"
sha256sum esvue.tgz > esvue.tgz.sha256
