"""Builder manifests can be rendered with explicit repositories and no env file."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required")
class BuilderPlatformDefaultsTest(unittest.TestCase):
    def render(self, **platforms):
        manifest = Path(__file__).resolve().parents[2] / "docker/_builder/docker-compose.yml"
        env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
               "TPOT_VERSION": "test", "TPOT_DOCKER_REPO": "test/hub",
               "TPOT_GHCR_REPO": "test/ghcr", **platforms}
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                ["docker", "compose", "--project-directory", folder, "--env-file", os.devnull,
                 "-f", str(manifest), "config", "--format", "json"],
                cwd=folder, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["services"]["cowrie"]["build"]["platforms"]

    def test_unset_platforms_build_both_supported_architectures(self):
        self.assertEqual(self.render(), ["linux/amd64", "linux/arm64"])

    def test_explicit_platform_values_are_preserved(self):
        self.assertEqual(self.render(TPOT_AMD64="linux/amd64", TPOT_ARM64="linux/arm64/v8"),
                         ["linux/amd64", "linux/arm64/v8"])
