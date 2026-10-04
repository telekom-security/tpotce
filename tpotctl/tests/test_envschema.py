""".env schema, its Python rules and the .env writer.

The cases in docker/tpotinit/tests/env_cases.yml are the same ones the bash rules of
tpotinit have to pass (docker/_tests/tests/tpotinit_env.sh).
"""

import os
import re
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    import yaml
except ImportError:
    yaml = None

from tpotctl.bootstrap import REPO_DIR  # noqa: E402
from tpotctl.envfile import EnvError, EnvFile  # noqa: E402

CASES = os.path.join(REPO_DIR, "docker", "tpotinit", "tests", "env_cases.yml")
ENV_EXAMPLE = os.path.join(REPO_DIR, "env.example")


def example_values():
    return EnvFile(ENV_EXAMPLE).values()


@unittest.skipUnless(yaml, "PyYAML is not installed")
class SchemaTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from tpotctl import envschema
        cls.envschema = envschema
        cls.schema = envschema.load_schema()

    def test_every_env_example_key_is_in_the_schema(self):
        for key in example_values():
            self.assertIn(key, self.schema, f"{key} of env.example is missing in env.schema.yml")

    def test_schema_keys_are_documented_in_env_example(self):
        with open(ENV_EXAMPLE, encoding="utf-8") as handle:
            text = handle.read()
        for key in self.schema:
            self.assertIn(key, text, f"{key} of env.schema.yml is not in env.example")

    def test_schema_is_well_formed(self):
        services = set()
        for name in os.listdir(os.path.join(REPO_DIR, "compose")):
            if name.endswith(".yml"):
                services |= self.envschema.compose_services(os.path.join(REPO_DIR, "compose", name))
        sections = {s for s, _title in self.envschema.SECTIONS}
        for key, rule in self.schema.items():
            with self.subTest(key=key):
                self.assertIn(rule.type, self.envschema.TYPES)
                self.assertIn(rule.section, sections)
                self.assertTrue(rule.title)
                self.assertIn(rule.scope, ("", "HIVE", "SENSOR"))
                for service in rule.services:
                    self.assertIn(service, services, f"{key}: no compose file runs {service}")
                if rule.type == "enum":
                    self.assertTrue(rule.values)
                if rule.type in ("int", "number"):
                    self.assertIsNotNone(rule.min)
                    self.assertIsNotNone(rule.max)
                    self.assertTrue(rule.message)
                if rule.type == "regex":
                    re.compile(rule.pattern)
                    self.assertTrue(rule.message)
                    self.assertNotIn("\\", rule.pattern, "bash =~ patterns without backslashes")
                for name in ("required_when", "warn_when"):
                    condition = getattr(rule, name)
                    if condition:
                        self.assertIn(condition["key"], self.schema)
                if rule.required_when:
                    self.assertTrue(rule.required_message)
                if rule.on_invalid:
                    self.assertEqual(rule.on_invalid, "default")
                    self.assertTrue(rule.default)

    def test_order_follows_env_example(self):
        order = list(example_values())
        known = [k for k in self.schema if k in order]
        self.assertEqual(known, [k for k in order if k in self.schema])

    def test_shared_cases(self):
        with open(CASES, encoding="utf-8") as handle:
            spec = yaml.safe_load(handle)
        for case in spec["cases"]:
            values = example_values()
            values.update({k: str(v) for k, v in (spec.get("base") or {}).items()})
            values.update({k: str(v) for k, v in (case.get("set") or {}).items()})
            problems = self.envschema.validate(values, case.get("services", []), self.schema, check_host=False)
            with self.subTest(case=case["name"]):
                self.assertEqual(sorted({p.key for p in problems if p.level == "error"}),
                                 sorted(case.get("errors", [])), [p.text for p in problems])
                self.assertEqual(sorted({p.key for p in problems if p.level == "warning"}),
                                 sorted(case.get("warnings", [])), [p.text for p in problems])

    def test_messages_match_tpotinit(self):
        values = example_values()
        values.update({"TPOT_PERSISTENCE": "true", "TPOT_PULL_POLICY": "sometimes", "WEB_USER": ""})
        texts = {p.key: p.text for p in self.envschema.validate(values, [], self.schema)}
        self.assertEqual(texts["TPOT_PERSISTENCE"], 'invalid value "true", allowed: on off.')
        self.assertEqual(texts["TPOT_PULL_POLICY"], 'invalid value "sometimes", allowed: always missing never '
                                                    'build daily weekly every_<duration>.')
        self.assertEqual(texts["WEB_USER"], "is not set or empty.")

    def test_compose_services(self):
        services = self.envschema.compose_services(os.path.join(REPO_DIR, "compose", "standard.yml"))
        self.assertIn("suricata", services)
        self.assertNotIn("galah", services)

    def test_mask(self):
        self.assertEqual(self.envschema.mask(""), "")
        self.assertEqual(self.envschema.mask("abc"), "••••••")
        self.assertIn("2 entries", self.envschema.mask("a b"))


class EnvFileTest(unittest.TestCase):

    SAMPLE = ("# T-Pot config file. Do not remove.\n"
              "WEB_USER=\n"
              "TPOT_BLACKHOLE=DISABLED   # comment\n"
              "QUOTED='x y'\n"
              'GALAH_LLM_PROVIDER: "ollama"\n'
              "\n"
              "#####\n"
              "# NEVER MAKE CHANGES TO THIS SECTION UNLESS YOU REALLY KNOW WHAT YOU ARE DOING!!! #\n"
              "#####\n"
              "\n"
              "TPOT_VERSION=24.04.2\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, ".env")
        with open(self.path, "w") as handle:
            handle.write(self.SAMPLE)
        os.chmod(self.path, 0o640)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_both_forms(self):
        values = EnvFile(self.path).values()
        self.assertEqual(values["TPOT_BLACKHOLE"], "DISABLED")
        self.assertEqual(values["QUOTED"], "x y")
        self.assertEqual(values["GALAH_LLM_PROVIDER"], "ollama")
        self.assertEqual(values["WEB_USER"], "")

    def test_set_keeps_form_and_everything_else(self):
        env = EnvFile(self.path)
        env.set("GALAH_LLM_PROVIDER", 'open"ai')
        env.set("QUOTED", "a b")
        env.set("WEB_USER", "a== b==")
        env.save()
        with open(self.path) as handle:
            text = handle.read()
        self.assertIn('GALAH_LLM_PROVIDER: "open\\"ai"\n', text)
        self.assertIn("QUOTED='a b'\n", text)
        self.assertIn("WEB_USER=a== b==\n", text)
        self.assertIn("# T-Pot config file. Do not remove.\n", text)
        self.assertEqual(EnvFile(self.path).values()["GALAH_LLM_PROVIDER"], 'open"ai')
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        self.assertEqual(len(text.splitlines()), len(self.SAMPLE.splitlines()))

    def test_new_keys_go_above_the_ruler_or_to_the_end(self):
        env = EnvFile(self.path)
        env.set("NEW_USER_KEY", "1")
        env.set("NEW_SYSTEM_KEY", "2", system=True)
        env.save()
        with open(self.path) as handle:
            lines = handle.read().splitlines()
        self.assertLess(lines.index("NEW_USER_KEY=1"), lines.index("#####"))
        self.assertEqual(lines[-1], "NEW_SYSTEM_KEY=2")

    def test_rejects_line_breaks(self):
        with self.assertRaises(EnvError):
            EnvFile(self.path).set("TPOT_BLACKHOLE", "ENABLED\nX=1")

    def test_comment_like_values_are_quoted(self):
        env = EnvFile(self.path)
        env.set("TPOT_BLACKHOLE", "a #b")
        self.assertEqual(env.values()["TPOT_BLACKHOLE"], "a #b")

    def test_env_example_round_trip(self):
        env = EnvFile(ENV_EXAMPLE)
        with open(ENV_EXAMPLE, encoding="utf-8") as handle:
            self.assertEqual(env.text(), handle.read())
        for key, value in env.values().items():
            env.set(key, value)
        with open(ENV_EXAMPLE, encoding="utf-8") as handle:
            self.assertEqual(env.text(), handle.read())


if __name__ == "__main__":
    unittest.main()
