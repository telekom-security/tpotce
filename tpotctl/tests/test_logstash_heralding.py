"""The Logstash csv filter for Heralding (docker/elk/logstash/dist): auth.csv of the T-Pot fork
has one column per field, so the filter must name every column in the order of the fork, or the
last value lands in a field `column11` and a new column of the fork shifts nothing into place.
"""

import csv
import io
import os
import re
import unittest
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CONFS = [ROOT / "docker" / "elk" / "logstash" / "dist" / name for name in ("logstash.conf", "http_output.conf")]

# The header of auth.csv as the fork writes it: AUTH_FIELDS of heralding/reporting/file_sink.py,
# github.com/t3chn0m4g3/heralding master 8a21ca0cbeeeb53b05b20f773f1155ef13f78a73 (2.0.0).
# The fork only appends new columns at the end.
HEADER = ("timestamp", "auth_id", "session_id", "source_ip", "source_port", "destination_ip",
          "destination_port", "protocol", "username", "password", "password_hash")

# the field names T-Pot gives these columns in Elasticsearch
FIELDS = {"source_ip": "src_ip", "source_port": "src_port", "destination_ip": "dest_ip",
          "destination_port": "dest_port", "protocol": "proto"}

SAMPLE = (",".join(HEADER) + "\n"
          "2026-10-16 08:15:42.123456,a1,s1,198.51.100.7,40001,172.18.0.5,22,ssh,root,123456,\n"
          "2026-10-16 08:15:43.654321,a2,s2,198.51.100.8,40002,172.18.0.5,5900,vnc,,,$vnc$*0011*FFEE\n")


def heralding_block(conf):
    """The filter block `if [type] == "Heralding"` up to its closing brace."""
    text = conf.read_text(encoding="utf-8")
    start = text.index('  if [type] == "Heralding" {')
    return text[start:text.index("\n  }\n", start) + 4]


def columns(conf):
    found = re.search(r"csv \{\s*columns => \[(.*?)\]", heralding_block(conf), re.S)
    return re.findall(r'"([^"]+)"', found.group(1))


class HeraldingCsvTest(unittest.TestCase):

    def test_every_column_of_the_fork_has_its_name(self):
        expected = [FIELDS.get(name, name) for name in HEADER]
        for conf in CONFS:
            with self.subTest(conf=conf.name):
                self.assertEqual(columns(conf), expected)

    def test_rows_fill_the_named_fields_only(self):
        for conf in CONFS:
            names = columns(conf)
            for row in list(csv.reader(io.StringIO(SAMPLE)))[1:]:
                with self.subTest(conf=conf.name, row=row[1]):
                    self.assertEqual(len(row), len(names))
                    event = dict(zip(names, row))
                    self.assertEqual(event["proto"], row[7])
                    self.assertEqual(event["password_hash"], row[10])

    def test_the_header_row_is_dropped(self):
        for conf in CONFS:
            with self.subTest(conf=conf.name):
                header = next(csv.reader(io.StringIO(SAMPLE)))
                self.assertEqual(dict(zip(columns(conf), header))["timestamp"], "timestamp")
                self.assertRegex(heralding_block(conf), r'if \[timestamp\] == "timestamp" \{\s*drop \{\}')

    def test_both_pipelines_agree(self):
        self.assertEqual(*(heralding_block(conf) for conf in CONFS))


if __name__ == "__main__":
    unittest.main()
