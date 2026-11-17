"""The layout of T-Pot's Logstash pipelines (docker/elk/logstash/dist/pipeline): the HIVE and the SENSOR
put their pipelines together from the same part files, one parse file per type, the files and the sensor
input hand their events to the one processing pipeline (pipeline-to-pipeline, address tpot_process).
"""

import glob
import re
import unittest

from tpotctl.tests import logstash_conf
from tpotctl.tests.logstash_conf import DIST, PIPELINE_DIR

try:
    import yaml
except ImportError:  # the python3 of the host; the venv has PyYAML
    yaml = None

INPUT_FILES = PIPELINE_DIR / "10-input-files.conf"
INPUT_TYPE = re.compile(r'^\s*type => "([^"]+)"', re.M)
TYPE_BLOCK = re.compile(r'^\s*if \[type\] == "([^"]+)"', re.M)
SEND_TO = re.compile(r'send_to => \[?"([^"]+)"')
ADDRESS = re.compile(r'address => "([^"]+)"')


class LayoutTest(unittest.TestCase):

    def test_every_type_has_one_parse_file(self):
        inputs = INPUT_TYPE.findall(INPUT_FILES.read_text(encoding="utf-8"))
        self.assertGreater(len(set(inputs)), 30)
        parsed = {}
        for path in sorted(PIPELINE_DIR.glob("40-parse-*.conf")):
            with self.subTest(file=path.name):
                types = TYPE_BLOCK.findall(path.read_text(encoding="utf-8"))
                self.assertEqual(len(types), 1, "one type per parse file")
                self.assertEqual(path, logstash_conf.parse_file(types[0]))
                parsed[types[0]] = path
        self.assertEqual(sorted(parsed), sorted(set(inputs)))
        for sensor in (False, True):
            with self.subTest(sensor=sensor):
                # each type is parsed once in the processing pipeline, in its own file only
                blocks = TYPE_BLOCK.findall(logstash_conf.pipeline_text("process", sensor))
                self.assertEqual(sorted(blocks), sorted(set(inputs)))

    def test_pipelines_resolve(self):
        expected = {False: ["files", "process", "sensors"], True: ["files", "process"]}
        used = set()
        for sensor in (False, True):
            found = logstash_conf.pipelines(sensor)
            self.assertEqual(sorted(found), expected[sensor])
            if yaml:
                with open(logstash_conf.PIPELINES_YML[sensor], encoding="utf-8") as handle:
                    self.assertEqual({entry["pipeline.id"]: entry for entry in yaml.safe_load(handle)}, found)
            sends, listens = set(), []
            for pipeline_id, settings in found.items():
                with self.subTest(sensor=sensor, pipeline=pipeline_id):
                    self.assertEqual(settings.get("pipeline.ecs_compatibility"), "disabled")
                    for pattern in logstash_conf.patterns(pipeline_id, sensor):
                        self.assertTrue(glob.glob(pattern), f"{pattern} matches no file")
                    files = logstash_conf.pipeline_files(pipeline_id, sensor)
                    self.assertEqual(len(files), len(set(files)), "Logstash would read a file twice")
                    used.update(files)
                    text = logstash_conf.pipeline_text(pipeline_id, sensor)
                    sends.update(SEND_TO.findall(text))
                    listens.extend(ADDRESS.findall(text))
            with self.subTest(sensor=sensor):
                self.assertEqual(sends, {"tpot_process"})
                self.assertEqual(sorted(listens), sorted(sends), "one pipeline listens on each address")
                self.assertEqual(ADDRESS.findall(logstash_conf.pipeline_text("process", sensor)), ["tpot_process"])
        self.assertEqual(sorted(set(PIPELINE_DIR.glob("*.conf")) - used), [], "part files no pipeline reads")

    def test_process_shared_by_hive_and_sensor(self):
        hive = logstash_conf.pipeline_files("process")
        sensor = logstash_conf.pipeline_files("process", sensor=True)
        shared = sorted(PIPELINE_DIR.glob("40-*.conf")) + [PIPELINE_DIR / "50-enrich.conf"]
        self.assertGreater(len(shared), 30)
        for path in shared:
            with self.subTest(file=path.name):
                self.assertIn(path, hive)
                self.assertIn(path, sensor)
        # one source: the two differ in their output only
        self.assertEqual(sorted(set(hive) ^ set(sensor)),
                         [PIPELINE_DIR / "90-output-es.conf", PIPELINE_DIR / "91-output-http.conf"])

    def test_no_monolith(self):
        for name in ("logstash.conf", "http_output.conf", "http_input.conf"):
            with self.subTest(file=name):
                self.assertFalse((DIST / name).exists())
        self.assertEqual(sorted(DIST.glob("*.conf")), [])
        dockerfile = (DIST.parent / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("\nCOPY dist/pipeline/ /usr/share/logstash/pipeline/\n", dockerfile)
        self.assertNotIn("dist/*.conf", dockerfile)


if __name__ == "__main__":
    unittest.main()
