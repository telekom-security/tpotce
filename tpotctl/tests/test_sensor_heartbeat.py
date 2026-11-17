"""Sensor heartbeat metadata at the actual tpotinit/Logstash startup boundary."""
import bz2
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
LOGSTASH = ROOT / "docker/elk/logstash/dist"
UPDATEIP = ROOT / "docker/tpotinit/dist/bin/updateip.sh"
ILM = ROOT / "docker/tpotinit/dist/etc/ilm"


def elasticsearch_blocks(text):
    """The bodies of the elasticsearch {} plugins of a part file, comment lines left out."""
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    blocks = []
    for found in re.finditer(r"\belasticsearch\s*\{", text):
        depth, position = 1, found.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[position], 0)
            position += 1
        blocks.append(text[found.end():position - 1])
    return blocks


class MetadataTest(unittest.TestCase):
    def test_no_template_management(self):
        blocks = [block for path in sorted((LOGSTASH / "pipeline").glob("*.conf"))
                  for block in elasticsearch_blocks(path.read_text())]
        self.assertGreaterEqual(len(blocks), 4)
        for block in blocks:
            self.assertRegex(block, r"(?m)^\s*manage_template\s*=>\s*false\s*$")
            self.assertNotRegex(block, r"(?m)^\s*template\w*\s*=>")

    def test_templates_only_in_tpotinit(self):
        self.assertEqual(sorted(path.name for path in LOGSTASH.glob("*.json")), [])
        self.assertNotIn(".json", (LOGSTASH.parent / "Dockerfile").read_text())
        self.assertEqual(sorted(path.name for path in ILM.glob("*.json")),
                         ["logstash.json", "tpot-failures.json", "tpot-sensor-heartbeat.json",
                          "tpot-sensor-status.json"])

    def test_log_path_is_a_keyword_in_the_logstash_template(self):
        template = json.loads((ILM / "logstash.json").read_text())
        self.assertEqual(template["index_patterns"], ["logstash-*"])
        self.assertEqual(template["template"]["mappings"]["properties"]["t-pot_log_path"], {"type": "keyword"})

    def test_healthcheck_honours_waiting_marker(self):
        lines = [line for line in (LOGSTASH.parent / "Dockerfile").read_text().splitlines()
                 if line.startswith("HEALTHCHECK")]
        self.assertEqual(lines, ["HEALTHCHECK --retries=10 CMD test -f /tmp/tpot-waiting || "
                                 "curl -s -XGET 'http://127.0.0.1:9600'"])

    def test_tpotinit_exports_the_running_version_with_existing_metadata(self):
        text = UPDATEIP.read_text()
        version = re.search(r"^myVERSION=.*$", text, re.M).group()
        writer = re.search(r"^myWRITE_IN_PLACE='.*?'$", text, re.M | re.S).group()
        output = re.search(r"^printf 'HONEY_UUID=.*?(?=\n\n)", text, re.M | re.S).group()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "elk_environment"
            script = version + "\n" + writer + "\n" + output.replace(
                "/data/tpot/etc/compose/elk_environment", str(path))
            script = script.replace('python3 -c ', f'"{sys.executable}" -c ')
            done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                  env=dict(os.environ, TPOT_VERSION="test-release",
                                           myUUID="test-uuid", myEXTIP="198.51.100.1",
                                           myLOCALIP="192.0.2.1", HOSTNAME="sensor-one"))
            self.assertEqual(done.returncode, 0, done.stderr)
            values = dict(line.split("=", 1) for line in path.read_text().splitlines())
            self.assertEqual(values, {
                "MY_VERSION": "test-release", "HONEY_UUID": "test-uuid",
                "MY_EXTIP": "198.51.100.1", "MY_INTIP": "192.0.2.1", "MY_HOSTNAME": "sensor-one",
            })


class EntrypointTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for directory in ("data/tpot/etc/compose", "data/elk/listbot",
                          "etc/listbot", "usr/share/logstash/bin", "usr/share/logstash/config", "bin", "tmp"):
            (self.root / directory).mkdir(parents=True)
        for name in ("iprep.yaml", "cve.yaml"):
            (self.root / "data/elk/listbot" / (name + ".bz2")).write_bytes(bz2.compress(b'"test": "test"\n'))
        self.environment = self.root / "data/tpot/etc/compose/elk_environment"
        self.environment.write_text(
            "MY_VERSION=test-release\nHONEY_UUID=00000000-0000-0000-0000-000000000000\n")
        (self.root / "usr/share/logstash/config/pipelines_sensor.yml").write_text("sensor pipeline\n")
        self.captured = self.root / "captured.json"
        self.marker = self.root / "tmp/tpot-waiting"
        logstash = self.root / "usr/share/logstash/bin/logstash"
        logstash.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            f"with open({str(self.captured)!r}, 'w') as handle:\n"
            "    json.dump({'version': os.environ.get('MY_VERSION'),\n"
            "               'started': os.environ.get('TPOT_LOGSTASH_START'),\n"
            f"               'waiting': os.path.exists({str(self.marker)!r}),\n"
            "               'args': sys.argv[1:]}, handle)\n")
        logstash.chmod(0o755)
        self.calls = self.root / "calls"
        curl = self.root / "bin/curl"
        curl.write_text(f"#!{sys.executable}\n" + CURL_STUB.format(calls=str(self.calls), marker=str(self.marker)))
        curl.chmod(0o755)
        text = (LOGSTASH / "entrypoint.sh").read_text()
        for prefix in ("/data/", "/etc/listbot/", "/usr/share/logstash/", "/tmp/"):
            text = text.replace(prefix, str(self.root) + prefix)
        self.script = self.root / "entrypoint.sh"
        self.script.write_text(text)
        self.env = dict(os.environ, TPOT_TYPE="SENSOR", TPOT_HIVE_IP="192.0.2.2",
                        TPOT_LOGSTASH_START="2000-01-01T00:00:00Z",
                        PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"])
        self.env.pop("MY_VERSION", None)

    def run_entrypoint(self):
        return subprocess.run(["bash", str(self.script)], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_version_and_process_start_reach_logstash(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        done = self.run_entrypoint()
        after = datetime.now(timezone.utc)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = json.loads(self.captured.read_text())
        self.assertEqual(data["version"], "test-release")
        self.assertEqual(data["args"], ["--config.reload.automatic"])
        self.assertIsInstance(data["started"], str)
        started = datetime.fromisoformat(data["started"].replace("Z", "+00:00"))
        self.assertLessEqual(before, started)
        self.assertLessEqual(started, after)

    def test_old_tpotinit_warns_but_does_not_disable_transport(self):
        self.environment.write_text("HONEY_UUID=00000000-0000-0000-0000-000000000000\n")
        done = self.run_entrypoint()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.captured.exists())
        self.assertIn("version", done.stderr.lower())
        self.assertIn("tpotinit", done.stderr.lower())
        self.assertEqual(json.loads(self.captured.read_text())["version"], "")

    def test_missing_clock_is_a_startup_failure_not_success(self):
        date = self.root / "bin/date"
        date.write_text("#!/bin/sh\nexit 1\n")
        date.chmod(0o755)
        done = self.run_entrypoint()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(self.captured.exists())
        self.assertIn("start", done.stderr.lower())

    def hive(self, mode):
        self.env.update(TPOT_TYPE="HIVE", TPOT_TEMPLATE_POLL="0.1", CURL_MODE=mode)

    def requests(self):
        lines = self.calls.read_text().splitlines() if self.calls.exists() else []
        return [json.loads(line) for line in lines]

    def test_waits_for_templates_without_limit(self):
        self.hive("late")
        done = self.run_entrypoint()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        logstash = [call for call in self.requests() if "/_index_template/logstash?" in call["url"]]
        self.assertEqual([call["code"] for call in logstash], ["404"] * 5 + ["200"])
        self.assertTrue(all(call["waiting"] for call in logstash))
        self.assertTrue(all(call["url"].startswith("http://elasticsearch:9200/_index_template/") and
                            call["url"].endswith("?filter_path=index_templates.index_template._meta.tpot_version")
                            for call in self.requests()))
        self.assertEqual(done.stdout.count("Waiting for the index templates"), 1, done.stdout)
        self.assertIn("Waiting for the index templates of tpotinit (es_retention.sh): logstash; "
                      "see docker logs tpotinit.", done.stdout)
        self.assertIn("Index templates in place, starting Logstash.", done.stdout)
        self.assertFalse(self.marker.exists())
        self.assertFalse(json.loads(self.captured.read_text())["waiting"])

    def test_waits_for_the_failures_template(self):
        self.hive("late-failures")
        done = self.run_entrypoint()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        failures = [call for call in self.requests() if "/_index_template/tpot-failures?" in call["url"]]
        self.assertEqual([call["code"] for call in failures], ["404"] * 3 + ["200"])
        self.assertIn("Waiting for the index templates of tpotinit (es_retention.sh): tpot-failures; "
                      "see docker logs tpotinit.", done.stdout)
        self.assertIn("Index templates in place, starting Logstash.", done.stdout)
        self.assertFalse(json.loads(self.captured.read_text())["waiting"])

    def test_wrong_template_version_keeps_waiting(self):
        self.hive("wrong")
        log = self.root / "out"
        with open(log, "w", encoding="utf-8") as handle:
            proc = subprocess.Popen(["bash", str(self.script)], env=self.env, stdout=handle,
                                    stderr=subprocess.STDOUT)
        try:
            time.sleep(2)
            self.assertIsNone(proc.poll(), log.read_text())
            self.assertTrue(self.marker.exists())
            self.assertFalse(self.captured.exists())
            self.assertGreater(len(self.requests()), 6)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        self.assertIn("logstash has T-Pot old-release, needs test-release", log.read_text())

    def test_sensor_does_not_wait(self):
        self.env["TPOT_TEMPLATE_POLL"] = "0.1"
        done = self.run_entrypoint()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.requests(), [])
        self.assertNotIn("index templates", done.stdout.lower())
        self.assertFalse(json.loads(self.captured.read_text())["waiting"])


# curl for the wait of the Logstash entrypoint: logs each call, Elasticsearch has the sensor templates;
# CURL_MODE late: logstash answers 404 five times, then with the version, wrong: with another version
CURL_STUB = """import json, os, sys
args = sys.argv[1:]
url = [arg for arg in args if arg.startswith("http")][-1]
fmt = args[args.index("-w") + 1] if "-w" in args else ""
count = failures = 0
if os.path.exists({calls!r}):
    with open({calls!r}) as handle:
        lines = handle.readlines()
    count = sum("/_index_template/logstash?" in line for line in lines)
    failures = sum("/_index_template/tpot-failures?" in line for line in lines)
code, body = "200", "{{}}"
if "/_index_template/tpot-failures?" in url and os.environ.get("CURL_MODE") == "late-failures" and failures < 3:
    code = "404"
if "/_index_template/logstash?" in url:
    late = os.environ.get("CURL_MODE") == "late" and count < 5
    version = "old-release" if os.environ.get("CURL_MODE") == "wrong" else "test-release"
    code, body = ("404", "{{}}") if late else (
        "200", json.dumps({{"index_templates": [{{"index_template": {{"_meta": {{"tpot_version": version}}}}}}]}},
                          separators=(",", ":")))
with open({calls!r}, "a") as handle:
    handle.write(json.dumps({{"url": url, "code": code, "waiting": os.path.exists({marker!r})}}) + "\\n")
sys.stdout.write(body + fmt.replace("\\\\n", "\\n").replace("%{{http_code}}", code))
"""


if __name__ == "__main__":
    unittest.main()
