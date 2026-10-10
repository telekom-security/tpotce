"""The retention rule of the ILM policy tpot (tpotctl/elastic.py).

The cases in docker/tpotinit/tests/ilm_cases.yml are the ones the jq rule of tpotinit
(docker/tpotinit/dist/bin/es_retention.sh) passes as well: docker/_tests/run.sh tpotinit_ilm.
"""

import copy
import json
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
