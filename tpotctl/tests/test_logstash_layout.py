"""The layout of T-Pot's Logstash pipelines (docker/elk/logstash/dist/pipeline): the HIVE puts its pipelines
together from part files, one parse file per type, the files and the sensor input hand their events to the
one processing pipeline (pipeline-to-pipeline, address tpot_process). Logstash has no SENSOR mode: Fluent Bit
sends the logs of a SENSOR.
"""

import glob
import json
import re
import shutil
import subprocess
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

    @unittest.skipUnless(yaml, "requires PyYAML")
    def test_mobile_image_uses_only_the_shared_local_pipelines(self):
        dockerfile = (DIST.parent / "Dockerfile").read_text()
        src = re.search(r"^COPY (\S+) /usr/share/logstash/config-mobile/pipelines.yml$", dockerfile, re.M)
        self.assertIsNotNone(src)
        mobile = yaml.safe_load((DIST.parent / src[1]).read_text())
        hive = yaml.safe_load((DIST / "pipelines.yml").read_text())
        self.assertEqual([p["pipeline.id"] for p in mobile], ["files", "process", "failures"])
        self.assertEqual(mobile, [p for p in hive if p["pipeline.id"] != "sensors"])

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
        # each type is parsed once in the processing pipeline, in its own file only
        blocks = TYPE_BLOCK.findall(logstash_conf.pipeline_text("process"))
        self.assertEqual(sorted(blocks), sorted(set(inputs)))

    def test_pipelines_resolve(self):
        used = set()
        found = logstash_conf.pipelines()
        self.assertEqual(sorted(found), ["failures", "files", "process", "sensors"])
        if yaml:
            with open(logstash_conf.PIPELINES_YML, encoding="utf-8") as handle:
                # logstash_conf reads every value as text (true, 1), YAML as bool / int
                self.assertEqual({entry["pipeline.id"]: {key: str(value).lower() if isinstance(value, bool)
                                                         else str(value) for key, value in entry.items()}
                                  for entry in yaml.safe_load(handle)}, found)
        sends, listens = set(), []
        for pipeline_id, settings in found.items():
            with self.subTest(pipeline=pipeline_id):
                self.assertEqual(settings.get("pipeline.ecs_compatibility"), "disabled")
                for pattern in logstash_conf.patterns(pipeline_id):
                    self.assertTrue(glob.glob(pattern), f"{pattern} matches no file")
                files = logstash_conf.pipeline_files(pipeline_id)
                self.assertEqual(len(files), len(set(files)), "Logstash would read a file twice")
                used.update(files)
                text = logstash_conf.pipeline_text(pipeline_id)
                sends.update(SEND_TO.findall(text))
                listens.extend(ADDRESS.findall(text))
        self.assertEqual(sends, {"tpot_process", "tpot_failures"})
        self.assertEqual(sorted(listens), sorted(sends), "one pipeline listens on each address")
        self.assertEqual(ADDRESS.findall(logstash_conf.pipeline_text("process")), ["tpot_process"])
        self.assertEqual(sorted(set(PIPELINE_DIR.glob("*.conf")) - used), [], "part files no pipeline reads")

    def test_process_files(self):
        process = logstash_conf.pipeline_files("process")
        shared = [DECODE] + sorted(PIPELINE_DIR.glob("40-*.conf")) + [PIPELINE_DIR / "50-enrich.conf"]
        self.assertGreater(len(shared), 30)
        for path in shared:
            with self.subTest(file=path.name):
                self.assertIn(path, process)
        self.assertEqual([path for path in process if path.name.startswith("9")],
                         [PIPELINE_DIR / "90-output-es.conf"])

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
        for settings in hive.values():
            with self.subTest(pipeline=settings["pipeline.id"]):
                self.assertNotIn("path.data", settings)
                self.assertNotIn("path.dead_letter_queue", settings)
        code = uncommented(PIPELINE_DIR / "60-input-failures.conf")
        self.assertRegex(code, r'pipeline \{\s*address => "tpot_failures"\s*\}')
        self.assertRegex(code, r'dead_letter_queue \{[^}]*pipeline_id => "process"[^}]*\}')
        self.assertRegex(code, r'dead_letter_queue \{[^}]*commit_offsets => true[^}]*\}')
        output = uncommented(PIPELINE_DIR / "90-output-es.conf")
        self.assertRegex(output, r'if "_grokparsefailure" in \[tags\] or "_jsonparsefailure" in \[tags\] \{'
                                 r'\s*pipeline \{\s*send_to => \["tpot_failures"\]\s*\}\s*\} else \{'
                                 r'\s*elasticsearch \{[^}]*index => "logstash-%\{\+YYYY\.MM\.dd\}"')
        self.assertIn('index => "tpot-failures-%{+YYYY.MM.dd}"', uncommented(PIPELINE_DIR / "92-output-failures.conf"))
        # no silent drop of a parse failure anywhere
        self.assertNotIn("drop", uncommented(PIPELINE_DIR / "50-enrich.conf"))

    def test_entrypoint_creates_the_dead_letter_queue_folder(self):
        """entrypoint.sh creates <path of the dead_letter_queue input>/<its pipeline_id> before Logstash
        starts: the input of failures would otherwise fail once on a fresh start."""
        conf = (PIPELINE_DIR / "60-input-failures.conf").read_text(encoding="utf-8")
        dlq = re.search(r'dead_letter_queue \{(.*?)\}', conf, re.S).group(1)
        path = re.search(r'path => "([^"]+)"', dlq).group(1)
        pipeline_id = re.search(r'pipeline_id => "([^"]+)"', dlq).group(1)
        entry = (DIST / "entrypoint.sh").read_text(encoding="utf-8")
        # unconditional: at the top level of the script, after the SENSOR stopped, before exec
        self.assertIn(f'\nmyDLQ_PATH="{path}"\n', entry)
        mkdir = entry.index(f'\nmkdir -p "${{myDLQ_PATH}}/{pipeline_id}" || ')
        self.assertLess(entry.index("Logstash does not run on a SENSOR"), mkdir)
        self.assertLess(mkdir, entry.index("\nexec /usr/share/logstash/bin/logstash\n"))

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

    def test_sensors_filter_order(self):
        # the probe leaves no trace; a body's @metadata (json codec) could forge routing flags: gone before
        # the marker of the request is read and before sensor_heartbeat.rb reads the authenticated name
        # from headers; an event without a sensor name goes; the request ballast headers / host after that
        blocks = filter_blocks(uncommented(PIPELINE_DIR / "20-sensors.conf"))
        roles = [
            ("probe", r'if \[@metadata\]\[tpot_probe\] \{\s*drop \{\}\s*\}'),
            ("@metadata", r'mutate \{\s*remove_field => \["\[@metadata\]"\]\s*\}'),
            ("marker", r'if \[headers\]\[x_tpot_ingest\] == "2" \{\s*mutate \{\s*'
                       r'add_field => \{ "\[@metadata\]\[tpot_ingest\]" => "2" \}\s*\}\s*\}'),
            ("sensor name", r'ruby \{\s*path => "/usr/share/logstash/pipeline/sensor_heartbeat\.rb"\s*\}'),
            ("no sensor name", r'if !\[t-pot_sensor\] \{\s*ruby \{.*\}\s*drop \{\}\s*\}'),
            ("headers / host", r'mutate \{\s*remove_field => \["headers", "host"\]\s*\}'),
            ("format", r'if \[@metadata\]\[tpot_ingest\] == "2" \{.*\} else \{.*\}'),
        ]
        self.assertEqual(len(blocks), len(roles), blocks)
        for block, (role, pattern) in zip(blocks, roles):
            with self.subTest(role=role):
                self.assertRegex(block, re.compile(r"\A\s*" + pattern + r"\s*\Z", re.S))

    def test_sensors_route_by_marker(self):
        # E1: the new format (X-TPot-Ingest 2) goes on to the processing pipeline, its raw line in message;
        # the old one is refused: per sensor at most one scripted upsert of refused.last_at a minute into
        # tpot-sensor-status, nothing else of it reaches an index
        blocks = filter_blocks(uncommented(PIPELINE_DIR / "20-sensors.conf"))
        unnamed = re.search(r'ruby \{\s*init => "@warned_at = nil"\s*code => \'(.*?)\'\s*\}', blocks[4], re.S)
        self.assertIsNotNone(unnamed, "one warning at most a minute for the events without a sensor name")
        self.assertIn("now - @warned_at >= 60", unnamed.group(1))
        self.assertIn("logger.warn(", unnamed.group(1))
        branch = re.fullmatch(r'\s*if \[@metadata\]\[tpot_ingest\] == "2" \{\s*mutate \{\s*'
                              r'rename => \{ "log" => "message" \}\s*\}\s*\} else \{(.*)\}\s*', blocks[6], re.S)
        self.assertIsNotNone(branch, "the new format: log becomes message")
        old = branch.group(1)
        reduce = old.find("ruby {")
        throttle = re.search(r"throttle \{\n(.*?)^\s*\}\n", old, re.S | re.M)
        dropped = re.search(r'if "throttled" in \[tags\] \{\s*drop \{\}\s*\}', old)
        self.assertTrue(0 <= reduce < throttle.start() < dropped.start(), old)
        # the reduction first: the sender's own tags and @timestamp cannot steer the throttle
        self.assertIn('unless ["t-pot_sensor", "@timestamp"].include?(key)', old)
        self.assertIn('event.set("[refused][last_at]", now.to_s)', old)
        self.assertIn("event.timestamp = now", old)
        self.assertEqual(dict(re.findall(r"^\s*(\w+) => (.+?)\s*$", throttle.group(1), re.M)),
                         {"key": '"%{[t-pot_sensor]}"', "after_count": "1", "period": "60", "max_age": "120",
                          "add_tag": '"throttled"'})

        output = uncommented(PIPELINE_DIR / "21-sensors-output.conf")
        route = re.fullmatch(r'\s*output \{\s*if \[@metadata\]\[tpot_ingest\] == "2" \{\s*'
                             r'if \[type\] == "TPotSensorHeartbeat" \{(.*)\} else \{\s*'
                             r'pipeline \{\s*send_to => \["tpot_process"\]\s*\}\s*\}\s*'
                             r'\} else \{\s*elasticsearch \{(.*)\}\s*\}\s*\}\s*', output, re.S)
        self.assertIsNotNone(route, "marked: heartbeats to the status, the rest to tpot_process; else refused")
        self.assertNotIn("logstash-", output)
        heartbeat, refused = route.groups()
        self.assertIn("def refused = ctx._source.refused;\n", heartbeat)
        self.assertRegex(heartbeat, r"ctx\._source = params\.event;\s*if \(refused != null\) \{\s*"
                                    r"ctx\._source\.refused = refused;\s*\}")
        self.assertIn("ctx.op = 'noop';", heartbeat)
        # the sensor's time orders the status, a receipt 90 s after the stored one heals a clock that
        # went back, a stored value the script cannot read is replaced
        for part in ("params.event['@timestamp']", "ctx._source['@timestamp']", ".plusSeconds(90)",
                     "try {", "catch (Exception e)"):
            self.assertIn(part, heartbeat)
        self.assertNotIn("the newest receipt wins",
                         (PIPELINE_DIR / "21-sensors-output.conf").read_text(encoding="utf-8"))
        settings = dict(re.findall(r"^\s*(\w+) => (\S.*?)\s*$", refused.split("script =>")[0], re.M))
        self.assertEqual(settings, {"id": '"tpot_sensor_refused"', "hosts": '["elasticsearch:9200"]',
                                    "index": '"tpot-sensor-status"', "document_id": '"%{[t-pot_sensor]}"',
                                    "action": '"update"', "scripted_upsert": "true", "retry_on_conflict": "5"})
        script = re.search(r'script => "(.*?)"', refused, re.S).group(1)
        self.assertEqual([line.strip() for line in script.strip().splitlines()],
                         ["if (ctx.op == 'create') {",
                          "ctx._source['t-pot_sensor'] = params.event['t-pot_sensor'];",
                          "}",
                          "ctx._source.refused = params.event.refused;"])

        template = json.loads((ROOT / "docker" / "tpotinit" / "dist" / "etc" / "ilm" / "tpot-sensor-status.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(template["version"], 2)
        self.assertEqual(template["template"]["mappings"]["properties"]["refused"],
                         {"properties": {"last_at": {"type": "date"}}})
        self.assertIn("{11-input-sensors,20-sensors,21-sensors-output}.conf",
                      logstash_conf.pipelines()["sensors"]["path.config"])

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
        # the probe, the events without a sensor name, the throttled refusals
        self.assertEqual(code.count("drop"), 3)
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
        names = [path.name for path in logstash_conf.pipeline_files("process")]
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

    def test_files_restores_utf8(self):
        # the file inputs read ISO-8859-1; 12-files.conf gives back the bytes of the file as UTF-8, an invalid
        # byte as U+FFFD, as Fluent Bit sends the line of a SENSOR (json.escape_unicode off)
        if not shutil.which("ruby"):
            self.skipTest("no ruby")
        text = (PIPELINE_DIR / "12-files.conf").read_text(encoding="utf-8")
        codes = re.findall(r"^\s*ruby \{\n\s*code => '(.*?)'\n\s*\}", text, re.M | re.S)
        self.assertEqual(len(codes), 1)
        script = (
            "class Event\n  def initialize(value); @data = {'message' => value}; end\n"
            "  def get(key); @data[key]; end\n  def set(key, value); @data[key] = value; end\nend\n"
            "STDIN.binmode\n"
            "STDIN.read.split(\"\\n\", -1).each do |line|\n"
            "  event = Event.new(line.force_encoding(Encoding::BINARY).encode(Encoding::UTF_8,"
            " Encoding::ISO_8859_1))\n"
            + codes[0] +
            "\n  value = event.get('message')\n"
            "  raise 'not valid UTF-8' unless value.encoding == Encoding::UTF_8 && value.valid_encoding?\n"
            "  STDOUT.write(value.b + \"\\n\")\nend\n")
        lines = ['{"password":"tpot-check"}'.encode(), '{"user":"\u0436\u20ac\U0001F600"}'.encode(),
                 b'{"password":"tpot-\xffcheck"}', b'{"cut":"\xe2\x82"}', b"\xc3"]
        result = subprocess.run(["ruby", "-e", script], input=b"\n".join(lines), capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout.split(b"\n")[:-1], [
            lines[0], lines[1], '{"password":"tpot-\ufffdcheck"}'.encode(), '{"cut":"\ufffd"}'.encode(),
            "\ufffd".encode()])

    def test_file_inputs_start_at_beginning(self):
        # events a honeypot wrote before Logstash started are kept; the sincedb stays in the container layer
        text = (PIPELINE_DIR / "10-input-files.conf").read_text(encoding="utf-8")
        blocks = re.findall(r"^  file \{\n(.*?)^  \}\n", text, re.M | re.S)
        self.assertGreater(len(blocks), 1)
        for block in blocks:
            with self.subTest(block=block.splitlines()[0]):
                self.assertEqual(block.count('    start_position => "beginning"\n'), 1)
                self.assertNotIn("sincedb_path", block)

    def test_no_http_output(self):
        # no SENSOR mode any more: Fluent Bit sends a SENSOR's logs, no pipeline sends events over http
        # (the http inputs of 11-input-sensors.conf take them on the HIVE)
        for path in sorted(PIPELINE_DIR.glob("*.conf")):
            with self.subTest(file=path.name):
                for output in top_level_blocks(uncommented(path), "output"):
                    self.assertNotRegex(output, r"(?m)^\s*http\s*\{")
        self.assertFalse((DIST / "pipelines_sensor.yml").exists())
        self.assertFalse((PIPELINE_DIR / "13-input-heartbeat.conf").exists())

    def test_dockerfile_copies_exist(self):
        dockerfile = (DIST.parent / "Dockerfile").read_text(encoding="utf-8")
        sources = re.findall(r"^COPY (dist/\S*) ", dockerfile, re.M)
        self.assertGreater(len(sources), 4)
        for source in sources:
            with self.subTest(source=source):
                self.assertTrue((DIST.parent / source).exists(), f"{source} is not in the checkout")

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
        found = logstash_conf.pipelines()
        self.assertEqual({key: value for key, value in found["files"].items() if key != "path.config"},
                         {"pipeline.id": "files", "pipeline.ecs_compatibility": "disabled",
                          "pipeline.workers": "1"})
        self.assertNotIn("pipeline.workers", found["process"])
        self.assertEqual({key: value for key, value in found["sensors"].items()
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


def filter_blocks(code):
    """The bodies of the top-level filter {} sections of a part file (without comments), in order."""
    return top_level_blocks(code, "filter")


def top_level_blocks(code, section):
    """The bodies of the top-level sections (input, filter, output) of a part file, in order."""
    blocks = []
    for found in re.finditer(r"^" + section + r" \{", code, re.M):
        depth, position = 1, found.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(code[position], 0)
            position += 1
        blocks.append(code[found.end():position - 1])
    return blocks


def golden_documents():
    with open(GOLDEN, encoding="utf-8") as handle:
        return {entry["name"]: entry["event"] for entry in map(json.loads, handle)}


if __name__ == "__main__":
    unittest.main()
