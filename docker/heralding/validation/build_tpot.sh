#!/usr/bin/env bash
# Build the pinned local source without publishing it to a remote repository.
set -euo pipefail
# an exported CDPATH turns a cd into a search that prints (or goes to) the folder it found
unset CDPATH
tpot_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
project_dir="${1:-${tpot_dir}/../heralding}"
image="${2:-heralding:tpot-dev}"
source_commit="$(sed -n 's/^ARG HERALDING_COMMIT=//p' "${tpot_dir}/docker/heralding/Dockerfile")"
source_context="$(mktemp -d "${TMPDIR:-/tmp}/heralding-source.XXXXXX")"
trap 'rm -rf -- "$source_context"' EXIT
mkdir "${source_context}/heralding"
git -C "$project_dir" archive --output="${source_context}/source.tar" "$source_commit"
tar -xf "${source_context}/source.tar" -C "${source_context}/heralding"
# Runtime images exclude the tests, as does the main Dockerfile's .dockerignore.
rm -rf -- "${source_context}/heralding/heralding/tests"
docker build --build-context "heralding_source=${source_context}" \
    -t "$image" "${tpot_dir}/docker/heralding"
