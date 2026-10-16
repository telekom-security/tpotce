"""AuthRandom of the patched core/auth.py with the pairs of [honeypot] auth_refused_pairs: those never
log in and leave AuthRandom's state as it is; every other pair logs in as with upstream AuthRandom.
The login events of core/checkers.py (not patched) keep their form."""
import ast
import configparser
import json
import random
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.helpers import tmpdir
from tests.test_patch import FIXTURE, PATCH
from tpot_personas import catalog
from tpot_personas.identity import draw
from tpot_personas.render import REFUSED_LOGINS, refused_logins_value, render_cfg

AUTH = "src/cowrie/core/auth.py"
CHECKERS = "src/cowrie/core/checkers.py"
_MISSING = object()

# pairs that bots and people send often: AuthRandom decides them, as before
COMMON_PAIRS = (("root", "root"), ("admin", "admin"), ("root", "123456"), ("root", "password"), ("root", ""),
                ("admin", "1234"), ("admin", "password"), ("admin", ""), ("ubnt", "ubnt"), ("pi", "raspberry"),
                ("user", "user"), ("test", "test"), ("guest", "guest"), ("support", "support"),
                ("root", "admin"), ("oracle", "oracle"), ("postgres", "postgres"), ("ftpuser", "ftpuser"),
                ("root", "P@ssw0rd"), ("admin", "P@ssw0rd1337!"), ("honeypot", "honeypot"))


class Config:
    """CowrieConfig as Cowrie reads it: ExtendedInterpolation, a missing option without fallback
    raises configparser.NoOptionError."""

    def __init__(self, values):
        self.parser = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
        for (section, option), value in values.items():
            if not self.parser.has_section(section):
                self.parser.add_section(section)
            self.parser.set(section, option, value)

    def get(self, section, option, fallback=_MISSING, **kwargs):
        if fallback is _MISSING:
            return self.parser.get(section, option)
        return self.parser.get(section, option, fallback=fallback)

    def getboolean(self, section, option, fallback=False):
        return self.parser.getboolean(section, option, fallback=fallback)


class Log:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        if level.startswith("_"):
            raise AttributeError(level)
        return lambda text, **kwargs: self.lines.append((level, text))


def without_imports(source, roots):
    """source as a module without the `from <root> ... import` lines of roots."""
    keep = [node for node in ast.parse(source).body
            if not (isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in roots)]
    return compile(ast.Module(keep, []), "<cowrie>", "exec")


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                       check=True, capture_output=True, text=True)
        cls.patched_root = root
        cls.patched = (root / AUTH).read_text(encoding="utf-8")
        cls.upstream = (FIXTURE / AUTH).read_text(encoding="utf-8")

    def auth_module(self, source, state_dir, refused=_MISSING, seed=7):
        """The module auth.py from source with a [honeypot] of Cowrie's form; randint drawn from
        random.Random(seed), so two modules with one seed draw the same numbers."""
        values = {("honeypot", "state_path"): str(state_dir), ("honeypot", "auth_class_parameters"): "2, 5, 10"}
        if refused is not _MISSING:
            values[("honeypot", "auth_refused_pairs")] = refused
        log = Log()
        draws = random.Random(seed)
        namespace = {"CowrieConfig": Config(values), "Logger": lambda: log,
                     "read_data_bytes": lambda *parts: (_ for _ in ()).throw(FileNotFoundError(parts))}
        exec(without_imports(source, ("cowrie", "twisted")), namespace)  # noqa: S102
        namespace["randint"] = draws.randint
        namespace["_log"] = log
        return namespace

    def check(self, module, login, password, ip):
        # checkers.py makes a new AuthRandom for every attempt
        return module["AuthRandom"]().checklogin(login.encode("utf-8"), password.encode("utf-8"), ip)

    def state(self, state_dir):
        path = state_dir / "auth_random.json"
        return path.read_bytes() if path.exists() else None

    def test_listed_pairs_never_log_in(self):
        state_dir = tmpdir(self)
        module = self.auth_module(self.patched, state_dir, refused_logins_value())
        for login, password in REFUSED_LOGINS:
            for attempt in range(25):
                with self.subTest(login=login, attempt=attempt):
                    self.assertFalse(self.check(module, login, password, "198.51.100.7"))
                    self.assertFalse(self.check(module, login, password, f"203.0.113.{attempt}"))
        # nothing of them is in the state: no source address, no try, no cached pair
        self.assertIsNone(self.state(state_dir))

    def test_listed_pairs_stay_refused_after_a_login(self):
        state_dir = tmpdir(self)
        module = self.auth_module(self.patched, state_dir, refused_logins_value())
        ip = "198.51.100.8"
        accepted = next(f"pw{n}" for n in range(20) if self.check(module, "root", f"pw{n}", ip))
        before = self.state(state_dir)
        for login, password in REFUSED_LOGINS * 3:
            with self.subTest(login=login):
                self.assertFalse(self.check(module, login, password, ip))
                self.assertFalse(self.check(module, login, password, "192.0.2.99"))
        self.assertEqual(self.state(state_dir), before)
        # the accepted pair still logs in on return, and from another address through the cache
        self.assertTrue(self.check(module, "root", accepted, ip))
        self.assertTrue(self.check(module, "root", accepted, "192.0.2.98"))

    def test_listed_pairs_in_an_older_state_are_refused(self):
        # a state of an AuthRandom without the list: the pair is cached and the address's own
        state_dir = tmpdir(self)
        login, password = REFUSED_LOGINS[0]
        userpass = f"{login}:{password}"
        state = {"cache": [userpass], "198.51.100.9": {"try": 3, "max": 3, "tried": [], "user": login,
                                                       "pw": password}}
        (state_dir / "auth_random.json").write_text(json.dumps(state), encoding="utf-8")
        module = self.auth_module(self.patched, state_dir, refused_logins_value())
        self.assertFalse(self.check(module, login, password, "198.51.100.9"))
        self.assertFalse(self.check(module, login, password, "198.51.100.10"))
        self.assertEqual(json.loads(self.state(state_dir)), state)

    def test_other_pairs_log_in_as_with_upstream(self):
        # one sequence of attempts on upstream AuthRandom and on the patched one with the listed pairs
        # in between: the same answers for every other pair, the same state at the end
        rng = random.Random(11)
        attempts = []
        for n in range(400):
            ip = f"198.51.100.{rng.randrange(6)}"
            login, password = rng.choice(COMMON_PAIRS + tuple((f"u{rng.randrange(9)}", f"p{rng.randrange(9)}")
                                                              for _ in range(3)))
            attempts.append((login, password, ip))
        for seed in range(5):
            with self.subTest(seed=seed):
                up_dir, our_dir = tmpdir(self), tmpdir(self)
                upstream = self.auth_module(self.upstream, up_dir, seed=seed)
                ours = self.auth_module(self.patched, our_dir, refused_logins_value(), seed=seed)
                listed = iter(REFUSED_LOGINS * 400)
                expected, got = [], []
                for login, password, ip in attempts:
                    expected.append(self.check(upstream, login, password, ip))
                    pair = next(listed)
                    self.assertFalse(self.check(ours, pair[0], pair[1], ip))
                    got.append(self.check(ours, login, password, ip))
                    self.assertFalse(self.check(ours, pair[0], pair[1], ip))
                self.assertEqual(got, expected)
                self.assertIn(True, got)
                self.assertEqual(self.state(our_dir), self.state(up_dir))

    def test_a_listed_pair_is_exact(self):
        # another case, a space or a byte more: AuthRandom decides, as for any pair
        state_dir = tmpdir(self)
        module = self.auth_module(self.patched, state_dir, refused_logins_value())
        login, password = REFUSED_LOGINS[1]
        for n, (other_login, other_password) in enumerate(((login.upper(), password), (login, password + " "),
                                                           (" " + login, password), (login, password[:-1]))):
            ip = f"192.0.2.{n}"
            with self.subTest(login=other_login, password=other_password):
                self.check(module, other_login, other_password, ip)
                # AuthRandom took it: the first try of a new address is in its state
                self.assertEqual(json.loads(self.state(state_dir))[ip]["tried"], [f"{other_login}:{other_password}"])

    def test_without_the_key_auth_random_is_upstreams(self):
        for refused in (_MISSING, "", "[]"):
            with self.subTest(refused=refused):
                up_dir, our_dir = tmpdir(self), tmpdir(self)
                upstream = self.auth_module(self.upstream, up_dir)
                ours = self.auth_module(self.patched, our_dir, refused)
                for n in range(30):
                    login, password = REFUSED_LOGINS[n % len(REFUSED_LOGINS)]
                    ip = f"198.51.100.{n % 3}"
                    self.assertEqual(self.check(ours, login, password, ip), self.check(upstream, login, password, ip))
                self.assertEqual(self.state(our_dir), self.state(up_dir))

    def test_a_value_of_another_form_is_ignored_with_a_warning(self):
        for refused in ("{", '{"admin": "x"}', '[["admin"]]', '[["admin", 1]]', '"admin"', "[1]"):
            with self.subTest(refused=refused):
                up_dir, our_dir = tmpdir(self), tmpdir(self)
                upstream = self.auth_module(self.upstream, up_dir)
                ours = self.auth_module(self.patched, our_dir, refused)
                for n in range(12):
                    self.assertEqual(self.check(ours, "admin", f"x{n % 4}", "198.51.100.1"),
                                     self.check(upstream, "admin", f"x{n % 4}", "198.51.100.1"))
                self.assertEqual(self.state(our_dir), self.state(up_dir))
                self.assertIn("warn", {level for level, _ in ours["_log"].lines})

    def test_the_value_is_parsed_once_and_a_bad_one_warned_once(self):
        """The list is read and parsed on the first attempt only (functools.cache, as the shell's
        history size); a value of another form gives one warning, not one per attempt."""
        for refused, warnings in ((refused_logins_value(), 0), ("[1]", 1)):
            with self.subTest(refused=refused):
                module = self.auth_module(self.patched, tmpdir(self), refused)
                reads = []
                config = module["CowrieConfig"]
                original = config.get

                def counting(section, option, *args, **kwargs):
                    if option == "auth_refused_pairs":
                        reads.append(option)
                    return original(section, option, *args, **kwargs)

                config.get = counting
                for n in range(20):
                    self.check(module, "admin", f"x{n}", f"198.51.100.{n % 4}")
                    self.check(module, *REFUSED_LOGINS[n % len(REFUSED_LOGINS)], "198.51.100.200")
                self.assertEqual(len(reads), 1)
                self.assertEqual(sum(1 for level, _ in module["_log"].lines if level == "warn"), warnings)

    # -- the login events of checkers.py

    def checker(self, auth_module, events):
        """HoneypotPasswordChecker of the fixture's checkers.py on the given auth module; a deferred is
        its result (True: succeeded)."""

        class Deferred:
            def __init__(self, result=None, callback=None):
                self.result = result

            def addCallback(self, function, *args):
                return function(self.result, *args)

        class Failed(Exception):
            pass

        defer = types.SimpleNamespace(succeed=lambda value: True, fail=lambda reason: False)
        namespace = {
            "implementer": lambda *interfaces: (lambda cls: cls),
            "ICredentialsChecker": object, "ISSHPrivateKey": object, "error": None, "keys": None,
            "failure": None, "UnauthorizedLogin": Failed, "UnhandledCredentials": Failed, "defer": defer,
            "Logger": Log, "CowrieConfig": Config({("honeypot", "auth_class"): "AuthRandom"}),
            "auth": types.SimpleNamespace(AuthRandom=auth_module["AuthRandom"], UserDB=None),
            "conchcredentials": types.SimpleNamespace(IUsernamePasswordIP=object,
                                                      IPluggableAuthenticationModulesIP=object, IUsername=object),
            "escape_nonprintable": lambda data: data.decode("ascii"),
        }
        source = (self.patched_root / CHECKERS).read_text(encoding="utf-8")
        exec(without_imports(source, ("cowrie", "twisted", "zope")), namespace)  # noqa: S102
        checker = namespace["HoneypotPasswordChecker"]()

        class Events:
            def dispatch(self, eventid, format, **fields):
                events.append((eventid, format, fields))

        def password(login, secret, ip):
            credentials = types.SimpleNamespace(username=login.encode(), password=secret.encode(), ip=ip,
                                                events=Events())
            return checker.requestAvatarId(credentials)

        def keyboard_interactive(login, secret, ip):
            # cowrie's userauth answers the prompt with the bytes the client sent
            conversation = lambda items: Deferred([(secret.encode(), 0)])  # noqa: E731
            credentials = types.SimpleNamespace(username=login.encode(), pamConversion=conversation, ip=ip,
                                                events=Events())
            return checker.requestAvatarId(credentials)

        return password, keyboard_interactive

    def test_checkers_py_is_not_patched(self):
        self.assertEqual((self.patched_root / CHECKERS).read_bytes(), (FIXTURE / CHECKERS).read_bytes())

    def test_listed_pairs_fail_with_the_usual_event(self):
        # password (SSH, telnet) and keyboard-interactive (SSH): a cowrie.login.failed event of the
        # usual form for every attempt, never a success
        events = []
        password, keyboard_interactive = self.checker(self.auth_module(self.patched, tmpdir(self),
                                                                       refused_logins_value()), events)
        for login, secret in REFUSED_LOGINS:
            for attempt in range(6):
                for method in (password, keyboard_interactive):
                    with self.subTest(login=login, attempt=attempt, method=method.__name__):
                        events.clear()
                        self.assertFalse(method(login, secret, "198.51.100.20"))
                        self.assertEqual(events, [("cowrie.login.failed",
                                                   "login attempt [%(username)s/%(password)s] failed",
                                                   {"username": login, "password": secret})])

    def test_other_pairs_give_the_events_of_upstream(self):
        up_events, our_events = [], []
        upstream = self.checker(self.auth_module(self.upstream, tmpdir(self)), up_events)
        ours = self.checker(self.auth_module(self.patched, tmpdir(self), refused_logins_value()), our_events)
        for n in range(40):
            login, secret = COMMON_PAIRS[n % len(COMMON_PAIRS)]
            method = n % 2
            ip = f"198.51.100.{n % 4}"
            self.assertEqual(ours[method](login, secret, ip), upstream[method](login, secret, ip))
            refused = REFUSED_LOGINS[n % len(REFUSED_LOGINS)]
            ours[1 - method](refused[0], refused[1], ip)
        failed = [event for event in our_events if (event[2]["username"], event[2]["password"]) in REFUSED_LOGINS]
        self.assertEqual({event[0] for event in failed}, {"cowrie.login.failed"})
        self.assertEqual([event for event in our_events if event not in failed], up_events)
        self.assertIn("cowrie.login.success", {event[0] for event in up_events})


class ListTest(unittest.TestCase):
    """The list of render.REFUSED_LOGINS and its [honeypot] key."""

    def test_the_list_is_narrow(self):
        self.assertLessEqual(len(REFUSED_LOGINS), 12)
        self.assertEqual(len(set(REFUSED_LOGINS)), len(REFUSED_LOGINS))
        for login, password in REFUSED_LOGINS:
            self.assertTrue(login and password, (login, password))
            self.assertEqual((login, password), (login.strip(), password.strip()))
        for pair in COMMON_PAIRS:
            self.assertNotIn(pair, REFUSED_LOGINS)

    def test_every_persona_renders_the_list(self):
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                out = tmpdir(self)
                text = render_cfg(persona, draw(persona, 1), out, out / "keys")
                # read as Cowrie reads it (ExtendedInterpolation: a $ is written as $$)
                cfg = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
                cfg.read_string(text)
                self.assertEqual(cfg.get("honeypot", "auth_class"), "AuthRandom")
                pairs = json.loads(cfg.get("honeypot", "auth_refused_pairs"))
                self.assertEqual([tuple(pair) for pair in pairs], list(REFUSED_LOGINS))

    def test_the_value_survives_cowries_config_reader(self):
        cfg = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
        value = refused_logins_value((("a$b", "p%c"), ("x", "y${z}"), ("ä", "q;#r")))
        cfg.read_string(f"[honeypot]\nauth_refused_pairs = {value}\n")
        self.assertEqual(json.loads(cfg.get("honeypot", "auth_refused_pairs")),
                         [["a$b", "p%c"], ["x", "y${z}"], ["ä", "q;#r"]])
        self.assertEqual(len(value.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
