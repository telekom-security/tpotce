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
COMMAND = (
    f"echo {BEGIN}; uname -r; hostname; nproc; grep -c ^processor /proc/cpuinfo; "
    f"awk '/MemTotal/ {{print $2}}' /proc/meminfo; cat /etc/os-release; "
    # the interpreter answers and the line goes on (a harmless stand-in, no attacker code)
    f"echo {PY_MARK}; python3 -c 'print(1)'; echo {AFTER_MARK}; echo {END}"
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

read_expected_identity() {
  docker exec -i -e PYTHONDONTWRITEBYTECODE=1 "${TEST_CONTAINER_NAME}" python3 - <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/tpot-cowrie")
from tpot_personas import catalog  # noqa: E402

identity = json.loads(Path("/tmp/cowrie/identity.json").read_text(encoding="utf-8"))
persona = catalog.by_id(identity["persona"])
print(json.dumps({
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

rest = lines[5:]
if "TPOT_PY" in rest:
    os_release, after = rest[:rest.index("TPOT_PY")], rest[rest.index("TPOT_PY") + 1:]
else:
    os_release, after = rest, []
# python3 -c 'print(1)' answers (a stand-in for code of any kind) and the line goes on
if "TPOT_AFTER" not in [entry.strip() for entry in after]:
    problems.append(f"the command line stopped at python3 -c, nothing came after it: {after!r}")
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
      f"{expected['cpus']} CPUs, MemTotal {expected['mem_kb']} kB, os-release ID {ids[0] if ids else '-'}")
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
    "bin/df",
    "bin/dmesg",
    "bin/mount",
    "bin/ulimit",
    "usr/bin/lscpu",
    "usr/bin/nproc",
):
    if not (txtcmds_path / command_path).is_file():
        fail(f"{selected} txtcmds is missing {command_path}")
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
