"""tpot check and tpot attackers: the host helpers of T-Pot in the look of tpot."""

import base64
import contextlib
import csv
import datetime
import fnmatch
import io
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    import fcntl
except ImportError:
    fcntl = None

from tpotctl.tests import isolate

isolate()

from tpotctl import events, ops  # noqa: E402
from tpotctl.checks import CheckError, HoldError  # noqa: E402
from tpotctl.checks import ewshold, fixtures, inventory, probes  # noqa: E402
from tpotctl.checks.inventory import Port  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HPTEST = os.path.join(REPO, "docker", "tpotinit", "dist", "bin", "hptest.sh")
PIPELINE = os.path.join(REPO, "docker", "tpotinit", "dist", "bin", "attackmap_pipeline_test.sh")
EWS_HOLD = os.path.join(REPO, "docker", "tpotinit", "dist", "bin", "ews_hold.sh")

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
try:
    import rich  # noqa: F401
except ImportError:
    rich = None
try:
    import yaml
except ImportError:
    yaml = None


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
            "ciscoasa": {"ports": [{"target": 500, "published": "500", "protocol": "udp"},
                                   {"target": 4500, "published": "4500", "protocol": "udp"},
                                   {"target": 8443, "published": "8443", "protocol": "tcp"}]},
            "nginx": {"ports": [{"target": 64297, "published": "64297", "protocol": "tcp"}]},
            "elasticsearch": {"ports": [{"host_ip": "127.0.0.1", "target": 9200, "published": "64298",
                                         "protocol": "tcp"}]},
            "tpotinit": {}}}
        self.assertEqual(self.call("fuPORTS", json.dumps(config)).split("\n"), ["T:22,T:23,T:8443", "U:500,U:4500"])

    def test_ike_ports_follow_published_overrides(self):
        config = {"services": {"ciscoasa": {"ports": [
            {"target": 500, "published": "15500", "protocol": "udp"},
            {"target": 4500, "published": "14500", "protocol": "udp"},
            {"target": 8443, "published": "18443", "protocol": "tcp"}]}}}
        self.assertEqual(self.call("fuIKEPORTS", json.dumps(config)).splitlines(), ["15500 0", "14500 1"])

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

    def fake_check(self, report=None, sensor=False):
        from tpotctl.checks import run as trun
        check = mock.Mock()
        check.sensor = sensor
        check.plan.return_value = ["Run id abc123, token tpot-check-abc123", "cowrie: probe 22/tcp"]
        check.run.return_value = report or trun.Report(run_id="abc123", rows=[])
        return check

    def run_check(self, *argv, check=None, tty=False, answer="n"):
        from tpotctl.checks import run as trun
        check = check or self.fake_check()
        stdin = mock.Mock()
        stdin.isatty.return_value = tty
        with mock.patch.object(trun, "Check", return_value=check) as made, mock.patch("sys.stdin", stdin), \
                mock.patch("tpotctl.cli.ask", return_value=answer) as asked:
            code, text = self.run_cli("check", *argv)
        return code, text, made, check, asked

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_the_honeypots_check_runs_the_check_of_tpotctl(self):
        from tpotctl.checks import run as trun
        code, _text, made, check, _asked = self.run_check("honeypots", "-y", "--only", "cowrie,dionaea", "--keep")
        self.assertEqual(code, 0)
        options = made.call_args[0][0]
        self.assertEqual((options.only, options.geo, options.geo_only, options.keep), (["cowrie", "dionaea"], True,
                                                                                         False, True))
        self.assertIsInstance(options, trun.Options)
        check.run.assert_called_once_with()

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_the_pipeline_is_the_check_without_probes(self):
        _code, _text, made, _check, _asked = self.run_check("pipeline", "-y", "--types", "cowrie")
        options = made.call_args[0][0]
        self.assertEqual((options.only, options.geo_only, options.geo), (["cowrie"], True, True))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            from tpotctl import cli
            cli.build_parser().parse_args(["check", "pipeline", "--no-geo"])

    def test_without_a_terminal_and_without_yes_it_cannot_ask(self):
        from tpotctl.checks import run as trun
        code, text, _made, check, asked = self.run_check("honeypots")
        self.assertEqual(code, 2)
        check.run.assert_not_called()
        asked.assert_not_called()
        self.assertIn(trun.QUESTION, " ".join(text.split()))
        self.assertIn("--yes", text)

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_the_question_names_ewsposter_the_test_events_and_the_cleanup(self):
        from tpotctl.checks import run as trun
        self.assertEqual(trun.QUESTION,
                         "During the check (a few minutes) ewsposter sends nothing; real attacks from this window "
                         "are not reported either. Test events are written to the honeypot logs and removed from "
                         "Elasticsearch afterwards (--keep keeps them).")
        code, text, _made, check, asked = self.run_check("honeypots", tty=True, answer="n")
        self.assertEqual(code, 1)
        asked.assert_called_once()
        check.run.assert_not_called()
        self.assertIn(trun.QUESTION, " ".join(text.split()))
        code, _text, _made, check, _asked = self.run_check("honeypots", tty=True, answer="y")
        self.assertEqual(code, 0)
        check.run.assert_called_once_with()
        self.assertIn("stay in Elasticsearch", trun.question(trun.Options(keep=True)))
        self.assertIn("HIVE", trun.question(trun.Options(), sensor=True))

    def test_the_dry_run_asks_nothing_and_touches_nothing(self):
        code, text, _made, check, asked = self.run_check("pipeline", "--dry-run")
        self.assertEqual(code, 0)
        check.run.assert_not_called()
        asked.assert_not_called()
        self.assertIn("cowrie: probe 22/tcp", text)

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_the_exit_code_is_the_one_of_the_run(self):
        from tpotctl.checks import run as trun
        for exit_code, result in ((1, "failed"), (3, "hold failed"), (130, "cancelled")):
            with self.subTest(exit_code=exit_code):
                report = trun.Report(run_id="abc123", exit_code=exit_code, result=result, error="no ack")
                code, _text, _made, _check, _asked = self.run_check("honeypots", "-y",
                                                                    check=self.fake_check(report))
                self.assertEqual(code, exit_code)

    def test_an_error_before_the_hold_is_said_without_a_table(self):
        from tpotctl.checks import run as trun
        report = trun.Report(run_id="abc123", exit_code=1, result="error", error="no address of this host found")
        code, text, _made, _check, _asked = self.run_check("honeypots", "-y", check=self.fake_check(report))
        self.assertEqual(code, 1)
        self.assertIn("no address of this host found", text)
        self.assertNotIn("Honeypot", text)

    def test_a_closed_stdout_after_the_run_keeps_its_exit_code(self):
        from tpotctl.checks import run as trun
        report = trun.Report(run_id="abc123", exit_code=1, result="failed")
        with mock.patch("tpotctl.cli.check_summary", side_effect=BrokenPipeError(32, "Broken pipe")), \
                mock.patch("tpotctl.cli.quiet_stdout") as quiet:
            code, _text, _made, _check, _asked = self.run_check("honeypots", "-y", check=self.fake_check(report))
        self.assertEqual(code, 1)
        quiet.assert_called_once_with()

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_a_stdout_closed_early_keeps_the_exit_code_of_the_run(self):
        folder = tempfile.mkdtemp(prefix="tpot-closed-")
        self.addCleanup(shutil.rmtree, folder)
        child = os.path.join(folder, "child.py")
        with open(child, "w", encoding="utf-8") as out:
            out.write(
                "import sys, time\n"
                f"sys.path.insert(0, {REPO!r})\n"
                "from unittest import mock\n"
                "from tpotctl.tests import isolate\n"
                "isolate()\n"
                "from tpotctl import cli\n"
                "from tpotctl.checks import run as trun, stages\n"
                "time.sleep(0.5)\n"           # the parent closes the pipe meanwhile
                "row = stages.Row('cowrie')\n"
                "row.set('port', stages.FAILED, 'refused')\n"
                "report = trun.Report(run_id='abc123', rows=[row], exit_code=3, result='hold failed', "
                "error='no ack')\n"
                "check = mock.Mock(sensor=False)\n"
                "check.run.return_value = report\n"
                "class Plain:\n"
                "    def print(self, what):\n"
                "        print('table', flush=True)\n"
                "patches = [mock.patch.object(trun, 'Check', return_value=check), "
                "mock.patch('tpotctl.ops.linux_host', return_value=True)]\n"
                "if sys.argv[1] == 'plain':\n"
                "    patches.append(mock.patch('tpotctl.cli._console', return_value=Plain()))\n"
                "for patch in patches:\n"
                "    patch.start()\n"
                "code = cli.main(['check', 'honeypots', '-y'])\n"
                "sys.exit(code)\n")
        # neither 120 (the last flush of CPython) nor Rich's own exit 1 on a broken pipe
        for console in ("rich", "plain"):
            with self.subTest(console=console):
                process = subprocess.Popen([sys.executable, child, console], stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
                process.stdout.close()
                self.assertEqual(process.wait(timeout=60), 3)

    def test_a_honeypot_this_t_pot_does_not_run_is_a_usage_error(self):
        from tpotctl.checks import run as trun
        check = self.fake_check()
        check.run.side_effect = trun.UsageError("not a honeypot of this T-Pot: nopot")
        code, text, _made, _check, _asked = self.run_check("honeypots", "-y", "--only", "nopot", check=check)
        self.assertEqual(code, 2)
        self.assertIn("nopot", text)

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_the_table_has_a_column_per_stage(self):
        from tpotctl.checks import run as trun, stages as tstages
        row = tstages.Row("cowrie")
        for name in tstages.STAGES:
            row.set(name, tstages.OK)
        row.set("attack_map", tstages.FAILED, "not on Redis")
        report = trun.Report(run_id="abc123", rows=[row], exit_code=1, result="failed",
                             cleanup={"state": "done", "deleted": 7, "detail": ""})
        code, text, _made, _check, _asked = self.run_check("honeypots", "-y", check=self.fake_check(report))
        self.assertEqual(code, 1)
        for title in ("Honeypot", "Port", "Answer", "Log", "ES", "Geo", "Map", "Held", "Note"):
            self.assertIn(title, text)
        self.assertIn("ES: Elasticsearch.", " ".join(text.split()))
        self.assertIn("✗", text)
        self.assertIn("Removed 7 test events", text)
        self.assertIn("Attack Map: not on Redis", " ".join(text.split()))

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_attackers(self):
        found = events.Sources([events.Source("203.0.113.7", 912, "Netherlands", "mass scanner")])
        with mock.patch.object(events, "top_sources", return_value=found):
            code, text = self.run_cli("attackers", "--count", "5")
        self.assertEqual(code, 0)
        self.assertIn("203.0.113.7", text)
        self.assertIn("Netherlands", text)


def gone(pid: int) -> bool:
    """The process has ended: no such process, or a zombie nobody reaps (a container without an init)."""
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            line = handle.read()
    except OSError:
        return False
    return line[line.rfind(")") + 2:].split()[0] in ("Z", "X", "x")


class FakeEwsposter:
    """ewsposter as far as the hold goes: once the marker is there, hold.ack is touched (after it)."""

    def __init__(self, ews_dir: str, delay: float = 0.05):
        self.ews_dir, self.delay = ews_dir, delay
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        marker, ack = os.path.join(self.ews_dir, "hold"), os.path.join(self.ews_dir, "hold.ack")
        while not self.stop.wait(self.delay):
            if os.path.isfile(marker):
                with open(ack, "a", encoding="utf-8"):
                    pass
                os.utime(ack, None)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()


class HoldFolder(unittest.TestCase):
    """A data folder with <data>/ews and a compose file that runs ewsposter."""

    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="tpot-hold-")
        self.addCleanup(shutil.rmtree, self.data)
        self.ews = os.path.join(self.data, "ews")
        os.mkdir(self.ews, 0o770)
        self.marker = os.path.join(self.ews, "hold")
        self.ack = os.path.join(self.ews, "hold.ack")
        self.lock = os.path.join(self.ews, "check.lock")

    def lock_elsewhere(self):
        """check.lock taken as a second check would take it."""
        handle = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o660)
        self.addCleanup(os.close, handle)
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def lock_is_free(self) -> bool:
        if not os.path.exists(self.lock):
            return True
        handle = os.open(self.lock, os.O_RDWR)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(handle)

    def write_state(self, state: str, age: float = 0):
        with open(os.path.join(self.ews, "hold.state"), "w", encoding="utf-8") as out:
            out.write(f"{state} {int(time.time() - age)}\n")

    def old_ack(self):
        with open(self.ack, "w", encoding="utf-8"):
            pass
        past = time.time() - 100
        os.utime(self.ack, (past, past))


@unittest.skipUnless(fcntl and ewshold.kibana_objects.DIR_FD, "no flock or folder descriptors here")
class EwsHoldTest(HoldFolder):

    def hold(self, names=("tpotinit", "ewsposter"), **options):
        self.said = []
        options.setdefault("timeout", 3)
        options.setdefault("ack_poll", 0.01)
        options.setdefault("running", lambda: True)
        options.setdefault("settle", 0)
        return ewshold.EwsHold(self.data, names, log=lambda kind, text: self.said.append((kind, text)), **options)

    def test_the_marker_comes_first_then_the_ack(self):
        with FakeEwsposter(self.ews):
            with self.hold() as hold:
                self.assertTrue(os.path.isfile(self.marker))
                self.assertTrue(hold.held and hold.acked)
                self.assertGreaterEqual(os.stat(self.ack).st_mtime_ns, os.stat(self.marker).st_mtime_ns)
                self.assertFalse(self.lock_is_free())
                self.assertEqual(oct(os.stat(self.marker).st_mode & 0o777), oct(0o660))
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())
        self.assertEqual(oct(os.stat(self.lock).st_mode & 0o777), oct(0o660))

    def test_no_ack_means_no_probe(self):
        probed = []
        with self.assertRaises(HoldError) as caught:
            with self.hold(timeout=0.3):
                probed.append(True)
        self.assertEqual(probed, [])
        self.assertIn("ewsposter did not confirm the hold within 0.3 s; nothing was probed", str(caught.exception))
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())

    def test_an_ack_from_before_the_marker_does_not_count(self):
        self.old_ack()
        with self.assertRaises(HoldError):
            with self.hold(timeout=0.3):
                self.fail("probed")
        self.assertFalse(os.path.exists(self.marker))

    def test_a_stuck_hold_is_named(self):
        self.write_state("stuck")
        with self.assertRaises(HoldError) as caught:
            with self.hold(timeout=0.1):
                pass
        self.assertIn("hold.state says stuck", str(caught.exception))
        self.write_state("held", age=600)
        with self.assertRaises(HoldError) as caught:
            with self.hold(timeout=0.1):
                pass
        self.assertIn("hold.state is missing or older than 120 s", str(caught.exception))

    def test_the_marker_goes_on_an_error_and_on_ctrl_c(self):
        for error in (RuntimeError, KeyboardInterrupt):
            with self.subTest(error=error.__name__), FakeEwsposter(self.ews):
                with self.assertRaises(error):
                    with self.hold():
                        self.assertTrue(os.path.isfile(self.marker))
                        raise error()
                self.assertFalse(os.path.exists(self.marker))
                self.assertTrue(self.lock_is_free())

    def test_ctrl_c_while_waiting_for_the_ack(self):
        def sleep(_seconds):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            with self.hold(sleep=sleep):
                self.fail("probed")
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())

    def test_a_busy_lock_ends_at_once(self):
        self.lock_elsewhere()
        start = time.monotonic()
        with self.assertRaises(CheckError) as caught:
            with self.hold():
                self.fail("probed")
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(str(caught.exception), "another check is running")
        self.assertNotIsInstance(caught.exception, HoldError)
        self.assertFalse(os.path.exists(self.marker))

    def test_no_ewsposter_no_marker_but_the_lock(self):
        with self.hold(names=("tpotinit", "cowrie")) as hold:
            self.assertFalse(os.path.exists(self.marker))
            self.assertFalse(hold.held)
            self.assertEqual(hold.note, "ewsposter does not run here, nothing to hold")
            self.assertIn(("info", "ewsposter does not run here, nothing to hold"), self.said)
            self.assertFalse(self.lock_is_free())
        self.assertTrue(self.lock_is_free())

    def test_ewsposter_down_gets_the_marker_without_waiting(self):
        text = "ewsposter is not running; the hold marker is set, so it starts held if it comes up during the check"
        start = time.monotonic()
        with self.hold(running=lambda: False, timeout=30) as hold:
            self.assertLess(time.monotonic() - start, 5)
            self.assertTrue(os.path.isfile(self.marker))
            self.assertTrue(hold.held)
            self.assertFalse(hold.acked)
            self.assertEqual(hold.note, text)
            self.assertIn(("info", text), self.said)
            hold.check()
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())

    def wait_for_stop(self, hold, text):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                hold.check()
            except HoldError as error:
                self.assertEqual(str(error), text)
                return
            time.sleep(0.02)
        self.fail("the check went on")

    def test_a_failing_refresh_stops_the_check(self):
        text = "the hold marker could not be refreshed, the check stops"
        with FakeEwsposter(self.ews):
            with self.hold(refresh=0.05) as hold:
                hold.check()

                def failing():
                    raise OSError("no space left")
                hold._put_marker = failing
                self.wait_for_stop(hold, text)
                self.assertIn(("error", text), self.said)
                self.assertEqual(sum(1 for kind, _text in self.said if kind == "warn"), 2)
                self.assertTrue(os.path.isfile(self.marker))
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())

    def test_a_removed_marker_stops_the_check(self):
        text = "the hold marker was removed (T-Pot restarted?), the check stops"
        target = os.path.join(self.data, "elsewhere")
        with open(target, "w", encoding="utf-8") as out:
            out.write("keep\n")
        for replace in (False, True):
            with self.subTest(link=replace), FakeEwsposter(self.ews):
                with self.hold(watch=0.05) as hold:
                    hold.check()
                    os.unlink(self.marker)
                    if replace:
                        os.symlink(target, self.marker)
                    self.wait_for_stop(hold, text)
                    self.assertIn(("error", text), self.said)
                    time.sleep(0.2)
                    self.assertEqual(os.path.islink(self.marker), replace)
                    self.assertTrue(replace or not os.path.exists(self.marker), "the marker came back")
                if replace:
                    self.assertTrue(os.path.islink(self.marker))
                    os.unlink(self.marker)
                self.assertTrue(self.lock_is_free())
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep\n")

    def test_the_end_of_the_hold_is_no_removed_marker(self):
        with FakeEwsposter(self.ews):
            with self.hold(watch=0.01, refresh=0.02) as hold:
                time.sleep(0.2)
                hold.check()
        self.assertNotIn(("error", "the hold marker was removed (T-Pot restarted?), the check stops"), self.said)

    def test_docker_that_cannot_say_counts_as_running(self):
        text = "docker cannot say whether ewsposter runs; the check waits for its confirmation as if it ran"
        with self.assertRaises(HoldError):
            with self.hold(running=lambda: None, timeout=0.2):
                self.fail("probed")
        self.assertIn(("warn", text), self.said)
        self.assertFalse(os.path.exists(self.marker))
        with FakeEwsposter(self.ews):
            with self.hold(running=lambda: None) as hold:
                self.assertTrue(hold.acked)
        with mock.patch("tpotctl.ops.containers", side_effect=__import__("tpotctl.ops").ops.OpsError("denied")):
            self.assertIsNone(ewshold.ewsposter_runs())

    def test_the_end_settles_10_s_by_default(self):
        self.assertEqual(ewshold.EwsHold(self.data, ()).settle, 10)

    def test_one_failed_refresh_is_tried_again(self):
        with FakeEwsposter(self.ews):
            with self.hold(refresh=0.05) as hold:
                real = hold._put_marker
                calls = []
                hold.watch = 1000

                def flaky():
                    calls.append(True)
                    if len(calls) % 2:
                        raise OSError("busy")
                    real()
                hold._put_marker = flaky
                deadline = time.monotonic() + 3
                while len(calls) < 6 and time.monotonic() < deadline:
                    time.sleep(0.02)
                hold.check()

    def test_no_compose_entry_asks_no_docker(self):
        def running():
            raise AssertionError("docker asked")
        with self.hold(names=("tpotinit",), running=running):
            pass

    def test_links_are_refused(self):
        target = os.path.join(self.data, "elsewhere")
        with open(target, "w", encoding="utf-8") as out:
            out.write("keep\n")
        for name in ("hold", "check.lock"):
            path = os.path.join(self.ews, name)
            with self.subTest(name=name):
                if os.path.lexists(path):
                    os.unlink(path)
                os.symlink(target, path)
                with self.assertRaises(CheckError) as caught:
                    with self.hold(timeout=0.2):
                        self.fail("probed")
                self.assertIn("is a link", str(caught.exception))
                self.assertNotIsInstance(caught.exception, HoldError)
                self.assertTrue(os.path.islink(path))
                os.unlink(path)
                with open(target, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "keep\n")
                self.assertTrue(self.lock_is_free())

    def test_a_link_as_the_ews_folder_is_refused(self):
        shutil.rmtree(self.ews)
        elsewhere = tempfile.mkdtemp(prefix="tpot-hold-elsewhere-")
        self.addCleanup(shutil.rmtree, elsewhere)
        os.symlink(elsewhere, self.ews)
        with self.assertRaises(CheckError):
            with self.hold():
                self.fail("probed")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_the_ews_folder_is_made_when_missing(self):
        shutil.rmtree(self.ews)
        with self.hold(names=("tpotinit",)):
            self.assertTrue(os.path.isdir(self.ews))
        with self.assertRaises(CheckError):
            with ewshold.EwsHold(os.path.join(self.data, "missing"), ()):
                self.fail("probed")

    def test_the_marker_is_written_anew_while_the_hold_lasts(self):
        with FakeEwsposter(self.ews):
            with self.hold(refresh=0.05):
                first = os.stat(self.marker).st_ino
                deadline = time.monotonic() + 3
                while os.stat(self.marker).st_ino == first and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertNotEqual(os.stat(self.marker).st_ino, first)
        time.sleep(0.2)
        self.assertFalse(os.path.exists(self.marker))

    def test_settle_before_the_marker_goes(self):
        slept = []
        with FakeEwsposter(self.ews):
            hold = self.hold(settle=7, sleep=lambda seconds: slept.append(seconds) or time.sleep(0.01))
            with hold:
                slept.clear()
        self.assertEqual(slept, [7])
        self.assertFalse(os.path.exists(self.marker))
        slept.clear()
        with self.hold(names=("tpotinit",), settle=7, sleep=slept.append):
            pass
        self.assertEqual(slept, [])

    def test_ews_state(self):
        self.assertEqual(ewshold.ews_state(self.data), ("unknown", None))
        self.write_state("held", age=5)
        state, age = ewshold.ews_state(self.data)
        self.assertEqual(state, "held")
        self.assertLess(abs(age - 5), 2)
        self.write_state("stuck")
        self.assertEqual(ewshold.ews_state(self.data)[0], "stuck")
        self.write_state("normal", age=300)
        self.assertEqual(ewshold.ews_state(self.data)[0], "unknown")
        self.write_state("odd")
        self.assertEqual(ewshold.ews_state(self.data), ("unknown", None))
        os.unlink(os.path.join(self.ews, "hold.state"))
        target = os.path.join(self.data, "state")
        with open(target, "w", encoding="utf-8") as out:
            out.write(f"normal {int(time.time())}\n")
        os.symlink(target, os.path.join(self.ews, "hold.state"))
        self.assertEqual(ewshold.ews_state(self.data), ("unknown", None))
        self.assertEqual(ewshold.ews_state(os.path.join(self.data, "missing")), ("unknown", None))


@unittest.skipUnless(fcntl and shutil.which("bash") and shutil.which("python3"), "no bash or flock")
class EwsHoldShellTest(HoldFolder):
    """ews_hold.sh with a docker stub; its lock is the flock of its python3 holder, the same lock as
    EwsHold takes."""

    def setUp(self):
        super().setUp()
        self.bin = tempfile.mkdtemp(prefix="tpot-hold-bin-")
        self.addCleanup(shutil.rmtree, self.bin)
        self.stub("docker", "#!/bin/sh\n"
                            "echo \"$*\" >> \"$STUB_CALLS\"\n"
                            "case \"${STUB_EWS_RUNNING:-true}\" in\n"
                            "  fail) echo 'permission denied while trying to connect to the docker API' >&2; exit 1 ;;\n"
                            "  absent) echo 'Error: No such object: ewsposter' >&2; exit 1 ;;\n"
                            "esac\n"
                            "echo \"${STUB_EWS_RUNNING:-true}\"\n")
        self.calls = os.path.join(self.bin, "calls")
        self.compose = os.path.join(self.data, "docker-compose.yml")
        self.write_compose(True)

    def stub(self, name, text):
        path = os.path.join(self.bin, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)

    def write_compose(self, ewsposter):
        with open(self.compose, "w", encoding="utf-8") as out:
            out.write("services:\n  tpotinit:\n    container_name: tpotinit\n")
            if ewsposter:
                out.write("  ewsposter:\n    container_name: ewsposter\n")

    def env(self, **extra):
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""), TPOT_GUM="off",
                   STUB_CALLS=self.calls, myEWS_HOLD_TIMEOUT="2", myEWS_HOLD_POLL="0.05",
                   myEWS_HOLD_SETTLE="0", myEWS_HOLD_TICK="0.1")
        env.update(extra)
        return env

    def source(self, script, **extra):
        text = f'source "{EWS_HOLD}"\n{script}'
        return subprocess.run(["bash", "-c", text, "test"], capture_output=True, universal_newlines=True,
                              env=self.env(**extra), timeout=60)

    BEGIN = ('fuEWS_HOLD_BEGIN "$DATA" "$COMPOSE"; myRC=$?; echo "rc=${myRC}"\n'
             '[ "${myRC}" -eq 0 ] && echo PROBE && [ -f "$DATA/ews/hold" ] && echo MARKER\n'
             'fuEWS_HOLD_END; [ -e "$DATA/ews/hold" ] || echo GONE\n')

    def begin(self, **extra):
        return self.source(self.BEGIN, DATA=self.data, COMPOSE=self.compose, **extra)

    def run_cli(self, *command, **extra):
        return subprocess.run([EWS_HOLD, "--data", self.data, "--compose", self.compose, "run", "--"] + list(command),
                              capture_output=True, universal_newlines=True, env=self.env(**extra), timeout=60)

    def test_the_marker_then_the_ack(self):
        with FakeEwsposter(self.ews):
            result = self.begin()
        self.assertEqual(result.stdout.count("PROBE"), 1, result.stdout + result.stderr)
        self.assertIn("rc=0\nPROBE\nMARKER\n", result.stdout)
        self.assertIn("ewsposter holds", result.stdout)
        self.assertIn("GONE", result.stdout)
        self.assertTrue(self.lock_is_free())

    def test_no_ack_means_no_probe(self):
        result = self.begin()
        self.assertIn("rc=3", result.stdout)
        self.assertNotIn("PROBE", result.stdout)
        self.assertIn("ewsposter did not confirm the hold within 2 s; nothing was probed", result.stderr)
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())

    def test_an_ack_from_before_the_marker_does_not_count(self):
        self.old_ack()
        result = self.begin()
        self.assertIn("rc=3", result.stdout)
        self.assertNotIn("PROBE", result.stdout)

    def test_a_stuck_hold_is_named(self):
        self.write_state("stuck")
        result = self.begin(myEWS_HOLD_TIMEOUT="0")
        self.assertIn("hold.state says stuck", result.stderr)

    def test_a_busy_lock_ends_at_once(self):
        self.lock_elsewhere()
        start = time.monotonic()
        with FakeEwsposter(self.ews):
            result = self.begin()
        self.assertLess(time.monotonic() - start, 5)
        self.assertIn("rc=3", result.stdout)
        self.assertIn("Another check is running.", result.stderr)
        self.assertNotIn("PROBE", result.stdout)
        self.assertFalse(os.path.exists(self.marker))

    def test_no_ewsposter_no_marker_but_the_lock(self):
        script = ('fuEWS_HOLD_BEGIN "$DATA" "$COMPOSE"; echo "rc=$?"; [ -e "$DATA/ews/hold" ] || echo NOMARKER\n'
                  'python3 -c "import fcntl, os, sys\n'
                  'h = os.open(sys.argv[1], os.O_RDWR)\n'
                  'try:\n    fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB); print(\'FREE\')\n'
                  'except BlockingIOError:\n    print(\'TAKEN\')" "$DATA/ews/check.lock" 9>&-\n'
                  'fuEWS_HOLD_END\n')
        self.write_compose(False)
        result = self.source(script, DATA=self.data, COMPOSE=self.compose)
        self.assertIn("rc=0\nNOMARKER\nTAKEN\n", result.stdout, result.stderr)
        self.assertIn("ewsposter does not run here, nothing to hold.", result.stdout)
        self.assertTrue(self.lock_is_free())
        self.assertFalse(os.path.exists(self.calls), "docker asked without ewsposter in the compose file")

    def test_ewsposter_down_gets_the_marker_without_waiting(self):
        start = time.monotonic()
        result = self.begin(STUB_EWS_RUNNING="false", myEWS_HOLD_TIMEOUT="30")
        self.assertLess(time.monotonic() - start, 10)
        self.assertIn("rc=0\nPROBE\nMARKER\n", result.stdout, result.stderr)
        self.assertIn("ewsposter is not running; the hold marker is set, so it starts held if it comes up "
                      "during the check.", result.stdout)
        self.assertIn("GONE", result.stdout)
        self.assertTrue(self.lock_is_free())

    def test_a_failing_refresh_stops_the_check(self):
        target = os.path.join(self.data, "elsewhere")
        with open(target, "w", encoding="utf-8") as out:
            out.write("keep\n")
        with FakeEwsposter(self.ews):
            process = subprocess.Popen(
                [EWS_HOLD, "--data", self.data, "--compose", self.compose, "run", "--", "sleep", "30"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                env=self.env(myEWS_HOLD_REFRESH="1", myEWS_HOLD_WATCH="1000"), start_new_session=True)
            self.addCleanup(lambda: os.killpg(process.pid, signal.SIGKILL) if process.poll() is None else None)
            self.wait_for(lambda: os.path.isfile(self.marker) and os.path.isfile(self.ack))
            time.sleep(0.3)
            os.unlink(self.marker)
            os.symlink(target, self.marker)
            out, err = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 143, out + err)
        self.assertIn("The hold marker could not be refreshed, the check stops.", err)
        self.assertEqual(out.count("could not be written anew"), 2, out)
        self.assertTrue(os.path.islink(self.marker))
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep\n")
        self.assertTrue(self.lock_is_free())

    def start_cli(self, *command, **extra):
        process = subprocess.Popen(
            [EWS_HOLD, "--data", self.data, "--compose", self.compose, "run", "--"] + list(command),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=self.env(**extra),
            start_new_session=True)
        self.addCleanup(lambda: os.killpg(process.pid, signal.SIGKILL) if process.poll() is None else None)
        return process

    def test_a_removed_marker_stops_the_check(self):
        with FakeEwsposter(self.ews):
            process = self.start_cli("sleep", "30", myEWS_HOLD_WATCH="1")
            self.wait_for(lambda: os.path.isfile(self.marker) and os.path.isfile(self.ack))
            os.unlink(self.marker)
            start = time.monotonic()
            out, err = process.communicate(timeout=20)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(process.returncode, 143, out + err)
        self.assertIn("The hold marker was removed (T-Pot restarted?), the check stops.", err)
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())

    def test_docker_that_cannot_say_counts_as_running(self):
        result = self.begin(STUB_EWS_RUNNING="fail")
        self.assertIn("rc=3", result.stdout)
        self.assertNotIn("PROBE", result.stdout)
        self.assertIn("docker cannot say whether ewsposter runs", result.stdout)
        self.assertIn("did not confirm the hold", result.stderr)
        with FakeEwsposter(self.ews):
            result = self.begin(STUB_EWS_RUNNING="fail")
        self.assertIn("rc=0\nPROBE\nMARKER\n", result.stdout, result.stderr)
        self.assertIn("ewsposter holds", result.stdout)
        result = self.begin(STUB_EWS_RUNNING="absent")
        self.assertIn("rc=0\nPROBE\nMARKER\n", result.stdout, result.stderr)
        self.assertIn("ewsposter is not running", result.stdout)

    def test_a_signal_to_the_group_keeps_the_lock_until_the_end_of_the_hold(self):
        with FakeEwsposter(self.ews):
            process = self.start_cli("sleep", "30", myEWS_HOLD_SETTLE="3")
            lines = []
            reader = threading.Thread(target=lambda: lines.extend(process.stdout), daemon=True)
            reader.start()
            # the script itself has seen the ack (not only the file is there): the hold began
            self.wait_for(lambda: any("ewsposter holds" in line for line in lines))
            os.killpg(process.pid, signal.SIGTERM)
            time.sleep(1)
            self.assertIsNone(process.poll())
            self.assertTrue(os.path.isfile(self.marker))
            self.assertFalse(self.lock_is_free(), "the lock was freed while the trap still ended the check")
            process.wait(timeout=30)
            reader.join(5)
            err = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(process.returncode, 143, "".join(lines) + err)
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())

    def test_a_link_or_fifo_as_the_state_is_unknown(self):
        state = os.path.join(self.ews, "hold.state")
        target = os.path.join(self.data, "state")
        with open(target, "w", encoding="utf-8") as out:
            out.write(f"stuck {int(time.time())}\n")
        for kind in ("link", "fifo"):
            with self.subTest(kind=kind):
                if os.path.lexists(state):
                    os.unlink(state)
                if kind == "link":
                    os.symlink(target, state)
                else:
                    os.mkfifo(state)
                start = time.monotonic()
                result = self.begin(myEWS_HOLD_TIMEOUT="0")
                self.assertLess(time.monotonic() - start, 10)
                self.assertIn("hold.state is missing or older than", result.stderr)
                self.assertNotIn("says stuck", result.stderr)

    def test_a_fifo_as_the_lock_is_refused_at_once(self):
        os.mkfifo(self.lock)
        start = time.monotonic()
        result = self.begin()
        self.assertLess(time.monotonic() - start, 10)
        self.assertIn("rc=3", result.stdout)
        self.assertIn("check.lock is a link or no regular file", result.stderr)

    def test_the_group_of_the_command_ends_before_the_hold(self):
        grandchild = os.path.join(self.data, "grandchild")
        log = os.path.join(self.data, "log")
        script = (f'sh -c \'trap "" TERM; sleep 1; test -f "{self.marker}" && echo present >> "{log}"\' & '
                  f'echo $! > "{grandchild}"; wait')
        with FakeEwsposter(self.ews):
            process = self.start_cli("sh", "-c", script)
            self.wait_for(lambda: os.path.isfile(grandchild) and os.path.getsize(grandchild) > 0)
            with open(grandchild, encoding="utf-8") as handle:
                pid = int(handle.read())
            os.kill(process.pid, signal.SIGTERM)
            start = time.monotonic()
            out, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 143, out + err)
        self.assertLess(time.monotonic() - start, 20)
        self.assertTrue(gone(pid))
        with open(log, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "present\n")
        self.assertFalse(os.path.exists(self.marker))

    def test_links_are_refused(self):
        target = os.path.join(self.data, "elsewhere")
        with open(target, "w", encoding="utf-8") as out:
            out.write("keep\n")
        for name in ("hold", "check.lock"):
            path = os.path.join(self.ews, name)
            with self.subTest(name=name), FakeEwsposter(self.ews):
                if os.path.lexists(path):
                    os.unlink(path)
                os.symlink(target, path)
                result = self.begin()
                self.assertIn("rc=3", result.stdout)
                self.assertNotIn("PROBE", result.stdout)
                self.assertIn("is a link", result.stderr)
                self.assertTrue(os.path.islink(path))
                os.unlink(path)
                with open(target, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "keep\n")

    def test_the_lock_is_made_without_following_a_link_and_0660(self):
        with FakeEwsposter(self.ews):
            self.begin()
        self.assertEqual(oct(os.stat(self.lock).st_mode & 0o777), oct(0o660))
        os.unlink(self.lock)
        os.link(os.path.join(self.data, "docker-compose.yml"), self.lock)
        result = self.begin()
        self.assertIn("rc=3", result.stdout)

    def test_the_marker_is_written_anew_and_the_renewal_ends(self):
        script = ('fuEWS_HOLD_BEGIN "$DATA" "$COMPOSE" || exit 3\n'
                  'first=$(ls -i "$DATA/ews/hold" | awk "{print \\$1}")\n'
                  'for i in $(seq 1 60); do\n'
                  '  [ "$(ls -i "$DATA/ews/hold" | awk "{print \\$1}")" != "${first}" ] && echo RENEWED && break\n'
                  '  sleep 0.1\n'
                  'done\n'
                  'wait\n'
                  'fuEWS_HOLD_END\n'
                  'sleep 1.5\n'
                  '[ -e "$DATA/ews/hold" ] || echo GONE\n')
        with FakeEwsposter(self.ews):
            result = self.source(script, DATA=self.data, COMPOSE=self.compose, myEWS_HOLD_REFRESH="1")
        self.assertIn("RENEWED\n", result.stdout, result.stderr)
        self.assertIn("GONE", result.stdout)
        self.assertEqual([n for n in os.listdir(self.ews) if n.endswith(".tmp")], [])

    def test_the_command_runs_under_the_hold(self):
        probe = f'test -f "{self.marker}" && echo HELD; exit 7'
        with FakeEwsposter(self.ews):
            result = self.run_cli("sh", "-c", probe)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("HELD", result.stdout)
        self.assertFalse(os.path.exists(self.marker))
        result = self.run_cli("sh", "-c", "echo PROBE")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertNotIn("PROBE", result.stdout)
        self.assertEqual(self.run_cli().returncode, 2)

    def test_int_and_term_lift_the_hold(self):
        for number, code in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with self.subTest(signal=number), FakeEwsposter(self.ews):
                process = subprocess.Popen(
                    [EWS_HOLD, "--data", self.data, "--compose", self.compose, "run", "--", "sleep", "30"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=self.env(),
                    start_new_session=True)
                self.wait_for(lambda: os.path.isfile(self.marker) and os.path.isfile(self.ack))
                time.sleep(0.3)
                os.killpg(process.pid, number)
                process.communicate(timeout=30)
                self.assertEqual(process.returncode, code)
                self.assertFalse(os.path.exists(self.marker))
                self.assertTrue(self.lock_is_free())

    def test_a_killed_helper_renews_nothing(self):
        with FakeEwsposter(self.ews):
            process = subprocess.Popen(
                [EWS_HOLD, "--data", self.data, "--compose", self.compose, "run", "--", "sleep", "30"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env(myEWS_HOLD_REFRESH="1"),
                start_new_session=True)
            self.addCleanup(lambda: os.killpg(process.pid, signal.SIGKILL) if process.poll() is None else None)
            self.wait_for(lambda: os.path.isfile(self.marker) and os.path.isfile(self.ack))
            time.sleep(0.3)
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            time.sleep(0.5)
            before = os.stat(self.marker).st_ino
            time.sleep(2.5)
            self.assertEqual(os.stat(self.marker).st_ino, before)

    def wait_for(self, condition, seconds=10):
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() > deadline:
                self.fail("timed out")
            time.sleep(0.05)

    def test_data_path_of_the_checkout(self):
        checkout = tempfile.mkdtemp(prefix="tpot-hold-co-")
        self.addCleanup(shutil.rmtree, checkout)
        cases = {"": f"{checkout}/data", "TPOT_DATA_PATH=./data\n": f"{checkout}/data",
                 "TPOT_DATA_PATH=/srv/tpot\n": "/srv/tpot", 'TPOT_DATA_PATH="~/tdata"\n': f"{os.environ['HOME']}/tdata"}
        for text, expected in cases.items():
            with self.subTest(env=text):
                with open(os.path.join(checkout, ".env"), "w", encoding="utf-8") as out:
                    out.write(text)
                result = self.source('fuEWS_DATA_PATH "$CO"', CO=checkout)
                self.assertEqual(result.stdout.strip(), expected)


@unittest.skipUnless(fcntl and shutil.which("bash") and shutil.which("python3"), "no bash")
class HptestUnderTheHoldTest(HoldFolder):
    """A TERM during the scans: the hold ends only after the scanners (stubs of timeout and nmap that
    keep running and take a second to end) are gone."""

    SCANNER = ('#!/bin/bash\n'
               'case " $* " in *" nmap "*) ;; *) exit 0 ;; esac\n'
               'echo $$ >> "$STUB_SCANS"\n'
               '[ -n "$STUB_SCAN_QUICK" ] && exit 0\n'
               'trap \'trap "" TERM; sleep 1; if [ -f "$STUB_MARKER" ]; then echo present >> "$STUB_LOG"; '
               'else echo gone >> "$STUB_LOG"; fi; exit 143\' TERM\n'
               'while :; do sleep 0.1; done\n')

    def setUp(self):
        super().setUp()
        self.bin = tempfile.mkdtemp(prefix="tpot-hptest-bin-")
        self.addCleanup(shutil.rmtree, self.bin)
        self.checkout = tempfile.mkdtemp(prefix="tpot-hptest-co-")
        self.addCleanup(shutil.rmtree, self.checkout)
        with open(os.path.join(self.checkout, ".env"), "w", encoding="utf-8") as out:
            out.write(f"TPOT_DATA_PATH={self.data}\n")
        with open(os.path.join(self.checkout, "docker-compose.yml"), "w", encoding="utf-8") as out:
            out.write("services:\n  cowrie:\n    container_name: cowrie\n"
                      "  ewsposter:\n    container_name: ewsposter\n")
        config = {"services": {"cowrie": {"ports": [{"target": 22, "published": "22", "protocol": "tcp"},
                                                    {"target": 69, "published": "69", "protocol": "udp"}]}}}
        with open(os.path.join(self.bin, "config.json"), "w", encoding="utf-8") as out:
            json.dump(config, out)
        self.stub("docker", '#!/bin/sh\n[ "$1" = compose ] && exec cat "$STUB_CONFIG"\necho true\n')
        self.stub("timeout", self.SCANNER)
        self.stub("sudo", '#!/bin/sh\n'
                          'while [ "${1#-}" != "$1" ]; do\n'
                          '  case "$1" in\n'
                          '    -v) exit 0 ;;\n'
                          '    -n) [ "$STUB_SUDO_N" = fail ] && exit 1 ;;\n'
                          '    -p) shift ;;\n'
                          '  esac\n'
                          '  shift\n'
                          'done\n'
                          'exec "$@"\n')
        for name in ("nmap", "ncat", "findscu", "getscu", "curl"):
            self.stub(name, "#!/bin/sh\nexit 0\n")
        self.scans = os.path.join(self.bin, "scans")
        self.log = os.path.join(self.bin, "log")

    def stub(self, name, text):
        path = os.path.join(self.bin, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)

    def scanners(self):
        try:
            with open(self.scans, encoding="utf-8") as handle:
                return [int(line) for line in handle.read().split()]
        except OSError:
            return []

    def env(self, **extra):
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""), TPOT_GUM="off",
                   TPOTCE_DIR=self.checkout, STUB_CONFIG=os.path.join(self.bin, "config.json"),
                   STUB_SCANS=self.scans, STUB_LOG=self.log, STUB_MARKER=self.marker,
                   myEWS_HOLD_SETTLE="0", myEWS_HOLD_POLL="0.05", myEWS_HOLD_TICK="0.1")
        env.update(extra)
        return env

    def test_a_skipped_udp_scan_says_so(self):
        with FakeEwsposter(self.ews):
            result = subprocess.run([HPTEST, "127.0.0.1"], capture_output=True, universal_newlines=True,
                                    env=self.env(STUB_SUDO_N="fail", STUB_SCAN_QUICK="1", TPOT_MARKS="1"),
                                    timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("UDP scan skipped: sudo needs a password again."), 1, result.stdout)
        self.assertIn("@@tpot warn scan UDP scan skipped: sudo needs a password again\n", result.stdout)
        self.assertEqual(len(self.scanners()), 1)
        self.assertFalse(os.path.exists(self.marker))

    def test_term_during_the_scan(self):
        env = self.env()
        with FakeEwsposter(self.ews):
            process = subprocess.Popen([HPTEST, "127.0.0.1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       universal_newlines=True, env=env, start_new_session=True)
            self.addCleanup(lambda: os.killpg(process.pid, signal.SIGKILL) if process.poll() is None else None)
            deadline = time.monotonic() + 30
            while len(self.scanners()) < 2:
                if time.monotonic() > deadline or process.poll() is not None:
                    self.fail("no scan: " + "".join(process.communicate()))
                time.sleep(0.05)
            self.assertTrue(os.path.isfile(self.marker))
            os.kill(process.pid, signal.SIGTERM)
            out, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 143, out + err)
        with open(self.log, encoding="utf-8") as handle:
            self.assertEqual(handle.read().split(), ["present", "present"])
        for pid in self.scanners():
            self.assertTrue(gone(pid))
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())


class ChecksUnderTheHoldTest(unittest.TestCase):
    """hptest.sh and the pipeline test run under the hold, their dry runs and --tools-only without one,
    and the pipeline test takes no source IPs from Elasticsearch."""

    @staticmethod
    def read(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_hptest_holds_before_the_first_probe_and_lifts_after_the_restarts(self):
        text = self.read(HPTEST)
        self.assertIn('source "${myHERE}/ews_hold.sh"', text)
        begin = text.index("fuEWS_HOLD_BEGIN")
        # probes still in the background after Ctrl+C end before the hold does
        self.assertLess(text.index("fuEWS_HOLD_TRAPS fuSTOP_PROBES"), begin)
        self.assertLess(text.index('[ -n "${myTOOLS_ONLY}" ] && exit 0'), begin)
        self.assertLess(begin, text.index("fuMARK phase probe"))
        self.assertLess(text.index("fuMARK phase restart"), text.rindex("fuEWS_HOLD_END"))
        self.assertLess(text.rindex("fuEWS_HOLD_END"), text.index('fuMARK phase "done"'))

    def test_the_pipeline_test_holds_before_the_first_injection(self):
        text = self.read(PIPELINE)
        main = text[text.index("# Main"):]
        self.assertIn('source "${myHERE}/ews_hold.sh"', text)
        begin = main.index("fuEWS_HOLD_BEGIN")
        self.assertLess(main.index('if [ "$myDRYRUN" = 1 ]'), begin)
        self.assertLess(main.index("fuEWS_HOLD_TRAPS fuCLEANUP"), begin)
        self.assertLess(begin, main.index("fuSTARTLISTENERS\nfuINJECT"))
        self.assertLess(main.index("fuWAITMAP"), main.index("fuEWS_HOLD_END"))
        self.assertNotIn("trap fuCLEANUP", text)

    def test_the_pipeline_test_takes_no_ips_from_elasticsearch(self):
        text = self.read(PIPELINE)
        body = text[text.index("function fuPICKIPS {"):]
        body = body[:body.index("\n}\n") + 3]
        for gone in ("docker", "curl", "elasticsearch", "9200", "_search", "myIPS"):
            self.assertNotIn(gone, body)
        self.assertNotIn("--ips", text)
        self.assertIn('myFALLBACKIPS="8.8.8.8 208.67.222.222 8.8.4.4 4.2.2.2"', text)

    @unittest.skipUnless(shutil.which("bash"), "no bash")
    def test_the_pipeline_test_ips_are_the_fixed_ones(self):
        text = self.read(PIPELINE)
        body = text[text.index("function fuPICKIPS {"):]
        body = body[:body.index("\n}\n") + 3]
        functions = re.search(r"^function fuTESTIP .*$", text, re.M).group(0)
        script = ('fuUI_INFO () { echo "$*"; }; fuUI_HINT () { :; }\n'
                  'myFALLBACKIPS="8.8.8.8 208.67.222.222 8.8.4.4 4.2.2.2"\n'
                  'myTYPES="cowrie,dionaea,honeytrap,rdphoneypot,ciscoasa"\n'
                  f"{functions}\n{body}\nfuPICKIPS\n")
        result = subprocess.run(["bash", "-c", script], capture_output=True, universal_newlines=True)
        self.assertEqual(result.stdout.strip(), "test source IPs: cowrie=8.8.8.8 dionaea=208.67.222.222 "
                                                "honeytrap=8.8.4.4 rdphoneypot=4.2.2.2 ciscoasa=8.8.8.8",
                         result.stderr)

    def test_the_cli_has_no_ips_option(self):
        from tpotctl import cli
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.build_parser().parse_args(["check", "pipeline", "--ips", "192.0.2.1"])


# ---------------------------------------------------------------------------
# inventory and probes (tpotctl.checks.inventory, tpotctl.checks.probes)
# ---------------------------------------------------------------------------

CATALOGUE = os.path.join(REPO, "compose", "tpot_services.yml")
LOGSTASH_CONF = os.path.join(REPO, "docker", "elk", "logstash", "dist", "logstash.conf")
TOKEN = probes.token_for("abc123")


def load_catalogue():
    with open(CATALOGUE, encoding="utf-8") as handle:
        return yaml.safe_load(handle)["services"]


@unittest.skipUnless(yaml, "PyYAML is missing (use the venv)")
class CoverageTest(unittest.TestCase):
    """Every honeypot of the catalogue has a probe or is external; a new one without either fails."""

    def setUp(self):
        self.services = load_catalogue()
        self.groups = inventory.catalogue_groups(REPO)

    def test_every_honeypot_has_a_probe_or_is_external(self):
        self.assertEqual(probes.uncovered(inventory.honeypot_names(self.groups)), [])
        # the nine Conpot services count one by one, not as one image
        conpot = [name for name, group in self.groups.items() if group == "conpot"]
        self.assertEqual(len(conpot), 9)
        for name in conpot:
            self.assertIn(name, probes.PROBES)

    def test_a_new_honeypot_without_a_probe_fails(self):
        groups = dict(self.groups, newpot="honeypots", newics="conpot", newllm="llm", newtool="nsm", newdb="elk")
        self.assertEqual(probes.uncovered(inventory.honeypot_names(groups)), ["newics", "newllm", "newpot", "newtool"])

    def test_no_probe_without_a_honeypot_and_external_is_not_probed(self):
        honeypots = {name for name, group in self.groups.items() if group in inventory.HONEYPOT_GROUPS}
        self.assertEqual(set(probes.PROBES) - honeypots, set())
        self.assertEqual(set(probes.PROBES) & inventory.EXTERNAL, set())
        self.assertEqual(inventory.EXTERNAL, {"honeytrap", "glutton", "p0f", "suricata"})
        self.assertEqual(inventory.EXTERNAL_HINT, "seen only for traffic from outside (NFQ / passive capture)")
        for name in inventory.EXTERNAL:
            self.assertIn(name, self.groups)
        for name, group in self.groups.items():
            if group in inventory.PASSIVE_GROUPS:
                self.assertIn(name, inventory.EXTERNAL)

    def test_every_port_of_the_catalogue_has_a_handler(self):
        for name, probe in probes.PROBES.items():
            targets = {(p.target, p.protocol) for p in inventory.published_ports(self.services[name])}
            # a new port of a honeypot needs its handler; a handler of a port it lost goes
            self.assertEqual(set(probe.handlers), targets, name)

    def test_the_logs_are_those_of_logstash(self):
        with open(LOGSTASH_CONF, encoding="utf-8") as handle:
            text = handle.read()
        inputs = text[:text.index("\nfilter {")]
        found = re.findall(r'path => \["/data/([^"]+)"\]\s*(?:codec => \S+\s*)?type => "([^"]+)"', inputs)
        self.assertGreater(len(found), 30)
        types = dict(found)
        for name in inventory.honeypot_names(self.groups):
            kind, path = inventory.LOGS[name]
            matches = [pattern for pattern in types if fnmatch.fnmatch(path, pattern)]
            self.assertTrue(matches, f"{name}: {path} is no input of logstash.conf")
            self.assertEqual(types[matches[0]], kind, name)

    def test_the_conpot_logs_are_those_of_the_catalogue(self):
        for name, group in self.groups.items():
            if group != "conpot":
                continue
            log = [item.split("=", 1)[1] for item in self.services[name]["environment"]
                   if item.startswith("CONPOT_JSON_LOG=")][0]
            self.assertEqual("conpot/log/" + os.path.basename(log), inventory.LOGS[name][1])


def container(name, state="running"):
    return ops.Container(name, state, "", "", "", "")


def port(target, published=None, protocol="tcp", host_ip=None):
    entry = {"mode": "ingress", "target": target, "published": str(published or target), "protocol": protocol}
    if host_ip:
        entry["host_ip"] = host_ip
    return entry


GROUPS = {"tpotinit": "core", "cowrie": "honeypots", "dionaea": "honeypots", "ciscoasa": "honeypots",
          "conpot_IEC104": "conpot", "galah": "llm", "glutton": "honeypots", "p0f": "nsm",
          "elasticsearch": "elk", "nginx": "tools"}


class InventoryTest(unittest.TestCase):

    CONFIG = {"services": {
        "tpotinit": {"container_name": "tpotinit"},
        # the customizer moved SSH to 2222; Telnet bound to one address
        "cowrie": {"container_name": "cowrie", "ports": [port(22, 2222), port(23, host_ip="192.0.2.10")]},
        "dionaea": {"container_name": "dionaea", "ports": [port(21), port(69, protocol="udp"),
                                                         port(3306, host_ip="127.0.0.1")]},
        "ciscoasa": {"container_name": "ciscoasa", "ports": [port(500, 15500, "udp"), port(8443)]},
        "conpot_IEC104": {"container_name": "conpot_iec104", "ports": [port(161, protocol="udp"), port(2404)]},
        "galah": {"container_name": "galah", "ports": [port(80, host_ip="0.0.0.0"), port(443, host_ip="::")]},
        "glutton": {"container_name": "glutton", "network_mode": "host"},
        "p0f": {"container_name": "p0f", "network_mode": "host"},
        "elasticsearch": {"container_name": "elasticsearch", "ports": [port(9200, 64298, host_ip="127.0.0.1")]},
        "nginx": {"container_name": "nginx", "ports": [port(64297)]},
        "mypot": {"container_name": "mypot", "ports": [port(9999)]},
    }}
    FOUND = [container("cowrie"), container("dionaea", "exited"), container("ciscoasa"),
             container("conpot_iec104"), container("glutton"), container("p0f"), container("elasticsearch"),
             container("other_thing")]

    def take(self):
        return {h.name: h for h in inventory.inventory(
            REPO, {"TPOT_DATA_PATH": "/srv/tpot"}, config=self.CONFIG, found=self.FOUND, groups=GROUPS)}

    def test_honeypots_and_their_published_ports(self):
        found = self.take()
        self.assertEqual(sorted(found), ["ciscoasa", "conpot_IEC104", "cowrie", "dionaea", "galah", "glutton", "p0f"])
        self.assertEqual(found["cowrie"].ports, [Port(2222, 22), Port(23, 23, "tcp", "192.0.2.10")])
        self.assertEqual(found["dionaea"].ports, [Port(21, 21), Port(69, 69, "udp")])
        self.assertEqual(found["ciscoasa"].ports, [Port(15500, 500, "udp"), Port(8443, 8443)])
        self.assertEqual(found["galah"].ports, [Port(80, 80), Port(443, 443)])
        self.assertEqual(found["conpot_IEC104"].container, "conpot_iec104")
        self.assertEqual(found["conpot_IEC104"].group, "conpot")
        self.assertEqual(found["conpot_IEC104"].logstash_type, "ConPot")
        self.assertEqual(found["conpot_IEC104"].log_path, "/srv/tpot/conpot/log/conpot_IEC104.json")
        self.assertEqual(found["cowrie"].logstash_type, "Cowrie")
        self.assertEqual(found["cowrie"].log_path, "/srv/tpot/cowrie/log/cowrie.json")
        self.assertEqual(found["galah"].group, "llm")

    def test_a_stopped_or_missing_container_is_not_running(self):
        found = self.take()
        self.assertTrue(found["cowrie"].running)
        self.assertFalse(found["dionaea"].running)
        self.assertFalse(found["galah"].running)    # no container at all

    def test_external_ones(self):
        found = self.take()
        self.assertTrue(found["glutton"].external)
        self.assertTrue(found["p0f"].external)
        self.assertEqual(found["glutton"].ports, [])
        self.assertFalse(found["cowrie"].external)

    def test_ports_of_the_short_form(self):
        service = {"ports": ["2222:22", "127.0.0.1:64298:9200", "[::1]:80:80", "192.0.2.1:8080:80/udp",
                             "64295:22", "5000-5010:5000-5010", 25, "[2001:db8::1]:443:443"]}
        self.assertEqual(inventory.published_ports(service), [
            Port(2222, 22), Port(8080, 80, "udp", "192.0.2.1"), Port(25, 25), Port(443, 443, "tcp", "2001:db8::1")])

    def test_compose_config_of_the_compose_file_in_use(self):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.CONFIG), "")

        found = inventory.inventory(REPO, {"TPOT_DOCKER_COMPOSE": "./compose/sensor.yml"}, found=self.FOUND,
                                    groups=GROUPS, run=run)
        # a compose file below the checkout root: the checkout is the project and its .env counts
        self.assertEqual(calls, [["docker", "compose", "--project-directory", REPO,
                                  "--env-file", os.path.join(REPO, ".env"),
                                  "-f", os.path.join(REPO, "compose", "sensor.yml"), "config", "--format", "json"]])
        self.assertIn("cowrie", [h.name for h in found])

    @unittest.skipUnless(shutil.which("docker"), "no docker")
    def test_a_compose_file_below_the_checkout_takes_its_env(self):
        checkout = tempfile.mkdtemp(prefix="tpot-inventory-")
        self.addCleanup(shutil.rmtree, checkout)
        os.mkdir(os.path.join(checkout, "compose"))
        with open(os.path.join(checkout, ".env"), "w", encoding="utf-8") as handle:
            handle.write("TPOT_DATA_PATH=./data\nTPOT_CHECK_PORT=2222\n")
        with open(os.path.join(checkout, "compose", "mine.yml"), "w", encoding="utf-8") as handle:
            handle.write("services:\n  cowrie:\n    image: alpine\n    ports:\n     - \"${TPOT_CHECK_PORT}:22\"\n"
                         "    volumes:\n     - ${TPOT_DATA_PATH}/cowrie/log:/log\n")
        try:
            config = inventory.compose_config(os.path.join(checkout, "compose", "mine.yml"), checkout)
        except CheckError as error:
            if "Cannot run" in str(error):
                self.skipTest(str(error))
            raise
        self.assertEqual(inventory.published_ports(config["services"]["cowrie"]), [Port(2222, 22)])
        # ./data of the .env is the data folder of the checkout, not of compose/
        source = config["services"]["cowrie"]["volumes"][0]["source"]
        self.assertEqual(os.path.realpath(source), os.path.join(os.path.realpath(checkout), "data", "cowrie", "log"))

    def test_the_environment_of_a_service(self):
        self.assertEqual(inventory._environment({"environment": {"A": "1", "B": None}}), {"A": "1", "B": ""})
        self.assertEqual(inventory._environment({"environment": ["A=1", "B=x=y", "C"]}),
                         {"A": "1", "B": "x=y", "C": ""})
        config = {"services": {"sentrypeer": {"container_name": "sentrypeer", "ports": [port(5060)],
                                              "environment": {"SENTRYPEER_PEER_TO_PEER": "1"}}}}
        found = inventory.inventory(REPO, {}, config=config, found=[], groups={"sentrypeer": "honeypots"})
        self.assertEqual(found[0].environment, {"SENTRYPEER_PEER_TO_PEER": "1"})

    def test_errors_are_check_errors(self):
        def failing(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "no such file\n")

        with self.assertRaisesRegex(CheckError, r"docker compose config\): no such file"):
            inventory.inventory(REPO, {}, found=[], groups=GROUPS, run=failing)

        def garbage(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "not json", "")

        with self.assertRaisesRegex(CheckError, "no JSON"):
            inventory.inventory(REPO, {}, found=[], groups=GROUPS, run=garbage)

        def no_docker(argv, **kwargs):
            raise FileNotFoundError("docker")

        with self.assertRaisesRegex(CheckError, "Cannot run docker compose"):
            inventory.inventory(REPO, {}, found=[], groups=GROUPS, run=no_docker)

    @unittest.skipUnless(yaml, "PyYAML is missing (use the venv)")
    def test_the_sensor_edition(self):
        with open(os.path.join(REPO, "compose", "sensor.yml"), encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        groups = inventory.catalogue_groups(REPO)
        names = list(config["services"])
        running = [container(config["services"][n].get("container_name", n)) for n in names]
        found = {h.name: h for h in inventory.inventory(REPO, {}, config=config, found=running, groups=groups)}
        expected = sorted(n for n in names if groups.get(n) in inventory.HONEYPOT_GROUPS + inventory.PASSIVE_GROUPS)
        self.assertEqual(sorted(found), expected)
        for name in ("elasticsearch", "kibana", "map_web", "nginx", "logstash", "tpotinit", "ewsposter"):
            self.assertNotIn(name, found)
        self.assertEqual(found["adbhoney"].ports, [Port(5555, 5555)])
        self.assertTrue(all(h.running for h in found.values()))
        for name, honeypot in found.items():
            self.assertTrue(honeypot.external or name in probes.PROBES, name)
            self.assertTrue(honeypot.logstash_type, name)
            self.assertTrue(honeypot.log_path.startswith(os.path.join(REPO, "data") + os.sep), name)


# -- fake servers for the probes ---------------------------------------------

class FakeTcp:
    """A server on 127.0.0.1 that speaks a script: script[0] (None: nothing) right away, every
    further entry after a line of the client; it records what the client sent."""

    def __init__(self, script, tls=None):
        self.script = list(script)
        self.tls = tls
        self.received = b""
        self.peers = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            conn, peer = self.sock.accept()
        except OSError:
            return
        self.peers.append(peer[1])
        if self.tls:
            try:
                conn = self.tls.wrap_socket(conn, server_side=True)
            except OSError:
                conn.close()
                return
        conn.settimeout(3)
        with conn:
            script = list(self.script)
            try:
                if script and script[0] is not None:
                    conn.sendall(script[0])
                script = script[1:]
                buffer = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    self.received += chunk
                    buffer += chunk
                    while script and b"\n" in buffer:
                        _, _, buffer = buffer.partition(b"\n")
                        conn.sendall(script.pop(0))
            except OSError:
                pass

    def close(self):
        self.sock.close()
        self.thread.join(5)


class FakeBinary(FakeTcp):
    """A server that answers the first `size` bytes with `answer`."""

    def __init__(self, size, answer):
        self.size, self.answer = size, answer
        super().__init__([])

    def serve(self):
        try:
            conn, peer = self.sock.accept()
        except OSError:
            return
        self.peers.append(peer[1])
        conn.settimeout(3)
        with conn:
            try:
                while len(self.received) < self.size:
                    chunk = conn.recv(self.size - len(self.received))
                    if not chunk:
                        return
                    self.received += chunk
                conn.sendall(self.answer)
                conn.recv(1)
            except OSError:
                pass


class FakeUdp:
    def __init__(self, answer=b""):
        self.received = b""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(3)
        self.port = self.sock.getsockname()[1]
        self.answer = answer
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            data, peer = self.sock.recvfrom(65535)
        except OSError:
            return
        self.received = data
        if self.answer:
            self.sock.sendto(self.answer(data) if callable(self.answer) else self.answer, peer)

    def close(self):
        self.thread.join(5)
        self.sock.close()


def free_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def listening():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    return sock


class ProbesTest(unittest.TestCase):

    def run_probe(self, name, server, target, protocol="tcp"):
        self.addCleanup(server.close)
        result = probes.PROBES[name]("127.0.0.1", [Port(server.port, target, protocol)], TOKEN)
        server.thread.join(5)
        return result

    def test_the_token(self):
        self.assertEqual(TOKEN, "tpot-check-abc123")
        for wrong in ("", "ABC", "a-b", "x" * 33, "a b"):
            with self.assertRaises(ValueError):
                probes.token_for(wrong)
        with self.assertRaises(ValueError):
            probes.PROBES["cowrie"]("127.0.0.1", [Port(1, 22)], "not-a-token")

    def test_ssh_banner(self):
        server = FakeTcp([b"SSH-2.0-OpenSSH_8.4p1\r\n"])
        result = self.run_probe("cowrie", server, 22)
        self.assertTrue(result.port_open and result.answered and result.answer_checked, result)
        self.assertEqual(server.received, b"SSH-2.0-TPotCheck " + TOKEN.encode() + b"\r\n")
        self.assertTrue(result.token_sent)
        self.assertEqual(result.src_ports, server.peers)
        self.assertEqual(result.detail, f"{server.port}/tcp: SSH-2.0-OpenSSH_8.4p1")

    def test_the_tarpit_holds_the_connection(self):
        server = FakeTcp([None])
        started = time.monotonic()
        result = self.run_probe("endlessh", server, 2222)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(result.answered, result)
        self.assertIn("holds the connection (tarpit)", result.detail)
        self.assertIn(TOKEN.encode(), server.received)

    def test_telnet_login(self):
        server = FakeTcp([b"\xff\xfd\x18login: ", b"Password: ", b"\r\nLogin incorrect\r\n"])
        result = self.run_probe("cowrie", server, 23)
        self.assertTrue(result.answered, result)
        self.assertIn(TOKEN.encode() + b"\r\n", server.received)
        self.assertIn(b"\xff\xfc\x18", server.received)   # WONT to the DO of the server
        self.assertIn(b"tpot-check\r\n", server.received)

    def test_http(self):
        server = FakeTcp([None, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"])
        result = self.run_probe("wordpot", server, 80)
        self.assertTrue(result.answered, result)
        self.assertTrue(server.received.startswith(b"POST /wp-login.php HTTP/1.1\r\n"))
        self.assertIn(b"log=" + TOKEN.encode() + b"&pwd=tpot-check", server.received)
        self.assertIn(b"User-Agent: Mozilla/5.0 (T-Pot check) " + TOKEN.encode(), server.received)
        self.assertEqual(result.detail, f"{server.port}/tcp: HTTP/1.1 200 OK")

    def test_a_tarpit_that_streams_its_head(self):
        # a status line and then nothing more: the head is not waited for to the end
        server = FakeTcp([None, b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"])
        started = time.monotonic()
        result = self.run_probe("hellpot", server, 8080)
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue(result.answered, result)
        self.assertIn(f"GET /tpot-check/{TOKEN}?probe={TOKEN} HTTP/1.1".encode(), server.received)

    def test_the_llm_honeypots_are_only_knocked_at(self):
        # galah on every port, beelzebub on 22 and on every port of the LLM edition (the default)
        for name, target in (("galah", 80), ("galah", 8080), ("galah", 443), ("galah", 8443),
                             ("beelzebub", 22), ("beelzebub", 3306), ("beelzebub", 8081)):
            server = FakeTcp([b"SSH-2.0-x\r\n" if target == 22 else b"HTTP/1.1 200 OK\r\n\r\n"])
            result = self.run_probe(name, server, target)
            self.assertEqual(server.received, b"", (name, target))
            self.assertEqual(len(server.peers), 1, (name, target))   # the connection was opened
            self.assertTrue(result.port_open, (name, target))
            self.assertFalse(result.answer_checked, (name, target))
            self.assertFalse(result.token_sent, (name, target))
            self.assertEqual(result.detail, f"{server.port}/tcp: port open, an answer would call the LLM")
        self.assertIsNotNone(probes.PROBES["beelzebub"].default)

    def test_no_http_answer(self):
        server = FakeTcp([None, b"garbage\r\n"])
        result = self.run_probe("elasticpot", server, 9200)
        self.assertTrue(result.port_open)
        self.assertFalse(result.answered)
        self.assertIn("no HTTP answer (garbage)", result.detail)

    def test_ftp(self):
        server = FakeTcp([b"220 FTP ready\r\n", b"331 Password required\r\n", b"530 Login incorrect\r\n",
                          b"221 Bye\r\n"])
        result = self.run_probe("dionaea", server, 21)
        self.assertTrue(result.answered, result)
        self.assertIn(b"USER " + TOKEN.encode() + b"\r\nPASS tpot-check\r\nQUIT\r\n", server.received)

    def test_smtp_mail_from(self):
        server = FakeTcp([b"220 mail ESMTP\r\n", b"250-mail\r\n250 AUTH PLAIN LOGIN\r\n", b"250 OK\r\n",
                          b"250 OK\r\n", b"221 Bye\r\n"])
        result = self.run_probe("mailoney", server, 25)
        self.assertTrue(result.answered, result)
        self.assertEqual(server.received, f"EHLO {TOKEN}.example\r\nMAIL FROM:<{TOKEN}@example.org>\r\n"
                                          f"RCPT TO:<{TOKEN}@example.net>\r\nQUIT\r\n".encode())

    def test_smtp_auth(self):
        server = FakeTcp([b"220 mail ESMTP\r\n", b"250 AUTH PLAIN\r\n", b"535 failed\r\n", b"221 Bye\r\n"])
        result = self.run_probe("heralding", server, 25)
        self.assertTrue(result.answered, result)
        self.assertIn(b"AUTH PLAIN " + base64.b64encode(f"\0{TOKEN}\0tpot-check".encode()), server.received)

    def test_pop3_and_imap(self):
        server = FakeTcp([b"+OK ready\r\n", b"+OK\r\n", b"-ERR no\r\n", b"+OK bye\r\n"])
        self.assertTrue(self.run_probe("heralding", server, 110).answered)
        self.assertIn(b"USER " + TOKEN.encode() + b"\r\nPASS tpot-check\r\n", server.received)
        server = FakeTcp([b"* OK ready\r\n", b"a001 NO authentication failed\r\n", b"* BYE\r\n"])
        self.assertTrue(self.run_probe("honeypots", server, 143).answered)
        self.assertIn(f'a001 LOGIN "{TOKEN}" "tpot-check"\r\n'.encode(), server.received)

    def test_socks5(self):
        server = FakeBinary(3, b"\x05\x02")
        result = self.run_probe("honeypots", server, 1080)
        self.assertTrue(result.answered, result)
        self.assertEqual(server.received, b"\x05\x01\x02")

    def test_a_binary_protocol_is_found_by_its_source_port(self):
        server = FakeBinary(6, bytes.fromhex("68040b000000"))
        result = self.run_probe("conpot_IEC104", server, 2404)
        self.assertTrue(result.answered, result)
        self.assertEqual(server.received, bytes.fromhex("680407000000"))
        self.assertFalse(result.token_sent)
        self.assertEqual(result.src_ports, server.peers)

    def test_a_wrong_binary_answer(self):
        server = FakeBinary(19, b"\x03\x00\x00\x0b\x06\xe0\x00")   # X.224 CR, not CC
        result = self.run_probe("rdphoneypot", server, 3389)
        self.assertTrue(result.port_open)
        self.assertFalse(result.answered)
        self.assertIn("no RDP answer", result.detail)

    def test_udp(self):
        server = FakeUdp(answer=b"!\"#$%&'()*+,-./0123456789\r\n")
        result = self.run_probe("ddospot", server, 19, "udp")
        self.assertTrue(result.port_open and result.answered, result)
        self.assertEqual(server.received, TOKEN.encode() + b"\n")
        self.assertTrue(result.token_sent)
        self.assertEqual(len(result.src_ports), 1)

    def test_udp_without_an_answer(self):
        server = FakeUdp()
        with mock.patch.object(probes, "PORT_TIME", 1.0):
            result = self.run_probe("ddospot", server, 123, "udp")
        self.assertFalse(result.port_open)
        self.assertFalse(result.answered)
        self.assertIn("no NTP answer", result.detail)

    def test_ike_follows_the_published_port(self):
        server = FakeUdp(answer=lambda data: data[:8] + b"responder" + data[16:])
        result = self.run_probe("ciscoasa", server, 500, "udp")
        self.assertTrue(result.answered, result)
        self.assertTrue(server.received.startswith(b"tpotprob"))
        server = FakeUdp(answer=lambda data: data[:12] + b"responder")
        result = self.run_probe("ciscoasa", server, 4500, "udp")
        self.assertTrue(result.answered, result)
        self.assertTrue(server.received.startswith(b"\0\0\0\0tpotprob"))

    def test_a_refused_connection_is_a_result(self):
        for name, target in (("cowrie", 22), ("wordpot", 80), ("medpot", 2575), ("dicompot", 11112),
                             ("dionaea", 445), ("mailoney", 465), ("conpot_iccp", 102)):
            result = probes.PROBES[name]("127.0.0.1", [Port(free_port(), target)], TOKEN)
            self.assertFalse(result.port_open, name)
            self.assertFalse(result.answered, name)
            self.assertIn("not open: refused", result.detail, name)

    @unittest.skipUnless(sys.platform.startswith("linux"), "an ICMP port unreachable from loopback: Linux")
    def test_a_refused_udp_port_is_a_result(self):
        result = probes.PROBES["ddospot"]("127.0.0.1", [Port(free_port(socket.SOCK_DGRAM), 123, "udp")], TOKEN)
        self.assertFalse(result.port_open)
        self.assertIn("refused", result.detail)

    def test_a_timed_out_connection_is_a_result(self):
        with mock.patch.object(probes.socket, "create_connection", side_effect=socket.timeout("timed out")):
            result = probes.PROBES["cowrie"]("192.0.2.1", [Port(22, 22), Port(23, 23)], TOKEN)
        self.assertFalse(result.port_open)
        self.assertFalse(result.answered)
        self.assertEqual(result.detail, "22/tcp: not open: timed out; 23/tcp: not open: timed out")

    def test_the_connect_time_and_the_address_of_a_port(self):
        seen = []

        def connect(address, timeout):
            seen.append((address, timeout))
            raise ConnectionRefusedError()

        with mock.patch.object(probes.socket, "create_connection", side_effect=connect):
            probes.PROBES["cowrie"]("192.0.2.1", [Port(2222, 22), Port(23, 23, "tcp", "192.0.2.10")], TOKEN)
        self.assertEqual([address for address, _ in seen], [("192.0.2.1", 2222), ("192.0.2.10", 23)])
        self.assertTrue(all(0 < timeout <= probes.CONNECT_TIMEOUT for _, timeout in seen))

    def test_a_silent_port_takes_only_its_own_time(self):
        # 23 of heralding stays silent; the ports after it still get their turn and answer
        silent = listening()
        self.addCleanup(silent.close)
        ftp = FakeTcp([b"220 FTP ready\r\n", b"530 no\r\n", b"221 Bye\r\n"])
        ssh = FakeTcp([b"SSH-2.0-x\r\n"])
        self.addCleanup(ftp.close)
        self.addCleanup(ssh.close)
        number = silent.getsockname()[1]
        started = time.monotonic()
        with mock.patch.object(probes, "PORT_TIME", 1.0):
            result = probes.PROBES["heralding"]("127.0.0.1", [Port(number, 23), Port(ftp.port, 21),
                                                              Port(ssh.port, 22)], TOKEN)
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(result.port_open, result)     # every port was tried and took the connection
        self.assertFalse(result.answered)             # the silent one did not answer
        self.assertEqual(result.not_reached, [])
        self.assertEqual(result.detail, f"{number}/tcp: no login: / username: prompt; "
                                        f"{ftp.port}/tcp: 220 FTP ready; {ssh.port}/tcp: SSH-2.0-x")

    def test_the_time_of_a_honeypot_and_ports_not_reached(self):
        silent = listening()
        self.addCleanup(silent.close)
        number = silent.getsockname()[1]
        ports = [Port(number, 22), Port(number, 22), Port(free_port(), 23)]
        self.assertEqual(min(probes.PROBE_TIME_MAX, probes.PORT_TIME * 3), 30)
        self.assertEqual(min(probes.PROBE_TIME_MAX, probes.PORT_TIME * 29), 60)
        with mock.patch.object(probes, "PORT_TIME", 0.5), mock.patch.object(probes, "PROBE_TIME_MAX", 0.8):
            result = probes.PROBES["cowrie"]("127.0.0.1", ports, TOKEN)
        # the third port is not reached: it does not count as closed (it would be refused)
        self.assertEqual(result.not_reached, [ports[2]])
        self.assertTrue(result.port_open, result)
        self.assertFalse(result.answered)
        self.assertTrue(result.detail.endswith(f"{ports[2].published}/tcp: not reached (the 0.8 s of the probe ran out)"),
                        result.detail)

    def test_ports_all_not_reached_are_never_closed(self):
        ports = [Port(free_port(), 22), Port(free_port(), 23)]   # both would be refused
        with mock.patch.object(probes, "PROBE_TIME_MAX", 0):
            result = probes.PROBES["cowrie"]("127.0.0.1", ports, TOKEN)
        self.assertEqual(result.state, "not_reached")
        self.assertFalse(result.port_open)
        self.assertEqual(result.not_reached, ports)
        self.assertNotIn("closed", [state for _, state in result.port_states])
        self.assertNotIn("not open", result.detail)

    def test_a_connect_cut_short_by_the_cap_is_not_reached(self):
        timeouts = []

        def slow(address, timeout):
            timeouts.append(timeout)
            raise socket.timeout("timed out")

        # 0.3 s left for the port, less than its 5 s connect time: not reached, not closed
        with mock.patch.object(probes, "PROBE_TIME_MAX", 0.3), \
                mock.patch.object(probes.socket, "create_connection", side_effect=slow):
            result = probes.PROBES["cowrie"]("192.0.2.1", [Port(22, 22)], TOKEN)
        self.assertLess(timeouts[0], probes.CONNECT_TIMEOUT)
        self.assertEqual(result.state, "not_reached")
        self.assertEqual(result.port_states, [(Port(22, 22), "not_reached")])
        self.assertEqual(result.detail, "22/tcp: not reached (cut short: timed out)")
        # the same time-out on the port's own full clock is closed; a refusal always is
        with mock.patch.object(probes.socket, "create_connection", side_effect=slow):
            self.assertEqual(probes.PROBES["cowrie"]("192.0.2.1", [Port(22, 22)], TOKEN).state, "closed")
        with mock.patch.object(probes, "PROBE_TIME_MAX", 0.3), \
                mock.patch.object(probes.socket, "create_connection", side_effect=ConnectionRefusedError()):
            self.assertEqual(probes.PROBES["cowrie"]("192.0.2.1", [Port(22, 22)], TOKEN).state, "closed")

    def test_the_state_of_a_result(self):
        self.assertTrue(probes.ProbeResult("open", True, "").port_open)
        for state in ("closed", "not_reached", "skipped"):
            self.assertFalse(probes.ProbeResult(state, False, "").port_open, state)
        with self.assertRaises(ValueError):
            probes.ProbeResult(True, True, "")
        with self.assertRaises(AttributeError):
            probes.ProbeResult("open", True, "").port_open = False

    def test_dicom_without_dcmtk(self):
        sock = listening()
        self.addCleanup(sock.close)
        with mock.patch.object(probes.shutil, "which", return_value=None):
            result = probes.PROBES["dicompot"]("127.0.0.1", [Port(sock.getsockname()[1], 11112)], TOKEN)
        self.assertTrue(result.port_open)
        self.assertFalse(result.answered)
        self.assertFalse(result.answer_checked)
        self.assertIn("DICOM not checked (DCMTK echoscu is not installed)", result.detail)

    def test_dicom_with_dcmtk(self):
        sock = listening()
        self.addCleanup(sock.close)
        number = sock.getsockname()[1]
        calls = []

        def echoscu(returncode, output=""):
            def run(argv, **kwargs):
                calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, returncode, output, None)
            return run

        with mock.patch.object(probes.shutil, "which", return_value="/usr/bin/echoscu"), \
                mock.patch.object(probes.subprocess, "run", side_effect=echoscu(0)):
            result = probes.PROBES["dicompot"]("127.0.0.1", [Port(number, 11112)], TOKEN)
        self.assertTrue(result.answered and result.answer_checked, result)
        self.assertEqual(result.detail, f"{number}/tcp: DICOM C-ECHO answer (echoscu)")
        argv, kwargs = calls[0]
        self.assertEqual(argv[0], "/usr/bin/echoscu")
        self.assertEqual(argv[-2:], ["127.0.0.1", str(number)])
        self.assertLessEqual(kwargs["timeout"], probes.PORT_TIME)
        with mock.patch.object(probes.shutil, "which", return_value="/usr/bin/echoscu"), \
                mock.patch.object(probes.subprocess, "run", side_effect=echoscu(1, "E: Association Request Failed\n")):
            result = probes.PROBES["dicompot"]("127.0.0.1", [Port(number, 11112)], TOKEN)
        self.assertTrue(result.port_open)
        self.assertFalse(result.answered)
        self.assertIn("no DICOM C-ECHO answer (E: Association Request Failed)", result.detail)
        with mock.patch.object(probes.shutil, "which", return_value="/usr/bin/echoscu"), \
                mock.patch.object(probes.subprocess, "run", side_effect=subprocess.TimeoutExpired("echoscu", 1)):
            result = probes.PROBES["dicompot"]("127.0.0.1", [Port(number, 11112)], TOKEN)
        self.assertIn("echoscu timed out", result.detail)

    def test_sentrypeer_in_p2p_mode_is_only_knocked_at(self):
        server = FakeTcp([None, b"SIP/2.0 200 OK\r\n\r\n"])
        self.addCleanup(server.close)
        udp = FakeUdp(answer=b"SIP/2.0 200 OK\r\n\r\n")
        honeypot = inventory.Honeypot("sentrypeer", "honeypots", "sentrypeer",
                                      [Port(server.port, 5060), Port(udp.port, 5060, "udp")], running=True,
                                      environment={"SENTRYPEER_PEER_TO_PEER": "1"})
        result = probes.probe(honeypot, "127.0.0.1", TOKEN)
        server.thread.join(5)
        udp.sock.close()
        udp.thread.join(5)
        self.assertEqual(server.received, b"")
        self.assertEqual(udp.received, b"")
        self.assertFalse(result.token_sent)
        self.assertTrue(result.port_open)
        self.assertEqual(result.state, "open")
        self.assertEqual(result.port_states, [(Port(server.port, 5060), "open"), (Port(udp.port, 5060, "udp"), "skipped")])
        # only the UDP port published: left alone, never closed
        only_udp = inventory.Honeypot("sentrypeer", "honeypots", "sentrypeer", [Port(udp.port, 5060, "udp")],
                                      running=True, environment={"SENTRYPEER_PEER_TO_PEER": "0"})
        result_udp = probes.probe(only_udp, "127.0.0.1", TOKEN)
        self.assertEqual(result_udp.state, "skipped")
        self.assertFalse(result_udp.port_open)
        reason = "P2P mode would share the probe's source with its peers"
        self.assertEqual(result.detail, f"{server.port}/tcp: port open, {reason}; "
                                        f"{udp.port}/udp: not probed, {reason}")
        # without P2P the SIP OPTIONS go out as the smoke test sends them
        server = FakeTcp([None, b"SIP/2.0 200 OK\r\n\r\n"])
        self.addCleanup(server.close)
        honeypot.ports, honeypot.environment = [Port(server.port, 5060)], {}
        self.assertTrue(probes.probe(honeypot, "127.0.0.1", TOKEN).answered)
        server.thread.join(5)
        self.assertTrue(server.received.startswith(b"OPTIONS sip:1000@127.0.0.1 SIP/2.0\r\n"))

    def test_a_port_only_port_next_to_a_checked_one(self):
        sock = listening()
        self.addCleanup(sock.close)
        server = FakeTcp([b"220 FTP ready\r\n", b"530 no\r\n", b"221 Bye\r\n"])
        self.addCleanup(server.close)
        result = probes.PROBES["honeypots"]("127.0.0.1", [Port(sock.getsockname()[1], 445), Port(server.port, 21)],
                                            TOKEN)
        self.assertTrue(result.port_open and result.answered and result.answer_checked, result)
        self.assertEqual(result.detail, f"{sock.getsockname()[1]}/tcp: port open (port only); "
                                        f"{server.port}/tcp: 220 FTP ready")

    @unittest.skipUnless(shutil.which("openssl"), "no openssl")
    def test_https(self):
        folder = tempfile.mkdtemp(prefix="tpot-probe-tls-")
        self.addCleanup(shutil.rmtree, folder)
        key, cert = os.path.join(folder, "key.pem"), os.path.join(folder, "cert.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                        "-days", "1", "-subj", "/CN=localhost"], capture_output=True, check=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server = FakeTcp([None, b"HTTP/1.1 200 OK\r\n\r\n"], tls=context)
        result = self.run_probe("h0neytr4p", server, 443)
        self.assertTrue(result.answered, result)
        self.assertIn(f"GET /.env?probe={TOKEN} HTTP/1.1".encode(), server.received)


class RunProbesTest(unittest.TestCase):

    def honeypot(self, name, running=True, external=False, ports=None):
        return inventory.Honeypot(name, "honeypots", name, [Port(1, 1)] if ports is None else ports,
                                  running=running, external=external)

    def test_every_honeypot_gets_a_result_and_external_ones_none(self):
        calls = []

        def fine(host, ports, token):
            calls.append((host, ports, token))
            return probes.ProbeResult("open", True, "ok")

        def broken(host, ports, token):
            raise RuntimeError("boom")

        found = probes.run_probes("192.0.2.5", [
            self.honeypot("a"), self.honeypot("b", running=False), self.honeypot("c", external=True),
            self.honeypot("d", ports=[]), self.honeypot("e"), self.honeypot("f")], TOKEN,
            probes={"a": fine, "b": fine, "d": fine, "e": broken})
        self.assertEqual(sorted(found), ["a", "b", "d", "e", "f"])
        self.assertEqual(calls, [("192.0.2.5", [Port(1, 1)], TOKEN)])
        self.assertTrue(found["a"].answered)
        self.assertEqual(found["b"].detail, "not running (container b)")
        self.assertEqual(found["d"].detail, "no published port")
        self.assertEqual(found["e"].detail, "probe failed: boom")
        self.assertEqual(found["f"].detail, "no probe for this honeypot")
        for name in "bdef":
            self.assertFalse(found[name].port_open, name)
            self.assertFalse(found[name].answer_checked, name)

    def test_the_budget(self):
        def slow(host, ports, token):
            time.sleep(0.5)
            return probes.ProbeResult("open", True, "ok")

        started = time.monotonic()
        found = probes.run_probes("127.0.0.1", [self.honeypot(str(n)) for n in range(4)], TOKEN, budget=0.2,
                                  workers=1, probes={str(n): slow for n in range(4)})
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(found["0"].answered)    # it ran: the call waits for it
        for name in "123":
            self.assertEqual(found[name].detail, "not probed: the time for the probes ran out")

    def test_no_probe_outlives_the_call(self):
        running = threading.Event()
        ended = threading.Event()

        def slow(host, ports, token):
            running.set()
            time.sleep(0.5)
            ended.set()
            return probes.ProbeResult("open", True, "ok")

        probes.run_probes("127.0.0.1", [self.honeypot("a")], TOKEN, budget=0.05, probes={"a": slow})
        self.assertTrue(running.is_set())
        self.assertTrue(ended.is_set())

    def test_a_hold_that_ended_stops_the_probes(self):
        state = {"calls": 0, "probed": 0}

        def check():
            state["calls"] += 1
            if state["calls"] > 1:
                raise HoldError("the hold of ewsposter ended")

        def fine(host, ports, token):
            state["probed"] += 1
            return probes.ProbeResult("open", True, "ok")

        into = {}
        with self.assertRaisesRegex(HoldError, "ended"):
            probes.run_probes("127.0.0.1", [self.honeypot(str(n)) for n in range(5)], TOKEN, workers=1,
                              probes={str(n): fine for n in range(5)}, check=check, into=into)
        self.assertEqual(state, {"calls": 2, "probed": 1})
        # what ran is known all the same: the cleanup names its events
        self.assertEqual(into["0"].state, "open")
        self.assertEqual(sorted(into), [str(n) for n in range(5)])

    def test_the_map_listeners_are_bounded_in_their_containers(self):
        started, ran = [], []

        class Proc:
            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

        folder = tempfile.mkdtemp(prefix="tpot-map-")
        self.addCleanup(shutil.rmtree, folder)
        listener = run_checks.MapListener(folder, 330, popen=lambda argv, **kw: started.append(argv) or Proc(),
                                          run=lambda argv, **kw: ran.append(argv))
        listener.start()
        self.assertEqual(started[0], ["docker", "exec", "map_redis", "timeout", "330", "redis-cli", "SUBSCRIBE",
                                      "attack-map-production"])
        self.assertEqual(started[1][:5], ["docker", "exec", "-e", "WS_SECONDS=330", "map_web"])
        self.assertIn(run_checks.WS_MARK, started[1][-1])
        listener.stop()
        self.assertEqual(ran, [["docker", "exec", "map_redis", "pkill", "-f", "redis-cli SUBSCRIBE attack-map-production"],
                               ["docker", "exec", "map_web", "pkill", "-f", run_checks.WS_MARK]])


# ---------------------------------------------------------------------------
# fixtures (tpotctl.checks.fixtures): one sample event per honeypot
# ---------------------------------------------------------------------------

FIXTURE_TS = datetime.datetime(2026, 10, 17, 8, 15, 42, 123456, tzinfo=datetime.timezone.utc)
FIXTURE_IP = "8.8.8.8"
FIXTURE_PORT = 51234
FIXTURE_SMOKE = os.path.join(REPO, "docker", "_tests", "tests", "logstash_fixtures.sh")


def render_fixture(name, ts=FIXTURE_TS, src_ip=FIXTURE_IP, src_port=FIXTURE_PORT, dst_port=8080, token=TOKEN):
    return fixtures.render(name, ts, src_ip, src_port, dst_port, token)


class FixturesTest(unittest.TestCase):

    def test_every_honeypot_with_a_logstash_input_has_a_fixture(self):
        self.assertEqual(fixtures.names(), sorted(inventory.LOGS))
        for name in inventory.LOGS:
            self.assertTrue(os.path.isfile(fixtures.fixture_path(name)), name)
            self.assertEqual(fixtures.logstash_type(name), inventory.LOGS[name][0])

    @unittest.skipUnless(yaml, "PyYAML is missing (use the venv)")
    def test_every_honeypot_of_the_catalogue_has_a_fixture(self):
        names = inventory.honeypot_names(inventory.catalogue_groups(REPO))
        self.assertEqual(sorted(set(names) - set(fixtures.names())), [])

    def test_every_fixture_renders_to_one_line(self):
        for name in fixtures.names():
            with self.subTest(name=name):
                line = render_fixture(name)
                self.assertNotIn("\n", line)
                self.assertIsNone(fixtures.PLACEHOLDER.search(line), line)
                self.assertNotIn("{token}", line)

    def test_every_fixture_carries_the_source_and_the_token_or_the_marker_port(self):
        for name in fixtures.names():
            with self.subTest(name=name):
                text = fixtures.template(name)
                self.assertIn("{src_ip}", text)
                self.assertTrue("{token}" in text or "{src_port}" in text, name)
                self.assertIn("{ts:", text)
                line = render_fixture(name)
                self.assertIn(FIXTURE_IP, line)
                self.assertTrue(TOKEN in line or str(FIXTURE_PORT) in line, name)

    def test_a_json_fixture_renders_valid_json(self):
        found = 0
        for name in fixtures.names():
            if not re.match(r'\{\s*"', fixtures.template(name)):
                continue
            found += 1
            with self.subTest(name=name):
                event = json.loads(render_fixture(name))
                self.assertIsInstance(event, dict)
                self.assertIn(FIXTURE_IP, json.dumps(event))
        self.assertEqual(found, len(fixtures.names()) - 2)   # endlessh (text) and heralding (csv)

    def test_every_json_fixture_is_named_json_or_log(self):
        for name in fixtures.names():
            ext = os.path.splitext(fixtures.fixture_path(name))[1]
            self.assertIn(ext, (".json", ".log", ".csv"), name)

    def test_the_time_is_written_in_the_format_of_the_honeypot(self):
        cases = {
            "cowrie": '"timestamp":"2026-10-17T08:15:42.123456+0000"',
            "dionaea": '"timestamp": "2026-10-17T08:15:42.123456"',
            "elasticpot": '"timestamp":"2026-10-17T08:15:42.123456+00:00Z"',
            "endlessh": "2026-10-17T08:15:42.123Z ACCEPT ",
            "heralding": "2026-10-17 08:15:42.123456,",
            "p0f": '"timestamp": "2026/10/17 08:15:42"',
            "sentrypeer": '"event_timestamp":"2026-10-17 08:15:42.123456000"',
            "go-pot": '"ts":1792224942.123456,',
            "citrixhoneypot": '"asctime": "2026-10-17 08:15:42,123"',
            "dicompot": '"time":"2026-10-17 08:15:42"',
            "ddospot": '"time":"2026-10-17 08:15:42.123456"',
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertIn(text, render_fixture(name))

    def test_a_time_counts_as_utc(self):
        naive = FIXTURE_TS.replace(tzinfo=None)
        other = FIXTURE_TS.astimezone(datetime.timezone(datetime.timedelta(hours=2)))
        self.assertEqual(render_fixture("cowrie", ts=naive), render_fixture("cowrie"))
        self.assertEqual(render_fixture("cowrie", ts=other), render_fixture("cowrie"))
        self.assertNotEqual(render_fixture("cowrie", ts=FIXTURE_TS + datetime.timedelta(seconds=1)),
                            render_fixture("cowrie"))

    def test_the_values_go_where_the_honeypot_logs_them(self):
        event = json.loads(render_fixture("cowrie", token="tpot-check-x1"))
        self.assertEqual((event["src_ip"], event["username"]), (FIXTURE_IP, "tpot-check-x1"))
        event = json.loads(render_fixture("honeytrap", src_port=51240, dst_port=2222))
        self.assertEqual(event["attack_connection"]["remote_ip"], FIXTURE_IP)
        self.assertEqual(event["attack_connection"]["remote_port"], 51240)
        self.assertEqual(event["attack_connection"]["local_port"], 2222)
        event = json.loads(render_fixture("sentrypeer", src_port=51240))
        self.assertEqual(event["source_ip"], f"{FIXTURE_IP}:51240")
        self.assertIn(TOKEN, event["sip_message"])

    def test_heralding_has_the_eleven_columns_of_the_fork(self):
        row = next(csv.reader(io.StringIO(render_fixture("heralding"))))
        self.assertEqual(len(row), 11)
        self.assertEqual((row[3], row[4], row[6], row[8]), (FIXTURE_IP, str(FIXTURE_PORT), "8080", TOKEN))

    def test_the_log_file_of_a_glob_input_is_one_of_its_files(self):
        for name in fixtures.names():
            with self.subTest(name=name):
                path = fixtures.log_file(name)
                self.assertFalse(any(char in path for char in "*?["), path)
                self.assertTrue(fnmatch.fnmatch(path, inventory.LOGS[name][1]), path)
        self.assertEqual(fixtures.log_file("ddospot"), "ddospot/log/dnspot.log")
        self.assertEqual(fixtures.log_file("honeypots"), "honeypots/log/http.log")

    def test_render_refuses_values_that_do_not_fit(self):
        for token in ("", "a b", 'a"b', "a,b", "a/b", "a\nb", "-a", "x" * 64):
            with self.subTest(token=token), self.assertRaises(ValueError):
                render_fixture("cowrie", token=token)
        for src_ip in ("", "8.8.8", "host.example"):
            with self.subTest(src_ip=src_ip), self.assertRaises(ValueError):
                render_fixture("cowrie", src_ip=src_ip)
        for value in (-1, 65536, "80", True, None):
            with self.subTest(port=value), self.assertRaises(ValueError):
                render_fixture("cowrie", src_port=value)
            with self.subTest(port=value), self.assertRaises(ValueError):
                render_fixture("cowrie", dst_port=value)
        with self.assertRaises(KeyError):
            fixtures.render("nopot", FIXTURE_TS, FIXTURE_IP, 1, 2, TOKEN)

    def test_the_fixtures_keep_to_harmless_text(self):
        for name in fixtures.names():
            text = fixtures.template(name).lower()
            for word in ("/dev/tcp", "pty.spawn", "/bin/sh", "jndi:", "wget ", "curl "):
                self.assertNotIn(word, text, name)

    def test_glutton_takes_the_time_of_its_slog_line_in_both_pipelines(self):
        blocks = []
        for conf in ("logstash.conf", "http_output.conf"):
            with open(os.path.join(os.path.dirname(LOGSTASH_CONF), conf), encoding="utf-8") as handle:
                text = handle.read()
            start = text.index('  if [type] == "Glutton" {')
            blocks.append(text[start:text.index("\n  }\n", start) + 4])
        self.assertEqual(blocks[0], blocks[1])
        self.assertIn('match => [ "time", "ISO8601" ]', blocks[0])
        self.assertIn('match => [ "ts", "UNIX" ]', blocks[0])
        self.assertIn('"time":"{ts:', fixtures.template("glutton"))

    def test_the_smoke_test_feeds_every_fixture_through_logstash(self):
        self.assertTrue(os.access(FIXTURE_SMOKE, os.X_OK))
        with open(FIXTURE_SMOKE, encoding="utf-8") as handle:
            text = handle.read()
        for needed in ("fixtures.names()", "fixtures.render(", "fixtures.log_file(", "logstash.conf",
                       "network_mode: none", 'endswith("failure")', 'COLUMN = re.compile(r"column\\d+")',
                       'event.get("src_ip") != want["src_ip"]', "start_position", "sincedb_path"):
            self.assertIn(needed, text)

    def test_an_injected_event_takes_an_ipv4_test_ip_only(self):
        for src_ip in ("2001:db8::1", "::ffff:8.8.8.8", "::1"):
            with self.subTest(src_ip=src_ip), self.assertRaisesRegex(ValueError, "IPv4"):
                render_fixture("cowrie", src_ip=src_ip)
        for src_ip in run_checks.TEST_IPS:
            self.assertIn(src_ip, render_fixture("dionaea", src_ip=src_ip))


# ---------------------------------------------------------------------------
# stages, cleanup, result (tpotctl.checks.stages, tpotctl.checks.run)
# ---------------------------------------------------------------------------

from tpotctl.checks import logfile, run as run_checks, stages  # noqa: E402

HOST = "192.0.2.5"
OWN = [HOST, "127.0.0.1", "::1", "172.17.0.1"]
BASE = datetime.datetime(2026, 10, 17, 8, 0, 0, tzinfo=datetime.timezone.utc)
ATTACKER = "203.0.113.9"


def es_field(source, name):
    name = name[:-len(".keyword")] if name.endswith(".keyword") else name
    if name in source:
        return source[name]
    value = source
    for part in name.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def es_match(query, source):
    """The part of the Elasticsearch query DSL the checks use, over one document."""
    if "bool" in query:
        clauses = query["bool"]
        if not all(es_match(q, source) for q in clauses.get("filter", []) + clauses.get("must", [])):
            return False
        if any(es_match(q, source) for q in clauses.get("must_not", [])):
            return False
        should = clauses.get("should", [])
        need = clauses.get("minimum_should_match", 0 if clauses.get("filter") or clauses.get("must") else 1)
        return sum(1 for q in should if es_match(q, source)) >= need
    if "term" in query:
        (name, value), = query["term"].items()
        return es_field(source, name) == value
    if "terms" in query:
        (name, values), = query["terms"].items()
        return es_field(source, name) in values
    if "range" in query:
        (name, window), = query["range"].items()
        value = es_field(source, name)
        return value is not None and window.get("gte", "") <= value <= window.get("lte", "9999")
    if "query_string" in query:
        return query["query_string"]["query"].strip('"') in json.dumps(source)
    raise AssertionError(f"query the fake does not know: {query}")


REFUSED = ('{"error":{"root_cause":[{"type":"query_shard_exception","reason":"failed to create query: '
           'field expansion matches too many fields, limit: 1024"}]},"status":400}')


class FakeElastic:
    def __init__(self, order=None):
        self.docs, self.searches, self.deletes = [], [], []
        self.refuse_token = False
        self.refuse_other = False
        self.down = False
        self.attempts = 0
        self.order = order if order is not None else []
        self.after_delete = None        # called after each pass: a late document

    def add(self, source):
        self.docs.append({"_id": f"doc{len(self.docs)}", "_index": "logstash-2026.10.17", "_source": source})

    def search(self, body):
        self.attempts += 1
        if self.down:
            raise run_checks.ElasticError("Elasticsearch is not reachable (refused)")
        self.refused(body)
        self.searches.append(body)
        hits = sorted((d for d in self.docs if es_match(body["query"], d["_source"])),
                      key=lambda d: d["_source"].get("@timestamp", ""))
        return {"hits": {"hits": hits[:body.get("size", 10)]}}

    def refused(self, body):
        if "query_string" in json.dumps(body):
            if self.refuse_token:
                raise run_checks.ElasticError("Elasticsearch answered 400", 400, REFUSED)
            if self.refuse_other:
                raise run_checks.ElasticError("Elasticsearch answered 400", 400,
                                              '{"error":{"type":"illegal_argument_exception"},"status":400}')

    def refresh(self):
        pass

    def count(self, body):
        return sum(1 for d in self.docs if es_match(body["query"], d["_source"]))

    def delete_by_query(self, body):
        self.refused(body)
        self.order.append("delete")
        self.deletes.append(body)
        keep = [d for d in self.docs if not es_match(body["query"], d["_source"])]
        deleted, self.docs = len(self.docs) - len(keep), keep
        if self.after_delete:
            self.after_delete(self)
        return deleted


class FakeFiles:
    def __init__(self, logs):
        self.logs = {rel: bytearray(text) for rel, text in logs.items()}
        self.appended = []
        self.on_append = None

    def size(self, rel):
        return len(self.logs[rel]) if rel in self.logs else None

    def read_from(self, rel, offset):
        data = bytes(self.logs[rel])
        return data[offset:] if len(data) >= offset else data

    def append(self, rel, text):
        if rel not in self.logs:
            raise CheckError(f"{rel} does not exist")
        self.logs[rel] += text.encode()
        self.appended.append((rel, text))
        if self.on_append:
            self.on_append(rel, text)

    def listing(self, rel_dir, pattern):
        return sorted(rel for rel in self.logs if os.path.dirname(rel) == rel_dir
                      and fnmatch.fnmatch(os.path.basename(rel), pattern))


class FakeHold:
    def __init__(self, world, enter=None, lose_after=None, acked=True, note=""):
        self.world, self.enter_error, self.lose_after = world, enter, lose_after
        self.acked, self.note, self.checks = acked, note, 0

    def __enter__(self):
        self.world.order.append("hold")
        if self.enter_error:
            raise self.enter_error
        return self

    def __exit__(self, *exc):
        self.world.order.append("release")
        return False

    def check(self):
        self.checks += 1
        if self.lose_after is not None and self.checks > self.lose_after:
            raise HoldError(ewshold.MARKER_GONE)


class FakeListener:
    def __init__(self, world):
        self.world, self.error, self.started, self.stopped = world, "", False, False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def lines(self, which):
        if which in self.world.map_missing:
            return []
        return [json.dumps({"src_ip": d["_source"]["src_ip"], "src_port": d["_source"]["src_port"]})
                for d in self.world.es.docs if d["_source"]["src_ip"] in stages.TEST_IPS]


def honeypot(name, ports=None, running=True, group="honeypots"):
    return inventory.Honeypot(name, "nsm" if name in ("p0f", "suricata") else group, name,
                              [Port(22, 22)] if ports is None else ports, running,
                              name in inventory.EXTERNAL, inventory.LOGS[name][0],
                              "/data/" + inventory.LOGS[name][1])


class World:
    """A T-Pot as far as a check sees it: logs, Elasticsearch with a Logstash that indexes what the
    honeypots and the injection write, the Attack Map, a hold, and a clock that runs on each sleep."""

    def __init__(self, test, honeypots, tpot_type="HIVE", hold=None, results=None, probe_logs=True,
                 probe_docs=True, map_missing=(), sleep_hook=None, host=None, **options):
        self.test, self.honeypots = test, honeypots
        self.clock, self.order, self.said, self.marks, self.sleeps = 0.0, [], [], [], []
        self.es, self.map_missing = FakeElastic(self.order), set(map_missing)
        self.results, self.probe_logs, self.probe_docs = results or {}, probe_logs, probe_docs
        self.sleep_hook = sleep_hook
        logs = {fixtures.log_file(h.name): b"an older line\n" for h in honeypots}
        self.files = FakeFiles(logs)
        self.files.on_append = self.logstash
        self.hold = hold or FakeHold(self)
        self.listener = None
        folder = tempfile.mkdtemp(prefix="tpot-check-run-")
        test.addCleanup(shutil.rmtree, folder)
        self.result_file = os.path.join(folder, "checks", "last.json")
        # a real attack in the window of the check: it must survive the cleanup
        self.es.add({"type": "Cowrie", "src_ip": ATTACKER, "src_port": 40001, "@timestamp": stages.iso(BASE)})
        self.host = host or HOST
        self.check = run_checks.Check(
            run_checks.Options(**options), folder,
            env={"TPOT_TYPE": tpot_type, "TPOT_DATA_PATH": os.path.join(folder, "data")},
            honeypots=lambda: list(self.honeypots),
            compose_names=["tpotinit", "ewsposter", "elasticsearch", "map_redis", "map_web"] + [h.name for h in honeypots],
            hold=lambda data, names: self.hold, run_probes=self.run_probes, elastic=self.es, files=self.files,
            listener=self.make_listener, addresses=lambda address: list(OWN), run_id="abc123",
            log=lambda kind, text: self.said.append((kind, text)), mark=self.marks.append,
            clock=lambda: self.clock, sleep=self.sleep, now=self.now, web_port=64297,
            result_file=self.result_file, host=lambda: self.host)

    def now(self):
        return BASE + datetime.timedelta(seconds=self.clock)

    def sleep(self, seconds):
        self.clock += seconds
        self.sleeps.append(seconds)
        if self.sleep_hook:
            self.sleep_hook(self)

    def make_listener(self, folder, seconds):
        shutil.rmtree(folder, ignore_errors=True)
        self.listener = FakeListener(self)
        return self.listener

    def run_probes(self, host, honeypots, token, check=None, into=None):
        self.order.append("probes")
        self.test.assertEqual(host, self.host)
        results = into if into is not None else {}
        for index, target in enumerate(honeypots):
            if target.external:
                continue
            check()
            if not target.running:
                results[target.name] = probes.ProbeResult("skipped", False, "not running", answer_checked=False)
                continue
            port = 40100 + index
            result = self.results.get(target.name) or probes.ProbeResult(
                "open", True, "22/tcp: answered", token_sent=True, src_ports=[port])
            results[target.name] = result
            line = {"src_ip": host, "src_port": port, "user": token if result.token_sent else "-"}
            if self.probe_logs and fixtures.log_file(target.name) in self.files.logs:
                self.files.logs[fixtures.log_file(target.name)] += (json.dumps(line) + "\n").encode()
            if self.probe_docs:
                self.es.add(dict(line, type=target.logstash_type, **{"@timestamp": stages.iso(self.now())}))
        return results

    def logstash(self, rel, text):
        self.order.append("inject")
        for row in self.check.rows.values():
            if row.geo is not None and row.geo.file == rel:
                self.es.add({"type": row.logstash_type, "src_ip": row.geo.src_ip, "src_port": row.geo.src_port,
                             "geoip": {"latitude": 37.751, "longitude": -97.822, "country_name": "United States"},
                             "tags": [], "@timestamp": stages.iso(self.now())})

    def run(self):
        report = self.check.run()
        self.order.append(f"exit {report.exit_code}")
        return report

    def last(self):
        with open(self.result_file, encoding="utf-8") as handle:
            return json.load(handle)


class StagesTest(unittest.TestCase):

    def result(self, **fields):
        fields.setdefault("token_sent", False)
        return probes.ProbeResult("open", True, "answered", **fields)

    def test_the_port_and_the_answer(self):
        cowrie = honeypot("cowrie")
        cases = [(probes.ProbeResult("open", True, "ok"), stages.OK, stages.OK),
                 (probes.ProbeResult("open", False, "no banner"), stages.OK, stages.FAILED),
                 (probes.ProbeResult("open", False, "port only", answer_checked=False), stages.OK,
                  stages.NOT_APPLICABLE),
                 (probes.ProbeResult("closed", False, "refused"), stages.FAILED, stages.NOT_REACHED),
                 (probes.ProbeResult("not_reached", False, "time ran out"), stages.NOT_REACHED, stages.NOT_REACHED),
                 (probes.ProbeResult("skipped", False, "no published port", answer_checked=False),
                  stages.NOT_APPLICABLE, stages.NOT_APPLICABLE),
                 (probes.ProbeResult("skipped", False, "probe failed: boom", answer_checked=False), stages.FAILED,
                  stages.NOT_REACHED)]
        for result, port, answer in cases:
            with self.subTest(detail=result.detail):
                stage = stages.port_stage(cowrie, result)
                self.assertEqual((stage.state, stages.answer_stage(result, stage).state), (port, answer))
        self.assertEqual(stages.port_stage(honeypot("cowrie", running=False), None).state, stages.FAILED)
        self.assertEqual(stages.port_stage(honeypot("honeytrap"), None).detail, inventory.EXTERNAL_HINT)

    def test_a_log_line_by_the_token_by_address_and_port_or_by_the_time_window(self):
        token = TOKEN
        lines = ['{"src_ip": "198.51.100.1", "src_port": 40100, "user": "x"}',
                 '{"src_ip": "192.0.2.55", "src_port": 40100}',
                 '{"src_ip": "::ffff:192.0.2.5", "src_port": 40100}']
        self.assertEqual(stages.match_line("dionaea", self.result(src_ports=[40100]), lines, token, OWN), lines[2])
        self.assertEqual(stages.match_line("dionaea", self.result(src_ports=[40101]), lines, token, OWN), "")
        with_token = lines + [f'{{"user": "{token}"}}']
        self.assertEqual(stages.match_line("cowrie", self.result(token_sent=True), with_token, token, OWN),
                         with_token[-1])
        # the injected geo event carries the token too: it never counts as the probe's line
        self.assertEqual(stages.match_line("cowrie", self.result(token_sent=True), with_token, token, OWN,
                                           skip=[with_token[-1]]), "")
        self.assertEqual(stages.match_line("dicompot", self.result(src_ports=[]), ["", "Connection from x"],
                                           token, OWN), "Connection from x")

    def test_geoip(self):
        self.assertEqual(stages.geo_stage(None, 90).state, stages.FAILED)
        failed = {"_source": {"tags": ["_geoip_lookup_failure"], "geoip": {}}}
        self.assertIn("_geoip_lookup_failure", stages.geo_stage(failed, 90).detail)
        self.assertEqual(stages.geo_stage({"_source": {"geoip": {"longitude": 1}}}, 90).state, stages.FAILED)
        found = {"_source": {"geoip": {"latitude": 37.751, "longitude": -97.822, "country_name": "United States"},
                             "ip_rep": "known attacker"}}
        stage = stages.geo_stage(found, 90)
        self.assertEqual(stage.state, stages.OK)
        self.assertIn("United States", stage.detail)
        self.assertEqual(stages.location(found), {"lat": 37.751, "lon": -97.822, "country": "United States",
                                                  "ip_rep": "known attacker"})

    def test_the_attack_map_by_ip_and_port(self):
        lines = ['message', 'attack-map-production', '{"src_ip": "8.8.8.8", "src_port": 51234, "dst_port": 22}']
        self.assertTrue(stages.seen(lines, "8.8.8.8", 51234))
        self.assertFalse(stages.seen(lines, "8.8.8.8", 5123))
        self.assertFalse(stages.seen(lines, "8.8.4.4", 51234))
        self.assertFalse(stages.on_the_map(honeypot("p0f")))
        self.assertTrue(stages.on_the_map(honeypot("cowrie")))

    def own(self):
        return stages.OwnEvents(TOKEN, HOST, probes={"Cowrie": [40100, 40101], "ConPot": [40200]},
                                probed=["ConPot", "Cowrie", "P0f"], token_types=["Cowrie"],
                                geos=[("Cowrie", "8.8.8.8", 51234), ("Dionaea", "208.67.222.222", 51235)])

    def test_the_cleanup_query(self):
        end = BASE + datetime.timedelta(minutes=4, seconds=3, microseconds=500000)
        start = BASE + datetime.timedelta(microseconds=750000)

        def kind(name):
            return {"term": {"type.keyword": name}}

        self.assertEqual(stages.cleanup_query(start, end, self.own()), {"query": {"bool": {
            "filter": [{"range": {"@timestamp": {"gte": "2026-10-17T08:00:00.000Z",
                                                 "lte": "2026-10-17T08:05:03.500Z"}}},
                       {"terms": {"type.keyword": ["ConPot", "Cowrie", "Dionaea"]}}],
            "should": [
                {"bool": {"filter": [{"terms": {"type.keyword": ["Cowrie"]}},
                                     {"query_string": {"query": f'"{TOKEN}"', "lenient": True}}]}},
                {"bool": {"filter": [kind("Cowrie"), {"term": {"src_ip.keyword": "8.8.8.8"}},
                                     {"term": {"src_port": 51234}}]}},
                {"bool": {"filter": [kind("Dionaea"), {"term": {"src_ip.keyword": "208.67.222.222"}},
                                     {"term": {"src_port": 51235}}]}},
                {"bool": {"filter": [kind("ConPot"), {"term": {"src_ip.keyword": HOST}},
                                     {"terms": {"src_port": [40200]}}]}},
                {"bool": {"filter": [kind("Cowrie"), {"term": {"src_ip.keyword": HOST}},
                                     {"terms": {"src_port": [40100, 40101]}}]}}],
            "minimum_should_match": 1}}})
        body = json.dumps(stages.cleanup_query(start, end, self.own(), with_token=False))
        self.assertNotIn("query_string", body)
        # never by an own address alone, never the passive tools
        for gone in ("172.17.0.1", "127.0.0.1", '"P0f"', "Suricata"):
            self.assertNotIn(gone, json.dumps(stages.cleanup_query(start, end, self.own())))
        self.assertIsNone(stages.cleanup_query(start, end, stages.OwnEvents(TOKEN)))
        # ports that do not survive (docker-proxy): no source clause at all
        own = self.own()
        own.source = ""
        self.assertNotIn(HOST, json.dumps(stages.cleanup_query(start, end, own)))

    def test_the_cleanup_leaves_every_real_event_alone(self):
        es = FakeElastic()
        inside, after = stages.iso(BASE + datetime.timedelta(minutes=1)), stages.iso(BASE + datetime.timedelta(hours=1))
        real = [
            {"type": "Cowrie", "src_ip": ATTACKER, "src_port": 40100, "@timestamp": inside},
            {"type": "Cowrie", "src_ip": "172.20.0.1", "src_port": 40100, "@timestamp": inside},   # IPv6 attack
            {"type": "Cowrie", "src_ip": HOST, "src_port": 50999, "@timestamp": inside},          # another port
            {"type": "Cowrie", "src_ip": "8.8.8.8", "src_port": 40100, "@timestamp": inside},     # test IP, other port
            {"type": "Cowrie", "src_ip": HOST, "src_port": 40100, "@timestamp": after},           # later
            {"type": "Suricata", "src_ip": "8.8.8.8", "src_port": 51234, "event_type": "dns", "@timestamp": inside},
            {"type": "Suricata", "src_ip": HOST, "src_port": 40100, "@timestamp": inside},
            {"type": "P0f", "src_ip": HOST, "src_port": 40100, "user": TOKEN, "@timestamp": inside},
            {"type": "Heralding", "src_ip": HOST, "src_port": 40100, "@timestamp": inside},       # not probed
            {"type": "Dionaea", "src_ip": "8.8.8.8", "src_port": 51235, "@timestamp": inside},    # other IP's port
        ]
        own = [
            {"type": "Cowrie", "src_ip": "10.0.0.9", "user": f"x {TOKEN}", "@timestamp": inside},       # (a)
            {"type": "Cowrie", "src_ip": "8.8.8.8", "src_port": 51234, "@timestamp": inside},           # (b)
            {"type": "Dionaea", "src_ip": "208.67.222.222", "src_port": 51235, "@timestamp": inside},   # (b)
            {"type": "Cowrie", "src_ip": HOST, "src_port": 40101, "@timestamp": inside},                # (c)
            {"type": "ConPot", "src_ip": HOST, "src_port": 40200, "@timestamp": inside},                # (c)
        ]
        for source in real + own:
            es.add(source)
        body = stages.cleanup_query(BASE, BASE + datetime.timedelta(minutes=5), self.own())
        self.assertEqual(es.delete_by_query(body), len(own))
        self.assertEqual([d["_source"] for d in es.docs], real)
        # what the probes sent from this host and none of (a) to (c) names: counted, not deleted
        left = stages.leftover_query(BASE, BASE + datetime.timedelta(minutes=5), self.own(), HOST)
        self.assertEqual(es.count(left), 1)
        self.assertEqual(stages.leftover_kql(self.own(), HOST), 'src_ip:"192.0.2.5" and type:("ConPot" or "Cowrie")')

    def test_the_kibana_link(self):
        query = stages.run_kql(TOKEN, [HOST, "8.8.8.8"])
        link = stages.kibana_link(HOST, 64297, query, BASE, BASE + datetime.timedelta(minutes=2))
        self.assertTrue(link.startswith("https://192.0.2.5:64297/kibana/app/discover#/?_g=(time:(from:"), link)
        self.assertIn("2026-10-17T08:00:00.000Z", link)
        self.assertIn("2026-10-17T08:03:00.000Z", link)
        self.assertIn(TOKEN, link)
        self.assertNotIn(" ", link)
        self.assertTrue(stages.kibana_link("2001:db8::5", 64297, query, BASE, BASE).startswith("https://[2001:db8::5]:"))

    def test_only_the_refusal_of_the_token_query_drops_it(self):
        self.assertTrue(run_checks.ElasticError("x", 400, REFUSED).token_refused)
        for status, body in ((400, '{"error":{"type":"illegal_argument_exception"}}'), (500, REFUSED), (0, "")):
            with self.subTest(status=status):
                self.assertFalse(run_checks.ElasticError("x", status, body).token_refused)


class RunTest(unittest.TestCase):

    HONEYPOTS = ("cowrie", "dionaea", "honeytrap", "p0f")

    def world(self, names=HONEYPOTS, **options):
        return World(self, [honeypot(name) for name in names], **options)

    def test_every_honeypot_from_the_port_to_the_attack_map(self):
        world = self.world()
        report = world.run()
        self.assertEqual(report.exit_code, 0, [(r.name, r.stages) for r in report.rows])
        rows = {row.name: row for row in report.rows}
        self.assertEqual({name: rows["cowrie"].state(name) for name in stages.STAGES},
                         {name: stages.OK for name in stages.STAGES})
        self.assertIn(TOKEN, rows["cowrie"].log_line)
        self.assertTrue(rows["cowrie"].es_id)
        # the n-th honeypot: the test IPs in turn, marker port 51234 + n
        self.assertEqual((rows["cowrie"].geo.src_ip, rows["cowrie"].geo.src_port), ("8.8.8.8", 51234))
        self.assertEqual((rows["dionaea"].geo.src_ip, rows["dionaea"].geo.src_port), ("208.67.222.222", 51235))
        self.assertEqual(rows["cowrie"].geo.location["country"], "United States")
        # honeytrap only sees traffic from outside: no probe, the geo event all the same
        self.assertEqual([rows["honeytrap"].state(name) for name in stages.STAGES],
                         [stages.NOT_APPLICABLE] * 4 + [stages.OK, stages.OK, stages.OK])
        # a passive tool writes its log without a pause: no test event in it
        self.assertEqual([rows["p0f"].state(name) for name in stages.STAGES[:6]], [stages.NOT_APPLICABLE] * 6)
        self.assertIsNone(rows["p0f"].geo)
        # the order: the hold, the probes, the geo events, the cleanup, the end of the hold
        self.assertEqual(world.order[:2], ["hold", "probes"])
        self.assertEqual(world.order[-4:], ["release", "delete", "delete", "exit 0"])
        self.assertLess(world.order.index("probes"), world.order.index("inject"))
        self.assertEqual(len(world.es.deletes), 2)
        self.assertEqual(report.cleanup["state"], "done")
        self.assertEqual(report.cleanup["passes"], [5, 0])
        self.assertEqual(report.cleanup["left"], 0)
        # the probes (from this host) and the geo events (test IPs) are gone, the real attack stays
        self.assertEqual([d["_source"]["src_ip"] for d in world.es.docs], [ATTACKER])
        self.assertEqual(report.cleanup["deleted"], 2 + 3)
        self.assertTrue(world.listener.started and world.listener.stopped)
        self.assertIn("@@tpot phase hold Holding ewsposter", world.marks)
        self.assertIn("@@tpot phase cleanup Removing the test events from Elasticsearch", world.marks)
        # every injected line is a fixture with a test IP, in the honeypot's own log
        for rel, text in world.files.appended:
            self.assertTrue(text.endswith("\n") and text.count("\n") == 1)
            self.assertTrue(any(ip in text for ip in stages.TEST_IPS), text)
            self.assertNotIn(HOST, text)

    def test_last_json(self):
        world = self.world(("cowrie",))
        world.run()
        last = world.last()
        self.assertEqual(oct(os.stat(world.result_file).st_mode & 0o777), "0o600")
        for key in ("version", "run_id", "token", "mode", "started", "ended", "host", "tpot_type", "options",
                    "exit_code", "result", "error", "ewsposter", "cleanup", "kibana", "stages", "titles",
                    "honeypots"):
            self.assertIn(key, last)
        self.assertEqual((last["version"], last["run_id"], last["token"], last["mode"], last["result"]),
                         (1, "abc123", TOKEN, "honeypots", "ok"))
        self.assertEqual(last["stages"], list(stages.STAGES))
        self.assertEqual(last["started"], "2026-10-17T08:00:00.000Z")
        cowrie = last["honeypots"][0]
        self.assertEqual(cowrie["name"], "cowrie")
        self.assertEqual(set(cowrie["stages"]), set(stages.STAGES))
        self.assertEqual(cowrie["stages"]["port"], {"state": "ok", "detail": "22/tcp: answered"})
        self.assertTrue(cowrie["es_id"])
        self.assertEqual(cowrie["geo"]["src_ip"], "8.8.8.8")
        self.assertIn(TOKEN, cowrie["geo"]["line"])
        self.assertEqual(last["ewsposter"]["state"], "held")
        self.assertIn(TOKEN, last["kibana"])
        self.assertIn("2026-10-17T08:00:00.000Z", last["kibana"])

    def test_keep_leaves_the_events(self):
        world = self.world(("cowrie",), keep=True)
        report = world.run()
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(world.es.deletes, [])
        self.assertEqual(report.cleanup["state"], "kept")
        self.assertEqual(len(world.es.docs), 3)

    def test_the_cleanup_body_is_the_one_of_the_run(self):
        world = self.world(("cowrie",))
        report = world.run()
        own = stages.OwnEvents(TOKEN, HOST, probes={"Cowrie": [40100]}, probed=["Cowrie"], token_types=["Cowrie"],
                               geos=[("Cowrie", "8.8.8.8", 51234)])
        first = report.ended
        second = first + datetime.timedelta(seconds=run_checks.SECOND_PASS)
        self.assertEqual(world.es.deletes, [stages.cleanup_query(report.started, first, own),
                                            stages.cleanup_query(report.started, second, own)])
        window = world.es.deletes[1]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
        self.assertEqual(window["gte"], "2026-10-17T08:00:00.000Z")
        self.assertEqual(window["lte"], stages.iso(second + datetime.timedelta(minutes=1)))

    def test_a_sensor_runs_the_first_three_stages(self):
        world = self.world(("cowrie", "honeytrap"), tpot_type="SENSOR")
        report = world.run()
        self.assertEqual(report.exit_code, 0)
        cowrie = {row.name: row for row in report.rows}["cowrie"]
        self.assertEqual([cowrie.state(name) for name in stages.SENSOR_STAGES], [stages.OK] * 3)
        for name in stages.STAGES[3:]:
            self.assertEqual((cowrie.state(name), cowrie.stages[name].detail), (stages.NOT_APPLICABLE,
                                                                                 stages.ON_THE_HIVE))
        self.assertEqual((world.es.searches, world.es.deletes, world.files.appended), ([], [], []))
        self.assertIsNone(world.listener)
        self.assertEqual(report.cleanup["state"], "skipped")
        self.assertEqual(world.last()["tpot_type"], "SENSOR")

    def test_a_hold_that_is_not_confirmed_probes_nothing(self):
        world = self.world(("cowrie",))
        world.hold = FakeHold(world, enter=HoldError("ewsposter did not confirm the hold within 180 s"))
        report = world.run()
        self.assertEqual(report.exit_code, 3)
        self.assertEqual(world.order, ["hold", "exit 3"])
        self.assertEqual((world.files.appended, world.es.deletes), ([], []))
        self.assertIn("@@tpot fail hold", world.marks)
        self.assertEqual(world.last()["result"], "hold failed")
        world = self.world(("cowrie",))
        world.hold = FakeHold(world, enter=CheckError("another check is running"))
        self.assertEqual(world.run().exit_code, 3)
        self.assertNotIn("probes", world.order)

    def test_a_stage_that_fails_is_exit_1(self):
        world = self.world(("cowrie", "dionaea"), probe_docs=False, map_missing=("ws",))
        report = world.run()
        self.assertEqual(report.exit_code, 1)
        rows = {row.name: row for row in report.rows}
        self.assertEqual(rows["cowrie"].state("elasticsearch"), stages.FAILED)
        self.assertIn("within 90 s", rows["cowrie"].stages["elasticsearch"].detail)
        self.assertEqual(rows["cowrie"].state("attack_map"), stages.FAILED)
        self.assertEqual(rows["cowrie"].stages["attack_map"].detail, "on Redis, not on the WebSocket")
        self.assertIn("@@tpot fail stages", world.marks)
        self.assertTrue(any(mark.startswith("@@tpot warn stages cowrie ") for mark in world.marks))
        self.assertEqual(world.last()["result"], "failed")

    def test_the_geo_line_is_not_taken_for_the_probe_line(self):
        world = self.world(("cowrie",), probe_logs=False)
        report = world.run()
        cowrie = report.rows[0]
        self.assertEqual(cowrie.state("log"), stages.FAILED)
        self.assertEqual(cowrie.log_line, "")

    def test_ctrl_c_cleans_up_and_ends_the_hold(self):
        world = self.world(("cowrie",))

        def interrupted(host, honeypots, token, check=None, into=None):
            world.order.append("probes")
            world.es.add({"type": "Cowrie", "src_ip": HOST, "src_port": 40100, "@timestamp": stages.iso(BASE)})
            raise KeyboardInterrupt

        world.check._run_probes = interrupted
        report = world.run()
        self.assertEqual(report.exit_code, 130)
        self.assertEqual(world.order, ["hold", "probes", "release", "exit 130"])
        # the probes ended without results: nothing names their events, they stay (no delete by address)
        self.assertEqual(world.es.deletes, [])
        self.assertEqual([d["_source"]["src_ip"] for d in world.es.docs], [ATTACKER, HOST])
        self.assertEqual(world.last()["result"], "cancelled")

    @unittest.skipUnless(hasattr(signal, "SIGTERM") and hasattr(signal, "SIGHUP"), "no SIGTERM / SIGHUP")
    def test_term_and_hup_end_the_hold_and_the_handlers_come_back(self):
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum):
                before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP),
                          signal.getsignal(signal.SIGINT))
                world = self.world(("cowrie",))
                probe = world.run_probes

                def signalled(*args, s=signum, **kwargs):
                    results = probe(*args, **kwargs)
                    os.kill(os.getpid(), s)
                    time.sleep(0.5)
                    return results

                world.check._run_probes = signalled
                report = world.run()
                self.assertEqual(report.exit_code, 130)        # every cancel, as tpot does elsewhere
                self.assertEqual(world.order[-4:], ["release", "delete", "delete", "exit 130"])
                self.assertEqual((signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP),
                                  signal.getsignal(signal.SIGINT)), before)

    def test_a_signal_during_the_end_does_not_cut_it_short(self):
        world = self.world(("cowrie",))
        delete = world.es.delete_by_query

        def deleting(body):
            os.kill(os.getpid(), signal.SIGTERM)
            return delete(body)

        world.es.delete_by_query = deleting
        report = world.run()
        self.assertEqual(world.order[-4:], ["release", "delete", "delete", "exit 130"])
        self.assertEqual(report.cleanup["state"], "done")

    def test_a_hold_lost_during_the_check_stops_it(self):
        world = self.world(("cowrie", "dionaea"))
        world.hold = FakeHold(world, lose_after=1)
        report = world.run()
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.ewsposter["state"], "failed")
        for row in report.rows:
            self.assertEqual(row.state("ewsposter"), stages.FAILED)
        self.assertEqual(world.order[-4:], ["release", "delete", "delete", "exit 1"])

    def test_ewsposter_that_does_not_run_here(self):
        world = self.world(("cowrie",))
        world.hold = FakeHold(world, acked=False, note=ewshold.NOT_HERE)
        report = world.run()
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.rows[0].state("ewsposter"), stages.NOT_APPLICABLE)
        self.assertEqual(report.ewsposter["state"], "not_here")

    def test_the_pipeline_has_no_probes(self):
        world = self.world(("cowrie", "honeytrap"), geo_only=True)
        report = world.run()
        self.assertEqual(report.exit_code, 0)
        self.assertNotIn("probes", world.order)
        for row in report.rows:
            self.assertEqual([row.state(name) for name in stages.STAGES[:4]], [stages.NOT_APPLICABLE] * 4)
            self.assertEqual(row.state("geoip"), stages.OK)
            self.assertEqual(row.state("attack_map"), stages.OK)
        self.assertEqual(world.last()["mode"], "pipeline")

    def test_no_geo_injects_nothing(self):
        world = self.world(("cowrie",), geo=False)
        report = world.run()
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(world.files.appended, [])
        self.assertEqual((report.rows[0].state("geoip"), report.rows[0].state("attack_map")),
                         (stages.NOT_APPLICABLE, stages.NOT_APPLICABLE))

    def test_only(self):
        world = self.world(("cowrie", "dionaea"), only=["dionaea"])
        report = world.run()
        self.assertEqual([row.name for row in report.rows], ["dionaea"])
        world = self.world(("cowrie",), only=["nopot"])
        with self.assertRaises(run_checks.UsageError):
            world.run()

    def test_a_honeypot_that_does_not_run(self):
        world = self.world(("cowrie",))
        world.honeypots = [honeypot("cowrie", running=False)]
        report = world.run()
        self.assertEqual(report.exit_code, 1)
        row = report.rows[0]
        self.assertEqual(row.stages["port"].detail, "not running (container cowrie)")
        self.assertEqual(row.state("geoip"), stages.NOT_REACHED)
        self.assertEqual(world.files.appended, [])

    def test_a_knock_may_leave_no_trace(self):
        knock = probes.ProbeResult("open", False, "22/tcp: open (only knocked)", answer_checked=False,
                                   src_ports=[40100])
        world = self.world(("beelzebub",), results={"beelzebub": knock}, probe_logs=False, probe_docs=False)
        report = world.run()
        self.assertEqual(report.exit_code, 0, report.rows[0].stages)
        row = report.rows[0]
        self.assertEqual([row.state(name) for name in ("port", "answer", "log", "elasticsearch")],
                         [stages.OK, stages.NOT_APPLICABLE, stages.NOT_APPLICABLE, stages.NOT_APPLICABLE])

    def test_dicompot_by_its_time_window(self):
        echo = probes.ProbeResult("open", True, "11112/tcp: C-ECHO answered", src_ports=[])
        world = self.world(("dicompot",), results={"dicompot": echo})
        report = world.run()
        self.assertEqual(report.rows[0].state("log"), stages.OK)
        self.assertEqual(report.rows[0].state("elasticsearch"), stages.OK)

    def test_an_elasticsearch_without_the_token_query_is_asked_by_source(self):
        world = self.world(("cowrie",))
        world.es.refuse_token = True
        report = world.run()
        self.assertEqual(report.exit_code, 0, report.rows[0].stages)
        self.assertNotIn("query_string", json.dumps(world.es.deletes))
        self.assertTrue(any("token query" in text for _kind, text in world.said))
        self.assertEqual([d["_source"]["src_ip"] for d in world.es.docs], [ATTACKER])

    def test_another_400_keeps_the_token_query_and_fails(self):
        world = self.world(("cowrie",))
        world.es.refuse_other = True
        report = world.run()
        self.assertEqual(report.exit_code, 1)
        self.assertIn("400", report.rows[0].stages["elasticsearch"].detail)
        self.assertEqual(report.cleanup["state"], "failed")

    def test_the_second_pass_takes_what_was_indexed_late(self):
        world = self.world(("cowrie",))

        def late(es):
            if len(es.deletes) == 1:        # a session that closed after the first pass
                es.add({"type": "Cowrie", "src_ip": HOST, "src_port": 40100, "eventid": "cowrie.session.closed",
                        "@timestamp": stages.iso(world.now())})

        world.es.after_delete = late
        report = world.run()
        self.assertEqual(report.cleanup["passes"], [2, 1])
        self.assertEqual(report.cleanup["deleted"], 3)
        self.assertEqual(world.last()["cleanup"]["passes"], [2, 1])
        self.assertIn(run_checks.SECOND_PASS, world.sleeps)
        self.assertTrue(any("2 + 1" in text for _kind, text in world.said))

    def test_probe_events_through_docker_proxy_stay_and_are_counted(self):
        knock = probes.ProbeResult("open", True, "2404/tcp: answered", src_ports=[40100])
        world = self.world(("conpot_IEC104",), host="127.0.0.1", results={"conpot_IEC104": knock}, geo=False)
        report = world.run()
        self.assertEqual(world.es.deletes, [])      # nothing it can name
        self.assertEqual(report.cleanup["left"], 1)
        self.assertIn("src_ip", report.cleanup["left_kibana"])
        self.assertTrue(any(text.startswith("1 probe events could not be told apart from real ones and stay; "
                                            "Kibana: https://") for _kind, text in world.said))
        self.assertEqual(world.last()["cleanup"]["left"], 1)

    def test_a_closed_stdout_stops_nothing(self):
        world = self.world(("cowrie",))

        def broken(*args):
            raise BrokenPipeError(32, "Broken pipe")

        world.check._mark = broken
        world.check._say = broken
        report = world.run()
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(world.order[-4:], ["release", "delete", "delete", "exit 0"])
        self.assertEqual(world.last()["result"], "ok")
        with open(os.path.join(os.path.dirname(world.result_file), "last.log"), encoding="utf-8") as handle:
            journal = handle.read()
        self.assertIn("@@tpot phase release", journal)
        self.assertIn("test events removed", journal)

    def test_marks_through_a_stdout_that_raises(self):
        world = self.world(("cowrie",))
        world.check._mark = None

        class Closed(io.StringIO):
            def write(self, text):
                raise BrokenPipeError(32, "Broken pipe")

        with mock.patch.dict(os.environ, {"TPOT_MARKS": "1"}), mock.patch("sys.stdout", Closed()):
            report = world.run()
        self.assertEqual(report.exit_code, 0)
        self.assertIn("release", world.order)
        self.assertEqual(len(world.es.deletes), 2)

    def test_the_end_writes_last_json_before_its_output(self):
        world = self.world(("cowrie",))
        seen = []
        world.check._mark = lambda line: seen.append((line, os.path.exists(world.result_file)))
        world.run()
        release = [exists for line, exists in seen if line.startswith("@@tpot phase release")]
        self.assertEqual(release, [True])
        self.assertEqual([exists for line, exists in seen if "phase hold" in line], [False])

    def test_an_error_before_the_hold_writes_last_json(self):
        world = self.world(("cowrie",))

        def no_address():
            raise CheckError("no address of this host found to probe")

        world.check._host = no_address
        report = world.run()
        self.assertEqual((report.exit_code, report.result), (1, "error"))
        last = world.last()
        self.assertEqual((last["result"], last["error"], last["honeypots"]),
                         ("error", "no address of this host found to probe", []))
        self.assertNotIn("hold", world.order)
        world = self.world(("cowrie",), only=["nopot"])
        with self.assertRaises(run_checks.UsageError):
            world.run()
        self.assertIn("nopot", world.last()["error"])

    def test_a_signal_at_the_start_of_the_end_still_ends_the_hold_and_cleans_up(self):
        world = self.world(("cowrie", "dionaea"))
        world.hold = FakeHold(world, lose_after=1)
        log = world.check.log

        def signalled(kind, text):
            if kind == "error" and text == ewshold.MARKER_GONE:     # the first line of the end
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(0.2)
            log(kind, text)

        world.check.log = signalled
        report = world.run()
        self.assertEqual(report.exit_code, 130)
        self.assertEqual(world.order[-4:], ["release", "delete", "delete", "exit 130"])
        self.assertEqual(world.last()["result"], "cancelled")

    def test_the_end_is_announced_and_a_cancel_there_is_noted_once(self):
        world = self.world(("cowrie",))
        delete = world.es.delete_by_query

        def deleting(body):
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.05)
            return delete(body)

        world.es.delete_by_query = deleting
        report = world.run()
        self.assertEqual(report.exit_code, 130)
        texts = [text for _kind, text in world.said]
        self.assertIn("Ending the check: the hold of ewsposter ends, then the cleanup (up to about 80 s).", texts)
        self.assertEqual(texts.count("Cancel noted, finishing the cleanup first."), 1)
        # the announcement goes out before the hold ends, the result only after last.json
        self.assertLess(texts.index("Ending the check: the hold of ewsposter ends, then the cleanup (up to about "
                                    "80 s)."), texts.index("Cancel noted, finishing the cleanup first."))
        self.assertEqual(report.cleanup["passes"], [2, 0])

    def test_another_fault_after_the_hold_still_writes_last_json(self):
        world = self.world(("cowrie",))

        def broken(body):
            world.order.append("delete")
            raise RuntimeError("boom")

        world.es.delete_by_query = broken
        report = world.run()
        self.assertEqual((report.exit_code, report.result), (1, "error"))
        self.assertIn("release", world.order)
        last = world.last()
        self.assertEqual((last["result"], last["exit_code"]), ("error", 1))
        self.assertEqual(last["error"], "RuntimeError: boom")
        self.assertEqual([row["name"] for row in last["honeypots"]], ["cowrie"])
        with open(os.path.join(os.path.dirname(world.result_file), "last.log"), encoding="utf-8") as handle:
            self.assertIn("Traceback", handle.read())

    def test_ports_survive_only_on_an_ipv4_address_of_the_host(self):
        for host, survive in ((HOST, True), ("127.0.0.1", False), ("2001:db8::5", False), ("", False)):
            with self.subTest(host=host):
                world = self.world(("cowrie",))
                world.check.host = host
                self.assertEqual(world.check.ports_survive(), survive)

    def test_elasticsearch_down(self):
        world = self.world(("cowrie", "dionaea"))
        world.es.down = True
        report = world.run()
        self.assertEqual(report.exit_code, 1)
        for row in report.rows:
            self.assertIn("not reachable", row.stages["elasticsearch"].detail)
            self.assertIn("not reachable", row.stages["geoip"].detail)
        # one question per round while Elasticsearch does not answer, not one per honeypot and stage
        rounds = world.sleeps.count(run_checks.POLL) + 1
        self.assertEqual(world.es.attempts, rounds)
        # the settle before the cleanup
        self.assertIn(run_checks.END_SETTLE, world.sleeps)

    def test_a_missing_log_injects_nothing(self):
        world = self.world(("cowrie",))
        del world.files.logs["cowrie/log/cowrie.json"]
        report = world.run()
        self.assertEqual(report.rows[0].state("geoip"), stages.FAILED)
        self.assertIn("does not exist", report.rows[0].stages["geoip"].detail)

    def test_the_dry_run_touches_nothing(self):
        world = self.world(("cowrie", "honeytrap"), dry_run=True)
        lines = world.check.plan()
        self.assertEqual(world.order, [])
        self.assertEqual(world.files.appended, [])
        self.assertTrue(any(line.startswith("cowrie geo event -> cowrie/log/cowrie.json: ") for line in lines))
        self.assertTrue(any("_delete_by_query" in line for line in lines))
        self.assertFalse(os.path.exists(world.result_file))


class SignalsTest(unittest.TestCase):

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "no SIGHUP")
    def test_a_shield_notes_a_signal(self):
        handler = run_checks.Signals()
        with handler:
            with handler.shield():
                os.kill(os.getpid(), signal.SIGHUP)
                time.sleep(0.01)
            self.assertEqual(handler.received, [signal.SIGHUP])
        self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_DFL)

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "no SIGHUP")
    def test_one_exception_per_run(self):
        handler = run_checks.Signals()
        with handler:
            with self.assertRaises(run_checks.Terminated):
                os.kill(os.getpid(), signal.SIGHUP)
                time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGHUP)      # the end of the run goes on
            time.sleep(0.01)
            self.assertEqual(handler.received, [signal.SIGHUP, signal.SIGHUP])

    def test_outside_the_main_thread_nothing_changes(self):
        seen = []

        def thread():
            with run_checks.Signals() as handler:
                seen.append(handler._previous)

        worker = threading.Thread(target=thread)
        worker.start()
        worker.join()
        self.assertEqual(seen, [{}])


@unittest.skipUnless(getattr(os, "O_NOFOLLOW", 0) and os.open in getattr(os, "supports_dir_fd", ()),
                     "no folder descriptors here")
class LogFileTest(unittest.TestCase):

    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="tpot-logfile-")
        self.addCleanup(shutil.rmtree, self.data)
        os.makedirs(os.path.join(self.data, "cowrie", "log"))
        self.log = os.path.join(self.data, "cowrie", "log", "cowrie.json")
        with open(self.log, "w", encoding="utf-8") as out:
            out.write("one\n")

    def test_append_and_read(self):
        logfile.append(self.data, "cowrie/log/cowrie.json", b"two\n")
        self.assertEqual(logfile.size(self.data, "cowrie/log/cowrie.json"), 8)
        self.assertEqual(logfile.read_from(self.data, "cowrie/log/cowrie.json", 4), b"two\n")
        # shorter than the offset: rotated, read from the start
        self.assertEqual(logfile.read_from(self.data, "cowrie/log/cowrie.json", 100), b"one\ntwo\n")

    def test_nothing_is_created(self):
        with self.assertRaises(FileNotFoundError):
            logfile.append(self.data, "cowrie/log/new.json", b"x\n")
        self.assertFalse(os.path.exists(os.path.join(self.data, "cowrie", "log", "new.json")))
        self.assertIsNone(logfile.size(self.data, "cowrie/log/new.json"))
        self.assertIsNone(logfile.size(self.data, "nopot/log/x.json"))

    def test_links_are_refused(self):
        target = os.path.join(self.data, "elsewhere")
        with open(target, "w", encoding="utf-8") as out:
            out.write("keep\n")
        os.symlink(target, os.path.join(self.data, "cowrie", "log", "link.json"))
        with self.assertRaises(logfile.LogFileError):
            logfile.append(self.data, "cowrie/log/link.json", b"x\n")
        os.link(target, os.path.join(self.data, "cowrie", "log", "hard.json"))
        with self.assertRaises(logfile.LogFileError):
            logfile.append(self.data, "cowrie/log/hard.json", b"x\n")
        os.symlink(os.path.join(self.data, "cowrie"), os.path.join(self.data, "linked"))
        with self.assertRaises(logfile.LogFileError):
            logfile.append(self.data, "linked/log/cowrie.json", b"x\n")
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep\n")
        for rel in ("../x", "/etc/passwd", "cowrie//log", "", "cowrie/./log"):
            with self.subTest(rel=rel), self.assertRaises(logfile.LogFileError):
                logfile.append(self.data, rel, b"x\n")
        self.assertEqual(logfile.listing(self.data, "cowrie/log", "*.json"), ["cowrie/log/cowrie.json",
                                                                              "cowrie/log/hard.json"])

    def test_the_files_of_a_check_as_the_user(self):
        files = run_checks.Files(self.data, lambda argv: self.fail(f"no sudo needed: {argv}"))
        watch = run_checks.LogWatch(files)
        watch.mark("cowrie/log/cowrie.json")
        files.append("cowrie/log/cowrie.json", "two\n")
        self.assertEqual(watch.new_lines("cowrie/log/cowrie.json"), ["two"])
        self.assertEqual(files.listing("cowrie/log", "*.json"), ["cowrie/log/cowrie.json"])
        with self.assertRaises(CheckError):
            os.symlink(self.log, os.path.join(self.data, "cowrie", "log", "link.json"))
            files.append("cowrie/log/link.json", "x\n")

    def test_the_script_appends_its_argument(self):
        result = subprocess.run([sys.executable, "-I", logfile.__file__, "append", self.data,
                                 "cowrie/log/cowrie.json", "two\n"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, "-I", logfile.__file__, "read", self.data,
                                 "cowrie/log/cowrie.json", "0"], capture_output=True)
        self.assertEqual(result.stdout, b"one\ntwo\n")
        result = subprocess.run([sys.executable, "-I", logfile.__file__, "size", self.data, "cowrie/log/none"],
                                capture_output=True, universal_newlines=True)
        self.assertEqual(result.stdout, "-\n")


class FilesTest(unittest.TestCase):

    def test_root_through_sudo_where_the_user_may_not(self):
        calls = []

        def sudo_run(argv, **kwargs):
            stdin = kwargs.get("stdin")
            calls.append((argv, getattr(stdin, "name", stdin)))
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        folder = tempfile.mkdtemp(prefix="tpot-become-")
        self.addCleanup(shutil.rmtree, folder)
        password = os.path.join(folder, "pw")
        with open(password, "w", encoding="utf-8") as out:
            out.write("s3cret-password\n")
        files = run_checks.Files("/data", run_checks.Sudo(password, run=sudo_run))
        with mock.patch.object(logfile, "append", side_effect=PermissionError(13, "denied")):
            files.append("cowrie/log/cowrie.json", "line\n")
        argv, stdin = calls[-1]
        self.assertEqual(argv[:4], ["sudo", "-S", "-p", ""])
        self.assertEqual(argv[5:], ["-I", os.path.abspath(logfile.__file__), "append", "/data",
                                    "cowrie/log/cowrie.json", "line\n"])
        self.assertEqual(stdin, password)
        self.assertNotIn("s3cret-password", " ".join(argv))
        calls.clear()
        files = run_checks.Files("/data", run_checks.Sudo("", run=sudo_run, interactive=lambda: False))
        with mock.patch.object(logfile, "size", side_effect=PermissionError(13, "denied")):
            files.size("cowrie/log/cowrie.json")
        self.assertEqual(calls[0][0][:2], ["sudo", "-n"])

    def test_a_link_is_no_case_for_sudo(self):
        calls = []
        files = run_checks.Files("/data", lambda argv: calls.append(argv))
        with mock.patch.object(logfile, "append", side_effect=logfile.LogFileError("a link")):
            with self.assertRaisesRegex(CheckError, "a link"):
                files.append("cowrie/log/cowrie.json", "line\n")
        self.assertEqual(calls, [])


class LogWatchTest(unittest.TestCase):

    def test_new_whole_lines_since_the_mark(self):
        files = FakeFiles({"cowrie/log/cowrie.json": b"old\n", "honeypots/log/http.log": b"old\n"})
        watch = run_checks.LogWatch(files)
        watch.mark("cowrie/log/cowrie.json")
        watch.mark("honeypots/log/*.log")
        files.logs["cowrie/log/cowrie.json"] += b"one\ntw"
        self.assertEqual(watch.new_lines("cowrie/log/cowrie.json"), ["one"])
        files.logs["cowrie/log/cowrie.json"] += b"o\n"
        self.assertEqual(watch.new_lines("cowrie/log/cowrie.json"), ["one", "two"])
        files.logs["cowrie/log/cowrie.json"] = bytearray(b"new\n")     # rotated
        self.assertEqual(watch.new_lines("cowrie/log/cowrie.json")[-1], "new")
        # a file of a glob that came up during the check is read from its start
        files.logs["honeypots/log/ftp.log"] = bytearray(b"ftp line\n")
        files.logs["honeypots/log/http.log"] += b"http line\n"
        self.assertEqual(sorted(watch.new_lines("honeypots/log/*.log")), ["ftp line", "http line"])


class WrapperTest(unittest.TestCase):
    """hptest.sh and the pipeline test hand over to tpot check where the T-Pot Manager runs."""

    TPOT = ('#!/bin/sh\n'
            '[ "$1" = setup ] && exit "${STUB_SETUP_RC:-0}"\n'
            'printf "%s\\n" "$@" > "$STUB_ARGS"\n'
            'exit 7\n')

    def setUp(self):
        self.checkout = tempfile.mkdtemp(prefix="tpot-wrapper-")
        self.addCleanup(shutil.rmtree, self.checkout)
        path = os.path.join(self.checkout, "tpot")
        with open(path, "w", encoding="utf-8") as out:
            out.write(self.TPOT)
        os.chmod(path, 0o755)
        with open(os.path.join(self.checkout, ".env"), "w", encoding="utf-8") as out:
            out.write(f"TPOT_DATA_PATH={self.checkout}/data\n")
        self.args = os.path.join(self.checkout, "args")
        self.password = os.path.join(self.checkout, "pw")
        with open(self.password, "w", encoding="utf-8") as out:
            out.write("pw\n")

    def run_script(self, script, *argv, **extra):
        env = dict(os.environ, TPOTCE_DIR=self.checkout, STUB_ARGS=self.args, TPOT_GUM="off", **extra)
        return subprocess.run([script] + list(argv), capture_output=True, universal_newlines=True, env=env,
                              timeout=60, stdin=subprocess.DEVNULL)

    def handed(self):
        try:
            with open(self.args, encoding="utf-8") as handle:
                return handle.read().splitlines()
        except OSError:
            return None

    def test_hptest_is_tpot_check_honeypots(self):
        result = self.run_script(HPTEST, "-B", self.password, "-y")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(self.handed(), ["check", "honeypots", "--become-file", self.password, "--yes"])
        os.unlink(self.args)
        self.run_script(HPTEST, "127.0.0.1")
        self.assertEqual(self.handed(), ["check", "honeypots"])

    def test_hptest_options_still_parse(self):
        for argv, code in ((["--bogus"], 1), (["-B"], 1), (["-B", os.path.join(self.checkout, "none")], 1),
                           (["-h"], 0)):
            with self.subTest(argv=argv):
                result = self.run_script(HPTEST, *argv)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertIsNone(self.handed())
        self.assertIn("--nmap", self.run_script(HPTEST, "-h").stdout)

    def test_the_pipeline_test_is_tpot_check_pipeline(self):
        result = self.run_script(PIPELINE, "--types", "cowrie,dionaea", "--timeout", "30", "--dry-run", "-y",
                                 "-B", self.password)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(self.handed(), ["check", "pipeline", "--only", "cowrie,dionaea", "--timeout", "30",
                                         "--dry-run", "--become-file", self.password, "--yes"])
        os.unlink(self.args)
        self.run_script(PIPELINE)
        self.assertEqual(self.handed(), ["check", "pipeline"])
        os.unlink(self.args)
        self.run_script(PIPELINE, "--types", "cowrie,Ciscoasa")
        self.assertEqual(self.handed(), ["check", "pipeline", "--only", "cowrie,ciscoasa"])

    def test_the_pipeline_test_options_still_parse(self):
        for argv, code in ((["--bogus"], 1), (["--types"], 1), (["--timeout", "soon"], 1), (["-h"], 0),
                           (["-B", os.path.join(self.checkout, "none")], 1)):
            with self.subTest(argv=argv):
                result = self.run_script(PIPELINE, *argv)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertIsNone(self.handed())

    def test_without_tpot_the_old_test_runs(self):
        result = self.run_script(PIPELINE, "--dry-run", STUB_SETUP_RC="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertIsNone(self.handed())
        result = self.run_script(PIPELINE, "--dry-run", "--types", "nopot", STUB_SETUP_RC="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown type", result.stdout + result.stderr)


class HptestFallbackTest(HptestUnderTheHoldTest):
    """--nmap and another host keep the probes of hptest.sh, under the hold, also where tpot runs."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.checkout, "tpot"), "w", encoding="utf-8") as out:
            out.write(WrapperTest.TPOT)
        os.chmod(os.path.join(self.checkout, "tpot"), 0o755)
        self.args = os.path.join(self.bin, "args")

    def test_term_during_the_scan(self):
        pass        # HptestUnderTheHoldTest has it

    def test_a_skipped_udp_scan_says_so(self):
        pass        # HptestUnderTheHoldTest has it

    def scan(self, *argv):
        with FakeEwsposter(self.ews):
            return subprocess.run([HPTEST] + list(argv), capture_output=True, universal_newlines=True,
                                  env=self.env(STUB_SCAN_QUICK="1", STUB_ARGS=self.args, TPOT_MARKS="1"),
                                  timeout=60)

    def test_nmap_keeps_the_old_probes_under_the_hold(self):
        result = self.scan("--nmap", "127.0.0.1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.args))
        self.assertIn("@@tpot phase hold", result.stdout)
        self.assertIn("@@tpot phase release", result.stdout)
        self.assertEqual(len(self.scanners()), 2)
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(self.lock_is_free())

    def test_another_host_keeps_the_old_probes(self):
        result = self.scan("198.51.100.7")
        self.assertFalse(os.path.exists(self.args))
        self.assertIn("is no address of this host", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
