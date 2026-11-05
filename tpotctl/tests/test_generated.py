"""Source fingerprints on the generated runtime configuration."""

import hashlib
import io
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

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

    def test_stamp_stays_outside_the_customizer_body(self):
        body = "# Built by the customizer\nservices: {}\n"
        checksum = hashlib.sha256(body.encode()).hexdigest()
        text = f"# T-Pot: CUSTOM\n# customizer: sha256={checksum}\n{body}"
        marked = generated.stamp_text(text, "a" * 64)
        self.assertTrue(marked.endswith(f"# customizer: sha256={checksum}\n{body}"))

    def test_restamping_does_not_hide_a_hand_edited_customizer_body(self):
        body = "services: {}\n"
        checksum = hashlib.sha256(body.encode()).hexdigest()
        text = f"# T-Pot: CUSTOM\n# customizer: sha256={checksum}\nservices: {{mine: {{}}}}\n"
        marked = generated.stamp_text(text, "a" * 64)
        self.assertIn(f"# customizer: sha256={checksum}\n", marked)
        self.assertIn("services: {mine: {}}\n", marked)

    def test_old_valid_customizer_stamp_can_move_without_invalidating_its_checksum(self):
        body = f"{generated.STAMP_PREFIX}{'a' * 64}\nservices: {{}}\n"
        checksum = hashlib.sha256(body.encode()).hexdigest()
        text = f"# T-Pot: CUSTOM\n# customizer: sha256={checksum}\n{body}"
        marked = generated.stamp_text(text, "b" * 64)
        expected = hashlib.sha256(b"services: {}\n").hexdigest()
        self.assertTrue(marked.endswith(f"# customizer: sha256={expected}\nservices: {{}}\n"))

    def test_env_stamp_uses_env_example_and_sets_private_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "env.example"), "wb") as out:
                out.write(b"TPOT_VERSION=1\n")
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as out:
                out.write("# Local configuration\nTPOT_VERSION=local\n")

            changed = generated.stamp_file(path, tmp, kind="env")

            self.assertTrue(changed)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with open(path, encoding="utf-8") as source:
                text = source.read()
            expected = generated.source_fingerprint(tmp, ["env.example"])
            self.assertTrue(generated.has_fingerprint(text, expected))
            self.assertIn("TPOT_VERSION=local\n", text)

    def test_custom_compose_stamp_tracks_selection_and_sources_but_not_manual_body_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_dir = os.path.join(tmp, "compose")
            os.makedirs(compose_dir)
            for name, value in (("standard.yml", "standard"), ("tpot_services.yml", "catalog")):
                with open(os.path.join(compose_dir, name), "w", encoding="utf-8") as out:
                    out.write(value)
            path = os.path.join(tmp, "docker-compose.yml")
            with open(path, "w", encoding="utf-8") as out:
                out.write(
                    "# T-Pot: CUSTOM\n"
                    "# customizer: version=2 base=STANDARD\n"
                    "# customizer: add=glutton\n"
                    "# customizer: remove=cowrie\n"
                    "# customizer: port=nginx:64297=64298\n"
                    "# customizer: sha256=body-only-checksum\n"
                    "services:\n  glutton: {}\n"
                )

            generated.stamp_file(path, tmp, kind="compose")
            with open(path, encoding="utf-8") as source:
                marked = source.read()
            expected = generated.source_fingerprint(
                tmp, ["compose/standard.yml", "compose/tpot_services.yml"],
                selection="version=2 base=STANDARD\nadd=glutton\nremove=cowrie\nport=nginx:64297=64298")
            self.assertTrue(generated.has_fingerprint(marked, expected))
            edited = marked.replace("glutton: {}", "glutton: {command: changed}")
            self.assertTrue(generated.configuration_is_current(edited, tmp, kind="compose"))

            with open(os.path.join(compose_dir, "standard.yml"), "a", encoding="utf-8") as out:
                out.write("-updated")
            self.assertFalse(generated.configuration_is_current(edited, tmp, kind="compose"))

    def test_edition_compose_fingerprint_uses_only_its_leading_edition_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_dir = os.path.join(tmp, "compose")
            os.makedirs(compose_dir)
            path = os.path.join(compose_dir, "sensor.yml")
            with open(path, "w", encoding="utf-8") as out:
                out.write("# T-Pot: SENSOR\nservices: {}\n")
            text = "# T-Pot: SENSOR\nservices: {}\n# T-Pot: STANDARD\n"
            expected = generated.source_fingerprint(tmp, ["compose/sensor.yml"], selection="SENSOR")
            self.assertEqual(generated.configuration_fingerprint(text, tmp, "compose"), expected)

    def test_runtime_config_check_reports_only_the_file_behind_its_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "compose"))
            with open(os.path.join(tmp, "env.example"), "w", encoding="utf-8") as out:
                out.write("TPOT_VERSION=1\n")
            with open(os.path.join(tmp, "compose", "standard.yml"), "w", encoding="utf-8") as out:
                out.write("# T-Pot: STANDARD\nservices: {}\n")
            with open(os.path.join(tmp, ".env"), "w", encoding="utf-8") as out:
                out.write("TPOT_VERSION=local\n")
            with open(os.path.join(tmp, "docker-compose.yml"), "w", encoding="utf-8") as out:
                out.write("# T-Pot: STANDARD\nservices: {}\n")
            generated.stamp_file(os.path.join(tmp, ".env"), tmp, kind="env")
            generated.stamp_file(os.path.join(tmp, "docker-compose.yml"), tmp, kind="compose")

            self.assertEqual(generated.configuration_drift(tmp), [])
            with open(os.path.join(tmp, "env.example"), "a", encoding="utf-8") as out:
                out.write("TPOT_REPO=example\n")
            self.assertEqual(generated.configuration_drift(tmp), [".env"])

    def test_unmanaged_compose_warns_without_bypassing_env_drift(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ("env.example", ".env"):
                with open(os.path.join(root, name), "w", encoding="utf-8") as out:
                    out.write("TPOT_DOCKER_COMPOSE=./own.yml\n")
            generated.stamp_file(os.path.join(root, ".env"), root, "env")
            compose = os.path.join(root, "own.yml")
            with open(compose, "w", encoding="utf-8") as out:
                out.write("services: {mine: {image: example/test:1}}\n")
            self.assertEqual(generated.configuration_drift(root), [])
            stream = io.StringIO()
            with redirect_stderr(stream):
                self.assertEqual(generated.main(["check", "--root", root]), 0)
            self.assertIn("own.yml is self-managed", stream.getvalue())
            self.assertIn("release compatibility is not checked", stream.getvalue())
            stream = io.StringIO()
            with redirect_stderr(stream):
                self.assertEqual(generated.main(["stamp-compose", "--file", compose, "--root", root]), 0)
            self.assertIn("self-managed", stream.getvalue())
            with open(compose, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "services: {mine: {image: example/test:1}}\n")
            with open(os.path.join(root, "env.example"), "a", encoding="utf-8") as out:
                out.write("# changed release\n")
            self.assertEqual(generated.configuration_drift(root), [".env"])

    def test_unmanaged_exemption_does_not_hide_missing_files_or_broken_generated_headers(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ("env.example", ".env"):
                with open(os.path.join(root, name), "w", encoding="utf-8") as out:
                    out.write("TPOT_VERSION=1\n")
            generated.stamp_file(os.path.join(root, ".env"), root, "env")
            self.assertEqual(generated.configuration_drift(root), ["docker-compose.yml"])
            for text in ("# T-Pot: CUSTOM\nservices: {}\n", "# T-Pot: STANDARD\nservices: {}\n",
                         "# T-Pot sources sha256: broken\nservices: {}\n"):
                with self.subTest(text=text):
                    with open(os.path.join(root, "docker-compose.yml"), "w", encoding="utf-8") as out:
                        out.write(text)
                    self.assertEqual(generated.configuration_drift(root), ["docker-compose.yml"])

    def test_stamp_command_leaves_self_managed_content_unchanged_and_warns(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "own.yml")
            with open(path, "w", encoding="utf-8") as out:
                out.write("services: {}\n")
            stream = io.StringIO()
            with redirect_stderr(stream):
                code = generated.main(["stamp-compose", "--file", path, "--root", root])
            self.assertEqual(code, 0, stream.getvalue())
            self.assertIn("self-managed", stream.getvalue())
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "services: {}\n")


if __name__ == "__main__":
    unittest.main()
