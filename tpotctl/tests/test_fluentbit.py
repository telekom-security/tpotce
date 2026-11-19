"""The image fluentbit (docker/fluentbit): Fluent Bit on a SENSOR ships the raw log lines of the
honeypots to the HIVE. Its tail inputs come from the log table (tpotctl.logs_config), the pinned
values of its configuration are read here as YAML, its Lua filter runs with the lua of the host
where there is one. The smoke test docker/_tests/tests/fluentbit.sh runs the image against a stub
HIVE (docker/_tests/lib/hive_stub.py)."""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
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
            "tls.ca_file": "/data/hive.crt", "tls.vhost": "${TPOT_HIVE_IP}",
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
        with open(IMAGE_DIR / "docker-compose.yml", encoding="utf-8") as handle:
            service = yaml.safe_load(handle)["services"]["fluentbit"]
        version = (ROOT / "version").read_text(encoding="utf-8").strip()
        self.assertEqual(service["image"], f"ghcr.io/telekom-security/fluentbit:{version}")
        self.assertTrue(service["read_only"])
        self.assertEqual(service["mem_limit"], "256m")
        self.assertIn("/tmp", service["tmpfs"][0])

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
        # E2: the probe of the HIVE before Fluent Bit starts (the smoke test runs every answer)
        self.assertLess(text.index("\nfuPROBE\n"), text.index("\nexec fluent-bit"))
        for message in ("The HIVE does not take this SENSOR's format yet (update the HIVE first); logs wait here.",
                        "The HIVE's Logstash pipeline is not ready for this SENSOR format; logs wait here.",
                        "The HIVE is not reachable ("):
            self.assertIn(message, text)
        # C4: a busy HIVE (its global limit of /tpot-ingest) has its own line, before the generic one
        busy = "The HIVE is busy (HTTP 429), next probe in ${FLB_PROBE_INTERVAL} s."
        self.assertIn(busy, text)
        self.assertLess(text.index(busy), text.index("The HIVE is not reachable (HTTP"))
        self.assertIn("https://${TPOT_HIVE_IP}:${TPOT_HIVE_PORT}/tpot-ingest", text)
        self.assertIn('"${FLB_PROBE_INTERVAL:-300}"', text)


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
