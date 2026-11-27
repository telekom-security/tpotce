"""tpot sensors: registry, access, SSH, certificate and status, on a temporary checkout."""

import base64
import fcntl
import http.client
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import AsyncTestCase, isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import sensor_probe, sensors, users  # noqa: E402
from tpotctl.bootstrap import REPO_DIR  # noqa: E402
from tpotctl.envfile import EnvFile  # noqa: E402

# the UI needs Textual, the CLI Rich
textual = importlib.util.find_spec("textual") and importlib.util.find_spec("rich")
try:
    import yaml
except ImportError:
    yaml = None

WEB_USER = "dHNlYzokYXByMSRYUnE2SC5rbiRVRjZQM1VVQmJVNWJUQmNmSGRuUFQxCgo="
# two sensors of 24.04.1, deployed with deploy.sh (apr1)
OLD_SENSORS = [users.encode("sensor-old-otter:$apr1$abc$def"), users.encode("sensor-old-lynx:$apr1$ghi$jkl")]
P384 = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-384"]
OPENSSL_TEXT = """        X509v3 extensions:
            X509v3 Subject Alternative Name:
                IP Address:192.168.5.15, DNS:hive.example.org
"""
# what cert_info asks openssl x509 for an RSA certificate of tpotinit of an earlier release
RSA_TEXT = """issuer=O=Internet Widgits Pty Ltd,ST=Some-State,C=AU
notAfter=Nov  7 22:19:59 2036 GMT
Certificate:
    Data:
        Signature Algorithm: sha512WithRSAEncryption
        Issuer: O=Internet Widgits Pty Ltd,ST=Some-State,C=AU
        Subject: O=Internet Widgits Pty Ltd,ST=Some-State,C=AU
        Subject Public Key Info:
            Public Key Algorithm: rsaEncryption
                Public-Key: (2048 bit)
                Modulus:
                    00:c4:5e
                Exponent: 65537 (0x10001)
        X509v3 extensions:
            X509v3 Subject Alternative Name:
                IP Address:192.168.5.15, DNS:hive.example.org
    Signature Algorithm: sha512WithRSAEncryption
"""
SELF_SIGNED = "O=Internet Widgits Pty Ltd,ST=Some-State,C=AU"
RENEW = "tpot sensors cert --renew --distribute"


def cert(sans, self_signed=True, key="ECDSA P-384", days=3650, issuer="Example Root CA", now=None):
    """A CertInfo of nginx.crt that ends `days` (and an hour) after now."""
    now = now or datetime.now(timezone.utc)
    return sensors.CertInfo(key=key, self_signed=self_signed, issuer=SELF_SIGNED if self_signed else issuer,
                            not_after=now + timedelta(days=days, hours=1), sans=list(sans))


def fake_openssl(text, verify=0):
    """openssl for cert_info: x509 prints `text`, the first certificate goes to -out, verify ends with `verify`."""
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[1] == "x509" and "-out" in command:
            with open(command[command.index("-out") + 1], "w") as handle:
                handle.write("first\n")
            return mock.Mock(returncode=0, stdout="", stderr="")
        if command[1] == "x509":
            return mock.Mock(returncode=0, stdout=text, stderr="")
        return mock.Mock(returncode=verify, stdout="", stderr="error 20 at 0 depth lookup" if verify else "")
    return run, calls


def flat(text):
    """The output without the line breaks of Rich."""
    return " ".join(text.split())


def openssl_3() -> bool:
    """OpenSSL 3 or later: the options of the certificate (LibreSSL of macOS takes others)."""
    if not shutil.which("openssl"):
        return False
    try:
        out = subprocess.run(["openssl", "version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    match = re.match(r"OpenSSL (\d+)\.", out or "")
    return bool(match) and int(match.group(1)) >= 3


def fake_hash(name, password, repo_dir=None):
    return f"{name}:$2y$05$" + base64.b64encode(password.encode()).decode().rstrip("=")


def checkout(test, ls_web_user=" ".join(OLD_SENSORS)):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    with open(os.path.join(REPO_DIR, "env.example"), encoding="utf-8") as handle:
        text = handle.read().replace("WEB_USER=\n", f"WEB_USER={WEB_USER}\n", 1)
    text = text.replace("LS_WEB_USER=\n", f"LS_WEB_USER={ls_web_user}\n", 1)
    with open(os.path.join(tmp.name, ".env"), "w", encoding="utf-8") as handle:
        handle.write(text)
    for folder in ("conf", "cert"):
        os.makedirs(os.path.join(tmp.name, "data", "nginx", folder))
    open(os.path.join(tmp.name, "data", "nginx", "conf", "lswebpasswd"), "w").close()
    for name in ("nginx.crt", "nginx.key"):
        with open(os.path.join(tmp.name, "data", "nginx", "cert", name), "w") as handle:
            handle.write(f"old {name}\n")
    shutil.copytree(os.path.join(REPO_DIR, "installer"), os.path.join(tmp.name, "installer"))
    return tmp.name


class Child:
    """A child of subprocess.Popen: wait() takes the next step, an exit code or an exception to raise."""

    def __init__(self, command, kwargs, steps):
        self.args, self.kwargs, self.steps, self.returncode = command, kwargs, list(steps), None

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        step = self.steps.pop(0) if self.steps else 0
        if isinstance(step, BaseException) or (isinstance(step, type) and issubclass(step, BaseException)):
            raise step
        self.returncode = step
        return step

    def kill(self):
        self.returncode = self.returncode if self.returncode is not None else -9

    def __enter__(self):            # subprocess.call and run use it so
        return self

    def __exit__(self, *_exc):
        return False


def fake_popen(*steps):
    """A Popen that records its children; each child waits through `steps`."""
    children = []

    def popen(command, **kwargs):
        children.append(Child(command, kwargs, steps))
        return children[-1]
    return popen, children


class TTY(io.StringIO):
    """stdin of a person at a terminal."""

    def isatty(self):
        return True


class HelpersTest(unittest.TestCase):
    DOCKERFILE_CERT_DIRECTORY_COPY = re.compile(
        r"(?im)^[ \t]*(?:COPY|ADD)\b[^\n]*(?:^|[\s/])(?:conf/ssl|nginx/ssl)(?:/|\s|$)")

    def test_nginx_dockerfile_check_only_matches_certificate_directories(self):
        check = self.DOCKERFILE_CERT_DIRECTORY_COPY
        self.assertIsNone(check.search("RUN apk add openssl && echo ssl"))
        self.assertIsNone(check.search("RUN printf 'COPY conf/ssl/' > /tmp/example"))
        self.assertIsNone(check.search('RUN echo "ADD nginx/ssl/"'))
        self.assertIsNone(check.search("# no conf/ssl or nginx/ssl is copied"))
        self.assertIsNone(check.search("COPY ssl-assets/ /etc/nginx/"))
        for line in ("COPY conf/ssl/ /etc/nginx/cert/", "COPY dist/conf/ssl /out/cert",
                     "ADD nginx/ssl/ /etc/nginx/cert/", "COPY docker/nginx/ssl /out/cert"):
            with self.subTest(line=line):
                self.assertIsNotNone(check.search(line))

    def test_names_and_passwords(self):
        name = sensors.new_name({"sensor-a-b"})
        self.assertRegex(name, r"^sensor-[a-z]+-[a-z]+$")
        password = sensors.new_password()
        self.assertEqual(len(password), 32)
        self.assertTrue(password.isalnum())
        self.assertEqual(base64.b64decode(sensors.hive_user("sensor-x-y", "pw")).decode(), "sensor-x-y:pw")

    def test_addresses_and_users(self):
        for good in ("192.168.1.10", "fe80::1", "hive.example.org", "sensor1"):
            self.assertEqual(sensors.check_address(good), good)
        for bad in ("", "a b", "a;reboot", "-oProxyCommand=x", "host:22", "x" * 300):
            with self.assertRaises(sensors.SensorsError):
                sensors.check_address(bad)
        sensors.check_user("tpot_admin")
        for bad in ("", "-l", "root;x", "Admin User"):
            with self.assertRaises(sensors.SensorsError):
                sensors.check_user(bad)

    def test_ssh(self):
        self.assertIn("2222", sensors.ssh_command("10.0.0.5", "debian", port=2222))
        with self.assertRaises(sensors.SensorsError):
            sensors.check_port("0")
        command = sensors.ssh_command("10.0.0.5", "debian", "true")
        self.assertEqual(command[:3], ["ssh", "-p", "64295"])
        self.assertIn("BatchMode=yes", command)
        self.assertIn("debian@10.0.0.5", command)
        for code, stderr, expected in ((0, "", "ok"), (255, "debian@10.0.0.5: Permission denied (publickey).", "key"),
                                       (255, "ssh: connect to host 10.0.0.5 port 64295: Connection refused",
                                        "unreachable")):
            fake = mock.Mock(return_value=mock.Mock(returncode=code, stderr=stderr))
            self.assertEqual(sensors.check_ssh("10.0.0.5", "debian", run=fake), expected)

    def test_deploy_command_and_environment(self):
        command = sensors.deploy_command("10.0.0.5", "debian", repo_dir="/r")
        self.assertEqual(command[:2], ["ansible-playbook", "/r/installer/install/deploy.yml"])
        self.assertIn("10.0.0.5,", command)
        self.assertIn("--ask-become-pass", command)
        self.assertNotIn("--ask-become-pass", sensors.deploy_command("10.0.0.5", "debian", become_pass=False))
        env = sensors.deploy_env("dXNlcjpwdw==", "192.168.1.2", repo_dir="/r")
        self.assertEqual((env["myTPOT_HIVE_USER"], env["myTPOT_HIVE_IP"]), ("dXNlcjpwdw==", "192.168.1.2"))
        self.assertEqual(env["ANSIBLE_LOG_PATH"], "/r/data/deploy_sensor.log")

    def test_distribute_asks_for_sudo(self):
        self.assertIn("--ask-become-pass", sensors.distribute_command("/tmp/inv"))
        self.assertNotIn("--ask-become-pass", sensors.distribute_command("/tmp/inv", become_pass=False))

    def test_distribute_hands_the_shipper_release_to_the_play(self):
        command = sensors.distribute_command("/tmp/inv")
        self.assertIn(f"tpot_shipper_since={sensors.SHIPPER_SINCE}", command)
        self.assertEqual(command[command.index(f"tpot_shipper_since={sensors.SHIPPER_SINCE}") - 1], "-e")

    def test_runs_logstash(self):
        self.assertEqual(sensors.SHIPPER_SINCE, "24.04.2")
        for version in ("24.04.1", "24.04", "23.12.1", "24.04.1-rc1", "22.04.0"):
            self.assertTrue(sensors.runs_logstash(version), version)
        for version in ("24.04.2", "24.04.10", "24.4.2", "24.11", "25.01.0", None, "", "dev", "v24.04.1",
                        "sensor-release"):
            self.assertFalse(sensors.runs_logstash(version), version)
        self.assertIn("tpot sensors update", sensors.LOGSTASH_SENSOR)

    def test_inventory(self):
        text = sensors.inventory_text([sensors.Sensor("sensor-a-b", host="10.0.0.5", ssh_user="debian")])
        self.assertIn("sensor-a-b ansible_host=10.0.0.5 ansible_user=debian ansible_port=64295", text)

    def test_certificate_sans(self):
        sans = sensors.parse_sans(OPENSSL_TEXT)
        self.assertEqual(sans, ["IP:192.168.5.15", "DNS:hive.example.org"])
        self.assertTrue(sensors.covers("hive.example.org", sans))
        self.assertFalse(sensors.covers("192.168.64.41", sans))
        command = sensors.renew_command(sans + ["IP:192.168.64.41"], "k", "c")
        self.assertIn("subjectAltName = IP:192.168.5.15,DNS:hive.example.org,IP:192.168.64.41", command)
        newkey = command.index("-newkey")
        self.assertEqual(command[newkey:newkey + 4], P384)
        self.assertIn("-sha384", command)
        self.assertFalse([part for part in command if "rsa:" in part], command)

    @unittest.skipUnless(openssl_3(), "needs OpenSSL 3 or later")
    def test_certificate_with_openssl(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, cert = os.path.join(tmp, "k"), os.path.join(tmp, "c")
            command = sensors.renew_command(["IP:10.1.2.3", "DNS:hive.test"], key, cert)
            self.assertEqual(subprocess.call(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), 0)
            self.assertEqual(sensors.cert_sans(cert), ["IP:10.1.2.3", "DNS:hive.test"])
            text = subprocess.run(["openssl", "x509", "-in", cert, "-noout", "-text"], stdout=subprocess.PIPE,
                                  universal_newlines=True, check=True).stdout
            self.assertIn("P-384", text)
            self.assertIn("ecdsa-with-SHA384", text)

    def test_tpotinit_issues_the_same_key(self):
        # tpotinit's first start and tpot sensors cert --renew issue the certificate of the HIVE alike
        with open(os.path.join(REPO_DIR, "docker", "tpotinit", "dist", "entrypoint.sh"), encoding="utf-8") as handle:
            script = handle.read()
        comment = script.index("# Generating self signed certificate")
        start = script.index("openssl req", comment)
        end = re.compile(r"[^\\]\n").search(script, start).end()
        command = shlex.split(script[start:end].replace("\\\n", " "))
        renew = sensors.renew_command(["IP:10.1.2.3"], "k", "c")
        self.assertIn("-sha384", command)
        newkey = command.index("-newkey")
        self.assertEqual(command[newkey:newkey + 4], P384)
        self.assertFalse([part for part in command if "rsa:" in part], command)
        for option in ("-days", "-subj"):
            self.assertEqual(command[command.index(option) + 1], renew[renew.index(option) + 1], option)

    def test_key_generation_extraction_ignores_an_earlier_openssl_req_comment(self):
        with open(os.path.join(REPO_DIR, "docker", "tpotinit", "dist", "entrypoint.sh"), encoding="utf-8") as handle:
            script = "# Earlier documentation mentions openssl req but is not the key-generation command.\n" + handle.read()
        comment = script.index("# Generating self signed certificate")
        start = script.index("openssl req", comment)
        end = re.compile(r"[^\\]\n").search(script, start).end()
        command = shlex.split(script[start:end].replace("\\\n", " "))
        self.assertIn("-newkey", command)

    def test_status(self):
        def bucket(key, host, ip):
            return {"key": key, "latest": {"hits": {"hits": [{"_source": {
                "@timestamp": "2026-10-04T10:00:00.000Z", "t-pot_hostname": host, "t-pot_ip_ext": ip}}]}}}
        answer = {"aggregations": {"sensors": {"buckets": [bucket("sensor-old-otter", "edge1", "1.2.3.4")]},
                                   "unlinked": {"hosts": {"buckets": [bucket("edge2", "edge2", "5.6.7.8"),
                                                                      bucket("hive", "hive", "9.9.9.9")]}}}}
        status = sensors.parse_status(answer, own_hostname="hive")
        self.assertEqual(status.sensors["sensor-old-otter"].hostname, "edge1")
        self.assertEqual(list(status.unlinked), ["edge2"])
        query = sensors.status_query(7)
        self.assertEqual(query["aggs"]["sensors"]["terms"]["field"], "t-pot_sensor.keyword")
        failing = mock.Mock(side_effect=OSError("connection refused"))
        self.assertIn("cannot be asked", sensors.fetch_status(opener=failing).problem)

    def test_ingest_probe_is_limited_per_address_before_the_login(self):
        # the probe asks for the SENSOR's login after a limit per address, a missing or wrong one gets
        # 401 with a marker of its own (no WWW-Authenticate): an old HIVE answers a plain 401
        conf = os.path.join(REPO_DIR, "docker", "nginx", "dist", "conf")
        with open(os.path.join(conf, "lsweb.conf"), encoding="utf-8") as handle:
            text = handle.read()

        def block(start):
            part = text[text.index(start):]
            return part[:part.index("\n    }\n")]

        self.assertIn("limit_req_zone $binary_remote_addr zone=tpot_ingest:1m rate=1r/s;", text)
        self.assertNotIn("tpot_ingest_all", text)
        probe = block("location = /tpot-ingest {")
        for line in ("limit_req zone=tpot_ingest burst=5 nodelay;", "limit_req_status 429;",
                     'auth_basic "closed site";', "auth_basic_user_file /etc/nginx/lswebpasswd;",
                     "error_page 401 = @tpot_ingest_login;"):
            self.assertIn(line, probe)
        login = block("location @tpot_ingest_login {")
        self.assertIn("more_clear_headers WWW-Authenticate;", login)
        self.assertIn("add_header X-TPot-Ingest login always;", login)
        for name in ("lsweb.conf", "tpotweb.conf"):
            with open(os.path.join(conf, name), encoding="utf-8") as handle:
                server = handle.read()
            self.assertIn("ssl_ecdh_curve X25519MLKEM768:X25519:secp384r1;", server, name)
            self.assertNotIn("ssl_dhparam", server, name)
        self.assertFalse(os.path.exists(os.path.join(conf, "ssl")))
        with open(os.path.join(REPO_DIR, "docker", "nginx", "Dockerfile"), encoding="utf-8") as handle:
            dockerfile = handle.read()
        self.assertIsNone(self.DOCKERFILE_CERT_DIRECTORY_COPY.search(dockerfile))


class CertificateTest(unittest.TestCase):
    """The certificate of the HIVE as the T-Pot Manager reads and shows it."""

    NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def openssl(self, *command):
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, cwd=self.tmp)

    @unittest.skipUnless(openssl_3(), "needs OpenSSL 3 or later")
    def test_cert_info_of_an_ecdsa_self_signed_certificate(self):
        key, crt = os.path.join(self.tmp, "nginx.key"), os.path.join(self.tmp, "nginx.crt")
        self.openssl(*sensors.renew_command(["IP:10.1.2.3", "DNS:hive.test"], key, crt))
        info = sensors.cert_info(crt)
        self.assertEqual(info.key, "ECDSA P-384")
        self.assertTrue(info.self_signed)
        self.assertEqual(info.issuer, SELF_SIGNED)                  # no CN: its RFC 2253 name
        self.assertEqual(info.sans, ["IP:10.1.2.3", "DNS:hive.test"])
        self.assertEqual(info.not_after.utcoffset(), timedelta(0))
        self.assertLessEqual(abs(sensors.days_left(info, datetime.now(timezone.utc)) - 3650), 1)
        self.assertFalse(sensors.suggests_ecdsa(info))

    @unittest.skipUnless(openssl_3(), "needs OpenSSL 3 or later")
    def test_cert_info_of_a_ca_certificate(self):
        # a root of an own CA signs the leaf, nginx.crt holds the leaf and the root after it
        self.openssl("openssl", "req", "-nodes", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                     "-keyout", "root.key", "-out", "root.crt", "-days", "30", "-subj",
                     "/O=Example, Inc./CN=Example Root CA")
        self.openssl("openssl", "req", "-nodes", "-new", "-newkey", "rsa:2048", "-keyout", "nginx.key",
                     "-out", "leaf.csr", "-subj", "/CN=hive.example.org")
        with open(os.path.join(self.tmp, "ext.cnf"), "w") as handle:
            handle.write("subjectAltName = IP:192.168.5.15, DNS:hive.example.org\n")
        self.openssl("openssl", "x509", "-req", "-in", "leaf.csr", "-CA", "root.crt", "-CAkey", "root.key",
                     "-CAcreateserial", "-out", "leaf.crt", "-days", "20", "-extfile", "ext.cnf")
        crt = os.path.join(self.tmp, "nginx.crt")
        with open(crt, "w") as out:
            for name in ("leaf.crt", "root.crt"):
                with open(os.path.join(self.tmp, name), encoding="utf-8") as handle:
                    out.write(handle.read())
        info = sensors.cert_info(crt)
        self.assertFalse(info.self_signed)                          # the root after it does not count
        self.assertEqual(info.issuer, "Example Root CA")
        self.assertEqual(info.key, "RSA 2048")
        self.assertEqual(info.sans, ["IP:192.168.5.15", "DNS:hive.example.org"])
        self.assertIn(sensors.days_left(info, datetime.now(timezone.utc)), (19, 20))
        self.assertFalse(sensors.suggests_ecdsa(info))              # a CA's RSA is not T-Pot's to change
        self.assertTrue(sensors.cert_info(os.path.join(self.tmp, "root.crt")).self_signed)

    def test_cert_info_of_an_rsa_certificate(self):
        crt = os.path.join(self.tmp, "nginx.crt")
        with open(crt, "w") as handle:
            handle.write("a certificate\n")
        run, calls = fake_openssl(RSA_TEXT)
        info = sensors.cert_info(crt, run=run)
        self.assertEqual(info, sensors.CertInfo(key="RSA 2048", self_signed=True, issuer=SELF_SIGNED,
                                                not_after=datetime(2036, 11, 7, 22, 19, 59, tzinfo=timezone.utc),
                                                sans=["IP:192.168.5.15", "DNS:hive.example.org"]))
        self.assertTrue(sensors.suggests_ecdsa(info))
        self.assertEqual(sensors.cert_sans(crt, run=run), ["IP:192.168.5.15", "DNS:hive.example.org"])
        # the first certificate alone, as the SENSOR checks it: never the whole file, never a partial chain
        verify = calls[2]
        first = verify[-1]
        self.assertEqual(verify, ["openssl", "verify", "-no-CApath", "-no-CAstore", "-no_check_time",
                                  "-CAfile", first, first])
        self.assertNotEqual(first, crt)
        self.assertEqual(calls[1], ["openssl", "x509", "-in", crt, "-out", first])
        self.assertFalse(os.path.exists(first))                     # its temporary copy is gone
        run, _calls = fake_openssl(RSA_TEXT, verify=2)              # verification failed: issued by a CA
        info = sensors.cert_info(crt, run=run)
        self.assertFalse(info.self_signed)
        self.assertFalse(sensors.suggests_ecdsa(info))

    def test_cert_info_none_for_a_missing_or_empty_file(self):
        never = mock.Mock(side_effect=AssertionError("openssl is not asked"))
        crt = os.path.join(self.tmp, "nginx.crt")
        self.assertIsNone(sensors.cert_info(crt, run=never))
        self.assertEqual(sensors.cert_sans(crt, run=never), [])
        open(crt, "w").close()
        self.assertIsNone(sensors.cert_info(crt, run=never))
        self.assertEqual(sensors.cert_sans(crt, run=never), [])

    def test_cert_info_raises_for_garbage(self):
        crt = os.path.join(self.tmp, "nginx.crt")
        with open(crt, "w") as handle:
            handle.write("old nginx.crt\n")
        failing = mock.Mock(return_value=mock.Mock(returncode=1, stdout="",
                                                   stderr="Could not find or decode certificate\nmore\n"))
        with self.assertRaisesRegex(sensors.SensorsError, "Could not find or decode certificate$"):
            sensors.cert_info(crt, run=failing)
        for text in (RSA_TEXT.replace("notAfter=Nov  7 22:19:59 2036 GMT", "notAfter=soon"),
                     RSA_TEXT.replace("notAfter=", "nothing=")):
            with self.subTest(text=text[:60]), self.assertRaises(sensors.SensorsError):
                sensors.cert_info(crt, run=fake_openssl(text)[0])
        with self.assertRaises(sensors.SensorsError):               # verify itself could not run
            sensors.cert_info(crt, run=fake_openssl(RSA_TEXT, verify=1)[0])
        with self.assertRaises(sensors.SensorsError):
            sensors.cert_info(crt, run=mock.Mock(side_effect=FileNotFoundError("openssl")))
        if shutil.which("openssl"):                                 # any openssl, LibreSSL too
            with self.assertRaises(sensors.SensorsError):
                sensors.cert_info(crt)
        os.unlink(crt)
        os.symlink(os.path.join(self.tmp, "gone.crt"), crt)        # a link to nothing is no missing file
        with self.assertRaises(sensors.SensorsError):
            sensors.cert_info(crt, run=mock.Mock(side_effect=AssertionError("openssl is not asked")))
        os.unlink(crt)
        os.mkdir(crt)                                               # nor is a folder, even an empty one
        with self.assertRaisesRegex(sensors.SensorsError, "it is no file"):
            sensors.cert_info(crt, run=mock.Mock(side_effect=AssertionError("openssl is not asked")))

    def test_cert_info_reports_temporary_directory_errors_as_sensor_errors(self):
        crt = os.path.join(self.tmp, "nginx.crt")
        with open(crt, "w") as handle:
            handle.write("certificate\n")
        run, _calls = fake_openssl(RSA_TEXT)
        with mock.patch.object(sensors.tempfile, "TemporaryDirectory", side_effect=OSError("permission denied")):
            with self.assertRaisesRegex(sensors.SensorsError, r"cannot read the certificate .*permission denied"):
                sensors.cert_info(crt, run=run)

    def test_cert_summary_lines(self):
        sans = ["IP:192.168.5.15", "DNS:hive.example.org"]

        def info(days, self_signed=True, key="ECDSA P-384"):
            return sensors.CertInfo(key=key, self_signed=self_signed,
                                    issuer=SELF_SIGNED if self_signed else "Example Root CA",
                                    not_after=self.NOW + timedelta(days=days), sans=sans)

        covers = "covers IP:192.168.5.15, DNS:hive.example.org"
        self.assertEqual(sensors.cert_summary(info(3650), self.NOW, RENEW), [
            ("info", f"Certificate: ECDSA P-384, self-signed, valid until 2036-11-17, {covers}")])
        self.assertEqual(sensors.cert_summary(info(29), self.NOW, RENEW)[1:], [
            ("warn", f"The certificate expires in 29 days (2026-12-19); renew it: {RENEW}.")])
        self.assertEqual(len(sensors.cert_summary(info(30), self.NOW, RENEW)), 1)
        self.assertEqual(sensors.cert_summary(info(-1), self.NOW, RENEW)[1:], [
            ("warn", f"The certificate expired on 2026-11-19; renew it: {RENEW}.")])
        self.assertEqual(sensors.cert_summary(info(10, self_signed=False), self.NOW, RENEW), [
            ("info", f"Certificate: ECDSA P-384, issued by Example Root CA, valid until 2026-11-30, {covers}"),
            ("warn", "The certificate expires in 10 days (2026-11-30); renew it at Example Root CA, "
                     "then tpot sensors cert --distribute.")])
        page = "Renew certificate below"
        self.assertEqual(sensors.cert_summary(info(10, key="RSA 8192"), self.NOW, page), [
            ("info", f"Certificate: RSA 8192, self-signed, valid until 2026-11-30, {covers}"),
            ("warn", f"The certificate expires in 10 days (2026-11-30); renew it: {page}."),
            ("hint", f"An ECDSA certificate costs this HIVE far less CPU per connection than RSA: {page}.")])
        self.assertEqual([level for level, _text in sensors.cert_summary(
            info(3650, self_signed=False, key="RSA 4096"), self.NOW, RENEW)], ["info"])
        self.assertEqual(sensors.cert_summary(None, self.NOW, RENEW, path="/data/nginx/cert/nginx.crt"), [
            ("warn", "Certificate: none (/data/nginx/cert/nginx.crt is missing or empty); "
                     "tpot sensors cert --renew issues one.")])
        self.assertTrue(sensors.cert_summary(None, self.NOW, RENEW)[0][1].startswith("Certificate: none ("))
        self.assertEqual(sensors.days_left(info(0), self.NOW + timedelta(seconds=1)), -1)
        self.assertEqual(sensors.ca_renew_refusal(info(10, self_signed=False), "/data/nginx/cert"),
                         "The certificate of this HIVE is issued by Example Root CA; T-Pot does not replace it. "
                         "Renew it at that CA, or move nginx.crt and nginx.key (/data/nginx/cert) aside, then "
                         "tpot sensors cert --renew issues a self-signed one (--add names its addresses).")

    def test_nginx_reload(self):
        from tpotctl import ops
        calls = []

        def docker(*answers):
            def run(command, **_kwargs):
                calls.append(command)
                code, out, err = answers[len(calls) - 1]
                return mock.Mock(returncode=code, stdout=out, stderr=err)
            return run

        with mock.patch.object(ops, "linux_host", return_value=False):
            self.assertEqual(ops.nginx_reload(run=docker()), "not running")
        with mock.patch.object(ops, "linux_host", return_value=True), \
                mock.patch.object(ops.shutil, "which", return_value=None):
            self.assertEqual(ops.nginx_reload(run=docker()), "not running")
        self.assertEqual(calls, [])
        inspect = ["docker", "inspect", "-f", "{{.State.Running}}", "nginx"]
        test = ["docker", "exec", "nginx", "nginx", "-t", "-q"]
        hup = ["docker", "kill", "--signal", "HUP", "nginx"]
        with mock.patch.object(ops, "linux_host", return_value=True), \
                mock.patch.object(ops.shutil, "which", return_value="/usr/bin/docker"):
            for answer in ((1, "", "Error: No such object: nginx"), (0, "false\n", "")):
                calls.clear()
                self.assertEqual(ops.nginx_reload(run=docker(answer)), "not running")
                self.assertEqual(calls, [inspect])
            calls.clear()
            emerg = 'nginx: [emerg] cannot load certificate "/etc/nginx/cert/nginx.crt"'
            refused = (1, "", emerg + "\nnginx: configuration file test failed")
            with self.assertRaises(ops.OpsError) as caught:
                ops.nginx_reload(run=docker((0, "true\n", ""), refused))
            self.assertEqual(str(caught.exception), emerg)          # its first line, for M-reload-fail
            self.assertEqual(calls, [inspect, test])                 # no HUP for a configuration nginx refuses
            calls.clear()
            self.assertEqual(ops.nginx_reload(run=docker((0, "true\n", ""), (0, "", ""), (0, "nginx\n", ""))),
                             "reloaded")
            self.assertEqual(calls, [inspect, test, hup])

    def test_nginx_reload_distinguishes_missing_container_from_inspect_failure(self):
        from tpotctl import ops
        with mock.patch.object(ops, "linux_host", return_value=True), \
                mock.patch.object(ops.shutil, "which", return_value="/usr/bin/docker"):
            self.assertEqual(ops.nginx_reload(run=lambda *_args, **_kwargs:
                                              mock.Mock(returncode=1, stdout="",
                                                        stderr="Error: No such object: nginx")),
                             "not running")
            with self.assertRaisesRegex(ops.OpsError, "Cannot connect to the Docker daemon"):
                ops.nginx_reload(run=lambda *_args, **_kwargs:
                                 mock.Mock(returncode=1, stdout="",
                                           stderr="Cannot connect to the Docker daemon"))


class HeartbeatTest(unittest.TestCase):
    NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc).timestamp()

    def heartbeat(self, age=0, version="new-release"):
        self.assertTrue(hasattr(sensors, "Heartbeat"), "heartbeat model is missing")
        return sensors.Heartbeat(received_at=self.NOW - age, version=version,
                                 sensor_at="2020-01-01T00:00:00Z", started_at="2019-01-01T00:00:00Z")

    def observed(self, heartbeat=None, sensor=None, takeable=False):
        self.assertTrue(hasattr(sensors, "observe"), "shared sensor state calculation is missing")
        status = sensors.Status(sensors={"sensor-one": sensors.Seen("2026-11-01T12:00:00Z")})
        if heartbeat:
            status.heartbeats["sensor-one"] = heartbeat
        return sensors.observe(sensor or sensors.Sensor("sensor-one"), status, "new-release",
                               takeable=takeable, now=self.NOW)

    def test_online_uses_receipt_time_and_exactly_180_seconds_is_offline(self):
        for age, want in ((0, "online"), (179.999, "online"), (180, "offline"), (900, "offline")):
            with self.subTest(age=age):
                observed = self.observed(self.heartbeat(age))
                self.assertEqual(observed.state, want)
                self.assertEqual(observed.last_event, "2026-11-01T12:00:00Z")

    def test_attacks_without_heartbeat_do_not_imply_online(self):
        self.assertEqual(self.observed().state, "unknown")

    def test_access_and_takeover_have_priority_over_heartbeat(self):
        sensor = sensors.Sensor("sensor-one", access=False)
        self.assertEqual(self.observed(self.heartbeat(), sensor).state, "no access")
        self.assertEqual(self.observed(self.heartbeat(), sensor, takeable=True).state, "to take over")

    def test_heartbeat_wins_over_cache_and_missing_heartbeat_version_stays_unknown(self):
        sensor = sensors.Sensor("sensor-one", version="cached")
        observed = self.observed(self.heartbeat(version="old-release"), sensor)
        self.assertEqual(observed.version, "old-release")
        self.assertTrue(observed.behind)
        self.assertFalse(observed.cached)
        unknown = self.observed(self.heartbeat(version=""), sensor)
        self.assertEqual(unknown.version, "")
        self.assertFalse(unknown.behind)
        self.assertEqual(unknown.state, "online")

    def test_verified_cache_keeps_its_date_without_claiming_online(self):
        self.assertIn("version_checked_at", sensors.Sensor.__dataclass_fields__)
        sensor = sensors.Sensor("sensor-one", version="old-release", version_checked_at="2026-10-01T10:00:00Z")
        observed = self.observed(sensor=sensor)
        self.assertEqual((observed.state, observed.version, observed.checked_at),
                         ("unknown", "old-release", "2026-10-01T10:00:00Z"))
        self.assertTrue(observed.cached)
        self.assertTrue(observed.behind)

    def test_legacy_cache_without_check_date_is_not_a_verified_version(self):
        observed = self.observed(sensor=sensors.Sensor("sensor-one", version="old-hive-version"))
        self.assertEqual(observed.version, "old-hive-version")
        self.assertEqual(observed.checked_at, "")
        self.assertTrue(observed.cached)
        self.assertFalse(observed.behind)

    def test_host_falls_back_to_observed_hostname_but_ssh_address_wins(self):
        heartbeat = self.heartbeat()
        heartbeat.hostname = "observed-host"
        self.assertEqual(self.observed(heartbeat).host, "observed-host")
        self.assertEqual(self.observed(heartbeat, sensors.Sensor("sensor-one", host="ssh-alias")).host, "ssh-alias")

    def refused(self, refused_age, heartbeat=None, sensor=None):
        status = sensors.Status(refused={"sensor-one": self.NOW - refused_age})
        if heartbeat:
            status.heartbeats["sensor-one"] = heartbeat
        return sensors.observe(sensor or sensors.Sensor("sensor-one"), status, "new-release", now=self.NOW)

    def test_refused_newer_than_heartbeat(self):
        observed = self.refused(10, self.heartbeat(60, version="new-release"))
        self.assertEqual(observed.state, "refused")
        self.assertTrue(observed.behind)
        self.assertEqual(observed.version, "new-release")

    def test_refused_older_than_window(self):
        observed = self.refused(sensors.REFUSED_WINDOW, self.heartbeat(1000))
        self.assertEqual(observed.state, "offline")
        self.assertFalse(observed.behind)
        self.assertEqual(self.refused(sensors.REFUSED_WINDOW).state, "unknown")
        self.assertEqual(self.refused(sensors.REFUSED_WINDOW - 0.001).state, "refused")

    def test_heartbeat_after_refusal(self):
        observed = self.refused(10, self.heartbeat(5))
        self.assertEqual(observed.state, "online")
        self.assertFalse(observed.behind)
        self.assertEqual(self.refused(10, self.heartbeat(10)).state, "online")

    def test_refused_without_heartbeat(self):
        observed = self.refused(0)
        self.assertEqual(observed.state, "refused")
        self.assertTrue(observed.behind)
        self.assertEqual(self.refused(-60).state, "refused")             # clock step: still in the window
        sensor = sensors.Sensor("sensor-one", access=False)
        self.assertEqual(self.refused(0, sensor=sensor).state, "no access")

    @unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
    def test_refused_row_says_update_needed(self):
        observed = self.refused(0)
        (_name, cells), = sensors.table_rows([(sensors.Sensor("sensor-one"), "sensor-one", observed)])
        self.assertTrue(cells[5].plain.endswith(" update needed (events refused)"))
        self.assertEqual(cells[2].plain, "unknown (behind)")

    def test_cache_date_round_trip_and_legacy_registry_compatibility(self):
        repo = checkout(self)
        registry = sensors.Registry(repo)
        sensor = registry.get("sensor-old-otter")
        self.assertTrue(hasattr(sensor, "version_checked_at"))
        self.assertEqual(sensor.version_checked_at, "")
        sensor.version, sensor.version_checked_at = "sensor-release", "2026-11-01T12:00:00Z"
        registry.record(sensor)
        again = sensors.Registry(repo).get(sensor.name)
        self.assertEqual((again.version, again.version_checked_at), ("sensor-release", "2026-11-01T12:00:00Z"))

    def test_invalid_cache_dates_are_reported_before_rendering(self):
        repo = checkout(self)
        registry = sensors.Registry(repo)
        for value in (7, "2026-11-01", "not a date"):
            with self.subTest(value=value):
                registry.get("sensor-old-otter").version_checked_at = value
                registry.save()
                with self.assertRaises(sensors.SensorsError):
                    sensors.Registry(repo)


class HeartbeatApiTest(unittest.TestCase):
    @staticmethod
    def hit(name="sensor-one"):
        return {"_id": name, "sort": [name], "_source": {
            "type": "TPotSensorHeartbeat", "t-pot_sensor": name, "t-pot_hostname": "edge",
            "@timestamp": "2020-01-01T10:00:00Z",
            "heartbeat": {"version": "old-release", "sequence": 1,
                          "started_at": "2020-01-01T09:00:00Z", "received_at": "2026-11-01T12:00:00Z"}}}

    @staticmethod
    def answer(hits):
        return {"timed_out": False, "_shards": {"failed": 0}, "hits": {"hits": hits}}

    @staticmethod
    def events():
        return {"aggregations": {"sensors": {"buckets": [
            {"key": "sensor-one", "latest": {"hits": {"hits": [
                {"_source": {"@timestamp": "2026-11-01T12:00:00Z", "t-pot_hostname": "edge"}}]}}}]},
            "unlinked": {"hosts": {"buckets": []}}}}

    def fetch(self, heartbeat, events=None):
        def opener(request, timeout):
            data = self.events() if "/logstash-*/" in request.full_url else heartbeat
            if events is not None and "/logstash-*/" in request.full_url:
                data = events
            if isinstance(data, Exception):
                raise data
            return io.BytesIO(json.dumps(data).encode())
        return sensors.fetch_status(url="http://es.test", opener=opener)

    def test_receipt_time_and_sensor_time_are_kept_separate(self):
        status = self.fetch(self.answer([self.hit()]))
        self.assertEqual(status.problem, "")
        self.assertIn("sensor-one", status.heartbeats)
        heartbeat = status.heartbeats["sensor-one"]
        self.assertEqual(heartbeat.received_at, 1793534400)
        self.assertEqual((heartbeat.sensor_at, heartbeat.version, heartbeat.hostname),
                         ("2020-01-01T10:00:00Z", "old-release", "edge"))
        self.assertEqual(status.sensors["sensor-one"].last, "2026-11-01T12:00:00Z")

    @staticmethod
    def refused_hit(name="sensor-two", last_at="2026-11-01T12:01:00.123Z"):
        return {"_id": name, "sort": [name], "_source": {"t-pot_sensor": name, "refused": {"last_at": last_at}}}

    def test_refused_only_doc(self):
        self.assertIsNone(sensors._heartbeat(self.refused_hit()))
        both = self.hit()
        both["_source"]["refused"] = {"last_at": "2026-11-01T12:05:00+00:00"}
        status = self.fetch(self.answer([self.hit("sensor-a"), self.refused_hit(), both]))
        self.assertEqual(status.problem, "")
        self.assertEqual(set(status.heartbeats), {"sensor-a", "sensor-one"})
        self.assertEqual(status.refused, {"sensor-two": 1793534460.123, "sensor-one": 1793534700})

    def test_malformed_refusal_is_reported(self):
        mutations = (
            lambda hit: hit.update(_id="other"),
            lambda hit: hit["_source"].update({"t-pot_sensor": "bad name"}),
            lambda hit: hit["_source"].update(refused=None),
            lambda hit: hit["_source"].update(refused="2026-11-01T12:00:00Z"),
            lambda hit: hit["_source"].update(refused={}),
            lambda hit: hit["_source"]["refused"].update(last_at="2026-11-01T12:00:00"),
            lambda hit: hit["_source"]["refused"].update(last_at=5),
            lambda hit: hit["_source"].pop("refused"),
        )
        for mutate in mutations:
            hit = self.refused_hit("sensor-one")
            mutate(hit)
            with self.subTest(hit=hit):
                status = self.fetch(self.answer([hit]))
                self.assertIn("heartbeat", status.problem.lower())
                self.assertEqual((status.heartbeats, status.refused), ({}, {}))
        both = self.hit()
        both["_source"]["refused"] = {"last_at": "not a date"}
        self.assertIn("heartbeat", self.fetch(self.answer([both])).problem.lower())

    def test_failed_heartbeat_is_visible_without_losing_attack_events(self):
        status = self.fetch(OSError("heartbeat unavailable"))
        self.assertIn("heartbeat unavailable", status.problem)
        self.assertIn("sensor-one", status.sensors)
        self.assertEqual(status.heartbeats, {})

    def test_failed_events_do_not_hide_a_working_heartbeat(self):
        status = self.fetch(self.answer([self.hit()]), events=OSError("events unavailable"))
        self.assertIn("events unavailable", status.problem)
        self.assertIn("sensor-one", status.heartbeats)

    def test_nullable_heartbeat_metadata_does_not_hide_other_sensors(self):
        nullable = self.hit("sensor-two")
        nullable["_source"].update({"t-pot_hostname": None, "t-pot_ip_ext": None, "t-pot_ip_int": None})
        status = self.fetch(self.answer([self.hit(), nullable]))
        self.assertEqual(status.problem, "")
        self.assertEqual(set(status.heartbeats), {"sensor-one", "sensor-two"})
        heartbeat = status.heartbeats["sensor-two"]
        self.assertEqual((heartbeat.hostname, heartbeat.ip_ext, heartbeat.ip_int), ("", "", ""))
        self.assertEqual(status.heartbeats["sensor-one"].hostname, "edge")

    def test_missing_index_is_requested_as_optional_and_has_no_heartbeats(self):
        requests = []
        def opener(request, timeout):
            requests.append(request.full_url)
            return io.BytesIO(json.dumps(self.events() if "/logstash-*/" in request.full_url
                                         else self.answer([])).encode())
        status = sensors.fetch_status(url="http://es.test", opener=opener)
        self.assertEqual(status.problem, "")
        self.assertEqual(status.heartbeats, {})
        self.assertTrue(any("/tpot-sensor-status/_search?ignore_unavailable=true" in url for url in requests))

    def test_paginate_beyond_one_thousand_without_searching_history(self):
        requests = []
        hits = [self.hit(f"sensor-{i:04d}") for i in range(1001)]
        def opener(request, timeout):
            query = json.loads(request.data)
            requests.append((request.full_url, query))
            if "/logstash-*/" in request.full_url:
                return io.BytesIO(json.dumps(self.events()).encode())
            self.assertEqual(request.full_url, "http://es.test/tpot-sensor-status/_search?ignore_unavailable=true")
            self.assertEqual(query["sort"], [{"t-pot_sensor": "asc"}])
            self.assertEqual(query["size"], 1000)
            after = query.get("search_after")
            self.assertIn(after, (None, ["sensor-0999"]))
            return io.BytesIO(json.dumps(self.answer(hits[:1000] if after is None else hits[1000:])).encode())
        status = sensors.fetch_status(url="http://es.test", opener=opener)
        self.assertEqual(status.problem, "")
        self.assertEqual(len(status.heartbeats), 1001)
        self.assertIn("sensor-1000", status.heartbeats)
        self.assertEqual(len(requests), 3)

    def test_malformed_heartbeat_is_not_silently_presented_as_missing(self):
        mutations = (
            lambda hit: hit.update(_id="other"),
            lambda hit: hit["_source"]["heartbeat"].update(received_at="2026-11-01T12:00:00"),
            lambda hit: hit["_source"]["heartbeat"].update(received_at="not a date"),
            lambda hit: hit["_source"]["heartbeat"].update(version=5),
            lambda hit: hit["_source"]["heartbeat"].update(sequence=True),
            lambda hit: hit["_source"].update(heartbeat=None),
            lambda hit: hit["_source"].update({"t-pot_ip_ext": []}),
            lambda hit: hit["_source"].update({"t-pot_ip_ext": False}),
            lambda hit: hit["_source"].update({"t-pot_ip_ext": 0}),
        )
        for mutate in mutations:
            hit = self.hit()
            mutate(hit)
            with self.subTest(hit=hit):
                status = self.fetch(self.answer([hit]))
                self.assertIn("heartbeat", status.problem.lower())
                self.assertEqual(status.heartbeats, {})
                self.assertIn("sensor-one", status.sensors)
        for answer in ([], {}, {"hits": None}, dict(self.answer([]), timed_out=True),
                       dict(self.answer([]), _shards={"failed": 1})):
            with self.subTest(answer=answer):
                self.assertTrue(self.fetch(answer).problem)

    def test_invalid_events_response_is_reported_and_heartbeat_still_available(self):
        for answer in ([], {"aggregations": []}, {"aggregations": {"sensors": {"buckets": [None]}}}):
            with self.subTest(answer=answer):
                status = self.fetch(self.answer([self.hit()]), events=answer)
                self.assertIn("events", status.problem.lower())
                self.assertIn("sensor-one", status.heartbeats)

    def test_invalid_event_fields_report_a_problem_instead_of_crashing_a_table(self):
        answer = self.events()
        answer["aggregations"]["sensors"]["buckets"][0]["latest"]["hits"]["hits"][0]["_source"]["@timestamp"] = []
        status = self.fetch(self.answer([self.hit()]), events=answer)
        self.assertIn("events", status.problem.lower())
        self.assertIn("sensor-one", status.heartbeats)

    def test_nullable_legacy_metadata_is_missing_not_a_failed_event_query(self):
        answer = self.events()
        answer["aggregations"]["sensors"]["buckets"][0]["latest"]["hits"]["hits"][0]["_source"].update(
            {"t-pot_hostname": None, "t-pot_ip_ext": None, "t-pot_ip_int": None})
        status = self.fetch(self.answer([self.hit()]), events=answer)
        self.assertEqual(status.problem, "")
        self.assertEqual(status.sensors["sensor-one"].hostname, "")

    def test_remove_deletes_only_the_named_status_after_revoking_access(self):
        self.assertTrue(hasattr(sensors, "remove"))
        repo = checkout(self)
        registry = sensors.Registry(repo)
        requests = []
        def opener(request, timeout):
            self.assertNotIn("sensor-old-otter", sensors.Registry(repo).records)
            requests.append((request.method, request.full_url))
            return io.BytesIO(b'{"result":"deleted"}')
        sensors.remove(registry, "sensor-old-otter", url="http://es.test", opener=opener)
        self.assertEqual(requests, [("DELETE", "http://es.test/tpot-sensor-status/_doc/sensor-old-otter")])

    def test_failed_status_cleanup_reports_partial_removal_not_success(self):
        self.assertTrue(hasattr(sensors, "remove"))
        repo = checkout(self)
        def opener(request, timeout):
            raise OSError("connection refused")
        with self.assertRaisesRegex(sensors.SensorsError, "access.*removed.*heartbeat"):
            sensors.remove(sensors.Registry(repo), "sensor-old-otter", opener=opener)
        self.assertNotIn("sensor-old-otter", sensors.Registry(repo).records)

    def test_status_already_absent_is_ok_but_other_404_is_not(self):
        self.assertTrue(hasattr(sensors, "delete_status"))
        for payload, okay in (({"result": "not_found"}, True),
                              ({"error": {"type": "index_not_found_exception"}}, True),
                              ({"error": "wrong endpoint"}, False)):
            def opener(request, timeout):
                raise urllib.error.HTTPError(request.full_url, 404, "Not found", {},
                                             io.BytesIO(json.dumps(payload).encode()))
            with self.subTest(payload=payload):
                if okay:
                    sensors.delete_status("sensor-one", opener=opener)
                else:
                    with self.assertRaises(sensors.SensorsError):
                        sensors.delete_status("sensor-one", opener=opener)


class VersionReadTest(unittest.TestCase):
    def test_reads_sensor_checkout_over_its_ssh_port(self):
        self.assertTrue(hasattr(sensors, "read_version"))
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            self.assertEqual(kwargs["timeout"], 15)
            return subprocess.CompletedProcess(command, 0, stdout="sensor-release\n", stderr="")
        self.assertEqual(sensors.read_version("edge", "debian", port=2222, run=run), "sensor-release")
        self.assertEqual(commands[0][:3], ["ssh", "-p", "2222"])
        self.assertEqual(commands[0][-2:], ["debian@edge", 'cat -- "$HOME/tpotce/version"'])

    def test_failed_missing_or_invalid_version_is_explicit(self):
        self.assertTrue(hasattr(sensors, "read_version"))
        for code, output in ((1, ""), (0, ""), (0, "release\ninjected"), (0, "../bad")):
            with self.subTest(code=code, output=output):
                def run(command, **kwargs):
                    return subprocess.CompletedProcess(command, code, stdout=output, stderr="unavailable")
                with self.assertRaises(sensors.SensorsError):
                    sensors.read_version("edge", "debian", run=run)
        with self.assertRaises(sensors.SensorsError):
            sensors.read_version("edge", "debian", run=mock.Mock(side_effect=subprocess.TimeoutExpired("ssh", 15)))
        with self.assertRaises(sensors.SensorsError):
            sensors.read_version("edge", "debian", run=mock.Mock(
                side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")))


class RegistryTest(unittest.TestCase):

    def setUp(self):
        self.repo = checkout(self)
        self.lswebpasswd = os.path.join(self.repo, "data", "nginx", "conf", "lswebpasswd")
        self.inode = os.stat(self.lswebpasswd).st_ino

    def registry(self):
        return sensors.Registry(self.repo, hasher=fake_hash)

    def test_migration_from_ls_web_user(self):
        registry = self.registry()
        self.assertEqual(registry.migrated, ["sensor-old-lynx", "sensor-old-otter"])
        with open(os.path.join(self.repo, "data", "sensors.json")) as handle:
            saved = json.load(handle)
        self.assertEqual([s["name"] for s in saved["sensors"]], ["sensor-old-lynx", "sensor-old-otter"])
        self.assertTrue(all(s["source"] == "migrated" for s in saved["sensors"]))
        self.assertEqual(self.registry().migrated, [])            # only once

    def test_grant_record_revoke(self):
        registry = self.registry()
        self.assertIn("right away", registry.grant("sensor-new-fox", "pw"))
        registry.record(sensors.Sensor("sensor-new-fox", host="10.0.0.5", ssh_user="debian",
                                       hive_address="192.168.1.2", source="deployed"))
        again = self.registry()
        self.assertEqual(again.get("sensor-new-fox").host, "10.0.0.5")
        self.assertEqual(len(again.entries()), 3)
        with open(self.lswebpasswd) as handle:
            self.assertIn(fake_hash("sensor-new-fox", "pw"), handle.read())
        self.assertEqual(os.stat(self.lswebpasswd).st_ino, self.inode, "lswebpasswd has to keep its inode")
        again.revoke("sensor-old-otter")
        names = [u.name for u in self.registry().entries()]
        self.assertEqual(names, ["sensor-old-lynx", "sensor-new-fox"])
        self.assertNotIn("sensor-old-otter", [s.name for s in self.registry().sensors()])
        self.assertEqual(self.registry().hive_addresses(), ["192.168.1.2"])

    def test_refusals(self):
        registry = self.registry()
        with self.assertRaises(sensors.SensorsError):
            registry.grant("sensor-old-otter", "pw")
        with self.assertRaises(sensors.SensorsError):
            registry.revoke("sensor-nobody")
        env = EnvFile(os.path.join(self.repo, ".env"))
        env.set("TPOT_TYPE", "SENSOR")
        env.save()
        with self.assertRaises(sensors.SensorsError):
            self.registry()

    def test_access_flag_for_a_record_without_entry(self):
        registry = self.registry()
        registry.record(sensors.Sensor("sensor-gone-owl", host="10.0.0.9", ssh_user="debian", source="deployed"))
        self.assertFalse(self.registry().get("sensor-gone-owl").access)

    def test_renew_keeps_the_old_certificate(self):
        registry = self.registry()

        def fake_openssl(command, **_kwargs):
            for flag in ("-keyout", "-out"):
                with open(command[command.index(flag) + 1], "w") as handle:
                    handle.write("new\n")
            return mock.Mock(returncode=0, stderr="")
        stamp = sensors.renew_cert(registry, ["IP:10.0.0.1"], run=fake_openssl)
        with open(registry.cert) as handle:
            self.assertEqual(handle.read(), "new\n")
        with open(f"{registry.cert}.bak-{stamp}") as handle:
            self.assertEqual(handle.read(), "old nginx.crt\n")
        failing = mock.Mock(return_value=mock.Mock(returncode=1, stderr="boom"))
        with self.assertRaises(sensors.SensorsError):
            sensors.renew_cert(registry, ["IP:10.0.0.1"], run=failing)

    def test_renew_preserves_mode_and_uses_the_unprivileged_cli_owner(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            key, crt = os.path.join(tmp, "nginx.key"), os.path.join(tmp, "nginx.crt")
            for path, content, mode in ((key, "old key\n", 0o600), (crt, "old certificate\n", 0o640)):
                with open(path, "w") as handle:
                    handle.write(content)
                os.chmod(path, mode)
            before = {path: os.stat(path) for path in (key, crt)}
            registry = SimpleNamespace(key=key, cert=crt)

            def fake_openssl(command, **_kwargs):
                for flag in ("-keyout", "-out"):
                    with open(command[command.index(flag) + 1], "w") as handle:
                        handle.write("new\n")
                return mock.Mock(returncode=0, stderr="")

            stamp = sensors.renew_cert(registry, ["IP:10.0.0.1"], run=fake_openssl)
            for path in (key, crt):
                renewed, previous = os.stat(path), before[path]
                self.assertEqual(stat.S_IMODE(renewed.st_mode), stat.S_IMODE(previous.st_mode), path)
                self.assertEqual((renewed.st_uid, renewed.st_gid), (os.geteuid(), os.getegid()), path)
                self.assertEqual((previous.st_uid, previous.st_gid), (os.geteuid(), os.getegid()), path)
                backup = os.stat(f"{path}.bak-{stamp}")
                self.assertEqual((backup.st_uid, backup.st_gid, stat.S_IMODE(backup.st_mode)),
                                 (previous.st_uid, previous.st_gid, stat.S_IMODE(previous.st_mode)), path)

    def test_link_hostnames(self):
        registry = self.registry()
        status = sensors.Status(sensors={"sensor-old-otter": sensors.Seen("2026-10-04T10:00:00Z", "edge1")})
        sensors.link_hostnames(registry, status)
        self.assertEqual(self.registry().get("sensor-old-otter").hostname, "edge1")


OTTER = "sensor-old-otter:$apr1$abc$def"
LYNX = "sensor-old-lynx:$apr1$ghi$jkl"


class FileOnlyTest(unittest.TestCase):
    """Sensors of lswebpasswd and lswebpasswd.lost that LS_WEB_USER does not name."""

    def setUp(self):
        # sensors.json knows both sensors, LS_WEB_USER is empty: tpotinit kept their lines in .lost
        self.repo = checkout(self)
        sensors.Registry(self.repo, hasher=fake_hash)
        env = EnvFile(os.path.join(self.repo, ".env"))
        env.set("LS_WEB_USER", "")
        env.save()
        self.lswebpasswd = os.path.join(self.repo, "data", "nginx", "conf", "lswebpasswd")
        with open(self.lswebpasswd + ".lost", "w") as handle:
            handle.write(f"{OTTER}\n{LYNX}\n")

    def registry(self):
        return sensors.Registry(self.repo, hasher=fake_hash)

    def read(self, path):
        with open(path) as handle:
            return handle.read()

    def test_offered_and_taken_over(self):
        registry = self.registry()
        self.assertEqual([(e.name, e.source) for e in registry.file_only()],
                         [("sensor-old-otter", "lswebpasswd.lost"), ("sensor-old-lynx", "lswebpasswd.lost")])
        self.assertFalse(any(s.access for s in registry.sensors()))
        inode = os.stat(self.lswebpasswd).st_ino
        self.assertIn("right away", registry.take_over(["sensor-old-otter", "sensor-old-lynx"]))
        again = self.registry()
        self.assertEqual([u.name for u in again.entries()], ["sensor-old-otter", "sensor-old-lynx"])
        self.assertTrue(all(s.access for s in again.sensors()))
        self.assertEqual(self.read(self.lswebpasswd), f"{OTTER}\n{LYNX}\n")
        self.assertEqual(os.stat(self.lswebpasswd).st_ino, inode)
        self.assertEqual(self.read(self.lswebpasswd + ".lost"), "")
        self.assertEqual(again.file_only(), [])

    def test_a_sensor_the_registry_does_not_know_is_imported(self):
        with open(self.lswebpasswd, "w") as handle:
            handle.write("sensor-new-fox:$apr1$x$y\n")
        registry = self.registry()
        registry.take_over(["sensor-new-fox"])
        self.assertEqual(self.registry().get("sensor-new-fox").source, "imported")
        self.assertTrue(self.registry().get("sensor-new-fox").access)

    def test_grant_and_revoke_refuse_while_the_live_file_holds_them(self):
        with open(self.lswebpasswd, "w") as handle:
            handle.write(f"{OTTER}\n")
        registry = self.registry()
        message = ("lswebpasswd holds 1 sensor user(s) the .env does not name (sensor-old-otter); take them over "
                   "first: tpot sensors import")
        for change in (lambda: registry.grant("sensor-new-fox", "pw"), lambda: registry.revoke("sensor-old-lynx"),
                       registry.check_writable):
            with self.assertRaises(sensors.SensorsError) as caught:
                change()
            self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.read(self.lswebpasswd), f"{OTTER}\n")
        self.assertEqual(self.registry().entries(), [])
        # .lost alone never blocks
        os.remove(self.lswebpasswd)
        open(self.lswebpasswd, "w").close()
        self.registry().grant("sensor-new-fox", "pw")
        self.assertEqual([u.name for u in self.registry().entries()], ["sensor-new-fox"])

    def test_a_line_t_pot_does_not_take_goes_into_the_lost_file(self):
        with open(self.lswebpasswd, "w") as handle:
            handle.write("legacy:$1$salt$legacyhash\n")
        registry = self.registry()
        self.assertEqual([e.name for e in registry.file_only()], ["sensor-old-otter", "sensor-old-lynx"])
        registry.grant("sensor-new-fox", "pw")
        self.assertEqual(self.read(self.lswebpasswd + ".lost"), f"{OTTER}\n{LYNX}\nlegacy:$1$salt$legacyhash\n")
        self.assertEqual(self.read(self.lswebpasswd), fake_hash("sensor-new-fox", "pw") + "\n")

    def test_no_change_when_the_lost_file_cannot_take_them(self):
        os.remove(self.lswebpasswd + ".lost")
        os.mkdir(self.lswebpasswd + ".lost")
        with open(self.lswebpasswd, "w") as handle:
            handle.write("legacy:$1$salt$legacyhash\n")
        with self.assertRaises(sensors.SensorsError) as caught:
            self.registry().grant("sensor-new-fox", "pw")
        self.assertIn("cannot be kept in", str(caught.exception))
        self.assertEqual(self.read(self.lswebpasswd), "legacy:$1$salt$legacyhash\n")
        self.assertEqual(self.registry().entries(), [])

    @unittest.skipUnless(yaml, "PyYAML is not installed, run with the venv of tpot")
    def test_env_check_warns(self):
        from tpotctl import settings
        problems = [p for p in settings.load(self.repo).problems() if p.key == "LS_WEB_USER"]
        self.assertIn(("warning", "lswebpasswd.lost holds 2 sensor(s) it does not name, take them over with: "
                                  "tpot sensors import."), [(p.level, p.text) for p in problems])


@unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
class CliTest(unittest.TestCase):

    def setUp(self):
        self.repo = checkout(self)
        from tpotctl.tests import current_configuration
        current_configuration(self, self.repo)
        original = self.Registry = sensors.Registry
        self.patches = [
            mock.patch.object(sensors, "Registry", lambda repo_dir=None, hasher=fake_hash:
                              original(repo_dir or self.repo, hasher=hasher)),
            mock.patch.object(sensors, "fetch_status", lambda days=7: sensors.Status(
                sensors={"sensor-old-otter": sensors.Seen("2026-10-04T10:00:00Z", "edge1", "1.2.3.4")})),
            mock.patch.object(sensors, "read_version", return_value="sensor-release", create=True),
            mock.patch.object(sensors, "delete_status", return_value=None),
            mock.patch("tpotctl.cli.os.geteuid", return_value=1000, create=True),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def run_cli(self, *args, stdin=None):
        from tpotctl import cli
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", stdin or io.StringIO()):
            try:
                code = cli.main(list(args))
            except SystemExit as stop:
                code = stop.code if isinstance(stop.code, int) else 2
                err.write(str(stop.code))
            except KeyboardInterrupt:       # unittest would stop on it
                code = "KeyboardInterrupt"
        return code, out.getvalue() + err.getvalue()

    def test_import(self):
        env = EnvFile(os.path.join(self.repo, ".env"))
        self.run_cli("sensors", "list")                    # the registry knows both now
        env.set("LS_WEB_USER", "")
        env.save()
        lswebpasswd = os.path.join(self.repo, "data", "nginx", "conf", "lswebpasswd")
        with open(lswebpasswd + ".lost", "w") as handle:
            handle.write(f"{OTTER}\n{LYNX}\n")
        code, text = self.run_cli("sensors", "list")
        self.assertEqual(code, 0)
        self.assertEqual(text.count("to take over"), 2, text)
        self.assertIn("lswebpasswd.lost holds 2 sensor(s) LS_WEB_USER does not name", text)
        code, text = self.run_cli("sensors", "import")
        self.assertEqual(code, 2, text)                    # no terminal, no --yes
        self.assertIn(f"To drop one on purpose instead, remove its line from {lswebpasswd}.lost (or the whole file).",
                      text)
        code, text = self.run_cli("sensors", "import", "-y")
        self.assertEqual(code, 0, text)
        self.assertIn("sensor-old-otter, sensor-old-lynx are in LS_WEB_USER now", text)
        with open(lswebpasswd) as handle:
            self.assertEqual(handle.read(), f"{OTTER}\n{LYNX}\n")
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)
        code, text = self.run_cli("sensors", "import", "-y")
        self.assertEqual(code, 0, text)
        self.assertIn("Nothing to take over", text)

    def test_sensor_role_gate_returns_environment_exit_code(self):
        from tpotctl import ops
        env = EnvFile(os.path.join(self.repo, ".env"))
        env.set("TPOT_TYPE", "SENSOR")
        env.save()
        with mock.patch.object(ops, "require_linux_host"), \
                mock.patch.object(ops, "require_config_current"):
            code, text = self.run_cli("sensors", "update", "--yes", "--no-become-pass")
        self.assertEqual(code, 3, text)
        self.assertIn("sensors are managed on the HIVE", text)

    def test_import_names_lines_in_another_format(self):
        env = EnvFile(os.path.join(self.repo, ".env"))
        env.set("LS_WEB_USER", "")
        env.save()
        lswebpasswd = os.path.join(self.repo, "data", "nginx", "conf", "lswebpasswd")
        legacy = "sensor-old-heron:$1$heronsalt$heronhash"
        with open(lswebpasswd + ".lost", "w") as handle:
            handle.write(f"{OTTER}\n{legacy}\n")
        code, text = self.run_cli("sensors", "import", "-y")
        self.assertEqual(code, 0, text)
        self.assertIn("sensor-old-otter is in LS_WEB_USER now", text)
        self.assertIn("1 line(s) LS_WEB_USER does not name have a hash in a format T-Pot does not take "
                      f"(sensor-old-heron): they cannot be taken over and only stay in {lswebpasswd}.lost", text)
        with open(lswebpasswd + ".lost") as handle:
            self.assertEqual(handle.read(), f"{legacy}\n")
        code, text = self.run_cli("sensors", "import", "-y")
        self.assertEqual(code, 0, text)
        self.assertIn("Nothing to take over, LS_WEB_USER names every sensor of lswebpasswd and its .lost that "
                      "T-Pot can take.", text)
        self.assertIn("(sensor-old-heron)", text)

    def test_add_refuses_before_it_asks(self):
        lswebpasswd = os.path.join(self.repo, "data", "nginx", "conf", "lswebpasswd")
        with open(lswebpasswd, "w") as handle:
            handle.write("sensor-new-fox:$apr1$x$y\n")
        with mock.patch.object(sensors, "check_ssh", side_effect=AssertionError("ssh")):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2")
        self.assertEqual(code, 1, text)
        self.assertIn("take them over first: tpot sensors import", text)

    def test_list_set_remove(self):
        code, text = self.run_cli("sensors", "list")
        self.assertEqual(code, 0)
        self.assertIn("sensor-old-otter", text)
        self.assertIn("Taken over from LS_WEB_USER", text)
        self.assertEqual(self.run_cli("sensors", "set", "sensor-old-lynx", "--host", "10.0.0.7",
                                      "--ssh-user", "debian")[0], 0)
        self.assertEqual(sensors.Registry(self.repo).get("sensor-old-lynx").host, "10.0.0.7")
        self.assertNotEqual(self.run_cli("sensors", "set", "sensor-old-lynx", "--host", "a;b")[0], 0)
        self.assertEqual(self.run_cli("sensors", "remove", "sensor-old-lynx")[0], 2)         # no --yes, cannot ask
        self.assertEqual(self.run_cli("sensors", "remove", "sensor-old-lynx", "--yes")[0], 0)
        self.assertEqual([u.name for u in sensors.Registry(self.repo).entries()], ["sensor-old-otter"])

    def test_list_displays_heartbeat_separately_and_dates_the_cached_version(self):
        registry = sensors.Registry(self.repo)
        sensor = registry.get("sensor-old-lynx")
        sensor.version, sensor.version_checked_at = "cached-release", "2026-10-01T10:00:00Z"
        registry.record(sensor)
        status = sensors.Status(
            sensors={"sensor-old-otter": sensors.Seen("2026-11-01T11:59:00Z")},
            heartbeats={"sensor-old-otter": sensors.Heartbeat(HeartbeatTest.NOW, "heartbeat-release")})
        with mock.patch.object(sensors, "fetch_status", return_value=status), \
                mock.patch("time.time", return_value=HeartbeatTest.NOW), mock.patch.dict(os.environ, COLUMNS="220"):
            code, text = self.run_cli("sensors", "list")
        self.assertEqual(code, 0, text)
        for word in ("Version", "Heartbeat", "Last event", "heartbeat-release", "behind", "online", "unknown",
                     "cached-release", "checked 2026-10-01", "2026-11-01 11:59:00"):
            self.assertIn(word, text)
        self.assertNotIn("sending", text)

    def test_list_shows_refused_sensor_as_update_needed(self):
        status = sensors.Status(refused={"sensor-old-lynx": HeartbeatTest.NOW - 60})
        with mock.patch.object(sensors, "fetch_status", return_value=status), \
                mock.patch("time.time", return_value=HeartbeatTest.NOW), mock.patch.dict(os.environ, COLUMNS="220"):
            code, text = self.run_cli("sensors", "list")
        self.assertEqual(code, 0, text)
        self.assertIn("update needed (events refused)", text)
        self.assertIn("(behind)", text)

    def test_last_event_is_displayed_in_utc(self):
        status = sensors.Status(sensors={"sensor-old-otter": sensors.Seen("2026-11-01T12:00:00+02:00")})
        with mock.patch.object(sensors, "fetch_status", return_value=status), mock.patch.dict(os.environ, COLUMNS="220"):
            code, text = self.run_cli("sensors", "list")
        self.assertEqual(code, 0, text)
        self.assertIn("2026-11-01 10:00:00", text)

    def test_remove_reports_cleanup_failure_and_keeps_access_revoked(self):
        with mock.patch.object(sensors, "delete_status", side_effect=sensors.SensorsError("heartbeat unavailable")):
            code, text = self.run_cli("sensors", "remove", "sensor-old-lynx", "-y")
        self.assertEqual(code, 1, text)
        self.assertIn("access and registry entry removed", text)
        self.assertIn("heartbeat unavailable", text)
        self.assertNotIn("sensor-old-lynx", sensors.Registry(self.repo).records)

    def test_add_caches_the_sensor_version_with_a_check_date(self):
        code, text, children = self.add_with(0)
        self.assertEqual(code, 0, text)
        added = [sensor for sensor in sensors.Registry(self.repo).sensors() if sensor.source == "deployed"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].version, "sensor-release")
        self.assertTrue(added[0].version_checked_at)
        self.assertLess(abs(datetime.now(timezone.utc).timestamp() -
                            datetime.fromisoformat(added[0].version_checked_at).timestamp()), 10)
        self.assertEqual(len(children), 1)

    def test_add_warns_old_sensor(self):
        with mock.patch.object(sensors, "read_version", return_value="24.04.1"):
            code, text, children = self.add_with(0)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(children), 1)                   # it warns, the deployment goes on
        self.assertIn("This SENSOR runs Logstash: the HIVE refuses its events until it is updated "
                      "(tpot sensors update).", " ".join(text.split()))
        code, text, _ = self.add_with(0)                     # "sensor-release": no claim, no warning
        self.assertEqual(code, 0, text)
        self.assertNotIn("runs Logstash", text)

    def test_missing_sensor_version_warns_but_does_not_invent_the_hive_version(self):
        with mock.patch.object(sensors, "read_version", side_effect=sensors.SensorsError("version unavailable")):
            code, text, _ = self.add_with(0)
        self.assertEqual(code, 0, text)
        self.assertIn("version unavailable", text)
        added = [sensor for sensor in sensors.Registry(self.repo).sensors() if sensor.source == "deployed"]
        self.assertEqual((added[0].version, added[0].version_checked_at), ("", ""))

    def test_ctrl_c_at_the_question_of_remove(self):
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            code, text = self.run_cli("sensors", "remove", "sensor-old-lynx", stdin=TTY())
        self.assertEqual(code, 130, text)
        self.assertIn("Cancelled.", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # nothing revoked

    def test_add_asks_or_says_which_option_it_needs(self):
        code, text = self.run_cli("sensors", "add", "--ssh-user", "debian")
        self.assertEqual(code, 2, text)
        self.assertIn("--host", text)
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            code, text = self.run_cli("sensors", "add", stdin=TTY())
        self.assertEqual(code, 130, text)
        self.assertNotIn("Traceback", text)

    def test_an_ipv6_hive_address_is_refused_before_ssh(self):
        """The HIVE address goes into the .env of the SENSOR (TPOT_HIVE_IP): IPv6 is refused at once."""
        with mock.patch.object(sensors, "check_ssh") as ssh, mock.patch("subprocess.Popen") as popen, \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "fd00::1", stdin=TTY())
        self.assertNotEqual(code, 0, text)
        self.assertIn("fd00::1", text)
        ssh.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # nothing granted

    def test_the_help_says_ipv4_for_the_hive_address(self):
        """As the question and the dialogs: a sensor cannot send to an IPv6 address of this HIVE."""
        for action in ("add", "set"):
            with self.subTest(action=action):
                code, text = self.run_cli("sensors", action, "-h")
                self.assertEqual(code, 0, text)
                self.assertIn("IPv4 or name the sensor reaches this HIVE on", " ".join(text.split()))

    def test_no_proposal_of_the_hive_address_asks_for_it(self):
        """An SSH alias (sensor_1) has no address of this HIVE on the way to it: no "[]" in the question,
        without a terminal exit 2 (cannot ask) and the option to give it with, an empty answer exit 1."""
        with mock.patch.object(sensors, "check_ssh", return_value="unreachable") as ssh, \
                mock.patch.object(sensors, "default_hive_address", return_value=""):
            code, text = self.run_cli("sensors", "add", "--host", "sensor_1", "--ssh-user", "u")
            self.assertEqual(code, 2, text)
            self.assertIn("--hive-address", text)
            self.assertNotIn("''", text)
            ssh.assert_not_called()
            prompts = []
            for answer in ("", "192.168.1.2"):
                with self.subTest(answer=answer), \
                        mock.patch("builtins.input", side_effect=lambda prompt: prompts.append(prompt) or answer):
                    code, text = self.run_cli("sensors", "add", "--host", "sensor_1", "--ssh-user", "u",
                                              stdin=TTY())
                    self.assertNotIn("[]", prompts[-1])
                    if answer:
                        self.assertEqual(code, 1, text)            # on to SSH, which is not there
                        self.assertIn("cannot log in", text)
                    else:                                   # it did ask: an empty answer is 1, as for --host
                        self.assertEqual(code, 1, text)
                        self.assertIn("nothing given", text)
                        self.assertIn("--hive-address", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # nothing granted

    @unittest.skipIf(os.geteuid() == 0, "root writes a read-only file")
    def test_an_access_that_counts_after_a_restart_is_said(self):
        """lswebpasswd cannot be written in place: nginx knows the sensor only after tpot restart."""
        lswebpasswd = os.path.join(self.repo, "data", "nginx", "conf", "lswebpasswd")
        os.chmod(lswebpasswd, 0o444)
        self.addCleanup(os.chmod, lswebpasswd, 0o644)
        code, text, _children = self.add_with(0)
        self.assertEqual(code, 0, text)
        self.assertIn("tpot restart", text)
        self.assertNotIn("and sends to", text)

    def add_with(self, *steps, ssh="ok"):
        """sensors add at a terminal with the children of fake_popen(*steps); its password is known."""
        popen, children = fake_popen(*steps)
        with mock.patch.object(sensors, "check_ssh", return_value=ssh), \
                mock.patch.object(sensors, "has_ssh_key", return_value=True), \
                mock.patch.object(sensors, "cert_info", return_value=cert(["IP:192.168.1.2"])), \
                mock.patch.object(sensors, "new_password", return_value="Pw-of-the-sensor-1234"), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=popen):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2", stdin=TTY())
        return code, text, children

    def test_ctrl_c_during_the_playbook_takes_the_access_back(self):
        """The playbook ends by the ^C: the access goes, the password is never shown, exit code 130."""
        code, text, children = self.add_with(KeyboardInterrupt, -2)
        self.assertEqual(children[0].args[0], "ansible-playbook")
        self.assertEqual(code, 130, text)
        self.assertIn("taken back", text)
        self.assertIn("Cancelled.", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # only the migrated ones
        self.assertEqual([s.source for s in sensors.Registry(self.repo).sensors()], ["migrated", "migrated"])

    def test_a_second_ctrl_c_cannot_stop_taking_the_access_back(self):
        import signal
        before, seen = signal.getsignal(signal.SIGINT), []
        original = self.Registry.revoke

        def revoke(registry, name, forget=True):
            seen.append(signal.getsignal(signal.SIGINT))
            return original(registry, name, forget)
        for steps in ((KeyboardInterrupt, -2), (2,)):
            with mock.patch.object(self.Registry, "revoke", revoke):
                self.add_with(*steps)
        self.assertEqual(seen, [signal.SIG_IGN, signal.SIG_IGN])
        self.assertEqual(signal.getsignal(signal.SIGINT), before)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_that_the_playbook_survives_takes_the_access_back(self):
        """ansible-playbook catches the ^C itself and exits 99: still a ^C, 130 and Cancelled., the access goes."""
        code, text, _children = self.add_with(KeyboardInterrupt, 99)
        self.assertEqual(code, 130, text)
        self.assertIn("taken back", text)
        self.assertIn("Cancelled.", text)
        self.assertNotIn("Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def ctrl_c_in(self, method, *steps):
        """sensors add with a real SIGINT to this process right after Registry.<method>."""
        import signal
        original = getattr(self.Registry, method)

        def wrapped(registry, *args, **kwargs):
            result = original(registry, *args, **kwargs)
            os.kill(os.getpid(), signal.SIGINT)
            return result
        before = signal.getsignal(signal.SIGINT)
        with mock.patch.object(self.Registry, method, wrapped):
            code, text, children = self.add_with(*steps)
        self.assertEqual(signal.getsignal(signal.SIGINT), before)               # given back
        return code, text, children

    def test_ctrl_c_right_after_the_grant_starts_no_deployment(self):
        """No window between the grant and what takes it back: the access goes, nothing is deployed."""
        code, text, children = self.ctrl_c_in("grant", 0)
        self.assertEqual(code, 130, text)
        self.assertEqual(children, [])
        self.assertIn("taken back", text)
        self.assertNotIn("Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_as_the_playbook_fails_takes_the_access_back(self):
        """The ^C comes just as the playbook ends with an error, before the access is taken back."""
        import signal
        popen, children = fake_popen(2)

        def late_popen(*args, **kwargs):
            child = popen(*args, **kwargs)
            wait, sent = child.wait, []

            def wait_then_ctrl_c(timeout=None):
                code = wait(timeout)
                if not sent:
                    sent.append(True)
                    os.kill(os.getpid(), signal.SIGINT)
                return code
            child.wait = wait_then_ctrl_c
            return child
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_info", return_value=cert(["IP:192.168.1.2"])), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=late_popen):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2", stdin=TTY())
        self.assertEqual(len(children), 1)
        self.assertEqual(code, 130, text)
        self.assertIn("taken back", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_while_taking_the_access_back_cannot_stop_it(self):
        code, text, _children = self.ctrl_c_in("revoke", 2)
        self.assertNotEqual(code, 0, text)
        self.assertIn("taken back", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_while_noting_a_deployed_sensor_keeps_it(self):
        code, text, _children = self.ctrl_c_in("record", 0)
        self.assertEqual(code, 0, text)
        self.assertIn("shown only now: Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 3)

    def test_a_playbook_that_cannot_start_takes_the_access_back(self):
        code, text, _children = self.add_with(FileNotFoundError(2, "No such file or directory", "ansible-playbook"))
        self.assertEqual(code, 1, text)
        self.assertIn("taken back", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("Pw-of-the-sensor-1234", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_any_other_break_before_the_deployment_ends_takes_the_access_back(self):
        with mock.patch.object(sensors, "deploy_env", side_effect=RuntimeError("a bug")), \
                self.assertRaises(RuntimeError):
            self.add_with(0)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_a_deployed_sensor_keeps_its_access_if_it_cannot_be_noted(self):
        """A registry write failure only costs details; access stays and the password is shown."""
        before = {u.name for u in sensors.Registry(self.repo).entries()}
        failure = sensors.SensorsError("cannot write data/sensors.json")
        with mock.patch.object(self.Registry, "record", side_effect=failure):
            code, text, _children = self.add_with(0)
        self.assertEqual(code, 0, text)
        self.assertIn("shown only now: Pw-of-the-sensor-1234", text)
        self.assertIn("cannot write data/sensors.json", text)
        self.assertIn("tpot sensors set", text)
        self.assertNotIn("taken back", text)
        added = {u.name for u in sensors.Registry(self.repo).entries()} - before
        self.assertEqual(len(added), 1, text)

    def test_no_key_login_without_a_terminal_cannot_ask(self):
        """ssh-copy-id asks for the password of the sensor: without a terminal exit code 2."""
        popen, children = fake_popen(0)
        with mock.patch.object(sensors, "check_ssh", return_value="key"), \
                mock.patch.object(sensors, "has_ssh_key", return_value=True), \
                mock.patch.object(sensors, "cert_info", return_value=cert(["IP:192.168.1.2"])), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=popen):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2")
        self.assertEqual(code, 2, text)
        self.assertIn("ssh-copy-id", text)
        self.assertEqual(children, [])
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_ctrl_c_at_ssh_copy_id_is_cancelled(self):
        code, text, children = self.add_with(KeyboardInterrupt, -2, ssh="key")
        self.assertEqual(children[0].args[0], "ssh-copy-id")
        self.assertEqual(code, 130, text)
        self.assertIn("Cancelled.", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)      # nothing granted

    def test_add_grants_first_and_takes_back_on_failure(self):
        popen, children = fake_popen(0)
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_info", return_value=cert(["IP:192.168.1.2"])), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=popen):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2", "--no-become-pass")
        self.assertEqual(code, 0, text)
        self.assertIn("is deployed to 10.0.0.5 and sends to 192.168.1.2.", text)
        password = re.search(r"shown only now: (\S+)", text).group(1)
        deployed = [s for s in sensors.Registry(self.repo).sensors() if s.source == "deployed"]
        self.assertEqual(len(deployed), 1)
        self.assertEqual((deployed[0].host, deployed[0].hive_address), ("10.0.0.5", "192.168.1.2"))
        command, kwargs = children[0].args, children[0].kwargs
        self.assertEqual(command[0], "ansible-playbook")
        self.assertNotIn("--ask-become-pass", command)
        self.assertEqual(base64.b64decode(kwargs["env"]["myTPOT_HIVE_USER"]).decode(),
                         f"{deployed[0].name}:{password}")
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_info", return_value=cert(["IP:192.168.1.2"])), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch("subprocess.Popen", side_effect=fake_popen(2)[0]):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.6", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2")
        self.assertEqual(code, 1)
        self.assertIn("taken back", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 3)      # 2 migrated + 1 deployed

    def add_renewing(self, info=None, reload="reloaded", note=None):
        """sensors add --yes with nginx.crt as `info` (by default one that lacks the hive address) and
        nginx_reload giving `reload`; the steps in their order. nginx.crt is read once."""
        from tpotctl import ops
        steps = []
        popen, _children = fake_popen(0)
        grant = self.Registry.grant

        def renewed(_registry, sans):
            steps.append(("renew", sans))
            stamp = "20261120-120000"
            for path in (_registry.key, _registry.cert):
                if os.path.exists(path):
                    os.replace(path, f"{path}.bak-{stamp}")
                with open(path, "w") as handle:
                    handle.write("new certificate\n")
            return stamp

        def reloaded():
            steps.append("reload")
            if isinstance(reload, Exception):
                raise reload
            return reload

        def deploy(command, **kwargs):
            steps.append("deploy")
            return popen(command, **kwargs)

        def granted(registry, *args, **kwargs):
            steps.append("grant")
            granted_note = grant(registry, *args, **kwargs)
            return granted_note if note is None else note

        cert_info = mock.Mock(return_value=info or cert(["IP:192.168.5.15"]))
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_info", cert_info), \
                mock.patch.object(sensors, "renew_cert", side_effect=renewed), \
                mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"), \
                mock.patch.object(self.Registry, "grant", granted), \
                mock.patch.object(ops, "nginx_reload", side_effect=reloaded), \
                mock.patch.object(ops, "service_command", side_effect=AssertionError("T-Pot is not restarted")), \
                mock.patch("subprocess.Popen", side_effect=deploy):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2", "--yes")
        self.assertEqual(cert_info.call_count, 1, "nginx.crt is read once")
        return code, text, steps

    def test_add_renews_and_reloads_before_the_grant(self):
        """nginx reads lswebpasswd per request: a reload before the grant takes nothing of the access."""
        code, text, steps = self.add_renewing()
        self.assertEqual(code, 0, text)
        self.assertEqual(steps, [("renew", ["IP:192.168.5.15", "IP:192.168.1.2"]), "reload", "grant", "deploy"])
        self.assertIn("is deployed to 10.0.0.5 and sends to 192.168.1.2.", flat(text))
        self.assertNotIn("tpot restart", text)
        self.assertIn("Remember: sensor-old-lynx, sensor-old-otter need the new certificate as well: "
                      "tpot sensors cert --distribute", flat(text))

    def test_add_renewed_without_a_reload_says_when_it_sends(self):
        code, text, steps = self.add_renewing(reload="not running")
        self.assertEqual(code, 0, text)
        self.assertEqual(steps[1:], ["reload", "grant", "deploy"])
        self.assertIn("nginx is not running, T-Pot uses the new certificate from its next start on.", flat(text))
        self.assertIn("is deployed to 10.0.0.5, it sends to 192.168.1.2 from the next start of T-Pot on "
                      "(tpot restart).", flat(text))
        self.assertNotIn("and sends to", text)

    def test_add_with_an_access_after_a_restart_says_so(self):
        """lswebpasswd could not be written in place: the reload of nginx does not bring the access."""
        code, text, steps = self.add_renewing(note="it counts from the next start of T-Pot on (tpot restart)")
        self.assertEqual(code, 0, text)
        self.assertEqual(steps[1:], ["reload", "grant", "deploy"])
        self.assertIn("is deployed to 10.0.0.5. nginx takes its access from the next start of T-Pot on "
                      "(tpot restart), it sends to 192.168.1.2 after that.", flat(text))
        self.assertNotIn("and sends to", text)

    def test_add_stops_before_the_grant_when_nginx_refuses_the_certificate(self):
        from tpotctl import ops
        code, text, steps = self.add_renewing(reload=ops.OpsError("nginx: [emerg] cannot load certificate"))
        self.assertEqual(code, 1, text)
        self.assertEqual(steps[1:], ["reload"])                             # no grant, no deployment
        self.assertIn("nginx does not take the new certificate (nginx: [emerg] cannot load certificate)",
                      flat(text))
        self.assertIn("the new certificate remains on disk", flat(text))
        self.assertIn("restore the prior files by moving", flat(text))
        self.assertIn("nginx.crt.bak-20261120-120000", flat(text))
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)

    def test_add_with_a_ca_certificate_stops_before_the_grant(self):
        code, text, steps = self.add_renewing(info=cert(["DNS:hive.example.org"], self_signed=False))
        self.assertEqual(code, 1, text)
        self.assertEqual(steps, [])                                         # not renewed, nothing granted
        self.assertIn("The certificate of this HIVE is issued by Example Root CA and does not cover 192.168.1.2; "
                      "reissue it at that CA with this address, or give --hive-address with an address it "
                      "covers (DNS:hive.example.org).", flat(text))
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)
        code, text, steps = self.add_renewing(info=cert(["IP:192.168.1.2"], self_signed=False))
        self.assertEqual(code, 0, text)                                     # one that covers it is fine
        self.assertEqual(steps, ["grant", "deploy"])
        self.assertIn("is deployed to 10.0.0.5 and sends to 192.168.1.2.", flat(text))

    def test_add_stops_without_ssh_and_with_uncovered_certificate(self):
        with mock.patch.object(sensors, "check_ssh", return_value="unreachable"):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2")
        self.assertEqual(code, 1)
        self.assertIn("cannot log in", text)
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_info", return_value=cert(["IP:192.168.5.15"])):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2")
        self.assertNotEqual(code, 0)
        self.assertIn("does not cover 192.168.1.2", text)
        unreadable = sensors.SensorsError("cannot read the certificate nginx.crt: Could not find certificate")
        with mock.patch.object(sensors, "check_ssh", return_value="ok"), \
                mock.patch.object(sensors, "cert_info", side_effect=unreadable), \
                mock.patch.object(sensors, "renew_cert", side_effect=AssertionError("not replaced")):
            code, text = self.run_cli("sensors", "add", "--host", "10.0.0.5", "--ssh-user", "debian",
                                      "--hive-address", "192.168.1.2", "--yes")
        self.assertEqual(code, 1, text)
        self.assertIn("cannot read the certificate nginx.crt", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(sensors.Registry(self.repo).entries()), 2)     # nothing granted

    def distribute(self, *names, code=0, retry=None):
        """tpot sensors cert --distribute NAMES with a playbook that writes `retry` into its sensor_cert.retry
        (None: no file) and ends with `code`; the output without Rich's line breaks, its children and, per
        child, the retry folder, ANSIBLE_RETRY_FILES_ENABLED and the mode of the folder it was given."""
        children, folders = [], []

        def popen(command, **kwargs):
            env = kwargs.get("env") or {}
            folder = env.get("ANSIBLE_RETRY_FILES_SAVE_PATH")
            folders.append((folder, env.get("ANSIBLE_RETRY_FILES_ENABLED"),
                            os.stat(folder).st_mode & 0o777 if folder else None))
            if folder and retry is not None:
                with open(os.path.join(folder, "sensor_cert.retry"), "w", encoding="utf-8") as handle:
                    handle.write("".join(f"{name}\n" for name in retry))
            children.append(Child(command, kwargs, [code]))
            return children[-1]
        with mock.patch.object(sensors, "cert_info", return_value=cert(["IP:192.168.5.15"])), \
                mock.patch("subprocess.Popen", side_effect=popen):
            exit_code, text = self.run_cli("sensors", "cert", "--distribute", *names)
        return exit_code, flat(text), children, folders

    def test_cert_distribute(self):
        """The sensors it does not reach (skipped, or named in Ansible's retry file) get the way by hand."""
        registry = sensors.Registry(self.repo)
        registry.record(sensors.Sensor("sensor-new-bat", host="10.0.0.4", ssh_user="debian", source="deployed"))
        by_hand = ("By hand for {}: copy " + registry.cert + " of this HIVE to ~/tpotce/data/hive.crt on the "
                   "sensor, then restart T-Pot there (sudo systemctl restart tpot).")
        code, text, children, _folders = self.distribute()
        self.assertEqual(code, 1, text)
        self.assertEqual(children, [])                                      # none to reach: no playbook
        # no access: sensors set would not give it, import or add again do
        self.assertIn("sensor-new-bat is skipped, it has no access to this HIVE: tpot sensors import takes its "
                      "line of lswebpasswd over where T-Pot keeps one, else tpot sensors add deploys it again", text)
        self.assertNotIn("tpot sensors set sensor-new-bat", text)
        for name in ("sensor-old-lynx", "sensor-old-otter"):                       # access, no host
            self.assertIn(f"{name} is skipped, add where it is: tpot sensors set {name} --host ... "
                          f"--ssh-user ...", text)
        self.assertIn(by_hand.format("sensor-new-bat, sensor-old-lynx, sensor-old-otter"), text)
        for name, host in (("sensor-new-fox", "10.0.0.5"), ("sensor-new-owl", "10.0.0.6")):
            registry.grant(name, "pw")
            registry.record(sensors.Sensor(name, host=host, ssh_user="debian", source="deployed"))

        code, text, children, folders = self.distribute(code=2, retry=["sensor-new-owl"])
        self.assertEqual(code, 1, text)
        self.assertEqual(len(children), 1, text)
        self.assertEqual(children[0].args[0], "ansible-playbook")
        env = children[0].kwargs.get("env") or {}
        folder, enabled, mode = folders[0]
        self.assertEqual((enabled, mode), ("True", 0o700))                  # a private folder
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))           # the environment with the two keys
        self.assertFalse(os.path.exists(folder))                            # removed afterwards
        self.assertIn("sensor-new-fox has the new certificate.", text)
        self.assertNotIn("sensor-new-owl has", text)
        self.assertIn(by_hand.format("sensor-new-bat, sensor-new-owl, sensor-old-lynx, sensor-old-otter"), text)
        for retry in (None, ["sensor-not-in-the-play"]):            # no file, or none of them: all failed
            with self.subTest(retry=retry):
                code, text, _children, folders = self.distribute(code=4, retry=retry)
                self.assertEqual(code, 1, text)
                self.assertNotIn("the new certificate.", text)
                self.assertIn(by_hand.format("sensor-new-bat, sensor-new-fox, sensor-new-owl, sensor-old-lynx, "
                                             "sensor-old-otter"), text)
                self.assertTrue(folders[0][0])                              # a folder was handed in
                self.assertFalse(folders[0][0] and os.path.exists(folders[0][0]))

        code, text, _children, _folders = self.distribute()                # copied, the skipped ones by hand
        self.assertEqual(code, 0, text)
        self.assertIn("sensor-new-fox, sensor-new-owl have the new certificate.", text)
        self.assertIn(by_hand.format("sensor-new-bat, sensor-old-lynx, sensor-old-otter"), text)
        code, text, _children, _folders = self.distribute("sensor-new-fox", "sensor-new-owl")
        self.assertEqual(code, 0, text)
        self.assertIn("sensor-new-fox, sensor-new-owl have the new certificate.", text)
        self.assertNotIn("By hand", text)
        with mock.patch.object(self.Registry, "sensors", return_value=[]):
            code, text, children, _folders = self.distribute()
        self.assertEqual(code, 0, text)
        self.assertIn("No sensors are registered, there is nothing to copy.", text)
        self.assertEqual(children, [])

    def test_ctrl_c_during_the_distribution_is_cancelled(self):
        registry = sensors.Registry(self.repo)
        registry.grant("sensor-new-fox", "pw")
        registry.record(sensors.Sensor("sensor-new-fox", host="10.0.0.5", ssh_user="debian", source="deployed"))
        popen, children = fake_popen(KeyboardInterrupt, -2)
        with mock.patch.object(sensors, "cert_info", return_value=cert(["IP:192.168.5.15"])), \
                mock.patch("subprocess.Popen", side_effect=popen):
            code, text = self.run_cli("sensors", "cert", "--distribute", "sensor-new-fox", stdin=TTY())
        self.assertEqual(children[0].args[0], "ansible-playbook")
        self.assertEqual(code, 130, text)
        self.assertNotIn("Traceback", text)
        inventory = children[0].args[children[0].args.index("-i") + 1]
        self.assertFalse(os.path.exists(inventory))                           # the inventory goes as well
        folder = (children[0].kwargs.get("env") or {}).get("ANSIBLE_RETRY_FILES_SAVE_PATH")
        self.assertTrue(folder)
        self.assertFalse(os.path.exists(folder))                              # and the retry folder

    def cert_cli(self, *args, info=None, reload="reloaded", stdin=None, error=None):
        """tpot sensors cert ... with nginx.crt as `info`; the renew steps in their order."""
        from tpotctl import ops
        steps = []

        def renewed(_registry, sans):
            steps.append(("renew", sans))
            stamp = "20261120-120000"
            for path in (_registry.key, _registry.cert):
                if os.path.exists(path):
                    os.replace(path, f"{path}.bak-{stamp}")
                with open(path, "w") as handle:
                    handle.write("new certificate\n")
            return stamp

        def reloaded():
            steps.append("reload")
            if isinstance(reload, Exception):
                raise reload
            return reload

        cert_info = mock.Mock(side_effect=error) if error else mock.Mock(return_value=info)
        with mock.patch.object(sensors, "cert_info", cert_info), \
                mock.patch.object(sensors, "renew_cert", side_effect=renewed), \
                mock.patch.object(ops, "nginx_reload", side_effect=reloaded), \
                mock.patch.object(ops, "service_command", side_effect=AssertionError("T-Pot is not restarted")):
            code, text = self.run_cli("sensors", "cert", *args, stdin=stdin)
        self.assertLessEqual(cert_info.call_count, 1, "nginx.crt is read once")
        return code, text, steps

    def test_cert_shows_the_certificate(self):
        code, text, steps = self.cert_cli("--add", "10.9.8.7", info=cert(["IP:192.168.5.15"], key="RSA 8192"))
        self.assertEqual(code, 0, text)
        self.assertEqual(steps, [])
        until = (datetime.now(timezone.utc) + timedelta(days=3650, hours=1)).strftime("%Y-%m-%d")
        self.assertIn(f"Certificate: RSA 8192, self-signed, valid until {until}, covers IP:192.168.5.15", flat(text))
        self.assertIn(f"An ECDSA certificate costs this HIVE far less CPU per connection than RSA: {RENEW}.",
                      flat(text))
        self.assertIn("Not covered: 10.9.8.7 - sensors checking with full verification cannot connect there.",
                      flat(text))
        self.assertNotIn("expires", text)
        code, text, _steps = self.cert_cli(info=cert(["IP:192.168.5.15"], self_signed=False, days=5))
        self.assertEqual(code, 0, text)
        self.assertIn("issued by Example Root CA", flat(text))
        self.assertIn("days (", flat(text))
        self.assertIn("renew it at Example Root CA, then tpot sensors cert --distribute.", flat(text))
        self.assertNotIn("ECDSA certificate costs", flat(text))
        code, text, _steps = self.cert_cli(info=None)
        self.assertEqual(code, 0, text)
        self.assertIn("Certificate: none (", flat(text))
        self.assertIn(f"Certificate:none({sensors.Registry(self.repo).cert}ismissingorempty)",
                      re.sub(r"\s+", "", flat(text)))
        self.assertIn("is missing or empty); tpot sensors cert --renew issues one.", flat(text))

    def test_renew_refuses_a_ca_certificate(self):
        code, text, steps = self.cert_cli("--renew", "--yes", info=cert(["IP:192.168.5.15"], self_signed=False))
        self.assertEqual(code, 2, text)
        self.assertEqual(steps, [])
        folder = os.path.join(self.repo, "data", "nginx", "cert")
        self.assertIn(sensors.ca_renew_refusal(cert(["IP:192.168.5.15"], self_signed=False), folder), flat(text))

    def test_cert_cli_reads_certificate_metadata_once_and_passes_those_sans_through(self):
        info = cert(["IP:192.168.5.15"])
        with mock.patch.object(sensors, "cert_info", return_value=info) as read_info, \
                mock.patch.object(sensors, "cert_sans", side_effect=AssertionError("SANs must come from CertInfo")):
            code, text = self.run_cli("sensors", "cert", "--renew", "--yes", "--no-restart", "--add",
                                      "hive.example.org")
        self.assertEqual(code, 0, text)
        self.assertEqual(read_info.call_count, 1)
        self.assertIn("new certificate covers: IP:192.168.5.15, DNS:hive.example.org", flat(text))

    def test_renew_reloads_nginx(self):
        from tpotctl import ops
        question = ("Issue the certificate of this HIVE anew (ECDSA P-384)? Browsers warn about it once more, "
                    "every sensor needs the new one (sensors without access are skipped).")
        old = cert(["IP:192.168.5.15"], key="RSA 8192")
        code, text, steps = self.cert_cli("--renew", info=old, stdin=TTY("y\n"))
        self.assertEqual(code, 0, text)
        self.assertIn(question, flat(text))
        self.assertEqual(steps, [("renew", ["IP:192.168.5.15"]), "reload"])
        self.assertIn("nginx uses the new certificate.", text)
        self.assertNotIn("8192 bit key", text)
        code, text, steps = self.cert_cli("--renew", info=old, stdin=TTY("n\n"))
        self.assertEqual(code, 1, text)
        self.assertEqual(steps, [])                                           # declined: nothing issued
        code, text, steps = self.cert_cli("--renew", "--yes", info=old, reload="not running")
        self.assertEqual(code, 0, text)
        self.assertEqual(steps[1:], ["reload"])
        self.assertIn("nginx is not running, T-Pot uses the new certificate from its next start on.", flat(text))
        code, text, steps = self.cert_cli("--renew", "--yes", info=old,
                                          reload=ops.OpsError('nginx: [emerg] cannot load certificate'))
        self.assertEqual(code, 1, text)
        self.assertIn("nginx does not take the new certificate (nginx: [emerg] cannot load certificate); the new "
                      "certificate remains on disk; restore the prior files by moving", flat(text))
        self.assertIn("nginx.crt.bak-20261120-120000", flat(text))
        self.assertIn("nginx.key.bak-20261120-120000", flat(text))
        code, text, steps = self.cert_cli("--renew", "--yes", "--no-restart", info=old)
        self.assertEqual(code, 0, text)
        self.assertEqual(steps, [("renew", ["IP:192.168.5.15"])])             # no reload
        self.assertIn("nginx uses the new certificate from the next start of T-Pot on (tpot restart).", flat(text))

    def test_renew_without_a_certificate_issues_one(self):
        registry = sensors.Registry(self.repo)
        registry.record(sensors.Sensor("sensor-new-fox", host="10.0.0.5", ssh_user="debian",
                                       hive_address="192.168.1.2", source="deployed"))
        code, text, steps = self.cert_cli("--renew", "--yes", "--add", "hive.example.org", info=None)
        self.assertEqual(code, 0, text)
        self.assertEqual(steps, [("renew", ["IP:192.168.1.2", "DNS:hive.example.org"]), "reload"])
        self.assertIn("Certificate: none (", flat(text))
        with mock.patch.object(self.Registry, "hive_addresses", return_value=[]):
            code, text, steps = self.cert_cli("--renew", "--yes", info=None)
        self.assertEqual(code, 2, text)
        self.assertEqual(steps, [])
        self.assertIn("give one with --add", flat(text))

    def test_distribute_without_a_certificate(self):
        from tpotctl import ops
        registry = sensors.Registry(self.repo)
        registry.grant("sensor-new-fox", "pw")
        registry.record(sensors.Sensor("sensor-new-fox", host="10.0.0.5", ssh_user="debian",
                                       hive_address="192.168.1.2", source="deployed"))
        popen, children = fake_popen(0)
        with mock.patch("subprocess.Popen", side_effect=popen):
            code, text, steps = self.cert_cli("--distribute", info=None)
            self.assertEqual(code, 1, text)
            self.assertEqual((steps, children), ([], []))                     # nothing is copied
            self.assertIn(f"there is no certificate to copy, {RENEW} issues one and copies it", flat(text))
            code, text, steps = self.cert_cli("--renew", "--distribute", info=None, stdin=TTY("n\n"))
            self.assertEqual(code, 1, text)
            self.assertEqual((steps, children), ([], []))                     # declined: nothing copied
            code, text, steps = self.cert_cli("--renew", "--yes", "--distribute", info=None,
                                              reload=ops.OpsError("nginx: [emerg] cannot load certificate"))
            self.assertEqual(code, 1, text)
            self.assertEqual(children, [])                                    # nginx refused it: not copied
            code, text, steps = self.cert_cli("--renew", "--yes", "--distribute", info=None)
        self.assertEqual(code, 0, text)
        self.assertEqual(steps, [("renew", ["IP:192.168.1.2"]), "reload"])
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].args[0], "ansible-playbook")
        self.assertIn("sensor-new-fox has the new certificate.", flat(text))

    def test_no_restart_still_distributes_the_new_certificate_without_reloading_nginx(self):
        registry = sensors.Registry(self.repo)
        registry.grant("sensor-new-fox", "pw")
        registry.record(sensors.Sensor("sensor-new-fox", host="10.0.0.5", ssh_user="debian",
                                       hive_address="192.168.1.2", source="deployed"))
        steps = []
        with open(registry.cert, "rb") as handle:
            original = handle.read()

        def renewed(_registry, sans):
            steps.append(("renew", sans))
            backup = f"{registry.cert}.bak-20261120-120000"
            os.replace(registry.cert, backup)
            with open(backup, "rb") as handle:
                self.assertEqual(handle.read(), original)
            with open(registry.cert, "wb") as handle:
                handle.write(b"new certificate")
            return "20261120-120000"

        def copied(command, **_kwargs):
            self.assertEqual(command[0], "ansible-playbook")
            with open(registry.cert, "rb") as handle:
                self.assertEqual(handle.read(), b"new certificate")
            steps.append("distribute")
            return 0

        from tpotctl import ops
        with mock.patch.object(sensors, "cert_info", return_value=cert(["IP:192.168.5.15"])), \
                mock.patch.object(sensors, "renew_cert", side_effect=renewed), \
                mock.patch.object(ops, "nginx_reload", side_effect=AssertionError("nginx must not reload")), \
                mock.patch("tpotctl.cli.call", side_effect=copied):
            code, text = self.run_cli("sensors", "cert", "--renew", "--yes", "--no-restart", "--distribute")
        self.assertEqual(code, 0, text)
        self.assertEqual(steps, [("renew", ["IP:192.168.5.15", "IP:192.168.1.2"]), "distribute"])
        self.assertIn("nginx uses the new certificate from the next start of T-Pot on (tpot restart).", flat(text))
        with open(registry.cert, "wb") as handle:
            handle.write(original)

    def test_reload_failure_without_old_files_does_not_claim_backups_exist(self):
        from tpotctl import ops
        registry = sensors.Registry(self.repo)
        for path in (registry.cert, registry.key):
            if os.path.exists(path):
                os.unlink(path)
        code, text, _steps = self.cert_cli("--renew", "--yes", "--add", "192.168.1.2",
                                          reload=ops.OpsError("nginx: [emerg] cannot load certificate"),
                                          info=None)
        self.assertEqual(code, 1, text)
        self.assertIn("the new certificate remains on disk", flat(text))
        self.assertIn("no prior certificate or key files were present to restore", flat(text))
        self.assertIn("no prior certificate or key files were present to keep", flat(text))
        self.assertNotIn("old ones are kept", flat(text))
        self.assertNotIn("old one is kept", flat(text))
        self.assertNotIn("nginx.crt.bak-20261120-120000", flat(text))

    def test_renew_refuses_a_certificate_it_cannot_read(self):
        crt = os.path.join(self.repo, "data", "nginx", "cert", "nginx.crt")
        with open(crt, "rb") as handle:
            before = handle.read()
        code, text, steps = self.cert_cli("--renew", "--yes", error=sensors.SensorsError(
            f"cannot read the certificate {crt}: Could not find or decode certificate"))
        self.assertEqual(code, 1, text)
        self.assertEqual(steps, [])
        self.assertIn("cannot read the certificate", text)
        self.assertNotIn("Traceback", text)
        with mock.patch.object(sensors, "cert_info", side_effect=sensors.SensorsError(
                f"cannot read the certificate {crt}: Could not find or decode certificate")), \
                mock.patch.object(sensors, "renew_cert", side_effect=AssertionError("not replaced")):
            code, text = self.run_cli("sensors", "cert", "--renew", "--yes")
        self.assertEqual(code, 1, text)
        self.assertIn("cannot read the certificate", flat(text))
        with open(crt, "rb") as handle:
            self.assertEqual(handle.read(), before)


@unittest.skipUnless(textual, "Textual is not installed, run with the venv of tpot")
class SensorsPaneTest(AsyncTestCase):

    def setUp(self):
        patch = mock.patch.object(sensors, "delete_status", return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def heartbeat_backend(self):
        from tpotctl.tests.test_app import FakeBackend
        from tpotctl import ops
        repo = checkout(self)

        class Backend(FakeBackend):
            status_value = sensors.Status(
                sensors={"sensor-old-lynx": sensors.Seen("2026-11-01T11:00:00Z")},
                heartbeats={"sensor-old-otter": sensors.Heartbeat(HeartbeatTest.NOW, "old-release")})

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return self.status_value

            def config_stamp(self):
                return ops.config_stamp(repo)

        return repo, Backend()

    async def test_heartbeat_table_counts_and_cursor_survive_refresh_at_80_columns(self):
        from tpotctl import app as tapp, theme
        repo, backend = self.heartbeat_backend()
        sensors.Registry(repo).record(sensors.Sensor("sensor-" + "long" * 70))
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        with mock.patch("tpotctl.sensors.time.time", return_value=HeartbeatTest.NOW) as now:
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause(0.5)
                app.goto("sensors")
                await pilot.pause(0.4)
                table = app.query_one("#sensors-table")
                self.assertEqual([str(c.label) for c in table.ordered_columns],
                                 ["Sensor", "Host", "Version", "Heartbeat", "Last event", "State"])
                self.assertLess(table.ordered_columns[0].get_render_width(table), table.scrollable_content_region.width)
                otter = table.get_row("sensor-old-otter")
                self.assertIn("behind", otter[2].plain)
                self.assertEqual(otter[2].style, theme.color("warn"))
                self.assertIn("online", otter[5].plain)
                self.assertIn("unknown", table.get_row("sensor-old-lynx")[5].plain)
                self.assertIn("Sensors: 1 online, 0 offline, 1 behind", str(app.query_one("#status-info").render()))
                table.move_cursor(row=1)
                table.scroll_to(x=table.max_scroll_x, animate=False)
                await pilot.pause()
                horizontal = table.scroll_x
                self.assertGreater(horizontal, 0)
                now.return_value = HeartbeatTest.NOW + 180
                await pilot.pause(2.2)
                self.assertEqual(table.cursor_row, 1)
                self.assertEqual(table.scroll_x, horizontal)
                self.assertIn("offline since", table.get_row("sensor-old-otter")[5].plain)
                self.assertIn("Sensors: 0 online, 1 offline, 1 behind", str(app.query_one("#status-info").render()))

    async def test_a_repaint_keeps_the_focus_on_the_sudo_and_update_buttons(self):
        """The page repaints every 2 s: a button that stays enabled keeps the focus, the arrows go on from it."""
        from tpotctl import app as tapp
        repo, backend = self.heartbeat_backend()
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause(0.5)
            app.goto("sensors")
            await pilot.pause(0.4)
            pane = app.query_one("#sensors")
            for button in ("#sensor-sudo", "#sensor-update"):
                with self.subTest(button=button):
                    widget = app.query_one(button)
                    self.assertFalse(widget.disabled)
                    widget.focus()
                    await pilot.pause()
                    pane.repaint()
                    await pilot.pause()
                    self.assertIs(app.focused, widget)

    async def test_refused_sensor_row_and_status_count(self):
        from tpotctl import app as tapp, theme
        repo, backend = self.heartbeat_backend()
        backend.status_value.refused = {"sensor-old-lynx": HeartbeatTest.NOW - 60}
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        with mock.patch("tpotctl.sensors.time.time", return_value=HeartbeatTest.NOW) as now:
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause(0.5)
                app.goto("sensors")
                await pilot.pause(0.4)
                table = app.query_one("#sensors-table")
                lynx = table.get_row("sensor-old-lynx")
                self.assertIn("update needed (events refused)", lynx[5].plain)
                self.assertEqual(lynx[5].style, theme.color("warn"))
                self.assertIn("(behind)", lynx[2].plain)
                info = app.query_one("#status-info").render()
                self.assertIn("Sensors: 1 online, 0 offline, 2 behind (1 refused)", str(info))
                backend.status_value = sensors.Status(refused=backend.status_value.refused)
                app.query_one("#sensors").load()
                await pilot.pause(0.6)
                self.assertIn("Sensors: 0 online, 0 offline, 1 behind (1 refused, 1 unknown)",
                              str(app.query_one("#status-info").render()))
                now.return_value = HeartbeatTest.NOW - 60 + sensors.REFUSED_WINDOW
                await pilot.pause(2.2)
                self.assertIn("unknown", table.get_row("sensor-old-lynx")[5].plain)
                self.assertIn("(2 unknown)", str(app.query_one("#status-info").render()))

    async def test_heartbeat_errors_and_partial_removal_are_visible(self):
        from tpotctl import app as tapp
        repo, backend = self.heartbeat_backend()
        backend.status_value = sensors.Status(problem="heartbeat endpoint unavailable")
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        notes = []
        app.notify = lambda message, **kwargs: notes.append(str(message))
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause(0.6)
            app.goto("sensors")
            await pilot.pause(0.3)
            self.assertIn("heartbeat endpoint unavailable", str(app.query_one("#sensors-info").render()))
            self.assertIn("heartbeat endpoint unavailable", str(app.query_one("#status-info").render()))
            with mock.patch.object(sensors, "delete_status", side_effect=sensors.SensorsError("heartbeat refused")):
                app.query_one("#sensors").revoke("sensor-old-otter")
                await pilot.pause(0.5)
            self.assertNotIn("sensor-old-otter", sensors.Registry(repo).records)
            self.assertTrue(any("heartbeat refused" in note and "access" in note for note in notes), notes)

    def cert_backend(self, info):
        """The heartbeat backend whose nginx.crt is `info` (a CertInfo, None, or an exception to raise)."""
        repo, backend = self.heartbeat_backend()
        paths = []

        def cert_info(path):
            paths.append(path)
            if isinstance(info, BaseException):
                raise info
            return info

        backend.cert_info = cert_info
        return repo, backend, paths

    def sensors_page(self, backend, commands):
        from tpotctl import app as tapp
        app = tapp.TpotApp(backend=backend, runner=lambda command, cwd=None: commands.append(command) or 0)
        return app

    async def test_the_page_shows_the_certificate_and_suggests_ecdsa(self):
        info = cert(["IP:192.0.2.5", "DNS:hive.test"], key="RSA 2048")
        repo, backend, paths = self.cert_backend(info)
        app = self.sensors_page(backend, [])
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause(0.5)
            app.goto("sensors")
            await pilot.pause(0.4)
            text = str(app.query_one("#sensors-info").render())
            date = info.not_after.strftime("%Y-%m-%d")
            self.assertIn(f"Certificate: RSA 2048, self-signed, valid until {date}, covers IP:192.0.2.5, "
                          f"DNS:hive.test", text)
            self.assertIn("An ECDSA certificate costs this HIVE far less CPU per connection than RSA: "
                          "Renew certificate below.", text)
            self.assertNotIn("expires", text)
            self.assertEqual(paths[-1], sensors.Registry(repo).cert)

    async def test_missing_certificate_summary_names_the_registry_path(self):
        repo, backend, _paths = self.cert_backend(None)
        app = self.sensors_page(backend, [])
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause(0.5)
            app.goto("sensors")
            await pilot.pause(0.4)
            text = re.sub(r"\s+", "", str(app.query_one("#sensors-info").render()))
            self.assertIn(f"Certificate:none({sensors.Registry(repo).cert}ismissingorempty)", text)

    async def test_a_certificate_that_ends_soon_is_a_warning(self):
        info = cert(["IP:192.0.2.5"], days=10)
        _repo, backend, _paths = self.cert_backend(info)
        app = self.sensors_page(backend, [])
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause(0.5)
            app.goto("sensors")
            await pilot.pause(0.4)
            date = info.not_after.strftime("%Y-%m-%d")
            self.assertIn(f"The certificate expires in 10 days ({date}); renew it: Renew certificate below.",
                          str(app.query_one("#sensors-info").render()))

    async def test_a_certificate_that_cannot_be_read_keeps_the_table(self):
        problem = sensors.SensorsError("cannot read the certificate nginx.crt: no public key")
        _repo, backend, _paths = self.cert_backend(problem)
        app = self.sensors_page(backend, [])
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause(0.5)
            app.goto("sensors")
            await pilot.pause(0.4)
            self.assertIn(str(problem), str(app.query_one("#sensors-info").render()))
            self.assertGreater(app.query_one("#sensors-table").row_count, 0)

    async def test_a_valid_certificate_stays_visible_when_later_sensor_reads_fail(self):
        info = cert(["IP:192.0.2.5"])
        for failing_read in ("status", "file_only"):
            with self.subTest(failing_read=failing_read):
                repo, backend, _paths = self.cert_backend(info)
                if failing_read == "status":
                    backend.sensor_status = mock.Mock(side_effect=sensors.SensorsError("status unavailable"))
                else:
                    registry = backend.sensors()
                    registry.file_only = mock.Mock(side_effect=sensors.SensorsError("file-only unavailable"))
                    backend.sensors = lambda: registry
                app = self.sensors_page(backend, [])
                async with app.run_test(size=(100, 35)) as pilot:
                    await pilot.pause(0.5)
                    app.goto("sensors")
                    await pilot.pause(0.4)
                    text = str(app.query_one("#sensors-info").render())
                    self.assertIn("Certificate: ECDSA P-384", text)
                    self.assertIn("192.0.2.5", text)
                    self.assertIn("unavailable", text)

    async def test_renew_renews_and_sends_a_self_signed_certificate(self):
        _repo, backend, _paths = self.cert_backend(cert(["IP:192.0.2.5"]))
        commands = []
        app = self.sensors_page(backend, commands)
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause(0.5)
            app.goto("sensors")
            await pilot.pause(0.4)
            app.query_one("#sensor-cert-renew").press()
            await pilot.pause(0.3)
            from tpotctl import app as tapp
            self.assertEqual(commands, [[tapp.LAUNCHER, "sensors", "cert", "--renew", "--distribute"]])

    async def test_renew_button_allows_a_missing_certificate_but_blocks_a_read_error(self):
        from tpotctl import app as tapp
        for info, expected_commands, expected_problem in (
                (None, [[tapp.LAUNCHER, "sensors", "cert", "--renew", "--distribute"]], ""),
                (sensors.SensorsError("cannot read the certificate nginx.crt: no public key"), [], "no public key")):
            with self.subTest(info=info):
                _repo, backend, _paths = self.cert_backend(info)
                commands = []
                app = self.sensors_page(backend, commands)
                async with app.run_test(size=(100, 35)) as pilot:
                    await pilot.pause(0.5)
                    app.goto("sensors")
                    await pilot.pause(0.4)
                    app.query_one("#sensor-cert-renew").press()
                    await pilot.pause(0.3)
                    self.assertEqual(commands, expected_commands)
                    if expected_problem:
                        self.assertIn(expected_problem, str(app.query_one("#sensors-info").render()))

    async def test_renew_refuses_a_certificate_of_a_ca(self):
        from tpotctl.screens.dialogs import NoticeDialog
        info = cert(["IP:192.0.2.5"], self_signed=False, issuer="Example Root CA")
        repo, backend, _paths = self.cert_backend(info)
        commands = []
        app = self.sensors_page(backend, commands)
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause(0.5)
            app.goto("sensors")
            await pilot.pause(0.4)
            app.query_one("#sensor-cert-renew").press()
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, NoticeDialog)
            folder = os.path.dirname(sensors.Registry(repo).cert)
            self.assertIn(sensors.ca_renew_refusal(info, folder), str(app.screen.query_one("#notice-body").render()))
            self.assertEqual(commands, [])
            await pilot.press("escape")
            await pilot.pause(0.2)
            self.assertNotIsInstance(app.screen, NoticeDialog)

    async def test_delayed_status_read_cannot_restore_removed_registry_entry(self):
        import threading
        from tpotctl import app as tapp
        repo, backend = self.heartbeat_backend()
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        started, release = threading.Event(), threading.Event()
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause(0.5)
            app.goto("sensors")
            await pilot.pause(0.3)
            def delayed(days=7):
                started.set()
                if not release.wait(5):
                    raise AssertionError("test did not release the status read")
                return sensors.Status(sensors={"sensor-old-otter": sensors.Seen(
                    "2026-11-01T12:00:00Z", "changed-hostname")})
            backend.sensor_status = delayed
            pane = app.query_one("#sensors")
            pane.load()
            try:
                await pilot.pause(0.2)
                self.assertTrue(started.is_set())
                pane.revoke("sensor-old-otter")
            finally:
                release.set()
            await pilot.pause(0.6)
            self.assertNotIn("sensor-old-otter", sensors.Registry(repo).records)
            self.assertEqual(app.query_one("#sensors-table").row_count, 1)

    async def test_list_deploy_remove(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import ConfirmDialog, SensorDialog
        repo = checkout(self)

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def images(self):
                return []

            def backups(self):
                return []

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={"sensor-old-otter": sensors.Seen("2026-10-04T10:00:00Z", "edge1")})

        commands = []
        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: commands.append(command) or 0)
        with mock.patch.object(sensors, "default_hive_address", return_value="192.168.1.2"):
            async with app.run_test(size=(150, 45)) as pilot:
                await pilot.pause(0.3)
                app.query_one("ContentSwitcher").current = "sensors"
                await pilot.pause(0.5)
                table = app.query_one("#sensors-table")
                self.assertEqual(table.row_count, 2)
                await pilot.click("#sensor-add")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, SensorDialog)
                app.screen.query_one("#sensor-host").value = "10.0.0.5"
                app.screen.query_one("#sensor-user").value = "debian"
                await pilot.click("#sensor-deploy")
                await pilot.pause(0.3)
                self.assertEqual(commands[-1][1:6], ["sensors", "add", "--host", "10.0.0.5", "--ssh-user"])
                table.move_cursor(row=0)
                await pilot.click("#sensor-remove")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, ConfirmDialog)
                await pilot.click("#yes")
                await pilot.pause(0.5)
        self.assertEqual([u.name for u in sensors.Registry(repo).entries()], ["sensor-old-otter"])

    async def test_update_sensors_opens_the_fleet_request_dialog(self):
        from tpotctl import app as tapp
        from tpotctl.screens.fleet import FleetUpdateDialog
        _repo, backend = self.heartbeat_backend()
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.goto("sensors")
            await pilot.pause(0.3)
            await pilot.click("#sensor-update")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, FleetUpdateDialog)
            self.assertEqual(app.screen.query_one("#fleet-parallel").value, "1")
            self.assertFalse(app.screen.query_one("#fleet-all").value)

    async def test_fleet_discard_is_available_before_collecting_credentials(self):
        from tpotctl import app as tapp
        from tpotctl.screens.dialogs import ChoiceDialog, ConfirmDialog
        from tpotctl.screens.fleet import FleetUpdateDialog, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        preparing = {
            "run_id": "a" * 32,
            "phase": "preparing",
            "targets": [{
                "name": "sensor-old-otter", "phase": "preparing", "previous_version": "",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = [preparing]
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        with mock.patch("tpotctl.screens.fleet.tfleet.FleetCoordinator", return_value=coordinator):
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                app.goto("sensors")
                await pilot.pause(0.3)
                await pilot.click("#sensor-update")
                await pilot.pause(0.5)
                self.assertIsInstance(app.screen, ChoiceDialog)
                self.assertIn(f"Discard {preparing['run_id']}", app.screen.options)

                choices = app.screen.query_one("#choices")
                choices.highlighted = 1
                await pilot.press("enter")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, ConfirmDialog)
                await pilot.click("#yes")
                await pilot.pause(0.5)
                coordinator.discard.assert_called_once_with(preparing["run_id"])
                self.assertNotIsInstance(app.screen, FleetUpdateDialog)

    async def test_fleet_request_dialog_returns_selection_without_credentials(self):
        from textual.app import App
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateDialog
        app = App()
        selected = []
        dialog = FleetUpdateDialog()
        async with app.run_test(size=(100, 40)) as pilot:
            app.push_screen(dialog, selected.append)
            await pilot.pause()
            dialog.query_one("#fleet-names").value = "sensor-a, sensor-b"
            dialog.query_one("#fleet-parallel").value = "3"
            await pilot.click("#fleet-start")
            await pilot.pause()
        self.assertEqual(len(selected), 1)
        self.assertIsInstance(selected[0], FleetRequest)
        self.assertEqual(selected[0].names, ["sensor-a", "sensor-b"])
        self.assertEqual(selected[0].parallel, 3)
        self.assertEqual(selected[0].password, "")
        self.assertNotIn("fleet-password", [widget.id for widget in dialog.query("*")])

    async def test_fleet_password_dialog_clears_password_when_cancelled(self):
        from textual.app import App
        from tpotctl.screens.fleet import FleetPasswordDialog
        app = App()
        dialog = FleetPasswordDialog()
        async with app.run_test(size=(100, 40)) as pilot:
            app.push_screen(dialog)
            await pilot.pause()
            password = dialog.query_one("#fleet-sudo-password")
            password.value = "secret-value"
            await pilot.click("#fleet-sudo-back")
            await pilot.pause()
        self.assertEqual(password.value, "")

    async def test_fleet_password_dialog_returns_passwordless_choice(self):
        from textual.app import App
        from tpotctl.screens.fleet import FleetCredentials, FleetPasswordDialog
        app = App()
        selected = []
        dialog = FleetPasswordDialog()
        async with app.run_test(size=(100, 40)) as pilot:
            app.push_screen(dialog, selected.append)
            await pilot.pause()
            dialog.query_one("#fleet-sudo-nopass").value = True
            await pilot.click("#fleet-sudo-continue")
            await pilot.pause()
        self.assertEqual(selected, [FleetCredentials(passwordless=True)])

    async def test_fleet_progress_requires_confirmation_before_starting(self):
        from tpotctl import app as tapp
        from tpotctl.screens.dialogs import ConfirmDialog
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        prepared = {
            "run_id": "f" * 32,
            "phase": "prepared",
            "version": "24.04.1",
            "targets": [{
                "name": "sensor-old-otter", "phase": "prepared", "previous_version": "24.04.0",
                "losses": ["@@tpot loss Elasticsearch data"], "warnings": [],
            }],
            "warnings": [],
        }
        completed = {
            "run_id": "f" * 32, "phase": "completed", "version": "24.04.1",
            "targets": [{
                "name": "sensor-old-otter", "phase": "completed", "previous_version": "24.04.0",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = []
        coordinator.store.read.return_value = completed
        coordinator.prepare.return_value = prepared
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        with mock.patch("tpotctl.screens.fleet.ops.require_linux_host"), \
                mock.patch("tpotctl.screens.fleet.ops.require_config_current"), \
                mock.patch.dict("os.environ", {"TPOT_REPO_URL": "https://example.test/tpotce",
                                               "TPOT_BRANCH": "main"}, clear=False):
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                app.push_screen(FleetUpdateScreen(FleetRequest([], False, 1, passwordless=True), backend.sensors(),
                                                  coordinator_factory=lambda **_kwargs: coordinator))
                await pilot.pause(0.4)
                self.assertIsInstance(app.screen, ConfirmDialog)
                self.assertIn("@@tpot loss Elasticsearch data", app.screen.body)
                coordinator.start.assert_not_called()
                await pilot.click("#no")
                await pilot.pause(0.2)
                self.assertEqual(app.screen.id, "fleet-update")
                coordinator.start.assert_not_called()

    async def test_fleet_resume_prompts_before_starting_a_prepared_retry(self):
        from tpotctl import app as tapp
        from tpotctl.screens.dialogs import ConfirmDialog
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        paused = {
            "run_id": "f" * 32,
            "phase": "paused",
            "version": "24.04.1",
            "targets": [{
                "name": "sensor-old-otter", "phase": "prepared", "attempt": 2,
                "previous_version": "24.04.0", "losses": ["Elasticsearch data may be lost."],
                "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = [paused]
        coordinator.store.read.return_value = paused
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.push_screen(FleetUpdateScreen(
                FleetRequest([], False, 1), backend.sensors(), run_id=paused["run_id"],
                coordinator_factory=lambda **_kwargs: coordinator))
            await pilot.pause(0.5)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIn("Elasticsearch data may be lost.", app.screen.body)
            coordinator.resume.assert_not_called()
            await pilot.click("#no")
            await pilot.pause(0.2)
            self.assertTrue(app.screen.query_one("#fleet-retry").display)
            self.assertEqual(str(app.screen.query_one("#fleet-retry").label), "Start retry")
            coordinator.start_retry.assert_not_called()

    async def test_interrupted_fleet_precheck_is_offered_for_discard(self):
        from tpotctl import app as tapp
        from tpotctl.screens.dialogs import ChoiceDialog
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        preparing = {
            "run_id": "a" * 32,
            "phase": "preparing",
            "targets": [{
                "name": "sensor-old-otter", "phase": "preparing", "previous_version": "",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = [preparing]
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.push_screen(FleetUpdateScreen(
                FleetRequest([], False, 1), backend.sensors(),
                coordinator_factory=lambda **_kwargs: coordinator))
            await pilot.pause(0.5)

            self.assertIsInstance(app.screen, ChoiceDialog)
            self.assertIn(f"Discard {preparing['run_id']}", app.screen.options)

    async def test_fleet_start_errors_are_shown_without_worker_crash(self):
        from tpotctl import app as tapp, fleet
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        prepared = {
            "run_id": "f" * 32,
            "phase": "prepared",
            "version": "24.04.1",
            "targets": [{
                "name": "sensor-old-otter", "phase": "prepared", "previous_version": "24.04.0",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = []
        coordinator.store.read.return_value = prepared
        coordinator.prepare.return_value = prepared
        coordinator.start.side_effect = fleet.FleetError("start failed")
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        with mock.patch("tpotctl.screens.fleet.ops.require_linux_host"), \
                mock.patch("tpotctl.screens.fleet.ops.require_config_current"), \
                mock.patch.dict("os.environ", {"TPOT_REPO_URL": "https://example.test/tpotce",
                                               "TPOT_BRANCH": "main"}, clear=False):
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                app.push_screen(FleetUpdateScreen(FleetRequest([], False, 1, passwordless=True), backend.sensors(),
                                                  coordinator_factory=lambda **_kwargs: coordinator))
                await pilot.pause(0.4)
                await pilot.click("#yes")
                await pilot.pause(0.4)
                self.assertIn("start failed", str(app.screen.query_one("#fleet-summary").render()))

    async def test_fleet_refresh_error_reenables_status_controls(self):
        import threading
        from tpotctl import app as tapp, fleet
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        running = {
            "run_id": "f" * 32,
            "phase": "running",
            "version": "24.04.1",
            "targets": [{
                "name": "sensor-old-otter", "phase": "running", "previous_version": "24.04.0",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = [running]
        coordinator.store.read.return_value = running
        release_refresh = threading.Event()

        def resume(*_args, **_kwargs):
            if coordinator.resume.call_count == 1:
                return running
            release_refresh.wait(5)
            raise fleet.FleetError("status unavailable")

        coordinator.resume.side_effect = resume
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.push_screen(FleetUpdateScreen(
                FleetRequest([], False, 1), backend.sensors(), run_id=running["run_id"],
                coordinator_factory=lambda **_kwargs: coordinator))
            await pilot.pause(0.5)
            self.assertFalse(app.screen.query_one("#fleet-refresh").disabled)
            app.screen.poll()
            await pilot.pause(0.1)
            self.assertTrue(app.screen.query_one("#fleet-refresh").disabled)
            self.assertTrue(app.screen.query_one("#fleet-back").disabled)
            release_refresh.set()
            await pilot.pause(0.4)
            self.assertFalse(app.screen.query_one("#fleet-refresh").disabled)
            self.assertFalse(app.screen.query_one("#fleet-back").disabled)
            self.assertIn("status unavailable", str(app.screen.query_one("#fleet-summary").render()))

    async def test_fleet_failure_decision_disables_controls_and_recovers_on_error(self):
        import threading
        from tpotctl import app as tapp, fleet
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        paused = {
            "run_id": "f" * 32,
            "phase": "paused",
            "version": "24.04.1",
            "targets": [{
                "name": "sensor-old-otter", "phase": "failed", "previous_version": "24.04.0",
                "losses": [], "warnings": [], "last_error": "update failed",
            }],
            "warnings": [],
        }
        release_decision = threading.Event()
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = [paused]
        coordinator.store.read.return_value = paused
        coordinator.resume.return_value = paused

        def resolve_failure(*_args, **_kwargs):
            release_decision.wait(5)
            raise fleet.FleetError("decision failed")

        coordinator.resolve_failure.side_effect = resolve_failure
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.push_screen(FleetUpdateScreen(
                FleetRequest([], False, 1, passwordless=True), backend.sensors(), run_id=paused["run_id"],
                coordinator_factory=lambda **_kwargs: coordinator))
            await pilot.pause(0.5)
            await pilot.click("#fleet-retry")
            await pilot.pause(0.1)
            self.assertTrue(app.screen.query_one("#fleet-retry").disabled)
            release_decision.set()
            await pilot.pause(0.4)
            self.assertFalse(app.screen.query_one("#fleet-retry").disabled)
            self.assertIn("decision failed", str(app.screen.query_one("#fleet-summary").render()))

    async def test_fleet_failure_actions_fit_an_80_column_terminal(self):
        from tpotctl import app as tapp
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        paused = {
            "run_id": "f" * 32,
            "phase": "paused",
            "version": "24.04.1",
            "targets": [{
                "name": "sensor-old-otter", "phase": "failed", "previous_version": "24.04.0",
                "archive": "/tmp/fleet-backup", "last_error": "update failed",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = [paused]
        coordinator.store.read.return_value = paused
        coordinator.resume.return_value = paused
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause(0.5)
            app.push_screen(FleetUpdateScreen(
                FleetRequest([], False, 1), backend.sensors(), run_id=paused["run_id"],
                coordinator_factory=lambda **_kwargs: coordinator))
            await pilot.pause(0.5)
            actions = app.screen.query_one("#fleet-actions")
            self.assertLessEqual(actions.virtual_size.width, actions.size.width)

    async def test_fleet_rollback_to_a_logstash_release_warns_first(self):
        from tpotctl import app as tapp
        from tpotctl.screens.dialogs import ConfirmDialog
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        paused = {
            "run_id": "f" * 32,
            "phase": "paused",
            "version": "24.04.2",
            "targets": [{
                "name": "sensor-old-otter", "phase": "failed", "previous_version": "24.04.1",
                "archive": "/tmp/fleet-backup", "last_error": "update failed",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = [paused]
        coordinator.store.read.return_value = paused
        coordinator.resume.return_value = paused
        coordinator.resolve_failure.return_value = paused
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.push_screen(FleetUpdateScreen(
                FleetRequest([], False, 1, passwordless=True), backend.sensors(), run_id=paused["run_id"],
                coordinator_factory=lambda **_kwargs: coordinator))
            await pilot.pause(0.5)
            await pilot.click("#fleet-rollback")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ConfirmDialog)
            self.assertIn(sensors.LOGSTASH_SENSOR, app.screen.body)
            self.assertIn("24.04.1", app.screen.title_text)
            coordinator.resolve_failure.assert_not_called()
            await pilot.click("#no")
            await pilot.pause(0.2)
            coordinator.resolve_failure.assert_not_called()
            await pilot.click("#fleet-rollback")
            await pilot.pause(0.2)
            await pilot.click("#yes")
            await pilot.pause(0.4)
            coordinator.resolve_failure.assert_called_once()
            self.assertEqual(coordinator.resolve_failure.call_args.args[1:], ("sensor-old-otter", "rollback"))

    async def test_fleet_rollback_to_a_fluent_bit_release_asks_nothing(self):
        from tpotctl import app as tapp
        from tpotctl.screens.dialogs import ConfirmDialog
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        paused = {
            "run_id": "f" * 32,
            "phase": "paused",
            "version": "24.04.3",
            "targets": [{
                "name": "sensor-old-otter", "phase": "failed", "previous_version": "24.04.2",
                "archive": "/tmp/fleet-backup", "last_error": "update failed",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = [paused]
        coordinator.store.read.return_value = paused
        coordinator.resume.return_value = paused
        coordinator.resolve_failure.return_value = paused
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.push_screen(FleetUpdateScreen(
                FleetRequest([], False, 1, passwordless=True), backend.sensors(), run_id=paused["run_id"],
                coordinator_factory=lambda **_kwargs: coordinator))
            await pilot.pause(0.5)
            await pilot.click("#fleet-rollback")
            await pilot.pause(0.4)
            self.assertNotIsInstance(app.screen, ConfirmDialog)
            coordinator.resolve_failure.assert_called_once()

    async def test_fleet_active_run_offers_abort(self):
        from tpotctl import app as tapp
        from tpotctl.screens.fleet import FleetRequest, FleetUpdateScreen
        _repo, backend = self.heartbeat_backend()
        running = {
            "run_id": "f" * 32,
            "phase": "running",
            "version": "24.04.1",
            "targets": [{
                "name": "sensor-old-otter", "phase": "running", "previous_version": "24.04.0",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = mock.Mock()
        coordinator.store.list_runs.return_value = [running]
        coordinator.store.read.return_value = running
        coordinator.resume.return_value = running
        app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.push_screen(FleetUpdateScreen(
                FleetRequest([], False, 1), backend.sensors(), run_id=running["run_id"],
                coordinator_factory=lambda **_kwargs: coordinator))
            await pilot.pause(0.5)
            self.assertTrue(app.screen.query_one("#fleet-abort").display)

    async def test_take_over_from_lswebpasswd(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import ConfirmDialog
        repo = checkout(self, ls_web_user="")
        lswebpasswd = os.path.join(repo, "data", "nginx", "conf", "lswebpasswd")
        with open(lswebpasswd + ".lost", "w") as handle:
            handle.write(f"{OTTER}\n{LYNX}\n")

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def images(self):
                return []

            def backups(self):
                return []

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status()

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(0.3)
            app.query_one("ContentSwitcher").current = "sensors"
            await pilot.pause(0.5)
            self.assertEqual(app.query_one("#sensors-table").row_count, 2)
            button = app.query_one("#sensor-import")
            self.assertTrue(app.query_one("#sensors-import-actions").display)
            self.assertEqual(str(button.label), "Take over 2 sensors")
            await pilot.click("#sensor-import")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ConfirmDialog)
            await pilot.click("#yes")
            await pilot.pause(0.5)
            self.assertFalse(app.query_one("#sensors-import-actions").display)
        self.assertEqual([u.name for u in sensors.Registry(repo).entries()], ["sensor-old-otter", "sensor-old-lynx"])
        self.assertEqual(sensors.Registry(repo).get("sensor-old-lynx").source, "imported")

    async def test_the_dialog_proposes_only_a_hive_address_it_found(self):
        from textual.app import App
        from tpotctl.screens.dialogs import SensorDialog
        for found in ("", "192.168.1.2"):
            with self.subTest(found=found):
                dialog = SensorDialog(sensors.check_address, sensors.check_user, lambda _host: found,
                                      sensors.check_hive_address)
                app = App()
                async with app.run_test() as pilot:
                    app.push_screen(dialog)
                    await pilot.pause()
                    dialog.query_one("#sensor-host").value = "sensor_1"
                    await pilot.pause(0.8)                  # the proposal comes after a pause in typing
                    placeholder = dialog.query_one("#sensor-hive").placeholder
                self.assertNotIn("[]", placeholder)
                self.assertIn("IPv4", placeholder)
                if found:
                    self.assertIn(f"[{found}]", placeholder)

    async def test_the_proposal_of_the_hive_address_does_not_hold_up_typing(self):
        """default_hive_address resolves the name (DNS) and runs ip route get: once after a pause in
        typing, in a thread, never per key in the UI thread; a late answer for an older host is dropped."""
        import threading
        from textual.app import App
        from tpotctl.screens.dialogs import SensorDialog
        calls = []

        def default_hive(host):
            calls.append((host, threading.current_thread() is threading.main_thread()))
            return "192.168.1.2" if host == "sensor1" else "10.9.9.9"
        from tpotctl.screens import dialogs
        dialog = SensorDialog(sensors.check_address, sensors.check_user, default_hive, sensors.check_hive_address)
        app = App()
        # a pause the keys of the test always beat, also on a busy machine (a key every 0.4 s would not)
        with mock.patch.object(dialogs, "PROPOSAL_PAUSE", 3.0):
            async with app.run_test() as pilot:
                app.push_screen(dialog)
                await pilot.pause()
                dialog.query_one("#sensor-host").focus()
                await pilot.press(*"sensor1")
                self.assertEqual(calls, [])                 # not while typing
                for _ in range(200):                        # once after the pause
                    if calls:
                        break
                    await pilot.pause(0.05)
                await pilot.pause(0.2)
                placeholder = dialog.query_one("#sensor-hive").placeholder
        self.assertEqual(calls, [("sensor1", False)])
        self.assertIn("[192.168.1.2]", placeholder)

    async def test_a_late_proposal_for_an_older_host_is_dropped(self):
        import threading
        from textual.app import App
        from tpotctl.screens.dialogs import SensorDialog
        slow = threading.Event()

        def default_hive(host):
            if host == "old":
                slow.wait(5)
                return "10.9.9.9"
            return "192.168.1.2"
        dialog = SensorDialog(sensors.check_address, sensors.check_user, default_hive, sensors.check_hive_address)
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(dialog)
            await pilot.pause()
            dialog.query_one("#sensor-host").value = "old"
            await pilot.pause(0.6)                      # its lookup hangs
            dialog.query_one("#sensor-host").value = "new"
            await pilot.pause(0.6)
            self.assertIn("[192.168.1.2]", dialog.query_one("#sensor-hive").placeholder)
            slow.set()                                  # the answer for "old" comes last
            await pilot.pause(0.3)
            placeholder = dialog.query_one("#sensor-hive").placeholder
        self.assertIn("[192.168.1.2]", placeholder)
        self.assertNotIn("10.9.9.9", placeholder)

    async def test_leaving_a_right_hive_address_keeps_the_problem_of_another_field(self):
        from textual.app import App
        from tpotctl.screens.dialogs import SensorDialog
        dialog = SensorDialog(sensors.check_address, sensors.check_user, lambda _host: "", sensors.check_hive_address)
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(dialog)
            await pilot.pause()
            dialog.query_one("#sensor-host").value = "10.0.0.5"
            dialog.query_one("#sensor-user").value = "no user"
            dialog.query_one("#sensor-hive").value = "192.168.1.2"
            await pilot.click("#sensor-deploy")
            await pilot.pause()
            self.assertIn("not a user name", str(dialog.query_one("#sensor-hint").render()))
            dialog.query_one("#sensor-hive").focus()
            await pilot.pause()
            dialog.query_one("#sensor-user").focus()
            await pilot.pause()
            self.assertIn("not a user name", str(dialog.query_one("#sensor-hint").render()))

    async def test_the_dialogs_check_the_hive_address_before_deploy(self):
        """IPv4 or a name: the placeholder says so, an IPv6 address is marked when the field is left,
        the mark goes when the value is right; for a new sensor and for one that is registered."""
        from textual.app import App
        from tpotctl.screens.dialogs import SensorDialog, SensorEditDialog
        new = lambda: SensorDialog(sensors.check_address, sensors.check_user, lambda _host: "",  # noqa: E731
                                   sensors.check_hive_address)
        edit = lambda: SensorEditDialog(sensors.Sensor("s1", host="192.0.2.9", ssh_user="t"),  # noqa: E731
                                        lambda **values: None, sensors.check_hive_address)
        for make, field, hint, after in ((new, "#sensor-hive", "#sensor-hint", "#sensor-nopass"),
                                         (edit, "#edit-hive", "#edit-hint", "#edit-host")):
            with self.subTest(field=field):
                dialog = make()
                app = App()
                async with app.run_test() as pilot:
                    app.push_screen(dialog)
                    await pilot.pause()
                    self.assertIn("IPv4 or name", dialog.query_one(field).placeholder)
                    dialog.query_one(field).focus()
                    await pilot.press(*"fd00::1")
                    dialog.query_one(after).focus()           # leaves the field
                    await pilot.pause()
                    self.assertIn("IPv6", str(dialog.query_one(hint).render()))
                    self.assertIs(app.screen, dialog)
                    dialog.query_one(field).value = "192.168.1.2"
                    await pilot.pause()
                    self.assertEqual(str(dialog.query_one(hint).render()).strip(), "")

    async def test_hive_address_dialog_proposes_and_checks(self):
        """The address this HIVE reaches its sensors from is the proposal (looked up in a thread, an empty
        field takes it), an IPv6 address stays in the dialog, without a proposal an empty field is a problem."""
        import threading
        from textual.app import App
        from tpotctl.screens.dialogs import HIVE_PLACEHOLDER, HiveAddressDialog
        results, threads = [], []

        def propose():
            threads.append(threading.current_thread() is threading.main_thread())
            return "198.51.100.7"
        dialog = HiveAddressDialog(["192.0.2.1"], propose, sensors.check_hive_address)
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(dialog, results.append)
            await pilot.pause(0.3)
            self.assertEqual(dialog.query_one("#hive-address").placeholder, f"{HIVE_PLACEHOLDER} [198.51.100.7]")
            dialog.query_one("#hive-address").value = "2001:db8::1"
            await pilot.click("#hive-address-change")
            await pilot.pause(0.3)          # a button takes no second click while it is -active (0.2 s)
            self.assertIn("IPv6", flat(str(dialog.query_one("#hive-address-hint").render())))
            self.assertIs(app.screen, dialog)
            dialog.query_one("#hive-address").value = ""
            await pilot.click("#hive-address-change")
            await pilot.pause()
        self.assertEqual(threads, [False])
        self.assertEqual(results, [{"address": "198.51.100.7", "nopass": False}])
        for propose in (None, lambda: "", lambda: 1 / 0):
            with self.subTest(propose=propose):
                dialog = HiveAddressDialog(["192.0.2.1"], propose, sensors.check_hive_address)
                app = App()
                async with app.run_test() as pilot:
                    app.push_screen(dialog, results.append)
                    await pilot.pause(0.3)
                    self.assertEqual(dialog.query_one("#hive-address").placeholder, HIVE_PLACEHOLDER)
                    await pilot.click("#hive-address-change")
                    await pilot.pause()
                    self.assertIs(app.screen, dialog)
                    self.assertIn("address", flat(str(dialog.query_one("#hive-address-hint").render())))
        self.assertEqual(len(results), 1)

    async def test_hive_address_dialog_names_the_current_addresses(self):
        from textual.app import App
        from textual.widgets import Static
        from tpotctl.screens.dialogs import HiveAddressDialog
        for current in (["192.0.2.1"], ["192.0.2.1", "hive.example.org"]):
            with self.subTest(current=current):
                dialog = HiveAddressDialog(current)
                app = App()
                async with app.run_test() as pilot:
                    app.push_screen(dialog)
                    await pilot.pause()
                    text = flat(" ".join(str(widget.render()) for widget in dialog.query(Static)))
                for address in current:
                    self.assertIn(address, text)

    def hive_address_backend(self):
        """The Sensors page of a HIVE with one sensor whose place is known (host, login, HIVE address)."""
        repo, backend = self.heartbeat_backend()
        registry = sensors.Registry(repo, hasher=fake_hash)
        registry.sensors()
        registry.update("sensor-old-otter", host="192.0.2.10", ssh_user="debian", hive_address="192.0.2.1")
        return repo, backend

    async def test_sensors_pane_runs_hive_address(self):
        from tpotctl import app as tapp
        from tpotctl.screens.dialogs import HiveAddressDialog
        _repo, backend = self.hive_address_backend()
        commands, hosts = [], []
        app = tapp.TpotApp(backend=backend, runner=lambda command, cwd=None: commands.append((command, cwd)) or 0)
        with mock.patch.object(sensors, "default_hive_address",
                               side_effect=lambda host: hosts.append(host) or "198.51.100.7"):
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                app.goto("sensors")
                await pilot.pause(0.4)
                pane = app.query_one("#sensors")
                for nopass, extra in ((False, []), (True, ["--no-become-pass"])):
                    with self.subTest(nopass=nopass):
                        await pilot.click("#sensor-hive-address")
                        await pilot.pause(0.3)
                        self.assertIsInstance(app.screen, HiveAddressDialog)
                        self.assertEqual(app.screen.current, ["192.0.2.1"])
                        app.screen.query_one("#hive-address-nopass").value = nopass
                        with mock.patch.object(pane, "load") as load:
                            await pilot.click("#hive-address-change")
                            await pilot.pause(0.3)
                        self.assertEqual(commands[-1], ([tapp.LAUNCHER, "sensors", "hive-address", "198.51.100.7"]
                                                        + extra, tapp.REPO_DIR))
                        load.assert_called_with()
                await pilot.click("#sensor-hive-address")
                await pilot.pause(0.3)
                await pilot.click("#hive-address-back")
                await pilot.pause(0.3)
        self.assertEqual(len(commands), 2)
        self.assertEqual(hosts, ["192.0.2.10"] * 3)

    async def test_hive_address_button_disabled_without_sensors(self):
        """Off without a registered sensor (nothing to change: a sensor of lswebpasswd alone is taken over
        first) and when the registry cannot be read."""
        from tpotctl import app as tapp
        repo, backend = self.hive_address_backend()
        empty = checkout(self, ls_web_user="")
        found = checkout(self, ls_web_user="")
        with open(os.path.join(found, "data", "nginx", "conf", "lswebpasswd.lost"), "w") as handle:
            handle.write(f"{LYNX}\n")

        def unreadable():
            raise sensors.SensorsError("sensors.json cannot be read")
        for label, registry in (("registered", lambda: sensors.Registry(repo, hasher=fake_hash)),
                                ("none", lambda: sensors.Registry(empty, hasher=fake_hash)),
                                ("taken over first", lambda: sensors.Registry(found, hasher=fake_hash)),
                                ("unreadable", unreadable)):
            with self.subTest(label):
                backend.sensors = registry
                app = tapp.TpotApp(backend=backend, runner=lambda *args, **kwargs: 0)
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.pause(0.5)
                    app.goto("sensors")
                    await pilot.pause(0.4)
                    self.assertEqual(app.query_one("#sensor-hive-address").disabled, label != "registered")
                    if label == "taken over first":
                        self.assertEqual(app.query_one("#sensors-table").row_count, 1)

    async def test_edit_where_a_sensor_is(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import SensorEditDialog
        repo = checkout(self)

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={})

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(0.3)
            app.goto("sensors")
            await pilot.pause(0.5)
            table = app.query_one("#sensors-table")
            table.move_cursor(row=0)
            name = app.query_one("#sensors").selected()
            await pilot.click("#sensor-edit")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, SensorEditDialog)
            self.assertIs(app.screen.check_hive, sensors.check_hive_address)
            app.screen.query_one("#edit-host").value = "10.0.0.9"
            app.screen.query_one("#edit-port").value = "2222"
            await pilot.click("#edit-save")
            await pilot.pause(0.4)
        sensor = sensors.Registry(repo).get(name)
        self.assertEqual((sensor.host, sensor.ssh_port), ("10.0.0.9", 2222))

    async def test_enter_on_a_sensor_row_offers_its_actions(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import ChoiceDialog, SensorEditDialog
        repo = checkout(self)

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={})

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(0.3)
            app.goto("sensors")
            await pilot.pause(0.5)
            table = app.query_one("#sensors-table")
            table.focus()
            table.move_cursor(row=1)
            name = app.query_one("#sensors").selected()
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ChoiceDialog)
            self.assertTrue(name in app.screen.title_text, app.screen.title_text)
            await pilot.press("escape")
            await pilot.pause(0.3)
            self.assertIs(app.focused, table)
            await pilot.press("enter")
            await pilot.pause(0.3)
            await pilot.press("enter")              # the first one: Edit
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, SensorEditDialog)

    async def test_row_action_stays_on_its_row(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import ChoiceDialog, SensorEditDialog
        repo = checkout(self)
        notes = []

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def sensors(self):
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={})

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        app.notify = lambda message, **kwargs: notes.append(message)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(0.3)
            app.goto("sensors")
            await pilot.pause(0.6)
            pane = app.query_one("#sensors")
            table = app.query_one("#sensors-table")
            table.focus()
            table.move_cursor(row=1)
            name = pane.selected()
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, ChoiceDialog)
            table.move_cursor(row=0)                    # as if a reload had moved the cursor
            await pilot.press("enter")                  # Edit
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, SensorEditDialog)
            self.assertEqual(app.screen.sensor.name, name)
            await pilot.press("escape")
            await pilot.pause(0.3)
            table.focus()
            table.move_cursor(row=1)
            name = pane.selected()
            await pilot.press("enter")
            await pilot.pause(0.3)
            sensors.Registry(repo, hasher=fake_hash).revoke(name)       # gone meanwhile
            pane.load()
            await pilot.pause(0.5)
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertNotIsInstance(app.screen, SensorEditDialog)
            self.assertTrue(any("no longer there" in n for n in notes), notes)

    async def test_revoke_loads_the_sensors_once(self):
        from tpotctl import app as tapp, ops
        repo = checkout(self)
        calls = []

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "STANDARD", "HIVE", "active", repo)

            def containers(self):
                return []

            def config_stamp(self):
                import threading
                import time
                if threading.current_thread() is not threading.main_thread():
                    time.sleep(0.3)             # a slow worker: the stamp it notes comes late
                return ops.config_stamp(repo)

            def sensors(self):
                calls.append(1)
                return sensors.Registry(repo, hasher=fake_hash)

            def sensor_status(self, days=7):
                return sensors.Status(sensors={})

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)

        async def no_settings_refresh():        # it takes long enough to hide the race otherwise
            return None
        app.refresh_config = no_settings_refresh
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(0.3)
            app.goto("sensors")
            await pilot.pause(0.8)
            before = len(calls)
            app.query_one("#sensors-table").move_cursor(row=0)
            await pilot.click("#sensor-remove")
            await pilot.pause(0.3)
            await pilot.click("#yes")
            await pilot.pause(1.0)
        self.assertEqual(len(calls) - before, 1, calls)

    def test_sensor_pages_only_on_a_hive(self):
        from tpotctl import app as tapp

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "SENSOR"

        app = tapp.TpotApp(backend=Backend(), runner=None)
        # on a SENSOR both pages stay in the menu, locked with the reason
        self.assertIn("sensors", app.locked)
        self.assertIn("users", app.locked)
        self.assertNotIn("status", app.locked)


if __name__ == "__main__":
    unittest.main()


class DeployComposeTest(unittest.TestCase):
    """installer/install/deploy.yml: a customizer file of the SENSOR is rebuilt, not replaced by sensor.yml."""

    PLAY = os.path.join(REPO_DIR, "installer", "install", "deploy.yml")
    NAME = "Put the sensor compose file in place"

    def setUp(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("needs PyYAML")
        with open(self.PLAY, encoding="utf-8") as handle:
            self.tasks = yaml.safe_load(handle)[0]["tasks"]
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.checkout = os.path.join(self.tmp, "tpotce")
        os.makedirs(self.checkout)
        for name in ("compose", "tpotctl"):
            shutil.copytree(os.path.join(REPO_DIR, name), os.path.join(self.checkout, name),
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
        for name in ("version", "env.example"):
            shutil.copy(os.path.join(REPO_DIR, name), self.checkout)
        self.sensor = os.path.join(self.checkout, "compose", "sensor.yml")
        self.compose = os.path.join(self.checkout, "docker-compose.yml")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        os.symlink(sys.executable, os.path.join(self.bin, "python3"))
        # HOME is the sensor's for ~/tpot_backups; DOCKER_CONFIG keeps the compose plugin of the real one
        self.env = dict(os.environ, XDG_DATA_HOME=os.path.join(self.tmp, "data"), HOME=self.tmp,
                        DOCKER_CONFIG=os.environ.get("DOCKER_CONFIG") or os.path.expanduser("~/.docker"),
                        PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        self.backups = os.path.join(self.tmp, "tpot_backups")

    def task(self, name):
        found = [task for task in self.tasks if task.get("name") == name]
        self.assertEqual(len(found), 1, name)
        return found[0]

    def run_task(self):
        task = self.task(self.NAME)
        self.assertEqual(task["args"]["executable"], "/bin/bash")
        self.assertNotIn("become", task)
        script = (task["ansible.builtin.shell"]
                  .replace("{{ remote_sensor_yml_path }}", self.sensor)
                  .replace("{{ remote_docker_compose_path }}", self.compose)
                  .replace("~/tpotce", self.checkout))
        self.assertNotIn("{{", script)
        proc = subprocess.run(["bash", "-c", script], env=self.env, capture_output=True, text=True,
                              timeout=120, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([name for name in os.listdir(self.checkout) if name.startswith(".")], [],
                         "no temp file is left")
        return proc.stdout.strip()

    def customize(self, base, *args):
        proc = subprocess.run([sys.executable, os.path.join(self.checkout, "compose", "customizer.py"),
                               "--base", base, *args, "-o", self.compose],
                              env=self.env, capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(self.compose, encoding="utf-8") as handle:
            return handle.read()

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_plain_compose_is_replaced(self):
        with open(self.compose, "w", encoding="utf-8") as handle:
            handle.write("services:\n  mine:\n    image: busybox\n")
        self.assertEqual(self.run_task(), "copied")
        self.assertEqual(self.read(self.compose), self.read(self.sensor))

    def test_sensor_customizer_file_is_rebuilt(self):
        self.assertIn("\n  ewsposter:\n", self.read(self.sensor))
        self.customize("SENSOR", "--remove", "ewsposter")
        self.assertEqual(self.run_task(), "rebuilt")
        text = self.read(self.compose)
        self.assertNotIn("\n  ewsposter:\n", text)
        self.assertRegex(text, r"(?m)^# customizer: version=\S+ base=SENSOR$")
        self.assertFalse(os.path.exists(self.backups))

    def test_comment_lines_are_kept_in_a_backup(self):
        text = "# my sensor\n\n" + self.customize("SENSOR", "--remove", "ewsposter")
        with open(self.compose, "w", encoding="utf-8") as handle:
            handle.write(text)
        out = self.run_task().splitlines()
        self.assertEqual(len(out), 2, out)
        self.assertEqual(out[0], "rebuilt-kept")
        path = out[1]
        self.assertEqual(os.path.dirname(path), self.backups)
        self.assertRegex(os.path.basename(path), r"^docker-compose_sensor_\d{14}\.yml$")
        self.assertEqual(self.read(path), text)
        rebuilt = self.read(self.compose)
        self.assertNotIn("# my sensor", rebuilt)
        self.assertNotIn("\n  ewsposter:\n", rebuilt)

    def test_a_backup_path_with_a_space_is_named_whole(self):
        home = os.path.join(self.tmp, "my home")
        os.makedirs(home)
        self.env["HOME"] = home
        text = "# my sensor\n\n" + self.customize("SENSOR", "--remove", "ewsposter")
        with open(self.compose, "w", encoding="utf-8") as handle:
            handle.write(text)
        out = self.run_task().splitlines()
        # the warning names stdout_lines[1], Ansible's splitlines() of stdout: the whole path, space and all
        backups = os.path.join(home, "tpot_backups")
        kept = os.listdir(backups)
        self.assertEqual(len(kept), 1, kept)
        path = os.path.join(backups, kept[0])
        self.assertEqual(out, ["rebuilt-kept", path])
        self.assertEqual(self.read(path), text)

    def test_comment_lines_without_a_backup_keep_the_file(self):
        text = "# my sensor\n\n" + self.customize("SENSOR", "--remove", "ewsposter")
        with open(self.compose, "w", encoding="utf-8") as handle:
            handle.write(text)
        with open(self.backups, "w", encoding="utf-8") as handle:
            handle.write("not a folder\n")
        self.assertEqual(self.run_task(), "kept-failed")
        self.assertEqual(self.read(self.compose), text)

    def test_edited_customizer_file_is_kept(self):
        text = self.customize("SENSOR", "--remove", "ewsposter").replace("restart: always", "restart: no", 1)
        with open(self.compose, "w", encoding="utf-8") as handle:
            handle.write(text)
        self.assertEqual(self.run_task(), "kept-edited")
        self.assertEqual(self.read(self.compose), text)

    def test_hive_customizer_file_is_replaced(self):
        self.assertIn("base=STANDARD", self.customize("STANDARD", "--remove", "ewsposter"))
        self.assertEqual(self.run_task(), "copied")
        self.assertEqual(self.read(self.compose), self.read(self.sensor))

    def test_rebuild_failure_keeps_the_file(self):
        text = self.customize("SENSOR", "--remove", "ewsposter")
        os.unlink(os.path.join(self.bin, "python3"))
        with open(os.path.join(self.bin, "python3"), "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(self.bin, "python3"), 0o755)
        self.assertEqual(self.run_task(), "kept-failed")
        self.assertEqual(self.read(self.compose), text)

    def test_only_a_written_file_is_stamped_and_a_kept_one_warned_about(self):
        compose = self.task(self.NAME)
        register = compose["register"]
        first = register + ".stdout.split() | first"
        written = "['rebuilt', 'rebuilt-kept', 'copied']"
        self.assertIn(first, str(compose["changed_when"]))
        self.assertIn(written, str(compose["changed_when"]))
        names = [task.get("name") for task in self.tasks]
        stamp = self.task("Stamp the generated compose file on the sensor")
        self.assertLess(names.index(self.NAME), names.index(stamp["name"]))
        when = " and ".join(stamp["when"]) if isinstance(stamp["when"], list) else stamp["when"]
        self.assertIn("my_sensor_generated.stat.isreg", when)
        self.assertIn(first, when)
        self.assertIn(written, when)
        warn = [task for task in self.tasks if "ansible.builtin.debug" in task
                and register in str(task.get("when", ""))]
        self.assertEqual(len(warn), 2)
        kept = [task for task in warn if "kept-edited" in str(task["when"])]
        self.assertEqual(len(kept), 1)
        self.assertIn(first, str(kept[0]["when"]))
        self.assertIn("T-Pot runs it as it is", str(kept[0]["ansible.builtin.debug"]))
        backup = [task for task in warn if "rebuilt-kept" in str(task["when"])]
        self.assertEqual(len(backup), 1)
        self.assertIn(first, str(backup[0]["when"]))
        message = str(backup[0]["ansible.builtin.debug"])
        self.assertIn("The lines above '# T-Pot: CUSTOM' in the docker-compose.yml of the sensor "
                      "are not in the rebuilt one, they are kept in ", message)
        self.assertIn(register + ".stdout_lines[1]", message)
        self.assertNotIn(".stdout.split()[1]", message)


class SensorCertPlayTest(unittest.TestCase):
    """installer/install/sensor_cert.yml: the certificate goes over, the shipper of the SENSOR restarts."""

    PLAY = os.path.join(REPO_DIR, "installer", "install", "sensor_cert.yml")

    def setUp(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("needs PyYAML")
        with open(self.PLAY, encoding="utf-8") as handle:
            self.tasks = yaml.safe_load(handle)[0]["tasks"]

    def task(self, needle):
        found = [task for task in self.tasks if needle in str(task.get("ansible.builtin.command", {}))
                 or needle in str(task.get("ansible.builtin.debug", {}))]
        self.assertEqual(len(found), 1, needle)
        return found[0]

    @staticmethod
    def when(task):
        when = task.get("when", [])
        return " and ".join(when) if isinstance(when, list) else str(when)

    def test_sensor_cert_restarts_fluentbit(self):
        restart = self.task("docker restart fluentbit")
        self.assertEqual(self.when(restart).replace(" ", ""), "'fluentbit'intpot_services")
        names = [task.get("name", "") for task in self.tasks]
        copy = names.index("Copy the HIVE certificate to the sensor")
        self.assertLess(copy, names.index(restart["name"]))
        self.assertTrue(any("tpot_shipper_since" in str(task.get("ansible.builtin.assert", {}))
                            for task in self.tasks[:copy]), "the play checks its extra var first")

    def test_sensor_cert_legacy_fallback_only_for_old_release(self):
        legacy = self.task("docker restart logstash")
        self.assertIn("legacy SENSOR", legacy["name"])
        condition = self.when(legacy).replace(" ", "")
        self.assertIn("'logstash'intpot_services", condition)
        self.assertIn("tpot_legacy_sensor", condition)
        self.assertNotIn("fluentbit", condition)
        facts = next(task["ansible.builtin.set_fact"] for task in self.tasks
                     if "tpot_legacy_sensor" in task.get("ansible.builtin.set_fact", {}))
        rule = str(facts["tpot_legacy_sensor"]).replace(" ", "")
        self.assertIn("version(tpot_shipper_since,'<')", rule)
        self.assertIn("isnone", rule)                        # an unknown version is a legacy SENSOR

        e9 = self.task("still runs Logstash")
        condition = self.when(e9).replace(" ", "")
        self.assertIn("'logstash'intpot_services", condition)
        self.assertIn("nottpot_legacy_sensor", condition)
        from tpotctl import settings
        self.assertIn("This SENSOR still runs Logstash: the HIVE refuses its events until ",
                      e9["ansible.builtin.debug"]["msg"])
        self.assertIn("uses fluentbit (tpot customize or update.sh --adapt-compose).",
                      e9["ansible.builtin.debug"]["msg"])
        self.assertTrue(settings.LOGSTASH_ON_SENSOR)

        none = self.task("runs no fluentbit service")
        self.assertEqual(none["ansible.builtin.debug"]["msg"],
                         "This SENSOR runs no fluentbit service: no logs are sent to the HIVE.")
        condition = self.when(none).replace(" ", "")
        self.assertIn("'fluentbit'notintpot_services", condition)
        self.assertIn("'logstash'notintpot_services", condition)

    def test_sensor_cert_reads_the_sensor_itself(self):
        with open(self.PLAY, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("{{ ansible_facts.user_dir }}/tpotce/version", text)
        self.assertIn("TPOT_DOCKER_COMPOSE", text)
        self.assertNotIn(sensors.SHIPPER_SINCE, text)        # only sensors.py names the release

    @unittest.skipUnless(shutil.which("ansible-playbook"), "needs ansible-playbook")
    def test_sensor_cert_syntax(self):
        done = subprocess.run(["ansible-playbook", "--syntax-check", "-i", "localhost,", self.PLAY],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class SensorProbeTest(unittest.TestCase):
    """tpotctl/sensor_probe.py reads a sensor over SSH: standard library only, it changes and creates nothing."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = self.temporary.name
        self.repo = os.path.join(self.home, "tpotce")
        self.os_release = os.path.join(self.home, "os-release")
        self.hold = os.path.join(self.home, "90-tpot-migration.conf")
        self.write("version", "24.04.1\n")
        self.write(".env", "TPOT_TYPE=SENSOR\nTPOT_HIVE_IP=192.0.2.1\n")
        self.write("docker-compose.yml", "services:\n\n  tpotinit:\n    image: a\n\n  logstash:\n    image: b\n")
        with open(self.os_release, "w", encoding="utf-8") as handle:
            handle.write('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\n')

    def write(self, name, text):
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def probe(self):
        return sensor_probe.probe(self.home, os_release=self.os_release, hold=self.hold)

    def test_probe_reads_a_24041_sensor(self):
        self.assertEqual(self.probe(), {"version": "24.04.1", "os_id": "debian", "hold": False,
                                        "update_locked": False, "hive_ip": "192.0.2.1",
                                        "services": ["tpotinit", "logstash"], "role": "SENSOR"})

    def test_probe_reads_the_role(self):
        self.write(".env", 'TPOT_TYPE=SENSOR\nTPOT_TYPE: "HIVE"\n')
        self.assertEqual(self.probe()["role"], "HIVE")
        self.write(".env", "TPOT_HIVE_IP=192.0.2.1\n")
        self.assertIsNone(self.probe()["role"])

    def test_probe_drops_comments_as_compose_does(self):
        cases = {"TPOT_HIVE_IP=192.0.2.1 # old\n": "192.0.2.1",
                 'TPOT_HIVE_IP: "hive.example" # old\n': "hive.example",
                 "TPOT_HIVE_IP='a # b'\n": "a # b",
                 "TPOT_HIVE_IP=a#b\n": "a#b"}
        for text, value in cases.items():
            with self.subTest(text):
                self.write(".env", "TPOT_TYPE=SENSOR # set by deploy\n" + text)
                self.assertEqual((self.probe()["hive_ip"], self.probe()["role"]), (value, "SENSOR"))

    # what docker compose v5.7.1 reads (--env-file, the parser of env_file): on a sensor compose hands the
    # .env to tpotinit (env_file) and to Fluent Bit (${TPOT_HIVE_IP}), its reading is the one that counts
    COMPOSE_READS = {"  TPOT_HIVE_IP = 192.0.2.1\n": "192.0.2.1",
                     "TPOT_HIVE_IP:\t192.0.2.1\n": "192.0.2.1",
                     "TPOT_HIVE_IP:192.0.2.1\n": "192.0.2.1",
                     "TPOT_HIVE_IP :192.0.2.1\n": "192.0.2.1",
                     "export TPOT_HIVE_IP=192.0.2.1\n": "192.0.2.1",
                     'TPOT_HIVE_IP="x" # c\n': "x",
                     "TPOT_HIVE_IP=\n": "",
                     "#TPOT_HIVE_IP=1\n": None}

    def test_probe_reads_the_lines_compose_reads(self):
        for text, value in self.COMPOSE_READS.items():
            with self.subTest(text):
                self.write(".env", text)
                self.assertEqual(self.probe()["hive_ip"], value)

    @unittest.skipUnless(shutil.which("docker"), "needs docker compose")
    def test_probe_reads_what_docker_compose_reads(self):
        compose = self.write("compose.yml", 'services:\n  t:\n    image: busybox\n'
                                            '    environment:\n      A: "${TPOT_HIVE_IP-unset}"\n')
        cases = dict(self.COMPOSE_READS)
        cases.update({"TPOT_HIVE_IP=192.0.2.1 # old\n": "192.0.2.1", "TPOT_HIVE_IP: \"hive.example\" # old\n":
                      "hive.example", "TPOT_HIVE_IP='a # b'\n": "a # b", "TPOT_HIVE_IP=a#b\n": "a#b",
                      'TPOT_HIVE_IP=a\nTPOT_HIVE_IP: "b"\n': "b"})
        for text, value in cases.items():
            with self.subTest(text):
                path = self.write(".env", text)
                done = subprocess.run(["docker", "compose", "--env-file", path, "-f", compose, "config",
                                       "--format", "json"], capture_output=True, text=True, timeout=60,
                                      env={key: item for key, item in os.environ.items() if key != "TPOT_HIVE_IP"})
                if done.returncode:
                    self.skipTest(f"docker compose config: {done.stderr.strip()}")
                read = json.loads(done.stdout)["services"]["t"]["environment"]["A"]
                self.assertEqual(self.probe()["hive_ip"], None if read == "unset" else read)
                self.assertEqual(self.probe()["hive_ip"], value)

    def test_probe_takes_the_last_line_and_both_forms(self):
        self.write(".env", 'TPOT_HIVE_IP=a\n#TPOT_HIVE_IP=c\nTPOT_HIVE_IP: "b"\n# TPOT_HIVE_IP=d\n')
        self.assertEqual(self.probe()["hive_ip"], "b")
        self.write(".env", "TPOT_HIVE_IP = 'e' \nTPOT_HIVE_IPS=f\n")
        self.assertEqual(self.probe()["hive_ip"], "e")
        self.write(".env", "TPOT_TYPE=SENSOR\n")
        self.assertIsNone(self.probe()["hive_ip"])
        os.remove(os.path.join(self.repo, ".env"))
        self.assertIsNone(self.probe()["hive_ip"])

    def test_probe_follows_tpot_docker_compose(self):
        self.write(os.path.join("compose", "x.yml"), "services:\n  tpotinit:\n  fluentbit:\n")
        self.write(".env", "TPOT_HIVE_IP=192.0.2.1\nTPOT_DOCKER_COMPOSE=./compose/x.yml\n")
        self.assertEqual(self.probe()["services"], ["tpotinit", "fluentbit"])
        elsewhere = os.path.join(self.home, "elsewhere.yml")
        with open(elsewhere, "w", encoding="utf-8") as handle:
            handle.write("services:\n  nginx:\n")
        self.write(".env", f'TPOT_DOCKER_COMPOSE: "{elsewhere}"\n')
        self.assertEqual(self.probe()["services"], ["nginx"])
        self.write(".env", "TPOT_DOCKER_COMPOSE=./missing.yml\n")
        self.assertEqual(self.probe()["services"], [])

    def test_probe_sees_a_held_update_lock_and_creates_none(self):
        lock = os.path.join(self.home, ".local", "share", "tpotce", "update.lock")
        self.assertFalse(self.probe()["update_locked"])
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".local")))
        os.makedirs(os.path.dirname(lock))
        with open(lock, "w", encoding="utf-8"):
            pass
        self.assertFalse(self.probe()["update_locked"])
        holder = os.open(lock, os.O_RDWR)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        self.assertTrue(self.probe()["update_locked"])
        fcntl.flock(holder, fcntl.LOCK_UN)
        self.assertFalse(self.probe()["update_locked"])
        # update.sh refuses a link there, so no update holds it; the probe does not follow it
        target = lock + ".target"
        os.rename(lock, target)
        os.symlink(target, lock)
        fcntl.flock(holder, fcntl.LOCK_EX)
        self.assertFalse(self.probe()["update_locked"])

    def test_probe_hold_and_os_release(self):
        with open(self.hold, "w", encoding="utf-8"):
            pass
        with open(self.os_release, "w", encoding="utf-8") as handle:
            handle.write('ID="ubuntu"\nID_LIKE=debian\n')
        probe = self.probe()
        self.assertTrue(probe["hold"])
        self.assertEqual(probe["os_id"], "ubuntu")
        os.remove(self.os_release)
        os.remove(os.path.join(self.repo, "version"))
        probe = self.probe()
        self.assertEqual((probe["os_id"], probe["version"]), ("", ""))
        # the guard update.sh refuses on, written by migration_runtime
        with open(os.path.join(REPO_DIR, "update.sh"), encoding="utf-8") as handle:
            self.assertIn(f"TPOT_MIGRATION_GUARD:-{sensor_probe.HOLD}}}", handle.read())

    def test_probe_runs_from_stdin(self):
        with open(sensor_probe.__file__, encoding="utf-8") as handle:
            source = handle.read()
        done = subprocess.run([sys.executable, "-"], input=source, capture_output=True, text=True, timeout=60,
                              cwd=self.home, env={"HOME": self.home, "PATH": os.environ.get("PATH", "")})
        self.assertEqual(done.returncode, 0, done.stderr)
        answer = json.loads(done.stdout)
        self.assertEqual(set(answer), {"version", "os_id", "hold", "update_locked", "hive_ip", "services", "role"})
        self.assertEqual((answer["version"], answer["hive_ip"]), ("24.04.1", "192.0.2.1"))

    def test_probe_sensor_parses_and_fails_clearly(self):
        sensor = sensors.Sensor("s1", host="192.0.2.10", ssh_user="tsec", ssh_port=64295)
        answer = {"version": "24.04.2", "os_id": "debian", "hold": False, "update_locked": False,
                  "hive_ip": None, "services": ["fluentbit"], "role": "SENSOR"}
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, json.dumps(answer), "")

        self.assertEqual(sensors.probe_sensor(sensor, run=run), sensors.Probe(**answer))
        command, kwargs = calls[0]
        self.assertEqual(command, sensors.ssh_command("192.0.2.10", "tsec", "python3", "-", port=64295))
        self.assertEqual(command[-2:], ["python3", "-"])
        with open(sensor_probe.__file__, encoding="utf-8") as handle:
            self.assertEqual(kwargs["input"], handle.read())
        self.assertEqual(kwargs["timeout"], 15)

        def answering(result):
            def run(command, **kwargs):
                if isinstance(result, BaseException):
                    raise result
                return result
            return run

        refused = "ssh: connect to host 192.0.2.10 port 64295: Connection refused\n"
        for result in (subprocess.CompletedProcess([], 255, "", refused), subprocess.TimeoutExpired("ssh", 15),
                       OSError("no ssh"), subprocess.CompletedProcess([], 0, "x", ""),
                       subprocess.CompletedProcess([], 0, json.dumps(dict(answer, hold="no")), ""),
                       subprocess.CompletedProcess([], 0, json.dumps(dict(answer, services=[1])), ""),
                       subprocess.CompletedProcess([], 0, json.dumps({"version": "24.04.2"}), "")):
            with self.subTest(result=result), self.assertRaisesRegex(sensors.SensorsError, "^s1 cannot be checked"):
                sensors.probe_sensor(sensor, run=answering(result))
        with self.assertRaisesRegex(sensors.SensorsError, "Connection refused"):
            sensors.probe_sensor(sensor, run=answering(subprocess.CompletedProcess([], 255, "", refused)))

    def test_probe_sensor_cleans_what_the_sensor_says(self):
        sensor = sensors.Sensor("s1", host="192.0.2.10", ssh_user="tsec", ssh_port=64295)
        answer = {"version": "24.04.2", "os_id": "debian", "hold": False, "update_locked": False,
                  "hive_ip": "192.0.2.1", "services": ["tpotinit", "fluentbit"], "role": "SENSOR"}

        def answering(stdout, stderr="", code=0):
            return lambda command, **kwargs: subprocess.CompletedProcess(command, code, stdout, stderr)

        # what a login file of the sensor prints comes before the answer
        greeting = "Welcome to T-Pot\n\n" + json.dumps(answer) + "\n\n"
        self.assertEqual(sensors.probe_sensor(sensor, run=answering(greeting)), sensors.Probe(**answer))
        for key, value in (("os_id", "Ubuntu"), ("os_id", "debian\x1b[2J"), ("os_id", "d" * 65),
                           ("version", "24.04.2\x1b]0;x\x07"), ("version", "2" * 129),
                           ("hive_ip", "192.0.2.1\x1b[31m"), ("hive_ip", "h" * 254), ("hive_ip", "hive\nx"),
                           ("role", "SENSOR\r"), ("role", "S" * 65), ("services", ["flu entbit"]),
                           ("services", ["\x1b"]), ("services", ["s" * 65]), ("services", ["s"] * 257)):
            with self.subTest(key=key, value=value), \
                    self.assertRaisesRegex(sensors.SensorsError, "^s1 cannot be checked"):
                sensors.probe_sensor(sensor, run=answering(json.dumps(dict(answer, **{key: value}))))
        for key, value in (("os_id", ""), ("version", ""), ("hive_ip", ""), ("hive_ip", "a # b"),
                           ("role", None), ("services", [])):
            with self.subTest(key=key, value=value):
                sensors.probe_sensor(sensor, run=answering(json.dumps(dict(answer, **{key: value}))))
        noisy = "ssh: \x1b[31mconnect\x07 to host" + "x" * 500 + "\n"
        with self.assertRaises(sensors.SensorsError) as caught:
            sensors.probe_sensor(sensor, run=answering("", noisy, 255))
        message = str(caught.exception)
        self.assertIn("ssh: [31mconnect to host", message)
        self.assertFalse(any(not char.isprintable() for char in message), message)
        self.assertLess(len(message), 300)


class HiveAddressRulesTest(unittest.TestCase):
    """The rules of tpot sensors hive-address that need neither SSH nor Elasticsearch."""

    SINCE = 1_800_000_000.0

    @staticmethod
    def probe(**changes):
        values = dict(version="24.04.2", os_id="debian", hold=False, update_locked=False,
                      hive_ip="192.0.2.1", services=["tpotinit", "fluentbit"], role="SENSOR")
        values.update(changes)
        return sensors.Probe(**values)

    def test_check_kind(self):
        self.assertEqual(sensors.check_kind(self.probe()), "heartbeat")
        self.assertEqual(sensors.check_kind(self.probe(version="24.04.1", services=["logstash"])), "refused")
        self.assertEqual(sensors.check_kind(self.probe(services=["logstash"])), "logstash")
        self.assertEqual(sensors.check_kind(self.probe(services=[])), "none")

    def test_hive_skip_reason_order(self):
        self.assertEqual(sensors.hive_skip_reason(self.probe(), "s1"), "")
        version = sensors.hive_skip_reason(self.probe(version="", role="HIVE", os_id="ubuntu", hold=True,
                                                      update_locked=True), "s1")
        self.assertIn("~/tpotce/version cannot be read", version)
        # a host of the registry that is a HIVE (this one, say): a restart there stops its Elasticsearch
        role = sensors.hive_skip_reason(self.probe(role="HIVE", os_id="ubuntu", hold=True, update_locked=True), "s1")
        for text in ("no SENSOR", "TPOT_TYPE=HIVE"):
            self.assertIn(text, role)
        self.assertIn("no TPOT_TYPE", sensors.hive_skip_reason(self.probe(role=None), "s1"))
        system = sensors.hive_skip_reason(self.probe(os_id="ubuntu", hold=True, update_locked=True), "s1")
        for text in ("ubuntu", "Debian", "tpot sensors add", "tpot migrate"):
            self.assertIn(text, system)
        hold = sensors.hive_skip_reason(self.probe(hold=True, update_locked=True), "s1")
        self.assertIn("migration", hold)
        locked = sensors.hive_skip_reason(self.probe(update_locked=True), "s1")
        self.assertIn("an update runs there, try again after it", locked)
        for reason in (version, role, system, hold, locked):
            self.assertTrue(reason.startswith("s1"), reason)
        self.assertEqual(len({version, role, system, hold, locked}), 5)
        self.assertIn("an unknown system", sensors.hive_skip_reason(self.probe(os_id=""), "s1"))
        self.assertIn("~/tpotce/version", sensors.hive_skip_reason(self.probe(version="24.04.2\nx"), "s1"))

    def test_confirmed_by_heartbeat(self):
        beats = {"s1": sensors.Heartbeat(self.SINCE + 5, "24.04.2", "B")}
        self.assertTrue(sensors.confirmed("heartbeat", "s1", beats, {}, self.SINCE, "A"))
        self.assertTrue(sensors.confirmed("heartbeat", "s1", beats, {}, self.SINCE, ""))
        at_once = {"s1": sensors.Heartbeat(self.SINCE, "24.04.2", "B")}
        self.assertTrue(sensors.confirmed("heartbeat", "s1", at_once, {}, self.SINCE, "A"))
        before = {"s1": sensors.Heartbeat(self.SINCE - 1, "24.04.2", "B")}
        self.assertFalse(sensors.confirmed("heartbeat", "s1", before, {}, self.SINCE, "A"))
        self.assertFalse(sensors.confirmed("heartbeat", "s2", beats, {}, self.SINCE, "A"))

    def test_confirmed_ignores_a_buffered_heartbeat(self):
        buffered = {"s1": sensors.Heartbeat(self.SINCE + 30, "24.04.2", "A")}
        self.assertFalse(sensors.confirmed("heartbeat", "s1", buffered, {}, self.SINCE, "A"))

    def test_confirmed_by_refusal(self):
        self.assertTrue(sensors.confirmed("refused", "s1", {}, {"s1": self.SINCE}, self.SINCE, ""))
        self.assertFalse(sensors.confirmed("refused", "s1", {}, {"s1": self.SINCE - 1}, self.SINCE, ""))
        self.assertFalse(sensors.confirmed("refused", "s2", {}, {"s1": self.SINCE}, self.SINCE, ""))
        beats = {"s1": sensors.Heartbeat(self.SINCE + 5, "24.04.2", "B")}
        for kind in ("logstash", "none"):
            self.assertFalse(sensors.confirmed(kind, "s1", beats, {"s1": self.SINCE + 5}, self.SINCE, "A"), kind)

    def test_fetch_heartbeats_raises_sensors_error(self):
        def refused(request, timeout):
            raise urllib.error.URLError("connection refused")

        def garbage(request, timeout):
            return io.BytesIO(b"[]")

        class Cut(io.BytesIO):
            def read(self, *args):
                raise http.client.IncompleteRead(b"")

        def cut(request, timeout):
            return Cut()

        def empty(request, timeout):
            self.assertIn("/tpot-sensor-status/_search", request.full_url)
            return io.BytesIO(json.dumps({"timed_out": False, "_shards": {"failed": 0},
                                          "hits": {"hits": []}}).encode())

        with self.assertRaisesRegex(sensors.SensorsError, "connection refused"):
            sensors.fetch_heartbeats("http://es.test", refused)
        with self.assertRaises(sensors.SensorsError):
            sensors.fetch_heartbeats("http://es.test", garbage)
        with self.assertRaisesRegex(sensors.SensorsError, "cannot be asked"):
            sensors.fetch_heartbeats("http://es.test", cut)
        self.assertEqual(sensors.fetch_heartbeats("http://es.test", empty), ({}, {}))

    def test_hive_address_command(self):
        play = os.path.join("/repo", "installer", "install", "sensor_hive.yml")
        self.assertEqual(sensors.hive_address_command("/tmp/inv", "198.51.100.7", repo_dir="/repo"),
                         ["ansible-playbook", play, "-i", "/tmp/inv", "-e", "tpot_hive_address=198.51.100.7",
                          "--ask-become-pass"])
        self.assertEqual(sensors.hive_address_command("/tmp/inv", "hive.example", False, repo_dir="/repo"),
                         ["ansible-playbook", play, "-i", "/tmp/inv", "-e", "tpot_hive_address=hive.example"])
        for address in ("2001:db8::1", "a b", "x tpot_shipper_since=1"):
            with self.subTest(address), self.assertRaises(sensors.SensorsError):
                sensors.hive_address_command("/tmp/inv", address)


class SensorHivePlayTest(unittest.TestCase):
    """installer/install/sensor_hive.yml: the certificate, TPOT_HIVE_IP of the sensor, a restart of T-Pot."""

    PLAY = os.path.join(REPO_DIR, "installer", "install", "sensor_hive.yml")
    CERT_PLAY = os.path.join(REPO_DIR, "installer", "install", "sensor_cert.yml")
    NAMES = ["Check the HIVE address", "Copy the HIVE certificate to the sensor", "Read the T-Pot configuration",
             "Set the HIVE address", "Restart T-Pot"]
    ADDRESS = "198.51.100.7"

    def setUp(self):
        if yaml is None:
            self.skipTest("needs PyYAML")
        with open(self.PLAY, encoding="utf-8") as handle:
            self.play = yaml.safe_load(handle)[0]
        self.tasks = {task["name"]: task for task in self.play["tasks"]}

    def run_play(self, env_file, address=ADDRESS, tags=("--tags", "tpot_env")):
        return subprocess.run(["ansible-playbook", self.PLAY, "-c", "local", "-i", "localhost,", *tags,
                               "-e", f"tpot_env_file={env_file}", "-e", f"tpot_hive_address={address}"],
                              capture_output=True, text=True, timeout=300, env=dict(os.environ, ANSIBLE_NOCOLOR="1"))

    def test_play_order_and_become(self):
        self.assertEqual([task["name"] for task in self.play["tasks"]], self.NAMES)
        self.assertEqual([task["name"] for task in self.play["tasks"] if task.get("become")],
                         ["Copy the HIVE certificate to the sensor", "Restart T-Pot"])
        self.assertNotIn("become", self.play)
        with open(self.CERT_PLAY, encoding="utf-8") as handle:
            cert_play = yaml.safe_load(handle)[0]
        copy = next(task for task in cert_play["tasks"] if task["name"] == "Copy the HIVE certificate to the sensor")
        self.assertEqual(self.tasks["Copy the HIVE certificate to the sensor"]["ansible.builtin.copy"],
                         copy["ansible.builtin.copy"])
        for key in ("local_nginx_cert_path", "remote_cert_path"):
            self.assertEqual(self.play["vars"][key], cert_play["vars"][key])
        restart = self.tasks["Restart T-Pot"]["ansible.builtin.systemd"]
        self.assertEqual((restart["name"], restart["state"]), ("tpot", "restarted"))
        for name in ("Read the T-Pot configuration", "Set the HIVE address"):
            self.assertIn("tpot_env", self.tasks[name]["tags"])
        line = self.tasks["Set the HIVE address"]["ansible.builtin.lineinfile"]
        self.assertIs(line["create"], False)
        self.assertEqual(line["path"], "{{ tpot_env_file }}")

    def test_play_regex_is_the_probe_regex(self):
        line = self.tasks["Set the HIVE address"]["ansible.builtin.lineinfile"]
        self.assertEqual(line["regexp"], sensor_probe.HIVE_IP.pattern)
        # the form of the line comes from the same pattern as the line it replaces
        self.assertIn(f"regex_findall('{sensor_probe.HIVE_IP.pattern}', multiline=True)", line["line"])
        regexp = line["regexp"]
        for text in ("TPOT_HIVE_IP=1", "TPOT_HIVE_IP =1", 'TPOT_HIVE_IP: "x"', "TPOT_HIVE_IP\t=1", "TPOT_HIVE_IP:1",
                     "  TPOT_HIVE_IP=1", "export TPOT_HIVE_IP=1", "TPOT_HIVE_IP :1"):
            self.assertTrue(re.search(regexp, text), text)
        for text in ("#TPOT_HIVE_IP=", "TPOT_HIVE_IPS=", "XTPOT_HIVE_IP=", "TPOT_HIVE_IP", "exportTPOT_HIVE_IP=1",
                     "# export TPOT_HIVE_IP=1"):
            self.assertFalse(re.search(regexp, text), text)

    def test_play_asserts_the_address(self):
        check = self.tasks["Check the HIVE address"]
        self.assertIn("always", check["tags"])
        check = check["ansible.builtin.assert"]
        self.assertIn("tpot sensors hive-address", check["fail_msg"])
        that = " ".join(str(condition) for condition in check["that"])
        self.assertIn("tpot_hive_address is defined", that)
        self.assertIn("^[A-Za-z0-9][A-Za-z0-9.-]*$", that)
        if not shutil.which("ansible-playbook"):
            return
        for tags in ((), ("--tags", "tpot_env")):
            with self.subTest(tags), tempfile.TemporaryDirectory() as folder:
                env_file = os.path.join(folder, "settings")
                with open(env_file, "w", encoding="utf-8") as handle:
                    handle.write("TPOT_HIVE_IP=192.0.2.1\n")
                done = self.run_play(env_file, address="a/b", tags=tags)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("tpot sensors hive-address", done.stdout + done.stderr)
                with open(env_file, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "TPOT_HIVE_IP=192.0.2.1\n")

    @unittest.skipUnless(shutil.which("ansible-playbook"), "needs ansible-playbook")
    def test_play_syntax(self):
        done = subprocess.run(["ansible-playbook", "--syntax-check", "-i", "localhost,", self.PLAY],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    @unittest.skipUnless(shutil.which("ansible-playbook"), "needs ansible-playbook")
    def test_play_keeps_the_form_locally(self):
        new = self.ADDRESS
        cases = {
            "equals": ("TPOT_TYPE=SENSOR\n#TPOT_HIVE_IP=old\nTPOT_HIVE_IP=192.0.2.1\nTPOT_HIVE_USER=x\n",
                       f"TPOT_TYPE=SENSOR\n#TPOT_HIVE_IP=old\nTPOT_HIVE_IP={new}\nTPOT_HIVE_USER=x\n"),
            "colon": ('TPOT_TYPE: "SENSOR"\nTPOT_HIVE_IP: "192.0.2.1"\n', f'TPOT_TYPE: "SENSOR"\nTPOT_HIVE_IP: "{new}"\n'),
            "missing": ("TPOT_TYPE=SENSOR\n", f"TPOT_TYPE=SENSOR\nTPOT_HIVE_IP={new}\n"),
            "last": ('TPOT_HIVE_IP=a\nTPOT_HIVE_IP: "b"\n', f'TPOT_HIVE_IP=a\nTPOT_HIVE_IP: "{new}"\n'),
            # compose reads these too; the line keeps its = or :, not its indent, export or comment
            "tight": ("TPOT_HIVE_IP:192.0.2.1\n", f'TPOT_HIVE_IP: "{new}"\n'),
            "export": ('TPOT_HIVE_IP: "a"\nexport TPOT_HIVE_IP=192.0.2.1 # old\n', f'TPOT_HIVE_IP: "a"\nTPOT_HIVE_IP={new}\n'),
            "indented": ("  TPOT_HIVE_IP = 192.0.2.1\nTPOT_HIVE_USER=x\n", f"TPOT_HIVE_IP={new}\nTPOT_HIVE_USER=x\n"),
        }
        for name, (before, after) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as home:
                os.makedirs(os.path.join(home, "tpotce"))
                env_file = os.path.join(home, "tpotce", ".env")
                with open(env_file, "w", encoding="utf-8") as handle:
                    handle.write(before)
                os.chmod(env_file, 0o640)
                done = self.run_play(env_file)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                with open(env_file, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), after)
                self.assertEqual(stat.S_IMODE(os.stat(env_file).st_mode), 0o640)
                self.assertEqual(sensor_probe.probe(home)["hive_ip"], new)


class FakeClock:
    """time of tpotctl.cli for the wait of tpot sensors hive-address: sleep() moves it on."""

    def __init__(self, now=1000.0):
        self.now, self.sleeps = now, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def __getattr__(self, name):
        import time
        return getattr(time, name)


def probe(**changes):
    """What a 24.04.2 SENSOR with Fluent Bit says of itself (probe_sensor), its TPOT_HIVE_IP the old address."""
    values = dict(version="24.04.2", os_id="debian", hold=False, update_locked=False, hive_ip="192.0.2.1",
                  services=["tpotinit", "fluentbit", "cowrie"], role="SENSOR")
    values.update(changes)
    return sensors.Probe(**values)


@unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
class HiveAddressCliTest(unittest.TestCase):
    """tpot sensors hive-address: one question after the checks of every sensor, then the play, the wait until
    the HIVE hears of each sensor at its new address, and what each sensor has read back."""
    NEW = "198.51.100.7"
    OLD = "192.0.2.1"
    run_cli = CliTest.run_cli               # the checkout of CliTest, not its tests

    def setUp(self):
        CliTest.setUp(self)
        registry = sensors.Registry(self.repo)
        for number in range(1, 6):
            name = f"s{number}"
            if name != "s4":                    # s4 has no access to this HIVE
                registry.grant(name, "pw")
            registry.record(sensors.Sensor(name, host=f"10.0.0.{number}", ssh_user="debian", hive_address=self.OLD,
                                           source="deployed"))
        patch = mock.patch("tpotctl.ops.require_linux_host")
        self.linux = patch.start()
        self.addCleanup(patch.stop)

    def registry(self):
        return sensors.Registry(self.repo)

    def by_hand(self, names):
        return (f"By hand for {names}: set TPOT_HIVE_IP={self.NEW} in ~/tpotce/.env on the sensor, copy "
                f"{self.registry().cert} of this HIVE to ~/tpotce/data/hive.crt there, restart T-Pot (sudo systemctl "
                f"restart tpot), then here for each: tpot sensors set NAME --hive-address {self.NEW}")

    def hive(self, *args, ssh=None, probes=None, beats=None, info=None, renew="reloaded", missed=(), runs=(),
             retry=None, code=0, stdin=None):
        """tpot sensors hive-address ARGS with fakes at the edges: check_ssh by host (default ok), probe_sensor
        per name (a list, before and after the play, default the old and then the new address), the answers of
        fetch_heartbeats in turn (the last one stays; an exception is raised), nginx.crt `info` (default it
        covers both addresses), renew_certificate returning `renew`, copy_certificate missing `missed`, the saved
        fleet runs (an exception is raised) and ansible-playbook writing `retry` and waiting through `code` (an
        exit code or the steps of Child)."""
        from types import SimpleNamespace
        seen = SimpleNamespace(ssh=[], probes=[], beats=0, events=[], children=[], folders=[], inventories=[],
                               clock=FakeClock())
        probes = dict(probes or {})
        beats = list(beats or [({}, {})])
        info = info or cert([f"IP:{self.OLD}", f"IP:{self.NEW}"])

        def check_ssh(host, user, run=None, port=sensors.SSH_PORT):
            seen.ssh.append(host)
            return (ssh or {}).get(host, "ok")

        def probe_sensor(sensor, run=None):
            seen.probes.append(sensor.name)
            answers = probes.setdefault(sensor.name, [probe(), probe(hive_ip=self.NEW)])
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(answer, BaseException):
                raise answer
            return answer

        def fetch_heartbeats():
            seen.beats += 1
            answer = beats.pop(0) if len(beats) > 1 else beats[0]
            if isinstance(answer, BaseException):
                raise answer
            return answer

        def renew_certificate(registry, addresses, console, yes, reload, *, current):
            seen.events.append(("renew", list(addresses), yes, reload, list(current)))
            return renew

        def copy_certificate(chosen, become_pass):
            seen.events.append(("copy", [s.name for s in chosen]))
            return [s for s in chosen if s.name in missed]

        def list_runs(_store):
            if isinstance(runs, BaseException):
                raise runs
            return list(runs)

        def popen(command, **kwargs):
            env = kwargs.get("env") or {}
            folder = env.get("ANSIBLE_RETRY_FILES_SAVE_PATH")
            seen.folders.append(folder)
            inventory = command[command.index("-i") + 1]
            with open(inventory, encoding="utf-8") as handle:
                seen.inventories.append((inventory, handle.read()))
            if folder and retry is not None:
                with open(os.path.join(folder, "sensor_hive.retry"), "w", encoding="utf-8") as handle:
                    handle.write("".join(f"{name}\n" for name in retry))
            seen.events.append(("play", command))
            seen.children.append(Child(command, kwargs, code if isinstance(code, list) else [code]))
            return seen.children[-1]
        with mock.patch.object(sensors, "check_ssh", side_effect=check_ssh), \
                mock.patch.object(sensors, "probe_sensor", side_effect=probe_sensor), \
                mock.patch.object(sensors, "fetch_heartbeats", side_effect=fetch_heartbeats), \
                mock.patch.object(sensors, "cert_info", return_value=info), \
                mock.patch("tpotctl.cli.renew_certificate", side_effect=renew_certificate), \
                mock.patch("tpotctl.cli.copy_certificate", side_effect=copy_certificate), \
                mock.patch("tpotctl.cli.HIVE_WAIT", 30, create=True), \
                mock.patch("tpotctl.cli.HIVE_POLL", 10, create=True), \
                mock.patch("tpotctl.cli.time", seen.clock), \
                mock.patch("tpotctl.fleet.RunStore.list_runs", autospec=True, side_effect=list_runs), \
                mock.patch("subprocess.Popen", side_effect=popen):
            exit_code, text = self.run_cli("sensors", "hive-address", *args, stdin=stdin)
        return exit_code, flat(text), seen

    def addresses(self):
        return {s.name: s.hive_address for s in self.registry().sensors()}

    def test_invalid_address_exits_2_before_ssh(self):
        before = self.addresses()
        code, text, seen = self.hive("2001:db8::1", "-y")
        self.assertEqual(code, 2, text)
        self.assertIn("'2001:db8::1' is an IPv6 address", text)
        self.assertEqual((seen.ssh, seen.beats, seen.events), ([], 0, []))
        self.assertEqual(self.addresses(), before)

    def test_ca_certificate_exits_2_before_any_change(self):
        info = cert([f"IP:{self.OLD}"], self_signed=False)
        code, text, seen = self.hive(self.NEW, "-y", info=info)
        self.assertEqual(code, 2, text)
        self.assertIn(flat(sensors.ca_renew_refusal(info, os.path.dirname(self.registry().cert))), text)
        self.assertEqual((seen.ssh, seen.events), ([], []))

    def test_es_unreachable_exits_1_before_any_change(self):
        down = sensors.SensorsError("Sensor heartbeats on http://127.0.0.1:64298 cannot be asked (refused)")
        code, text, seen = self.hive(self.NEW, "-y", beats=[down])
        self.assertEqual(code, 1, text)
        self.assertIn("cannot be asked (refused): start T-Pot on this HIVE first", text)
        self.assertEqual((seen.ssh, seen.events), ([], []))

    def test_fleet_state_unreadable_exits_1_before_any_change(self):
        from tpotctl import fleet
        broken = fleet.FleetError("The fleet state directory contains an invalid entry.")
        code, text, seen = self.hive(self.NEW, "-y", runs=broken)
        self.assertEqual(code, 1, text)
        self.assertIn("The fleet state directory contains an invalid entry.", text)
        self.assertEqual((seen.ssh, seen.events), ([], []))

    def test_precheck_names_every_skip_and_no_changes_nothing(self):
        before = self.addresses()
        code, text, seen = self.hive(self.NEW, ssh={"10.0.0.2": "key"}, probes={"s3": [probe(os_id="ubuntu")]},
                                     runs=[{"phase": "running", "targets": [{"name": "s5"}]}], stdin=TTY("n\n"))
        self.assertEqual(code, 1, text)
        self.assertEqual(seen.events, [])                           # no play, no certificate
        self.assertEqual(self.addresses(), before)
        self.assertEqual(seen.ssh, ["10.0.0.1", "10.0.0.2", "10.0.0.3"])     # not s4 (access), not s5 (fleet)
        self.assertEqual(seen.probes, ["s1", "s3"])
        self.assertIn(f"s1: TPOT_HIVE_IP {self.OLD} becomes {self.NEW}", text)
        self.assertIn("s2: no key login on debian@10.0.0.2, run: ssh-copy-id -p 64295 debian@10.0.0.2", text)
        self.assertIn("s3 runs on ubuntu: T-Pot supports only Debian", text)
        self.assertIn("s4 is skipped, it has no access to this HIVE", text)
        self.assertIn("s5 is in a saved fleet update, finish it first: tpot sensors update --resume, or drop it: "
                      "tpot sensors update --discard", text)
        for name in ("sensor-old-lynx", "sensor-old-otter"):
            self.assertIn(f"{name} is skipped, add where it is: tpot sensors set {name} --host ... --ssh-user ...",
                          text)
        self.assertIn(f"Set {self.NEW} on 1 sensor(s) and restart T-Pot there?", text)

    def test_no_targets_ends_with_1_without_a_question(self):
        code, text, seen = self.hive(self.NEW, "s4", stdin=TTY())
        self.assertEqual(code, 1, text)
        self.assertIn("s4 is skipped, it has no access to this HIVE", text)
        self.assertNotIn("[y/N]", text)
        self.assertEqual(seen.events, [])

    def test_no_question_without_terminal_exits_2(self):
        code, text, seen = self.hive(self.NEW, "s1")
        self.assertEqual(code, 2, text)
        self.assertIn("Add --yes to do it without a terminal.", text)
        self.assertEqual(seen.events, [])

    def test_heartbeat_confirms_and_updates_the_registry(self):
        beats = [({"s1": sensors.Heartbeat(received_at=900.0, started_at="A")}, {}),
                 ({"s1": sensors.Heartbeat(received_at=1005.0, started_at="B")}, {})]
        code, text, seen = self.hive(self.NEW, "s1", "-y", beats=beats)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.registry().get("s1").hive_address, self.NEW)
        self.assertEqual(self.registry().get("s2").hive_address, self.OLD)
        self.assertIn(f"s1 sends to {self.NEW}.", text)
        self.assertIn(f"Back to the old address: tpot sensors hive-address {self.OLD} s1", text)
        self.assertNotIn("By hand", text)
        command = seen.children[0].args
        self.assertEqual(command[:2], ["ansible-playbook", os.path.join(REPO_DIR, "installer", "install",
                                                                         "sensor_hive.yml")])
        self.assertIn(f"tpot_hive_address={self.NEW}", command)
        self.assertIn("--ask-become-pass", command)
        inventory, content = seen.inventories[0]
        self.assertEqual(content.splitlines()[1:], ["s1 ansible_host=10.0.0.1 ansible_user=debian ansible_port=64295"])
        self.assertFalse(os.path.exists(inventory))
        self.assertFalse(os.path.exists(seen.folders[0]))
        self.assertEqual(seen.clock.sleeps, [])                     # confirmed with the first answer

    def test_no_become_pass_leaves_the_sudo_question_out(self):
        beats = [({}, {}), ({"s1": sensors.Heartbeat(received_at=1005.0, started_at="B")}, {})]
        code, text, seen = self.hive(self.NEW, "s1", "-y", "--no-become-pass", beats=beats)
        self.assertEqual(code, 0, text)
        self.assertNotIn("--ask-become-pass", seen.children[0].args)

    def test_buffered_heartbeat_does_not_confirm(self):
        beats = [({"s1": sensors.Heartbeat(received_at=900.0, started_at="A")}, {}),
                 ({"s1": sensors.Heartbeat(received_at=1005.0, started_at="A")}, {})]
        code, text, seen = self.hive(self.NEW, "s1", "-y", beats=beats)
        self.assertEqual(code, 1, text)
        self.assertEqual(self.registry().get("s1").hive_address, self.NEW)     # the sensor has it
        self.assertIn("s1 is not confirmed yet", text)
        self.assertIn(self.by_hand("s1"), text)
        self.assertEqual(seen.clock.sleeps, [10, 10, 10])          # HIVE_POLL up to HIVE_WAIT
        self.assertEqual(seen.beats, 5)

    def test_old_sensor_is_confirmed_by_refusal(self):
        old = dict(version="24.04.1", services=["tpotinit", "logstash", "cowrie"])
        beats = [({}, {"s1": 800.0}), ({}, {"s1": 1003.0})]
        code, text, _seen = self.hive(self.NEW, "s1", "-y", beats=beats,
                                      probes={"s1": [probe(**old), probe(hive_ip=self.NEW, **old)]})
        self.assertEqual(code, 0, text)
        self.assertIn("s1 reaches this HIVE, update needed: tpot sensors update s1", text)
        self.assertEqual(self.registry().get("s1").hive_address, self.NEW)

    def test_old_sensor_without_an_event_says_where_it_shows_up(self):
        old = dict(version="24.04.1", services=["tpotinit", "logstash", "cowrie"])
        code, text, _seen = self.hive(self.NEW, "s1", "-y",
                                      probes={"s1": [probe(**old), probe(hive_ip=self.NEW, **old)]})
        self.assertEqual(code, 1, text)
        self.assertIn("s1 is not confirmed yet", text)
        self.assertIn("update needed (events refused)", text)

    def test_logstash_on_a_new_release_is_changed_not_awaited(self):
        new = dict(services=["tpotinit", "logstash", "cowrie"])
        code, text, seen = self.hive(self.NEW, "s1", "-y",
                                     probes={"s1": [probe(**new), probe(hive_ip=self.NEW, **new)]})
        self.assertEqual(code, 1, text)
        self.assertEqual(seen.beats, 1)                             # only before: nothing to wait for
        self.assertIn("~/tpotce/update.sh --adapt-compose", text)
        self.assertEqual(self.registry().get("s1").hive_address, self.NEW)
        self.assertNotIn("By hand", text)

    def test_renew_keeps_the_sans_of_the_others_and_sends_them_the_certificate(self):
        registry = self.registry()
        registry.update("s2", hive_address="hive.example")
        beats = [({}, {}), ({"s1": sensors.Heartbeat(received_at=1005.0, started_at="B")}, {})]
        info = cert([f"IP:{self.OLD}"])
        cert_by_hand = ("By hand for {}: copy " + registry.cert + " of this HIVE to ~/tpotce/data/hive.crt on the "
                        "sensor, then restart T-Pot there (sudo systemctl restart tpot).")
        code, text, seen = self.hive(self.NEW, "s1", "-y", beats=beats, info=info)
        self.assertEqual(code, 0, text)
        self.assertEqual([event[0] for event in seen.events], ["renew", "play", "copy"])
        self.assertEqual(seen.events[0], ("renew", [self.OLD, "hive.example", self.NEW], True, True,
                                          [f"IP:{self.OLD}"]))
        self.assertEqual(seen.events[2], ("copy", ["s2", "s3", "s5"]))         # s4 has no access
        self.assertIn("Only the new certificate goes to: s2, s3, s5", text)
        self.assertIn("s2, s3, s5 have the new certificate.", text)
        self.assertIn(cert_by_hand.format("sensor-old-lynx, sensor-old-otter"), text)   # access, no host

        code, text, seen = self.hive(self.NEW, "s1", "-y", beats=beats, info=info, missed=["s3"])
        self.assertEqual(code, 1, text)
        self.assertIn("s2, s5 have the new certificate.", text)
        self.assertIn(cert_by_hand.format("s3, sensor-old-lynx, sensor-old-otter"), text)

    def test_renew_leaves_the_certificate_out_where_an_update_or_a_migration_holds_the_sensor(self):
        """sensor_cert.yml restarts Fluent Bit: that would start it where a fleet update, an update or a
        migration keeps T-Pot stopped."""
        beats = [({}, {}), ({"s1": sensors.Heartbeat(received_at=1005.0, started_at="B")}, {})]
        info = cert([f"IP:{self.OLD}"])
        later = "{} gets the new certificate once the update or migration there is done: tpot sensors cert --distribute {}"
        fleet_run = [{"phase": "running", "targets": [{"name": "s2"}]}]
        for s5, receivers in ((probe(), ["s5"]), (probe(hold=True), None)):
            with self.subTest(s5_hold=s5.hold):
                code, text, seen = self.hive(self.NEW, "s1", "s3", "-y", beats=beats, info=info, runs=fleet_run,
                                             probes={"s3": [probe(update_locked=True)], "s5": [s5]})
                self.assertEqual(code, 0, text)
                copies = [event[1] for event in seen.events if event[0] == "copy"]
                self.assertEqual(copies, [receivers] if receivers else [])
                self.assertNotIn("s2", seen.probes)                     # a fleet run says it without SSH
                for name in ("s2", "s3") if receivers else ("s2", "s3", "s5"):
                    self.assertIn(later.format(name, name), text)
                if receivers:
                    self.assertNotIn(later.format("s5", "s5"), text)

    def test_renew_names_a_sensor_it_cannot_ask_for_the_certificate_by_hand(self):
        beats = [({}, {}), ({"s1": sensors.Heartbeat(received_at=1005.0, started_at="B")}, {})]
        down = sensors.SensorsError("s5 cannot be asked: ssh timed out")
        code, text, seen = self.hive(self.NEW, "s1", "-y", beats=beats, info=cert([f"IP:{self.OLD}"]),
                                     probes={"s5": [down]})
        self.assertEqual(code, 1, text)
        copies = [event[1] for event in seen.events if event[0] == "copy"]
        self.assertEqual(copies, [["s2", "s3"]])
        self.assertIn("s5 cannot be asked: ssh timed out", text)
        self.assertRegex(" ".join(text.split()), r"By hand for [^:]*s5")

    def test_failed_nginx_reload_touches_no_sensor(self):
        code, text, seen = self.hive(self.NEW, "s1", "-y", info=cert([f"IP:{self.OLD}"]), renew="")
        self.assertEqual(code, 1, text)
        self.assertEqual([event[0] for event in seen.events], ["renew"])
        self.assertEqual(seen.children, [])
        self.assertEqual(self.registry().get("s1").hive_address, self.OLD)

    def test_playbook_failure_keeps_the_registry_where_the_sensor_kept_its_address(self):
        code, text, seen = self.hive(self.NEW, "s1", "s2", "-y", code=2, retry=["s1", "s2"],
                                     probes={"s2": [probe(), probe()]})
        self.assertEqual(code, 1, text)
        self.assertEqual(self.registry().get("s1").hive_address, self.NEW)
        self.assertEqual(self.registry().get("s2").hive_address, self.OLD)
        self.assertIn(f"s1 has TPOT_HIVE_IP={self.NEW}, but the play stopped there: T-Pot takes it from its next "
                      f"start (sudo systemctl restart tpot)", text)
        self.assertIn(f"s2 keeps TPOT_HIVE_IP={self.OLD}", text)
        self.assertIn(self.by_hand("s2"), text)
        self.assertNotIn("By hand for s1", text)
        self.assertEqual(seen.beats, 1)                             # nothing to wait for
        self.assertFalse(os.path.exists(seen.folders[0]))

    def test_failed_play_after_a_renew_names_the_certificate_where_the_address_was_there_before(self):
        """The play copies hive.crt before it changes .env: a line it changed means the sensor has the new
        certificate, a line that was there before says nothing of it."""
        code, text, _seen = self.hive(self.NEW, "s1", "s2", "-y", code=2, retry=["s1", "s2"],
                                      info=cert([f"IP:{self.OLD}"]),
                                      probes={"s1": [probe(hive_ip=self.NEW)], "s2": [probe(), probe(hive_ip=self.NEW)]})
        self.assertEqual(code, 1, text)
        self.assertIn(f"s1 had TPOT_HIVE_IP={self.NEW} already, but the play stopped there, maybe before it got "
                      f"the new certificate", text)
        self.assertIn(f"s2 has TPOT_HIVE_IP={self.NEW}, but the play stopped there: T-Pot takes it from its next "
                      f"start (sudo systemctl restart tpot)", text)
        self.assertIn(self.by_hand("s1"), text)
        self.assertNotIn("By hand for s1, s2", text)
        self.assertEqual(self.registry().get("s1").hive_address, self.NEW)

    def test_unreadable_sensor_after_the_play_keeps_its_entry(self):
        gone = sensors.SensorsError("s1 cannot be checked (no answer within 15 s)")
        code, text, _seen = self.hive(self.NEW, "s1", "-y", code=2, retry=["s1"], probes={"s1": [probe(), gone]})
        self.assertEqual(code, 1, text)
        self.assertEqual(self.registry().get("s1").hive_address, self.OLD)
        self.assertIn("s1 cannot be checked (no answer within 15 s)", text)
        self.assertIn(self.by_hand("s1"), text)

    def test_ctrl_c_during_wait_still_reads_back_and_exits_130(self):
        code, text, seen = self.hive(self.NEW, "s1", "-y", beats=[({}, {}), KeyboardInterrupt()], stdin=TTY())
        self.assertEqual(code, 130, text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(seen.probes, ["s1", "s1"])                 # read back all the same
        self.assertEqual(self.registry().get("s1").hive_address, self.NEW)
        self.assertFalse(os.path.exists(seen.inventories[0][0]))
        self.assertFalse(os.path.exists(seen.folders[0]))

    def test_ctrl_c_during_the_play_reads_back_and_exits_130(self):
        code, text, seen = self.hive(self.NEW, "s1", "-y", code=[KeyboardInterrupt, -2], stdin=TTY(),
                                     probes={"s1": [probe(), probe()]})
        self.assertEqual(code, 130, text)
        self.assertEqual(seen.probes, ["s1", "s1"])
        self.assertEqual(seen.beats, 1)                             # no wait after a cancelled play
        self.assertIn(f"s1 keeps TPOT_HIVE_IP={self.OLD}", text)
        self.assertEqual(self.registry().get("s1").hive_address, self.OLD)

    def test_set_help_points_to_hive_address(self):
        code, text = self.run_cli("sensors", "set", "-h")
        self.assertEqual(code, 0, text)
        self.assertIn("only the registry, the sensor keeps its address: tpot sensors hive-address changes it there",
                      flat(text))
