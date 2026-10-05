"""tpot check and tpot attackers: the host helpers of T-Pot in the look of tpot."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tpotctl.tests import isolate

isolate()

from tpotctl import events  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HPTEST = os.path.join(REPO, "docker", "tpotinit", "dist", "bin", "hptest.sh")
PIPELINE = os.path.join(REPO, "docker", "tpotinit", "dist", "bin", "attackmap_pipeline_test.sh")

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
try:
    import rich  # noqa: F401
except ImportError:
    rich = None


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TopSourcesTest(unittest.TestCase):

    ANSWER = {"aggregations": {"sources": {"buckets": [
        {"key": "203.0.113.7", "doc_count": 912,
         "country": {"buckets": [{"key": "Netherlands", "doc_count": 912}]},
         "rep": {"buckets": [{"key": "mass scanner", "doc_count": 900}]}},
        {"key": "198.51.100.2", "doc_count": 40, "country": {"buckets": []}, "rep": {"buckets": []}}]}}}

    def test_parse(self):
        found = events.top_sources(hours=24, size=5, url="http://es:9200",
                                   opener=lambda request, timeout: FakeResponse(json.dumps(self.ANSWER).encode()))
        self.assertEqual(found.problem, "")
        self.assertEqual([(s.ip, s.count, s.country, s.reputation) for s in found.sources],
                         [("203.0.113.7", 912, "Netherlands", "mass scanner"), ("198.51.100.2", 40, "", "")])

    def test_query_leaves_out_the_nsm_tools(self):
        seen = []

        def opener(request, timeout):
            seen.append(json.loads(request.data))
            return FakeResponse(json.dumps(self.ANSWER).encode())

        events.top_sources(hours=6, size=3, opener=opener)
        query = json.dumps(seen[0])
        self.assertIn("now-6h", query)
        self.assertIn("src_ip.keyword", query)
        self.assertIn("Suricata", query)
        self.assertEqual(seen[0]["aggs"]["sources"]["terms"]["size"], 3)

    def test_no_elasticsearch(self):
        def opener(request, timeout):
            raise OSError("refused")
        self.assertIn("not reachable", events.top_sources(opener=opener).problem)


@unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), "no bash")
class HptestTest(unittest.TestCase):

    def call(self, function, stdin="", *args):
        return subprocess.run(["bash", "-c", f'source "{HPTEST}"; {function} "$@"', "hptest"] + list(args),
                              input=stdin, capture_output=True, universal_newlines=True,
                              env=dict(os.environ, TPOT_GUM="off")).stdout.strip()

    def test_ports_from_docker_compose_config(self):
        config = {"services": {
            "cowrie": {"ports": [{"target": 22, "published": "22", "protocol": "tcp"},
                                 {"target": 23, "published": "23", "protocol": "tcp"}]},
            "ciscoasa": {"ports": [{"target": 5000, "published": "5000", "protocol": "udp"},
                                   {"target": 8443, "published": "8443", "protocol": "tcp"}]},
            "nginx": {"ports": [{"target": 64297, "published": "64297", "protocol": "tcp"}]},
            "elasticsearch": {"ports": [{"host_ip": "127.0.0.1", "target": 9200, "published": "64298",
                                         "protocol": "tcp"}]},
            "tpotinit": {}}}
        self.assertEqual(self.call("fuPORTS", json.dumps(config)).split("\n"), ["T:22,T:23,T:8443", "U:5000"])

    def test_tools_per_distribution(self):
        folder = tempfile.mkdtemp(prefix="tpot-os-release-")
        self.addCleanup(shutil.rmtree, folder)
        cases = {
            'ID=debian\nVERSION_ID="13"': "apt nmap ncat dcmtk",
            'ID=ubuntu\nID_LIKE=debian': "apt nmap ncat dcmtk",
            'ID=raspbian\nID_LIKE=debian': "apt nmap ncat dcmtk",
            'ID=fedora': "dnf nmap nmap-ncat dcmtk",
            'ID="almalinux"\nID_LIKE="rhel centos fedora"': "dnf nmap nmap-ncat",
            'ID="rocky"\nID_LIKE="rhel centos fedora"': "dnf nmap nmap-ncat",
            'ID="rhel"\nID_LIKE="fedora"': "dnf nmap nmap-ncat",
            'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"': "zypper netcat-openbsd dcmtk",
        }
        for text, expected in cases.items():
            with self.subTest(os_release=text.split()[0]):
                path = os.path.join(folder, "os-release")
                with open(path, "w", encoding="utf-8") as out:
                    out.write(text + "\n")
                self.assertEqual(self.call("fuTOOLS_FOR", "", path), expected)

    def test_look_and_options(self):
        with open(HPTEST, encoding="utf-8") as handle:
            text = handle.read()
        for part in ("fuUI_", "fuMARK phase", "-B", "--tools-only", "installer/lib/ui.sh"):
            self.assertTrue(part in text, part)
        for gone in ("dpkg -s", "yq ", "telnet "):
            self.assertFalse(gone in text, gone)

    def test_pipeline_test_look_and_options(self):
        with open(PIPELINE, encoding="utf-8") as handle:
            text = handle.read()
        for part in ("fuUI_", "fuMARK phase", "--become-file", "installer/lib/ui.sh"):
            self.assertTrue(part in text, part)


class ChecksCliTest(unittest.TestCase):

    def run_cli(self, *argv):
        from tpotctl import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("tpotctl.ops.linux_host", return_value=True):
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_honeypots_runs_hptest(self):
        with mock.patch("os.execv") as execv, mock.patch("os.chdir"):
            self.run_cli("check", "honeypots", "--host", "192.0.2.5")
        command = execv.call_args[0][1]
        self.assertEqual(command[0], HPTEST)
        self.assertEqual(command[-1], "192.0.2.5")

    def test_pipeline_needs_a_yes_without_a_terminal(self):
        with mock.patch("sys.stdin", io.StringIO("")), mock.patch("os.execv") as execv:
            code, _text = self.run_cli("check", "pipeline")
        self.assertEqual(code, 2)
        execv.assert_not_called()

    def test_pipeline_dry_run_needs_no_yes(self):
        with mock.patch("sys.stdin", io.StringIO("")), mock.patch("os.execv") as execv, mock.patch("os.chdir"):
            self.run_cli("check", "pipeline", "--dry-run")
        self.assertIn("--dry-run", execv.call_args[0][1])

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_attackers(self):
        found = events.Sources([events.Source("203.0.113.7", 912, "Netherlands", "mass scanner")])
        with mock.patch.object(events, "top_sources", return_value=found):
            code, text = self.run_cli("attackers", "--count", "5")
        self.assertEqual(code, 0)
        self.assertIn("203.0.113.7", text)
        self.assertIn("Netherlands", text)


if __name__ == "__main__":
    unittest.main()
