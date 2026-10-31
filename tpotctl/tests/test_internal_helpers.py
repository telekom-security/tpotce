"""Internal helper usage must not reach privileged or mutating operations."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "docker/tpotinit/dist/bin"


class HelperUsageTest(unittest.TestCase):
    def invoke(self, script, args):
        with tempfile.TemporaryDirectory() as folder:
            stub = Path(folder) / "whoami"
            marker = Path(folder) / "called"
            stub.write_text('#!/bin/sh\nprintf called > "$HELPER_MARKER"\necho nobody\n')
            stub.chmod(0o755)
            yq = Path(folder) / "yq"
            yq.write_text('#!/bin/sh\nprintf called > "$HELPER_MARKER"\nexit 1\n')
            yq.chmod(0o755)
            env = dict(os.environ, PATH=f"{folder}:{os.environ['PATH']}",
                       HELPER_MARKER=str(marker))
            result = subprocess.run(["bash", str(BIN / script), *args], env=env,
                                    capture_output=True, text=True, timeout=5)
            return result, marker.exists()

    def test_help_before_privilege_checks(self):
        for script in ("rules.sh", "blackhole.sh"):
            for option in ("-h", "--help"):
                with self.subTest(script=script, option=option):
                    result, privileged = self.invoke(script, [option])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Usage:", result.stdout)
                    self.assertFalse(privileged)

    def test_invalid_usage_has_failure_exit(self):
        for script, args in (("rules.sh", []), ("rules.sh", ["compose.yml", "bad"]),
                             ("rules.sh", ["compose.yml", "set", "extra"]),
                             ("blackhole.sh", []), ("blackhole.sh", ["bad"]),
                             ("blackhole.sh", ["add", "extra"])):
            with self.subTest(script=script, args=args):
                result, privileged = self.invoke(script, args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Usage:", result.stdout + result.stderr)
                self.assertFalse(privileged)

    def test_blackhole_action_requires_root(self):
        result, privileged = self.invoke("blackhole.sh", ["add"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("root", result.stdout + result.stderr)
        self.assertTrue(privileged)

    def blackhole(self, action, ip_body):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        data = root / "data"
        data.mkdir()
        (data / "mass_scanner.txt").write_text(
            "".join(f"192.0.2.{number}\n" for number in range(1, 4)))
        script = root / "blackhole.sh"
        script.write_text((BIN / "blackhole.sh").read_text().replace(
            "/etc/blackhole", str(data)).replace('myBASELINE="500"', 'myBASELINE="2"'))
        for name, body in {"whoami": "echo root", "ip": ip_body,
                           "grep": 'if [ "$1" = -o ]; then printf "192.0.2.1\\n192.0.2.2\\n192.0.2.3\\n"; else /usr/bin/grep "$@"; fi',
                           "date": "echo 1000",
                           "aria2c": "echo unexpected-download >&2; exit 99"}.items():
            stub = root / name
            stub.write_text("#!/bin/sh\n" + body + "\n")
            stub.chmod(0o755)
        return subprocess.run(["bash", str(script), action], text=True, capture_output=True,
                              env=dict(os.environ, PATH=f"{root}:{os.environ['PATH']}"),
                              timeout=5), data

    def test_route_failure_is_reported(self):
        result, _ = self.blackhole("add", 'if [ "$1" = route ]; then echo denied >&2; exit 1; fi')
        self.assertEqual(result.returncode, 1)
        self.assertIn("denied", result.stdout + result.stderr)

    def test_failed_deletion_keeps_the_route_list(self):
        result, data = self.blackhole(
            "del", 'if [ "$1" = route ]; then echo denied >&2; exit 1; fi\n'
            'if [ "$1" = -4 ]; then echo "blackhole 192.0.2.1"; fi')
        self.assertEqual(result.returncode, 1)
        self.assertTrue((data / "mass_scanner.txt").is_file())

    def test_deletion_retry_succeeds_when_routes_are_already_absent(self):
        result, data = self.blackhole(
            "del", 'if [ "$1" = route ]; then echo "No such process" >&2; exit 1; fi')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((data / "mass_scanner.txt").exists())
