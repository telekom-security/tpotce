"""tpot users: WEB_USER and nginxpasswd, on a copy of env.example in a temporary checkout."""

import base64
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None
# the UI needs Textual, the CLI Rich
textual = importlib.util.find_spec("textual") and importlib.util.find_spec("rich")

from tpotctl import users  # noqa: E402
from tpotctl.bootstrap import REPO_DIR  # noqa: E402

# tsec:tsec of env.example, apr1, created by htpasswd -n (decodes with two newlines)
APR1 = "dHNlYzokYXByMSRYUnE2SC5rbiRVRjZQM1VVQmJVNWJUQmNmSGRuUFQxCgo="
DES = base64.b64encode(b"old:abJnggxhB/yWI").decode()     # htpasswd -d, T-Pot does not start with it


def fake_hash(name, password, repo_dir=None):
    return f"{name}:$2y$05$" + base64.b64encode(password.encode()).decode().rstrip("=")


def checkout(test, web_user=APR1):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    with open(os.path.join(REPO_DIR, "env.example"), encoding="utf-8") as handle:
        text = handle.read().replace("WEB_USER=\n", f"WEB_USER={web_user}\n", 1)
    with open(os.path.join(tmp.name, ".env"), "w", encoding="utf-8") as handle:
        handle.write(text)
    os.makedirs(os.path.join(tmp.name, "data", "nginx", "conf"))
    passwd = os.path.join(tmp.name, "data", "nginx", "conf", "nginxpasswd")
    open(passwd, "w").close()
    shutil.copy(os.path.join(REPO_DIR, "compose", "standard.yml"), os.path.join(tmp.name, "docker-compose.yml"))
    return tmp.name, passwd


class ParseTest(unittest.TestCase):

    def test_schemes_and_problems(self):
        entries = users.parse(f"{APR1} {DES} not*base64 {users.encode('nocolon')}")
        self.assertEqual([u.name for u in entries], ["tsec", "old", "entry 3", "entry 4"])
        self.assertEqual(entries[0].scheme, "apr1 (MD5)")
        self.assertTrue(entries[0].ok)
        self.assertIn("htpasswd hash", entries[1].problem)
        self.assertEqual(entries[2].problem, "not base64")
        self.assertEqual(entries[3].problem, "not name:hash")
        self.assertEqual(users.parse(users.encode(fake_hash("a", "b")))[0].scheme, "bcrypt")

    def test_names(self):
        users.check_name("web_admin-1.x")
        for bad in ("", "a b", "a:b", "ä"):
            with self.assertRaises(users.UsersError):
                users.check_name(bad)

    def test_weakness_without_cracklib(self):
        with mock.patch.object(users, "_find", return_value=None):
            self.assertIn("shorter", users.weakness("short"))
            self.assertIsNone(users.weakness("a long enough passphrase"))
            self.assertEqual(users.weakness(""), "it is empty")

    def test_weakness_with_cracklib(self):
        def fake(command, input=None, **_kwargs):
            word = input.strip()
            return mock.Mock(stdout=f"{word}: {'OK' if len(word) > 10 else 'it is too short'}\n")
        with mock.patch.object(users, "_find", return_value="/usr/sbin/cracklib-check"):
            self.assertEqual(users.weakness("abc", run=fake), "it is too short")
            self.assertIsNone(users.weakness("Tr0ub4dor&3xyz", run=fake))

    @unittest.skipUnless(users._find("htpasswd"), "htpasswd is not installed")
    def test_real_bcrypt(self):
        line = users.hash_line("alice", "a secret with spaces and 'quotes'")
        self.assertTrue(line.startswith("alice:$2y$"))

    def test_password_is_not_on_the_command_line(self):
        seen = {}

        def fake(command, input=None, **_kwargs):
            seen["command"], seen["input"] = command, input
            return mock.Mock(returncode=0, stdout="bob:$2y$05$abc\n\n", stderr="")
        with mock.patch.object(users, "_find", return_value="/usr/bin/htpasswd"):
            self.assertEqual(users.hash_line("bob", "s3cret", run=fake), "bob:$2y$05$abc")
        self.assertNotIn("s3cret", " ".join(seen["command"]))
        self.assertIn("-B", seen["command"])
        self.assertEqual(seen["input"], "s3cret\n")

    def test_the_fallback_image_takes_the_version_of_the_checkout(self):
        """Without htpasswd the tpotinit image of .env, else the one of the checkout's version file."""
        seen = []

        def fake(command, input=None, **_kwargs):
            seen.append(command)
            return mock.Mock(returncode=0, stdout="bob:$2y$05$abc\n\n", stderr="")
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "version"), "w", encoding="utf-8") as handle:
                handle.write("99.1.0\n")
            with open(os.path.join(repo, ".env"), "w", encoding="utf-8") as handle:
                handle.write("TPOT_REPO=example.org/tsec\n")
            with mock.patch.object(users, "_find", return_value=None), \
                    mock.patch.object(users.shutil, "which", return_value="/usr/bin/docker"):
                users.hash_line("bob", "s3cret", repo_dir=repo, run=fake)
                self.assertIn("example.org/tsec/tpotinit:99.1.0", seen[-1])
                with open(os.path.join(repo, ".env"), "a", encoding="utf-8") as handle:
                    handle.write("TPOT_VERSION=98.0.0\n")       # the images this installation pulls
                users.hash_line("bob", "s3cret", repo_dir=repo, run=fake)
                self.assertIn("example.org/tsec/tpotinit:98.0.0", seen[-1])


@unittest.skipUnless(yaml, "PyYAML is not installed")
class StoreTest(unittest.TestCase):

    def setUp(self):
        self.repo, self.passwd = checkout(self)
        self.inode = os.stat(self.passwd).st_ino

    def store(self):
        return users.load(self.repo, hasher=fake_hash)

    def test_add_passwd_remove(self):
        self.assertIn("right away", self.store().add("alice", "pw1"))
        self.assertEqual([u.name for u in self.store().users()], ["tsec", "alice"])
        self.store().passwd("tsec", "pw2")
        self.assertEqual(self.store().find("tsec").scheme, "bcrypt")
        self.store().remove("alice")
        self.assertEqual([u.name for u in self.store().users()], ["tsec"])
        with open(self.passwd) as handle:
            self.assertEqual(handle.read(), fake_hash("tsec", "pw2") + "\n")
        self.assertEqual(os.stat(self.passwd).st_ino, self.inode, "nginxpasswd has to keep its inode")

    def test_refusals(self):
        with self.assertRaises(users.UsersError):
            self.store().add("tsec", "x")                 # exists
        with self.assertRaises(users.UsersError):
            self.store().passwd("nobody", "x")
        with self.assertRaises(users.UsersError):
            self.store().remove("tsec")                   # last one

    def test_repair_a_broken_entry(self):
        repo, _passwd = checkout(self, f"{APR1} {DES}")
        store = users.load(repo, hasher=fake_hash)
        self.assertFalse(store.find("old").ok)
        store.passwd("old", "new password")
        self.assertTrue(users.load(repo).find("old").ok)

    def test_sensor_has_no_web_users(self):
        from tpotctl.envfile import EnvFile
        env = EnvFile(os.path.join(self.repo, ".env"))
        env.set("TPOT_TYPE", "SENSOR")
        env.save()
        with self.assertRaises(users.UsersError):
            users.load(self.repo)

    def test_unwritable_nginxpasswd_waits_for_restart(self):
        os.remove(self.passwd)
        os.makedirs(self.passwd)                          # cannot be opened as a file
        self.assertIn("next start", self.store().add("carol", "pw"))

    @unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
    def test_cli(self):
        from tpotctl import cli
        original = users.load
        with mock.patch.object(users, "load", lambda: original(self.repo, hasher=fake_hash)), \
                mock.patch.object(users, "weakness", lambda pw: None if len(pw) > 11 else "too short"), \
                mock.patch.object(cli.os, "geteuid", return_value=1000, create=True):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err), \
                    mock.patch("sys.stdin", io.StringIO("a long passphrase\nshort\nshort\n")):
                self.assertEqual(cli.main(["users", "add", "alice", "--password-stdin"]), 0)
                self.assertEqual(cli.main(["users", "add", "bob", "--password-stdin"]), 2)      # weak, cannot ask
                self.assertEqual(cli.main(["users", "add", "bob", "--password-stdin", "--allow-weak"]), 0)
                self.assertEqual(cli.main(["users", "remove", "bob"]), 2)                       # no --yes
                self.assertEqual(cli.main(["users", "remove", "bob", "--yes"]), 0)
                self.assertEqual(cli.main(["users", "list"]), 0)
            self.assertIn("alice", out.getvalue())
            self.assertIn("--allow-weak", err.getvalue())
        self.assertEqual([u.name for u in users.load(self.repo).users()], ["tsec", "alice"])

    def run_cli(self, *argv, tty=True, **patches):
        """cli.main at a terminal (or not); patches: input / getpass as mock side effects."""
        from tpotctl import cli
        original = users.load

        class Stdin(io.StringIO):
            def isatty(self):
                return tty
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(users, "load", lambda: original(self.repo, hasher=fake_hash)), \
                mock.patch.object(cli.os, "geteuid", return_value=1000, create=True), \
                mock.patch("builtins.input", side_effect=patches.get("input", AssertionError("asked"))), \
                mock.patch("getpass.getpass", side_effect=patches.get("getpass", AssertionError("asked"))), \
                mock.patch("sys.stdin", Stdin()), redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as stop:
                code = stop.code
            except KeyboardInterrupt:       # unittest would stop on it
                code = "KeyboardInterrupt"
        return code, out.getvalue() + err.getvalue()

    @unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
    def test_ctrl_c_at_a_question_is_cancelled_with_130(self):
        for argv, patches in ((["users", "add"], {"input": KeyboardInterrupt}),
                              (["users", "add", "alice"], {"getpass": KeyboardInterrupt}),
                              (["users", "add", "alice"], {"getpass": ["a long passphrase", KeyboardInterrupt]}),
                              (["users", "passwd", "tsec"], {"getpass": KeyboardInterrupt}),
                              (["users", "remove", "tsec"], {"input": KeyboardInterrupt})):
            code, text = self.run_cli(*argv, **patches)
            self.assertEqual(code, 130, (argv, text))
            self.assertIn("Cancelled.", text)
            self.assertNotIn("Traceback", text)
        self.assertEqual([u.name for u in users.load(self.repo).users()], ["tsec"])

    @unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
    def test_ctrl_d_at_the_password_is_no_password(self):
        for steps in (EOFError, ["a long passphrase", EOFError]):
            code, text = self.run_cli("users", "add", "alice", getpass=steps)
            self.assertEqual(code, 1, text)
            self.assertIn("no password entered", text)
            self.assertNotIn("Traceback", text)

    @unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
    def test_without_a_terminal_it_cannot_ask_and_says_so_with_2(self):
        for argv, hint in ((["users", "add", "alice"], "--password-stdin"), (["users", "passwd", "tsec"],
                                                                            "--password-stdin"),
                           (["users", "add"], "NAME"), (["users", "remove", "tsec"], "--yes")):
            code, text = self.run_cli(*argv, tty=False)
            self.assertEqual(code, 2, (argv, text))
            self.assertIn(hint, text)


ALICE = "alice:$2y$05$aliceshashaliceshashaliceshashaliceshashaliceshash"
BOB = "bob:$apr1$bobsalt$bobshashbobshash"


@unittest.skipUnless(yaml, "PyYAML is not installed")
class FileOnlyTest(unittest.TestCase):
    """Users of nginxpasswd and nginxpasswd.lost that WEB_USER does not name: offered, taken over, and no
    change of the web users drops them."""

    def setUp(self):
        self.repo, self.passwd = checkout(self, web_user="")
        self.lost = self.passwd + ".lost"
        with open(self.passwd, "w") as handle:
            handle.write(f"{ALICE}\n{BOB}\n")

    def store(self):
        return users.load(self.repo, hasher=fake_hash)

    def read(self, path):
        with open(path) as handle:
            return handle.read()

    def test_an_empty_web_user_offers_the_users_of_the_file(self):
        found = self.store().file_only()
        self.assertEqual([(e.name, e.source, e.line) for e in found],
                         [("alice", "nginxpasswd", ALICE), ("bob", "nginxpasswd", BOB)])
        self.assertEqual(found[0].entry, users.encode(ALICE))

    def test_every_change_refuses_while_the_file_holds_them(self):
        inode = os.stat(self.passwd).st_ino
        for change in (lambda s: s.add("carol", "pw"),):
            with self.assertRaises(users.UsersError) as caught:
                change(self.store())
            self.assertEqual(str(caught.exception), "nginxpasswd holds 2 web user(s) the .env does not name "
                                                    "(alice, bob); take them over first: tpot users import")
        # with users of its own in WEB_USER: passwd and remove refuse as well
        repo, passwd = checkout(self, f"{APR1} {users.encode(fake_hash('dave', 'pw'))}")
        with open(passwd, "w") as handle:
            handle.write(f"{ALICE}\n")
        store = users.load(repo, hasher=fake_hash)
        for change in (lambda: store.passwd("tsec", "pw2"), lambda: store.remove("dave"),
                       lambda: store.add("carol", "pw")):
            with self.assertRaises(users.UsersError) as caught:
                change()
            self.assertIn("tpot users import", str(caught.exception))
        self.assertEqual(self.read(passwd), f"{ALICE}\n")
        self.assertEqual(self.read(self.passwd), f"{ALICE}\n{BOB}\n")
        self.assertEqual(os.stat(self.passwd).st_ino, inode)
        self.assertEqual(self.store().users(), [])

    def test_take_over_writes_web_user_and_the_file(self):
        inode = os.stat(self.passwd).st_ino
        self.assertIn("right away", self.store().take_over(["alice", "bob"]))
        self.assertEqual([u.line for u in self.store().users()], [ALICE, BOB])
        self.assertEqual(self.read(self.passwd), f"{ALICE}\n{BOB}\n")
        self.assertEqual(os.stat(self.passwd).st_ino, inode)
        self.assertEqual(self.store().file_only(), [])
        # a change works again
        self.store().add("carol", "pw")
        with self.assertRaises(users.UsersError):
            self.store().take_over(["nobody"])

    def test_a_line_t_pot_does_not_take_is_not_offered(self):
        with open(self.passwd, "w") as handle:
            handle.write(f"{ALICE}\nold:abJnggxhB/yWI\nno colon here\n\n")
        self.assertEqual([e.name for e in self.store().file_only()], ["alice"])

    def test_the_lost_file_is_offered_but_does_not_block(self):
        repo, passwd = checkout(self)                   # WEB_USER: tsec
        with open(passwd, "w") as handle:
            handle.write(users.parse(APR1)[0].line + "\n")
        with open(passwd + ".lost", "w") as handle:
            handle.write(f"bob:$apr1$older$hash\n{BOB}\n{ALICE}\n")
        store = users.load(repo, hasher=fake_hash)
        found = store.file_only()
        # the last line of a name counts in .lost
        self.assertEqual([(e.name, e.source, e.line) for e in found],
                         [("bob", "nginxpasswd.lost", BOB), ("alice", "nginxpasswd.lost", ALICE)])
        store.add("carol", "pw")                         # not blocked
        users.load(repo, hasher=fake_hash).take_over(["bob"])
        self.assertEqual(self.read(passwd + ".lost"), f"{ALICE}\n")
        self.assertEqual([u.name for u in users.load(repo).users()], ["tsec", "carol", "bob"])
        self.assertIn(f"{BOB}\n", self.read(passwd))

    def test_the_live_file_wins_over_the_lost_one(self):
        with open(self.lost, "w") as handle:
            handle.write("alice:$apr1$older$hash\n")
        found = {e.name: e for e in self.store().file_only()}
        self.assertEqual((found["alice"].source, found["alice"].line), ("nginxpasswd", ALICE))

    def legacy_checkout(self):
        """WEB_USER: tsec; nginxpasswd: tsec and a line nginx takes but T-Pot does not ($1$)."""
        repo, passwd = checkout(self)
        with open(passwd, "w") as handle:
            handle.write(users.parse(APR1)[0].line + "\nlegacy:$1$salt$legacyhash\n")
        return repo, passwd

    def test_a_line_t_pot_does_not_take_goes_into_the_lost_file(self):
        repo, passwd = self.legacy_checkout()
        store = users.load(repo, hasher=fake_hash)
        self.assertEqual(store.file_only(), [])                  # not offered, it does not block either
        self.assertIn("1 line(s) of nginxpasswd are kept in nginxpasswd.lost", store.add("carol", "pw"))
        self.assertEqual(self.read(passwd + ".lost"), "legacy:$1$salt$legacyhash\n")
        self.assertEqual(oct(os.stat(passwd + ".lost").st_mode & 0o777), "0o660")
        self.assertNotIn("legacy", self.read(passwd))
        # removing a user of WEB_USER keeps nothing
        users.load(repo, hasher=fake_hash).remove("carol")
        self.assertEqual(self.read(passwd + ".lost"), "legacy:$1$salt$legacyhash\n")

    def test_no_change_when_the_lost_file_cannot_take_them(self):
        repo, passwd = self.legacy_checkout()
        os.mkdir(passwd + ".lost")
        before = self.read(passwd)
        with self.assertRaises(users.UsersError) as caught:
            users.load(repo, hasher=fake_hash).add("carol", "pw")
        self.assertEqual(str(caught.exception), f"1 line(s) of nginxpasswd cannot be kept in {passwd}.lost (it is not "
                                                f"a regular file), nginxpasswd stays as it is")
        self.assertEqual(self.read(passwd), before)
        self.assertEqual([u.name for u in users.load(repo).users()], ["tsec"])

    def test_take_over_replaces_the_lost_file(self):
        with open(self.lost, "w") as handle:
            handle.write(f"erin:$apr1$x$y\n{ALICE}\n")
        os.chmod(self.lost, 0o644)
        inode = os.stat(self.lost).st_ino
        users.drop_lost(self.passwd, ["alice"])
        self.assertEqual(self.read(self.lost), "erin:$apr1$x$y\n")
        self.assertEqual(oct(os.stat(self.lost).st_mode & 0o777), "0o660")
        self.assertNotEqual(os.stat(self.lost).st_ino, inode)
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.lost)) if n.startswith(".")], [])

    @unittest.skipIf(os.geteuid() == 0, "root reads every file")
    @unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
    def test_import_names_a_file_it_cannot_read(self):
        from tpotctl import cli
        with open(self.lost, "w") as handle:
            handle.write("erin:$apr1$x$y\n")
        os.chmod(self.lost, 0)
        self.addCleanup(os.chmod, self.lost, 0o600)
        original = users.load
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(users, "load", lambda: original(self.repo, hasher=fake_hash)), \
                mock.patch.object(cli.os, "geteuid", return_value=1000, create=True), \
                redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.main(["users", "import", "-y"]), 1)
        text = out.getvalue() + err.getvalue()
        self.assertIn(f"cannot read {self.lost}: Permission denied, log in again for the group tpot", text)
        self.assertNotIn("Nothing to take over", text)

    def test_keep_puts_them_into_the_lost_file(self):
        """The edition switch of a SENSOR to a HIVE: its nginxpasswd is not served, its users are kept."""
        with open(self.lost, "w") as handle:
            handle.write(f"{BOB}\n")
        note = self.store().add("carol", "pw", keep=True)
        self.assertIn("1 line(s) of nginxpasswd are kept in nginxpasswd.lost", note)
        self.assertEqual(self.read(self.lost), f"{BOB}\n{ALICE}\n")
        self.assertEqual(self.read(self.passwd), fake_hash("carol", "pw") + "\n")
        self.assertEqual([e.name for e in self.store().file_only()], ["bob", "alice"])

    @unittest.skipUnless(textual, "Rich is not installed, run with the venv of tpot")
    def test_cli_import(self):
        from tpotctl import cli
        original = users.load

        class Stdin(io.StringIO):
            def isatty(self):
                return False
        with mock.patch.object(users, "load", lambda: original(self.repo, hasher=fake_hash)), \
                mock.patch.object(cli.os, "geteuid", return_value=1000, create=True):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", Stdin()):
                try:
                    code = cli.main(["users", "import"])
                except SystemExit as stop:
                    code = stop.code
                self.assertEqual(code, 2)                               # cannot ask
                self.assertEqual(cli.main(["users", "list"]), 1)
                self.assertEqual(cli.main(["users", "add", "carol", "--password-stdin"]), 1)
                self.assertEqual(cli.main(["users", "import", "-y"]), 0)
                self.assertEqual(cli.main(["users", "import", "--yes"]), 0)  # nothing left
            text = out.getvalue() + err.getvalue()
            self.assertIn("nginxpasswd holds 2 web user(s) WEB_USER does not name (alice, bob), take them over "
                          "with: tpot users import", text)
            self.assertIn("take them over first: tpot users import", text)
            self.assertIn("alice  (from nginxpasswd)", text)
            self.assertNotIn("To drop one on purpose", text)          # nothing of .lost
            self.assertIn("Nothing to take over", text)
        self.assertEqual(self.read(self.passwd), f"{ALICE}\n{BOB}\n")
        self.assertEqual([u.name for u in users.load(self.repo).users()], ["alice", "bob"])

    def test_env_check_names_the_import(self):
        from tpotctl import settings
        problems = [p for p in settings.load(self.repo).problems() if p.key == "WEB_USER"]
        self.assertEqual([p.level for p in problems], ["error"])
        self.assertEqual(problems[0].text, "is not set or empty; nginxpasswd holds 2 web user(s) it does not name, "
                                           "take them over with: tpot users import.")
        self.store().take_over(["alice", "bob"])
        self.assertEqual([p for p in settings.load(self.repo).problems() if p.key == "WEB_USER"], [])
        with open(self.lost, "w") as handle:
            handle.write("erin:$apr1$x$y\n")
        problems = [p for p in settings.load(self.repo).problems() if p.key == "WEB_USER"]
        self.assertEqual([(p.level, p.text) for p in problems],
                         [("warning", "nginxpasswd.lost holds 1 web user(s) it does not name, take them over "
                                      "with: tpot users import.")])


@unittest.skipUnless(textual and yaml, "Textual is not installed, run with the venv of tpot")
class UsersPaneTest(unittest.IsolatedAsyncioTestCase):

    async def test_add_and_remove(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import ConfirmDialog, UserDialog
        repo, _passwd = checkout(self)

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

            def users(self):
                return users.load(repo, hasher=fake_hash)

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        with mock.patch.object(users, "weakness", lambda pw: None):
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.pause(0.3)
                app.query_one("ContentSwitcher").current = "users"
                await pilot.pause(0.3)
                self.assertEqual(app.query_one("#users-table").row_count, 1)
                await pilot.click("#user-add")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, UserDialog)
                app.screen.query_one("#user-name").value = "alice"
                app.screen.query_one("#user-password").value = "a long passphrase"
                app.screen.query_one("#user-repeat").value = "a long passphrase"
                await pilot.click("#user-save")
                await pilot.pause(0.3)
                self.assertEqual(app.query_one("#users-table").row_count, 2)
                table = app.query_one("#users-table")
                table.move_cursor(row=1)
                await pilot.click("#user-remove")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, ConfirmDialog)
                await pilot.click("#yes")
                await pilot.pause(0.3)
                self.assertEqual(app.query_one("#users-table").row_count, 1)
        self.assertEqual([u.name for u in users.load(repo).users()], ["tsec"])

    async def test_take_over_the_users_of_nginxpasswd(self):
        from tpotctl import app as tapp, ops
        from tpotctl.screens.dialogs import ConfirmDialog, UserDialog
        repo, passwd = checkout(self, web_user="")
        with open(passwd, "w") as handle:
            handle.write(f"{ALICE}\n{BOB}\n")

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

            def users(self):
                return users.load(repo, hasher=fake_hash)

        app = tapp.TpotApp(backend=Backend(), runner=lambda command, cwd=None: 0)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause(0.3)
            app.query_one("ContentSwitcher").current = "users"
            await pilot.pause(0.3)
            self.assertEqual(app.query_one("#users-table").row_count, 0)
            self.assertTrue(app.query_one("#users-import-actions").display)
            self.assertEqual(str(app.query_one("#user-import").label), "Take over 2 users")
            self.assertIn("nginxpasswd holds 2 web user(s) WEB_USER does not name (alice, bob)",
                          str(app.query_one("#users-info").render()))
            await pilot.click("#user-add")             # refused before the dialog
            await pilot.pause(0.2)
            self.assertNotIsInstance(app.screen, UserDialog)
            await pilot.click("#user-import")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ConfirmDialog)
            await pilot.click("#yes")
            await pilot.pause(0.3)
            self.assertEqual(app.query_one("#users-table").row_count, 2)
            self.assertFalse(app.query_one("#users-import-actions").display)
        self.assertEqual([u.name for u in users.load(repo).users()], ["alice", "bob"])

    async def test_users_page_is_locked_on_a_sensor(self):
        from tpotctl import app as tapp

        class Backend(tapp.Backend):
            def linux_host(self):
                return True

            def tpot_type(self):
                return "SENSOR"

        # the page stays in the menu, locked with the reason
        self.assertIn("users", tapp.TpotApp(backend=Backend(), runner=None).locked)


if __name__ == "__main__":
    unittest.main()
