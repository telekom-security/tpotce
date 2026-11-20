#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="nginx"
DEFAULT_IMAGE="dtagdevsec/nginx:24.04.2"
DEFAULT_INIT_IMAGE="ghcr.io/telekom-security/tpotinit:24.04.2"
IMAGE=""
INIT_IMAGE=""
HTTPS_PORT=""
STATE_DIR=""
MAPPED_INGEST_PORT=""
LS_USER="sensor-smoke"
LS_PASSWORD=""
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
the login, its security headers and caching, and the neutral error page; the ingest
listener of a HIVE (64294) with a stand-in for Logstash: the login, large batches and
the capability probe /tpot-ingest of the SENSORs (its limit per address, then the
login of the SENSOR, then Logstash) against a new and an old Logstash, 30 senders
without a login at once (containers of their own, the init image), and the key
exchange and key of the TLS handshake (openssl of the init image).

Options:
  --image IMAGE      Image to test. Defaults to docker/nginx/docker-compose.yml.
  --init-image IMAGE Image the Logstash stand-in, the senders and openssl run in. Defaults to
                     docker/tpotinit/docker-compose.yml.
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
      --init-image)
        [[ $# -ge 2 ]] || test_die "--init-image requires an argument"
        INIT_IMAGE="$2"
        shift 2
        ;;
      --init-image=*)
        INIT_IMAGE="${1#*=}"
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
  STATE_DIR="${TEST_TMP_ROOT}/state"
  BURST_DIR="${TEST_TMP_ROOT}/burst"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${CERT_DIR}" "${LOG_DIR}" "${STATE_DIR}" "${BURST_DIR}"
  chmod 0777 "${STATE_DIR}"
  chmod 0755 "${CERT_DIR}"
  chmod 0777 "${LOG_DIR}"

  # the checks trust this certificate and nothing else (no switched off verification); an ECDSA
  # P-384 key, the handshake check (run_tls_checks) looks for its size
  openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:P-384 -sha384 -days 1 -subj "/CN=localhost" \
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
  LS_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  python3 - "${LS_USER}" "${LS_PASSWORD}" > "${LSPASSWD_FILE}" <<'PY'
import base64, hashlib, sys
print(f"{sys.argv[1]}:{{SHA}}" + base64.b64encode(hashlib.sha1(sys.argv[2].encode()).digest()).decode())
PY
  chmod 0644 "${PASSWD_FILE}" "${LSPASSWD_FILE}"
  write_logstash_standin
  write_burst_sender

  local port_mapping="${TEST_BIND_IP}::64297"
  if [[ -n "${HTTPS_PORT}" ]]; then
    port_mapping="${TEST_BIND_IP}:${HTTPS_PORT}:64297"
  fi
  local ingest_mapping="${TEST_BIND_IP}::64294"

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
      - "${ingest_mapping}"
    volumes:
      - "${CERT_DIR}:/etc/nginx/cert:ro"
      - "${PASSWD_FILE}:/etc/nginx/nginxpasswd:ro"
      - "${LSPASSWD_FILE}:/etc/nginx/lswebpasswd:ro"
      - "${LOG_DIR}:/var/log/nginx"
  logstash:
    image: "${INIT_IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}-logstash"
    restart: "no"
    entrypoint: ["python3", "-u", "/state/logstash.py"]
    volumes:
      - "${STATE_DIR}:/state"
  # the senders of the burst on /tpot-ingest (run_ingest_burst): only with the profile burst
  sender:
    image: "${INIT_IMAGE}"
    profiles: ["burst"]
    restart: "no"
    user: "$(id -u):$(id -g)"
    entrypoint: ["python3", "-u", "/burst/sender.py"]
    volumes:
      - "${BURST_DIR}:/burst"
      - "${CERT_DIR}:/cert:ro"
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

# A stand-in for Logstash under the name logstash: 64305 (the sensors input) records every request
# and answers 200; 64306 (the probe input of a new Logstash) records too and answers 204 with the
# marker and headers that must not reach the client. /state/mode: "new" (default), "200" (the probe
# port answers 200 with the marker and a body), "401" (the probe port asks for a login of its own),
# "old" (no listener on 64306, as a Logstash before it)
write_logstash_standin() {
  cat > "${STATE_DIR}/logstash.py" <<'PY'
import http.server
import json
import threading
import time

lock = threading.Lock()


def mode():
    try:
        with open("/state/mode", encoding="utf-8") as handle:
            return handle.read().strip() or "new"
    except OSError:
        return "new"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def record(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        entry = {"port": self.server.server_address[1], "method": self.command, "path": self.path,
                 "headers": {k.lower(): v for k, v in self.headers.items()}, "length": len(body)}
        with lock, open("/state/requests", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def answer(self):
        self.record()
        if self.server.server_address[1] == 64305:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        elif mode() == "200":
            self.send_response(200)
            self.send_header("X-TPot-Ingest", "2")
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"LEAK-BODY")
        elif mode() == "401":
            self.send_response(401)
            self.send_header("X-TPot-Ingest", "2")
            self.send_header("WWW-Authenticate", 'Basic realm="stand-in"')
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"LEAK-BODY")
        else:
            self.send_response(204)
            self.send_header("X-TPot-Ingest", "2")
            self.send_header("X-Leak", "stand-in")
            self.send_header("Set-Cookie", "leak=1")
            self.end_headers()

    do_GET = do_POST = do_HEAD = do_PUT = answer

    def log_message(self, *args):
        pass


def serve(port):
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


serve(64305)
probe = None
while True:
    if mode() == "old" and probe:
        probe.shutdown()
        probe.server_close()
        probe = None
    elif mode() != "old" and not probe:
        probe = serve(64306)
    with open("/state/probe-port", "w", encoding="utf-8") as handle:
        handle.write("closed" if probe is None else "open")
    time.sleep(0.2)
PY
}

# A sender of the burst: one container, one source address, one GET /tpot-ingest without a login. It
# says it runs (ready/), opens its TLS connection once armed (connected/; nginx closes an idle one
# after 10 s, so not before), sends its request once the go file is there and writes the status and
# the marker (result/)
write_burst_sender() {
  mkdir -p "${BURST_DIR}/ready" "${BURST_DIR}/connected" "${BURST_DIR}/result"
  cat > "${BURST_DIR}/sender.py" <<'PY'
import http.client
import os
import socket
import ssl
import time

name = socket.gethostname()


def mark(folder, text=""):
    with open(f"/burst/{folder}/{name}", "w", encoding="utf-8") as handle:
        handle.write(text)


def wait_for(path):
    deadline = time.monotonic() + 120
    while not os.path.exists(path):
        if time.monotonic() > deadline:
            raise SystemExit(f"no {path} within 120 s")
        time.sleep(0.005)


mark("ready")
wait_for("/burst/arm")
context = ssl.create_default_context(cafile="/cert/nginx.crt")
context.check_hostname = False
conn = http.client.HTTPSConnection("nginx", 64294, timeout=30, context=context)
conn.connect()
mark("connected")
wait_for("/burst/go")
try:
    conn.request("GET", "/tpot-ingest")
    response = conn.getresponse()
    status = f"{response.status} {response.getheader('X-TPot-Ingest', '-')}"
except OSError as error:
    status = f"error {error}"
mark("result", status)
PY
  chmod 0777 "${BURST_DIR}" "${BURST_DIR}/ready" "${BURST_DIR}/connected" "${BURST_DIR}/result"
  chmod 0644 "${BURST_DIR}/sender.py"
}

run_ingest_checks() {
  python3 - "${TEST_BIND_IP}" "${MAPPED_INGEST_PORT}" "${LS_USER}" "${LS_PASSWORD}" "${TEST_TIMEOUT}" \
    "${CERT_DIR}/nginx.crt" "${STATE_DIR}" <<'PY'
import base64
import http.client
import json
import os
import ssl
import sys
import time

host, port, user, password, timeout = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5])
context = ssl.create_default_context(cafile=sys.argv[6])
state = sys.argv[7]
auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
failures = []


def ask(method, path, login=False, body=None, headers=None):
    conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    sent = dict(headers or {})
    if login:
        sent["Authorization"] = auth
    conn.request(method, path, body=body, headers=sent)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, response.headers, data


def check(what, ok):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def requests():
    try:
        with open(os.path.join(state, "requests"), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except OSError:
        return []


def set_mode(value):
    with open(os.path.join(state, "mode"), "w", encoding="utf-8") as handle:
        handle.write(value)
    want = "closed" if value == "old" else "open"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(os.path.join(state, "probe-port"), encoding="utf-8") as handle:
                if handle.read().strip() == want:
                    time.sleep(0.5)
                    return
        except OSError:
            pass
        time.sleep(0.2)
    raise SystemExit(f"FAIL the stand-in did not switch to {value}")


def probe(method="GET", login=True, headers=None):
    # one request a second: the probe is limited to 1 r/s per address (burst 5)
    time.sleep(1.1)
    return ask(method, "/tpot-ingest", login=login, headers=headers)


# wait until nginx and the stand-in answer
set_mode("new")
deadline = time.monotonic() + timeout
while True:
    try:
        ask("GET", "/", login=True)
        break
    except OSError as exc:
        if time.monotonic() > deadline:
            print(f"FAIL nginx does not answer on {host}:{port}: {exc}")
            sys.exit(1)
        time.sleep(1)

# the ingest itself: only with the login of a sensor (the test runs from outside the container)
status, _, _ = ask("GET", "/")
check("ingest: 401 without a login", status == 401)
before = len(requests())
status, _, _ = ask("POST", "/", login=True, body=b"x" * (3 * 1024 * 1024),
                   headers={"Content-Type": "application/json"})
new = requests()[before:]
check("ingest: a 3 MB batch with the login is passed on (not 413)",
      status == 200 and any(r["port"] == 64305 and r["length"] == 3 * 1024 * 1024 for r in new))
check("ingest: the sensor user goes to Logstash",
      any(r["headers"].get("x-tpot-sensor") == user for r in new))

# no login: 401 with the marker of this HIVE and no WWW-Authenticate, nothing reaches Logstash
wrong = "Basic " + base64.b64encode(f"{user}:wrong-{password}".encode()).decode()
for what, sent in (("no login", {"X-Client": "secret"}), ("a wrong login", {"Authorization": wrong})):
    before = len(requests())
    status, headers, body = probe(login=False, headers=sent)
    check(f"probe, {what}: 401", status == 401)
    check(f"probe, {what}: X-TPot-Ingest login, once", headers.get_all("X-TPot-Ingest") == ["login"])
    check(f"probe, {what}: no WWW-Authenticate", headers.get("WWW-Authenticate") is None)
    check(f"probe, {what}: says a login is required", body == b"login required\n")
    check(f"probe, {what}: no request reaches Logstash (64306 or 64305)", requests()[before:] == [])

# new Logstash and a valid login: 204 with the marker, nothing of the client or of Logstash passed on
before = len(requests())
status, headers, body = probe(headers={"X-Client": "secret", "Cookie": "c=1"})
check("probe, new Logstash: 204", status == 204)
check("probe, new Logstash: X-TPot-Ingest 2, once", headers.get_all("X-TPot-Ingest") == ["2"])
check("probe, new Logstash: no body", body == b"")
check("probe, new Logstash: no header of the stand-in",
      headers.get("X-Leak") is None and headers.get("Set-Cookie") is None)
new = requests()[before:]
check("probe, new Logstash: one GET on 64306, nothing on 64305",
      [(r["port"], r["method"]) for r in new] == [(64306, "GET")])
check("probe, new Logstash: no header or body of the client (the login neither) reaches Logstash",
      all(not ({"authorization", "x-client", "cookie", "x-tpot-sensor"} & set(r["headers"])) and r["length"] == 0
          for r in new))
status, headers, _ = probe("HEAD")
check("probe: HEAD with the login works too", status == 204 and headers.get_all("X-TPot-Ingest") == ["2"])
for login in (False, True):
    before = len(requests())
    status, headers, _ = ask("POST", "/tpot-ingest", login=login, body=b"{}",
                             headers={"Content-Type": "application/json"})
    check(f"probe: 405 for POST {'with' if login else 'without'} a login, nothing to Logstash",
          status == 405 and headers.get("X-TPot-Ingest") is None and requests()[before:] == [])

# a probe port that answers anything but 204: 503 without the marker, no body of Logstash; its 401
# must not look like a refused login of the SENSOR
set_mode("200")
status, headers, body = probe()
check("probe, 200 of Logstash: 503 without the marker",
      status == 503 and headers.get("X-TPot-Ingest") is None and b"LEAK" not in body)
set_mode("401")
status, headers, body = probe()
check("probe, 401 of Logstash: 503 without a marker or WWW-Authenticate",
      status == 503 and headers.get("X-TPot-Ingest") is None and headers.get("WWW-Authenticate") is None
      and body == b"ingest pipeline not ready\n")

# old Logstash (no probe input): 503 without the marker, and no event could be made
set_mode("old")
before = len(requests())
status, headers, body = probe()
check("probe, old Logstash: 503", status == 503)
check("probe, old Logstash: no marker", headers.get("X-TPot-Ingest") is None)
check("probe, old Logstash: says the pipeline is not ready", body == b"ingest pipeline not ready\n")
check("probe, old Logstash: nothing reached the 64305 input", requests()[before:] == [])

# the limit per address comes before the login: a few a second, then 429, and none reaches Logstash
set_mode("new")
time.sleep(6)
before = len(requests())
answers = [ask("GET", "/tpot-ingest") for _ in range(12)]
codes = [status for status, _, _ in answers]
check(f"probe without a login: 401 first, 429 beyond the burst ({codes})",
      codes[0] == 401 and 429 in codes and set(codes) == {401, 429})
check("probe without a login: every 401 has the marker",
      all(headers.get("X-TPot-Ingest") == "login" for status, headers, _ in answers if status == 401))
check("probe without a login: no request of the burst reaches Logstash", requests()[before:] == [])

if failures:
    print(f"{len(failures)} check(s) failed")
    sys.exit(1)
PY
}

# /tpot-ingest has no limit over all addresses: 30 senders with an address of their own each send one
# GET without a login at the same time, each gets the 401 with the marker (none a 429), and none of
# them reaches Logstash
run_ingest_burst() {
  local senders=30 rc=0
  # the senders belong to the project: the down of the cleanup takes them too
  export COMPOSE_PROFILES=burst
  test_info "Starting ${senders} senders for the burst on /tpot-ingest"
  test_compose up -d --no-build --no-deps --scale "sender=${senders}" sender >/dev/null \
    || test_die "Could not start the senders of the burst"
  python3 - "${BURST_DIR}" "${senders}" "${TEST_TIMEOUT}" "${STATE_DIR}" <<'PY' || rc=$?
import os
import sys
import time

burst, senders, timeout, state = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]


def requests():
    try:
        with open(os.path.join(state, "requests"), encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def wait_for(folder, seconds):
    deadline = time.monotonic() + seconds
    while len(os.listdir(os.path.join(burst, folder))) < senders:
        if time.monotonic() > deadline:
            raise SystemExit(f"FAIL only {len(os.listdir(os.path.join(burst, folder)))} of {senders} senders "
                             f"in {folder}/ after {seconds} s")
        time.sleep(0.05)


wait_for("ready", timeout)
open(os.path.join(burst, "arm"), "w").close()
# nginx closes a connection without a request after 10 s (client_header_timeout)
wait_for("connected", 8)
before = requests()
open(os.path.join(burst, "go"), "w").close()
wait_for("result", timeout)
codes = []
for name in sorted(os.listdir(os.path.join(burst, "result"))):
    with open(os.path.join(burst, "result", name), encoding="utf-8") as handle:
        codes.append(handle.read())
counts = {code: codes.count(code) for code in sorted(set(codes))}
ok = len(codes) == senders and set(codes) == {"401 login"}
print(("ok   " if ok else "FAIL ") + f"probe: {senders} senders without a login at once, all 401 with the marker, "
      f"none 429 ({counts})")
reached = requests() - before
print(("ok   " if reached == 0 else "FAIL ") + f"probe: none of the {senders} senders reaches Logstash ({reached})")
sys.exit(0 if ok and reached == 0 else 1)
PY
  test_compose rm -sf sender >/dev/null 2>&1 || true
  return "${rc}"
}

# The handshake of both listeners (openssl of the init image, in the network of the nginx container):
# the post-quantum hybrid key exchange of ssl_ecdh_curve and the P-384 key of the test certificate
run_tls_checks() {
  local port output rc=0
  for port in 64294 64297; do
    output="$(docker run --rm --network "container:${TEST_CONTAINER_NAME}" --entrypoint openssl "${INIT_IMAGE}" \
      s_client -connect "127.0.0.1:${port}" -groups X25519MLKEM768 </dev/null 2>&1)" || true
    if grep -q "group: X25519MLKEM768" <<<"${output}"; then
      echo "ok   TLS ${port}: the key exchange is X25519MLKEM768"
    else
      echo "FAIL TLS ${port}: the key exchange is not X25519MLKEM768"
      rc=1
    fi
    if grep -q "Server public key is 384 bit" <<<"${output}"; then
      echo "ok   TLS ${port}: the server key is 384 bit"
    else
      echo "FAIL TLS ${port}: the server key is not 384 bit"
      rc=1
    fi
    [[ "${rc}" -eq 0 ]] || printf '%s\n' "${output}" | grep -E "group|Server public key|error|Cipher" >&2 || true
  done
  return "${rc}"
}

main() {
  parse_args "$@"
  validate_args
  test_check_dependencies
  test_require_command openssl

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi
  if [[ -z "${INIT_IMAGE}" ]]; then
    INIT_IMAGE="$(test_read_compose_image tpotinit "${DEFAULT_INIT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"
  test_require_image "${INIT_IMAGE}" "docker compose -f docker/tpotinit/docker-compose.yml build tpotinit"

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
  MAPPED_INGEST_PORT="$(test_get_mapped_port nginx 64294)" || test_die "Could not resolve the mapped ingest port"
  test_info "The ingest listener is on https://${TEST_BIND_IP}:${MAPPED_INGEST_PORT}"
  run_ingest_checks || test_die "Nginx ingest checks failed"
  run_ingest_burst || test_die "Nginx ingest probe of many senders failed"
  run_tls_checks || test_die "Nginx TLS handshake checks failed"
  test_ok "Nginx smoke test passed"
}

main "$@"
