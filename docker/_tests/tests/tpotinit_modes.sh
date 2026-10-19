#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="tpotinit_modes"
DEFAULT_IMAGE="dtagdevsec/tpotinit:24.04.2"
IMAGE=""
TPOTINIT_DIR="${DOCKER_ROOT}/tpotinit"

usage() {
  cat <<EOF
Usage: $0 [options]

Check the modes tpotinit gives the data folder: folders 770, files 660 (no execute bit),
nginx conf and cert 774 / 664, and links are neither followed nor changed. With
TPOT_PERSISTENCE=off clean.sh keeps the identity and state of each service.

The image only provides the tools (busybox chmod, logrotate, tar): dist/bin/data_modes.sh,
dist/bin/clean.sh and update_permissions of dist/entrypoint.sh of this checkout run inside
it on a data folder of the container, so an older image tests the current rules.

Options:
  --image IMAGE      Image to test. Defaults to docker/tpotinit/docker-compose.yml.
  --timeout SEC      Accepted for runner compatibility.
  --bind-ip IP       Accepted for runner compatibility; nothing is published.
  --keep-artifacts   Keep the temporary files for debugging.
  -h, --help         Show this help message.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image) [[ $# -ge 2 ]] || test_die "--image requires an argument"; IMAGE="$2"; shift 2 ;;
      --image=*) IMAGE="${1#*=}"; shift ;;
      --timeout) [[ $# -ge 2 ]] || test_die "--timeout requires an argument"; TEST_TIMEOUT="$2"; shift 2 ;;
      --timeout=*) TEST_TIMEOUT="${1#*=}"; shift ;;
      --bind-ip) [[ $# -ge 2 ]] || test_die "--bind-ip requires an argument"; TEST_BIND_IP="$2"; shift 2 ;;
      --bind-ip=*) TEST_BIND_IP="${1#*=}"; shift ;;
      --keep-artifacts) TEST_KEEP_ARTIFACTS="true"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) test_die "Unknown option: $1" ;;
    esac
  done
}

write_runner() {
  cat > "${TEST_TMP_ROOT}/run.sh" <<'EOF'
#!/usr/bin/env bash
# Runs inside the tpotinit image, prints one line per check and FAIL lines on a mismatch.
set -u
failed=0

check() {   # $1 = name, $2 = expected, $3 = actual
  if [[ "$2" == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected [$2], got [$3]"
    failed=1
  fi
}

mode() { stat -c %a "$1"; }

# Things outside the tree that links point to: they keep their modes, and nothing in them changes
outside() {
  rm -rf /outside
  mkdir -p /outside/folder
  echo inert > /outside/file
  echo inert > /outside/folder/file
  mkdir /outside/top
  chmod 0751 /outside/folder /outside/top
  chmod 0705 /outside/file /outside/folder/file
}

# $1 = tree: folders and files with an execute bit, links to the outside
tree() {
  mkdir -p "$1/sub/deeper"
  echo inert > "$1/run"
  echo inert > "$1/sub/plain"
  echo inert > "$1/sub/deeper/data"
  chmod 0777 "$1/run"
  chmod 0755 "$1/sub/plain" "$1/sub"
  chmod 0700 "$1/sub/deeper"
  chmod 0644 "$1/sub/deeper/data"
  ln -s /outside/file "$1/sub/to-file"
  ln -s /outside/folder "$1/sub/to-folder"
}

outside_unchanged() {   # $1 = prefix of the names
  check "$1: a link to a file outside is not followed" 705 "$(mode /outside/file)"
  check "$1: a link to a folder outside is not entered" "751 705" "$(mode /outside/folder) $(mode /outside/folder/file)"
  check "$1: the links stay links" "yes yes" "$( [ -L "$2/sub/to-file" ] && echo yes) $( [ -L "$2/sub/to-folder" ] && echo yes)"
}

# 1. the helper on its own
. /opt/tpot/bin/data_modes.sh
outside
tree /t
tree /t/web
fuDATA_MODES /t >/dev/null
fuDATA_WEB_MODES /t/web/ >/dev/null
check "helper: files lose the execute bit" "660 660 660" "$(mode /t/run) $(mode /t/sub/plain) $(mode /t/sub/deeper/data)"
check "helper: folders 770" "770 770 770" "$(mode /t) $(mode /t/sub) $(mode /t/sub/deeper)"
check "helper: web files 664" "664 664 664" "$(mode /t/web/run) $(mode /t/web/sub/plain) $(mode /t/web/sub/deeper/data)"
check "helper: web folders 774" "774 774 774" "$(mode /t/web) $(mode /t/web/sub) $(mode /t/web/sub/deeper)"
outside_unchanged helper /t
ln -s /outside/top /t/top
out="$(fuDATA_MODES /t/top/ 2>&1)"
check "helper: a link given as path is not followed" 751 "$(mode /outside/top)"
check "helper: a link given as path is named" "# Warning: /t/top is a link, its modes stay as they are." "${out}"

# 2. clean.sh on a data folder from an earlier release (everything 770), then update_permissions
outside
rm -rf /data
mkdir -p /data/cowrie/log /data/elk/data/indices/x /data/nginx/conf /data/nginx/cert /data/ciscoasa/payloads
tree /data/cowrie/log
echo inert > /data/elk/data/indices/x/segment
echo inert > /data/nginx/conf/nginxpasswd
echo inert > /data/nginx/cert/nginx.crt
echo inert > /data/ciscoasa/payloads/sample
echo inert > /data/uuid
chmod -R 770 /data
ln -s /outside/file /data/elk/to-file
ln -s /outside/folder /data/elk/to-folder
bash /opt/tpot/bin/clean.sh on 30 > /tmp/clean.log 2>&1 || { echo "FAIL clean.sh on"; sed 's/^/     /' /tmp/clean.log; failed=1; }
eval "$(sed -n '/^\. \/opt\/tpot\/bin\/data_modes.sh$/p; /^update_permissions() {$/,/^}$/p' /work/entrypoint.sh)"
check "entrypoint: update_permissions is there" "function" "$(type -t update_permissions)"
update_permissions > /tmp/update.log 2>&1 || { echo "FAIL update_permissions"; sed 's/^/     /' /tmp/update.log; failed=1; }
check "data: no file with an execute bit" "" "$(find /data -type f -perm /111 | sort | tr '\n' ' ')"
check "data: every folder 770 (nginx conf and cert 774)" "" "$(find /data -type d ! -perm 770 | grep -Ev '^/data/nginx/(conf|cert)$' | sort | tr '\n' ' ')"
check "data: nginx conf and cert" "774 774 664 664" "$(mode /data/nginx/conf) $(mode /data/nginx/cert) $(mode /data/nginx/conf/nginxpasswd) $(mode /data/nginx/cert/nginx.crt)"
check "data: files rw for owner and group" "660 660 660 660" "$(mode /data/cowrie/log/run) $(mode /data/elk/data/indices/x/segment) $(mode /data/ciscoasa/payloads/sample) $(mode /data/uuid)"
check "data: owner tpot" "tpot:tpot" "$(stat -c %U:%G /data/elk/data/indices/x/segment)"
outside_unchanged data /data/cowrie/log
check "data: the links of elk stay" "yes yes" "$( [ -L /data/elk/to-file ] && echo yes) $( [ -L /data/elk/to-folder ] && echo yes)"

# 3. clean.sh with TPOT_PERSISTENCE=off: logs, downloads, payloads and their archives go, the identity
#    and state of each service stays
rm -rf /data
kept="beelzebub/key/ssh_host_key ciscoasa/identity/identity.json conpot/identity/identity.json
cowrie/keys/persona-state.json cowrie/keys/ubuntu-noble/ssh_host_ed25519_key endlessh/identity/banner
galah/cert/cert.pem galah/cache/cache.db miniprint/data/state.json rdphoneypot/cert/server.pem suricata/rules/suricata.rules
nginx/conf/nginxpasswd.lost nginx/conf/lswebpasswd.lost"
gone="beelzebub/log/beelzebub.json ciscoasa/log/ciscoasa.json ciscoasa/payloads/sample conpot/log/conpot.json
cowrie/log/cowrie.json cowrie/log/tty/session cowrie/downloads/sample cowrie/downloads.tgz
endlessh/log/endlessh.log galah/log/galah.json miniprint/log/miniprint.json
miniprint/uploads/job.ps miniprint/uploads.tgz rdphoneypot/log/rdphoneypot.log suricata/log/eve.json"
for file in ${kept} ${gone}; do
  mkdir -p "$(dirname "/data/${file}")"
  echo inert > "/data/${file}"
done
bash /opt/tpot/bin/clean.sh off 30 > /tmp/clean-off.log 2>&1 || { echo "FAIL clean.sh off"; sed 's/^/     /' /tmp/clean-off.log; failed=1; }
check "persistence off: identity and state stay" "" \
      "$(for file in ${kept}; do [ -f "/data/${file}" ] || printf '%s ' "${file}"; done)"
check "persistence off: logs, downloads and payloads go" "" \
      "$(for file in ${gone}; do [ -e "/data/${file}" ] && printf '%s ' "${file}"; done)"
check "persistence off: the folders are there again" "yes" \
      "$( [ -d /data/cowrie/log/tty ] && [ -d /data/cowrie/downloads ] && [ -d /data/ciscoasa/payloads ] && [ -d /data/miniprint/uploads ] && [ -d /data/galah/log ] && echo yes)"
exit "${failed}"
EOF
  chmod 0755 "${TEST_TMP_ROOT}/run.sh"
}

main() {
  parse_args "$@"
  test_validate_timeout
  test_require_command docker
  docker info >/dev/null 2>&1 || test_die "Docker daemon is not accessible"
  IMAGE="${IMAGE:-$(test_read_compose_image tpotinit "${DEFAULT_IMAGE}")}"
  test_require_image "${IMAGE}" "docker compose -f docker/tpotinit/docker-compose.yml build"
  test_prepare_harness "${TEST_NAME}"
  trap 'if [[ "${TEST_KEEP_ARTIFACTS}" != "true" ]]; then rm -rf "${TEST_TMP_ROOT}"; else test_info "Artifacts in ${TEST_TMP_ROOT}"; fi' EXIT
  write_runner

  test_info "Checking the data folder modes of tpotinit in ${IMAGE} with the scripts of this checkout"
  local output status=0
  output="$(docker run --rm --entrypoint bash \
    -v "${TPOTINIT_DIR}/dist/bin/data_modes.sh:/opt/tpot/bin/data_modes.sh:ro" \
    -v "${TPOTINIT_DIR}/dist/bin/clean.sh:/opt/tpot/bin/clean.sh:ro" \
    -v "${TPOTINIT_DIR}/dist/entrypoint.sh:/work/entrypoint.sh:ro" \
    -v "${TEST_TMP_ROOT}/run.sh:/work/run.sh:ro" \
    "${IMAGE}" /work/run.sh 2>&1)" || status=$?
  printf '%s\n' "${output}"
  [[ "${status}" -eq 0 ]] || test_die "The data folder modes of tpotinit are not as expected"
  test_ok "tpotinit data folder modes pass all $(grep -c '^ok ' <<<"${output}") checks"
}

main "$@"
