"""Narrow regressions for retained legacy helpers, with every action isolated."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[2] / "docker/tpotinit/dist/bin/deprecated"


class LegacyHelperTest(unittest.TestCase):
    def stub(self, folder, name, body):
        path = folder / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def test_elastic_helpers_are_directly_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.stub(folder, "curl", 'printf "{}\\n"')
            self.stub(folder, "jq", "cat")
            env = dict(os.environ, PATH=f"{folder}:{os.environ['PATH']}")
            for name in ("dump_es.sh", "restore_es.sh", "unlock_es.sh"):
                with self.subTest(name=name):
                    try:
                        result = subprocess.run([str(BIN / name)], env=env, cwd=folder,
                                                capture_output=True, text=True, timeout=5)
                    except OSError as error:
                        self.fail(f"{name} cannot be executed: {error}")
                    self.assertIn("Elasticsearch is not available", result.stdout)

    def test_hpfeeds_config_path_preserves_spaces_and_equals(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            config = folder / "broker settings=local.cfg"
            marker = folder / "loaded"
            config.write_text(f"printf loaded > {shlex.quote(str(marker))}\n")
            self.stub(folder, "whoami", "echo root")
            self.stub(folder, "systemctl", "exit 0")
            self.stub(folder, "sed", "exit 0")
            script = folder / "hpfeeds.sh"
            script.write_text((BIN / "hpfeeds_optin.sh").read_text().replace(
                "/data/ews/conf/hpfeeds.cfg", str(folder / "saved.cfg")))
            result = subprocess.run(
                ["bash", str(script), f"--conf={config}"], input="q",
                env=dict(os.environ, PATH=f"{folder}:{os.environ['PATH']}"), cwd=folder,
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.exists(), result.stdout + result.stderr)

    def test_legacy_port_reader_uses_explicit_compose_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            compose = folder / "ports with spaces.yml"
            compose.write_text("test-ports\n")
            self.stub(folder, "yq",
                      'if grep -q test-ports; then printf \'"8022:22/tcp"\\n\'; fi')
            source = (BIN / "hptest.sh").read_text().split("# Main\n")[0]
            result = subprocess.run(["bash", "-c", source + '\nfuGETPORTS "$1"', "test",
                                     str(compose)], cwd=folder, text=True, capture_output=True,
                                    env=dict(os.environ, PATH=f"{folder}:{os.environ['PATH']}"),
                                    timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("8022", result.stdout)
