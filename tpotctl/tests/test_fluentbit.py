"""The image fluentbit (docker/fluentbit): Fluent Bit on a SENSOR ships the raw log lines of the
honeypots to the HIVE. Its tail inputs come from the log table (tpotctl.logs_config), the pinned
values of its configuration are read here as YAML, its Lua filter runs with the lua of the host
where there is one. The smoke test docker/_tests/tests/fluentbit.sh runs the image against a stub
HIVE (docker/_tests/lib/hive_stub.py)."""

import base64
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tpotctl import logs_config

try:
    import yaml
except ImportError:  # the python3 of the host; the venv has PyYAML
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
IMAGE_DIR = ROOT / "docker" / "fluentbit"
DIST = IMAGE_DIR / "dist"
LUA = shutil.which("luajit") or shutil.which("lua")


def flb(value):
    """A value of the YAML as Fluent Bit reads it: text (YAML reads on / true as bool, 1 as int)."""
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def section(entry):
    return {key: flb(value) for key, value in entry.items()}


@unittest.skipUnless(yaml, "needs PyYAML (the venv of the T-Pot Manager)")
class ConfigTest(unittest.TestCase):

    def load(self, name):
        with open(DIST / name, encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def test_inputs_generated(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(logs_config.main(["--check"]), 0, stderr.getvalue())
        self.assertIn(DIST / "inputs.yaml", logs_config.TARGETS)
        text = (DIST / "inputs.yaml").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(logs_config.HEADER))
        inputs = self.load("inputs.yaml")["pipeline"]["inputs"]
        self.assertEqual(len(inputs), len(logs_config.INPUTS))
        # one offsets db per input: at its init a tail input deletes the rows of every inode it does not
        # watch, so a shared db would lose the offsets of the others on a restart of the container
        dbs = [tail["db"] for tail in inputs]
        self.assertEqual(len(set(dbs)), len(dbs), dbs)
        for db in dbs:
            self.assertRegex(db, r"\A/data/fluentbit/offsets\.db\.[a-z0-9_-]+\Z")
        self.assertEqual(dbs[0], "/data/fluentbit/offsets.db.suricata")
        self.assertIn("/data/fluentbit/offsets.db.go-pot", dbs)
        for entry, tail in zip(logs_config.INPUTS, inputs):
            with self.subTest(type=entry.type):
                self.assertEqual(section(tail), {
                    "name": "tail", "tag": f"tpot.{entry.type}", "path": ",".join(entry.paths),
                    "path_key": "t-pot_log_path", "key": "log",
                    "read_from_head": "on", "db": tail["db"], "db.locking": "on",
                    "skip_long_lines": "on", "buffer_chunk_size": "64k", "buffer_max_size": "1M",
                    "refresh_interval": "10", "storage.type": "filesystem"})

    def test_config_pins(self):
        config = self.load("fluent-bit.yaml")
        self.assertEqual(section(config["service"]), {
            "flush": "1", "http_server": "on", "http_listen": "127.0.0.1", "http_port": "2020",
            "storage.path": "/data/fluentbit/storage", "json.escape_unicode": "off"})
        self.assertEqual(config["includes"], ["inputs.yaml"])
        pipeline = config["pipeline"]
        # flush_on_startup emits before storage_backlog takes the chunks, which then frees the heartbeat's
        # chunk twice (v5.1.5, v5.2.0): a one-shot dummy gives the first heartbeat instead
        heartbeat = {"name": "dummy", "tag": "tpot_heartbeat", "dummy": '{"type":"TPotSensorHeartbeat"}',
                     "storage.type": "memory", "mem_buf_limit": "1M"}
        self.assertEqual([section(entry) for entry in pipeline["inputs"]], [
            {**heartbeat, "interval_sec": "60"}, {**heartbeat, "interval_sec": "2", "samples": "1"}])
        for entry in pipeline["inputs"]:
            self.assertEqual(json.loads(entry["dummy"]), {"type": "TPotSensorHeartbeat"})
            self.assertNotIn("flush_on_startup", entry)
        self.assertEqual([section(entry) for entry in pipeline["filters"]], [
            {"name": "lua", "match": "tpot.*", "script": "/etc/fluent-bit/tpot.lua", "call": "tag_record"},
            {"name": "lua", "match": "tpot_heartbeat", "script": "/etc/fluent-bit/tpot.lua", "call": "heartbeat"}])
        outputs = pipeline["outputs"]
        self.assertEqual(len(outputs), 1)
        output = dict(outputs[0])
        self.assertEqual(output.pop("header"), ["Authorization Basic ${TPOT_HIVE_USER}", "X-TPot-Ingest 2"])
        # warn on the output only: no info lines per flush (HTTP status=200, ok), the warnings of the tail
        # inputs (skipped long lines) and of the storage stay
        self.assertEqual(section(output), {
            "name": "http", "match": "*", "log_level": "warn", "host": "${TPOT_HIVE_IP}", "port": "${TPOT_HIVE_PORT}", "uri": "/",
            "format": "json", "json_date_key": "@timestamp", "json_date_format": "iso8601", "compress": "gzip",
            "tls": "on", "tls.verify": "${FLB_TLS_VERIFY}", "tls.verify_hostname": "${FLB_TLS_VERIFY}",
            "tls.ca_file": "${FLB_CA_FILE}", "tls.vhost": "${TPOT_HIVE_IP}",
            "retry_limit": "no_limits", "storage.total_limit_size": "${FLB_STORAGE_LIMIT}", "workers": "1"})

    def test_image_pins(self):
        dockerfile = (IMAGE_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"^FROM (\S+)", dockerfile, re.M), ["debian:trixie-slim"] * 2)
        self.assertIn("C3C0A28534B9293EAF51FABD9F9DDC083888C1CD", dockerfile)
        self.assertIn("signed-by=", dockerfile)
        self.assertIn("https://packages.fluentbit.io/debian/trixie trixie main", dockerfile)
        self.assertRegex(dockerfile, r"fluent-bit=\$\{FLB_VERSION\}")
        self.assertRegex(dockerfile, r"(?m)^ARG FLB_VERSION=5\.2\.0$")
        self.assertRegex(dockerfile, r"(?m)^USER 2000:2000$")
        self.assertRegex(dockerfile, r"(?m)^HEALTHCHECK CMD test -f /tmp/tpot-waiting \|\| "
                                     r"curl -fs http://127\.0\.0\.1:2020/api/v1/uptime$")
        # gnupg checks the key in the first stage only
        final = dockerfile.rsplit("\nFROM ", 1)[1]
        self.assertNotIn("gnupg", final)
        # the entrypoint decides the trust file and the TLS verdict with openssl, named, not left to curl's deps
        self.assertIn("apt-get install -y --no-install-recommends ca-certificates curl openssl &&", final)
        with open(IMAGE_DIR / "docker-compose.yml", encoding="utf-8") as handle:
            service = yaml.safe_load(handle)["services"]["fluentbit"]
        version = (ROOT / "version").read_text(encoding="utf-8").strip()
        self.assertEqual(service["image"], f"ghcr.io/telekom-security/fluentbit:{version}")
        self.assertTrue(service["read_only"])
        self.assertEqual(service["mem_limit"], "512m")
        self.assertIn("/tmp", service["tmpfs"][0])
        for name in ("sensor.yml", "tpot_services.yml"):
            with open(ROOT / "compose" / name, encoding="utf-8") as handle:
                self.assertEqual(yaml.safe_load(handle)["services"]["fluentbit"]["mem_limit"], "512m", name)

    def test_entrypoint(self):
        text = (DIST / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("/data/tpot/etc/compose/elk_environment", text)
        self.assertIn("set -o allexport", text)
        for line in ('TPOT_HIVE_PORT="${TPOT_HIVE_PORT:-64294}"', 'FLB_STORAGE_LIMIT="${FLB_STORAGE_LIMIT:-512M}"'):
            self.assertIn(line, text)
        self.assertRegex(text, r"(?m)^exec fluent-bit -c /etc/fluent-bit/fluent-bit\.yaml$")
        # C2: the storage limit comes from TPOT_SENSOR_BUFFER_MB, a wrong one stops the start
        self.assertIn("^[1-9][0-9]*[KMG]?$", text)
        # tpotinit takes leading zeros (0512): they go before the check (the smoke test runs 0512M)
        self.assertLess(text.index("^0+([0-9][0-9]*[KMG]?)$"), text.index("^[1-9][0-9]*[KMG]?$"))
        self.assertRegex(text, r"FLB_STORAGE_LIMIT is .*TPOT_SENSOR_BUFFER_MB")
        # E2: the probe of the HIVE before Fluent Bit starts (ProbeTest and the smoke test run every answer)
        self.assertLess(text.index("\nfuPROBE\n"), text.index("\nexec fluent-bit"))
        for message in ("The HIVE takes this SENSOR's format; Fluent Bit starts.",
                        "The HIVE does not take this SENSOR's format yet (update the HIVE first); logs wait here.",
                        "The HIVE does not accept this SENSOR's login (TPOT_HIVE_USER); tpot sensors list on the HIVE "
                        "shows its access; logs wait here.",
                        "The HIVE's Logstash pipeline is not ready for this SENSOR format; logs wait here.",
                        "The HIVE is not reachable (HTTP ${myCODE}).",
                        "The HIVE is not reachable (${myERROR:-no answer})."):
            self.assertIn(message, text)
        # C4 / I1: a busy HIVE (its limit per address of /tpot-ingest) has its own line, before the generic one
        busy = "The HIVE is busy (HTTP 429), next probe in ${myWAIT} s."
        self.assertIn(busy, text)
        self.assertLess(text.index(busy), text.index("The HIVE is not reachable (HTTP"))
        self.assertIn("myWAIT=$((15 + RANDOM % 31))", text)
        self.assertIn("https://${TPOT_HIVE_IP}:${TPOT_HIVE_PORT}/tpot-ingest", text)
        self.assertIn('"${FLB_PROBE_INTERVAL:-300}"', text)
        # the login goes to curl from a file, never in its argv
        self.assertIn("-H @", text)
        self.assertIn('"${FLB_PROBE_DIR:-/tmp}"', text)
        # the trust file of Fluent Bit and the probe (ProbeTest runs the rule and the verdict)
        self.assertIn('"${TPOT_HIVE_CRT:-/data/hive.crt}"', text)
        self.assertIn('"${FLB_SYSTEM_CA:-/etc/ssl/certs/ca-certificates.crt}"', text)
        self.assertRegex(text, r"(?m)^export .*\bFLB_CA_FILE\b")
        self.assertIn("timeout 10 openssl s_client", text)
        # Fluent Bit takes no partial chain: neither may the rule or the verdict
        self.assertNotIn("partial_chain", text)
        for message in ("The HIVE's certificate is not trusted (${myREASON}); on the HIVE, tpot sensors cert "
                        "--distribute copies its certificate here (after --renew if it expired).",
                        "The HIVE's certificate is not trusted (${myREASON}); with a certificate of your own CA, "
                        "append the root of that CA to nginx.crt on the HIVE, then run tpot sensors cert "
                        "--distribute there."):
            self.assertIn(message, text)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class ProbeTest(unittest.TestCase):
    """E2 / I1: the entrypoint probes /tpot-ingest of the HIVE in two stages each round, without the
    SENSOR's login and, only after a 401 with X-TPot-Ingest login, with it. It runs with the bash of PATH
    (it stays bash 3.2 compatible) and stubs: curl answers stage 1 (no -H) from answer-1 and stage 2 from
    answer-2 (one line per call, "CODE [MARK]", the last line stays), sleep records its seconds and sends
    TERM to the entrypoint at its `sleeps`-th call, fluent-bit records the files of FLB_PROBE_DIR and its
    trust file. Before the stages each round picks the trust file (hive.crt pinned when one certificate of
    it verifies itself, else the system store) and, with LS_SSL_VERIFICATION=full, asks openssl s_client
    for the TLS verdict: openssl verify passes a PEM block that says self (with the options of the rule
    only), s_client prints the line of s_client to stderr and fails with it (no file: verified), timeout
    runs the rest."""

    AUTH = base64.b64encode(b"sensor-x:secret").decode()
    OLD = "The HIVE does not take this SENSOR's format yet (update the HIVE first); logs wait here."
    START = "The HIVE takes this SENSOR's format; Fluent Bit starts."
    LOGIN = ("The HIVE does not accept this SENSOR's login (TPOT_HIVE_USER); tpot sensors list on the HIVE "
             "shows its access; logs wait here.")
    PIPELINE = "The HIVE's Logstash pipeline is not ready for this SENSOR format; logs wait here."
    BUSY = re.compile(r"The HIVE is busy \(HTTP 429\), next probe in (\d+) s\.")
    PINNED = ("The HIVE's certificate is not trusted ({}); on the HIVE, tpot sensors cert --distribute copies its "
              "certificate here (after --renew if it expired).")
    SYSTEM = ("The HIVE's certificate is not trusted ({}); with a certificate of your own CA, append the root of "
              "that CA to nginx.crt on the HIVE, then run tpot sensors cert --distribute there.")
    SELF = "-----BEGIN CERTIFICATE-----\nself\n-----END CERTIFICATE-----\n"
    LEAF = "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n"

    CURL = """#!{python}
import json, os, sys
stub = {stub!r}
args = sys.argv[1:]
call = {{"argv": args, "header": None}}
headers = None
stage = "1"
for index, arg in enumerate(args[:-1]):
    if arg == "-D":
        headers = args[index + 1]
    elif arg == "-H":
        stage = "2"
        call["header"] = args[index + 1]
        if args[index + 1].startswith("@"):
            path = args[index + 1][1:]
            try:
                with open(path, encoding="utf-8") as handle:
                    call["auth"] = handle.read()
                call["mode"] = os.stat(path).st_mode & 0o777
            except OSError as error:
                call["auth"] = str(error)
with open(os.path.join(stub, "curl.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(call) + "\\n")
answers = os.path.join(stub, "answer-" + stage)
with open(answers, encoding="utf-8") as handle:
    lines = handle.read().splitlines()
if len(lines) > 1:
    with open(answers, "w", encoding="utf-8") as handle:
        handle.write("\\n".join(lines[1:]) + "\\n")
code, _, mark = lines[0].partition(" ")
with open(headers, "w", encoding="utf-8") as handle:
    if os.path.exists(os.path.join(stub, "proxy")) and "--suppress-connect-headers" not in args:
        handle.write("HTTP/1.1 200 Connection established\\r\\nX-TPot-Ingest: login\\r\\n\\r\\n")
    handle.write("HTTP/1.1 " + code + " Stub\\r\\n" + ("X-TPot-Ingest: " + mark + "\\r\\n" if mark else "") + "\\r\\n")
"""

    SLEEP = """#!{python}
import json, os, signal, sys
stub, probe = {stub!r}, {probe!r}
with open(os.path.join(stub, "sleep.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"seconds": sys.argv[1], "files": sorted(os.listdir(probe))}}) + "\\n")
with open(os.path.join(stub, "sleep.jsonl"), encoding="utf-8") as handle:
    count = len(handle.read().splitlines())
with open(os.path.join(stub, "sleeps"), encoding="utf-8") as handle:
    if count >= int(handle.read()):
        os.kill(os.getppid(), signal.SIGTERM)
"""

    FLUENT_BIT = """#!{python}
import json, os, sys
stub, probe = {stub!r}, {probe!r}
with open(os.path.join(stub, "fluent-bit.json"), "w", encoding="utf-8") as handle:
    json.dump({{"argv": sys.argv[1:], "files": sorted(os.listdir(probe)), "ca_file": os.environ.get("FLB_CA_FILE")}},
              handle)
"""

    OPENSSL = """#!{python}
import json, os, sys
stub = {stub!r}
args = sys.argv[1:]
call = {{"argv": args}}
if args[:1] == ["verify"]:
    try:
        with open(args[-1], encoding="utf-8") as handle:
            call["text"] = handle.read()
    except OSError as error:
        call["text"] = str(error)
with open(os.path.join(stub, "openssl.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(call) + "\\n")
if args[:1] == ["verify"]:
    rule = {{"-no_check_time", "-no-CApath", "-no-CAstore"}} <= set(args) and "-partial_chain" not in args
    anchor = "-CAfile" in args and args[args.index("-CAfile") + 1] == args[-1]
    sys.exit(0 if rule and anchor and "self" in call["text"] else 2)
if args[:1] == ["s_client"]:
    line = ""
    if os.path.exists(os.path.join(stub, "s_client")):
        with open(os.path.join(stub, "s_client"), encoding="utf-8") as handle:
            line = handle.read().strip()
    if not line:
        print("Verify return code: 0 (ok)")
        sys.exit(0)
    print(line, file=sys.stderr)
    sys.exit(1)
sys.exit(3)
"""

    TIMEOUT = """#!{python}
import json, os, sys
stub = {stub!r}
with open(os.path.join(stub, "timeout.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"argv": sys.argv[1:]}}) + "\\n")
os.execvp(sys.argv[2], sys.argv[2:])
"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.stub = self.folder / "stub"
        self.probe = self.folder / "probe"
        self.bin = self.folder / "bin"
        self.hive_crt = self.folder / "hive.crt"
        self.system_ca = self.folder / "ca-certificates.crt"
        for path in (self.stub, self.probe, self.bin):
            path.mkdir()
        values = {"python": sys.executable, "stub": str(self.stub), "probe": str(self.probe)}
        for name, text in (("curl", self.CURL), ("sleep", self.SLEEP), ("fluent-bit", self.FLUENT_BIT),
                           ("openssl", self.OPENSSL), ("timeout", self.TIMEOUT)):
            (self.bin / name).write_text(text.format(**values), encoding="utf-8")
            (self.bin / name).chmod(0o755)

    def records(self, name):
        path = self.stub / name
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def run_probe(self, stage1, stage2=("500",), sleeps=1, **env):
        """The entrypoint with these answers; (result, curl calls, sleeps, what fluent-bit saw or None)."""
        (self.stub / "answer-1").write_text("\n".join(stage1) + "\n", encoding="utf-8")
        (self.stub / "answer-2").write_text("\n".join(stage2) + "\n", encoding="utf-8")
        (self.stub / "sleeps").write_text(str(sleeps), encoding="utf-8")
        environ = {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                   "TPOT_HIVE_IP": "192.0.2.10", "TPOT_HIVE_USER": self.AUTH, "FLB_PROBE_DIR": str(self.probe),
                   "TPOT_HIVE_CRT": str(self.hive_crt),
                   "FLB_SYSTEM_CA": str(self.system_ca), "LS_SSL_VERIFICATION": "none", **env}
        result = subprocess.run(["bash", str(DIST / "entrypoint.sh")], env=environ, capture_output=True,
                                text=True, timeout=60, check=False)
        started = self.stub / "fluent-bit.json"
        return (result, self.records("curl.jsonl"), self.records("sleep.jsonl"),
                json.loads(started.read_text(encoding="utf-8")) if started.exists() else None)

    def verify_tls(self, stage1, stage2=("500",), crt=None, s_client=None, **env):
        """run_probe with LS_SSL_VERIFICATION=full, hive.crt of these PEM blocks (None: no file) and the line
        of s_client (None: verified); also the s_client calls (the argv after timeout and openssl)."""
        for name in ("curl.jsonl", "sleep.jsonl", "fluent-bit.json", "openssl.jsonl", "timeout.jsonl", "s_client"):
            (self.stub / name).unlink(missing_ok=True)
        self.hive_crt.unlink(missing_ok=True)
        if crt is not None:
            self.hive_crt.write_text(crt, encoding="utf-8")
        if s_client is not None:
            (self.stub / "s_client").write_text(s_client + "\n", encoding="utf-8")
        result, calls, sleeps, started = self.run_probe(stage1, stage2, LS_SSL_VERIFICATION="full", **env)
        verdicts = [call["argv"][2:] for call in self.records("timeout.jsonl")]
        self.assertEqual([call["argv"][:3] for call in self.records("timeout.jsonl")],
                         [["10", "openssl", "s_client"]] * len(verdicts))
        self.assertEqual(len(verdicts), len([call for call in self.records("openssl.jsonl")
                                             if call["argv"][:1] == ["s_client"]]))
        return result, calls, sleeps, started, verdicts

    @staticmethod
    def option(argv, name):
        """The value after option NAME in an argv (None without it)."""
        return argv[argv.index(name) + 1] if name in argv else None

    def test_a_hive_of_this_release_starts_fluent_bit(self):
        result, calls, sleeps, started = self.run_probe(["401 login"], ["204 2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.START, result.stdout)
        self.assertEqual(started["argv"], ["-c", "/etc/fluent-bit/fluent-bit.yaml"])
        self.assertNotIn("tpot-waiting", started["files"])
        self.assertEqual([call["header"] for call in calls], [None, "@" + str(self.probe / "tpot-probe.auth")])
        self.assertNotIn("-H", calls[0]["argv"])
        self.assertEqual(sleeps, [])

    def test_an_old_hive_never_gets_the_login(self):
        result, calls, sleeps, started = self.run_probe(["401"], ["204 2"], sleeps=2)
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertEqual(result.stderr.count(self.OLD), 2, result.stderr)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertNotIn("-H", call["argv"])
        self.assertEqual([sleep["seconds"] for sleep in sleeps], ["300", "300"])
        self.assertIsNone(started)

    def test_a_refused_login_waits_and_stays_healthy(self):
        result, calls, sleeps, started = self.run_probe(["401 login"], ["401 login"])
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn(self.LOGIN, result.stderr)
        self.assertEqual([sleep["seconds"] for sleep in sleeps], ["300"])
        self.assertIn("tpot-waiting", sleeps[0]["files"])
        self.assertTrue((self.probe / "tpot-waiting").exists())
        self.assertEqual(len(calls), 2)
        self.assertIsNone(started)
        # the stop takes the login along
        self.assertFalse((self.probe / "tpot-probe.auth").exists())

    def test_a_pipeline_not_ready_waits(self):
        result, _calls, sleeps, started = self.run_probe(["401 login"], ["503"])
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn(self.PIPELINE, result.stderr)
        self.assertEqual([sleep["seconds"] for sleep in sleeps], ["300"])
        self.assertIsNone(started)

    def test_a_busy_hive_is_asked_again_soon(self):
        for interval, low, high in (("300", 15, 45), ("5", 5, 5)):
            with self.subTest(interval=interval):
                for name in ("curl.jsonl", "sleep.jsonl", "fluent-bit.json"):
                    (self.stub / name).unlink(missing_ok=True)
                # round 1: 429 at stage 1, round 2: 429 at stage 2, round 3: the HIVE takes the format
                result, calls, sleeps, started = self.run_probe(["429", "401 login"], ["429", "204 2"], sleeps=3,
                                                                FLB_PROBE_INTERVAL=interval)
                self.assertEqual(result.returncode, 0, result.stderr)
                waits = [int(found) for found in self.BUSY.findall(result.stderr)]
                self.assertEqual(waits, [int(sleep["seconds"]) for sleep in sleeps])
                self.assertEqual(len(waits), 2, result.stderr)
                for wait in waits:
                    self.assertGreaterEqual(wait, low)
                    self.assertLessEqual(wait, high)
                self.assertEqual([call["header"] is not None for call in calls], [False, False, True, False, True])
                self.assertIn(self.START, result.stdout)
                self.assertIsNotNone(started)

    def test_any_other_answer_is_not_reachable(self):
        for stage1, stage2, code, logins in ((["204 2"], ["204 2"], "204", 0), (["401 login"], ["500"], "500", 1)):
            with self.subTest(stage1=stage1, stage2=stage2):
                for name in ("curl.jsonl", "sleep.jsonl"):
                    (self.stub / name).unlink(missing_ok=True)
                result, calls, sleeps, started = self.run_probe(stage1, stage2)
                self.assertEqual(result.returncode, 143, result.stderr)
                self.assertIn(f"The HIVE is not reachable (HTTP {code}).", result.stderr)
                self.assertEqual(sum(call["header"] is not None for call in calls), logins)
                self.assertEqual([sleep["seconds"] for sleep in sleeps], ["300"])
                self.assertIsNone(started)

    def test_a_proxy_marker_never_gets_the_login(self):
        # an HTTPS proxy's CONNECT reply is no answer of the HIVE: its X-TPot-Ingest must not open stage 2
        (self.stub / "proxy").write_text("", encoding="utf-8")
        result, calls, _sleeps, started = self.run_probe(["401"], ["204 2"])
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn(self.OLD, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["header"])
        self.assertIsNone(started)

    def test_the_login_is_never_in_the_argv_and_gone_before_the_start(self):
        result, calls, _sleeps, started = self.run_probe(["401 login"], ["204 2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        for call in calls:
            self.assertFalse([arg for arg in call["argv"] if self.AUTH in arg], call["argv"])
        self.assertEqual(calls[1]["auth"], f"Authorization: Basic {self.AUTH}\n")
        self.assertEqual(oct(calls[1]["mode"]), oct(0o600))
        self.assertNotIn("tpot-probe.auth", started["files"])
        self.assertFalse((self.probe / "tpot-probe.auth").exists())

    def test_a_self_signed_certificate_is_pinned(self):
        # the HIVE's own certificate, or a leaf with the root of its CA appended: one block verifies itself
        for crt, address, check in ((self.SELF, "192.0.2.10", ["-verify_ip", "192.0.2.10"]),
                                    (self.LEAF + self.SELF, "hive.example",
                                     ["-servername", "hive.example", "-verify_hostname", "hive.example"])):
            with self.subTest(blocks=crt.count("BEGIN"), address=address):
                result, calls, _sleeps, started, verdicts = self.verify_tls(["401 login"], ["204 2"], crt=crt,
                                                                            TPOT_HIVE_IP=address)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([self.option(call["argv"], "--cacert") for call in calls], [str(self.hive_crt)] * 2)
                self.assertEqual(len(verdicts), 1)
                verdict = verdicts[0]
                self.assertEqual(self.option(verdict, "-CAfile"), str(self.hive_crt))
                self.assertEqual(self.option(verdict, "-connect"), f"{address}:64294")
                for flag in ("-no-CApath", "-no-CAstore", "-verify_return_error"):
                    self.assertIn(flag, verdict)
                self.assertEqual([arg for arg in verdict if arg in ("-verify_ip", "-servername", "-verify_hostname")],
                                 check[::2])
                for flag, value in zip(check[::2], check[1::2]):
                    self.assertEqual(self.option(verdict, flag), value)
                self.assertEqual(started["ca_file"], str(self.hive_crt))
                # the rule asks openssl verify for each block alone, the split files are gone again
                checked = [call for call in self.records("openssl.jsonl") if call["argv"][:1] == ["verify"]]
                self.assertTrue(checked)
                for call in checked:
                    self.assertEqual(call["text"].count("-----BEGIN CERTIFICATE-----"), 1, call)
                    self.assertNotEqual(call["argv"][-1], str(self.hive_crt))
                self.assertEqual(started["files"], [])

    def test_a_ca_certificate_uses_the_system_store(self):
        # a leaf issued by a CA (and its intermediate): no block verifies itself, Fluent Bit takes the system store
        result, calls, _sleeps, started, verdicts = self.verify_tls(["401 login"], ["204 2"],
                                                                    crt=self.LEAF + self.LEAF)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([self.option(call["argv"], "--cacert") for call in calls], [str(self.system_ca)] * 2)
        self.assertEqual([self.option(verdict, "-CAfile") for verdict in verdicts], [str(self.system_ca)])
        self.assertEqual(started["ca_file"], str(self.system_ca))
        self.assertEqual(started["files"], [])

    def test_without_hive_crt_the_system_store(self):
        result, calls, _sleeps, started, verdicts = self.verify_tls(["401 login"], ["204 2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([self.option(call["argv"], "--cacert") for call in calls], [str(self.system_ca)] * 2)
        self.assertEqual([self.option(verdict, "-CAfile") for verdict in verdicts], [str(self.system_ca)])
        self.assertEqual(started["ca_file"], str(self.system_ca))
        self.assertEqual([call for call in self.records("openssl.jsonl") if call["argv"][:1] == ["verify"]], [])

    def test_an_expired_self_signed_certificate_stays_pinned(self):
        # openssl verify of the rule passes only with -no_check_time (the stub checks it): an expired
        # certificate of the HIVE stays its anchor and the verdict names the reason
        result, calls, sleeps, started, verdicts = self.verify_tls(
            ["401 login"], ["204 2"], crt=self.SELF, s_client="verify error:num=10:certificate has expired")
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn(self.PINNED.format("certificate has expired"), result.stderr)
        self.assertEqual([self.option(verdict, "-CAfile") for verdict in verdicts], [str(self.hive_crt)])
        self.assertEqual(calls, [])
        self.assertEqual([sleep["seconds"] for sleep in sleeps], ["300"])
        self.assertIn("tpot-waiting", sleeps[0]["files"])
        self.assertIsNone(started)

    def test_a_ca_certificate_that_fails_names_the_root(self):
        result, calls, sleeps, started, verdicts = self.verify_tls(
            ["401 login"], ["204 2"], crt=self.LEAF,
            s_client="verify error:num=20:unable to get local issuer certificate")
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn(self.SYSTEM.format("unable to get local issuer certificate"), result.stderr)
        self.assertNotIn("(after --renew if it expired)", result.stderr)
        self.assertEqual([self.option(verdict, "-CAfile") for verdict in verdicts], [str(self.system_ca)])
        self.assertEqual(calls, [])
        self.assertEqual([sleep["seconds"] for sleep in sleeps], ["300"])
        self.assertIsNone(started)

    def test_s_client_without_a_verdict_goes_on_to_curl(self):
        # no verify error (no answer, a refused connection, the timeout): curl names what it finds
        result, calls, _sleeps, started, verdicts = self.verify_tls(["401 login"], ["204 2"], crt=self.SELF,
                                                                    s_client="connect:errno=111")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(len(calls), 2)
        self.assertIn(self.START, result.stdout)
        self.assertNotIn("is not trusted", result.stderr)
        self.assertIsNotNone(started)

    def test_verification_none_asks_no_openssl(self):
        self.hive_crt.write_text(self.SELF, encoding="utf-8")
        result, calls, _sleeps, started = self.run_probe(["401 login"], ["204 2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["argv"].count("-k") for call in calls], [1, 1])
        for call in calls:
            self.assertNotIn("--cacert", call["argv"])
        self.assertEqual([call for call in self.records("openssl.jsonl") if call["argv"][:1] == ["s_client"]], [])
        self.assertEqual(self.records("timeout.jsonl"), [])
        self.assertIsNotNone(started)


@unittest.skipUnless(LUA, "needs lua or luajit on PATH (the smoke test runs the filter in Fluent Bit)")
class LuaTest(unittest.TestCase):

    HARNESS = r"""
dofile(arg[1])
local function show(label, code, ts, record)
  local keys = {}
  for key in pairs(record) do keys[#keys + 1] = key end
  table.sort(keys)
  for _, key in ipairs(keys) do
    local value = record[key]
    if type(value) == "table" then
      local inner = {}
      for name in pairs(value) do inner[#inner + 1] = name end
      table.sort(inner)
      for _, name in ipairs(inner) do
        print(label .. "\t" .. key .. "." .. name .. "\t" .. tostring(value[name]))
      end
    else
      print(label .. "\t" .. key .. "\t" .. tostring(value))
    end
  end
  print(label .. "\tcode\t" .. tostring(code) .. "\t" .. tostring(ts))
end
show("tag", tag_record("tpot.Go-pot", 1.5, {log = "line", ["t-pot_log_path"] = "/data/go-pot/log/go-pot.json"}))
show("beat1", heartbeat("tpot_heartbeat", 2.5, {type = "TPotSensorHeartbeat"}))
show("beat2", heartbeat("tpot_heartbeat", 3.5, {type = "TPotSensorHeartbeat"}))
"""

    def run_lua(self, environ):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        harness = Path(folder.name) / "harness.lua"
        harness.write_text(self.HARNESS, encoding="utf-8")
        result = subprocess.run([LUA, str(harness), str(DIST / "tpot.lua")], env=environ,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = {}
        for line in result.stdout.splitlines():
            label, key, value = line.split("\t")[:3]
            lines.setdefault(label, {})[key] = value
        return lines

    def test_lua_sets_fields(self):
        environ = {"MY_HOSTNAME": "sensor-1", "MY_EXTIP": "198.51.100.7", "MY_INTIP": "10.0.0.7",
                   "MY_VERSION": "24.04.2", "TPOT_FLUENTBIT_START": "2026-11-17T21:00:00Z"}
        found = self.run_lua(environ)
        stamped = {"t-pot_hostname": "sensor-1", "t-pot_ip_ext": "198.51.100.7", "t-pot_ip_int": "10.0.0.7"}
        self.assertEqual(found["tag"], {"log": "line", "t-pot_log_path": "/data/go-pot/log/go-pot.json",
                                        "type": "Go-pot", "code": "2", **stamped})
        for number, label in ((1, "beat1"), (2, "beat2")):
            with self.subTest(heartbeat=number):
                self.assertEqual(found[label], {
                    "type": "TPotSensorHeartbeat", "code": "2", **stamped, "heartbeat.sequence": str(number),
                    "heartbeat.started_at": "2026-11-17T21:00:00Z", "heartbeat.version": "24.04.2"})

    def test_lua_without_values(self):
        # no value of elk_environment: no empty field (an empty t-pot_ip_ext is no address); an unknown
        # version goes as empty text, the HIVE decides
        found = self.run_lua({"TPOT_FLUENTBIT_START": "2026-11-17T21:00:00Z"})
        self.assertEqual(found["tag"], {"log": "line", "t-pot_log_path": "/data/go-pot/log/go-pot.json",
                                        "type": "Go-pot", "code": "2"})
        self.assertEqual(found["beat1"]["heartbeat.version"], "")
        self.assertNotIn("t-pot_hostname", found["beat1"])


class TpotinitShipperTest(unittest.TestCase):
    """E9: tpotinit warns about a SENSOR whose compose file still runs Logstash, or no fluentbit; the start goes on."""

    E9 = ("# Warning: This SENSOR still runs Logstash: the HIVE refuses its events until docker-compose.yml uses "
          "fluentbit (tpot customize or update.sh --adapt-compose).")
    NONE = "# Warning: This SENSOR runs no fluentbit service: no logs are sent to the HIVE."
    TPOTINIT = ROOT / "docker" / "tpotinit" / "dist"

    def test_entrypoint_warns_for_a_sensor_and_goes_on(self):
        text = (self.TPOTINIT / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertRegex(text, r'(?m)^if \[ "\$\{TPOT_TYPE\}" == "SENSOR" \];\n  then\n    fuSENSOR_SHIPPER\nfi$')
        self.assertLess(text.index("if ! fuENV_REPORT;"), text.index("    fuSENSOR_SHIPPER\n"))

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_warnings(self):
        script = self.TPOTINIT / "bin" / "env_validate.sh"
        for services, expected in ((["logstash"], self.E9), (["fluentbit"], ""), (["cowrie"], self.NONE),
                                   (["fluentbit", "logstash"], self.E9)):
            with self.subTest(services=services), tempfile.TemporaryDirectory() as folder:
                compose = Path(folder) / "docker-compose.yml"
                compose.write_text("services:\n" + "".join(f"  {s}:\n    image: x\n" for s in services),
                                   encoding="utf-8")
                result = subprocess.run([shutil.which("bash"), "-c", 'source "$1"; fuSENSOR_SHIPPER', "x", str(script)],
                                        env={"PATH": os.environ.get("PATH", ""), "COMPOSE": str(compose)},
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)


class OutageCountTest(unittest.TestCase):
    """Case 13 of the smoke test after the outage: a chunk Fluent Bit retried during it waits up to 2001 s
    for its next retry (scheduler.cap, 2000 s by default), so the check waits while Fluent Bit holds chunks
    and calls lines lost only when it holds none. The checker of fluentbit.sh runs with a clock of its own,
    docker is a stub that lists the chunk files of the file `chunks`."""

    SMOKE = ROOT / "docker" / "_tests" / "tests" / "fluentbit.sh"
    CONTAINER = "tpot-smoke-fluentbit"
    MANIFEST = {"/data/cowrie/log/cowrie.json": ["flood-00000", "flood-00002"],
                "/data/dionaea/log/dionaea.json": ["flood-00001"]}

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.run_dir = self.folder / "outage"
        (self.run_dir / "stub").mkdir(parents=True)
        self.manifest = self.folder / "flood.json"
        self.manifest.write_text(json.dumps(self.MANIFEST), encoding="utf-8")
        self.chunks = self.folder / "chunks"
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            f'[ "$*" = "exec {self.CONTAINER} find /data/fluentbit/storage -type f -name *.flb" ] || exit 1\n'
            f'cat "{self.chunks}" || exit 1\n', encoding="utf-8")
        docker.chmod(0o755)

    def hold(self, count):
        self.chunks.write_text("".join(f"/data/fluentbit/storage/tail.{n}/1-1.{n}.flb\n" for n in range(count)),
                               encoding="utf-8")

    def deliver(self, *tokens):
        """A request as hive_stub.py records it, answered 200."""
        body = json.dumps([{"log": json.dumps({"token": token, "pad": "x" * 64}), "type": "Cowrie"}
                           for token in tokens])
        record = {"time": 0, "method": "POST", "path": "/", "headers": {"x-tpot-ingest": "2"}, "status": 200,
                  "gzip": True, "body": body}
        with open(self.run_dir / "stub" / "requests.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def run_check(self, events=(), timeout="120"):
        """outage-count of the checker; EVENTS: (seconds after its start, action). Returns the exit code,
        the seconds it ran, stdout and stderr."""
        source = self.SMOKE.read_text(encoding="utf-8").split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        body, call = source.rsplit("\n", 1)
        self.assertTrue(call.startswith("COMMANDS[sys.argv[1]]"), call)
        clock = [0.0]
        pending = sorted(events, key=lambda event: event[0])

        def sleep(seconds):
            clock[0] += seconds
            while pending and pending[0][0] <= clock[0]:
                pending.pop(0)[1]()

        env = {"SMOKE_HOSTNAME": "sensor-smoke", "SMOKE_EXTIP": "198.51.100.7", "SMOKE_INTIP": "10.0.0.7",
               "SMOKE_VERSION": "24.04.2", "SMOKE_AUTH": "eA==", "SMOKE_TIMEOUT": timeout,
               "SMOKE_CONTAINER": self.CONTAINER, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        namespace = {"__name__": "check"}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(body, str(self.SMOKE), "exec"), namespace)  # noqa: S102 - the checker of the smoke test
            namespace["time"] = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
            try:
                namespace["outage_count"](str(self.run_dir), str(self.manifest), timeout)
                code = 0
            except SystemExit as stop:
                code = stop.code
        return code, clock[0], out.getvalue(), err.getvalue()

    def test_waits_while_fluent_bit_holds_chunks(self):
        self.deliver("flood-00000", "flood-00001")
        self.hold(1)
        code, took, out, err = self.run_check([(1500, lambda: (self.deliver("flood-00002"), self.hold(0)))])
        self.assertEqual(code, 0, err)
        self.assertGreaterEqual(took, 1500)
        self.assertIn("all 3 lines arrived", out)

    def test_lost_when_fluent_bit_holds_no_chunk(self):
        self.deliver("flood-00000", "flood-00001")
        self.hold(0)
        code, took, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertLess(took, 60)
        self.assertIn("lost", err)
        self.assertIn("flood-00002", err)

    def test_gives_up_while_chunks_are_still_held(self):
        for timeout, bound in (("120", 2100), ("3000", 3000)):
            with self.subTest(timeout=timeout):
                (self.run_dir / "stub" / "requests.jsonl").unlink(missing_ok=True)
                self.deliver("flood-00000", "flood-00001")
                self.hold(3)
                code, took, _out, err = self.run_check(timeout=timeout)
                self.assertEqual(code, 1)
                self.assertGreaterEqual(took, bound)
                self.assertLess(took, bound + 20)
                self.assertIn("3 chunks", err)
                self.assertNotIn("lost", err)

    def test_fails_when_docker_cannot_count_the_chunks(self):
        self.deliver("flood-00000", "flood-00001")
        code, took, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertLess(took, 60)
        self.assertIn("could not count the chunks", err)
        self.assertNotIn("lost", err)


if __name__ == "__main__":
    unittest.main()
