#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="redis"
DEFAULT_IMAGE="dtagdevsec/redis:24.04.2"
IMAGE=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Redis image of the Attack Map.

Options:
  --image IMAGE       Image to test. Defaults to docker/redis/docker-compose.yml.
  --timeout SEC       Timeout for startup and checks. Default: 30.
  --bind-ip IP        Accepted for the common options, Redis publishes no port.
  --keep-artifacts    Keep temporary compose file for debugging.
  -h, --help          Show this help message.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image)
        [[ $# -ge 2 ]] || test_die "--image requires an argument"
        IMAGE="$2"
        shift 2
        ;;
      --image=*)
        IMAGE="${1#*=}"
        shift
        ;;
      --timeout)
        [[ $# -ge 2 ]] || test_die "--timeout requires an argument"
        TEST_TIMEOUT="$2"
        shift 2
        ;;
      --timeout=*)
        TEST_TIMEOUT="${1#*=}"
        shift
        ;;
      --bind-ip)
        [[ $# -ge 2 ]] || test_die "--bind-ip requires an argument"
        TEST_BIND_IP="$2"
        shift 2
        ;;
      --bind-ip=*)
        TEST_BIND_IP="${1#*=}"
        shift
        ;;
      --keep-artifacts)
        TEST_KEEP_ARTIFACTS="true"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        test_die "Unknown option: $1"
        ;;
    esac
  done
}

prepare_redis_harness() {
  test_prepare_harness "${TEST_NAME}"

  # as map_redis runs it: read-only, no port on the host
  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  redis:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    stop_signal: SIGKILL
    tty: true
    read_only: true
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

redis_cli() {
  docker exec "${TEST_CONTAINER_NAME}" redis-cli "$@"
}

wait_for_pong() {
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if [[ "$(redis_cli PING 2>/dev/null || true)" == "PONG" ]]; then
      return 0
    fi
    sleep 1
  done

  return 1
}

main() {
  parse_args "$@"
  test_validate_timeout
  test_check_dependencies

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  prepare_redis_harness
  test_enable_cleanup

  test_info "Starting isolated Redis container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Redis container did not stay running"
  test_ok "Container is running"

  wait_for_pong || test_die "Redis did not answer PING with PONG"
  test_ok "Redis answers PING"

  [[ "$(docker exec "${TEST_CONTAINER_NAME}" id -u)" == "2000" ]] || test_die "Redis does not run as uid 2000"
  test_ok "Redis runs as uid 2000"

  local token="redis-test-$(date +%s)-$$"
  [[ "$(redis_cli SET tpot_smoke "${token}")" == "OK" ]] || test_die "SET failed"
  [[ "$(redis_cli GET tpot_smoke)" == "${token}" ]] || test_die "GET did not return the stored value"
  test_ok "SET and GET work on the read-only root file system"

  [[ "$(redis_cli CONFIG GET save | tail -n 1)" == "" ]] || test_die "Redis persists to disk, /etc/redis.conf was not used"
  test_ok "Redis keeps its data in memory only (/etc/redis.conf)"

  test_wait_for_container || test_die "Redis container stopped during the checks"
  test_ok "Redis post-build smoke test completed successfully"
}

main "$@"
