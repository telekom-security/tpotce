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
                self.assertEqual(cli.main(["users", "add", "bob", "--password-stdin"]), 1)      # weak
                self.assertEqual(cli.main(["users", "add", "bob", "--password-stdin", "--allow-weak"]), 0)
                self.assertEqual(cli.main(["users", "remove", "bob"]), 2)                       # no --yes
                self.assertEqual(cli.main(["users", "remove", "bob", "--yes"]), 0)
                self.assertEqual(cli.main(["users", "list"]), 0)
            self.assertIn("alice", out.getvalue())
            self.assertIn("--allow-weak", err.getvalue())
        self.assertEqual([u.name for u in users.load(self.repo).users()], ["tsec", "alice"])


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
