#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="cowrie"
DEFAULT_IMAGE="dtagdevsec/cowrie:24.04.2"
IMAGE=""
SSH_PORT=""
TELNET_PORT=""
PERSONA=""
ALL_PERSONAS="false"
LOG_DIR=""
TTY_LOG_DIR=""
DL_DIR=""
KEYS_DIR=""
JSON_LOG_FILE=""
MAPPED_SSH_PORT=""
MAPPED_TELNET_PORT=""
EXPECTED_IDENTITY_FILE=""
SHELL_OUTPUT_FILE=""
# the second start with COWRIE_PERSONA_ROTATION; run_all_personas does it once, for the first persona
ROTATION_STEP="true"
ROTATION_DAYS="7"

usage() {
  cat <<EOF
Usage: $0 [options]

Run an isolated post-build smoke test for the Cowrie image.

Options:
  --image IMAGE        Image to test. Defaults to docker/cowrie/docker-compose.yml.
  --ssh-port PORT      Host TCP port for SSH. Default: dynamic free port.
  --telnet-port PORT   Host TCP port for Telnet. Default: dynamic free port.
  --persona ID         Force a Cowrie persona; several IDs separated by spaces
                       ("raspios ipcam") pick one of them. Default: random.
  --all-personas       Run the whole test once per persona of the image.
  --timeout SEC        Timeout for startup, protocol, and log checks. Default: 30.
  --bind-ip IP         Host IP to bind. Default: 127.0.0.1.
  --keep-artifacts     Keep temporary compose file and logs for debugging.
  -h, --help           Show this help message.

Environment:
  TEST_LONG_LINE_SECONDS  Seconds the echo of a 10,000-character line may take. Default: 2.
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
      --ssh-port)
        [[ $# -ge 2 ]] || test_die "--ssh-port requires an argument"
        SSH_PORT="$2"
        shift 2
        ;;
      --ssh-port=*)
        SSH_PORT="${1#*=}"
        shift
        ;;
      --telnet-port)
        [[ $# -ge 2 ]] || test_die "--telnet-port requires an argument"
        TELNET_PORT="$2"
        shift 2
        ;;
      --telnet-port=*)
        TELNET_PORT="${1#*=}"
        shift
        ;;
      --persona)
        [[ $# -ge 2 ]] || test_die "--persona requires an argument"
        PERSONA="$2"
        shift 2
        ;;
      --persona=*)
        PERSONA="${1#*=}"
        shift
        ;;
      --all-personas)
        ALL_PERSONAS="true"
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
  if [[ -n "${SSH_PORT}" ]]; then
    test_validate_port "${SSH_PORT}"
  fi
  if [[ -n "${TELNET_PORT}" ]]; then
    test_validate_port "${TELNET_PORT}"
  fi
  if [[ -n "${PERSONA}" && ! "${PERSONA}" =~ ^[a-z0-9][a-z0-9-]*(\ [a-z0-9][a-z0-9-]*)*$ ]]; then
    test_die "--persona must be persona IDs (lowercase letters, numbers, and dashes) separated by single spaces"
  fi
  if [[ "${ALL_PERSONAS}" == "true" && -n "${PERSONA}" ]]; then
    test_die "--persona and --all-personas cannot be used together"
  fi
  if [[ ! "${LONG_LINE_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ || "${LONG_LINE_SECONDS}" =~ ^0+([.]0+)?$ ]]; then
    test_die "TEST_LONG_LINE_SECONDS must be a number of seconds greater than 0"
  fi
}

prepare_cowrie_harness() {
  test_prepare_harness "${TEST_NAME}"

  LOG_DIR="${TEST_TMP_ROOT}/log"
  TTY_LOG_DIR="${LOG_DIR}/tty"
  DL_DIR="${TEST_TMP_ROOT}/downloads"
  KEYS_DIR="${TEST_TMP_ROOT}/keys"
  JSON_LOG_FILE="${LOG_DIR}/cowrie.json"
  EXPECTED_IDENTITY_FILE="${TEST_TMP_ROOT}/expected-identity.json"
  SHELL_OUTPUT_FILE="${TEST_TMP_ROOT}/telnet-shell.txt"
  TEST_ARTIFACT_LOG_DIR="${LOG_DIR}"

  mkdir -p "${LOG_DIR}" "${TTY_LOG_DIR}" "${DL_DIR}" "${KEYS_DIR}"
  chmod 0777 "${LOG_DIR}" "${TTY_LOG_DIR}" "${DL_DIR}" "${KEYS_DIR}"

  cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  cowrie:
    image: "${IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    restart: "no"
    read_only: true
    user: "2000:2000"
    tmpfs:
      - /tmp/cowrie:uid=2000,gid=2000
      - /tmp/cowrie/data:uid=2000,gid=2000
    ports:
      - "${TEST_BIND_IP}:${SSH_PORT}:22"
      - "${TEST_BIND_IP}:${TELNET_PORT}:23"
    environment:
      COWRIE_PERSONA: "${PERSONA}"
      COWRIE_PERSONA_ROTATION: "\${COWRIE_PERSONA_ROTATION:-0}"
    volumes:
      - "${DL_DIR}:/home/cowrie/cowrie/dl"
      - "${KEYS_DIR}:/home/cowrie/cowrie/etc"
      - "${LOG_DIR}:/home/cowrie/cowrie/log"
      - "${TTY_LOG_DIR}:/home/cowrie/cowrie/log/tty"
networks:
  default:
    name: "${TEST_PROJECT_NAME}_net"
EOF
}

wait_for_json_log() {
  local deadline=$((SECONDS + TEST_TIMEOUT))

  while (( SECONDS < deadline )); do
    if [[ -f "${JSON_LOG_FILE}" ]]; then
      return 0
    fi
    sleep 1
  done

  return 1
}

run_ssh_banner_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_SSH_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
client_banner = f"SSH-2.0-tpot-cowrie-smoke-{token}\r\n".encode("ascii")

try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(1)
        chunks = []
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(256)
            except socket.timeout:
                continue
            if not chunk:
                raise RuntimeError("Connection closed before SSH banner")
            chunks.append(chunk)
            if b"\n" in b"".join(chunks):
                break

        banner = b"".join(chunks).splitlines()[0]
        if not banner.startswith(b"SSH-"):
            raise RuntimeError(f"Expected SSH banner, got {banner!r}")

        sock.sendall(client_banner)
        time.sleep(0.2)

    print(f"SSH banner: {banner.decode('ascii', errors='replace')}")
except Exception as exc:
    print(f"SSH probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_ssh_banner_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_ssh_banner_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

# An SSH session up to the authentication (the pre-login banner of /etc/issue.net is sent there), with
# the Twisted SSH client of the image itself in a container in the honeypot's network: user
# "smoke-test", the "none" method only, no password, nothing run.
run_ssh_userauth_probe() {
  docker run --rm --network "container:${TEST_CONTAINER_NAME}" --read-only --user 2000:2000 \
    --entrypoint python3 "${IMAGE}" -c '
import sys
from twisted.conch.ssh import connection, transport, userauth
from twisted.internet import defer, protocol, reactor

reached = []


class Auth(userauth.SSHUserAuthClient):
    def ssh_USERAUTH_FAILURE(self, packet):
        reached.append(True)
        self.transport.loseConnection()

    def getPassword(self, prompt=None):
        return None


class Client(transport.SSHClientTransport):
    def verifyHostKey(self, pubKey, fingerprint):
        return defer.succeed(True)

    def connectionSecure(self):
        self.requestService(Auth(b"smoke-test", connection.SSHConnection()))

    def connectionLost(self, reason):
        transport.SSHClientTransport.connectionLost(self, reason)
        if reactor.running:
            reactor.stop()


factory = protocol.ClientFactory()
factory.protocol = Client
reactor.connectTCP("127.0.0.1", 22, factory)
reactor.callLater(int(sys.argv[1]), reactor.stop)
reactor.run()
if not reached:
    sys.exit("SSH userauth probe: no authentication reply")
print("SSH session reached the authentication")
' "${TEST_TIMEOUT}"
}

# The pairs of [honeypot] auth_refused_pairs in the container's cowrie.cfg, read as Cowrie reads it.
read_refused_pairs() {
  docker exec -i -e PYTHONDONTWRITEBYTECODE=1 "${TEST_CONTAINER_NAME}" python3 - <<'PY'
import configparser
import json

config = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
config.read("/tmp/cowrie/runtime/cowrie.cfg")
print(json.dumps(json.loads(config.get("honeypot", "auth_refused_pairs"))))
PY
}

# Every pair of auth_refused_pairs three times over SSH (password, the Twisted client of the image, one
# connection per pair) and three times over Telnet, with the clients in a container in the honeypot's
# network: never a login.
run_refused_login_probe() {
  local pairs="$1"

  docker run --rm --network "container:${TEST_CONTAINER_NAME}" --read-only --user 2000:2000 \
    --entrypoint python3 "${IMAGE}" -c '
import json
import socket
import sys
import time

from twisted.conch.ssh import connection, transport, userauth
from twisted.internet import defer, protocol, reactor

PAIRS = [tuple(pair) for pair in json.loads(sys.argv[1])]
TIMEOUT = int(sys.argv[2])
TRIES = 3
results = {"ssh_failed": 0, "ssh_success": 0, "telnet_failed": 0, "telnet_success": 0}


class Auth(userauth.SSHUserAuthClient):
    def __init__(self, login, password):
        userauth.SSHUserAuthClient.__init__(self, login.encode(), connection.SSHConnection())
        self.secret = password.encode()
        self.tries = 0

    def tryAuth(self, kind):
        # the password method only, TRIES times
        if kind != b"password" or self.tries == TRIES:
            return False
        self.tries += 1
        return userauth.SSHUserAuthClient.tryAuth(self, kind)

    def getPassword(self, prompt=None):
        return defer.succeed(self.secret)

    def ssh_USERAUTH_FAILURE(self, packet):
        if self.lastAuth == b"password":
            results["ssh_failed"] += 1
        return userauth.SSHUserAuthClient.ssh_USERAUTH_FAILURE(self, packet)

    def ssh_USERAUTH_SUCCESS(self, packet):
        results["ssh_success"] += 1
        self.transport.loseConnection()


class Client(transport.SSHClientTransport):
    def verifyHostKey(self, pubKey, fingerprint):
        return defer.succeed(True)

    def connectionSecure(self):
        self.requestService(Auth(*self.factory.pair))

    def connectionLost(self, reason):
        transport.SSHClientTransport.connectionLost(self, reason)
        if not self.factory.done.called:
            self.factory.done.callback(None)


class Factory(protocol.ClientFactory):
    protocol = Client

    def __init__(self, pair):
        self.pair = pair
        self.done = defer.Deferred()

    def clientConnectionFailed(self, connector, reason):
        self.done.callback(None)


def ssh():
    factories = [Factory(pair) for pair in PAIRS]
    for factory in factories:
        reactor.connectTCP("127.0.0.1", 22, factory)
    defer.DeferredList([factory.done for factory in factories]).addBoth(lambda _: reactor.stop())


reactor.callWhenRunning(ssh)
reactor.callLater(TIMEOUT, reactor.stop)
reactor.run()


class Deadline(Exception):
    pass


def read_until(sock, needles, deadline):
    data = bytearray()
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            break
        index = 0
        while index < len(chunk):
            if chunk[index] == 255 and index + 2 < len(chunk) and chunk[index + 1] in (251, 252, 253, 254):
                # refuse every option: WONT for DO / DONT, DONT for WILL / WONT
                sock.sendall(bytes([255, 252 if chunk[index + 1] in (253, 254) else 254, chunk[index + 2]]))
                index += 3
                continue
            data.append(chunk[index])
            index += 1
        text = data.decode("utf-8", errors="replace").lower().rstrip()
        for needle in needles:
            if text.endswith(needle):
                return needle
    # the time of the whole probe is over: a timeout; the end of a shorter wait only: no such needle
    if time.monotonic() >= END:
        raise Deadline(f"telnet: none of {needles} within {TIMEOUT} s, received {bytes(data)[-120:]!r}")
    raise RuntimeError(f"telnet: none of {needles} in {bytes(data)[-120:]!r}")


deadline = END = time.monotonic() + TIMEOUT
try:
    for login, password in PAIRS:
        with socket.create_connection(("127.0.0.1", 23), timeout=TIMEOUT) as sock:
            sock.settimeout(0.5)
            read_until(sock, ("login:",), deadline)
            for _ in range(TRIES):
                sock.sendall(login.encode() + b"\r\n")
                read_until(sock, ("password:",), deadline)
                sock.sendall(password.encode() + b"\r\n")
                # refused: "Login incorrect" and the login prompt again within 10 s; anything else
                # counts as a login, the end of the time of the whole probe as a timeout
                try:
                    read_until(sock, ("login:",), min(deadline, time.monotonic() + 10))
                    results["telnet_failed"] += 1
                except Deadline:
                    raise
                except RuntimeError:
                    results["telnet_success"] += 1
                    break
except (Deadline, TimeoutError) as exc:
    sys.exit(f"refused login probe: Telnet timed out after {TIMEOUT} s ({exc}); so far {json.dumps(results)}")
except OSError as exc:
    sys.exit(f"refused login probe: Telnet failed ({exc}); so far {json.dumps(results)}")

print(json.dumps(results))
expected = {"ssh_failed": TRIES * len(PAIRS), "ssh_success": 0, "telnet_failed": TRIES * len(PAIRS),
            "telnet_success": 0}
if results != expected:
    sys.exit(f"refused login probe: {results}, expected {expected}")
' "${pairs}" "${TEST_TIMEOUT}"
}

# AuthRandom's state after run_refused_login_probe, read in the container ([honeypot] state_path
# /tmp/cowrie/data): no pair of auth_refused_pairs in its cache, in the tries or as the login of an
# address, and no entry for 127.0.0.1, the address of the probe's clients (nothing else logs in from
# there before it). No state file at all is the same.
assert_refused_pairs_left_no_state() {
  local pairs="$1"

  docker exec -i -e PYTHONDONTWRITEBYTECODE=1 "${TEST_CONTAINER_NAME}" python3 - "${pairs}" <<'PY'
import json
import sys
from pathlib import Path

pairs = {f"{login}:{password}" for login, password in json.loads(sys.argv[1])}
path = Path("/tmp/cowrie/data/auth_random.json")
state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
problems = []
cached = sorted(pairs & set(state.get("cache", [])))
if cached:
    problems.append(f"cache has {cached}")
for address, entry in state.items():
    if not isinstance(entry, dict):
        continue
    tried = sorted(pairs & set(entry.get("tried", [])))
    if tried:
        problems.append(f"{address} tried {tried}")
    if f"{entry.get('user')}:{entry.get('pw')}" in pairs:
        problems.append(f"{address} logs in with {entry.get('user')}:{entry.get('pw')}")
if "127.0.0.1" in state:
    problems.append(f"127.0.0.1 has an entry: {state['127.0.0.1']}")
if problems:
    sys.exit("auth_random.json after the refused logins: " + "; ".join(problems))
print(f"auth_random.json: {len(state)} entries" if path.exists() else "auth_random.json: none yet")
PY
}

# A cowrie.login.failed event for every attempt of run_refused_login_probe, no cowrie.login.success.
wait_for_refused_login_events() {
  local pairs="$1"

  python3 - "${JSON_LOG_FILE}" "${pairs}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
pairs = {tuple(pair) for pair in json.loads(sys.argv[2])}
deadline = time.monotonic() + int(sys.argv[3])
while True:
    failed, success = {}, 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        pair = (event.get("username"), event.get("password"))
        if pair not in pairs:
            continue
        if event.get("eventid") == "cowrie.login.failed":
            failed[pair] = failed.get(pair, 0) + 1
        elif event.get("eventid") == "cowrie.login.success":
            success += 1
    if success:
        sys.exit(f"cowrie.login.success for a pair of auth_refused_pairs ({success})")
    # three attempts over SSH and three over Telnet
    if all(failed.get(pair, 0) >= 6 for pair in pairs):
        print(f"cowrie.login.failed for {len(pairs)} pairs: {sorted(failed.values())}")
        break
    if time.monotonic() > deadline:
        sys.exit(f"cowrie.login.failed events missing: {failed}")
    time.sleep(0.5)
PY
}

run_telnet_login_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_TELNET_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import socket
import sys
import time

IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
username = f"cowrie-user-{token}"
password = f"cowrie-pass-{token}"
deadline = time.monotonic() + timeout


def readable(data):
    return data.decode("utf-8", errors="ignore").lower()


def read_until(sock, needles):
    data = bytearray()
    in_subnegotiation = False
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(256)
        except socket.timeout:
            continue
        if not chunk:
            break

        index = 0
        while index < len(chunk):
            byte = chunk[index]
            if byte == IAC and index + 1 < len(chunk):
                command = chunk[index + 1]
                if command in (DO, DONT, WILL, WONT) and index + 2 < len(chunk):
                    option = chunk[index + 2]
                    if command in (DO, DONT):
                        sock.sendall(bytes([IAC, WONT, option]))
                    else:
                        sock.sendall(bytes([IAC, DONT, option]))
                    index += 3
                    continue
                if command == SB:
                    in_subnegotiation = True
                    index += 2
                    continue
                if command == SE:
                    in_subnegotiation = False
                    index += 2
                    continue
                index += 2
                continue

            if not in_subnegotiation:
                data.append(byte)
            index += 1

        text = readable(data)
        if any(needle in text for needle in needles):
            return bytes(data)

    raise RuntimeError(f"Timed out waiting for one of {needles}; received {bytes(data)!r}")


try:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(1)
        login_prompt = read_until(sock, ("login:", "username:"))
        sock.sendall((username + "\r\n").encode("ascii"))
        password_prompt = read_until(sock, ("password:",))
        sock.sendall((password + "\r\n").encode("ascii"))
        time.sleep(0.5)

    print(
        "Telnet prompts: "
        f"{login_prompt[-80:].decode('utf-8', errors='replace')!r}; "
        f"{password_prompt[-80:].decode('utf-8', errors='replace')!r}"
    )
except Exception as exc:
    print(f"Telnet probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

run_telnet_login_probe_with_retries() {
  local token="$1"
  local deadline=$((SECONDS + TEST_TIMEOUT))
  local output=""

  while (( SECONDS < deadline )); do
    if output="$(run_telnet_login_probe "${token}" 2>&1)"; then
      printf '%s\n' "${output}"
      return 0
    fi
    sleep 1
  done

  printf '%s\n' "${output}" >&2
  return 1
}

run_telnet_shell_probe() {
  local token="$1"

  python3 - "${TEST_BIND_IP}" "${MAPPED_TELNET_PORT}" "${token}" "${TEST_TIMEOUT}" <<'PY'
import re
import socket
import sys
import time

IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240

BEGIN = "TPOT_BEGIN"
END = "TPOT_END"
PY_MARK = "TPOT_PY"
AFTER_MARK = "TPOT_AFTER"
APT_MARK = "TPOT_APT"
COMMAND = (
    f"echo {BEGIN}; uname -r; hostname; nproc; grep -c ^processor /proc/cpuinfo; "
    f"awk '/MemTotal/ {{print $2}}' /proc/meminfo; "
    # the forms bots end a session with: df through awk (the persona's df table and awk)
    "df -h | awk 'FNR == 2 {print \"[\" $4 \"]\"}'; "
    "df -h | awk 'NR == 2 || NR == 3 {printf \"%s \", $0} END {print \"\"}'; "
    "df -k | awk 'FNR == 2 {print \"[\" $4 \"]\"}'; "
    f"cat /etc/os-release; "
    # the interpreter answers and the line goes on (a harmless stand-in, no attacker code)
    f"echo {PY_MARK}; python3 -c 'print(1)'; echo {AFTER_MARK}; "
    # apt-get of the persona with a name no release has (EdgeOS: the emulated apt 1.4 without sources)
    f"echo {APT_MARK}; apt-get install nosuchpkg; echo {END}"
)
# AuthRandom (2, 5, 10) accepts after 2 to 5 different attempts per source IP
MAX_LOGINS = 12
# bash "user@host:~$ ", BusyBox "# " / "$ ", EdgeOS vbash "ubnt@host:~$ "
PROMPT = re.compile(r"[$#] ?$")
# Cowrie's insults switch the insert mode around every line (ESC [4l / ESC [4h)
ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

host = sys.argv[1]
port = int(sys.argv[2])
token = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + 2 * timeout


class Closed(Exception):
    pass


def clean(text):
    return ESCAPE.sub("", text).replace("\r", "")


def last_line(text):
    return clean(text).rstrip("\n").split("\n")[-1]


def at_login(text):
    return last_line(text).strip().lower().endswith(("login:", "username:"))


def at_prompt(text):
    return PROMPT.search(clean(text)) is not None and not at_login(text)


class Session:
    def __init__(self):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(0.5)
        self.data = bytearray()
        self.in_subnegotiation = False

    def close(self):
        self.sock.close()

    def feed(self, chunk):
        index = 0
        while index < len(chunk):
            byte = chunk[index]
            if byte == IAC and index + 1 < len(chunk):
                command = chunk[index + 1]
                if command in (DO, DONT, WILL, WONT) and index + 2 < len(chunk):
                    option = chunk[index + 2]
                    if command in (DO, DONT):
                        self.sock.sendall(bytes([IAC, WONT, option]))
                    else:
                        self.sock.sendall(bytes([IAC, DONT, option]))
                    index += 3
                    continue
                if command == SB:
                    self.in_subnegotiation = True
                elif command == SE:
                    self.in_subnegotiation = False
                index += 2
                continue
            if not self.in_subnegotiation:
                self.data.append(byte)
            index += 1

    def text(self):
        return self.data.decode("utf-8", errors="replace")

    def wait(self, done, quiet=0.0):
        """Read until done(text) holds and the server has been quiet for `quiet` seconds."""
        last = time.monotonic()
        while time.monotonic() < deadline:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                if done(self.text()) and time.monotonic() - last >= quiet:
                    return self.text()
                continue
            if not chunk:
                raise Closed(self.text())
            self.feed(chunk)
            last = time.monotonic()
        raise RuntimeError(f"Timed out, received {clean(self.text())[-300:]!r}")

    def send(self, line):
        self.data.clear()
        self.sock.sendall((line + "\r\n").encode("ascii"))


def login():
    """A shell after at most MAX_LOGINS attempts; the passwords only depend on the token and the attempt,
    so a retry with the same token repeats the accepted one."""
    attempt = 0
    while attempt < MAX_LOGINS:
        session = Session()
        try:
            session.wait(at_login)
            while attempt < MAX_LOGINS:
                attempt += 1
                session.send("root")
                session.wait(lambda text: "password:" in text.lower())
                session.send(f"tpot-smoke-{token}-{attempt}")
                text = session.wait(lambda text: at_login(text) or at_prompt(text), quiet=1.0)
                if at_prompt(text):
                    return session, attempt
        except Closed:
            session.close()
    raise RuntimeError(f"No shell after {MAX_LOGINS} login attempts")


try:
    session, attempts = login()
    session.send(COMMAND)
    text = session.wait(lambda text: END in clean(text).split("\n") and at_prompt(text), quiet=0.5)
    session.send("exit")
    try:
        session.wait(lambda text: False)
    except (Closed, RuntimeError):
        pass
    session.close()
except Exception as exc:
    print(f"Telnet shell probe failed: {exc}", file=sys.stderr)
    sys.exit(1)

lines = [line.rstrip() for line in clean(text).split("\n")]
output = lines[lines.index(BEGIN) + 1:lines.index(END)]
print(f"Telnet shell after {attempts} login attempts", file=sys.stderr)
print("\n".join(output))
PY
}

# A line of 10,000 characters and a download (`echo a...a; wget http://127.0.0.1:1/x`) in the
# interactive shell over Telnet and SSH (pty), typed one byte per write and pasted in one write, with
# the clients in a container in the honeypot's network: the echo of the whole line is back within
# LONG_LINE_SECONDS, the shell answers both commands and gives the prompt back. Then a pasted line of
# 70,000 characters over Telnet: the shell keeps 64 KiB of it and takes the next line.
# Cowrie's wget (also the applet of the BusyBox personas) refuses an address that is not global before
# it connects, with this answer and no download event; cowrie.command.input logs the line.
LONG_LINE_LENGTH=10000
# the seconds the echo of the line may take (TEST_LONG_LINE_SECONDS for a slow host)
LONG_LINE_SECONDS="${TEST_LONG_LINE_SECONDS:-2}"
LONG_LINE_WGET="wget: unable to resolve host address ‘127.0.0.1’"
LONG_LINE_MAX=65536
LONG_LINE_OVER=70000

long_line() {
  python3 -c 'import sys; print("echo " + "a" * int(sys.argv[1]) + "; wget http://127.0.0.1:1/x")' "${LONG_LINE_LENGTH}"
}

run_long_line_probe() {
  docker run --rm -i --network "container:${TEST_CONTAINER_NAME}" --read-only --user 2000:2000 \
    --entrypoint python3 "${IMAGE}" - "$(long_line)" "${LONG_LINE_SECONDS}" "${LONG_LINE_OVER}" \
    "${TEST_TIMEOUT}" "${LONG_LINE_WGET}" <<'PY'
import json
import re
import socket
import sys
import threading
import time

LINE = sys.argv[1].encode()
SECONDS = float(sys.argv[2])
OVER = int(sys.argv[3])
TIMEOUT = int(sys.argv[4])
WGET = sys.argv[5]
LENGTH = LINE.count(b"a")
# AuthRandom accepts after 2 to 5 different attempts per source IP and keeps the pair it accepted
PASSWORDS = [f"tpot-long-{n}" for n in range(1, 13)]
PROMPT = re.compile(rb"[$#] ?$")
ESCAPE = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")


def clean(data):
    return ESCAPE.sub(b"", bytes(data)).replace(b"\r", b"")


def last_line(data):
    return clean(data).rstrip(b"\n").split(b"\n")[-1]


def at_login(data):
    return last_line(data).strip().lower().endswith((b"login:", b"username:"))


def at_prompt(data):
    return PROMPT.search(last_line(data)) is not None and not at_login(data)


class Closed(Exception):
    pass


class Telnet:
    """A Telnet client that refuses every option."""

    def __init__(self):
        self.sock = socket.create_connection(("127.0.0.1", 23), timeout=TIMEOUT)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(0.1)
        self.data = bytearray()
        self.end = b"\r\n"

    def send(self, payload):
        self.sock.sendall(payload)

    def wait(self, done, deadline, quiet=0.0):
        last = time.monotonic()
        while time.monotonic() < deadline:
            if done(self.data) and time.monotonic() - last >= quiet:
                return True
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise Closed()
            index = 0
            while index < len(chunk):
                if chunk[index] == 255 and index + 2 < len(chunk) and chunk[index + 1] in (251, 252, 253, 254):
                    self.sock.sendall(bytes([255, 252 if chunk[index + 1] in (253, 254) else 254, chunk[index + 2]]))
                    index += 3
                    continue
                if chunk[index] == 255 and index + 1 < len(chunk):
                    index += 2
                    continue
                self.data.append(chunk[index])
                index += 1
            last = time.monotonic()
        return False

    def close(self):
        self.sock.close()


def telnet_shell(deadline):
    session = None
    for password in PASSWORDS:
        if session is None:
            session = Telnet()
            session.wait(at_login, deadline)
        session.data.clear()
        session.send(b"root\r\n")
        session.wait(lambda data: b"password:" in data.lower(), deadline)
        session.data.clear()
        session.send(password.encode() + b"\r\n")
        try:
            session.wait(lambda data: at_login(data) or at_prompt(data), deadline, quiet=1.0)
        except Closed:
            session = None
            continue
        if at_prompt(session.data):
            return session
    raise RuntimeError("telnet: no shell")


class Ssh:
    """The Twisted SSH client of the image with a pty, in a thread of its own."""

    reactor = None

    def __init__(self, deadline):
        from twisted.conch.ssh import channel, connection, session, transport, userauth
        from twisted.internet import defer, protocol, reactor

        self.data = bytearray()
        self.lock = threading.Condition()
        self.channel = None
        self.end = b"\r"
        client = self
        passwords = list(PASSWORDS)

        class Shell(channel.SSHChannel):
            name = b"session"

            def channelOpen(self, _):
                pty = session.packRequest_pty_req(b"xterm", (24, 80, 0, 0), b"")
                self.conn.sendRequest(self, b"pty-req", pty, wantReply=False)
                self.conn.sendRequest(self, b"shell", b"", wantReply=False)
                client.channel = self

            def dataReceived(self, payload):
                with client.lock:
                    client.data += payload
                    client.lock.notify_all()

        class Conn(connection.SSHConnection):
            def serviceStarted(self):
                self.openChannel(Shell(conn=self))

        class Auth(userauth.SSHUserAuthClient):
            def tryAuth(self, kind):
                return kind == b"password" and userauth.SSHUserAuthClient.tryAuth(self, kind)

            def getPassword(self, prompt=None):
                return defer.succeed(passwords.pop(0).encode()) if passwords else None

        class Client(transport.SSHClientTransport):
            def verifyHostKey(self, pubKey, fingerprint):
                return defer.succeed(True)

            def connectionSecure(self):
                self.requestService(Auth(b"root", Conn()))

        factory = protocol.ClientFactory()
        factory.protocol = Client
        Ssh.reactor = reactor
        reactor.callFromThread(reactor.connectTCP, "127.0.0.1", 22, factory)
        if not self.wait(lambda data: self.channel is not None and at_prompt(data), deadline, quiet=0.5):
            raise RuntimeError("ssh: no shell")

    def send(self, payload):
        Ssh.reactor.callFromThread(self.channel.write, payload)

    def wait(self, done, deadline, quiet=0.0):
        with self.lock:
            size, last = -1, time.monotonic()
            while time.monotonic() < deadline:
                if len(self.data) != size:
                    size, last = len(self.data), time.monotonic()
                if done(self.data) and time.monotonic() - last >= quiet:
                    return True
                self.lock.wait(0.05)
        return False

    def close(self):
        Ssh.reactor.callFromThread(self.channel.loseConnection)


def type_line(session, line, typed, deadline):
    """Sends line (typed: one byte per write), returns the seconds until its echo is back; then the
    line end, and the output up to the prompt."""
    session.data.clear()
    started = time.monotonic()
    if typed:
        for value in line:
            session.send(bytes([value]))
    else:
        session.send(line)
    count = line.count(b"a")
    if not session.wait(lambda data: data.count(b"a") >= count, deadline):
        raise RuntimeError(f"echo incomplete: {bytes(session.data).count(b'a')} of {count}")
    echo = time.monotonic() - started
    session.data.clear()
    session.send(session.end)
    if not session.wait(at_prompt, deadline, quiet=0.5):
        raise RuntimeError(f"no prompt after the line: {clean(session.data)[-200:]!r}")
    return echo, clean(session.data).decode("utf-8", errors="replace")


results = []
try:
    deadline = time.monotonic() + 6 * TIMEOUT
    from twisted.internet import reactor

    threading.Thread(target=reactor.run, kwargs={"installSignalHandlers": False}, daemon=True).start()
    for name in ("telnet", "ssh"):
        for typed in (True, False):
            session = telnet_shell(deadline) if name == "telnet" else Ssh(deadline)
            echo, output = type_line(session, LINE, typed, deadline)
            lines = output.split("\n")
            result = {"protocol": name, "typed": typed, "echo_seconds": round(echo, 3),
                      "echo_line": any(line.strip() == "a" * LENGTH for line in lines),
                      "wget": [line for line in lines if "wget" in line][-1:],
                      "wget_answer": WGET in (line.strip() for line in lines)}
            results.append(result)
            session.close()
            if echo >= SECONDS or not result["echo_line"] or not result["wget_answer"]:
                raise RuntimeError(f"long line: {result}")
    # beyond the bound: 64 KiB are kept, the shell takes the next line
    session = telnet_shell(deadline)
    echo, output = type_line(session, b"echo " + b"a" * OVER, False, deadline)
    _, after = type_line(session, b"echo TPOT_LONG_DONE", False, deadline)
    session.close()
    results.append({"protocol": "telnet", "over": OVER, "echo_seconds": round(echo, 3),
                    "next_line": "TPOT_LONG_DONE" in after.split("\n")})
    if not results[-1]["next_line"]:
        raise RuntimeError(f"no answer after a line of {OVER} characters: {after[-200:]!r}")
except Exception as exc:
    print(json.dumps(results))
    sys.exit(f"long line probe failed: {exc}")
finally:
    if Ssh.reactor is not None:
        Ssh.reactor.callFromThread(Ssh.reactor.stop)
print(json.dumps(results))
PY
}

# cowrie.command.input has the long line of each of the four sessions as it was sent, the line beyond
# the bound cut to 64 KiB, and the next line of that session.
wait_for_long_line_events() {
  python3 - "${JSON_LOG_FILE}" "$(long_line)" "${LONG_LINE_MAX}" "${LONG_LINE_OVER}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
line = sys.argv[2]
line_max = int(sys.argv[3])
cut = ("echo " + "a" * int(sys.argv[4]))[:line_max]
deadline = time.monotonic() + int(sys.argv[5])
while True:
    inputs = []
    for text in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(text)
        except ValueError:
            continue
        if event.get("eventid") == "cowrie.command.input":
            inputs.append((event.get("session"), event.get("input")))
    full = {session for session, value in inputs if value == line}
    cut_sessions = {session for session, value in inputs if value == cut}
    done = {session for session, value in inputs if value == "echo TPOT_LONG_DONE"}
    if len(full) >= 4 and cut_sessions & done:
        print(f"cowrie.command.input: the long line in {len(full)} sessions, {line_max} of the longer one")
        break
    if time.monotonic() > deadline:
        longest = sorted((len(value or "") for _, value in inputs), reverse=True)[:6]
        sys.exit(f"long line events missing: {len(full)} sessions with the line, cut {bool(cut_sessions)}, "
                 f"next line {bool(cut_sessions & done)}; longest inputs {longest}")
    time.sleep(0.5)
PY
}

read_expected_identity() {
  docker exec -i -e PYTHONDONTWRITEBYTECODE=1 "${TEST_CONTAINER_NAME}" python3 - <<'PY'
import configparser
import json
import sys
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/tpot-cowrie")
from tpot_personas import catalog  # noqa: E402

identity = json.loads(Path("/tmp/cowrie/identity.json").read_text(encoding="utf-8"))
persona = catalog.by_id(identity["persona"])
config = configparser.ConfigParser()
config.read("/tmp/cowrie/runtime/cowrie.cfg")
df = json.loads(Path(config.get("shell", "df_table")).read_text(encoding="utf-8"))
# the first row df shows: a file system with a size
first = [row for row in df["filesystems"] if row["size"] and row["source"] != "rootfs"][0]
print(json.dumps({
    "df_style": df["style"],
    "df_source": first["source"],
    "df_target": first["target"],
    "df_avail": first["avail"],
    "persona": persona.id,
    "kind": persona.kind,
    "os_id": persona.os_id,
    "os_release": Path("/tmp/cowrie/runtime/honeyfs/etc/os-release").is_file(),
    "hostname": identity["hostname"],
    "kernel_version": identity["release"]["kernel_version"],
    "cpus": identity["hardware"]["cpus"],
    "mem_kb": identity["hardware"]["mem_kb"],
}))
PY
}

assert_shell_matches_identity() {
  python3 - "${EXPECTED_IDENTITY_FILE}" "${SHELL_OUTPUT_FILE}" <<'PY'
import json
import sys
from pathlib import Path

expected = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
lines = Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
persona = expected["persona"]
problems = []


def line(index):
    return lines[index].strip() if index < len(lines) else "<missing>"


checks = (
    ("uname -r", line(0), expected["kernel_version"]),
    ("hostname", line(1), expected["hostname"]),
    ("nproc", line(2), str(expected["cpus"])),
    ("grep -c ^processor /proc/cpuinfo", line(3), str(expected["cpus"])),
    ("MemTotal of /proc/meminfo", line(4), str(expected["mem_kb"])),
)
for name, got, want in checks:
    if got != want:
        problems.append(f"{name}: {got!r}, identity.json says {want!r}")
# BusyBox puts a device name of more than 20 characters on a line of its own: line 2 has no $4
wrapped = expected["df_style"] == "busybox" and len(expected["df_source"]) > 20
row = line(6).split()
if row[:1] != [expected["df_source"]] or row[5:6] != [expected["df_target"]]:
    problems.append(f"df -h line 2: {line(6)!r}, the df table starts with {expected['df_source']} on "
                    f"{expected['df_target']}")
want = "[]" if wrapped else f"[{row[3] if len(row) > 3 else ''}]"
if line(5) != want:
    problems.append(f"df -h | awk 'FNR == 2 {{print $4}}': {line(5)!r}, df -h says {want!r}")
want = "[]" if wrapped else f"[{expected['df_avail']}]"
if line(7) != want:
    problems.append(f"df -k | awk 'FNR == 2 {{print $4}}': {line(7)!r}, the df table says {want!r}")

rest = lines[8:]
if "TPOT_PY" in rest:
    os_release, after = rest[:rest.index("TPOT_PY")], rest[rest.index("TPOT_PY") + 1:]
else:
    os_release, after = rest, []
# python3 -c 'print(1)' answers (a stand-in for code of any kind) and the line goes on
if "TPOT_AFTER" not in [entry.strip() for entry in after]:
    problems.append(f"the command line stopped at python3 -c, nothing came after it: {after!r}")
apt = after[[entry.strip() for entry in after].index("TPOT_APT") + 1:] if "TPOT_APT" in after else []
# EdgeOS: apt-get is the emulated apt 1.4 of the persona (upstream's would install any name)
if persona == "ubiquiti-edgerouter-x" and "E: Unable to locate package nosuchpkg" not in apt:
    problems.append(f"apt-get install nosuchpkg of EdgeOS is not the persona's apt: {apt!r}")
ids = [entry.split("=", 1)[1].strip().strip('"') for entry in os_release if entry.startswith("ID=")]
if expected["os_release"] or expected["kind"] != "device":
    if ids != [expected["os_id"]]:
        problems.append(f"/etc/os-release ID {ids}, the catalog says {expected['os_id']!r}")
elif ids:
    problems.append(f"/etc/os-release names ID {ids} but the persona has no os-release")

if problems:
    print(f"{persona}: the shell does not match identity.json:", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print("Shell output:\n" + "\n".join(lines), file=sys.stderr)
    sys.exit(1)

print(f"{persona}: kernel {expected['kernel_version']}, host {expected['hostname']}, "
      f"{expected['cpus']} CPUs, MemTotal {expected['mem_kb']} kB, os-release ID {ids[0] if ids else '-'}, "
      f"df {line(5)} avail on {expected['df_target']}")
PY
}

assert_host_keys() {
  local persona="$1"
  local keys_dir="${KEYS_DIR}/${persona}"
  local key=""
  local keys=""

  keys="$(
    docker exec "${TEST_CONTAINER_NAME}" sed -n 's/^[a-z0-9]*_private_key = //p' /tmp/cowrie/runtime/cowrie.cfg
  )" || return 1
  [[ "${keys}" == *"/ssh_host_rsa_key"* ]] || { printf 'cowrie.cfg names no RSA host key\n' >&2; return 1; }

  while IFS= read -r key; do
    [[ -n "${key}" ]] || continue
    [[ "${key}" == "/home/cowrie/cowrie/etc/${persona}/"* ]] || { printf 'Host key outside etc/%s: %s\n' "${persona}" "${key}" >&2; return 1; }
    if [[ -x "${keys_dir}" ]]; then
      [[ -s "${keys_dir}/${key##*/}" ]] || { printf 'Host key missing or empty: %s\n' "${keys_dir}/${key##*/}" >&2; return 1; }
    else
      # etc/<persona> is 0750 for uid 2000, another host user cannot look inside
      docker exec "${TEST_CONTAINER_NAME}" test -s "${key}" || { printf 'Host key missing or empty: %s\n' "${key}" >&2; return 1; }
    fi
    printf 'Host key: etc/%s/%s\n' "${persona}" "${key##*/}"
  done <<<"${keys}"
}

wait_for_json_event_containing() {
  local token="$1"
  local mode="$2"

  python3 - "${JSON_LOG_FILE}" "${token}" "${mode}" "${TEST_TIMEOUT}" <<'PY'
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
token = sys.argv[2]
mode = sys.argv[3]
timeout = int(sys.argv[4])
deadline = time.monotonic() + timeout
last_error = None


def contains_token(value):
    if isinstance(value, str):
        return token in value
    if isinstance(value, dict):
        return any(contains_token(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_token(item) for item in value)
    return False


def matches_mode(event):
    eventid = str(event.get("eventid", ""))
    if mode == "ssh":
        return contains_token(event) and (
            eventid.startswith("cowrie.client.")
            or eventid.startswith("cowrie.session.")
            or eventid.startswith("cowrie.login.")
        )
    if mode == "telnet":
        return contains_token(event) and (
            eventid.startswith("cowrie.login.")
            or eventid.startswith("cowrie.session.")
            or eventid.startswith("cowrie.client.")
        )
    if mode == "command":
        return eventid == "cowrie.command.input" and contains_token(event)
    raise RuntimeError(f"Unknown mode: {mode}")


def closed_session(events):
    """The closed event of the session that ran the token's command, with an integer duration_ms."""
    sessions = {event.get("session") for event in events
                if event.get("eventid") == "cowrie.command.input" and contains_token(event)}
    for event in events:
        duration = event.get("duration_ms")
        if (event.get("eventid") == "cowrie.session.closed" and event.get("session") in sessions
                and isinstance(duration, int) and not isinstance(duration, bool)):
            return event
    return None


while time.monotonic() < deadline:
    if not path.exists():
        last_error = f"{path} does not exist yet"
        time.sleep(1)
        continue

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        last_error = f"Could not read {path}: {exc}"
        time.sleep(1)
        continue

    events = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            last_error = f"Invalid JSON in {path}:{line_number}: {exc}"
            continue
        events.append(event)

        if mode != "closed" and matches_mode(event):
            print(f"JSON event found in {path}:{line_number}: {event.get('eventid', '<missing eventid>')}")
            sys.exit(0)

    if mode == "closed":
        event = closed_session(events)
        if event is not None:
            print(f"JSON event found in {path}: cowrie.session.closed of session {event.get('session')}, "
                  f"duration_ms {event['duration_ms']}")
            sys.exit(0)

    last_error = f"No {mode} JSON event found in {path} for token {token}"
    time.sleep(1)

if last_error:
    print(last_error, file=sys.stderr)
sys.exit(1)
PY
}

assert_custom_filesystem() {
  docker exec -i -e PYTHONDONTWRITEBYTECODE=1 "${TEST_CONTAINER_NAME}" python3 - "${PERSONA}" <<'PY'
import configparser
import json
import pickle
import re
import sys
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/tpot-cowrie")
from tpot_personas import catalog, tree, validate  # noqa: E402

requested = sys.argv[1].split()
root = Path("/home/cowrie/cowrie")
personas_root = root / "personas"
metadata_path = personas_root / "personas.json"
base_pickle_path = personas_root / "base.pickle"
source_commit_path = root / ".cowrie-source-commit"
selected_path = Path("/tmp/cowrie/persona")
identity_path = Path("/tmp/cowrie/identity.json")
runtime = Path("/tmp/cowrie/runtime")
runtime_config_path = runtime / "cowrie.cfg"
protocol_path = root / "src" / "cowrie" / "shell" / "protocol.py"
offenders = []
expected_source_commit = "c17c9b73d6af0972334ea1e90b20d974cb24eeca"
# docker/cowrie/tests/test_catalog.py PACKAGES + DEVICE_PACKAGES
expected_package_managers = {
    "ubuntu-noble": {"apt", "apt-get", "dpkg"},
    "ubuntu-jammy": {"apt", "apt-get", "dpkg"},
    "debian-trixie": {"apt", "apt-get", "dpkg"},
    "debian-bookworm": {"apt", "apt-get", "dpkg"},
    "rhel-9": {"dnf", "rpm", "yum"},
    "centos-7": {"rpm", "yum"},
    "raspios": {"apt", "apt-get", "dpkg"},
    "gpu-ubuntu": {"apt", "apt-get", "dpkg"},
    "dlink-dir859": set(),
    "tplink-wr841n": set(),
    "zyxel-nas326": set(),
    "openwrt": {"opkg"},
    "qnap-qts": {"qpkg_cli"},
    "synology-dsm": {"synopkg"},
    "ubiquiti-edgerouter-x": {"apt-get", "dpkg"},
    "ipcam": set(),
}
ubuntu_personas = {"ubuntu-noble", "ubuntu-jammy", "gpu-ubuntu"}
ubuntu_only_markers = (
    b"ubuntu 22.04",
    b"ubuntu 24.04",
    b"id=ubuntu",
    b"jammy jellyfish",
    b"noble numbat",
)
forbidden_markers = (
    b"phil",
    b"svr04",
    b"2.6.26-2-686",
    b"2.6.26-19lenny",
    b"com/ubuntu/upstart",
    b"dannf@debian.org",
    b"wed nov 4 20:45:37",
)
package_manager_paths = (
    "bin/apt",
    "bin/apt-get",
    "bin/dnf",
    "bin/opkg",
    "bin/rpm",
    "bin/yum",
    "sbin/opkg",
    "sbin/qpkg_cli",
    "usr/bin/apt",
    "usr/bin/apt-get",
    "usr/bin/dnf",
    "usr/bin/dpkg",
    "usr/bin/opkg",
    "usr/bin/rpm",
    "usr/bin/yum",
    "usr/sbin/opkg",
    "usr/sbin/qpkg_cli",
    "usr/syno/bin/synopkg",
)


def read_bytes(path):
    try:
        return path.read_bytes()
    except OSError as exc:
        fail(f"Could not read {path}: {exc}")


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def check_forbidden(path, os_id):
    markers = forbidden_markers + (ubuntu_only_markers if os_id != "ubuntu" else ())
    if any(marker in path.name.lower().encode("utf-8") for marker in markers):
        offenders.append(str(path))
    if path.is_file():
        # the runtime folder names itself in cowrie.cfg, it is no persona content
        data = read_bytes(path).lower().replace(str(runtime).encode("utf-8"), b"")
        if any(marker in data for marker in markers):
            offenders.append(str(path))


def check_forbidden_in_tree(fs, os_id):
    """The markers in what the shell shows of the tree (the path of every node, file contents, link
    targets), read the way the validator of the image reads it. The raw pickle is no text: its 4-byte
    ctimes spell words from some date on (b"phil" from July 2027), so the bytes are never scanned.
    The markers are this test's own table, not validate.FORBIDDEN_MARKERS."""
    markers = forbidden_markers + (ubuntu_only_markers if os_id != "ubuntu" else ())
    found = {}
    for path, data in validate._tree_texts(fs):
        data = data.lower()
        for marker in markers:
            if marker in data:
                found.setdefault(marker, path)
    for marker, path in sorted(found.items()):
        offenders.append(f"fs.pickle: {marker.decode()} in {path}")


def node_children(node):
    return node[7] if len(node) > 7 and isinstance(node[7], list) else []


def find_node(root_node, relative_path, depth=0):
    """The node of the path; links of its directories are followed as Cowrie does
    (bin -> usr/bin, targets from the root), the last part is not."""
    parts = [part for part in Path(relative_path).parts if part not in ("", "/")]
    current = root_node
    for index, part in enumerate(parts):
        current = next((child for child in node_children(current) if child[0] == part), None)
        if current is None:
            return None
        if index < len(parts) - 1 and current[1] == 0 and len(current) > 8 and current[8] and depth < 10:
            current = find_node(root_node, current[8], depth + 1)
            if current is None:
                return None
    return current


for path in (metadata_path, base_pickle_path, source_commit_path, selected_path, identity_path,
             runtime_config_path, runtime / "identity.json"):
    if not path.is_file():
        fail(f"Missing Cowrie persona file: {path}")
if source_commit_path.read_text(encoding="utf-8").strip() != expected_source_commit:
    fail("Cowrie source commit does not match expected upstream pin")
protocol_text = protocol_path.read_text(encoding="utf-8")
if "skip_python_commands" not in protocol_text:
    fail("Cowrie protocol.py does not contain persona command filtering patch")
if "operator_path = Path(txtcmds_path) / relpath" not in protocol_text:
    fail("Cowrie protocol.py does not contain upstream txtcmds_path support")

# personas.json: the metadata of every persona of the image
personas = json.loads(metadata_path.read_text(encoding="utf-8"))
if len(personas) != 16:
    fail(f"Expected 16 Cowrie personas, found {len(personas)}")
ids = {persona["id"]: persona for persona in personas}
if set(ids) != set(expected_package_managers):
    fail(f"personas.json IDs {sorted(ids)} are not {sorted(expected_package_managers)}")
if [persona["id"] for persona in personas] != catalog.ids():
    fail("personas.json does not list the personas of the catalog in its order")
for persona_id, persona in ids.items():
    if set(persona.get("package_managers", [])) != expected_package_managers[persona_id]:
        fail(f"{persona_id} package managers do not match: {sorted(persona.get('package_managers', []))}")
    if not persona.get("ssh_banners") or not all(b.startswith("SSH-2.0-") for b in persona["ssh_banners"]):
        fail(f"{persona_id} personas.json has no valid SSH banners: {persona.get('ssh_banners')}")
    for key in ("family", "kind", "user", "arch", "summary"):
        if not persona.get(key):
            fail(f"{persona_id} personas.json has no {key}")
ubuntu = {persona_id for persona_id in ids if catalog.by_id(persona_id).os_id == "ubuntu"}
if ubuntu != ubuntu_personas:
    fail(f"Personas with os_id ubuntu are {sorted(ubuntu)}, expected {sorted(ubuntu_personas)}")

# the persona of this start
selected = selected_path.read_text(encoding="utf-8").strip()
if selected not in ids:
    fail(f"Selected persona is not in personas.json: {selected}")
if requested and selected not in requested:
    fail(f"Selected persona {selected} is not one of the requested {' '.join(requested)}")
persona = ids[selected]
os_id = catalog.by_id(selected).os_id
identity = json.loads(identity_path.read_text(encoding="utf-8"))
if identity != json.loads((runtime / "identity.json").read_text(encoding="utf-8")):
    fail("/tmp/cowrie/identity.json differs from the one in the runtime folder")
if identity.get("persona") != selected:
    fail(f"identity.json is for {identity.get('persona')}, the selected persona is {selected}")

config_text = runtime_config_path.read_text(encoding="utf-8")
config = configparser.ConfigParser()
config.read_string(config_text)
expected_paths = (
    ("shell", "filesystem", runtime / "fs.pickle"),
    ("shell", "processes", runtime / "cmdoutput.json"),
    ("honeypot", "contents_path", runtime / "honeyfs"),
    ("honeypot", "txtcmds_path", runtime / "txtcmds"),
    ("honeypot", "etc_path", root / "etc" / selected),
)
for section, key, expected in expected_paths:
    if config.get(section, key, fallback="") != str(expected):
        fail(f"Runtime config {section}.{key} is not {expected}")
if config.get("ssh", "version", fallback="") not in persona["ssh_banners"]:
    fail(f"Runtime config SSH banner {config.get('ssh', 'version', fallback='')!r} is not one of {selected}")
if config.get("honeypot", "hostname", fallback="") != identity["hostname"]:
    fail("Runtime config hostname does not match identity.json")
if config.get("honeypot", "boot_offset", fallback="") != str(identity["boot_offset"]):
    fail("Runtime config boot_offset does not match identity.json")
if "ssh-dss" in config_text.lower() or re.search(r"\bdsa", config_text.lower()):
    fail("Runtime config names DSA host keys")
# the live top of the patched Cowrie: its style, and the host key algorithms of the banner's server
if config.get("shell", "top_style", fallback="") not in ("procps", "busybox"):
    fail(f"Runtime config has no top_style for the live top of {selected}")
if not config.get("ssh", "host_key_algorithms", fallback=""):
    fail(f"Runtime config has no host_key_algorithms for {selected}")
# Cowrie's apt answers on the Debian servers only (dpkg), everywhere else it is skipped
skipped = {entry.strip() for entry in config.get("shell", "skip_python_commands", fallback="").split(",")}
selected_persona = catalog.by_id(selected)
apt_answers = selected_persona.kind == "server" and "dpkg" in selected_persona.package_managers
if ("apt" in skipped) == apt_answers:
    fail(f"Runtime config {'skips' if apt_answers else 'does not skip'} Cowrie's apt on {selected}")

pickle_path = runtime / "fs.pickle"
honeyfs = runtime / "honeyfs"
cmdoutput_path = runtime / "cmdoutput.json"
txtcmds_path = runtime / "txtcmds"
for path in (pickle_path, cmdoutput_path):
    if not path.is_file():
        fail(f"Missing runtime file: {path}")
for path in (honeyfs, txtcmds_path):
    if not path.is_dir():
        fail(f"Missing runtime folder: {path}")
# the floor of tpot_personas.validate.MIN_PICKLE_SIZE: a device tree is about 1 MB
if pickle_path.stat().st_size < 800000:
    fail(f"{selected} fs.pickle is unexpectedly small: {pickle_path.stat().st_size} bytes")

pickle_bytes = read_bytes(pickle_path)
with pickle_path.open("rb") as handle:
    pickle_tree = pickle.load(handle)
if persona["user"].encode("utf-8") not in pickle_bytes:
    fail(f"{selected} fs.pickle does not contain persona user {persona['user']}")
if identity["hostname"].encode("utf-8") not in pickle_bytes:
    fail(f"{selected} fs.pickle does not contain the host name {identity['hostname']}")
cmdoutput = json.loads(cmdoutput_path.read_text(encoding="utf-8"))
if not cmdoutput.get("command", {}).get("ps"):
    fail(f"{selected} cmdoutput.json has no ps process list")
package_managers = expected_package_managers[selected]
for command_path in (
    "bin/dmesg",
    "bin/mount",
    "bin/ulimit",
    "usr/bin/lscpu",
    "usr/bin/nproc",
):
    if not (txtcmds_path / command_path).is_file():
        fail(f"{selected} txtcmds is missing {command_path}")
# df and awk are commands of the patched Cowrie over the persona's file system table
if (txtcmds_path / "bin" / "df").exists():
    fail(f"{selected} txtcmds still has bin/df")
df_table = Path(config.get("shell", "df_table", fallback=""))
if not df_table.is_file() or not json.loads(df_table.read_text(encoding="utf-8")).get("filesystems"):
    fail(f"{selected} has no df table: {df_table}")
if config.get("shell", "awk_style", fallback="") not in ("gawk", "mawk", "busybox"):
    fail(f"{selected} has no awk style")
# wc, sort and seq, ip, ss, netstat and ifconfig are commands of the patched Cowrie over the persona's data
if config.get("shell", "text_style", fallback="") not in ("gnu", "busybox"):
    fail(f"{selected} has no style of wc, sort and seq")
network_path = Path(config.get("shell", "network", fallback=""))
if not network_path.is_file() or not json.loads(network_path.read_text(encoding="utf-8")).get("interfaces"):
    fail(f"{selected} has no network: {network_path}")
# apt, yum 3 and opkg know the package names of the release; opkg is a command of the patched Cowrie
lists_names = (persona["kind"] == "server" and "dnf" not in package_managers
               and bool(package_managers & {"apt-get", "yum"})) or "opkg" in package_managers
if lists_names:
    names_path = Path(config.get("shell", "package_names", fallback=""))
    if not names_path.is_file() or len(names_path.read_text(encoding="utf-8").splitlines()) < 1000:
        fail(f"{selected} has no package names: {names_path}")
if "opkg" in package_managers and (txtcmds_path / "bin" / "opkg").exists():
    fail(f"{selected} txtcmds still has bin/opkg")
for command_path in package_manager_paths:
    should_exist = Path(command_path).name in package_managers
    exists_in_pickle = find_node(pickle_tree, command_path) is not None
    exists_in_txtcmds = (txtcmds_path / command_path).is_file()
    if should_exist and exists_in_txtcmds and not exists_in_pickle:
        fail(f"{selected} package manager is missing from fs.pickle: {command_path}")
    if not should_exist and (exists_in_pickle or exists_in_txtcmds):
        fail(f"{selected} has mismatched package manager command: {command_path}")

passwd = read_bytes(honeyfs / "etc" / "passwd")
hostname = read_bytes(honeyfs / "etc" / "hostname")
if persona["user"].encode("utf-8") not in passwd:
    fail(f"{selected} honeyfs /etc/passwd does not contain persona user")
if hostname.strip() != identity["hostname"].encode("utf-8"):
    fail(f"{selected} honeyfs /etc/hostname is not {identity['hostname']}")
os_release_path = honeyfs / "etc" / "os-release"
if os_release_path.is_file() or persona["kind"] != "device":
    os_release = read_bytes(os_release_path).lower()
    if not re.search(rb'^id="?' + re.escape(os_id.encode("utf-8")) + rb'"?$', os_release, re.M):
        fail(f"{selected} honeyfs /etc/os-release has no ID={os_id}")
if os_id == "ubuntu":
    if b"id=ubuntu" not in read_bytes(os_release_path).lower():
        fail(f"{selected} honeyfs /etc/os-release does not say ID=ubuntu")

check_forbidden_in_tree(pickle_tree, os_id)
check_forbidden(runtime_config_path, os_id)
check_forbidden(cmdoutput_path, os_id)
for item in honeyfs.rglob("*"):
    check_forbidden(item, os_id)
for item in txtcmds_path.rglob("*"):
    check_forbidden(item, os_id)

if offenders:
    fail("Cowrie persona files contain forbidden markers: " + ", ".join(offenders))

print(f"Cowrie persona validated: {selected} ({identity['hostname']}) of {len(personas)} personas, "
      f"no forbidden markers")
PY
}

host_key_sums() {
  local persona="$1"

  docker exec "${TEST_CONTAINER_NAME}" sh -c "sha256sum /home/cowrie/cowrie/etc/${persona}/ssh_host_* | sort"
}

# COWRIE_PERSONA_ROTATION reaches the container: a second start in the same keys folder keeps persona,
# host name and host keys
assert_rotation_keeps_identity() {
  local persona="$1"
  local before_sums="" after_sums="" after_file="${TEST_TMP_ROOT}/identity-after.json"
  local deadline=$((SECONDS + TEST_TIMEOUT))

  before_sums="$(host_key_sums "${persona}")" || return 1
  [[ -n "${before_sums}" ]] || { printf 'No host keys of %s to compare\n' "${persona}" >&2; return 1; }

  test_compose stop >/dev/null || return 1
  COWRIE_PERSONA_ROTATION="${ROTATION_DAYS}" test_compose up -d --no-build >/dev/null || return 1
  test_wait_for_container || return 1

  until test_compose logs --no-color 2>/dev/null | grep -F -q "Keeping Cowrie persona ${persona} drawn"; do
    (( SECONDS < deadline )) || { printf 'No "Keeping Cowrie persona %s" line with COWRIE_PERSONA_ROTATION=%s\n' "${persona}" "${ROTATION_DAYS}" >&2; return 1; }
    sleep 1
  done
  until [[ -s "${TEST_TMP_ROOT}/log/cowrie.json" ]] && docker exec "${TEST_CONTAINER_NAME}" test -s /tmp/cowrie/identity.json 2>/dev/null; do
    (( SECONDS < deadline )) || { printf 'The second start did not come up\n' >&2; return 1; }
    sleep 1
  done

  read_expected_identity > "${after_file}" || return 1
  python3 - "${EXPECTED_IDENTITY_FILE}" "${after_file}" <<'PY' || return 1
import json
import sys
from pathlib import Path

first, second = (json.loads(Path(arg).read_text(encoding="utf-8")) for arg in sys.argv[1:3])
for key in ("persona", "hostname", "kernel_version", "cpus", "mem_kb"):
    if first[key] != second[key]:
        print(f"COWRIE_PERSONA_ROTATION: {key} is {second[key]!r} after the second start, was {first[key]!r}", file=sys.stderr)
        sys.exit(1)
PY
  after_sums="$(host_key_sums "${persona}")" || return 1
  [[ "${before_sums}" == "${after_sums}" ]] || { printf 'The host keys of %s changed with COWRIE_PERSONA_ROTATION=%s\n' "${persona}" "${ROTATION_DAYS}" >&2; return 1; }
}

assert_no_runtime_errors() {
  if grep -R -E "Traceback|NameError|Unhandled Error|Exception" "${LOG_DIR}" >/dev/null 2>&1; then
    test_die "Cowrie runtime error found in log files"
  fi

  if test_compose logs --no-color 2>/dev/null | grep -E "Traceback|NameError|Unhandled Error|Exception" >/dev/null 2>&1; then
    test_die "Cowrie runtime error found in Docker logs"
  fi
}


read_image_persona_ids() {
  docker run --rm --network none --read-only --user 2000:2000 --entrypoint python3 "${IMAGE}" -c '
import json
for persona in json.load(open("/home/cowrie/cowrie/personas/personas.json", encoding="utf-8")):
    print(persona["id"])
'
}

run_smoke_test() {
  prepare_cowrie_harness
  test_enable_cleanup

  test_info "Starting isolated Cowrie container${PERSONA:+ (COWRIE_PERSONA=${PERSONA})}"
  test_compose up -d --no-build >/dev/null

  test_wait_for_container || test_die "Cowrie container did not stay running"
  test_ok "Container is running"

  MAPPED_SSH_PORT="$(test_get_mapped_port "${TEST_NAME}" "22")" || test_die "Could not resolve mapped host port for 22/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_SSH_PORT} maps to container port 22/tcp"

  MAPPED_TELNET_PORT="$(test_get_mapped_port "${TEST_NAME}" "23")" || test_die "Could not resolve mapped host port for 23/tcp"
  test_ok "Port ${TEST_BIND_IP}:${MAPPED_TELNET_PORT} maps to container port 23/tcp"

  test_info "Waiting for cowrie.json"
  wait_for_json_log || test_die "cowrie.json was not created"
  test_ok "cowrie.json exists"

  test_info "Validating the Cowrie persona"
  assert_custom_filesystem || test_die "Cowrie persona validation failed"
  read_expected_identity > "${EXPECTED_IDENTITY_FILE}" || test_die "Could not read identity.json of the container"
  local persona=""
  persona="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["persona"])' "${EXPECTED_IDENTITY_FILE}")"
  test_ok "Cowrie persona ${persona} is valid"

  local ssh_token=""
  ssh_token="ssh-test-$(date +%s)-$$"
  test_info "Running SSH banner probe with token: ${ssh_token}"
  run_ssh_banner_probe_with_retries "${ssh_token}" || test_die "SSH probe failed on ${TEST_BIND_IP}:${MAPPED_SSH_PORT}"
  test_wait_for_container || test_die "Cowrie container stopped after SSH probe"

  test_info "Waiting for SSH probe event in cowrie.json"
  wait_for_json_event_containing "${ssh_token}" "ssh" || test_die "SSH probe token was not found in cowrie.json"
  test_ok "SSH probe was written to cowrie.json"

  assert_host_keys "${persona}" || test_die "SSH host keys of ${persona} are missing in the keys volume"
  test_ok "SSH host keys of ${persona} are in the keys volume"

  test_info "Running an SSH session up to the authentication"
  run_ssh_userauth_probe || test_die "SSH session did not reach the authentication on ${persona}"
  test_wait_for_container || test_die "Cowrie container stopped after the SSH session"

  local refused_pairs=""
  refused_pairs="$(read_refused_pairs)" || test_die "Could not read auth_refused_pairs of ${persona}"
  test_info "Trying the pairs of auth_refused_pairs over SSH and Telnet"
  run_refused_login_probe "${refused_pairs}" || test_die "A pair of auth_refused_pairs logged in on ${persona}"
  wait_for_refused_login_events "${refused_pairs}" || test_die "The refused logins of ${persona} are not in cowrie.json"
  assert_refused_pairs_left_no_state "${refused_pairs}" || test_die "The refused logins changed AuthRandom's state on ${persona}"
  test_wait_for_container || test_die "Cowrie container stopped after the refused logins"
  test_ok "The pairs of auth_refused_pairs never logged in"

  local telnet_token=""
  telnet_token="telnet-test-$(date +%s)-$$"
  test_info "Running Telnet login probe with token: ${telnet_token}"
  run_telnet_login_probe_with_retries "${telnet_token}" || test_die "Telnet probe failed on ${TEST_BIND_IP}:${MAPPED_TELNET_PORT}"
  test_wait_for_container || test_die "Cowrie container stopped after Telnet probe"

  test_info "Waiting for Telnet probe event in cowrie.json"
  wait_for_json_event_containing "${telnet_token}" "telnet" || test_die "Telnet probe token was not found in cowrie.json"
  test_ok "Telnet probe was written to cowrie.json"

  test_info "Logging in over Telnet and comparing the shell with identity.json"
  run_telnet_shell_probe "${telnet_token}" > "${SHELL_OUTPUT_FILE}" || test_die "Telnet shell probe failed on ${TEST_BIND_IP}:${MAPPED_TELNET_PORT}"
  assert_shell_matches_identity || test_die "The shell of ${persona} does not match its identity"
  test_ok "Shell of ${persona} matches identity.json"

  test_info "Waiting for the shell commands in cowrie.json"
  wait_for_json_event_containing "TPOT_BEGIN" "command" || test_die "The shell command was not found in cowrie.json"
  wait_for_json_event_containing "TPOT_BEGIN" "closed" || test_die "No cowrie.session.closed event with an integer duration_ms for the shell session"
  test_ok "Shell commands and the closed session were written to cowrie.json"

  test_info "Typing and pasting a line of ${LONG_LINE_LENGTH} characters with a download over Telnet and SSH"
  local long_line_results=""
  long_line_results="$(run_long_line_probe)" || test_die "The shell of ${persona} did not take the long line"
  test_info "Long line: ${long_line_results}"
  wait_for_long_line_events || test_die "The long lines of ${persona} are not in cowrie.json"
  test_wait_for_container || test_die "Cowrie container stopped after the long lines"
  test_ok "The long line came back within ${LONG_LINE_SECONDS} s, cowrie.command.input has it with its wget"

  if [[ "${ROTATION_STEP}" == "true" ]]; then
    test_info "Starting ${persona} again with COWRIE_PERSONA_ROTATION=${ROTATION_DAYS}"
    assert_rotation_keeps_identity "${persona}" || test_die "A second start with COWRIE_PERSONA_ROTATION=${ROTATION_DAYS} did not keep ${persona}"
    test_ok "Second start kept persona, host name and host keys of ${persona}"
  fi

  assert_no_runtime_errors
  test_ok "No Cowrie runtime errors found in logs"
}

run_all_personas() {
  local ids=()
  local id=""
  local rc=0

  while IFS= read -r id; do
    [[ -n "${id}" ]] && ids+=("${id}")
  done < <(read_image_persona_ids) || true
  (( ${#ids[@]} > 0 )) || test_die "Could not read the persona IDs of ${IMAGE}"
  test_info "Testing ${#ids[@]} personas: ${ids[*]}"

  local first="true"
  for id in "${ids[@]}"; do
    test_info "Persona ${id}"
    PERSONA="${id}"
    # the second start costs a container start: once, for the first persona
    ROTATION_STEP="${first}"
    first="false"
    # one harness per persona: the subshell's EXIT trap takes it down
    set +e
    ( set -e; run_smoke_test )
    rc=$?
    set -e
    (( rc != 130 )) || exit 130
    (( rc == 0 )) || test_die "${id}: Cowrie persona smoke test failed"
    test_ok "${id}: Cowrie persona smoke test passed"
  done
}

main() {
  parse_args "$@"
  validate_args
  test_check_dependencies

  if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(test_read_compose_image "${TEST_NAME}" "${DEFAULT_IMAGE}")"
  fi

  test_info "Using image: ${IMAGE}"
  test_require_image "${IMAGE}" "docker compose -f docker/${TEST_NAME}/docker-compose.yml build ${TEST_NAME}"

  if [[ -n "${SSH_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${SSH_PORT}" || test_die "${TEST_BIND_IP}:${SSH_PORT} is already in use. Try --ssh-port <free-port>."
  fi
  if [[ -n "${TELNET_PORT}" ]]; then
    test_ensure_port_free "${TEST_BIND_IP}" "${TELNET_PORT}" || test_die "${TEST_BIND_IP}:${TELNET_PORT} is already in use. Try --telnet-port <free-port>."
  fi

  if [[ "${ALL_PERSONAS}" == "true" ]]; then
    run_all_personas
  else
    run_smoke_test
  fi

  test_ok "Cowrie post-build smoke test completed successfully"
}

main "$@"
