#!/usr/bin/env bash

set -Eeuo pipefail

unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="fluentbit"
FLUENTBIT_DIR="${DOCKER_ROOT}/fluentbit"
DEFAULT_IMAGE="ghcr.io/telekom-security/fluentbit:24.04.2"
DEFAULT_INIT_IMAGE="ghcr.io/telekom-security/tpotinit:24.04.2"
IMAGE=""
INIT_IMAGE=""
# case 13: under a soft limit of 1024 (what read_nofile sets where compose/sensor.yml has none, the default
# of a Debian docker) 120 s of load lost no line, 240 s only now and then; the files of Fluent Bit reach
# the limit after about 240 s, so 360 s
OUTAGE_SECONDS=360
CASES="1,2,3,4,5,6,7,8,9,10,11,12,13"
# what tpotinit writes into elk_environment of a SENSOR, and the access of the sensor at the HIVE
SMOKE_HOSTNAME="sensor-smoke"
SMOKE_EXTIP="198.51.100.7"
SMOKE_INTIP="10.0.0.7"
SMOKE_VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/version")"
SMOKE_AUTH="$(printf '%s' 'sensor-smoke:not-a-secret' | base64)"
CERTS=""
RUN_DIR=""
RUN_START=""
HEALTH_INTERVAL=""
# the file limit of Fluent Bit: that of compose/sensor.yml, else what a Debian docker gives a container
NOFILE_SOFT=""
NOFILE_HARD=""
NOFILE_SOURCE=""

usage() {
  cat <<EOF
Usage: $0 [options]

Runs the Fluent Bit image of a SENSOR against a stub of the HIVE's ingest endpoint
(docker/_tests/lib/hive_stub.py: HTTPS with a test CA, every request recorded,
answers 200 or what its status file says; its capability probe /tpot-ingest 204
with X-TPot-Ingest 2 or what its probe file says). The stub runs in the tpotinit image
without a network, Fluent Bit shares its network namespace (127.0.0.1, and
hive.test through the hosts file of the stub). Cases:

  1  Every fixture of tpotctl/checks/fixtures arrives once with the raw line in
     log, its type, the file it came from in t-pot_log_path, the t-pot_* fields
     and an ISO @timestamp, gzipped, with Basic auth and X-TPot-Ingest 2; the
     path of a honeypot's own JSON stays in log.
  2  The first heartbeat within 10 s, the second about 60 s later: sequence 1
     and 2 as JSON integers, started_at and version.
  3  A line of 2 MB is skipped with a log line, the next line arrives.
  4  An invalid byte arrives as U+FFFD, the rest of its batch arrives too.
  5  The HIVE answers 503 for 30 s and Fluent Bit restarts meanwhile: every
     line arrives, none twice.
  6  A file made after the start (in a glob and in a new folder) arrives from
     its head.
  7  FLB_STORAGE_LIMIT=1M, 503 and 5 MB of lines in 10 batches: chunks are
     dropped with a log line, Fluent Bit keeps running, the newest lines
     arrive after the 503.
  8  TLS: the HIVE as 127.0.0.1 and as hive.test is verified and delivers, a
     certificate without the address delivers nothing and the log names it.
  9  uid 2000, a read-only root, healthy, the plugins tail, dummy, lua and
     http, and the entrypoint refuses an empty TPOT_HIVE_IP / TPOT_HIVE_USER
     or a wrong LS_SSL_VERIFICATION or FLB_PROBE_INTERVAL.
 10  The HIVE answers 401 on /tpot-ingest (an old HIVE): no POST, the message
     names it, the container stays healthy for 3 checks in a row.
 11  Then 503 without the marker (its Logstash not ready): no POST, the message
     names it.
 12  Then 204 with X-TPot-Ingest 2 (FLB_PROBE_INTERVAL=5): every line written
     before arrives within 15 s, once each.
 13  The HIVE stops answering (its stub paused) while 500 lines/s go into the
     log of every input for 360 s; 30 s after the load it answers again: every
     line arrives (duplicates allowed), the log has no "Too many open files".

Fluent Bit runs with the file limit (ulimits nofile) of compose/sensor.yml; where
it has none, with soft 1024 / hard 524288, what a Debian docker gives a container
(Docker Desktop gives 1048576 and would hide a leak of files).

All cases take about 22 minutes (heartbeat interval, 503 and retries; case 13
alone about 17: 6 min of load, then up to 15 min until the retries delivered).

Options:
  --image IMAGE       Fluent Bit image to test. Defaults to docker/fluentbit/docker-compose.yml.
  --init-image IMAGE  Image the stub runs in (python3 with ssl, openssl). Defaults to
                      docker/tpotinit/docker-compose.yml.
  --cases LIST        Only these cases (comma separated numbers 1-13). Default: all.
  --timeout SEC       Time to wait for a delivery (at least 120 s are used; case 13
                      waits at least 900 s for the lines of its outage).
  --bind-ip IP        Accepted for runner compatibility; nothing is published.
  --keep-artifacts    Keep the temporary files for debugging.
  -h, --help          Show this help message.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --image) [[ $# -ge 2 ]] || test_die "--image requires an argument"; IMAGE="$2"; shift 2 ;;
      --image=*) IMAGE="${1#*=}"; shift ;;
      --init-image) [[ $# -ge 2 ]] || test_die "--init-image requires an argument"; INIT_IMAGE="$2"; shift 2 ;;
      --init-image=*) INIT_IMAGE="${1#*=}"; shift ;;
      --cases) [[ $# -ge 2 ]] || test_die "--cases requires an argument"; CASES="$2"; shift 2 ;;
      --cases=*) CASES="${1#*=}"; shift ;;
      --timeout) [[ $# -ge 2 ]] || test_die "--timeout requires an argument"; TEST_TIMEOUT="$2"; shift 2 ;;
      --timeout=*) TEST_TIMEOUT="${1#*=}"; shift ;;
      --bind-ip) [[ $# -ge 2 ]] || test_die "--bind-ip requires an argument"; TEST_BIND_IP="$2"; shift 2 ;;
      --bind-ip=*) TEST_BIND_IP="${1#*=}"; shift ;;
      --keep-artifacts) TEST_KEEP_ARTIFACTS="true"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) test_die "Unknown option: $1" ;;
    esac
  done
  local case
  [[ "${CASES}" =~ ^[0-9]+(,[0-9]+)*$ ]] || test_die "--cases takes numbers 1-13, comma separated: ${CASES}"
  for case in ${CASES//,/ }; do
    (( 10#${case} >= 1 && 10#${case} <= 13 )) || test_die "--cases takes numbers 1-13, comma separated: ${CASES}"
  done
}

wanted() {
  [[ ",${CASES}," == *",$1,"* ]]
}

# The files of the test belong to the user of the host again (Fluent Bit writes as uid 2000)
test_release_artifacts() {
  [[ -n "${TEST_TMP_ROOT}" && -d "${TEST_TMP_ROOT}" ]] || return 0
  docker run --rm --network none --user 0:0 --entrypoint chown \
    --mount "type=bind,src=${TEST_TMP_ROOT},dst=/work" "${INIT_IMAGE}" -R -h "$(id -u):$(id -g)" /work
}

checker() {
  SMOKE_HOSTNAME="${SMOKE_HOSTNAME}" SMOKE_EXTIP="${SMOKE_EXTIP}" SMOKE_INTIP="${SMOKE_INTIP}" \
    SMOKE_VERSION="${SMOKE_VERSION}" SMOKE_AUTH="${SMOKE_AUTH}" SMOKE_TIMEOUT="${TEST_TIMEOUT}" \
    PYTHONPATH="${REPO_ROOT}" python3 "${TEST_TMP_ROOT}/check.py" "$@"
}

write_checker() {
  cat > "${TEST_TMP_ROOT}/check.py" <<'PY'
"""The checks of the fluentbit smoke test on the records of hive_stub.py (stub/requests.jsonl)."""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
FIELDS = {"@timestamp", "log", "t-pot_log_path", "type", "t-pot_hostname", "t-pot_ip_ext", "t-pot_ip_int"}
STAMP = {"t-pot_hostname": os.environ["SMOKE_HOSTNAME"], "t-pot_ip_ext": os.environ["SMOKE_EXTIP"],
         "t-pot_ip_int": os.environ["SMOKE_INTIP"]}
TIMEOUT = max(int(os.environ["SMOKE_TIMEOUT"]), 120)


def fail(text):
    print("[FAIL] " + text, file=sys.stderr)
    sys.exit(1)


def requests(run):
    found = []
    try:
        lines = (Path(run) / "stub" / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return found
    for line in lines:
        try:
            found.append(json.loads(line))
        except ValueError:
            pass  # a line the stub is still writing
    return found


def delivered(run):
    """(request, event) of every event the stub answered with 200."""
    return [(request, event) for request in requests(run) if request["status"] == 200
            for event in json.loads(request["body"])]


def wait(run, find, what, timeout=None):
    timeout = timeout or TIMEOUT
    deadline = time.monotonic() + timeout
    while True:
        result = find(delivered(run))
        if result:
            return result
        if time.monotonic() > deadline:
            fail(f"{what} after {timeout} s")
        time.sleep(0.5)


def check_request(request, what):
    want = {"method": "POST", "path": "/", "gzip": True}
    got = {key: request[key] for key in want}
    if got != want:
        fail(f"{what}: request {got}, expected {want}")
    headers = request["headers"]
    if headers.get("authorization") != "Basic " + os.environ["SMOKE_AUTH"]:
        fail(f"{what}: Authorization {headers.get('authorization')!r}")
    if headers.get("x-tpot-ingest") != "2":
        fail(f"{what}: X-TPot-Ingest {headers.get('x-tpot-ingest')!r}")


def token_of(event):
    try:
        return json.loads(event.get("log", "")).get("token")
    except (ValueError, AttributeError):
        return None


def prepare_fixtures(data, manifest_path):
    from tpotctl.checks import fixtures
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    manifest = {}
    for number, name in enumerate(fixtures.names()):
        rel = fixtures.log_file(name)
        path = Path(data) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        token = "fluentbit" + re.sub("[^a-z0-9]", "", name.lower())
        line = fixtures.render(name, ts, "8.8.8.8", 40000 + number, 8080 + number, token)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        manifest[name] = {"line": line, "type": fixtures.logstash_type(name), "path": "/data/" + rel, "token": token}
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1)
    print(f"{len(manifest)} fixtures written")


def folders(data, *keep_out):
    """The folders of every input path, as tpotinit makes them on a T-Pot (a missing folder of a glob
    makes Fluent Bit log a read error); the folders of keep_out stay missing."""
    from tpotctl import logs_config
    for entry in logs_config.INPUTS:
        for path in entry.paths:
            folder = str(Path(path).parent)
            if folder not in keep_out:
                (Path(data) / Path(folder).relative_to("/data")).mkdir(parents=True, exist_ok=True)


def case_fixtures(run, manifest_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    lines = {want["line"] for want in manifest.values()}

    def all_there(events):
        seen = {event.get("log") for _, event in events}
        return events if lines <= seen else None

    events = wait(run, all_there, f"not every one of the {len(lines)} fixtures arrived")
    with_path = 0
    for name, want in sorted(manifest.items()):
        found = [(request, event) for request, event in events if event.get("log") == want["line"]]
        if len(found) != 1:
            fail(f"{name}: {len(found)} events with its line, expected 1")
        request, event = found[0]
        check_request(request, name)
        if set(event) != FIELDS:
            fail(f"{name}: fields {sorted(event)}, expected {sorted(FIELDS)}")
        expect = {"type": want["type"], "t-pot_log_path": want["path"], **STAMP}
        got = {key: event[key] for key in expect}
        if got != expect:
            fail(f"{name}: {got}, expected {expect}")
        if not ISO.match(event["@timestamp"]):
            fail(f"{name}: @timestamp {event['@timestamp']!r} is no ISO time")
        try:
            payload = json.loads(event["log"])
        except ValueError:
            continue
        if isinstance(payload, dict) and "path" in payload:
            if payload["path"] != "/" + want["token"]:
                fail(f"{name}: the path of its JSON is {payload['path']!r}, expected /{want['token']}")
            with_path += 1
    if not with_path:
        fail("no fixture with a path of its own in its JSON")
    print(f"{len(manifest)} fixtures arrived once each, {with_path} with their own path untouched")


def case_heartbeats(run, start, count):
    start = float(start)
    count = int(count)

    def beats(events):
        found = [(request, event) for request, event in events if event.get("type") == "TPotSensorHeartbeat"]
        return found if len(found) >= count else None

    found = wait(run, beats, f"fewer than {count} heartbeats", timeout=10 if count == 1 else 80)
    if count == 1:
        print(f"first heartbeat {found[0][0]['time'] - start:.1f} s after the start")
    for number, (request, event) in enumerate(found[:count], 1):
        check_request(request, f"heartbeat {number}")
        beat = event.get("heartbeat") or {}
        sequence = beat.get("sequence")
        if type(sequence) is not int or sequence != number:
            fail(f"heartbeat {number}: sequence {sequence!r} ({type(sequence).__name__}), expected the integer {number}")
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", str(beat.get("started_at"))):
            fail(f"heartbeat {number}: started_at {beat.get('started_at')!r}")
        if beat.get("version") != os.environ["SMOKE_VERSION"]:
            fail(f"heartbeat {number}: version {beat.get('version')!r}, expected {os.environ['SMOKE_VERSION']}")
        expect = {"type": "TPotSensorHeartbeat", **STAMP}
        if {key: event.get(key) for key in expect} != expect or not ISO.match(event.get("@timestamp", "")):
            fail(f"heartbeat {number}: {event}")
    if count == 2:
        first, second = (request["time"] for request, _ in found[:2])
        if not 50 <= second - first <= 75:
            fail(f"the second heartbeat came {second - first:.1f} s after the first, expected about 60 s")
        if found[0][1]["heartbeat"]["started_at"] != found[1][1]["heartbeat"]["started_at"]:
            fail("started_at changed between two heartbeats of one start")
        print(f"second heartbeat {second - first:.1f} s after the first, sequence 1 and 2 as integers")


def append(path, kind, prefix, count="1"):
    """Writes lines of a kind into a log file, in one write (one batch)."""
    count = int(count)
    if kind == "lines":
        data = "".join(json.dumps({"token": f"{prefix}-{n:05d}", "pad": "x" * 64}) + "\n"
                       for n in range(count)).encode()
    elif kind == "long":
        data = (json.dumps({"token": f"{prefix}-long", "pad": "y" * (2 * 1024 * 1024)}) + "\n"
                + json.dumps({"token": f"{prefix}-after"}) + "\n").encode()
    elif kind == "invalid":
        data = (b'{"token": "' + prefix.encode() + b'-bad", "v": "a\xffb"}\n'
                + b"".join(json.dumps({"token": f"{prefix}-{n:05d}"}).encode() + b"\n" for n in range(3)))
    elif kind == "bulk":
        data = "".join(json.dumps({"token": f"{prefix}-{n:05d}", "pad": "z" * 1000}) + "\n"
                       for n in range(count)).encode()
    else:
        fail(f"unknown kind {kind}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as handle:
        handle.write(data)
    os.chmod(path, 0o644)


def expect_tokens(run, once, settle, *tokens, timeout=None):
    timeout = timeout or TIMEOUT
    wanted = set(tokens)

    def there(events):
        seen = {token_of(event) for _, event in events}
        return events if wanted <= seen else None

    def missing():
        return sorted(wanted - {token_of(event) for _, event in delivered(run)})

    deadline = time.monotonic() + timeout
    while not there(delivered(run)):
        if time.monotonic() > deadline:
            fail(f"{len(missing())} lines missing after {timeout} s, e.g. {missing()[:5]}")
        time.sleep(0.5)
    if once == "once":
        time.sleep(float(settle))
        counts = {}
        for _, event in delivered(run):
            counts[token_of(event)] = counts.get(token_of(event), 0) + 1
        twice = sorted(token for token in wanted if counts.get(token) != 1)
        if twice:
            fail(f"{len(twice)} lines did not arrive exactly once, e.g. {[(t, counts.get(t)) for t in twice[:5]]}")
    print(f"{len(wanted)} lines arrived" + (" once each" if once == "once" else ""))


def case_long(run, prefix):
    expect_tokens(run, "any", "0", f"{prefix}-after")
    for _, event in delivered(run):
        if len(event.get("log", "")) > 1024 * 1024 or token_of(event) == f"{prefix}-long":
            fail(f"a line over 1 MB arrived ({len(event.get('log', ''))} characters)")
    print("the 2 MB line was skipped, the next one arrived")


def case_invalid(run, prefix):
    bad = '{"token": "' + prefix + '-bad", "v": "a\ufffdb"}'
    expect_tokens(run, "once", "2", f"{prefix}-bad", *(f"{prefix}-{n:05d}" for n in range(3)))
    logs = [event["log"] for _, event in delivered(run) if token_of(event) == f"{prefix}-bad"]
    if logs != [bad]:
        fail(f"the line with the invalid byte arrived as {logs!r}, expected {[bad]!r}")
    print("the invalid byte arrived as U+FFFD, the rest of its batch too")


def nothing(run, seconds):
    time.sleep(float(seconds))
    found = requests(run)
    if found:
        fail(f"{len(found)} requests reached the stub, expected none")
    print(f"no request reached the stub in {seconds} s")


def within(run, seconds, *tokens):
    """expect once, within SECONDS from now (a delivery after the probe of the HIVE succeeded)."""
    start = time.monotonic()
    expect_tokens(run, "once", "2", *tokens, timeout=float(seconds))
    print(f"within {time.monotonic() - start:.1f} s")


def posts(run, count):
    """The number of POSTs of events (not the probes /tpot-ingest) is COUNT."""
    found = [request for request in requests(run) if request["path"] != "/tpot-ingest"]
    if len(found) != int(count):
        fail(f"{len(found)} requests of events reached the stub, expected {count}")
    print(f"{len(found)} requests of events")


def probes(run, status):
    """Waits for a probe /tpot-ingest the stub answered with STATUS."""
    deadline = time.monotonic() + TIMEOUT
    while not any(r["path"] == "/tpot-ingest" and r["status"] == int(status) for r in requests(run)):
        if time.monotonic() > deadline:
            fail(f"no probe answered {status} after {TIMEOUT} s")
        time.sleep(0.5)
    found = [r for r in requests(run) if r["path"] == "/tpot-ingest"]
    bad = [r for r in found if r["method"] != "GET" or r["body"]]
    if bad:
        fail(f"a probe is no GET without a body: {bad[0]}")
    print(f"{len(found)} probes so far")


def statuses(run, status):
    """Waits for a request the stub answered with STATUS (Fluent Bit tried to deliver)."""
    deadline = time.monotonic() + TIMEOUT
    while not any(request["status"] == int(status) for request in requests(run)):
        if time.monotonic() > deadline:
            fail(f"no request answered {status} after {TIMEOUT} s")
        time.sleep(0.5)


def flood(data, prefix, rate, seconds, manifest_path):
    """Appends RATE lines per second for SECONDS seconds in the format of `append ... lines`, round-robin
    to the log file of every fixture whose folder exists (paced like docker/_tests/perf/load.py), one
    write per line; MANIFEST gets the tokens per file."""
    from tpotctl.checks import fixtures
    rate, seconds = int(rate), int(seconds)
    paths = []
    for name in fixtures.names():
        path = Path(data) / fixtures.log_file(name)
        if path.parent.is_dir():
            paths.append(path)
    if not paths:
        fail(f"no log folder of a fixture under {data}")
    files = {}
    manifest = {}
    try:
        for path in paths:
            if path not in files:
                files[path] = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
                os.chmod(path, 0o644)
                manifest["/data/" + str(path.relative_to(data))] = []
        start = time.monotonic()
        for number in range(rate * seconds):
            pause = start + number / rate - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            path = paths[number % len(paths)]
            token = f"{prefix}-{number:05d}"
            os.write(files[path], (json.dumps({"token": token, "pad": "x" * 64}) + "\n").encode())
            manifest["/data/" + str(path.relative_to(data))].append(token)
        took = time.monotonic() - start
    finally:
        for fd in files.values():
            os.close(fd)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    print(f"{rate * seconds} lines into {len(files)} files in {took:.1f} s")


def outage_count(run, manifest_path, timeout):
    """Waits until every token of MANIFEST arrived in a request answered below 300, at least 900 s (a
    chunk Fluent Bit retried 6 times during the outage waits up to 640 s for its next retry); fails
    with the number missing per file and the first 5 missing tokens; reports duplicates."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    wanted = {token for tokens in manifest.values() for token in tokens}
    timeout = max(int(timeout), 900)
    counts = {}
    log = Path(run) / "stub" / "requests.jsonl"
    offset = 0
    start = time.monotonic()
    while True:
        try:
            with open(log, "rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except FileNotFoundError:
            chunk = b""
        # only whole lines, a line the stub is still writing waits for the next round
        end = chunk.rfind(b"\n") + 1
        offset += end
        for line in chunk[:end].splitlines():
            try:
                request = json.loads(line)
            except ValueError:
                continue
            if request.get("status", 999) >= 300 or request.get("path") != "/":
                continue
            for event in json.loads(request["body"] or "[]"):
                token = token_of(event)
                if token in wanted:
                    counts[token] = counts.get(token, 0) + 1
        if len(counts) == len(wanted):
            break
        if time.monotonic() - start > timeout:
            missing = {path: [token for token in tokens if token not in counts] for path, tokens in manifest.items()}
            per_file = ", ".join(f"{path} {len(tokens)}" for path, tokens in sorted(missing.items()) if tokens)
            first = sorted(token for tokens in missing.values() for token in tokens)[:5]
            fail(f"{len(wanted) - len(counts)} of {len(wanted)} lines missing after {timeout} s ({per_file}), "
                 f"e.g. {first}")
        time.sleep(2)
    twice = sum(1 for count in counts.values() if count > 1)
    print(f"all {len(wanted)} lines arrived {time.monotonic() - start:.0f} s after the HIVE answered again, "
          f"{twice} of them more than once ({sum(counts.values()) - len(counts)} duplicates)")


COMMANDS = {"folders": folders, "prepare-fixtures": prepare_fixtures, "fixtures": case_fixtures, "heartbeats": case_heartbeats,
            "append": append, "expect": expect_tokens, "long": case_long, "invalid": case_invalid,
            "nothing": nothing, "status": statuses, "within": within, "posts": posts, "probes": probes,
            "flood": flood, "outage-count": outage_count}
COMMANDS[sys.argv[1]](*sys.argv[2:])
PY
}

write_compose() {
  # write_compose <certificate name> [KEY=VALUE ...]: the stub and Fluent Bit of a run
  local cert="$1"
  shift
  local pair key
  local -A env=()
  local -a keys=()
  # a later KEY=VALUE replaces an earlier one (the defaults of start_run come first)
  for pair in "$@"; do
    key="${pair%%=*}"
    [[ -v "env[${key}]" ]] || keys+=("${key}")
    env["${key}"]="${pair#*=}"
  done
  {
    printf 'services:\n'
    printf '  hive:\n'
    printf '    image: "%s"\n' "${INIT_IMAGE}"
    printf '    user: "%s:%s"\n' "$(id -u)" "$(id -g)"
    printf '    network_mode: none\n'
    printf '    extra_hosts: ["hive.test:127.0.0.1"]\n'
    printf '    entrypoint: ["python3", "/stub-lib/hive_stub.py", "serve", "--cert", "/certs/%s.crt", "--key", "/certs/%s.key", "--log", "/stub/requests.jsonl", "--status-file", "/stub/status", "--probe-file", "/stub/probe"]\n' "${cert}" "${cert}"
    printf '    volumes:\n'
    printf '      - "%s:/stub-lib:ro"\n' "${TEST_LIB_DIR}"
    printf '      - "%s:/certs:ro"\n' "${CERTS}"
    printf '      - "%s/stub:/stub"\n' "${RUN_DIR}"
    printf '  fluentbit:\n'
    printf '    image: "%s"\n' "${IMAGE}"
    printf '    container_name: "%s"\n' "${TEST_CONTAINER_NAME}"
    printf '    network_mode: "service:hive"\n'
    printf '    depends_on: [hive]\n'
    printf '    read_only: true\n'
    printf '    tmpfs: ["/tmp:uid=2000,gid=2000"]\n'
    printf '    mem_limit: 256m\n'
    printf '    ulimits:\n'
    printf '      nofile:\n'
    printf '        soft: %s\n' "${NOFILE_SOFT}"
    printf '        hard: %s\n' "${NOFILE_HARD}"
    if [[ -n "${HEALTH_INTERVAL}" ]]; then
      printf '    healthcheck:\n'
      printf '      interval: %s\n' "${HEALTH_INTERVAL}"
    fi
    printf '    environment:\n'
    for key in "${keys[@]}"; do
      printf '      %s: "%s"\n' "${key}" "${env[${key}]}"
    done
    printf '    volumes:\n'
    printf '      - "%s/data:/data"\n' "${RUN_DIR}"
  } > "${TEST_HARNESS_COMPOSE}"
}

# The data folder of a run: what tpotinit gives Fluent Bit on a SENSOR (elk_environment, hive.crt,
# the folder fluentbit), the files of the stub beside it
prepare_run() {
  # prepare_run <run> [folder ...]: the folders of the inputs except these (paths in the container)
  RUN_DIR="${TEST_TMP_ROOT}/$1"
  shift
  mkdir -p "${RUN_DIR}/data/tpot/etc/compose" "${RUN_DIR}/data/fluentbit/storage" "${RUN_DIR}/stub"
  checker folders "${RUN_DIR}/data" "$@"
  cp "${CERTS}/ca.crt" "${RUN_DIR}/data/hive.crt"
  cat > "${RUN_DIR}/data/tpot/etc/compose/elk_environment" <<EOF
HONEY_UUID=00000000-0000-4000-8000-000000000000
MY_EXTIP=${SMOKE_EXTIP}
MY_INTIP=${SMOKE_INTIP}
MY_HOSTNAME=${SMOKE_HOSTNAME}
MY_VERSION=${SMOKE_VERSION}
EOF
  echo 200 > "${RUN_DIR}/stub/status"
  chmod 0777 "${RUN_DIR}/stub"
}

# Folders readable for uid 2000, the folder fluentbit writable for it
open_data() {
  find "${RUN_DIR}/data" -type d -exec chmod 0755 {} +
  find "${RUN_DIR}/data" -type f -exec chmod 0644 {} +
  chmod 0777 "${RUN_DIR}/data/fluentbit" "${RUN_DIR}/data/fluentbit/storage"
}

start_run() {
  # start_run <certificate name> [KEY=VALUE ...]: the environment of Fluent Bit besides the defaults
  local cert="$1"
  shift
  open_data
  # FLB_PROBE_INTERVAL=2: a first probe before the stub listens must not hold a run for 300 s
  write_compose "${cert}" "TPOT_HIVE_IP=127.0.0.1" "TPOT_HIVE_USER=${SMOKE_AUTH}" "LS_SSL_VERIFICATION=full" \
    "FLB_PROBE_INTERVAL=2" "$@"
  test_compose up -d --force-recreate --remove-orphans >/dev/null 2>&1 || test_die "Could not start the run $(basename "${RUN_DIR}")"
  RUN_START="$(python3 -c 'import time; print(time.time())')"
  test_wait_for_container || test_die "Fluent Bit did not start"
}

stop_run() {
  test_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}

flb_logs() {
  docker logs "${TEST_CONTAINER_NAME}" 2>&1
}

# Run main: the fixtures, heartbeats, a long line, an invalid byte, files made after the start, the image
run_main() {
  local health
  # case 6 makes the folder of nginx after the start
  if wanted 6; then prepare_run main /data/nginx/log; else prepare_run main; fi
  if wanted 1; then
    checker prepare-fixtures "${RUN_DIR}/data" "${RUN_DIR}/manifest.json"
  fi
  # a file of the glob before the start, the file of case 6 is a new one beside it
  checker append "${RUN_DIR}/data/honeypots/log/http.log" lines early 1
  start_run hive
  if wanted 2; then
    checker heartbeats "${RUN_DIR}" "${RUN_START}" 1
  fi
  if wanted 1; then
    checker fixtures "${RUN_DIR}" "${RUN_DIR}/manifest.json"
    test_ok "1 fixtures: record, headers, the honeypot's own path"
  fi
  if wanted 3; then
    checker append "${RUN_DIR}/data/cowrie/log/cowrie.json" long longline
    checker long "${RUN_DIR}" longline
    flb_logs | grep -i -E "long line|skip" >/dev/null || test_die "No log line of Fluent Bit names the skipped long line"
    test_ok "3 long line skipped: $(flb_logs | grep -i -E 'long line|skip' | head -n 1)"
  fi
  if wanted 4; then
    checker append "${RUN_DIR}/data/adbhoney/log/adbhoney.json" invalid invalid
    checker invalid "${RUN_DIR}" invalid
    test_ok "4 invalid byte as U+FFFD ($(docker image inspect -f '{{.Architecture}}' "${IMAGE}"))"
  fi
  if wanted 6; then
    checker append "${RUN_DIR}/data/honeypots/log/smtp.log" lines newglob 3
    checker append "${RUN_DIR}/data/nginx/log/access.log" lines newfolder 3
    chmod 0755 "${RUN_DIR}/data/nginx" "${RUN_DIR}/data/nginx/log"
    checker expect "${RUN_DIR}" once 2 newglob-00000 newglob-00001 newglob-00002 \
      newfolder-00000 newfolder-00001 newfolder-00002
    test_ok "6 files made after the start arrive from their head"
  fi
  if wanted 2; then
    checker heartbeats "${RUN_DIR}" "${RUN_START}" 2
    test_ok "2 heartbeats"
  fi
  if wanted 9; then
    [[ "$(docker exec "${TEST_CONTAINER_NAME}" id -u):$(docker exec "${TEST_CONTAINER_NAME}" id -g)" == "2000:2000" ]] \
      || test_die "Fluent Bit does not run as 2000:2000"
    if docker exec "${TEST_CONTAINER_NAME}" touch /etc/fluent-bit/tpot-write-test >/dev/null 2>&1; then
      test_die "The root file system is writable"
    fi
    health=""
    for _ in $(seq 1 60); do
      health="$(docker inspect -f '{{.State.Health.Status}}' "${TEST_CONTAINER_NAME}")"
      [[ "${health}" == "healthy" ]] && break
      sleep 1
    done
    [[ "${health}" == "healthy" ]] || test_die "Fluent Bit is not healthy: ${health}"
    test_ok "9 uid 2000, read-only root, healthy"
  fi
  if flb_logs | grep -E "\[ *(error|warn)\]" | grep -v -i -E "long line|skip" >&2; then
    test_die "Fluent Bit logged errors or warnings (above)"
  fi
  stop_run
}

# Run retry: 503 for 30 s and a restart of Fluent Bit meanwhile
run_retry() {
  local data refused n
  local tokens=()
  prepare_run retry
  data="${RUN_DIR}/data"
  checker append "${data}/cowrie/log/cowrie.json" lines before-cowrie 5
  checker append "${data}/dionaea/log/dionaea.json" lines before-dionaea 5
  checker append "${data}/conpot/log/conpot_retry.json" lines before-conpot 5
  start_run hive
  checker expect "${RUN_DIR}" any 0 before-cowrie-00004 before-dionaea-00004 before-conpot-00004
  echo 503 > "${RUN_DIR}/stub/status"
  refused="${SECONDS}"
  checker append "${data}/cowrie/log/cowrie.json" lines during-cowrie 20
  checker append "${data}/dionaea/log/dionaea.json" lines during-dionaea 20
  checker append "${data}/conpot/log/conpot_retry.json" lines during-conpot 20
  checker status "${RUN_DIR}" 503
  sleep 5
  docker restart -t 10 "${TEST_CONTAINER_NAME}" >/dev/null
  checker append "${data}/cowrie/log/cowrie.json" lines after-cowrie 10
  checker append "${data}/conpot/log/conpot_new.json" lines after-conpot 10
  while (( SECONDS - refused < 30 )); do sleep 1; done
  echo 200 > "${RUN_DIR}/stub/status"
  for n in $(seq -f '%05g' 0 4); do tokens+=("before-cowrie-${n}" "before-dionaea-${n}" "before-conpot-${n}"); done
  for n in $(seq -f '%05g' 0 19); do tokens+=("during-cowrie-${n}" "during-dionaea-${n}" "during-conpot-${n}"); done
  for n in $(seq -f '%05g' 0 9); do tokens+=("after-cowrie-${n}" "after-conpot-${n}"); done
  checker expect "${RUN_DIR}" once 20 "${tokens[@]}"
  test_ok "5 503 for 30 s and a restart: all ${#tokens[@]} lines once each"
  stop_run
}

# Run limit: a storage limit of 1M while the HIVE refuses 5 MB of lines, in 10 batches 3 s apart. A
# single burst above the limit within one flush interval loses its newest lines instead ("no available
# chunk": no chunk is a task yet, so none can be evicted)
run_limit() {
  local data restarts deadline batch
  prepare_run limit
  data="${RUN_DIR}/data"
  checker append "${data}/cowrie/log/cowrie.json" lines first 1
  start_run hive FLB_STORAGE_LIMIT=1M
  checker expect "${RUN_DIR}" any 0 first-00000
  echo 503 > "${RUN_DIR}/stub/status"
  for batch in 0 1 2 3 4 5 6 7 8 9; do
    checker append "${data}/cowrie/log/cowrie.json" bulk "bulk${batch}" 500
    sleep 3
  done
  deadline=$((SECONDS + 120))
  until flb_logs | grep -i -E "drop|discard|evict" >/dev/null; do
    (( SECONDS < deadline )) || test_die "Fluent Bit logged no dropped chunk within 120 s"
    sleep 2
  done
  sleep 5
  [[ "$(docker inspect -f '{{.State.Status}}' "${TEST_CONTAINER_NAME}")" == "running" ]] || test_die "Fluent Bit stopped"
  restarts="$(docker inspect -f '{{.RestartCount}} {{.State.OOMKilled}}' "${TEST_CONTAINER_NAME}")"
  [[ "${restarts}" == "0 false" ]] || test_die "Fluent Bit restarted or was killed (restarts, OOM): ${restarts}"
  echo 200 > "${RUN_DIR}/stub/status"
  checker expect "${RUN_DIR}" any 0 bulk9-00498 bulk9-00499
  test_ok "7 storage limit 1M: $(flb_logs | grep -i -E 'drop|discard|evict' | head -n 1)"
  stop_run
}

# Run tls: the certificate of the HIVE is checked against the address Fluent Bit uses (127.0.0.1 in
# the run main, hive.test here)
run_tls() {
  prepare_run tls-name
  start_run hive TPOT_HIVE_IP=hive.test
  checker heartbeats "${RUN_DIR}" "${RUN_START}" 1
  stop_run
  prepare_run tls-wrong
  start_run wrong
  checker nothing "${RUN_DIR}" 15
  flb_logs | grep -i -E "tls|certificate|verify" >/dev/null || test_die "No TLS error in the log of Fluent Bit"
  flb_logs | grep -i -E "The HIVE is not reachable \(curl: \([0-9]+\).*(certificate|ssl|tls)" >/dev/null \
    || test_die "The probe does not name the TLS error of curl"
  test_ok "8 TLS: 127.0.0.1 and hive.test deliver; a wrong certificate: $(flb_logs | grep -i -E 'tls|certificate|verify' | head -n 1)"
  stop_run
}

# Case 9 without a HIVE: the plugins and the checks of the entrypoint
run_image() {
  local help out rc setting plugin
  help="$(docker run --rm --network none --entrypoint fluent-bit "${IMAGE}" -h 2>&1)" || true
  for plugin in tail dummy lua http; do
    grep -E "^[[:space:]]+${plugin}[[:space:]]" <<< "${help}" >/dev/null || test_die "fluent-bit -h does not list ${plugin}"
  done
  for setting in "TPOT_HIVE_IP=" "TPOT_HIVE_USER=" "LS_SSL_VERIFICATION=sometimes" "FLB_PROBE_INTERVAL=0"; do
    rc=0
    out="$(docker run --rm --network none --read-only -e TPOT_HIVE_IP=127.0.0.1 -e "TPOT_HIVE_USER=${SMOKE_AUTH}" \
      -e "${setting}" "${IMAGE}" 2>&1)" || rc=$?
    [[ "${rc}" -eq 1 ]] || test_die "The entrypoint takes ${setting} (rc ${rc}): ${out}"
    grep -F "${setting%%=*}" <<< "${out}" >/dev/null || test_die "The entrypoint does not name ${setting%%=*}: ${out}"
  done
  test_ok "9 plugins tail, dummy, lua, http; the entrypoint refuses an empty HIVE address or user, a wrong verification or probe interval"
}

# The healthy checks in a row at the end of the health log of Fluent Bit
healthy_streak() {
  local code streak=0
  for code in $(docker inspect -f '{{if .State.Health}}{{range .State.Health.Log}}{{.ExitCode}} {{end}}{{end}}' "${TEST_CONTAINER_NAME}"); do
    if [[ "${code}" == "0" ]]; then streak=$((streak + 1)); else streak=0; fi
  done
  printf '%s\n' "${streak}"
}

wait_for_log() {
  # wait_for_log <text> <seconds>
  local deadline=$((SECONDS + $2))
  until flb_logs | grep -F -- "$1" >/dev/null; do
    (( SECONDS < deadline )) || test_die "Fluent Bit did not log within $2 s: $1"
    sleep 1
  done
}

# Run probe (E2): a HIVE that does not take the format (401), then its Logstash not ready (503 without
# the marker), then one that takes it; Fluent Bit reads and sends nothing until then
run_probe() {
  local data n deadline
  local tokens=()
  prepare_run probe
  data="${RUN_DIR}/data"
  echo 401 > "${RUN_DIR}/stub/probe"
  checker append "${data}/cowrie/log/cowrie.json" lines held-cowrie 5
  checker append "${data}/dionaea/log/dionaea.json" lines held-dionaea 5
  HEALTH_INTERVAL=2s start_run hive FLB_PROBE_INTERVAL=5
  checker probes "${RUN_DIR}" 401
  wait_for_log "The HIVE does not take this SENSOR's format yet (update the HIVE first); logs wait here." 30
  checker append "${data}/cowrie/log/cowrie.json" lines waiting-cowrie 5
  if wanted 10; then
    deadline=$((SECONDS + 60))
    until [[ "$(healthy_streak)" -ge 3 ]]; do
      (( SECONDS < deadline )) || test_die "Fluent Bit was not healthy for 3 checks in a row while it waits: $(docker inspect -f '{{json .State.Health}}' "${TEST_CONTAINER_NAME}")"
      sleep 1
    done
    [[ "$(docker inspect -f '{{.State.Health.Status}}' "${TEST_CONTAINER_NAME}")" == "healthy" ]] \
      || test_die "Fluent Bit is not healthy while it waits"
    checker posts "${RUN_DIR}" 0
    test_ok "10 401 on /tpot-ingest: no POST, healthy for $(healthy_streak) checks in a row, the message names it"
  fi
  echo 503 > "${RUN_DIR}/stub/probe"
  checker probes "${RUN_DIR}" 503
  wait_for_log "The HIVE's Logstash pipeline is not ready for this SENSOR format; logs wait here." 30
  checker append "${data}/dionaea/log/dionaea.json" lines waiting-dionaea 5
  checker posts "${RUN_DIR}" 0
  if wanted 11; then
    test_ok "11 503 without the marker: no POST, the message names it"
  fi
  echo 204 > "${RUN_DIR}/stub/probe"
  for n in $(seq -f '%05g' 0 4); do
    tokens+=("held-cowrie-${n}" "held-dionaea-${n}" "waiting-cowrie-${n}" "waiting-dionaea-${n}")
  done
  checker within "${RUN_DIR}" 15 "${tokens[@]}"
  if wanted 12; then
    test_ok "12 204 with X-TPot-Ingest 2: all ${#tokens[@]} lines written before arrive within 15 s, once each"
  fi
  if flb_logs | grep -E "\[ *(error|warn)\]" >&2; then
    test_die "Fluent Bit logged errors or warnings (above)"
  fi
  stop_run
}

# Run outage (G): the HIVE stops answering while the honeypots write 500 lines/s for 360 s; every
# pending retry of Fluent Bit holds a file (a timer) until the HIVE answers again
run_outage() {
  local data manifest result paused errors
  prepare_run outage
  data="${RUN_DIR}/data"
  manifest="${RUN_DIR}/flood.json"
  checker append "${data}/cowrie/log/cowrie.json" lines first 1
  start_run hive
  checker expect "${RUN_DIR}" any 0 first-00000
  test_compose pause hive >/dev/null 2>&1 || test_die "Could not pause the stub of the HIVE"
  paused="${SECONDS}"
  result="$(checker flood "${data}" flood 500 "${OUTAGE_SECONDS}" "${manifest}")"
  test_info "13 HIVE paused, 500 lines/s for ${OUTAGE_SECONDS} s: ${result}"
  sleep 30
  test_compose unpause hive >/dev/null 2>&1 || test_die "Could not unpause the stub of the HIVE"
  test_info "13 HIVE answers again after $((SECONDS - paused)) s"
  result="$(checker outage-count "${RUN_DIR}" "${manifest}" "${TEST_TIMEOUT}")" || {
    errors="$(flb_logs | grep -c "Too many open files" || true)"
    test_die "13 lines lost after the outage (\"Too many open files\" ${errors} times in the log of Fluent Bit)"
  }
  test_info "13 ${result}"
  errors="$(flb_logs | grep -c "Too many open files" || true)"
  [[ "${errors}" == "0" ]] || test_die "13 every line arrived, but the log of Fluent Bit has \"Too many open files\" ${errors} times"
  test_ok "13 outage under load: every line arrived after $((SECONDS - paused)) s, no \"Too many open files\""
  stop_run
}

# The file limit of Fluent Bit in compose/sensor.yml (yq of the tpotinit image, the host may lack PyYAML)
read_nofile() {
  local json
  json="$(docker run --rm --network none -v "${REPO_ROOT}/compose:/c:ro" --entrypoint yq "${INIT_IMAGE}" \
    -o json -I 0 '.services.fluentbit.ulimits.nofile // {}' /c/sensor.yml)" \
    || test_die "Could not read the file limit of fluentbit in compose/sensor.yml"
  if [[ "${json}" == "{}" ]]; then
    NOFILE_SOFT=1024
    NOFILE_HARD=524288
    NOFILE_SOURCE="default of a Debian docker, compose/sensor.yml has none"
  else
    read -r NOFILE_SOFT NOFILE_HARD < <(python3 -c 'import json, sys; n = json.loads(sys.argv[1]); print(n["soft"], n["hard"])' "${json}") \
      || test_die "compose/sensor.yml: no soft and hard in the file limit of fluentbit: ${json}"
    [[ "${NOFILE_SOFT}" =~ ^[0-9]+$ && "${NOFILE_HARD}" =~ ^[0-9]+$ ]] \
      || test_die "compose/sensor.yml: the file limit of fluentbit is no number: ${json}"
    NOFILE_SOURCE="compose/sensor.yml"
  fi
}

make_certs() {
  local name="$1"
  shift
  docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint python3 \
    -v "${TEST_LIB_DIR}:/stub-lib:ro" -v "${CERTS}:/certs" "${INIT_IMAGE}" \
    /stub-lib/hive_stub.py certs /certs "${name}" "$@"
}

main() {
  parse_args "$@"
  test_validate_timeout
  test_check_dependencies
  IMAGE="${IMAGE:-$(test_read_compose_image fluentbit "${DEFAULT_IMAGE}")}"
  INIT_IMAGE="${INIT_IMAGE:-$(test_read_compose_image tpotinit "${DEFAULT_INIT_IMAGE}")}"
  test_require_image "${IMAGE}" "cd ${FLUENTBIT_DIR} && docker compose build"
  test_require_image "${INIT_IMAGE}" "cd ${DOCKER_ROOT}/tpotinit && docker compose build"
  test_prepare_harness "${TEST_NAME}"
  test_enable_cleanup
  write_checker
  CERTS="${TEST_TMP_ROOT}/certs"
  mkdir -p "${CERTS}"
  make_certs hive --san IP:127.0.0.1 --san DNS:hive.test
  make_certs wrong --san DNS:other.test
  read_nofile
  test_info "Testing ${IMAGE} (stub in ${INIT_IMAGE}), cases ${CASES}"
  test_info "File limit of Fluent Bit ${NOFILE_SOFT}/${NOFILE_HARD} (${NOFILE_SOURCE})"
  if wanted 1 || wanted 2 || wanted 3 || wanted 4 || wanted 6 || wanted 9; then run_main; fi
  if wanted 5; then run_retry; fi
  if wanted 7; then run_limit; fi
  if wanted 8; then run_tls; fi
  if wanted 10 || wanted 11 || wanted 12; then run_probe; fi
  if wanted 9; then run_image; fi
  if wanted 13; then run_outage; fi
  test_ok "fluentbit smoke test passed (cases ${CASES})"
}

main "$@"
