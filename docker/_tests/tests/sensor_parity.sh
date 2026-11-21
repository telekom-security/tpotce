#!/usr/bin/env bash
set -Eeuo pipefail
unset CDPATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"

TEST_NAME="sensor_parity"
LOGSTASH_DIR="${DOCKER_ROOT}/elk/logstash"
IMAGE=""
FLB_IMAGE=""
NGINX_IMAGE=""
SENSOR_USER="sensor-parity"
CASES="1,2,3,4,5,6,7,8"

usage() {
  cat <<EOF
Usage: $0 [--image IMAGE] [--fluentbit-image IMAGE] [--nginx-image IMAGE] [--cases LIST] [--timeout SEC] [--bind-ip IP] [--keep-artifacts]

Parity of the two ways a honeypot log reaches the HIVE: the same rendered
fixtures are read by the HIVE's own Logstash (files pipeline) and, from a
SENSOR's data folder, by Fluent Bit, which sends them through nginx (lsweb.conf,
a test certificate and lswebpasswd) into the HIVE's sensors pipeline. Both sets
of events must equal the golden file (docker/_tests/golden); the SENSOR's keep
the exact t-pot_log_path of their file, a payload path and their sensor name.
Special cases, one line each:
  1  files made after the start in the ddospot and honeypots globs, both ways
  2  a Heralding header line: no event, no failure, the next line arrives
  3  a 2 MB line on the SENSOR is skipped (Fluent Bit's log), the next arrives
  4  an invalid UTF-8 byte gives the same event on both ways
  5  a 3 MB batch on the SENSOR: every line once, no 413 / 429 in nginx's log
  6  Logstash stopped for 60 s: what the SENSOR wrote meanwhile arrives once
  7  Fluent Bit restarted alone, Logstash restarted alone: no duplicates
  8  the T-Pot start of clean.sh (copytruncate, offsets removed), then with
     TPOT_PERSISTENCE=off (log folders made anew): only new lines, once
  9  lines written before Logstash is ready arrive once
--cases takes some of 1-8 (e.g. 4,6; 9 and the golden compare always run).
Logstash is the official image at the version of its Dockerfile with the
pipelines of this checkout mounted, every Elasticsearch output replaced by a
file; Fluent Bit and nginx are the T-Pot images (--fluentbit-image and
--nginx-image take others, e.g. amd64 builds of their own tag). Uses local
images only, never builds or pulls; nothing is published, the network is
internal (Listbot is never reached: stand-in dictionaries). Each wait takes up
to ten times --timeout; a run lasts about 6 minutes.
--bind-ip is accepted for runner compatibility.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
    --fluentbit-image) FLB_IMAGE="${2:?--fluentbit-image needs a value}"; shift 2 ;;
    --nginx-image) NGINX_IMAGE="${2:?--nginx-image needs a value}"; shift 2 ;;
    --cases) CASES="${2:?--cases needs a value}"; shift 2 ;;
    --timeout) TEST_TIMEOUT="${2:?--timeout needs a value}"; shift 2 ;;
    --bind-ip) TEST_BIND_IP="${2:?--bind-ip needs a value}"; shift 2 ;;
    --keep-artifacts) TEST_KEEP_ARTIFACTS=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) test_die "Unknown option: $1" ;;
  esac
done

[[ "${CASES}" =~ ^[1-8](,[1-8])*$ ]] || test_die "--cases takes numbers 1-8 separated by commas, not '${CASES}'"

test_check_dependencies
test_validate_timeout
test_require_command openssl
LS_VERSION="$(sed -n 's/^ARG LS_VER=//p' "${LOGSTASH_DIR}/Dockerfile" | head -n 1)"
IMAGE="${IMAGE:-docker.elastic.co/logstash/logstash:${LS_VERSION}}"
FLB_IMAGE="${FLB_IMAGE:-$(test_read_compose_image fluentbit "")}"
NGINX_IMAGE="${NGINX_IMAGE:-$(test_read_compose_image nginx "")}"
test_require_image "${IMAGE}" "docker pull ${IMAGE}"
test_require_image "${FLB_IMAGE}" "cd docker/fluentbit && docker compose build"
test_require_image "${NGINX_IMAGE}" "cd docker/nginx && docker compose build"
test_prepare_harness "${TEST_NAME}"
TEST_CONTAINER_NAME="${TEST_PROJECT_NAME}-fluentbit"
TEST_ARTIFACT_LOG_DIR="${TEST_TMP_ROOT}/nginx-log"
WAIT=$(( TEST_TIMEOUT * 10 ))
test_enable_cleanup

# The files of the test belong to the user of the host again (Fluent Bit writes as uid 2000, Logstash
# as 1000, nginx as its own user)
test_release_artifacts() {
  [[ -n "${TEST_TMP_ROOT}" && -d "${TEST_TMP_ROOT}" ]] || return 0
  docker run --rm --network none --user 0:0 --entrypoint chown \
    --mount "type=bind,src=${TEST_TMP_ROOT},dst=/work" "${FLB_IMAGE}" -R -h "$(id -u):$(id -g)" /work
}

checker() {
  PARITY_ROOT="${TEST_TMP_ROOT}" PARITY_WAIT="${WAIT}" PARITY_SENSOR="${SENSOR_USER}" \
    PYTHONPATH="${REPO_ROOT}" python3 "${TEST_TMP_ROOT}/check.py" "$@"
}

cat > "${TEST_TMP_ROOT}/check.py" <<'PY'
"""The steps and checks of sensor_parity.sh on the data folders of the HIVE and the SENSOR and on the
outputs of Logstash (out/events.json, out/failures.json, out/sensors.json)."""
import gzip
import json
import os
import re
import shutil
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

from tpotctl import logs_config  # noqa: E402
from tpotctl.checks import fixtures  # noqa: E402
from tpotctl.tests import logstash_conf  # noqa: E402

ROOT = Path(os.environ["PARITY_ROOT"])
sys.path.insert(0, str(logstash_conf.ROOT / "docker" / "_tests" / "lib"))
import golden  # noqa: E402

GOLDEN = logstash_conf.ROOT / "docker" / "_tests" / "golden" / "logstash_fixtures.jsonl"
DATA = {"hive": ROOT / "hive" / "data", "sensor": ROOT / "sensor" / "data"}
OUT = ROOT / "out"
LEDGER = ROOT / "ledger.tsv"
# an invalid byte and valid characters of two, three and four bytes after it
INVALID = b"tpot-\xffcheck-" + "\u0436\u20ac\U0001F600".encode()
INVALID_TEXT = "tpot-\ufffdcheck-\u0436\u20ac\U0001F600"
WAIT = float(os.environ["PARITY_WAIT"])
SENSOR = os.environ["PARITY_SENSOR"]
# the values of logstash_fixtures.sh: the golden file holds what they give
MOMENT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
SRC_IP = "8.8.8.8"
# the lines of the special cases: a token of their own (no "golden", no part of another), a source port
# below the marker ports of the fixtures (40000-59999)
TOKEN = re.compile(r"parity-[a-z0-9]+-[0-9]{6}-x")
HERALDING_HEADER = "timestamp,auth_id,session_id,src_ip,src_port,dest_ip,dest_port,proto,username,password,password_hash"


def fail(message):
    print(f"[FAIL] {message}", file=sys.stderr)
    sys.exit(1)


def side_of(line):
    return "sensor" if '"t-pot_sensor"' in line else "hive"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    for folder in [path.parent, *path.parent.parents]:
        if folder in DATA.values() or folder == ROOT:
            break
        os.chmod(folder, 0o755)
    with open(path, "ab") as handle:
        handle.write(data)
    os.chmod(path, 0o644)


def folders(data):
    """The folders of every input path, as tpotinit makes them (a missing folder of a glob makes Fluent Bit
    log a read error)."""
    for entry in logs_config.INPUTS:
        for path in entry.paths:
            folder = data / Path(path).parent.relative_to("/data")
            folder.mkdir(parents=True, exist_ok=True)
            os.chmod(folder, 0o755)


def prepare():
    manifest = {}
    for number, name in enumerate(fixtures.names()):
        marker = 40000 + zlib.crc32(name.encode()) % 20000
        token = "golden" + re.sub("[^a-z0-9]", "", name.lower())
        rel = fixtures.log_file(name)
        line = fixtures.render(name, MOMENT, SRC_IP, marker, 8080 + number, token)
        for data in DATA.values():
            write(data / rel, (line + "\n").encode())
        manifest[name] = {"path": "/data/" + rel, "token": token, "marker": marker}
    for data in DATA.values():
        folders(data)
    sensor = DATA["sensor"]
    for folder in (sensor / "fluentbit", sensor / "fluentbit" / "storage"):
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o777)
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    with open(OUT / "expect.tsv", "w", encoding="utf-8") as handle:
        for name, want in sorted(manifest.items()):
            handle.write(f"{name}\t{want['token']}\t{want['marker']}\n")
    LEDGER.write_text("", encoding="utf-8")
    print(f"{len(manifest)} fixtures into the data folders of the HIVE and the SENSOR")


def rewrite(pipeline):
    """Every Elasticsearch output of the HIVE's pipelines becomes a file of JSON lines (as many as shipped)."""
    block = re.compile(r"^( *)elasticsearch \{\n.*?^\1\}\n", re.M | re.S)
    for name, target, count in (("90-output-es.conf", "events", 1), ("92-output-failures.conf", "failures", 1),
                                ("21-sensors-output.conf", "sensors", 3)):
        path = Path(pipeline) / name
        text, found = block.subn(lambda m: f'{m.group(1)}file {{ path => "/out/{target}.json" codec => json_lines }}\n',
                                 path.read_text(encoding="utf-8"))
        if found != count:
            fail(f"{name}: {found} Elasticsearch outputs replaced, expected {count}")
        if re.search(r"^\s*elasticsearch \{", text, re.M):
            fail(f"{name}: an Elasticsearch output is left")
        path.write_text(text, encoding="utf-8")
    print("the Elasticsearch outputs of 90-output-es, 92-output-failures and 21-sensors-output are files")


def add(sides, name, tag, count, rel="", kind="plain"):
    """Appends lines of the fixture of a honeypot with tokens parity-<tag>-<n>-x, in one write per file."""
    sides = sides.split(",")
    rel = rel or fixtures.log_file(name)
    count = int(count)
    lines = []
    tokens = []
    # tokens stay unique within a tag over several calls
    first = len(ledger(tag)) if LEDGER.exists() else 0
    if kind == "bulk":
        # lines until the batch holds 3 MiB
        size, number = 0, first
        while size < 3 * 1024 * 1024:
            token = f"parity-{tag}-{number:06d}-x"
            line = fixtures.render(name, MOMENT, SRC_IP, 20000 + number % 20000, 2222, token).encode() + b"\n"
            lines.append(line)
            tokens.append(token)
            size += len(line)
            number += 1
    else:
        for number in range(first, first + count):
            token = f"parity-{tag}-{number:06d}-x"
            line = fixtures.render(name, MOMENT, SRC_IP, 20000 + number % 20000, 2222, token).encode()
            if token.encode() not in line:
                fail(f"{name}: the fixture has no place for a token")
            if kind == "invalid":
                if b"tpot-check" not in line:
                    fail(f"{name}: no place for the invalid byte")
                line = line.replace(b"tpot-check", INVALID, 1)
            lines.append(line + b"\n")
            tokens.append(token)
    if kind == "header":
        lines.insert(0, HERALDING_HEADER.encode() + b"\n")
    elif kind == "long":
        lines.insert(0, b'{"eventid": "parity.long", "pad": "' + b"y" * (2 * 1024 * 1024) + b'"}\n')
    for side in sides:
        write(DATA[side] / rel, b"".join(lines))
    with open(LEDGER, "a", encoding="utf-8") as handle:
        for token in tokens:
            handle.write(f"{token}\t{tag}\t{','.join(sides)}\t/data/{rel}\n")
    print(f"{len(tokens)} {'line' if len(tokens) == 1 else 'lines'} of {name} into /data/{rel} ({' and '.join(sides)})"
          + (f", {sum(map(len, lines)) / 1048576:.1f} MiB in one write" if kind == "bulk" else ""))


def ledger(tag=None):
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        token, row_tag, sides, path = line.split("\t")
        if tag is None or row_tag == tag:
            rows.append({"token": token, "tag": row_tag, "sides": sides.split(","), "path": path})
    return rows


def events():
    """(side, tokens, line) of every event of the processing pipeline."""
    path = OUT / "events.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8", errors="strict") as handle:
        return [(side_of(line), set(TOKEN.findall(line)), line) for line in handle if line.strip()]


def counts(found):
    result = {}
    for side, tokens, _ in found:
        for token in tokens:
            result[(side, token)] = result.get((side, token), 0) + 1
    return result


def wait(tag, settle="5"):
    """Waits until every line of a tag arrived on its ways, then checks that each arrived once (and nowhere
    else)."""
    rows = ledger(tag)
    if not rows:
        fail(f"no lines of {tag}")
    started = time.monotonic()
    deadline = started + WAIT
    while True:
        seen = counts(events())
        missing = [(side, row["token"]) for row in rows for side in row["sides"] if (side, row["token"]) not in seen]
        if not missing:
            break
        if time.monotonic() > deadline:
            fail(f"{tag}: {len(missing)} lines missing after {WAIT:.0f} s, e.g. {missing[:4]}")
        time.sleep(1)
    took = time.monotonic() - started
    time.sleep(float(settle))
    seen = counts(events())
    wrong = []
    for row in rows:
        for side in ("hive", "sensor"):
            want = 1 if side in row["sides"] else 0
            if seen.get((side, row["token"]), 0) != want:
                wrong.append((side, row["token"], seen.get((side, row["token"]), 0)))
    if wrong:
        fail(f"{tag}: {len(wrong)} lines did not arrive exactly once on their way, e.g. {wrong[:4]}")
    print(f"{len(rows)} lines of {tag} arrived once each on their way ({took:.0f} s)")


def by_token(tag):
    """{token: {side: event}} of the lines of a tag."""
    rows = {row["token"]: row for row in ledger(tag)}
    result = {}
    for side, tokens, line in events():
        for token in tokens & set(rows):
            result.setdefault(token, {})[side] = json.loads(line)
    return rows, result


def logpaths(tag):
    """The SENSOR's events of a tag carry the exact file they were written to, the sensor and the HIVE's way
    the same type."""
    rows, found = by_token(tag)
    for token, row in rows.items():
        sensor = found.get(token, {}).get("sensor")
        if "sensor" in row["sides"]:
            if sensor is None:
                fail(f"{token}: no event on the SENSOR's way")
            if sensor.get("t-pot_log_path") != row["path"] or sensor.get("t-pot_sensor") != SENSOR:
                fail(f"{token}: t-pot_log_path {sensor.get('t-pot_log_path')!r} t-pot_sensor "
                     f"{sensor.get('t-pot_sensor')!r}, expected {row['path']!r} {SENSOR!r}")
        hive = found.get(token, {}).get("hive")
        if hive is not None and sensor is not None and hive.get("type") != sensor.get("type"):
            fail(f"{token}: type {hive.get('type')!r} on the HIVE, {sensor.get('type')!r} from the SENSOR")
    types = sorted({event.get("type") for sides in found.values() for event in sides.values()})
    print(f"t-pot_log_path, t-pot_sensor and type as written ({', '.join(map(str, types))})")


def compare_golden():
    """Both ways against the golden file: the events of the fixtures (by token or marker port) only, every
    fixture once; the SENSOR's events keep their file, a payload path and the sensor name."""
    expect = golden.read_expect(OUT / "expect.tsv")
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    wanted = golden.load(GOLDEN)
    found = {"hive": [], "sensor": []}
    for side, _, line in events():
        event = json.loads(line)
        if golden.owner(event, expect) is not None:
            found[side].append(event)
    for side in ("hive", "sensor"):
        try:
            matched = golden.match(found[side], expect)
        except golden.MatchError as error:
            fail(f"{side}: {error}")
        diffs = golden.compare(wanted, {name: golden.normalize(event, golden.payload_keys(name))
                                        for name, event in matched.items()})
        if diffs:
            fail(f"{side}: {len(diffs)} differences to the golden file:\n" + "\n".join(diffs[:40]))
        if side == "sensor":
            for name, event in matched.items():
                if event.get("t-pot_log_path") != manifest[name]["path"]:
                    fail(f"{name}: t-pot_log_path {event.get('t-pot_log_path')!r}, expected {manifest[name]['path']!r}")
                if event.get("t-pot_sensor") != SENSOR:
                    fail(f"{name}: t-pot_sensor {event.get('t-pot_sensor')!r}, expected {SENSOR!r}")
            payload = matched["wordpot"].get("path")
            if payload != "/goldenwordpot":
                fail(f"wordpot: the payload path is {payload!r}, expected '/goldenwordpot'")
        print(f"{side}: {len(matched)} fixtures equal to the golden file")


def ready(count):
    """Waits until both ways delivered every fixture."""
    expect = golden.read_expect(OUT / "expect.tsv")
    deadline = time.monotonic() + WAIT
    started = time.monotonic()
    while True:
        names = {"hive": set(), "sensor": set()}
        for side, _, line in events():
            name = golden.owner(json.loads(line), expect)
            if name:
                names[side].add(name)
        if all(len(found) >= int(count) for found in names.values()):
            break
        if time.monotonic() > deadline:
            fail(f"after {WAIT:.0f} s {len(names['hive'])} fixtures on the HIVE's way, {len(names['sensor'])} on "
                 f"the SENSOR's, expected {count}: missing {sorted(set(e[0] for e in expect) - names['sensor'])[:8]}")
        time.sleep(1)
    print(f"every fixture arrived on both ways ({time.monotonic() - started:.0f} s)")


def heralding(tag):
    """No event and no failure document of the header line."""
    rows, found = by_token(tag)
    for side, _, line in events():
        event = json.loads(line)
        if event.get("type") == "Heralding" and (event.get("auth_id") == "auth_id" or "auth_id,session_id" in line):
            fail(f"{side}: an event of the header line: {line[:300]}")
    failures = OUT / "failures.json"
    if failures.exists() and "auth_id,session_id" in failures.read_text(encoding="utf-8", errors="replace"):
        fail("a failure document of the header line")
    print("the header line gave no event and no failure, the lines after it arrived")


def invalid(tag):
    """The line with the invalid byte gives the same event on both ways."""
    rows, found = by_token(tag)
    for token in rows:
        pair = found.get(token, {})
        if set(pair) != {"hive", "sensor"}:
            fail(f"{token}: arrived on {sorted(pair)}")
        hive, sensor = (golden.normalize(pair[side], golden.payload_keys("cowrie")) for side in ("hive", "sensor"))
        if hive != sensor:
            diffs = golden.compare({"line": hive}, {"line": sensor})
            fail(f"{token}: the two ways differ: {diffs}")
        for side in ("hive", "sensor"):
            if pair[side].get("password") != INVALID_TEXT:
                fail(f"{token}: password {pair[side].get('password')!r} on the {side} way, not {INVALID_TEXT!r}")
    print(f"the same event on both ways, the invalid byte U+FFFD, the rest kept ({INVALID_TEXT!r})")


def long_line():
    with open(OUT / "events.json", encoding="utf-8") as handle:
        for line in handle:
            if len(line) > 1024 * 1024 or "parity.long" in line:
                fail(f"a line over 1 MB arrived ({len(line)} characters)")
    print("no event of the 2 MB line")


def clean(persistence):
    """The steps of clean.sh at a T-Pot start on both data folders: with persistence the copytruncate of
    logrotate (compress, no delaycompress: <file>.1.gz), else the log folders deleted and made again; on
    the SENSOR the offsets of Fluent Bit go, its storage stays."""
    for side, data in DATA.items():
        seen = set()
        for entry in logs_config.INPUTS:
            for path in entry.paths:
                folder = data / Path(path).parent.relative_to("/data")
                if folder in seen:
                    continue
                seen.add(folder)
                if persistence == "off":
                    shutil.rmtree(folder, ignore_errors=True)
                    continue
                for file in sorted(folder.glob("*")) if folder.is_dir() else []:
                    if file.is_file() and not file.name.endswith(".gz"):
                        with open(file, "rb") as source, gzip.open(f"{file}.1.gz", "wb") as target:
                            shutil.copyfileobj(source, target)
                        with open(file, "r+b") as handle:
                            handle.truncate(0)
        folders(data)
    removed = sorted(path.name for path in (DATA["sensor"] / "fluentbit").glob("offsets.db*"))
    for name in removed:
        (DATA["sensor"] / "fluentbit" / name).unlink()
    storage = DATA["sensor"] / "fluentbit" / "storage"
    if not storage.is_dir():
        fail("the storage of Fluent Bit is gone")
    print(f"TPOT_PERSISTENCE={persistence}: "
          + ("logs copied and truncated" if persistence == "on" else "log folders deleted and made again")
          + f", {len(removed)} offset files removed, storage kept")


def final():
    """Every line of the run once on its ways and nowhere else, no line of the test unknown, the fixtures once
    each (the golden file), no failure document."""
    rows = ledger()
    known = {row["token"] for row in rows}
    seen = counts(events())
    wrong = [(side, row["token"], seen.get((side, row["token"]), 0)) for row in rows for side in ("hive", "sensor")
             if seen.get((side, row["token"]), 0) != (1 if side in row["sides"] else 0)]
    if wrong:
        fail(f"{len(wrong)} lines not once on their way over the whole run, e.g. {wrong[:5]}")
    stray = sorted({token for _, token in seen} - known)
    if stray:
        fail(f"events of no line of the test: {stray[:5]}")
    compare_golden()
    failures = OUT / "failures.json"
    if failures.exists() and failures.read_text(encoding="utf-8", errors="replace").strip():
        fail("failure documents: " + failures.read_text(encoding="utf-8", errors="replace")[:600])
    total = len(events())
    print(f"{len(rows)} lines of the special cases once each over the run, {total} events, no failure document")


COMMANDS = {"prepare": prepare, "rewrite": rewrite, "add": add, "wait": wait, "logpaths": logpaths,
            "golden": compare_golden, "ready": ready, "heralding": heralding, "invalid": invalid,
            "long": long_line, "clean": clean, "final": final}
COMMANDS[sys.argv[1]](*sys.argv[2:])
PY

# Folders: the data of the HIVE (read by Logstash, uid 1000) and of the SENSOR (Fluent Bit, uid 2000), the
# outputs of Logstash, the pipelines as the Dockerfile builds them, the files of nginx
mkdir -p "${TEST_TMP_ROOT}/hive/data" "${TEST_TMP_ROOT}/sensor/data/tpot/etc/compose" "${TEST_TMP_ROOT}/out" \
  "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/listbot" "${TEST_TMP_ROOT}/cert" "${TEST_ARTIFACT_LOG_DIR}"
chmod 0777 "${TEST_TMP_ROOT}/out" "${TEST_ARTIFACT_LOG_DIR}"
checker prepare
cp "${LOGSTASH_DIR}/dist/pipeline/"*.conf "${LOGSTASH_DIR}/dist/sensor_heartbeat.rb" "${TEST_TMP_ROOT}/pipeline/"
checker rewrite "${TEST_TMP_ROOT}/pipeline"
printf '"192.0.2.1": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/iprep.yaml"
printf '"0": "stand-in"\n' > "${TEST_TMP_ROOT}/listbot/cve.yaml"

# nginx: a test certificate for the name Fluent Bit uses as TPOT_HIVE_IP (it is the SENSOR's hive.crt), the
# sensor's access in lswebpasswd, a web user nobody uses
openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:P-384 -sha384 -days 1 -subj "/CN=nginx" \
  -addext "subjectAltName=DNS:nginx" \
  -keyout "${TEST_TMP_ROOT}/cert/nginx.key" -out "${TEST_TMP_ROOT}/cert/nginx.crt" >/dev/null 2>&1 \
  || test_die "Could not create a test certificate with openssl"
cp "${TEST_TMP_ROOT}/cert/nginx.crt" "${TEST_TMP_ROOT}/sensor/data/hive.crt"
SENSOR_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
for pair in "lswebpasswd:${SENSOR_USER}:${SENSOR_PASSWORD}" "nginxpasswd:web-parity:$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"; do
  python3 - "${pair#*:}" > "${TEST_TMP_ROOT}/${pair%%:*}" <<'PY'
import base64, hashlib, sys
user, _, password = sys.argv[1].partition(":")
print(f"{user}:{{SHA}}" + base64.b64encode(hashlib.sha1(password.encode()).digest()).decode())
PY
done
SENSOR_AUTH="$(printf '%s:%s' "${SENSOR_USER}" "${SENSOR_PASSWORD}" | base64 | tr -d '\n')"
cat > "${TEST_TMP_ROOT}/sensor/data/tpot/etc/compose/elk_environment" <<EOF
HONEY_UUID=00000000-0000-4000-8000-000000000000
MY_EXTIP=208.67.222.222
MY_INTIP=172.20.0.1
MY_HOSTNAME=tpot-fixtures
MY_VERSION=test-release
EOF
find "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/listbot" "${TEST_TMP_ROOT}/cert" "${TEST_TMP_ROOT}/sensor/data/tpot" \
  "${TEST_TMP_ROOT}/hive/data" -type d -exec chmod 0755 {} +
find "${TEST_TMP_ROOT}/pipeline" "${TEST_TMP_ROOT}/listbot" "${TEST_TMP_ROOT}/cert" "${TEST_TMP_ROOT}/sensor/data/tpot" \
  -type f -exec chmod 0644 {} +
chmod 0644 "${TEST_TMP_ROOT}/lswebpasswd" "${TEST_TMP_ROOT}/nginxpasswd" "${TEST_TMP_ROOT}/sensor/data/hive.crt"
chmod 0755 "${TEST_TMP_ROOT}/sensor" "${TEST_TMP_ROOT}/sensor/data" "${TEST_TMP_ROOT}/hive"

cat > "${TEST_HARNESS_COMPOSE}" <<EOF
services:
  logstash:
    image: "${IMAGE}"
    container_name: "${TEST_PROJECT_NAME}-logstash"
    hostname: tpot-fixtures
    environment:
      LS_JAVA_OPTS: "-Xms512m -Xmx512m"
      XPACK_GEOIP_DOWNLOADER_ENABLED: "false"
      TZ: UTC
      MY_EXTIP: "208.67.222.222"
      MY_INTIP: "172.20.0.1"
      MY_HOSTNAME: tpot-fixtures
      MY_VERSION: test-release
    # the folder of the dead letter queue input of failures, as the T-Pot entrypoint makes it
    entrypoint: ["/bin/bash", "-c", "mkdir -p /usr/share/logstash/data/dead_letter_queue/process && exec /usr/local/bin/docker-entrypoint --pipeline.workers=2"]
    volumes:
      - "${TEST_TMP_ROOT}/pipeline:/usr/share/logstash/pipeline:ro"
      - "${LOGSTASH_DIR}/dist/pipelines.yml:/usr/share/logstash/config/pipelines.yml:ro"
      - "${TEST_TMP_ROOT}/listbot:/etc/listbot:ro"
      - "${TEST_TMP_ROOT}/hive/data:/data:ro"
      - "${TEST_TMP_ROOT}/out:/out"
  nginx:
    image: "${NGINX_IMAGE}"
    container_name: "${TEST_PROJECT_NAME}-nginx"
    read_only: true
    tmpfs:
      - /var/tmp/nginx/client_body
      - /var/tmp/nginx/proxy
      - /var/tmp/nginx/fastcgi
      - /var/tmp/nginx/uwsgi
      - /var/tmp/nginx/scgi
      - /run
      - /var/lib/nginx/tmp:uid=100,gid=82
    volumes:
      - "${TEST_TMP_ROOT}/cert:/etc/nginx/cert:ro"
      - "${TEST_TMP_ROOT}/nginxpasswd:/etc/nginx/nginxpasswd:ro"
      - "${TEST_TMP_ROOT}/lswebpasswd:/etc/nginx/lswebpasswd:ro"
      - "${TEST_ARTIFACT_LOG_DIR}:/var/log/nginx"
  fluentbit:
    image: "${FLB_IMAGE}"
    container_name: "${TEST_CONTAINER_NAME}"
    read_only: true
    tmpfs: ["/tmp:uid=2000,gid=2000"]
    mem_limit: 512m
    environment:
      TPOT_HIVE_IP: nginx
      TPOT_HIVE_USER: "${SENSOR_AUTH}"
      LS_SSL_VERIFICATION: full
      FLB_PROBE_INTERVAL: "5"
    volumes:
      - "${TEST_TMP_ROOT}/sensor/data:/data"
networks:
  default:
    internal: true
EOF

flb_logs() {
  docker logs "${TEST_CONTAINER_NAME}" 2>&1
}

wait_for_log() {
  # wait_for_log <container> <regex>
  local deadline=$(( SECONDS + WAIT ))
  # no grep -q: with pipefail the SIGPIPE of docker logs would hide a match in a long log
  until docker logs "$1" 2>&1 | grep -E "$2" >/dev/null; do
    (( SECONDS < deadline )) || test_die "${1##*-} logged no line like '$2' within ${WAIT} s"
    sleep 1
  done
}

want() {
  [[ ",${CASES}," == *",$1,"* ]]
}

case_ok() {
  # case_ok <started> <text>
  test_ok "$2 ($(( SECONDS - $1 )) s)"
}

ARCH="$(docker image inspect -f '{{.Architecture}}' "${FLB_IMAGE}")/$(docker image inspect -f '{{.Architecture}}' "${NGINX_IMAGE}")"
test_info "Fluent Bit / nginx ${ARCH}, Logstash $(docker image inspect -f '{{.Architecture}}' "${IMAGE}"); waits up to ${WAIT} s"

# 9: nginx and Fluent Bit first; Fluent Bit waits for the HIVE's sensors pipeline, the lines meanwhile wait on
# the disk; then Logstash
started="${SECONDS}"
test_compose up -d nginx fluentbit >/dev/null 2>&1 || test_die "Could not start nginx and Fluent Bit"
wait_for_log "${TEST_CONTAINER_NAME}" "not ready for this SENSOR format"
checker add sensor cowrie early 20
sleep 10
test_compose up -d logstash >/dev/null 2>&1 || test_die "Could not start Logstash"
wait_for_log "${TEST_CONTAINER_NAME}" "Fluent Bit starts"
checker wait early
case_ok "${started}" "9 lines written before Logstash was ready arrived once"

test_info "The golden documents on both ways"
checker ready 39
checker golden
test_ok "both ways equal the golden file; the SENSOR's events keep their file, the wordpot path and the sensor"

if want 1; then
  started="${SECONDS}"
  checker add hive,sensor ddospot newglob 3 ddospot/log/ntpot.log
  checker add hive,sensor honeypots newglob 3 honeypots/log/ftp.log
  checker wait newglob
  checker logpaths newglob
  case_ok "${started}" "1 globs: new files of ddospot and honeypots arrive on both ways (conpot, ddospot, honeypots in the golden compare)"
fi

if want 2; then
  started="${SECONDS}"
  checker add hive,sensor heralding header 3 "" header
  checker wait header
  checker heralding header
  case_ok "${started}" "2 Heralding header line"
fi

if want 3; then
  started="${SECONDS}"
  checker add sensor cowrie long 3 "" long
  checker wait long
  checker long
  flb_logs | grep -i -E "long line|skip" >/dev/null || test_die "No log line of Fluent Bit names the skipped long line"
  case_ok "${started}" "3 long line skipped: $(flb_logs | grep -i -E 'long line|skip' | head -n 1)"
fi

if want 4; then
  started="${SECONDS}"
  checker add hive,sensor cowrie invalid 1 "" invalid
  checker wait invalid
  checker invalid invalid
  case_ok "${started}" "4 invalid UTF-8 gives the same event on both ways"
fi

if want 5; then
  started="${SECONDS}"
  checker add sensor cowrie bulk 0 "" bulk
  checker wait bulk 10
  if grep -r -h -E "too large body|limiting requests" "${TEST_ARTIFACT_LOG_DIR}" >/dev/null 2>&1; then
    test_die "nginx refused a request: $(grep -r -h -E 'too large body|limiting requests' "${TEST_ARTIFACT_LOG_DIR}" | head -n 1)"
  fi
  case_ok "${started}" "5 3 MB batch: every line once, no 413 / 429"
fi

if want 6; then
  started="${SECONDS}"
  test_compose stop logstash >/dev/null 2>&1 || test_die "Could not stop Logstash"
  stopped="${SECONDS}"
  checker add sensor cowrie outage 20
  sleep 30
  checker add sensor elasticpot outage2 20
  sleep $(( 60 - (SECONDS - stopped) > 0 ? 60 - (SECONDS - stopped) : 0 ))
  outage=$(( SECONDS - stopped ))
  test_compose start logstash >/dev/null 2>&1 || test_die "Could not start Logstash"
  checker wait outage
  checker wait outage2
  case_ok "${started}" "6 Logstash stopped for ${outage} s: no gap, no duplicate"
fi

if want 7; then
  started="${SECONDS}"
  test_compose stop fluentbit >/dev/null 2>&1 || test_die "Could not stop Fluent Bit"
  checker add sensor cowrie flbrestart 10
  test_compose start fluentbit >/dev/null 2>&1 || test_die "Could not start Fluent Bit"
  checker wait flbrestart
  test_compose restart fluentbit >/dev/null 2>&1 || test_die "Could not restart Fluent Bit"
  checker add hive,sensor cowrie flbrestart2 5
  checker wait flbrestart2
  test_compose stop logstash >/dev/null 2>&1 || test_die "Could not stop Logstash"
  checker add hive,sensor cowrie lsrestart 10
  test_compose start logstash >/dev/null 2>&1 || test_die "Could not start Logstash"
  checker wait lsrestart
  test_compose restart logstash >/dev/null 2>&1 || test_die "Could not restart Logstash"
  checker add hive,sensor cowrie lsrestart2 5
  checker wait lsrestart2 15
  checker final
  case_ok "${started}" "7 Fluent Bit restarted alone, Logstash restarted alone: no duplicates, the lines meanwhile arrived"
fi

if want 8; then
  for persistence in on off; do
    started="${SECONDS}"
    test_compose down >/dev/null 2>&1 || test_die "Could not stop the T-Pot of the test"
    checker clean "${persistence}"
    test_compose up -d >/dev/null 2>&1 || test_die "Could not start the T-Pot of the test"
    checker add hive,sensor cowrie "start${persistence}" 5
    checker add hive,sensor heralding "herald${persistence}" 2 "" header
    checker add hive,sensor ddospot "glob${persistence}" 2
    checker wait "start${persistence}"
    checker wait "herald${persistence}"
    checker wait "glob${persistence}" 15
    checker heralding "herald${persistence}"
    checker final
    case_ok "${started}" "8 T-Pot start with TPOT_PERSISTENCE=${persistence}: only the new lines, once"
  done
fi

test_info "The whole run"
checker final
test_ok "sensor_parity: the HIVE's and the SENSOR's way give the same documents"
