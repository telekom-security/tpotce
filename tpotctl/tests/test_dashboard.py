"""The Status page: CPU / memory / disk, the attacks from Elasticsearch, the honeycomb."""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import events, ops, system  # noqa: E402

try:
    import rich  # noqa: F401
except ImportError:
    rich = None

STAT_1 = "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4\n"
STAT_2 = "cpu  150 0 150 900 0 0 0 0 0 0\ncpu0 1 2 3 4\n"
MEMINFO = "MemTotal:        8000000 kB\nMemFree:          500000 kB\nMemAvailable:    2000000 kB\n"


class SystemTest(unittest.TestCase):

    def test_parsers(self):
        self.assertEqual(system.parse_stat(STAT_1), (200, 1000))
        memory = system.parse_meminfo(MEMINFO)
        self.assertEqual((memory.used, memory.total), (6000000 * 1024, 8000000 * 1024))
        self.assertAlmostEqual(memory.percent, 75.0)
        self.assertIsNone(system.parse_meminfo("nothing"))
        self.assertEqual(system.parse_uptime("3542.29 13950.64\n"), 3542.29)
        self.assertEqual(system.parse_loadavg("0.42 0.38 0.30 2/412 12345\n"), 0.42)
        self.assertIsNone(system.parse_uptime(""))
        self.assertIsNone(system.parse_loadavg("busy"))

    def test_meter_needs_two_readings_for_the_cpu(self):
        proc = tempfile.mkdtemp()
        for name, text in (("meminfo", MEMINFO), ("uptime", "600.5 1200.0\n"), ("loadavg", "1.50 1 1 1/1 1\n")):
            with open(os.path.join(proc, name), "w") as handle:
                handle.write(text)
        meter = system.Meter(proc)
        for text, cpu in ((STAT_1, None), (STAT_2, 50.0)):
            with open(os.path.join(proc, "stat"), "w") as handle:
                handle.write(text)
            reading = meter.read({"TPOT_DATA_PATH": proc}, repo_dir=proc)
            self.assertEqual(reading.cpu, cpu)
        self.assertIsNotNone(reading.disk)
        self.assertEqual(reading.disk_path, proc)
        self.assertEqual((reading.uptime, reading.load), (600.5, 1.5))
        self.assertGreaterEqual(reading.cpus, 1)

    def test_data_path_and_a_missing_folder(self):
        self.assertEqual(system.data_path({"TPOT_DATA_PATH": "./data"}, "/home/t/tpotce"), "/home/t/tpotce/data")
        self.assertEqual(system.data_path({}, "/r"), "/r/data")
        self.assertIsNotNone(system.disk("/no/such/folder/yet"))      # its parents count

    def test_human(self):
        self.assertEqual(system.human(512), "512 B")
        self.assertEqual(system.human(5 * 1024 ** 3), "5.0 GB")
        self.assertEqual(system.human(256 * 1024 ** 3), "256 GB")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class EventsTest(unittest.TestCase):

    def test_query_leaves_out_the_nsm_tools(self):
        query = json.dumps(events.query())
        self.assertIn('"Suricata"', query)
        self.assertIn('"P0f"', query)
        self.assertIn("type.keyword", query)
        self.assertEqual(events.query()["aggs"]["latest"], {"max": {"field": "@timestamp"}})
        self.assertEqual(events.query()["aggs"]["hour"]["aggs"]["sources"],
                         {"cardinality": {"field": "src_ip.keyword"}})     # the field of tpot attackers

    def test_parse_and_fetch(self):
        answer = {"hits": {"total": {"value": 4242}},
                  "aggregations": {"hour": {"minutes": {"buckets": [{"doc_count": n} for n in range(61)]}},
                                   "top": {"buckets": [{"key": "Cowrie", "doc_count": 99}]},
                                   "latest": {"value": 1791460000123.0}}}
        answer["aggregations"]["hour"]["sources"] = {"value": 312}
        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, json.loads(request.data)))
            return FakeResponse(json.dumps(answer).encode())

        attacks = events.fetch("http://es:9200", opener)
        self.assertEqual(seen[0][0], "http://es:9200/logstash-*/_search")
        self.assertEqual(attacks.last_day, 4242)
        self.assertEqual(len(attacks.per_minute), 60)
        self.assertEqual(attacks.top, [("Cowrie", 99)])
        self.assertAlmostEqual(attacks.latest, 1791460000.123)
        self.assertEqual(attacks.sources, 312)

    def test_no_attack_yet(self):
        attacks = events.parse({"hits": {"total": 0}, "aggregations": {"latest": {"value": None}}})
        self.assertIsNone(attacks.latest)
        self.assertEqual(attacks.sources, 0)

    def test_unreachable(self):
        def opener(request, timeout):
            raise OSError("connection refused")
        self.assertIn("not reachable", events.fetch(opener=opener).problem)

    def test_sparkline(self):
        self.assertEqual(events.sparkline([0, 4, 8], "▁▂▃▄▅▆▇█"), [" ▄█"])
        top, bottom = events.sparkline([0, 8, 16], "▁▂▃▄▅▆▇█", rows=2)
        self.assertEqual((top, bottom), ("  █", " ██"))
        self.assertEqual(events.sparkline([0, 0], "abc"), ["  "])


def container(name, state="running", health=""):
    return ops.Container(name, state, "", health, "", "")


@unittest.skipUnless(rich, "Rich is not installed")
class HoneycombTest(unittest.TestCase):

    def setUp(self):
        try:
            from tpotctl.widgets import comb
        except ImportError:
            self.skipTest("Textual is not installed")
        self.comb = comb

    def test_states(self):
        self.assertEqual(ops.cell_state(container("a")), ("on", "ok"))
        self.assertEqual(ops.cell_state(container("a", health="unhealthy")), ("on", "error"))
        self.assertEqual(ops.cell_state(container("a", health="starting")), ("on", "warn"))
        self.assertEqual(ops.cell_state(container("a", "restarting")), ("warn", "error"))
        self.assertEqual(ops.cell_state(container("a", "exited")), ("off", "error"))
        self.assertEqual(ops.cell_state(container("a", "created")), ("off", "mist"))

    def test_every_second_row_is_shifted(self):
        rows = self.comb.render([container(f"c{i}") for i in range(9)], 50).plain.split("\n")
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[1].startswith(" " * (self.comb.CELL // 2)))
        self.assertFalse(rows[0].startswith(" "))

    def test_packed_comb_names_only_the_problems(self):
        many = [container(f"honeypot{i}") for i in range(40)] + [container("p0f", "restarting")]
        self.assertFalse(self.comb.fits_names(len(many), 60))
        packed = self.comb.render(many, 60, names=False).plain
        self.assertNotIn("honeypot1", packed)
        legend = self.comb.legend(many, name_problems=True).plain
        self.assertIn("p0f  restarting", legend)
        self.assertIn("40 running", legend)


if __name__ == "__main__":
    unittest.main()
