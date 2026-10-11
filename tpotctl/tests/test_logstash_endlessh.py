"""The Logstash grok for Endlessh (docker/elk/logstash/dist): every ACCEPT and CLOSE line of
endlessh.log must match, as a line that does not is dropped (_grokparsefailure). A tarpit keeps
clients for minutes or hours, so the duration of a CLOSE line is far beyond 60 seconds.
"""

import os
import re
import unittest
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CONFS = [ROOT / "docker" / "elk" / "logstash" / "dist" / name for name in ("logstash.conf", "http_output.conf")]

# the grok patterns the Endlessh block uses, as Logstash defines them (TIMESTAMP_ISO8601 shortened)
GROK = {
    "TIMESTAMP_ISO8601": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
    "SPACE": r"\s*",
    "WORD": r"\b\w+\b",
    "IPV4": r"(?:\d{1,3}\.){3}\d{1,3}",
    "INT": r"[+-]?\d+",
    "NUMBER": r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)",
    "SECOND": r"(?:[0-5]?[0-9]|60)(?:[:.,][0-9]+)?",
}

LINES = [
    "2026-10-11T08:00:00.000Z ACCEPT host=192.0.2.10 port=51234 fd=7 n=12/4096",
    "2026-10-11T08:00:20.015Z CLOSE host=192.0.2.10 port=51234 fd=7 time=20.015 bytes=246",
    "2026-10-11T08:21:01.002Z CLOSE host=192.0.2.11 port=40000 fd=8 time=61.002 bytes=1024",
    "2026-10-12T08:00:00.123Z CLOSE host=192.0.2.12 port=40001 fd=9 time=86400.123 bytes=262144",
]


def endlessh_patterns(conf):
    """The grok patterns of the filter block `if [type] == "Endlessh"`."""
    text = conf.read_text(encoding="utf-8")
    block = text[text.index('if [type] == "Endlessh"'):]
    grok = re.search(r'grok \{ match => \{ "message" => \[(.*?)\] \} \}', block, re.S).group(1)
    return re.findall(r'"((?:[^"\\]|\\.)*)"', grok)


def to_regex(pattern):
    def name(match):
        return "(?:" + GROK[match.group(1)] + ")"
    return re.compile(re.sub(r"%\{(\w+)(?::[\w@\[\]]+)?(?::\w+)?\}", name, pattern.replace("\\A", r"\A")))


class EndlesshGrokTest(unittest.TestCase):

    def test_every_line_matches(self):
        for conf in CONFS:
            patterns = [to_regex(p) for p in endlessh_patterns(conf)]
            self.assertEqual(len(patterns), 2, conf.name)
            for line in LINES:
                with self.subTest(conf=conf.name, line=line):
                    self.assertTrue(any(p.match(line) for p in patterns), f"{conf.name} drops: {line}")

    def test_duration_is_a_number(self):
        for conf in CONFS:
            with self.subTest(conf=conf.name):
                self.assertIn("time=%{NUMBER:duration:float}", " ".join(endlessh_patterns(conf)))

    def test_both_pipelines_agree(self):
        self.assertEqual(*(endlessh_patterns(conf) for conf in CONFS))


if __name__ == "__main__":
    unittest.main()
