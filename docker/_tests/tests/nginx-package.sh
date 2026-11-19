#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"

DEFAULT_IMAGE="ghcr.io/telekom-security/nginx:24.04.2"
IMAGE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image|--timeout|--bind-ip)
      [[ $# -ge 2 ]] || test_die "$1 requires an argument"
      if [[ "$1" == "--image" ]]; then IMAGE="$2"; fi
      shift 2
      ;;
    --image=*)
      IMAGE="${1#*=}"
      shift
      ;;
    --timeout=*|--bind-ip=*|--keep-artifacts)
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--image IMAGE] [--timeout SEC] [--bind-ip IP] [--keep-artifacts]"
      exit 0
      ;;
    *)
      test_die "Unknown option: $1"
      ;;
  esac
done

test_check_dependencies
IMAGE="${IMAGE:-$(test_read_compose_image nginx "${DEFAULT_IMAGE}")}"
test_require_image "${IMAGE}" "docker compose -f docker/nginx/docker-compose.yml build nginx"

docker run --rm --entrypoint apk "${IMAGE}" info -e 'nginx>=1.30.7-r0' \
  || test_die "Image ${IMAGE} does not contain nginx 1.30.7-r0 or newer (nginx2root)"
test_ok "nginx package includes the nginx2root fixes"
