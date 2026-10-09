#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="nginx"
DEFAULT_IMAGE="dtagdevsec/nginx:24.04.2"
IMAGE=""
HTTPS_PORT=""
CERT_DIR=""
LOG_DIR=""
PASSWD_FILE=""
LSPASSWD_FILE=""
MAPPED_HTTPS_PORT=""
WEB_USER="smoke"
WEB_PASSWORD=""

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Nginx image: the landing page behind
the login, its security headers and caching, and the neutral error page.

Options:
  --image IMAGE      Image to test. Defaults to dtagdevsec/nginx:24.04.2.
  --https-port PORT  Host TCP port for the web UI (64297 in T-Pot). Default: dynamic loopback port.
  --timeout SEC      Timeout for startup and checks. Default: 30.
  --bind-ip IP       Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts   Keep temporary compose file, certificate and logs for debugging.
  -h, --help         Show this help message.
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
      --https-port|--port)
        [[ $# -ge 2 ]] || test_die "$1 requires an argument"
        HTTPS_PORT="$2"
        shift 2
        ;;
      --https-port=*|--port=*)
        HTTPS_PORT="${1#*=}"
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

validate_args() {
  test_validate_timeout

  if [[ -n "${HTTPS_PORT}" ]]; then
    test_validate_port "${HTTPS_PORT}"
  fi
}

prepare_nginx_harness() {
  test_prepare_harness "${TEST_NAME}"

  CERT_DIR="${TEST_TMP_ROOT}/cert"
  LOG_DIR="${TEST_TMP_ROOT}/log"
  PASSWD_FILE="${TEST_TMP_ROOT}/nginxpasswd"
  LSPASSWD_FILE="${TEST_TMP_ROOT}/lswebpasswd"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${CERT_DIR}" "${LOG_DIR}"
  chmod 0755 "${CERT_DIR}"
  chmod 0777 "${LOG_DIR}"

  # the checks trust this certificate and nothing else (no switched off verification)
  openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 1 -subj "/CN=localhost" \
    -addext "subjectAltName=IP:${TEST_BIND_IP},DNS:localhost" \
    -keyout "${CERT_DIR}/nginx.key" -out "${CERT_DIR}/nginx.crt" >/dev/null 2>&1 \
    || test_die "Could not create a test certificate with openssl"
  chmod 0644 "${CERT_DIR}/nginx.key" "${CERT_DIR}/nginx.crt"

  # a throwaway web user; nginx reads {SHA} entries as well as the bcrypt ones of T-Pot
  WEB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  python3 - "${WEB_USER}" "${WEB_PASSWORD}" > "${PASSWD_FILE}" <<'PY'
import base64, hashlib, sys
print(f"{sys.argv[1]}:{{SHA}}" + base64.b64encode(hashlib.sha1(sys.argv[2].encode()).digest()).decode())
PY
  : > "${LSPASSWD_FILE}"
  chmod 0644 "${PASSWD_FILE}" "${LSPASSWD_FILE}"

  local port_mapping="${TEST_BIND_IP}::64297"
  if [[ -n "${HTTPS_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${HTTPS_PORT}:64297"
  fi

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  nginx:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    tmpfs:
      - /var/tmp/nginx/client_body
      - /var/tmp/nginx/proxy
      - /var/tmp/nginx/fastcgi
      - /var/tmp/nginx/uwsgi
      - /var/tmp/nginx/scgi
      - /run
      - /var/lib/nginx/tmp:uid=100,gid=82
    ports:
      - "${port_mapping}"
    volumes:
      - "${CERT_DIR}:/etc/nginx/cert:ro"
      - "${PASSWD_FILE}:/etc/nginx/nginxpasswd:ro"
      - "${LSPASSWD_FILE}:/etc/nginx/lswebpasswd:ro"
      - "${LOG_DIR}:/var/log/nginx"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

run_checks() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_HTTPS_PORT}" "${WEB_USER}" "${WEB_PASSWORD}" "${TEST_TIMEOUT}" \
    "${CERT_DIR}/nginx.crt" <<'PY'
import base64
import http.client
import re
import ssl
import sys
import time

host, port, user, password, timeout = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5])
# the certificate made for this test is the only one trusted, its name is the address of the test
context = ssl.create_default_context(cafile=sys.argv[6])
auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
failures = []


def ask(method, path, login=True):
    conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    headers = {"Authorization": auth} if login else {}
    conn.request(method, path, body=b"" if method == "POST" else None, headers=headers)
    response = conn.getresponse()
    body = response.read().decode("utf-8", errors="replace")
    conn.close()
    return response.status, response.headers, body


def check(what, ok):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def once(headers, name):
    return len(headers.get_all(name) or []) == 1


# wait until nginx answers
deadline = time.monotonic() + timeout
while True:
    try:
        ask("GET", "/", login=False)
        break
    except OSError as exc:
        if time.monotonic() > deadline:
            print(f"FAIL nginx does not answer on {host}:{port}: {exc}")
            sys.exit(1)
        time.sleep(1)

# before the login: 401, the browser asks for the password, the page is neutral
status, headers, body = ask("GET", "/", login=False)
check("401 without a login", status == 401)
check("401 keeps WWW-Authenticate", headers.get("WWW-Authenticate", "").startswith("Basic"))
check("the error page shows the status", re.search(r"<h1>\s*401\s*</h1>", body) is not None)
check("the error page names neither T-Pot nor the server", re.search(r"t-?pot|telekom|nginx|honeypot", body, re.I) is None)
check("the error page has its own policy", "style-src 'sha256-" in headers.get("Content-Security-Policy", ""))

# the landing page
status, headers, body = ask("GET", "/")
csp = headers.get("Content-Security-Policy", "")
check("200 with a login", status == 200)
check("the landing page carries the version", re.search(r'<span class="version">[0-9][\w.-]*</span>', body) is not None)
for directive in ("default-src 'none'", "script-src 'self'", "connect-src 'self'", "frame-ancestors 'self'"):
    check(f"CSP {directive}", directive in csp)
check("Content-Security-Policy once", once(headers, "Content-Security-Policy"))
check("Referrer-Policy: no-referrer", headers.get("Referrer-Policy") == "no-referrer")
check("HSTS of the server block kept", "max-age=" in headers.get("Strict-Transport-Security", ""))
check("X-Frame-Options of the server block kept", headers.get("X-Frame-Options", "").upper() == "SAMEORIGIN")
check("Cache-Control: no-cache, once", once(headers, "Cache-Control") and headers.get("Cache-Control") == "no-cache")

# an asset of the page
src = re.search(r'src="(assets/js/app\.js)"', body)
check("the page loads assets/js/app.js", src is not None)
if src:
    status, headers, _ = ask("GET", "/" + src.group(1))
    check("200 for the script", status == 200)
    check("the script has the policy too", "default-src 'none'" in headers.get("Content-Security-Policy", ""))
    check("the script is revalidated", once(headers, "Cache-Control") and headers.get("Cache-Control") == "no-cache")
status, _, body = ask("GET", "/assets/queries.json")
check("the queries are served", status == 200 and '"ranges"' in body)

# errors behind the login: neutral as well
status, _, body = ask("POST", "/")
check("405 for POST on the page", status == 405 and re.search(r"<h1>\s*405\s*</h1>", body) is not None)
status, _, body = ask("POST", "/es/logstash-*/_search")
check("502 without Elasticsearch, neutral", status == 502 and re.search(r"t-?pot|nginx", body, re.I) is None)
status, _, _ = ask("GET", "/error.html")
check("the error page cannot be asked for itself", status == 404)

if failures:
    print(f"{len(failures)} check(s) failed")
    sys.exit(1)
PY
}

main() {
  parse_args "$@"
  validate_args
  test_check_dependencies
  test_require_command openssl

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  if [[ -n "${HTTPS_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${HTTPS_PORT}" || test_die "${TEST_BIND_IP}:${HTTPS_PORT} is already in use. Try --https-port <free-port>."
  fi

  prepare_nginx_harness
  test_enable_cleanup

  test_info "Starting isolated Nginx container"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Nginx container did not stay running"
  MAPPED_HTTPS_PORT="$(test_get_mapped_port nginx 64297)" || test_die "Could not resolve the mapped HTTPS port"
  test_info "Nginx is listening on https://${TEST_BIND_IP}:${MAPPED_HTTPS_PORT}"

  run_checks || test_die "Nginx checks failed"
  test_ok "Nginx smoke test passed"
}

main "$@"
