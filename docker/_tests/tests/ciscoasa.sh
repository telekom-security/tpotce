#!/usr/bin/env bash
set -Eeuo pipefail
unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"
TEST_NAME="ciscoasa"
DEFAULT_IMAGE="dtagdevsec/ciscoasa:24.04.2"
IMAGE=""
HTTPS_PORT="0"
IKE_PORT="0"
NATT_PORT="0"
usage() {
  cat <<HELP
Usage: $0 [--image IMAGE] [--https-port PORT] [--ike-port PORT] [--natt-port PORT]
          [--timeout SEC] [--bind-ip IP] [--keep-artifacts]

Test WebVPN, AnyConnect, inert uploads, JSON events and IKE on 500/4500.
Host ports are assigned dynamically by default. The image must already exist.
HELP
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --image|--https-port|--ike-port|--natt-port|--timeout|--bind-ip)
      [[ $# -ge 2 ]] || test_die "$1 requires an argument"
      case "$1" in
        --image) IMAGE="$2" ;; --https-port) HTTPS_PORT="$2" ;; --ike-port) IKE_PORT="$2" ;;
        --natt-port) NATT_PORT="$2" ;; --timeout) TEST_TIMEOUT="$2" ;; --bind-ip) TEST_BIND_IP="$2" ;;
      esac
      shift 2 ;;
    --*=*) set -- "${1%%=*}" "${1#*=}" "${@:2}" ;;
    --keep-artifacts) TEST_KEEP_ARTIFACTS=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) test_die "Unknown option: $1" ;;
  esac
done
test_validate_timeout
for port in "$HTTPS_PORT" "$IKE_PORT" "$NATT_PORT"; do [[ "$port" == 0 ]] || test_validate_port "$port"; done
test_check_dependencies
[[ -n "$IMAGE" ]] || IMAGE="$(test_read_compose_image "$TEST_NAME" "$DEFAULT_IMAGE")"
test_require_image "$IMAGE" "docker compose -f docker/ciscoasa/docker-compose.yml build ciscoasa"
test_prepare_harness "$TEST_NAME"
TEST_ARTIFACT_LOG_DIR="${TEST_TMP_ROOT}/log"
mkdir -p "${TEST_TMP_ROOT}/log" "${TEST_TMP_ROOT}/identity" "${TEST_TMP_ROOT}/payloads"
chmod 0777 "${TEST_TMP_ROOT}/log" "${TEST_TMP_ROOT}/identity" "${TEST_TMP_ROOT}/payloads"
cat > "${TEST_HARNESS_COMPOSE}" <<YAML
services:
  ciscoasa:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    read_only: true
    user: "2000:2000"
    ports:
      - "${TEST_BIND_IP}:${HTTPS_PORT}:8443"
      - "${TEST_BIND_IP}:${IKE_PORT}:500/udp"
      - "${TEST_BIND_IP}:${NATT_PORT}:4500/udp"
    volumes:
      - "${TEST_TMP_ROOT}/log:/var/log/ciscoasa"
      - "${TEST_TMP_ROOT}/identity:/var/lib/ciscoasa"
      - "${TEST_TMP_ROOT}/payloads:/var/lib/ciscoasa/payloads"
YAML
test_enable_cleanup
test_compose up -d --no-build >/dev/null
test_wait_for_container || test_die "Ciscoasa did not stay running"
HTTPS_PORT="$(test_get_mapped_port ciscoasa 8443)"
IKE_PORT="$(test_get_mapped_port ciscoasa 500/udp)"
NATT_PORT="$(test_get_mapped_port ciscoasa 4500/udp)"
python3 "${SCRIPT_DIR}/../probes/ciscoasa.py" "$TEST_BIND_IP" "$HTTPS_PORT" "$IKE_PORT" "$NATT_PORT" "$TEST_TMP_ROOT" "$TEST_TIMEOUT"
# Ensure identity persistence across a restart, and log reopening remains usable.
cp "${TEST_TMP_ROOT}/identity/identity.json" "${TEST_TMP_ROOT}/identity.before.json"
test_compose restart ciscoasa >/dev/null
HTTPS_PORT="$(test_get_mapped_port ciscoasa 8443)"
IKE_PORT="$(test_get_mapped_port ciscoasa 500/udp)"
NATT_PORT="$(test_get_mapped_port ciscoasa 4500/udp)"
python3 "${SCRIPT_DIR}/../probes/ciscoasa.py" "$TEST_BIND_IP" "$HTTPS_PORT" "$IKE_PORT" "$NATT_PORT" "$TEST_TMP_ROOT" "$TEST_TIMEOUT"
cmp "${TEST_TMP_ROOT}/identity.before.json" "${TEST_TMP_ROOT}/identity/identity.json" || test_die "Identity changed after restart"
test_ok "Ciscoasa protocols, logs, upload storage and persistent identity verified"
