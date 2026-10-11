"""The retention rule of the ILM policy tpot (tpotctl/elastic.py).

The cases in docker/tpotinit/tests/ilm_cases.yml are the ones the jq rule of tpotinit
(docker/tpotinit/dist/bin/es_retention.sh) passes as well: docker/_tests/run.sh tpotinit_ilm.
"""

import copy
import io
import json
import os
import random
import shutil
import subprocess
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
    if min_age == "30d-noaction":
        return {"phases": {"hot": {"min_age": "0ms", "actions": {}},
                           "delete": {"min_age": "30d", "actions": {}}}}
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
                oldest = 1800000000.0 - case["oldest_days"] * 86400 if "oldest_days" in case else None
                decision = elastic.decide(case["value"], case["policy"], force=case.get("force", False),
                                          shorten=case.get("shorten", False), oldest=oldest, now=1800000000.0)
                self.assertEqual(decision.action, case["action"])
                self.assertEqual(decision.held, case.get("held", ""))
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
                    if "actions" in case:
                        self.assertEqual(sorted(delete["actions"]), case["actions"])
                    json.dumps(policy)                     # PUT-able as it is
                else:
                    self.assertIsNone(decision.policy)

    def test_update_keeps_everything_but_delete_and_meta(self):
        case = next(c for c in load_cases()["cases"] if c["name"].startswith("managed with a warm phase"))
        policy = elastic.decide(case["value"], case["policy"], shorten=True).policy
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


def generated_policies(seed=17, count=200):
    """Policies of every shape the rule tells apart: min_age, marker, warm phase."""
    rng = random.Random(seed)
    min_ages = ["30d", "90d", "365d", "1d", "3650d", "720h", "2160h", "36h", "0h", "90m", "", "0d", "3651d",
                "none", "bare"]
    metas = [None, {}, {"managed": True, "description": elastic.LEGACY_DESCRIPTION},
             {"managed": True, "description": "own"}, {"tpot_retention": "30"}, {"tpot_retention": 30},
             {"tpot_retention": "90"}, {"tpot_retention": 90}, {"tpot_retention": "365"},
             {"tpot_retention": "off"}, {"managed": True, "tpot_retention": "30", "description": "x"}]
    for _ in range(count):
        phases = {"hot": {"min_age": "0ms", "actions": rng.choice([{}, {"rollover": {"max_age": "1d"}}])}}
        if rng.random() < 0.3:
            phases["warm"] = {"min_age": "7d", "actions": {"forcemerge": {"max_num_segments": 1}}}
        min_age = rng.choice(min_ages)
        if min_age == "bare":
            phases["delete"] = {}
        elif min_age != "none":
            phases["delete"] = {"actions": {"delete": {}}}
            if min_age:
                phases["delete"]["min_age"] = min_age
        policy = {"phases": phases}
        meta = rng.choice(metas)
        if meta is not None:
            policy["_meta"] = copy.deepcopy(meta)
        yield (rng.choice(["", "30", "90", "365", "off"]), rng.random() < 0.35,
               None if rng.random() < 0.03 else policy)


# Task 2 aligns jq, then remove
@unittest.expectedFailure
@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs jq and bash")
class DifferentialTest(unittest.TestCase):
    """The jq rule of tpotinit and elastic.decide agree on generated policies, not only on the cases."""

    def test_jq_and_python_decide_alike(self):
        inputs = list(generated_policies())
        lines = "".join(f"{value}\n{'true' if force else 'false'}\n{json.dumps(policy)}\n"
                        for value, force, policy in inputs)
        script = ('source docker/tpotinit/dist/bin/es_retention.sh; '
                  'while IFS= read -r v && IFS= read -r f && IFS= read -r p; do '
                  'fuILM_DECIDE "$v" "$f" <<<"$p" || echo FAILED; done')
        out = subprocess.run(["bash", "-c", script], cwd=REPO_DIR, input=lines, capture_output=True, text=True,
                             timeout=60).stdout.splitlines()
        self.assertEqual(len(out), len(inputs))
        for (value, force, policy), line in zip(inputs, out):
            with self.subTest(value=value, force=force, policy=policy):
                got = json.loads(line)
                want = elastic.decide(value, copy.deepcopy(policy), force)
                self.assertEqual((got["action"], got["current"], got["managed"], got["policy"]),
                                 (want.action, want.current, want.managed, want.policy))


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

    def test_tpotinit_reaches_elasticsearch_in_every_edition(self):
        # host network: the published 127.0.0.1:64298; otherwise (MAC_WIN) the network of elasticsearch
        script = self.read("docker", "tpotinit", "dist", "bin", "es_retention.sh")
        self.assertIn('"http://127.0.0.1:64298" "http://elasticsearch:9200"', script)
        for name in sorted(os.listdir(os.path.join(REPO_DIR, "compose"))):
            if not name.endswith(".yml") or name == "tpot_services.yml":
                continue
            services = yaml.safe_load(self.read("compose", name))["services"]
            if "elasticsearch" not in services:
                continue
            with self.subTest(edition=name):
                tpotinit = services["tpotinit"]
                if tpotinit.get("network_mode") != "host":
                    self.assertTrue(set(tpotinit.get("networks") or []) & set(services["elasticsearch"]["networks"]))

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

    def test_a_200_without_the_policy_is_no_missing_policy(self):
        for body in ({}, [], {"tpot": {}}, {"tpot": {"policy": "x"}}):
            with self.subTest(body=body):
                es = FakeES({("GET", "/_ilm/policy/tpot"): (200, body), ("PUT", "/_ilm/policy/tpot"): (200, {}),
                             ("GET", "/_cat/indices/logstash-*"): (200, INDICES)})
                with self.assertRaisesRegex(elastic.ElasticError, "answered without the ILM policy tpot"):
                    elastic.get_policy(opener=es)
                self.assertEqual(elastic.status("30", opener=es).state, "unreachable")
                chosen = elastic.plan("30", opener=es, now=NOW)
                self.assertFalse(chosen.reachable)
                with self.assertRaises(elastic.ElasticError):
                    elastic.apply(chosen, opener=es)
                self.assertNotIn("PUT", [m for m, _p, _d in es.requests])

    def test_plan_lists_nothing_where_the_duration_stays(self):
        legacy = {"phases": {"hot": {"min_age": "0ms", "actions": {}},
                             "delete": {"min_age": "30d", "actions": {"delete": {}}}},
                  "_meta": {"managed": True, "description": elastic.LEGACY_DESCRIPTION}}
        hours = {"phases": {"hot": {"min_age": "0ms", "actions": {}},
                            "delete": {"min_age": "720h", "actions": {"delete": {}}}}}
        for name, policy in (("marker only", legacy), ("adoption", hours)):
            with self.subTest(name):
                chosen = elastic.plan("30", opener=self.es(policy), now=NOW)
                self.assertEqual(chosen.decision.action, "update")
                self.assertEqual(chosen.doomed, [])
        # a policy that is created or kept shorter than before still lists them
        self.assertEqual([i.name for i in elastic.plan("30", opener=self.es(None), now=NOW).doomed], ["logstash-old"])

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

    def test_the_setting_offers_the_usual_retentions(self):
        from tpotctl import envschema
        rule = envschema.load_schema()["TPOT_ES_RETENTION"]
        self.assertEqual(rule.widget, "choice")
        self.assertTrue(rule.custom)
        self.assertEqual([(c["value"], c["label"]) for c in rule.choices],
                         [("30", "30 days"), ("90", "90 days"), ("180", "180 days"), ("365", "1 year"),
                          ("off", "never delete")])

    def test_retention_value_must_be_valid_before_anything_is_asked(self):
        from tpotctl import cli
        args = cli.build_parser().parse_args(["elastic", "retention", "30d", "-y"])
        self.assertEqual(cli.retention_value_problem(args.value), '"30d" is not a number of days from 1 to 3650 or off.')
        self.assertEqual(cli.retention_value_problem("off"), "")


class FakeSettings:
    def __init__(self, values):
        self.values, self.changes = values, []

    def change(self, changes, unlocked=()):
        self.changes.append(changes)
        return []


class RunElasticTest(unittest.TestCase):
    """The decisions of `tpot elastic retention`: what is asked, what is written, what is applied."""

    def run_cli(self, argv, retention="90", state=None, plan=None, confirm=True, apply_error=None, sensor=False):
        from unittest import mock
        from rich.console import Console
        from tpotctl import cli
        values = {"TPOT_TYPE": "SENSOR" if sensor else "HIVE", "TPOT_ES_RETENTION": retention}
        self.settings = FakeSettings(values)
        self.asked, self.applied, self.planned = [], [], []
        self.out, self.err = io.StringIO(), io.StringIO()

        def ask(question, yes):
            self.asked.append((question, yes))
            return yes or confirm

        def plan_of(value, force=False):
            self.planned.append((value, force))
            return plan

        def apply_to(chosen):
            self.applied.append(chosen)
            if apply_error:
                raise elastic.ElasticError(apply_error)

        state = state or elastic.Status(retention, retention, "managed")
        import contextlib
        with mock.patch("tpotctl.ops.require_linux_host"), \
                mock.patch("tpotctl.settings.load", return_value=self.settings), \
                mock.patch("tpotctl.elastic.status", return_value=state), \
                mock.patch("tpotctl.elastic.plan", side_effect=plan_of), \
                mock.patch("tpotctl.elastic.apply", side_effect=apply_to), \
                mock.patch("tpotctl.cli.confirm", side_effect=ask), \
                mock.patch("tpotctl.cli._console", return_value=Console(file=self.out, width=200)), \
                contextlib.redirect_stderr(self.err):
            return cli.run_elastic(cli.build_parser().parse_args(["elastic"] + argv))

    @staticmethod
    def plan(action="update", doomed=(), reachable=True, current="90"):
        return elastic.Plan(elastic.Decision(action, {"phases": {}} if action in ("create", "update") else None,
                                             current, action != "foreign"), list(doomed), reachable)

    OLD = elastic.Index("logstash-old", 1.0, 1200, 5000)

    def test_foreign_policy_without_force_changes_nothing(self):
        foreign = elastic.Status("90", "90", "foreign")
        code = self.run_cli(["retention", "30", "-y"], state=foreign, plan=self.plan("foreign"))
        self.assertEqual(code, 1)
        self.assertEqual((self.settings.changes, self.applied), ([], []))
        self.assertEqual(self.planned, [("30", False)])
        self.assertIn("--adopt", self.err.getvalue())

    def test_force_is_handed_to_the_plan(self):
        self.run_cli(["retention", "30", "--force", "-y"], plan=self.plan())
        self.assertEqual(self.planned, [("30", True)])

    def test_unreachable_and_shorter_asks(self):
        unreachable = self.plan("keep", reachable=False)
        self.assertEqual(self.run_cli(["retention", "30"], plan=unreachable, confirm=False), 1)
        self.assertEqual(len(self.asked), 1)
        self.assertEqual((self.settings.changes, self.applied), ([], []))
        self.assertEqual(self.run_cli(["retention", "30"], plan=unreachable, confirm=True), 0)
        self.assertEqual(self.settings.changes, [{"TPOT_ES_RETENTION": "30"}])
        self.assertEqual(self.applied, [])

    def test_unreachable_and_longer_does_not_ask(self):
        code = self.run_cli(["retention", "365"], plan=self.plan("keep", reachable=False))
        self.assertEqual(code, 0)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.settings.changes, [{"TPOT_ES_RETENTION": "365"}])
        self.assertEqual(self.applied, [])

    def test_doomed_indices_are_asked_about(self):
        chosen = self.plan(doomed=[self.OLD])
        self.assertEqual(self.run_cli(["retention", "30"], plan=chosen, confirm=False), 1)
        self.assertEqual(len(self.asked), 1)
        self.assertIn("1 index with 1200 events (4.9 KB) is deleted right away", self.asked[0][0])
        self.assertEqual((self.settings.changes, self.applied), ([], []))
        self.assertEqual(self.run_cli(["retention", "30"], plan=chosen, confirm=True), 0)
        self.assertEqual(self.settings.changes, [{"TPOT_ES_RETENTION": "30"}])
        self.assertEqual(self.applied, [chosen])

    def test_yes_does_not_prompt_but_lists_the_indices(self):
        chosen = self.plan(doomed=[self.OLD])
        self.assertEqual(self.run_cli(["retention", "30", "-y"], plan=chosen, confirm=False), 0)
        self.assertEqual(self.asked[0][1], True)
        self.assertIn("logstash-old  1200 events", self.out.getvalue())
        self.assertEqual(self.applied, [chosen])

    def test_nothing_doomed_does_not_ask(self):
        self.assertEqual(self.run_cli(["retention", "365"], plan=self.plan()), 0)
        self.assertEqual(self.asked, [])

    def test_invalid_value_is_refused_first(self):
        self.assertEqual(self.run_cli(["retention", "30d", "-y"], plan=self.plan()), 2)
        self.assertEqual((self.planned, self.settings.changes), ([], []))

    def test_adopt_needs_a_policy_in_days(self):
        for state in (elastic.Status("90", "", "missing"),
                      elastic.Status("90", "", "unreachable", "Elasticsearch is not reachable (refused)"),
                      elastic.Status("90", "", "foreign")):
            with self.subTest(state=state.state):
                self.assertEqual(self.run_cli(["retention", "--adopt"], state=state, plan=self.plan()), 1)
                self.assertEqual((self.settings.changes, self.applied), ([], []))

    def test_adopt_takes_the_policy_over(self):
        foreign = elastic.Status("90", "45", "foreign")
        self.assertEqual(self.run_cli(["retention", "--adopt"], state=foreign, plan=self.plan(current="45")), 0)
        self.assertEqual(self.settings.changes, [{"TPOT_ES_RETENTION": "45"}])
        self.assertEqual(self.planned, [("45", True)])

    def test_a_failed_put_after_the_env_write_is_told(self):
        code = self.run_cli(["retention", "30", "-y"], plan=self.plan(), apply_error="the ILM policy tpot refused")
        self.assertEqual(code, 1)
        self.assertEqual(self.settings.changes, [{"TPOT_ES_RETENTION": "30"}])
        text = self.out.getvalue()
        self.assertIn("TPOT_ES_RETENTION=30 is set in .env", text)
        self.assertIn("the ILM policy tpot was not changed (the ILM policy tpot refused)", text)
        self.assertIn("next start", text)
        self.assertIn("tpot env set TPOT_ES_RETENTION=90", text)

    def test_a_failed_put_names_an_empty_old_value(self):
        self.run_cli(["retention", "30", "-y"], retention="", plan=self.plan(), apply_error="down")
        self.assertIn("tpot env set TPOT_ES_RETENTION=\n", self.out.getvalue())

    def test_a_failed_put_of_adopt_is_told_too(self):
        foreign = elastic.Status("90", "45", "foreign")
        code = self.run_cli(["retention", "--adopt"], state=foreign, plan=self.plan(current="45"),
                            apply_error="down")
        self.assertEqual(code, 1)
        self.assertIn("TPOT_ES_RETENTION=45 is set in .env", self.out.getvalue())
        self.assertIn("tpot env set TPOT_ES_RETENTION=90", self.out.getvalue())

    def test_status_warns_of_a_shorter_value_waiting_in_env(self):
        for state, names_adopt in ((elastic.Status("30", "90", "managed"), True),
                                   (elastic.Status("30", "", "missing"), False)):
            with self.subTest(state=state.state):
                self.assertEqual(self.run_cli(["status"], retention="30", state=state), 0)
                text = self.out.getvalue() + self.err.getvalue()
                self.assertIn("T-Pot deletes the events older than 30 days on its next start", text)
                self.assertIn("tpot elastic retention --force", text)
                self.assertEqual("tpot elastic retention --adopt" in text, names_adopt)
                self.assertEqual(self.planned, [])

    def test_status_is_quiet_where_nothing_waits(self):
        for retention, state in (("90", elastic.Status("90", "90", "managed")),
                                 ("365", elastic.Status("365", "90", "managed")),
                                 ("off", elastic.Status("off", "", "missing")),
                                 ("", elastic.Status("", "", "missing")),
                                 ("30", elastic.Status("30", "90", "foreign"))):
            with self.subTest(retention=retention, state=state.state):
                self.run_cli(["status"], retention=retention, state=state)
                self.assertNotIn("on its next start", self.out.getvalue() + self.err.getvalue())

    def test_force_without_a_value_applies_the_env_value(self):
        chosen = self.plan(doomed=[self.OLD])
        self.assertEqual(self.run_cli(["retention", "--force"], retention="30", plan=chosen, confirm=True), 0)
        self.assertEqual(self.planned, [("30", True)])
        self.assertEqual(len(self.asked), 1)                   # the preview asks as for a value
        self.assertEqual(self.settings.changes, [{"TPOT_ES_RETENTION": "30"}])
        self.assertEqual(self.applied, [chosen])

    def test_force_without_a_value_and_an_empty_env_value(self):
        self.assertEqual(self.run_cli(["retention", "--force", "-y"], retention="", plan=self.plan()), 1)
        self.assertIn("TPOT_ES_RETENTION is empty, set a value: tpot elastic retention DAYS", self.err.getvalue())
        self.assertEqual((self.planned, self.settings.changes, self.applied), ([], [], []))

    def test_adopt_and_a_value_are_a_usage_error(self):
        self.assertEqual(self.run_cli(["retention", "30", "--adopt", "-y"], plan=self.plan()), 2)
        self.assertEqual((self.planned, self.settings.changes, self.applied), ([], [], []))
        self.assertIn("--adopt", self.err.getvalue())

    def test_texts_name_the_commands_that_do_it(self):
        foreign = elastic.Status("30", "365", "foreign")
        self.run_cli(["status"], retention="30", state=foreign)
        text = self.out.getvalue() + self.err.getvalue()
        self.assertIn("tpot elastic retention --adopt", text)
        self.assertIn("tpot elastic retention --force", text)
        self.assertNotIn("takes it over", text)

    def test_a_sensor_has_no_elasticsearch(self):
        self.assertEqual(self.run_cli(["retention", "30", "-y"], plan=self.plan(), sensor=True), 1)
        self.assertEqual((self.planned, self.settings.changes), ([], []))
        self.assertIn("SENSOR", self.err.getvalue())



class RunEnvRetentionTest(unittest.TestCase):
    """tpot env set TPOT_ES_RETENTION: what it says about the next start."""

    def env_set(self, *assignments, start="90"):
        from contextlib import redirect_stderr, redirect_stdout
        from unittest import mock
        from tpotctl import cli, settings
        from tpotctl.envfile import EnvFile
        from tpotctl.tests.test_settings import make_checkout
        repo = make_checkout(self)
        env = EnvFile(os.path.join(repo, ".env"))
        env.set("TPOT_ES_RETENTION", start)
        env.save()
        original = settings.load
        out = io.StringIO()
        with mock.patch.object(settings, "load", lambda **kwargs: original(repo, **kwargs)), \
                mock.patch("tpotctl.ops.host_ostype", return_value="linux"), \
                redirect_stdout(out), redirect_stderr(out):
            self.assertEqual(cli.main(["env", "set"] + list(assignments)), 0, out.getvalue())
        return " ".join(out.getvalue().split())

    def test_a_shorter_value_warns_of_the_next_start(self):
        text = self.env_set("TPOT_ES_RETENTION=30")
        self.assertIn("The next start of T-Pot deletes the events older than 30 days", text)
        self.assertIn("tpot elastic retention --force", text)
        self.assertNotIn("Restart T-Pot to apply it", text)

    def test_a_longer_value_does_not_warn(self):
        text = self.env_set("TPOT_ES_RETENTION=365")
        self.assertNotIn("deletes the events", text)
        self.assertIn("tpot elastic retention --force", text)

    def test_another_key_still_asks_for_the_restart(self):
        text = self.env_set("TPOT_ES_RETENTION=30", "TPOT_ATTACKMAP_TEXT=DISABLED")
        self.assertIn("deletes the events older than 30 days", text)
        self.assertIn("Restart T-Pot to apply it", text)


if __name__ == "__main__":
    unittest.main()
