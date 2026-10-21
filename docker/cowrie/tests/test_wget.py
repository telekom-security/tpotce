"""wget of the BusyBox personas (commands/wget.py after the patch): upstream's download with the
texts of BusyBox wget (busybox:1.27.2 / 1.36.1, round 4 references; the usage of the older releases
from their sources); GNU wget as upstream elsewhere."""
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from urllib import parse

from tests.test_patch import FIXTURE, PATCH, FakeConfig

WGET = "src/cowrie/commands/wget.py"
NEEDLE = 'commands["/usr/bin/dget"] = Command_wget\ncommands["dget"] = Command_wget\n'


class Base:
    """Command_wget of upstream as far as the subclass calls it."""

    def __init__(self):
        self.err, self.exited, self.exit_code = [], False, 0

    def errorWrite(self, data):
        self.err.append(data)

    def print_usage_error(self, error_msg=""):
        self.err.append(f"wget: {error_msg}\nUsage: wget [OPTION]... [URL]...\n\nTry `wget --help' for more options.\n")

    def exit(self, code=None):
        self.exited = True
        if code is not None:
            self.exit_code = code

    def success(self, response):
        return "upstream success"

    def error(self, response):
        return "upstream error"


class WgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        cls.source = (root / WGET).read_text().split(NEEDLE, 1)[1]

    def command(self, values, host="198.51.100.7", port=80, url="http://198.51.100.7/x", outfile="/tmp/x", ip=None):
        namespace = {"CowrieConfig": FakeConfig(values), "Command_wget": Base, "parse": parse,
                     "is_ip_address": lambda h: h.replace(".", "").isdigit(), "inlineCallbacks": lambda f: f,
                     "reactor": None, "error": types.SimpleNamespace(ConnectionRefusedError=ConnectionRefusedError),
                     "abort_body": lambda r: None, "report_download_failure": lambda c, u: None, "commands": {},
                     "communication_allowed": None}
        exec(compile(self.source, "<wget>", "exec"), namespace)  # noqa: S102
        command = namespace["Command_tpot_wget"]()
        command.host, command.port, command.outfile = host, port, outfile
        # start keeps the URL of the arguments (Cowrie sets self.url only past its address check)
        command.tpot_url = url
        command.tpot_ip = ip if ip is not None else (host if host.replace(".", "").isdigit() else None)
        command.currentlength = 0
        return command

    BB127 = {("shell", "wget_style"): "busybox", ("shell", "text_version"): "1.27.2",
             ("shell", "text_banner"): "BusyBox v1.27.2 (2017-11-01 23:22:25 UTC)"}

    def test_gnu_wget_stays(self):
        command = self.command({})
        command.errorWrite("--2026-10-14 10:00:00--  http://x/\n")
        self.assertEqual(command.err, ["--2026-10-14 10:00:00--  http://x/\n"])

    def test_a_local_address(self):
        # busybox 1.27.2: wget http://127.0.0.1:1/x
        command = self.command(self.BB127, host="127.0.0.1", port=1, url="http://127.0.0.1:1/x")
        for line in ("--2026-10-14 10:00:00--  http://127.0.0.1:1/x\n",
                     "Resolving 127.0.0.1 (127.0.0.1)... failed: Temporary failure in name resolution.\n",
                     "wget: unable to resolve host address ‘127.0.0.1’\n"):
            command.errorWrite(line)
        self.assertEqual("".join(command.err), "Connecting to 127.0.0.1:1 (127.0.0.1:1)\n"
                                               "wget: can't connect to remote host (127.0.0.1): Connection refused\n")

    def test_the_rate_limit_of_upstream(self):
        # upstream's rate limit writes a GNU timeout; BusyBox says it could not connect
        command = self.command(self.BB127, host="203.0.113.9", url="http://203.0.113.9/x")
        for line in ("Connecting to 203.0.113.9:80... ", "failed: Connection timed out.\n", "Retrying.\n\n"):
            command.errorWrite(line)
        self.assertEqual("".join(command.err), "Connecting to 203.0.113.9 (203.0.113.9:80)\n"
                                               "wget: can't connect to remote host (203.0.113.9): Connection timed out\n")

    def test_a_name_that_does_not_resolve(self):
        command = self.command(self.BB127, host="nosuch.invalid", url="http://nosuch.invalid/x")
        command.errorWrite("wget: unable to resolve host address ‘nosuch.invalid’\n")
        self.assertEqual(command.err, ["wget: bad address 'nosuch.invalid'\n"])

    def test_a_download(self):
        # busybox 1.27.2: Connecting to H (IP:P), the progress line at the end and a blank line
        command = self.command(self.BB127, host="example.org", url="http://example.org/x", ip="203.0.113.5")
        for line in ("--2026-10-14 10:00:00--  http://example.org/x\n", "Connecting to example.org:80... connected.\n",
                     "HTTP request sent, awaiting response... ", "200 OK\n", "Length: 6 (6B) [text/plain]\n",
                     "Saving to: `/tmp/x'\n\n", "\r100% [=====>] 6  0.0K/s  eta 0s"):
            command.errorWrite(line)
        command.currentlength = 6
        command.errorWrite("\r100% [=====>] 6 0.0K/s")
        command.errorWrite("\n\n")
        command.errorWrite("2026-10-14 10:00:00 (0.01 KB/s) - `/tmp/x' saved [6/6]\n\n")
        self.assertEqual("".join(command.err),
                         "Connecting to example.org (203.0.113.5:80)\n"
                         "x                    100% |*******************************|     6   0:00:00 ETA\n\n")
        command = self.command(self.BB127, outfile="/tmp/big.bin")
        command.currentlength = 123456
        self.assertEqual(command.tpot_progress((1, 27)),
                         "big.bin              100% |*******************************|   120k  0:00:00 ETA\n\n")

    def test_a_download_of_1_36(self):
        values = {**self.BB127, ("shell", "text_version"): "1.36.1"}
        command = self.command(values, host="127.0.0.1", port=8080, url="http://127.0.0.1:8080/x")
        command.errorWrite("Connecting to 127.0.0.1:8080... connected.\n")
        command.currentlength = 6
        command.errorWrite("2026-10-14 10:00:00 (0.01 KB/s) - `/tmp/x' saved [6/6]\n\n")
        self.assertEqual("".join(command.err),
                         "Connecting to 127.0.0.1:8080 (127.0.0.1:8080)\nsaving to '/tmp/x'\n"
                         "x                    100% |********************************|     6  0:00:00 ETA\n"
                         "'/tmp/x' saved\n")

    def test_an_error_of_the_server(self):
        command = self.command(self.BB127)
        response = types.SimpleNamespace(code=404, phrase=b"Not Found", version=(b"HTTP", 1, 1))
        self.assertIsNone(command.success(response))
        self.assertEqual((command.err, command.exit_code), (["wget: server returned error: HTTP/1.1 404 Not Found\n"], 1))
        self.assertEqual(self.command(self.BB127).success(types.SimpleNamespace(code=200)), "upstream success")

    def test_the_usage_per_release(self):
        command = self.command(self.BB127)
        command.print_usage_error("missing URL")
        self.assertEqual("".join(command.err), "BusyBox v1.27.2 (2017-11-01 23:22:25 UTC) multi-call binary.\n\n"
                         "Usage: wget [-c|--continue] [--spider] [-q|--quiet] [-O|--output-document FILE]\n"
                         "\t[--header 'header: value'] [-Y|--proxy on/off] [-P DIR]\n"
                         "\t[-S|--server-response] [-U|--user-agent AGENT] [-T SEC] URL...\n\n"
                         "Retrieve files via HTTP or FTP\n\n"
                         "\t--spider\tOnly check URL existence: $? is 0 if exists\n"
                         "\t-c\t\tContinue retrieval of aborted transfer\n\t-q\t\tQuiet\n"
                         "\t-P DIR\t\tSave to DIR (default .)\n\t-S    \t\tShow server response\n"
                         "\t-T SEC\t\tNetwork read timeout is SEC seconds\n\t-O FILE\t\tSave to FILE ('-' for stdout)\n"
                         "\t-U STR\t\tUse STR for User-Agent header\n\t-Y on/off\tUse proxy\n")
        self.assertEqual(command.exit_code, 1)
        for version, line in (("1.26.2", "\t--spider\tSpider mode"), ("1.24.1", "\t-s\tSpider mode"),
                              ("1.19.4", "[--no-check-certificate]"), ("1.14.1", "url\n\n")):
            with self.subTest(version=version):
                command = self.command({**self.BB127, ("shell", "text_version"): version})
                command.print_usage_error("missing URL")
                self.assertIn(line, "".join(command.err))
        # 1.14 says "cannot", later releases "can't"
        command = self.command({**self.BB127, ("shell", "text_version"): "1.14.1"}, host="10.0.0.1")
        command.errorWrite("wget: unable to resolve host address ‘10.0.0.1’\n")
        self.assertIn("wget: cannot connect to remote host (10.0.0.1): Connection refused\n", "".join(command.err))

    def test_the_usage_of_the_recorded_releases(self):
        import json
        data = json.loads((Path(__file__).parent / "fixtures" / "busybox_wget_usage.json").read_text())
        for version, text in data["usage"].items():
            with self.subTest(version=version):
                banner = text.split(" multi-call binary.", 1)[0]
                keys = {("shell", key): value for key, value in data["keys"].get(version, {}).items()}
                command = self.command({**self.BB127, ("shell", "text_version"): version,
                                        ("shell", "text_banner"): banner, **keys})
                command.print_usage_error("missing URL")
                self.assertEqual("".join(command.err), text)

    C7 = {("shell", "wget_style"): "busybox", ("shell", "text_version"): "1.19.4", ("shell", "text_libc"): "uclibc",
          ("shell", "text_banner"): "BusyBox v1.19.4 (2022-07-14 23:31:52 EDT)", ("shell", "wget_timeout"): "no"}

    def bb(self, version, args, libc="glibc", timeout="yes"):
        command = self.command({("shell", "wget_style"): "busybox", ("shell", "text_version"): version,
                                ("shell", "text_banner"): f"BusyBox v{version} (2020-01-01 00:00:00 UTC)",
                                ("shell", "text_libc"): libc, ("shell", "wget_timeout"): timeout})
        command.args = list(args)
        return command.tpot_bb_args(), "".join(command.err), command

    def test_the_options_of_busybox_wget(self):
        # -T and -t are taken and ignored, the options that do not change the download go with their values
        url = "http://198.51.100.7/x"
        self.assertEqual(self.bb("1.27.2", ["-T", "5", "-U", "Mozilla", "-Y", "off", "-q", "-O", "-", url])[0],
                         ["-q", "-O", "-", url])
        self.assertEqual(self.bb("1.27.2", ["-T5", "-t", "2", "-UMozilla", "-nv", "-S", url])[0], [url])
        self.assertEqual(self.bb("1.27.2", ["--user-agent=Mozilla", "--output-document", "/tmp/a", "--quiet",
                                            "--tries=3", "--no-check-certificate", url])[0],
                         ["-O", "/tmp/a", "--quiet", url])
        args, err, command = self.bb("1.27.2", [url, "--header"])
        self.assertIsNone(args)
        self.assertTrue(err.startswith("wget: option '--header' requires an argument\n"))
        args, err, command = self.bb("1.27.2", ["-x", url])
        self.assertIsNone(args)
        self.assertTrue(err.startswith("wget: invalid option -- 'x'\nBusyBox v1.27.2 "))
        self.assertEqual(command.exit_code, 1)

    def test_the_option_table_of_each_release(self):
        """busybox:1.27.2 (uClibc) / 1.31.1 / 1.36.1 (glibc): -s goes with 1.25, -o / --output-file come
        with 1.30, --post-file with 1.36."""
        url = "http://198.51.100.7/x"
        cases = (("1.27.2", "uclibc", ["-s", url], "wget: invalid option -- s\n"),
                 ("1.27.2", "uclibc", ["-o", "/tmp/l", url], "wget: invalid option -- o\n"),
                 ("1.27.2", "uclibc", ["--output-file=/tmp/l", url], "wget: unrecognized option `--output-file=/tmp/l'\n"),
                 ("1.31.1", "uclibc", ["--post-file=/etc/hostname", url],
                  "wget: unrecognized option `--post-file=/etc/hostname'\n"),
                 ("1.36.1", "glibc", ["-s", url], "wget: invalid option -- 's'\n"),
                 ("1.36.1", "glibc", ["--bogus", url], "wget: unrecognized option '--bogus'\n"))
        for version, libc, args, first in cases:
            with self.subTest(version=version, args=args):
                got, err, _ = self.bb(version, args, libc)
                self.assertIsNone(got)
                self.assertTrue(err.startswith(first + f"BusyBox v{version} "), err)
        for version, args in (("1.31.1", ["-o", "/tmp/l", url]), ("1.36.1", ["--output-file=/tmp/l", url]),
                              ("1.36.1", ["--post-file=/etc/hostname", "-nc", url]), ("1.19.4", ["-s", url]),
                              ("1.14.1", ["-T", "5", "-t", "2", "-s", url])):
            with self.subTest(version=version, args=args):
                self.assertEqual(self.bb(version, args)[0], [url])

    def test_the_archer_c7_takes_and_ignores_t(self):
        # BusyBox v1.19.4 of the Archer C7 v5 (220715 image, qemu-mips-static; no FEATURE_WGET_TIMEOUT):
        #   -T N, -T5, -t N and -s -T N connect; --timeout=5, -Z and -T without a value are errors
        url = "http://127.0.0.1:1/x"
        for args in (["-T", "5", url], ["-T5", url], ["-t", "2", url], ["-s", "-T", "5", url]):
            with self.subTest(args=args):
                self.assertEqual(self.bb("1.19.4", args, "uclibc", "no")[0], [url])
        for args, first in ((["--timeout=5", url], "wget: unrecognized option `--timeout=5'\n"),
                            (["-Z", url], "wget: invalid option -- Z\n"),
                            (["-T"], "wget: option requires an argument -- T\n")):
            with self.subTest(args=args):
                got, err, command = self.bb("1.19.4", args, "uclibc", "no")
                self.assertIsNone(got)
                self.assertTrue(err.startswith(first + "BusyBox v1.19.4 (2020-01-01 00:00:00 UTC) multi-call binary.\n"
                                               "\nUsage: wget [-c|--continue]"), err)
                self.assertTrue(err.endswith("\t-Y\tUse proxy ('on' or 'off')\n\n"))
                self.assertEqual(command.exit_code, 1)
        # with the feature --timeout is an option
        self.assertEqual(self.bb("1.19.4", ["--timeout=5", url], "uclibc", "yes")[0], [url])

    def test_the_timeout_is_a_number_with_the_feature(self):
        # getopt32 converts -T SEC with xatoi_positive where the build has FEATURE_WGET_TIMEOUT (from 1.18:
        #   "T+" in wget.c 1.18.0 to 1.36.1; busybox:1.26.2 / 1.27.2 / 1.36.1 recorded): "invalid number 'X'" or
        #   the range of an int, status 1 and no usage, before the URL is looked at
        url = "http://127.0.0.1:1/x"
        cases = ((["-T", "abc", url], "wget: invalid number 'abc'\n"), (["-Tabc", url], "wget: invalid number 'abc'\n"),
                 (["-T", "5x", url], "wget: invalid number '5x'\n"), (["-T", "+5", url], "wget: invalid number '+5'\n"),
                 (["-T", " 5", url], "wget: invalid number ' 5'\n"), (["-T", "", url], "wget: invalid number ''\n"),
                 (["-T", "4294967296", url], "wget: invalid number '4294967296'\n"),
                 (["-T", "2147483648", url], "wget: number 2147483648 is not in 0..2147483647 range\n"),
                 (["--timeout=abc", url], "wget: invalid number 'abc'\n"), (["-T", "abc"], "wget: invalid number 'abc'\n"),
                 (["-T", "abc", "-Z", url], "wget: invalid number 'abc'\n"))
        for version in ("1.19.4", "1.26.2", "1.36.1"):
            for args, err in cases:
                with self.subTest(version=version, args=args):
                    got, text, command = self.bb(version, args)
                    self.assertIsNone(got)
                    self.assertEqual((text, command.exit_code), (err, 1))
            for value in ("2147483647", "007", "0"):
                self.assertEqual(self.bb(version, ["-T", value, url])[0], [url])
        # without the feature the value is taken and ignored (the Archer C7 v5, 1.14.1 of the DIR-859)
        self.assertEqual(self.bb("1.19.4", ["-T", "abc", url], "uclibc", "no")[0], [url])
        self.assertEqual(self.bb("1.14.1", ["-T", "abc", url])[0], [url])

    def test_quiet_and_a_log_file(self):
        # busybox:1.27.2: -q drops the Connecting line, the error stays; -o LOGFILE (1.31.1) writes nothing
        _, _, command = self.bb("1.27.2", ["-q", "http://198.51.100.7/x"])
        command.errorWrite("Connecting to 198.51.100.7:80... ")
        command.errorWrite("failed: Connection timed out.\n")
        self.assertEqual(command.err, ["wget: can't connect to remote host (198.51.100.7): Connection timed out\n"])
        _, _, command = self.bb("1.31.1", ["-o", "/tmp/l", "http://198.51.100.7/x"])
        command.errorWrite("Connecting to 198.51.100.7:80... ")
        self.assertEqual(command.err, [])

    def test_the_busybox_personas(self):
        import configparser

        from tpot_personas import catalog
        from tpot_personas.identity import draw
        from tpot_personas.render import render_cfg
        expected = {"dlink-dir859", "tplink-archer-c7", "zyxel-nas326", "qnap-qts", "ipcam"}
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render_cfg(persona, draw(persona, 0), Path("/tmp/r"), Path("/tmp/k")))
                self.assertEqual(cfg.get("shell", "wget_style", fallback="gnu") == "busybox", persona.id in expected)
                # the BusyBox of the Archer C7 is built without FEATURE_WGET_TIMEOUT (no -T in its usage, no --timeout)
                self.assertEqual(cfg.get("shell", "wget_timeout", fallback="yes"),
                                 "no" if persona.id == "tplink-archer-c7" else "yes")
                self.assertEqual(cfg.get("shell", "dd_status_line", fallback="yes"),
                                 "no" if persona.id == "tplink-archer-c7" else "yes")

    def test_fails_loudly_on_unknown_upstream(self):
        with tempfile.TemporaryDirectory() as handle:
            root = Path(handle) / "cowrie"
            shutil.copytree(FIXTURE, root)
            path = root / WGET
            text = path.read_text()
            self.assertEqual(text.count(NEEDLE), 1)
            path.write_text(text.replace(NEEDLE, NEEDLE.replace("Command_wget\n", "Command_wget  # dget\n", 1)))
            done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], capture_output=True,
                                  text=True)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("Could not find expected text", done.stderr)


class ImportNeedleTest(unittest.TestCase):
    def test_fails_loudly_on_unknown_upstream(self):
        root = Path(tempfile.mkdtemp()) / "cowrie"
        self.addCleanup(shutil.rmtree, root.parent, True)
        shutil.copytree(FIXTURE, root)
        path = root / WGET
        old = "    communication_allowed,\n    is_ip_address,\n"
        text = path.read_text()
        self.assertEqual(text.count(old), 1)
        path.write_text(text.replace(old, "    communication_allowed,\n    outbound_bind_address,\n    is_ip_address,\n"))
        done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("Could not find expected text", done.stderr)


class UpstreamWgetTest(unittest.TestCase):
    """The subclass over Command_wget of the patched fixture (upstream v3.1.3 wget.py): the texts upstream's
    download path writes come out as BusyBox writes them."""

    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        cls.source = (root / WGET).read_text()

    def module(self, values):
        from tests.test_patch import module_all

        class Config(FakeConfig):
            def getint(self, section, option, fallback=0):
                return int(self.get(section, option, fallback))

        class HoneyPotCommand:
            def __init__(self):
                self.err, self.exited, self.exit_code = [], False, 0

            def errorWrite(self, data):
                self.err.append(data)

            def writeBytes(self, data):
                pass

            def exit(self, code=None):
                self.exited = True

        class DNSLookupError(Exception):
            pass

        class Failure:
            def __init__(self, kind):
                self.kind = kind

            def check(self, *kinds):
                return self.kind if self.kind in kinds else None

        stub = types.SimpleNamespace
        namespace = {
            "CowrieConfig": Config(values), "HoneyPotCommand": HoneyPotCommand, "Logger": lambda: stub(info=print),
            "defer": None, "reactor": None, "inlineCallbacks": lambda f: f, "CancelledError": type("C", (Exception,), {}),
            "ClientCreator": None, "Protocol": object, "CommandFailed": type("F", (Exception,), {}), "FTPClient": None,
            "failure": None, "UNKNOWN_LENGTH": -1, "Artifact": None,
            "BlockedAddress": type("B", (Exception,), {}), "UnreachableAddress": type("U", (Exception,), {}),
            "capture_download": lambda *a, **k: None, "fetch": None, "outbound_rate_limiter": lambda name: None,
            "report_download_failure": lambda *a: None, "DownloadLimitExceeded": Exception, "abort_body": lambda r: None,
            "communication_allowed": None, "is_ip_address": lambda h: h.replace(".", "").isdigit(),
            "outbound_bind_address": None,
            "error": stub(DNSLookupError=DNSLookupError, ConnectingCancelledError=type("CC", (Exception,), {}),
                          ConnectionDone=type("CD", (Exception,), {}), TCPTimedOutError=type("T", (Exception,), {}),
                          ConnectionRefusedError=type("R", (Exception,), {})),
        }
        sys.modules.setdefault("treq", types.ModuleType("treq"))
        module_all(self.source, namespace)
        return namespace, Failure

    def download(self, values):
        namespace, _ = self.module(values)
        command = namespace["Command_tpot_wget"]()
        command.host, command.port, command.quiet, command.outfile = "198.51.100.7", 80, False, "/tmp/x"
        command.url, command.tpot_url, command.tpot_ip = b"http://198.51.100.7/x", "http://198.51.100.7/x", "198.51.100.7"
        command.artifact = types.SimpleNamespace(write=lambda data: None, close=lambda: None)
        command.limit_size = 0
        command._begin_download(6, "text/plain", "200 OK")
        command.collect(b"hello\n")
        command.collectioncomplete(None)
        return "".join(command.err)

    def test_a_download_with_upstreams_path(self):
        bb = {("shell", "wget_style"): "busybox", ("shell", "text_version"): "1.27.2"}
        self.assertEqual(self.download(bb),
                         "x                    100% |*******************************|     6   0:00:00 ETA\n\n")
        # GNU wget of upstream (no BusyBox): its own texts
        gnu = self.download({})
        self.assertIn("Saving to: `/tmp/x'", gnu)
        self.assertIn("saved [6/6]", gnu)

    def started(self, url, limited=False, blocked=False):
        """start() of the BusyBox wget over upstream's start (inlineCallbacks of the test reactor): what it wrote and
        the order of the rate limit, upstream's address check and the resolution of the name."""
        from tests.test_patch import later_callbacks
        calls = []
        limiter = types.SimpleNamespace(check=lambda host: calls.append(("limit", host)) or not limited)

        def resolve_allowed(address):
            calls.append(("check", address))
            return None if blocked else "203.0.113.9"

        def resolve(address):
            calls.append(("resolve", address))
            return "203.0.113.9"

        values = {("shell", "wget_style"): "busybox", ("shell", "text_version"): "1.27.2"}
        namespace, _ = self.module(values)
        namespace.update({"inlineCallbacks": later_callbacks, "resolve_allowed": resolve_allowed,
                          "reactor": types.SimpleNamespace(resolve=resolve),
                          "Logger": lambda: types.SimpleNamespace(info=lambda *a, **k: None),
                          "Artifact": lambda name: types.SimpleNamespace(write=lambda d: None, close=lambda: None)})
        namespace["outbound_rate_limiter"] = lambda name: limiter
        from tests.test_patch import module_all
        module_all(self.source, namespace)
        command = namespace["Command_tpot_wget"]()
        command.args, command.cwd = [url], "/root"
        command.fs = types.SimpleNamespace(resolve_path=lambda p, cwd: "/root/" + p.lstrip("/"),
                                           exists=lambda p: True, isdir=lambda p: True)
        command.httpDownload = lambda u: None
        command.start()
        return "".join(command.err), calls, namespace["tpot_wget_asking"]

    def test_the_name_is_resolved_after_the_rate_limit_and_the_address_check(self):
        out, calls, asking = self.started("http://example.test/x", limited=True)
        self.assertEqual(calls, [("limit", "example.test")])
        self.assertEqual(out, "Connecting to example.test (example.test:80)\n"
                              "wget: can't connect to remote host (example.test): Connection timed out\n")
        self.assertEqual(asking, [])
        out, calls, asking = self.started("http://example.test/x")
        self.assertEqual(calls, [("limit", "example.test"), ("check", "example.test")])
        self.assertTrue(out.startswith("Connecting to example.test (203.0.113.9:80)\n"), out)
        out, calls, asking = self.started("http://example.test/x", blocked=True)
        self.assertEqual(calls, [("limit", "example.test"), ("check", "example.test"), ("resolve", "example.test")])
        self.assertEqual(out, "Connecting to example.test (203.0.113.9:80)\n"
                              "wget: can't connect to remote host (203.0.113.9): Connection refused\n")
        self.assertEqual(asking, [])

    def test_a_name_that_does_not_resolve_on_upstreams_path(self):
        namespace, failure = self.module({("shell", "wget_style"): "busybox", ("shell", "text_version"): "1.27.2"})
        command = namespace["Command_tpot_wget"]()
        command.host, command.port, command.url, command.tpot_ip = "nosuch.invalid", 80, b"http://nosuch.invalid/x", None
        command.error(failure(namespace["error"].DNSLookupError))
        self.assertEqual(command.err, ["wget: bad address 'nosuch.invalid'\n"])


if __name__ == "__main__":
    unittest.main()
