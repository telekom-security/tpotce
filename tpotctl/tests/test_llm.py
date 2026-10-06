"""tpot llm: find an Ollama, scan for one on request, test the model of a honeypot."""

import io
import json
import socket
import unittest
import urllib.error

from tpotctl.tests import isolate

isolate()

from tpotctl import llm  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_opener(answers, seen=None):
    """answers: {url: json} for GET and POST alike; anything else is refused."""
    def opener(request, timeout=0):
        url = getattr(request, "full_url", request)
        if seen is not None:
            seen.append(request)
        answer = answers.get(url)
        if answer is None:
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(json.dumps(answer).encode())
    return opener


def ollama_at(base, version="0.12.3", models=("openchat", "llama3.1")):
    return {f"{base}/api/version": {"version": version},
            f"{base}/api/tags": {"models": [{"name": name} for name in models]}}


class CandidatesTest(unittest.TestCase):

    def test_configured_urls_first_then_the_usual_places(self):
        found = llm.candidates({"BEELZEBUB_LLM_HOST": "http://10.0.0.5:11434/api/chat",
                                "GALAH_LLM_SERVER_URL": "http://10.0.0.5:11434"},
                               addresses=["192.168.1.7"], gateways=["172.17.0.1", "192.168.1.1"])
        self.assertEqual(found[0], "http://10.0.0.5:11434")
        for base in ("http://localhost:11434", "http://host.docker.internal:11434", "http://ollama.local:11434",
                     "http://ollama:11434", "http://172.17.0.1:11434", "http://192.168.1.7:11434",
                     "http://192.168.1.1:11434"):
            self.assertIn(base, found)
        self.assertEqual(len(found), len(set(found)))

    def test_a_broken_configured_url_is_left_out(self):
        found = llm.candidates({"GALAH_LLM_SERVER_URL": "not a url"}, [], [])
        self.assertNotIn("not a url", " ".join(found))
        self.assertEqual(found[0], "http://localhost:11434")


class ProbeTest(unittest.TestCase):

    def test_found_with_version_and_models(self):
        found = llm.probe("http://10.0.0.5:11434", opener=fake_opener(ollama_at("http://10.0.0.5:11434")))
        self.assertEqual((found.url, found.version, found.models), ("http://10.0.0.5:11434", "0.12.3", 2))
        self.assertTrue(found.reachable_from_docker)

    def test_nothing_there(self):
        self.assertIsNone(llm.probe("http://10.0.0.6:11434", opener=fake_opener({})))

    def test_not_an_ollama(self):
        opener = fake_opener({"http://10.0.0.7:11434/api/version": {"hello": "world"}})
        self.assertIsNone(llm.probe("http://10.0.0.7:11434", opener=opener))

    def test_loopback_hit_is_not_offered_as_url(self):
        for base in ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"):
            with self.subTest(base=base):
                found = llm.probe(base, opener=fake_opener(ollama_at(base)))
                self.assertFalse(found.reachable_from_docker)
                self.assertIn("OLLAMA_HOST=0.0.0.0", found.note)
                self.assertNotIn("listens on localhost only", found.note)     # it may listen elsewhere too
                self.assertIn("localhost in a container is the container itself", found.note)

    def test_container_name_hit_warns_about_icc(self):
        found = llm.probe("http://ollama:11434", opener=fake_opener(ollama_at("http://ollama:11434")))
        self.assertIn("ICC", found.note)


class DiscoverTest(unittest.TestCase):

    def test_finds_the_ones_that_answer(self):
        answers = dict(ollama_at("http://192.168.1.9:11434"))
        answers.update(ollama_at("http://localhost:11434"))
        found = llm.discover({}, addresses=["192.168.1.9"], gateways=[], opener=fake_opener(answers), linux=True)
        self.assertEqual(sorted(f.url for f in found), ["http://192.168.1.9:11434", "http://localhost:11434"])
        # the ones the honeypots can reach first
        self.assertTrue(found[0].reachable_from_docker)


class DockerDesktopTest(unittest.TestCase):

    def test_loopback_is_host_docker_internal_off_linux(self):
        answers = dict(ollama_at("http://localhost:11434"))
        answers.update(ollama_at("http://127.0.0.1:11434"))
        found = llm.discover({}, addresses=[], gateways=[], opener=fake_opener(answers), linux=False)
        self.assertEqual([f.url for f in found], ["http://host.docker.internal:11434"])
        self.assertTrue(found[0].reachable_from_docker)
        self.assertIn("Docker Desktop", found[0].note)

    def test_loopback_stays_unreachable_on_linux(self):
        found = llm.discover({}, addresses=[], gateways=[], opener=fake_opener(ollama_at("http://localhost:11434")),
                             linux=True)
        self.assertFalse(found[0].reachable_from_docker)


class ScanTest(unittest.TestCase):

    def test_targets_stay_in_the_slash_24(self):
        targets = llm.scan_targets("10.1.2.3/16")
        self.assertEqual(len(targets), 253)
        self.assertNotIn("10.1.2.3", targets)
        self.assertTrue(all(t.startswith("10.1.2.") for t in targets))
        self.assertNotIn("10.1.2.0", targets)
        self.assertNotIn("10.1.2.255", targets)

    def test_targets_of_a_small_network(self):
        self.assertEqual(llm.scan_targets("192.168.5.2/30"), ["192.168.5.1"])

    def test_no_ipv6_scan(self):
        self.assertEqual(llm.scan_targets("fd00::5/64"), [])

    def test_scan_probes_only_open_ports(self):
        tried = []

        def connect(address, timeout=0):
            tried.append(address)
            if address[0] != "10.1.2.40":
                raise OSError("closed")
            return socket.socket()

        answers = ollama_at("http://10.1.2.40:11434")
        found = llm.scan(["10.1.2.39", "10.1.2.40"], connect=connect, opener=fake_opener(answers))
        self.assertEqual([f.url for f in found], ["http://10.1.2.40:11434"])
        self.assertEqual(sorted(tried), [("10.1.2.39", 11434), ("10.1.2.40", 11434)])


class UrlForTest(unittest.TestCase):

    def test_beelzebub_wants_the_chat_endpoint(self):
        self.assertEqual(llm.url_for("beelzebub", "http://h:11434"), "http://h:11434/api/chat")

    def test_galah_wants_the_base(self):
        self.assertEqual(llm.url_for("galah", "http://h:11434"), "http://h:11434")


class TestCallTest(unittest.TestCase):

    def test_ollama_answer(self):
        seen = []
        opener = fake_opener({"http://h:11434/api/chat": {"message": {"content": "OK"}}}, seen)
        result = llm.test("ollama", "http://h:11434/api/chat", "openchat", opener=opener)
        self.assertTrue(result.ok)
        self.assertEqual(result.answer, "OK")
        body = json.loads(seen[0].data)
        self.assertEqual(body["model"], "openchat")
        self.assertFalse(body["stream"])

    def test_openai_answer(self):
        seen = []
        opener = fake_opener({"https://api.openai.com/v1/chat/completions":
                              {"choices": [{"message": {"content": "OK."}}]}}, seen)
        result = llm.test("openai", "", "gpt-4o", api_key="sk-secret", opener=opener)
        self.assertTrue(result.ok)
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer sk-secret")

    def test_key_never_in_the_problem(self):
        error = urllib.error.HTTPError("https://api.openai.com/v1/chat/completions", 401, "Unauthorized", {}, None)
        opener = fake_opener({"https://api.openai.com/v1/chat/completions": error})
        result = llm.test("openai", "", "gpt-4o", api_key="sk-secret", opener=opener)
        self.assertFalse(result.ok)
        self.assertIn("401", result.problem)
        self.assertNotIn("sk-secret", result.problem)

    def test_unreachable(self):
        result = llm.test("ollama", "http://h:11434", "openchat", opener=fake_opener({}))
        self.assertFalse(result.ok)
        self.assertIn("cannot be reached", result.problem)

    def test_no_model(self):
        self.assertIn("model", llm.test("ollama", "http://h:11434", "").problem)

    def test_other_providers(self):
        self.assertIn("not supported", llm.test("anthropic", "", "claude", api_key="k").problem)

    def test_long_answers_are_cut(self):
        opener = fake_opener({"http://h:11434/api/chat": {"message": {"content": "x" * 500}}})
        self.assertLessEqual(len(llm.test("ollama", "http://h:11434", "m", opener=opener).answer), 201)


try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None


@unittest.skipUnless(yaml, "the schema needs PyYAML")
class LlmCliTest(unittest.TestCase):
    """tpot llm on a checkout of its own (STANDARD, without the LLM honeypots)."""

    def setUp(self):
        import os
        import shutil
        import tempfile
        from unittest import mock
        from tpotctl import settings
        from tpotctl.bootstrap import REPO_DIR
        self.repo = tempfile.mkdtemp(prefix="tpot-llm-cli-")
        self.addCleanup(shutil.rmtree, self.repo)
        shutil.copy(os.path.join(REPO_DIR, "env.example"), os.path.join(self.repo, ".env"))
        shutil.copy(os.path.join(REPO_DIR, "compose", "standard.yml"), os.path.join(self.repo, "docker-compose.yml"))
        original = settings.load
        patcher = mock.patch.object(settings, "load", lambda repo_dir=None, **kw: original(self.repo, **kw))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        import contextlib
        from tpotctl import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["llm"] + list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_overview(self):
        code, text = self.run_cli()
        self.assertEqual(code, 0)
        for part in ("Beelzebub", "Galah", "not in your edition", "ollama"):
            self.assertIn(part, text)

    def test_detect(self):
        from unittest import mock
        with mock.patch.object(llm, "discover", return_value=[llm.Found("http://10.0.0.5:11434", "0.12.3", 2, True),
                                                               llm.Found("http://127.0.0.1:11434", "0.12.3", 1, False,
                                                                         "listens on localhost only")]):
            code, text = self.run_cli("detect")
        self.assertEqual(code, 0)
        self.assertIn("http://10.0.0.5:11434", text)
        self.assertIn("listens on localhost only", text)

    def test_scan_needs_a_yes_without_a_terminal(self):
        from unittest import mock
        with mock.patch("sys.stdin", io.StringIO("")), mock.patch.object(llm, "scan") as scan, \
                mock.patch.object(llm, "discover", return_value=[]):
            code, text = self.run_cli("detect", "--scan")
        self.assertEqual(code, 2)
        scan.assert_not_called()

    def test_test(self):
        from unittest import mock
        with mock.patch.object(llm, "test", return_value=llm.TestResult(True, "OK", 0.8)) as call:
            code, text = self.run_cli("test", "galah")
        self.assertEqual(code, 0)
        self.assertIn("OK", text)
        self.assertEqual(call.call_args[0][0], "ollama")
        with mock.patch.object(llm, "test", return_value=llm.TestResult(False, problem="cannot be reached")):
            code, text = self.run_cli("test", "beelzebub")
        self.assertEqual(code, 1)
        self.assertIn("cannot be reached", text)

    def test_models(self):
        from unittest import mock
        with mock.patch.object(llm, "list_models", return_value=["llama3.1", "openchat"]):
            code, text = self.run_cli("models", "galah")
        self.assertEqual(code, 0)
        self.assertIn("openchat", text)


if __name__ == "__main__":
    unittest.main()


class HostAddressesTest(unittest.TestCase):

    def test_gateways_from_ip_route(self):
        from tpotctl import netinfo
        text = json.dumps([{"dst": "default", "gateway": "192.168.1.1", "dev": "eth0"},
                           {"dst": "default", "gateway": "fe80::1", "dev": "eth0"}, {"dst": "default"}])
        self.assertEqual(netinfo.parse_gateways(text), ["192.168.1.1", "fe80::1"])

    def test_addresses_of_the_host_and_its_bridges(self):
        from tpotctl import netinfo
        interfaces = [netinfo.Interface("lo", True, ["127.0.0.1/8"]),
                      netinfo.Interface("eth0", True, ["192.168.1.7/24", "fe80::5/64"], True),
                      netinfo.Interface("docker0", True, ["172.17.0.1/16"]),
                      netinfo.Interface("br-4f2a", True, ["172.18.0.1/16"])]
        self.assertEqual(llm.host_addresses(interfaces), ["192.168.1.7"])
        self.assertEqual(llm.bridge_addresses(interfaces), ["172.17.0.1", "172.18.0.1"])


class RecommendedModelTest(unittest.TestCase):
    """llama3.1:8b is the model T-Pot recommends to Beelzebub and Galah, the same everywhere."""

    MODEL = "llama3.1:8b"

    def read(self, *parts):
        import os
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_env_example_recommends_it(self):
        text = self.read("env.example")
        for key in ("BEELZEBUB_LLM_MODEL", "GALAH_LLM_MODEL"):
            line = f'{key}: "{self.MODEL}"'
            self.assertTrue(line in text.splitlines(), line)
        for other in ("llama3.2:3b", "qwen3:30b-a3b", "gpt-4o-mini"):
            self.assertTrue(other in text, other)

    def test_fallbacks_say_the_same(self):
        for parts in (("compose", "llm.yml"), ("compose", "tpot_services.yml")):
            text = self.read(*parts)
            for key in ("BEELZEBUB_LLM_MODEL", "GALAH_LLM_MODEL"):
                fallback = "${%s:-%s}" % (key, self.MODEL)
                self.assertTrue(fallback in text, (parts, fallback))
        for service in ("beelzebub", "galah"):
            line = f'LLM_MODEL: "{self.MODEL}"'
            self.assertTrue(line in self.read("docker", service, "docker-compose.yml"), (service, line))
        self.assertTrue(f"LLM_MODEL={self.MODEL}" in self.read("docker", "beelzebub", "Dockerfile"))
        self.assertTrue(f'myOLLAMAMODEL:-{self.MODEL}' in self.read("update.sh"))
        schema = self.read("docker", "tpotinit", "dist", "etc", "env.schema.yml")
        self.assertEqual(schema.count(f"i.e. {self.MODEL} (ollama"), 2)
