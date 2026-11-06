"""Sensor heartbeat metadata at the actual tpotinit/Logstash startup boundary."""
import bz2
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
LOGSTASH = ROOT / "docker/elk/logstash/dist"
UPDATEIP = ROOT / "docker/tpotinit/dist/bin/updateip.sh"


class MetadataTest(unittest.TestCase):
    def test_template_copies_match_across_build_contexts(self):
        for name in ("tpot-sensor-status.json", "tpot-sensor-heartbeat.json"):
            self.assertEqual((LOGSTASH / name).read_bytes(),
                             (ROOT / "docker/tpotinit/dist/etc/ilm" / name).read_bytes())

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
                          "etc/listbot", "usr/share/logstash/bin", "usr/share/logstash/config", "bin"):
            (self.root / directory).mkdir(parents=True)
        for name in ("iprep.yaml", "cve.yaml"):
            (self.root / "data/elk/listbot" / (name + ".bz2")).write_bytes(bz2.compress(b'"test": "test"\n'))
        self.environment = self.root / "data/tpot/etc/compose/elk_environment"
        self.environment.write_text(
            "MY_VERSION=test-release\nHONEY_UUID=00000000-0000-0000-0000-000000000000\n")
        (self.root / "usr/share/logstash/config/pipelines_sensor.yml").write_text("sensor pipeline\n")
        self.captured = self.root / "captured.json"
        logstash = self.root / "usr/share/logstash/bin/logstash"
        logstash.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            f"with open({str(self.captured)!r}, 'w') as handle:\n"
            "    json.dump({'version': os.environ.get('MY_VERSION'),\n"
            "               'started': os.environ.get('TPOT_LOGSTASH_START'),\n"
            "               'args': sys.argv[1:]}, handle)\n")
        logstash.chmod(0o755)
        text = (LOGSTASH / "entrypoint.sh").read_text()
        for prefix in ("/data/", "/etc/listbot/", "/usr/share/logstash/"):
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


if __name__ == "__main__":
    unittest.main()
