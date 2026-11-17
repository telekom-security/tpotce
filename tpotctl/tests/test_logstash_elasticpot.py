"""The Logstash filter for ElasticPot (docker/elk/logstash/dist/pipeline/40-parse-elasticpot.conf): the
honeypot writes its timestamp with two offsets in a row (2026-11-07T10:00:00.123456+00:00Z), which is no
date Elasticsearch takes. The filter has to normalise it before it parses it, on the HIVE and on a SENSOR
(both process with the same file): a sensor that sent it on as it was left a timestamp field of no date format.
"""

import json
import re
import unittest
from datetime import datetime

from tpotctl.checks import fixtures
from tpotctl.tests import logstash_conf

CONF = logstash_conf.parse_file("ElasticPot")
HEADER = 'if [type] == "ElasticPot"'

# a date Elasticsearch takes for a field that is mapped as a date (dynamic date detection of the
# template logstash, docker/tpotinit/dist/etc/ilm/logstash.json: strict_date_optional_time): one
# offset, not two of them (as in docker/_tests/tests/logstash_fixtures.sh)
ES_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}(:\d{2}(:\d{2}([.,]\d{1,9})?)?)?(Z|[+-]\d{2}(:?\d{2})?)?)?)?)?$")
GSUB = re.compile(r'"timestamp",\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"')


def block(conf, header=HEADER):
    """The filter block that starts with `header`, up to its closing brace (quoted strings are skipped)."""
    text = conf.read_text(encoding="utf-8")
    start = text.index(header)
    depth, quoted = 0, False
    for position in range(text.index("{", start), len(text)):
        char = text[position]
        if char == '"' and text[position - 1] != "\\":
            quoted = not quoted
        elif not quoted:
            depth += (char == "{") - (char == "}")
            if depth == 0:
                return text[start:position + 1]
    raise ValueError(f"{conf.name}: the block {header} is not closed")


def written_timestamp():
    """The timestamp as the honeypot writes it (the fixture of the checks is a real sample event)."""
    line = fixtures.render("elasticpot", datetime(2026, 11, 7, 10, 0, 0, 123456), "192.0.2.10", 51234, 9200, "token1")
    return json.loads(line)["timestamp"]


class ElasticPotTimestampTest(unittest.TestCase):

    def test_the_honeypot_writes_two_offsets(self):
        stamp = written_timestamp()
        self.assertEqual(stamp, "2026-11-07T10:00:00.123456+00:00Z")
        self.assertIsNone(ES_DATE.match(stamp), "Elasticsearch takes this date; the filters are not needed")

    def test_the_filter_fixes_it_before_it_parses_it(self):
        text = block(CONF)
        fixes = GSUB.findall(text)
        self.assertTrue(fixes, f"{CONF.name} leaves the timestamp of ElasticPot as it is")
        self.assertLess(text.index("gsub"), text.index("date {"), "the date is parsed before the fix")
        stamp = written_timestamp()
        for pattern, replacement in fixes:
            stamp = re.sub(pattern, replacement, stamp)
        self.assertEqual(stamp, "2026-11-07T10:00:00.123456+00:00")
        self.assertTrue(ES_DATE.match(stamp))

    def test_one_filter_for_both_pipelines(self):
        for sensor in (False, True):
            self.assertIn(CONF, logstash_conf.pipeline_files("process", sensor))


if __name__ == "__main__":
    unittest.main()
