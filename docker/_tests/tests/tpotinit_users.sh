#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="tpotinit_users"
DEFAULT_IMAGE="dtagdevsec/tpotinit:24.04.2"
IMAGE=""
TPOTINIT_DIR="${DOCKER_ROOT}/tpotinit"

usage() {
  cat <<EOF
Usage: $0 [options]

Check the passwd files tpotinit writes from WEB_USER / LS_WEB_USER: a line whose user the
.env no longer names is kept in <file>.lost (never twice) and the file is written in place;
a SENSOR keeps nothing; a .lost that cannot take the lines stops the start with the file as it
was; a HIVE without WEB_USER is told what nginxpasswd still holds. The
.lost files get the modes of nginx conf (664) and stay with TPOT_PERSISTENCE=off.

The image only provides the tools (busybox base64, grep, chmod): create_web_users and the
functions around it of dist/entrypoint.sh, the hash formats of dist/bin/env_validate.sh,
dist/bin/data_modes.sh and dist/bin/clean.sh of this checkout run inside it on a data folder
of the container, so an older image tests the current rules.

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
b64() { printf '%s' "$1" | base64 -w0; }
content() { tr '\n' '|' < "$1"; }

# the hash formats T-Pot takes, as tpotinit has them from env_validate.sh
eval "$(grep '^myENV_HTPASSWD=' /opt/tpot/bin/env_validate.sh)"
check "env_validate: the hash formats are there" "yes" "$([ -n "${myENV_HTPASSWD:-}" ] && echo yes || echo no)"
eval "$(sed -n '/^write_passwd_file() {$/,/^}$/p; /^create_web_users() {$/,/^}$/p; /^web_users_hint() {$/,/^}$/p; /^\. \/opt\/tpot\/bin\/data_modes.sh$/p; /^update_permissions() {$/,/^}$/p' /work/entrypoint.sh)"
check "entrypoint: the functions are there" "function function function function" \
      "$(type -t write_passwd_file) $(type -t create_web_users) $(type -t web_users_hint) $(type -t update_permissions)"

ALICE='alice:$2y$05$aliceshashaliceshashaliceshashaliceshashaliceshash'
BOB='bob:$apr1$bobsalt$bobshashbobshash'
OTTER='sensor-otter:$2y$05$otterhashotterhashotterhashotterhashotterhash'
LYNX='sensor-lynx:$apr1$lynxsalt$lynxhashlynxhash'
CONF=/data/nginx/conf

fresh() {
  rm -rf /data
  mkdir -p "${CONF}"
  : > "${CONF}/nginxpasswd"
  : > "${CONF}/lswebpasswd"
}

# 1. the files come from the .env
fresh
TPOT_TYPE=HIVE WEB_USER="$(b64 "${ALICE}") $(b64 "${BOB}")" LS_WEB_USER="$(b64 "${OTTER}")"
out="$(create_web_users /data)"
check "env: nginxpasswd" "${ALICE}|${BOB}|" "$(content "${CONF}/nginxpasswd")"
check "env: lswebpasswd" "${OTTER}|" "$(content "${CONF}/lswebpasswd")"
check "env: nothing kept" "no no" "$( [ -e "${CONF}/nginxpasswd.lost" ] && echo yes || echo no) $( [ -e "${CONF}/lswebpasswd.lost" ] && echo yes || echo no)"

# 2. LS_WEB_USER empty: the sensors are kept, the file is emptied in place
fresh
printf '%s\n%s\n' "${OTTER}" "${LYNX}" > "${CONF}/lswebpasswd"
inode="$(stat -c %i "${CONF}/lswebpasswd")"
TPOT_TYPE=HIVE WEB_USER="$(b64 "${ALICE}")" LS_WEB_USER=""
out="$(create_web_users /data)"
check "empty key: lswebpasswd is empty" "" "$(content "${CONF}/lswebpasswd")"
check "empty key: written in place" "${inode}" "$(stat -c %i "${CONF}/lswebpasswd")"
check "empty key: the lines are kept" "${OTTER}|${LYNX}|" "$(content "${CONF}/lswebpasswd.lost")"
check "empty key: warned" "1" "$(grep -c '^# Warning: 2 sensor user(s) of lswebpasswd are not in LS_WEB_USER and lose access, they are kept in lswebpasswd.lost. Take them back with: tpot sensors import$' <<<"${out}")"
out="$(create_web_users /data)"
check "second start: nothing added" "${OTTER}|${LYNX}|" "$(content "${CONF}/lswebpasswd.lost")"
check "second start: named" "1" "$(grep -c '^# lswebpasswd.lost holds 2 sensor user(s) LS_WEB_USER does not name' <<<"${out}")"

# 3. one name less: only that one is kept
printf '%s\n%s\n' "${ALICE}" "${BOB}" > "${CONF}/nginxpasswd"
out="$(create_web_users /data)"
check "one less: nginxpasswd" "${ALICE}|" "$(content "${CONF}/nginxpasswd")"
check "one less: kept" "${BOB}|" "$(content "${CONF}/nginxpasswd.lost")"
check "one less: warned" "1" "$(grep -c '^# Warning: 1 web user(s) of nginxpasswd are not in WEB_USER' <<<"${out}")"

# 4. the same content: nothing kept
fresh
printf '%s\n' "${ALICE}" > "${CONF}/nginxpasswd"
out="$(create_web_users /data)"
check "same content: nothing kept" "no" "$( [ -e "${CONF}/nginxpasswd.lost" ] && echo yes || echo no)"

# 5. a SENSOR keeps nothing
fresh
printf '%s\n' "${ALICE}" > "${CONF}/nginxpasswd"
printf '%s\n' "${OTTER}" > "${CONF}/lswebpasswd"
TPOT_TYPE=SENSOR WEB_USER="" LS_WEB_USER=""
out="$(create_web_users /data)"
check "sensor: files empty, nothing kept, nothing said" "|| no no 0" \
      "$(content "${CONF}/nginxpasswd")|$(content "${CONF}/lswebpasswd")| $( [ -e "${CONF}/nginxpasswd.lost" ] && echo yes || echo no) $( [ -e "${CONF}/lswebpasswd.lost" ] && echo yes || echo no) $(grep -c Warning <<<"${out}")"

# 6. the hint before the abort of a HIVE without WEB_USER, the file stays
fresh
printf '%s\n%s\n' "${ALICE}" "${BOB}" > "${CONF}/nginxpasswd"
check "hint: named" "# ${CONF}/nginxpasswd still holds 2 line(s), the file stays as it is; the users among them T-Pot accepts are taken over with: tpot users import" "$(web_users_hint /data)"
check "hint: the file stays" "${ALICE}|${BOB}|" "$(content "${CONF}/nginxpasswd")"

# 7. a .lost that cannot take the lines (here a folder): the file stays as it is, the start stops
fresh
mkdir "${CONF}/lswebpasswd.lost"
printf '%s\n' "${OTTER}" > "${CONF}/lswebpasswd"
TPOT_TYPE=HIVE WEB_USER="$(b64 "${ALICE}")" LS_WEB_USER=""
out="$(create_web_users /data)"
rc=$?
check "lost folder: create_web_users fails" "1" "${rc}"
check "lost folder: lswebpasswd stays" "${OTTER}|" "$(content "${CONF}/lswebpasswd")"
check "lost folder: named" "1" "$(grep -c '^# Error: the lines of /data/nginx/conf/lswebpasswd that LS_WEB_USER does not name cannot be kept in /data/nginx/conf/lswebpasswd.lost (it is not a regular file), lswebpasswd stays as it is\.$' <<<"${out}")"
check "lost folder: nothing said to be kept" "0" "$(grep -c 'kept in lswebpasswd.lost' <<<"${out}")"
rmdir "${CONF}/lswebpasswd.lost"

# 7b. a line with a hash in a format T-Pot does not take is kept, but not named for the import
LEGACY='legacy:$1$legacysalt$legacyhashlegacyhash'
fresh
printf '%s\n%s\n' "${OTTER}" "${LEGACY}" > "${CONF}/lswebpasswd"
TPOT_TYPE=HIVE WEB_USER="$(b64 "${ALICE}")" LS_WEB_USER=""
out="$(create_web_users /data)"
check "other format: both kept" "${OTTER}|${LEGACY}|" "$(content "${CONF}/lswebpasswd.lost")"
check "other format: one to take back" "1" "$(grep -c '^# Warning: 1 sensor user(s) of lswebpasswd are not in LS_WEB_USER and lose access' <<<"${out}")"
check "other format: one only kept" "1" "$(grep -c '^# Warning: 1 line(s) of lswebpasswd that LS_WEB_USER does not name lose access, their hash is in a format T-Pot does not take' <<<"${out}")"
out="$(create_web_users /data)"
check "other format, next start: one to take back" "1" "$(grep -c '^# lswebpasswd.lost holds 1 sensor user(s) LS_WEB_USER does not name' <<<"${out}")"
check "other format, next start: one only kept" "1" "$(grep -c '^# lswebpasswd.lost keeps 1 line(s) with a hash in a format T-Pot does not take' <<<"${out}")"

# 8. the modes of the .lost files after update_permissions, and TPOT_PERSISTENCE=off keeps them
fresh
mkdir -p /data/cowrie/log /data/nginx/cert
echo inert > /data/uuid
printf '%s\n' "${BOB}" > "${CONF}/nginxpasswd"
printf '%s\n' "${OTTER}" > "${CONF}/lswebpasswd"
TPOT_TYPE=HIVE WEB_USER="$(b64 "${ALICE}")" LS_WEB_USER=""
create_web_users /data > /dev/null
check "modes: new .lost files" "660 660" "$(mode "${CONF}/nginxpasswd.lost") $(mode "${CONF}/lswebpasswd.lost")"
update_permissions > /tmp/update.log 2>&1 || { echo "FAIL update_permissions"; sed 's/^/     /' /tmp/update.log; failed=1; }
check "modes: after update_permissions as the passwd files" "664 664 664 664" \
      "$(mode "${CONF}/nginxpasswd") $(mode "${CONF}/lswebpasswd") $(mode "${CONF}/nginxpasswd.lost") $(mode "${CONF}/lswebpasswd.lost")"
check "modes: owner tpot" "tpot:tpot tpot:tpot" "$(stat -c %U:%G "${CONF}/nginxpasswd.lost") $(stat -c %U:%G "${CONF}/lswebpasswd.lost")"
bash /opt/tpot/bin/clean.sh off 30 > /tmp/clean-off.log 2>&1 || { echo "FAIL clean.sh off"; sed 's/^/     /' /tmp/clean-off.log; failed=1; }
check "persistence off: the .lost files stay" "${BOB}| ${OTTER}|" "$(content "${CONF}/nginxpasswd.lost") $(content "${CONF}/lswebpasswd.lost")"
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

  test_info "Checking the passwd files of tpotinit in ${IMAGE} with the scripts of this checkout"
  local output status=0
  output="$(docker run --rm --entrypoint bash \
    -v "${TPOTINIT_DIR}/dist/bin/data_modes.sh:/opt/tpot/bin/data_modes.sh:ro" \
    -v "${TPOTINIT_DIR}/dist/bin/clean.sh:/opt/tpot/bin/clean.sh:ro" \
    -v "${TPOTINIT_DIR}/dist/bin/env_validate.sh:/opt/tpot/bin/env_validate.sh:ro" \
    -v "${TPOTINIT_DIR}/dist/entrypoint.sh:/work/entrypoint.sh:ro" \
    -v "${TEST_TMP_ROOT}/run.sh:/work/run.sh:ro" \
    "${IMAGE}" /work/run.sh 2>&1)" || status=$?
  printf '%s\n' "${output}"
  [[ "${status}" -eq 0 ]] || test_die "The passwd files of tpotinit are not as expected"
  test_ok "tpotinit passwd files pass all $(grep -c '^ok ' <<<"${output}") checks"
}

main "$@"
