#!/bin/bash
# Needs buildx to build. Run tpotce/bin/setup-builder.sh first
set -euo pipefail

cd "$(dirname "$0")"
OUT_DIR="../../dist/html/esvue"

docker buildx build --no-cache --progress plain --output "${OUT_DIR}/" .

cd "${OUT_DIR}"
sha256sum esvue.tgz > esvue.tgz.sha256
