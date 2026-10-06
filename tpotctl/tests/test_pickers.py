"""The helpers of the Settings page: interfaces, time zones, LLM models, the widgets of the schema."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import llm, netinfo, tz  # noqa: E402
from tpotctl.bootstrap import REPO_DIR  # noqa: E402

try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None
try:
    import textual  # noqa: F401
except ImportError:
    textual = None

IP_JSON = json.dumps([
    {"ifname": "lo", "flags": ["LOOPBACK", "UP"], "addr_info": [{"family": "inet", "local": "127.0.0.1",
                                                                  "prefixlen": 8, "scope": "host"}]},
    {"ifname": "eth0", "flags": ["BROADCAST", "UP", "LOWER_UP"],
     "addr_info": [{"family": "inet", "local": "192.168.5.15", "prefixlen": 24, "scope": "global"},
                   {"family": "inet6", "local": "fe80::1", "prefixlen": 64, "scope": "link"}]},
    {"ifname": "eth1", "flags": ["BROADCAST"], "addr_info": []},
    {"ifname": "docker0", "flags": ["UP"], "addr_info": [{"family": "inet", "local": "172.17.0.1",
                                                          "prefixlen": 16, "scope": "global"}]},
])


def fake_run(outputs):
    """subprocess.run stand-in: the first word of the command picks the output."""
    def run(command, **kwargs):
        key = " ".join(command)
        for prefix, (code, out) in outputs.items():
            if key.startswith(prefix):
                return subprocess.CompletedProcess(command, code, out, "")
        return subprocess.CompletedProcess(command, 1, "", "")
    return run


class NetinfoTest(unittest.TestCase):

    def test_parse_ip_json(self):
        found = {i.name: i for i in netinfo.parse_ip_json(IP_JSON)}
        self.assertTrue(found["eth0"].up)
        self.assertTrue(found["eth0"].global_v4)
        self.assertEqual(found["eth0"].addresses, ["192.168.5.15/24", "fe80::1/64"])
        self.assertFalse(found["eth1"].up)
        self.assertTrue(found["docker0"].internal and found["lo"].internal)
        self.assertFalse(found["eth0"].internal)

    def test_route_dev(self):
        self.assertEqual(netinfo.parse_route_dev("1.1.1.1 via 192.168.5.2 dev eth0 src 192.168.5.15 uid 1000"),
                         "eth0")
        self.assertEqual(netinfo.parse_route_dev(""), "")

    def test_detect_like_capture_if(self):
        if not shutil.which("ip"):
            # _run only calls what is installed; pretend ip is there
            original = netinfo.shutil.which
            netinfo.shutil.which = lambda name: "/sbin/ip" if name == "ip" else original(name)
            self.addCleanup(setattr, netinfo.shutil, "which", original)
        found = netinfo.parse_ip_json(IP_JSON)
        route = fake_run({"ip route get": (0, "1.1.1.1 via 10.0.0.1 dev lima0 src 10.0.0.5\n")})
        self.assertEqual(netinfo.detect(route, found), "lima0")
        v6 = fake_run({"ip -6 route get": (0, "2606:4700:4700::1111 via fe80::1 dev eth1 proto ra\n")})
        self.assertEqual(netinfo.detect(v6, found), "eth1")
        # no route at all: the first interface that is up with a global IPv4, not docker0
        self.assertEqual(netinfo.detect(fake_run({}), found), "eth0")
        self.assertEqual(netinfo.detect(fake_run({}), []), "")


class TimezoneTest(unittest.TestCase):

    def test_zones_start_with_utc(self):
        zones = tz.zones()
        self.assertEqual(zones[0], "UTC")
        self.assertIn("Europe/Berlin", zones)
        self.assertNotIn("US/Eastern", zones)

    def test_search(self):
        zones = ["UTC", "America/New_York", "Europe/Berlin", "America/Argentina/Buenos_Aires", "Asia/Berlinia"]
        self.assertEqual(tz.search("new york", zones), ["America/New_York"])
        self.assertEqual(tz.search("BERL", zones)[0], "Europe/Berlin")
        self.assertEqual(tz.search("buenos_aires", zones), ["America/Argentina/Buenos_Aires"])
        self.assertEqual(tz.search("", zones), zones)

    def test_detect(self):
        etc = tempfile.mkdtemp()
        self.assertEqual(tz.detect(fake_run({"timedatectl": (0, "Europe/Berlin\n")}), etc) if shutil.which(
            "timedatectl") else "Europe/Berlin", "Europe/Berlin")
        with open(os.path.join(etc, "timezone"), "w") as handle:
            handle.write("Asia/Tokyo\n")
        self.assertEqual(tz.detect(fake_run({}), etc), "Asia/Tokyo")
        os.unlink(os.path.join(etc, "timezone"))
        os.symlink("/usr/share/zoneinfo/America/Chicago", os.path.join(etc, "localtime"))
        self.assertEqual(tz.detect(fake_run({}), etc), "America/Chicago")
        with open(os.path.join(etc, "timezone"), "w") as handle:
            handle.write("Etc/UTC\n")                         # Debian on a UTC host
        self.assertEqual(tz.detect(fake_run({}), etc), "UTC")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class LLMTest(unittest.TestCase):

    def opener(self, answer):
        self.requests = []

        def open_(request, timeout):
            self.requests.append(request)
            return FakeResponse(json.dumps(answer).encode())
        return open_

    def test_ollama_from_a_chat_endpoint(self):
        models = llm.list_models("ollama", "http://ollama.lan:11434/api/chat", "",
                                 self.opener({"models": [{"name": "openchat"}, {"name": "llama3.1"}]}))
        self.assertEqual(models, ["llama3.1", "openchat"])
        self.assertEqual(self.requests[0].full_url, "http://ollama.lan:11434/api/tags")
        self.assertIsNone(self.requests[0].get_header("Authorization"))

    def test_openai_key_only_in_the_header(self):
        models = llm.list_models("openai", "", "sk-secret", self.opener({"data": [{"id": "gpt-4o"}]}))
        self.assertEqual(models, ["gpt-4o"])
        request = self.requests[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-secret")
        self.assertNotIn("sk-secret", request.full_url)

    def test_problems(self):
        with self.assertRaises(llm.LLMError):
            llm.list_models("ollama", "", "")
        with self.assertRaises(llm.LLMError):
            llm.list_models("openai", "", "")
        with self.assertRaises(llm.LLMError):
            llm.list_models("anthropic", "", "key")
        with self.assertRaises(llm.LLMError):
            llm.base_url("ollama:11434")

        def refused(request, timeout):
            raise urllib.error.URLError("Name or service not known")
        with self.assertRaises(llm.LLMError) as caught:
            llm.list_models("ollama", "http://ollama:11434", "", refused)
        self.assertIn("Docker network", str(caught.exception))


@unittest.skipUnless(yaml, "PyYAML is not installed")
class SchemaWidgetsTest(unittest.TestCase):

    def setUp(self):
        from tpotctl import envschema
        self.envschema = envschema
        self.schema = envschema.load_schema()

    def test_widget_fields_are_consistent(self):
        for key, rule in self.schema.items():
            with self.subTest(key=key):
                self.assertIn(rule.widget, self.envschema.WIDGETS)
                if rule.widget == "switch":
                    self.assertEqual(rule.type, "enum")
                    self.assertEqual(len(rule.values), 2)
                if rule.widget == "choice":
                    self.assertTrue(rule.choices)
                    for choice in rule.choices:
                        problems = self.envschema.check(rule, {key: choice["value"]}, {}, check_host=False)
                        self.assertEqual(problems, [], f"{choice['value']} has to be valid")
                if rule.show_when:
                    self.assertIn(rule.show_when["key"], self.schema)
                if rule.widget == "llm_model":
                    for name in ("provider", "url", "api_key"):
                        self.assertIn(rule.llm[name], self.schema)
                if rule.widget == "llm_url":
                    self.assertIn(rule.llm["provider"], self.schema)
                    self.assertIn(rule.llm["service"], rule.services)
                if rule.offer:
                    self.assertTrue(rule.services, "an offered key belongs to a service")

    def test_every_llm_key_is_offered(self):
        llm_keys = [k for k in self.schema if k.startswith(("BEELZEBUB_LLM_", "GALAH_LLM_"))]
        self.assertEqual(len(llm_keys), 11)
        for key in llm_keys:
            self.assertTrue(self.schema[key].offer, key)
        self.assertEqual(self.schema["GALAH_LLM_SERVER_URL"].widget, "llm_url")
        self.assertEqual(self.schema["BEELZEBUB_LLM_HOST"].widget, "llm_url")

    def test_show_when(self):
        key_rule = self.schema["GALAH_LLM_API_KEY"]
        self.assertFalse(self.envschema.shown_now(key_rule, {}, self.schema))          # default ollama
        self.assertTrue(self.envschema.shown_now(key_rule, {"GALAH_LLM_PROVIDER": "openai"}, self.schema))
        self.assertTrue(self.envschema.shown_now(self.schema["GALAH_LLM_SERVER_URL"], {}, self.schema))


def make_llm_checkout(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    shutil.copy(os.path.join(REPO_DIR, "env.example"), os.path.join(tmp.name, ".env"))
    shutil.copy(os.path.join(REPO_DIR, "compose", "llm.yml"), os.path.join(tmp.name, "docker-compose.yml"))
    return tmp.name


class SettingsHelpersBase(unittest.IsolatedAsyncioTestCase):
    """The app of the settings tests on an LLM checkout, no tests of its own."""

    async def asyncSetUp(self):
        from tpotctl import app as tapp, events, ops, settings
        repo = self.repo = make_llm_checkout(self)

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "HIVE"

            def status(self):
                return ops.Status("24.04.2", "dev", "abc", "LLM", "HIVE", "active", repo)

            def containers(self):
                return []

            def images(self):
                return []

            def backups(self):
                return []

            def settings(self):
                return settings.load(repo, host_ostype=self.host_ostype())

            def edition_current(self):
                from tpotctl import editions
                return editions.current(repo)

            def config_stamp(self):
                return ops.config_stamp(repo)

            def users(self):
                from tpotctl import users
                from tpotctl.tests.test_users import fake_hash
                return users.load(repo, hasher=fake_hash)

            def system(self):
                return None

            def attacks(self):
                return events.Attacks(problem="none")

        self.app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)

    async def open_settings(self, pilot):
        await pilot.pause(0.3)
        self.app.goto("settings")
        await pilot.pause(0.4)
        return self.app.query_one("#settings")

    async def open_llm(self, pilot):
        await pilot.pause(0.3)
        self.app.goto("llm")
        await pilot.pause(0.4)
        return self.app.query_one("#llm")



@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class SettingsHelpersTest(SettingsHelpersBase):

    async def test_pull_policy_with_a_value_of_your_own(self):
        from tpotctl.widgets.fields import OTHER
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            self.app.goto_setting("TPOT_PULL_POLICY")
            await pilot.pause(0.3)
            self.app.query_one("#set-TPOT_PULL_POLICY").value = "daily"
            await pilot.pause(0.2)
            self.assertEqual(pane.changes(), {"TPOT_PULL_POLICY": "daily"})
            self.app.query_one("#set-TPOT_PULL_POLICY").value = OTHER
            await pilot.pause(0.2)
            own = self.app.query_one("#own-TPOT_PULL_POLICY")
            self.assertTrue(own.display)
            own.value = "every_6h"
            await pilot.pause(0.2)
            self.assertEqual(pane.changes(), {"TPOT_PULL_POLICY": "every_6h"})
            self.assertFalse(self.app.query_one("#settings-save").disabled)
            own.value = "every 6h"
            await pilot.pause(0.2)
            self.assertTrue(self.app.query_one("#settings-save").disabled)       # the regex says no

    async def test_timezone_picker(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            self.app.goto_setting("TPOT_ATTACKMAP_TEXT_TIMEZONE")
            await pilot.pause(0.3)
            await pilot.click("#pick-TPOT_ATTACKMAP_TEXT_TIMEZONE")
            await pilot.pause(0.5)
            self.app.screen.query_one("#picker-filter").value = "berlin"
            await pilot.pause(0.3)
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertEqual(pane.changes(), {"TPOT_ATTACKMAP_TEXT_TIMEZONE": "Europe/Berlin"})

    async def test_interface_picker_automatic_and_detect(self):
        from tpotctl.screens import pickers
        found = netinfo.parse_ip_json(IP_JSON)
        original = (netinfo.interfaces, netinfo.detect)
        netinfo.interfaces = lambda *a, **k: found
        netinfo.detect = lambda *a, **k: "eth0"
        self.addCleanup(lambda: (setattr(netinfo, "interfaces", original[0]), setattr(netinfo, "detect", original[1])))
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            self.app.goto_setting("TPOT_CAPTURE_INTERFACE")
            await pilot.pause(0.3)
            await pilot.click("#pick-TPOT_CAPTURE_INTERFACE")
            await pilot.pause(0.5)
            picker = self.app.screen
            self.assertIsInstance(picker, pickers.Picker)
            self.assertEqual([i[0] for i in picker.shown], ["", "eth0", "eth1"])    # lo, docker0 hidden
            await pilot.click("#picker-detect")
            await pilot.pause(0.4)
            self.assertEqual(picker.highlighted_value(), "eth0")
            await pilot.click("#picker-take")
            await pilot.pause(0.3)
            self.assertEqual(pane.changes(), {"TPOT_CAPTURE_INTERFACE": "eth0"})
            await pilot.click("#pick-TPOT_CAPTURE_INTERFACE")
            await pilot.pause(0.5)
            self.app.screen.query_one("#picker-list").highlighted = 0                 # Automatic
            await pilot.click("#picker-take")
            await pilot.pause(0.3)
            self.assertEqual(pane.changes(), {})

    async def test_provider_shows_the_fields_it_needs(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.query_one("#settings-tabs").active = "tab-honeypots"
            await pilot.pause(0.3)
            row = self.app.query_one("#row-GALAH_LLM_CLOUD_PROJECT")
            self.assertFalse(row.display)
            self.app.query_one("#set-GALAH_LLM_PROVIDER").value = "gcp-vertex"
            await pilot.pause(0.3)
            self.assertTrue(row.display)
            self.assertTrue(self.app.query_one("#row-GALAH_LLM_CLOUD_PROJECT").has_class("-problem"))
            self.assertFalse(self.app.query_one("#row-GALAH_LLM_SERVER_URL").display)

    async def test_models_from_the_server(self):
        original = llm.list_models
        llm.list_models = lambda provider, url, key="", opener=None: ["llama3.1", "openchat"]
        self.addCleanup(setattr, llm, "list_models", original)
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            self.app.goto_setting("GALAH_LLM_MODEL")
            await pilot.pause(0.3)
            # pressed, not clicked: under load the row may still scroll into view
            self.app.query_one("#pick-GALAH_LLM_MODEL").press()
            for _wait in range(30):           # the models load in a thread, slower under load
                await pilot.pause(0.1)
                if getattr(self.app.screen, "shown", None):
                    break
            self.assertEqual([i[0] for i in self.app.screen.shown], ["llama3.1", "openchat"])
            self.app.screen.query_one("#picker-filter").value = "open"
            await pilot.pause(0.2)
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertEqual(pane.changes(), {"GALAH_LLM_MODEL": "openchat"})

    async def test_llm_settings_without_the_service(self):
        shutil.copy(os.path.join(REPO_DIR, "compose", "standard.yml"), os.path.join(self.repo, "docker-compose.yml"))
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.query_one("#settings-tabs").active = "tab-honeypots"
            await pilot.pause(0.3)
            tag = self.app.query_one("#row-GALAH_LLM_MODEL").query(".setting-absent")
            self.assertIn("not in your edition", str(tag.first().render()))
            self.assertFalse(self.app.query_one("#row-OINKCODE").query(".setting-absent"))

    async def test_find_ollama_fills_the_url_of_the_service(self):
        original = llm.discover
        llm.discover = lambda env, **kwargs: [
            llm.Found("http://10.0.0.5:11434", "0.12.3", 2, True),
            llm.Found("http://127.0.0.1:11434", "0.12.3", 1, False, "listens on localhost only")]
        self.addCleanup(setattr, llm, "discover", original)
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            for key, url in (("GALAH_LLM_SERVER_URL", "http://10.0.0.5:11434"),
                             ("BEELZEBUB_LLM_HOST", "http://10.0.0.5:11434/api/chat")):
                self.app.goto_setting(key)
                await pilot.pause(0.3)
                await pilot.click(f"#pick-{key}")
                await pilot.pause(0.5)
                self.assertEqual([i[0] for i in self.app.screen.shown], [url])     # localhost hidden
                await pilot.press("enter")
                await pilot.pause(0.3)
                self.assertEqual(pane.changes().get(key), url)

    async def test_llm_page_has_both_honeypots(self):
        shutil.copy(os.path.join(REPO_DIR, "compose", "standard.yml"), os.path.join(self.repo, "docker-compose.yml"))
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_llm(pilot)
            tabs = pane.query_one("#llm-tabs")
            self.assertEqual(tabs.active, "tab-beelzebub")
            self.assertTrue(pane.query("#row-BEELZEBUB_LLM_MODEL"))
            self.assertIn("not in your edition", str(pane.query_one("#llm-notice").render()))
            self.assertTrue(pane.query_one("#llm-add").display)
            tabs.active = "tab-galah"
            await pilot.pause(0.3)
            self.assertIn("Galah", str(pane.query_one("#llm-add").label))

    async def test_llm_page_in_the_llm_edition(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_llm(pilot)
            self.assertFalse(pane.query_one("#llm-add").display)

    async def test_test_button_asks_the_model(self):
        original = llm.test
        asked = []
        llm.test = lambda provider, url, model, api_key="", **kwargs: (
            asked.append((provider, url, model)) or llm.TestResult(True, "OK", 1.25))
        self.addCleanup(setattr, llm, "test", original)
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_llm(pilot)
            pane.query_one("#llm-tabs").active = "tab-galah"
            await pilot.pause(0.3)
            await pilot.click("#llm-test")
            await pilot.pause(0.5)
            result = str(pane.query_one("#llm-result").render())
        self.assertEqual(asked[0][0], "ollama")
        self.assertIn("OK", result)
        self.assertIn("1.2", result)

    async def test_scan_only_after_a_yes(self):
        from tpotctl.screens.dialogs import ConfirmDialog
        original_scan, original_net = llm.scan, llm.scan_network
        scans = []
        llm.scan = lambda targets, **kwargs: scans.append(list(targets)) or []
        llm.scan_network = lambda: "10.1.2.3/24"
        self.addCleanup(setattr, llm, "scan", original_scan)
        self.addCleanup(setattr, llm, "scan_network", original_net)
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_llm(pilot)
            await pilot.click("#llm-scan")
            await pilot.pause(0.3)
            self.assertIsInstance(self.app.screen, ConfirmDialog)
            self.assertIn("10.1.2.0/24", str(self.app.screen.query_one(".dialog-title").render()))
            await pilot.click("#no")
            await pilot.pause(0.3)
            self.assertEqual(scans, [])
            await pilot.click("#llm-scan")
            await pilot.pause(0.3)
            await pilot.click("#yes")
            await pilot.pause(0.6)
        self.assertEqual(len(scans), 1)
        self.assertEqual(len(scans[0]), 253)

    async def test_saving_on_the_llm_page_updates_the_settings_page(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_llm(pilot)
            pane.query_one("#llm-tabs").active = "tab-galah"
            await pilot.pause(0.3)
            pane.query_one("#set-GALAH_LLM_MODEL").value = "qwen3"
            await pilot.pause(0.3)
            await pilot.click("#llm-save")
            await pilot.pause(0.6)
            if self.app.screen.query("#no"):
                await pilot.click("#no")                   # no restart
                await pilot.pause(0.3)
            settings = self.app.query_one("#settings")
            await pilot.pause(0.3)
            self.assertEqual(settings.draft.get("GALAH_LLM_MODEL"), "qwen3")

    async def test_reloads_do_not_overlap(self):
        import asyncio
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            pane.include_all = True
            await asyncio.gather(pane.reload(), pane.reload(), pane.reload())
            await pilot.pause(0.5)
            self.assertIn("TPOT_HIVE_IP", pane.rows)
            self.assertEqual(len(pane.query("#row-TPOT_HIVE_IP")), 1)

    async def test_all_settings_checkbox_adds_the_rest(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            self.assertNotIn("TPOT_HIVE_IP", pane.rows)              # SENSOR only
            self.app.query_one("#settings-all").value = True
            for _wait in range(30):           # the page reloads, slower under load
                await pilot.pause(0.1)
                if "TPOT_HIVE_IP" in pane.rows:
                    break
            self.assertIn("TPOT_HIVE_IP", pane.rows)

    async def test_secret_can_be_shown(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.goto_setting("OINKCODE")
            await pilot.pause(0.3)
            field = self.app.query_one("#set-OINKCODE")
            self.assertTrue(field.password)
            await pilot.click("#reveal-OINKCODE")
            await pilot.pause(0.2)
            self.assertFalse(field.password)

    async def test_goto_setting_focuses_the_field(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.goto_setting("TPOT_REPO")
            await pilot.pause(0.4)
            self.assertEqual(self.app.query_one("#settings-tabs").active, "tab-system")
            self.assertEqual(self.app.focused.id, "set-TPOT_REPO")

    async def test_arrows_move_through_the_settings(self):
        from textual.widgets import Tabs
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.query_one("#sidebar").focus()
            await pilot.press("right")                       # from the menu into the page
            await pilot.pause(0.2)
            self.assertEqual(self.app.focused.id, "row-WEB_USER")       # shown only, the row takes the focus
            await pilot.press("down", "down")
            await pilot.pause(0.2)
            self.assertEqual(self.app.focused.id, "set-TPOT_BLACKHOLE")
            row = self.app.query_one("#row-TPOT_BLACKHOLE")
            self.assertTrue(row.has_pseudo_class("focus-within"))
            await pilot.press("space")                       # switch it, the change is tagged on the title
            await pilot.pause(0.2)
            self.assertIn("changed", str(row.query_one(".setting-title").render()))
            await pilot.press("up", "up", "up")
            await pilot.pause(0.2)
            self.assertIsInstance(self.app.focused, Tabs)    # above the first setting: the tabs
            await pilot.press("right")
            await pilot.pause(0.2)
            self.assertEqual(self.app.query_one("#settings-tabs").active, "tab-honeypots")
            await pilot.press("down")
            await pilot.pause(0.2)
            self.assertTrue(self.app.focused.id.startswith("set-"))
            await pilot.press("escape")                      # back to the menu
            await pilot.pause(0.2)
            self.assertEqual(self.app.focused.id, "sidebar")
            self.assertEqual(self.app.query_one("#settings").changes(), {"TPOT_BLACKHOLE": "ENABLED"})

    async def test_a_dropdown_stays_one_row_and_opens_with_enter(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.goto_setting("TPOT_PULL_POLICY")
            await pilot.pause(0.3)
            select = self.app.query_one("#set-TPOT_PULL_POLICY")
            self.assertEqual(select.outer_size.height, 1)
            await pilot.press("down")                        # moves on, does not open it
            await pilot.pause(0.2)
            self.assertFalse(select.expanded)
            self.assertNotEqual(self.app.focused, select)
            await pilot.press("up", "enter")
            await pilot.pause(0.3)
            self.assertTrue(select.expanded)
            self.assertEqual(select.outer_size.height, 1)


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class StartProblemsTest(SettingsHelpersBase):
    """What T-Pot would not start with (WEB_USER is empty in the LLM checkout): readable marks,
    a notice per value, the warning on q."""

    async def test_error_badge_has_its_own_background(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            tab = pane.query_one("#settings-tabs").get_tab("tab-base")      # the section of WEB_USER
            badge = [span for span in tab.label.spans if " on " in str(span.style)]
            self.assertTrue(badge, tab.label.spans)
            self.assertIn("WEB_USER", str(pane.query_one("#settings-status").render()))

    def notes(self):
        notes = []
        self.app.notify = lambda message, **kwargs: notes.append((kwargs.get("title"), message))
        return notes

    async def test_one_notice_per_start_problem(self):
        notes = self.notes()
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                await self.open_settings(pilot)
                await pilot.pause(0.3)
        start = [m for t, m in notes if t == "T-Pot would not start"]
        self.assertEqual(len(start), 2, start)
        self.assertTrue(any("TPOT_OSTYPE" in m and "Save" in m and "MAC_WIN" in m for m in start), start)
        self.assertTrue(any("WEB_USER" in m and "Web users" in m for m in start), start)
        self.assertFalse([m for m in start if ".." in m], start)         # texts that end with a full stop

    def with_web_user(self):
        from tpotctl.tests.test_settings import WEB_USER
        env = os.path.join(self.repo, ".env")
        with open(env, encoding="utf-8") as handle:
            text = handle.read().replace("WEB_USER=\n", f"WEB_USER={WEB_USER}\n", 1)
        with open(env, "w", encoding="utf-8") as out:
            out.write(text)

    async def test_no_notice_on_a_fine_linux_host(self):
        self.with_web_user()
        notes = self.notes()
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            await pilot.pause(0.3)
            self.assertEqual(self.app.start_problems(), [])
        self.assertFalse([m for t, m in notes if t == "T-Pot would not start"], notes)

    async def test_quit_warns_about_the_proposal_and_the_web_user(self):
        from tpotctl.screens.dialogs import ConfirmDialog
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                await self.open_settings(pilot)
                self.app.query_one("#sidebar").focus()
                await pilot.press("q")
                await pilot.pause(0.3)
                self.assertIsInstance(self.app.screen, ConfirmDialog)
                body = " ".join(str(w.render()) for w in self.app.screen.query("Static"))
                for part in ("Unsaved on the Settings page: TPOT_OSTYPE", "WEB_USER", "Web users"):
                    self.assertTrue(part in body, (part, body))
                await pilot.click("#no")
                await pilot.pause(0.3)
                self.assertTrue(self.app.is_running)
                self.assertEqual(self.app.query_one("#panes").current, "settings")

    async def test_ctrl_q_on_the_quit_dialog_quits(self):
        from tpotctl.screens.dialogs import ConfirmDialog
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                await self.open_settings(pilot)
                self.app.query_one("#sidebar").focus()
                await pilot.press("q")
                await pilot.pause(0.3)
                self.assertIsInstance(self.app.screen, ConfirmDialog)
                await pilot.press("ctrl+q")             # the second ask to quit is the yes
                await pilot.pause(0.3)
                self.assertFalse(self.app.is_running)

    async def test_ctrl_q_elsewhere_still_only_says_so(self):
        from tpotctl.screens.dialogs import ConfirmDialog
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            pane.focus_setting("TPOT_DATA_PATH")
            await pilot.pause(0.3)
            self.app.query_one("#unlock-TPOT_DATA_PATH").press()
            await pilot.pause(0.4)
            dialog = self.app.screen
            self.assertIsInstance(dialog, ConfirmDialog)
            await pilot.press("ctrl+q")
            await pilot.pause(0.3)
            self.assertTrue(self.app.is_running)
            self.assertIs(self.app.screen, dialog)

    async def test_ready_only_when_the_draft_holds_it(self):
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                pane = await self.open_settings(pilot)

                def fix():
                    return next(p.fix for p in self.app.start_problems() if p.key == "TPOT_OSTYPE")
                self.assertIn("ready", fix())
                pane.query_one("#set-TPOT_OSTYPE").value = "win"           # your own value, not saved
                await pilot.pause(0.3)
                self.assertNotIn("ready", fix())
                self.assertIn("Set it to mac", fix())
                pane.query_one("#set-TPOT_OSTYPE").value = "mac"
                await pilot.pause(0.3)
                self.assertIn("ready", fix())

    async def test_mac_win_hint_when_the_edition_is_unknown(self):
        def unknown():
            raise OSError("no compose file")
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                await self.open_settings(pilot)
                self.app.backend.edition_current = unknown
                fix = next(p.fix for p in self.app.start_problems() if p.key == "TPOT_OSTYPE")
        self.assertIn("Docker Desktop runs the MAC_WIN edition", fix)
        self.assertNotIn("switch to", fix)

    async def test_mac_win_hint_follows_the_edition_in_use(self):
        cases = {("CUSTOM", "MAC_WIN"): "", ("MAC_WIN", ""): "", ("none", ""): "Docker Desktop runs",
                 ("unknown", ""): "Docker Desktop runs", ("STANDARD", ""): "switch to the MAC_WIN"}
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                await self.open_settings(pilot)
                for in_use, want in cases.items():
                    with self.subTest(edition=in_use):
                        self.app.backend.edition_current = lambda in_use=in_use: in_use
                        fix = next(p.fix for p in self.app.start_problems() if p.key == "TPOT_OSTYPE")
                        if want:
                            self.assertIn(want, fix)
                        else:
                            self.assertNotIn("MAC_WIN", fix)

    async def test_q_on_the_quit_dialog_quits(self):
        from tpotctl.screens.dialogs import ConfirmDialog
        from textual.widgets import Input
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                await self.open_settings(pilot)
                self.app.query_one("#sidebar").focus()
                await pilot.press("q")
                await pilot.pause(0.3)
                self.assertIsInstance(self.app.screen, ConfirmDialog)
                self.assertFalse(self.app.screen.query(Input))            # nothing to type q into
                await pilot.press("q")
                await pilot.pause(0.3)
                self.assertFalse(self.app.is_running)

    async def test_q_elsewhere_does_nothing(self):
        from tpotctl.screens.dialogs import ConfirmDialog
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            pane.focus_setting("TPOT_DATA_PATH")
            await pilot.pause(0.3)
            self.app.query_one("#unlock-TPOT_DATA_PATH").press()
            await pilot.pause(0.4)
            dialog = self.app.screen
            self.assertIsInstance(dialog, ConfirmDialog)
            await pilot.press("q")
            await pilot.pause(0.3)
            self.assertTrue(self.app.is_running)
            self.assertIs(self.app.screen, dialog)

    async def test_quit_anyway(self):
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                await self.open_settings(pilot)
                self.app.query_one("#sidebar").focus()
                await pilot.press("q")
                await pilot.pause(0.3)
                await pilot.click("#yes")
                await pilot.pause(0.3)
                self.assertFalse(self.app.is_running)

    async def test_quit_without_anything_pending_ends_at_once(self):
        self.with_web_user()
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.query_one("#sidebar").focus()
            await pilot.press("q")
            await pilot.pause(0.3)
            self.assertFalse(self.app.is_running)

    async def test_quit_dialog_not_on_top_of_another_screen(self):
        from tpotctl.screens.restore import RestoreScreen
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.push_screen(RestoreScreen(lambda: []))
            await pilot.pause(0.3)
            await pilot.press("ctrl+q")
            await pilot.pause(0.3)
            self.assertIsInstance(self.app.screen, RestoreScreen)
            self.assertTrue(self.app.is_running)

    async def test_focused_problem_row_keeps_the_error_colour(self):
        from textual.color import Color
        from tpotctl import theme
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            row = pane.rows["WEB_USER"]
            row.focus()
            await pilot.pause(0.2)
            kind, colour = row.styles.border_top
            self.assertEqual(kind, "heavy")
            want = Color.parse(theme.color("error")).rgb
            self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(colour.rgb, want)), (colour.rgb, want))  # css rounds


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class OutsideChangeTest(SettingsHelpersBase):
    """.env changed elsewhere (the Web users page, an edition switch, tpot env set in a terminal):
    the settings pages, the start problems and the quit dialog follow it."""

    def set_env(self, key, value):
        from tpotctl.envfile import EnvFile
        env = EnvFile(os.path.join(self.repo, ".env"))
        env.set(key, value)
        env.save()

    def notes(self):
        notes = []
        self.app.notify = lambda message, **kwargs: notes.append(message)
        return notes

    async def test_outside_web_user_clears_the_start_problem(self):
        from tpotctl.tests.test_settings import WEB_USER
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            self.assertIn("WEB_USER", str(pane.query_one("#settings-status").render()))
            self.set_env("WEB_USER", WEB_USER)
            self.assertTrue(pane.stale())
            await self.app.refresh_config()
            await pilot.pause(0.3)
            self.assertNotIn("WEB_USER", str(pane.query_one("#settings-status").render()))
            label = pane.query_one("#settings-tabs").get_tab("tab-base").label
            self.assertNotIn(" on ", " ".join(str(span.style) for span in label.spans))
            self.assertFalse([p for p in self.app.start_problems() if p.key == "WEB_USER"])

    async def test_outside_change_keeps_the_other_draft(self):
        from tpotctl.tests.test_settings import WEB_USER
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            pane.query_one("#set-TPOT_ATTACKMAP_TEXT_TIMEZONE").value = "Europe/Berlin"
            await pilot.pause(0.3)
            notes = self.notes()
            self.set_env("WEB_USER", WEB_USER)
            await self.app.refresh_config()
            await pilot.pause(0.3)
            self.assertEqual(pane.changes().get("TPOT_ATTACKMAP_TEXT_TIMEZONE"), "Europe/Berlin")
            self.assertEqual(notes, [])

    async def test_outside_change_of_the_drafted_key_wins_and_says_so(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            self.app.goto_setting("GALAH_LLM_MODEL")
            await pilot.pause(0.3)
            pane.query_one("#set-GALAH_LLM_MODEL").value = "mistral"
            await pilot.pause(0.3)
            notes = self.notes()
            self.set_env("GALAH_LLM_MODEL", "qwen3")
            await self.app.refresh_config()
            await pilot.pause(0.3)
            self.assertNotIn("GALAH_LLM_MODEL", pane.changes())
            self.assertEqual(pane.current.values.get("GALAH_LLM_MODEL"), "qwen3")
            self.assertTrue(any("GALAH_LLM_MODEL" in n and "changed meanwhile" in n for n in notes), notes)

    async def test_outside_change_keeps_an_unlocked_draft(self):
        from tpotctl.tests.test_settings import WEB_USER
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            await pane.unlock(pane.rows["TPOT_DATA_PATH"])
            await pilot.pause(0.2)
            pane.query_one("#set-TPOT_DATA_PATH").value = "/srv/tpot-data"
            await pilot.pause(0.3)
            self.set_env("WEB_USER", WEB_USER)
            await self.app.refresh_config()
            await pilot.pause(0.3)
            self.assertIn("TPOT_DATA_PATH", pane.unlocked)
            self.assertEqual(pane.changes().get("TPOT_DATA_PATH"), "/srv/tpot-data")

    async def test_palette_jump_after_an_outside_change(self):
        from tpotctl.tests.test_settings import WEB_USER
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            self.app.goto("status")
            await pilot.pause(0.3)
            self.set_env("WEB_USER", WEB_USER)
            self.app.goto_setting("TPOT_BLACKHOLE")
            await pilot.pause(0.8)
            row = pane.rows["TPOT_BLACKHOLE"]
            self.assertTrue(row.is_mounted)
            self.assertIsNotNone(self.app.focused)
            self.assertIn(row, self.app.focused.ancestors_with_self)

    async def test_a_page_with_a_load_error_recovers(self):
        env = os.path.join(self.repo, ".env")
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            os.chmod(env, 0)
            self.addCleanup(os.chmod, env, 0o644)
            await pane.reload()
            await pilot.pause(0.3)
            self.assertIsNone(pane.current)
            os.chmod(env, 0o644)
            self.assertTrue(pane.stale())
            await self.app.refresh_config()
            await pilot.pause(0.3)
            self.assertIsNotNone(pane.current)

    async def test_touch_without_change_says_nothing(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            pane.query_one("#set-TPOT_ATTACKMAP_TEXT_TIMEZONE").value = "Europe/Berlin"
            await pilot.pause(0.3)
            notes = self.notes()
            env = os.path.join(self.repo, ".env")
            stat = os.stat(env)
            os.utime(env, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            self.assertTrue(pane.stale())
            await self.app.refresh_config()
            await pilot.pause(0.3)
            self.assertFalse(pane.stale())
            self.assertEqual(notes, [])
            self.assertEqual(pane.changes().get("TPOT_ATTACKMAP_TEXT_TIMEZONE"), "Europe/Berlin")

    async def test_refresh_while_saving(self):
        import asyncio
        from tpotctl.tests.test_settings import WEB_USER
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            pane.query_one("#set-TPOT_ATTACKMAP_TEXT_TIMEZONE").value = "Europe/Berlin"
            await pilot.pause(0.3)
            self.set_env("WEB_USER", WEB_USER)
            await asyncio.gather(pane.reload(), self.app.refresh_config(), pane.reload())
            await pilot.pause(0.5)
            self.assertIn("WEB_USER", pane.rows)

    async def test_quit_after_an_outside_fix(self):
        from tpotctl.tests.test_settings import WEB_USER
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.set_env("WEB_USER", WEB_USER)
            self.app.query_one("#sidebar").focus()
            await pilot.press("q")
            await pilot.pause(0.5)
            self.assertFalse(self.app.is_running)


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class RefreshTriggersTest(SettingsHelpersBase):
    """The actions of tpot that write the configuration bring every page that shows it along."""

    async def test_web_user_added_clears_the_start_problem(self):
        from tpotctl import users as tusers
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.assertTrue([p for p in self.app.start_problems() if p.key == "WEB_USER"])
            self.app.goto("users")
            await pilot.pause(0.3)
            with mock.patch.object(tusers, "weakness", lambda password: None):
                self.app.query_one("#users").save("add", ("alice", "a long passphrase"))
            await pilot.pause(0.6)
            settings_pane = self.app.query_one("#settings")
            self.assertFalse(settings_pane.stale())                 # brought along without a visit
            self.assertNotIn("WEB_USER", str(settings_pane.query_one("#settings-status").render()))
            self.app.query_one("#sidebar").focus()
            await pilot.press("q")
            await pilot.pause(0.5)
            self.assertFalse(self.app.is_running)

    async def test_edition_switch_refreshes_the_proposal(self):
        from tpotctl.screens.task import Task, TaskScreen
        repo = self.repo

        class Switching:
            """tpot edition set mac_win as it leaves the checkout: compose file and TPOT_OSTYPE."""
            def __init__(self, command, env=None, cwd=None):
                pass

            def run(self, line):
                from tpotctl.envfile import EnvFile
                shutil.copy(os.path.join(REPO_DIR, "compose", "mac_win.yml"), os.path.join(repo, "docker-compose.yml"))
                env = EnvFile(os.path.join(repo, ".env"))
                env.set("TPOT_OSTYPE", "mac")
                env.save()
                line("@@tpot phase done Done\n")
                return 0

        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                pane = await self.open_settings(pilot)
                self.assertEqual(pane.changes().get("TPOT_OSTYPE"), "mac")
                self.app.engine = Switching
                self.app.run_task(Task("Switch to the macOS / Windows edition", ["/x/tpot", "edition", "set"]))
                await pilot.pause(0.3)
                self.assertIsInstance(self.app.screen, TaskScreen)
                await pilot.click("#task-run")
                for _ in range(40):
                    await pilot.pause(0.05)
                    if self.app.screen.code is not None:
                        break
                await pilot.press("escape")
                await pilot.pause(0.8)
                self.assertNotIn("TPOT_OSTYPE", pane.changes())
                self.assertFalse([p for p in self.app.start_problems() if p.key == "TPOT_OSTYPE"])

    async def test_customizer_refreshes_the_services(self):
        from tpotctl import app as tapp
        from tpotctl.screens.customizer import core
        from tpotctl.screens.dialogs import ConfirmDialog
        repo = self.repo
        custom = os.path.join(repo, "docker-compose-custom.yml")

        def runner(command, cwd=None):          # the mv of the replace, on the temporary checkout
            shutil.move(custom, os.path.join(repo, "docker-compose.yml"))
            return 0
        with mock.patch.object(tapp, "CUSTOM_OUTPUT", custom):
            async with self.app.run_test(size=(150, 50)) as pilot:
                pane = await self.open_settings(pilot)
                self.app.goto_setting("GALAH_LLM_MODEL")
                await pilot.pause(0.3)
                self.assertEqual(pane.rows["GALAH_LLM_MODEL"].note, "")            # llm.yml runs Galah
                self.app.runner = runner
                self.app.customized(core.Catalog(), core.Selection("STANDARD"))
                await pilot.pause(0.3)
                self.assertIsInstance(self.app.screen, ConfirmDialog)
                await pilot.click("#yes")
                await pilot.pause(0.8)
                self.assertEqual(pane.rows["GALAH_LLM_MODEL"].note, "not in your edition")

    async def test_users_page_reads_anew_when_shown(self):
        from tpotctl import users as tusers
        from tpotctl.tests.test_users import fake_hash
        async with self.app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            self.app.goto("users")
            await pilot.pause(0.3)
            table = self.app.query_one("#users-table")
            before = table.row_count
            self.app.goto("settings")
            await pilot.pause(0.3)
            with mock.patch.object(tusers, "weakness", lambda password: None):
                tusers.load(self.repo, hasher=fake_hash).add("bob", "a long passphrase")     # tpot users add elsewhere
            self.app.goto("users")
            await pilot.pause(0.5)
            self.assertEqual(table.row_count, before + 1)

    async def test_enter_on_a_user_row_offers_its_actions(self):
        from tpotctl import users as tusers
        from tpotctl.screens.dialogs import ChoiceDialog, UserDialog
        from tpotctl.tests.test_users import fake_hash
        with mock.patch.object(tusers, "weakness", lambda password: None):
            store = tusers.load(self.repo, hasher=fake_hash)
            store.add("alice", "a long passphrase")
            store.add("bob", "another long passphrase")
        async with self.app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            self.app.goto("users")
            await pilot.pause(0.4)
            table = self.app.query_one("#users-table")
            table.focus()
            table.move_cursor(row=1)
            await pilot.pause(0.1)
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertIsInstance(self.app.screen, ChoiceDialog)
            self.assertIn("bob", self.app.screen.title_text)
            await pilot.press("escape")
            await pilot.pause(0.3)
            self.assertIs(self.app.focused, table)
            await pilot.press("enter")
            await pilot.pause(0.3)
            await pilot.press("enter")              # the first one: the password
            await pilot.pause(0.3)
            self.assertIsInstance(self.app.screen, UserDialog)

    async def test_header_reloads_on_the_first_outside_change(self):
        from tpotctl.envfile import EnvFile
        calls = []
        original = self.app.load_header
        self.app.load_header = lambda: (calls.append(1), original())
        async with self.app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.5)
            before = len(calls)
            env = EnvFile(os.path.join(self.repo, ".env"))
            env.set("TPOT_ATTACKMAP_TEXT", "DISABLED")
            env.save()
            self.app.goto("settings")
            await pilot.pause(0.6)
            self.assertEqual(len(calls), before + 1)
            self.app.goto("llm")                    # nothing changed since
            await pilot.pause(0.6)
            self.assertEqual(len(calls), before + 1)

    async def test_the_menu_brings_the_settings_page_along(self):
        from tpotctl.envfile import EnvFile
        from tpotctl.tests.test_settings import WEB_USER
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            self.app.goto("status")
            await pilot.pause(0.3)
            env = EnvFile(os.path.join(self.repo, ".env"))
            env.set("WEB_USER", WEB_USER)
            env.save()
            sidebar = self.app.query_one("#sidebar")
            sidebar.focus()
            for _ in range(2):                      # down to Settings with the menu
                await pilot.press("down")
            await pilot.pause(0.6)
            self.assertNotIn("WEB_USER", str(pane.query_one("#settings-status").render()))


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class CtrlSTest(SettingsHelpersBase):
    """ctrl+s saves the Settings and the LLM page, the footer offers it there."""

    def env(self):
        with open(os.path.join(self.repo, ".env"), encoding="utf-8") as handle:
            return handle.read()

    async def later(self, pilot):
        for _ in range(20):
            await pilot.pause(0.1)
            if self.app.screen.query("#no"):
                await pilot.click("#no")             # no restart of T-Pot
                await pilot.pause(0.3)
                return True
        return False

    async def test_ctrl_s_saves(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_settings(pilot)
            field = pane.query_one("#set-TPOT_ATTACKMAP_TEXT_TIMEZONE")
            field.focus()
            field.value = "Europe/Berlin"
            await pilot.pause(0.3)
            await pilot.press("ctrl+s")
            self.assertTrue(await self.later(pilot))
        self.assertTrue("TPOT_ATTACKMAP_TEXT_TIMEZONE=Europe/Berlin" in self.env())

    async def test_ctrl_s_with_nothing_to_save_says_so(self):
        notes = []
        self.app.notify = lambda message, **kwargs: notes.append(message)
        before = self.env()
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.query_one("#settings").enter()
            await pilot.pause(0.2)
            await pilot.press("ctrl+s")
            await pilot.pause(0.3)
        self.assertTrue(any("Nothing to save" in n for n in notes), notes)
        self.assertEqual(self.env(), before)

    async def test_ctrl_s_is_in_the_footer_of_the_settings_page(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            await self.open_settings(pilot)
            self.app.query_one("#settings").enter()
            await pilot.pause(0.2)
            shown = {key for key, active in self.app.active_bindings.items() if active.binding.show}
            self.assertIn("ctrl+s", shown)
            self.app.goto("status")
            self.app.query_one("#sidebar").focus()
            await pilot.pause(0.2)
            self.assertNotIn("ctrl+s", self.app.active_bindings)

    async def test_ctrl_s_on_the_llm_page(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            pane = await self.open_llm(pilot)
            pane.query_one("#llm-tabs").active = "tab-galah"
            await pilot.pause(0.3)
            field = pane.query_one("#set-GALAH_LLM_MODEL")
            field.focus()
            field.value = "qwen3"
            await pilot.pause(0.3)
            await pilot.press("ctrl+s")
            self.assertTrue(await self.later(pilot))
            await pilot.pause(0.3)
            self.assertFalse(self.app.screen.query("#no"))      # one save, one question
        self.assertTrue("qwen3" in self.env())


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class SettingsDraftTest(SettingsHelpersBase):
    """A save on one of Settings / LLM keeps the unsaved changes of the other."""

    async def save_llm_model(self, pilot, model):
        llm_pane = self.app.query_one("#llm")
        self.app.goto("llm")
        await pilot.pause(0.4)
        llm_pane.query_one("#llm-tabs").active = "tab-galah"
        await pilot.pause(0.3)
        llm_pane.query_one("#set-GALAH_LLM_MODEL").value = model
        await pilot.pause(0.3)
        await pilot.click("#llm-save")
        await pilot.pause(0.6)
        if self.app.screen.query("#no"):
            await pilot.click("#no")
            await pilot.pause(0.3)

    async def test_draft_of_the_other_page_survives(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            settings = await self.open_settings(pilot)
            settings.query_one("#set-TPOT_ATTACKMAP_TEXT_TIMEZONE").value = "Europe/Berlin"
            await pilot.pause(0.3)
            await self.save_llm_model(pilot, "qwen3")
            self.assertEqual(settings.changes().get("TPOT_ATTACKMAP_TEXT_TIMEZONE"), "Europe/Berlin")
            self.assertEqual(settings.draft.get("GALAH_LLM_MODEL"), "qwen3")   # what was saved is there

    async def test_the_saved_value_wins_and_says_so(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            settings = await self.open_settings(pilot)
            self.app.goto_setting("GALAH_LLM_MODEL")
            await pilot.pause(0.3)
            settings.query_one("#set-GALAH_LLM_MODEL").value = "mistral"
            await pilot.pause(0.3)
            notes = []
            self.app.notify = lambda message, **kwargs: notes.append(message)
            await self.save_llm_model(pilot, "qwen3")
            self.assertNotIn("GALAH_LLM_MODEL", settings.changes())
            self.assertTrue(any("GALAH_LLM_MODEL" in n for n in notes))

    async def test_settings_page_on_a_mac_proposes_the_os_type(self):
        notes = []
        self.app.notify = lambda message, **kwargs: notes.append(message)
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                settings_pane = await self.open_settings(pilot)
                self.assertEqual(settings_pane.changes().get("TPOT_OSTYPE"), "mac")
                self.assertIn("TPOT_OSTYPE", settings_pane.unlocked)
                self.assertTrue(any("TPOT_OSTYPE" in n for n in notes), notes)
                self.assertIn("mac fits this host", str(settings_pane.rows["TPOT_OSTYPE"].note))
                settings_pane.query_one("#settings-save").press()
                for _ in range(30):
                    await pilot.pause(0.1)
                    if self.app.screen.query("#no"):
                        break
                if self.app.screen.query("#no"):
                    await pilot.click("#no")             # no restart of T-Pot
                    await pilot.pause(0.3)
        with open(os.path.join(self.repo, ".env"), encoding="utf-8") as handle:
            self.assertTrue("TPOT_OSTYPE=mac\n" in handle.read())

    async def test_settings_page_on_linux_proposes_nothing(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            settings_pane = await self.open_settings(pilot)
            self.assertNotIn("TPOT_OSTYPE", settings_pane.changes())
            self.assertNotIn("TPOT_OSTYPE", settings_pane.unlocked)

    async def test_the_llm_page_does_not_take_the_os_type(self):
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                llm_pane = await self.open_llm(pilot)
                self.assertNotIn("TPOT_OSTYPE", llm_pane.changes())

    async def test_revert_declines_the_proposal(self):
        with mock.patch.dict(os.environ, {"TPOT_HOST_OSTYPE": "mac"}):
            async with self.app.run_test(size=(150, 50)) as pilot:
                pane = await self.open_settings(pilot)
                self.assertEqual(pane.changes().get("TPOT_OSTYPE"), "mac")
                pane.query_one("#settings-revert").press()
                await pilot.pause(0.6)
                self.assertNotIn("TPOT_OSTYPE", pane.changes())
                problem = next(p for p in self.app.start_problems() if p.key == "TPOT_OSTYPE")
                self.assertNotIn("ready", problem.fix)               # nothing is ready any more
                await self.save_llm_model(pilot, "qwen3")            # a reload from the other page
                self.assertNotIn("TPOT_OSTYPE", pane.changes())
                self.app.fix_setting("TPOT_OSTYPE")                  # the palette entry asks again
                await pilot.pause(0.6)
                self.assertEqual(pane.changes().get("TPOT_OSTYPE"), "mac")

    async def test_same_value_saved_elsewhere_says_nothing(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            settings = await self.open_settings(pilot)
            self.app.goto_setting("GALAH_LLM_MODEL")
            await pilot.pause(0.3)
            settings.query_one("#set-GALAH_LLM_MODEL").value = "qwen3"
            await pilot.pause(0.3)
            notes = []
            self.app.notify = lambda message, **kwargs: notes.append(message)
            await self.save_llm_model(pilot, "qwen3")
            self.assertNotIn("GALAH_LLM_MODEL", settings.changes())
            self.assertFalse(any("GALAH_LLM_MODEL" in n for n in notes), notes)

    async def test_a_save_on_llm_saves_once_and_leaves_the_focus_there(self):
        from tpotctl.screens.dialogs import ConfirmDialog
        async with self.app.run_test(size=(150, 50)) as pilot:
            settings = await self.open_settings(pilot)
            await settings.unlock(settings.rows["TPOT_DATA_PATH"])
            await pilot.pause(0.2)
            settings.query_one("#set-TPOT_DATA_PATH").value = "/srv/tpot-data"
            await pilot.pause(0.3)
            llm_pane = self.app.query_one("#llm")
            self.app.goto("llm")
            await pilot.pause(0.4)
            llm_pane.query_one("#llm-tabs").active = "tab-galah"
            await pilot.pause(0.3)
            llm_pane.query_one("#set-GALAH_LLM_MODEL").value = "qwen3"
            await pilot.pause(0.3)
            llm_pane.query_one("#llm-save").press()
            for _wait in range(50):            # writing .env and reloading both pages, slower under load
                await pilot.pause(0.1)
                if isinstance(self.app.screen, ConfirmDialog):
                    break
            await pilot.pause(0.5)             # a second save would ask a second time
            dialogs = [s for s in self.app.screen_stack if isinstance(s, ConfirmDialog)]
            self.assertEqual(len(dialogs), 1)                      # one save, one question
            focused = self.app.screen_stack[0].focused
            self.assertFalse(focused is not None and settings in focused.ancestors_with_self)

    async def test_unlocked_draft_survives_a_save_elsewhere(self):
        async with self.app.run_test(size=(150, 50)) as pilot:
            settings = await self.open_settings(pilot)
            await settings.unlock(settings.rows["TPOT_DATA_PATH"])
            await pilot.pause(0.2)
            settings.query_one("#set-TPOT_DATA_PATH").value = "/srv/tpot-data"
            await pilot.pause(0.3)
            await self.save_llm_model(pilot, "qwen3")
            self.assertIn("TPOT_DATA_PATH", settings.unlocked)
            self.assertEqual(settings.changes().get("TPOT_DATA_PATH"), "/srv/tpot-data")


if __name__ == "__main__":
    unittest.main()
