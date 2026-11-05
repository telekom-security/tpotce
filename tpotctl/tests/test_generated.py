"""Source fingerprints on the generated runtime configuration."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tpotctl import generated  # noqa: E402


class GeneratedTest(unittest.TestCase):

    def test_source_fingerprint_tracks_sources_and_selection_not_generated_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "compose"))
            with open(os.path.join(tmp, "env.example"), "wb") as out:
                out.write(b"TPOT_VERSION=1\n")
            with open(os.path.join(tmp, "compose", "standard.yml"), "wb") as out:
                out.write(b"# T-Pot: STANDARD\nservices: {}\n")

            first = generated.source_fingerprint(
                tmp, ["env.example", "compose/standard.yml"], selection="STANDARD")
            same_sources = generated.source_fingerprint(
                tmp, ["compose/standard.yml", "env.example"], selection="STANDARD")
            other_selection = generated.source_fingerprint(
                tmp, ["env.example", "compose/standard.yml"], selection="SENSOR")
            self.assertEqual(first, same_sources)
            self.assertNotEqual(first, other_selection)

            marked = generated.stamp_text("WEB_USER=private-value\n", first)
            self.assertTrue(generated.has_fingerprint(marked, first))
            edited = marked.replace("private-value", "another-private-value")
            self.assertTrue(generated.has_fingerprint(edited, first))

            with open(os.path.join(tmp, "env.example"), "ab") as out:
                out.write(b"TPOT_REPO=example\n")
            changed_sources = generated.source_fingerprint(
                tmp, ["env.example", "compose/standard.yml"], selection="STANDARD")
            self.assertNotEqual(first, changed_sources)
            self.assertFalse(generated.has_fingerprint(edited, changed_sources))

    def test_stamp_replaces_an_old_stamp_without_changing_configuration(self):
        original = "# T-Pot: STANDARD\n# comment\nservices: {}\n"
        old = generated.stamp_text(original, "a" * 64)
        current = generated.stamp_text(old, "b" * 64)

        self.assertEqual(current.count(generated.STAMP_PREFIX), 1)
        self.assertIn("# T-Pot: STANDARD\n# comment\n", current)
        self.assertIn("services: {}\n", current)
        self.assertTrue(generated.has_fingerprint(current, "b" * 64))
        self.assertFalse(generated.has_fingerprint(current, "a" * 64))


if __name__ == "__main__":
    unittest.main()
