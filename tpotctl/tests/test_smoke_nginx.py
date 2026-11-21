import os
import re
import signal
import subprocess
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
NGINX_SMOKE = REPO / "docker" / "_tests" / "tests" / "nginx.sh"


class NginxSmokeTest(unittest.TestCase):
    def test_tls_handshake_timeout_fails_with_diagnostics(self):
        source = NGINX_SMOKE.read_text(encoding="utf-8")
        match = re.search(r"(?ms)^run_tls_checks\(\) \{\n.*?^\}", source)
        self.assertIsNotNone(match, "run_tls_checks must remain directly testable")
        script = f"""
TEST_TIMEOUT=1
INIT_IMAGE=init-image
TEST_CONTAINER_NAME=nginx
docker() {{
  if [[ " $* " == *" --entrypoint timeout ${{INIT_IMAGE}} "* \
        && " $* " == *" ${{TEST_TIMEOUT}} openssl s_client "* ]]; then
    printf '%s\\n' 's_client: error: simulated handshake timeout' >&2
    return 124
  fi
  sleep 30
}}
{match.group(0)}
run_tls_checks
"""
        process = subprocess.Popen(
            ["bash", "-c", script],
            cwd=REPO,
            env=dict(os.environ),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            self.fail("run_tls_checks did not bound the stalled s_client command")

        self.assertNotEqual(process.returncode, 0, output)
        self.assertIn("s_client: error: simulated handshake timeout", output)
        self.assertIn("FAIL TLS 64297: s_client exited with status 124", output)


if __name__ == "__main__":
    unittest.main()
