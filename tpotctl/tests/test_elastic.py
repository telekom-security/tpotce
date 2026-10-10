"""The retention rule of the ILM policy tpot (tpotctl/elastic.py).

The cases in docker/tpotinit/tests/ilm_cases.yml are the ones the jq rule of tpotinit
(docker/tpotinit/dist/bin/es_retention.sh) passes as well: docker/_tests/run.sh tpotinit_ilm.
"""

import copy
import io
import json
import os
import unittest
import urllib.error

import yaml

from tpotctl import elastic

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CASES = os.path.join(REPO_DIR, "docker", "tpotinit", "tests", "ilm_cases.yml")


def load_cases():
    with open(CASES, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def with_min_age(min_age):
    if min_age == "none":
        return {"phases": {"hot": {"min_age": "0ms", "actions": {}}}}
    return {"phases": {"hot": {"min_age": "0ms", "actions": {}},
                       "delete": {"min_age": min_age, "actions": {"delete": {}}}}}


class RuleTest(unittest.TestCase):

    def test_value_of(self):
        for item in load_cases()["values"]:
            with self.subTest(min_age=item["min_age"]):
                self.assertEqual(elastic.value_of(with_min_age(item["min_age"])), item["value"])
        self.assertEqual(elastic.value_of(None), "")

    def test_shapes(self):
        for item in load_cases()["shapes"]:
            with self.subTest(shape=item["name"]):
                self.assertEqual(elastic.value_of(item["policy"]), item["value"])

    def test_value_of_command_rejects_what_is_no_policy(self):
        import subprocess
        import sys
        import tempfile
        for text in ("[]", json.dumps({"tpot": {"policy": {"phases": {}}}})):
            with self.subTest(text=text):
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                    handle.write(text)
                try:
                    result = subprocess.run([sys.executable, "-m", "tpotctl.elastic", "value-of", handle.name],
                                            cwd=REPO_DIR, capture_output=True, text=True)
                finally:
                    os.unlink(handle.name)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_shared_cases(self):
        for case in load_cases()["cases"]:
            with self.subTest(case=case["name"]):
                before = copy.deepcopy(case["policy"])
                decision = elastic.decide(case["value"], case["policy"], force=case.get("force", False))
                self.assertEqual(decision.action, case["action"])
                self.assertEqual(decision.current, case["current"])
                self.assertEqual(decision.managed, case["managed"])
                self.assertEqual(case["policy"], before, "decide must not change its input")
                if case["action"] in ("create", "update"):
                    policy = decision.policy
                    delete = policy["phases"].get("delete")
                    self.assertEqual(delete["min_age"] if delete else "none", case["min_age"])
                    self.assertEqual(policy["_meta"]["tpot_retention"], case["retention"])
                    self.assertTrue(policy["_meta"]["managed"])
                    self.assertEqual(sorted(policy["phases"]), case["phases"])
                    if delete:
                        self.assertIn("delete", delete["actions"])
                    json.dumps(policy)                     # PUT-able as it is
                else:
                    self.assertIsNone(decision.policy)

    def test_update_keeps_everything_but_delete_and_meta(self):
        case = next(c for c in load_cases()["cases"] if c["name"].startswith("managed with a warm phase"))
        policy = elastic.decide(case["value"], case["policy"]).policy
        self.assertEqual(policy["phases"]["warm"], case["policy"]["phases"]["warm"])
        self.assertEqual(policy["phases"]["hot"], case["policy"]["phases"]["hot"])
        self.assertEqual(policy["phases"]["delete"]["actions"], case["policy"]["phases"]["delete"]["actions"])

    def test_shortens(self):
        self.assertTrue(elastic.shortens("90", "30"))
        self.assertTrue(elastic.shortens("off", "3650"))
        self.assertTrue(elastic.shortens("", "30"))
        self.assertFalse(elastic.shortens("30", "90"))
        self.assertFalse(elastic.shortens("30", "off"))
        self.assertFalse(elastic.shortens("30", "30"))
        self.assertFalse(elastic.shortens("30", ""))

    def test_value_of_command_reads_the_export_of_update_sh(self):
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"policy": with_min_age("365d")}, handle)
        try:
            out = subprocess.run([__import__("sys").executable, "-m", "tpotctl.elastic", "value-of", handle.name],
                                 cwd=REPO_DIR, capture_output=True, text=True, check=True).stdout
        finally:
            os.unlink(handle.name)
        self.assertEqual(out.strip(), "365")


class WiringTest(unittest.TestCase):

    def read(self, *parts):
        with open(os.path.join(REPO_DIR, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_tpotinit_applies_the_rule_on_a_hive_with_elasticsearch(self):
        text = self.read("docker", "tpotinit", "dist", "entrypoint.sh")
        start = text.index("touch /tmp/success")
        self.assertIn("/opt/tpot/bin/es_retention.sh", text[start:])
        block = text[text.index("/opt/tpot/bin/es_retention.sh") - 300:text.index("/opt/tpot/bin/es_retention.sh")]
        self.assertIn('"${TPOT_TYPE}" == "HIVE"', block)
        self.assertIn("elasticsearch", block)

    def test_logstash_no_longer_writes_the_policy(self):
        text = self.read("docker", "elk", "logstash", "dist", "entrypoint.sh")
        self.assertNotIn("_ilm/policy", text)
        self.assertIn("lifecycle", self.read("docker", "elk", "logstash", "dist", "tpot-template.json"))


class FakeES:
    """urlopen for tests: routes (method, path) -> (status, body); records the requests."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, request, timeout=None):
        path = request.full_url.split("64298", 1)[1]
        self.requests.append((request.get_method(), path, request.data))
        key = (request.get_method(), path.split("?", 1)[0])
        if key not in self.routes:
            raise urllib.error.URLError("connection refused")
        status, body = self.routes[key]
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(json.dumps(body).encode()))
        return io.BytesIO(json.dumps(body).encode())       # a context manager, as urlopen's answer


MANAGED_90 = {"phases": {"hot": {"min_age": "0ms", "actions": {}},
                         "delete": {"min_age": "90d", "actions": {"delete": {}}}},
              "_meta": {"managed": True, "tpot_retention": "90"}}
NOW = 1_800_000_000.0
DAY = 86400
INDICES = [{"index": "logstash-old", "creation.date": str(int((NOW - 60 * DAY) * 1000)), "docs.count": "1200",
            "store.size": "5000"},
           {"index": "logstash-new", "creation.date": str(int((NOW - 2 * DAY) * 1000)), "docs.count": "30",
            "store.size": "100"}]


class AccessTest(unittest.TestCase):

    def es(self, policy=MANAGED_90):
        routes = {("GET", "/_cat/indices/logstash-*"): (200, INDICES), ("PUT", "/_ilm/policy/tpot"): (200, {})}
        routes[("GET", "/_ilm/policy/tpot")] = (200, {"tpot": {"policy": policy}}) if policy else (404, {})
        return FakeES(routes)

    def test_status_managed_and_foreign(self):
        self.assertEqual(elastic.status("90", opener=self.es()).state, "managed")
        foreign = dict(MANAGED_90, _meta={})
        self.assertEqual(elastic.status("90", opener=self.es(foreign)).state, "foreign")
        self.assertEqual(elastic.status("30", opener=self.es(None)).state, "missing")
        down = elastic.status("30", opener=FakeES({}))
        self.assertEqual(down.state, "unreachable")
        self.assertIn("Elasticsearch", down.problem)

    def test_plan_shows_what_a_shorter_retention_deletes(self):
        plan = elastic.plan("30", opener=self.es(), now=NOW)
        self.assertEqual(plan.decision.action, "update")
        self.assertEqual([i.name for i in plan.doomed], ["logstash-old"])
        self.assertEqual(plan.doomed[0].docs, 1200)

    def test_plan_longer_deletes_nothing_and_off_neither(self):
        self.assertEqual(elastic.plan("365", opener=self.es(), now=NOW).doomed, [])
        self.assertEqual(elastic.plan("off", opener=self.es(), now=NOW).doomed, [])

    def test_apply_puts_only_on_create_or_update(self):
        es = self.es()
        elastic.apply(elastic.plan("90", opener=es, now=NOW), opener=es)
        self.assertNotIn("PUT", [m for m, _p, _d in es.requests])
        elastic.apply(elastic.plan("30", opener=es, now=NOW), opener=es)
        method, path, data = es.requests[-1]
        self.assertEqual((method, path), ("PUT", "/_ilm/policy/tpot"))
        self.assertEqual(json.loads(data)["policy"]["phases"]["delete"]["min_age"], "30d")

    def test_apply_refuses_a_foreign_policy(self):
        foreign = dict(MANAGED_90, _meta={})
        es = self.es(foreign)
        with self.assertRaises(elastic.ElasticError):
            elastic.apply(elastic.plan("30", opener=es, now=NOW), opener=es)
        elastic.apply(elastic.plan("30", force=True, opener=es, now=NOW), opener=es)
        self.assertEqual(es.requests[-1][0], "PUT")

    def test_unreachable_plan(self):
        plan = elastic.plan("30", opener=FakeES({}), now=NOW)
        self.assertFalse(plan.reachable)
        with self.assertRaises(elastic.ElasticError):
            elastic.apply(plan, opener=FakeES({}))


class CliTest(unittest.TestCase):

    def test_parser_knows_elastic(self):
        from tpotctl import cli
        args = cli.build_parser().parse_args(["elastic", "retention", "60", "-y"])
        self.assertEqual((args.command, args.elastic_command, args.value, args.yes), ("elastic", "retention", "60", True))
        self.assertEqual(cli.build_parser().parse_args(["elastic"]).command, "elastic")

    def test_retention_value_must_be_valid_before_anything_is_asked(self):
        from tpotctl import cli
        args = cli.build_parser().parse_args(["elastic", "retention", "30d", "-y"])
        self.assertEqual(cli.retention_value_problem(args.value), '"30d" is not a number of days from 1 to 3650 or off.')
        self.assertEqual(cli.retention_value_problem("off"), "")


if __name__ == "__main__":
    unittest.main()
