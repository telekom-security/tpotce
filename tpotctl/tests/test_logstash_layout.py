"""The layout of T-Pot's Logstash pipelines (docker/elk/logstash/dist/pipeline): the HIVE and the SENSOR
put their pipelines together from the same part files, one parse file per type, the files and the sensor
input hand their events to the one processing pipeline (pipeline-to-pipeline, address tpot_process).
"""

import glob
import json
import re
import unittest
from datetime import datetime, timezone

from tpotctl.checks import fixtures
from tpotctl.tests import logstash_conf
from tpotctl.tests.logstash_conf import DIST, PIPELINE_DIR, ROOT

try:
    import yaml
except ImportError:  # the python3 of the host; the venv has PyYAML
    yaml = None

INPUT_FILES = PIPELINE_DIR / "10-input-files.conf"
DECODE = PIPELINE_DIR / "30-decode.conf"
GOLDEN = ROOT / "docker" / "_tests" / "golden" / "logstash_fixtures.jsonl"
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
        shared = [DECODE] + sorted(PIPELINE_DIR.glob("40-*.conf")) + [PIPELINE_DIR / "50-enrich.conf"]
        self.assertGreater(len(shared), 30)
        for path in shared:
            with self.subTest(file=path.name):
                self.assertIn(path, hive)
                self.assertIn(path, sensor)
        # one source: the two differ in their output only
        self.assertEqual(sorted(set(hive) ^ set(sensor)),
                         [PIPELINE_DIR / "90-output-es.conf", PIPELINE_DIR / "91-output-http.conf"])

    def test_process_reads_no_environment(self):
        paths = [path for pattern in ("30-*.conf", "40-*.conf", "50-*.conf")
                 for path in sorted(PIPELINE_DIR.glob(pattern))]
        self.assertGreater(len(paths), 30)
        for path in paths:
            with self.subTest(file=path.name):
                self.assertNotIn("${", path.read_text(encoding="utf-8"))

    def test_decode_before_parse(self):
        for sensor in (False, True):
            with self.subTest(sensor=sensor):
                names = [path.name for path in logstash_conf.pipeline_files("process", sensor)]
                self.assertEqual(names[:2], ["14-input-process.conf", "30-decode.conf"])
                self.assertTrue(names[2].startswith("40-parse-"))

    def test_honeypot_message_survives(self):
        # the raw line leaves message before the JSON goes into the event, so Cowrie's own message stays
        text = DECODE.read_text(encoding="utf-8")
        moved = text.find('rename => { "message" => "[@metadata][raw]" }')
        parsed = text.find('json {\n      source => "[@metadata][raw]"')
        failed = text.find('if "_jsonparsefailure" in [tags]')
        back = text.find('copy => { "[@metadata][raw]" => "message" }')
        self.assertTrue(0 < moved < parsed < failed < back, "rename, json, the raw line back on a failure")
        self.assertEqual(text.count('"message"'), 2)
        golden = golden_documents()
        line = json.loads(fixtures.render("cowrie", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
                                          "8.8.8.8", 40000, 8080, "goldencowrie"))
        self.assertEqual(golden["cowrie"]["message"], line["message"])
        # a honeypot without a message of its own gets none (the raw line would be one)
        self.assertNotIn("message", golden["glutton"])

    def test_blank_json_line_gives_no_event(self):
        # as the json codec did: a blank line (empty or whitespace only) of a JSON type is no event
        text = DECODE.read_text(encoding="utf-8")
        moved = text.find('rename => { "message" => "[@metadata][raw]" }')
        blank = text.find('    if [@metadata][raw] =~ /\\A\\s*\\z/ {\n      drop {}\n    }\n')
        parsed = text.find("json {")
        self.assertTrue(0 < moved < blank < parsed, "the blank raw line dropped before the json filter")
        self.assertEqual(text.count("drop"), 1)
        self.assertLess(text.find("if [type] in ["), blank, "only in the branch of the JSON types")

    def test_file_source_path_does_not_clobber_payload_path(self):
        text = DECODE.read_text(encoding="utf-8")
        moved = re.search(r'^  if \[path\] and !\[t-pot_log_path\] \{\n    mutate \{\n'
                          r'      rename => \{ "path" => "t-pot_log_path" \}\n    \}\n  \}\n', text, re.M)
        self.assertIsNotNone(moved, "the source file goes to t-pot_log_path, a sensor's stays")
        self.assertLess(moved.end(), text.find("json {"), "before the JSON can bring a path of its own")
        code = "".join(line for line in text.splitlines(True) if not line.lstrip().startswith("#"))
        self.assertEqual(code.count("path"), 4, "nothing else touches path or t-pot_log_path")
        golden = golden_documents()
        for name, path in (("go-pot", "/goldengopot"), ("honeyaml", "/goldenhoneyaml"),
                           ("wordpot", "/goldenwordpot")):
            with self.subTest(honeypot=name):
                self.assertEqual(golden[name]["path"], path)
        for path in sorted(PIPELINE_DIR.glob("*.conf")):
            with self.subTest(file=path.name):
                # the source file is t-pot_log_path from 30-decode.conf on
                self.assertNotRegex(path.read_text(encoding="utf-8"), r'\[path\] *[=!]= *"/data/')

    def test_files_sets_tpot_fields(self):
        text = (PIPELINE_DIR / "12-files.conf").read_text(encoding="utf-8")
        fields = re.findall(r'^\s*"(t-pot_[^"]+)" => "([^"]*)"', text, re.M)
        self.assertEqual(fields, [("t-pot_ip_ext", "${MY_EXTIP}"),
                                  ("t-pot_ip_int", "${MY_INTIP}"),
                                  ("t-pot_hostname", "${MY_HOSTNAME}")])
        self.assertEqual(re.findall(r'"(t-pot_[^"]+)" =>', text), [f for f, _ in fields])

    def test_no_monolith(self):
        for name in ("logstash.conf", "http_output.conf", "http_input.conf"):
            with self.subTest(file=name):
                self.assertFalse((DIST / name).exists())
        self.assertEqual(sorted(DIST.glob("*.conf")), [])
        dockerfile = (DIST.parent / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("\nCOPY dist/pipeline/ /usr/share/logstash/pipeline/\n", dockerfile)
        self.assertNotIn("dist/*.conf", dockerfile)


def golden_documents():
    with open(GOLDEN, encoding="utf-8") as handle:
        return {entry["name"]: entry["event"] for entry in map(json.loads, handle)}


if __name__ == "__main__":
    unittest.main()
