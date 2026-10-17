"""tpot check and tpot attackers: the host helpers of T-Pot in the look of tpot."""

import contextlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
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

from tpotctl import events  # noqa: E402
from tpotctl.checks import CheckError, HoldError  # noqa: E402
from tpotctl.checks import ewshold  # noqa: E402

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

    def test_a_failing_refresh_stops_the_check(self):
        target = os.path.join(self.data, "elsewhere")
        with open(target, "w", encoding="utf-8") as out:
            out.write("keep\n")
        with FakeEwsposter(self.ews):
            with self.hold(refresh=0.05) as hold:
                hold.check()
                os.unlink(self.marker)
                os.symlink(target, self.marker)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        hold.check()
                    except HoldError as error:
                        self.assertEqual(str(error), "the hold marker could not be refreshed, the check stops")
                        break
                    time.sleep(0.02)
                else:
                    self.fail("the check went on")
                self.assertIn(("error", "the hold marker could not be refreshed, the check stops"), self.said)
                self.assertEqual(sum(1 for kind, _text in self.said if kind == "warn"), 2)
        self.assertTrue(os.path.islink(self.marker))
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep\n")
        self.assertTrue(self.lock_is_free())

    def test_one_failed_refresh_is_tried_again(self):
        with FakeEwsposter(self.ews):
            with self.hold(refresh=0.05) as hold:
                real = hold._put_marker
                calls = []

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
    """ews_hold.sh with a flock of fcntl (same lock as util-linux / busybox) and a docker stub."""

    def setUp(self):
        super().setUp()
        self.bin = tempfile.mkdtemp(prefix="tpot-hold-bin-")
        self.addCleanup(shutil.rmtree, self.bin)
        self.stub("flock", "#!/bin/sh\n"
                           "[ \"$1\" = \"-n\" ] || exit 64\n"
                           "exec python3 -c 'import fcntl, sys\n"
                           "try:\n"
                           "    fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                           "except BlockingIOError:\n"
                           "    sys.exit(1)' \"$2\"\n")
        self.stub("docker", "#!/bin/sh\necho \"$*\" >> \"$STUB_CALLS\"\necho \"${STUB_EWS_RUNNING:-true}\"\n")
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
                env=self.env(myEWS_HOLD_REFRESH="1"), start_new_session=True)
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


if __name__ == "__main__":
    unittest.main()
