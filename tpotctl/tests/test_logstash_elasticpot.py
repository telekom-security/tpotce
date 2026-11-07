"""The Logstash filter for ElasticPot (docker/elk/logstash/dist): the honeypot writes its timestamp with
two offsets in a row (2026-11-07T10:00:00.123456+00:00Z), which is no date Elasticsearch takes. Both
pipelines have to normalise it before they parse it, the HIVE one (logstash.conf) and the SENSOR one
(http_output.conf): a sensor that sent it on as it was left a timestamp field of no date format.
"""

import json
import os
import re
import unittest
from datetime import datetime
from pathlib import Path

from tpotctl.checks import fixtures

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CONFS = [ROOT / "docker" / "elk" / "logstash" / "dist" / name for name in ("logstash.conf", "http_output.conf")]
HEADER = 'if [type] == "ElasticPot"'

# a date Elasticsearch takes for a field that is mapped as a date (dynamic date detection of
# tpot-template.json: strict_date_optional_time): one offset, not two of them (as in
# docker/_tests/tests/logstash_fixtures.sh)
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

    def test_both_pipelines_fix_it_before_they_parse_it(self):
        for conf in CONFS:
            with self.subTest(conf=conf.name):
                text = block(conf)
                fixes = GSUB.findall(text)
                self.assertTrue(fixes, f"{conf.name} leaves the timestamp of ElasticPot as it is")
                self.assertLess(text.index("gsub"), text.index("date {"), "the date is parsed before the fix")
                stamp = written_timestamp()
                for pattern, replacement in fixes:
                    stamp = re.sub(pattern, replacement, stamp)
                self.assertEqual(stamp, "2026-11-07T10:00:00.123456+00:00")
                self.assertTrue(ES_DATE.match(stamp))

    def test_both_pipelines_agree(self):
        blocks = [" ".join(block(conf).split()) for conf in CONFS]
        self.assertEqual(*blocks)


if __name__ == "__main__":
    unittest.main()
