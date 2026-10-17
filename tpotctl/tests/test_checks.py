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

        with self.assertRaisesRegex(HoldError, "ended"):
            probes.run_probes("127.0.0.1", [self.honeypot(str(n)) for n in range(5)], TOKEN, workers=1,
                              probes={str(n): fine for n in range(5)}, check=check)
        self.assertEqual(state, {"calls": 2, "probed": 1})


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


if __name__ == "__main__":
    unittest.main()
