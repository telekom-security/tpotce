#!/usr/bin/env bash

set -Eeuo pipefail

TEST_LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="$(cd -- "${TEST_LIB_DIR}/.." && pwd)"
REPO_ROOT="$(cd -- "${TEST_ROOT}/../.." && pwd)"
DOCKER_ROOT="${REPO_ROOT}/docker"

TEST_TIMEOUT="${TEST_TIMEOUT:-30}"
TEST_BIND_IP="${TEST_BIND_IP:-127.0.0.1}"
TEST_KEEP_ARTIFACTS="${TEST_KEEP_ARTIFACTS:-false}"

TEST_TMP_ROOT=""
TEST_HARNESS_COMPOSE=""
TEST_PROJECT_NAME=""
TEST_CONTAINER_NAME=""
TEST_ARTIFACT_LOG_DIR=""

test_info() {
  printf '==> %s\n' "$*"
}

test_ok() {
  printf '[OK] %s\n' "$*"
}

test_die() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

test_require_command() {
  command -v "$1" >/dev/null 2>&1 || test_die "Required command not found: $1"
}

test_check_dependencies() {
  [[ -n "${BASH_VERSION:-}" ]] || test_die "This test suite requires bash"
  test_require_command docker
  test_require_command python3
  docker compose version >/dev/null 2>&1 || test_die "Docker Compose plugin is required"
  docker info >/dev/null 2>&1 || test_die "Docker daemon is not accessible"
}

test_validate_timeout() {
  [[ "${TEST_TIMEOUT}" =~ ^[0-9]+$ ]] || test_die "--timeout must be a number"
  (( TEST_TIMEOUT >= 1 )) || test_die "--timeout must be at least 1"
}

test_validate_port() {
  local port="$1"

  [[ "${port}" =~ ^[0-9]+$ ]] || test_die "Port must be a number: ${port}"
  (( port >= 1 && port <= 65535 )) || test_die "Port must be between 1 and 65535: ${port}"
}

test_read_compose_image() {
  local service="$1"
  local fallback="$2"
  local compose_file="${DOCKER_ROOT}/${service}/docker-compose.yml"
  local detected=""

  if [[ -f "${compose_file}" ]]; then
    detected="$(
      sed -n 's/^[[:space:]]*image:[[:space:]]*//p' "${compose_file}" \
        | head -n 1 \
        | tr -d "\"'"
    )"
  fi

  if [[ -n "${detected}" ]]; then
    printf '%s\n' "${detected}"
  else
    printf '%s\n' "${fallback}"
  fi
}

test_require_image() {
  local image="$1"
  local build_hint="$2"

  docker image inspect "${image}" >/dev/null 2>&1 || test_die "Image not found: ${image}. Build it first, for example: ${build_hint}"
}

test_ensure_port_free() {
  local bind_ip="$1"
  local host_port="$2"

  python3 - "${bind_ip}" "${host_port}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError as exc:
    print(f"{host}:{port} is not available: {exc}", file=sys.stderr)
    sys.exit(1)
finally:
    sock.close()
PY
}

test_ensure_udp_port_free() {
  local bind_ip="$1"
  local host_port="$2"

  python3 - "${bind_ip}" "${host_port}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.bind((host, port))
except OSError as exc:
    print(f"{host}:{port}/udp is not available: {exc}", file=sys.stderr)
    sys.exit(1)
finally:
    sock.close()
PY
}

test_prepare_harness() {
  local test_name="$1"

  TEST_TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/tpot-${test_name}.XXXXXX")"
  TEST_HARNESS_COMPOSE="${TEST_TMP_ROOT}/docker-compose.yml"
  TEST_PROJECT_NAME="tpot-${test_name}-$(date +%s)-$$"
  TEST_CONTAINER_NAME="${TEST_PROJECT_NAME}-service"

  chmod 0777 "${TEST_TMP_ROOT}"
}

test_compose() {
  docker compose -f "${TEST_HARNESS_COMPOSE}" -p "${TEST_PROJECT_NAME}" "$@"
}

test_wait_for_container() {
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local state=""

  while (( SECONDS < deadline )); do
    state="$(docker inspect -f '{{.State.Status}}' "${TEST_CONTAINER_NAME}" 2>/dev/null || true)"
    case "${state}" in
      running)
        return 0
        ;;
      exited|dead)
        return 1
        ;;
    esac
    sleep 1
  done

  return 1
}

test_get_mapped_port() {
  local service="$1"
  local container_port="$2"
  local mapping=""
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    mapping="$(test_compose port "${service}" "${container_port}" 2>/dev/null | tail -n 1 || true)"
    if [[ -z "${mapping}" && -n "${TEST_CONTAINER_NAME}" ]]; then
      mapping="$(docker port "${TEST_CONTAINER_NAME}" "${container_port}" 2>/dev/null | tail -n 1 || true)"
    fi
    if [[ -n "${mapping}" ]]; then
      printf '%s\n' "${mapping##*:}"
      return 0
    fi
    sleep 1
  done

  return 1
}

test_wait_for_file_text() {
  local text="$1"
  local directory="$2"
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if grep -R -F -- "${text}" "${directory}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  return 1
}

test_show_diagnostics() {
  printf '\n[diagnostics] Container state\n' >&2
  if [[ -n "${TEST_CONTAINER_NAME}" ]]; then
    docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} error={{.State.Error}}' "${TEST_CONTAINER_NAME}" >&2 || true
  else
    printf 'No container name available.\n' >&2
  fi

  printf '\n[diagnostics] Docker logs\n' >&2
  if [[ -n "${TEST_HARNESS_COMPOSE}" && -f "${TEST_HARNESS_COMPOSE}" ]]; then
    test_compose logs --no-color --tail=120 >&2 || true
  else
    printf 'No temporary compose file available.\n' >&2
  fi

  if [[ -n "${TEST_ARTIFACT_LOG_DIR}" ]]; then
    printf '\n[diagnostics] Test log artifacts\n' >&2
    if [[ -d "${TEST_ARTIFACT_LOG_DIR}" ]]; then
      find "${TEST_ARTIFACT_LOG_DIR}" -maxdepth 1 -type f -print | sort >&2 || true
      while IFS= read -r file; do
        printf '\n--- %s ---\n' "${file}" >&2
        tail -n 80 "${file}" >&2 || true
      done < <(find "${TEST_ARTIFACT_LOG_DIR}" -maxdepth 1 -type f -print | sort)
    else
      printf 'No temporary log directory available.\n' >&2
    fi
  fi
}

test_cleanup() {
  local status=$?
  trap - EXIT

  if (( status != 0 )); then
    test_show_diagnostics
  fi

  if [[ -n "${TEST_HARNESS_COMPOSE}" && -f "${TEST_HARNESS_COMPOSE}" ]]; then
    test_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi

  if [[ "${TEST_KEEP_ARTIFACTS}" == "true" ]]; then
    if [[ -n "${TEST_TMP_ROOT}" ]]; then
      printf 'Artifacts kept at: %s\n' "${TEST_TMP_ROOT}" >&2
    fi
  elif [[ -n "${TEST_TMP_ROOT}" && -d "${TEST_TMP_ROOT}" ]]; then
    rm -rf -- "${TEST_TMP_ROOT}"
  fi

  exit "${status}"
}

test_enable_cleanup() {
  trap test_cleanup EXIT
  trap 'exit 130' INT
}
