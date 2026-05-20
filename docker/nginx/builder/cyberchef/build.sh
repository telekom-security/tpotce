#!/bin/bash
# Needs buildx to build. Run tpotce/bin/setup-builder.sh first
set -euo pipefail

cd "$(dirname "$0")"
OUT_DIR="../../dist/html/cyberchef"

docker buildx build --output "${OUT_DIR}/" .

cd "${OUT_DIR}"
sha256sum cyberchef.tgz > cyberchef.tgz.sha256
