"""The landing page of the web UI (docker/nginx/dist/html): its queries, its colours, and the rules that
keep it safe. The data it shows comes from attackers, so it sets text only, runs no inline code, loads
nothing from elsewhere and checks every file it loads by its hash; the error page is seen before the
login and must not tell that this is a T-Pot.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl import events, landing  # noqa: E402

ROOT = Path(landing.REPO)
HTML = ROOT / "docker" / "nginx" / "dist" / "html"
CONF = ROOT / "docker" / "nginx" / "dist" / "conf" / "tpotweb.conf"
PAGE = HTML / "index.html"
ERROR = HTML / "error.html"
CSS = HTML / "assets" / "css" / "tpot.css"
JS = sorted((HTML / "assets" / "js").glob("*.js"))

# the links that may leave the page
OUTSIDE = ("https://sicherheitstacho.eu/", "https://github.com/telekom-security/tpotce/",
           "https://github.com/telekom-security/tpotce/blob/master/README.md")
# what the page left behind with the former landing page
GONE = ("assets/js/particles.min.js", "assets/js/particles_conf.js", "assets/js/clock.js", "assets/fonts/awesome")


def read(path):
    return Path(path).read_text(encoding="utf-8")


def walk(node):
    """Every dict in a query, depth first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


class QueriesTest(unittest.TestCase):

    def test_queries_json_is_the_generated_one(self):
        self.assertEqual(landing.main(["--check"]), 0, "run python3 -m tpotctl.landing")

    def test_every_range_has_its_queries(self):
        data = json.loads(read(landing.QUERIES))
        self.assertIn(data["default"], data["ranges"])
        self.assertEqual(set(data["ranges"]), {r["key"] for r in landing.RANGES})
        for key, r in data["ranges"].items():
            with self.subTest(range=key):
                self.assertEqual(set(r) & {"overview", "sources", "sensors"}, {"overview", "sources", "sensors"})
                self.assertGreater(r["points"], 1)

    def test_the_tools_of_the_nsm_are_no_attacks(self):
        """Suricata and P0f describe the connections the honeypots see; the attacks leave them out, as
        tpotctl.events does (the sensors count every event they send)."""
        for r in landing.RANGES:
            for name, query in (("overview", landing.overview(r)), ("sources", landing.sources(r))):
                with self.subTest(range=r["key"], query=name):
                    excluded = [d["terms"]["type.keyword"] for d in walk(query)
                                if "terms" in d and isinstance(d["terms"].get("type.keyword"), list)]
                    self.assertEqual(excluded, [events.NOT_ATTACKS])

    def test_the_sources_are_the_query_of_the_manager(self):
        self.assertEqual(landing.sources(landing.RANGES[0]), events.sources_query(24, 12))


class LookTest(unittest.TestCase):

    def test_the_colours_are_the_tokens_of_the_theme(self):
        """theme.py needs Textual, so its colours are read from the source."""
        source = read(ROOT / "tpotctl" / "theme.py")
        tokens = dict(re.findall(r'^([A-Z_]+) = "(#[0-9A-Fa-f]{6})"', source, re.M))
        ok, warn, error = re.search(r'^OK, WARN, ERROR = "(#\w+)", "(#\w+)", "(#\w+)"', source, re.M).groups()
        tokens.update(OK=ok, WARN=warn, ERROR=error)
        css = dict(re.findall(r"--([a-z-]+):\s*(#[0-9A-Fa-f]{6});", read(CSS)))
        for name in ("INK", "COMB", "COMB_LIT", "WAX", "MAGENTA", "PETROL", "GLASS", "MIST", "KEY", "ASH",
                     "OK", "WARN", "ERROR"):
            with self.subTest(token=name):
                self.assertEqual(css[name.lower().replace("_", "-")].upper(), tokens[name].upper())


class PageTest(unittest.TestCase):

    def setUp(self):
        self.page = read(PAGE)

    def test_every_script_and_stylesheet_is_checked_by_its_hash(self):
        refs = re.findall(r'<(?:script|link)\b[^>]*?(?:src|href)="(assets/(?:js|css)/[^"]+)"[^>]*>', self.page)
        self.assertEqual(sorted(refs), sorted(["assets/css/tpot.css"] + ["assets/js/" + p.name for p in JS]))
        for tag in re.findall(r'<(?:script|link rel="stylesheet")\b[^>]*>', self.page):
            with self.subTest(tag=tag[:60]):
                path = re.search(r'(?:src|href)="([^"]+)"', tag).group(1)
                digest = base64.b64encode(hashlib.sha384((HTML / path).read_bytes()).digest()).decode()
                self.assertIn(f'integrity="sha384-{digest}"', tag,
                              f"{path} changed: openssl dgst -sha384 -binary {path} | openssl base64 -A")
                self.assertIn('crossorigin="anonymous"', tag)

    def test_no_inline_code(self):
        """The CSP allows the files only: no script or style in the page, no style attributes."""
        self.assertIsNone(re.search(r"<script\b(?![^>]*\bsrc=)", self.page))
        self.assertNotIn("<style", self.page)
        self.assertIsNone(re.search(r"\sstyle=", self.page))
        self.assertIsNone(re.search(r"\son[a-z]+=", self.page))
        self.assertNotIn("http-equiv", self.page.lower())

    def test_links_that_leave_the_page(self):
        urls = re.findall(r'(?:src|href)="((?:https?:)?//[^"]+)"', self.page)
        self.assertTrue(urls)
        for url in urls:
            with self.subTest(url=url):
                self.assertIn(url, OUTSIDE)
        for tag in re.findall(r"<a\b[^>]*>", self.page):
            if 'target="_blank"' in tag:
                with self.subTest(tag=tag[:60]):
                    self.assertIn('rel="noopener noreferrer"', tag)

    def test_the_scripts_set_text_only(self):
        forbidden = re.compile(r"\.innerHTML|\.outerHTML|insertAdjacentHTML|document\.write|\beval\s*\(|"
                               r"new Function|setTimeout\(\s*['\"]|setInterval\(\s*['\"]")
        for path in JS:
            with self.subTest(file=path.name):
                text = path.read_bytes()
                self.assertTrue(all(b < 128 for b in text), "only ASCII (escape the rest as \\uXXXX)")
                self.assertIsNone(forbidden.search(text.decode()))

    def test_git_ignores_no_file_of_the_page(self):
        """The .gitignore drops every data/ folder; a file of the page there would be missing in a checkout."""
        if not (ROOT / ".git").exists() or shutil.which("git") is None:
            self.skipTest("no git checkout")
        files = [str(p.relative_to(ROOT)) for p in HTML.rglob("*")
                 if p.is_file() and "cyberchef" not in p.parts and "esvue" not in p.parts and p.name != ".DS_Store"]
        result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=ROOT, input="\n".join(files),
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.split(), [])

    def test_the_former_page_is_gone(self):
        for rel in GONE:
            with self.subTest(path=rel):
                self.assertFalse((HTML / rel).exists())
                self.assertNotIn(rel, self.page)


class ServerTest(unittest.TestCase):

    def setUp(self):
        self.conf = read(CONF)
        self.landing = self.conf[self.conf.index("    location / {"):]
        self.landing = self.landing[:self.landing.index("\n    }\n")]
        self.error = self.conf[self.conf.index("    location = /error.html {"):]
        self.error = self.error[:self.error.index("\n    }\n")]

    def test_the_landing_page_has_its_policy(self):
        for directive in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "connect-src 'self'",
                          "base-uri 'none'", "form-action 'none'", "frame-ancestors 'self'"):
            with self.subTest(directive=directive):
                self.assertIn(directive, self.landing)
        self.assertIn('"Referrer-Policy: no-referrer"', self.landing)
        self.assertIn('"Cache-Control: no-cache"', self.landing)

    def test_no_add_header_in_the_locations_of_the_page(self):
        """An add_header in a location drops every add_header of the server block (HSTS and the rest)."""
        for block in (self.landing, self.error):
            self.assertNotIn("add_header", block)

    def test_the_error_page_is_neutral(self):
        page = read(ERROR)
        self.assertNotRegex(page, re.compile(r"t-?pot|telekom|nginx|honeypot", re.I))
        self.assertNotRegex(page, re.compile(r"<(?:script|img|link|svg)\b", re.I))
        self.assertEqual(set(re.findall(r'echo var="(\w+)"', page)), {"status"})
        self.assertNotRegex(page, re.compile(r"\$(?:uri|request|args|arg_|http_|host)", re.I))
        for directive in ("internal;", "ssi on;", "auth_basic off;"):
            self.assertIn(directive, self.error)

    def test_the_login_survives_the_error_page(self):
        """The redirect of a 401 to the error page drops the challenge of auth_basic; the error page sets it
        again with the same realm, or no browser would ask for the login."""
        realms = set(re.findall(r'^\s*auth_basic\s+"([^"]+)";', self.conf, re.M))
        self.assertEqual(len(realms), 1, realms)
        self.assertIn(f"more_set_headers -s 401 'WWW-Authenticate: Basic realm=\"{realms.pop()}\"';", self.error)

    def test_no_default_page_of_nginx(self):
        """The default error pages name nginx: every status nginx answers itself gets the neutral page."""
        codes = set(re.search(r"^\s*error_page ([\d ]+) /error.html;", self.conf, re.M).group(1).split())
        self.assertLessEqual({"400", "401", "403", "404", "405", "408", "413", "414", "429", "500", "502", "503", "504"},
                             codes)

    def test_the_style_of_the_error_page_is_allowed_by_its_hash(self):
        style = re.search(r"<style>(.*?)</style>", read(ERROR), re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        self.assertIn(f"style-src 'sha256-{digest}'", self.error)


if __name__ == "__main__":
    unittest.main()
