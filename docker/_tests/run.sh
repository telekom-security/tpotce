#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="${SCRIPT_DIR}/tests"

LIST_ONLY="false"
TEST_TIMEOUT="30"
TEST_BIND_IP="127.0.0.1"
TEST_KEEP_ARTIFACTS="false"
SELECTED_TESTS=()
DEPENDENCY_ERRORS=()

usage() {
  cat <<EOF
Usage: $0 [options] [test ...]

Run T-Pot Docker post-build smoke tests.

Options:
  --list             List available tests.
  --timeout SEC      Timeout passed to each test. Default: 30.
  --bind-ip IP       Host IP used by tests for loopback bindings. Default: 127.0.0.1.
  --keep-artifacts   Keep temporary compose files and logs for failed or passed tests.
  -h, --help         Show this help message.

Examples:
  $0 --list
  $0
  $0 adbhoney
EOF
}

die() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

dependency_error() {
  DEPENDENCY_ERRORS+=("$*")
}

require_command_for() {
  local owner="$1"
  local command_name="$2"

  command -v "${command_name}" >/dev/null 2>&1 || dependency_error "${owner}: required command not found: ${command_name}"
}

require_one_command_for() {
  local owner="$1"
  shift
  local command_name=""

  for command_name in "$@"; do
    if command -v "${command_name}" >/dev/null 2>&1; then
      return 0
    fi
  done

  dependency_error "${owner}: required command not found: one of $*"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --list)
        LIST_ONLY="true"
        shift
        ;;
      --timeout)
        [[ $# -ge 2 ]] || die "--timeout requires an argument"
        TEST_TIMEOUT="$2"
        shift 2
        ;;
      --timeout=*)
        TEST_TIMEOUT="${1#*=}"
        shift
        ;;
      --bind-ip)
        [[ $# -ge 2 ]] || die "--bind-ip requires an argument"
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
      -*)
        die "Unknown option: $1"
        ;;
      *)
        SELECTED_TESTS+=("$1")
        shift
        ;;
    esac
  done
}

validate_args() {
  [[ "${TEST_TIMEOUT}" =~ ^[0-9]+$ ]] || die "--timeout must be a number"
  (( TEST_TIMEOUT >= 1 )) || die "--timeout must be at least 1"
}

list_tests() {
  local test_file=""

  find "${TEST_DIR}" -maxdepth 1 -type f -name '*.sh' -perm -u+x -print \
    | sort \
    | while IFS= read -r test_file; do
        basename "${test_file}" .sh
      done
}

test_path_for() {
  local test_name="$1"
  local test_path="${TEST_DIR}/${test_name}.sh"

  [[ -x "${test_path}" ]] || return 1
  printf '%s\n' "${test_path}"
}

validate_selected_tests() {
  local test_name=""

  for test_name in "${SELECTED_TESTS[@]}"; do
    test_path_for "${test_name}" >/dev/null || die "Unknown test: ${test_name}"
  done
}

check_common_dependencies() {
  require_command_for "test runner" docker
  require_command_for "test runner" python3

  if command -v docker >/dev/null 2>&1; then
    docker compose version >/dev/null 2>&1 || dependency_error "test runner: Docker Compose plugin is required"
    docker info >/dev/null 2>&1 || dependency_error "test runner: Docker daemon is not accessible"
  fi
}

check_test_dependencies() {
  local test_name="$1"

  case "${test_name}" in
    dicompot)
      require_command_for "${test_name}" echoscu
      require_command_for "${test_name}" getscu
      require_command_for "${test_name}" dcmdump
      require_one_command_for "${test_name}" setscu storescu
      ;;
  esac
}

check_dependencies() {
  local test_name=""
  local error=""

  DEPENDENCY_ERRORS=()
  check_common_dependencies

  for test_name in "${SELECTED_TESTS[@]}"; do
    check_test_dependencies "${test_name}"
  done

  if [[ ${#DEPENDENCY_ERRORS[@]} -gt 0 ]]; then
    printf '[FAIL] Missing dependencies for selected smoke tests:\n' >&2
    for error in "${DEPENDENCY_ERRORS[@]}"; do
      printf '  - %s\n' "${error}" >&2
    done
    printf 'Install the missing tools and retry.\n' >&2
    exit 1
  fi
}

main() {
  parse_args "$@"
  validate_args

  if [[ "${LIST_ONLY}" == "true" ]]; then
    list_tests
    exit 0
  fi

  if [[ ${#SELECTED_TESTS[@]} -eq 0 ]]; then
    mapfile -t SELECTED_TESTS < <(list_tests)
  fi

  [[ ${#SELECTED_TESTS[@]} -gt 0 ]] || die "No tests found in ${TEST_DIR}"
  validate_selected_tests
  check_dependencies

  local common_args=(--timeout "${TEST_TIMEOUT}" --bind-ip "${TEST_BIND_IP}")
  if [[ "${TEST_KEEP_ARTIFACTS}" == "true" ]]; then
    common_args+=(--keep-artifacts)
  fi

  local passed=0
  local failed=0
  local test_name=""
  local test_path=""

  for test_name in "${SELECTED_TESTS[@]}"; do
    test_path="$(test_path_for "${test_name}")" || die "Unknown test: ${test_name}"

    printf '\n### Running %s\n' "${test_name}"
    if "${test_path}" "${common_args[@]}"; then
      printf '### PASS %s\n' "${test_name}"
      passed=$((passed + 1))
    else
      printf '### FAIL %s\n' "${test_name}" >&2
      failed=$((failed + 1))
    fi
  done

  printf '\n### Summary: %s passed, %s failed\n' "${passed}" "${failed}"
  (( failed == 0 ))
}

main "$@"
