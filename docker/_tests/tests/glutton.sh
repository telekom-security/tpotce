#!/usr/bin/env bash
set -Eeuo pipefail
unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"

DEFAULT_IMAGE="dtagdevsec/glutton:24.04.2"
DEFAULT_INIT_IMAGE="dtagdevsec/tpotinit:24.04.2"
IMAGE="$(test_read_compose_image glutton "$DEFAULT_IMAGE")"
INIT_IMAGE="$(test_read_compose_image tpotinit "$DEFAULT_INIT_IMAGE")"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --image|--init-image|--timeout|--bind-ip)
      [[ $# -ge 2 ]] || test_die "$1 requires an argument"
      case "$1" in
        --image) IMAGE="$2" ;;
        --init-image) INIT_IMAGE="$2" ;;
        --timeout) TEST_TIMEOUT="$2" ;;
        --bind-ip) TEST_BIND_IP="$2" ;;
      esac
      shift 2 ;;
    --image=*) IMAGE="${1#*=}"; shift ;;
    --init-image=*) INIT_IMAGE="${1#*=}"; shift ;;
    --timeout=*) TEST_TIMEOUT="${1#*=}"; shift ;;
    --bind-ip=*) TEST_BIND_IP="${1#*=}"; shift ;;
    --keep-artifacts) TEST_KEEP_ARTIFACTS=true; shift ;;
    -h|--help)
      printf 'Usage: %s [--image IMAGE] [--init-image IMAGE] [--timeout SEC] [--bind-ip IP] [--keep-artifacts]\n' "$0"
      printf 'Isolated Glutton/tpotinit TPROXY, service protection, stop, restart and SIGKILL tests.\n'
      printf 'Requires both images locally; no host firewall changes.\n'
      exit 0 ;;
    *) test_die "Unknown option: $1" ;;
  esac
done
test_validate_timeout
test_check_dependencies
test_require_image "$IMAGE" "cd docker/glutton && docker compose build"
test_require_image "$INIT_IMAGE" "cd docker/tpotinit && docker compose build"
test_prepare_harness glutton
test_enable_cleanup
python3 "${SCRIPT_DIR}/../probes/glutton.py" test "$IMAGE" "$INIT_IMAGE" \
  "$TEST_TMP_ROOT" "$TEST_PROJECT_NAME" "$TEST_BIND_IP" "$TEST_TIMEOUT"
test_ok "Glutton: native TPROXY, real services, startup refusal, stop, restart and SIGKILL"
