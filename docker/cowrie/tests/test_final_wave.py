"""Final wave, the smaller items: one rule of the process name for systemd and the persona data, the privilege
check of the package managers, the size of a file a package writes, logrotate of RHEL, a reinstall of
openssh-server that keeps the session's sshd, and the commands of a fresh time window that answer in full."""
import re
import time
import types
import unittest

from tests import test_patch
from tests.test_cross_track import PackageSession
from tests.test_packages import SessionCase
from tpot_personas import catalog, servers, system
from tpot_personas.identity import draw

COMMANDS = ("/usr/bin/python3 -Es /usr/sbin/tuned -l -P", "/usr/bin/python2 -Es /usr/sbin/tuned -l -P",
            "/bin/sh /usr/bin/mysqld_safe", "@dbus-daemon --system --address=systemd:", "nginx: master process x",
            "sh -c true", "/usr/bin/perl -w /usr/sbin/x", "-bash", "/usr/lib/systemd/systemd-journald",
            "/usr/bin/python3 -m http.server", "/usr/sbin/sshd -D")


class CommTest(SessionCase):
    def test_systemd_and_the_persona_name_a_process_alike(self):
        run = self.session("rhel-9", uid=0)
        unit_comm = run.commands["systemctl"].call.__globals__["tpot_unit_comm"]
        self.assertEqual([servers.process_name(c) for c in COMMANDS], [unit_comm(c) for c in COMMANDS])
        self.assertEqual([servers.process_name(c) for c in COMMANDS],
                         ["tuned", "tuned", "mysqld_safe", "dbus-daemon", "nginx", "sh", "x", "bash",
                          "systemd-journal", "python3", "sshd"])

    def test_the_status_of_tuned_names_it(self):
        run = self.session("rhel-9", uid=0)
        self.assertIn(" (tuned)\n", run(["systemctl", "status", "tuned"])[0])


class PrivilegeTest(unittest.TestCase):
    def test_apt_and_yum_ask_tpot_privileged(self):
        root = test_patch.PatchTest("test_applies_to_v3_1_3")
        root.addCleanup = self.addCleanup
        patched = root.patched()
        for name in ("apt", "yum"):
            with self.subTest(name):
                source = (patched / f"src/cowrie/commands/{name}.py").read_text()
                self.assertEqual(source.count('"tpot_sudo"'), 1, name)   # the one of tpot_privileged
                self.assertGreater(source.count("tpot_privileged(self)"), 2, name)


class PackageWriteTest(SessionCase):
    def test_a_long_file_has_the_size_of_its_text(self):
        run = self.session("debian-bookworm", uid=0)
        namespace = run.commands["systemctl"].call.__globals__
        nodes = {}

        class Fs:
            def exists(self, path):
                return path in nodes or path == "/etc"

            def mkfile(self, path, uid, gid, size, mode):
                nodes[path] = [path, 1, 0, 0, size, mode, 0, [], None, None]

            def getfile(self, path):
                return nodes[path]

        cmd = types.SimpleNamespace(fs=Fs())
        text = b"x" * (namespace["TPOT_UNIT_LIMIT"] + 10)
        self.assertTrue(namespace["tpot_package_write"](cmd, "/etc/big", text))
        node = nodes["/etc/big"]
        self.assertEqual(node[4], len(node[7]))
        self.assertEqual(node[4], namespace["TPOT_UNIT_LIMIT"])


class LogrotateTest(SessionCase):
    def test_rhel_has_logrotate_of_its_minor_release(self):
        persona = catalog.by_id("rhel-9")
        for seed in range(6):
            ident = draw(persona, seed)
            versions = {"9.4": "3.18.0-8.el9", "9.5": "3.18.0-8.el9", "9.6": "3.18.0-9.el9"}
            with self.subTest(release=ident.release.label):
                self.assertEqual(system.program_owner(persona, ident, "/usr/sbin/logrotate"), "logrotate")
        run = self.session("rhel-9", uid=0)
        out = run("rpm -q logrotate")[0]
        self.assertRegex(out, r"^logrotate-3\.18\.0-[89]\.el9\.x86_64\n$")
        self.assertIn(out.split("-", 1)[1].rsplit(".x86_64", 1)[0].split("logrotate-")[-1], versions.values())
        self.assertEqual(run("rpm -qf /usr/sbin/logrotate")[0], out)


class ReinstallTest(PackageSession):
    def test_a_reinstall_of_openssh_server_keeps_the_sshd_of_the_session(self):
        for pid, line in (("ubuntu-noble", "apt-get install -y --reinstall openssh-server"),
                          ("debian-bookworm", "apt-get install -y --reinstall openssh-server"),
                          ("rhel-9", "dnf reinstall -y openssh-server")):
            with self.subTest(pid):
                run = self.session(pid)
                before = [(p["PID"], p.get("PPID")) for p in run.protocol.process if "sshd" in p["COMMAND"]]
                self.assertTrue(before)
                self.assertEqual(run(line)[2], 0)
                after = [(p["PID"], p.get("PPID")) for p in run.protocol.process if "sshd" in p["COMMAND"]]
                # the listener, the parent of the session's sshd, is the same process
                self.assertEqual(after, before)


class FreshWindowTest(SessionCase):
    """The ordinary commands of a session answer in full in a fresh time window (TPOT_BUDGET)."""

    def fresh(self, run):
        budget = test_patch.budget_names()["tpot_budget"](run.protocol)
        for slot in budget.slots:
            slot[0] -= 10 * len(budget.slots)

    def test_package_installs_answer_in_full(self):
        for pid, line, done in (("debian-bookworm", "apt-get install -y nginx", "Setting up nginx ("),
                                ("centos-7", "yum install -y tree", "Complete!"),
                                ("rhel-9", "dnf install -y tree", "Complete!")):
            with self.subTest(pid):
                run = self.session(pid, uid=0)
                self.fresh(run)
                out, err, code = run(line)
                self.assertEqual(code, 0, err)
                self.assertIn(done, out)

    def test_text_tools_answer_in_full(self):
        """awk, sort, seq and wc as far as their own bounds take them (awk reads 1 MiB, sort 50000 lines of 1 MiB):
        the same answer as without a time budget."""
        run = self.session("debian-bookworm", uid=0, extra_modules=("fs", "awk", "wc"))
        lines = [f"{n}\n" for n in range(1, 202000)]
        numbers = "".join(lines).encode()   # 1.3 MB
        self.assertGreater(len(numbers), 1_300_000)
        first = "".join(lines[:150000]).encode()   # what awk takes whole (below its 1 MiB)
        some = lines[:50000]
        for args, data, expected in ((["awk", "END{print NR}"], numbers, f"{len(numbers[:1 << 20].splitlines())}\n"),
                                     (["awk", "{print $1}"], first, first.decode()),
                                     (["sort", "-n"], "".join(reversed(some)).encode(), "".join(some)),
                                     (["sort"], "".join(some).encode(), "".join(sorted(some))),
                                     (["seq", "100000"], None, "".join(f"{n}\n" for n in range(1, 100001))),
                                     (["wc", "-l"], numbers, "201999\n")):
            with self.subTest(args=args):
                self.fresh(run)
                out, err, code = run(args, input_data=data)
                self.assertEqual((err, code), ("", 0))
                self.assertEqual(out, expected)

    def test_the_budget_is_that_of_the_tuning(self):
        names = test_patch.budget_names()
        self.assertEqual((names["TPOT_BUDGET_CPU"], names["TPOT_BUDGET_WALL"]), (0.4, 0.5))


if __name__ == "__main__":
    unittest.main()
