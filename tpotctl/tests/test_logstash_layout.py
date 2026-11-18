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
        expected = {False: ["failures", "files", "process", "sensors"], True: ["files", "process"]}
        addresses = {False: {"tpot_process", "tpot_failures"}, True: {"tpot_process"}}
        used = set()
        for sensor in (False, True):
            found = logstash_conf.pipelines(sensor)
            self.assertEqual(sorted(found), expected[sensor])
            if yaml:
                with open(logstash_conf.PIPELINES_YML[sensor], encoding="utf-8") as handle:
                    # logstash_conf reads every value as text (true, 1), YAML as bool / int
                    self.assertEqual({entry["pipeline.id"]: {key: str(value).lower() if isinstance(value, bool)
                                                             else str(value) for key, value in entry.items()}
                                      for entry in yaml.safe_load(handle)}, found)
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
                self.assertEqual(sends, addresses[sensor])
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

    def test_failures_pipeline(self):
        # HIVE: parse failures (pipeline-to-pipeline) and the events Elasticsearch rejected (the dead
        # letter queue of process) go to tpot-failures-*, nothing is dropped silently
        hive = logstash_conf.pipelines()
        self.assertEqual({key: value for key, value in hive["failures"].items() if key != "path.config"},
                         {"pipeline.id": "failures", "pipeline.ecs_compatibility": "disabled",
                          "pipeline.workers": "1"})
        self.assertEqual([path.name for path in logstash_conf.pipeline_files("failures")],
                         ["60-input-failures.conf", "61-failures.conf", "92-output-failures.conf"])
        self.assertEqual({key: value for key, value in hive["process"].items() if key.startswith("dead_letter")},
                         {"dead_letter_queue.enable": "true", "dead_letter_queue.max_bytes": "256mb",
                          "dead_letter_queue.storage_policy": "drop_older"})
        for sensor in (False, True):
            for settings in logstash_conf.pipelines(sensor).values():
                with self.subTest(sensor=sensor, pipeline=settings["pipeline.id"]):
                    self.assertNotIn("path.data", settings)
                    self.assertNotIn("path.dead_letter_queue", settings)
        # the SENSOR as before: no failures pipeline, no dead letter queue
        for settings in logstash_conf.pipelines(sensor=True).values():
            self.assertEqual([key for key in settings if key.startswith("dead_letter")], [])
        code = uncommented(PIPELINE_DIR / "60-input-failures.conf")
        self.assertRegex(code, r'pipeline \{\s*address => "tpot_failures"\s*\}')
        self.assertRegex(code, r'dead_letter_queue \{[^}]*pipeline_id => "process"[^}]*\}')
        self.assertRegex(code, r'dead_letter_queue \{[^}]*commit_offsets => true[^}]*\}')
        output = uncommented(PIPELINE_DIR / "90-output-es.conf")
        self.assertRegex(output, r'if "_grokparsefailure" in \[tags\] or "_jsonparsefailure" in \[tags\] \{'
                                 r'\s*pipeline \{\s*send_to => \["tpot_failures"\]\s*\}\s*\} else \{'
                                 r'\s*elasticsearch \{[^}]*index => "logstash-%\{\+YYYY\.MM\.dd\}"')
        self.assertIn('index => "tpot-failures-%{+YYYY.MM.dd}"', uncommented(PIPELINE_DIR / "92-output-failures.conf"))
        # no silent drop of a parse failure anywhere; the SENSOR keeps not sending them (91-output-http.conf)
        self.assertNotIn("drop", uncommented(PIPELINE_DIR / "50-enrich.conf"))
        self.assertRegex(uncommented(PIPELINE_DIR / "91-output-http.conf"),
                         r'if "_grokparsefailure" not in \[tags\] and "_jsonparsefailure" not in \[tags\] \{'
                         r'\s*http \{')

    def test_entrypoint_creates_the_dead_letter_queue_folder(self):
        """The HIVE branch of entrypoint.sh creates <path of the dead_letter_queue input>/<its pipeline_id>
        before Logstash starts: the input of failures would otherwise fail once on a fresh start."""
        conf = (PIPELINE_DIR / "60-input-failures.conf").read_text(encoding="utf-8")
        dlq = re.search(r'dead_letter_queue \{(.*?)\}', conf, re.S).group(1)
        path = re.search(r'path => "([^"]+)"', dlq).group(1)
        pipeline_id = re.search(r'pipeline_id => "([^"]+)"', dlq).group(1)
        entry = (DIST / "entrypoint.sh").read_text(encoding="utf-8")
        hive = entry.split('if [ "$TPOT_TYPE" != "SENSOR" ];', 1)[1].split("\nfi\n", 1)[0]
        self.assertIn(f'myDLQ_PATH="{path}"', hive)
        self.assertIn(f'mkdir -p "${{myDLQ_PATH}}/{pipeline_id}"', hive)
        self.assertLess(entry.index("mkdir -p \"${myDLQ_PATH}"), entry.index("exec /usr/share/logstash/bin/logstash"))

    def test_failures_template(self):
        template = json.loads((ROOT / "docker" / "tpotinit" / "dist" / "etc" / "ilm" / "tpot-failures.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(template["index_patterns"], ["tpot-failures-*"])
        self.assertEqual(template["priority"], 100)
        self.assertEqual(template["template"]["settings"], {"index.lifecycle.name": "tpot",
                                                            "index.number_of_shards": 1,
                                                            "index.number_of_replicas": 0})
        mappings = template["template"]["mappings"]
        self.assertIs(mappings["dynamic"], False)
        keyword = {"type": "keyword"}
        self.assertEqual(mappings["properties"], {
            "@timestamp": {"type": "date"}, "type": keyword, "t-pot_sensor": keyword,
            "t-pot_hostname": keyword, "t-pot_log_path": keyword,
            "failure": {"properties": {"stage": keyword,
                                       "reason": {"type": "keyword", "ignore_above": 1024}}}})

    def test_sensors_strip_headers(self):
        # a body's @metadata (json codec) could forge routing flags: gone before sensor_heartbeat.rb reads
        # the authenticated name from headers; the request ballast headers / host goes after it
        code = uncommented(PIPELINE_DIR / "20-sensors.conf")
        metadata = re.search(r'mutate \{\s*remove_field => \["\[@metadata\]"\]\s*\}', code)
        ruby = code.find('path => "/usr/share/logstash/pipeline/sensor_heartbeat.rb"')
        ballast = re.search(r'mutate \{\s*remove_field => \["headers", "host"\]\s*\}', code)
        self.assertIsNotNone(metadata, "the body's @metadata removed")
        self.assertIsNotNone(ballast, "headers and host removed")
        self.assertTrue(0 < metadata.end() < ruby < ballast.start(), "@metadata, the ruby filter, headers / host")
        self.assertEqual(code.count("remove_field"), 2)

    def test_sensors_ingest_probe(self):
        # E2: the capability probe of a SENSOR (nginx /tpot-ingest) is an input of the sensors pipeline
        # itself, so only a Logstash that runs this pipeline answers 204 with the marker; its event is
        # dropped by the first filter (no document, status, history)
        inputs = re.findall(r"^  http \{\n(.*?)^  \}\n", uncommented(PIPELINE_DIR / "11-input-sensors.conf"),
                            re.M | re.S)
        self.assertEqual(len(inputs), 2)
        probe = [block for block in inputs if 'id => "tpot_ingest_probe"' in block]
        self.assertEqual(len(probe), 1)
        settings = dict(re.findall(r"^\s*(\w+) => (.+?)\s*$", probe[0], re.M))
        self.assertEqual(settings, {"id": '"tpot_ingest_probe"', "host": '"0.0.0.0"', "port": "64306",
                                    "response_code": "204",
                                    "response_headers": '{ "X-TPot-Ingest" => "2" }',
                                    "add_field": '{ "[@metadata][tpot_probe]" => "true" }',
                                    "ecs_compatibility": "disabled"})
        code = uncommented(PIPELINE_DIR / "20-sensors.conf")
        first = re.match(r"\s*filter \{\s*if \[@metadata\]\[tpot_probe\] \{\s*drop \{\}\s*\}\s*\}", code)
        self.assertIsNotNone(first, "the drop of the probe is the first filter of the sensors pipeline")
        self.assertEqual(code.count("drop"), 1)
        # container network only: never published by a compose file
        composes = [ROOT / "docker-compose.yml", *sorted((ROOT / "compose").glob("*.yml")),
                    *sorted((ROOT / "docker").glob("*/docker-compose.yml")),
                    *sorted((ROOT / "docker").glob("*/*/docker-compose.yml"))]
        for path in composes:
            if path.exists():
                with self.subTest(file=str(path.relative_to(ROOT))):
                    self.assertNotIn("64306", path.read_text(encoding="utf-8"))

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

    def test_file_inputs_start_at_beginning(self):
        # events a honeypot wrote before Logstash started are kept; the sincedb stays in the container layer
        text = (PIPELINE_DIR / "10-input-files.conf").read_text(encoding="utf-8")
        blocks = re.findall(r"^  file \{\n(.*?)^  \}\n", text, re.M | re.S)
        self.assertGreater(len(blocks), 1)
        for block in blocks:
            with self.subTest(block=block.splitlines()[0]):
                self.assertEqual(block.count('    start_position => "beginning"\n'), 1)
                self.assertNotIn("sincedb_path", block)

    def test_no_monolith(self):
        for name in ("logstash.conf", "http_output.conf", "http_input.conf"):
            with self.subTest(file=name):
                self.assertFalse((DIST / name).exists())
        self.assertEqual(sorted(DIST.glob("*.conf")), [])
        dockerfile = (DIST.parent / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("\nCOPY dist/pipeline/ /usr/share/logstash/pipeline/\n", dockerfile)
        self.assertNotIn("dist/*.conf", dockerfile)

    def test_pipeline_workers(self):
        # files and sensors run with one worker (sensors: the newest receipt of a heartbeat wins, only
        # one worker keeps receipt order equal to arrival order), process with the default of Logstash
        for sensor in (False, True):
            with self.subTest(sensor=sensor):
                found = logstash_conf.pipelines(sensor)
                self.assertEqual({key: value for key, value in found["files"].items() if key != "path.config"},
                                 {"pipeline.id": "files", "pipeline.ecs_compatibility": "disabled",
                                  "pipeline.workers": "1"})
                self.assertNotIn("pipeline.workers", found["process"])
        self.assertEqual({key: value for key, value in logstash_conf.pipelines()["sensors"].items()
                          if key != "path.config"},
                         {"pipeline.id": "sensors", "pipeline.ecs_compatibility": "disabled",
                          "pipeline.workers": "1"})

    def test_no_legacy_settings(self):
        settings = (DIST / "logstash.yml").read_text(encoding="utf-8")
        self.assertEqual([line for line in settings.splitlines() if line and not line.startswith("#")],
                         ['api.http.host: "127.0.0.1"'])
        self.assertNotIn("xpack.monitoring", settings)
        entrypoint = (DIST / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertNotIn("--config.reload.automatic", entrypoint)
        self.assertIn("\nexec /usr/share/logstash/bin/logstash\n", entrypoint)
        dockerfile = (DIST.parent / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("logstash-output-syslog", dockerfile)
        self.assertNotIn("logstash-plugin install", dockerfile)
        for path in sorted(PIPELINE_DIR.glob("*.conf")):
            with self.subTest(file=path.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"(?i)syslog")

    def test_jvm_trims_native_heap(self):
        # Logstash's launcher reads config/jvm.options and LS_JAVA_OPTS only, never jvm.options.d; the
        # build stops when the JDK no longer knows the flag as a product flag
        dockerfile = (DIST.parent / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("jvm.options.d", dockerfile)
        self.assertEqual(re.findall(r"TrimNativeHeapInterval\S*", dockerfile),
                         ["TrimNativeHeapInterval.*{product}'", "TrimNativeHeapInterval=60000\\n'"])
        self.assertRegex(dockerfile, r"/usr/share/logstash/jdk/bin/java -XX:\+PrintFlagsFinal -version \| "
                                     r"grep -q ' TrimNativeHeapInterval\.\*\{product\}' && \\\n")
        # the jvm.options of the image ends without a newline
        self.assertIn("    printf '\\n# T-Pot: give freed native memory back to the system every 60 s"
                      "\\n-XX:TrimNativeHeapInterval=60000\\n' \\\n"
                      "        >> /usr/share/logstash/config/jvm.options && \\\n", dockerfile)


def uncommented(path):
    """The text of a part file without its comment lines."""
    return "".join(line for line in path.read_text(encoding="utf-8").splitlines(True)
                   if not line.lstrip().startswith("#"))


def golden_documents():
    with open(GOLDEN, encoding="utf-8") as handle:
        return {entry["name"]: entry["event"] for entry in map(json.loads, handle)}


if __name__ == "__main__":
    unittest.main()
