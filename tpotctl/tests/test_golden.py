"""The golden Logstash documents of every fixture (docker/_tests/golden, docker/_tests/lib/golden.py)."""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from tpotctl.checks import fixtures
from tpotctl.tests import isolate

isolate()

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "docker" / "_tests" / "golden" / "logstash_fixtures.jsonl"
sys.path.insert(0, str(REPO / "docker" / "_tests" / "lib"))

import golden  # noqa: E402


class GoldenTest(unittest.TestCase):
    def test_normalize_drops_volatile_fields(self):
        e = {"@version": "1", "host": "x", "headers": {}, "t-pot_sensor": "s", "tags": ["b", "a"], "src_ip": "8.8.8.8"}
        self.assertEqual(golden.normalize(e), {"tags": ["a", "b"], "src_ip": "8.8.8.8"})

    def test_normalize_keeps_honeypot_path_but_ignores_file_path(self):
        self.assertEqual(golden.normalize({"path": "/wp-login.php", "t-pot_log_path": "/data/wordpot/log/wordpot.json"}),
                         {"path": "/wp-login.php"})
        self.assertNotIn("path", golden.normalize({"path": "/data/wordpot/log/wordpot.json"}))
        # a file of an input that reads a glob is a source path too, a path below it is none
        self.assertNotIn("path", golden.normalize({"path": "/data/ddospot/log/dnspot.log"}))
        self.assertEqual(golden.normalize({"path": "/data/ddospot/log/x/dnspot.log"}),
                         {"path": "/data/ddospot/log/x/dnspot.log"})

    def test_normalize_sorts_the_keys_of_a_json_object_logstash_dumped(self):
        # LogStash::Json.dump writes the keys of a hash in no stable order (Honeypots data)
        dumped = {"data": '{"uri":"/x","Host":"h","m":{"b":[2,{"d":1,"c":"\u00e9"}],"a":null}}'}
        self.assertEqual(golden.normalize(dumped),
                         {"data": '{"Host":"h","m":{"a":null,"b":[2,{"c":"\u00e9","d":1}]},"uri":"/x"}'})
        # any other string stays as it is: spaces, an array, a plain text
        for kept in ('{"b": 1, "a": 2}', '[{"b":1,"a":2}]', "{not json", "GET /"):
            self.assertEqual(golden.normalize({"data": kept}), {"data": kept})

    def test_normalize_keeps_a_volatile_key_the_honeypot_logs_itself(self):
        gopot = {"@version": "1", "host": "192.0.2.1:8085", "src_ip": "8.8.8.8"}
        self.assertEqual(golden.normalize(gopot, payload=("host", "src_ip")),
                         {"host": "192.0.2.1:8085", "src_ip": "8.8.8.8"})
        self.assertEqual(golden.normalize(gopot), {"src_ip": "8.8.8.8"})
        # the host the file input adds goes, the headers log4pot logs stay
        log4pot = {"headers": {"Host": "192.0.2.1", "User-Agent": "goldenlog4pot"}, "host": "tpot-fixtures"}
        self.assertEqual(golden.normalize(log4pot, payload=("client", "headers")),
                         {"headers": {"Host": "192.0.2.1", "User-Agent": "goldenlog4pot"}})
        self.assertEqual(golden.normalize(log4pot), {})

    def test_payload_keys_are_the_top_level_keys_of_a_json_fixture(self):
        self.assertIn("host", golden.payload_keys("go-pot"))
        self.assertIn("host", golden.payload_keys("honeyaml"))
        self.assertIn("headers", golden.payload_keys("log4pot"))
        self.assertNotIn("host", golden.payload_keys("cowrie"))
        self.assertEqual(golden.payload_keys("endlessh"), ())   # no JSON line

    def test_write_keeps_the_payload_host_of_its_fixture_only(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            events = [{"type": "Go-pot", "host": "192.0.2.1:8085", "path": "/goldengopot", "@version": "1"},
                      {"type": "Endlessh", "host": "tpot-fixtures", "src_port": 41000}]
            (folder / "events.json").write_text("".join(golden.json.dumps(e) + "\n" for e in events))
            (folder / "expect.tsv").write_text("endlessh\tgoldenendlessh\t41000\ngo-pot\tgoldengopot\t42000\n")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = golden.main(["write", "--golden", str(folder / "golden.jsonl"),
                                  "--events", str(folder / "events.json"), "--expect", str(folder / "expect.tsv")])
            self.assertEqual(rc, 0)
            self.assertEqual(golden.load(folder / "golden.jsonl"),
                             {"endlessh": {"type": "Endlessh", "src_port": 41000},
                              "go-pot": {"type": "Go-pot", "host": "192.0.2.1:8085", "path": "/goldengopot"}})

    def test_match_by_token_and_port(self):
        got = golden.match([{"m": "goldencowrie"}, {"src_port": 41000}], [("cowrie", "goldencowrie", 1), ("dicompot", "golden", 41000)])
        self.assertEqual(set(got), {"cowrie", "dicompot"})

    def test_match_gives_an_event_to_the_longest_token_it_carries(self):
        got = golden.match([{"src_port": 41000}, {"m": "goldencowrie"}],
                           [("dicompot", "golden", 41000), ("cowrie", "goldencowrie", 1)])
        self.assertEqual(got, {"cowrie": {"m": "goldencowrie"}, "dicompot": {"src_port": 41000}})

    def test_match_refuses_an_event_of_no_fixture_or_a_second_one(self):
        expect = [("cowrie", "goldencowrie", 1)]
        for events in ([{"m": "other"}], [{"m": "goldencowrie"}, {"src_port": 1}]):
            with self.subTest(events=events), self.assertRaises(golden.MatchError):
                golden.match(events, expect)

    def test_compare_reports_field(self):
        self.assertEqual(golden.compare({"a": {"x": 1}}, {"a": {"x": 2}}), ["a: x: 1 != 2"])

    def test_compare_reports_missing_and_extra_fields_and_fixtures(self):
        self.assertEqual(golden.compare({"a": {"x": 1}, "b": {}}, {"a": {"y": 1}, "c": {}}),
                         ["a: x: 1 != (missing)", "a: y: (missing) != 1", "b: no event", "c: not in the golden file"])

    def test_dump_is_stable(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder, "first.jsonl"), Path(folder, "second.jsonl")
            golden.dump(first, {"b": {"y": 1, "x": [2, 1]}, "a": {"z": "ü"}})
            golden.dump(second, {"a": {"z": "ü"}, "b": {"x": [2, 1], "y": 1}})
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(golden.load(first), {"a": {"z": "ü"}, "b": {"x": [2, 1], "y": 1}})

    def test_golden_file_covers_every_fixture(self):
        self.assertEqual(set(golden.load(GOLDEN)), set(fixtures.names()))


if __name__ == "__main__":
    unittest.main()
