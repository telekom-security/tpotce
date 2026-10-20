"""The commands of a connection (TpotCommands of the patched shell/protocol.py): a package install or removal of
one connection changes its own view of the commands; Cowrie's commands, which every session reads, stay as they
are."""
import shutil
import subprocess
import sys
import types
import unittest

from tests.helpers import tmpdir
from tests.test_packages import SessionCase
from tests.test_patch import FIXTURE, PATCH, method, module_parts


class Wget:
    """A stand-in for Cowrie's wget."""

    __name__ = "Command_wget"


WGET = ("wget", "/usr/bin/wget", "/bin/wget")


def protocol_source(case):
    return (case.patched() / "src/cowrie/shell/protocol.py").read_text()


class ViewTest(SessionCase):
    def names(self):
        return module_parts(protocol_source(self), ["TpotCommands", "tpot_commands"], {})

    def test_a_removal_and_an_install_change_only_the_view(self):
        names = self.names()
        shared = {"wget": Wget, "ls": "Command_ls"}
        one = names["tpot_commands"](types.SimpleNamespace(), shared)
        other = names["tpot_commands"](types.SimpleNamespace(), shared)
        self.assertIs(one.pop("wget"), Wget)
        one["/usr/bin/tree"] = "faked"
        self.assertEqual(shared, {"wget": Wget, "ls": "Command_ls"})
        self.assertNotIn("wget", one)
        self.assertEqual(dict(one), {"/usr/bin/tree": "faked", "ls": "Command_ls"})
        self.assertEqual(dict(other), shared)
        self.assertIsNone(one.get("wget"))
        # a reinstall: Cowrie's own command again
        self.assertTrue(one.tpot_restore("/usr/bin/wget"))
        self.assertIs(one["wget"], Wget)
        self.assertFalse(one.tpot_restore("/usr/bin/wget"))
        # Cowrie's own command set again is no addition of the view
        one.pop("ls")
        one["ls"] = "Command_ls"
        self.assertEqual(one.tpot_added, {"/usr/bin/tree": "faked"})
        self.assertEqual(one.tpot_removed, set())

    def test_the_channels_of_a_connection_share_its_view(self):
        names = self.names()
        shared = {"wget": Wget}
        avatar = types.SimpleNamespace()
        first, second = names["tpot_commands"](avatar, shared), names["tpot_commands"](avatar, shared)
        del first["wget"]
        self.assertNotIn("wget", second)
        self.assertIn("wget", names["tpot_commands"](types.SimpleNamespace(), shared))
        # an avatar that takes no attributes keeps a view of its own
        self.assertIn("wget", names["tpot_commands"](object(), shared))

    def test_the_protocol_reads_the_commands_of_its_connection(self):
        source = protocol_source(self)
        names = module_parts(source, ["TpotCommands", "tpot_commands"], {})
        shared = {"wget": Wget}
        cls = type("HoneyPotBaseProtocol", (), {"commands": shared})
        init = method(source, "HoneyPotBaseProtocol", "__init__",
                      {"tpot_commands": names["tpot_commands"], "HoneyPotBaseProtocol": cls})
        is_command = method(source, "HoneyPotBaseProtocol", "isCommand")

        def avatar():
            server = types.SimpleNamespace(hostname="h", fs=None)
            return types.SimpleNamespace(environ={}, server=server)

        one, other = cls.__new__(cls), cls.__new__(cls)
        init(one, avatar())
        init(other, avatar())
        one.commands.pop("wget")
        self.assertFalse(is_command(one, "wget"))
        self.assertTrue(is_command(other, "wget"))
        self.assertIs(cls.commands, shared)
        self.assertEqual(shared, {"wget": Wget})


class SessionTest(SessionCase):
    def wget_session(self, pid):
        run = self.session(pid, uid=0)
        run.commands.update({spelling: Wget for spelling in WGET})
        return run

    def assert_cowries(self, run):
        for spelling in WGET:
            self.assertIs(run.protocol.commands.get(spelling), Wget, spelling)
        self.assertEqual({spelling: run.commands[spelling] for spelling in WGET}, dict.fromkeys(WGET, Wget))

    def test_a_removal_is_not_seen_by_another_connection(self):
        for pid, line in (("debian-bookworm", "apt-get remove -y wget"), ("centos-7", "rpm -e wget"),
                          ("rhel-9", "rpm -e wget")):
            with self.subTest(pid=pid):
                run = self.wget_session(pid)
                shared = dict(run.commands)
                self.assertEqual(run(line)[2], 0)
                self.assertNotIn("wget", run.protocol.commands)
                self.assertNotIn("/usr/bin/wget", run.protocol.commands)
                # Cowrie's commands are as they were, a new connection has wget
                self.assertEqual(run.commands, shared)
                other = type(run.protocol)()
                self.assertIs(other.commands.get("wget"), Wget)
                # a channel of the same connection sees the removal
                self.assertNotIn("wget", type(run.protocol)(run.protocol.user).commands)

    def test_an_install_is_not_seen_by_another_connection(self):
        run = self.session("debian-bookworm", uid=0)
        shared = dict(run.commands)
        self.assertEqual(run("apt-get install -y tree")[2], 0)
        self.assertIn("/usr/bin/tree", run.protocol.commands)
        self.assertEqual(run.commands, shared)
        self.assertNotIn("/usr/bin/tree", type(run.protocol)().commands)

    def test_wget_is_cowries_after_a_remove_and_a_reinstall(self):
        for pid, remove, install in (("debian-bookworm", "apt-get remove -y wget", "apt-get install -y wget"),
                                     ("ubuntu-noble", "apt remove -y wget", "apt install -y wget"),
                                     ("rhel-9", "dnf remove -y wget", "dnf install -y wget"),
                                     ("rhel-9", "yum remove -y wget", "yum install -y wget"),
                                     ("rhel-9", "rpm -e wget", "dnf install -y wget"),
                                     ("centos-7", "yum remove -y wget", "yum install -y wget"),
                                     ("centos-7", "rpm -e wget", "yum install -y wget")):
            with self.subTest(pid=pid, remove=remove):
                run = self.wget_session(pid)
                self.assertEqual(run(remove)[2], 0)
                self.assertNotIn("wget", run.protocol.commands)
                out, err, code = run(install)
                self.assertEqual(code, 0, (out, err))
                self.assert_cowries(run)
                self.assertEqual(getattr(run.protocol, "tpot_faked", None) or {}, {})


class NeedleTest(unittest.TestCase):
    def test_fails_loudly_on_unknown_upstream(self):
        rel = "src/cowrie/shell/protocol.py"
        for old, new in (("\n\nclass HoneyPotBaseProtocol(insults.TerminalProtocol, TimeoutMixin):\n",
                          "\n\nclass HoneyPotBaseProtocol(insults.TerminalProtocol, TimeoutMixin):  # base\n"),
                         ("        self.user = avatar\n        self.environ = avatar.environ\n",
                          "        self.user = avatar\n        self.environ = dict(avatar.environ)\n")):
            with self.subTest(needle=old):
                root = tmpdir(self) / "cowrie"
                shutil.copytree(FIXTURE, root)
                path = root / rel
                text = path.read_text()
                self.assertEqual(text.count(old), 1)
                path.write_text(text.replace(old, new))
                done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                      capture_output=True, text=True)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("Could not find expected text", done.stderr)


if __name__ == "__main__":
    unittest.main()
